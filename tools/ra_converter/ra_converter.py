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
    # Bug #2 fix: SoapUI's <con:parameters> is a flat list -- SoapUI itself
    # relies on a service-definition `style` attr to route each entry to
    # header vs query, but the exported project XML doesn't carry that
    # metadata reliably. Prior whitelist of 6 names left everything else
    # (Hilton-Operator-DutyCode, X-JWT-Assertion, Hilton-Operator-Location,
    # X-Correlation-Id-Extension, etc.) landing as ?queryKey=val on the
    # wire, while ReadyAPI actually sent them as HTTP headers. Effect:
    # some Hilton APIs use these for duty-code routing / auth, so a
    # misrouted request returns 401/403/data-wrong-shape.
    #
    # Heuristic (HTTP naming convention beats guessing):
    #   1. exact match on the well-known whitelist  -> header
    #   2. contains "-" (Title-Case-With-Hyphens)   -> header
    #   3. starts with "X-" or "x-" case-ins        -> header (RFC 6648)
    #   4. otherwise                                -> query
    # Query params are conventionally camelCase / snake_case, no hyphens,
    # so no false positives against real query params.
    path_params, headers, query_params = {}, {}, {}
    header_names = {"authorization", "content-type", "accept", "correlationid",
                    "x-correlation-id", "x-request-id"}
    def _looks_like_header(key: str) -> bool:
        kl = (key or "").lower()
        if kl in header_names:
            return True
        if "-" in key:
            return True
        if kl.startswith("x-"):
            return True
        return False
    for k, v in all_params.items():
        if f"{{{k}}}" in resource_path:
            path_params[k] = v
        elif _looks_like_header(k):
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

    Tokenizes on WORD boundaries -- URL separators (`/_-. `) AND CamelCase
    transitions -- so `DeleteProgramAccountMember` splits into
    `[delete, program, account, member]` and DELETE fires. Prior
    behaviour split only on URL separators, so any all-camel op name
    stayed a single token and no keyword ever matched -> body-based
    fallback (POST/GET) took over -> DELETE methods emitted as GET."""
    hay = " ".join((method_name, step_name, resource_path))
    # Insert spaces at CamelCase boundaries: `DeleteFoo` -> `Delete Foo`,
    # `HTTPRequest` -> `HTTP Request`, `deleteXBar` -> `delete X Bar`.
    hay = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", hay)
    hay = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", hay)
    # Then split on URL separators + spaces.
    tokens = re.split(r"[/_\-\.\s]+", hay.lower())
    token_set = set(t for t in tokens if t)
    for verb, kws in _METHOD_KEYWORDS.items():
        for kw in kws:
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
        # Audit fix #6: ReadyAPI JDBC DataSource variant embeds its
        # config as UNQUALIFIED elements inside <con:configuration>:
        #    <con:configuration>
        #      <driver>org.postgresql.Driver</driver>
        #      <connstr>jdbc:postgresql://...</connstr>
        #      <pass>...</pass>
        #      <query>SELECT ...</query>
        #    </con:configuration>
        # Prior parser only walked <con:property>, so JDBC DataSource
        # steps silently emitted with 0 columns and 0 config -- any
        # ${DataSource#col} ref then resolved to null. Currently the
        # only JDBC DataSources in accountmemberregression.xml are
        # `disabled="true"`, so no runtime break, but any enabled JDBC
        # DataSource in another SoapUI export would silently fail.
        for cfg_container in cfg_el.findall(".//con:configuration", NS):
            for jdbc_tag in ("driver", "connstr", "pass", "query"):
                el = cfg_container.find(jdbc_tag)  # unqualified
                if el is not None and el.text:
                    # Store into step's attributes if the shape supports it;
                    # otherwise stash under the columns list with a
                    # `_jdbc_<tag>` marker so downstream can reason about it.
                    marker = f"_jdbc_{jdbc_tag}"
                    if marker not in ds.columns:
                        ds.columns.append(marker)
                    # Track the raw value for the file_path slot when it's
                    # a connstr (closest match to "external data source
                    # locator" that DataSourceStep exposes today).
                    if jdbc_tag == "connstr" and not ds.file_path:
                        ds.file_path = el.text.strip()
    return ds


def _parse_transfer_step(step_el: ET.Element) -> TransferStep:
    step_name = step_el.get("name", "")
    ts = TransferStep(step_name=step_name)
    cfg_el = step_el.find("con:config", NS)
    if cfg_el is not None:
        # SoapUI stores each transfer as ONE `<con:transfers>` element
        # (plural is the type-attribute-carrier, not a container -- the
        # step element can have several sibling `<con:transfers>`
        # children, one per transfer). Prior code looked for
        # `<con:transfer>` (singular) and always found zero -- every
        # PropertyTransfer step therefore emitted an empty stub with
        # no ctx.put and the audit logged them as SKIPPED.
        # Also: field names are `sourceStep` / `targetStep`, NOT
        # `sourceStepName` / `targetStepName`. `<con:type>` carries
        # the path language (JSONPATH / XPATH). `<con:targetType>`
        # is the target property NAME when `<con:targetPath>` is empty
        # (surprising, but matches SoapUI runtime semantics).
        for xfer in cfg_el.findall(".//con:transfers", NS):
            src = xfer.find("con:sourceStep", NS)
            src_path = xfer.find("con:sourcePath", NS)
            src_type = xfer.find("con:sourceType", NS)
            src_lang = xfer.find("con:type", NS)
            tgt = xfer.find("con:targetStep", NS)
            tgt_path = xfer.find("con:targetPath", NS)
            tgt_type = xfer.find("con:targetType", NS)
            # SoapUI writes the target property name in one of two
            # places depending on how the transfer was authored:
            # targetPath if non-empty, else targetType. Consumer code
            # uses whichever is set to build the ctx key.
            tp = _text(tgt_path) or _text(tgt_type)
            ts.transfers.append({
                "source_step": _text(src),
                "source_path": _text(src_path),
                "source_type": _text(src_type),
                "source_path_language": _text(src_lang),
                "target_step": _text(tgt),
                "target_path": tp,
                "target_type": _text(tgt_type),
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
    # Audit fix #10: standalone JDBC steps may embed <query>/<driver>/
    # <connstr> as UNQUALIFIED children (same shape as the JDBC
    # DataSource variant in #6). Some SoapUI exports use the namespaced
    # form (<con:query>) while others don't. Check both to catch either
    # shape -- prior parser missed the unqualified form and emitted
    # empty query/driver/connection for those steps, producing a JDBC
    # emit with no SQL to run.
    def _first(el, tag):
        # Try namespaced then unqualified. Both forms observed in the wild.
        found = el.find(f"con:{tag}", NS) if el is not None else None
        if found is None and el is not None:
            found = el.find(tag)
        return _text(found) if found is not None else ""
    query  = _first(cfg_el, "query")
    conn   = _first(cfg_el, "connectionString")
    if not conn:
        # Unqualified variant uses `connstr` (short form).
        conn = _first(cfg_el, "connstr")
    driver = _first(cfg_el, "driver")
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
        # ReadyAPI exports the delay as an UNQUALIFIED `<delay>N</delay>`
        # child element (no `con:` namespace prefix), NOT as a namespaced
        # `<con:delay>` or an attribute. Prior code only checked the
        # namespaced form + attribute form -- both miss the actual XML
        # shape, so every delay step silently emitted `Thread.sleep(0L)`.
        # Symptom: race between HHonorsEnroll and the immediately-following
        # POST /guests/.../businesses call, which stg then rejects with
        # "Member status is invalid" because the enrolled member record
        # isn't fully committed yet. ReadyAPI honored the delay; our
        # tests skipped it -> "ReadyAPI passes but our tests fail" divergence.
        raw = _text(cfg_el.find("delay"))  # unnamed child (actual shape)
        if raw is None:
            raw = _text(cfg_el.find("con:delay", NS))  # namespaced (older exports)
        if raw is None:
            raw = cfg_el.get("delay", "0")  # attribute form (fallback)
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


# Numeric segments treated as hardcoded ids (6+ digits).
# Bare 5-digit-or-shorter segments stay literal to avoid touching
# API version markers (`v2`), port fragments, or short SKUs.
_HARDCODED_PATH_ID_RX = re.compile(r"^\d{6,}$")


def _normalize_hardcoded_path_ids(resource_path: str,
                                  path_params: dict) -> tuple[str, dict, list]:
    """Rewrite bare numeric-id segments to {paramName} placeholders and
    add matching Properties refs to path_params. Only fires on segments
    of 6+ digits so version markers (`v2`), short numeric SKUs, and
    port fragments are left alone.

    Prior emit only rewrote hardcoded ids when the URL already had a
    matching `{X}` template -- SoapUI URLs like
    `/guests/076187465/businesses` (no braces) shipped the stale id
    verbatim, then `http_request_200_1` hit a non-existent guest and
    cascaded 400/404 through every downstream step that depended on
    its response.

    Returns (new_path, updated_path_params, rewrite_log). Callers can
    surface rewrite_log via the audit ledger."""
    if not resource_path or '/' not in resource_path:
        return resource_path, path_params or {}, []
    segments = resource_path.split('/')
    new_segments: list[str] = []
    new_params = dict(path_params) if path_params else {}
    used_names = set(new_params.keys())
    rewrites: list[tuple[str, str, str]] = []  # (segment, param, orig_value)
    for i, seg in enumerate(segments):
        if not _HARDCODED_PATH_ID_RX.fullmatch(seg):
            new_segments.append(seg)
            continue
        # Bug A #1 fix (pre-pass): skip rewrite when the segment is a
        # run of identical digits (`8888888888`, `9999999999999`,
        # `1111111111`). Author-picked "guaranteed non-existent" ids
        # for `_notexist_` / `_invalid_` / negative test cases --
        # rewriting to a ctx lookup defeats the test's whole point.
        # Mirrors the guard at the emit-site rewrite (line ~4809).
        if len(set(seg)) == 1:
            new_segments.append(seg)
            continue
        # Derive param name from the preceding non-empty segment.
        parent = ""
        for j in range(i - 1, -1, -1):
            if segments[j] and not segments[j].startswith('{'):
                parent = segments[j]
                break
        # Naive English singularization; keeps 'businesses' -> 'businesse'
        # imperfect but stable + collision-safe. Path-arg lookup goes
        # through Properties.<name> so the exact singular form doesn't
        # need to match a domain vocabulary.
        singular = parent[:-1] if parent.endswith('s') and len(parent) > 1 else parent
        base_name = f"{singular}Id" if singular else "id"
        # Collision-resolve: guestId, guestId2, guestId3 ...
        param_name = base_name
        n = 2
        while param_name in used_names:
            param_name = f"{base_name}{n}"
            n += 1
        used_names.add(param_name)
        new_segments.append("{" + param_name + "}")
        new_params[param_name] = "${#TestCase#Properties." + param_name + "}"
        rewrites.append((seg, param_name, seg))
    return '/'.join(new_segments), new_params, rewrites


def normalize_hardcoded_path_ids_in_place(cases: list) -> list:
    """Walk every RestStep in every case and mutate resource_path +
    path_params to replace bare hardcoded numeric ids with {param}
    templates. Runs BEFORE `collect_shared_rest_steps` so client
    method grouping (keyed by (method_name, resource_path)) sees the
    normalized form -- otherwise the emitted client method signature
    would be based on the raw literal path but call sites would use
    the normalized template, breaking the (op, path) lookup.

    Returns a flat list of rewrites: [(case_name, step_name, seg, param)]
    for the audit ledger to log."""
    all_rewrites: list[tuple[str, str, str, str]] = []
    for case in cases:
        for step in getattr(case, 'steps', []):
            if not isinstance(step, RestStep):
                continue
            new_path, new_params, rewrites = _normalize_hardcoded_path_ids(
                step.resource_path, step.path_params)
            if rewrites:
                step.resource_path = new_path
                step.path_params = new_params
                for seg, param, orig in rewrites:
                    all_rewrites.append(
                        (case.name, step.step_name, orig, param))
    return all_rewrites


_EMBEDDED_DOLLAR_REF_RX = re.compile(r"\$\{[^}]+\}")


def normalize_dollar_refs_in_resource_paths(cases: list) -> int:
    """Rewrite SoapUI `${...}` refs embedded in a resource_path to
    `{paramName}` java-template braces, adding matching path_params
    entries with the ORIGINAL `${...}` value preserved. Downstream
    path-arg processing calls soapui_expr_to_java on those preserved
    refs -- which already handles `${step#Response#$.X}`
    (safeJsonExtract), `${step#property}` (ctxGet), etc.

    Prior emit quoted the path verbatim -- SoapUI paths like
    `/guests/${HHonorsEnroll#Response#$.guestId}/members` shipped
    the `${...}` literal on the wire, always 404. Runs BEFORE
    collect_shared_rest_steps so client method grouping keys agree
    with the normalized template.

    Returns the count of steps whose resource_path was rewritten."""
    changed = 0
    for case in cases:
        for step in getattr(case, 'steps', []):
            if not isinstance(step, RestStep):
                continue
            path = step.resource_path or ""
            if "${" not in path:
                continue
            existing = set(re.findall(r"\{([A-Za-z0-9_]+)\}", path))
            existing |= set((step.path_params or {}).keys())
            new_params = dict(step.path_params) if step.path_params else {}
            touched = False

            def _replace(m):
                nonlocal touched
                orig = m.group(0)
                inner = orig[2:-1]  # strip ${...}
                # Best-effort param name from the last identifier
                # inside the ref. Falls back to `pathArg` on exotic
                # shapes. Collision-resolves with numeric suffix so
                # multiple embedded refs get unique names.
                tail = re.split(r"[#.$'\"\[\]]", inner)
                base = ""
                for tok in reversed(tail):
                    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", tok or ""):
                        base = tok
                        break
                if not base:
                    base = "pathArg"
                name = base
                n = 2
                while name in existing:
                    name = f"{base}{n}"
                    n += 1
                existing.add(name)
                new_params[name] = orig
                touched = True
                return "{" + name + "}"

            new_path = _EMBEDDED_DOLLAR_REF_RX.sub(_replace, path)
            if touched:
                step.resource_path = new_path
                step.path_params = new_params
                changed += 1
    return changed


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
    _skipped_suites: list[str] = []
    _skipped_cases: list[tuple[str, str]] = []
    _skipped_steps: list[tuple[str, str, str]] = []
    for ts_el in ts_elements:
        suite_name = ts_el.get("name", "") or "unnamed_suite"
        # SoapUI/ReadyAPI honors `disabled="true"` at three levels
        # (testSuite / testCase / testStep). A disabled test in the
        # ReadyAPI UI does NOT run; before this filter the converter
        # emitted disabled cases anyway, so tests the author had
        # explicitly turned off (e.g., because the API contract
        # changed and the assertions were stale) still ran against
        # stg and produced misleading failures. Skip disabled items
        # entirely and stash a summary the caller prints so the
        # exclusions are visible.
        if ts_el.get("disabled", "").lower() == "true":
            _skipped_suites.append(suite_name)
            continue
        cases: list[TestCase] = []
        for tc_el in ts_el.findall("con:testCase", NS):
            if tc_el.get("disabled", "").lower() == "true":
                _skipped_cases.append((suite_name, tc_el.get("name", "")))
                continue
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
                if step_el.get("disabled", "").lower() == "true":
                    _skipped_steps.append(
                        (suite_name, tc_el.get("name", ""),
                         step_el.get("name", "")))
                    continue
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
    # Print a one-shot exclusion summary so `disabled="true"` items
    # aren't silently dropped -- ops needs to know which cases the
    # author took offline. Stdout so the ra_converter run banner
    # captures it alongside the rest of the emit-time output.
    if _skipped_suites or _skipped_cases or _skipped_steps:
        print(f"[ra_converter] SoapUI `disabled=\"true\"` exclusions "
              f"(not emitted): "
              f"{len(_skipped_suites)} suite(s), "
              f"{len(_skipped_cases)} case(s), "
              f"{len(_skipped_steps)} step(s)")
        for sn in _skipped_suites[:5]:
            print(f"    [suite disabled] {sn}")
        if len(_skipped_suites) > 5:
            print(f"    ... and {len(_skipped_suites) - 5} more suite(s)")
        for sn, cn in _skipped_cases[:20]:
            print(f"    [case disabled]  {sn} / {cn}")
        if len(_skipped_cases) > 20:
            print(f"    ... and {len(_skipped_cases) - 20} more case(s)")
        for sn, cn, stn in _skipped_steps[:20]:
            print(f"    [step disabled]  {sn} / {cn} / {stn}")
        if len(_skipped_steps) > 20:
            print(f"    ... and {len(_skipped_steps) - 20} more step(s)")
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
    # (prefix, case, step_name, step_type, method_or_kind, endpoint_or_query,
    #  coverage, gap_detail)
    # One row per SoapUI step (REST/Groovy/Properties/DataSource/Transfer/
    # Manual/Jdbc). Fills the traceability hole where non-assertion steps
    # were invisible in the old audit: REST step -> assertion CSV only if
    # it had assertions, JDBC step -> unmapped.csv only if untranslated,
    # PropertyTransfer step -> completely silent. Now every step reports.
    steps: list[tuple] = field(default_factory=list)
    # (prefix, case, java_method, reason, source_step)
    # Every ra_converter-emitted `throw new SkipException(...)` -- the
    # runtime-visible outcome that hides from the audit today because the
    # throw is assembled from string parts at emit time. Populated by the
    # emitter site that generates the throw. Rolled into summary.md as
    # "Cases skipped at runtime".
    runtime_skips: list[tuple] = field(default_factory=list)
    # (severity, category, case, detail)
    # Preflight-lint findings: bug patterns detected at emit time that
    # would cause runtime failures. Auto-applied fixes (token hoist,
    # token inject) are recorded here too so a QA lead can see WHAT the
    # converter had to work around. Rendered as `preflight.md` +
    # rolled into `summary.md`.
    preflight: list[tuple] = field(default_factory=list)

    def add_assertion(self, prefix, case, step, soapui_type, cfg,
                       emitted_java, coverage, partial_because=""):
        """Record a translated (or partially translated) SoapUI assertion.
        ``partial_because`` is a short human-readable phrase explaining WHY
        coverage is not FULL (e.g. "content is JSON blob -- deep-diff not
        translated"). Absent for FULL rows. Piped into unmapped.csv so a
        QA lead can act on a gap without cross-referencing config_json."""
        self.assertions.append((prefix, case, step, soapui_type,
                                 json.dumps(cfg or {}), emitted_java, coverage))
        if coverage in ("TODO", "STUB", "PARTIAL"):
            reason = partial_because or self._default_partial_reason(soapui_type, coverage)
            self.unmapped.append((prefix, case, step, "assertion",
                                   f'{soapui_type} ({coverage}): {reason}'))

    def _default_partial_reason(self, soapui_type: str, coverage: str) -> str:
        """Fallback reason text when an emit site did not pass one."""
        if coverage == "TODO":
            return f'no recognizer for assertion type "{soapui_type}" -- ' \
                   f'add a branch in Emitter._render_assertion'
        if coverage == "STUB":
            return f'runnable no-op stub emitted; hand-translate required'
        return f'partial translation -- see config_json in assertions.csv'

    def add_step(self, prefix, case, step_name, step_type, method_or_kind,
                  endpoint_or_query, coverage, gap_detail=""):
        """Record one SoapUI step's emit outcome. Populated by every
        step renderer (REST/Groovy/Properties/DataSource/Transfer/Manual/
        Jdbc) so the audit shows every step, not just assertions and
        Groovy. Feeds unmapped.csv when coverage is not FULL."""
        self.steps.append((prefix, case, step_name, step_type,
                            method_or_kind, endpoint_or_query, coverage,
                            gap_detail))
        if coverage in ("TODO", "STUB", "PARTIAL"):
            det = gap_detail or f'{step_type} coverage {coverage}'
            self.unmapped.append((prefix, case, step_name, "step",
                                   det))

    def add_runtime_skip(self, prefix, case, java_method, reason,
                          source_step=""):
        """Record a `throw new SkipException(...)` the emitter inserted
        so the audit surfaces "cases that will always skip at runtime"
        instead of that failure mode hiding from the summary."""
        self.runtime_skips.append((prefix, case, java_method, reason,
                                    source_step))

    def add_preflight_finding(self, severity, category, case, detail):
        """Record a preflight-lint finding: a bug pattern detected at
        conversion time that WOULD cause runtime failures. Severity in
        {BLOCKER, HIGH, MEDIUM, LOW, INFO}. Category is a short slug
        (e.g. 'token-hoist-applied', 'token-injected', 'missing-config-key',
        'unresolved-groovy-project-ref'). Piped into preflight.md so a
        QA lead can scan the class of bugs BEFORE running the suite."""
        if not hasattr(self, "preflight"):
            self.preflight = []
        self.preflight.append((severity, category, case, detail))

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
        Java class + method + CSV row. Empty ``expected_status`` is
        back-filled from the case-name suffix (e.g. `..._200`, `..._404`)
        so the summary roll-up covers cases whose XML didn't declare it
        as an explicit status assertion."""
        if not expected_status:
            m = re.search(r'_(\d{3})(?:_|$|\W)', soapui_case or "")
            if m:
                expected_status = m.group(1)
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

    def _write_preflight_md(self, base: str, rows: list) -> None:
        """Human-readable preflight report. Sits next to summary.md so a
        QA lead can scan `preflight.md` BEFORE running the suite and know
        which categories of failure to expect (and which auto-fixes the
        emitter already applied)."""
        severities = ["BLOCKER", "HIGH", "MEDIUM", "LOW", "INFO"]
        by_sev = {s: [] for s in severities}
        by_cat = defaultdict(int)
        for sev, cat, case, det in rows:
            by_sev.setdefault(sev, []).append((cat, case, det))
            by_cat[cat] += 1
        lines = [
            "# Preflight report",
            "",
            "> Bug-pattern scan run at conversion time. Findings here map to",
            "> known classes of runtime failure -- fix or acknowledge each",
            "> BEFORE running the suite. Auto-applied fixes (token-hoist,",
            "> token-inject) are listed too so you know what the emitter",
            "> did on your behalf.",
            "",
            f"- Total findings: **{len(rows)}**",
            f"- Blockers: **{len(by_sev.get('BLOCKER', []))}** "
            "(runtime WILL fail without manual action)",
            f"- Auto-fixes applied: "
            f"**{by_cat.get('token-hoist-applied', 0)}** hoist + "
            f"**{by_cat.get('token-injected', 0)}** inject",
            "",
            "## Findings by category",
            "",
            "| Category | Count | Meaning |",
            "|---|---:|---|",
        ]
        MEANINGS = {
            "token-hoist-applied": "tokenRequest reordered to run first",
            "token-injected": "synthetic tokenRequest cloned from suite",
            "missing-token-with-no-canonical": "no token step + no template to clone -- REST will 401",
            "jdbc-mutation-skip": "untranslated Groovy JDBC mutation -- test throws SkipException",
            "unresolved-project-ref": "Groovy uses `#Project#Foo` not in Config -- resolves empty",
            "unresolved-step-ref": "`${step#Response#...}` refers to step not in the same method",
        }
        for cat, n in sorted(by_cat.items(), key=lambda x: -x[1]):
            meaning = MEANINGS.get(cat, "(no meaning registered)")
            lines.append(f"| {cat} | {n} | {meaning} |")
        if not by_cat:
            lines.append("| _(no findings)_ | 0 | -- |")
        lines.append("")
        for sev in severities:
            items = by_sev.get(sev, [])
            if not items:
                continue
            lines.extend([
                f"## {sev} ({len(items)})",
                "",
                "| Category | Case | Detail |",
                "|---|---|---|",
            ])
            for cat, case, det in items[:50]:
                det_short = (det or "").replace("|", "\\|")[:160]
                case_short = (case or "")[:60]
                lines.append(f"| {cat} | {case_short} | {det_short} |")
            if len(items) > 50:
                lines.append(f"| ... | ... | _(+{len(items) - 50} more in preflight.csv)_ |")
            lines.append("")
        os.makedirs(base, exist_ok=True)
        with open(os.path.join(base, "preflight.md"), "w",
                  encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

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
        # Per-step ledger. Every SoapUI step of every case appears here
        # (REST/Groovy/Properties/DataSource/Transfer/Manual/Jdbc), not
        # just the ones that had assertions or that failed to translate.
        # Enables "which steps didn't fully translate?" queries the old
        # audit could not answer.
        self._write_csv(
            os.path.join(base, "steps.csv"),
            ["prefix", "case", "step_name", "step_type",
             "method_or_kind", "endpoint_or_query", "coverage",
             "gap_detail"],
            self.steps)
        # Cases whose @Test body ends with `throw new SkipException` --
        # they will always skip at runtime regardless of env. Kept in
        # their own ledger so summary.md can loudly report coverage loss
        # (a pass=0/fail=0/skip=48 suite otherwise hides the truth).
        self._write_csv(
            os.path.join(base, "runtime_skips.csv"),
            ["prefix", "case", "java_method", "reason", "source_step"],
            self.runtime_skips)
        # Preflight lint findings -- bug patterns detected at emit time
        # (see AuditLedger.add_preflight_finding). Written whether or not
        # findings exist so a QA lead can trust "empty preflight.csv =
        # nothing flagged" instead of second-guessing whether the check ran.
        pre_rows = getattr(self, "preflight", [])
        self._write_csv(
            os.path.join(base, "preflight.csv"),
            ["severity", "category", "case", "detail"],
            pre_rows)
        # Also emit a human-readable preflight.md scoped to the top
        # findings-by-severity + auto-fixes summary.
        self._write_preflight_md(base, pre_rows)

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

        # Preflight callout at TOP of summary so it can't be missed.
        pre_rows = getattr(self, "preflight", []) or []
        pre_blockers = sum(1 for r in pre_rows if r[0] == "BLOCKER")
        pre_high = sum(1 for r in pre_rows if r[0] == "HIGH")
        pre_auto = sum(1 for r in pre_rows
                       if r[1] in ("token-hoist-applied", "token-injected"))
        preflight_callout = []
        if pre_rows:
            preflight_callout = [
                f"> **Preflight**: {len(pre_rows)} finding(s) "
                f"(**{pre_blockers} BLOCKER** / {pre_high} HIGH / "
                f"{pre_auto} auto-fix). "
                f"Open `preflight.md` before running the suite.",
                "",
            ]

        lines = [
            f"# ra_converter audit report",
            "",
            f"- Generated: {generated_at}",
            f"- Source XML: `{source_xml}`",
            f"- Output root: `{output_dir}`",
            "",
            *preflight_callout,
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
            # Expected-status roll-up (from `expected_status` col, back-filled
            # by add_case_mapping from the case-name `_NNN` suffix when
            # the XML didn't declare it explicitly).
            status_counts: dict[str, int] = defaultdict(int)
            for row in self.case_mapping:
                st = (row[8] or "").strip() or "(unknown)"
                status_counts[st] += 1
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
                "> ⚠ `xray_key` column in the CSV holds the SoapUI case_id "
                "(what `@XrayTest(...)` in the emitted Java carries). It is NOT "
                "a real Jira/Xray issue key -- Xray sync via this column will "
                "not resolve to tickets. Populate a genuine key by adding a "
                "`<con:properties>` entry named `xray_key` to each case in the "
                "SoapUI XML, or hand-edit `case_to_method_mapping.csv` post-run.",
                "",
                "### Test counts by expected status",
                "",
                "| Status | Cases |",
                "|---|---:|",
            ])
            for st in sorted(status_counts, key=lambda k: (k == "(unknown)", k)):
                lines.append(f"| {st} | {status_counts[st]} |")
            lines.append("")

        # ---- Per-case assertion coverage roll-up ---------------------
        # Rolls the flat `assertions.csv` up per case so a QA lead can
        # scan "which cases have PARTIAL assertions that need eyes?"
        # without pivoting the flat CSV in Excel. Top 25 by PARTIAL+TODO
        # count -- the cases most worth reviewing.
        if self.assertions:
            case_cov: dict[tuple, dict[str, int]] = defaultdict(
                lambda: defaultdict(int))
            for prefix, case, _step, _t, _cfg, _emit, cov in self.assertions:
                case_cov[(prefix, case)][cov] += 1
            ranked = sorted(
                case_cov.items(),
                key=lambda kv: -(kv[1].get("PARTIAL", 0) + kv[1].get("TODO", 0)
                                 + kv[1].get("STUB", 0)))
            worth_reviewing = [
                (k, v) for k, v in ranked
                if (v.get("PARTIAL", 0) + v.get("TODO", 0) + v.get("STUB", 0)) > 0]
            lines.extend([
                "## Per-case assertion coverage (top 25 by gap count)",
                "",
                "> Cases with the most non-FULL assertions. Empty section = "
                "every case's assertions translated FULL.",
                "",
                "| Prefix | Case | FULL | PARTIAL | STUB | TODO | SKIPPED |",
                "|---|---|---:|---:|---:|---:|---:|",
            ])
            for (prefix, case), c in worth_reviewing[:25]:
                lines.append(
                    f"| {prefix} | {case} | {c.get('FULL', 0)} | "
                    f"{c.get('PARTIAL', 0)} | {c.get('STUB', 0)} | "
                    f"{c.get('TODO', 0)} | {c.get('SKIPPED', 0)} |")
            if not worth_reviewing:
                lines.append("| _(none)_ | -- | -- | -- | -- | -- | -- |")
            lines.append("")

        # ---- Runtime-skip inventory ---------------------------------
        if self.runtime_skips:
            skip_by_reason: dict[str, int] = defaultdict(int)
            for _p, _c, _m, reason, _src in self.runtime_skips:
                skip_by_reason[reason[:80]] += 1
            # One case can have multiple emit-sites for SkipException (each
            # Groovy step throws), but only the FIRST throw is reachable at
            # runtime (rest javac-suppressed). "Cases" = distinct (prefix,
            # case) pairs; "Skip-emit sites" = raw row count.
            distinct_cases = len({(p, c) for p, c, *_ in self.runtime_skips})
            lines.extend([
                "## Cases skipped at runtime",
                "",
                "> The emitter inserted a `throw new SkipException(...)` for "
                "these cases. They will ALWAYS skip regardless of environment "
                "-- silent capacity loss unless surfaced here. Full list in "
                "`runtime_skips.csv`.",
                "",
                f"- Cases with runtime skip: **{distinct_cases}** distinct case(s) "
                f"({len(self.runtime_skips)} emit-site(s) total, "
                f"only the first per method is reachable at runtime)",
                f"- Distinct skip reasons: **{len(skip_by_reason)}**",
                "",
                "### Top skip reasons",
                "",
                "| Skips | Reason (truncated) |",
                "|---:|---|",
            ])
            for reason, n in sorted(skip_by_reason.items(), key=lambda x: -x[1])[:10]:
                r = reason.replace("|", "\\|")
                lines.append(f"| {n} | {r} |")
            lines.append("")

        # ---- Per-step coverage roll-up ------------------------------
        if self.steps:
            step_type_cov: dict[str, dict[str, int]] = defaultdict(
                lambda: defaultdict(int))
            for _p, _c, _sn, step_type, _mk, _eq, cov, _gd in self.steps:
                step_type_cov[step_type][cov] += 1
            lines.extend([
                "## Per-step coverage by step type",
                "",
                "> Every SoapUI step (REST / Groovy / Properties / DataSource / "
                "PropertyTransfer / Manual / JDBC) accounted for. Full detail "
                "in `steps.csv`.",
                "",
                "| Step type | Total | FULL | PARTIAL | STUB | TODO | SKIPPED |",
                "|---|---:|---:|---:|---:|---:|---:|",
            ])
            for step_type in sorted(step_type_cov):
                c = step_type_cov[step_type]
                total = sum(c.values())
                lines.append(
                    f"| {step_type} | {total} | {c.get('FULL', 0)} | "
                    f"{c.get('PARTIAL', 0)} | {c.get('STUB', 0)} | "
                    f"{c.get('TODO', 0)} | {c.get('SKIPPED', 0)} |")
            lines.append("")

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
# `${#[suite#case#step]#property}` -- SoapUI cross-testcase property
# reference. Emitted by the SoapUI UI when an author drags a property
# from a DIFFERENT test case into a header/body cell. The framework
# treats every test method as scope-independent, but every case's
# `<step>.<property>` value is namespaced identically in ctx by our
# emitted Groovy translations (see e.g. `ctx.put("tokenId.
# GeneratedTokenID", ...)`), so the suite+case wrapper is dropped
# and only `<step>.<property>` matters at read time.
#
# Real example that surfaced this: `${#[regression#token_generation#
# tokenId]#GeneratedTokenID}` in the Authorization header of every
# REST step in the accountmemberregression suite -- untranslated, this
# went to the wire as a literal Bearer value and every call 401'd.
_CROSS_TC_RX = re.compile(
    r"\$\{#\[[^\]]+#(?P<step>[A-Za-z0-9_.-]+)\]#(?P<prop>[A-Za-z0-9_.-]+)\}")
# `${step#Response#<jsonPath>}` -- SoapUI's shorthand for "read a value
# out of another step's response body via JsonPath". The jsonPath can
# contain any char except `}` (e.g. `$['guestId']`, `$.foo.bar`, `$[0]`).
# Must be matched BEFORE _STEP_PROP_RX because that pattern's second
# group excludes `#` -- it would fail to match this shape but be
# checked first anyway; keeping the response one earlier removes any
# ordering surprise.
# Audit fix #1: broadened to also match SoapUI's ResponseAsXml / ResponseAsJson
# variants (96 occurrences in the accountmemberregression project). Group 2
# captures the variant (empty string for the default Response; "AsXml",
# "AsJson", "Headers", "AsHtml" for the alternates); group 3 captures the
# path expression. The emitter uses the variant to pick the right extractor
# (safeJsonExtract for Response/AsJson, XPath-leaf heuristic for AsXml,
# res.getHeader for Headers).
_STEP_RESPONSE_RX = re.compile(
    r"\$\{([A-Za-z0-9_ -]+?)#Response(AsXml|AsJson|Headers|AsHtml)?#([^}]+)\}")
_STEP_PROP_RX = re.compile(r"\$\{([A-Za-z0-9_ -]+?)#([A-Za-z0-9_.-]+)\}")
# Bare `${var}` -- only match identifiers that AREN'T already caught
# by one of the scoped patterns above. SoapUI uses this for TestCase-
# level property lookup by default. Skips `${=groovy}` (starts with =)
# and `${#...}` (scoped).
_BARE_PROP_RX = re.compile(r"\$\{(?!#|=)([A-Za-z_][A-Za-z0-9_.-]*)\}")
# `${=<groovy expression>}` -- inline Groovy evaluation. Rare; passes
# through as-is so the emitter can log a TODO for manual review.
_GROOVY_EXPR_RX = re.compile(r"\$\{=([^}]+)\}")


def _translate_soapui_jsonpath(path: str) -> str:
    """Convert a SoapUI JSON path (`$['x']`, `$.foo.bar`, `$[0].id`) to
    the RestAssured JsonPath string form (`x`, `foo.bar`, `[0].id`).
    Strips the leading `$`/`$.` and unwraps `['key']` -> `.key`."""
    p = (path or "").strip()
    if p.startswith("$."):
        p = p[2:]
    elif p.startswith("$"):
        p = p[1:]
    p = re.sub(r"\['([^']+)'\]", r".\1", p)
    p = re.sub(r'\["([^"]+)"\]', r".\1", p)
    return p.lstrip(".")


def soapui_expr_to_java(expr: str) -> str:
    """Translate a SoapUI property expression to a Java-code equivalent.

    Ctx reads go through `TestSupport.ctxGet(ctx, "<key>")` (not the
    raw `ctx.get`) so path params + query params + header values benefit
    from alias walking -- ie a value written under `PropertiesGuestId.
    guestId` by a Groovy extract is visible when the next step reads
    `Properties.guestID`. See TestSupport.ctxGet for the walk order.

    `${step#Response#$['field']}` refs translate to
    `<sanitized_step>Res.jsonPath().getString("field")`. That response
    variable is only in scope inside the SAME test method as the step
    that produced it -- fine for path params of a subsequent step; if
    the reference reaches outside that scope, javac fails visibly."""
    if expr is None:
        return "null"
    e = expr
    # Cross-testcase reference must run BEFORE the other scoped patterns
    # because its content contains `#` separators that would otherwise be
    # partially consumed. suite/case components are dropped -- ctx is
    # scope-flat per emitted test method.
    e = _CROSS_TC_RX.sub(
        lambda m: f'TestSupport.ctxGet(ctx, "{m.group("step")}.{m.group("prop")}")',
        e)
    e = _PROJ_PROP_RX.sub(lambda m: f'config.get("{m.group(1)}")', e)
    # Scoped non-Project props all resolve from mergedRow's config/ctx bag:
    # TestSuite/TestCase properties -> ctx (published by setup); Global/Env
    # -> config; MockService -> ctx (rare, treated as runtime).
    def _scoped(m):
        scope, prop = m.group(1), m.group(2)
        if scope in ("Global", "Env"):
            return f'config.get("{prop}")'
        return f'TestSupport.ctxGet(ctx, "{prop}")'
    e = _SCOPE_PROP_RX.sub(_scoped, e)
    # `${step#Response#$jsonPath}` -- resolves against the emitted response
    # variable (in scope only within the same test method).
    # Audit fix #1: also handle ResponseAsXml/AsJson/Headers/AsHtml. The
    # regex now captures the variant as group 2; extractor differs per
    # variant.
    def _step_response(m):
        step = sanitize_identifier(m.group(1))
        variant = m.group(2) or ""  # "" for the default Response
        path_raw = m.group(3)
        if variant == "Headers":
            # Response header lookup -- SoapUI ${step#ResponseHeaders#Name}
            # returns the header value; RestAssured Response exposes
            # .getHeader(name) which returns null when missing.
            header = path_raw.strip().replace('"', '\\"')
            return f'({step}Res.getHeader("{header}") == null ? "" : {step}Res.getHeader("{header}"))'
        if variant in ("AsXml", "AsHtml"):
            # SoapUI XPath -- extract the LAST identifier from the path
            # (typical shape: "declare namespace ns1='...'; //ns1:Response
            # [1]/ns1:accountId[1]"). Hilton APIs return JSON in stg even
            # when the SoapUI project was captured against an XML-shaped
            # earlier version, so extracting via the JSON field of the
            # same name (accountId, memberId, etc.) is the pragmatic
            # bridge. Falls back to safeJsonExtract with the leaf name.
            expr = path_raw
            if "declare namespace" in expr and ";" in expr:
                expr = expr.split(";", 1)[1].strip()
            segments = expr.split("/")
            leaf = segments[-1] if segments else expr
            if ":" in leaf:
                leaf = leaf.split(":", 1)[-1]
            leaf = re.sub(r"\[\d+\]", "", leaf).strip()
            if not leaf:
                leaf = "unknown"
            return f'com.ak.api.rest.utilities.RestUtilities.safeJsonExtract({step}Res, "{leaf}")'
        # Default: Response / AsJson -- existing safeJsonExtract with
        # translated JSONPath.
        path = _translate_soapui_jsonpath(path_raw)
        # Route through safeJsonExtract so an upstream 4xx that returned
        # an empty/HTML body degrades to "" here instead of crashing the
        # whole test with an unchecked JsonPathException.
        return f'com.ak.api.rest.utilities.RestUtilities.safeJsonExtract({step}Res, "{path}")'
    e = _STEP_RESPONSE_RX.sub(_step_response, e)
    e = _STEP_PROP_RX.sub(
        lambda m: f'TestSupport.ctxGet(ctx, "{m.group(1)}.{m.group(2)}")', e)
    # Bare `${var}` -- default namespace lookup, resolve from ctx (which
    # includes TestCase-level properties published by setup steps).
    e = _BARE_PROP_RX.sub(
        lambda m: f'TestSupport.ctxGet(ctx, "{m.group(1)}")', e)
    starts_with_known = any(e.startswith(p) for p in
                            ("config.get", "TestSupport.ctxGet"))
    # If we substituted a step-response ref, `e` starts with a Java
    # expression (e.g. `RestUtilities.safeJsonExtract(enroll_guestRes, ...)`
    # or legacy `enroll_guestRes.jsonPath()...`) -- treat as raw.
    if (re.match(r'^[A-Za-z_][A-Za-z0-9_]*Res\.jsonPath', e)
            or e.startswith('com.ak.api.rest.utilities.RestUtilities.safeJsonExtract')):
        return e
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
        # Step names with spaces are sanitized (space -> underscore)
        # to match the Java identifier form used everywhere else.
        step_id = re.sub(r"[^A-Za-z0-9_]", "_", m.group(1).strip())
        var = f"{step_id}_{m.group(2)}".replace(".", "_").replace("-", "_")
        placeholders.append(var)
        return f"#{var}#"
    def _step_response(m):
        # `${step#Response#$['field']}` -- SoapUI shorthand for reading
        # a value out of another step's response via JsonPath. Runtime
        # substitution reads from ctx under `<sanitized_step>_<field>`
        # -- populated by the REST step's auto-extract-to-ctx emit
        # (see _render_rest_step_body). Without translating, the raw
        # `${...}` reaches the wire and the target rejects the payload
        # (or JSON parse-fails on the leading `{` of an unquoted ref).
        #
        # Audit fix #1: regex now captures the variant as group 2 (empty
        # for the plain Response form; "AsXml"/"AsJson"/"Headers"/
        # "AsHtml" for the alternates). Group 3 is the path expression.
        # For AsXml/AsHtml, extract the LAST XPath leaf and use that
        # as the placeholder-name suffix (Hilton APIs return JSON with
        # matching field names).
        step_id = re.sub(r"[^A-Za-z0-9_]", "_", m.group(1).strip())
        variant = m.group(2) or ""
        raw_path = m.group(3).strip()
        if variant in ("AsXml", "AsHtml"):
            expr = raw_path
            if "declare namespace" in expr and ";" in expr:
                expr = expr.split(";", 1)[1].strip()
            segments = expr.split("/")
            leaf = segments[-1] if segments else expr
            if ":" in leaf:
                leaf = leaf.split(":", 1)[-1]
            leaf = re.sub(r"\[\d+\]", "", leaf).strip()
            if not leaf:
                leaf = "unknown"
            field = leaf.replace(".", "_").replace("-", "_")
        elif variant == "Headers":
            # Header refs land as a `_Header_<name>` suffix; runtime
            # substitution needs a separate ctx-put step but at least
            # the placeholder shape is stable.
            field = f"Header_{raw_path.strip().replace('-', '_').replace('.', '_')}"
        else:
            # Default Response / AsJson: strip JsonPath syntax to bare
            # field name matching _translate_soapui_jsonpath.
            field = raw_path.lstrip("$").lstrip(".")
            field = re.sub(r"\['?([^'\]]+)'?\]", r".\1", field)
            field = re.sub(r'\["?([^"\]]+)"?\]', r".\1", field)
            field = field.lstrip(".").replace(".", "_").replace("-", "_")
        var = f"{step_id}_Response_{field}" if field else f"{step_id}_Response"
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
    # STEP_RESPONSE must run BEFORE STEP_PROP because both start with
    # `${<step>#`; STEP_PROP would match up to the first `#` and eat the
    # `Response` as the property name, giving the wrong translation.
    translated = _STEP_RESPONSE_RX.sub(_step_response, translated)
    translated = _STEP_PROP_RX.sub(_step, translated)
    translated = _GROOVY_EXPR_RX.sub(_groovy, translated)
    # Bare ${var} runs LAST so scoped patterns get first pass.
    translated = _BARE_PROP_RX.sub(_bare, translated)
    return translated, placeholders


_SET_PROP_INSIDE_GROOVY_RX = re.compile(
    r'setPropertyValue\(\s*[\'"]([A-Za-z0-9_.-]+)[\'"]', re.IGNORECASE)


# Author-controlled random fields. `TestSupport.regenRandomProperties`
# refreshes ONLY these (plus their case-flipped / suite-specific
# variants) before each REST step whose body references any of them.
# Id-shaped fields deliberately EXCLUDED -- they're extracted from
# upstream responses and clobbering them would cascade 404s downstream.
_REGEN_TRIGGER_KEYS = frozenset({
    "username", "usernamemember",
    "email", "emailaddress", "emailmember", "generatedemail",
    "generatedemailaddress", "guestmemberemail",
    "phone", "phonenumber",
    "domain", "websitedomain", "weburl",
    "hhonorsnumber",
})

# Match any SoapUI-style ref: ${...}. We then scan the inner tokens
# for anything in _REGEN_TRIGGER_KEYS (case-insensitive). Catches
# ${Properties#Username}, ${#TestCase#Properties#username},
# ${Properties.email}, and bare ${Username}.
_SOAPUI_ANY_REF_RX = re.compile(r"\$\{[^}]+\}")


def _step_needs_regen(step: "RestStep") -> bool:
    """True if this REST step's body / query / headers reference any
    author-controlled random property that should be refreshed before
    the request goes out. See `_REGEN_TRIGGER_KEYS`.

    Detection deliberately runs on the RAW SoapUI text (${...} form)
    so we don't have to replicate the placeholder-translation stage
    for query params and headers. Falsy return means the emitter
    skips the regen call -- keeps steps that don't submit fresh
    identity data from paying the (tiny) FakeData cost.
    """
    parts: list[str] = [step.request_body or ""]
    for qv in (getattr(step, "query_params", None) or {}).values():
        parts.append(qv or "")
    for hv in (getattr(step, "headers", None) or {}).values():
        parts.append(hv or "")
    blob = "\n".join(parts)
    if not blob:
        return False
    for m in _SOAPUI_ANY_REF_RX.finditer(blob):
        inner = m.group(0)
        for tok in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", inner):
            if tok.lower() in _REGEN_TRIGGER_KEYS:
                return True
    return False


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
                    if not val:
                        continue
                    ctx_key = f'{step.step_name}.{prop}'
                    # Filter out credentials: neither the key name nor the
                    # value should indicate a secret. Stale bearer tokens
                    # baked into a defaults JSON file are worse than useless
                    # (they mask a broken auth flow with a 401 that looks
                    # like a real API error). Runtime auth is responsible
                    # for these; the frozen literal has no valid role.
                    if _is_secret_path(ctx_key) or _looks_like_bearer_token(val):
                        continue
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


def _find_canonical_token_pair(cases: list) -> tuple:
    """Scan ``cases`` for the first (tokenRequest REST step, Token Groovy
    extractor) pair and return it. Groovy extractor is optional -- if the
    tokenRequest step isn't immediately followed by a Groovy step that
    parses ``access_token``, only the REST step is returned as
    ``(rest_step, None)``.

    Returns ``(None, None)`` when no case in the suite has a token step.
    Used to synthesize a token-fetch preamble for cases that lack their
    own token step (relied on cross-case #Project#Token state in SoapUI).
    """
    for case in cases:
        steps = getattr(case, "steps", None) or []
        for i, s in enumerate(steps):
            if isinstance(s, RestStep) and _is_token_fetch_step(s):
                extractor = None
                if i + 1 < len(steps):
                    nxt = steps[i + 1]
                    if isinstance(nxt, GroovyStep):
                        name_l = (getattr(nxt, "step_name", "") or "").lower()
                        script_l = (getattr(nxt, "script", "") or "").lower()
                        if ("token" in name_l or "access_token" in script_l):
                            extractor = nxt
                return (s, extractor)
    return (None, None)


def _hoist_token_fetch_steps(steps: list) -> list:
    """Reorder ``steps`` so any OAuth-token-fetch REST step (and its
    immediately-following ``Token``-flavor Groovy extractor) runs FIRST.

    Motivation: SoapUI test authors sometimes place the ``tokenRequest``
    step near the END of a case (e.g. position 28 of 30), relying on
    project-level ``#Project#Token`` state persisted from a previous
    test case's run to authenticate the EARLIER REST steps of the
    current case. Framework has no cross-case project state; without a
    hoist, every earlier REST step 401s because ``ctx.accessToken`` is
    empty. Reordering preserves author intent (the token step still
    runs, its response still updates ctx) but populates the token
    BEFORE auth-dependent steps fire.

    Only the FIRST token-fetch step is hoisted (multi-token cases are
    rare; hoisting all would reorder them among each other). The
    Groovy successor is hoisted only when it looks like an extractor
    (name matches ``Token`` case-insensitively, or its script parses
    the response's ``access_token`` field). Everything else keeps
    original SoapUI order.

    Returns a NEW list. Original ``steps`` is not mutated.
    """
    idx_token = -1
    for i, s in enumerate(steps):
        if isinstance(s, RestStep) and _is_token_fetch_step(s):
            idx_token = i
            break
    # No token step, or already early enough (first 2 REST steps) -- no-op.
    if idx_token < 0:
        return steps
    rest_step_positions = [i for i, s in enumerate(steps) if isinstance(s, RestStep)]
    if idx_token in rest_step_positions[:2]:
        return steps
    hoisted = [steps[idx_token]]
    # Grab the immediately-following Groovy step iff it looks like a Token extractor.
    if idx_token + 1 < len(steps):
        nxt = steps[idx_token + 1]
        if isinstance(nxt, GroovyStep):
            name_l = (getattr(nxt, "step_name", "") or "").lower()
            script_l = (getattr(nxt, "script", "") or "").lower()
            if ("token" in name_l or "access_token" in script_l):
                hoisted.append(nxt)
    hoisted_names = {id(x) for x in hoisted}
    rest = [s for s in steps if id(s) not in hoisted_names]
    return hoisted + rest


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
    "credential", "tokenid", "generatedtokenid", "bearer", "jwt",
)


def _looks_like_bearer_token(value: str) -> bool:
    """True when a literal value looks like an OAuth Bearer token that
    should NEVER be shipped as a default. Catches `Bearer <jwt>`, bare
    JWT (3 dot-separated base64 segments), and long hex/GUID-shaped
    tokens where 'token' keyword tipped us off."""
    if not value:
        return False
    v = value.strip()
    if v.lower().startswith("bearer "):
        return True
    # JWT: three base64url segments separated by dots
    if re.match(r'^[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}$', v):
        return True
    return False


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


def _has_soapui_placeholder(s: str) -> bool:
    """True when the string contains an unresolved SoapUI property
    placeholder that would confuse a downstream evaluator (JsonPath,
    Groovy, regex, etc.). Matches:
      - `${...}` -- bare or scoped property
      - `{X#Y}`  -- SoapUI shorthand (no leading `$`) that Groovy sees
                    as an unclosed block start.
    """
    if not s:
        return False
    if "${" in s:
        return True
    # Bare {X#Y} form -- avoid false positive on `{key: value}` JSON.
    return bool(re.search(r"\{[A-Za-z_][A-Za-z0-9_.]*#[A-Za-z_][A-Za-z0-9_.-]*\}", s))


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
        # Bug #4 fix: for multi-code assertions (e.g. `200, 201, 206, 204`),
        # emit an EMPTY CSV default so the runtime's Set.contains fallback
        # fires at assertion time. If we defaulted to the first code, the
        # emitted `if (rawStatus != null && !rawStatus.isEmpty())` branch
        # would take the strict assertEquals(actual, first_code) path,
        # bypassing the multi-code accept-any semantics entirely.
        code_list = [c for c in re.split(r"[,\s]+", codes)
                     if c and c.strip().lstrip("-").isdigit()]
        if len(code_list) > 1:
            return ""  # let runtime Set.contains handle it
        return code_list[0] if code_list else "200"
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


def _csv_cell(value: str, col_name: str = "") -> str:
    """Quote a CSV cell when it contains a comma, quote, or newline.
    Doubles existing quotes per RFC 4180. Bare values pass through.

    Two runtime-safety transforms applied before quoting:

    * ``${...}`` SoapUI refs translated to framework ``#placeholder#``
      via ``soapui_body_to_placeholders`` (so ``mapJsonValues`` can
      resolve them at runtime).
    * Bare 6+ digit numeric strings in ID-named columns
      (``guestId``, ``accountID``, ``memberId``, ``hhonorsNumber``,
      etc.) rewritten to ``@Properties_<field>@`` so the runtime
      substitution reads a fresh id from ctx (populated by
      random_email_generator or a Groovy extract) instead of the
      SoapUI author's stale hardcoded value that now points at a
      different account on the target env. Column name lookup uses
      the CSV header (``col_name`` param) rather than value shape
      alone to avoid rewriting legitimate small numerics like
      ``postalCode: 40515``.
    * Literal ``null`` string emptied so mapJsonValues doesn't splice
      the word "null" into JSON where an id is expected.
    """
    s = "" if value is None else str(value)
    if "${" in s:
        s, _ph = soapui_body_to_placeholders(s)
    if s.strip().lower() == "null":
        s = ""
    # Id-shape rewrite: SoapUI author baked stale Hilton ids into CSV
    # cells (e.g. PropertiesDetails.accountID = 2000016128). Runtime
    # would use these literals, target 404s. Swap for @Properties_X@
    # placeholder so ctxGet returns a live value.
    if col_name and s and s.strip().isdigit() and len(s.strip()) >= 6:
        col_l = col_name.lower()
        ID_HINTS = ("guestid", "accountid", "memberid", "hhonorsnumber",
                    "hhonors_number", "partneraccountid", "customerid",
                    "userid", "hilton_member_id", "hiltonmemberid")
        if any(h in col_l for h in ID_HINTS):
            # Strip the trailing prefix segment (`PropertiesDetails.` etc.)
            # so the placeholder maps to the bare field name that
            # random_email_generator + ctxGet's alias-walk understand.
            field = col_name.rsplit(".", 1)[-1] if "." in col_name else col_name
            s = f'@Properties_{field}@'
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
        # And whether that client method takes a `Map<String,String>
        # queryParams` arg. When ANY step referencing this op declares
        # <con:parameters> entries that don't map to path/header slots,
        # every emitted call site for the shared client method must
        # pass a map (possibly empty) so the signature is uniform.
        self.client_takes_query: dict[tuple[str, str], bool] = {}
        # Bug #2 fix: extra HTTP headers beyond the standard set
        # (Authorization / Content-Type / Accept / Correlation-Id).
        # When ANY step under this op declares custom headers
        # (Hilton-Operator-DutyCode, X-JWT-Assertion, etc.), the client
        # method must accept a `Map<String,String> extraHeaders` arg
        # so per-step values reach the wire. Same shared-signature
        # invariant as client_takes_query: every call site passes a
        # map (possibly empty) once the op is flagged.
        self.client_takes_extra_headers: dict[tuple[str, str], bool] = {}
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
        # Cluster-union of assertions was deleted -- per-case emit is
        # correct (a sibling case's assertion shouldn't leak into
        # another case's step whose response doesn't contain the
        # asserted field). Fields kept as `None` so any accidental
        # future reader hits a NoneType error rather than silently
        # reintroducing the aggregation. When it's safe to purge, drop
        # these + the `_union_cluster_asserts` method definition.
        self._cluster_asserts_by_pos = None
        self._current_rest_step_pos = 0

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

    # Bundled framework Java files that are pure-framework-generic
    # (contain NO per-suite content). When one of these is being
    # emitted and already exists on disk, `_write` SKIPs to preserve
    # any hand-edits (audit fixes, per-project tweaks). User forces a
    # re-emit by deleting the file. Suite-specific bundled files
    # (TestSupport.java, SetupHelper.java, Templates.java) are NOT in
    # this set -- they MUST regenerate per suite because their bundled
    # content varies with the SoapUI input XML.
    _AUTHOR_EDITABLE_BASENAMES = frozenset({
        "AuthHelper.java",
        "PerMethodCsvDataProvider.java",
        "PlaceholderResolver.java",
        "ProgressLogListener.java",
    })

    def _write(self, rel_path: str, content: str) -> str:
        abs_path = os.path.join(self.output_dir, rel_path)
        # SKIP-IF-EXISTS for author-editable framework files. Centralized
        # here so every emit site benefits without needing per-site edits
        # to the (many) bundled-string emitters. Prints a visible line
        # so the intent is clear in the conversion tail.
        if os.path.basename(rel_path) in self._AUTHOR_EDITABLE_BASENAMES:
            if os.path.exists(abs_path):
                print(f"[ra_converter] SKIP (author-editable, exists): {rel_path}"
                      f" -- delete file to re-emit the bundled version.")
                self.written.append(rel_path)
                return abs_path
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
            # Union of query-param keys across every step sharing this op.
            # ANY step with query params flips the shared client method to
            # accept a map; steps with no params pass an empty map. Prior
            # emit ignored <con:parameters> entirely, silently shipping
            # `.get(baseUrl + "/search")` for `GET /search?filter=X`.
            self.client_takes_query[(op_name, path)] = any(
                bool(getattr(s, "query_params", None))
                for (_, s) in occurrences)
            # Bug #2 fix: flag the client method to accept extraHeaders
            # when ANY step under this op declares non-standard headers.
            # "Authorization" is intentionally NOT counted -- the base
            # client method already handles it via the `token` param.
            def _has_extra_headers(s):
                hs = getattr(s, "headers", None) or {}
                for k in hs:
                    if k.lower() == "authorization":
                        continue
                    return True
                return False
            self.client_takes_extra_headers[(op_name, path)] = any(
                _has_extra_headers(s) for (_, s) in occurrences)
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
        # Build method params: (String token, [path_params...],
        # [Map<String,String> queryParams if op has any], [String body
        # if POST/PUT/PATCH])
        params = ["String token"]
        if java_path_params:
            params.append(java_path_params)
        # Query params: consult the shared-client shape flag rather than
        # this one step's declaration, so a client method shared across
        # cases keeps a stable signature.
        client_key = (op_name, path)
        takes_query = self.client_takes_query.get(client_key, bool(step.query_params))
        if takes_query:
            params.append("Map<String, String> queryParams")
        # Bug #2: extraHeaders slot for custom HTTP headers beyond the
        # standard Authorization / Content-Type / Accept / Correlation-Id.
        # Same shared-signature invariant as queryParams.
        takes_extra_headers = self.client_takes_extra_headers.get(
            client_key, False)
        if takes_extra_headers:
            params.append("Map<String, String> extraHeaders")
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
        # Normalize the Authorization value: SetupHelper stores it with a
        # "Bearer " prefix while AuthHelper stores the raw JWT. If both
        # ever coexist for the same test method (or the caller changes
        # which one populates ctx), we'd send `Authorization: <rawJwt>`
        # and the server 401s cryptically. Prefix only when missing.
        auth_expr = ('(token == null || token.isEmpty() || token.startsWith("Bearer ") '
                     '? token : "Bearer " + token)')
        if raw_mt == "application/json":
            headers_block = f"""Map<String, String> headers = Headers.builder()
                .contentTypeJson()
                .acceptJson()
                .header("Authorization", {auth_expr})
                .correlationId()
                .build();"""
        else:
            headers_block = f"""Map<String, String> headers = new java.util.HashMap<>();
        headers.put("Content-Type", "{raw_mt}");
        headers.put("Accept", "{raw_mt}");
        headers.put("Authorization", {auth_expr});"""
        # Bug #2: after building the base headers, merge per-step
        # extraHeaders on top so a call-site override (e.g. custom
        # Hilton-Operator-DutyCode) reaches the wire. Base headers are
        # mutable in both branches; JSON branch's Headers.builder().build()
        # returns a HashMap (see Headers.java), so putAll is safe.
        if takes_extra_headers:
            headers_block += (
                "\n        if (extraHeaders != null && !extraHeaders.isEmpty()) "
                "{ headers.putAll(extraHeaders); }")

        # Call site per verb -- ContentType is set explicitly on every
        # body-bearing verb so it matches the headers map above.
        body_chain = ".body(requestBody)" if needs_body else ""
        content_chain = (f".contentType({content_type_expr})"
                         if needs_body else "")
        # `.queryParams(map)` is null-safe: RestAssured's Map<String,?>
        # overload iterates entries and skips a null map cleanly. Only
        # emit the chain segment when the shared client method declares
        # the arg, to avoid an unbound `queryParams` compile error in
        # signatures that don't take one.
        query_chain = ".queryParams(queryParams)" if takes_query else ""
        verb_call = verb.lower()
        if verb == "GET":
            call = ('Response res = RestAssured.given()\n'
                    '                .headers(headers)\n'
                    f'                {query_chain}\n'
                    '                .get(baseUrl + path);')
        elif verb == "DELETE":
            call = ('Response res = RestAssured.given()\n'
                    '                .headers(headers)\n'
                    f'                {query_chain}\n'
                    '                .delete(baseUrl + path);')
        elif verb in ("POST", "PUT", "PATCH"):
            # Use the direct RestAssured chain uniformly (no more
            # RestUtilities.getResponsePost which was JSON-hardcoded).
            call = ('Response res = RestAssured.given()\n'
                    '                .headers(headers)\n'
                    f'                {content_chain}\n'
                    f'                {query_chain}\n'
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
        # DelayStep -> next-REST-step retry-on-transient hand-off:
        # when a DelayStep sets this, the very next RestStep's client
        # call gets wrapped in a Supplier + isTransientResponse retry
        # loop with this ms as the deadline budget. Cleared after
        # consumption so it applies to exactly one step.
        self._pending_retry_deadline_ms = 0

    def _wrap_rest_call_for_retry(self, lines, deadline_ms, step_name, expected_status=-1):
        """Post-process REST step lines to wrap the `Response Xres = client.callX(...);`
        line in a call to `RestUtilities.callWithTransientRetry`.

        Emits the 4-arg form of the helper so a negative test that
        EXPECTS a specific transient-looking code (e.g. Reject's
        post_account_403 expects HTTP 400 with body "Member status is
        invalid" -- both fields of the transient signature) does NOT
        retry the authoritative expected answer. Prior 3-arg form
        wasted 5+ seconds per negative test retrying the correct
        answer 7 times.

        Emitted (before -> after):
            Response Xres = client.callX(...);
        becomes:
            Response Xres = RestUtilities.callWithTransientRetry(
                    "X", <deadlineMs>L, <expectedStatus>, () -> client.callX(...));

        Pass expected_status = -1 to disable the gate (retry always
        fires on transient regardless of what we got).

        Idempotent on lines that do not contain a client call (rare --
        stub / auth-error emit paths): returns lines unchanged.
        """
        call_re = re.compile(r"^(\s*)(Response \w+Res)\s*=\s*(client\.[^;]+);\s*$")
        safe_step = re.sub(r"[^A-Za-z0-9_]", "_", step_name) or "step"
        out = []
        wrapped = False
        for line in lines:
            m = call_re.match(line)
            if not m or wrapped:
                out.append(line)
                continue
            indent, var_decl, call_expr = m.groups()
            out.append(
                f"{indent}// [retry-on-transient] universal wrapper -- retries "
                f"ONLY on 5xx / 429 / 400+\"invalid\" body AND ONLY when "
                f"response != expected ({expected_status}). Deadline "
                f"{deadline_ms}ms; tune with -Dtest.transientRetryDeadlineMs=<ms>.")
            out.append(
                f"{indent}{var_decl} = com.ak.api.rest.utilities.RestUtilities."
                f"callWithTransientRetry(\"{safe_step}\", {deadline_ms}L, "
                f"{expected_status}, () -> {call_expr});")
            wrapped = True
        return out

    def _render_step(self, step, service_class_name: str) -> list[str]:
        """Render one step's Java lines. Shared by test-method emission
        AND SetupHelper emission so bug fixes benefit both."""
        lines: list[str] = []
        if isinstance(step, RestStep):
            rest_lines = self._render_rest_step_body(step, service_class_name)
            # Universal retry-on-transient with expected-status gate.
            #
            # Retry fires only when BOTH:
            #   (a) response is transient (5xx / 429 / 400 with "invalid"
            #       body per RestUtilities.isTransientResponse), AND
            #   (b) response status != the expected status this step
            #       was configured to receive.
            # The (b) gate is critical: SoapUI negative tests
            # (post_account_403, post_account_notexist_403, etc.) EXPECT
            # 4xx codes that also happen to match the transient signature
            # (e.g., 400 with "Member status is invalid" body). Without
            # this gate we retried the authoritative expected answer 7
            # times over 5.5 seconds, wasting wall-clock every negative
            # test.
            #
            # Deadline bumped from 5000ms -> 15000ms because the observed
            # Hilton stg state-commit window in the reference project is
            # 15-30s. Tune runtime with -Dtest.transientRetryDeadlineMs.
            # Extract expected status from step's Valid HTTP Status
            # Codes assertion (first code, if any). -1 disables the
            # gate (retry on transient regardless of what we got).
            # Bug #3 fix: when the assertion accepts multiple codes
            # (e.g. `<codes>200, 201, 206, 204</codes>`), we CANNOT
            # pass any single code as the "expected" -- a legitimate
            # 201 would then be treated as unexpected and the retry
            # would burn the full 15s deadline retrying an already-
            # authoritative response. Use -1 (disable gate) so retry
            # fires only on the transient signature (5xx / 429 / 400+
            # "invalid" body), which is safe for multi-code steps.
            expected_status = -1
            for _a in (step.assertions or []):
                if getattr(_a, "type", "") == "Valid HTTP Status Codes":
                    _codes = (_a.config.get("codes", "") or "").strip() if getattr(_a, "config", None) else ""
                    if _codes:
                        _code_list = [c for c in re.split(r"[,\s]+", _codes)
                                      if c and c.strip().lstrip("-").isdigit()]
                        if len(_code_list) == 1:
                            try:
                                expected_status = int(_code_list[0])
                            except (TypeError, ValueError):
                                pass
                        # else: multi-code -> leave expected_status=-1
                        # so retry gate falls back to transient-only.
                        break
            rest_lines = self._wrap_rest_call_for_retry(
                rest_lines, 15000, step.step_name, expected_status)
            lines.extend(rest_lines)
        elif isinstance(step, GroovyStep):
            # Console marker so a groovy-side hang or long-running side
            # effect is attributable in the log stream.
            lines.append(
                f'LOG.info(" .. groovy step: {_jlit(step.step_name)}");')
            # Allure step marker so translated Groovy work (token extract,
            # data-gen, DB operations) shows up as its own node in the
            # Allure report tree instead of collapsing into the enclosing
            # REST step's attachments.
            lines.append(
                f'io.qameta.allure.Allure.step('
                f'"groovy: {_jlit(step.step_name)}");')
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
                # putIfNonEmpty -- do NOT plant an empty value into ctx
                # even if testData walks the whole precedence chain and
                # finds nothing. If we planted empties, ctxGet's
                # "primary key present" short-circuit would mistake a
                # missing-default for a "Groovy extract wrote empty"
                # signal and refuse to alias-walk -- the exact bug the
                # ctxGet fix was meant to solve. Skipping the put here
                # lets a downstream ctxGet fall through to a sibling
                # key populated by a Groovy extract with the actual value.
                lines.append(
                    f'TestSupport.putIfNonEmpty(ctx, "{ctx_key}", '
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
            raw_q = (step.query or "").replace("\r", " ").replace("\n", " ")
            q_escaped = _jlit(raw_q)
            # Detect hardcoded numeric literals in WHERE clauses -- these
            # were frozen from the SoapUI author's dev-time data (e.g.
            # `where account_id=2000008886`) and won't match anything on
            # a fresh env. Convert each `<column> = <literal>` to a
            # placeholder `<column> = '#<column>#'` so PlaceholderResolver
            # can substitute at runtime from ctx. The emitted comment
            # points to the column names authors should populate via
            # a preceding REST-step extract or CSV row cell.
            hard_lits = re.findall(
                r"\b([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:'([^']+)'|(\d[\d.]*))",
                raw_q)
            # Only parameterize STALE-ID-SHAPED literals -- 6+ digit
            # numbers on id-shaped column names. Enum values
            # (status='active', web_site='foo.com') MUST stay as-is:
            # the SoapUI author intended those, and no upstream step
            # populates them in ctx, so parameterizing creates `null`
            # fallbacks that Db.execute then refuses.
            ID_COL_HINTS = ("id", "guest", "account", "member", "hhonors",
                            "hilton", "partner", "customer", "user")
            transformed_q = raw_q
            substituted_cols: list[str] = []
            for col, val, num in hard_lits:
                col_l = col.lower()
                if col_l in ("null", "true", "false"):
                    continue
                if col in substituted_cols:
                    continue
                literal = num or val
                looks_like_id = len(literal) >= 6 and literal.isdigit()
                col_hints_id = any(h in col_l for h in ID_COL_HINTS)
                if not (looks_like_id and col_hints_id):
                    continue  # keep literal, do not parameterize
                pattern = re.compile(
                    rf"\b{re.escape(col)}\s*=\s*(?:'[^']+'|\d[\d.]*)")
                transformed_q = pattern.sub(
                    f"{col}='#{col}#'", transformed_q, count=1)
                substituted_cols.append(col)
            # Translate SoapUI refs `${...}` inside the SQL to
            # framework placeholders so mapJsonValues resolves them at
            # runtime (otherwise Db.unsafeSqlReason refuses the SQL for
            # containing untranslated `${...}`).
            transformed_q = re.sub(
                r'\$\{#(?:TestCase|TestSuite|Global|Env|MockService)#'
                r'([A-Za-z0-9_.-]+)\}',
                lambda m: '#' + m.group(1).replace('.', '_') + '#',
                transformed_q)
            transformed_q = re.sub(
                r'\$\{#Project#([A-Za-z0-9_.-]+)\}',
                lambda m: '#' + m.group(1).replace('.', '_') + '#',
                transformed_q)
            transformed_q = re.sub(
                r'\$\{([A-Za-z_][A-Za-z0-9_]*)#([A-Za-z0-9_.-]+)\}',
                lambda m: '#' + m.group(1) + '_' + m.group(2).replace('.', '_') + '#',
                transformed_q)
            transformed_q = re.sub(
                r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}',
                lambda m: '#' + m.group(1) + '#',
                transformed_q)
            trans_escaped = _jlit(transformed_q)
            # SELECT-vs-mutation routing: SoapUI dedicated JDBC steps
            # commonly issue read queries too ("check account exists"),
            # and Db.execute is INSERT/UPDATE/DELETE only (uses
            # .executeUpdate() which the driver rejects for SELECT with
            # "A result was returned when none was expected"). Match the
            # sql.execute("SELECT ...") -> Db.queryAll routing added to
            # the Groovy translator so both emit paths behave the same.
            _is_select_step = bool(re.match(
                r"\s*(?:with\b.*?\bselect|select)\b",
                transformed_q, re.IGNORECASE | re.DOTALL))
            lines.append(f'// [jdbc step] {step.step_name}')
            if substituted_cols:
                lines.append(
                    f'// [jdbc] parameterized {len(substituted_cols)} '
                    f'hardcoded WHERE literal(s) with #placeholder# refs -- '
                    f'columns: {", ".join(substituted_cols)}. Original SQL: '
                    f'{raw_q[:120].replace(chr(10), " ")}')
                lines.append(
                    f'// [jdbc] Populate ctx (via prior REST extract or '
                    f'CSV row cell) for these keys, or the runtime-resolved '
                    f'query will still carry `#{substituted_cols[0]}#` '
                    f'unresolved -- watch the WARN from mapJsonValues.')
            # d.HINT: ordering hint for placeholders that ReadyAPI test
            # authors commonly populate LATER in the flow (e.g. HHonorsEnroll
            # response yields guestId; a DELETE that references guest_id
            # BEFORE HHonorsEnroll will trip the null-fallback safety net).
            # Heuristic list -- not a cross-step scan -- so no false positives
            # on early-set placeholders, but only catches names we know are
            # typically late-set. Keeps the safety net advisory in the emitted
            # Java so authors can see it at code-review time, not just at
            # runtime via the mapJsonValues WARN.
            _late_set_hints = {
                "guestID":         "usually populated by an /enroll or /guests POST response",
                "guestId":         "usually populated by an /enroll or /guests POST response",
                "accountID":       "usually populated by a /businesses or /accounts POST response",
                "accountId":       "usually populated by a /businesses or /accounts POST response",
                "memberID":        "usually populated by a /members POST response",
                "memberId":        "usually populated by a /members POST response",
                "hilton_member_id":"usually populated by a member-related POST response",
                "hiltonmemberid":  "usually populated by a member-related POST response",
                "hilton_account_id":"usually populated by an /accounts POST response",
                "hiltonaccountid": "usually populated by an /accounts POST response",
            }
            _all_ph_here = set(re.findall(r"#([A-Za-z0-9_]+)#", transformed_q))
            _late_ph_hits = [p for p in _all_ph_here if p in _late_set_hints]
            if _late_ph_hits:
                for _p in _late_ph_hits:
                    lines.append(
                        f'// [ordering hint] `#{_p}#` -- {_late_set_hints[_p]}. '
                        f'If this JDBC step runs BEFORE that POST in the flow, '
                        f'mapSqlValues will fallback to `null` and the SQL '
                        f'will be SKIPPED at runtime by the safety net. '
                        f'Check step ordering in your source SoapUI project '
                        f'if 0-row results are unexpected.')
            sid = sanitize_identifier(step.step_name)
            if _is_select_step:
                lines.append(
                    f'java.util.List<java.util.Map<String, Object>> '
                    f'__jdbcRows_{sid} = null;')
            lines.append('if (Db.isConfigured()) {')
            # RestUtilities.mapJsonValues expands #X# placeholders against
            # a merged (row + ctx + config) view so a query like
            # `where account_id='#accountID#'` resolves to the value
            # captured earlier in the test. Non-strict: unresolved keys
            # fall back to "null" and WARN so operators see the gap.
            lines.append(f'    try {{')
            lines.append(
                f'        String __jdbcSql_{sid} = '
                f'RestUtilities.mapSqlValues('
                f'"{trans_escaped}", TestSupport.mergedRow(row, ctx), ctx);')
            # Sanity-check FIRST so an unsafe SQL emits ONE clean WARN
            # line with the reason, instead of LOG.info(SQL) followed by
            # Db.execute\'s own refuse-WARN (two lines that read as if we
            # tried the query then something failed).
            # SELECT steps route to Db.queryAll -- use the ForQuery
            # variant of the sanity check so the SELECT-vs-execute
            # reason (only relevant for Db.execute misroutes) doesn't
            # spuriously refuse a correctly-routed queryAll.
            _sanity_fn = ("unsafeSqlReasonForQuery" if _is_select_step
                          else "unsafeSqlReason")
            lines.append(
                f'        String __jdbcReason_{sid} = com.ak.api.db.Db.{_sanity_fn}(__jdbcSql_{sid});')
            lines.append(
                f'        if (__jdbcReason_{sid} != null) {{')
            lines.append(
                f'            LOG.warn(" .. jdbc SKIPPED ({{}}): {{}}", '
                f'__jdbcReason_{sid}, __jdbcSql_{sid});')
            lines.append(
                f'        }} else {{')
            lines.append(
                f'            LOG.info(" .. jdbc SQL: {{}}", __jdbcSql_{sid});')
            if _is_select_step:
                lines.append(
                    f'            __jdbcRows_{sid} = Db.queryAll(__jdbcSql_{sid});')
                lines.append(
                    f'            LOG.info(" .. jdbc rows returned: {{}}", '
                    f'__jdbcRows_{sid} == null ? 0 : __jdbcRows_{sid}.size());')
            else:
                lines.append(
                    f'            Db.execute(__jdbcSql_{sid});')
            lines.append(
                f'        }}')
            lines.append(f'    }} catch (Exception __jdbcEx) {{')
            lines.append(
                f'        LOG.warn("JDBC step `{_jlit(step.step_name)}` failed: {{}}", '
                f'__jdbcEx.getMessage());')
            lines.append(f'    }}')
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
            # Emit Thread.sleep of the configured delay_ms -- matches
            # ReadyAPI's behavior exactly. The upstream "wire the following
            # REST step's client call in retry-on-transient" approach was
            # reverted: SoapUI authors don't always place the delay
            # IMMEDIATELY before the actually-race-prone step (the
            # accountmemberregression project puts the 5s wait before
            # Partition_before_http_request_200 for Kafka offset reasons,
            # but the step that races is http_request_pending_200 which
            # comes 2 steps later). Wrapping only the immediate-next REST
            # step therefore doesn't protect the actually-failing call.
            # A future improvement would be a UNIVERSAL retry-on-transient
            # wrapper on every REST call (safe because isTransientResponse
            # is narrow: 5xx, 429, or 400 with "invalid" body). For now,
            # the plain Thread.sleep matches ReadyAPI + covers the race
            # via wall-clock time.
            lines.append(f'// [delay step] {step.step_name} -- sleep {step.delay_ms}ms')
            # Audit fix #9: bracket ANY sleep >= 5s with LOG.info before
            # + after so a 61s / 30s / etc. delay reads as intentional
            # progress in the log instead of an apparent hang. Short
            # sleeps (< 5s) skip the log noise -- typical test-flow
            # waits at 2-3s don't need progress markers.
            if step.delay_ms >= 5000:
                safe_name = _jlit(step.step_name)
                lines.append(
                    f'LOG.info(" .. [delay step] {safe_name} -- sleeping {step.delay_ms}ms (NOT a hang, matches ReadyAPI)");')
            lines.append(f'try {{ Thread.sleep({step.delay_ms}L); }} '
                         f'catch (InterruptedException __ie) {{ '
                         f'Thread.currentThread().interrupt(); }}')
            if step.delay_ms >= 5000:
                safe_name = _jlit(step.step_name)
                lines.append(
                    f'LOG.info(" .. [delay step] {safe_name} -- awake");')
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
            # Body + endpoint MUST go through the same substitution
            # pipeline as REST steps: mapJsonValues (row + ctx + config
            # merged view) for #X#/@X@/%X%, then PlaceholderResolver.
            # resolveAll for <<X>> faker + leftover ${X}. Prior emit
            # shipped `body_lit` verbatim, so a SOAP body like
            #   <soap:Envelope>...<accountId>#Properties_accountID#</accountId>...
            # sent the literal `#Properties_accountID#` to the server.
            body_raw, _ph = soapui_body_to_placeholders(
                (step.request_body or "").strip())
            body_lit = _jlit(body_raw)
            ep_raw = step.endpoint or ""
            ep_lit = _jlit(ep_raw)
            lines.append(f'// [soaprequest] {step.step_name} '
                         f'(operation={_jlit(step.operation)})')
            resp_var = f"{sanitize_identifier(step.step_name)}Res"
            self._locals_in_method.add(resp_var)
            self.response_var_by_step[step.step_name] = resp_var
            payload_var = f'{sanitize_identifier(step.step_name)}Payload'
            self._locals_in_method.add(payload_var)
            url_var = f'{sanitize_identifier(step.step_name)}Url'
            self._locals_in_method.add(url_var)
            # SOAP body is XML, NOT JSON -- values containing `"` or
            # `\` must NOT be JSON-escaped (would land as `\"`/`\\` in
            # the XML envelope and blow up the SOAP parser). Pass
            # jsonEscape=false to mapJsonValues.
            lines.append(
                f'String {payload_var} = PlaceholderResolver.resolveAll('
                f'RestUtilities.mapJsonValues("{body_lit}", '
                f'TestSupport.mergedRow(row, ctx), '
                f'/* strict */ false, /* jsonEscape */ false), ctx);')
            lines.append(
                f'String {url_var} = PlaceholderResolver.resolveAll('
                f'"{ep_lit}", ctx);')
            lines.append(
                f'RestUtilities.assertPathResolved("POST", '
                f'"{_jlit(step.step_name)}", {url_var});')
            lines.append(
                f'Response {resp_var} = io.restassured.RestAssured.given()'
                f'.contentType("{step.media_type}")'
                f'.body({payload_var})'
                f'.post({url_var});')
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
            # Same substitution wrap as REST + SOAP steps -- prior emit
            # shipped `body_lit` and `ep_lit` verbatim so `#X#` / `${X}`
            # / `<<X>>` refs never resolved.
            body_raw, _ph = soapui_body_to_placeholders(
                (step.request_body or "").strip())
            body_lit = _jlit(body_raw)
            ep_lit = _jlit(step.endpoint or "")
            verb = (step.http_method or "GET").lower()
            has_body = step.http_method in ("POST", "PUT", "PATCH") and step.request_body.strip()
            lines.append(f'// [httprequest] {step.step_name} '
                         f'({step.http_method} {step.endpoint or ""})')
            resp_var = f"{sanitize_identifier(step.step_name)}Res"
            self._locals_in_method.add(resp_var)
            self.response_var_by_step[step.step_name] = resp_var
            url_var = f'{sanitize_identifier(step.step_name)}Url'
            self._locals_in_method.add(url_var)
            lines.append(
                f'String {url_var} = PlaceholderResolver.resolveAll('
                f'"{ep_lit}", ctx);')
            lines.append(
                f'RestUtilities.assertPathResolved("{step.http_method or "GET"}", '
                f'"{_jlit(step.step_name)}", {url_var});')
            if has_body:
                payload_var = f'{sanitize_identifier(step.step_name)}Payload'
                self._locals_in_method.add(payload_var)
                # jsonEscape only when the raw HTTP request declares a
                # JSON media type. Anything else (XML / form-encoded /
                # text) must NOT be JSON-escaped -- a value containing
                # `"` or `\` would land as `\"` / `\\` in the wire
                # payload and blow up the server parser.
                _media = (step.media_type or "").lower()
                _json_ctx = "true" if "json" in _media else "false"
                lines.append(
                    f'String {payload_var} = PlaceholderResolver.resolveAll('
                    f'RestUtilities.mapJsonValues("{body_lit}", '
                    f'TestSupport.mergedRow(row, ctx), '
                    f'/* strict */ false, /* jsonEscape */ {_json_ctx}), ctx);')
                body_chain = f'.body({payload_var})'
            else:
                body_chain = ""
            lines.append(
                f'Response {resp_var} = io.restassured.RestAssured.given()'
                f'.contentType("{step.media_type}"){body_chain}'
                f'.{verb}({url_var});')
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
        # ---- steps.csv ledger: one row per SoapUI step regardless of type
        self._record_step_in_ledger(step)
        return lines

    def _preflight_scan_case(self, case, steps_to_render, skip_count,
                              emit_stop_checks) -> None:
        """Detect known bug-pattern classes for this case and log them to
        the audit ledger. Called BEFORE emission so the preflight report
        also reflects the auto-applied fixes (hoist, inject) that follow.

        Categories detected here (fixes recorded by the caller after they
        actually apply): jdbc-mutation-skip, unresolved-project-ref,
        unresolved-step-ref, missing-token-with-no-canonical (case has no
        token step AND the suite has no canonical pair to inject -- test
        will 401 with no auto-fix available).
        """
        # Missing-token without a canonical to inject from = untriaged failure.
        has_own_token = any(
            isinstance(s, RestStep) and _is_token_fetch_step(s)
            for s in steps_to_render)
        canonical = getattr(self, "_canonical_token_pair", (None, None))
        if (not has_own_token and canonical == (None, None)
                and any(isinstance(s, RestStep) for s in steps_to_render)
                and skip_count == 0 and not emit_stop_checks):
            self.ledger.add_preflight_finding(
                "BLOCKER", "missing-token-with-no-canonical", case.name,
                "Case needs auth (has REST steps) but has NO tokenRequest "
                "step and no other case in the same suite has one either. "
                "Framework cannot auto-inject. Every REST call will 401 "
                "unless you hand-add a tokenRequest step or wire an "
                "AuthHelper for this suite.")
        # JDBC-mutation Skip: any Groovy sql.execute with mutation keywords.
        # Tracked already via runtime_skips ledger; add a preflight so the
        # count is visible in preflight.md too.
        mutation_kws = ("update", "insert", "delete", "upsert", "merge",
                        "cleanup", "clean_data", "seed")
        for s in steps_to_render:
            if isinstance(s, GroovyStep):
                sn = (getattr(s, "step_name", "") or "").lower()
                if any(kw in sn for kw in mutation_kws) and "sql.execute" in (
                        getattr(s, "script", "") or ""):
                    self.ledger.add_preflight_finding(
                        "HIGH", "jdbc-mutation-skip", case.name,
                        f"Groovy step `{s.step_name}` calls sql.execute "
                        f"with a mutation query the emitter cannot "
                        f"translate. Test will throw SkipException at "
                        f"runtime after any side-effect REST calls "
                        f"already fired against the target env.")
                    break
        # Unresolved #Project#Foo Groovy refs -- SoapUI project-level
        # properties that the framework has no map for. Values default to
        # empty at runtime -> downstream body cells / SQL placeholders
        # unresolved -> silent broken payloads.
        for s in steps_to_render:
            if isinstance(s, GroovyStep):
                script = getattr(s, "script", "") or ""
                proj_refs = re.findall(
                    r'#Project#([A-Za-z_][A-Za-z0-9_]*)', script)
                if proj_refs:
                    uniq = ", ".join(sorted(set(proj_refs))[:4])
                    self.ledger.add_preflight_finding(
                        "MEDIUM", "unresolved-project-ref", case.name,
                        f"Groovy step `{s.step_name}` references "
                        f"SoapUI project-scope property `#Project#` "
                        f"({uniq}). Framework maps Project scope to "
                        f"Config, but these key(s) aren't in "
                        f"program_configuration.json -- they will "
                        f"resolve to empty at runtime.")
                    break
        # JDBC preflight: query left with untranslated `${...}` after
        # translation. Would fail at DB driver with syntax error 42601.
        # (Runtime Db.execute now refuses these too, but flagging at emit
        # gives you a preflight.md entry to fix the SoapUI XML or add a
        # translator recognizer for the ref shape.)
        for s in steps_to_render:
            if isinstance(s, JdbcStep):
                q = (s.query or "")
                if "${" in q:
                    self.ledger.add_preflight_finding(
                        "HIGH", "jdbc-untranslated-soapui-ref", case.name,
                        f"JDBC step `{s.step_name}` query still contains "
                        f"SoapUI ref `${{...}}` after translation: "
                        f"`{q[:80]}` -- runtime Db.execute will refuse "
                        f"this SQL. Fix the translator recognizer or "
                        f"hand-edit the SoapUI XML to inline the value.")
                    break
        # Duplicate-cell-in-multiple-REST-bodies: two REST steps in the
        # same case whose request-body templates BOTH reference the same
        # `Properties.<field>` cell (typically username / email). If the
        # first REST creates a unique resource under that value, the
        # second REST tries to create the SAME resource and gets a
        # "not unique" 400 -- observed in token/CreateTest where
        # HHonorsEnroll + MemberHHonorsEnroll both used `#tpl_username#
        # = #Properties_usernamemember#` and one succeeded / the other
        # 400'd.
        unique_fields = ("username", "email", "hhonorsnumber", "guestid",
                         "accountid", "memberid")
        seen_field_to_step: dict[str, str] = {}
        for s in steps_to_render:
            if not isinstance(s, RestStep):
                continue
            body = (getattr(s, "request_body", "") or "").lower()
            for f in unique_fields:
                pat = f"properties_{f}#"  # matches #Properties_username#
                if pat in body:
                    prior = seen_field_to_step.get(f)
                    if prior and prior != s.step_name:
                        self.ledger.add_preflight_finding(
                            "MEDIUM", "duplicate-unique-field-across-steps",
                            case.name,
                            f"Both REST step `{prior}` and `{s.step_name}` "
                            f"reference the same `Properties.{f}` cell in "
                            f"their request bodies. If the first creates a "
                            f"resource under that value the second will hit "
                            f"a `not unique` 4xx.")
                        break
                    seen_field_to_step.setdefault(f, s.step_name)
        # Untranslated SoapUI cross-testcase refs in a header value.
        # Pattern `${#[suite#case#step]#property}` -- if soapui_expr_to_java
        # ever fails to translate one, it reaches the emit as a literal
        # string. Historical bug: 300 cases had this in their Authorization
        # header and all 300 REST calls 401'd. Now caught here so the
        # preflight surfaces any regression BEFORE runtime.
        for s in steps_to_render:
            if not isinstance(s, RestStep):
                continue
            for hname, hval in (getattr(s, "headers", None) or {}).items():
                if hval and _CROSS_TC_RX.search(hval):
                    self.ledger.add_preflight_finding(
                        "HIGH", "untranslated-cross-tc-ref", case.name,
                        f"REST step `{s.step_name}` header `{hname}` "
                        f"contains SoapUI cross-testcase ref `{hval[:80]}` "
                        f"-- would go to the wire literally without "
                        f"translation (auth 401). Ensure "
                        f"soapui_expr_to_java's _CROSS_TC_RX regex "
                        f"matched this variant.")
                    return
        # Step-response refs whose source step isn't in the current case
        # body -- e.g. `${otherStep#Response#$['id']}` where otherStep
        # doesn't appear before this reference. Compile ok, runtime will
        # have an empty extract.
        emitted_step_names = {
            getattr(s, "step_name", "") for s in steps_to_render
            if isinstance(s, RestStep)}
        for s in steps_to_render:
            body = ""
            if isinstance(s, RestStep):
                body = (getattr(s, "request_body", "") or "") + \
                       (getattr(s, "resource_path", "") or "")
            elif isinstance(s, GroovyStep):
                body = getattr(s, "script", "") or ""
            for m in re.finditer(
                    r'\$\{([A-Za-z0-9_-]+)#Response#', body):
                src = m.group(1)
                if src and src not in emitted_step_names:
                    self.ledger.add_preflight_finding(
                        "LOW", "unresolved-step-ref", case.name,
                        f"Step body references `${{{src}#Response#...}}` "
                        f"but no step named `{src}` is emitted in the "
                        f"same @Test method. Runtime value will be "
                        f"empty; downstream extraction/assertion may "
                        f"silently fail.")
                    return  # one per case is enough

    def _record_step_in_ledger(self, step) -> None:
        """Emit exactly one steps.csv row per _render_step invocation.
        Maps step class -> (step_type, coverage, endpoint_or_query, method_or_kind).
        Coverage is inferred from step type; STUB/SKIPPED steps auto-populate
        `unmapped.csv` via AuditLedger.add_step's internal gap tracking."""
        step_name = getattr(step, "step_name", "") or ""
        if isinstance(step, RestStep):
            self.ledger.add_step(
                self._current_prefix, self._current_case, step_name,
                "REST", step.http_method or "?", step.resource_path or "",
                "FULL", "")
        elif isinstance(step, GroovyStep):
            # groovy.csv carries the FULL/PARTIAL/STUB breakdown for the
            # translator's own recognizers. Mirror the last-seen coverage
            # (best guess) so steps.csv stays consistent.
            cov = "FULL"
            for r in reversed(self.ledger.groovy):
                if r[1] == self._current_case and r[2] == step_name:
                    cov = r[4]
                    break
            self.ledger.add_step(
                self._current_prefix, self._current_case, step_name,
                "GROOVY", "-", "-", cov, "")
        elif isinstance(step, PropertiesStep):
            self.ledger.add_step(
                self._current_prefix, self._current_case, step_name,
                "PROPS", "-",
                f"{len(step.properties or {})} prop(s)",
                "FULL", "")
        elif isinstance(step, DataSourceStep):
            cov = "STUB" if step.file_path else "FULL"
            gap = (f"external file `{step.file_path}` not migrated"
                   if step.file_path else "")
            self.ledger.add_step(
                self._current_prefix, self._current_case, step_name,
                "DATASOURCE", step.ds_type or "-", step.file_path or "-",
                cov, gap)
        elif isinstance(step, TransferStep):
            cov = "FULL" if step.transfers else "STUB"
            self.ledger.add_step(
                self._current_prefix, self._current_case, step_name,
                "TRANSFER", "-",
                f"{len(step.transfers or [])} transfer(s)",
                cov, "")
        elif isinstance(step, ManualStep):
            self.ledger.add_step(
                self._current_prefix, self._current_case, step_name,
                "MANUAL", "-",
                (step.description or "")[:60],
                "SKIPPED", "documentation only -- no runtime action")
        elif isinstance(step, JdbcStep):
            preview = (step.query or "").replace("\n", " ")[:60]
            self.ledger.add_step(
                self._current_prefix, self._current_case, step_name,
                "JDBC", "-", preview, "FULL", "")
        elif isinstance(step, CallTestCaseStep):
            self.ledger.add_step(
                self._current_prefix, self._current_case, step_name,
                "CALLTESTCASE", "-",
                f"{step.target_test_suite}/{step.target_test_case}",
                "STUB",
                "SoapUI calltestcase -- extract target into shared helper by hand")
        elif isinstance(step, DelayStep):
            self.ledger.add_step(
                self._current_prefix, self._current_case, step_name,
                "DELAY", "-", f"{step.delay_ms}ms", "FULL", "")
        elif isinstance(step, GotoStep):
            self.ledger.add_step(
                self._current_prefix, self._current_case, step_name,
                "GOTO", "-",
                f"{len(step.conditions)} condition(s)",
                "STUB", "Java has no goto -- manual refactor into if/loop required")
        elif isinstance(step, SoapRequestStep):
            self.ledger.add_step(
                self._current_prefix, self._current_case, step_name,
                "SOAP", "POST", step.endpoint or "-", "FULL", "")
        elif isinstance(step, HttpRequestStep):
            self.ledger.add_step(
                self._current_prefix, self._current_case, step_name,
                "HTTP", step.http_method or "GET", step.endpoint or "-",
                "FULL", "")
        elif isinstance(step, MockResponseStep):
            self.ledger.add_step(
                self._current_prefix, self._current_case, step_name,
                "MOCK", "-", "-", "STUB",
                "SoapUI mock-server step -- not runnable in REST Assured")
        elif isinstance(step, JmsStep):
            self.ledger.add_step(
                self._current_prefix, self._current_case, step_name,
                "JMS", step.direction or "-", step.destination or "-",
                "STUB", "REST Assured does not cover JMS -- manual translation")
        else:
            self.ledger.add_step(
                self._current_prefix, self._current_case, step_name,
                type(step).__name__, "-", "-", "STUB",
                "no recognizer -- runnable no-op stub emitted")

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

        # Fresh username/email/phone/domain BEFORE body substitution so
        # every sibling REST step that submits identity data gets its own
        # unique value. Prior behaviour: DataGenInput's Groovy step ran
        # once at case-start, published `Properties.Username`, and BOTH
        # `HHonorsEnroll` and `MemberHHonorsEnroll` POSTs read the same
        # value -> "Username is not unique" 400 on the second one and
        # every dependent step cascaded 404. Regen runs BEFORE the
        # payload build AND before the query-param loop below (both call
        # mapJsonValues -> mergedRow, which reads from the freshly
        # updated ctx). Id-shaped keys (guestId / accountId / memberId)
        # are NOT regenerated -- those come from response extracts and
        # overwriting them would break every downstream URL path.
        if _step_needs_regen(step):
            lines.append(
                '// [regen] fresh username/email/phone/domain '
                'before this step\'s body -- prevents shared-generator '
                'collisions across sibling REST steps.')
            # Dump ctx BEFORE regen so a diff vs the post-regen dump
            # shows exactly which keys got refreshed (and confirms the
            # ctx.put calls actually took effect against LiveMap).
            lines.append(
                f'TestSupport.traceCtx(ctx, "before-regen:'
                f'{_jlit(step.step_name)}");')
            lines.append('TestSupport.regenRandomProperties(ctx);')
            # Diagnostic log at the call site: makes it unmistakable in
            # the test log whether the regen actually fired for this
            # step, AND what value ended up in ctx. Prior symptom: both
            # enroll bodies showed identical username/email even though
            # the emitter said regen was in place -- turned out the
            # user's downloaded emit was stale. This line collapses that
            # ambiguity into one grep-able log entry per REST step.
            lines.append(
                f'LOG.info(" .. [regen] step={_jlit(step.step_name)} '
                f'Properties.Username={{}} Properties.Email={{}}", '
                f'ctx.get("Properties.Username"), '
                f'ctx.get("Properties.Email"));')
            lines.append(
                f'TestSupport.traceCtx(ctx, "after-regen:'
                f'{_jlit(step.step_name)}");')
        else:
            # Diagnostic: even for non-regen steps, dump ctx immediately
            # before the REST call so a placeholder-resolution mismatch
            # (e.g. `#Properties_totpCodeDB#` reading a stale value or
            # unrelated key via alias-walk) is attributable to a specific
            # step. Prior emit only fired ctx dumps around regen calls,
            # leaving no visibility into the ctx state feeding the
            # payload for steps that pass through untouched.
            lines.append(
                f'TestSupport.traceCtx(ctx, "before:'
                f'{_jlit(step.step_name)}");')
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
            # Post-mapJsonValues intermediate payload -- captures what
            # #X# resolution produced BEFORE PlaceholderResolver.resolveAll's
            # <<faker>> + ${ref} + hash-ref pass runs. If this shows the
            # fresh value and the final body doesn't, the drift is in
            # PlaceholderResolver; if this shows stale, mergedRow /
            # mapJsonValues is the culprit.
            lines.append(
                f'LOG.info(" .. [after-mapJsonValues] step={_jlit(step.step_name)} '
                f'({{}} chars): {{}}", {payload_var}.length(), {payload_var});')
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
            # Analogous rewrite to _placeholder_hardcoded_ids for request
            # bodies: if the SoapUI author baked a stale 6+ digit id
            # literal into the URL path (`/guests/567456/...`), swap it
            # for a ctx lookup so runtime substitution uses the current
            # test's live id (from Groovy extract OR
            # random_email_generator fallback). Applies only to id-shaped
            # param names to avoid touching legitimate small numeric
            # segments like `/v2/`.
            ID_NAMES = {"guestId", "guestID", "accountId", "accountID",
                        "memberId", "memberID", "hhonorsNumber",
                        "hHonorsNumber", "partnerAccountId",
                        "partnerAccountID", "customerId", "userId"}
            expr_stripped = (expr or "").strip().strip('"').strip("'")
            # Bug A guard: skip rewrite when the value is a run of
            # identical digits (e.g. `8888888888`, `9999999999999`,
            # `1111111111`). Those are author-picked "guaranteed
            # non-existent" ids for `_notexist_` / `_invalid_` /
            # negative test cases -- rewriting them to a live ctx
            # value defeats the whole point of the test (which
            # expects the endpoint to return 404 / 400 for the fake).
            is_test_fake = (expr_stripped.isdigit()
                            and len(expr_stripped) >= 6
                            and len(set(expr_stripped)) == 1)
            if (p in ID_NAMES and expr_stripped.isdigit()
                    and len(expr_stripped) >= 6
                    and "${" not in expr
                    and not is_test_fake):
                # Rewrite to a Properties ref -- soapui_expr_to_java will
                # then emit `TestSupport.ctxGet(ctx, "Properties.<name>")`
                # which reads from the merged runtime bag (Groovy extracts
                # win over random_email_generator fallback ids).
                expr = "${#TestCase#Properties." + p + "}"
                self.ledger.add_preflight_finding(
                    "INFO", "hardcoded-path-id-rewritten",
                    self._current_case,
                    f"REST step `{step.step_name}` URL had hardcoded id "
                    f"`{p}={expr_stripped}` in the path template. "
                    f"Rewritten to Properties.{p} so runtime uses live id.")
            elif is_test_fake:
                self.ledger.add_preflight_finding(
                    "INFO", "hardcoded-path-id-preserved-as-testfake",
                    self._current_case,
                    f"REST step `{step.step_name}` URL param `{p}="
                    f"{expr_stripped}` looks like a deliberate test-fake "
                    f"(all-same digit). Preserved literal so negative "
                    f"tests still hit a guaranteed-nonexistent resource.")
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
            elif atype.lower() in ("no authorization", "none", ""):
                # Bug #6: SoapUI's "No Authorization" profile explicitly
                # suppresses the Authorization header. Prior emit fell to
                # the else branch which silently reused the ctx bearer
                # token -- so any negative test designed to prove 401/403
                # on missing auth actually sent a valid bearer and got a
                # 200. Emit empty token so no Authorization header is
                # sent, matching SoapUI's actual wire behavior.
                lines.append(
                    f'// [auth override] step declares "No Authorization" -- '
                    f'sending WITHOUT bearer token so negative auth tests '
                    f'get the intended 401/403 from the server.')
                token_expr = '""'
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
        # Query params (shared-shape decision): if the client method
        # declared the map arg, EVERY call site must pass one -- steps
        # without their own <con:parameters> hand in an empty map so
        # the signature is uniform. Values are translated through the
        # same substitution pipeline as body payloads (${...} -> #X#
        # placeholders) and resolved at runtime via
        # PlaceholderResolver.resolveAll against the row+ctx merged
        # view, so faker tokens, cross-step refs, and CSV overrides
        # all work consistently with what body substitution does.
        client_takes_query = self.client_takes_query.get(
            (step.method_name, step.resource_path),
            bool(step.query_params))
        if client_takes_query:
            qp_var = self._uniq_local(f"__queryParams_{base}")
            lines.append(
                f'java.util.Map<String, String> {qp_var} = '
                f'new java.util.LinkedHashMap<>();')
            for qk, qv in (step.query_params or {}).items():
                translated, _ = soapui_body_to_placeholders(qv or "")
                # jsonEscape=false: query params are URL-encoded
                # downstream by RestAssured; JSON-escaping a value
                # containing `"` before URL encoding would produce
                # `%5C%22` on the wire (wrong -- should be `%22`).
                lines.append(
                    f'{qp_var}.put("{_jlit(qk)}", '
                    f'PlaceholderResolver.resolveAll('
                    f'RestUtilities.mapJsonValues('
                    f'"{_jlit(translated)}", '
                    f'TestSupport.mergedRow(row, ctx), '
                    f'/* strict */ false, /* jsonEscape */ false), ctx));')
            call_args.append(qp_var)
        # Bug #2: extraHeaders (same shared-shape invariant as query).
        # If the client method declares the extraHeaders arg, EVERY
        # call site must pass one. Steps with no custom headers hand
        # in an empty map so the signature stays uniform.
        client_takes_extra_headers = self.client_takes_extra_headers.get(
            (step.method_name, step.resource_path), False)
        if client_takes_extra_headers:
            eh_var = self._uniq_local(f"__extraHeaders_{base}")
            lines.append(
                f'java.util.Map<String, String> {eh_var} = '
                f'new java.util.LinkedHashMap<>();')
            for hk, hv in (step.headers or {}).items():
                if hk.lower() == "authorization":
                    continue  # handled via token param
                translated, _ = soapui_body_to_placeholders(hv or "")
                # jsonEscape=false: header values ship raw; JSON-escaping
                # would produce `\"` in the header value and confuse HTTP.
                lines.append(
                    f'{eh_var}.put("{_jlit(hk)}", '
                    f'PlaceholderResolver.resolveAll('
                    f'RestUtilities.mapJsonValues('
                    f'"{_jlit(translated)}", '
                    f'TestSupport.mergedRow(row, ctx), '
                    f'/* strict */ false, /* jsonEscape */ false), ctx));')
            call_args.append(eh_var)
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
        # Build the RESOLVED URL at runtime so the log shows the actual
        # path (with substituted values) instead of the template with
        # literal `{guestId}` braces -- previously misleading when the
        # log said `-> DELETE /guests/{guestId}/...` while the wire
        # actually got `/guests//...` from an empty substitution.
        # Also runs a sanity check: empty path segments (`//`) and any
        # lingering `{...}` mean the caller passed empty ctx values,
        # will 404 / 405 at the target -- WARN with the offending URL
        # so the failure is attributable BEFORE the HTTP call.
        resolved_path_expr = f'"{step.resource_path}"'
        for i, p in enumerate(path_param_names):
            resolved_path_expr = (f'{resolved_path_expr}.replace('
                                   f'"{{{p}}}", '
                                   f'({path_args[i]}) == null ? "" : ({path_args[i]}))')
        resolved_url_var = self._uniq_local(f"__resolvedUrl_{base}")
        # Wrap in PlaceholderResolver.resolveAll so #X# / @X@ refs
        # translated from ${step#Response#$.field} / ${step#property}
        # by normalize_dollar_refs_in_resource_paths resolve against
        # ctx before the URL fires. No-op on paths with no such refs
        # (fast-path returns the string unchanged when there's no `#`
        # or `@`).
        lines.append(
            f'String {resolved_url_var} = '
            f'PlaceholderResolver.resolveAll({resolved_path_expr}, ctx);')
        lines.append(
            f'RestUtilities.assertPathResolved('
            f'"{step.http_method}", "{_jlit(step.step_name)}", '
            f'{resolved_url_var});')
        lines.append(
            f'LOG.info(" -> {step.http_method} {{}}  '
            f'(step={_jlit(step.step_name)})", {resolved_url_var});')
        # Allure step banner for the REST call. AllureRestAssured filter
        # (wired globally in BaseApiTest) auto-attaches request + response
        # bodies to the CALLING step; the .step() call groups them under a
        # human-readable label in the report tree ("HHonorsEnroll: POST
        # /realms/guests/enroll" instead of a bare RestAssured entry).
        allure_label = (
            f"{step.step_name}: {step.http_method} {step.resource_path}"
        )[:120]
        lines.append(
            f'io.qameta.allure.Allure.step("{_jlit(allure_label)}");')
        # Log the outgoing request body (when present) so the value that
        # went to the server is visible both in the console AND (via
        # AllureRestAssured filter) in the Allure attachment. Guarded to
        # `body_var` since GET calls skip the payload.
        if verb_expects_body and body_var != '""':
            lines.append(
                f'LOG.info(" .. request body ({{}} chars): {{}}", '
                f'{body_var}.length(), {body_var});')
        lines.append(f'long {elapsed_var} = System.currentTimeMillis();')
        lines.append(f'Response {response_var} = client.{method_name_java}({", ".join(call_args)});')
        lines.append(
            f'LOG.info(" <- HTTP {{}} in {{}}ms  '
            f'(step={_jlit(step.step_name)})", '
            f'{response_var}.getStatusCode(), '
            f'System.currentTimeMillis() - {elapsed_var});')
        # Response-body log: ALWAYS print the body so downstream JsonPath
        # assertions failing with `actual=[]` are debuggable. Prior emit
        # printed 4xx only to keep 2xx runs quiet -- but when a 200
        # response's shape doesn't match the SoapUI author's expected
        # JsonPath, the assertion shows `expected=X actual=` (empty) with
        # no visibility into what the response actually contained.
        # WARN for 4xx (loud, unusual), INFO for 2xx/3xx (routine
        # trace). Truncated to 800 chars either way to avoid flooding
        # on huge HTML error pages or fat success payloads.
        lines.append(
            f'{{'
        )
        lines.append(
            f'    String __body_{elapsed_var} = RestUtilities.getResponseAsString({response_var});'
        )
        lines.append(
            f'    if (__body_{elapsed_var} == null) __body_{elapsed_var} = "<null>";'
        )
        lines.append(
            f'    if (__body_{elapsed_var}.length() > 800) '
            f'__body_{elapsed_var} = __body_{elapsed_var}.substring(0, 800) + "... (truncated)";'
        )
        lines.append(
            f'    if ({response_var}.getStatusCode() >= 400) {{'
        )
        lines.append(
            f'        LOG.warn(" .. response body (HTTP {{}}): {{}}", '
            f'{response_var}.getStatusCode(), __body_{elapsed_var});'
        )
        lines.append(
            f'    }} else {{'
        )
        lines.append(
            f'        LOG.info(" .. response body (HTTP {{}}): {{}}", '
            f'{response_var}.getStatusCode(), __body_{elapsed_var});'
        )
        lines.append(
            f'    }}'
        )
        lines.append('}')
        # Gap #4 fix: dump response headers at DEBUG so a downstream
        # groovy extract that reads `hilton-member-location` /
        # `x-hilton-*` / auth challenge headers can be traced. Currently
        # we only log the body; when the extract fails silently (header
        # missing / wrong case / different value than expected), the
        # only visible symptom is a downstream URL producing 404.
        # DEBUG level to keep default log volume unchanged.
        lines.append(
            f'if (LOG.isDebugEnabled()) {{ '
            f'LOG.debug(" .. response headers: {{}}", '
            f'{response_var}.getHeaders()); }}'
        )
        lines.append(f'RestUtilities.logResponseBody(testCaseId, holder, RestUtilities.getResponseAsString({response_var}));')

        # Assertions:
        # Emit ONLY this case's own assertions (not the cluster union).
        # Prior union-across-cluster-members design was well-intentioned
        # ("don't silently lose sibling cases' extra assertions") but
        # caused false failures: sibling case B might attach a
        # MessageContent-accountStatus assertion to step X, but case A's
        # step X response doesn't contain that field, so cross-mixing
        # the assertion fired against the wrong response and reported
        # spurious "expected [X] but found []". SoapUI author's intent
        # is per-case -- assertions attached to a specific case's step
        # apply to THAT case only. Cases sharing a cluster contribute
        # CSV ROWS (different data), not different assertion shapes.
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
            # Also skip when the JsonPath expression itself contains an
            # unresolved SoapUI placeholder -- e.g. `$.foo[?(@.id ==
            # {Properties#partnerAccountID})]`. RestAssured / Groovy will
            # choke on the `{` char with "Invalid JSON expression". Would
            # need runtime string-concat + type-aware quoting to translate;
            # deferred. Skip at emit time so the test fails loudly at
            # convert-time rather than blowing up with a cryptic Groovy
            # parse error at runtime.
            if _has_soapui_placeholder(raw_path):
                safe_path = raw_path.replace("*/", "* /")
                return ([
                    f'// [{t}] SKIPPED at emit time -- JsonPath expression '
                    f'contains an unresolved SoapUI property placeholder.',
                    f'// path: {safe_path}',
                    f'// Runtime interpolation with ctx values needs type-'
                    f'aware quoting (string vs number); not auto-translated. '
                    f'Rewrite manually as: `String path = "$.foo[?(@.id == "'
                    f' + TestSupport.ctxGet(ctx, "Properties.X") + ")]";`',
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
            # Audit fix #7: when the SoapUI assertion has NO configured
            # codes (author left the assertion but never set an expected
            # value), use -1 as the emit-time sentinel instead of
            # defaulting to 200. Prior default silently made every
            # unconfigured step expect 200, so a negative test that
            # correctly returned 400 would fail as "expected 200 but
            # got 400" -- attributed to the wrong side. With -1,
            # runtime emits an actionable "assertion SKIPPED" WARN
            # instead of a bogus failure.
            code_list = [c for c in re.split(r"[,\s]+", codes)
                         if c and c.strip().lstrip("-").isdigit()]
            first_code = code_list[0] if code_list else "-1"
            # Bug C fix: SoapUI's "Valid HTTP Status Codes" assertion
            # accepts ANY of the listed codes (e.g. `<codes>200, 201,
            # 206, 204</codes>` passes on any 2xx). Prior emit took
            # only the first code and silently failed on the others.
            # When >1 code declared, emit a Set.contains membership
            # check instead of an assertEquals on the first code.
            # CSV override (`expected_<step>_status_code`) still wins
            # when non-empty AND overrides with a strict single value.
            if len(code_list) > 1:
                valid_set_lit = ("java.util.Set.of("
                                 + ", ".join(code_list) + ")")
                return ([
                    f'String rawStatus_{vsid} = row.get("{col_name}");',
                    f'java.util.Set<Integer> validCodes_{vsid} = {valid_set_lit};',
                    f'if (rawStatus_{vsid} != null && !rawStatus_{vsid}.isEmpty()) {{',
                    f'    int expected_{vsid} = com.ak.api.rest.utilities.RestUtilities'
                    f'.parseIntOrDefault(rawStatus_{vsid}, {first_code}, "{col_name}");',
                    f'    softAssert.assertEquals({response_var}.statusCode(), expected_{vsid}, "expected status for {step_name} (CSV override of multi-code {code_list})");',
                    f'}} else {{',
                    f'    softAssert.assertTrue(validCodes_{vsid}.contains({response_var}.statusCode()), "expected status for {step_name} in {code_list} but got " + {response_var}.statusCode());',
                    f'}}',
                ], "FULL")
            # Two-tier lookup: the standalone `expected_<step>_status_code`
            # column wins; otherwise fall back to `exp.getInt("statusCode", ...)`
            # which parses the legacy `expected` combined column. Empty
            # cell -> treat as missing so the fallback fires (not parseInt("")).
            #
            # Runtime guard: when the resolved expected is < 0 (SoapUI
            # codes empty AND CSV cell empty AND expected column has no
            # statusCode) -> WARN + SKIP the assertion. Applies only
            # when NO source of truth exists; the typical case (any of
            # the three configured) fires the assertion normally.
            return ([
                f'String rawStatus_{vsid} = row.get("{col_name}");',
                f'int expected_{vsid} = com.ak.api.rest.utilities.RestUtilities'
                f'.parseIntOrDefault(rawStatus_{vsid}, '
                f'exp.getInt("statusCode", {first_code}), "{col_name}");',
                f'if (expected_{vsid} < 0) {{',
                f'    LOG.warn(" .. [status-code assert SKIPPED] step={step_name} '
                f'-- no expected status configured (SoapUI codes empty, CSV '
                f'column `{col_name}` empty, and `expected` column has no '
                f'`statusCode:`). Populate one of these sources to enable '
                f'the assertion. Actual status was {{}}.", {response_var}.statusCode());',
                f'}} else {{',
                f'    softAssert.assertEquals({response_var}.statusCode(), expected_{vsid}, "expected status for {step_name}");',
                f'}}',
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
                    f'softAssert.assertNotNull(com.ak.api.rest.utilities.RestUtilities'
                    f'.safeJsonGet({response_var}, "{path}"), "JsonPath present: {path}");',
                ], "PARTIAL")
            if "${" in content_raw:
                # SoapUI property expansion in expected value -- keep runtime
                # substitution behavior; still parameterizable via CSV column.
                java_expr = soapui_expr_to_java(content_raw)
                pre_lines: list[str] = []
                # If the fallback resolves to a ctxGet(K) that a LATER step
                # populates from THIS response (typical SoapUI pattern:
                # Groovy accountDetails extracts response.accountId ->
                # ctx["PropertiesDetails.accountID"], and the assertion for
                # the SAME response asserts jsonpath accountId against
                # ${PropertiesDetails#accountID}), populate that ctx key
                # NOW from the response so the assertion isn't racing the
                # later step. Prior behaviour: ctxGet fired before the
                # Groovy extract, returned a generator default (e.g.
                # `658716206`), and assertions failed spuriously with
                # "expected [<generator-random>] but found [<real
                # response id>]" -- confusing since neither is user-visible.
                # Emits ONE putExtracted per (K, path) pair -- idempotent
                # with the later Groovy step, which re-writes the same value.
                m_ctx = re.match(
                    r'^\s*TestSupport\.ctxGet\(ctx,\s*"([^"]+)"\)\s*$',
                    java_expr)
                if m_ctx:
                    hoisted_key = m_ctx.group(1)
                    pre_lines.append(
                        f'// [assert-hoist] pre-populate ctx.'
                        f'{hoisted_key} from this response so the '
                        f'${{{content_raw[2:-1]}}} fallback resolves to '
                        f'the extracted value, not a stale generator '
                        f'default from before the later Groovy extract.')
                    pre_lines.append(
                        f'TestSupport.putExtracted(ctx, "{_jlit(hoisted_key)}", '
                        f'com.ak.api.rest.utilities.RestUtilities'
                        f'.safeJsonExtract({response_var}, "{path}"));')
                return (pre_lines + [
                    # Empty-as-missing: treat blank CSV cell as "no
                    # override" so the ${...} fallback fires. Prior
                    # `getOrDefault` returned the empty string when the
                    # cell was present-but-empty, and the assertion
                    # then compared response.field vs "" and always
                    # failed. Every other assertion helper (via
                    # `_row_expr`) already does this null-or-empty
                    # ternary; this branch was the odd one out.
                    f'String expected_{vsid} = (row.get("{col_name}") == null || '
                    f'row.get("{col_name}").isEmpty() ? '
                    f'String.valueOf({java_expr}) : row.get("{col_name}"));',
                    f'LOG.info(" .. [assert] {path} expected={{}} actual={{}}", '
                    f'expected_{vsid}, com.ak.api.rest.utilities.RestUtilities'
                    f'.safeJsonExtract({response_var}, "{path}"));',
                    f'softAssert.assertEquals(com.ak.api.rest.utilities.RestUtilities'
                    f'.safeJsonExtract({response_var}, "{path}"), '
                    f'expected_{vsid}, "JsonPath Match: {path}");',
                ], "FULL")
            content = _jlit(content_raw)
            return ([
                f'String expected_{vsid} = {_row_expr(content)};',
                f'LOG.info(" .. [assert] {path} expected={{}} actual={{}}", '
                f'expected_{vsid}, com.ak.api.rest.utilities.RestUtilities'
                f'.safeJsonExtract({response_var}, "{path}"));',
                f'softAssert.assertEquals(com.ak.api.rest.utilities.RestUtilities'
                f'.safeJsonExtract({response_var}, "{path}"), '
                f'expected_{vsid}, "JsonPath Match: {path}");',
            ], "FULL")
        if t == "JsonPath Existence Match":
            path = _jlit(_jsonpath_to_gpath(cfg.get("path", "")))
            # existence check can be turned OFF for a row by setting the CSV
            # cell to "false"; empty cell keeps the (default = must-exist)
            # behavior.
            return ([
                f'if (!"false".equalsIgnoreCase(row.getOrDefault("{col_name}", "true"))) {{',
                f'    softAssert.assertNotNull(com.ak.api.rest.utilities.RestUtilities'
                f'.safeJsonGet({response_var}, "{path}"), '
                f'"JsonPath exists: {path}");',
                f'}}',
            ], "FULL")
        if t == "JsonPath Count":
            path = _jlit(_jsonpath_to_gpath(cfg.get("path", "")))
            expected_raw = (cfg.get("expectedCount", "") or cfg.get("content", "") or "0").strip()
            expected_int = expected_raw if expected_raw.lstrip("-").isdigit() else "0"
            return ([
                f'Object count_{vsid} = com.ak.api.rest.utilities.RestUtilities'
                f'.safeJsonGet({response_var}, "{path}");',
                f'int actualCount_{vsid} = count_{vsid} instanceof java.util.List ? '
                f'((java.util.List<?>) count_{vsid}).size() : (count_{vsid} == null ? 0 : 1);',
                f'String rawExp_{vsid} = row.get("{col_name}");',
                f'int expectedCount_{vsid} = com.ak.api.rest.utilities.RestUtilities'
                f'.parseIntOrDefault(rawExp_{vsid}, {expected_int}, "{col_name}");',
                f'softAssert.assertEquals(actualCount_{vsid}, expectedCount_{vsid}, '
                f'"JsonPath Count for {path}");',
            ], "FULL")
        if t == "JsonPath RegEx Match":
            path = _jlit(_jsonpath_to_gpath(cfg.get("path", "")))
            content = _jlit(cfg.get("content", ""))
            return ([
                f'String matched_{vsid} = com.ak.api.rest.utilities.RestUtilities'
                f'.safeJsonExtract({response_var}, "{path}");',
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
                f'long sla_{vsid} = com.ak.api.rest.utilities.RestUtilities'
                f'.parseLongOrDefault(rawSla_{vsid}, {sla}L, "{col_name}");',
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
                f'String actual_{v} = com.ak.api.rest.utilities.RestUtilities'
                f'.safeJsonExtract({response_var}, "{_jlit(jpath)}");')
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
                f'String actual_{v} = com.ak.api.rest.utilities.RestUtilities'
                f'.safeJsonExtract({response_var}, "{_jlit(path)}");')
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
        Also logs each block to the audit ledger, including a runtime-skip
        entry when the translator emitted a `throw new SkipException(...)`
        (typically for an untranslated JDBC mutation) so the summary can
        surface silent capacity loss."""
        from groovy_translator import translate as translate_groovy
        lines, meta = translate_groovy(
            step.script or "", self.response_var_by_step,
            step_name_hint=step.step_name)
        self.ledger.add_groovy(
            self._current_prefix, self._current_case, step.step_name, meta)
        # Detect emitted SkipException throws so the audit tracks them as
        # runtime skips. Concatenates every string-literal chunk inside
        # the throw() call so multi-part " + " concats (used by the
        # groovy_translator's mutation-jdbc emitter) surface the FULL
        # reason text in runtime_skips.csv, not just the first literal.
        joined = "\n".join(lines)
        for m in re.finditer(
                r'throw\s+new\s+org\.testng\.SkipException\((.*?)\);',
                joined, flags=re.DOTALL):
            args = m.group(1)
            parts = re.findall(r'"([^"\\]*(?:\\.[^"\\]*)*)"', args)
            reason = "".join(parts).strip() or args.strip()[:120]
            self.ledger.add_runtime_skip(
                self._current_prefix, self._current_case,
                getattr(self, "_current_method", "?"),
                reason, step.step_name)
        return lines

    def _render_transfer_translated(self, step: TransferStep) -> list[str]:
        """Turn SoapUI PropertyTransfer into a ctx.put backed by an
        extract expression suited to the source's actual shape:

        - source_path starts with `$` OR language=JSONPATH ->
          safeJsonExtract with `_translate_soapui_jsonpath` (handles
          `$['x']` bracket syntax that the prior lstrip("$.") crude
          strip mangled)
        - language=HEADER OR path looks like a header name and source
          is a REST response -> response.header(name)
        - source_type=Property OR source_step names a Properties/Data
          step -> ctxGet(ctx, "sourceStep.sourcePath") reads the
          upstream Properties value out of ctx
        - source_path empty -> transfer entire body via .asString()

        Prior emit gave up on every non-JsonPath case with a
        `// [transfer] no response found` stub, silently losing the
        publication -- downstream URLs / SQL then saw empty ctx keys
        and cascaded 404/skip warnings. Audit measured 327 SKIPPED
        transfers under the old code path."""
        lines = [f'// [transfer step] {step.step_name}']
        for t in step.transfers:
            src_step = t.get("source_step", "")
            src_path = t.get("source_path", "") or ""
            src_type = (t.get("source_type", "") or "").strip()
            src_lang = (t.get("source_path_language", "") or "").strip().upper()
            tgt_step = t.get("target_step", "")
            tgt_path = t.get("target_path", "")
            ctx_key = f"{tgt_step}.{tgt_path}" if tgt_path else tgt_step
            src_resp = self.response_var_by_step.get(src_step)

            # (1) Property-source: read from ctx directly, no response
            # var needed. Handles Properties/DataGen steps whose
            # sourceStep isn't a REST call.
            if src_type.lower() in ("property", "properties") or (
                    not src_resp and src_path and not src_path.startswith("$")
                    and src_lang not in ("JSONPATH", "XPATH", "HEADER")):
                lines.append(
                    f'TestSupport.putExtracted(ctx, "{_jlit(ctx_key)}", '
                    f'TestSupport.ctxGet(ctx, "{_jlit(src_step)}.{_jlit(src_path)}"));')
                continue

            if not src_resp:
                # No response variable in scope (source step ran in a
                # different method OR was skipped by cluster grouping).
                # Emit a comment so the gap is visible in the diff.
                lines.append(
                    f'// [transfer] no response var in scope for source step '
                    f'"{_jlit(src_step)}" (path=`{_jlit(src_path)}` lang=`{_jlit(src_lang or "?")}`); '
                    f'target `{_jlit(ctx_key)}` will stay unset -- caller '
                    f'may hit ctxGet fallback or an empty URL segment.')
                continue

            # (2) Empty source_path -> transfer the entire body.
            if not src_path:
                lines.append(
                    f'TestSupport.putExtracted(ctx, "{_jlit(ctx_key)}", '
                    f'{src_resp}.asString());')
                continue

            # (3) Header extract -- explicit language OR the path is a
            # bare header-name shape (no `$`, no `.`, no `[`, no `/`).
            looks_like_header = (
                bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", src_path))
                and src_lang != "JSONPATH" and src_lang != "XPATH")
            if src_lang == "HEADER" or looks_like_header:
                lines.append(
                    f'TestSupport.putExtracted(ctx, "{_jlit(ctx_key)}", '
                    f'{src_resp}.header("{_jlit(src_path)}"));')
                continue

            # (4) JsonPath (either explicit language or starts with `$`).
            if src_lang == "JSONPATH" or src_path.startswith("$"):
                jp = _translate_soapui_jsonpath(src_path)
                lines.append(
                    f'TestSupport.putExtracted(ctx, "{_jlit(ctx_key)}", '
                    f'com.ak.api.rest.utilities.RestUtilities'
                    f'.safeJsonExtract({src_resp}, "{_jlit(jp)}"));')
                continue

            # (5) XPath -- best-effort xmlPath extract via RestAssured.
            # Falls back to raw body if the response isn't XML.
            if src_lang == "XPATH" or src_path.startswith("/"):
                lines.append(
                    f'try {{ TestSupport.putExtracted(ctx, "{_jlit(ctx_key)}", '
                    f'{src_resp}.xmlPath().getString("{_jlit(src_path)}")); }} '
                    f'catch (Exception __xpEx) {{ '
                    f'LOG.warn("transfer xpath `{_jlit(src_path)}` failed on '
                    f'{{}}: {{}}", "{_jlit(src_step)}", __xpEx.getMessage()); }}')
                continue

            # Fallback: unknown shape -- publish the whole body but
            # comment the raw path for author review.
            lines.append(
                f'TestSupport.putExtracted(ctx, "{_jlit(ctx_key)}", '
                f'{src_resp}.asString()); '
                f'// [transfer] unknown source-path shape `{_jlit(src_path)}` '
                f'(lang=`{_jlit(src_lang or "?")}`) -- publishing whole body')
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
import com.ak.api.data.FakeData;

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

    /** SLF4J logger for framework-level diagnostics (putExtracted writes,
     *  putExtracted skips, ctx alias-walks). Emits at DEBUG so the volume
     *  stays low; enable com.ak.api.** = DEBUG in log4j2.xml to see. */
    private static final org.slf4j.Logger LOG =
            org.slf4j.LoggerFactory.getLogger(TestSupport.class);

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
     * Alias-aware ctx lookup. Solves the SoapUI-property-namespace
     * mismatch where a Groovy step captures a value into (say)
     * {{@code ctx.put("PropertiesGuestId.guestId", ...)}} but the next
     * REST step's path param reads {{@code ctx.get("Properties.guestID")}}
     * -- different key, so the captured value is invisible.
     *
     * <p>Walk order:
     * <ol>
     *   <li>Exact key: ctx.get(primaryKey)</li>
     *   <li>Case-flipped tail (guestID vs guestId, ID vs Id)</li>
     *   <li>Namespace-stripped: any ctx key ending in `.field` where
     *       field is the trailing component of primaryKey. Catches
     *       PropertiesGuestId.guestId vs Properties.guestID.</li>
     *   <li>Bare-field lookup: ctx.get(field)</li>
     * </ol>
     * Returns {{@code ""}} when nothing matches. Empty ctx values are
     * treated as "not set" so a still-frozen literal from a Properties
     * step doesn't beat a Groovy-captured value under a sibling key.
     */
    public static String ctxGet(Map<String, String> ctx, String primaryKey) {{
        String raw = ctxGetRaw(ctx, primaryKey);
        return expandPlaceholders(ctx, raw);
    }}

    /** Raw lookup without placeholder expansion (package-private). */
    static String ctxGetRaw(Map<String, String> ctx, String primaryKey) {{
        if (ctx == null || primaryKey == null) return "";
        // KNOWN-EMPTY signal: if the primary key was explicitly written to
        // ctx with an empty value (typically by safeJsonExtract after an
        // upstream 4xx returned an empty/HTML body), do NOT alias-walk --
        // substituting a stale hardcoded ID from a sibling key would mask
        // the upstream failure as a Hilton-API bug and pump requests at
        // random resources. Empty-known beats any inferred alias.
        if (ctx.containsKey(primaryKey)) {{
            String direct = ctx.get(primaryKey);
            return direct == null ? "" : direct;
        }}
        // Extract the trailing field name after the last dot.
        int lastDot = primaryKey.lastIndexOf('.');
        String field = (lastDot >= 0) ? primaryKey.substring(lastDot + 1) : primaryKey;
        // Case-flipped variants: guestId <-> guestID, accountId <-> accountID
        String fieldAlt = flipTrailingCase(field);
        // Walk every ctx key -- match by suffix on the field name.
        String bestByAlias = null;
        for (Map.Entry<String, String> e : ctx.entrySet()) {{
            String k = e.getKey();
            String val = e.getValue();
            if (val == null || val.isEmpty() || k.equals(primaryKey)) continue;
            int kDot = k.lastIndexOf('.');
            String kField = (kDot >= 0) ? k.substring(kDot + 1) : k;
            if (kField.equals(field) || (fieldAlt != null && kField.equals(fieldAlt))) {{
                // Prefer values written INTO ctx by a Groovy extract
                // (namespaced) over bare-field ones.
                if (kDot >= 0) return val;
                if (bestByAlias == null) bestByAlias = val;
            }}
        }}
        if (bestByAlias != null) return bestByAlias;
        // Last resort: bare-field lookup
        String bare = ctx.get(field);
        if (bare != null && !bare.isEmpty()) return bare;
        return "";
    }}

    /**
     * Recursively expand {{@code #Key#}} and {{@code @Key@}} refs in
     * the value against ctx. Bounded at 5 iterations to break cycles.
     * URL path substitution reads ctx directly (NOT through
     * mapJsonValues), so without this expansion the emitter's CSV
     * id-shape rewrite (which replaces stale ids like
     * {{@code PropertiesDetails.accountID = 2000016128}} with
     * {{@code @Properties_accountID@}}) would send the literal
     * placeholder in the URL path.
     */
    private static String expandPlaceholders(Map<String, String> ctx, String value) {{
        if (value == null || value.isEmpty()) return value == null ? "" : value;
        if (value.indexOf('#') < 0 && value.indexOf('@') < 0) return value;
        String cur = value;
        for (int i = 0; i < 5; i++) {{
            String next = expandOnce(cur, HASH_REF, ctx);
            next = expandOnce(next, AT_REF, ctx);
            if (next.equals(cur)) return next;
            cur = next;
        }}
        return cur;
    }}

    /**
     * Replace matches of {{@code pattern}} only when ctxGetRaw returns a
     * non-empty value; otherwise leave the placeholder literal so
     * {{@code assertPathResolved}} can surface the unresolved key at
     * request time. Prior impl substituted "" on unknown keys, which
     * silently produced {{@code /guests//accounts}} and masked the
     * upstream extract failure as a target-server 404.
     */
    private static String expandOnce(String text, java.util.regex.Pattern pattern,
                                     Map<String, String> ctx) {{
        java.util.regex.Matcher m = pattern.matcher(text);
        StringBuilder out = new StringBuilder();
        while (m.find()) {{
            String key = m.group(1);
            String v = ctxGetRaw(ctx, key);
            if (v == null || v.isEmpty()) {{
                m.appendReplacement(out, java.util.regex.Matcher.quoteReplacement(m.group()));
            }} else {{
                m.appendReplacement(out, java.util.regex.Matcher.quoteReplacement(v));
            }}
        }}
        m.appendTail(out);
        return out.toString();
    }}

    private static final java.util.regex.Pattern HASH_REF =
            java.util.regex.Pattern.compile("#([A-Za-z0-9_.-]+)#");
    private static final java.util.regex.Pattern AT_REF =
            java.util.regex.Pattern.compile("@([A-Za-z0-9_.-]+)@");

    private static String flipTrailingCase(String field) {{
        if (field == null || field.length() < 2) return null;
        int n = field.length();
        char last = field.charAt(n - 1);
        char alt;
        if (last == 'D') alt = 'd';
        else if (last == 'd') alt = 'D';
        else return null;
        return field.substring(0, n - 1) + alt;
    }}

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
     *
     * <p>Case-mismatch aliasing (e.g. {{@code Properties.Username}} vs
     * {{@code Properties.username}}) is intentionally NOT expanded here:
     * a case-flip alias would cause a later {{@code put}} of the
     * lower-case key to OVERWRITE a genuine value written earlier
     * under the upper-case key (LinkedHashMap iteration order fires
     * the two direct writes in insertion sequence, so the case-flip
     * alias of the second write clobbers the direct value of the
     * first). Case-mismatch resolution lives in {{@link #ctxGet}}
     * (per-lookup, walk alternatives at read time) instead.</p>
     */
    private static void putWithAliases(Map<String, String> merged, String key, String value) {{
        if (key == null) return;
        merged.put(key, value);
        String underscoreForm = key.replace('.', '_').replace('-', '_');
        if (!underscoreForm.equals(key)) merged.put(underscoreForm, value);
        String dotForm = key.replace('-', '_');
        if (!dotForm.equals(key) && !dotForm.equals(underscoreForm)) merged.put(dotForm, value);
        // Snake-case bare alias of the trailing FIELD, so a SQL
        // placeholder like `#account_id#` (SoapUI script uses the DB
        // column name) resolves against a ctx key like
        // `PropertiesDetails.accountID` or `Properties.accountId`.
        // Without this, mapJsonValues looked up "account_id" verbatim,
        // found nothing, substituted the "null" fallback, and the
        // JDBC layer refused the query with
        //   "SQL has 'null' literal from unresolved #placeholder#".
        // Iteration order in ctx is insertion order (LinkedHashMap),
        // so if a PropertyTransfer write comes AFTER a generator
        // fake write for the same field, the extract's real value
        // wins on the shared snake alias.
        int lastDot = key.lastIndexOf('.');
        String field = (lastDot >= 0) ? key.substring(lastDot + 1) : key;
        String snake = camelToSnakeLower(field);
        if (snake != null && !snake.equals(field)
                && !snake.equals(key) && !snake.equals(underscoreForm)) {{
            merged.put(snake, value);
        }}
    }}

    /** camelCase / PascalCase / camelID -> snake_case_lower. Inserts an
     *  underscore before every uppercase letter preceded by a lowercase
     *  letter, then lowercases the whole thing. Handles trailing all-caps
     *  abbreviations by treating them as one word (accountID -> account_id,
     *  not account_i_d) via the lookahead. Returns null on null/empty. */
    private static String camelToSnakeLower(String s) {{
        if (s == null || s.isEmpty()) return null;
        // Insert `_` before capital-preceded-by-lowercase, and also
        // before a trailing capital-followed-by-lowercase in an ALLCAPS
        // run (splits ID before a following word, keeps ID together at
        // the end).
        String withUnderscores = s
                .replaceAll("([a-z0-9])([A-Z])", "$1_$2")
                .replaceAll("([A-Z]+)([A-Z][a-z])", "$1_$2");
        return withUnderscores.toLowerCase();
    }}

    /**
     * Set {{@code ctx[key] = value}} ONLY if value is non-empty AND the
     * key isn't already present. Used by the emitter's Properties-step
     * defaults path so an absent default doesn't plant an empty string
     * into ctx (which would defeat {{@link #ctxGet}}'s "primary key
     * present" short-circuit and force it to return "" instead of
     * alias-walking to find a Groovy-extracted value under a sibling
     * key).
     */
    public static void putIfNonEmpty(Map<String, String> ctx, String key, String value) {{
        if (ctx == null || key == null) return;
        if (value == null || value.isEmpty()) return;
        ctx.putIfAbsent(key, value);
    }}

    /**
     * Publish a value extracted from a live REST response (or Groovy
     * mid-flow computation) into ctx. Semantics: OVERWRITE existing
     * value iff the new value is non-empty. Distinct from
     * {{@link #putIfNonEmpty}} which uses putIfAbsent and is meant for
     * seeding generator defaults; using that method for extracts
     * causes the "fresh response id gets shadowed by stale generator
     * default" cascade (Properties.guestID stayed as
     * DataGenInput's fake random id even after HHonorsEnroll
     * responded, so every downstream URL 400'd or 404'd on the
     * fake).
     *
     * <p>Skips the write when the extract returned empty so a 4xx
     * upstream doesn't silently plant "" into ctx -- ctxGet's
     * "primary key present" short-circuit would then refuse to
     * alias-walk and downstream URL substitution would collapse to
     * `//`. Empty-extract falls through to whatever value was there
     * before (usually the generator default), which is safer than
     * empty.</p>
     */
    public static void putExtracted(Map<String, String> ctx, String key, String value) {{
        if (ctx == null || key == null) return;
        if (value == null || value.isEmpty()) {{
            // Diagnostic: extract returned empty -- log so a silent
            // "fell through to stale generator default" bug is visible
            // instead of appearing as a downstream 400 with mystery
            // origin. Common cause: safeJsonExtract on a 4xx response
            // body that had no JSON field for `key`.
            LOG.debug(" .. [putExtracted SKIPPED] key={{}} value=<empty> "
                    + "(ctx keeps its prior value, if any -- typically a "
                    + "stale generator default from DataGenInput regen)", key);
            return;
        }}
        // Diagnostic: publish the ctx write so a placeholder-resolution
        // bug (where a subsequent step reads a DIFFERENT key) is
        // attributable. Log at DEBUG to keep INFO output lean; DEBUG is
        // enabled for com.ak.api.** in the framework log4j2.xml default.
        LOG.debug(" .. [putExtracted] {{}} <- {{}}", key,
                value.length() > 60 ? value.substring(0, 60) + "..." : value);
        ctx.put(key, value);
        // Also publish under the trailing-D/d case-flipped alias so a
        // template that references the OTHER casing of an id field
        // (Properties.guestId vs Properties.guestID) sees this
        // extract's value instead of the DataGenInput generator's
        // fake fallback under the sibling key. flipTrailingCase
        // returns null for non-id fields (last char isn't D or d) so
        // this is a no-op for Username/Email/etc. -- targeted at
        // exactly the case-mismatch class ctxGet's short-circuit
        // ("primary key present, don't alias-walk") can't fix on its
        // own once the sibling has a stale non-empty value.
        String flipped = flipTrailingCase(key);
        if (flipped != null && !flipped.equals(key)) ctx.put(flipped, value);
    }}

    /**
     * Dump every ctx entry whose KEY starts with one of the prefixes
     * a REST-step body would care about (Properties.*, tokenId.*,
     * PropertiesDetails.*). Sorted so successive dumps compare
     * cleanly with `diff` in the log. Used as a tracing helper --
     * emitter drops calls to this around each regen + before
     * mergedRow so a stale value's origin is unambiguous.
     */
    public static void traceCtx(Map<String, String> ctx, String tag) {{
        org.slf4j.Logger log = org.slf4j.LoggerFactory.getLogger(TestSupport.class);
        if (ctx == null || ctx.isEmpty()) {{
            log.info(" .. [ctx@{{}}] <empty>", tag);
            return;
        }}
        java.util.TreeMap<String, String> sorted = new java.util.TreeMap<>();
        for (Map.Entry<String, String> e : ctx.entrySet()) {{
            String k = e.getKey();
            if (k == null) continue;
            if (k.startsWith("Properties") || k.startsWith("tokenId")
                    || k.startsWith("PropertiesDetails")
                    || k.startsWith("PropertiesGuestId")) {{
                sorted.put(k, e.getValue());
            }}
        }}
        if (sorted.isEmpty()) {{
            log.info(" .. [ctx@{{}}] <no Properties/tokenId keys>", tag);
            return;
        }}
        StringBuilder sb = new StringBuilder(sorted.size() * 40);
        for (Map.Entry<String, String> e : sorted.entrySet()) {{
            sb.append("\\n     ").append(e.getKey()).append(" = ")
              .append(e.getValue());
        }}
        log.info(" .. [ctx@{{}}] ({{}} keys):{{}}", tag, sorted.size(), sb);
    }}

    /**
     * Regenerate the author-controlled random Properties so every
     * request that submits fresh identity data gets its own unique
     * value. Fixes the "Username is not unique." 400 that hits any
     * sibling POST /enroll after the first one -- both bodies used to
     * read the same {{@code Properties.Username}} written once by the
     * DataGenInput Groovy step.
     *
     * <p><b>Only these fields are refreshed:</b> username, email,
     * phone, domain, and hhonorsNumber (each with case-flipped +
     * suite-specific variants so every template naming convention
     * lands on the same fresh value within one request). Id-shaped
     * fields ({{@code Properties.guestId}}, {{@code accountId}},
     * {{@code memberId}}) are DELIBERATELY LEFT ALONE -- overwriting
     * them would clobber the value a Groovy extract just published
     * from an upstream response, breaking every downstream URL path
     * substitution.</p>
     *
     * <p>Also uses {{@code ctx.put}} (not putIfNonEmpty) so we
     * actually replace the stale value each call -- putIfNonEmpty
     * would no-op after the first invocation and defeat the point.</p>
     */
    public static void regenRandomProperties(Map<String, String> ctx) {{
        if (ctx == null) return;
        // Domain source: prefer the SoapUI-frozen Properties.Hardcodeddomain
        // (author-picked "allowed" domain that Hilton stg has pre-registered
        // as a valid emailDomain -- e.g. `vjuum.com`, `dpptd.com`). Falls
        // back to a fresh random word.com only when the frozen value is
        // absent. Prior behaviour ALWAYS used FakeData.username()+".com"
        // which produced fresh random domains like `abe.greenfelder.com`
        // that Hilton stg rejects with 400 "Email address domain must
        // match an allowed domain within program account" -- because
        // some templates hardcode the emailDomains array to literal
        // "vjuum.com" while ownerEmailAddress uses the (now-random)
        // Properties.hardcodedemail. Preserving the frozen value keeps
        // the request body coherent with the author's intent AND with
        // the pre-registered stg domain allowlist.
        String frozen = ctx.getOrDefault("Properties.Hardcodeddomain",
                        ctx.getOrDefault("Properties.hardcodeddomain",
                        ctx.getOrDefault("Properties.websitedomain", "")));
        boolean usingFrozenDomain = (frozen != null && !frozen.isEmpty());
        String domain = usingFrozenDomain ? frozen : FakeData.username() + ".com";
        String uname = FakeData.username();
        String email = FakeData.username() + "@" + domain;
        String phone = FakeData.faker().numerify("#########");
        String hhon = FakeData.faker().numerify("#########");
        // Username variants -- covers BOTH case forms the DataGenInput
        // generator writes (first-char case-flipped). If a template
        // references the sibling casing (e.g. Properties.Usernamemember
        // vs Properties.usernamemember), missing the flip would leave
        // that variant stuck at the generator's initial random value.
        ctx.put("Properties.Username", uname);
        ctx.put("Properties.username", uname);
        ctx.put("Properties.Username2", uname);
        ctx.put("Properties.username2", uname);
        ctx.put("Properties.usernamemember", uname);
        ctx.put("Properties.Usernamemember", uname);
        ctx.put("Properties.usernameM", uname);
        ctx.put("Properties.UsernameM", uname);
        // Email variants -- both case forms of every variant so a
        // template using Properties.emailMember (lower e) gets the
        // fresh value, not the DataGenInput stale.
        ctx.put("Properties.Email", email);
        ctx.put("Properties.email", email);
        ctx.put("Properties.EmailAddress", email);
        ctx.put("Properties.emailAddress", email);
        ctx.put("Properties.EmailMember", email);
        ctx.put("Properties.emailMember", email);
        ctx.put("Properties.GuestMemberEmail", email);
        ctx.put("Properties.guestMemberEmail", email);
        ctx.put("Properties.GeneratedEmail", email);
        ctx.put("Properties.generatedEmail", email);
        ctx.put("Properties.GeneratedemailAddress", email);
        ctx.put("Properties.generatedemailAddress", email);
        // Phone variants
        ctx.put("Properties.Phone", phone);
        ctx.put("Properties.phone", phone);
        ctx.put("Properties.PhoneNumber", phone);
        ctx.put("Properties.phoneNumber", phone);
        // Domain variants (bare + embedded-in-email + web-facing)
        ctx.put("Properties.Domain", domain);
        ctx.put("Properties.domain", domain);
        ctx.put("Properties.WebsiteDomain", domain);
        ctx.put("Properties.Websitedomain", domain);
        ctx.put("Properties.websiteDomain", domain);
        ctx.put("Properties.websitedomain", domain);
        ctx.put("Properties.weburl", domain);
        ctx.put("Properties.Weburl", domain);
        // "Hardcoded" domain/email variants -- named "hardcoded" in SoapUI
        // because the author froze them as static test-case Properties, BUT
        // some request bodies mix them with the regen'd Email/websiteDomain
        // in the same JSON (e.g., ownerEmailAddress=${{Properties#Email}},
        // emailDomains=[${{Properties#Hardcodeddomain}}]). If we regen one
        // side and leave the other at its static value, Hilton stg rejects
        // with 400 "Email address domain must match an allowed domain
        // within program account". Keep the whole domain cluster coherent.
        //
        // username1 / username2: SoapUI author used the EMAIL LOCAL-PART
        // (`9ory0xrak` in `9ory0xrak@dpptd.com`) as a stand-alone identity
        // in later request bodies. If we regen Email to a fresh local-part
        // but leave username1 / username2 at the static frozen value,
        // downstream steps that expect (localPart + domain) to reconstruct
        // the same email get a stranger. Derive both from the FRESH local
        // part so email = "<localPart>@<domain>" AND username1 = localPart
        // stay identity-consistent across the whole test method.
        //
        // updatedemail: some flows mutate the primary email and expect the
        // update to carry the fresh identity; keep it on the same domain
        // cluster too.
        String localPart = FakeData.username();
        String hcEmail = localPart + "@" + domain;
        // Bug D++ fix: only write to Hardcodeddomain/hardcodedemail when we
        // actually got a FROZEN value from ctx. When we fell back to a
        // random domain (empty ctx path -- typically the very first regen
        // during test setup, before the main @Test method's CSV
        // putIfNonEmpty seeding has run), leaving these keys ABSENT lets
        // the subsequent putIfNonEmpty (uses putIfAbsent under the hood)
        // successfully seed the CSV value like "vjuum.com" -- which the
        // NEXT regen call reads as `frozen` and preserves. Prior behavior
        // wrote a random domain to Hardcodeddomain here, poisoning the
        // key so putIfAbsent no-op'd and the CSV value never landed.
        // username1 / username2 / updatedemail follow the same rule --
        // they're the coherent-cluster siblings; if we have no frozen
        // anchor we shouldn't fabricate them either.
        if (usingFrozenDomain) {{
            ctx.put("Properties.hardcodedemail", hcEmail);
            ctx.put("Properties.Hardcodedemail", hcEmail);
            ctx.put("Properties.Hardcodeddomain", domain);
            ctx.put("Properties.hardcodeddomain", domain);
        }}
        ctx.put("Properties.username1", localPart);
        ctx.put("Properties.Username1", localPart);
        ctx.put("Properties.username2", localPart);
        // NOTE: Username2 (capital U) is already claimed by the top-of-
        // block username variants; do NOT overwrite it here.
        String updatedEmail = "bh" + localPart + "jff@" + domain;
        if (usingFrozenDomain) {{
            ctx.put("Properties.updatedemail", updatedEmail);
            ctx.put("Properties.Updatedemail", updatedEmail);
            ctx.put("Properties.updatedmailAddress", updatedEmail);
            ctx.put("Properties.UpdatedmailAddress", updatedEmail);
        }}
        // hhonorsNumber variants
        ctx.put("Properties.hhonorsNumber", hhon);
        ctx.put("Properties.HhonorsNumber", hhon);
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
        String tokenBase = Config.get("api_config.token_end_point", "");
        String tokenRoute = Config.get("api_config.token_route", "");
        // Landmine: if BOTH keys are empty, refuse to POST creds against
        // baseUrl -- doing so silently uploaded the client_id/secret to
        // the API root, which returns 404/415 and leaves the accessToken
        // empty. That failure mode is easy to blame on the target API
        // when the real cause is missing config.
        if (tokenBase.isEmpty() && tokenRoute.isEmpty()) {{
            LOG.warn("AuthHelper: neither `api_config.token_end_point` nor "
                    + "`api_config.token_route` is set in program_configuration.json "
                    + "-- refusing to POST client_id/client_secret to baseUrl "
                    + "(would exfiltrate creds to the wrong endpoint). Set at "
                    + "least one and re-run.");
            return;
        }}
        if (tokenBase.isEmpty()) tokenBase = Config.baseUrl();
        String tokenUrl;
        if (tokenRoute.isEmpty()) {{
            tokenUrl = tokenBase;
        }} else if (tokenRoute.startsWith("http://") || tokenRoute.startsWith("https://")) {{
            // Tier 2 audit fix #3: SoapUI exports commonly stash the entire
            // token URL in `token_route` (a habit from SoapUI where "token
            // endpoint" and "token route" are the same property). Detect
            // the full-URL shape and use it verbatim -- concatenating with
            // tokenBase would produce `<baseUrl>/https://...` garbage.
            tokenUrl = tokenRoute;
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
            String token = com.ak.api.rest.utilities.RestUtilities.safeJsonExtract(resp, "access_token");
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
            String headerLine = readLogicalCsvRow(br);
            if (headerLine == null) {{
                throw new IllegalStateException("PerMethodCsvDataProvider: empty CSV " + resourcePath);
            }}
            // Strip UTF-8 BOM (﻿) if present. Excel + Notepad on
            // Windows save CSVs as UTF-8-with-BOM by default; without
            // this strip the first header cell becomes "﻿<name>"
            // and every `row.get("<name>")` returns null, silently
            // routing tests to fallback defaults with no error.
            if (headerLine.length() > 0 && headerLine.charAt(0) == '\\uFEFF') {{
                headerLine = headerLine.substring(1);
            }}
            String[] header = splitCsvLine(headerLine);
            String line;
            int rowIndex = 0;
            org.slf4j.Logger log = org.slf4j.LoggerFactory.getLogger(PerMethodCsvDataProvider.class);
            while ((line = readLogicalCsvRow(br)) != null) {{
                rowIndex++;
                if (line.isEmpty()) continue;
                String[] cells = splitCsvLine(line);
                Map<String, String> row = new LinkedHashMap<>();
                for (int i = 0; i < header.length; i++) {{
                    row.put(header[i], i < cells.length ? cells[i] : "");
                }}
                // Tier 1 audit fix #6: warn -- do NOT throw -- when a row
                // has MORE cells than the header. Silent drop was the
                // previous behaviour and hid the common authoring mistake
                // of adding a value column without adding the header
                // above it (the value ended up as an unattributed trailing
                // cell, invisible to the test).
                if (cells.length > header.length) {{
                    StringBuilder dropped = new StringBuilder();
                    for (int i = header.length; i < cells.length; i++) {{
                        if (i > header.length) dropped.append(" | ");
                        dropped.append(cells[i]);
                    }}
                    log.warn("PerMethodCsvDataProvider: row {{}} in {{}} has {{}} cells but header has {{}} columns; "
                            + "extra cells DROPPED: [{{}}]. Add matching header column(s) or remove trailing cell(s).",
                            rowIndex, resourcePath, cells.length, header.length, dropped);
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
     * Read ONE logical CSV row -- keeps reading physical lines and
     * joining them with newline until the accumulated content has all
     * quoted cells closed. Handles the ra_converter output where
     * request-body CSV cells contain embedded newlines (pretty-printed
     * JSON): without this, {{@link BufferedReader#readLine}} splits a
     * single logical row into N physical rows, the downstream loop sees
     * N fragmentary rows (mostly empty), TestNG fires the @Test N times
     * against near-duplicate params -- inflating the ProgressLogListener
     * ATTEMPTS counter and causing "attempt 15" banners for a method
     * that should have run 1-2 CSV rows.
     *
     * @return the fully-assembled logical row, or {{@code null}} at EOF
     */
    private static String readLogicalCsvRow(BufferedReader br) throws java.io.IOException {{
        String first = br.readLine();
        if (first == null) return null;
        StringBuilder buf = new StringBuilder(first);
        // Tier 3 audit fix #11: hard cap on physical lines per logical
        // row. Without this, a source CSV with an unclosed `"` (easy
        // authoring mistake: pasted JSON with a stray quote) reads to
        // EOF into one StringBuilder -- OOMs on multi-MB CSVs and TestNG
        // then reports "0 rows" with no diagnostic. 200 is generous for
        // pretty-printed JSON payloads but small enough to abort quickly
        // on malformed input.
        final int MAX_LINES = 200;
        int linesRead = 1;
        while (!balancedQuotes(buf)) {{
            if (linesRead >= MAX_LINES) {{
                String preview = buf.length() > 200 ? buf.substring(0, 200) + "..." : buf.toString();
                throw new java.io.IOException(
                        "CSV row exceeded " + MAX_LINES + " physical lines without "
                        + "closing a quoted cell -- probable unclosed `\\"` in the "
                        + "source. Row starts: " + preview);
            }}
            String next = br.readLine();
            if (next == null) break;
            buf.append('\\n').append(next);
            linesRead++;
        }}
        return buf.toString();
    }}

    /**
     * True iff every {{@code "}} in {{@code s}} that opens a quoted field
     * has a matching close-quote (per the strict cell-start rule used
     * by {{@link #splitCsvLine}}). Used to decide whether the logical
     * row is complete after {{@code readLine}}.
     */
    private static boolean balancedQuotes(CharSequence s) {{
        boolean inQuotes = false;
        boolean atCellStart = true;
        for (int i = 0; i < s.length(); i++) {{
            char c = s.charAt(i);
            if (inQuotes) {{
                if (c == '"') {{
                    if (i + 1 < s.length() && s.charAt(i + 1) == '"') {{
                        i++;
                    }} else {{
                        inQuotes = false;
                        atCellStart = false;
                    }}
                }}
            }} else {{
                if (c == ',') {{
                    atCellStart = true;
                }} else if (c == '"' && atCellStart) {{
                    inQuotes = true;
                }} else {{
                    atCellStart = false;
                }}
            }}
        }}
        return !inQuotes;
    }}

    /**
     * Strict RFC-4180-ish CSV splitter: honors double-quoted fields
     * (with embedded commas, newlines, and "" escaped quotes).
     * Multi-line quoted fields must be pre-assembled by
     * {{@link #readLogicalCsvRow}}; this method operates on a single
     * logical row (embedded newlines within cells are preserved).
     *
     * <p>Enter-quotes-only-at-cell-start: a stray {{@code "}} mid-cell
     * is treated as literal so a malformed cell doesn't shift the
     * remaining cells left.</p>
     */
    private static String[] splitCsvLine(String line) {{
        List<String> out = new ArrayList<>();
        StringBuilder cur = new StringBuilder();
        boolean inQuotes = false;
        boolean atCellStart = true;
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
                    atCellStart = true;
                    continue;
                }} else if (c == '"' && atCellStart) {{
                    inQuotes = true;
                }} else {{
                    cur.append(c);
                }}
                atCellStart = false;
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
    // ${{X}} -- key may include dots + underscores + digits + dashes + `#`.
    // The `#` accommodates SoapUI-style scoped refs (`${{step#field}}`,
    // `${{Properties#Domain}}`) that reach us verbatim from imported test
    // data. Without `#` in the char class the entire token was a literal
    // non-match, so cells like `expected_..._domain=${{Properties#Domain}}`
    // stayed as raw strings and failed later assertions with confusing
    // "expected `${{Properties#Domain}}` but was `<real value>`" diffs.
    private static final Pattern DOLLAR_REF =
        Pattern.compile("\\\\$\\\\{{([A-Za-z_][A-Za-z0-9_.#-]*)\\\\}}");

    /** Run pass 1, pass 2, then pass 3. Safe to call on already-resolved
     *  text (idempotent -- no faker tokens, ${{}}, #X#, or @X@ refs to match). */
    public static String resolveAll(String text, Map<String, String> ctx) {{
        if (text == null || text.isEmpty()) return text;
        String phase1 = resolveFakerTokens(text);
        String phase2 = resolveDollarRefs(phase1, ctx);
        return resolveHashAtRefs(phase2, ctx);
    }}

    /**
     * Pass 3: framework-native {{@code #Key#}} and {{@code @Key@}} refs.
     * mapJsonValues handles these for body templates at HTTP-call time,
     * but CSV cells that reach OTHER consumers (assertion emit reads
     * {{@code row.getOrDefault(col, ...)}} verbatim; URL path
     * substitution calls {{@code TestSupport.ctxGet}}) never went
     * through mapJsonValues -- literal {{@code #Properties_Domain#}}
     * in an assertion cell would compare against the actual response
     * value and mismatch every time. Bounded at 5 iterations. No-op
     * when text has no {{@code #}} or {{@code @}} chars.
     */
    public static String resolveHashAtRefs(String text, Map<String, String> ctx) {{
        if (text == null || text.isEmpty()) return text;
        if (text.indexOf('#') < 0 && text.indexOf('@') < 0) return text;
        String cur = text;
        for (int i = 0; i < 5; i++) {{
            String next = replaceIfKnown(cur, HASH_REF, ctx);
            next = replaceIfKnown(next, AT_REF, ctx);
            if (next.equals(cur)) return next;
            cur = next;
        }}
        return cur;
    }}

    /**
     * Iterate matches of {{@code pattern}} in {{@code text}}; substitute
     * only when ctx has a NON-EMPTY value for the key. Placeholders
     * whose key isn't in ctx yet are LEFT UNCHANGED so a later
     * consumer (mapJsonValues, ctxGet) can still resolve them once
     * ctx is populated (e.g. Groovy DataGenInput fires after resolveRow).
     * Eagerly replacing with "" wipes the placeholder and permanently
     * breaks later resolution.
     */
    private static String replaceIfKnown(String text, Pattern pattern,
                                         Map<String, String> ctx) {{
        Matcher m = pattern.matcher(text);
        StringBuilder out = new StringBuilder();
        while (m.find()) {{
            String rawKey = m.group(1);
            String v = null;
            if (ctx != null) {{
                v = ctx.get(rawKey);
                if (v == null || v.isEmpty()) v = ctx.get(rawKey.replace('_', '.'));
                if (v == null || v.isEmpty()) v = ctx.get(rawKey.replace('.', '_'));
            }}
            if (v == null || v.isEmpty()) {{
                m.appendReplacement(out, Matcher.quoteReplacement(m.group()));
            }} else {{
                m.appendReplacement(out, Matcher.quoteReplacement(v));
            }}
        }}
        m.appendTail(out);
        return out.toString();
    }}

    private static final Pattern HASH_REF =
            Pattern.compile("#([A-Za-z0-9_.-]+)#");
    private static final Pattern AT_REF =
            Pattern.compile("@([A-Za-z0-9_.-]+)@");

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
            // Null-guard: an unrecognized faker key OR an internal null
            // return would otherwise crash Matcher.quoteReplacement with
            // an NPE. Leave the literal <<X>> in place so the caller
            // (or a later stage) can see what wasn't resolved.
            if (value == null) {{
                value = m.group();
            }}
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
            String rawKey = m.group(1);
            // SoapUI-style scoped refs (`Properties#Domain`, `step#field`)
            // come through with `#` separators. ctx uses `.` -- try both
            // forms so a value stored under `Properties.Domain` resolves
            // whether the template writes `${{Properties#Domain}}` or
            // `${{Properties.Domain}}`.
            String value = null;
            if (ctx != null) {{
                value = ctx.get(rawKey);
                if ((value == null || value.isEmpty()) && rawKey.indexOf('#') >= 0) {{
                    value = ctx.get(rawKey.replace('#', '.'));
                }}
                if ((value == null || value.isEmpty()) && rawKey.indexOf('#') >= 0) {{
                    value = ctx.get(rawKey.replace('#', '_'));
                }}
            }}
            if (value == null || value.isEmpty()) value = autoGenerate(rawKey, ctx);
            if (value == null || value.isEmpty()) {{
                // Unresolved OR resolved-to-empty -- leave the literal
                // ${{X}} in place so it's visible as a marker in the
                // request log rather than silently emitting `""` into
                // the JSON body (which servers reject with "must match
                // regex" and no framework signal).
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
            # Cluster-union of assertions REMOVED (per-case emit only).
            # Kept the field as None so _render_rest_step_body's read
            # (which now always uses `step.assertions`) can be audited
            # for any accidental re-introduction. The
            # `_union_cluster_asserts` method is dead but kept in
            # source for now with a deprecation note so a future
            # reviewer sees the design decision.
            self._cluster_asserts_by_pos = None
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
    // LinkedHashMap so putWithAliases in TestSupport.mergedRow iterates
    // ctx in INSERTION order. Case-flip aliases rely on the ctx.put site
    // (runtime-generated value) firing AFTER the row-time entry so its
    // aliases overwrite the older CSV value. HashMap's arbitrary iteration
    // order broke that guarantee.
    // Wrapped in Collections.synchronizedMap so a Suites/*.xml that
    // ever flips to parallel="methods" (multiple threads sharing one
    // test-class instance, each running its own @BeforeMethod) cannot
    // corrupt ctx with concurrent put/clear. Under the current
    // parallel="classes" config, one instance = one thread, so the
    // sync wrapper has zero contention -- defense-in-depth for a
    // config change we would otherwise silently mis-behave on. Same
    // landmine class as BaseApiTest.holders (which was defused in the
    // Tier 1 audit sweep).
    private final Map<String, String> ctx = java.util.Collections.synchronizedMap(new java.util.LinkedHashMap<>());

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
        # Populated so runtime_skips.csv rows can name the containing @Test
        # method -- without this, every runtime_skips row carries "?" and
        # the audit can't jump you to the code.
        self._current_method = method_name
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
            # Allure metadata: after row is finalized. `final` capture so
            # the lambda that updates the Allure test-case model can
            # reference it (Java's effectively-final rule -- `row` gets
            # reassigned above, so it's not effectively final). Also set
            # the display name to the SoapUI case name so multi-row
            # methods show one Allure node per row.
            'final java.util.Map<String, String> __rowForAllure = row;',
            'final String __testCaseIdForAllure = testCaseId;',
            'io.qameta.allure.Allure.getLifecycle().updateTestCase(tc -> {',
            '    tc.setName(__testCaseIdForAllure);',
            '    if (__rowForAllure != null) {',
            '        for (java.util.Map.Entry<String, String> __e : __rowForAllure.entrySet()) {',
            '            String __v = __e.getValue();',
            '            if (__v == null || __v.isEmpty()) continue;',
            '            String __d = __v.length() > 120 ? __v.substring(0, 120) + "..." : __v;',
            '            tc.getParameters().add(new io.qameta.allure.model.Parameter()'
            '.setName(__e.getKey()).setValue(__d));',
            '        }',
            '    }',
            '});',
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
        # ---- Auth-hoist: if the case has an OAuth-token-fetch REST step
        # that the SoapUI author placed LATE in the sequence (not in the
        # first 2 REST steps), every prior REST step 401s because ctx.
        # accessToken is empty. Hoist the token step + its immediately-
        # following "Token" Groovy step (which extracts access_token into
        # ctx) to the FRONT of the run-order so auth-dependent steps
        # succeed. Only applies when there's no SetupHelper.flow prefix
        # (skip_count == 0) and no prefix-merge stop-markers (which would
        # break under positional reorder).
        steps_to_render = case.steps[skip_count:]
        # ---- Preflight lint: proactively detect known bug-patterns in
        # this case's step sequence and log them to the audit BEFORE
        # emitting Java. Categories:
        #   token-hoist-applied  = auto-reordered token step to position 0
        #   token-injected       = synthesized token step (case had none)
        #   jdbc-mutation-skip   = case will throw SkipException at runtime
        #   unresolved-project-ref = Groovy references #Project#Foo that
        #                            we cannot translate (needs manual)
        #   unresolved-step-ref  = ${step#Response#field} reaches outside
        #                          the emit method scope (compile ok, run
        #                          ok but the value will be empty)
        # Findings are cheap to add; author scans preflight.md before
        # running to know which categories of failure to expect.
        self._preflight_scan_case(case, steps_to_render, skip_count,
                                    emit_stop_checks)
        if skip_count == 0 and not emit_stop_checks:
            pre_hoist_order = list(steps_to_render)
            steps_to_render = _hoist_token_fetch_steps(steps_to_render)
            if steps_to_render is not pre_hoist_order and steps_to_render != pre_hoist_order:
                self.ledger.add_preflight_finding(
                    "MEDIUM", "token-hoist-applied", case.name,
                    "SoapUI author placed the tokenRequest step later "
                    "than position 2 in the REST-step order. Emitter "
                    "hoisted it to position 0 so earlier REST calls do "
                    "not 401. Original SoapUI intent preserved (step "
                    "still fires; response still populates ctx).")
            # ---- Auth-inject: if the case has NO token-fetch step at all
            # (i.e. SoapUI author relied on cross-case #Project#Token
            # persisted state), prepend a synthetic tokenRequest step
            # cloned from another case in the same suite. Framework has no
            # cross-case project state, so without this every REST step
            # 401s. Only fires when a canonical pair was found for the
            # suite (skipped for edge cases with no token step anywhere).
            has_own_token = any(
                isinstance(s, RestStep) and _is_token_fetch_step(s)
                for s in steps_to_render)
            canonical = getattr(self, "_canonical_token_pair", (None, None))
            if not has_own_token and canonical and canonical[0] is not None:
                injected = [canonical[0]]
                if canonical[1] is not None:
                    injected.append(canonical[1])
                steps_to_render = injected + steps_to_render
                # Cross-case template-registry copy: the injected step
                # object is CLONED from another case, so its template
                # path was registered under (canonical_case, step_name),
                # NOT (this case, step_name). Without copying the entry,
                # _render_rest_step_body's lookup for the classpath
                # (self._template_path_by_step.get((self._current_case,
                # step.step_name))) misses, and the emitter falls back
                # to a flat classpath `templates/<suite>/<step>.json`
                # that no writer ever creates -- runtime dies with
                # `IllegalArgumentException: Template not found on
                # classpath`. Same bug class as the same-body dedup
                # mismatch fixed in _emit_tier1_for. Copy every injected
                # step's registration (path + merged cells) under this
                # case's name so the lookup hits the ALREADY-WRITTEN file.
                for inj in injected:
                    if not isinstance(inj, RestStep):
                        continue
                    for (src_case, src_step), path in list(
                            self._template_path_by_step.items()):
                        if src_step == inj.step_name and src_case != case.name:
                            self._template_path_by_step.setdefault(
                                (case.name, inj.step_name), path)
                            merged = getattr(self, "_merged_template_cells", {})
                            cells = merged.get((src_case, inj.step_name))
                            if cells is not None:
                                merged.setdefault(
                                    (case.name, inj.step_name), cells)
                            break
                self.ledger.add_preflight_finding(
                    "HIGH", "token-injected", case.name,
                    "Case had NO tokenRequest step of its own -- SoapUI "
                    "author relied on cross-case #Project#Token state "
                    "that the framework does not replicate. Emitter "
                    "injected a synthetic tokenRequest (cloned from "
                    "another case in the same suite) at position 0.")
        for step in steps_to_render:
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
                        code_list = [c for c in re.split(r"[,\s]+", codes)
                                     if c and c.strip().lstrip("-").isdigit()]
                        first_code = code_list[0] if code_list else "200"
                        # Bug C fix: preserve multi-code intent in the
                        # author-visible `expected` column with `|`-sep
                        # so a CSV editor can see the full accept-list.
                        # Runtime path still takes first_code for the
                        # single-code assertion; multi-code assertion
                        # (validCodes_ Set) is emitted separately at
                        # the assertion-emit site (see _emit_assertion
                        # "Valid HTTP Status Codes" branch).
                        combined = ("|".join(code_list)
                                    if len(code_list) > 1
                                    else first_code)
                        expected_bits.append(f"statusCode:{combined}")
                        if not derived_status:
                            derived_status = first_code
            expected_str = ";".join(expected_bits)

            assert_cells = [
                _csv_cell(assert_vals_per_case[col][case_idx], col)
                for col in assert_cols_order
            ]
            merged_tpl_cells = [
                _csv_cell(merged_tpl_vals[col][case_idx], col)
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
                _csv_cell(migrated_vals_per_case[col][case_idx], col)
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

    _HARDCODED_ID_FIELDS = (
        # Field-name patterns where a bare 6+ digit value in the SoapUI
        # request body is almost certainly a stale hardcoded Hilton
        # resource id (author baked in an id that existed on the day
        # the test was authored, expired years ago). Rewriting these to
        # placeholders lets the runtime substitute whatever the current
        # test's upstream step (HHonorsEnroll response extract, Groovy
        # DataGenInput, PropertyTransfer) put into ctx -- even if that's
        # a fresh-random 9-digit from random_email_generator, it beats
        # a stale valid id that maps to some other account.
        "guestId", "guestID",
        "accountId", "accountID",
        "memberId", "memberID",
        "hhonorsNumber", "hHonorsNumber",
        "partnerAccountId", "partnerAccountID",
        "customerId", "userId",
    )

    @staticmethod
    def _placeholder_hardcoded_ids(body_text: str, media_type: str) -> tuple:
        """Rewrite hardcoded id-shaped values in known id fields inside
        a JSON request body to framework placeholders. Preserves JSON
        type: quoted-string ids use ``#Properties_<field>#`` (stays a
        string); bare-number ids use ``"@Properties_<field>@"`` (the
        runtime ``mapJsonValues`` P_AT_Q pattern strips the surrounding
        quotes AND substitutes the numeric value, yielding a valid JSON
        number). Only touches values that look like ids (6+ digits) so
        legitimate small numbers (``phoneCountry: 124``, ``postalCode:
        40515``, ``employeeCount: 0``) are untouched. Fields that
        already have a placeholder are untouched too.

        Returns (rewritten_text, count_of_replacements).
        """
        if not body_text:
            return body_text, 0
        mt = (media_type or "").lower()
        is_json = ("json" in mt or mt.endswith("+json")
                   or body_text.lstrip().startswith(("{", "[")))
        if not is_json:
            return body_text, 0
        n = 0
        out = body_text
        for field in Emitter._HARDCODED_ID_FIELDS:
            # Match `"<field>": "<6+ digits>"` OR `"<field>": <6+ digits>`
            # followed by a JSON delimiter. Group 2 = digits if quoted,
            # group 3 = digits if unquoted; exactly one of them matches
            # per call. Lookahead on `[,}\s\]]` prevents matching inside
            # an id like `"guestIdList": 1234567890` where the digits
            # actually continue a longer string.
            pattern = re.compile(
                r'"' + re.escape(field) + r'"\s*:\s*'
                r'(?:"(\d{6,})"|(\d{6,}))'
                r'(?=\s*[,}\]\s])'
            )
            def _repl(m, _field=field):
                nonlocal n
                digits = m.group(1) or m.group(2)
                # Bug A guard: skip rewrite when the value is a run of
                # identical digits (e.g. "8888888888", "9999999999999").
                # Author-picked "guaranteed non-existent" ids for negative
                # tests -- rewriting them defeats the test purpose.
                if digits and len(set(digits)) == 1:
                    return m.group(0)
                n += 1
                if m.group(1):  # quoted
                    return f'"{_field}": "#Properties_{_field}#"'
                # unquoted number: @X@ strips the added quotes at runtime
                return f'"{_field}": "@Properties_{_field}@"'
            out = pattern.sub(_repl, out)
        return out, n

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
                # Rewrite hardcoded id-shaped values in known id fields
                # (guestId / accountId / memberId / etc.) to framework
                # placeholders so runtime substitution uses the current
                # test's live ids (from HHonorsEnroll extract or
                # DataGenInput fallback) instead of stale ids the SoapUI
                # author hardcoded years ago (which now map to some
                # other Hilton account -> 404 / 405).
                translated, __n_ids = Emitter._placeholder_hardcoded_ids(
                    translated, step.media_type or "application/json")
                if __n_ids > 0:
                    self.ledger.add_preflight_finding(
                        "INFO", "hardcoded-id-rewritten", case.name,
                        f"REST step `{step.step_name}` request body had "
                        f"{__n_ids} hardcoded id-shaped value(s) "
                        f"rewritten to #Properties_<field># / "
                        f"@Properties_<field>@ so runtime substitution "
                        f"uses live ids instead of stale hardcoded ones.")
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
        every (case, step) to the same classpath -- the file the FIRST
        entry for that hash wrote. Prior version regenerated the
        classpath per-entry using the per-step sanitized name, so when
        two steps had the same body hash (e.g. `http_request_200_3` and
        `http_request_200_3 3`), the first step wrote a file and the
        second step registered its OWN filename in _template_path_by_step
        without writing -- runtime got IllegalArgumentException `Template
        not found on classpath: <second-step-name>_<hash>.json`.
        Uses the per-entry `ext` field so XML/form/plain bodies land as
        `.xml`/`.form`/`.txt` instead of `.json`."""
        for e in entries:
            if e["hash"] in hash_to_path:
                # Same-body dedup: reuse the classpath the FIRST entry
                # with this hash wrote; the file already exists on disk.
                classpath = hash_to_path[e["hash"]]
            else:
                # First time we've seen this body -- write it and record
                # the classpath under the hash for future dedup hits.
                classpath_dir = f"templates/{self.suite_name}/{e['bucket']}/"
                ext = e.get("ext", "json")
                classpath_file = f"{sanitize_identifier(e['step']).lower()}_{e['hash']}.{ext}"
                classpath = classpath_dir + classpath_file
                self._write(f"src/main/resources/{classpath}", e["translated"])
                hash_to_path[e["hash"]] = classpath
            self._template_path_by_step[(e["case"], e["step"])] = classpath

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
<!-- configfailurepolicy="continue" so an @AfterMethod failure (typically
     softAssert.assertAll() throwing on accumulated soft-assertion failures)
     doesn't cause TestNG to SKIP every subsequent @Test method in the same
     class. Prior default ("skip") turned a class with 4 @Test methods and
     one assertion failure into 1 fail + 3 skipped -- misleading because the
     remaining tests would have provided independent signal against the same
     API. With "continue", each @Test runs on its own merit; soft failures
     stay per-scenario. -->
<suite name="{suite_display}" parallel="classes" thread-count="3" configfailurepolicy="continue">

    <parameter name="testSuite" value="{variant.lower()}"/>

    <listeners>
        <!-- Allure is auto-loaded via ServiceLoader (allure-testng SPI file);
             Allure attachments for every REST call come from the
             AllureRestAssured filter wired in BaseApiTest.bootstrapRestAssured. -->
        <!-- Progress banner listener: emitted by ra_converter (see
             emit_progress_listener). Prints class + method + timing to
             the mvn console so parallel classes are attributable. -->
        <listener class-name="com.ak.api.reporting.ProgressLogListener"/>
        <!-- Suite-level counters + reset (src/test/java/com/ak/api/reporting/
             TestSuiteListener.java). Advances pass/fail counters via TestNG
             callbacks so a test that throws before reaching assertAll still
             counts as failed. -->
        <listener class-name="com.ak.api.reporting.TestSuiteListener"/>
        <!-- Per-test flow logging (TestCaseLogListener). Emits banner-separated
             per-test log files under logs/<Class>.log listing every REST
             exchange captured via RestAssuredRecordingFilter. -->
        <listener class-name="com.ak.api.reporting.TestCaseLogListener"/>
        <!-- ExtentReports HTML writer. Reads ReportBuffer (populated by
             RestAssuredRecordingFilter) at test end and attaches every
             request/response body to the ExtentTest node with pretty-
             printed JSON. Output: extent-reports/<timestamp>-Extent.html. -->
        <listener class-name="com.ak.api.reporting.ExtentReportListener"/>
        <!-- Xray results pusher (JIRA Xray integration). No-op when
             xray.enabled=false in application.properties (default). -->
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
    // Per-method attempt counter: keyed by class#method (no hashcode).
    // Increments on every onTestStart; a terminal PASSED/FAILED clears
    // the entry. RetryAnalyzer-driven retries share the same key so we
    // can suppress intermediate STARTED lines and annotate the terminal
    // outcome with "[after N attempts]".
    private static final ConcurrentHashMap<String, java.util.concurrent.atomic.AtomicInteger> ATTEMPTS
        = new ConcurrentHashMap<>();

    private String key(ITestResult r) {{
        return r.getTestClass().getRealClass().getSimpleName()
                + "#" + r.getMethod().getMethodName() + "@" + r.hashCode();
    }}

    /**
     * Per-INVOCATION key. Includes a hash of the data-provider parameters
     * so different CSV rows of the same @Test method own different
     * attempt counters. Without the params hash, `[attempt N]` inflated
     * with every row for data-driven tests -- a method with 20 rows
     * showed `[attempt 20]` on the last row even though no retry ever
     * happened. Only true retries (RetryAnalyzer re-invoking with the
     * same params) share a counter now.
     */
    private String methodKey(ITestResult r) {{
        return r.getTestClass().getRealClass().getName()
                + "#" + r.getMethod().getMethodName()
                + "@" + java.util.Arrays.deepHashCode(r.getParameters());
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

    private int currentAttempt(ITestResult r) {{
        java.util.concurrent.atomic.AtomicInteger c = ATTEMPTS.get(methodKey(r));
        return c == null ? 1 : c.get();
    }}

    private void clearAttempts(ITestResult r) {{
        ATTEMPTS.remove(methodKey(r));
    }}

    // Simple approach: log STARTED once EVER per (class, method) --
    // never clear the seen-set. Multi-row data-driven methods thus show
    // ONE STARTED line + N terminal outcomes (one per row). Retries
    // don't re-log STARTED because the key is already seen. Cleaner and
    // resistant to TestNG's flaky wasRetried() semantics.
    private static final java.util.Set<String> STARTED_LOGGED =
        java.util.Collections.newSetFromMap(new ConcurrentHashMap<>());

    /** Started-set key = class+method only (no params). One STARTED banner
     *  per @Test method for the WHOLE run, regardless of rows / retries. */
    private String startedKey(ITestResult r) {{
        return r.getTestClass().getRealClass().getName()
                + "#" + r.getMethod().getMethodName();
    }}

    @Override
    public void onTestStart(ITestResult r) {{
        STARTS.put(key(r), System.currentTimeMillis());
        ATTEMPTS.computeIfAbsent(methodKey(r),
                k -> new java.util.concurrent.atomic.AtomicInteger(0))
                .incrementAndGet();
        if (STARTED_LOGGED.add(startedKey(r))) {{
            LOG.info("[TEST] STARTED  {{}}  (thread={{}})",
                    label(r), Thread.currentThread().getName());
        }}
    }}

    @Override
    public void onTestSuccess(ITestResult r) {{
        int attempt = currentAttempt(r);
        if (attempt > 1) {{
            LOG.info("[TEST] PASSED   {{}}  ({{}}ms) [attempt {{}}]",
                    label(r), elapsedMs(r), attempt);
        }} else {{
            LOG.info("[TEST] PASSED   {{}}  ({{}}ms)", label(r), elapsedMs(r));
        }}
    }}

    @Override
    public void onTestFailure(ITestResult r) {{
        int attempt = currentAttempt(r);
        Throwable t = r.getThrowable();
        String msg = (t == null) ? "(no throwable)" : t.getClass().getSimpleName()
                + ": " + (t.getMessage() == null ? "" : t.getMessage());
        // Log every FAILED at WARN with attempt count. Users see the
        // retry story in the [Retry] lines RetryAnalyzer prints AND in
        // the attempt=N here. Silencing intermediate failures based on
        // wasRetried() is unreliable in TestNG (see prior bug), so we
        // log all failures and let the reader spot the pattern.
        LOG.warn("[TEST] FAILED   {{}}  ({{}}ms) [attempt {{}}] -- {{}}",
                label(r), elapsedMs(r), attempt, msg);
    }}

    @Override
    public void onTestSkipped(ITestResult r) {{
        int attempt = currentAttempt(r);
        // Skips triggered by RetryAnalyzer's failure-then-retry cycle
        // show 0ms elapsed. Suppress those; the surrounding [Retry] and
        // FAILED lines already tell the story.
        long elapsed = elapsedMs(r);
        if (elapsed < 5) {{
            return;
        }}
        LOG.info("[TEST] SKIPPED  {{}}  ({{}}ms) [attempt {{}}]",
                label(r), elapsed, attempt);
    }}

    @Override
    public void onStart(ITestContext context) {{
        // Reset per-<test> state so a long-lived JVM (Surefire fork reuse
        // across two <test> blocks, or `mvn -Dtest=Foo test surefire:test`
        // rerun in the same fork) starts fresh: STARTED banners re-fire
        // per <test> block, and stale attempt counters from a prior block
        // don't leak into the new one's [attempt N] annotations.
        STARTED_LOGGED.clear();
        ATTEMPTS.clear();
        STARTS.clear();
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

    # Normalize hardcoded ids baked into resource_path segments BEFORE
    # collect_shared_rest_steps builds the (op, path) map, or the
    # client method signature (frozen off the raw literal path) won't
    # match the normalized template that call sites use downstream.
    _path_id_rewrites = normalize_hardcoded_path_ids_in_place(cases_in_scope)
    if _path_id_rewrites:
        print(f"[ra_converter] normalized {len(_path_id_rewrites)} hardcoded "
              f"path id(s) to Properties refs across "
              f"{len(set(r[0] for r in _path_id_rewrites))} case(s)")
    # Translate embedded ${...} refs in resource_paths to #X# form so
    # runtime PlaceholderResolver.resolveAll (wrapped around the URL
    # builder in _render_rest_step_body) can resolve them against ctx.
    # Also runs BEFORE collect_shared_rest_steps so client grouping
    # sees the normalized path shape.
    _path_dollar_count = normalize_dollar_refs_in_resource_paths(cases_in_scope)
    if _path_dollar_count:
        print(f"[ra_converter] translated ${{...}} refs in {_path_dollar_count} "
              f"resource_path(s) to #X# placeholders")

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
        # Suite-wide canonical (tokenRequest, Token-Groovy-extractor) pair.
        # Used by _render_test_method_v2 to inject a token-fetch preamble
        # for cases that have NO tokenRequest step of their own -- those
        # cases relied on SoapUI's cross-case #Project#Token state (a token
        # persisted in project scope from a PRIOR case's run) which the
        # framework does not replicate; without a synthetic prepend they
        # 401 on every REST call. Pair reused verbatim from another case in
        # the same suite so the JSON body / headers / client method stay
        # identical to the SoapUI author's intent.
        emitter._canonical_token_pair = _find_canonical_token_pair(sui_cases)
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
