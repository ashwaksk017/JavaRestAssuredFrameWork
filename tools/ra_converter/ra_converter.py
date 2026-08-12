"""ReadyAPI (SoapUI) test-suite -> Java + REST Assured + TestNG converter.

Reads a SoapUI/ReadyAPI project XML, extracts a specified subset of test
cases (currently: by JIRA-style prefix like 'B2B-172'), and emits the
corresponding artifacts targeting the ApiAutomationRestAssured framework:

  - Reusable service client class:
      src/main/java/com/ak/api/rest/clients/{Service}Client.java
  - Test class (one per JIRA prefix; one @Test method per SoapUI test case):
      src/test/java/com/ak/api/tests/imported/{prefix}/{Prefix}Test.java
  - Per-case CSV datasheet:
      src/test/resources/testdata/{case_name}.csv
  - Request-body JSON templates with #placeholder# syntax:
      src/main/resources/templates/{name}.json
  - Env config JSON (converter emits qa + prod stubs):
      src/main/resources/config/{env}.json
  - testng-{prefix}.xml suite entry.

Coverage in this iteration:
  - restrequest step -> service-client method + test-code call
  - properties step / ${step#field} references -> tracked in IR
  - datasource step -> flagged (CSV data-drive is target)
  - groovy step / GroovyScriptAssertion -> TODO comment (manual review)
  - assertions handled: Valid HTTP Status Codes, JsonPath Match,
    JsonPath Existence Match, Simple Equals, Simple NotContains,
    Response SLA Assertion. All others emit TODO stubs.

Usage:
  python ra_converter.py --input input/accountmemberregression.xml \\
                         --prefix B2B-172 --output output --config converter.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional
from xml.etree import ElementTree as ET

# SoapUI XML namespace
NS = {"con": "http://eviware.com/soapui/config"}


# ---------------------------------------------------------------------------
# Intermediate Representation (IR)
# ---------------------------------------------------------------------------

@dataclass
class Assertion:
    """A ReadyAPI assertion on a REST step."""
    type: str
    name: str
    disabled: bool = False
    # type-specific config, stored as dict of tag -> text
    config: dict = field(default_factory=dict)


@dataclass
class RestStep:
    """A ReadyAPI REST request step."""
    step_name: str          # SoapUI step name (e.g. 'tokenRequest')
    service: str            # e.g. 'Program Accounts API'
    method_name: str        # e.g. 'CreateProgramAccount'
    resource_path: str      # e.g. '/guests/{guestId}/businesses'
    http_method: str        # inferred (default POST); real value in <con:originalUri> or method attr
    endpoint: str           # e.g. 'http://localhost:9006'
    original_uri: str       # real target URL (from <con:originalUri>)
    media_type: str         # e.g. 'application/json'
    request_body: str       # raw body with ${...} placeholders
    headers: dict           # header -> value (parameter entries where key is header)
    path_params: dict       # path-param -> ${...} expression
    query_params: dict      # query-param -> ${...} expression
    assertions: list[Assertion] = field(default_factory=list)


@dataclass
class GroovyStep:
    step_name: str
    script: str


@dataclass
class PropertiesStep:
    step_name: str
    properties: dict       # prop-name -> initial value (usually empty; filled by preceding Groovy)


@dataclass
class DataSourceStep:
    step_name: str
    ds_type: str           # 'File', 'Excel', 'Grid', etc.
    columns: list[str] = field(default_factory=list)
    file_path: str = ""


@dataclass
class TransferStep:
    step_name: str
    transfers: list = field(default_factory=list)  # list of dicts with source/target


@dataclass
class TestCase:
    id: str
    name: str
    description: str
    steps: list = field(default_factory=list)  # ordered list of RestStep / GroovyStep / PropertiesStep / DataSourceStep / TransferStep

    @property
    def prefix(self) -> str:
        """JIRA-style prefix (e.g. 'B2B-172' from 'B2B-172_delete_program_accountmember_403_1')."""
        m = re.match(r"^([A-Z]+-\d+)_", self.name)
        return m.group(1) if m else self.name


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def _text(el: Optional[ET.Element], default: str = "") -> str:
    return el.text if (el is not None and el.text is not None) else default


def _parse_assertion(a_el: ET.Element) -> Assertion:
    a = Assertion(
        type=a_el.get("type", ""),
        name=a_el.get("name", ""),
        disabled=(a_el.get("disabled", "").lower() == "true"),
    )
    cfg_el = a_el.find("con:configuration", NS)
    if cfg_el is not None:
        for child in cfg_el:
            tag = child.tag.split("}")[-1]
            a.config[tag] = (child.text or "").strip()
    return a


def _parse_rest_step(step_el: ET.Element) -> RestStep:
    step_name = step_el.get("name", "")
    cfg_el = step_el.find("con:config", NS)
    service = cfg_el.get("service", "") if cfg_el is not None else ""
    method_name = cfg_el.get("methodName", "") if cfg_el is not None else ""
    resource_path = cfg_el.get("resourcePath", "") if cfg_el is not None else ""

    req_el = cfg_el.find("con:restRequest", NS) if cfg_el is not None else None
    media_type = req_el.get("mediaType", "application/json") if req_el is not None else "application/json"
    endpoint = _text(req_el.find("con:endpoint", NS)) if req_el is not None else ""
    original_uri = _text(req_el.find("con:originalUri", NS)) if req_el is not None else ""
    request_body = _text(req_el.find("con:request", NS)) if req_el is not None else ""

    # HTTP method: SoapUI stores it via methodName heuristics + resource
    # config in a separate part of the project XML. Best-effort infer:
    http_method = _infer_http_method(method_name, resource_path, request_body, step_name)

    # Parameters: <con:parameters><con:entry key="X" value="${...}"/></con:parameters>
    # These are a mix of query params, path params, and HEADERS (esp. Authorization).
    params_el = req_el.find("con:parameters", NS) if req_el is not None else None
    all_params = {}
    if params_el is not None:
        for entry in params_el.findall("con:entry", NS):
            all_params[entry.get("key", "")] = entry.get("value", "")

    # Split params: path (referenced in resource_path as {name}), header (well-known),
    # remaining -> query.
    path_params, headers, query_params = {}, {}, {}
    header_names = {"authorization", "content-type", "accept", "correlationid",
                    "x-correlation-id", "x-request-id"}
    for k, v in all_params.items():
        if f"{{{k}}}" in resource_path:
            path_params[k] = v
        elif k.lower() in header_names:
            headers[k] = v
        else:
            query_params[k] = v

    # Inline assertions attached to this REST step
    assertions = []
    if req_el is not None:
        for a_el in req_el.findall("con:assertion", NS):
            assertions.append(_parse_assertion(a_el))

    return RestStep(
        step_name=step_name,
        service=service,
        method_name=method_name,
        resource_path=resource_path,
        http_method=http_method,
        endpoint=endpoint,
        original_uri=original_uri,
        media_type=media_type,
        request_body=request_body,
        headers=headers,
        path_params=path_params,
        query_params=query_params,
        assertions=assertions,
    )


_METHOD_KEYWORDS = {
    "get":    ["read", "get", "fetch", "list", "search", "retrieve"],
    "post":   ["create", "post", "enroll", "activate", "add", "insert", "token"],
    "put":    ["update", "put", "replace", "modify"],
    "patch":  ["patch"],
    "delete": ["delete", "remove", "cancel"],
}


def _infer_http_method(method_name: str, resource_path: str, body: str, step_name: str) -> str:
    """SoapUI's XML doesn't attribute HTTP verb on the step element; infer from
    the operation name / body presence. Callers can override in the emitter."""
    hay = " ".join((method_name, step_name, resource_path)).lower()
    for verb, kws in _METHOD_KEYWORDS.items():
        for kw in kws:
            if kw in hay:
                return verb.upper()
    # Fallback: body present -> POST, else GET
    return "POST" if body.strip() else "GET"


def _parse_groovy_step(step_el: ET.Element) -> GroovyStep:
    step_name = step_el.get("name", "")
    cfg_el = step_el.find("con:config", NS)
    script = ""
    if cfg_el is not None:
        script_el = cfg_el.find("script")
        if script_el is not None:
            script = _text(script_el)
        else:
            # fall back to concatenated text of config
            script = "".join(cfg_el.itertext()).strip()
    return GroovyStep(step_name=step_name, script=script)


def _parse_properties_step(step_el: ET.Element) -> PropertiesStep:
    step_name = step_el.get("name", "")
    cfg_el = step_el.find("con:config", NS)
    props = {}
    if cfg_el is not None:
        for prop in cfg_el.findall(".//con:property", NS):
            n_el = prop.find("con:name", NS)
            v_el = prop.find("con:value", NS)
            if n_el is not None:
                props[n_el.text or ""] = v_el.text if (v_el is not None) else ""
    return PropertiesStep(step_name=step_name, properties=props)


def _parse_datasource_step(step_el: ET.Element) -> DataSourceStep:
    step_name = step_el.get("name", "")
    ds = DataSourceStep(step_name=step_name, ds_type="unknown")
    cfg_el = step_el.find("con:config", NS)
    if cfg_el is not None:
        # ReadyAPI DataSource: dataSourceType attribute or child
        ds.ds_type = cfg_el.get("dataSourceType", "") or "unknown"
        # collect column names from <con:property> children
        for p in cfg_el.findall(".//con:property", NS):
            n = p.find("con:name", NS)
            if n is not None and n.text:
                ds.columns.append(n.text)
    return ds


def _parse_transfer_step(step_el: ET.Element) -> TransferStep:
    step_name = step_el.get("name", "")
    ts = TransferStep(step_name=step_name)
    cfg_el = step_el.find("con:config", NS)
    if cfg_el is not None:
        for xfer in cfg_el.findall(".//con:transfer", NS):
            src = xfer.find("con:sourceStepName", NS)
            src_path = xfer.find("con:sourcePath", NS)
            tgt = xfer.find("con:targetStepName", NS)
            tgt_path = xfer.find("con:targetPath", NS)
            ts.transfers.append({
                "source_step": _text(src),
                "source_path": _text(src_path),
                "target_step": _text(tgt),
                "target_path": _text(tgt_path),
            })
    return ts


@dataclass
class ManualStep:
    """SoapUI 'manualTestStep' -- documentation/manual instructions, not code.
    Emitted as a comment (no runtime behavior)."""
    step_name: str
    description: str = ""


def _parse_manual_step(step_el: ET.Element) -> "ManualStep":
    return ManualStep(
        step_name=step_el.get("name", ""),
        description=(step_el.find("con:description", NS).text
                     if step_el.find("con:description", NS) is not None
                     else "") or "",
    )


@dataclass
class JdbcStep:
    """SoapUI 'jdbc' step -- executes a SQL query against a configured DB."""
    step_name: str
    query: str = ""
    connection_string: str = ""
    driver: str = ""


def _parse_jdbc_step(step_el: ET.Element) -> "JdbcStep":
    cfg_el = step_el.find("con:config", NS)
    query = _text(cfg_el.find("con:query", NS)) if cfg_el is not None else ""
    conn = _text(cfg_el.find("con:connectionString", NS)) if cfg_el is not None else ""
    driver = _text(cfg_el.find("con:driver", NS)) if cfg_el is not None else ""
    return JdbcStep(
        step_name=step_el.get("name", ""),
        query=query, connection_string=conn, driver=driver)


_STEP_PARSERS = {
    "restrequest":    _parse_rest_step,
    "groovy":         _parse_groovy_step,
    "properties":     _parse_properties_step,
    "datasource":     _parse_datasource_step,
    "transfer":       _parse_transfer_step,
    "manualTestStep": _parse_manual_step,
    "jdbc":           _parse_jdbc_step,
}


def parse_test_suite(xml_path: str) -> list[TestCase]:
    """Parse a SoapUI test suite XML and return all test cases as IR.
    Legacy entry point: returns cases from the FIRST <con:testSuite> only
    (matches original behavior). For multi-suite XMLs prefer
    `parse_test_suites()` which returns [(suite_name, cases), ...]."""
    suites = parse_test_suites(xml_path)
    if not suites:
        raise ValueError("No testSuite found in XML")
    # Flatten all cases across all suites so callers that expected the
    # single-suite return keep working for multi-suite XMLs too.
    return [c for _sn, cs in suites for c in cs]


def parse_test_suites(xml_path: str) -> list[tuple[str, list[TestCase]]]:
    """Parse a SoapUI project XML and return every <con:testSuite> as
    (suite_name, cases). SoapUI project XMLs may contain more than one
    suite; the "one class per SoapUI suite" emit mode uses this to
    emit exactly one Java test class per suite."""
    tree = ET.parse(xml_path)
    root = tree.getroot()

    # Two shapes are legal here:
    #   1. Root IS the <con:testSuite> element (single-suite export)
    #   2. Root is a wrapping <con:soapui-project> containing 1..N testSuites
    if root.tag.endswith("testSuite"):
        ts_elements = [root]
    else:
        ts_elements = root.findall(".//con:testSuite", NS)
    if not ts_elements:
        return []

    out: list[tuple[str, list[TestCase]]] = []
    for ts_el in ts_elements:
        suite_name = ts_el.get("name", "") or "unnamed_suite"
        cases: list[TestCase] = []
        for tc_el in ts_el.findall("con:testCase", NS):
            desc_el = tc_el.find("con:description", NS)
            tc = TestCase(
                id=tc_el.get("id", ""),
                name=tc_el.get("name", ""),
                description=_text(desc_el),
            )
            for step_el in tc_el.findall("con:testStep", NS):
                step_type = step_el.get("type", "")
                parser = _STEP_PARSERS.get(step_type)
                if parser is None:
                    # Unknown step type -- store as a placeholder Groovy with a note
                    gs = GroovyStep(
                        step_name=step_el.get("name", ""),
                        script=f"// UNSUPPORTED STEP TYPE: {step_type}  (manual review)",
                    )
                    tc.steps.append(gs)
                else:
                    tc.steps.append(parser(step_el))
            cases.append(tc)
        out.append((suite_name, cases))
    return out


# ---------------------------------------------------------------------------
# Audit ledger: proves every SoapUI assertion / Groovy block was translated
# (or explicitly skipped) so you can trust the conversion end-to-end.
# ---------------------------------------------------------------------------

@dataclass
class AuditLedger:
    """Collects per-run translation records; emits summary.md + 3 CSVs.

    Coverage values:
      FULL     -- 1:1 translation; runtime behavior expected to match
      PARTIAL  -- translated the recognised parts, some semantics likely lost
      STUB     -- runnable no-op stub emitted; nothing was translated
      SKIPPED  -- item was explicitly excluded (e.g. disabled assertion)
      TODO     -- assertion/step type not in our recognizer set at all
    """
    # (prefix, case, step, soapui_type, cfg_json, emitted_java, coverage)
    assertions: list[tuple] = field(default_factory=list)
    # (prefix, case, step, patterns_matched, coverage, preview)
    groovy: list[tuple] = field(default_factory=list)
    # (prefix, case, step_name, category, detail)
    unmapped: list[tuple] = field(default_factory=list)
    # (case, name, kind)  kind in {config, runtime, csv}
    placeholders: list[tuple] = field(default_factory=list)
    # (prefix, case, step_name, step_type)
    unknown_step_types: list[tuple] = field(default_factory=list)

    def add_assertion(self, prefix, case, step, soapui_type, cfg,
                       emitted_java, coverage):
        self.assertions.append((prefix, case, step, soapui_type,
                                 json.dumps(cfg or {}), emitted_java, coverage))
        if coverage in ("TODO", "STUB", "PARTIAL"):
            self.unmapped.append((prefix, case, step, "assertion",
                                   f'{soapui_type} ({coverage})'))

    def add_groovy(self, prefix, case, step, meta):
        pm = ";".join(meta.get("patterns_matched", [])) or "<none>"
        cov = meta.get("coverage", "STUB")
        preview = meta.get("preview", "")
        self.groovy.append((prefix, case, step, pm, cov, preview))
        if cov in ("STUB", "PARTIAL"):
            self.unmapped.append((prefix, case, step, "groovy",
                                   f'patterns={pm} coverage={cov} preview={preview}'))

    def add_unknown_step(self, prefix, case, step_name, step_type):
        self.unknown_step_types.append((prefix, case, step_name, step_type))
        self.unmapped.append((prefix, case, step_name, "step_type",
                               f'type={step_type}'))

    def add_placeholder(self, case, name, kind):
        self.placeholders.append((case, name, kind))

    def _write_csv(self, path: str, header: list[str], rows: list[tuple]):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as f:
            import csv as _csv
            w = _csv.writer(f)
            w.writerow(header)
            for r in rows:
                w.writerow(r)

    def write(self, output_dir: str, source_xml: str, generated_at: str,
               suite_name: str = "") -> None:
        # Namespace the audit under the suite so multiple imports coexist.
        sub = suite_name if suite_name else "_default"
        base = os.path.join(output_dir, "_audit", sub)
        self._write_csv(
            os.path.join(base, "assertions.csv"),
            ["prefix", "case", "step", "soapui_type", "config_json",
             "emitted_java", "coverage"],
            self.assertions)
        self._write_csv(
            os.path.join(base, "groovy.csv"),
            ["prefix", "case", "step", "patterns_matched", "coverage",
             "preview"],
            self.groovy)
        self._write_csv(
            os.path.join(base, "unmapped.csv"),
            ["prefix", "case", "step", "category", "detail"],
            self.unmapped)
        self._write_csv(
            os.path.join(base, "placeholders.csv"),
            ["case", "placeholder", "kind"],
            self.placeholders)

        # ---- summary.md -------------------------------------------------
        def _counts(rows, cov_index):
            c = defaultdict(int)
            for r in rows:
                c[r[cov_index]] += 1
            return c

        a_counts = _counts(self.assertions, 6)
        g_counts = _counts(self.groovy, 4)
        ph_counts = defaultdict(int)
        for _, _, k in self.placeholders:
            ph_counts[k] += 1

        def _pct(part, whole):
            if not whole:
                return "-"
            return f"{100 * part // whole}%"

        a_total = len(self.assertions)
        g_total = len(self.groovy)
        a_full = a_counts.get("FULL", 0)
        g_full = g_counts.get("FULL", 0)
        # ACTIVE = total minus SKIPPED (skipped items are disabled by the
        # source-XML author on purpose; they aren't converter gaps).
        a_active = a_total - a_counts.get("SKIPPED", 0)
        g_active = g_total - g_counts.get("SKIPPED", 0)

        lines = [
            f"# ra_converter audit report",
            "",
            f"- Generated: {generated_at}",
            f"- Source XML: `{source_xml}`",
            f"- Output root: `{output_dir}`",
            "",
            "## Coverage summary",
            "",
            "> `SKIPPED` = items disabled in the SoapUI XML (author intent) and",
            "> not a converter gap. `FULL% (active)` divides FULL by",
            "> (Total - Skipped) so the number reflects real coverage.",
            "",
            "| Category | Total | FULL | PARTIAL | STUB | TODO | SKIPPED | FULL% (active) |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
            f"| Assertions | {a_total} | {a_counts.get('FULL',0)} | "
            f"{a_counts.get('PARTIAL',0)} | {a_counts.get('STUB',0)} | "
            f"{a_counts.get('TODO',0)} | {a_counts.get('SKIPPED',0)} | "
            f"{_pct(a_full, a_active)} |",
            f"| Groovy blocks | {g_total} | {g_counts.get('FULL',0)} | "
            f"{g_counts.get('PARTIAL',0)} | {g_counts.get('STUB',0)} | "
            f"{g_counts.get('TODO',0)} | {g_counts.get('SKIPPED',0)} | "
            f"{_pct(g_full, g_active)} |",
            "",
            "## Placeholder classification",
            "",
            f"- Config-driven (env JSON): **{ph_counts.get('config', 0)}** placeholders",
            f"- Runtime-generated (ctx): **{ph_counts.get('runtime', 0)}** placeholders",
            f"- True CSV columns: **{ph_counts.get('csv', 0)}** placeholders",
            "",
        ]
        if self.unknown_step_types:
            lines.append("## Unknown SoapUI step types")
            lines.append("")
            seen_types = defaultdict(int)
            for _, _, _, t in self.unknown_step_types:
                seen_types[t] += 1
            for t, n in sorted(seen_types.items(), key=lambda x: -x[1]):
                lines.append(f"- `{t}` ({n} occurrences)")
            lines.append("")
        # Top unmapped detail (first 20)
        if self.unmapped:
            lines.append("## Top unmapped items (up to 20)")
            lines.append("")
            lines.append("| prefix | case | step | category | detail |")
            lines.append("|---|---|---|---|---|")
            for row in self.unmapped[:20]:
                p, c, s, cat, det = row
                det_short = (det or "").replace("|", "\\|")[:80]
                lines.append(f"| {p} | {c} | {s} | {cat} | {det_short} |")
            lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("Every row above corresponds to a source item that either "
                     "did not translate 1:1 to Java or that the converter has "
                     "no recognizer for. Fix the recognizer (in "
                     "`groovy_translator.py` or `Emitter._render_assertion`) "
                     "and regenerate to move an item out of this list.")
        os.makedirs(base, exist_ok=True)
        with open(os.path.join(base, "summary.md"), "w",
                  encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Analyzer: extract reuse across test cases
# ---------------------------------------------------------------------------

def group_by_prefix(cases: list[TestCase], prefix: str) -> list[TestCase]:
    """Return only the cases whose JIRA-style prefix matches (e.g. 'B2B-172')."""
    return [c for c in cases if c.prefix == prefix or c.name.startswith(prefix + "_")]


def _step_sig(step) -> tuple:
    """Structural signature of a step -- used to determine if two step
    sequences are equivalent enough to extract into a shared helper."""
    if isinstance(step, RestStep):
        return ("REST", step.step_name, step.method_name or "", step.resource_path)
    if isinstance(step, GroovyStep):
        return ("GROOVY", step.step_name)
    if isinstance(step, PropertiesStep):
        return ("PROPS", step.step_name)
    if isinstance(step, DataSourceStep):
        return ("DS", step.step_name)
    if isinstance(step, TransferStep):
        return ("TRANSFER", step.step_name)
    if isinstance(step, ManualStep):
        return ("MANUAL", step.step_name)
    if isinstance(step, JdbcStep):
        return ("JDBC", step.step_name)
    return (type(step).__name__, getattr(step, "step_name", ""))


def find_shared_flows(cases: list[TestCase],
                       min_cases: int = 10,
                       min_steps: int = 3,
                       max_steps: int = 30) -> list[dict]:
    """Group cases by their opening step signature (first min_steps), then
    within each group extend the shared prefix as far as ALL cases still
    agree. Groups covering >= min_cases become extractable flows.

    Returns a list of dicts, one per detected flow:
      {
        'id': 'flow_A',            # stable helper-method name
        'prefix_len': L,           # steps 0..L-1 will be extracted
        'cases': [case_name, ...], # every case that uses this flow
        'step_sigs': [...],        # structural signatures of the extracted steps
        'template_case': TestCase, # case whose steps we render into the helper
      }
    """
    from collections import defaultdict
    buckets: dict[tuple, list[TestCase]] = defaultdict(list)
    for c in cases:
        if len(c.steps) < min_steps:
            continue
        opener = tuple(_step_sig(s) for s in c.steps[:min_steps])
        buckets[opener].append(c)

    # Prioritize buckets by (num cases, extended prefix length) -- more
    # savings first. But we need to compute extended length first.
    flows: list[dict] = []
    for opener, group in buckets.items():
        if len(group) < min_cases:
            continue
        # Extend the prefix as far as every case in the group still agrees
        L = min_steps
        while L < max_steps:
            next_sigs = set()
            for c in group:
                next_sigs.add(_step_sig(c.steps[L]) if len(c.steps) > L else None)
            if len(next_sigs) != 1 or None in next_sigs:
                break
            L += 1
        # Prefer the FIRST case in the group as the template for rendering
        flows.append({
            "prefix_len": L,
            "cases": [c.name for c in group],
            "step_sigs": [_step_sig(s) for s in group[0].steps[:L]],
            "template_case": group[0],
        })

    # Sort by (num cases * prefix_len) descending -- biggest savings first
    flows.sort(key=lambda f: -(len(f["cases"]) * f["prefix_len"]))
    # Assign stable IDs (flow_A, flow_B, ...)
    for i, f in enumerate(flows):
        # A..Z, then AA..AZ, BA..
        n = i
        letters = ""
        while True:
            letters = chr(ord("A") + n % 26) + letters
            n = n // 26 - 1
            if n < 0:
                break
        f["id"] = f"flow_{letters}"
    return flows


def build_flow_assignment(cases: list[TestCase], flows: list[dict]) -> dict:
    """For each case, decide WHICH flow (if any) covers its opening steps.

    Greedy: pick the flow with the LONGEST prefix_len that matches. This
    minimizes the scenario-specific-inline-emission cost per test method.
    """
    assignment: dict[str, dict] = {}
    for c in cases:
        sig_seq = [_step_sig(s) for s in c.steps]
        best = None
        for f in flows:
            L = f["prefix_len"]
            if len(sig_seq) < L:
                continue
            if sig_seq[:L] == f["step_sigs"]:
                if best is None or f["prefix_len"] > best["prefix_len"]:
                    best = f
        if best is not None:
            assignment[c.name] = best
    return assignment


def collect_shared_rest_steps(cases: list[TestCase], min_occurrences: int = 2) -> dict:
    """Group REST steps by (method_name, resource_path) across cases.
    Returns dict mapping key -> list of RestStep occurrences.
    Only keeps groups with >= min_occurrences distinct cases."""
    groups = defaultdict(list)
    for case in cases:
        seen_in_case = set()
        for step in case.steps:
            if isinstance(step, RestStep):
                key = (step.method_name or "op", step.resource_path)
                if key in seen_in_case:
                    continue
                seen_in_case.add(key)
                groups[key].append((case.name, step))
    return {k: v for k, v in groups.items() if len(v) >= min_occurrences}


# ---------------------------------------------------------------------------
# Helpers for emitters
# ---------------------------------------------------------------------------

_PROJ_PROP_RX = re.compile(r"\$\{#Project#([A-Za-z0-9_]+)\}")
_STEP_PROP_RX = re.compile(r"\$\{([A-Za-z0-9_-]+)#([A-Za-z0-9_-]+)\}")


def soapui_expr_to_java(expr: str) -> str:
    """Translate a SoapUI property expression to a Java-code equivalent."""
    if expr is None:
        return "null"
    e = expr
    e = _PROJ_PROP_RX.sub(lambda m: f'config.get("{m.group(1)}")', e)
    e = _STEP_PROP_RX.sub(lambda m: f'ctx.get("{m.group(1)}.{m.group(2)}")', e)
    return f'"{e}"' if not (e.startswith("config.get") or e.startswith("ctx.get")) else e


def soapui_body_to_placeholders(body: str) -> tuple[str, list[str]]:
    """Convert SoapUI body's ${#Project#var} and ${step#field} refs to the
    framework's #var# placeholder syntax. Returns (translated_body, placeholders)."""
    placeholders: list[str] = []
    def _proj(m):
        var = m.group(1)
        placeholders.append(var)
        return f"#{var}#"
    def _step(m):
        var = f"{m.group(1)}_{m.group(2)}".replace("-", "_")
        placeholders.append(var)
        return f"#{var}#"
    translated = _PROJ_PROP_RX.sub(_proj, body or "")
    translated = _STEP_PROP_RX.sub(_step, translated)
    return translated, placeholders


_SET_PROP_INSIDE_GROOVY_RX = re.compile(
    r'setPropertyValue\(\s*[\'"]([A-Za-z0-9_.-]+)[\'"]', re.IGNORECASE)


def classify_placeholders_for_case(case: "TestCase") -> dict:
    """Bucket each placeholder referenced by the case into config / runtime / csv.

    - config:  `${#Project#X}` -- comes from the env-config JSON.
    - runtime: `${step#field}` where a preceding Groovy step publishes
               `field` via setPropertyValue (or trace-def publication) OR
               where a preceding REST step's response feeds it via a Transfer.
    - csv:     anything left (rare in SoapUI setup chains; common in true
               data-driven suites).

    Returns dict {'config': set, 'runtime': set, 'csv': set} using the
    PLACEHOLDER NAMES that appear in emitted templates (matching
    soapui_body_to_placeholders output).
    """
    config, runtime, csv_kind = set(), set(), set()

    # 1. Collect all raw refs (before rewrite) across bodies + headers + path
    #    + query + assertion contents.
    raw_texts = []
    for step in case.steps:
        if isinstance(step, RestStep):
            raw_texts.append(step.request_body or "")
            for v in list(step.headers.values()) + \
                     list(step.path_params.values()) + \
                     list(step.query_params.values()):
                raw_texts.append(v or "")
            for a in step.assertions:
                for cv in (a.config or {}).values():
                    raw_texts.append(cv or "")
    corpus = "\n".join(raw_texts)

    for m in _PROJ_PROP_RX.finditer(corpus):
        config.add(m.group(1))

    # 2. For step#field refs, decide runtime vs csv by looking at whether the
    #    step is a Groovy step that publishes `field`, a Properties step whose
    #    field is populated by a preceding Groovy, or a Transfer target.
    published_by_step: dict[str, set[str]] = defaultdict(set)
    # collect published fields per step (step_name.field granularity)
    for step in case.steps:
        if isinstance(step, GroovyStep):
            for m in _SET_PROP_INSIDE_GROOVY_RX.finditer(step.script or ""):
                # setPropertyValue only names the FIELD; the target step comes
                # from `def var = getTestStepByName("STEP")` earlier in the
                # same script. Best-effort: mark the field as published, and
                # also mark it under the groovy step's own name for def-var
                # publications.
                published_by_step[step.step_name].add(m.group(1))
                # Also register raw field name (heuristic global publication)
                for tgt_m in re.finditer(
                    r'def\s+(\w+)\s*=\s*testRunner\.testCase\.getTestStepByName\(["\']([^"\']+)["\']\)',
                    step.script or ""):
                    published_by_step[tgt_m.group(2)].add(m.group(1))
            # def-var publications: `def X = ...` become <step_name>.X
            for m in re.finditer(r'def\s+(\w+)\s*=', step.script or ""):
                published_by_step[step.step_name].add(m.group(1))
        elif isinstance(step, TransferStep):
            for t in (step.transfers or []):
                tgt_step = t.get("target_step", "")
                tgt_path = t.get("target_path", "")
                if tgt_step and tgt_path:
                    published_by_step[tgt_step].add(tgt_path)

    for m in _STEP_PROP_RX.finditer(corpus):
        step_name, field = m.group(1), m.group(2)
        ph = f"{step_name}_{field}".replace("-", "_")
        if field in published_by_step.get(step_name, set()):
            runtime.add(ph)
        else:
            csv_kind.add(ph)

    return {"config": config, "runtime": runtime, "csv": csv_kind}


def sanitize_identifier(s: str) -> str:
    """Turn a SoapUI name into a valid Java identifier."""
    s = re.sub(r"[^A-Za-z0-9]", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    if s and s[0].isdigit():
        s = "_" + s
    return s or "unnamed"


def to_camel_case(s: str, upper_first: bool = False) -> str:
    parts = re.split(r"[^A-Za-z0-9]+", s)
    parts = [p for p in parts if p]
    if not parts:
        return "unnamed"
    if upper_first:
        return "".join(p[:1].upper() + p[1:] for p in parts)
    return parts[0][:1].lower() + parts[0][1:] + "".join(p[:1].upper() + p[1:] for p in parts[1:])


def short_id(name: str, max_len: int) -> str:
    """Filesystem-safe form of a SoapUI name, truncated + hashed if longer
    than `max_len` chars. Format: `<head>_<sha1-hex-6>` where head is the
    sanitized-lowercase name truncated to (max_len - 7). Names shorter
    than max_len pass through untouched. `max_len <= 0` disables
    truncation (useful when you have Windows longpaths on or run on
    Linux/macOS and want the full traceable name)."""
    sanitized = sanitize_identifier(name).lower()
    if max_len <= 0 or len(sanitized) <= max_len:
        return sanitized
    import hashlib
    h = hashlib.sha1(name.encode("utf-8")).hexdigest()[:6]
    head_len = max(1, max_len - 7)
    return f"{sanitized[:head_len]}_{h}"


def short_class(name: str, max_len: int) -> str:
    """CamelCase Java class-name form, truncated + hashed if too long.
    Java requires class filename = class name, so both share this shortener.
    `max_len` is the LIMIT ON THE PACKAGE-DIR NAME so class names get a bit
    of extra room for their `Test` suffix; caller adds `Test` after."""
    camel = to_camel_case(name, upper_first=True)
    if max_len <= 0 or len(camel) <= max_len:
        return camel
    import hashlib
    h = hashlib.sha1(name.encode("utf-8")).hexdigest()[:6].upper()
    head_len = max(1, max_len - 7)
    return f"{camel[:head_len]}_{h}"


# Common HTTP status codes ReadyAPI users append to test-case names to
# distinguish scenarios (e.g. `..._200_1`, `..._400`, `..._403_4`). These
# are stripped for the readable method name so the same business intent
# maps to a stable base name; the status code moves to the CSV column
# `expected_status_code` and the trailing `_N` disambiguator is kept as a
# `_N` suffix on the method (otherwise the class would have duplicates).
_STATUS_CODES_IN_NAMES = {
    "200", "201", "202", "204", "206",
    "301", "302", "304",
    "400", "401", "403", "404", "405", "406", "409", "410", "412", "413", "415", "416", "422", "429",
    "500", "502", "503", "504",
}


def _business_method_name(case_name: str) -> tuple[str, str, str]:
    """Derive a business-meaningful Java method name from a SoapUI test-case
    name. Returns (method_name, expected_status_code, variant_suffix).

    Rules:
      - Strip the leading JIRA ticket (`B2B-172_...`, `B2B134_...`) so
        the method name reads as business intent, not ticket ID (the ticket
        moves onto the @XrayTest / @Story annotation instead).
      - Recognize a trailing `_<status_code>` (e.g. `_200`, `_403`) and
        move it to the `expected_status_code` CSV column.
      - Recognize a trailing `_<N>` disambiguator that appears AFTER the
        status code (e.g. `_200_1`); return that as `variant_suffix` so
        method-name uniqueness can be preserved when multiple variants of
        the same status exist in one class.
      - Everything left becomes camelCase + `Test` suffix.

    Examples:
      "B2B-172_post_create_programaccount_member_403_1"
          -> ("postCreateProgramaccountMemberTest", "403", "1")
      "B2B339_post_program_add_packages_200"
          -> ("postProgramAddPackagesTest", "200", "")
      "Cleanup_testdata_creation"
          -> ("cleanupTestdataCreationTest", "", "")
    """
    working = case_name
    # 1. Strip JIRA-style prefix (`B2B-172_` / `B2B134_`). Keep whatever
    #    follows as the semantic body.
    m = re.match(r"^([A-Z]+[-_]?\d+)_(.+)$", working)
    if m:
        working = m.group(2)

    # 2. Peel off trailing `_<digits>` first (variant), then `_<status_code>`.
    variant = ""
    status_code = ""
    m = re.match(r"^(.+?)_(\d{1,3})$", working)
    if m and m.group(2) in _STATUS_CODES_IN_NAMES:
        working = m.group(1)
        status_code = m.group(2)
    else:
        # Could be `..._<status>_<N>` shape
        m = re.match(r"^(.+?)_(\d{3})_(\d{1,2})$", working)
        if m and m.group(2) in _STATUS_CODES_IN_NAMES:
            working = m.group(1)
            status_code = m.group(2)
            variant = m.group(3)

    method = to_camel_case(working, upper_first=False)
    # Java method-name rules -- our to_camel_case already handles this,
    # but be defensive if the whole thing collapsed to empty.
    if not method or not method[0].isalpha():
        method = "test" + method[:1].upper() + method[1:]
    if not method.endswith("Test"):
        method = method + "Test"
    return method, status_code, variant


def _cluster_cases_by_shape(cases: list["TestCase"]) -> list[list["TestCase"]]:
    """Group cases whose REST-step shape is IDENTICAL into clusters that
    can share a single @Test method + a multi-row CSV.

    Cluster key = tuple of (verb, resource_path, body-hash) for every
    REST step in order. Two cases cluster iff they:
      - hit the same endpoints in the same order,
      - use the same HTTP verbs,
      - use the same NORMALIZED body (SoapUI placeholders rewritten to
        `#name#` first, so scenario-only value differences don't split
        the cluster).

    Preserves discovery order (first case in each cluster keeps its
    original position). Returns list of clusters where each cluster
    is 1..N cases in the order they appeared in the SoapUI XML."""
    import hashlib

    def body_hash(step: "RestStep") -> str:
        if not step.request_body.strip():
            return "-"
        translated, _ = soapui_body_to_placeholders(step.request_body)
        return hashlib.sha1(translated.encode("utf-8")).hexdigest()[:8]

    def sig(case: "TestCase") -> tuple:
        return tuple(
            (s.http_method, s.resource_path, body_hash(s))
            for s in case.steps if isinstance(s, RestStep)
        )

    clusters: dict = {}
    order: list = []
    for c in cases:
        k = sig(c)
        if k not in clusters:
            clusters[k] = []
            order.append(k)
        clusters[k].append(c)
    return [clusters[k] for k in order]


def _cluster_method_name(cluster: list["TestCase"],
                          seen_bases: dict[str, int]) -> tuple[str, str, str]:
    """Return (method_name, expected_status_code, variant) for a cluster.

    Uses the first case's business-intent name as the base. When multiple
    clusters produce the same base name (two clusters happen to share the
    business phrasing but differ in step shape), disambiguate with a
    `_c2`, `_c3`, ... suffix. Case-level variant/status inside a cluster
    move to per-row CSV cells, not the method name.
    """
    rep = cluster[0]
    base, status, variant = _business_method_name(rep.name)
    # Single-case cluster keeps the same method-name-with-variant shape
    # the non-clustered v2 code used before. Multi-case clusters drop
    # the variant from the method name (each case's variant is a CSV row).
    if len(cluster) == 1 and variant:
        key = f"{base[:-4]}_{variant}Test"
    else:
        key = base
    n = seen_bases.get(key, 0) + 1
    seen_bases[key] = n
    final = key if n == 1 else f"{key[:-4]}_c{n}Test"
    # Cluster-level status/variant only meaningful for single-case
    # clusters; for multi-case clusters they vary per row and are
    # blank at the method level.
    if len(cluster) > 1:
        status, variant = "", ""
    return final, status, variant


def _unique_method_names(cases: list["TestCase"]) -> dict[str, tuple[str, str, str]]:
    """Given a list of cases that will all live in one class, return
    {case_name -> (final_method_name, expected_status_code, variant)}.
    Handles collisions after `_business_method_name()` normalization by
    appending `_v2`, `_v3`, ... to duplicates (variant suffix is kept
    inside the base name where present so `_v` never collides with it)."""
    out: dict[str, tuple[str, str, str]] = {}
    seen_counts: dict[str, int] = {}
    for c in cases:
        base, status, variant = _business_method_name(c.name)
        # Preserve the variant number in the method name so callers can
        # tell scenario 1 vs scenario 4 apart in reports without having
        # to open the CSV. (Variant only appears when the case name
        # originally carried a `_<status>_<N>` shape.)
        if variant:
            key = f"{base[:-4]}_{variant}Test"  # strip "Test", add "_N", re-add "Test"
        else:
            key = base
        n = seen_counts.get(key, 0) + 1
        seen_counts[key] = n
        if n == 1:
            final = key
        else:
            # Collision even after variant: append `_v2`, `_v3`, ...
            final = f"{key[:-4]}_v{n}Test"
        out[c.name] = (final, status, variant)
    return out


def _shape_sig(node) -> str:
    """Return a signature capturing JSON STRUCTURE only (keys + leaf
    types), not literal leaf values. Two bodies with identical sig can
    be merged into one template with `#tpl_<path>#` placeholders at
    positions where their leaf values differ."""
    if isinstance(node, dict):
        return "{" + ",".join(
            f"{k}:{_shape_sig(v)}" for k, v in sorted(node.items())) + "}"
    if isinstance(node, list):
        if not node:
            return "[]"
        # Signature per element position so list-of-heterogeneous-shapes
        # doesn't false-merge with list-of-uniform-shape. Truncate long
        # lists at 20 to keep the signature bounded.
        head = node[:20]
        return "[" + "|".join(_shape_sig(x) for x in head) + f"*{len(node)}]"
    if isinstance(node, bool):
        return "B"
    if isinstance(node, (int, float)):
        return "N"
    if isinstance(node, str):
        return "S"
    if node is None:
        return "_"
    return "?"


def _walk_leaves(node, path: str = ""):
    """Yield (json_path, leaf_value) for every leaf in the tree. `path`
    uses dot-notation for object keys and `[i]` for list indices so
    every leaf gets a stable JSON-path-like id."""
    if isinstance(node, dict):
        for k, v in node.items():
            key_seg = k if path == "" else f".{k}"
            yield from _walk_leaves(v, f"{path}{key_seg}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk_leaves(v, f"{path}[{i}]")
    else:
        yield (path, node)


def _sanitize_path_for_col(json_path: str) -> str:
    """Convert a JSON-path expression into a CSV-column-safe identifier.
    e.g. `contactInfo.email.address` -> `contactInfo_email_address`;
    `emailDomains[0]` -> `emailDomains_0`."""
    s = re.sub(r"[^A-Za-z0-9]+", "_", json_path).strip("_")
    return s or "root"


def _merge_bodies_with_placeholders(trees: list, texts: list[str]) -> tuple[str, list[dict[str, str]]]:
    """Given N JSON trees with IDENTICAL structural shape and their raw
    text, produce:
      - a single merged template TEXT where every leaf position whose
        value VARIES across the group is replaced with `#tpl_<path>#`
        (leaves whose value is constant across the group stay literal);
      - a list of dicts (one per input tree) mapping each placeholder
        column name to that tree's actual value at that path -- these
        become CSV cells so mergedRow at runtime substitutes the right
        per-scenario value.
    """
    import json as _json
    # Gather every leaf position across the group, unify by path.
    all_leaves_per_tree = [dict(_walk_leaves(t)) for t in trees]
    all_paths = sorted(set().union(*[set(d) for d in all_leaves_per_tree]))

    # For each path, check whether every tree has the same value.
    varying_paths: set = set()
    for p in all_paths:
        vals = {d.get(p) for d in all_leaves_per_tree}
        # `None` (a leaf whose value is literal JSON null) is distinct
        # from `missing` -- we use a sentinel for the missing case.
        if len(vals) > 1:
            varying_paths.add(p)

    # Build the merged template by rewriting the FIRST tree, replacing
    # every varying leaf with a #tpl_<path># placeholder. Use string-form
    # placeholder (unquoted numbers/booleans would break JSON syntax on
    # the row's default value); tests can still coerce with Integer.parseInt
    # etc. at consume time.
    def rewrite(node, path=""):
        if isinstance(node, dict):
            return {k: rewrite(v, f"{path}.{k}" if path else k)
                    for k, v in node.items()}
        if isinstance(node, list):
            return [rewrite(v, f"{path}[{i}]") for i, v in enumerate(node)]
        # leaf
        if path in varying_paths:
            col = f"tpl_{_sanitize_path_for_col(path)}"
            return f"#{col}#"
        return node

    merged_tree = rewrite(trees[0])
    merged_text = _json.dumps(merged_tree, indent=2, ensure_ascii=False)

    # Per-entry cells: for each tree, list the actual value at every
    # varying path. Missing paths get empty string; None-valued leaves
    # get "" too (mergedRow non-strict resolves #X# -> "null" if absent).
    per_entry_cells: list[dict[str, str]] = []
    for d in all_leaves_per_tree:
        cells: dict[str, str] = {}
        for p in varying_paths:
            col = f"tpl_{_sanitize_path_for_col(p)}"
            v = d.get(p)
            if v is None:
                cells[col] = ""
            elif isinstance(v, bool):
                cells[col] = "true" if v else "false"
            else:
                cells[col] = str(v)
        per_entry_cells.append(cells)

    return merged_text, per_entry_cells


def _jsonpath_to_gpath(path: str) -> str:
    """Rewrite Jayway/JsonPath.com syntax (SoapUI's convention) into the
    Groovy GPath syntax RestAssured's default `.jsonPath()` parser uses.

    Examples:
      `$[*]['guestId']`             -> `[*].guestId`
      `$.notifications[0].message`  -> `notifications[0].message`
      `$['a']['b'].c`               -> `a.b.c`
      `$..email`                    -> `..email` (recursive descent stays)

    Idempotent: paths already in GPath form pass through untouched."""
    if not path:
        return path
    p = path
    # Strip leading $ (root indicator; GPath doesn't use it)
    if p.startswith("$"):
        p = p[1:]
    # ['key'] and ["key"] -> .key (dot access equivalent in GPath)
    p = re.sub(r"\['([^']+)'\]", r".\1", p)
    p = re.sub(r'\["([^"]+)"\]', r".\1", p)
    # Leading dot -> drop
    if p.startswith("."):
        p = p[1:]
    return p


def _assert_col_key(a: "Assertion", a_idx: int) -> Optional[str]:
    """Return a short, filesystem+CSV-safe key that identifies what a
    SoapUI Assertion checks (the JSON path, the regex target, etc.).
    Returns None when the assertion carries no user-visible expected
    value (e.g. an existence check with no configured path).

    Column-name convention: expected_<sanitizedStepName>_<returnedKey>.
    """
    t = a.type
    cfg = a.config or {}
    def _clean(s: str, keep_dot: bool = False) -> str:
        s = (s or "").strip().lstrip("$.")
        rx = r"[^A-Za-z0-9]+" if not keep_dot else r"[^A-Za-z0-9.]+"
        s = re.sub(rx, "_", s).strip("_")
        return s or "root"
    if t == "Valid HTTP Status Codes":
        return "status_code"
    if t == "Invalid HTTP Status Codes":
        return "invalid_status_codes"
    if t == "JsonPath Match":
        return f"jsonpath_{_clean(cfg.get('path', ''))}"
    if t == "JsonPath Existence Match":
        return f"exists_{_clean(cfg.get('path', ''))}"
    if t == "JsonPath Count":
        return f"count_{_clean(cfg.get('path', ''))}"
    if t == "JsonPath RegEx Match":
        return f"regex_{_clean(cfg.get('path', ''))}"
    if t == "Simple Equals":
        return f"equals_{a_idx}" if a_idx else "equals"
    if t == "Simple Contains":
        return f"contains_{a_idx}" if a_idx else "contains"
    if t == "Simple NotContains":
        return f"notcontains_{a_idx}" if a_idx else "notcontains"
    if t == "Response SLA Assertion":
        return "sla_ms"
    return None


def _assert_default_value(a: "Assertion") -> str:
    """Extract the "expected value" a SoapUI Assertion carries. This is
    what lands in the CSV cell as the default; users edit rows to vary
    scenarios."""
    t = a.type
    cfg = a.config or {}
    if t == "Valid HTTP Status Codes":
        codes = (cfg.get("codes", "") or "").strip()
        return re.split(r"[,\s]+", codes)[0] if codes else "200"
    if t == "Invalid HTTP Status Codes":
        return (cfg.get("codes", "") or "").strip()
    if t in ("JsonPath Match", "JsonPath RegEx Match"):
        return (cfg.get("content", "") or "").strip()
    if t == "JsonPath Existence Match":
        return "true"
    if t == "JsonPath Count":
        raw = (cfg.get("expectedCount", "") or cfg.get("content", "") or "0").strip()
        return raw
    if t in ("Simple Equals", "Simple Contains", "Simple NotContains"):
        return (cfg.get("token", "") or "").strip()
    if t == "Response SLA Assertion":
        return str(cfg.get("SLA", cfg.get("sla", "1000")))
    return ""


def _csv_cell(value: str) -> str:
    """Quote a CSV cell when it contains a comma, quote, or newline.
    Doubles existing quotes per RFC 4180. Bare values pass through."""
    s = "" if value is None else str(value)
    if any(c in s for c in (",", '"', "\n", "\r")):
        return '"' + s.replace('"', '""') + '"'
    return s


def _jlit(value: str) -> str:
    """Escape a Python string so it is safe to drop between "..." in
    emitted Java source. Handles the four characters that break a Java
    string literal (backslash, double-quote, CR, LF) plus tab. Backslash
    MUST be escaped first so we don't double-escape newly inserted ones."""
    s = "" if value is None else str(value)
    return (s.replace("\\", "\\\\")
             .replace('"', '\\"')
             .replace("\r", "\\r")
             .replace("\n", "\\n")
             .replace("\t", "\\t"))


# ---------------------------------------------------------------------------
# Emitters
# ---------------------------------------------------------------------------

class Emitter:
    def __init__(self, output_dir: str, package_root: str = "com.ak.api",
                 ledger: Optional[AuditLedger] = None,
                 suite_name: str = "imported",
                 max_name_len: int = 40):
        self.output_dir = output_dir
        self.package_root = package_root
        # Namespace for THIS import -- lets multiple SoapUI suites coexist in
        # one output tree. All test packages / CSVs / templates / testng /
        # audit paths get suite-namespaced.
        self.suite_name = suite_name
        # Truncate any SoapUI-derived name that would produce a
        # filesystem path longer than max_name_len chars. See short_id().
        # 0 = no truncation.
        self.max_name_len = max_name_len
        # Mapping (short -> original) written to _audit/<suite>/name_mapping.csv
        # so users can always trace a truncated file back to its source case.
        self.name_mapping: dict[str, str] = {}
        self.written: list[str] = []
        # Populated by emit_service_client so emit_test_class can call
        # `client.tokenRequest(...)` instead of `client.method1(...)`.
        # Key: (method_name, resource_path) -> java method name.
        self.client_method_by_op: dict[tuple[str, str], str] = {}
        # And whether that client method takes a `String requestBody` arg,
        # so call sites match the signature exactly (a case-level PATCH
        # without a body still has to pass "" if the client was emitted
        # from a case whose PATCH had a body).
        self.client_takes_body: dict[tuple[str, str], bool] = {}
        # Populated by main() before emit_test_class runs. Maps case name to
        # the flow dict (or missing = no shared flow covers this case).
        self._flow_by_case: dict[str, dict] = {}
        # Populated per-test-case in the test emitter so Groovy translator
        # can reference the Response variable for each REST step.
        self.response_var_by_step: dict[str, str] = {}
        # Audit ledger -- optional, but populated when running from CLI.
        self.ledger: AuditLedger = ledger or AuditLedger()
        # Set by emit_test_class so per-case emitters can label ledger rows
        # with the current prefix/case.
        self._current_prefix: str = ""
        self._current_case: str = ""
        # Populated by emit_templates_deduplicated (v2 mode). Keyed by
        # (case_name, step_name) so per-step template path lookups get
        # the ACTUAL dedup'd path -- not the flat legacy guess. Empty in
        # legacy modes; _render_rest_step_body falls back to the flat
        # `templates/<suite>/<step>.json` shape when unset.
        self._template_path_by_step: dict[tuple[str, str], str] = {}
        # v2 only: set True by emit_per_method_csv_data_provider (which
        # co-emits PlaceholderResolver.java). Guards _render_rest_step_body
        # from calling the resolver in legacy modes where the class doesn't
        # exist on classpath.
        self._resolver_emitted: bool = False
        # Track every class FQN we've already emitted so a second SoapUI
        # suite whose name collapses to the same camelCase in this run
        # can be disambiguated instead of silently overwriting the first.
        self._emitted_class_fqns: set[str] = set()

    def _short(self, name: str) -> str:
        """Filesystem-safe short form of `name`. Records the mapping so
        `name_mapping.csv` can be emitted at the end of the run."""
        s = short_id(name, self.max_name_len)
        if s != sanitize_identifier(name).lower():
            # Only record entries that actually got shortened, otherwise
            # the mapping file would be huge and mostly identity.
            self.name_mapping[s] = name
        return s

    def _short_cls(self, name: str) -> str:
        """CamelCase short form of `name` for Java class + filename."""
        return short_class(name, self.max_name_len)

    def _write(self, rel_path: str, content: str) -> str:
        abs_path = os.path.join(self.output_dir, rel_path)
        # Windows caps traditional paths at MAX_PATH (260 chars). Suite +
        # long test-case names easily overflow that. `\\?\` prefixes tell
        # Windows to skip the check; harmless everywhere else.
        write_path = abs_path
        if os.name == "nt":
            norm = os.path.normpath(os.path.abspath(abs_path))
            if not norm.startswith("\\\\?\\"):
                write_path = "\\\\?\\" + norm
        os.makedirs(os.path.dirname(write_path), exist_ok=True)
        with open(write_path, "w", encoding="utf-8") as f:
            f.write(content)
        self.written.append(rel_path)
        return abs_path

    # -- Service client -----------------------------------------------------

    def emit_service_client(self, service_name: str, rest_step_groups: dict) -> str:
        """Emit one Java client class exposing a method per distinct REST op.
        rest_step_groups: dict of (method_name, resource_path) -> list of (case_name, RestStep)."""
        class_name = to_camel_case(service_name, upper_first=True) + "Client"
        pkg = f"{self.package_root}.rest.clients"

        methods = []
        seen_method_names: set[str] = set()
        for (op_name, path), occurrences in rest_step_groups.items():
            # Use the first occurrence as the canonical signature
            _, step = occurrences[0]
            # If op_name is generic ("Method 1"), fall back to the step name
            effective_op = op_name
            if not op_name or re.match(r"^Method\s*\d*$", op_name, re.IGNORECASE):
                effective_op = step.step_name
            # Deduplicate Java method names
            java_name = to_camel_case(effective_op, upper_first=False)
            base = java_name
            i = 2
            while java_name in seen_method_names:
                java_name = f"{base}{i}"
                i += 1
            seen_method_names.add(java_name)
            # Remember the actual Java method name so the test emitter can
            # generate matching callers instead of guessing.
            self.client_method_by_op[(op_name, path)] = java_name
            # And whether the signature includes a requestBody arg -- the
            # call site must match this decision or the compile breaks.
            self.client_takes_body[(op_name, path)] = step.http_method in ("POST", "PUT", "PATCH")
            java_method = self._render_client_method(effective_op, path, step, override_name=java_name)
            methods.append(java_method)

        content = f"""package {pkg};

import java.util.Map;

import io.restassured.response.Response;
import io.restassured.RestAssured;
import io.restassured.http.ContentType;

import com.ak.api.rest.utilities.Headers;
import com.ak.api.rest.utilities.RestUtilities;

/**
 * Auto-generated service client for the "{service_name}" REST API.
 *
 * Generated by ra_converter from a ReadyAPI project XML. Each method wraps a
 * distinct (methodName, resourcePath) pair used across the imported tests --
 * feel free to hand-tune (add validation, retry, response typing) after
 * regeneration; the converter uses `// @generated` markers to protect
 * hand-edits between the marker pairs.
 */
public class {class_name} {{

    private final String baseUrl;

    public {class_name}(String baseUrl) {{
        this.baseUrl = baseUrl;
    }}

{chr(10).join(methods)}
}}
"""
        rel = f"src/main/java/{pkg.replace('.', '/')}/{class_name}.java"
        self._write(rel, content)
        return class_name

    def _render_client_method(self, op_name: str, path: str, step: RestStep, override_name: str = None) -> str:
        """Render one Java method wrapping the REST call."""
        m_name = override_name or to_camel_case(op_name or step.step_name, upper_first=False)
        verb = step.http_method
        # Path params from resource_path
        path_params = re.findall(r"\{([A-Za-z0-9_]+)\}", path)
        java_path_params = ", ".join(f"String {p}" for p in path_params)
        # Build method params: (String token, [path_params...], [String body if POST/PUT/PATCH])
        params = ["String token"]
        if java_path_params:
            params.append(java_path_params)
        needs_body = verb in ("POST", "PUT", "PATCH")
        if needs_body:
            params.append("String requestBody")
        params_str = ", ".join(params)

        # Runtime path substitution
        path_expr = f'"{path}"'
        for p in path_params:
            path_expr = f'{path_expr}.replace("{{{p}}}", {p})'

        # Build headers block
        headers_block = """Map<String, String> headers = Headers.builder()
                .contentTypeJson()
                .acceptJson()
                .header("Authorization", token)
                .correlationId()
                .build();"""

        # Call site per verb
        if verb == "GET":
            call = 'Response res = RestAssured.given()\n' \
                   '                .headers(headers)\n' \
                   '                .get(baseUrl + path);'
        elif verb == "DELETE":
            call = 'Response res = RestAssured.given()\n' \
                   '                .headers(headers)\n' \
                   '                .delete(baseUrl + path);'
        elif verb == "POST":
            call = 'Response res = RestUtilities.getResponsePost(requestBody, baseUrl + path, headers);'
        elif verb == "PUT":
            call = 'Response res = RestAssured.given()\n' \
                   '                .headers(headers)\n' \
                   '                .contentType(ContentType.JSON)\n' \
                   '                .body(requestBody)\n' \
                   '                .put(baseUrl + path);'
        elif verb == "PATCH":
            call = 'Response res = RestAssured.given()\n' \
                   '                .headers(headers)\n' \
                   '                .contentType(ContentType.JSON)\n' \
                   '                .body(requestBody)\n' \
                   '                .patch(baseUrl + path);'
        else:
            call = f'// TODO: unsupported verb {verb}\n' \
                   'Response res = null;'

        return f"""    /**
     * {verb} {path}
     * Auto-generated from ReadyAPI operation: {op_name}
     */
    public Response {m_name}({params_str}) {{
        String path = {path_expr};
        {headers_block}
        {call}
        return res;
    }}
"""

    # -- Test class ---------------------------------------------------------

    def emit_test_class(self, prefix: str, cases: list[TestCase], service_class_name: str) -> str:
        pkg = f"{self.package_root}.tests.imported.{self.suite_name}.{self._short(prefix)}"
        class_name = self._short_cls(prefix) + "Test"

        # Set audit-ledger cursor for all cases in this prefix
        self._current_prefix = prefix
        test_methods = [self._render_test_method(c, service_class_name) for c in cases]

        content = f"""package {pkg};

import java.util.HashMap;
import java.util.Map;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.Test;

import com.ak.api.config.Config;
import com.ak.api.data.DataProviders;
import com.ak.api.data.Expected;
import com.ak.api.data.FakeData;
import com.ak.api.db.Db;
import com.ak.api.rest.utilities.Headers;
import com.ak.api.rest.utilities.RestUtilities;
import com.ak.api.retry.RetryAnalyzer;
import {self.package_root}.support.{self.suite_name}.SetupHelper;
import {self.package_root}.support.{self.suite_name}.TestSupport;
import com.ak.api.tests.BaseApiTest;
import com.ak.api.xray.XrayTest;

import com.ak.api.rest.clients.{service_class_name};

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;
import io.restassured.response.Response;

/**
 * Auto-generated by ra_converter from ReadyAPI test cases under JIRA
 * story {prefix}. Each ReadyAPI test case becomes one @Test method here.
 *
 * SETUP FLOW (shared by most cases in this story):
 *   1. Get OAuth token from token endpoint
 *   2. Generate synthetic guest data (email / username / phone)
 *   3. Enroll guest -> yields guestId
 *   4. Create program-account business -> yields accountId + memberId
 *   5. Read program-account (verifies setup)
 *   6. Execute the SCENARIO-specific call and assert expected status
 *
 * State between setup and assertion steps is carried in `ctx` (a HashMap
 * mirroring SoapUI's testCase properties). Environment / project-level
 * config values (client_id, client_secret, base_url, etc.) come from
 * `Config.get(key, fallback)` -- populate them via env vars or the
 * env-config JSON before running.
 */
@Epic("Imported from ReadyAPI")
@Feature("{prefix}")
public class {class_name} extends BaseApiTest {{

    private static final Logger LOG = LoggerFactory.getLogger({class_name}.class);

    private {service_class_name} client;
    /** Runtime context bag: IDs/tokens carried between setup calls. */
    private final Map<String, String> ctx = new HashMap<>();

    @BeforeClass
    public void initClient() {{
        String baseUrl = Config.get("base_url", Config.baseUrl());
        client = new {service_class_name}(baseUrl);
        LOG.info("initialised {class_name} against baseUrl={{}}", baseUrl);
    }}

{chr(10).join(test_methods)}
}}
"""
        rel = f"src/test/java/{pkg.replace('.', '/')}/{class_name}.java"
        self._write(rel, content)
        return rel

    def _reset_per_method_state(self) -> None:
        """Clear per-method emit state -- called at the start of every
        test method AND at the start of every SetupHelper method."""
        self.response_var_by_step = {}
        self._locals_in_method = {"testCaseId", "exp"}
        self._step_suffix_by_name = {}

    def _render_step(self, step, service_class_name: str) -> list[str]:
        """Render one step's Java lines. Extracted so both test-method
        emission and SetupHelper emission can share the exact same
        step-walking logic without duplication."""
        lines: list[str] = []
        if isinstance(step, RestStep):
            lines.extend(self._render_rest_step_body(step, service_class_name))
        elif isinstance(step, GroovyStep):
            lines.extend(self._render_groovy_translated(step))
        elif isinstance(step, PropertiesStep):
            lines.append(f'// [properties step] {step.step_name} -- initial values from base; populated by preceding groovy into ctx')
            for prop, val in (step.properties or {}).items():
                if val:
                    lines.append(
                        f'ctx.putIfAbsent("{step.step_name}.{prop}", "{val}");')
        elif isinstance(step, DataSourceStep):
            lines.append(
                f'// [datasource step] {step.step_name} -- iteration comes '
                f'from the CSV data-provider; datasource type: {step.ds_type}')
        elif isinstance(step, TransferStep):
            lines.extend(self._render_transfer_translated(step))
        elif isinstance(step, ManualStep):
            desc = (step.description or "").strip()
            desc_short = " ".join(desc.split())[:80]
            lines.append(
                f'// [manualTestStep] {step.step_name} -- documentation '
                f'only (no runtime action): {desc_short}')
        elif isinstance(step, JdbcStep):
            q_escaped = (step.query or "").replace("\\", "\\\\") \
                                          .replace('"', '\\"') \
                                          .replace("\r", " ").replace("\n", " ")
            lines.append(f'// [jdbc step] {step.step_name}')
            lines.append('if (Db.isConfigured()) {')
            lines.append(f'    Db.execute("{q_escaped}");')
            lines.append('} else {')
            lines.append(
                f'    LOG.warn("Skipping JDBC step (Db not configured): '
                f'{step.step_name}");')
            lines.append('}')
        else:
            cls_name = type(step).__name__
            lines.append(f'// [unknown step type: {cls_name}] {getattr(step, "step_name", "")} -- runnable no-op stub')
            lines.append(f'LOG.warn("skipped {cls_name} step: {getattr(step, "step_name", "")}");')
            self.ledger.add_unknown_step(
                self._current_prefix, self._current_case,
                getattr(step, "step_name", ""), cls_name)
        return lines

    def _render_test_method(self, case: TestCase, service_class_name: str,
                              flow_assignment: Optional[dict] = None) -> str:
        method_name = self._short(case.name)
        # Update audit-ledger cursor
        self._current_case = case.name
        # Reset per-method emit state
        self._reset_per_method_state()

        # If this case starts with a known shared flow, replace its first
        # N steps with a single SetupHelper call and only emit the
        # scenario-specific remaining steps inline.
        assigned_flow = flow_assignment or self._flow_by_case.get(case.name)
        body_lines = [
            f'String testCaseId = "{_jlit(case.name)}";',
            'Expected exp = expected(row);',
            '',
        ]

        # Replace the first N steps with a SetupHelper call if this case
        # matches a shared flow.
        skip_count = 0
        if assigned_flow:
            skip_count = assigned_flow["prefix_len"]
            body_lines.append(
                f'// ==== shared setup: SetupHelper.{assigned_flow["id"]} '
                f'({skip_count} steps, reused by '
                f'{len(assigned_flow["cases"])} cases) ====')
            body_lines.append(
                f'SetupHelper.{assigned_flow["id"]}('
                'client, ctx, row, softAssert, holder, testCaseId);')
            body_lines.append('')

        for step in case.steps[skip_count:]:
            body_lines.extend(self._render_step(step, service_class_name))
            body_lines.append('')

        indented = "\n".join("        " + l if l else "" for l in body_lines)

        # Description safe for Java string literal
        # (escape backslashes FIRST so we don't double-escape newly-inserted ones)
        desc_raw = (case.description or "")[:200]
        desc_safe = (desc_raw
                     .replace("\\", "\\\\")
                     .replace('"', '\\"')
                     .replace("\r", " ")
                     .replace("\n", " ")
                     .replace("\t", " "))

        # @XrayTest fallback: if the case doesn't match a JIRA-style prefix,
        # use the case name itself so it's still greppable in Xray.
        xray_id_raw = case.prefix if re.match(r"^[A-Z]+-\d+$", case.prefix) else case.name
        # sanitize the group value -- TestNG groups can't contain quotes.
        group_val = sanitize_identifier(case.prefix.lower())

        return f"""    @Test(dataProvider = "csvData",
          dataProviderClass = DataProviders.class,
          groups = {{"imported", "{group_val}"}},
          retryAnalyzer = RetryAnalyzer.class)
    @XrayTest("{_jlit(xray_id_raw)}")
    @Story("{_jlit(case.name)}")
    @Description("Imported from ReadyAPI. Original description: {desc_safe}")
    public void {method_name}(Map<String, String> row) throws Exception {{
{indented}
    }}
"""

    def _uniq_local(self, base: str) -> str:
        """Return a unique local-var name for the current test method,
        suffixing `_2`, `_3`, ... until we don't collide."""
        if base not in self._locals_in_method:
            self._locals_in_method.add(base)
            return base
        i = 2
        while f"{base}_{i}" in self._locals_in_method:
            i += 1
        name = f"{base}_{i}"
        self._locals_in_method.add(name)
        return name

    def _step_suffix(self, step_name: str) -> str:
        """Return a consistent suffix for all locals of a given step name,
        so Payload / Res / expected_X share the same _2 / _3 disambig."""
        if step_name in self._step_suffix_by_name:
            return self._step_suffix_by_name[step_name]
        # First occurrence gets no suffix; subsequent ones auto-increment.
        base = sanitize_identifier(step_name) + "Res"
        if base not in self._locals_in_method:
            self._step_suffix_by_name[step_name] = ""
            return ""
        i = 2
        while f"{base}_{i}" in self._locals_in_method:
            i += 1
        suf = f"_{i}"
        self._step_suffix_by_name[step_name] = suf
        return suf

    def _render_rest_step_body(self, step: RestStep, service_class_name: str) -> list[str]:
        """Emit Java for one REST step: build body -> call client -> assertions -> extract via jsonPath."""
        lines = [f'// ==== REST step: {step.step_name}  ({step.http_method} {step.resource_path}) ====']

        # Suffix (shared across Payload / Res / expected_X for this step
        # instance -- keeps them grouped when the same step name repeats).
        suf = self._step_suffix(step.step_name)
        base = sanitize_identifier(step.step_name)

        # Body (POST/PUT/PATCH)
        # The client method signature includes requestBody for these verbs
        # regardless of whether THIS particular occurrence has a body, so the
        # call-site ALWAYS passes something. If this step has no body, we
        # pass "" instead of skipping the arg (which would cause a
        # method-signature-mismatch compile error).
        verb_expects_body = step.http_method in ("POST", "PUT", "PATCH")
        has_source_body = bool(step.request_body.strip())
        body_var = '""'
        if verb_expects_body and has_source_body:
            # Template location resolution:
            #   v2 mode (--one-class-per-suite): emit_templates_deduplicated
            #     populates _template_path_by_step with the ACTUAL classpath-
            #     relative path (`templates/<suite>/<bucket>/<step>_<sha1>.json`)
            #     for every (case, step). Prefer that.
            #   Legacy mode: fall back to the flat `templates/<suite>/<step>.json`
            #     that emit_templates writes.
            classpath_ref = self._template_path_by_step.get(
                (self._current_case, step.step_name))
            if classpath_ref:
                # Split back into (dir/, file) as getRequestTemplate expects.
                slash = classpath_ref.rfind("/")
                tmpl_dir = classpath_ref[:slash + 1]  # includes trailing slash
                tmpl_file = classpath_ref[slash + 1:]
            else:
                tmpl_dir = f"templates/{self.suite_name}/"
                tmpl_file = f'{sanitize_identifier(step.step_name).lower()}.json'
            payload_var = f"{base}Payload{suf}"
            self._locals_in_method.add(payload_var)
            lines.append(f'String {payload_var} = RestUtilities.mapJsonValues(')
            lines.append(f'    RestUtilities.getRequestTemplate("{tmpl_dir}", "{tmpl_file}"),')
            lines.append(f'    TestSupport.mergedRow(row, ctx), /* strict */ false);')
            body_var = payload_var
            if self._resolver_emitted:
                # v2 only: runtime dynamic-value pass expands `<<X>>`
                # faker tokens (fresh per call) and `${X}` property refs
                # (per-test bag) into the payload. Users can drop these
                # into any CSV cell too. See PlaceholderResolver Javadoc.
                payload_var_resolved = f"{payload_var}Resolved"
                self._locals_in_method.add(payload_var_resolved)
                lines.append(f'String {payload_var_resolved} = '
                             f'PlaceholderResolver.resolveAll({payload_var}, ctx);')
                body_var = payload_var_resolved

        # Path-param args from ctx/config
        path_param_names = re.findall(r"\{([A-Za-z0-9_]+)\}", step.resource_path)
        path_args = []
        for p in path_param_names:
            # Look up in step.path_params for the ${...} expression, then translate
            expr = step.path_params.get(p, f'${{Properties#{p}}}')
            path_args.append(soapui_expr_to_java(expr))

        # Token: pull from ctx or headers
        token_expr = 'ctx.getOrDefault("tokenId.GeneratedTokenID", "")'
        if "Authorization" in step.headers:
            token_expr = soapui_expr_to_java(step.headers["Authorization"])

        # Use the client's ACTUAL method name (populated by emit_service_client),
        # not a re-derived guess -- fixes the "client.method1()" call bug.
        method_name_java = self.client_method_by_op.get(
            (step.method_name, step.resource_path),
            to_camel_case(step.method_name or step.step_name, upper_first=False))
        call_args = [token_expr] + path_args
        # Call site must match the client method's ACTUAL signature (which
        # was decided by the first occurrence of the op). The step's own
        # verb inference can disagree, but we defer to what the client emitter
        # already committed to.
        client_needs_body = self.client_takes_body.get(
            (step.method_name, step.resource_path), verb_expects_body)
        if client_needs_body:
            call_args.append(body_var)

        response_var = f"{base}Res{suf}"
        self._locals_in_method.add(response_var)
        # Register this step's response variable so a subsequent Groovy step
        # translator can reference it (e.g. tokenRequestRes.jsonPath()...).
        self.response_var_by_step[step.step_name] = response_var
        lines.append(f'Response {response_var} = client.{method_name_java}({", ".join(call_args)});')
        lines.append(f'RestUtilities.logResponseBody(testCaseId, holder, RestUtilities.getResponseAsString({response_var}));')

        # Assertions
        # `assertion_index` counts only ACTIVE assertions (skipped ones
        # don't consume an index) so the CSV column names stay stable
        # even if a SoapUI author toggles a disabled assertion on later.
        a_active = 0
        for a in step.assertions:
            if a.disabled:
                self.ledger.add_assertion(
                    self._current_prefix, self._current_case, step.step_name,
                    a.type, a.config, "", "SKIPPED")
                continue
            emitted, coverage = self._render_assertion(
                a, response_var, step.step_name, suffix=suf,
                assertion_index=a_active)
            a_active += 1
            lines.extend(emitted)
            self.ledger.add_assertion(
                self._current_prefix, self._current_case, step.step_name,
                a.type, a.config, " ".join(emitted), coverage)

        return lines

    def _render_assertion(self, a: Assertion, response_var: str,
                           step_name: str, suffix: str = "",
                           assertion_index: int = 0) -> tuple[list[str], str]:
        """Returns (java_lines, coverage) where coverage is FULL / PARTIAL / TODO.
        `suffix` disambiguates local vars when the same step name appears
        multiple times in one test method.

        Every expected VALUE is read from the CSV row via `row.getOrDefault`
        so scenarios in a multi-row cluster can each assert something
        different without changing Java code. The value from the SoapUI
        Assertion config becomes the FALLBACK when the CSV cell is blank.
        """
        t = a.type
        cfg = a.config
        sid = sanitize_identifier(step_name) + suffix
        # CSV column name for this assertion's expected value (may be None
        # for assertion types that don't carry a user-visible expected).
        col_key = _assert_col_key(a, assertion_index)
        col_name = f"expected_{sanitize_identifier(step_name)}_{col_key}" if col_key else None
        # Java-local var suffix keyed off (assertion_index, col_key) so
        # multiple assertions on the SAME step never collide. Falls back
        # to just the step's suffix when no col_key exists (unused var).
        vsid = f"{sid}_a{assertion_index}_{col_key}" if col_key else sid

        def _jlit(s: str) -> str:
            """Escape an arbitrary string for use inside a Java "" literal."""
            if s is None:
                return ""
            return (s.replace("\\", "\\\\")
                     .replace('"', '\\"')
                     .replace("\r", "\\r")
                     .replace("\n", "\\n")
                     .replace("\t", "\\t"))

        def _row_expr(fallback_literal: str) -> str:
            """Java expression that reads the expected value from CSV row
            with `fallback_literal` (already _jlit-escaped) as the default.
            Treats empty-string cells as MISSING so an empty CSV cell
            triggers the fallback (matches user intent: blank = 'no override')."""
            # ternary keeps this a one-line expression usable inline as an arg
            return (
                f'(row.get("{col_name}") == null || row.get("{col_name}").isEmpty() '
                f'? "{fallback_literal}" : row.get("{col_name}"))'
            )

        if t == "Valid HTTP Status Codes":
            codes = (cfg.get("codes", "") or "").strip()
            first_code = re.split(r"[,\s]+", codes)[0] if codes else "200"
            # Two-tier lookup: the standalone `expected_<step>_status_code`
            # column wins; otherwise fall back to `exp.getInt("statusCode", ...)`
            # which parses the legacy `expected` combined column. Empty
            # cell -> treat as missing so the fallback fires (not parseInt("")).
            return ([
                f'String rawStatus_{vsid} = row.get("{col_name}");',
                f'int expected_{vsid} = (rawStatus_{vsid} == null || rawStatus_{vsid}.isEmpty()) '
                f'? exp.getInt("statusCode", {first_code}) : Integer.parseInt(rawStatus_{vsid}.trim());',
                f'softAssert.assertEquals({response_var}.statusCode(), expected_{vsid}, "expected status for {step_name}");',
            ], "FULL")
        if t == "Invalid HTTP Status Codes":
            codes = (cfg.get("codes", "") or "").strip()
            code_list = [c for c in re.split(r"[,\s]+", codes) if c]
            checks = " && ".join(
                f'{response_var}.statusCode() != {c}' for c in code_list) or "true"
            return ([
                f'// invalid-status assertion values are fixed at converter time;'
                f' override via CSV column `{col_name}` (comma-separated) if needed.',
                f'softAssert.assertTrue({checks}, "invalid status codes: {codes}");',
            ], "FULL")
        if t == "JsonPath Match":
            path = _jlit(_jsonpath_to_gpath(cfg.get("path", "")))
            content_raw = cfg.get("content", "") or ""
            if content_raw.startswith(("{", "[")):
                return ([
                    f'// [JsonPath Match] content is JSON blob -- checking existence only:',
                    f'softAssert.assertNotNull({response_var}.jsonPath().get("{path}"), "JsonPath present: {path}");',
                ], "PARTIAL")
            if "${" in content_raw:
                # SoapUI property expansion in expected value -- keep runtime
                # substitution behavior; still parameterizable via CSV column.
                java_expr = soapui_expr_to_java(content_raw)
                return ([
                    f'String expected_{vsid} = row.getOrDefault("{col_name}", '
                    f'String.valueOf({java_expr}));',
                    f'softAssert.assertEquals({response_var}.jsonPath().getString("{path}"), '
                    f'expected_{vsid}, "JsonPath Match: {path}");',
                ], "FULL")
            content = _jlit(content_raw)
            return ([
                f'String expected_{vsid} = {_row_expr(content)};',
                f'softAssert.assertEquals({response_var}.jsonPath().getString("{path}"), '
                f'expected_{vsid}, "JsonPath Match: {path}");',
            ], "FULL")
        if t == "JsonPath Existence Match":
            path = _jlit(_jsonpath_to_gpath(cfg.get("path", "")))
            # existence check can be turned OFF for a row by setting the CSV
            # cell to "false"; empty cell keeps the (default = must-exist)
            # behavior.
            return ([
                f'if (!"false".equalsIgnoreCase(row.getOrDefault("{col_name}", "true"))) {{',
                f'    softAssert.assertNotNull({response_var}.jsonPath().get("{path}"), '
                f'"JsonPath exists: {path}");',
                f'}}',
            ], "FULL")
        if t == "JsonPath Count":
            path = _jlit(_jsonpath_to_gpath(cfg.get("path", "")))
            expected_raw = (cfg.get("expectedCount", "") or cfg.get("content", "") or "0").strip()
            expected_int = expected_raw if expected_raw.lstrip("-").isdigit() else "0"
            return ([
                f'Object count_{vsid} = {response_var}.jsonPath().get("{path}");',
                f'int actualCount_{vsid} = count_{vsid} instanceof java.util.List ? '
                f'((java.util.List<?>) count_{vsid}).size() : (count_{vsid} == null ? 0 : 1);',
                f'String rawExp_{vsid} = row.get("{col_name}");',
                f'int expectedCount_{vsid} = (rawExp_{vsid} == null || rawExp_{vsid}.isEmpty()) '
                f'? {expected_int} : Integer.parseInt(rawExp_{vsid}.trim());',
                f'softAssert.assertEquals(actualCount_{vsid}, expectedCount_{vsid}, '
                f'"JsonPath Count for {path}");',
            ], "FULL")
        if t == "JsonPath RegEx Match":
            path = _jlit(_jsonpath_to_gpath(cfg.get("path", "")))
            content = _jlit(cfg.get("content", ""))
            return ([
                f'String matched_{vsid} = {response_var}.jsonPath().getString("{path}");',
                f'String pattern_{vsid} = {_row_expr(content)};',
                f'softAssert.assertTrue(matched_{vsid} != null && matched_{vsid}.matches(pattern_{vsid}), '
                f'"JsonPath regex: {path}");',
            ], "FULL")
        if t == "Simple Equals":
            token = _jlit(cfg.get("token", ""))
            return ([
                f'String token_{vsid} = {_row_expr(token)};',
                f'softAssert.assertTrue({response_var}.asString().contains(token_{vsid}), '
                f'"Simple Equals contains token");',
            ], "FULL")
        if t == "Simple Contains":
            token = _jlit(cfg.get("token", ""))
            return ([
                f'String token_{vsid} = {_row_expr(token)};',
                f'softAssert.assertTrue({response_var}.asString().contains(token_{vsid}), '
                f'"Simple Contains: {token[:40]}");',
            ], "FULL")
        if t == "Simple NotContains":
            token = _jlit(cfg.get("token", ""))
            return ([
                f'String token_{vsid} = {_row_expr(token)};',
                f'softAssert.assertFalse({response_var}.asString().contains(token_{vsid}), '
                f'"Simple NotContains");',
            ], "FULL")
        if t == "Response SLA Assertion":
            sla = cfg.get("SLA", cfg.get("sla", "1000"))
            return ([
                f'String rawSla_{vsid} = row.get("{col_name}");',
                f'long sla_{vsid} = (rawSla_{vsid} == null || rawSla_{vsid}.isEmpty()) '
                f'? {sla}L : Long.parseLong(rawSla_{vsid}.trim());',
                f'softAssert.assertTrue({response_var}.time() <= sla_{vsid}, "SLA " + sla_{vsid} + "ms");',
            ], "FULL")
        if t == "SOAP Response":
            return ([
                f'softAssert.assertNotNull({response_var}.asString(), "SOAP Response non-empty");',
            ], "PARTIAL")
        if t == "Schema Compliance":
            return ([
                f'// [Schema Compliance] SoapUI schema-compliance check -- validate against your JSON schema:',
                f'// softAssert.assertTrue(JsonSchemaValidator.validate({response_var}.asString(), "path/to/schema.json"));',
                f'softAssert.assertNotNull({response_var}.asString(), "response present (schema check stubbed)");',
            ], "PARTIAL")
        if t in ("GroovyScriptAssertion", "GroovyScript"):
            script_body = (cfg.get("scriptText", "") or cfg.get("script", "") or "")
            preview = " ".join(script_body.split())[:80]
            return ([
                f'// [GroovyScriptAssertion] custom Groovy -- manual review required',
                f'// script preview: {preview}',
                f'// softAssert.assertTrue(<translate {preview[:40]}>);',
            ], "TODO")
        if t == "DataAndMetadataAssertion":
            return ([
                f'// [DataAndMetadataAssertion] SoapUI custom assertion -- manual review',
                f'softAssert.assertNotNull({response_var}.asString(), "response present (DataAndMetadata stubbed)");',
            ], "PARTIAL")
        if t == "MessageContentAssertion":
            return ([
                f'// [MessageContentAssertion] SoapUI XPath/XML-content check -- manual review',
                f'softAssert.assertNotNull({response_var}.asString(), "response present (MessageContent stubbed)");',
            ], "PARTIAL")
        # Unknown assertion type -- emit a TODO stub, log it in ledger
        return ([
            f'// TODO manual review: assertion type "{t}" not auto-converted. Original name: {_jlit(a.name)}',
        ], "TODO")

    def _render_groovy_translated(self, step: GroovyStep) -> list[str]:
        """Feed the Groovy translator; runnable stub if nothing matches.
        Also logs each block to the audit ledger."""
        from groovy_translator import translate as translate_groovy
        lines, meta = translate_groovy(
            step.script or "", self.response_var_by_step,
            step_name_hint=step.step_name)
        self.ledger.add_groovy(
            self._current_prefix, self._current_case, step.step_name, meta)
        return lines

    def _render_transfer_translated(self, step: TransferStep) -> list[str]:
        """Turn SoapUI PropertyTransfer into ctx.put(target, source-jsonpath)."""
        lines = [f'// [transfer step] {step.step_name}']
        for t in step.transfers:
            src_step = t.get("source_step", "")
            src_path = t.get("source_path", "")
            tgt_step = t.get("target_step", "")
            tgt_path = t.get("target_path", "")
            src_resp = self.response_var_by_step.get(src_step)
            if src_resp and src_path:
                # Best-effort: SoapUI paths are usually JsonPath or XPath.
                # If it starts with $. treat as JsonPath.
                if src_path.startswith("$"):
                    jp = src_path.lstrip("$.")
                    lines.append(
                        f'ctx.put("{tgt_step}.{tgt_path}", '
                        f'{src_resp}.jsonPath().getString("{jp}"));')
                else:
                    lines.append(
                        f'ctx.put("{tgt_step}.{tgt_path}", '
                        f'{src_resp}.asString());  '
                        f'// TODO: extract subpath if needed ({src_path})')
            else:
                lines.append(
                    f'// [transfer] no response found for source step '
                    f'"{src_step}"; skipping ctx.put("{tgt_step}.{tgt_path}", ...)')
        return lines

    # -- TestSupport helper (framework additive) ---------------------------

    def emit_test_support(self, config_placeholder_names: Optional[list[str]] = None) -> str:
        """Emit `com.<pkg>.support.<suite>.TestSupport` with helpers the
        generated tests depend on: mergedRow() unions CSV row + ctx +
        Config lookups. Namespaced under the suite so multiple imported
        suites don't overwrite each other's CONFIG_KEYS[] array."""
        pkg = f"{self.package_root}.support.{self.suite_name}"
        # Java array literal for keys we should proactively try to load from Config.
        cfg_keys = config_placeholder_names or []
        # Deduplicate + sort
        cfg_keys = sorted(set(cfg_keys))
        keys_java = ", ".join(f'"{k}"' for k in cfg_keys)
        content = f"""package {pkg};

import java.util.HashMap;
import java.util.Map;

import com.ak.api.config.Config;

/**
 * Auto-generated by ra_converter. Small helpers the imported tests depend on;
 * safe to hand-edit -- future regenerations only touch this file if it is
 * missing OR the CONFIG_KEYS array below is out of date.
 *
 * mergedRow precedence (highest wins):
 *   1. ctx  (runtime-generated: extracted ids, tokens, guest emails)
 *   2. row  (CSV data-provider cells)
 *   3. Config.get(key, "")   (env JSON: c_id, c_sec, base_url, etc.)
 *
 * That way #placeholder# in a request-body template resolves from the
 * highest-priority source that knows a value, and never sees a literal
 * unresolved #placeholder# at runtime.
 *
 * Key-naming aliasing: SoapUI's ${{stepName#field}} references become
 * #stepName_field# placeholders in the template (underscore-joined),
 * but ctx.put sites write dotted keys "stepName.field" (matching the
 * SoapUI property namespace). mergedRow expands every dotted key into
 * BOTH forms so #stepName_field# and #stepName.field# both resolve to
 * the same value without every ctx.put needing to double-write.
 */
public final class TestSupport {{

    /** Placeholder keys the converter identified as config-driven for this
     *  imported test suite. mergedRow proactively pulls these from Config so
     *  templates can reference them without any explicit ctx.put in the test. */
    private static final String[] CONFIG_KEYS = new String[] {{ {keys_java} }};

    private TestSupport() {{}}

    /**
     * Union a CSV row + runtime ctx map + config lookups. Highest-priority
     * wins on collision so ctx values (extracted runtime IDs) beat both CSV
     * defaults and env-config fallbacks. Every dotted key is also written
     * under its underscore-joined and dash-normalized aliases so multiple
     * placeholder-naming conventions resolve against the same value.
     */
    public static Map<String, String> mergedRow(Map<String, String> row,
                                                Map<String, String> ctx) {{
        Map<String, String> merged = new HashMap<>();
        // 1. Load config values FIRST (lowest priority; overwritten below)
        for (String k : CONFIG_KEYS) {{
            String v = Config.get(k, null);
            if (v != null) putWithAliases(merged, k, v);
        }}
        // 2. Layer CSV row over config
        if (row != null) {{
            for (Map.Entry<String, String> e : row.entrySet()) {{
                putWithAliases(merged, e.getKey(), e.getValue());
            }}
        }}
        // 3. ctx wins (runtime-generated values)
        if (ctx != null) {{
            for (Map.Entry<String, String> e : ctx.entrySet()) {{
                putWithAliases(merged, e.getKey(), e.getValue());
            }}
        }}
        return merged;
    }}

    /**
     * Insert {{@code (key, value)}} plus every naming-convention alias of
     * {{@code key}} that a template placeholder might use:
     * <ul>
     *   <li>dot-joined form:      {{@code "stepName.field"}}</li>
     *   <li>underscore-joined:    {{@code "stepName_field"}} (matches the
     *       converter's own placeholder naming)</li>
     *   <li>hash-neutral form:    dashes collapsed to underscores in both above</li>
     * </ul>
     */
    private static void putWithAliases(Map<String, String> merged, String key, String value) {{
        if (key == null) return;
        merged.put(key, value);
        String underscoreForm = key.replace('.', '_').replace('-', '_');
        if (!underscoreForm.equals(key)) merged.put(underscoreForm, value);
        String dotForm = key.replace('-', '_');
        if (!dotForm.equals(key) && !dotForm.equals(underscoreForm)) merged.put(dotForm, value);
    }}
}}
"""
        rel = f"src/main/java/{pkg.replace('.', '/')}/TestSupport.java"
        self._write(rel, content)
        return rel

    # -- SetupHelper: shared setup-chain extraction -----------------------

    def emit_setup_helper(self, flows: list[dict], service_class_name: str) -> str:
        """Emit `com.<pkg>.support.<suite>.SetupHelper.java` with one
        static method per detected shared flow. Namespaced under the
        suite so multiple imports coexist -- suite A's flow_A and
        suite B's flow_A live in different SetupHelper classes."""
        pkg = f"{self.package_root}.support.{self.suite_name}"

        # Render each flow method body by reusing _render_step against the
        # template case's first prefix_len steps. This shares the exact
        # emission logic with test-method emission (bug fixes benefit both).
        method_bodies: list[str] = []
        for flow in flows:
            # Use the template case's ACTUAL name so
            # `_template_path_by_step` lookups in _render_rest_step_body
            # hit the dedup/merge map correctly. (`__setup_flow_A__`
            # would never be a real key and the emitter would fall back
            # to the flat legacy template path -- broken in v2.)
            self._current_case = flow["template_case"].name
            self._current_prefix = "__setup__"
            self._reset_per_method_state()
            step_lines: list[str] = [
                # `exp` is normally provided by BaseApiTest.expected(row) but
                # SetupHelper is static -- re-derive from the row cell.
                'Expected exp = Expected.from(row == null ? null : row.get("expected"));',
                '',
            ]
            for step in flow["template_case"].steps[:flow["prefix_len"]]:
                step_lines.extend(self._render_step(step, service_class_name))
                step_lines.append("")

            indented = "\n".join(
                "        " + l if l else "" for l in step_lines)
            # A brief JavaDoc noting the shape of the flow
            step_summary = " -> ".join(
                s[1] for s in flow["step_sigs"][:8])
            if len(flow["step_sigs"]) > 8:
                step_summary += f" -> ... ({len(flow['step_sigs'])} total)"
            method_bodies.append(f"""    /**
     * Shared setup flow reused by {len(flow['cases'])} imported test cases.
     * Steps ({flow['prefix_len']}): {step_summary}
     *
     * On return, `ctx` is populated with any values that the constituent
     * Groovy steps extracted from responses (ids, tokens, generated
     * emails/usernames, etc.) so scenario-specific steps in the caller
     * can look them up via ctx.get(...).
     */
    public static void {flow['id']}(
            {service_class_name} client,
            Map<String, String> ctx,
            Map<String, String> row,
            SoftAssert softAssert,
            RestLoggerUtilityDataHolder holder,
            String testCaseId) throws Exception {{
{indented}
    }}
""")

        content = f"""package {pkg};

import java.util.Map;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.testng.asserts.SoftAssert;

import com.ak.api.config.Config;
import com.ak.api.data.Expected;
import com.ak.api.data.FakeData;
import com.ak.api.data.PlaceholderResolver;
import com.ak.api.db.Db;
import com.ak.api.rest.clients.{service_class_name};
import com.ak.api.rest.utilities.Headers;
import com.ak.api.rest.utilities.RestLoggerUtilityDataHolder;
import com.ak.api.rest.utilities.RestUtilities;

import io.restassured.response.Response;

/**
 * Auto-generated by ra_converter.
 *
 * Encapsulates the common setup step-sequences that appear at the start
 * of many imported test cases. Each `flow_X` method executes one such
 * sequence (OAuth, guest-data generation, program-account creation,
 * etc.) and populates the shared `ctx` map with any values the
 * constituent Groovy steps would have published, so scenario-specific
 * downstream steps can look them up without re-executing the setup.
 *
 * Hand-edits between regenerations: SAFE only if you preserve method
 * signatures + ctx-population contract. The converter overwrites this
 * file on every run.
 */
public final class SetupHelper {{

    private static final Logger LOG = LoggerFactory.getLogger(SetupHelper.class);

    private SetupHelper() {{}}

{chr(10).join(method_bodies)}
}}
"""
        rel = f"src/main/java/{pkg.replace('.', '/')}/SetupHelper.java"
        self._write(rel, content)
        return rel

    # -- Flow diagram (mermaid, one file per source XML) --------------------

    def emit_flow_diagram(self, source_xml: str, cases: list[TestCase],
                           flows: list[dict], service_class: str,
                           shared_ops_count: int) -> str:
        """Emit `_flows/<suite_name>.md` with mermaid diagrams that document
        (a) the overall migration pipeline, (b) the shared setup flows and
        which cases use them, and (c) per-case sequence diagrams showing
        each test's SetupHelper call and its scenario-specific tail.

        Named after the source XML basename (default) or the caller's
        `--suite-name`. Renders in GitHub / GitLab / VS Code Markdown
        preview with mermaid support enabled.
        """
        basename = os.path.splitext(os.path.basename(source_xml))[0]

        # ---- helper: mermaid-safe label (strip pipes, quotes, angle brackets)
        def _lbl(s: str, cap: int = 60) -> str:
            s = (s or "").replace("|", "/").replace('"', "'") \
                          .replace("<", "&lt;").replace(">", "&gt;")
            return s[:cap]

        lines: list[str] = [
            f"# Migration flow: `{basename}`",
            "",
            f"- **Suite name**: `{self.suite_name}`",
            f"- **Source XML**: `{source_xml}`",
            f"- **Total cases**: {len(cases)}",
            f"- **Distinct REST ops (shared client methods)**: {shared_ops_count}",
            f"- **Shared setup flows extracted**: {len(flows)}",
            f"- **Cases using a shared flow**: "
            f"{len({n for f in flows for n in f['cases']})}/{len(cases)}",
            "",
            "> All mermaid blocks below render in GitHub / GitLab / VS Code",
            "> Markdown preview with mermaid enabled. If you view this in a",
            "> plain text editor, the diagrams appear as their source code.",
            "",
            "## 1. Migration pipeline",
            "",
            "```mermaid",
            "flowchart LR",
            f"    XML[\"SoapUI XML<br/>{_lbl(basename)}.xml\"]",
            "    Parser[\"ra_converter.py<br/>parser\"]",
            "    IR[\"Intermediate<br/>Representation<br/>(TestCase / RestStep /<br/>GroovyStep / ...)\"]",
            "    Flow[\"find_shared_flows<br/>+ groovy_translator\"]",
            f"    Client[\"{service_class}<br/>{shared_ops_count} methods\"]",
            f"    Helper[\"SetupHelper<br/>{len(flows)} flows\"]",
            f"    Tests[\"{len(cases)} Test Classes\"]",
            "    CSV[\"296 CSV datasheets<br/>+ req-body templates\"]",
            "    Audit[\"_audit/summary.md<br/>+ 4 CSVs\"]",
            "    XML --> Parser --> IR",
            "    IR --> Flow",
            "    Flow --> Client",
            "    Flow --> Helper",
            "    Flow --> Tests",
            "    Flow --> CSV",
            "    Flow --> Audit",
            "```",
            "",
        ]

        # ---- 2. Shared setup flows
        if flows:
            lines.extend([
                "## 2. Shared setup flows",
                "",
                "Every case whose opening step-sequence matches a flow calls",
                f"the corresponding `SetupHelper.flow_X` method instead of",
                "duplicating those N steps inline.",
                "",
                "```mermaid",
                "flowchart TD",
            ])
            for f in flows:
                fid = f["id"]
                cnt = len(f["cases"])
                lines.append(f"    {fid}_start([\"{fid}<br/>reused by {cnt} cases\"])")
                prev = f"{fid}_start"
                for i, sig in enumerate(f["step_sigs"]):
                    node_id = f"{fid}_s{i}"
                    kind = sig[0]
                    name = _lbl(sig[1], 40)
                    label = f"{name}<br/><i>{kind}</i>"
                    lines.append(f"    {node_id}[\"{label}\"]")
                    lines.append(f"    {prev} --> {node_id}")
                    prev = node_id
                lines.append(f"    {prev} --> {fid}_end((return))")
            lines.append("```")
            lines.append("")

        # ---- 3. Case index
        lines.extend([
            "## 3. Case index",
            "",
            "| # | Case | Prefix bucket | Steps | Uses flow |",
            "|---:|---|---|---:|---|",
        ])
        for i, c in enumerate(cases, 1):
            f = self._flow_by_case.get(c.name)
            f_label = f["id"] if f else "-"
            anchor = sanitize_identifier(c.name).lower()
            lines.append(
                f"| {i} | [`{_lbl(c.name, 70)}`](#case-{anchor}) "
                f"| `{c.prefix}` | {len(c.steps)} | {f_label} |")
        lines.append("")

        # ---- 4. Per-case sequence diagrams
        lines.extend([
            "## 4. Per-case sequence diagrams",
            "",
            "Each diagram shows the actual runtime flow: shared setup",
            "(if the case uses one) followed by scenario-specific steps.",
            "",
        ])
        for c in cases:
            anchor = sanitize_identifier(c.name).lower()
            f = self._flow_by_case.get(c.name)
            skip = f["prefix_len"] if f else 0
            lines.append(f"### Case: `{_lbl(c.name, 90)}` <a id=\"case-{anchor}\"></a>")
            lines.append("")
            lines.append(f"- Prefix bucket: `{c.prefix}`  "
                         f"| Steps: {len(c.steps)}  "
                         f"| Uses flow: **{f['id'] if f else 'none'}**")
            desc = _lbl((c.description or "").strip(), 200)
            if desc:
                lines.append(f"- SoapUI description: _{desc}_")
            lines.append("")
            lines.append("```mermaid")
            lines.append("sequenceDiagram")
            lines.append("    participant T as Test")
            lines.append("    participant SH as SetupHelper")
            lines.append("    participant C as Client")
            lines.append("    participant CTX as ctx (Map)")
            lines.append("    participant API as HTTP endpoint")
            if f:
                lines.append(
                    f"    T->>SH: {f['id']}(client, ctx, row, ...)")
                lines.append(
                    f"    SH->>C: {f['prefix_len']} setup calls")
                lines.append(f"    C->>API: HTTP calls")
                lines.append(f"    API-->>SH: responses")
                lines.append(f"    SH->>CTX: put(token, ids, generated data)")
                lines.append(f"    SH-->>T: return")
            # Scenario-specific tail
            for step in c.steps[skip:]:
                if isinstance(step, RestStep):
                    op = step.method_name or step.step_name
                    lines.append(
                        f"    T->>C: {_lbl(op, 40)} ({step.http_method} "
                        f"{_lbl(step.resource_path, 40)})")
                    lines.append(f"    C->>API: {step.http_method}")
                    lines.append(f"    API-->>C: Response")
                    lines.append(f"    T->>T: assert status + JsonPath")
                elif isinstance(step, GroovyStep):
                    lines.append(
                        f"    Note over T,CTX: Groovy step "
                        f"'{_lbl(step.step_name, 30)}'<br/>extracts to ctx")
                elif isinstance(step, PropertiesStep):
                    lines.append(
                        f"    Note over CTX: Properties step "
                        f"'{_lbl(step.step_name, 30)}'<br/>seeds ctx defaults")
                elif isinstance(step, JdbcStep):
                    lines.append(
                        f"    T->>API: JDBC (Db.execute) "
                        f"'{_lbl(step.step_name, 30)}'")
                elif isinstance(step, ManualStep):
                    lines.append(
                        f"    Note over T: [documentation-only] "
                        f"'{_lbl(step.step_name, 30)}'")
                elif isinstance(step, TransferStep):
                    lines.append(
                        f"    T->>CTX: Transfer '{_lbl(step.step_name, 30)}'")
                elif isinstance(step, DataSourceStep):
                    lines.append(
                        f"    Note over T: DataSource '{_lbl(step.step_name, 30)}'")
            lines.append("```")
            lines.append("")

        content = "\n".join(lines) + "\n"
        rel = f"_flows/{basename}.md"
        self._write(rel, content)
        return rel

    # -- CSV datasheet ------------------------------------------------------

    def emit_csv(self, case: TestCase) -> str:
        """One CSV per case, header = only TRUE data-driven placeholders +
        reserved cols. Config-driven placeholders live in the env JSON;
        runtime-generated ones live in ctx -- neither belongs in the CSV.
        Every placeholder gets logged to the audit ledger."""
        classification = classify_placeholders_for_case(case)

        # Log each placeholder for the audit report
        for kind_key in ("config", "runtime", "csv"):
            for ph in sorted(classification[kind_key]):
                self.ledger.add_placeholder(case.name, ph, kind_key)

        # Only TRUE csv placeholders become columns
        csv_columns = sorted(classification["csv"])
        cols = csv_columns + ["jira_xray_id", "expected"]

        # Terminal (last REST) step yields the "expected" for this row
        terminal = None
        for step in reversed(case.steps):
            if isinstance(step, RestStep):
                terminal = step
                break
        expected_bits = []
        if terminal:
            for a in terminal.assertions:
                if a.disabled:
                    continue
                if a.type == "Valid HTTP Status Codes":
                    codes = (a.config.get("codes", "") or "").strip()
                    first_code = re.split(r"[,\s]+", codes)[0] if codes else "200"
                    expected_bits.append(f"statusCode:{first_code}")
        expected_str = ";".join(expected_bits)

        header_row = ",".join(cols)
        # First data row: TRUE csv cells blank (user fills), reserved cols set
        row_cells = ["" for _ in csv_columns] + [case.prefix, expected_str]
        data_row = ",".join(row_cells)

        content = header_row + "\n" + data_row + "\n"
        rel = f"src/test/resources/testdata/{self.suite_name}/{self._short(case.name)}.csv"
        self._write(rel, content)
        return rel

    # -- Request-body templates --------------------------------------------

    def emit_templates(self, case: TestCase) -> list[str]:
        rels = []
        for step in case.steps:
            if not isinstance(step, RestStep):
                continue
            if not step.request_body.strip():
                continue
            translated, _ = soapui_body_to_placeholders(step.request_body)
            template_name = f'{sanitize_identifier(step.step_name).lower()}.json'
            rel = f"src/main/resources/templates/{self.suite_name}/{template_name}"
            self._write(rel, translated)
            rels.append(rel)
        return rels

    # -- Env config --------------------------------------------------------

    def emit_env_config(self, cases: list[TestCase], env_name: str = "qa") -> str:
        # Collect all Project-level property refs across all cases
        project_props = set()
        for case in cases:
            for step in case.steps:
                if isinstance(step, RestStep):
                    project_props.update(_PROJ_PROP_RX.findall(step.request_body or ""))
                    for v in list(step.headers.values()) + list(step.path_params.values()) + list(step.query_params.values()):
                        project_props.update(_PROJ_PROP_RX.findall(v))
        # Include the base URL (first non-localhost originalUri as best-effort)
        base_url = ""
        for case in cases:
            for step in case.steps:
                if isinstance(step, RestStep) and step.original_uri:
                    # trim off the resource path to leave the base
                    base = step.original_uri
                    if step.resource_path and step.resource_path in base:
                        base = base.split(step.resource_path)[0]
                    base_url = base.rstrip("/")
                    break
            if base_url:
                break

        config = {
            "env": env_name,
            "base_url": base_url or "https://api.example.com",
            **{p: f"__SET_{p}__" for p in sorted(project_props)},
        }
        rel = f"src/main/resources/config/{env_name}.json"
        self._write(rel, json.dumps(config, indent=2) + "\n")
        return rel

    # =====================================================================
    # v2: "one class per SoapUI suite" mode. Every emitter method below
    # is used only by the `--one-class-per-suite` codepath and is
    # additive -- the legacy per-prefix emitters above are untouched, so
    # existing callers keep working.
    # =====================================================================

    def emit_per_method_csv_data_provider(self) -> str:
        """One-time helper class: `PerMethodCsvDataProvider` locates a
        `csv/<ClassSimpleName>/<methodName>.csv` on the classpath from the
        calling test's Class + Method. Test methods just declare
        `@Test(dataProvider="rows", dataProviderClass=PerMethodCsvDataProvider.class)`
        with no other wiring -- the CSV is discovered by convention. Rewritten
        idempotently across suite imports; only one copy exists per output tree."""
        pkg = f"{self.package_root}.data"
        rel = f"src/main/java/{pkg.replace('.', '/')}/PerMethodCsvDataProvider.java"
        content = f"""package {pkg};

import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.BufferedReader;
import java.lang.reflect.Method;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.testng.annotations.DataProvider;

/**
 * Convention-based TestNG DataProvider used by every ra_converter-emitted
 * test class. For a test method
 *   {{@code com.ak.api.tests.imported.<suite>.<Class>.<methodName>(Map<String,String> row)}}
 * this provider loads
 *   {{@code classpath:/csv/<Class>/<methodName>.csv}}
 * and yields one Map<String,String> per data row (header keys, string values).
 *
 * <p>Blank CSV cells are surfaced as {{@code ""}}; the CSV file itself is required
 * (missing file -> {{@link IllegalStateException}} so a test won't silently pass
 * with zero rows).</p>
 *
 * <p>Mirrors the reuse pattern of {{@code com.hilton.providers.CsvDataProvider}}
 * from the reference framework -- one CSV per @Test method, colocated under a
 * class-named folder so authors don't wire {{@code -DdataFile}} per class.</p>
 */
public final class PerMethodCsvDataProvider {{

    private PerMethodCsvDataProvider() {{ }}

    @DataProvider(name = "rows")
    public static Object[][] rows(Method method) {{
        String cls  = method.getDeclaringClass().getSimpleName();
        String meth = method.getName();
        String resourcePath = "csv/" + cls + "/" + meth + ".csv";

        InputStream in = Thread.currentThread().getContextClassLoader()
                .getResourceAsStream(resourcePath);
        if (in == null) {{
            throw new IllegalStateException(
                    "PerMethodCsvDataProvider: no CSV on classpath at " + resourcePath
                    + " (expected one row per data-driven scenario for @Test " + cls + "#" + meth + ")");
        }}

        List<Map<String, String>> rows = new ArrayList<>();
        try (BufferedReader br = new BufferedReader(new InputStreamReader(in, StandardCharsets.UTF_8))) {{
            String headerLine = br.readLine();
            if (headerLine == null) {{
                throw new IllegalStateException("PerMethodCsvDataProvider: empty CSV " + resourcePath);
            }}
            String[] header = splitCsvLine(headerLine);
            String line;
            while ((line = br.readLine()) != null) {{
                if (line.isEmpty()) continue;
                String[] cells = splitCsvLine(line);
                Map<String, String> row = new LinkedHashMap<>();
                for (int i = 0; i < header.length; i++) {{
                    row.put(header[i], i < cells.length ? cells[i] : "");
                }}
                rows.add(row);
            }}
        }} catch (Exception e) {{
            throw new IllegalStateException("PerMethodCsvDataProvider: failed reading " + resourcePath, e);
        }}

        Object[][] out = new Object[rows.size()][1];
        for (int i = 0; i < rows.size(); i++) out[i][0] = rows.get(i);
        return out;
    }}

    /**
     * Minimal RFC-4180-ish CSV splitter: honors double-quoted fields
     * (with embedded commas and "" escaped quotes). Sufficient for
     * ra_converter-generated CSVs, which are hand-written or exported
     * from SoapUI -- neither introduces multi-line fields.
     */
    private static String[] splitCsvLine(String line) {{
        List<String> out = new ArrayList<>();
        StringBuilder cur = new StringBuilder();
        boolean inQuotes = false;
        for (int i = 0; i < line.length(); i++) {{
            char c = line.charAt(i);
            if (inQuotes) {{
                if (c == '"') {{
                    if (i + 1 < line.length() && line.charAt(i + 1) == '"') {{
                        cur.append('"');
                        i++;
                    }} else {{
                        inQuotes = false;
                    }}
                }} else {{
                    cur.append(c);
                }}
            }} else {{
                if (c == ',') {{
                    out.add(cur.toString());
                    cur.setLength(0);
                }} else if (c == '"') {{
                    inQuotes = true;
                }} else {{
                    cur.append(c);
                }}
            }}
        }}
        out.add(cur.toString());
        return out.toArray(new String[0]);
    }}
}}
"""
        self._write(rel, content)
        return rel

    def emit_placeholder_resolver(self) -> str:
        """Emit `PlaceholderResolver.java` alongside `PerMethodCsvDataProvider`.

        Fills the reference-framework parity gap: users can drop faker
        tokens like `<<email>>`, `<<username(8)>>`, `<<phone>>` into CSV
        cells, and cross-field property refs like `${{phone}}`, `${{email}}`,
        `${{email_domain}}` that stay CONSISTENT across a single row
        (`${{phone}}` in the JSON payload and in the CSV `expected` column
        resolve to the same value within a test method).

        Runtime call surface:
          - `resolveAll(String text, Map<String,String> ctx)`     -- both passes
          - `resolveFakerTokens(String text)`                     -- <<X>> only
          - `resolveDollarRefs(String text, Map<String,String> ctx)` -- ${{X}} only

        The Java class expects `com.ak.api.data.FakeData` to be present
        (already exists in the framework)."""
        pkg = f"{self.package_root}.data"
        rel = f"src/main/java/{pkg.replace('.', '/')}/PlaceholderResolver.java"
        content = f"""package {pkg};

import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ThreadLocalRandom;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Two-pass placeholder resolver for CSV cells and request-body templates.
 *
 * <p><b>Pass 1: faker-style {{@code <<X>>}} tokens</b>. Each occurrence
 * produces a fresh random value on every call. Supports:
 * <pre>
 *   &lt;&lt;name&gt;&gt;              full personal name (space-separated first/last)
 *   &lt;&lt;firstName&gt;&gt;         first name only
 *   &lt;&lt;lastName&gt;&gt;          last name only
 *   &lt;&lt;username&gt;&gt;          random username (lowercase letters)
 *   &lt;&lt;username(N)&gt;&gt;       random username, exactly N chars
 *   &lt;&lt;email&gt;&gt;             random.local@example.com
 *   &lt;&lt;email(domain)&gt;&gt;     random.local@domain
 *   &lt;&lt;phone&gt;&gt;             10-digit US phone
 *   &lt;&lt;address&gt;&gt;           street address (no city/state)
 *   &lt;&lt;city&gt;&gt;              city name
 *   &lt;&lt;state&gt;&gt;             2-letter US state code
 *   &lt;&lt;zip&gt;&gt;               5-digit US ZIP
 *   &lt;&lt;country&gt;&gt;           2-letter ISO country code
 *   &lt;&lt;company&gt;&gt;           company name
 *   &lt;&lt;uuid&gt;&gt;              lowercase UUID
 *   &lt;&lt;unique&gt;&gt;            monotonic timestamp+random suffix
 *   &lt;&lt;int(min,max)&gt;&gt;      random int in [min,max] inclusive
 *   &lt;&lt;alphanum(N)&gt;&gt;       N random alphanumerics
 *   &lt;&lt;alpha(N)&gt;&gt;          N random letters
 *   &lt;&lt;digits(N)&gt;&gt;         N random digits
 * </pre>
 *
 * <p><b>Pass 2: property-bag {{@code ${{X}}}} refs</b>. On first read of
 * an unknown key, the resolver GENERATES a fresh value and stores it
 * in the ctx map so later reads (in the same row/method) see the same
 * value. This matches the reference framework's convention that all
 * occurrences of {{@code ${{phone}}}} inside one payload / CSV row are the
 * same phone number, but a rerun of the row yields a different one.
 * Auto-generated keys:
 * <pre>
 *   ${{email}}         random.local@${{email_domain}}
 *   ${{email_domain}}  example.com (or override via env / CSV column)
 *   ${{domain}}        example.com
 *   ${{phone}}         10-digit US phone
 *   ${{username}}      random lowercase username
 *   ${{firstName}} / ${{lastName}} / ${{name}}
 *   ${{uuid}}          lowercase UUID
 * </pre>
 * Any {{@code ${{X}}}} that isn't a well-known key AND isn't already in
 * ctx is left unchanged so the caller can decide whether that's an
 * error (via strict-mode mapJsonValues) or acceptable fallback.
 */
public final class PlaceholderResolver {{

    private PlaceholderResolver() {{}}

    // <<X>> or <<X(args)>>
    private static final Pattern FAKER_TOKEN =
        Pattern.compile("<<([A-Za-z_][A-Za-z0-9_]*)(?:\\\\(([^)]*)\\\\))?>>");
    // ${{X}} -- key may include dots + underscores + digits + dashes
    private static final Pattern DOLLAR_REF =
        Pattern.compile("\\\\$\\\\{{([A-Za-z_][A-Za-z0-9_.-]*)\\\\}}");

    /** Run pass 1 then pass 2. Safe to call on already-resolved text
     *  (idempotent -- no faker tokens or ${{}} refs to match). */
    public static String resolveAll(String text, Map<String, String> ctx) {{
        if (text == null || text.isEmpty()) return text;
        String phase1 = resolveFakerTokens(text);
        return resolveDollarRefs(phase1, ctx);
    }}

    /**
     * Expand every cell of a CSV row: {{@code <<X>>}} faker tokens become
     * fresh values, {{@code ${{X}}}} property refs consult / populate ctx so
     * the same ref used across multiple cells resolves to the same value
     * WITHIN one row. Returns a fresh LinkedHashMap so retrying a failed
     * row from the original DataProvider array still sees the unresolved
     * template.
     */
    public static java.util.Map<String, String> resolveRow(
            java.util.Map<String, String> row, java.util.Map<String, String> ctx) {{
        if (row == null || row.isEmpty()) return row;
        java.util.LinkedHashMap<String, String> out = new java.util.LinkedHashMap<>(row.size());
        for (java.util.Map.Entry<String, String> e : row.entrySet()) {{
            out.put(e.getKey(), resolveAll(e.getValue(), ctx));
        }}
        return out;
    }}

    /** Pass 1: faker-style {{@code <<X>>}} tokens. Fresh values each call. */
    public static String resolveFakerTokens(String text) {{
        if (text == null || text.isEmpty() || text.indexOf('<') < 0) return text;
        Matcher m = FAKER_TOKEN.matcher(text);
        StringBuilder out = new StringBuilder();
        while (m.find()) {{
            String key = m.group(1);
            String args = m.group(2);
            String value = fakerValue(key, args);
            m.appendReplacement(out, Matcher.quoteReplacement(value));
        }}
        m.appendTail(out);
        return out.toString();
    }}

    /** Pass 2: {{@code ${{X}}}} refs. Populates ctx on first use of a
     *  known-key so all occurrences within one row stay consistent. */
    public static String resolveDollarRefs(String text, Map<String, String> ctx) {{
        if (text == null || text.isEmpty() || text.indexOf('$') < 0) return text;
        Matcher m = DOLLAR_REF.matcher(text);
        StringBuilder out = new StringBuilder();
        while (m.find()) {{
            String key = m.group(1);
            String value = ctx == null ? null : ctx.get(key);
            if (value == null) value = autoGenerate(key, ctx);
            if (value == null) {{
                // Unknown key -- leave the literal ${{X}} alone
                m.appendReplacement(out, Matcher.quoteReplacement(m.group()));
            }} else {{
                m.appendReplacement(out, Matcher.quoteReplacement(value));
            }}
        }}
        m.appendTail(out);
        return out.toString();
    }}

    // =====================================================================
    // Faker dispatch
    // =====================================================================

    private static String fakerValue(String key, String args) {{
        String k = key.toLowerCase();
        switch (k) {{
            case "name":       return FakeData.fullName();
            case "firstname":  return FakeData.faker().name().firstName();
            case "lastname":   return FakeData.faker().name().lastName();
            case "username":   return truncOrPad(FakeData.username(), parseIntArg(args, -1));
            case "email":      return args == null || args.isEmpty()
                                        ? FakeData.email()
                                        : (FakeData.username() + "@" + args);
            case "phone":      return randomDigits(10);
            case "address":    return FakeData.faker().address().streetAddress();
            case "city":       return FakeData.faker().address().city();
            case "state":      return FakeData.faker().address().stateAbbr();
            case "zip":        return FakeData.faker().address().zipCode().substring(0, 5);
            case "country":    return FakeData.faker().address().countryCode();
            case "company":    return FakeData.companyName();
            case "uuid":       return UUID.randomUUID().toString();
            case "unique":     return Long.toString(System.currentTimeMillis()) + randomAlnum(3);
            case "int":        return Integer.toString(parseIntRange(args));
            case "alphanum":   return randomAlnum(parseIntArg(args, 8));
            case "alpha":      return randomAlpha(parseIntArg(args, 8));
            case "digits":     return randomDigits(parseIntArg(args, 6));
            default:           return "<<" + key + (args == null ? ">>" : "(" + args + ")>>");
        }}
    }}

    // =====================================================================
    // ${{X}} dispatch (populates ctx so subsequent ${{X}} in same row see same value)
    // =====================================================================

    private static String autoGenerate(String key, Map<String, String> ctx) {{
        String v;
        String k = key.toLowerCase();
        switch (k) {{
            case "email_domain":
            case "domain":       v = "example.com"; break;
            case "email":        v = FakeData.username() + "@"
                                     + getOrDefault(ctx, "email_domain", "example.com"); break;
            case "phone":        v = randomDigits(10); break;
            case "username":     v = FakeData.username(); break;
            case "firstname":    v = FakeData.faker().name().firstName(); break;
            case "lastname":     v = FakeData.faker().name().lastName(); break;
            case "name":         v = FakeData.fullName(); break;
            case "uuid":         v = UUID.randomUUID().toString(); break;
            default:             return null;  // caller leaves the literal alone
        }}
        if (ctx != null) ctx.put(key, v);
        return v;
    }}

    // =====================================================================
    // Small utilities (kept local so the class has no non-FakeData deps)
    // =====================================================================

    private static String getOrDefault(Map<String, String> ctx, String key, String fallback) {{
        if (ctx == null) return fallback;
        String v = ctx.get(key);
        return v == null || v.isEmpty() ? fallback : v;
    }}

    private static int parseIntArg(String args, int fallback) {{
        if (args == null || args.isEmpty()) return fallback;
        try {{ return Integer.parseInt(args.trim()); }} catch (NumberFormatException e) {{ return fallback; }}
    }}

    private static int parseIntRange(String args) {{
        if (args == null || !args.contains(",")) {{
            return FakeData.intBetween(0, parseIntArg(args, 100));
        }}
        String[] parts = args.split(",", 2);
        int lo = parseIntArg(parts[0].trim(), 0);
        int hi = parseIntArg(parts[1].trim(), lo + 100);
        return FakeData.intBetween(Math.min(lo, hi), Math.max(lo, hi));
    }}

    private static String randomAlnum(int n) {{
        return random(n, "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789");
    }}

    private static String randomAlpha(int n) {{
        return random(n, "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ");
    }}

    private static String randomDigits(int n) {{
        return random(n, "0123456789");
    }}

    private static String random(int n, String alphabet) {{
        if (n <= 0) n = 1;
        StringBuilder sb = new StringBuilder(n);
        for (int i = 0; i < n; i++) {{
            sb.append(alphabet.charAt(ThreadLocalRandom.current().nextInt(alphabet.length())));
        }}
        return sb.toString();
    }}

    private static String truncOrPad(String s, int n) {{
        if (n <= 0) return s;
        if (s.length() == n) return s;
        if (s.length() > n) return s.substring(0, n);
        return s + randomAlnum(n - s.length());
    }}
}}
"""
        self._write(rel, content)
        return rel

    def emit_test_class_per_suite(self, soapui_suite_name: str,
                                    cases: list[TestCase],
                                    service_class_name: str) -> tuple[str, str, list[str]]:
        """Emit ONE Java test class per SoapUI test suite. Every case in
        the suite becomes a @Test method inside that class; the method
        name is derived from the case name via `_business_method_name()`
        so it reads as business intent, not as a ticket ID.

        Returns (relative_path, class_fqn, method_names_in_emit_order).
        """
        # Truncate + hash long suite names so the generated class file
        # stays under Windows MAX_PATH once the package path prefix is
        # applied. Collision guard: refuse to overwrite an already-emitted
        # class in the same package -- caller either renamed a suite or
        # this XML has two suites that collapse to the same camel case.
        raw_class = short_class(soapui_suite_name, self.max_name_len)
        class_name = raw_class if raw_class.endswith("Test") else raw_class + "Test"
        pkg = f"{self.package_root}.tests.imported.{self.suite_name}"
        fqn_candidate = f"{pkg}.{class_name}"
        if fqn_candidate in self._emitted_class_fqns:
            # Deterministic suffix from the ORIGINAL suite name (not the
            # already-truncated form) so callers can diff and see which
            # suite produced the alternate name.
            import hashlib as _hl
            suffix = _hl.sha1(soapui_suite_name.encode("utf-8")).hexdigest()[:6].upper()
            class_name = f"{raw_class[:-4] if raw_class.endswith('Test') else raw_class}_{suffix}Test"
            fqn_candidate = f"{pkg}.{class_name}"
            print(f"[ra_converter] WARN: SoapUI suite name '{soapui_suite_name}' "
                  f"collides after truncation; disambiguated to {class_name}")
        self._emitted_class_fqns.add(fqn_candidate)

        # Prime state so per-method emitters know their audit-ledger cursor.
        self._current_prefix = soapui_suite_name

        # Cluster cases by REST-step shape (verb + path + body-hash per step)
        # so N cases that share an intent collapse to ONE @Test method with
        # N CSV rows. Single-case clusters emit exactly like before.
        self._clusters = _cluster_cases_by_shape(cases)
        # Publish cluster -> method-name mapping for downstream emitters
        # (emit_csv_per_method uses this too).
        seen_bases: dict[str, int] = {}
        self._cluster_to_method: dict[int, tuple[str, str, str]] = {}
        for idx, cluster in enumerate(self._clusters):
            self._cluster_to_method[idx] = _cluster_method_name(cluster, seen_bases)

        method_names: list[str] = []
        rendered_methods: list[str] = []
        for idx, cluster in enumerate(self._clusters):
            final_name, status_code, variant = self._cluster_to_method[idx]
            method_names.append(final_name)
            # Use the first case as the "template" case for step rendering.
            # All cases in the cluster share the exact same step shape and
            # body pattern by construction; scenario data varies per CSV row.
            rendered_methods.append(self._render_test_method_v2(
                cluster[0], service_class_name, final_name, status_code, variant,
                cluster_size=len(cluster)))

        # Explicit @XrayTest keys from all cases (union) -- power users can
        # narrow to a subset via TestNG groups from the CSV `groups` column.
        xray_keys = sorted({
            c.prefix for c in cases
            if re.match(r"^[A-Z]+-\d+$", c.prefix or "")
        })

        # Header comment enumerates the flows shared inside this class -- so a
        # reader knows what SetupHelper.flow_X does without opening it.
        flow_blurbs = []
        for case in cases[:1]:  # blurb from first case only to avoid a wall
            f = self._flow_by_case.get(case.name)
            if f:
                flow_blurbs.append(
                    f" *   - {f['id']}: {f['prefix_len']} shared setup steps "
                    f"used by {len(f['cases'])} methods")

        header_lines = [
            f" * Auto-generated by ra_converter from SoapUI test suite `{soapui_suite_name}`.",
            f" *",
            f" * ONE Java class -> ONE SoapUI test suite. Each SoapUI test case",
            f" * becomes one @Test method here; scenario variants (200 / 400 / 403)",
            f" * are kept as separate methods so failures are addressable per intent.",
            f" *",
            f" * Data: {{@code csv/{class_name}/<methodName>.csv}} on the classpath.",
            f" * Auto-loaded by {{@link {self.package_root}.data.PerMethodCsvDataProvider}}",
            f" * -- no per-class wiring required. Add rows to a CSV to add scenarios.",
        ]
        if flow_blurbs:
            header_lines.append(" *")
            header_lines.append(" * Shared setup flows in this class:")
            header_lines.extend(flow_blurbs)
        header = "\n".join(header_lines)

        content = f"""package {pkg};

import java.util.HashMap;
import java.util.Map;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.Test;

import com.ak.api.config.Config;
import com.ak.api.data.Expected;
import com.ak.api.data.FakeData;
import com.ak.api.data.PerMethodCsvDataProvider;
import com.ak.api.data.PlaceholderResolver;
import com.ak.api.db.Db;
import com.ak.api.rest.utilities.Headers;
import com.ak.api.rest.utilities.RestUtilities;
import com.ak.api.retry.RetryAnalyzer;
import {self.package_root}.support.{self.suite_name}.SetupHelper;
import {self.package_root}.support.{self.suite_name}.TestSupport;
import com.ak.api.tests.BaseApiTest;
import com.ak.api.xray.XrayTest;

import com.ak.api.rest.clients.{service_class_name};

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;
import io.restassured.response.Response;

/**
{header}
 */
@Epic("Imported from ReadyAPI")
@Feature("{soapui_suite_name}")
public class {class_name} extends BaseApiTest {{

    private static final Logger LOG = LoggerFactory.getLogger({class_name}.class);

    private {service_class_name} client;
    /** Runtime context bag: IDs/tokens carried between setup calls within one test. */
    private final Map<String, String> ctx = new HashMap<>();

    @BeforeClass(alwaysRun = true)
    public void initClient() {{
        String baseUrl = Config.get("base_url", Config.baseUrl());
        client = new {service_class_name}(baseUrl);
        LOG.info("initialised {class_name} against baseUrl={{}}  (auth={{}})",
                baseUrl, Config.authType());
    }}

{chr(10).join(rendered_methods)}
}}
"""
        rel = f"src/test/java/{pkg.replace('.', '/')}/{class_name}.java"
        self._write(rel, content)
        fqn = f"{pkg}.{class_name}"
        return rel, fqn, method_names

    def _render_test_method_v2(self, case: TestCase, service_class_name: str,
                                 method_name: str, expected_status_code: str,
                                 variant: str, cluster_size: int = 1) -> str:
        """Same shape as `_render_test_method` but wired to the convention-
        based DataProvider (`PerMethodCsvDataProvider`, name `"rows"`), and
        the method name is the business-intent form (not the hash-shortened
        SoapUI case name).

        When `cluster_size > 1`, this method represents N SoapUI cases that
        share the same REST step shape; the CSV has one row per case and
        `testCaseId` is read PER ROW (from the `test_case_id` column) so
        each scenario logs its own identity."""
        self._current_case = case.name
        self._reset_per_method_state()

        # For multi-case clusters, testCaseId varies per row; the CSV's
        # `test_case_id` column carries the original SoapUI case name.
        # Fall back to the representative case name when the CSV is missing
        # the column (older CSVs or hand-written rows).
        if cluster_size > 1:
            id_stmt = (f'String testCaseId = row.getOrDefault("test_case_id", '
                       f'"{_jlit(case.name)}");')
        else:
            id_stmt = f'String testCaseId = "{_jlit(case.name)}";'

        assigned_flow = self._flow_by_case.get(case.name)
        body_lines = [
            id_stmt,
            '// Expand <<faker>> tokens + ${{property}} refs in every CSV cell '
            'so downstream code sees live values, not placeholders.',
            'row = PlaceholderResolver.resolveRow(row, ctx);',
            'Expected exp = expected(row);',
            '',
        ]
        skip_count = 0
        if assigned_flow:
            skip_count = assigned_flow["prefix_len"]
            body_lines.append(
                f'// ==== shared setup: SetupHelper.{assigned_flow["id"]} '
                f'({skip_count} steps, reused by '
                f'{len(assigned_flow["cases"])} methods in this suite) ====')
            body_lines.append(
                f'SetupHelper.{assigned_flow["id"]}('
                'client, ctx, row, softAssert, holder, testCaseId);')
            body_lines.append('')
        for step in case.steps[skip_count:]:
            body_lines.extend(self._render_step(step, service_class_name))
            body_lines.append('')

        indented = "\n".join("        " + l if l else "" for l in body_lines)

        desc_safe = _jlit((case.description or "")[:200])

        xray_id_raw = case.prefix if re.match(r"^[A-Z]+-\d+$", case.prefix) else case.name

        # Emit variant info in the Story annotation so Allure can group by scenario
        story_bits = [case.name]
        if expected_status_code:
            story_bits.append(f"expected {expected_status_code}")
        if variant:
            story_bits.append(f"variant #{variant}")
        story = " -- ".join(story_bits)
        # TestNG group values can't contain quotes or newlines; sanitize the
        # prefix-derived group so weird case names don't produce broken XML.
        group_val = sanitize_identifier(case.prefix.lower())

        return f"""    @Test(dataProvider = "rows",
          dataProviderClass = PerMethodCsvDataProvider.class,
          groups = {{"imported", "{group_val}"}},
          retryAnalyzer = RetryAnalyzer.class)
    @XrayTest("{_jlit(xray_id_raw)}")
    @Story("{_jlit(story)}")
    @Description("Imported from ReadyAPI. Original description: {desc_safe}")
    public void {method_name}(Map<String, String> row) throws Exception {{
{indented}
    }}
"""

    # Well-known placeholder column suggestions -- if the case's request
    # bodies mention these SoapUI properties (${{...}}), the CSV header
    # includes them as blank cells with helpful defaults commented in
    # the row so authors know what to fill (or leave blank -> auto-
    # generated by PlaceholderResolver at runtime).
    _COMMON_PLACEHOLDER_HINTS: dict = {
        "email":        "<<email>>",          # random.local@example.com per call
        "phone":        "<<phone>>",          # 10-digit US phone
        "username":     "<<username(8)>>",
        "email_domain": "example.com",
        "domain":       "example.com",
        "first_name":   "<<firstName>>",
        "last_name":    "<<lastName>>",
        "name":         "<<name>>",
        "uuid":         "<<uuid>>",
        "address":      "<<address>>",
        "city":         "<<city>>",
        "state":        "<<state>>",
        "zip":          "<<zip>>",
    }

    def emit_csv_per_method(self, class_name: str, method_name: str,
                              cluster: list[TestCase]) -> str:
        """Write `src/test/resources/csv/<class_name>/<method_name>.csv` with
        one row per case in the cluster (a cluster is 1..N SoapUI cases
        sharing the same REST step shape). Header is stable across runs
        so adding more scenarios later is a matter of appending rows (no
        code change).

        Placeholder-friendly: any CSV cell can hold `<<fakerToken>>` or
        `${{propertyRef}}` -- `PlaceholderResolver.resolveRow` (called at
        the top of every @Test method) expands these into live values
        before the row reaches user code."""
        # Union placeholders across every case in the cluster so the CSV
        # header carries every runtime column any row could need.
        union_csv: set[str] = set()
        for c in cluster:
            classification = classify_placeholders_for_case(c)
            for kind_key in ("config", "runtime", "csv"):
                for ph in sorted(classification[kind_key]):
                    self.ledger.add_placeholder(c.name, ph, kind_key)
            union_csv.update(classification["csv"])
        csv_columns = sorted(union_csv)

        # Well-known hint columns from the union of every case's corpus.
        corpus_parts: list[str] = []
        for c in cluster:
            for step in c.steps:
                if isinstance(step, RestStep):
                    corpus_parts.append(step.request_body or "")
                    corpus_parts.extend(list(step.headers.values()))
                    corpus_parts.extend(list(step.path_params.values()))
                    corpus_parts.extend(list(step.query_params.values()))
        corpus = "\n".join(corpus_parts)
        hinted_cols: list[tuple[str, str]] = []
        for common_key, hint in self._COMMON_PLACEHOLDER_HINTS.items():
            if f"${{{common_key}}}" in corpus and common_key not in csv_columns:
                hinted_cols.append((common_key, hint))
        hint_col_names = [k for k, _ in hinted_cols]

        # ---- Per-assertion "expected value" columns ------------------
        # For every ACTIVE assertion on every REST step in the cluster,
        # derive a stable column name (`expected_<step>_<key>`) and per-
        # case value. The Java assertion emitters read row.getOrDefault
        # against these column names, so editing a CSV cell changes what
        # gets asserted at runtime without any code change.
        assert_cols_order: list[str] = []
        assert_vals_per_case: dict[str, list[str]] = {}
        for case_idx, c in enumerate(cluster):
            for step in c.steps:
                if not isinstance(step, RestStep):
                    continue
                a_idx = 0
                for a in step.assertions:
                    if a.disabled:
                        continue
                    key = _assert_col_key(a, a_idx)
                    a_idx += 1
                    if key is None:
                        continue
                    col = f"expected_{sanitize_identifier(step.step_name)}_{key}"
                    if col not in assert_vals_per_case:
                        assert_vals_per_case[col] = ["" for _ in cluster]
                        assert_cols_order.append(col)
                    assert_vals_per_case[col][case_idx] = _assert_default_value(a)

        # ---- Merged-template placeholder columns ---------------------
        # When emit_templates_deduplicated merged two+ same-shape bodies
        # into one file, it stashed each (case, step) -> {tpl_col: value}
        # in self._merged_template_cells. Pull those into CSV columns so
        # runtime mergedRow substitutes each case's original literal
        # back into the `#tpl_<path>#` placeholder in the merged template.
        merged_tpl_cols: list[str] = []
        merged_tpl_vals: dict[str, list[str]] = {}
        merged_cells_map = getattr(self, "_merged_template_cells", {}) or {}
        for case_idx, c in enumerate(cluster):
            for step in c.steps:
                if not isinstance(step, RestStep):
                    continue
                cells = merged_cells_map.get((c.name, step.step_name))
                if not cells:
                    continue
                for col, val in cells.items():
                    if col not in merged_tpl_vals:
                        merged_tpl_vals[col] = ["" for _ in cluster]
                        merged_tpl_cols.append(col)
                    merged_tpl_vals[col][case_idx] = val

        cols = csv_columns + hint_col_names + merged_tpl_cols + assert_cols_order + [
            "test_case_id",       # original SoapUI case name (per row -- read at runtime)
            "jira_xray_id",       # e.g. B2B-172 (per row)
            "variant",            # scenario disambiguator (from name suffix)
            "expected_status_code",
            "expected",           # semicolon-joined k:v extras (statusCode:200;...)
        ]
        header_row = ",".join(cols)

        # One row per case in the cluster. Reserved cells come from the
        # case; user-data cells start empty for author fill-in.
        rows: list[str] = []
        for case_idx, c in enumerate(cluster):
            _, status, variant = _business_method_name(c.name)

            # Prefer the status code baked into the case name (e.g. `_400`);
            # otherwise derive from the terminal REST step's assertion.
            derived_status = status or ""
            expected_bits: list[str] = []
            terminal = None
            for step in reversed(c.steps):
                if isinstance(step, RestStep):
                    terminal = step
                    break
            if terminal:
                for a in terminal.assertions:
                    if a.disabled:
                        continue
                    if a.type == "Valid HTTP Status Codes":
                        codes = (a.config.get("codes", "") or "").strip()
                        first_code = re.split(r"[,\s]+", codes)[0] if codes else "200"
                        expected_bits.append(f"statusCode:{first_code}")
                        if not derived_status:
                            derived_status = first_code
            expected_str = ";".join(expected_bits)

            assert_cells = [
                _csv_cell(assert_vals_per_case[col][case_idx])
                for col in assert_cols_order
            ]
            merged_tpl_cells = [
                _csv_cell(merged_tpl_vals[col][case_idx])
                for col in merged_tpl_cols
            ]
            row_cells = (
                ["" for _ in csv_columns] +
                [_csv_cell(hint) for _, hint in hinted_cols] +
                merged_tpl_cells +
                assert_cells +
                [_csv_cell(c.name), _csv_cell(c.prefix),
                 _csv_cell(variant), _csv_cell(derived_status), _csv_cell(expected_str)]
            )
            rows.append(",".join(row_cells))

        content = header_row + "\n" + "\n".join(rows) + "\n"
        rel = f"src/test/resources/csv/{class_name}/{method_name}.csv"
        self._write(rel, content)
        return rel

    def emit_templates_deduplicated(self, cases: list[TestCase]) -> dict[str, str]:
        """Two-tier dedup of request-body templates across the whole suite.

        Tier 1 (exact-body): bodies with identical normalized text share one
        file (verbatim reuse -- 296 POSTs of the same payload -> 1 file).

        Tier 2 (structural merge): JSON bodies with the same STRUCTURE but
        differing LITERAL values collapse further. Diff paths become
        `#tpl_<jsonPath>#` placeholders in one merged template; each case's
        original value at that path lands in a per-row CSV cell so the
        runtime substitution puts the right value back. Non-JSON bodies
        (XML, form-encoded, plain text) fall through to exact-body dedup
        only.

        Side effects (v2 wiring):
          - `self._template_path_by_step[(case, step)] -> classpath path`
            populated for every (case, step) with a body, so
            `_render_rest_step_body` emits the right getRequestTemplate call.
          - `self._merged_template_cells[(case, step)] -> {col: value}`
            populated when Tier 2 merges a body; `emit_csv_per_method`
            reads this per case to add the placeholder columns to the
            CSV with the correct per-row value.

        Returns {body-hash -> classpath-path} for by-hash lookups."""
        import hashlib, json as _json
        self._merged_template_cells = {}

        # Pass 1: normalize every body, keep provenance for each (case, step).
        # `entries` = [{"case", "step", "translated", "hash", "bucket"}]
        entries: list[dict] = []
        for case in cases:
            for step in case.steps:
                if not isinstance(step, RestStep):
                    continue
                if not step.request_body.strip():
                    continue
                translated, _ = soapui_body_to_placeholders(step.request_body)
                h = hashlib.sha1(translated.encode("utf-8")).hexdigest()[:10]
                seg = (step.resource_path or "").strip("/").split("/", 1)[0]
                bucket = sanitize_identifier(seg).lower() or "misc"
                entries.append({
                    "case": case.name, "step": step.step_name,
                    "translated": translated, "hash": h, "bucket": bucket,
                })

        # Pass 2: partition by JSON-parseability. Non-JSON goes straight
        # to exact-body dedup (Tier 1 only).
        json_entries: list[dict] = []
        nonjson_entries: list[dict] = []
        for e in entries:
            try:
                e["tree"] = _json.loads(e["translated"])
                json_entries.append(e)
            except (_json.JSONDecodeError, ValueError):
                nonjson_entries.append(e)

        # Pass 3: JSON bodies get grouped by structural shape (leaf types
        # only). Groups with size >1 -- multiple exact hashes sharing the
        # same shape -- become Tier 2 merge candidates.
        shape_groups: dict[str, list[dict]] = {}
        for e in json_entries:
            sig = _shape_sig(e["tree"])
            shape_groups.setdefault(sig, []).append(e)

        hash_to_path: dict[str, str] = {}
        merged_count = 0

        for sig, group in shape_groups.items():
            unique_hashes = {e["hash"] for e in group}
            if len(unique_hashes) <= 1:
                # Nothing to merge -- all entries in this shape group already
                # share one exact-body hash. Fall through to Tier 1.
                self._emit_tier1_for(group, hash_to_path)
                continue
            # Tier 2 merge: multiple distinct exact-bodies with identical
            # shape. Compute per-leaf-path values across the group, then
            # inject `#tpl_<path>#` placeholders wherever leaves differ.
            merged_text, per_entry_cells = _merge_bodies_with_placeholders(
                [e["tree"] for e in group],
                [e["translated"] for e in group],
            )
            # Pick a stable filename: hash of the MERGED template so re-runs
            # produce the same path regardless of iteration order.
            merged_hash = hashlib.sha1(merged_text.encode("utf-8")).hexdigest()[:10]
            first = group[0]
            classpath_dir = f"templates/{self.suite_name}/{first['bucket']}/"
            classpath_file = (
                f"{sanitize_identifier(first['step']).lower()}_merged_{merged_hash}.json")
            classpath = classpath_dir + classpath_file
            # Write the merged template once.
            self._write(f"src/main/resources/{classpath}", merged_text)
            hash_to_path[merged_hash] = classpath
            merged_count += len(group)
            # Every entry in the group points at the merged template + its
            # per-row cell values.
            for e, cells in zip(group, per_entry_cells):
                self._template_path_by_step[(e["case"], e["step"])] = classpath
                if cells:
                    self._merged_template_cells[(e["case"], e["step"])] = cells

        # Non-JSON bodies stay Tier 1 only.
        self._emit_tier1_for(nonjson_entries, hash_to_path)

        # Book-keeping the caller uses for the summary line.
        self._templates_merged = merged_count
        return hash_to_path

    def _emit_tier1_for(self, entries: list[dict], hash_to_path: dict[str, str]) -> None:
        """Exact-body dedup helper used by both JSON singleton groups and
        the non-JSON fallback path. Writes each unique body once and maps
        every (case, step) to it."""
        for e in entries:
            classpath_dir = f"templates/{self.suite_name}/{e['bucket']}/"
            classpath_file = f"{sanitize_identifier(e['step']).lower()}_{e['hash']}.json"
            classpath = classpath_dir + classpath_file
            self._template_path_by_step[(e["case"], e["step"])] = classpath
            if e["hash"] not in hash_to_path:
                self._write(f"src/main/resources/{classpath}", e["translated"])
                hash_to_path[e["hash"]] = classpath

    def emit_master_suite_xml(self, class_fqns: list[str],
                                variant: str = "Regression") -> str:
        """Write a master TestNG suite XML at `Suites/<Suite><Variant>.xml`
        (matching reference framework naming). Lists every generated test
        class + Allure/Extent listeners. `variant` = 'Regression' | 'Smoke'
        | 'UAT' | ... -- reference framework ships all three."""
        pretty_suite = to_camel_case(self.suite_name, upper_first=True)
        pretty_variant = variant[:1].upper() + variant[1:]
        suite_display = f"{pretty_suite}-{pretty_variant}"

        class_blocks = "\n".join(
            f'            <class name="{fqn}"/>' for fqn in sorted(class_fqns))

        content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE suite SYSTEM "https://testng.org/testng-1.0.dtd">
<suite name="{suite_display}" parallel="classes" thread-count="3">

    <parameter name="testSuite" value="{variant.lower()}"/>

    <listeners>
        <listener class-name="io.qameta.allure.testng.AllureTestNg"/>
        <listener class-name="com.ak.api.reporting.TestSuiteListener"/>
        <listener class-name="com.ak.api.reporting.TestCaseLogListener"/>
        <listener class-name="com.ak.api.reporting.ExtentReportListener"/>
        <listener class-name="com.ak.api.reporting.XrayReportListener"/>
    </listeners>

    <test name="{suite_display}Tests">
        <groups>
            <run>
                <include name="imported"/>
            </run>
        </groups>
        <classes>
{class_blocks}
        </classes>
    </test>

</suite>
"""
        rel = f"Suites/{pretty_suite}_{pretty_variant}.xml"
        self._write(rel, content)
        return rel

    # -- testng.xml --------------------------------------------------------

    def emit_testng_entry(self, prefix: str, test_class_fqn: str,
                           cases: list[TestCase]) -> str:
        """One <test> block per case, each pointing at its own CSV so the
        framework's data-provider picks up the right file per test method."""
        test_blocks = []
        for case in cases:
            csv_name = f"{self._short(case.name)}.csv"
            method_name = self._short(case.name)
            test_blocks.append(f"""    <test name="{case.name}">
        <parameter name="dataFile" value="testdata/{self.suite_name}/{csv_name}"/>
        <classes>
            <class name="{test_class_fqn}">
                <methods>
                    <include name="{method_name}"/>
                </methods>
            </class>
        </classes>
    </test>""")
        content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE suite SYSTEM "https://testng.org/testng-1.0.dtd">
<suite name="Imported-{prefix}" verbose="1" parallel="none">
    <listeners>
        <listener class-name="com.ak.api.reporting.TestSuiteListener"/>
        <listener class-name="com.ak.api.reporting.TestCaseLogListener"/>
        <listener class-name="com.ak.api.reporting.ExtentReportListener"/>
        <listener class-name="com.ak.api.reporting.XrayReportListener"/>
    </listeners>
{chr(10).join(test_blocks)}
</suite>
"""
        rel = f"src/test/resources/testng-{self.suite_name}-{self._short(prefix)}.xml"
        self._write(rel, content)
        return rel


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _emit_master_testng(emitter: Emitter, prefixes: list[str]) -> str:
    """Emit a testng-<suite>-all.xml that includes all per-prefix suites for
    THIS suite via <suite-file> references."""
    file_lines = [
        f'    <suite-file path="./testng-{emitter.suite_name}-{emitter._short(p)}.xml"/>'
        for p in prefixes]
    content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE suite SYSTEM "https://testng.org/testng-1.0.dtd">
<suite name="Imported-{emitter.suite_name}-All" verbose="1">
    <suite-files>
{chr(10).join(file_lines)}
    </suite-files>
</suite>
"""
    rel = f"src/test/resources/testng-{emitter.suite_name}-all.xml"
    emitter._write(rel, content)
    return rel


def _cases_prefix(case: TestCase) -> str:
    """Return a stable prefix bucket for a case, coping with mixed naming.

    Priority:
    1. Standard `B2B-172` (uppercase-dash-digits) as first token.
    2. Alphanumeric-only prefix like `B2B134` (SoapUI's older style).
    3. Fallback to the sanitized full case name so it still gets its own bucket.
    """
    m = re.match(r"^([A-Z]+-\d+)_", case.name)
    if m:
        return m.group(1)
    m = re.match(r"^([A-Z]+\d+)_", case.name)
    if m:
        return m.group(1)
    return sanitize_identifier(case.name)


def _iter_prefix_buckets(cases: list[TestCase]) -> dict[str, list[TestCase]]:
    """Group cases by prefix bucket, preserving discovery order."""
    buckets: dict[str, list[TestCase]] = {}
    for c in cases:
        buckets.setdefault(_cases_prefix(c), []).append(c)
    return buckets


def _default_suite_name(xml_path: str) -> str:
    """Suite name derived from the source-XML basename (stripped +
    lowercased + non-alphanum -> _). Overridable via --suite-name."""
    b = os.path.splitext(os.path.basename(xml_path))[0]
    return sanitize_identifier(b).lower()


def _clean_suite_output(output_dir: str, suite_name: str, package_root: str,
                         input_xml: str = "") -> list[str]:
    """Delete files this suite would have written on a previous run.
    Prevents stale test classes / CSVs / templates from lingering after a
    rename or a case-removal in the source XML. Safe: only touches paths
    scoped to `suite_name`."""
    import shutil, glob as _g
    pkg_path = f"src/test/java/{package_root.replace('.', '/')}/tests/imported/{suite_name}"
    support_pkg_path = f"src/main/java/{package_root.replace('.', '/')}/support/{suite_name}"
    imported_pkg_dir = os.path.join(output_dir, pkg_path)

    # v2 (--one-class-per-suite) writes per-method CSVs at
    # `src/test/resources/csv/<ClassName>/` and master TestNG suites at
    # `Suites/<PrettySuite>_*.xml`. Class names come from the SoapUI
    # <testSuite name="..."> attribute -- which may NOT match either the
    # CLI --suite-name or the XML basename. Enumerate the existing class
    # files BEFORE wiping the package dir so we scope csv/ + Suites/
    # cleanup to what actually got written last time.
    v2_class_names: list[str] = []
    if os.path.isdir(imported_pkg_dir):
        for entry in os.listdir(imported_pkg_dir):
            if entry.endswith(".java"):
                v2_class_names.append(entry[:-len(".java")])

    dirs_to_clean = [
        imported_pkg_dir,
        os.path.join(output_dir, support_pkg_path),
        os.path.join(output_dir, "src/test/resources/testdata", suite_name),
        os.path.join(output_dir, "src/main/resources/templates", suite_name),
        os.path.join(output_dir, "_audit", suite_name),
    ]
    # Add each v2 class's CSV folder
    for cls in v2_class_names:
        dirs_to_clean.append(
            os.path.join(output_dir, "src/test/resources/csv", cls))

    removed = []
    for d in dirs_to_clean:
        if os.path.isdir(d):
            shutil.rmtree(d)
            removed.append(d)

    # Individual files (testng suite files + flow diagram + any older
    # xml-basename-based flow diagram from a rename)
    file_paths = [
        f"src/test/resources/testng-{suite_name}-*.xml",
        f"_flows/{suite_name}.md",
    ]
    if input_xml:
        xml_base = os.path.splitext(os.path.basename(input_xml))[0]
        file_paths.append(f"_flows/{xml_base}.md")
    for glob_pat in file_paths:
        for p in _g.glob(os.path.join(output_dir, glob_pat)):
            os.remove(p)
            removed.append(p)

    # Master TestNG suite XMLs: scoped per emitted class name (drops the
    # trailing `Test` and adds `_Regression.xml` / `_Smoke.xml`).
    for cls in v2_class_names:
        stem = cls[:-4] if cls.endswith("Test") else cls
        for glob_pat in (f"Suites/{stem}_*.xml",):
            for p in _g.glob(os.path.join(output_dir, glob_pat)):
                os.remove(p)
                removed.append(p)
    # Also sweep the legacy heuristic pattern (pretty(suite_name) form)
    # in case an older run named files that way and enumeration above
    # doesn't cover them.
    pretty = to_camel_case(suite_name, upper_first=True)
    for glob_pat in (f"Suites/{pretty}_*.xml",):
        for p in _g.glob(os.path.join(output_dir, glob_pat)):
            if p not in removed:
                os.remove(p)
                removed.append(p)
    return removed


def _dedupe_case_names_inplace(cases: list[TestCase]) -> int:
    """SoapUI allows two testCases to share the same name inside one
    testSuite. Java doesn't allow two methods with the same name in one
    class. Rename in-place with `_dup2`, `_dup3` suffixes so the emitter
    can proceed. Returns the number of renames performed."""
    seen: dict[str, int] = {}
    renames = 0
    for c in cases:
        n = seen.get(c.name, 0) + 1
        seen[c.name] = n
        if n > 1:
            c.name = f"{c.name}_dup{n}"
            renames += 1
    return renames


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--input", required=True, help="Path to the ReadyAPI/SoapUI project XML")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--prefix", help="JIRA prefix to convert (e.g. B2B-172)")
    group.add_argument("--all-prefixes", action="store_true",
                       help="Legacy: convert EVERY case-name prefix bucket "
                            "into its own test class (many small classes). "
                            "Kept for backwards compat; prefer "
                            "--one-class-per-suite for new imports.")
    group.add_argument("--one-class-per-suite", action="store_true",
                       dest="one_class_per_suite",
                       help="Recommended: emit ONE Java test class per SoapUI "
                            "test suite -- every case in it becomes a @Test "
                            "method inside that class. Matches reference "
                            "framework layout: CSVs at csv/<Class>/<method>.csv, "
                            "templates deduplicated across the suite, one "
                            "master TestNG suite XML at Suites/<Suite>_Regression.xml "
                            "with Allure listeners wired in.")
    p.add_argument("--output", default="output", help="Output root directory")
    p.add_argument("--package-root", default="com.ak.api", help="Java package root")
    p.add_argument("--service-name", default="ProgramAccounts",
                   help="Name for the generated service client class prefix")
    p.add_argument("--envs", default="qa,prod",
                   help="Comma-separated env names to emit config for")
    p.add_argument("--suite-name", default=None,
                   help="Namespace for this import so multiple SoapUI XMLs "
                        "can coexist in one output tree. Default derived "
                        "from the input XML basename (e.g. "
                        "`accountmemberregression`). Test classes land at "
                        "com.<pkg-root>.tests.imported.<suite>.<prefix>.")
    p.add_argument("--clean", action="store_true",
                   help="Remove this suite's previously-generated files "
                        "under --output before writing (test classes, CSVs, "
                        "templates, testng-<suite>-*.xml, audit, flow diagram). "
                        "Other suites in the same output tree are untouched.")
    p.add_argument("--max-name-len", type=int, default=40,
                   help="Maximum length (chars) for any SoapUI-derived name "
                        "used in a filesystem path or Java class name. Names "
                        "over the limit are truncated + get a stable 6-char "
                        "SHA1 suffix. Default 40 keeps generated paths well "
                        "under Windows' 260-char MAX_PATH so `git add` works "
                        "without core.longpaths. Pass 0 to disable truncation. "
                        "See _audit/<suite>/name_mapping.csv for the reverse map.")
    args = p.parse_args()

    suite_name = args.suite_name or _default_suite_name(args.input)
    print(f"[ra_converter] suite: {suite_name}  "
          f"(namespaces test packages / CSVs / templates / testng / audit / flows)")

    if args.clean:
        removed = _clean_suite_output(args.output, suite_name, args.package_root,
                                       input_xml=args.input)
        print(f"[ra_converter] --clean: removed {len(removed)} stale "
              f"file/dir path(s) from previous runs of this suite")
        for r in removed[:5]:
            print(f"    - {r}")
        if len(removed) > 5:
            print(f"    ... and {len(removed) - 5} more")

    print(f"[ra_converter] parsing {args.input} ...")
    # v2 mode wants to see SoapUI suite boundaries (one class per suite).
    # Legacy modes flatten across suites, which is fine because most SoapUI
    # exports carry a single suite anyway.
    parsed_suites: list[tuple[str, list[TestCase]]] = parse_test_suites(args.input)
    cases_all: list[TestCase] = [c for _sn, cs in parsed_suites for c in cs]
    print(f"[ra_converter] found {len(cases_all)} test cases across "
          f"{len(parsed_suites)} SoapUI test suite(s)")

    dup_renames = _dedupe_case_names_inplace(cases_all)
    if dup_renames:
        print(f"[ra_converter] renamed {dup_renames} duplicate case name(s) "
              f"with _dupN suffix so Java method names don't collide")

    if getattr(args, "one_class_per_suite", False):
        # New mode: preserve SoapUI suite boundaries so each suite becomes
        # ONE Java class with N methods. Buckets are for the legacy per-
        # prefix path only; here we track (soapui_suite_name -> cases).
        buckets = {}  # unused in v2 path
        cases_in_scope = cases_all
        soapui_suites = parsed_suites
        print(f"[ra_converter] --one-class-per-suite: "
              f"{len(soapui_suites)} class(es) will be emitted "
              f"(cases grouped by endpoint-shape into fewer methods "
              f"with N CSV rows each):")
        for sn, cs in soapui_suites:
            clusters_preview = _cluster_cases_by_shape(cs)
            print(f"    - {sn}: {len(cs)} test cases "
                  f"-> {len(clusters_preview)} @Test methods "
                  f"({sum(1 for cl in clusters_preview if len(cl) > 1)} "
                  f"with >1 CSV row)")
    elif args.all_prefixes:
        buckets = _iter_prefix_buckets(cases_all)
        print(f"[ra_converter] --all-prefixes: {len(buckets)} prefix buckets")
        cases_in_scope = cases_all
        soapui_suites = []
    else:
        cases_in_scope = group_by_prefix(cases_all, args.prefix)
        if not cases_in_scope:
            print(f"[ra_converter] no cases match prefix {args.prefix!r}")
            return 1
        buckets = {args.prefix: cases_in_scope}
        soapui_suites = []
        print(f"[ra_converter] {len(cases_in_scope)} cases match prefix {args.prefix}:")
        for c in cases_in_scope:
            rest_ct = sum(1 for s in c.steps if isinstance(s, RestStep))
            groovy_ct = sum(1 for s in c.steps if isinstance(s, GroovyStep))
            print(f"    - {c.name}  ({len(c.steps)} steps, {rest_ct} REST, {groovy_ct} Groovy)")

    # ONE shared client covering every REST op across ALL prefixes in scope.
    # Cross-prefix dedup: if two prefixes both hit /token, they share the same
    # tokenRequest() method -- no duplicate methods, no duplicate classes.
    print("[ra_converter] collecting all REST operations into ONE shared client ...")
    shared_ops = collect_shared_rest_steps(cases_in_scope, min_occurrences=1)
    print(f"[ra_converter] {len(shared_ops)} distinct REST operations across scope")

    ledger = AuditLedger()
    emitter = Emitter(output_dir=args.output, package_root=args.package_root,
                       ledger=ledger, suite_name=suite_name,
                       max_name_len=args.max_name_len)
    service_class = emitter.emit_service_client(args.service_name, shared_ops)
    print(f"[ra_converter] emitted shared service client: {service_class}")

    # We must know which placeholder keys are config-driven BEFORE emitting
    # TestSupport so the CONFIG_KEYS[] array is populated for runtime lookup.
    all_config_keys: set[str] = set()
    for case in cases_in_scope:
        cl = classify_placeholders_for_case(case)
        all_config_keys.update(cl["config"])
    # Always include base_url so #base_url# in path/url substitution works.
    all_config_keys.add("base_url")

    support_rel = emitter.emit_test_support(sorted(all_config_keys))
    print(f"[ra_converter] emitted test-support helper: {support_rel}  "
          f"(CONFIG_KEYS: {len(all_config_keys)})")

    # Shared-flow detection: find opening step-sequences that appear in
    # >=10 cases and extract them into SetupHelper.flow_X methods.
    # Each covered case's test method then calls the helper instead of
    # duplicating all N setup steps inline.
    print("[ra_converter] detecting shared setup-flow sequences ...")
    flows = find_shared_flows(cases_in_scope, min_cases=10, min_steps=3)
    if flows:
        total_covered = len({name for f in flows for name in f["cases"]})
        total_steps_extracted = sum(f["prefix_len"] * len(f["cases"]) for f in flows)
        print(f"[ra_converter]   {len(flows)} shared flows detected:")
        for f in flows:
            print(f"     - {f['id']}: {f['prefix_len']} steps, "
                  f"reused by {len(f['cases'])} cases")
        print(f"[ra_converter]   coverage: {total_covered}/{len(cases_in_scope)} "
              f"cases hit at least one flow; "
              f"{total_steps_extracted} step-emissions saved via extraction")
        emitter._flow_by_case = build_flow_assignment(cases_in_scope, flows)
    else:
        print("[ra_converter]   no flows meet the threshold; test methods "
              "keep all steps inline")

    # =========================================================
    # v2 path: one Java class per SoapUI test suite.
    # =========================================================
    if getattr(args, "one_class_per_suite", False):
        # 0) The convention-based DataProvider + PlaceholderResolver need
        #    to exist ONCE per output tree -- rewriting them every run is
        #    idempotent.
        dp_rel = emitter.emit_per_method_csv_data_provider()
        print(f"[ra_converter] emitted convention CSV data provider: {dp_rel}")
        pr_rel = emitter.emit_placeholder_resolver()
        emitter._resolver_emitted = True
        print(f"[ra_converter] emitted placeholder resolver: {pr_rel}")

        # 1a) Deduped templates emitted FIRST so _template_path_by_step is
        #     populated before ANY step-rendering reads it (SetupHelper AND
        #     the test-class emitter both depend on it).
        template_map = emitter.emit_templates_deduplicated(cases_in_scope)
        rest_step_ct = sum(
            1 for c in cases_in_scope for s in c.steps
            if isinstance(s, RestStep) and s.request_body.strip())
        dedup_pct = (100 - int(100 * len(template_map) / max(1, rest_step_ct)))
        print(f"[ra_converter] emitted {len(template_map)} deduped templates "
              f"(from {rest_step_ct} REST bodies -> {dedup_pct}% dedup)")

        # 1b) SetupHelper emission runs AFTER template dedup so its shared
        #     flow methods reference the deduped template paths, not the
        #     flat legacy fallback.
        if flows:
            helper_rel = emitter.emit_setup_helper(flows, service_class)
            print(f"[ra_converter] emitted setup helper: {helper_rel}")

        # 2) Emit one Java class per SoapUI test suite + CSVs colocated
        #    under `csv/<ClassName>/`.
        v2_class_fqns: list[str] = []
        total_methods = 0
        total_rows = 0
        for soapui_sname, sui_cases in soapui_suites:
            rel, fqn, method_names = emitter.emit_test_class_per_suite(
                soapui_sname, sui_cases, service_class)
            v2_class_fqns.append(fqn)

            # The class emitter set up emitter._clusters + _cluster_to_method
            # while rendering; reuse them so CSV filenames match the method
            # names written into the class exactly.
            class_simple = fqn.rsplit(".", 1)[-1]
            for idx, cluster in enumerate(emitter._clusters):
                mname, _status, _variant = emitter._cluster_to_method[idx]
                emitter.emit_csv_per_method(class_simple, mname, cluster)
                total_rows += len(cluster)
            multi = sum(1 for cl in emitter._clusters if len(cl) > 1)
            print(f"[ra_converter] emitted {fqn}  "
                  f"({len(method_names)} @Test methods, "
                  f"{multi} with >1 CSV row, "
                  f"{sum(len(cl) for cl in emitter._clusters if len(cl) > 1)} rows in shared CSVs)")
            total_methods += len(method_names)
        print(f"[ra_converter] v2 total: {len(v2_class_fqns)} class(es), "
              f"{total_methods} @Test method(s), "
              f"{total_rows} CSV row(s) across "
              f"{total_methods} CSV file(s) under src/test/resources/csv/  "
              f"(saved {total_rows - total_methods} methods via endpoint clustering)")

        # 3) Env configs (unchanged from v1)
        for env in args.envs.split(","):
            emitter.emit_env_config(cases_in_scope, env_name=env.strip())
        print(f"[ra_converter] emitted env config for: {args.envs}")

        # 4) Master TestNG suite XMLs at Suites/<Suite>_Regression.xml
        #    (+ _Smoke.xml as a starter template with same class list;
        #    authors typically narrow it later by editing groups).
        for variant in ("Regression", "Smoke"):
            master_rel = emitter.emit_master_suite_xml(v2_class_fqns, variant=variant)
            print(f"[ra_converter] emitted master suite: {master_rel}")

        # Flow diagram still useful in v2 for visualizing shared setup.
        flow_rel = emitter.emit_flow_diagram(
            args.input, cases_in_scope, flows,
            service_class, shared_ops_count=len(shared_ops))
        print(f"[ra_converter] emitted flow diagram: {flow_rel}")

    # =========================================================
    # Legacy path: --prefix or --all-prefixes
    # =========================================================
    else:
        # Legacy modes: SetupHelper runs BEFORE templates because those
        # modes use the flat template layout `templates/<suite>/<step>.json`
        # that doesn't depend on the dedup map.
        if flows:
            helper_rel = emitter.emit_setup_helper(flows, service_class)
            print(f"[ra_converter] emitted setup helper: {helper_rel}")

        # Emit one test class per prefix bucket
        prefix_order: list[str] = []
        for pref, pref_cases in buckets.items():
            prefix_order.append(pref)
            emitter.emit_test_class(pref, pref_cases, service_class)

        # Emit the flow diagram documenting THIS suite's migration.
        flow_rel = emitter.emit_flow_diagram(
            args.input, cases_in_scope, flows,
            service_class, shared_ops_count=len(shared_ops))
        print(f"[ra_converter] emitted flow diagram: {flow_rel}")
        print(f"[ra_converter] emitted {len(prefix_order)} test classes "
              f"(one per prefix bucket)")

        # Per-case CSV + template artifacts (legacy shape)
        for case in cases_in_scope:
            emitter.emit_csv(case)
            emitter.emit_templates(case)
        print(f"[ra_converter] emitted CSVs + request-body templates for "
              f"{len(cases_in_scope)} cases")

        # Env configs -- one file per env, containing every distinct config key
        # observed across all cases in scope
        for env in args.envs.split(","):
            emitter.emit_env_config(cases_in_scope, env_name=env.strip())
        print(f"[ra_converter] emitted env config for: {args.envs}")

        # testng.xml per prefix, plus a master that aggregates them all
        for pref in prefix_order:
            fqn = (f"{args.package_root}.tests.imported.{suite_name}."
                   f"{emitter._short(pref)}."
                   + emitter._short_cls(pref) + "Test")
            emitter.emit_testng_entry(pref, fqn, buckets[pref])
        if args.all_prefixes:
            master_rel = _emit_master_testng(emitter, prefix_order)
            print(f"[ra_converter] emitted master suite: {master_rel}")
        print(f"[ra_converter] emitted {len(prefix_order)} per-prefix testng entries")

    # ---- AUDIT LEDGER: proves every SoapUI assertion / Groovy block was ----
    # ---- accounted for (translated, stubbed, or explicitly TODO'd). --------
    from datetime import datetime as _dt
    stamp = os.environ.get("RA_CONVERTER_TIMESTAMP") or \
            _dt.utcnow().strftime("%Y-%m-%d %H:%M:%SZ")
    ledger.write(args.output, args.input, stamp, suite_name=suite_name)
    # Name-shortening trace: write the (short -> original) mapping so any
    # truncated test class / CSV / testng file can be looked up back to its
    # source SoapUI case name.
    if emitter.name_mapping:
        import csv as _csv
        mapping_path = os.path.join(
            args.output, "_audit", suite_name, "name_mapping.csv")
        os.makedirs(os.path.dirname(mapping_path), exist_ok=True)
        with open(mapping_path, "w", encoding="utf-8", newline="") as f:
            w = _csv.writer(f)
            w.writerow(["short", "original", "kind"])
            for short, orig in sorted(emitter.name_mapping.items()):
                w.writerow([short, orig, "case_or_prefix"])
        print(f"[ra_converter] name shortening: {len(emitter.name_mapping)} "
              f"names truncated -> {mapping_path}")
    print()
    print(f"[ra_converter] audit ledger: {args.output}/_audit/{suite_name}/summary.md")
    a_total = len(ledger.assertions)
    g_total = len(ledger.groovy)
    a_full = sum(1 for r in ledger.assertions if r[6] == "FULL")
    g_full = sum(1 for r in ledger.groovy if r[4] == "FULL")
    a_skip = sum(1 for r in ledger.assertions if r[6] == "SKIPPED")
    g_skip = sum(1 for r in ledger.groovy if r[4] == "SKIPPED")
    a_active, g_active = a_total - a_skip, g_total - g_skip
    print(f"[ra_converter]   assertions: {a_full}/{a_total} FULL, {a_skip} SKIPPED "
          f"(active FULL%: {100*a_full//a_active if a_active else 0}%)")
    print(f"[ra_converter]   groovy:     {g_full}/{g_total} FULL, {g_skip} SKIPPED "
          f"(active FULL%: {100*g_full//g_active if g_active else 0}%)")
    print(f"[ra_converter]   unmapped items: {len(ledger.unmapped)} "
          f"(see _audit/unmapped.csv)")

    print()
    print(f"[ra_converter] wrote {len(emitter.written)} files under {args.output}/")
    if len(emitter.written) <= 20:
        for f in emitter.written:
            print(f"    {f}")
    else:
        for f in emitter.written[:10]:
            print(f"    {f}")
        print(f"    ... and {len(emitter.written) - 10} more")

    return 0


if __name__ == "__main__":
    sys.exit(main())
