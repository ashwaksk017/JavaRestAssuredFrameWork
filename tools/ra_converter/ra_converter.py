"""ReadyAPI (SoapUI) test-suite -> Java + REST Assured + TestNG converter.

Reads a SoapUI/ReadyAPI project XML and emits Java + TestNG + CSV +
templates + a master TestNG suite XML targeting the ApiAutomationRestAssured
framework. Single emission mode:

  - ONE Java test class per SoapUI <con:testSuite>:
      src/test/java/com/ak/api/tests/imported/<suite>/<Suite>Test.java
    Cases sharing (verb, path, body-shape) per REST step cluster into one
    @Test method with N CSV rows; prefix-matching clusters fold shorter
    scenarios into longer ones with an early-return `_stop_after` CSV cell.
  - Per-method CSV datasheets (one row per SoapUI case):
      src/test/resources/csv/<Suite>Test/<methodName>.csv
    Every expected value (status code, JsonPath match, JsonPath count,
    header presence, SLA, etc.) is a CSV column -- edit the CSV to change
    what a scenario asserts, no code change.
  - Deduped request-body templates:
      src/main/resources/templates/<suite>/<bucket>/<step>_<sha1>.json
    Bodies with identical JSON shape merge into ONE template with
    #tpl_<jsonPath># placeholders; per-case literal values live in CSV cells.
  - Convention CSV DataProvider + faker/property placeholder resolver:
      src/main/java/com/ak/api/data/PerMethodCsvDataProvider.java
      src/main/java/com/ak/api/data/PlaceholderResolver.java
  - Master TestNG suite XMLs at Suites/<Suite>_Regression.xml + _Smoke.xml
    with Allure + Extent + Xray listeners wired in.
  - Env config JSON (qa + prod stubs) at src/main/resources/config/<env>.json.
  - Shared service client at src/main/java/com/ak/api/rest/clients/<Service>Client.java.
  - Setup-flow helper for opening step-sequences reused by >=10 cases.

Coverage:
  - REST step -> service-client method + test-code call
  - Properties / ${step#field} refs -> classified as config / runtime / csv
  - Groovy step -> pattern-translated (JsonSlurper, setPropertyValue,
    def-var publications, JDBC literal queries) with runtime WARN + Allure
    attachment for unrecognized shapes
  - Assertions: Valid HTTP Status Codes, Invalid HTTP Status Codes,
    JsonPath Match / Existence / Count / RegEx, Simple Equals / Contains /
    NotContains, Response SLA, MessageContentAssertion (per-element XPath),
    DataAndMetadataAssertion (per-element JsonPath), GroovyScriptAssertion
    (common patterns; others log + attach to Allure).

Usage:
  python ra_converter.py --input path/to/soapui.xml --output . \\
                         --package-root com.ak.api --service-name YourService
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
    # For MessageContentAssertion / DataAndMetadataAssertion the
    # configuration wraps 1..N <elements> children -- each is one
    # element check (xpath/JsonPath + expectedValue + enabled + ...).
    # Empty for scalar-config assertion types.
    elements: list = field(default_factory=list)


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
    # SoapUI per-request auth override -- populated from <con:credentials>
    # when the step declares its own auth. Empty when the step inherits
    # from project-level auth (typical). Keys:
    #   auth_type    -> "No Authorization" | "Basic" | "OAuth 2.0" |
    #                    "OAuth 1.0" | "NTLM" | "Kerberos" | "SPNEGO" |
    #                    "WS-Security" (SOAP)
    #   profile_name -> name of the SoapUI auth profile
    #   username/password/domain -- when Basic/NTLM/Kerberos
    #   oauth_token / oauth_client_id / oauth_client_secret -- when OAuth 2.0
    auth_profile: dict = field(default_factory=dict)
    # File attachments (multipart/form-data uploads). Populated from
    # `<con:attachment>` elements on the REST request. Each entry is
    # a dict {name, content_type, part_name, data_ref}. Empty when the
    # step doesn't upload files. Consumers must emit multipart-aware
    # code (which the current emitter doesn't yet -- emitted as a TODO
    # comment + WARN at runtime).
    attachments: list = field(default_factory=list)


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
    # Test-management annotations mined from `<con:testCase>` attributes.
    # Populated by parse_test_suites when present in the source XML.
    # Blank string means "not set in SoapUI". Consumed by
    # emit_test_class_per_suite to emit @XrayTest / @TmsLink / @Issue
    # annotations that pass through to Allure + JIRA integrations.
    zephyr_test_id: str = ""
    zephyr_test_name: str = ""
    jira: str = ""
    # Free-form dict for other custom test-case attributes we notice.
    # Non-standard SoapUI plugins sometimes stash IDs here (e.g. `qtestId`).
    tm_extras: dict = field(default_factory=dict)

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


# Populated by parse_test_suites() when a full SoapUI project XML is
# passed (i.e. the file includes <con:interface> definitions). Maps
# the operation `methodName` attribute stored on each <con:testStep>
# to the AUTHORITATIVE HTTP verb from its <con:method method="..."/>
# declaration. testSuite-only exports won't populate this -- the parser
# falls back to keyword-based inference in _infer_http_method.
_INTERFACE_METHOD_MAP: dict[str, str] = {}


def _build_interface_method_map(root: ET.Element) -> dict[str, str]:
    """Scan the project XML for `<con:interface xsi:type="con:RestService">`
    definitions. Each interface contains `<con:resource>` children with
    `<con:method name="X" method="POST"/>` grandchildren; return
    {methodName -> HTTPVerb}. Empty for suite-only exports."""
    out: dict[str, str] = {}
    # Match any interface descendant, then walk method nodes.
    for iface in root.iter("{http://eviware.com/soapui/config}interface"):
        for meth in iface.iter("{http://eviware.com/soapui/config}method"):
            name = meth.get("name", "")
            verb = (meth.get("method", "") or "").upper()
            if name and verb:
                out[name] = verb
    return out


# Populated by parse_test_suites when the project XML declares
# multiple environments via `<con:environments>` / `<con:environment
# name="X">` blocks. Maps env-name -> {interface_name -> base_url}.
# emit_env_config reads this to write per-env base_url values instead
# of cloning one URL into every env file.
_PROJECT_ENVIRONMENTS: dict[str, dict[str, str]] = {}


def _build_environment_map(root: ET.Element) -> dict[str, dict[str, str]]:
    """Scan the project XML for `<con:environments>` /
    `<con:environment name="X">` definitions. Each environment carries
    per-interface endpoints via nested `<con:endpoint>` elements.
    Returns {env_name -> {interface_name -> base_url}}. Empty for
    single-env projects."""
    out: dict[str, dict[str, str]] = {}
    for envs_container in root.iter("{http://eviware.com/soapui/config}environments"):
        for env_el in envs_container.findall("{http://eviware.com/soapui/config}environment"):
            env_name = env_el.get("name", "")
            if not env_name:
                continue
            per_iface: dict[str, str] = {}
            # SoapUI stores endpoints under either `<con:endpoints>` (list)
            # or as attributes on `<con:interface>` children within the env.
            for iface in env_el.iter("{http://eviware.com/soapui/config}interface"):
                iface_name = iface.get("name", "")
                for ep in iface.iter("{http://eviware.com/soapui/config}endpoint"):
                    text = (ep.text or "").strip()
                    if iface_name and text:
                        per_iface[iface_name] = text
                        break  # first endpoint per interface
            if per_iface:
                out[env_name] = per_iface
    return out


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
            if tag == "elements":
                # MessageContentAssertion + DataAndMetadataAssertion:
                # each <elements> block is one element-level check
                # with its own xpath/path + expectedValue + enabled.
                el_data: dict = {}
                for gc in child:
                    gc_tag = gc.tag.split("}")[-1]
                    el_data[gc_tag] = (gc.text or "").strip()
                a.elements.append(el_data)
            else:
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

    # HTTP method resolution priority:
    #   1. AUTHORITATIVE: <con:interface>/<con:resource>/<con:method method="X">
    #      populated by parse_test_suites when a full project XML is passed.
    #      Uses `methodName` on this step as the lookup key.
    #   2. FALLBACK: keyword-based inference from method_name + step_name +
    #      resource_path -- unreliable but the only signal available for
    #      suite-only exports.
    http_method = _INTERFACE_METHOD_MAP.get(method_name, "").upper()
    if not http_method:
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

    # SoapUI per-request auth profile (<con:credentials>). Empty when the
    # step inherits from project-level auth (the common case), populated
    # when the step overrides it. Also captures the selected auth-profile
    # NAME so the emitter can hint at which project-level profile drives
    # this request when the step itself only stores the reference.
    auth_profile: dict = {}
    if req_el is not None:
        creds = req_el.find("con:credentials", NS)
        if creds is not None:
            def _c(tag: str) -> str:
                return _text(creds.find(f"con:{tag}", NS)) or ""
            auth_profile = {
                "profile_name": _c("selectedAuthProfile"),
                "auth_type": _c("authType"),
                "username": _c("username"),
                "password": _c("password"),
                "domain": _c("domain"),
                # OAuth 2 flow: SoapUI stores tokens/refresh under nested
                # <con:oauth2Flow>. Best-effort flat pull; consumers can
                # dig deeper via the raw string when needed.
                "oauth_token": _c("accessToken") or _c("oauth2Token"),
                "oauth_client_id": _c("clientID") or _c("clientId"),
                "oauth_client_secret": _c("clientSecret"),
                "oauth_refresh_token": _c("refreshToken"),
            }
            # Drop empty fields so downstream code can test `if
            # step.auth_profile` as a truthy override check.
            auth_profile = {k: v for k, v in auth_profile.items() if v}

    # File attachments (multipart uploads). `<con:attachment>` children
    # of the REST request carry name, content-type, and data reference.
    attachments: list = []
    if req_el is not None:
        for att_el in req_el.findall("con:attachment", NS):
            def _t(tag: str) -> str:
                return _text(att_el.find(f"con:{tag}", NS)) or att_el.get(tag, "") or ""
            entry = {
                "name": _t("name") or att_el.get("name", ""),
                "content_type": _t("contentType") or att_el.get("contentType", ""),
                "part_name": _t("partName") or att_el.get("partName", ""),
                "data_ref": _t("data") or "",  # base64 blob or file ref
                "file_ref": _t("url") or _t("filename") or "",
            }
            if any(entry.values()):
                attachments.append({k: v for k, v in entry.items() if v})

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
        auth_profile=auth_profile,
        attachments=attachments,
    )


# Verb keyword hints for `_infer_http_method`. Multi-lingual coverage
# helps when the SoapUI project uses Spanish/German/French/Italian
# operation names (common in EU/LATAM enterprise SoapUI projects).
# Full-word-only matching (see `_infer_http_method`) so `list` doesn't
# match `deletedItems/list`. Order matters: verbs earlier in the dict
# win when multiple match; DELETE goes first so `list` doesn't win over
# `delete` when both appear.
_METHOD_KEYWORDS = {
    "delete": ["delete", "remove", "cancel", "destroy", "borrar", "eliminar",
                "supprimer", "loeschen", "loschen", "eliminare", "rimuovere"],
    "patch":  ["patch", "parche"],
    "put":    ["update", "put", "replace", "modify", "actualizar", "reemplazar",
                "modificar", "aktualisieren", "ersetzen", "mettreajour",
                "aggiornare", "sostituire"],
    "post":   ["create", "post", "enroll", "activate", "add", "insert", "token",
                "crear", "agregar", "insertar", "erstellen", "hinzufuegen",
                "hinzufugen", "creer", "ajouter", "creare", "aggiungere"],
    "get":    ["read", "get", "fetch", "list", "search", "retrieve", "find",
                "leer", "obtener", "buscar", "lesen", "abrufen", "suchen",
                "lire", "chercher", "leggere", "ottenere", "cercare"],
}


def _infer_http_method(method_name: str, resource_path: str, body: str, step_name: str) -> str:
    """SoapUI's XML doesn't attribute HTTP verb on the step element in
    suite-only exports; infer from the operation name / body presence.
    Full-project exports get the AUTHORITATIVE verb via `_build_interface_method_map`;
    this function only runs as a fallback.

    Uses WORD-BOUNDARY matching so `/deletedItems/list` doesn't get
    inferred as DELETE (bare substring match previously fired on
    "delete" inside "deletedItems"). Words boundaries include the
    common URL-segment separators (`/`, `-`, `_`, `.`, ` `)."""
    hay = " ".join((method_name, step_name, resource_path))
    # Normalize separators to spaces so word-boundary regex matches
    # `deletedItems/list` -> tokens `deletedItems`, `list`.
    tokens = re.split(r"[/_\-\.\s]+", hay.lower())
    token_set = set(t for t in tokens if t)
    for verb, kws in _METHOD_KEYWORDS.items():
        for kw in kws:
            # Full-word match (no more `delete` matching `deletedItems`).
            if kw in token_set:
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
        # File-based DataSource references an external CSV/Excel via
        # <con:file> or <con:fileName>. Grab it so downstream code can
        # migrate the actual data file (or at least log its absence).
        for tag in ("file", "fileName", "excelFile"):
            file_el = cfg_el.find(f".//con:{tag}", NS)
            if file_el is not None and file_el.text:
                ds.file_path = file_el.text.strip()
                break
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


# --- Additional first-class step types ------------------------------------

@dataclass
class CallTestCaseStep:
    """SoapUI `calltestcase` / `runtestcase` step -- invokes another test
    case within the same project. Emitted as a comment + a call to the
    target case's generated Java method when the target lives in the
    same class; otherwise as a TODO stub."""
    step_name: str
    target_test_suite: str = ""
    target_test_case: str = ""


def _parse_calltestcase_step(step_el: ET.Element) -> "CallTestCaseStep":
    cfg_el = step_el.find("con:config", NS)
    tgt_suite = ""
    tgt_case = ""
    if cfg_el is not None:
        tgt_suite = _text(cfg_el.find("con:targetTestSuite", NS)) or cfg_el.get("targetTestSuite", "")
        tgt_case = _text(cfg_el.find("con:targetTestCase", NS)) or cfg_el.get("targetTestCase", "")
    return CallTestCaseStep(
        step_name=step_el.get("name", ""),
        target_test_suite=tgt_suite, target_test_case=tgt_case,
    )


@dataclass
class DelayStep:
    """SoapUI `delay` / `wait` step -- sleeps for N milliseconds."""
    step_name: str
    delay_ms: int = 0


def _parse_delay_step(step_el: ET.Element) -> "DelayStep":
    cfg_el = step_el.find("con:config", NS)
    delay_ms = 0
    if cfg_el is not None:
        raw = _text(cfg_el.find("con:delay", NS)) or cfg_el.get("delay", "0")
        try:
            delay_ms = int(raw)
        except (TypeError, ValueError):
            delay_ms = 0
    return DelayStep(step_name=step_el.get("name", ""), delay_ms=delay_ms)


@dataclass
class GotoStep:
    """SoapUI `gotostep` / `conditionalgoto` step -- branches to another
    step based on a condition. Emitted as a TODO comment since Java
    doesn't have goto; author must refactor into if/loop."""
    step_name: str
    conditions: list = field(default_factory=list)


def _parse_goto_step(step_el: ET.Element) -> "GotoStep":
    cfg_el = step_el.find("con:config", NS)
    conditions = []
    if cfg_el is not None:
        for cond in cfg_el.iter("{http://eviware.com/soapui/config}condition"):
            conditions.append({
                "target_step": cond.get("targetStep", ""),
                "expression": _text(cond.find("con:expression", NS)) or cond.get("expression", ""),
            })
    return GotoStep(step_name=step_el.get("name", ""), conditions=conditions)


@dataclass
class SoapRequestStep:
    """SoapUI `request` (wsdlrequest / soaprequest) -- classic SOAP call.
    Emitted with best-effort SOAP envelope handling (assumes existing
    RestAssured setup can send arbitrary XML with the right Content-Type
    header). Assertion types coincide with REST for most SoapUI checks."""
    step_name: str
    operation: str = ""
    endpoint: str = ""
    request_body: str = ""
    media_type: str = "text/xml"
    assertions: list = field(default_factory=list)


def _parse_soap_request_step(step_el: ET.Element) -> "SoapRequestStep":
    cfg_el = step_el.find("con:config", NS)
    operation = cfg_el.get("operation", "") if cfg_el is not None else ""
    req_el = cfg_el.find("con:request", NS) if cfg_el is not None else None
    body = _text(req_el.find("con:request", NS)) if req_el is not None else ""
    endpoint = _text(req_el.find("con:endpoint", NS)) if req_el is not None else ""
    assertions = []
    if req_el is not None:
        for a_el in req_el.findall("con:assertion", NS):
            assertions.append(_parse_assertion(a_el))
    return SoapRequestStep(
        step_name=step_el.get("name", ""),
        operation=operation, endpoint=endpoint,
        request_body=body, assertions=assertions,
    )


@dataclass
class HttpRequestStep:
    """SoapUI `httprequest` -- raw HTTP (not REST-resource-backed). Rare;
    used for non-REST endpoints. Best-effort: emit as a plain
    RestAssured given().body(...).post(url) call."""
    step_name: str
    http_method: str = "GET"
    endpoint: str = ""
    request_body: str = ""
    media_type: str = "application/octet-stream"
    assertions: list = field(default_factory=list)


def _parse_http_request_step(step_el: ET.Element) -> "HttpRequestStep":
    cfg_el = step_el.find("con:config", NS)
    method = "GET"
    endpoint = ""
    body = ""
    media_type = "application/octet-stream"
    assertions = []
    if cfg_el is not None:
        req_el = cfg_el.find("con:restRequest", NS) or cfg_el.find("con:request", NS)
        if req_el is not None:
            method = (req_el.get("method", "") or "GET").upper()
            media_type = req_el.get("mediaType", "application/octet-stream")
            endpoint = _text(req_el.find("con:endpoint", NS))
            body = _text(req_el.find("con:request", NS))
            for a_el in req_el.findall("con:assertion", NS):
                assertions.append(_parse_assertion(a_el))
    return HttpRequestStep(
        step_name=step_el.get("name", ""),
        http_method=method, endpoint=endpoint,
        request_body=body, media_type=media_type,
        assertions=assertions,
    )


@dataclass
class MockResponseStep:
    """SoapUI `mockresponse` step -- listens for an incoming request.
    Not runnable in a pure REST Assured test; emitted as a TODO stub."""
    step_name: str


def _parse_mock_response_step(step_el: ET.Element) -> "MockResponseStep":
    return MockResponseStep(step_name=step_el.get("name", ""))


@dataclass
class JmsStep:
    """SoapUI `jms` step -- send/receive JMS messages. Emitted as a
    TODO stub since REST Assured doesn't cover JMS."""
    step_name: str
    direction: str = ""
    destination: str = ""


def _parse_jms_step(step_el: ET.Element) -> "JmsStep":
    cfg_el = step_el.find("con:config", NS)
    direction = ""
    destination = ""
    if cfg_el is not None:
        direction = cfg_el.get("direction", "") or _text(cfg_el.find("con:direction", NS))
        destination = cfg_el.get("destination", "") or _text(cfg_el.find("con:destination", NS))
    return JmsStep(
        step_name=step_el.get("name", ""),
        direction=direction, destination=destination,
    )


_STEP_PARSERS = {
    "restrequest":       _parse_rest_step,
    "groovy":            _parse_groovy_step,
    "properties":        _parse_properties_step,
    "datasource":        _parse_datasource_step,
    "transfer":          _parse_transfer_step,
    "manualTestStep":    _parse_manual_step,
    "jdbc":              _parse_jdbc_step,
    # Extended step types (previously silently dropped to Groovy placeholder):
    "calltestcase":      _parse_calltestcase_step,
    "runtestcase":       _parse_calltestcase_step,
    "delay":             _parse_delay_step,
    "wait":              _parse_delay_step,
    "gotostep":          _parse_goto_step,
    "conditionalgoto":   _parse_goto_step,
    "goto":              _parse_goto_step,
    "request":           _parse_soap_request_step,     # wsdlrequest / soaprequest
    "wsdlrequest":       _parse_soap_request_step,
    "soaprequest":       _parse_soap_request_step,
    "httprequest":       _parse_http_request_step,
    "mockresponse":      _parse_mock_response_step,
    "jms":               _parse_jms_step,
    "jmsreceive":        _parse_jms_step,
    "jmsdispatch":       _parse_jms_step,
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
    emit exactly one Java test class per suite.

    UTF-8 BOM tolerant: some SoapUI exports (especially from Windows
    editors) include a byte-order mark; ET.parse chokes on that unless
    we pre-read and strip it. Falls back to a bytes-level open + parse
    when the file starts with \\xEF\\xBB\\xBF."""
    with open(xml_path, "rb") as _f:
        _head = _f.read(3)
    if _head == b"\xef\xbb\xbf":
        with open(xml_path, "r", encoding="utf-8-sig") as _f:
            tree = ET.parse(_f)
    else:
        tree = ET.parse(xml_path)
    root = tree.getroot()

    # Populate the AUTHORITATIVE HTTP-verb lookup table from <con:interface>
    # definitions in this XML (full project exports include them; suite-
    # only exports don't -- in which case the map stays empty and
    # _parse_rest_step falls back to keyword-based inference).
    global _INTERFACE_METHOD_MAP, _PROJECT_ENVIRONMENTS
    _INTERFACE_METHOD_MAP = _build_interface_method_map(root)
    # Same for multi-environment endpoint tables (empty for single-env).
    _PROJECT_ENVIRONMENTS = _build_environment_map(root)

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
            # Test-management annotations (Zephyr / JIRA) live as XML
            # attributes on the `<con:testCase>` element. Empty defaults
            # mean the field wasn't set in SoapUI. Also scoop up any
            # attribute NAME containing "test" / "jira" / "story" / "qtest"
            # so custom-plugin IDs land in `tm_extras` for reporting.
            zephyr_test_id = tc_el.get("zephyrTestId", "")
            zephyr_test_name = tc_el.get("zephyrTestName", "")
            jira = tc_el.get("jira", "")
            tm_extras: dict = {}
            for attr_name, attr_val in tc_el.attrib.items():
                if not attr_val:
                    continue
                # Skip attributes we've already captured or are core SoapUI
                # config (id / name / boolean flags on the testCase element).
                if attr_name in ("id", "name", "zephyrTestId", "zephyrTestName",
                                  "jira", "discardOkResults", "failOnError",
                                  "failTestCaseOnErrors", "keepSession",
                                  "searchProperties", "timeout", "wsrmEnabled",
                                  "wsrmVersion", "wsrmAckTo", "amfAuthorisation",
                                  "amfEndpoint", "amfLogin", "amfPassword",
                                  "disabled", "{http://www.w3.org/2001/XMLSchema-instance}nil"):
                    continue
                lower = attr_name.lower()
                if any(t in lower for t in ("test", "jira", "story", "qtest", "issue", "id")):
                    tm_extras[attr_name] = attr_val
            tc = TestCase(
                id=tc_el.get("id", ""),
                name=tc_el.get("name", ""),
                description=_text(desc_el),
                zephyr_test_id=zephyr_test_id,
                zephyr_test_name=zephyr_test_name,
                jira=jira,
                tm_extras=tm_extras,
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
    # (prefix, case, step_name, prop_name, literal_value, ctx_key)
    # SoapUI Properties-step defaults emitted as ctx.putIfAbsent literals.
    # These are frozen, environment-specific values baked into the source
    # XML -- they should be flagged so authors know what to move to
    # per-env config or replace with fresh <<faker>> tokens.
    frozen_properties: list[tuple] = field(default_factory=list)
    # (soapui_suite, soapui_case, xray_key, java_class_fqn, java_method,
    #  csv_path, cluster_size, cluster_row_index, expected_status)
    # One row per SoapUI test case, showing where its logic ended up in
    # the emitted Java + CSV. Populated by main() after class emission.
    # Bidirectional traceability: verify a specific SoapUI case moved to
    # the right place; find which SoapUI cases share a @Test method.
    case_mapping: list[tuple] = field(default_factory=list)

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

    def add_case_mapping(self, soapui_suite, soapui_case, xray_key,
                           java_class_fqn, java_method, csv_path,
                           cluster_size, cluster_row_index, expected_status):
        """Record where a SoapUI test case ended up in the emitted tree.
        Provides bidirectional traceability -- open the CSV to see which
        SoapUI cases share a method; open a SoapUI case name to find its
        Java class + method + CSV row."""
        self.case_mapping.append((
            soapui_suite, soapui_case, xray_key, java_class_fqn,
            java_method, csv_path, cluster_size, cluster_row_index,
            expected_status))

    def add_frozen_property(self, prefix, case, step_name, prop_name,
                             literal_value, ctx_key):
        """Record a SoapUI Properties-step default value emitted as a
        `ctx.putIfAbsent(ctx_key, literal_value)` line. These frozen
        literals are one of the biggest env-portability + freshness
        risks in the emitted code -- surface them so authors can move
        them to per-env config or replace with faker tokens."""
        self.frozen_properties.append(
            (prefix, case, step_name, prop_name, literal_value, ctx_key))

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
        self._write_csv(
            os.path.join(base, "frozen_properties.csv"),
            ["prefix", "case", "step_name", "prop_name",
             "literal_value", "ctx_key"],
            self.frozen_properties)
        # Case -> Java @Test method mapping. Open this CSV in Excel to
        # verify a specific ReadyAPI case landed in the intended Java
        # class + method, or to find which cases share a data-driven
        # method (multi-row clusters).
        self._write_csv(
            os.path.join(base, "case_to_method_mapping.csv"),
            ["soapui_suite", "soapui_case", "xray_key", "java_class_fqn",
             "java_method", "csv_path", "cluster_size",
             "cluster_row_index", "expected_status"],
            self.case_mapping)

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

        # ---- ReadyAPI -> REST Assured case mapping -------------------
        if self.case_mapping:
            distinct_classes = len({row[3] for row in self.case_mapping})
            distinct_methods = len({(row[3], row[4]) for row in self.case_mapping})
            shared_methods = sum(1 for row in self.case_mapping if row[6] > 1)
            lines.extend([
                "## ReadyAPI -> REST Assured case mapping",
                "",
                "> Bidirectional traceability report. Every ReadyAPI test case",
                "> is listed with its landing spot -- Java class + @Test method +",
                "> CSV row. Verify per-case landings by opening",
                "> `case_to_method_mapping.csv` in Excel.",
                "",
                f"- ReadyAPI cases converted: **{len(self.case_mapping)}**",
                f"- Landed across: **{distinct_classes}** Java classes / "
                f"**{distinct_methods}** @Test methods",
                f"- Cases that share a data-driven method (cluster_size > 1): "
                f"**{shared_methods}**",
                "",
            ])

        # ---- frozen Properties-step literals -------------------------
        # SoapUI Properties-step defaults emitted as ctx.putIfAbsent
        # literals. These are ENV-FROZEN values baked into the source
        # XML at export time -- big risk for env-portability, freshness,
        # and (occasionally) leaking real IDs. Surface every one so
        # authors can decide: keep, move to env config, or replace with
        # <<faker>> tokens. See full list in `frozen_properties.csv`.
        if self.frozen_properties:
            fp_unique_keys: dict[str, set] = defaultdict(set)
            fp_by_prop_name: dict[str, int] = defaultdict(int)
            for prefix, case, step_name, prop_name, val, ctx_key in self.frozen_properties:
                fp_unique_keys[ctx_key].add(val)
                fp_by_prop_name[prop_name] += 1
            multi_valued_keys = {k: sorted(vs) for k, vs in fp_unique_keys.items()
                                 if len(vs) > 1}
            lines.extend([
                "## Frozen Properties-step literals",
                "",
                "> SoapUI `<properties>` step defaults emitted as "
                "`ctx.putIfAbsent(\"<step>.<prop>\", \"<literal>\")` in every method's",
                "> body. These are ENV-FROZEN: run against a different env (prod / "
                "fresh QA) and IDs / domains may not exist there. Reference framework",
                "> (oneqa) uses `<<faker>>` tokens or per-env config for the same",
                "> use case; see `frozen_properties.csv` for the full list.",
                "",
                f"- Total emissions: **{len(self.frozen_properties)}** "
                f"across {len(fp_unique_keys)} unique ctx keys",
                f"- Distinct prop names: **{len(fp_by_prop_name)}**",
                f"- Multi-valued keys (same key, different literals across cases): "
                f"**{len(multi_valued_keys)}** -- indicates the SoapUI author "
                f"varied the seed per case; these should almost certainly be CSV columns",
                "",
                "### Top 15 by prop name (frequency)",
                "",
                "| Prop name | Emissions |",
                "|---|---:|",
            ])
            for prop_name, count in sorted(
                    fp_by_prop_name.items(), key=lambda x: -x[1])[:15]:
                lines.append(f"| `{prop_name}` | {count} |")
            lines.append("")
            if multi_valued_keys:
                lines.extend([
                    "### Multi-valued keys (top 10 -- CSV-column candidates)",
                    "",
                    "| ctx key | distinct literal values |",
                    "|---|---|",
                ])
                for key, vals in list(multi_valued_keys.items())[:10]:
                    preview = ", ".join(
                        f"`{v[:40]}{'...' if len(v) > 40 else ''}`"
                        for v in vals[:4])
                    if len(vals) > 4:
                        preview += f", + {len(vals) - 4} more"
                    lines.append(f"| `{key}` | {preview} |")
                lines.append("")
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

# SoapUI property expression regexes.
#
# `${#SCOPE#property}` for the standard property scopes:
#   Project, TestSuite, TestCase, Global, Env, MockService
# `${step#field}` for step-response references
# `${var}` for bare property names (SoapUI's default namespace lookup --
# resolves against TestCase properties first, then TestSuite, then Project)
_PROJ_PROP_RX = re.compile(r"\$\{#Project#([A-Za-z0-9_.-]+)\}")
_SCOPE_PROP_RX = re.compile(
    r"\$\{#(TestSuite|TestCase|Global|Env|MockService)#([A-Za-z0-9_.-]+)\}")
_STEP_PROP_RX = re.compile(r"\$\{([A-Za-z0-9_-]+)#([A-Za-z0-9_.-]+)\}")
# Bare `${var}` -- only match identifiers that AREN'T already caught
# by one of the scoped patterns above. SoapUI uses this for TestCase-
# level property lookup by default. Skips `${=groovy}` (starts with =)
# and `${#...}` (scoped).
_BARE_PROP_RX = re.compile(r"\$\{(?!#|=)([A-Za-z_][A-Za-z0-9_.-]*)\}")
# `${=<groovy expression>}` -- inline Groovy evaluation. Rare; passes
# through as-is so the emitter can log a TODO for manual review.
_GROOVY_EXPR_RX = re.compile(r"\$\{=([^}]+)\}")


def soapui_expr_to_java(expr: str) -> str:
    """Translate a SoapUI property expression to a Java-code equivalent."""
    if expr is None:
        return "null"
    e = expr
    e = _PROJ_PROP_RX.sub(lambda m: f'config.get("{m.group(1)}")', e)
    # Scoped non-Project props all resolve from mergedRow's config/ctx bag:
    # TestSuite/TestCase properties -> ctx (published by setup); Global/Env
    # -> config; MockService -> ctx (rare, treated as runtime).
    def _scoped(m):
        scope, prop = m.group(1), m.group(2)
        if scope in ("Global", "Env"):
            return f'config.get("{prop}")'
        return f'ctx.get("{prop}")'
    e = _SCOPE_PROP_RX.sub(_scoped, e)
    e = _STEP_PROP_RX.sub(lambda m: f'ctx.get("{m.group(1)}.{m.group(2)}")', e)
    # Bare `${var}` -- default namespace lookup, resolve from ctx (which
    # includes TestCase-level properties published by setup steps).
    e = _BARE_PROP_RX.sub(lambda m: f'ctx.get("{m.group(1)}")', e)
    starts_with_known = any(e.startswith(p) for p in ("config.get", "ctx.get"))
    return f'"{e}"' if not starts_with_known else e


def soapui_body_to_placeholders(body: str) -> tuple[str, list[str]]:
    """Convert SoapUI body's `${#SCOPE#var}`, `${step#field}`, and bare
    `${var}` refs to the framework's `#var#` placeholder syntax so
    RestUtilities.mapJsonValues can substitute at runtime. Returns
    (translated_body, placeholders).

    Scoped placeholders are namespaced by scope prefix so `${#Env#foo}`
    doesn't collide with `${#Project#foo}` when both appear.
    """
    placeholders: list[str] = []
    def _proj(m):
        var = m.group(1)
        placeholders.append(var)
        return f"#{var}#"
    def _scoped(m):
        scope, prop = m.group(1), m.group(2)
        # Namespace with scope so `${#Env#foo}` != `${#Project#foo}`
        var = f"{scope.lower()}_{prop}".replace(".", "_").replace("-", "_")
        placeholders.append(var)
        return f"#{var}#"
    def _step(m):
        var = f"{m.group(1)}_{m.group(2)}".replace(".", "_").replace("-", "_")
        placeholders.append(var)
        return f"#{var}#"
    def _bare(m):
        var = m.group(1).replace(".", "_").replace("-", "_")
        placeholders.append(var)
        return f"#{var}#"
    def _groovy(m):
        # Inline Groovy evaluation -- can't safely translate. Preserve
        # verbatim so a human reader spots it, and emit a placeholder
        # column so tests can override at runtime.
        preview = m.group(1).strip()[:50]
        placeholders.append(f"__GROOVY_EXPR__")
        return f"#groovy_expr#/*{preview}*/"

    translated = _PROJ_PROP_RX.sub(_proj, body or "")
    translated = _SCOPE_PROP_RX.sub(_scoped, translated)
    translated = _STEP_PROP_RX.sub(_step, translated)
    translated = _GROOVY_EXPR_RX.sub(_groovy, translated)
    # Bare ${var} runs LAST so scoped patterns get first pass.
    translated = _BARE_PROP_RX.sub(_bare, translated)
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


# Java reserved words + keywords + literal names. Any identifier that
# matches one of these gets a trailing `_` appended so emitted Java
# compiles. Sourced from JLS 3.9 + boolean/null literals.
_JAVA_RESERVED: set[str] = {
    "abstract", "assert", "boolean", "break", "byte", "case", "catch",
    "char", "class", "const", "continue", "default", "do", "double",
    "else", "enum", "extends", "final", "finally", "float", "for",
    "goto", "if", "implements", "import", "instanceof", "int",
    "interface", "long", "native", "new", "non-sealed", "package",
    "permits", "private", "protected", "public", "record", "return",
    "sealed", "short", "static", "strictfp", "super", "switch",
    "synchronized", "this", "throw", "throws", "transient", "try",
    "void", "volatile", "while", "yield",
    "true", "false", "null", "var",
}


def _transliterate_non_ascii(s: str) -> str:
    """Best-effort Unicode -> ASCII so Japanese/Chinese/umlaut case names
    don't collapse to `unnamed`. Uses NFKD decomposition to strip
    combining marks (`é` -> `e`, `ö` -> `o`), then keeps whatever ASCII
    letters/digits survive. Non-Latin scripts (CJK, Cyrillic, Arabic)
    still fall through to `unnamed` -- but at least accented Latin
    names round-trip cleanly."""
    if not s:
        return s
    if all(ord(c) < 128 for c in s):
        return s
    import unicodedata as _ud
    nfkd = _ud.normalize("NFKD", s)
    return "".join(c for c in nfkd if ord(c) < 128)


def sanitize_identifier(s: str) -> str:
    """Turn a SoapUI name into a valid Java identifier. Guards against
    Java reserved words (appends `_`), leading digits (prepends `_`),
    and non-ASCII characters (transliterates when possible, falls back
    to `unnamed`)."""
    if s:
        s = _transliterate_non_ascii(s)
    s = re.sub(r"[^A-Za-z0-9]", "_", s or "")
    s = re.sub(r"_+", "_", s).strip("_")
    if s and s[0].isdigit():
        s = "_" + s
    if not s:
        return "unnamed"
    # Java reserved-word collision -> suffix with `_` so
    # `class` -> `class_`, `new` -> `new_`, `return` -> `return_`. JLS
    # allows trailing underscore in identifiers.
    if s in _JAVA_RESERVED:
        s = s + "_"
    return s


def to_camel_case(s: str, upper_first: bool = False) -> str:
    """Convert to camelCase (or PascalCase when upper_first=True).
    Unicode-tolerant: non-ASCII chars get transliterated so `テスト_case`
    doesn't collapse to `unnamed`. Java-reserved-word safe: identifiers
    matching Java keywords get a trailing `_` appended."""
    if s:
        s = _transliterate_non_ascii(s)
    parts = re.split(r"[^A-Za-z0-9]+", s or "")
    parts = [p for p in parts if p]
    if not parts:
        return "unnamed"
    if upper_first:
        result = "".join(p[:1].upper() + p[1:] for p in parts)
    else:
        result = parts[0][:1].lower() + parts[0][1:] + "".join(p[:1].upper() + p[1:] for p in parts[1:])
    if result in _JAVA_RESERVED:
        result = result + "_"
    return result


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
    # 0. Strip `__step<N>` suffix added by `_flatten_repeat_endpoint_cases`
    #    so a flattened case derives the same base method name as its
    #    siblings from other SoapUI cases with the same intent.
    m = re.match(r"^(.+?)__step\d+$", working)
    if m:
        working = m.group(1)
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


# HTTP verb -> English word for business-area class naming. Kept small
# and explicit so an unknown verb gets a titlecased fallback rather than
# a Python KeyError -- future verbs (LINK, UNLINK, TRACE, ...) surface
# as themselves in the class name until we map them explicitly.
_HTTP_VERB_WORD = {
    "GET":     "Get",
    "POST":    "Create",
    "PUT":     "Update",
    "PATCH":   "Patch",
    "DELETE":  "Delete",
    "HEAD":    "Head",
    "OPTIONS": "Options",
}

# Last-path-segment words that themselves describe the intent -- when a URL
# ends in one of these (e.g. `/members/{id}/activate`), we use the action
# verb as the class-name verb and the PARENT segment as its noun
# (`ActivateMembersTest`) instead of the boring `<HttpVerb><LastSegment>`
# form (`CreateActivateTest`). Curated from common REST resource verbs
# across SoapUI / Postman collections -- extend as needed.
_URL_ACTION_VERBS = {
    "activate", "deactivate", "verify", "cancel", "refresh", "revoke",
    "invite", "reset", "confirm", "reject", "approve", "search",
    "validate", "merge", "restrict", "unrestrict", "reactivate",
    "unlock", "lock", "resend", "notify", "publish", "unpublish",
    "enable", "disable", "list", "import", "export", "register",
    "login", "logout", "authenticate", "authorize", "assign", "unassign",
    "attach", "detach", "start", "stop", "pause", "resume", "restart",
    "sync", "count", "batch", "bulk", "clone", "duplicate", "archive",
    "unarchive", "restore", "purge", "checkout", "checkin", "submit",
    "approve", "process",
}


def _terminal_rest_step(case: "TestCase") -> Optional["RestStep"]:
    """Return the LAST RestStep in a case's step sequence, or None when
    the case has no REST call (pure Groovy / cleanup cases fall through).

    We key business-area bucketing on the terminal REST step because
    setup calls (login, token, prime data) are shared across many
    intents; the LAST call is what the test is actually testing."""
    for step in reversed(case.steps):
        if isinstance(step, RestStep):
            return step
    return None


def _business_area_from_rest(http_method: str, resource_path: str) -> tuple[str, str]:
    """Derive (resource_slug, operation_class_simple_name) from an HTTP
    verb + resource path. Groups tests by INTENT so N SoapUI cases cluster
    into a handful of business-area classes organized:
        `<suite>.<resource_slug>.<Operation>Test`

    Returns:
      resource_slug -- lowercase snake_case sub-package segment
                       (e.g. `program_account_member`, `guest`).
      op_class_simple -- PascalCase Java class name for the operation
                         (e.g. `CreateTest`, `DeleteTest`, `ActivateTest`).
                         Always ends in `Test`.

    Rules:
      1. Strip path-param segments (`{guestId}`, `{accountId}`).
      2. If the last non-param segment is an action verb (`activate`,
         `verify`, `invite`, ...), the operation is that action and the
         resource is the PARENT segment (`.../members/{id}/activate`
         -> resource `members`, operation `ActivateTest`).
      3. Otherwise: operation = verb word (POST->Create, GET->Get, etc.)
         and resource = last non-param segment.
      4. Root-level (`/`) endpoints fall back to `root` / `<Verb>Test`.
      5. Resource slug is snake_case + Java-package-safe (no reserved
         words, no leading digits).
    """
    verb = (http_method or "GET").upper()
    segs = [s for s in (resource_path or "").split("/") if s]
    non_param = [s for s in segs if not (s.startswith("{") and s.endswith("}"))]
    if not non_param:
        # `POST /` (rare) -- one bucket per verb at the root.
        return "root", _HTTP_VERB_WORD.get(verb, verb.title()) + "Test"
    last = non_param[-1].lower()
    if last in _URL_ACTION_VERBS and len(non_param) >= 2:
        # `.../<resource>/{id}/<action>` -> resource=<resource>, op=<Action>Test
        resource = non_param[-2]
        op = to_camel_case(last, upper_first=True) + "Test"
    elif last in _URL_ACTION_VERBS:
        # `.../<action>` at root -> resource=<action> (best we can do),
        # op=<Verb><Action>Test so multiple verbs on the same action
        # don't collapse into one bucket.
        resource = last
        op = (_HTTP_VERB_WORD.get(verb, verb.title())
              + to_camel_case(last, upper_first=True) + "Test")
    else:
        resource = non_param[-1]
        op = _HTTP_VERB_WORD.get(verb, verb.title()) + "Test"
    # Snake_case + package-safe slug for the sub-package name.
    slug = re.sub(r"[^A-Za-z0-9]+", "_", resource).strip("_").lower()
    if not slug:
        slug = "root"
    if slug[0].isdigit():
        slug = "_" + slug
    if slug in _JAVA_RESERVED:
        slug = slug + "_"
    return slug, op


# ---------------------------------------------------------------------------
# Frozen-Properties migration -- turn SoapUI Properties-step defaults from
# hardcoded `ctx.putIfAbsent("key", "literal")` emissions into either
# per-row CSV columns (when the SoapUI author varied the value per case)
# or per-env Config lookups (when the value is constant across cases).
# Never auto-replaces with `<<faker>>` -- that could break tests that
# reference specific pre-seeded QA data records. Faker migration is left
# as a manual opt-in the user does after seeing the audit report.
# ---------------------------------------------------------------------------

_EMAIL_RX = re.compile(r'^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$', re.I)
_PHONE_RX = re.compile(r'^\d{7,15}$')
_UUID_RX  = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I)


def _detect_property_shape(values) -> str:
    """Return a shape label for a set of literal values: 'email' | 'phone'
    | 'uuid' | 'literal'. Used only for the audit report -- the migrator
    itself doesn't switch on shape. Empty input returns 'literal'."""
    if not values:
        return 'literal'
    if all(_EMAIL_RX.match(v or "") for v in values):
        return 'email'
    if all(_PHONE_RX.match(v or "") for v in values):
        return 'phone'
    if all(_UUID_RX.match(v or "") for v in values):
        return 'uuid'
    return 'literal'


def _classify_frozen_properties(cases: list["TestCase"]) -> dict:
    """Analyze all `PropertiesStep` values across the given cases and
    return a migration plan keyed by ctx_key. The Emitter uses this plan
    to decide whether each `ctx.putIfAbsent(...)` line becomes a CSV
    column lookup or a Config lookup at emission time.

    Returns
    -------
    dict[str, dict] with per-key entries:
        destination     -- 'csv' if the SoapUI author varied the value
                           per case, 'config' if constant across cases
        shape           -- 'email' | 'phone' | 'uuid' | 'literal'
                           (audit-report metadata, not used for routing)
        default         -- most-common literal value; used as the code-
                           level fallback when the CSV row or Config
                           lookup returns empty
        per_case        -- {case_name: literal_value} map so the CSV
                           emitter can populate per-row columns for the
                           'csv' destination
        distinct_count  -- number of unique literal values across cases
    """
    key_case_values: dict[str, dict[str, str]] = defaultdict(dict)
    for case in cases:
        for step in case.steps:
            if isinstance(step, PropertiesStep):
                for prop, val in (step.properties or {}).items():
                    if val:
                        ctx_key = f'{step.step_name}.{prop}'
                        # Last-write-wins if the same case has this step twice.
                        key_case_values[ctx_key][case.name] = val

    from collections import Counter as _Counter
    migration: dict[str, dict] = {}
    for ctx_key, case_values in key_case_values.items():
        distinct = set(case_values.values())
        # Most-common value = the fallback default. Deterministic tie-break
        # via the sorted() call so re-runs don't churn the emitted default.
        most_common = _Counter(case_values.values()).most_common()
        top_count = most_common[0][1]
        tied = sorted(v for v, c in most_common if c == top_count)
        default = tied[0]
        migration[ctx_key] = {
            'destination': 'csv' if len(distinct) > 1 else 'config',
            'shape':       _detect_property_shape(distinct),
            'default':     default,
            'per_case':    dict(case_values),
            'distinct_count': len(distinct),
        }
    return migration


def _is_token_fetch_step(step: "RestStep") -> bool:
    """Heuristic: does this REST step look like an OAuth token fetch?

    Signature (both must match):
      - POST verb
      - URL path ends in one of the well-known token endpoints:
        `/token`, `/oauth/token`, `/access-token`, `/access_token`,
        `/oauth2/token`, `/authorize`, or ANY path containing `/realms/`
        and ending in `/token` (Keycloak / custom OAuth realm servers).
      - Request body / form params / headers reference AT LEAST ONE of
        the classic OAuth credential fields: `grant_type`, `client_id`,
        `client_secret`, `refresh_token`, or the classic username +
        password pair. Covers form-encoded (grant_type), JSON with
        credentials (client_id + client_secret), and password grant
        (username + password) all in one check.

    False positives here would wrap a real business call in an
    if-not-cached guard (bad); false negatives just leave a redundant
    token fetch inline (harmless). The path suffix requirement keeps
    the false-positive rate low -- normal REST resources rarely end in
    `/token` and don't also carry client_id/client_secret."""
    if (step.http_method or "").upper() != "POST":
        return False
    path = (step.resource_path or "").lower()
    ends_in_token_endpoint = any(path.endswith(p) for p in (
        "/token", "/oauth/token", "/oauth2/token",
        "/access-token", "/access_token", "/authorize",
    ))
    if not ends_in_token_endpoint:
        return False
    corpus = "\n".join([
        step.request_body or "",
        *(step.headers.values() if step.headers else []),
        *(step.query_params.values() if step.query_params else []),
    ]).lower()
    # At least one OAuth credential field must be present.
    if any(tok in corpus for tok in
           ("grant_type", "client_id", "client_secret", "refresh_token")):
        return True
    # Password-grant pattern: BOTH username and password.
    return "username" in corpus and "password" in corpus


def _business_bucket_of_case(case: "TestCase") -> tuple[str, str]:
    """Business-area (resource_slug, operation_class) for a whole case.

    Setup-only cases (no REST call) land in `misc/CleanupOrSetupTest.java`
    so they still get executed but don't pollute a real resource bucket."""
    step = _terminal_rest_step(case)
    if step is None:
        return "misc", "CleanupOrSetupTest"
    return _business_area_from_rest(step.http_method, step.resource_path)


def _body_shape_key(step: "RestStep") -> str:
    """Return a stable body-shape fingerprint for clustering purposes.
    Reused by `_cluster_cases_by_shape`, `_flatten_repeat_endpoint_cases`,
    and `_merge_prefix_clusters` so all three see the same equality."""
    import json as _json, re as _re
    if not step.request_body.strip():
        return "-"
    translated, _ = soapui_body_to_placeholders(step.request_body)
    try:
        return "j:" + _shape_sig(_json.loads(translated))
    except (_json.JSONDecodeError, ValueError):
        return "raw:" + _re.sub(r"\s+", "", translated)[:120]


def _rest_shape_sig(case: "TestCase") -> tuple:
    """Full REST-step signature of a case: tuple of
    (verb, path, media-type, body-shape) per REST step in emission
    order. Used by both shape-clustering and prefix-merge to compare
    cases.

    Media type is part of the key so a JSON POST at /foo doesn't
    cluster with an XML POST at /foo -- they need different Content-Type
    headers, and the generated client method's ContentType is baked in
    at emit time from the FIRST occurrence in the cluster. Without the
    guard, the second occurrence would send its body under the first's
    content-type and get 415 Unsupported Media Type."""
    return tuple(
        (s.http_method, s.resource_path,
         (s.media_type or "application/json").split(";")[0].strip().lower(),
         _body_shape_key(s))
        for s in case.steps if isinstance(s, RestStep)
    )


def _merge_prefix_clusters(
    clusters: list[list["TestCase"]]
) -> list[tuple[list["TestCase"], dict[str, str]]]:
    """When one cluster's REST-step signature is a strict PREFIX of another
    (shorter case's flow is a truncated version of the longer's), fold the
    shorter cluster into the longer one. Emitted method runs the LONGEST
    case's full step sequence; shorter cases get a `_stop_after` CSV cell
    naming the last REST step they should execute before returning early.

    Guards:
      - Only merges when prefix match is EXACT on (verb, path, body-shape)
        for every REST step in the shorter signature.
      - Doesn't merge two clusters of equal length (that's regular shape
        clustering's job).
      - Preserves clusters whose signatures don't prefix-match anyone
        else as-is (with an empty stop_markers dict).

    Returns: list of (merged_cluster, {case_name -> stop_after_step_name}).
    A missing case name in stop_markers means "run everything" (i.e.,
    the case IS the longest one).
    """
    # Compute sig once per cluster.
    sigs = [_rest_shape_sig(cl[0]) for cl in clusters]

    # Sort by (sig length desc, case-count desc, first-case-index asc) so:
    #   - Longer clusters process first (they absorb shorter prefixes).
    #   - Among ties on length, the LARGER cluster wins absorption — a
    #     short prefix ends up in the biggest bucket for the strongest
    #     data-driven grouping.
    #   - Final tie-break on original order gives stable, reproducible
    #     naming across re-runs when inputs don't change.
    order = sorted(
        range(len(clusters)),
        key=lambda i: (-len(sigs[i]), -len(clusters[i]), i))
    consumed: set[int] = set()
    out: list[tuple[list["TestCase"], dict[str, str]]] = []

    for base_idx in order:
        if base_idx in consumed:
            continue
        base_cluster = list(clusters[base_idx])
        base_sig = sigs[base_idx]
        stop_markers: dict[str, str] = {}
        # Look for shorter clusters whose sig is a prefix of base_sig.
        for other_idx in order:
            if other_idx == base_idx or other_idx in consumed:
                continue
            other_sig = sigs[other_idx]
            if len(other_sig) >= len(base_sig):
                continue
            if base_sig[:len(other_sig)] != other_sig:
                continue
            # Prefix match -- fold in. Use POSITIONAL step-index (into the
            # LONGER cluster's REST-step sequence) as the stop marker, NOT
            # the shorter's step-name. Two reasons this matters:
            #   1. Cluster members whose (verb, path, body-shape) match can
            #      still differ in their `step_name` attributes -- the
            #      emitted Java walks the LONGER cluster's steps and uses
            #      its own names, so a shorter's step-name may never appear.
            #   2. If the LONGER flow legitimately repeats a step name
            #      (e.g. `http_request_200_1` twice), a name-based check
            #      would fire at the FIRST occurrence and skip work the
            #      shorter scenario expected to do.
            # The marker is stored as a stringified int so it round-trips
            # cleanly through CSV.
            stop_after_index = len(other_sig)  # exit after the Nth REST step
            for c in clusters[other_idx]:
                stop_markers[c.name] = str(stop_after_index)
            base_cluster.extend(clusters[other_idx])
            consumed.add(other_idx)
        out.append((base_cluster, stop_markers))
        consumed.add(base_idx)
    return out


def _flatten_repeat_endpoint_cases(cases: list["TestCase"]) -> list["TestCase"]:
    """When a SoapUI case bundles N REST calls to the same (verb, path,
    body-shape) as one test case, treat those N calls as N independent
    scenarios: split into N pseudo-cases each with the shared setup +
    ONE REST call. Downstream clustering then folds pseudo-cases from
    multiple SoapUI cases into a single @Test method with N CSV rows.

    Guards against wrong-splitting cases where the N calls are meant to
    run sequentially with state carried between them:
      - Requires ALL REST steps to share (verb, path, body-shape). A
        `create -> activate -> verify` case (3 different endpoints)
        stays unsplit because its shape signature isn't uniform.
      - Requires REST steps to be CONTIGUOUS at the end -- no non-REST
        logic (Groovy, Transfer, Properties) between them. If a case
        has `REST1 -> Groovy -> REST2`, the Groovy might publish state
        REST2 depends on, so we leave it alone.
      - Single-REST-step cases pass through untouched.

    Pseudo-case naming: `<original>__step<N>` so `test_case_id` in the
    CSV row can be traced back to the source SoapUI case + step index.
    """
    out: list["TestCase"] = []
    for c in cases:
        rest_steps = [s for s in c.steps if isinstance(s, RestStep)]
        if len(rest_steps) < 2:
            out.append(c)
            continue

        # Uniform shape across every REST step?
        keys = {(s.http_method, s.resource_path, _body_shape_key(s)) for s in rest_steps}
        if len(keys) > 1:
            out.append(c)
            continue

        # REST steps must be contiguous at the end -- no non-REST between them.
        rest_started = False
        contiguous = True
        for s in c.steps:
            if isinstance(s, RestStep):
                rest_started = True
            elif rest_started:
                contiguous = False
                break
        if not contiguous:
            out.append(c)
            continue

        # NEW: refuse to flatten when REST[i] references any earlier REST's
        # response via `${prevStep#field}`. Even without an explicit Transfer
        # or Groovy step in between, a case may thread state through the
        # SoapUI property namespace directly (e.g. path param `{accountId}`
        # bound to `${create#Response#$['id']}`). Splitting would leave
        # REST[i]'s pseudo-case with no producer for that reference.
        rest_step_names = {s.step_name for s in rest_steps}
        has_inter_rest_dep = False
        for i, rs in enumerate(rest_steps):
            # A step's body, headers, path params, query params, and
            # assertion configs can all contain ${prevStep#field} refs.
            texts = [rs.request_body or ""]
            texts.extend(rs.headers.values())
            texts.extend(rs.path_params.values())
            texts.extend(rs.query_params.values())
            for a in rs.assertions:
                for v in (a.config or {}).values():
                    texts.append(v or "")
            corpus = "\n".join(texts)
            for m in _STEP_PROP_RX.finditer(corpus):
                referenced_step = m.group(1)
                # Refs to any other REST step in THIS case = inter-REST
                # dependency; splitting would break the chain. Refs to
                # non-REST steps (Groovy/Properties) are fine -- those live
                # in setup_steps and get replayed for every pseudo-case.
                if referenced_step in rest_step_names and referenced_step != rs.step_name:
                    has_inter_rest_dep = True
                    break
            if has_inter_rest_dep:
                break
        if has_inter_rest_dep:
            out.append(c)
            continue

        # Emit N pseudo-cases. Setup steps are SHARED across pseudo-cases;
        # each pseudo-case ends with one distinct REST step (which carries
        # its own assertions).
        setup_steps = [s for s in c.steps if not isinstance(s, RestStep)]
        for idx, rest in enumerate(rest_steps, start=1):
            pseudo = TestCase(
                id=f"{c.id}__step{idx}",
                name=f"{c.name}__step{idx}",
                description=c.description,
                steps=list(setup_steps) + [rest],
            )
            out.append(pseudo)
    return out


def _cluster_cases_by_shape(cases: list["TestCase"]) -> list[list["TestCase"]]:
    """Group cases whose REST-step shape is IDENTICAL into clusters that
    can share a single @Test method + a multi-row CSV.

    Cluster key = tuple of (verb, resource_path, body-SHAPE) for every
    REST step in order. Two cases cluster iff they:
      - hit the same endpoints in the same order,
      - use the same HTTP verbs,
      - use the same JSON body STRUCTURE (keys + leaf types), even when
        their leaf VALUES differ -- the literal-value diffs become
        `#tpl_<jsonpath>#` placeholders via emit_templates_deduplicated
        and land as per-row CSV cells at runtime.

    Body shape (not exact hash) is intentional: scenario variants of one
    operation typically share body structure but differ in field values;
    keying on exact hash would leave every variant in its own singleton
    cluster (defeating the reference-framework pattern of "one method,
    N data rows"). Non-JSON bodies fall back to a raw-text fingerprint
    since we can't structurally normalize them.

    Preserves discovery order (first case in each cluster keeps its
    original position). Returns list of clusters where each cluster
    is 1..N cases in the order they appeared in the SoapUI XML."""
    def sig(case: "TestCase") -> tuple:
        return _rest_shape_sig(case)

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


_SECRET_LEAF_KEY_HINTS = (
    "password", "passwd", "secret", "apikey", "api_key",
    "authorization", "auth_token", "access_token", "refresh_token",
    "clientsecret", "client_secret", "privatekey", "private_key",
    "credential",
)


def _is_secret_path(path: str) -> bool:
    """True when the last segment of a JSON path names a credential-ish
    field. These should NOT be extracted as per-row CSV cells even if
    values differ across cluster members -- surfacing secrets in CSVs
    is a policy leak and per-row secret variation is almost always
    accidental (usually just one test using a different sandbox account).
    Keep the literal in the merged template (from case[0]) and let
    users move it to env config later if they need to.
    """
    if not path:
        return False
    # Last dot-separated segment, stripped of array index suffix `[N]`.
    last = re.sub(r"\[\d+\]$", "", path.rsplit(".", 1)[-1]).lower()
    return any(h in last for h in _SECRET_LEAF_KEY_HINTS)


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

    Secret-looking leaves (password / secret / token / credential / etc.)
    are EXEMPTED from placeholder extraction: keep case[0]'s literal in
    the merged template regardless of whether values differ. Prevents
    credentials from leaking into per-row CSV cells.
    """
    import json as _json
    # Gather every leaf position across the group, unify by path.
    all_leaves_per_tree = [dict(_walk_leaves(t)) for t in trees]
    all_paths = sorted(set().union(*[set(d) for d in all_leaves_per_tree]))

    # For each path, check whether every tree has the same value.
    varying_paths: set = set()
    for p in all_paths:
        # Never extract secret-looking leaves. Case[0]'s literal stays in
        # the merged template; users can move it to Config later.
        if _is_secret_path(p):
            continue
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


def _soapui_xpath_to_jsonpath(xpath: str) -> str:
    """Translate SoapUI's XPath-with-namespace notation (used in
    MessageContentAssertion) into a simple JSON-path-like accessor.

    SoapUI shows JSON responses as XML internally, so a MessageContent
    XPath like `//ns1:Response[1]/ns1:accountId[1]` really means the
    JSON field `accountId` on the response root. Strip namespaces,
    positional [1] indices, and the outer `Response` wrapper; keep any
    non-trivial [N] positional indices as list-index JSON-path segments.

    Examples:
      `declare namespace ns1='...'; //ns1:Response[1]/ns1:accountId[1]`
        -> `accountId`
      `//ns1:Response[1]/ns1:members[3]/ns1:role[1]`
        -> `members[3-1].role` (0-indexed conversion) = `members[2].role`
    """
    if not xpath:
        return ""
    # Drop namespace declarations
    p = re.sub(r"declare\s+namespace\s+\w+\s*=\s*'[^']*'\s*;\s*", "", xpath).strip()
    # Split into steps
    steps = [s for s in p.split("/") if s.strip()]
    out_parts: list[str] = []
    for step in steps:
        # Strip namespace prefix
        step = re.sub(r"^[A-Za-z_][A-Za-z0-9_]*:", "", step)
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)(?:\[(\d+)\])?$", step)
        if not m:
            continue
        name, idx = m.group(1), m.group(2)
        # Drop the outer `Response` wrapper (SoapUI's synthetic root).
        if name.lower() == "response" and not out_parts:
            continue
        # SoapUI XPath is 1-indexed; JsonPath / GPath is 0-indexed. Only
        # emit [N] when the index is > 1 (positional filter, not the
        # trivial "first element" that SoapUI emits by default).
        if idx and int(idx) > 1:
            out_parts.append(f"{name}[{int(idx) - 1}]")
        else:
            out_parts.append(name)
    return ".".join(out_parts)


def _jsonpath_to_gpath(path: str) -> str:
    """Rewrite Jayway/JsonPath.com syntax (SoapUI's convention) into the
    Groovy GPath syntax RestAssured's default `.jsonPath()` parser uses.

    Supported (translated):
      `$[*]['guestId']`             -> `[*].guestId`
      `$.notifications[0].message`  -> `notifications[0].message`
      `$['a']['b'].c`               -> `a.b.c`

    UNSUPPORTED (translator returns path as-is; GPath will likely reject
    it or match the wrong node -- these need manual review):
      `$..field`                    recursive descent (GPath uses **)
      `$[?(@.status=='active')]`    filter expressions
      `$[0,2,4]` / `$['a','b']`     union indexing
      `$.a[?(@.b)].c`               inline filters
      `$.a[(len-1)]`                script indexing

    Idempotent: paths already in GPath form pass through untouched."""
    if not path:
        return path
    # Bail out early on unsupported syntax so we don't mistranslate --
    # let the caller notice a passthrough and either fail loud at
    # runtime or hand-fix. Detection is intentionally conservative.
    if (".." in path
            or "[?(" in path
            or re.search(r"\[[^]]*,[^]]*\]", path)  # union: [a,b] or ['a','b']
            or re.search(r"\[\([^)]*\)\]", path)):  # script index: [(expr)]
        return path  # leave unchanged; runtime will surface the issue
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


def _jsonpath_is_gpath_incompatible(path: str) -> bool:
    """True when the JsonPath uses syntax GPath doesn't support --
    caller emits a `// TODO` comment + skips the assertion to avoid
    a runtime IllegalArgumentException from `.jsonPath().getString(path)`."""
    if not path:
        return False
    return (".." in path
            or "[?(" in path
            or bool(re.search(r"\[[^]]*,[^]]*\]", path))
            or bool(re.search(r"\[\([^)]*\)\]", path)))


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


def _assert_element_cols(a: "Assertion", step_name: str) -> list[tuple[str, str]]:
    """For assertion types whose configuration wraps N element-level checks
    (MessageContentAssertion, DataAndMetadataAssertion), return one
    (col_name, default_value) tuple per ENABLED element. The translator
    (`_render_message_content_assertion` / `_render_data_and_metadata_assertion`)
    emits matching `row.get(col_name)` lookups keyed off the SAME
    naming scheme; this helper feeds `emit_csv_per_method` so the CSV
    header carries columns for every user-overridable value.

    Non-multi-element assertion types return empty."""
    if a.type not in ("MessageContentAssertion", "DataAndMetadataAssertion"):
        return []
    prefix = ("msgcontent" if a.type == "MessageContentAssertion"
              else "datameta")
    out: list[tuple[str, str]] = []
    for idx, el in enumerate(a.elements):
        if (el.get("enabled", "true") or "").lower() == "false":
            continue
        elem_name = el.get("element", "") or f"el{idx}"
        col = (f"expected_{sanitize_identifier(step_name)}"
               f"_{prefix}_{sanitize_identifier(elem_name)}")
        val = el.get("expectedValue", "") or el.get("content", "")
        out.append((col, val))
    return out


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
        # Populated by main() before rendering. See _classify_frozen_properties.
        # Maps ctx_key -> {destination, shape, default, per_case, distinct_count}.
        # Consumed by _render_step's PropertiesStep branch AND
        # emit_csv_per_method (which injects per-case columns for CSV
        # destinations). Empty dict = no migration (fall back to literals).
        self._property_migration: dict = {}
        # Populated by emit_templates_class (after emit_templates_deduplicated).
        # Maps `templates/<suite>/<subdir>/<file>.<ext>` -> Java constant
        # name in the emitted Templates class. Consumed by
        # `_render_rest_step_body` so call sites emit
        # `Templates.<NAME>` instead of two literal string args.
        self._template_const_by_path: dict[str, str] = {}
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
        # Populated by emit_test_class_per_suite when prefix-merged
        # clusters are in play. Keyed by cluster index -> {case_name ->
        # stop_after_step_name}. Empty when no prefix-merge happened.
        self._stop_markers_per_cluster: dict[int, dict[str, str]] = {}
        # Populated by _render_test_method_v2 before each method render.
        # Keys are the REST-step position index in cluster[0]'s step
        # sequence; values are the UNION of unique Assertion objects
        # across every case in the current cluster at that position.
        # _render_rest_step_body reads this instead of step.assertions
        # so cluster members' extra assertions don't silently vanish.
        self._cluster_asserts_by_pos: dict[int, list] = {}
        # Running counter incremented inside _render_rest_step_body so
        # it can index into _cluster_asserts_by_pos. Reset per method.
        self._current_rest_step_pos: int = 0

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

    @staticmethod
    def _java_content_type_for(mime: str) -> tuple[str, str]:
        """Map an HTTP media type to the Java expression that produces
        the right RestAssured ContentType. Returns (expression, accept_hint).
        Unknown types fall through to a raw string literal that
        RestAssured accepts via `.contentType(String)`."""
        m = (mime or "").lower()
        if m in ("application/json", "application/vnd.api+json") or m.endswith("+json"):
            return ("ContentType.JSON", "application/json")
        if m in ("text/xml", "application/xml", "application/soap+xml") or m.endswith("+xml"):
            return ("ContentType.XML", "application/xml")
        if m == "application/x-www-form-urlencoded":
            return ("ContentType.URLENC", "application/x-www-form-urlencoded")
        if m == "multipart/form-data":
            return ('"multipart/form-data"', "multipart/form-data")
        if m == "text/plain":
            return ("ContentType.TEXT", "text/plain")
        if m == "text/html":
            return ("ContentType.HTML", "text/html")
        if m == "application/octet-stream":
            return ("ContentType.BINARY", "application/octet-stream")
        # Unknown: pass through as a string literal (RestAssured has an
        # overload accepting arbitrary String content-types).
        return (f'"{m}"', m)

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
        """Render one Java method wrapping the REST call.

        Content-type aware: the media_type from the source SoapUI step
        drives which ContentType helper is chosen in the emitted Java
        so XML / form-encoded / multipart bodies aren't sent under
        `application/json` (which used to produce 415 Unsupported Media
        Type at runtime). Callers with a DIFFERENT media type on the
        same client method can pass an override via the per-call
        RestAssured spec (a follow-up refactor)."""
        m_name = override_name or to_camel_case(op_name or step.step_name, upper_first=False)
        verb = step.http_method
        # Path params from resource_path -- sanitize each name so Java
        # reserved words (`class`, `new`, `return`, etc.) get a `_` suffix.
        raw_path_params = re.findall(r"\{([A-Za-z0-9_]+)\}", path)
        # Map raw-name -> safe-name so we can rewrite the replace() calls too.
        path_param_map = {p: sanitize_identifier(p) for p in raw_path_params}
        java_path_params = ", ".join(
            f"String {path_param_map[p]}" for p in raw_path_params)
        # Build method params: (String token, [path_params...], [String body if POST/PUT/PATCH])
        params = ["String token"]
        if java_path_params:
            params.append(java_path_params)
        needs_body = verb in ("POST", "PUT", "PATCH")
        if needs_body:
            params.append("String requestBody")
        params_str = ", ".join(params)

        # Runtime path substitution (rewrite `{raw}` -> safe-name lookup).
        path_expr = f'"{path}"'
        for p in raw_path_params:
            path_expr = f'{path_expr}.replace("{{{p}}}", {path_param_map[p]})'

        # Content-type dispatch: normalize the media_type and pick the
        # right ContentType constant + Accept header. Unknown media types
        # fall through to a raw string content-type (RestAssured accepts
        # arbitrary strings, sends them as the Content-Type header).
        raw_mt = (step.media_type or "application/json").split(";")[0].strip().lower()
        content_type_expr, accept_helper = self._java_content_type_for(raw_mt)

        # Build headers block. For JSON we use the existing Headers builder;
        # for non-JSON we build the map inline with the right content type
        # so nothing forces application/json on top.
        if raw_mt == "application/json":
            headers_block = """Map<String, String> headers = Headers.builder()
                .contentTypeJson()
                .acceptJson()
                .header("Authorization", token)
                .correlationId()
                .build();"""
        else:
            headers_block = f"""Map<String, String> headers = new java.util.HashMap<>();
        headers.put("Content-Type", "{raw_mt}");
        headers.put("Accept", "{raw_mt}");
        headers.put("Authorization", token);"""

        # Call site per verb -- ContentType is set explicitly on every
        # body-bearing verb so it matches the headers map above.
        body_chain = ".body(requestBody)" if needs_body else ""
        content_chain = (f".contentType({content_type_expr})"
                         if needs_body else "")
        verb_call = verb.lower()
        if verb == "GET":
            call = ('Response res = RestAssured.given()\n'
                    '                .headers(headers)\n'
                    '                .get(baseUrl + path);')
        elif verb == "DELETE":
            call = ('Response res = RestAssured.given()\n'
                    '                .headers(headers)\n'
                    '                .delete(baseUrl + path);')
        elif verb in ("POST", "PUT", "PATCH"):
            # Use the direct RestAssured chain uniformly (no more
            # RestUtilities.getResponsePost which was JSON-hardcoded).
            call = ('Response res = RestAssured.given()\n'
                    '                .headers(headers)\n'
                    f'                {content_chain}\n'
                    f'                {body_chain}\n'
                    f'                .{verb_call}(baseUrl + path);')
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

    def _reset_per_method_state(self) -> None:
        """Clear per-method emit state -- called at the start of every
        test method AND at the start of every SetupHelper method."""
        self.response_var_by_step = {}
        self._locals_in_method = {"testCaseId", "exp"}
        self._step_suffix_by_name = {}
        # Reset the cluster REST-step position counter so the next method
        # walks _cluster_asserts_by_pos from position 0 again.
        self._current_rest_step_pos = 0

    def _render_step(self, step, service_class_name: str) -> list[str]:
        """Render one step's Java lines. Shared by test-method emission
        AND SetupHelper emission so bug fixes benefit both."""
        lines: list[str] = []
        if isinstance(step, RestStep):
            lines.extend(self._render_rest_step_body(step, service_class_name))
        elif isinstance(step, GroovyStep):
            # Console marker so a groovy-side hang or long-running side
            # effect is attributable in the log stream.
            lines.append(
                f'LOG.info(" .. groovy step: {_jlit(step.step_name)}");')
            lines.extend(self._render_groovy_translated(step))
        elif isinstance(step, PropertiesStep):
            lines.append(f'// [properties step] {step.step_name} -- values '
                         'resolved via TestSupport.testData(row, key):')
            lines.append('//   1. CSV row cell  2. Config test_data.<key>  '
                         '3. bundled test_data_defaults JSON  4. ""')
            for prop, val in (step.properties or {}).items():
                if not val:
                    continue
                ctx_key = f'{step.step_name}.{prop}'
                # Track the original SoapUI literal for the audit even
                # after we migrate the emission -- the "frozen" audit
                # reflects what the SOURCE XML shipped, not what we emit.
                self.ledger.add_frozen_property(
                    self._current_prefix, self._current_case,
                    step.step_name, prop, val, ctx_key)
                # Single emission shape regardless of destination -- the
                # runtime helper walks the precedence chain and finds the
                # right value. No hardcoded literal in the emitted Java.
                lines.append(
                    f'ctx.putIfAbsent("{ctx_key}", '
                    f'TestSupport.testData(row, "{ctx_key}"));')
        elif isinstance(step, DataSourceStep):
            lines.append(
                f'// [datasource step] {step.step_name} -- iteration comes '
                f'from the CSV data-provider; datasource type: {step.ds_type}')
            if step.file_path:
                lines.append(
                    f'// [datasource external file] "{_jlit(step.file_path)}" '
                    f'-- migrate this file into src/test/resources/csv/ and '
                    f'point PerMethodCsvDataProvider at it, or hand-populate '
                    f'the method CSV with the same rows.')
                lines.append(
                    f'LOG.warn("STUB DataSource external file: {_jlit(step.file_path)}");')
        elif isinstance(step, TransferStep):
            lines.extend(self._render_transfer_translated(step))
        elif isinstance(step, ManualStep):
            desc = (step.description or "").strip()
            desc_short = " ".join(desc.split())[:80]
            lines.append(
                f'// [manualTestStep] {step.step_name} -- documentation '
                f'only (no runtime action): {desc_short}')
        elif isinstance(step, JdbcStep):
            q_escaped = _jlit((step.query or "").replace("\r", " ").replace("\n", " "))
            lines.append(f'// [jdbc step] {step.step_name}')
            lines.append('if (Db.isConfigured()) {')
            lines.append(f'    Db.execute("{q_escaped}");')
            lines.append('} else {')
            lines.append(
                f'    LOG.warn("Skipping JDBC step (Db not configured): '
                f'{_jlit(step.step_name)}");')
            lines.append('}')
        elif isinstance(step, CallTestCaseStep):
            # SoapUI `calltestcase` invokes another test case by name.
            # In our emitted model each SoapUI case becomes ONE @Test
            # method, so calling from inside another method would run
            # a data-driven test outside its DataProvider context --
            # not straightforward. Emit a TODO with the target coords.
            lines.append(
                f'// [calltestcase] {step.step_name} -- calls SoapUI test '
                f'case: {step.target_test_suite}/{step.target_test_case}')
            lines.append(
                f'// TODO manual: extract the target test case\'s body into '
                f'a shared helper and call it here.')
            lines.append(
                f'LOG.warn("STUB calltestcase {_jlit(step.target_test_case or step.step_name)}");')
        elif isinstance(step, DelayStep):
            lines.append(f'// [delay step] {step.step_name} -- sleep {step.delay_ms}ms')
            lines.append(f'try {{ Thread.sleep({step.delay_ms}L); }} '
                         f'catch (InterruptedException __ie) {{ '
                         f'Thread.currentThread().interrupt(); }}')
        elif isinstance(step, GotoStep):
            # goto/conditional-goto can't be mechanically translated into
            # Java (no `goto`; refactoring into if/loop needs human review).
            lines.append(
                f'// [gotostep] {step.step_name} -- {len(step.conditions)} '
                f'condition(s). Java has no goto; refactor the surrounding '
                f'code into a loop/if.')
            for c in step.conditions:
                lines.append(f'//   IF ({c.get("expression", "?")}) '
                             f'GOTO {c.get("target_step", "?")}')
            lines.append(
                f'LOG.warn("STUB gotostep {_jlit(step.step_name)} -- '
                f'manual refactor required");')
        elif isinstance(step, SoapRequestStep):
            # SOAP request: send raw XML body with text/xml content-type.
            # Uses generic RestAssured; assumes framework's request-spec
            # allows a body + endpoint override.
            body_lit = _jlit((step.request_body or "").strip())
            ep_lit = _jlit(step.endpoint or "")
            lines.append(f'// [soaprequest] {step.step_name} '
                         f'(operation={_jlit(step.operation)})')
            resp_var = f"{sanitize_identifier(step.step_name)}Res"
            self._locals_in_method.add(resp_var)
            self.response_var_by_step[step.step_name] = resp_var
            lines.append(
                f'Response {resp_var} = io.restassured.RestAssured.given()'
                f'.contentType("{step.media_type}")'
                f'.body("{body_lit}")'
                f'.post("{ep_lit}");')
            lines.append(
                f'RestUtilities.logResponseBody(testCaseId, holder, '
                f'RestUtilities.getResponseAsString({resp_var}));')
            for a in step.assertions:
                if a.disabled:
                    continue
                emitted, _cov = self._render_assertion(
                    a, resp_var, step.step_name, suffix="", assertion_index=0)
                lines.extend(emitted)
        elif isinstance(step, HttpRequestStep):
            # Raw HTTP (not REST-resource-backed). Emit a generic given()
            # call to the endpoint with the right verb + body.
            body_lit = _jlit((step.request_body or "").strip())
            ep_lit = _jlit(step.endpoint or "")
            verb = (step.http_method or "GET").lower()
            has_body = step.http_method in ("POST", "PUT", "PATCH") and step.request_body.strip()
            lines.append(f'// [httprequest] {step.step_name} '
                         f'({step.http_method} {ep_lit})')
            resp_var = f"{sanitize_identifier(step.step_name)}Res"
            self._locals_in_method.add(resp_var)
            self.response_var_by_step[step.step_name] = resp_var
            body_chain = f'.body("{body_lit}")' if has_body else ""
            lines.append(
                f'Response {resp_var} = io.restassured.RestAssured.given()'
                f'.contentType("{step.media_type}"){body_chain}'
                f'.{verb}("{ep_lit}");')
            lines.append(
                f'RestUtilities.logResponseBody(testCaseId, holder, '
                f'RestUtilities.getResponseAsString({resp_var}));')
            for a in step.assertions:
                if a.disabled:
                    continue
                emitted, _cov = self._render_assertion(
                    a, resp_var, step.step_name, suffix="", assertion_index=0)
                lines.extend(emitted)
        elif isinstance(step, MockResponseStep):
            lines.append(
                f'// [mockresponse] {step.step_name} -- SoapUI mock-server '
                f'step, not runnable in a pure REST Assured test. Skipped.')
            lines.append(
                f'LOG.warn("STUB mockresponse {_jlit(step.step_name)}");')
        elif isinstance(step, JmsStep):
            lines.append(
                f'// [jms {step.direction}] {step.step_name} '
                f'destination={_jlit(step.destination)} '
                f'-- REST Assured does not cover JMS; manual translation required.')
            lines.append(
                f'LOG.warn("STUB jms {_jlit(step.step_name)}");')
        else:
            cls_name = type(step).__name__
            step_name = getattr(step, "step_name", "")
            lines.append(f'// [unknown step type: {cls_name}] {step_name} -- runnable no-op stub')
            lines.append(f'LOG.warn("skipped {cls_name} step: {_jlit(step_name)}");')
            self.ledger.add_unknown_step(
                self._current_prefix, self._current_case, step_name, cls_name)
        return lines

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
        # SoapUI file attachments (multipart uploads). The current client
        # emitter builds a JSON/XML body via a template; multipart support
        # would need a `.multiPart(...)` chain per attachment. Emit a
        # TODO stub so tests still compile but the missing multipart is
        # loud at runtime.
        if step.attachments:
            att_names = [a.get("name") or a.get("part_name") or "?"
                          for a in step.attachments]
            lines.append(
                f'// [attachments] {len(step.attachments)} file '
                f'attachment(s): {", ".join(att_names)[:120]}')
            lines.append(
                f'// TODO manual: emit .multiPart(new File(...), "{step.media_type}") '
                f'per attachment; existing client method sends only the JSON body.')
            lines.append(
                f'LOG.warn("STUB: {len(step.attachments)} file attachment(s) '
                f'not sent for step {_jlit(step.step_name)}");')

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
            payload_var = f"{base}Payload{suf}"
            self._locals_in_method.add(payload_var)
            # Prefer the generated Templates.<NAME> constant so the classpath
            # string never appears as a literal in the emitted Java. Falls
            # back to the split (dir, file) literals only when either the
            # step has no dedup entry OR the templates class emitter hasn't
            # run yet (legacy path).
            const_name = (self._template_const_by_path.get(classpath_ref)
                          if classpath_ref else None)
            if const_name:
                lines.append(f'String {payload_var} = RestUtilities.mapJsonValues(')
                lines.append(f'    RestUtilities.getRequestTemplate(Templates.{const_name}),')
                lines.append(f'    TestSupport.mergedRow(row, ctx), /* strict */ false);')
            else:
                if classpath_ref:
                    slash = classpath_ref.rfind("/")
                    tmpl_dir = classpath_ref[:slash + 1]
                    tmpl_file = classpath_ref[slash + 1:]
                else:
                    tmpl_dir = f"templates/{self.suite_name}/"
                    tmpl_file = f'{sanitize_identifier(step.step_name).lower()}.json'
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

        # Token / Authorization header resolution priority:
        #   1. Per-request `<con:credentials>` auth profile (if this step
        #      overrides the project-level auth) -- see step.auth_profile.
        #      Basic and OAuth 2.0 Bearer can be fully translated to Java
        #      here; more exotic types (NTLM, Kerberos, WS-Security) emit
        #      a WARN comment and fall through to the ctx-based default so
        #      the request still goes out with SOME auth (even if wrong).
        #   2. Explicit `Authorization` parameter on the step -- old
        #      SoapUI style where the header value is a ${...} expression.
        #   3. Default: ctx-published token key (framework convention).
        token_expr = 'ctx.getOrDefault("tokenId.GeneratedTokenID", "")'
        if step.auth_profile:
            atype = (step.auth_profile.get("auth_type") or "").strip()
            if atype in ("Basic", "Preemptive"):
                u = _jlit(step.auth_profile.get("username", ""))
                p = _jlit(step.auth_profile.get("password", ""))
                lines.append(
                    f'// [auth override] step declares Basic auth profile "'
                    f'{_jlit(step.auth_profile.get("profile_name", ""))}"')
                token_expr = (f'"Basic " + java.util.Base64.getEncoder().encodeToString('
                               f'("{u}:{p}").getBytes(java.nio.charset.StandardCharsets.UTF_8))')
            elif atype in ("OAuth 2.0", "OAuth2", "Bearer") and step.auth_profile.get("oauth_token"):
                tok = _jlit(step.auth_profile["oauth_token"])
                lines.append(
                    f'// [auth override] step declares OAuth 2.0 profile "'
                    f'{_jlit(step.auth_profile.get("profile_name", ""))}"')
                token_expr = f'"Bearer {tok}"'
            else:
                lines.append(
                    f'// [auth override] step declares "'
                    f'{_jlit(atype or step.auth_profile.get("profile_name", ""))}" '
                    f'auth profile -- not auto-translated; falling back to '
                    f'ctx token. Wire up manually if needed.')
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
        # Console surround: before-log the verb+path so a hung REST call
        # is immediately visible ("stuck on POST /X was the last thing
        # printed"); after-log the status+elapsed ms so slow endpoints
        # stand out. `_uniq_local` walks _2/_3/... until unique so
        # multiple REST calls in one method never clash on this local.
        elapsed_var = self._uniq_local(f"__restT_{base}")
        lines.append(
            f'LOG.info(" -> {step.http_method} {step.resource_path}  '
            f'(step={_jlit(step.step_name)})");')
        lines.append(f'long {elapsed_var} = System.currentTimeMillis();')
        lines.append(f'Response {response_var} = client.{method_name_java}({", ".join(call_args)});')
        lines.append(
            f'LOG.info(" <- HTTP {{}} in {{}}ms  '
            f'(step={_jlit(step.step_name)})", '
            f'{response_var}.getStatusCode(), '
            f'System.currentTimeMillis() - {elapsed_var});')
        lines.append(f'RestUtilities.logResponseBody(testCaseId, holder, RestUtilities.getResponseAsString({response_var}));')

        # Assertions:
        # Prefer the UNION of active assertions computed across the whole
        # cluster (populated by `_union_cluster_asserts` in
        # `emit_test_class_per_suite`) so a cluster member's extra
        # assertions don't silently vanish. Fall back to just this
        # step's own assertions when not in cluster mode.
        assertions_to_emit = (
            self._cluster_asserts_by_pos.get(self._current_rest_step_pos)
            if self._cluster_asserts_by_pos else None)
        if assertions_to_emit is None:
            assertions_to_emit = [a for a in step.assertions if not a.disabled]
        # `assertion_index` counts only ACTIVE assertions (skipped ones
        # don't consume an index) so the CSV column names stay stable
        # even if a SoapUI author toggles a disabled assertion on later.
        for a in step.assertions:
            if a.disabled:
                self.ledger.add_assertion(
                    self._current_prefix, self._current_case, step.step_name,
                    a.type, a.config, "", "SKIPPED")
        a_active = 0
        for a in assertions_to_emit:
            emitted, coverage = self._render_assertion(
                a, response_var, step.step_name, suffix=suf,
                assertion_index=a_active)
            a_active += 1
            lines.extend(emitted)
            self.ledger.add_assertion(
                self._current_prefix, self._current_case, step.step_name,
                a.type, a.config, " ".join(emitted), coverage)

        # Advance the cluster REST-step position so the next call reads
        # assertions from position+1.
        self._current_rest_step_pos += 1

        # Auth-reuse note: originally we tried wrapping token-fetch steps
        # in `if (!ctx.containsKey("accessToken")) { ... }` to skip a
        # redundant fetch when @BeforeClass primed via AuthHelper. That
        # broke compile -- downstream Groovy steps reference the response
        # variable (`<step>Res`), which the wrap scoped to the block.
        # Correct fix would hoist the response declaration out of the
        # guard; deferred. Instead we make @BeforeClass priming
        # CONDITIONAL on whether the class already has an inline token
        # fetch (see `emit_test_class_per_suite`) so we never fetch
        # twice for the same class.

        return lines

    def _union_cluster_asserts(self, cluster: list) -> dict[int, list]:
        """Compute the UNION of active Assertion objects across every case
        in a cluster, keyed by REST-step POSITION (0-based) into any
        cluster case's REST-step sequence. Deduplication key = (type,
        `_assert_col_key` output) so cases asserting different values on
        the same JSON path collapse to ONE emitted block (whose per-row
        value comes from the CSV column).

        Since cluster construction guarantees (verb, path, body-shape)
        equality per REST-step position across members, position N in any
        case is comparable to position N in every other case. Longer
        cases (prefix-merged in) may extend beyond the base cluster's
        REST-step count -- we honor those too.
        """
        by_pos: dict[int, list] = {}
        by_pos_seen: dict[int, set] = {}
        for c in cluster:
            pos = 0
            for step in c.steps:
                if not isinstance(step, RestStep):
                    continue
                a_idx = 0
                for a in step.assertions:
                    if a.disabled:
                        continue
                    key = (a.type, _assert_col_key(a, a_idx) or f"a{a_idx}")
                    a_idx += 1
                    seen = by_pos_seen.setdefault(pos, set())
                    if key in seen:
                        continue
                    seen.add(key)
                    by_pos.setdefault(pos, []).append(a)
                pos += 1
        return by_pos

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

        # Bail early on JsonPath-family assertions whose expression uses
        # GPath-incompatible syntax (recursive descent `..`, filters
        # `[?()]`, unions `[a,b]`, script indices `[(expr)]`). Passing
        # these through to RestAssured's `.jsonPath().getString(...)`
        # would throw IllegalArgumentException at runtime.
        _JSONPATH_TYPES = ("JsonPath Match", "JsonPath Existence Match",
                            "JsonPath Count", "JsonPath RegEx Match")
        if t in _JSONPATH_TYPES:
            raw_path = cfg.get("path", "")
            if _jsonpath_is_gpath_incompatible(raw_path):
                # Escape raw_path for a Java comment: replace only chars
                # that would break comment-block syntax (`*/` sequence).
                safe_path = raw_path.replace("*/", "* /")
                return ([
                    f'// [{t}] SKIPPED at emit time -- JsonPath expression '
                    f'uses syntax GPath does not support.',
                    f'// path: {safe_path}',
                    f'// Unsupported: `..` (recursive descent), `[?()]` (filters), '
                    f'`[a,b]` (unions), `[(expr)]` (script index).',
                    f'// Convert manually or use com.jayway.jsonpath.JsonPath.read(...) '
                    f'directly. See audit ledger for full context.',
                ], "TODO")

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
            return self._render_groovy_script_assertion(
                a, response_var, step_name, vsid)
        if t == "DataAndMetadataAssertion":
            return self._render_data_and_metadata_assertion(
                a, response_var, step_name, vsid)
        if t == "MessageContentAssertion":
            return self._render_message_content_assertion(
                a, response_var, step_name, vsid)
        # Unknown assertion type -- emit a TODO stub, log it in ledger
        return ([
            f'// TODO manual review: assertion type "{t}" not auto-converted. Original name: {_jlit(a.name)}',
        ], "TODO")

    # =====================================================================
    # SoapUI "complex" assertion translators (MessageContent, DataAndMetadata,
    # GroovyScript). Each returns (java_lines, coverage).
    # =====================================================================

    def _render_message_content_assertion(self, a: Assertion, response_var: str,
                                            step_name: str, vsid: str) -> tuple[list[str], str]:
        """SoapUI MessageContent: 1..N XPath+expectedValue elements per
        assertion. Each ENABLED element becomes one assertion in Java.
        XPath is translated to a JsonPath-ish accessor via
        `_soapui_xpath_to_jsonpath` (SoapUI wraps JSON responses in a
        synthetic XML view for these checks). Every expected value goes
        to a per-row CSV column so scenario variants can override it."""
        active_elements = [
            e for e in a.elements
            if (e.get("enabled", "true").lower() != "false")
        ]
        if not active_elements:
            return ([
                f'// [MessageContentAssertion] "{_jlit(a.name)}" -- all '
                f'{len(a.elements)} element(s) disabled in source XML',
            ], "SKIPPED")
        lines: list[str] = [
            f'// [MessageContentAssertion] "{_jlit(a.name)}" '
            f'({len(active_elements)} active element(s))',
        ]
        for idx, el in enumerate(active_elements):
            xpath = el.get("xpath", "") or el.get("path", "")
            jpath = _soapui_xpath_to_jsonpath(xpath)
            expected = el.get("expectedValue", "") or el.get("content", "")
            operator = el.get("operator", "=") or "="
            elem_name = el.get("element", "") or f"el{idx}"
            col_name = (f"expected_{sanitize_identifier(step_name)}"
                        f"_msgcontent_{sanitize_identifier(elem_name)}")
            v = f"{vsid}_msg{idx}"
            fallback = _jlit(expected)
            lines.append(
                f'String actual_{v} = {response_var}.jsonPath().getString("{_jlit(jpath)}");')
            lines.append(
                f'String expected_{v} = (row.get("{col_name}") == null || '
                f'row.get("{col_name}").isEmpty()) ? "{fallback}" : row.get("{col_name}");')
            if operator.strip() == "!=":
                lines.append(
                    f'softAssert.assertNotEquals(actual_{v}, expected_{v}, '
                    f'"MessageContent != for {_jlit(elem_name)}");')
            else:
                lines.append(
                    f'softAssert.assertEquals(actual_{v}, expected_{v}, '
                    f'"MessageContent = for {_jlit(elem_name)}");')
        return (lines, "FULL")

    def _render_data_and_metadata_assertion(self, a: Assertion, response_var: str,
                                              step_name: str, vsid: str) -> tuple[list[str], str]:
        """SoapUI DataAndMetadata: 1..N JsonPath+expectedValue+operatorId
        elements. Similar to MessageContent but the path is JsonPath and
        operatorId is numeric (1=equals, 2=notequals, 3=contains — we
        default to equals when unrecognized)."""
        active_elements = [
            e for e in a.elements
            if (e.get("enabled", "true").lower() != "false")
        ]
        if not active_elements:
            return ([
                f'// [DataAndMetadataAssertion] "{_jlit(a.name)}" -- all '
                f'{len(a.elements)} element(s) disabled in source XML',
            ], "SKIPPED")
        lines: list[str] = [
            f'// [DataAndMetadataAssertion] "{_jlit(a.name)}" '
            f'({len(active_elements)} active element(s))',
        ]
        for idx, el in enumerate(active_elements):
            path = _jsonpath_to_gpath(el.get("path", ""))
            expected = el.get("expectedValue", "") or el.get("content", "")
            op = (el.get("operatorId", "1") or "1").strip()
            elem_name = el.get("element", "") or f"el{idx}"
            col_name = (f"expected_{sanitize_identifier(step_name)}"
                        f"_datameta_{sanitize_identifier(elem_name)}")
            v = f"{vsid}_dm{idx}"
            fallback = _jlit(expected)
            lines.append(
                f'String actual_{v} = {response_var}.jsonPath().getString("{_jlit(path)}");')
            lines.append(
                f'String expected_{v} = (row.get("{col_name}") == null || '
                f'row.get("{col_name}").isEmpty()) ? "{fallback}" : row.get("{col_name}");')
            if op == "2":  # not equals
                lines.append(
                    f'softAssert.assertNotEquals(actual_{v}, expected_{v}, '
                    f'"DataAndMetadata != for {_jlit(elem_name)}");')
            elif op == "3":  # contains
                lines.append(
                    f'softAssert.assertTrue(actual_{v} != null && '
                    f'actual_{v}.contains(expected_{v}), '
                    f'"DataAndMetadata contains for {_jlit(elem_name)}");')
            else:  # default: equals
                lines.append(
                    f'softAssert.assertEquals(actual_{v}, expected_{v}, '
                    f'"DataAndMetadata = for {_jlit(elem_name)}");')
        return (lines, "FULL")

    # Pattern library for GroovyScriptAssertion translation. Each entry is
    # (compiled_regex, function(match, response_var, vsid) -> list[str]).
    _GROOVY_ASSERT_PATTERNS: list = None  # populated lazily in _init

    @staticmethod
    def _strip_groovy_comments_and_strings(script: str) -> str:
        """Return `script` with line comments (`//...`), block comments
        (`/*...*/`), and string literals (single- + double-quoted + triple-
        quoted) replaced by spaces. Used to sanitize the script BEFORE
        running assertion-pattern regexes so a commented-out
        `// assert response.status == 500` (or an assert-string inside
        a heredoc) doesn't spuriously match a pattern and emit real Java.

        Length-preserving replacement (spaces, not deletion) so anchor
        positions in the regex stay meaningful."""
        out = list(script)
        i = 0
        n = len(script)
        while i < n:
            c = script[i]
            # Line comment: // to end of line
            if c == "/" and i + 1 < n and script[i + 1] == "/":
                j = script.find("\n", i)
                end = j if j != -1 else n
                for k in range(i, end):
                    if out[k] != "\n":
                        out[k] = " "
                i = end
                continue
            # Block comment: /* ... */
            if c == "/" and i + 1 < n and script[i + 1] == "*":
                j = script.find("*/", i + 2)
                end = j + 2 if j != -1 else n
                for k in range(i, end):
                    if out[k] != "\n":
                        out[k] = " "
                i = end
                continue
            # Triple-quoted string: '''...''' or """..."""
            if c in ("'", '"') and i + 2 < n and script[i + 1] == c and script[i + 2] == c:
                triple = c * 3
                j = script.find(triple, i + 3)
                end = j + 3 if j != -1 else n
                for k in range(i, end):
                    if out[k] != "\n":
                        out[k] = " "
                i = end
                continue
            # Single-line string: ' or "
            if c in ("'", '"'):
                j = i + 1
                while j < n and script[j] != c:
                    # Skip escaped char
                    if script[j] == "\\" and j + 1 < n:
                        j += 2
                        continue
                    if script[j] == "\n":
                        break
                    j += 1
                end = j + 1 if j < n else n
                for k in range(i, end):
                    if out[k] != "\n":
                        out[k] = " "
                i = end
                continue
            i += 1
        return "".join(out)

    def _render_groovy_script_assertion(self, a: Assertion, response_var: str,
                                          step_name: str, vsid: str) -> tuple[list[str], str]:
        """Attempt to translate common Groovy-assertion shapes into Java
        soft-asserts. Unrecognized scripts emit a runtime WARN + attach
        the raw script to the Allure report so the parity gap is visible.

        Pattern matching runs against a COMMENT-STRIPPED and STRING-
        STRIPPED copy of the script so a commented-out
        `// assert response.status == 500` doesn't fire a real assert."""
        script = (a.config.get("scriptText", "") or a.config.get("script", "") or "").strip()
        # Sanitize before pattern-matching so comments/strings can't fake a hit.
        sanitized = self._strip_groovy_comments_and_strings(script)

        # Pattern 1: `assert messageExchange.responseHeaders["X"] != null`
        m = re.search(
            r'assert\s+messageExchange\.responseHeaders\[\s*["\'](?P<h>[^"\']+)["\']\s*\]\s*!=\s*null',
            sanitized)
        if m:
            # Re-extract the header name from the ORIGINAL script at the
            # matched position (sanitized has strings blanked out).
            m2 = re.search(
                r'assert\s+messageExchange\.responseHeaders\[\s*["\'](?P<h>[^"\']+)["\']\s*\]\s*!=\s*null',
                script)
            header = _jlit(m2.group("h") if m2 else "?")
            return ([
                f'// [GroovyScriptAssertion] header presence: {header}',
                f'softAssert.assertNotNull({response_var}.header("{header}"), '
                f'"header present: {header}");',
            ], "FULL")

        # Pattern 2: `assert messageExchange.responseHeaders["X"] == "value"`
        # Match against the ORIGINAL script (needs the string literals),
        # but only if the assertion isn't inside a comment (verify via
        # sanitized copy: same position should still contain "assert").
        m = re.search(
            r'assert\s+messageExchange\.responseHeaders\[\s*["\'](?P<h>[^"\']+)["\']\s*\]'
            r'\s*==\s*["\'](?P<v>[^"\']*)["\']', script)
        if m and "assert" in sanitized[max(0, m.start()):m.start() + 6]:
            header = _jlit(m.group("h"))
            expected = _jlit(m.group("v"))
            col_name = (f"expected_{sanitize_identifier(step_name)}_"
                        f"header_{sanitize_identifier(header)}")
            return ([
                f'// [GroovyScriptAssertion] header value: {header}',
                f'String expected_{vsid}_gsh = (row.get("{col_name}") == null || '
                f'row.get("{col_name}").isEmpty()) ? "{expected}" : row.get("{col_name}");',
                f'softAssert.assertEquals({response_var}.header("{header}"), '
                f'expected_{vsid}_gsh, "header = for {header}");',
            ], "FULL")

        # Pattern 3: `assert response.status == N` (older SoapUI style)
        m = re.search(r'assert\s+response\.status\s*==\s*(\d{3})', sanitized)
        if m:
            code = m.group(1)
            return ([
                f'// [GroovyScriptAssertion] status code check: {code}',
                f'softAssert.assertEquals({response_var}.statusCode(), {code}, '
                f'"response status == {code}");',
            ], "FULL")

        # Pattern 4: assert body contains substring (string literal in
        # ORIGINAL, but position must map into sanitized `assert` region).
        m = re.search(
            r'assert\s+(?:response\.body|messageExchange\.responseContent)'
            r'\.?(?:toString\(\))?\.contains\(\s*["\'](?P<t>[^"\']+)["\']\s*\)', script)
        if m and "assert" in sanitized[max(0, m.start()):m.start() + 6]:
            token = _jlit(m.group("t"))
            # TRUNCATE BEFORE jlit-escape so we never split an escape
            # sequence at the boundary (bug: `\\"` at pos 39-40 gives `\\`
            # alone -> unterminated Java literal).
            token_preview = _jlit(m.group("t")[:40])
            return ([
                f'// [GroovyScriptAssertion] response body contains: {token_preview}',
                f'softAssert.assertTrue({response_var}.asString().contains("{token}"), '
                f'"body contains: {token_preview}");',
            ], "FULL")

        # Nothing matched -- WARN at runtime + attach the raw Groovy to
        # Allure so the parity gap is loudly visible in reports. Test
        # still passes (soft-assert not fired) but the assertion is not
        # silently dropped anymore.
        #
        # Escape rules:
        #   - preview goes into LOG.warn's "..." literal -> full _jlit
        #     (backslashes, CR, LF, tabs, quotes all covered);
        #   - full script goes into an Allure attachment string -> also
        #     full _jlit.
        # Truncate BEFORE escaping so we never split a `\\n` at the boundary.
        preview_raw = " ".join(script.split())[:120]
        preview_j = _jlit(preview_raw)
        script_j = _jlit(script)
        assert_name_j = _jlit(a.name)
        step_name_j = _jlit(step_name)
        return ([
            f'// [GroovyScriptAssertion] "{assert_name_j}" -- unrecognized '
            f'Groovy pattern; original script attached to Allure report',
            f'LOG.warn("STUBBED GroovyScriptAssertion for step \\"{step_name_j}\\": '
            f'{preview_j}");',
            f'io.qameta.allure.Allure.addAttachment('
            f'"STUBBED Groovy assertion: {assert_name_j}", "text/x-groovy", '
            f'"{script_j}");',
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

import java.io.IOException;
import java.io.InputStream;
import java.util.HashMap;
import java.util.Iterator;
import java.util.Map;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

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
 *
 * testData(row, key) is a separate lookup used by every migrated
 * `ctx.putIfAbsent` for a SoapUI Properties-step value. Walks:
 *   1. CSV row cell            (author override per scenario)
 *   2. Config test_data.<key>  (env override, program_configuration.json)
 *   3. Bundled defaults JSON   (test_data_defaults/{self.suite_name}.json)
 *   4. ""                      (empty; caller decides how to handle)
 * so the emitted Java never carries hardcoded default literals.
 */
public final class TestSupport {{

    /** Placeholder keys the converter identified as config-driven for this
     *  imported test suite. mergedRow proactively pulls these from Config so
     *  templates can reference them without any explicit ctx.put in the test. */
    private static final String[] CONFIG_KEYS = new String[] {{ {keys_java} }};

    /** Per-suite test-data defaults, loaded once from the bundled JSON.
     *  Populated in the static initializer; empty when the file is missing
     *  (converter skipped writing it because the source XML had no
     *  Properties steps). */
    private static final Map<String, String> TEST_DATA_DEFAULTS = loadTestDataDefaults();

    private TestSupport() {{}}

    /**
     * Test-data lookup for values that used to be hardcoded literals in
     * the emitted Java. Called by every migrated `ctx.putIfAbsent` line.
     * Precedence (first non-empty wins):
     * <ol>
     *   <li>{{@code row.get(key)}} -- CSV row cell (author picks per scenario)</li>
     *   <li>{{@code Config.get("test_data." + key)}} -- env override</li>
     *   <li>{{@link #TEST_DATA_DEFAULTS}} -- bundled per-suite default</li>
     *   <li>empty string {{@code ""}}</li>
     * </ol>
     */
    public static String testData(Map<String, String> row, String key) {{
        if (row != null) {{
            String v = row.get(key);
            if (v != null && !v.isEmpty()) return v;
        }}
        String cfg = Config.get("test_data." + key, null);
        if (cfg != null && !cfg.isEmpty()) return cfg;
        return TEST_DATA_DEFAULTS.getOrDefault(key, "");
    }}

    private static Map<String, String> loadTestDataDefaults() {{
        Map<String, String> m = new HashMap<>();
        String path = "test_data_defaults/{self.suite_name}.json";
        try (InputStream in = TestSupport.class.getClassLoader()
                .getResourceAsStream(path)) {{
            if (in == null) return m;
            JsonNode root = new ObjectMapper().readTree(in);
            Iterator<Map.Entry<String, JsonNode>> it = root.fields();
            while (it.hasNext()) {{
                Map.Entry<String, JsonNode> e = it.next();
                m.put(e.getKey(), e.getValue().asText(""));
            }}
        }} catch (IOException ignored) {{
            // File missing / unreadable -> defaults stay empty; row + config
            // still work as override sources.
        }}
        return m;
    }}

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
import {self.package_root}.templates.{self.suite_name}.Templates;

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

    # -- Env config --------------------------------------------------------

    def emit_env_config(self, cases: list[TestCase], env_name: str = "qa") -> str:
        """Emit `src/main/resources/config/<env>.json` with a base_url
        + placeholder entries for every `${#Project#X}` and `${#Env#X}`
        reference seen across the case scope.

        Base URL resolution priority:
          1. When the source XML declares `<con:environments>` matching
             `env_name` (via `_PROJECT_ENVIRONMENTS`), use the first
             interface endpoint from that env -- SoapUI's real config.
          2. Otherwise: fall back to the first non-empty `<con:originalUri>`
             seen on any REST step (trim off the resource_path suffix).
          3. Otherwise: the placeholder `https://api.example.com`.
        """
        # Collect all Project-scoped and Env-scoped property refs across all cases.
        project_props: set[str] = set()
        env_props: set[str] = set()
        for case in cases:
            for step in case.steps:
                if isinstance(step, RestStep):
                    for text in ([step.request_body or ""]
                                  + list(step.headers.values())
                                  + list(step.path_params.values())
                                  + list(step.query_params.values())):
                        project_props.update(_PROJ_PROP_RX.findall(text))
                        for m in _SCOPE_PROP_RX.finditer(text):
                            if m.group(1) in ("Env", "Global"):
                                env_props.add(f"{m.group(1).lower()}_{m.group(2)}")

        # PRIMARY: use SoapUI-declared env endpoints if present.
        env_endpoint = ""
        if _PROJECT_ENVIRONMENTS and env_name in _PROJECT_ENVIRONMENTS:
            # First interface endpoint in the env; if multiple interfaces
            # exist we take the shortest URL (usually the "base API host").
            per_iface = _PROJECT_ENVIRONMENTS[env_name]
            if per_iface:
                env_endpoint = min(per_iface.values(), key=len).rstrip("/")

        # FALLBACK: derive from any REST step's original URI.
        base_url = env_endpoint
        if not base_url:
            for case in cases:
                for step in case.steps:
                    if isinstance(step, RestStep) and step.original_uri:
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
            **{p: f"__SET_{p}__" for p in sorted(env_props)},
        }
        # When the XML declared SoapUI environments, emit ALL of them as
        # sibling `<other_env>_base_url` keys so authors can eyeball the
        # difference in one file without checking each env.json.
        if _PROJECT_ENVIRONMENTS:
            for other_env, per_iface in _PROJECT_ENVIRONMENTS.items():
                if other_env == env_name or not per_iface:
                    continue
                short = min(per_iface.values(), key=len).rstrip("/")
                config[f"__soapui_env_{other_env}_base_url"] = short
        rel = f"src/main/resources/config/{env_name}.json"
        self._write(rel, json.dumps(config, indent=2) + "\n")
        return rel

    # =====================================================================
    # v2: "one class per SoapUI suite" mode. Every emitter method below
    # is used only by the `--one-class-per-suite` codepath and is
    # additive -- the legacy per-prefix emitters above are untouched, so
    # existing callers keep working.
    # =====================================================================

    def emit_test_data_defaults_json(self, migration: dict) -> Optional[str]:
        """Emit `src/main/resources/test_data_defaults/<suite>.json` -- the
        bundled data file every migrated `ctx.putIfAbsent` reads through
        `TestSupport.testData(row, key)`. Keeps the emitted Java sources
        free of hardcoded default literals: defaults live here as pure
        data, editable independently of code.

        One flat JSON object mapping `<step_name>.<prop_name>` -> default
        literal. Empty {} when no PropertiesStep values were classified."""
        if not migration:
            return None
        rel = f"src/main/resources/test_data_defaults/{self.suite_name}.json"
        defaults = {ctx_key: m['default']
                    for ctx_key, m in migration.items()}
        # Emit sorted for deterministic diff-ability across regenerations.
        payload = json.dumps(
            {k: defaults[k] for k in sorted(defaults)},
            indent=2, ensure_ascii=False)
        self._write(rel, payload + "\n")
        return rel

    def emit_test_data_config(self, migration: dict) -> Optional[str]:
        """Emit `src/main/resources/test_data.example.properties` -- a hint
        file listing every `test_data.*` key the migrated PropertiesStep
        code will look up via `Config.get(...)`, with its default value
        pre-filled. Users copy the relevant keys into their real
        `program_configuration.json` (under a `test_data:` block per env)
        or `-D` them on the command line to override per env.

        Returns the relative path when the file is written; None when
        there are no config-destination migrations."""
        cfg_entries = [
            (ctx_key, m['default'], m['shape'])
            for ctx_key, m in (migration or {}).items()
            if m['destination'] == 'config'
        ]
        if not cfg_entries:
            return None
        rel = "src/main/resources/test_data.example.properties"
        lines = [
            "# Auto-generated by ra_converter -- copy the keys you need",
            "# into `program_configuration.json` under a `test_data:` block",
            "# per env, or override individually with `-Dtest_data.<key>=<value>`.",
            "#",
            "# Every emitted `ctx.putIfAbsent(...)` for a constant-across-cases",
            "# SoapUI Properties value now reads through Config, so a single",
            "# env-specific value can change without editing Java sources.",
            "#",
            "# Format: `test_data.<original SoapUI ctx key>=<observed default>`",
            "",
        ]
        for ctx_key, default, shape in sorted(cfg_entries):
            shape_note = f"  # shape={shape}" if shape != 'literal' else ""
            lines.append(f"test_data.{ctx_key}={default}{shape_note}")
        content = "\n".join(lines) + "\n"
        self._write(rel, content)
        return rel

    def emit_auth_helper(self) -> str:
        """Emit `com.ak.api.rest.utilities.AuthHelper` -- a framework-level
        one-time OAuth 2.0 token bootstrap. Every generated business-area
        class calls `AuthHelper.primeClientCredentialsToken(ctx)` in its
        `@BeforeClass` so N @Test methods reuse a single token instead of
        each re-fetching. Emitted idempotently: overwritten on every run
        with the current version.

        Config-driven -- reads `api_config.client_id / .client_secret /
        .token_end_point / .token_route / .grant_type` from Config (the
        nested-JSON support already wired). No-ops (WARN) when required
        creds are absent; individual REST-step-level token fetches remain
        as a fallback so tests that need the pre-existing inline flow
        keep working.

        Also emits `HeadersHelper` alongside so both helpers ship as a
        pair -- HeadersHelper.defaultsWithAuth(ctx) collapses the
        10-line boilerplate header block per method into one call."""
        pkg = "com.ak.api.rest.utilities"
        rel = f"src/main/java/{pkg.replace('.', '/')}/AuthHelper.java"
        content = f"""package {pkg};

import java.util.Map;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import io.restassured.RestAssured;
import io.restassured.http.ContentType;
import io.restassured.response.Response;

import com.ak.api.config.Config;

/**
 * One-time OAuth 2.0 bootstrap. Business-area test classes call
 * {{@link #primeClientCredentialsToken(Map)}} from {{@code @BeforeClass}}
 * so every {{@code @Test}} method starts with {{@code ctx.get("accessToken")}}
 * already populated -- no re-fetch per method.
 *
 * <p>Config keys read (from {{@code program_configuration.json}} via
 * {{@link Config}}):</p>
 * <ul>
 *   <li>{{@code api_config.client_id}}</li>
 *   <li>{{@code api_config.client_secret}}</li>
 *   <li>{{@code api_config.grant_type}} (default: {{@code client_credentials}})</li>
 *   <li>{{@code api_config.token_end_point}} (base URL)</li>
 *   <li>{{@code api_config.token_route}} (path appended to base)</li>
 * </ul>
 *
 * <p>Missing / empty creds -> logs WARN, leaves ctx untouched. Individual
 * REST steps that fetch a token inline still run in that case (fallback),
 * so tests never break on absent config -- they just don't benefit from
 * the class-level token reuse.</p>
 *
 * <p>Idempotent: safe to call multiple times. If {{@code ctx}} already
 * carries a non-empty {{@code accessToken}}, returns immediately.</p>
 */
public final class AuthHelper {{

    private static final Logger LOG = LoggerFactory.getLogger(AuthHelper.class);

    private AuthHelper() {{}}

    /**
     * Fetch a client-credentials OAuth token and store it in {{@code ctx}}
     * under the key {{@code "accessToken"}}. Safe to call from any
     * {{@code @BeforeClass}} -- becomes a no-op when the key is already set.
     */
    public static void primeClientCredentialsToken(Map<String, String> ctx) {{
        if (ctx.containsKey("accessToken")
                && ctx.get("accessToken") != null
                && !ctx.get("accessToken").isEmpty()) {{
            LOG.debug("AuthHelper: ctx already has accessToken -- skipping bootstrap");
            return;
        }}
        String clientId     = Config.get("api_config.client_id", "");
        String clientSecret = Config.get("api_config.client_secret", "");
        String grantType    = Config.get("api_config.grant_type", "client_credentials");
        if (clientId.isEmpty() || clientSecret.isEmpty()) {{
            LOG.warn("AuthHelper: api_config.client_id / client_secret not "
                    + "configured; leaving ctx.accessToken empty. Tests that "
                    + "need auth will fall back to fetching a token inline.");
            return;
        }}
        String tokenBase = Config.get("api_config.token_end_point", Config.baseUrl());
        String tokenRoute = Config.get("api_config.token_route", "");
        String tokenUrl;
        if (tokenRoute.isEmpty()) {{
            tokenUrl = tokenBase;
        }} else if (tokenBase.endsWith("/") || tokenRoute.startsWith("/")) {{
            tokenUrl = tokenBase + tokenRoute;
        }} else {{
            tokenUrl = tokenBase + "/" + tokenRoute;
        }}

        try {{
            Response resp = RestAssured.given()
                    .contentType(ContentType.URLENC)
                    .formParam("grant_type",    grantType)
                    .formParam("client_id",     clientId)
                    .formParam("client_secret", clientSecret)
                    .post(tokenUrl);

            int status = resp.getStatusCode();
            if (status != 200) {{
                String snippet = resp.asString();
                if (snippet.length() > 200) snippet = snippet.substring(0, 200) + "...";
                LOG.warn("AuthHelper: token endpoint {{}} returned HTTP {{}} -- "
                        + "leaving ctx.accessToken empty (inline fallback will run). "
                        + "Body: {{}}", tokenUrl, status, snippet);
                return;
            }}
            String token = resp.jsonPath().getString("access_token");
            if (token == null || token.isEmpty()) {{
                LOG.warn("AuthHelper: token endpoint {{}} returned 200 but no "
                        + "access_token field in body -- leaving ctx empty", tokenUrl);
                return;
            }}
            ctx.put("accessToken", token);
            LOG.info("AuthHelper: primed ctx.accessToken from {{}}", tokenUrl);
        }} catch (Exception e) {{
            LOG.warn("AuthHelper: token bootstrap failed against {{}}: {{}} "
                    + "(inline fallback will run)", tokenUrl, e.getMessage());
        }}
    }}
}}
"""
        self._write(rel, content)
        return rel

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
 *   {{@code com.ak.api.tests.imported.<suite>[.<resource>].<Class>.<methodName>(Map<String,String> row)}}
 * this provider loads
 *   {{@code classpath:/csv/<suite>[/<resource>]/<Class>/<methodName>.csv}}
 * -- the CSV directory tree mirrors the Java sub-package tree so two
 * `CreateTest` classes in different resource sub-packages don't collide
 * (they used to, when we keyed on {{@code getSimpleName()}} alone).
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
        // Derive CSV location from the calling class's FQN so the CSV
        // directory tree mirrors the Java sub-package tree exactly. We
        // strip the shared `.tests.imported.` prefix so paths stay
        // relative to the imported-tests root, then translate `.` -> `/`.
        String fqn = method.getDeclaringClass().getName();
        String anchor = ".tests.imported.";
        int idx = fqn.indexOf(anchor);
        String subPath = (idx >= 0
                ? fqn.substring(idx + anchor.length())
                : method.getDeclaringClass().getSimpleName())
                .replace('.', '/');
        String meth = method.getName();
        String resourcePath = "csv/" + subPath + "/" + meth + ".csv";

        InputStream in = Thread.currentThread().getContextClassLoader()
                .getResourceAsStream(resourcePath);
        if (in == null) {{
            throw new IllegalStateException(
                    "PerMethodCsvDataProvider: no CSV on classpath at " + resourcePath
                    + " (expected one row per data-driven scenario for @Test "
                    + method.getDeclaringClass().getSimpleName() + "#" + meth + ")");
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
                                    service_class_name: str,
                                    class_name_override: Optional[str] = None,
                                    subpackage_override: Optional[str] = None
                                    ) -> tuple[str, str, list[str]]:
        """Emit ONE Java test class per business-area bucket. `cases` is a
        SUBSET of a SoapUI suite (the cases that share a business intent,
        e.g. all POST /members tests). Each case becomes a @Test method;
        the method name is derived from the case name via
        `_business_method_name()` so it reads as business intent, not as
        a ticket ID.

        `class_name_override` / `subpackage_override` let the caller
        decide the emitted class name + optional sub-package (mirroring
        the URL resource so the file tree reads as a resource catalog).
        When both are None, falls back to the old one-class-per-suite
        shape (class named after the SoapUI suite, no sub-package).

        Returns (relative_path, class_fqn, method_names_in_emit_order).
        """
        # Truncate + hash long suite names so the generated class file
        # stays under Windows MAX_PATH once the package path prefix is
        # applied. Collision guard: refuse to overwrite an already-emitted
        # class in the same package -- caller either renamed a suite or
        # this XML has two suites that collapse to the same camel case.
        if class_name_override:
            raw_class = (class_name_override[:-4]
                         if class_name_override.endswith("Test")
                         else class_name_override)
            # Length shortener so per-bucket class names still fit under
            # Windows MAX_PATH; hashes the tail like short_class does.
            if self.max_name_len > 0 and len(raw_class) > self.max_name_len:
                import hashlib as _hl
                h = _hl.sha1(raw_class.encode("utf-8")).hexdigest()[:6].upper()
                head_len = max(1, self.max_name_len - 7)
                raw_class = f"{raw_class[:head_len]}_{h}"
        else:
            raw_class = short_class(soapui_suite_name, self.max_name_len)
        class_name = raw_class if raw_class.endswith("Test") else raw_class + "Test"
        # Base package = <root>.tests.imported.<suite>.  Business-area
        # buckets nest one level deeper into <suite>.<resource_slug>/ so
        # the file tree reads as a resource catalog.
        if subpackage_override:
            pkg = (f"{self.package_root}.tests.imported."
                   f"{self.suite_name}.{subpackage_override}")
        else:
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

        # Auth-priming decision: if ANY case in this bucket has an inline
        # token-fetch REST step (directly OR via a SetupHelper flow whose
        # setup steps include one), the @BeforeClass call to AuthHelper
        # would be a REDUNDANT second fetch -- skip it. Priming only helps
        # classes where no method fetches its own token (e.g. Groovy-only
        # cleanup classes that still need auth for their downstream
        # framework calls).
        has_inline_token_fetch = False
        for case in cases:
            for step in case.steps:
                if isinstance(step, RestStep) and _is_token_fetch_step(step):
                    has_inline_token_fetch = True
                    break
            if has_inline_token_fetch:
                break

        # Cluster cases by REST-step shape (verb + path + body-hash per step)
        # so N cases that share an intent collapse to ONE @Test method with
        # N CSV rows. Single-case clusters emit exactly like before.
        shape_clusters = _cluster_cases_by_shape(cases)
        # Prefix-merge pass: fold shorter clusters into longer ones when the
        # shorter's REST-step signature is a prefix of the longer's. Merged
        # methods emit the LONGEST shape; shorter-case rows carry a
        # `_stop_after` CSV cell so they return early at the right step.
        merged = _merge_prefix_clusters(shape_clusters)
        self._clusters = [cl for cl, _sm in merged]
        self._stop_markers_per_cluster = {idx: sm for idx, (_cl, sm) in enumerate(merged)}
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
            # Precompute UNION of assertions across all cluster cases at
            # each REST-step position so _render_rest_step_body emits every
            # unique assertion (not just cluster[0]'s). Members' extra
            # assertions become CSV-conditional -- their default from the
            # source case is baked in, but rows for other cases can leave
            # the cell blank to skip.
            self._cluster_asserts_by_pos = self._union_cluster_asserts(cluster)
            # Use the first case as the "template" case for step rendering.
            # In merged clusters the FIRST case is always the LONGEST (the
            # base cluster from `_merge_prefix_clusters`), so its steps are
            # the full sequence -- shorter cases stop early via CSV cell.
            rendered_methods.append(self._render_test_method_v2(
                cluster[0], service_class_name, final_name, status_code, variant,
                cluster_size=len(cluster),
                stop_markers=self._stop_markers_per_cluster.get(idx, {})))

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

        # Business-area layout blurb only when we're actually splitting
        # by area (caller passed a sub-package). Otherwise fall back to
        # the flat one-class-per-suite legacy description.
        if subpackage_override:
            layout_blurb = [
                f" * Business-area class: `{subpackage_override}/{class_name}`.",
                f" * Every @Test method in this file is a scenario for ONE business intent",
                f" * (HTTP verb + resource). Data-similar scenarios collapse into a single",
                f" * method with N CSV rows -- add a row to add a scenario, no code change.",
            ]
        else:
            layout_blurb = [
                f" * ONE Java class -> ONE SoapUI test suite. Each SoapUI test case",
                f" * becomes one @Test method here; scenario variants (200 / 400 / 403)",
                f" * are kept as separate methods so failures are addressable per intent.",
            ]

        csv_dir = (f"{self.suite_name}/{subpackage_override}/{class_name}"
                   if subpackage_override
                   else f"{self.suite_name}/{class_name}")

        # @BeforeClass body -- AuthHelper priming is CONDITIONAL. Emit it
        # only for classes where NO @Test method inlines its own token
        # fetch (checked above). Priming a class that also fetches inline
        # would just be a redundant double-fetch.
        if has_inline_token_fetch:
            auth_prime_line = (
                "        // No @BeforeClass auth priming: this class's tests fetch their\n"
                "        // own token inline (via a token-endpoint REST step or a SetupHelper\n"
                "        // flow that includes one), so priming here would be a redundant fetch.\n"
            )
        else:
            auth_prime_line = (
                "        // Prime OAuth token ONCE per class -- every @Test method reuses\n"
                "        // ctx.get(\"accessToken\") instead of fetching per method. Reads\n"
                "        // client credentials from Config (`api_config.*`). Safe no-op when\n"
                "        // config is missing.\n"
                "        AuthHelper.primeClientCredentialsToken(ctx);\n"
            )
        header_lines = [
            f" * Auto-generated by ra_converter from SoapUI test suite `{soapui_suite_name}`.",
            f" *",
            *layout_blurb,
            f" *",
            f" * Data: {{@code csv/{csv_dir}/<methodName>.csv}} on the classpath.",
            f" * Auto-loaded by {{@link {self.package_root}.data.PerMethodCsvDataProvider}}",
            f" * -- no per-class wiring required. Add rows to a CSV to add scenarios.",
        ]
        if flow_blurbs:
            header_lines.append(" *")
            header_lines.append(" * Shared setup flows (defined in SetupHelper for this suite):")
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
import com.ak.api.rest.utilities.AuthHelper;
import com.ak.api.rest.utilities.Headers;
import com.ak.api.rest.utilities.RestUtilities;
import com.ak.api.retry.RetryAnalyzer;
import {self.package_root}.support.{self.suite_name}.SetupHelper;
import {self.package_root}.support.{self.suite_name}.TestSupport;
import {self.package_root}.templates.{self.suite_name}.Templates;
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
    public void initClientAndAuth() {{
        String baseUrl = Config.get("base_url", Config.baseUrl());
        client = new {service_class_name}(baseUrl);
{auth_prime_line}        LOG.info("initialised {class_name} against baseUrl={{}}  (auth={{}})",
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
                                 variant: str, cluster_size: int = 1,
                                 stop_markers: dict[str, str] = None) -> str:
        """Same shape as `_render_test_method` but wired to the convention-
        based DataProvider (`PerMethodCsvDataProvider`, name `"rows"`), and
        the method name is the business-intent form (not the hash-shortened
        SoapUI case name).

        When `cluster_size > 1`, this method represents N SoapUI cases that
        share the same REST step shape; the CSV has one row per case and
        `testCaseId` is read PER ROW (from the `test_case_id` column) so
        each scenario logs its own identity.

        When `stop_markers` is non-empty, the cluster is a prefix-merged
        one: shorter cases stop early at a designated REST step. After
        each REST step's body we emit a runtime check that returns from
        the method when `row["_stop_after"]` equals the step's name.
        """
        self._current_case = case.name
        self._reset_per_method_state()
        stop_markers = stop_markers or {}
        emit_stop_checks = bool(stop_markers)

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
        # Per-method banner + duration so `mvn test` console reads as a
        # sequential log ("STARTED xxx", REST calls with -> / <- markers,
        # then "FINISHED xxx in Nms"). Makes stuck tests obvious.
        body_lines = [
            id_stmt,
            'long __methodStartMs = System.currentTimeMillis();',
            f'LOG.info("========== STARTED {method_name}  "'
            f'+ "(case=" + testCaseId + ") ==========");',
            '// Prevent state leakage between rows of a multi-scenario method,'
            ' BUT preserve the class-level accessToken primed by @BeforeClass',
            '// so each row doesn\'t re-fetch a fresh OAuth token.',
            'String __auth = ctx.get("accessToken");',
            'ctx.clear();',
            'if (__auth != null) ctx.put("accessToken", __auth);',
            '// Expand <<faker>> tokens + ${{property}} refs in every CSV cell '
            'so downstream code sees live values, not placeholders.',
            'row = PlaceholderResolver.resolveRow(row, ctx);',
            'Expected exp = expected(row);',
            '',
        ]
        if emit_stop_checks:
            # POSITIONAL stop marker: the `_stop_after` CSV cell carries
            # the 1-based count of REST steps this row should execute
            # before returning. We increment a running counter after each
            # REST step and return when it matches.
            body_lines.append(
                '// Prefix-merged cluster: shorter-scenario rows return early '
                'after N REST calls per their `_stop_after` CSV cell.')
            body_lines.append('int __restStepIdx = 0;')
            body_lines.append('String __stopAfter = row.getOrDefault("_stop_after", "");')
            body_lines.append('')
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
            # SetupHelper flows contain their own REST calls -- count them
            # too so the positional marker stays consistent across the
            # helper boundary.
            if emit_stop_checks:
                helper_rest_count = sum(
                    1 for s in case.steps[:skip_count] if isinstance(s, RestStep))
                if helper_rest_count:
                    body_lines.append(f'__restStepIdx += {helper_rest_count};')
                body_lines.append(
                    'if (!__stopAfter.isEmpty() && __restStepIdx >= '
                    'Integer.parseInt(__stopAfter)) { return; }')
            body_lines.append('')
        for step in case.steps[skip_count:]:
            body_lines.extend(self._render_step(step, service_class_name))
            # Prefix-merge early-return: after each REST step, check whether
            # this row's cumulative REST-step count has hit the `_stop_after`
            # threshold. If so, return from the method so the LONGER
            # cluster's tail steps don't run for shorter-scenario rows.
            if emit_stop_checks and isinstance(step, RestStep):
                body_lines.append('__restStepIdx++;')
                body_lines.append(
                    'if (!__stopAfter.isEmpty() && __restStepIdx >= '
                    'Integer.parseInt(__stopAfter)) { return; }')
            body_lines.append('')

        # Trailing method-duration banner. Not wrapped in try/finally so
        # exceptions still propagate cleanly to TestNG; on failure the
        # PASSED/FAILED line from TestNG delimits the method instead.
        body_lines.append(
            f'LOG.info("========== FINISHED {method_name}  "'
            f'+ "(" + (System.currentTimeMillis() - __methodStartMs) + '
            f'"ms) ==========");')

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

        # Optional TM-integration annotations sourced from SoapUI's
        # `<con:testCase>` attributes. Emitted only when present so the
        # generated code stays lean for the common (no-TM-attrs) case.
        # Allure's `@TmsLink` renders as a clickable link to Zephyr/qTest
        # in the report; `@Issue` renders as a clickable JIRA link.
        extra_annotations: list[str] = []
        if case.zephyr_test_id:
            extra_annotations.append(
                f'    @io.qameta.allure.TmsLink("{_jlit(case.zephyr_test_id)}")')
        if case.zephyr_test_name:
            # Zephyr name often carries the same info as story; only add
            # as a Label if it's DIFFERENT from case.name to avoid noise.
            if case.zephyr_test_name != case.name:
                extra_annotations.append(
                    f'    @io.qameta.allure.Label(name = "zephyrTestName", '
                    f'value = "{_jlit(case.zephyr_test_name)}")')
        if case.jira:
            extra_annotations.append(
                f'    @io.qameta.allure.Issue("{_jlit(case.jira)}")')
        for k, v in (case.tm_extras or {}).items():
            extra_annotations.append(
                f'    @io.qameta.allure.Label(name = "{_jlit(k)}", '
                f'value = "{_jlit(v)}")')
        extra_annotations_str = ("\n" + "\n".join(extra_annotations)
                                  if extra_annotations else "")

        return f"""    @Test(dataProvider = "rows",
          dataProviderClass = PerMethodCsvDataProvider.class,
          groups = {{"imported", "{group_val}"}},
          retryAnalyzer = RetryAnalyzer.class)
    @XrayTest("{_jlit(xray_id_raw)}")
    @Story("{_jlit(story)}")
    @Description("Imported from ReadyAPI. Original description: {desc_safe}"){extra_annotations_str}
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
                              cluster: list[TestCase],
                              stop_markers: dict[str, str] = None,
                              csv_subpackage: Optional[str] = None) -> str:
        """Write `src/test/resources/csv/<suite>[/<sub>]/<class>/<method>.csv`
        with one row per case in the cluster (a cluster is 1..N SoapUI
        cases sharing the same REST step shape). Header is stable across
        runs so adding more scenarios later is a matter of appending rows
        (no code change).

        `csv_subpackage` optionally namespaces the CSV under a resource
        sub-directory (mirrors the Java sub-package layout, so
        `program_account_member.CreateTest#foo` -> CSV at
        `csv/<suite>/program_account_member/CreateTest/foo.csv`). Without
        it, CSVs land at `csv/<suite>/<class>/<method>.csv`. `PerMethod-
        CsvDataProvider` derives the same path from the calling class's
        FQN so both sides agree at runtime.

        Placeholder-friendly: any CSV cell can hold `<<fakerToken>>` or
        `${{propertyRef}}` -- `PlaceholderResolver.resolveRow` (called at
        the top of every @Test method) expands these into live values
        before the row reaches user code.

        When `stop_markers` is populated (cluster is a prefix-merged one),
        adds a `_stop_after` column whose per-row value is the step name
        where the shorter case's flow originally ended -- the emitted
        method returns early at that step so the LONGER cluster's tail
        steps don't run for shorter-scenario rows."""
        stop_markers = stop_markers or {}
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
        #
        # Column NAMING uses cluster[0]'s step name at each REST position
        # -- NOT each case's own step name -- so cluster members with
        # different names for the same-shape step (e.g. `token` vs
        # `getToken`) all write to the SAME column that the emitted Java
        # actually reads. Without this, member #2's cell would land in a
        # column the Java never queries.
        cluster0_rest_step_names: list[str] = [
            s.step_name for s in cluster[0].steps if isinstance(s, RestStep)]
        assert_cols_order: list[str] = []
        assert_vals_per_case: dict[str, list[str]] = {}
        for case_idx, c in enumerate(cluster):
            pos = 0
            for step in c.steps:
                if not isinstance(step, RestStep):
                    continue
                # Use cluster[0]'s step name at this position (falls back to
                # the case's own name for cases longer than cluster[0]
                # -- prefix-merged flavour where cluster[0] is longest).
                canonical_step_name = (
                    cluster0_rest_step_names[pos]
                    if pos < len(cluster0_rest_step_names) else step.step_name)
                pos += 1
                a_idx = 0
                for a in step.assertions:
                    if a.disabled:
                        continue
                    # Simple assertion types: single (col, val)
                    key = _assert_col_key(a, a_idx)
                    a_idx += 1
                    if key is not None:
                        col = f"expected_{sanitize_identifier(canonical_step_name)}_{key}"
                        if col not in assert_vals_per_case:
                            assert_vals_per_case[col] = ["" for _ in cluster]
                            assert_cols_order.append(col)
                        assert_vals_per_case[col][case_idx] = _assert_default_value(a)
                    # Multi-element assertion types (MessageContent /
                    # DataAndMetadata): each enabled element becomes its
                    # own column.
                    for col, val in _assert_element_cols(a, canonical_step_name):
                        if col not in assert_vals_per_case:
                            assert_vals_per_case[col] = ["" for _ in cluster]
                            assert_cols_order.append(col)
                        assert_vals_per_case[col][case_idx] = val

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

        # Include `_stop_after` column ONLY when the cluster is prefix-
        # merged. Header stays lean for regular clusters so authors don't
        # see a mystery blank column.
        stop_col = ["_stop_after"] if stop_markers else []

        # Column order: FOUR logical groups so an Excel user can scan
        # left-to-right and know exactly what they're looking at.
        #
        #   A. Meta / traceability
        #        description, test_case_id, jira_xray_id, variant
        #
        #   B. Control
        #        _stop_after  (only when prefix-merged clusters exist)
        #
        #   C. Request data (everything that goes INTO the request)
        #        csv_columns       -- runtime placeholders (Properties_X)
        #        hint_col_names    -- faker/property hints (<<email>>)
        #        merged_tpl_cols   -- template diff values (tpl_X)
        #
        #   D. Expected values (everything ASSERTED against the response)
        #        expected_status_code   -- top-level status shortcut
        #        expected               -- semicolon combined shortcut
        #        assert_cols_order      -- per-assertion expected values
        #                                  (expected_<step>_<assertion_key>)
        # Migrated frozen-Properties columns. For every CSV-destination key
        # in the migration map that ANY case in this cluster uses, add a
        # column so per-row values reach the emitted `row.getOrDefault(...)`
        # lookup. Skip keys that no case in the cluster references (avoids
        # polluting narrow-scope methods with columns that would always be
        # blank).
        migrated_cols_order: list[str] = []
        migrated_vals_per_case: dict[str, list[str]] = {}
        if self._property_migration:
            cluster_ctx_keys: set[str] = set()
            for c in cluster:
                for step in c.steps:
                    if isinstance(step, PropertiesStep):
                        for prop, val in (step.properties or {}).items():
                            if val:
                                cluster_ctx_keys.add(f'{step.step_name}.{prop}')
            for ctx_key in sorted(cluster_ctx_keys):
                m = self._property_migration.get(ctx_key)
                if not m or m['destination'] != 'csv':
                    continue
                migrated_cols_order.append(ctx_key)
                migrated_vals_per_case[ctx_key] = [
                    m['per_case'].get(c.name, "") for c in cluster
                ]

        group_a_meta = [
            "description",        # SoapUI <con:description> text
            "test_case_id",       # original SoapUI case name (per row)
            "jira_xray_id",       # e.g. B2B-172 (per row)
            "variant",            # scenario disambiguator (from name suffix)
        ]
        group_b_control = stop_col
        group_c_request = (csv_columns + hint_col_names + merged_tpl_cols
                           + migrated_cols_order)
        group_d_expected = ["expected_status_code", "expected"] + assert_cols_order
        cols = group_a_meta + group_b_control + group_c_request + group_d_expected
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
            stop_cell = [_csv_cell(stop_markers.get(c.name, ""))] if stop_markers else []
            # Description from SoapUI: collapse newlines to spaces so it
            # stays a single CSV cell without breaking row boundaries.
            desc_flat = " ".join((c.description or "").split())
            # Match header column groups exactly:
            #   Group A: meta / traceability (4 cells)
            #   Group B: control (0 or 1 cells)
            #   Group C: request data (csv_columns + hint_col_names + merged_tpl_cols)
            #   Group D: expected values (status_code + expected + assert_cols_order)
            group_a_cells = [
                _csv_cell(desc_flat), _csv_cell(c.name),
                _csv_cell(c.prefix), _csv_cell(variant),
            ]
            migrated_cells = [
                _csv_cell(migrated_vals_per_case[col][case_idx])
                for col in migrated_cols_order
            ]
            group_c_cells = (
                ["" for _ in csv_columns] +
                [_csv_cell(hint) for _, hint in hinted_cols] +
                merged_tpl_cells +
                migrated_cells
            )
            group_d_cells = (
                [_csv_cell(derived_status), _csv_cell(expected_str)] +
                assert_cells
            )
            row_cells = group_a_cells + stop_cell + group_c_cells + group_d_cells
            rows.append(",".join(row_cells))

        content = header_row + "\n" + "\n".join(rows) + "\n"
        # CSV lives under the same package tail its Java class does, so
        # two `CreateTest.java` files in different sub-packages don't
        # collide in `csv/`. Loader is `PerMethodCsvDataProvider` and it
        # derives the SAME layout from the calling class's FQN.
        _sub = f"/{csv_subpackage}" if csv_subpackage else ""
        rel = (f"src/test/resources/csv/{self.suite_name}{_sub}/"
               f"{class_name}/{method_name}.csv")
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
        import hashlib, json as _json, re as _re
        self._merged_template_cells = {}

        def _canonicalize(text: str) -> tuple[str, object]:
            """Return (canonical_text, parsed_tree_or_None). For JSON,
            parses and re-serializes with fixed indent so trivial
            whitespace/formatting differences produce identical text.
            For non-JSON, collapses runs of whitespace so extra spaces
            around fields don't make bodies hash differently.

            This kills a class of duplicate templates that only differ
            in author-invisible whitespace (e.g. one file has a leading
            space before "annualSalesRange" and the other doesn't --
            same content, same shape, was two files, now one).

            Repairs a common corruption pattern before giving up on
            JSON parsing: SoapUI XMLs edited by hand on Windows can
            store JSON with 2-char `\\r`/`\\n` sequences (backslash +
            letter) AS whitespace between tokens instead of real CR/LF.
            That produces invalid JSON since `\\r` outside a string is
            not a valid escape. Retry after stripping those sequences."""
            try:
                tree = _json.loads(text)
                canon = _json.dumps(tree, indent=2, ensure_ascii=False,
                                     sort_keys=False)
                return canon, tree
            except (_json.JSONDecodeError, ValueError):
                pass
            # Retry: strip stray backslash-escape sequences that leaked
            # into JSON whitespace positions (2-char `\r`, `\n`, `\t`).
            # These occur when a SoapUI XML is edited and Windows CRLF
            # gets mangled to backslash-r + LF. Replacing globally is
            # safe because inside string values, `\r`/`\n`/`\t` are
            # valid escapes AND already parse-through cleanly; the only
            # problem case is when they land BETWEEN JSON tokens.
            repaired = text.replace("\\r\\n", "\n") \
                            .replace("\\r", " ") \
                            .replace("\\n", "\n") \
                            .replace("\\t", " ")
            try:
                tree = _json.loads(repaired)
                canon = _json.dumps(tree, indent=2, ensure_ascii=False,
                                     sort_keys=False)
                return canon, tree
            except (_json.JSONDecodeError, ValueError):
                pass
            # Still non-JSON: collapse whitespace runs, strip.
            collapsed = _re.sub(r"\s+", " ", repaired).strip()
            return collapsed, None

        # Pass 1: normalize every body, keep provenance for each (case, step).
        # `entries` = [{"case", "step", "translated", "hash", "bucket", "tree"}]
        entries: list[dict] = []
        for case in cases:
            for step in case.steps:
                if not isinstance(step, RestStep):
                    continue
                if not step.request_body.strip():
                    continue
                translated, _ph = soapui_body_to_placeholders(step.request_body)
                # Pick the right file extension for this body's media type
                # (JSON / XML / form / plain). Non-JSON bodies also skip
                # JSON canonicalization -- canonicalize would try to parse
                # XML as JSON and fall through to whitespace-collapse,
                # which is fine but the file extension needs to match so
                # editors + IDEs open with the right syntax highlighting.
                mt = (step.media_type or "application/json").split(";")[0].strip().lower()
                is_json_mt = (mt in ("application/json", "application/vnd.api+json")
                              or mt.endswith("+json"))
                if is_json_mt:
                    canonical, tree = _canonicalize(translated)
                    ext = "json"
                elif mt in ("text/xml", "application/xml", "application/soap+xml") or mt.endswith("+xml"):
                    canonical, tree = translated.strip(), None
                    ext = "xml"
                elif mt == "application/x-www-form-urlencoded":
                    canonical, tree = translated.strip(), None
                    ext = "form"
                else:
                    canonical, tree = translated.strip(), None
                    ext = "txt"
                h = hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:10]
                seg = (step.resource_path or "").strip("/").split("/", 1)[0]
                bucket = sanitize_identifier(seg).lower() or "misc"
                entries.append({
                    "case": case.name, "step": step.step_name,
                    # Write the CANONICAL form on disk (not the raw
                    # SoapUI-preserved formatting) so bodies with
                    # cosmetically-different whitespace collapse into one file.
                    "translated": canonical, "tree": tree,
                    "hash": h, "bucket": bucket, "ext": ext,
                })

        # Pass 2: partition by JSON-parseability. Non-JSON goes straight
        # to exact-body dedup (Tier 1 only).
        json_entries: list[dict] = []
        nonjson_entries: list[dict] = []
        for e in entries:
            if e["tree"] is not None:
                json_entries.append(e)
            else:
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
                f"{sanitize_identifier(first['step']).lower()}_merged_{merged_hash}.{first['ext']}")
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
        every (case, step) to it. Uses the per-entry `ext` field for the
        file extension so XML/form/plain bodies land as `.xml`/`.form`/
        `.txt` instead of `.json`."""
        for e in entries:
            classpath_dir = f"templates/{self.suite_name}/{e['bucket']}/"
            ext = e.get("ext", "json")
            classpath_file = f"{sanitize_identifier(e['step']).lower()}_{e['hash']}.{ext}"
            classpath = classpath_dir + classpath_file
            self._template_path_by_step[(e["case"], e["step"])] = classpath
            if e["hash"] not in hash_to_path:
                self._write(f"src/main/resources/{classpath}", e["translated"])
                hash_to_path[e["hash"]] = classpath

    def emit_templates_class(self) -> Optional[str]:
        """Emit `com.<pkg>.support.<suite>.Templates` -- a constants class
        listing every deduped request-body template with a stable logical
        name that call sites reference instead of the raw string path.

        Design:
          - One `public static final String <NAME> = "<full classpath>"`
            per unique template file.
          - Names derived: `<UPPER_SUBDIR>_<UPPER_BASENAME_WITHOUT_HASH>`.
            The trailing `_<sha1prefix>` hash is stripped so a template's
            NAME stays stable across regenerations even when its content
            (and therefore its hash-suffixed file name) changes. Only the
            VALUE string updates; every call site keeps compiling.
          - Collisions after hash-strip disambiguated with `_2`, `_3`, ...
          - Alphabetically sorted for deterministic diffs.

        Side effect: populates `self._template_const_by_path` -- a
        {full classpath -> constant name} map. `_render_rest_step_body`
        consults it to emit `RestUtilities.getRequestTemplate(Templates.<N>)`
        instead of two literal string args. When the map lookup misses
        (shouldn't happen; guarded), the emitter falls back to the
        legacy two-arg literal form.

        Returns the relative path of the emitted file, or None when no
        templates exist (empty suite)."""
        # Unique full paths across all (case, step) template assignments.
        unique_paths = sorted(set(self._template_path_by_step.values()))
        if not unique_paths:
            self._template_const_by_path = {}
            return None

        # Derive stable constant names.
        # `templates/<suite>/<subdir>/<basename>_<sha1prefix>.<ext>`
        # -> subdir = `<subdir>`, basename = `<basename>` (hash stripped).
        # Prefix subdir uppercase + underscore-joined for global uniqueness.
        seen_names: dict[str, int] = {}
        const_by_path: dict[str, str] = {}
        # 10-char lowercase hex tail preceded by `_` is what the emitter
        # writes; matches `_<8-12 hex>` conservatively to cover renames.
        _hash_tail_rx = re.compile(r'_[0-9a-f]{6,}$', re.I)
        for full_path in unique_paths:
            # `templates/<suite>/<subdir>/<file>.<ext>`
            parts = full_path.split('/')
            if len(parts) < 4:
                # Unexpected shape -- fall back to whole-name uppercased.
                base = re.sub(r'[^A-Za-z0-9]+', '_',
                              full_path.rsplit('/', 1)[-1].rsplit('.', 1)[0])
            else:
                subdir = parts[-2]
                file_stem = parts[-1].rsplit('.', 1)[0]
                # Strip trailing hash suffix so the constant name doesn't
                # rename every time content hashes shift.
                file_stem = _hash_tail_rx.sub('', file_stem)
                base = f'{subdir}_{file_stem}'
            base = re.sub(r'[^A-Za-z0-9]+', '_', base).strip('_').upper() or 'TEMPLATE'
            # Java identifier -- leading digit gets underscore prefix.
            if base[0].isdigit():
                base = '_' + base
            # Collision-safe unique suffix. Must guard against BOTH kinds of
            # collision: (a) two paths reduce to the same base, (b) a base
            # name that happens to look like `<other_base>_<n>` (e.g. a
            # template stem ending in `_2` colliding with the counter-2
            # form of a different base). Walk `n` forward until candidate
            # is free.
            candidate = base
            n = 1
            while candidate in seen_names:
                n += 1
                candidate = f'{base}_{n}'
            seen_names[candidate] = 1
            const_by_path[full_path] = candidate

        # Publish so _render_rest_step_body can look up while rendering.
        self._template_const_by_path = const_by_path

        # Templates lives under its own top-level package (`<root>.templates.<suite>`)
        # so it reads as a first-class framework concept rather than being
        # buried under `support`. Other framework artefacts under `support`
        # (SetupHelper, TestSupport) are per-suite Java LOGIC; Templates
        # is pure data + belongs alongside `rest.utilities` / `data`.
        pkg = f'{self.package_root}.templates.{self.suite_name}'
        rel = f'src/main/java/{pkg.replace(".", "/")}/Templates.java'
        # Emit sorted by constant NAME for readability.
        const_lines: list[str] = []
        for path, name in sorted(const_by_path.items(), key=lambda x: x[1]):
            const_lines.append(f'    public static final String {name} = "{path}";')
        body = "\n".join(const_lines)
        content = f"""package {pkg};

/**
 * Auto-generated by ra_converter. Constants class listing every deduped
 * request-body template shipped with this suite. Test methods reference
 * these constants instead of hardcoding the classpath string, so:
 * <ul>
 *   <li>Template renames only touch this file -- call sites don't churn.</li>
 *   <li>Content-hash changes (which shift the file's suffix) update the
 *       VALUE here but keep the NAME stable, so no test source changes.</li>
 *   <li>IDE autocomplete + Find-Usages work for template references.</li>
 * </ul>
 *
 * <p>Layout: {{@code Templates.<UPPER_SUBDIR>_<UPPER_STEP>}}. See any
 * generated test class for usage:
 * {{@code RestUtilities.getRequestTemplate(Templates.REALMS_TOKENREQUEST)}}.</p>
 */
public final class Templates {{

    private Templates() {{}}

{body}
}}
"""
        self._write(rel, content)
        return rel

    def emit_suite_readme(self, soapui_suite_name: str,
                            bucket_manifest: list[dict]) -> str:
        """Write `src/test/java/<pkg>/README.md` -- a business-friendly
        table of contents for the emitted business-area classes. Lets a
        BA / QA lead scan the resource catalog without opening any Java
        file: one section per resource, one bullet per test class with
        method count + scenario (row) count.

        `bucket_manifest` -- list of {resource, class, fqn, method_count,
        multi_row_methods, case_count} dicts assembled by main().
        """
        pkg_dir = f"src/test/java/{self.package_root.replace('.', '/')}/tests/imported/{self.suite_name}"
        rel = f"{pkg_dir}/README.md"

        # Group by resource for the section layout.
        by_resource: dict[str, list[dict]] = {}
        resource_order: list[str] = []
        for entry in bucket_manifest:
            r = entry["resource"]
            if r not in by_resource:
                by_resource[r] = []
                resource_order.append(r)
            by_resource[r].append(entry)

        total_classes = len(bucket_manifest)
        total_methods = sum(e["method_count"] for e in bucket_manifest)
        total_scenarios = sum(e["case_count"] for e in bucket_manifest)

        lines: list[str] = []
        lines.append(f"# {soapui_suite_name}")
        lines.append("")
        lines.append("Auto-generated business-area catalog for tests imported from ReadyAPI.")
        lines.append("")
        lines.append("| Metric | Count |")
        lines.append("|--------|-------|")
        lines.append(f"| Business-area classes | {total_classes} |")
        lines.append(f"| @Test methods (unique intents) | {total_methods} |")
        lines.append(f"| Scenarios (CSV rows across all methods) | {total_scenarios} |")
        if total_methods:
            lines.append(f"| Reuse factor (scenarios per method) | "
                         f"{(total_scenarios / total_methods):.1f}x |")
        lines.append("")
        lines.append("Layout: one folder per REST resource, one `.java` per operation.")
        lines.append("Add a CSV row under `src/test/resources/csv/<suite>/<resource>/<Class>/`")
        lines.append("to add a scenario -- no code change required.")
        lines.append("")
        lines.append("---")
        lines.append("")

        for resource in resource_order:
            entries = by_resource[resource]
            lines.append(f"## `{resource}/`")
            lines.append("")
            lines.append("| Class | @Test methods | Data-driven | Total scenarios |")
            lines.append("|-------|:-------------:|:-----------:|:---------------:|")
            for e in entries:
                dd = f"{e['multi_row_methods']}" if e["multi_row_methods"] else "-"
                lines.append(f"| `{e['class']}` | {e['method_count']} | {dd} | {e['case_count']} |")
            lines.append("")

        self._write(rel, "\n".join(lines) + "\n")
        return rel

    def emit_master_suite_xml(self, class_fqns: list[str],
                                variant: str = "Regression") -> str:
        """Write a master TestNG suite XML at `Suites/<Suite><Variant>.xml`
        (matching reference framework naming). Lists every generated test
        class. `variant` = 'Regression' | 'Smoke' | 'UAT' | ...

        Emits ONLY listeners we've verified are present on the classpath
        of this framework -- Allure via the always-present allure-testng
        dep, plus the ProgressLogListener that emit_progress_listener()
        writes into `com.ak.api.reporting`. Extent / Xray / TMS
        integrations from the reference framework live in comments so
        authors can uncomment after adding those adapter classes."""
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
        <!-- Allure: always available (allure-testng in pom.xml). -->
        <listener class-name="io.qameta.allure.testng.AllureTestNg"/>
        <!-- Progress banner listener: emitted by ra_converter (see
             emit_progress_listener). Prints class + method + timing to
             the mvn console so parallel classes are attributable. -->
        <listener class-name="com.ak.api.reporting.ProgressLogListener"/>
        <!-- OPTIONAL reference-framework integrations. Uncomment ONLY after
             adding the matching classes under src/main/java/com/ak/api/
             reporting/ (TestNG will fail-fast with ClassNotFound otherwise).
        <listener class-name="com.ak.api.reporting.ExtentReportListener"/>
        <listener class-name="com.ak.api.reporting.XrayReportListener"/>
        -->
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

    def emit_progress_listener(self) -> str:
        """Emit `com.ak.api.reporting.ProgressLogListener` -- a lightweight
        TestNG listener that prints a one-line banner when each @Test
        method starts and finishes. Complements the per-method
        STARTED/FINISHED logs inside the test body: when tests run in
        parallel (parallel="classes"), the listener output is TestNG-
        emitted and therefore reliably interleaved by the framework,
        so you can see WHICH THREAD is on which method.

        Emitted once per output tree (idempotent, framework-level)."""
        pkg = f"{self.package_root}.reporting"
        rel = f"src/main/java/{pkg.replace('.', '/')}/ProgressLogListener.java"
        content = f"""package {pkg};

import java.util.concurrent.ConcurrentHashMap;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.testng.ITestContext;
import org.testng.ITestListener;
import org.testng.ITestResult;

/**
 * TestNG listener that prints per-test banner lines to the console. Auto-
 * generated by ra_converter and wired into every emitted TestNG suite XML.
 * Safe to hand-edit; the converter never overwrites this file after the
 * first run (uses File.exists() check upstream).
 *
 * <p>Output shape:
 * <pre>
 *   [TEST] STARTED  &lt;class&gt;#&lt;method&gt;  (thread=pool-1-thread-3)
 *   [TEST] PASSED   &lt;class&gt;#&lt;method&gt;  (Nms)
 *   [TEST] FAILED   &lt;class&gt;#&lt;method&gt;  (Nms) -- &lt;exception message&gt;
 *   [TEST] SKIPPED  &lt;class&gt;#&lt;method&gt;  (Nms)
 * </pre>
 * </p>
 *
 * <p>Complements the STARTED/FINISHED lines embedded inside test method
 * bodies -- those show sequential progress within one thread; this
 * listener shows the framework-level lifecycle across threads.</p>
 */
public class ProgressLogListener implements ITestListener {{

    private static final Logger LOG = LoggerFactory.getLogger(ProgressLogListener.class);
    private static final ConcurrentHashMap<String, Long> STARTS = new ConcurrentHashMap<>();

    private String key(ITestResult r) {{
        return r.getTestClass().getRealClass().getSimpleName()
                + "#" + r.getMethod().getMethodName() + "@" + r.hashCode();
    }}

    private String label(ITestResult r) {{
        return r.getTestClass().getRealClass().getSimpleName()
                + "#" + r.getMethod().getMethodName();
    }}

    private long elapsedMs(ITestResult r) {{
        Long start = STARTS.remove(key(r));
        if (start == null) return -1L;
        return System.currentTimeMillis() - start;
    }}

    @Override
    public void onTestStart(ITestResult r) {{
        STARTS.put(key(r), System.currentTimeMillis());
        LOG.info("[TEST] STARTED  {{}}  (thread={{}})",
                label(r), Thread.currentThread().getName());
    }}

    @Override
    public void onTestSuccess(ITestResult r) {{
        LOG.info("[TEST] PASSED   {{}}  ({{}}ms)", label(r), elapsedMs(r));
    }}

    @Override
    public void onTestFailure(ITestResult r) {{
        Throwable t = r.getThrowable();
        String msg = (t == null) ? "(no throwable)" : t.getClass().getSimpleName()
                + ": " + (t.getMessage() == null ? "" : t.getMessage());
        LOG.warn("[TEST] FAILED   {{}}  ({{}}ms) -- {{}}", label(r), elapsedMs(r), msg);
    }}

    @Override
    public void onTestSkipped(ITestResult r) {{
        LOG.info("[TEST] SKIPPED  {{}}  ({{}}ms)", label(r), elapsedMs(r));
    }}

    @Override
    public void onStart(ITestContext context) {{
        LOG.info("[TEST] ==== SUITE START: {{}} (thread-count={{}}) ====",
                context.getName(), context.getSuite().getXmlSuite().getThreadCount());
    }}

    @Override
    public void onFinish(ITestContext context) {{
        LOG.info("[TEST] ==== SUITE FINISH: {{}}  passed={{}}, failed={{}}, skipped={{}} ====",
                context.getName(),
                context.getPassedTests().size(),
                context.getFailedTests().size(),
                context.getSkippedTests().size());
    }}
}}
"""
        self._write(rel, content)
        return rel


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

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
    templates_pkg_path = f"src/main/java/{package_root.replace('.', '/')}/templates/{suite_name}"
    imported_pkg_dir = os.path.join(output_dir, pkg_path)

    # Business-area emission writes:
    #   - Java under `<pkg>/tests/imported/<suite>/<resource>/<Op>Test.java`
    #   - CSVs under `src/test/resources/csv/<suite>/<resource>/<Op>Test/*.csv`
    # Wiping the whole `csv/<suite>/` subtree is simpler + safer than
    # enumerating classes -- everything the last run wrote for this suite
    # lives under that one path.
    dirs_to_clean = [
        imported_pkg_dir,
        os.path.join(output_dir, support_pkg_path),
        os.path.join(output_dir, templates_pkg_path),
        os.path.join(output_dir, "src/test/resources/csv", suite_name),
        os.path.join(output_dir, "src/test/resources/testdata", suite_name),
        os.path.join(output_dir, "src/main/resources/templates", suite_name),
        os.path.join(output_dir, "src/main/resources/test_data_defaults", suite_name + ".json"),
        os.path.join(output_dir, "_audit", suite_name),
    ]
    # Legacy: older runs wrote CSVs at `csv/<ClassName>/` (flat layout,
    # no suite dir). Nuke those too so a re-run against an old tree
    # doesn't leave orphan CSVs alongside the new nested ones.
    legacy_csv_root = os.path.join(output_dir, "src/test/resources/csv")
    if os.path.isdir(imported_pkg_dir):
        for entry in os.listdir(imported_pkg_dir):
            if entry.endswith(".java"):
                legacy_cls = entry[:-len(".java")]
                candidate = os.path.join(legacy_csv_root, legacy_cls)
                if os.path.isdir(candidate):
                    dirs_to_clean.append(candidate)

    removed = []
    _locked_files: list[str] = []

    def _on_rm_error(func, path, exc_info):
        """Skip files that another process (usually Excel/IDE) has open,
        log them, and continue. Prior behavior was to fail-fast with a
        PermissionError which forced the user to close every open CSV
        before regenerating.

        Also skip the cascade `OSError: [WinError 145] directory not
        empty` that fires when we skipped a locked file and then
        Python tries to `os.rmdir` the parent directory it lives in --
        that's a natural consequence of the file skip, not a separate
        error worth failing on."""
        err = exc_info[1] if exc_info else None
        if isinstance(err, PermissionError):
            _locked_files.append(path)
            return
        # Windows "directory not empty" after a skipped file inside it.
        if isinstance(err, OSError) and getattr(err, "winerror", None) == 145:
            _locked_files.append(path + " (parent of locked file)")
            return
        # Missing file/dir mid-walk (another process racing us). Ignore.
        if isinstance(err, FileNotFoundError):
            return
        # Re-raise anything else -- real IO error worth stopping on.
        raise err

    for d in dirs_to_clean:
        if os.path.isdir(d):
            shutil.rmtree(d, onerror=_on_rm_error)
            removed.append(d)
    if _locked_files:
        print(f"[ra_converter] --clean: {len(_locked_files)} file(s) locked "
              f"by another process (Excel? IDE? open editor?) -- skipped. "
              f"Close them and re-run to fully clean.")
        for p in _locked_files[:3]:
            print(f"    LOCKED: {p}")
        if len(_locked_files) > 3:
            print(f"    ... and {len(_locked_files) - 3} more")

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

    # Master TestNG suite XMLs are `Suites/<PrettySuite>_<Variant>.xml`,
    # keyed on suite_name (not on per-class names). Sweep them so a
    # renamed suite doesn't leave stale XMLs behind.
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
    # (Legacy `--prefix` and `--all-prefixes` modes have been removed.
    # There is exactly ONE emission mode now: ONE Java test class per
    # SoapUI <testSuite>, with cases clustered into methods, per-method
    # multi-row CSVs, deduped templates, assertions parameterized via
    # CSV cells, faker+property placeholder resolution at runtime, and
    # a master TestNG suite XML with Allure listeners wired in.)
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

    # SINGLE emission mode: one Java test class per SoapUI <testSuite>,
    # cases clustered into methods with N CSV rows, deduped templates,
    # assertions parameterized via CSV cells, per-test-method placeholder
    # resolution at runtime, master TestNG suite XML with Allure wired in.

    # FLATTEN: a SoapUI case that bundles N calls to the same endpoint
    # (create/update/verify all on the same URL) is really N scenarios;
    # split into N pseudo-cases so clustering can fold them with
    # variants from OTHER SoapUI cases into a single method with N rows.
    flattened_suites: list[tuple[str, list[TestCase]]] = []
    total_before, total_after = 0, 0
    for sn, cs in parsed_suites:
        flat = _flatten_repeat_endpoint_cases(cs)
        flattened_suites.append((sn, flat))
        total_before += len(cs)
        total_after += len(flat)
    if total_after > total_before:
        print(f"[ra_converter] flatten repeat-endpoint cases: "
              f"{total_before} SoapUI cases -> {total_after} pseudo-cases "
              f"(split {total_after - total_before} extra pseudo-cases "
              f"so same-endpoint variants can share a method)")
    parsed_suites = flattened_suites
    cases_in_scope = [c for _sn, cs in parsed_suites for c in cs]
    soapui_suites = parsed_suites
    print(f"[ra_converter] {len(soapui_suites)} class(es) will be emitted "
          f"(cases grouped by endpoint-shape into fewer methods "
          f"with N CSV rows each):")
    for sn, cs in soapui_suites:
        clusters_preview = _cluster_cases_by_shape(cs)
        print(f"    - {sn}: {len(cs)} test cases "
              f"-> {len(clusters_preview)} @Test methods "
              f"({sum(1 for cl in clusters_preview if len(cl) > 1)} "
              f"with >1 CSV row)")

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

    # 0) The convention-based DataProvider + PlaceholderResolver need
    #    to exist ONCE per output tree -- rewriting them every run is
    #    idempotent.
    dp_rel = emitter.emit_per_method_csv_data_provider()
    print(f"[ra_converter] emitted convention CSV data provider: {dp_rel}")
    pr_rel = emitter.emit_placeholder_resolver()
    emitter._resolver_emitted = True
    print(f"[ra_converter] emitted placeholder resolver: {pr_rel}")
    # AuthHelper -- class-level OAuth token bootstrap. Every emitted
    # business-area class calls this from @BeforeClass so N @Test methods
    # reuse one token instead of each re-fetching. Inline token-fetch
    # REST steps are wrapped in a `!ctx.containsKey("accessToken")` guard
    # so they run only as a fallback.
    ah_rel = emitter.emit_auth_helper()
    print(f"[ra_converter] emitted auth helper: {ah_rel}")

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

    # 1a-2) Templates constants class -- MUST run after emit_templates_deduplicated
    #     populated `_template_path_by_step`, and BEFORE any _render_step
    #     call (SetupHelper + test-class emitters both look up
    #     `_template_const_by_path` when rendering REST steps).
    tmpl_class_rel = emitter.emit_templates_class()
    if tmpl_class_rel:
        print(f"[ra_converter] emitted templates constants class: "
              f"{tmpl_class_rel} "
              f"({len(emitter._template_const_by_path)} constants -- "
              f"call sites use Templates.<NAME> instead of literal paths)")

    # 1b) Frozen-Properties migration classifier -- must run BEFORE any
    #     _render_step call (SetupHelper AND test-class emitters both
    #     call it, both need `emitter._property_migration` set).
    emitter._property_migration = _classify_frozen_properties(cases_in_scope)
    if emitter._property_migration:
        csv_count = sum(1 for m in emitter._property_migration.values()
                        if m['destination'] == 'csv')
        cfg_count = sum(1 for m in emitter._property_migration.values()
                        if m['destination'] == 'config')
        print(f"[ra_converter] classified {len(emitter._property_migration)} "
              f"frozen Property keys: {csv_count} -> CSV column, "
              f"{cfg_count} -> Config (test_data.*)")
        # Bundled defaults JSON -- TestSupport.testData(row, key) reads
        # this at runtime as the lowest-precedence fallback. Keeps the
        # emitted Java sources free of hardcoded default literals.
        td_json = emitter.emit_test_data_defaults_json(emitter._property_migration)
        if td_json:
            print(f"[ra_converter] emitted test-data defaults JSON: {td_json}")
        td_rel = emitter.emit_test_data_config(emitter._property_migration)
        if td_rel:
            print(f"[ra_converter] emitted test-data hint file: {td_rel} "
                  f"(paste keys into program_configuration.json for env overrides)")

    # 1c) SetupHelper emission runs AFTER template dedup + migration
    #     classification so its shared flow methods reference the
    #     deduped template paths AND emit the migrated putIfAbsent lines.
    if flows:
        helper_rel = emitter.emit_setup_helper(flows, service_class)
        print(f"[ra_converter] emitted setup helper: {helper_rel}")

    # 2) Emit one Java class PER BUSINESS AREA (verb + resource) inside
    #    each SoapUI suite. Package layout:
    #        <root>.tests.imported.<suite>.<resource_slug>.<Op>Test
    #    File tree reads as a resource catalog so a business analyst can
    #    navigate `program_account_member/` -> Create/Update/Delete/... .
    #    CSVs mirror the same tree so no two `CreateTest` classes collide.
    class_fqns: list[str] = []
    total_methods = 0
    total_rows = 0
    # Per-suite bucket manifest for the README emission at the end.
    suite_bucket_manifest: dict[str, list[dict]] = {}
    for soapui_sname, sui_cases in soapui_suites:
        # Bucket cases into (resource_slug, operation_class) so each
        # emitted class holds ONE business intent, not the whole SoapUI
        # suite. Cluster mechanics (shape + prefix merge) run inside
        # `emit_test_class_per_suite` on the bucket's cases.
        buckets: dict[tuple[str, str], list[TestCase]] = {}
        bucket_order: list[tuple[str, str]] = []
        for case in sui_cases:
            key = _business_bucket_of_case(case)
            if key not in buckets:
                buckets[key] = []
                bucket_order.append(key)
            buckets[key].append(case)
        preview = ", ".join(f"{r}/{op}" for r, op in bucket_order[:6])
        print(f"[ra_converter] SoapUI suite '{soapui_sname}' -> "
              f"{len(bucket_order)} business-area class(es): {preview}"
              f"{'...' if len(bucket_order) > 6 else ''}")
        suite_bucket_manifest[soapui_sname] = []

        for (resource_slug, op_class) in bucket_order:
            area_cases = buckets[(resource_slug, op_class)]
            rel, fqn, method_names = emitter.emit_test_class_per_suite(
                soapui_sname, area_cases, service_class,
                class_name_override=op_class,
                subpackage_override=resource_slug)
            class_fqns.append(fqn)

            # `emit_test_class_per_suite` populated emitter._clusters +
            # _cluster_to_method while rendering; reuse them so CSV
            # filenames match method names one-for-one.
            class_simple = fqn.rsplit(".", 1)[-1]
            prefix_merged = 0
            for idx, cluster in enumerate(emitter._clusters):
                mname, status, _variant = emitter._cluster_to_method[idx]
                sm = emitter._stop_markers_per_cluster.get(idx, {})
                if sm:
                    prefix_merged += 1
                emitter.emit_csv_per_method(
                    class_simple, mname, cluster, stop_markers=sm,
                    csv_subpackage=resource_slug)
                total_rows += len(cluster)
                # Record case -> method mapping so authors can verify (and
                # BAs can audit) where each SoapUI case landed. One ledger
                # row per case in the cluster; cluster_row_index tracks
                # the row in the CSV file that this case occupies.
                csv_rel_path = (
                    f"src/test/resources/csv/{suite_name}/{resource_slug}/"
                    f"{class_simple}/{mname}.csv")
                for row_idx, c in enumerate(cluster, start=1):
                    xray = c.prefix if re.match(r"^[A-Z]+-\d+$", c.prefix or "") else ""
                    ledger.add_case_mapping(
                        soapui_sname, c.name, xray, fqn, mname,
                        csv_rel_path, len(cluster), row_idx, status)
            multi = sum(1 for cl in emitter._clusters if len(cl) > 1)
            print(f"[ra_converter]   {resource_slug}/{class_simple}  "
                  f"({len(method_names)} @Test method(s), "
                  f"{multi} with >1 CSV row, "
                  f"{prefix_merged} prefix-merged)")
            total_methods += len(method_names)
            suite_bucket_manifest[soapui_sname].append({
                "resource": resource_slug,
                "class": class_simple,
                "fqn": fqn,
                "method_count": len(method_names),
                "multi_row_methods": multi,
                "case_count": len(area_cases),
            })
    print(f"[ra_converter] total: {len(class_fqns)} business-area class(es), "
          f"{total_methods} @Test method(s), "
          f"{total_rows} CSV row(s) across "
          f"{total_methods} CSV file(s) under src/test/resources/csv/  "
          f"(saved {total_rows - total_methods} methods via clustering+prefix-merge)")
    # Prominent conversion summary + pointer to the traceability CSV so
    # authors and BAs know where to look to verify per-case landings.
    src_case_count = len(cases_in_scope)
    dest_row_count = len(ledger.case_mapping)
    print(f"[ra_converter] CONVERSION SUMMARY:")
    print(f"[ra_converter]   {src_case_count} ReadyAPI test case(s) -> "
          f"{total_methods} REST Assured @Test method(s) "
          f"({dest_row_count} case-rows in CSVs)")
    print(f"[ra_converter]   case -> method mapping (open in Excel to verify): "
          f"_audit/{suite_name}/case_to_method_mapping.csv")

    # 2b) Per-suite README.md -- business-friendly table of contents so
    #     a BA can navigate the resource catalog without opening Java files.
    for soapui_sname, manifest in suite_bucket_manifest.items():
        if manifest:
            readme_rel = emitter.emit_suite_readme(soapui_sname, manifest)
            print(f"[ra_converter] emitted business-area README: {readme_rel}")

    # 3) Env configs (one file per env, containing every distinct config key).
    # When the source XML declares SoapUI environments, use their names as
    # the authoritative list AND union with any --envs values so the user
    # can add extras (dev, ci) that aren't in the XML. Otherwise use the
    # CLI list verbatim.
    env_names: list[str] = [e.strip() for e in args.envs.split(",") if e.strip()]
    if _PROJECT_ENVIRONMENTS:
        for xml_env in _PROJECT_ENVIRONMENTS:
            if xml_env not in env_names:
                env_names.append(xml_env)
        print(f"[ra_converter] detected {len(_PROJECT_ENVIRONMENTS)} "
              f"SoapUI environments in project XML: "
              f"{', '.join(sorted(_PROJECT_ENVIRONMENTS))}")
    for env in env_names:
        emitter.emit_env_config(cases_in_scope, env_name=env)
    print(f"[ra_converter] emitted env config for: {', '.join(env_names)}")

    # 4) Master TestNG suite XMLs at Suites/<Suite>_Regression.xml
    #    (+ _Smoke.xml as a starter template with same class list;
    #    authors typically narrow it later by editing groups).
    # Framework-level ProgressLogListener emitted ONCE and referenced by
    # every suite XML. Provides per-@Test STARTED/PASSED/FAILED banners
    # visible in the mvn console -- crucial for `parallel="classes"`
    # runs where multiple threads interleave inline method-body logs.
    listener_rel = emitter.emit_progress_listener()
    print(f"[ra_converter] emitted progress listener: {listener_rel}")
    for variant in ("Regression", "Smoke"):
        master_rel = emitter.emit_master_suite_xml(class_fqns, variant=variant)
        print(f"[ra_converter] emitted master suite: {master_rel}")

    # Flow diagram for visualizing shared setup.
    flow_rel = emitter.emit_flow_diagram(
        args.input, cases_in_scope, flows,
        service_class, shared_ops_count=len(shared_ops))
    print(f"[ra_converter] emitted flow diagram: {flow_rel}")

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

    # Frozen Properties-step literals -- ENV-FROZEN values baked into
    # each emitted method. Surface prominently: if a test suite ships
    # 300+ hardcoded IDs/emails/domains and they never get moved into
    # per-env config, cross-env runs will silently 404 without warning.
    fp_total = len(ledger.frozen_properties)
    if fp_total:
        fp_unique = len({row[5] for row in ledger.frozen_properties})
        fp_cases = len({row[1] for row in ledger.frozen_properties})
        fp_multival = 0
        _tmp: dict = {}
        for _, _, _, _, val, key in ledger.frozen_properties:
            _tmp.setdefault(key, set()).add(val)
        fp_multival = sum(1 for vs in _tmp.values() if len(vs) > 1)
        print(f"[ra_converter]   frozen Properties.* literals: {fp_total} "
              f"emissions across {fp_unique} unique ctx keys, {fp_cases} case(s) affected")
        if fp_multival:
            print(f"[ra_converter]     ^ {fp_multival} key(s) hold DIFFERENT "
                  f"values across cases -- strong CSV-column candidates")
        print(f"[ra_converter]     see _audit/{suite_name}/frozen_properties.csv "
              f"+ 'Frozen Properties-step literals' section in summary.md")

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
