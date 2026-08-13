"""Deterministic SoapUI-Groovy -> framework-Java translator.

Recognizes the small set of Groovy patterns that dominate SoapUI test scripts
(response extraction, property assignment, random data generation, JDBC,
logging) and emits equivalent calls into the ApiAutomationRestAssured
framework. Anything that doesn't match a pattern is preserved as a comment
plus a runnable no-op stub, so a compile never breaks on unrecognized
Groovy -- the worst case is a runtime warning + a test that fails cleanly
if the untranslated block was load-bearing.

Public API:
    translate(script, response_var_by_step, step_name_hint=""):
        Returns (lines, meta) tuple.
        - lines: Java code lines suitable for inclusion in a test method body
        - meta:  {
              'patterns_matched': list[str] of recognizer names that fired,
              'coverage': 'FULL' | 'PARTIAL' | 'STUB',
              'preview': first-80-char summary for audit,
          }
        `response_var_by_step` maps SoapUI step names to the Java variable
        that holds the Response for that step, so
        `testSteps["tokenRequest"].testRequest.response` becomes
        `tokenRequestRes.asString()`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class Pattern:
    """One recognizer: regex + emitter that returns Java lines."""
    name: str
    regex: re.Pattern
    emit: Callable[[re.Match, dict], list[str]]


# ---------------------------------------------------------------------------
# Small emit helpers
# ---------------------------------------------------------------------------

def _resp_var_for(step_name: str, ctx: dict) -> str:
    """Lookup the Response variable that holds a given step's response."""
    m = ctx.get("response_var_by_step", {})
    return m.get(step_name, f"/* unknown_response_for_{step_name} */ null")


def _ctx_key(step_name: str, field: str) -> str:
    """Framework's convention for ctx keys: stepName.field."""
    return f"{step_name}.{field}"


def _trace_groovy_defs(script: str) -> dict:
    """Walk `def X = ...` bindings and label what each local var represents.

    Returns dict[var_name -> info-dict]. `info-dict` has a `kind` field:

    - `response_obj`: var holds a SoapUI response object for `source_step`
    - `response_str`: var holds the response body (contentAsString /
      getPropertyValue("response")) of `source_step`
    - `parsed_json`: var is a JsonSlurper.parseText result of a response_str
      from `source_step`
    - `extracted`: var is a field/subpath extraction from a `parsed_json`
      binding; carries `source_step` + `jsonpath`

    This is what lets us translate the classic Bearer-token block:

        def resp    = testRunner.testCase.testSteps["tokenRequest"]
                        .testRequest.response          // response_obj
        def body    = resp.contentAsString              // response_str
        def parsed  = new JsonSlurper().parseText(body) // parsed_json
        def token   = parsed.access_token               // extracted (path=access_token)
        def bearer  = "Bearer " + token                 // (handled by _emit_def_publications)
    """
    b: dict = {}
    # response_obj: testRunner.testCase.testSteps["X"].testRequest.response
    for m in re.finditer(
        r'def\s+(\w+)\s*=\s*testRunner\.testCase\.testSteps\[["\']([^"\']+)["\']\]\.testRequest\.response',
        script):
        b[m.group(1)] = {"kind": "response_obj", "source_step": m.group(2)}
    # step_ref: def X = testRunner.testCase.getTestStepByName("Y")
    for m in re.finditer(
        r'def\s+(\w+)\s*=\s*testRunner\.testCase\.getTestStepByName\(["\']([^"\']+)["\']\)',
        script):
        b[m.group(1)] = {"kind": "step_ref", "source_step": m.group(2)}
    # response_str via .contentAsString on a known response_obj
    for m in re.finditer(r'def\s+(\w+)\s*=\s*(\w+)\.contentAsString', script):
        parent = b.get(m.group(2))
        if parent:
            b[m.group(1)] = {"kind": "response_str",
                              "source_step": parent.get("source_step")}
    # response_str via .getPropertyValue("response") on a known step_ref
    for m in re.finditer(
        r'def\s+(\w+)\s*=\s*(\w+)\.getPropertyValue\(["\']response["\']\)', script):
        parent = b.get(m.group(2))
        if parent:
            b[m.group(1)] = {"kind": "response_str",
                              "source_step": parent.get("source_step")}
    # parsed_json via JsonSlurper().parseText(<response_str_var>)
    for m in re.finditer(
        r'def\s+(\w+)\s*=\s*(?:new\s+)?[Jj]son[Ss]lurper\w*\s*(?:\(\))?\s*\.parseText\(\s*(\w+)\s*\)',
        script):
        parent = b.get(m.group(2))
        if parent:
            b[m.group(1)] = {"kind": "parsed_json",
                              "source_step": parent.get("source_step")}
    # extracted via <parsed_json_var>.field[.subfield ...]
    # Iterate multiple times because chains like `def x = parsed.a; def y = x`
    # aren't a common SoapUI pattern but we still want to handle simple depth.
    for _ in range(3):
        for m in re.finditer(r'def\s+(\w+)\s*=\s*(\w+)\.((?:\w+)(?:\.\w+)*)\s*(?:$|;|\r|\n)',
                              script + "\n", re.MULTILINE):
            new_var, parent_var, path = m.group(1), m.group(2), m.group(3)
            if new_var in b:
                continue
            parent = b.get(parent_var)
            if parent and parent.get("kind") == "parsed_json":
                b[new_var] = {"kind": "extracted",
                              "source_step": parent.get("source_step"),
                              "jsonpath": path}
    return b


def _emit_def_publications(script: str, bindings: dict, ctx: dict,
                            step_name_hint: str) -> list[str]:
    """For each `def X = ...` where X ends up as an extracted value (or a
    Bearer-prefixed one), publish it to ctx under `<step_name_hint>.X` so
    downstream `${step#X}` property expansions resolve via ctx.get."""
    out: list[str] = []
    published: set[str] = set()

    # 1) Bearer-prefixed publications get priority
    #    def <name> = "Bearer " + <extracted_var>
    for m in re.finditer(
        r'def\s+(\w+)\s*=\s*["\']Bearer\s*["\']\s*\+\s*(\w+)',
        script):
        new_var, src_var = m.group(1), m.group(2)
        info = bindings.get(src_var)
        if not info or info.get("kind") != "extracted":
            continue
        resp = _resp_var_for(info["source_step"], ctx)
        out.append(
            f'ctx.put("{step_name_hint}.{new_var}", "Bearer " + '
            f'{resp}.jsonPath().getString("{info["jsonpath"]}"));')
        published.add(new_var)
        published.add(src_var)

    # 2) Direct extractions (skip vars already published via Bearer above)
    for var_name, info in bindings.items():
        if info.get("kind") != "extracted":
            continue
        if var_name in published:
            continue
        # If some later `def Y = "Bearer " + var_name` will publish this, skip
        if re.search(rf'def\s+\w+\s*=\s*["\']Bearer["\']\s*\+\s*{re.escape(var_name)}\b',
                     script):
            continue
        resp = _resp_var_for(info["source_step"], ctx)
        out.append(
            f'ctx.put("{step_name_hint}.{var_name}", '
            f'{resp}.jsonPath().getString("{info["jsonpath"]}"));')
        published.add(var_name)
    return out


# ---------------------------------------------------------------------------
# Pattern recognizers
# ---------------------------------------------------------------------------

# 1. Token-flavored extraction: `jsonSlurper.parseText(response).access_token`
#    typically followed by `newTokenId = "Bearer " + tokenId` and a
#    `setPropertyValue(...)` on a properties step named `tokenId`.
_TOKEN_RX = re.compile(
    r"jsonSlurper\w*\.parseText\([^)]*\)\.(?P<field>access_token|token|id_token)",
    re.IGNORECASE)

def _has_bearer_concat(script: str) -> bool:
    """True when the script contains an actual `"Bearer " + something`
    concatenation pattern (Groovy string literal followed by `+`, or
    preceded by `+`). Prior version used bare `"Bearer" in script`
    which false-matched any occurrence -- including comments,
    exception messages, and unrelated identifiers -- and produced
    wrong `"Bearer " + token` output in tests that had "Bearer"
    literally anywhere in the script."""
    if not script:
        return False
    # Strip line + block comments first so commented Bearer doesn't count.
    stripped = re.sub(r'//[^\n]*', ' ', script)
    stripped = re.sub(r'/\*.*?\*/', ' ', stripped, flags=re.DOTALL)
    # Look for the actual concat pattern: a `"Bearer"` / `"Bearer "` /
    # `'Bearer '` string literal in a concat context (with `+` on either
    # side). Only this pattern signals real token-prefixing intent.
    return bool(re.search(
        r'["\']Bearer\s*["\']\s*\+|\+\s*["\']Bearer\s*["\']', stripped))


def _emit_token_extract(m: re.Match, ctx: dict) -> list[str]:
    field = m.group("field")
    # Best-effort: which step's response are we parsing? Grep the surrounding
    # script for the most recent `testStepByName("X")` or `testSteps["X"]`.
    step_name = ctx.get("_last_source_step", "tokenRequest")
    resp = _resp_var_for(step_name, ctx)
    # If actual `"Bearer " + X` concat appears in the script, emit that
    # prefix in Java too; otherwise raw. Bare substring match would fire
    # on comments/error strings/unrelated identifiers.
    prefix = '"Bearer " + ' if _has_bearer_concat(ctx.get("_script", "")) else ""
    return [
        f'// [translated] extract {field} from {step_name} response',
        f'String extractedToken = {prefix}{resp}.jsonPath().getString("{field}");',
        f'ctx.put("tokenId.GeneratedTokenID", extractedToken);',
        f'LOG.info("token extracted: {{}}", extractedToken != null ? "<redacted>" : "null");',
    ]


# 2. Generic JSON extract + setPropertyValue:
#    def resp = testRunner.testCase.getTestStepByName("STEP").getPropertyValue('response');
#    def obj = jsonSlurper.parseText(resp)
#    PropertiesX.setPropertyValue("FIELD", obj.SOMEPATH.toString().trim())
_JSON_EXTRACT_RX = re.compile(
    r"getTestStepByName\(['\"](?P<source_step>[^'\"]+)['\"]\)\.getPropertyValue\(['\"]response['\"]\)",
    re.IGNORECASE)

def _balanced_arg_call(script: str, call_prefix_rx: str) -> list[tuple[str, ...]]:
    """Find every occurrence of a Groovy call whose args must be paren-balanced.
    Naive `[^)]+` breaks when the arg itself contains parentheses (e.g.
    `setPropertyValue("f", x.toString().trim())`). This walks the opening
    `(` and consumes until matching `)`, returning captured groups + the arg
    list body."""
    results = []
    prefix = re.compile(call_prefix_rx)
    i = 0
    while i < len(script):
        m = prefix.search(script, i)
        if not m:
            break
        # cursor is now just past the opening (
        depth = 1
        j = m.end()
        while j < len(script) and depth > 0:
            ch = script[j]
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
            j += 1
        if depth != 0:
            i = m.end()
            continue
        args_body = script[m.end():j - 1]  # exclude trailing )
        results.append((m.groups(), args_body))
        i = j
    return results


def _find_setproperty_targets(script: str) -> list[tuple[str, str, str]]:
    """Find every setPropertyValue("field", expr) with proper paren balance."""
    results: list[tuple[str, str, str]] = []
    # First locate: `def X = testRunner.testCase.getTestStepByName("TARGET_STEP")`
    step_bindings: dict[str, str] = {}
    for m in re.finditer(
        r"def\s+(\w+)\s*=\s*testRunner\.testCase\.getTestStepByName\(['\"]([^'\"]+)['\"]\)",
        script):
        step_bindings[m.group(1)] = m.group(2)
    # Then: `X.setPropertyValue("field", expr)` with paren-balanced expr
    for (groups, args_body) in _balanced_arg_call(
            script, r"(\w+)\.setPropertyValue\("):
        var = groups[0]
        # Split "field", expr -- but only on the FIRST comma at depth 0
        depth = 0
        split_at = -1
        in_str = None
        for k, ch in enumerate(args_body):
            if in_str:
                if ch == in_str and args_body[k-1:k] != "\\":
                    in_str = None
            elif ch in ('"', "'"):
                in_str = ch
            elif ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
            elif ch == ',' and depth == 0:
                split_at = k
                break
        if split_at < 0:
            continue
        field_lit = args_body[:split_at].strip().strip('"').strip("'")
        expr = args_body[split_at + 1:].strip()
        target_step = step_bindings.get(var)
        if target_step:
            results.append((target_step, field_lit, expr))
    return results


def _extract_jsonpath_from_expr(expr: str, script: str) -> Optional[str]:
    """Given `resp_obj.guestId.toString().trim()` or similar, return the
    JSON path (`guestId`) if the object came from a JsonSlurper.parseText
    of a response. Returns None if not recognizable."""
    # Trim trailing .toString().trim() / .toString() calls
    e = re.sub(r"\.toString\(\)(?:\.trim\(\))?$", "", expr.strip())
    # Match `varName.field[.field2]`
    m = re.match(r"^(\w+)\.(.+)$", e)
    if not m:
        return None
    var, path = m.group(1), m.group(2)
    # Was `var` assigned from `jsonSlurper.parseText(...)` earlier?
    assign_rx = re.compile(
        rf"def\s+{re.escape(var)}\s*=\s*\w*[Ss]lurper\w*\.parseText\(",
        re.MULTILINE)
    if assign_rx.search(script):
        return path
    return None


# 3. Header extraction from response:
#    testSteps["STEP"].testRequest.response.responseHeaders["HDR"]
_HEADER_RX = re.compile(
    r'testSteps\["(?P<source_step>[^"]+)"\]\.testRequest\.response\.responseHeaders\["(?P<header>[^"]+)"\]',
    re.IGNORECASE)


# 4. Random string / user / domain generators
_RANDOM_GEN_ALPHA_RX = re.compile(
    r"new\s+Random\(\).+?\s*(?P<n>\d+)\s*\)")

_DOMAIN_LIST_RX = re.compile(
    r'domainList\s*=\s*\[[^]]*\.com[^]]*\]', re.IGNORECASE)

_EMAIL_ASSIGN_RX = re.compile(
    r"generatedEmail\s*=\s*generatedUser\s*\+\s*['\"]@['\"]", re.IGNORECASE)


# 5. Location-header slicing:
#    hilton_member_location_header[0].replace("/guests/","").split('/') [4]
_LOC_HEADER_SPLIT_RX = re.compile(
    r"(\w+)\[0\]\.replace\(['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]*)['\"]\)\.split\(['\"]([^'\"]+)['\"]\)\s*\[\s*(\d+)\s*\]")


# 6. JDBC:
#    def sql = Sql.newInstance(...)
#    sql.execute("...")  OR  sql.rows("...")
_SQL_EXECUTE_RX = re.compile(
    r"sql\.execute\(\s*(?P<query>[^)]+)\)", re.IGNORECASE)


# 7. log.info / log.error
_LOG_RX = re.compile(
    r"log\.(?P<level>info|warn|error|debug)\s*\(\s*(?P<msg>.+?)\s*\)",
    re.IGNORECASE)


# 8. context.expand('${#Project#X}')  -> Config.get("X", "")
_CONTEXT_EXPAND_RX = re.compile(
    r"context\.expand\(\s*['\"]\$\{#Project#(?P<var>[A-Za-z_][A-Za-z0-9_]*)\}['\"]\s*\)")


# ---------------------------------------------------------------------------
# The main translator
# ---------------------------------------------------------------------------

def translate(script: str, response_var_by_step: dict[str, str],
              step_name_hint: str = "") -> tuple[list[str], dict]:
    """Translate a Groovy script into Java lines for the test method body.

    Uses a "recognize, emit, cross off" loop: each pattern is scanned once;
    what it produces is added to the emit list AND the source range is marked
    as consumed. Anything left over at the end becomes commented preservation.

    Returns (lines, meta). See module docstring for meta shape.
    """
    if not script or not script.strip():
        return [], {"patterns_matched": [], "coverage": "FULL", "preview": ""}

    lines: list[str] = [f'// [groovy] {step_name_hint} -- auto-translated']
    ctx = {
        "response_var_by_step": response_var_by_step or {},
        "_script": script,
    }
    patterns_matched: list[str] = []
    consumed = False

    # ---- Def-var tracing: recognize `def X = <expr>` chains rooted in a step
    # response, and publish each extracted var to ctx under
    # `<step_name_hint>.X`. This handles the classic Bearer-token flow that
    # doesn't have a trailing setPropertyValue.
    bindings = _trace_groovy_defs(script)
    def_lines = _emit_def_publications(script, bindings, ctx, step_name_hint)
    if def_lines:
        lines.extend(def_lines)
        patterns_matched.append("def_publications")
        consumed = True

    # ---- Token extraction (single-expression variant:
    # jsonSlurper.parseText(...).access_token in one line -- older SoapUI style)
    if _TOKEN_RX.search(script) and "def_publications" not in patterns_matched:
        # Find the last testStepByName / testSteps reference before the extract
        step_ref = None
        for m in re.finditer(
                r'testSteps\["([^"]+)"\]|getTestStepByName\(["\']([^"\']+)["\']\)',
                script):
            step_ref = m.group(1) or m.group(2)
            # only the FIRST reference matters -- it's the source we're parsing
            break
        if step_ref:
            ctx["_last_source_step"] = step_ref
        m = _TOKEN_RX.search(script)
        lines.extend(_emit_token_extract(m, ctx))
        patterns_matched.append("token_extract_oneline")
        consumed = True

    # ---- Generic JSON extract -> ctx.put pattern
    # Find the source step (only need it once for the whole script for this pattern)
    source_step_matches = _JSON_EXTRACT_RX.findall(script)
    if source_step_matches:
        source_step = source_step_matches[0]  # ('source_step',)
        resp_var = _resp_var_for(source_step, ctx)
        # Every setPropertyValue after that is a candidate
        targets = _find_setproperty_targets(script)
        for target_step, field, expr in targets:
            # Skip the initial-blank pattern: setPropertyValue("X", "")
            if expr in ('""', "''"):
                continue
            path = _extract_jsonpath_from_expr(expr, script)
            if path:
                lines.append(
                    f'ctx.put("{_ctx_key(target_step, field)}", '
                    f'{resp_var}.jsonPath().getString("{path}"));')
                if "setproperty_extract" not in patterns_matched:
                    patterns_matched.append("setproperty_extract")
                consumed = True

    # ---- Track patterns_matched for the remaining recognizers via a set
    #      so downstream code can just `.add()` without duplicate checks.
    _pm_set = set(patterns_matched)

    def _mark(name: str) -> None:
        if name not in _pm_set:
            _pm_set.add(name)
            patterns_matched.append(name)

    # ---- Location-header slice pattern
    # Wrap in a `{}` scope so `locHeader_X` and `parts_X` don't collide
    # when the same header-slice Groovy block appears in multiple steps.
    for m in _LOC_HEADER_SPLIT_RX.finditer(script):
        _, strip, _, splitter, idx = m.group(1), m.group(2), m.group(3), \
                                     m.group(4), m.group(5)
        lines.append(
            f'// [translated] location-header slice: '
            f'.replace("{strip}", "").split("{splitter}")[{idx}]')
        # Find the location header source step + header name
        hm = _HEADER_RX.search(script)
        if hm:
            src = hm.group("source_step")
            hdr = hm.group("header")
            resp_var = _resp_var_for(src, ctx)
            hdr_id = hdr.replace("-", "_")
            lines.append('{')
            lines.extend([
                f'    String locHeader_{hdr_id} = {resp_var}.header("{hdr}");',
                f'    String[] parts_{hdr_id} = '
                f'locHeader_{hdr_id} == null ? new String[0] : '
                f'locHeader_{hdr_id}.replace("{strip}", "").split("{splitter}");',
            ])
            for target_step, field, expr in _find_setproperty_targets(script):
                if expr in ('""', "''") or field != hdr:
                    continue
                lines.append(
                    f'    if (parts_{hdr_id}.length > {idx}) '
                    f'ctx.put("{_ctx_key(target_step, field)}", '
                    f'parts_{hdr_id}[{idx}]);')
            lines.append('}')
        _mark("location_header_slice")
        consumed = True

    # ---- Random data generator patterns (email + username + string)
    # Wrap in a `{}` block so the local `genUsername`/`genEmail`/`genValue`
    # declarations don't collide if the same generator Groovy block appears
    # twice inside one test method.
    if _EMAIL_ASSIGN_RX.search(script) or "generatedEmail" in script:
        lines.extend([
            '// [translated] random email + username generator',
            '{',
            '    String genUsername = FakeData.username();',
            '    String genEmail = genUsername + "@" + Config.get("ALLOWED_DOMAIN", "example.com");',
            '    // Update ctx / config with generated values so downstream steps can use them',
            '    ctx.put("Properties.Email", genEmail);',
            '    ctx.put("Properties.Username", genUsername);',
            '}',
        ])
        _mark("random_email_generator")
        consumed = True
    elif _RANDOM_GEN_ALPHA_RX.search(script):
        lines.extend([
            '// [translated] random alphanumeric generator',
            '{',
            '    String genValue = FakeData.faker().regexify("[a-z0-9]{11}");',
            '    ctx.put("Properties.generated", genValue);',
            '}',
        ])
        _mark("random_alpha_generator")
        consumed = True

    # ---- JDBC (uses paren-balanced walker; safe with nested parens)
    for groups, args_body in _balanced_arg_call(script, r"sql\.execute\("):
        query = args_body.strip()
        # Detect Groovy list-arg syntax: `sql.execute("QUERY", [p1, p2])`
        # -> translate to Java varargs. Groovy's `[...]` list literal isn't
        # valid Java; we split at the top-level comma and rewrap.
        query_expr, args_expr = query, None
        # scan for a top-level comma
        depth = 0
        in_str = None
        for k, ch in enumerate(query):
            if in_str:
                if ch == in_str and query[k-1:k] != "\\":
                    in_str = None
            elif ch in ('"', "'"):
                in_str = ch
            elif ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
            elif ch == '[':
                depth += 1
            elif ch == ']':
                depth -= 1
            elif ch == ',' and depth == 0:
                query_expr = query[:k].strip()
                args_expr = query[k + 1:].strip()
                break
        params_java = ""
        params_translatable = True
        if args_expr:
            # Groovy list literal `[a, b, c]` -> would-be Java varargs.
            # But if any element is a bare Groovy identifier (undefined in
            # Java scope), the compile would fail. Only translate if every
            # element is a Java-safe literal (quoted string / number / null).
            if args_expr.startswith("[") and args_expr.endswith("]"):
                inner = args_expr[1:-1].strip()
                # naive split at top-level commas
                parts, depth, buf, in_str = [], 0, "", None
                for ch in inner:
                    if in_str:
                        if ch == in_str:
                            in_str = None
                        buf += ch
                    elif ch in ('"', "'"):
                        in_str = ch
                        buf += ch
                    elif ch in "([":
                        depth += 1; buf += ch
                    elif ch in ")]":
                        depth -= 1; buf += ch
                    elif ch == "," and depth == 0:
                        parts.append(buf.strip()); buf = ""
                    else:
                        buf += ch
                if buf.strip():
                    parts.append(buf.strip())
                safe_parts = []
                for p in parts:
                    if (p.startswith('"') and p.endswith('"')) or \
                       (p.startswith("'") and p.endswith("'")) or \
                       p in ("null",) or p.lstrip("-").replace(".", "").isdigit():
                        # normalise single-quoted Groovy strings to Java
                        if p.startswith("'"):
                            p = '"' + p[1:-1].replace('"', '\\"') + '"'
                        safe_parts.append(p)
                    else:
                        params_translatable = False
                        break
                if params_translatable:
                    params_java = ", " + ", ".join(safe_parts) if safe_parts else ""
            else:
                # Not a list literal -- treat as unsafe.
                params_translatable = False
        if not params_translatable:
            preview_p = (args_expr or "")[:60].replace("*/", "* /")
            lines.append(
                f'// [jdbc] params expression contains Groovy identifiers '
                f'(`{preview_p}`) -- skipping bind values')
            params_java = ""
        # Determine whether the query is a Java-safe expression.
        # Safe: a string literal like "SELECT 1" or 'SELECT 1' (with Groovy
        # single quotes converted to double).
        # Unsafe: a bare Groovy identifier (e.g. `sql_query`), a
        # `context.expand(...)` call, or ANY concatenation containing bare
        # identifiers (e.g. `"..." + allowedDomains + "..."`) -- those don't
        # exist in Java scope and would break compile. Downgrade to a
        # runtime warning instead.
        def _has_top_level_plus(s: str) -> bool:
            depth = 0; in_str = None
            for k, ch in enumerate(s):
                if in_str:
                    if ch == in_str and s[k-1:k] != "\\":
                        in_str = None
                elif ch in ('"', "'"):
                    in_str = ch
                elif ch in "([":
                    depth += 1
                elif ch in ")]":
                    depth -= 1
                elif ch == "+" and depth == 0:
                    return True
            return False
        looks_like_str_double = len(query_expr) >= 2 and query_expr[0] == '"' and query_expr[-1] == '"'
        looks_like_str_single = len(query_expr) >= 2 and query_expr[0] == "'" and query_expr[-1] == "'"
        has_plus = _has_top_level_plus(query_expr)
        is_double_str = looks_like_str_double and not has_plus
        is_single_str = looks_like_str_single and not has_plus
        if is_double_str or is_single_str:
            java_query = query_expr
            if is_single_str:
                # Groovy single-quoted string -> Java "..." literal
                inner = query_expr[1:-1].replace("\\", "\\\\").replace('"', '\\"')
                java_query = f'"{inner}"'
            query_for_log = query_expr[1:-1].replace('"', "'")[:60]
            lines.extend([
                f'// [translated] JDBC execute',
                f'if (Db.isConfigured()) {{',
                f'    Db.execute({java_query}{params_java});',
                f'}} else {{',
                f'    LOG.warn("Skipping JDBC step (Db not configured): {query_for_log}");',
                f'}}',
            ])
        else:
            # Query is a Groovy variable / expression we can't safely inline.
            # Log a warning so the test still runs; comment preserves intent.
            preview_c = query_expr.replace("*/", "* /")[:80]
            # Escape for the Java "..." literal below
            preview_java = (query_expr[:80]
                            .replace("\\", "\\\\")
                            .replace('"', '\\"')
                            .replace("\r", " ")
                            .replace("\n", " "))
            lines.extend([
                f'// [jdbc] query is a Groovy expression (`{preview_c}`) '
                f'-- not translated. Populate it via Config or hand-fill:',
                f'LOG.warn("Skipping JDBC step -- query expression not '
                f'translatable to Java: {preview_java}");',
            ])
        _mark("jdbc_execute")
        consumed = True

    # ---- log.info / log.error direct swap (SLF4J-compatible).
    # Uses paren-balanced walker so args with nested calls / GString
    # interpolations don't terminate the match early.
    for groups, args_body in _balanced_arg_call(
            script, r"log\.(info|warn|error|debug)\("):
        level = groups[0].lower()
        msg = args_body.strip()
        # Bail out if the message references any Groovy variable via + or
        # ${...} interpolation -- those identifiers don't exist in Java
        # scope. Preserve the intent as a comment; skip emission.
        has_interp = "${" in msg
        # Detect concatenation with an unquoted identifier: `"..." + var`
        has_var_concat = False
        stripped = msg.strip()
        if "+" in stripped:
            # Split on + at depth 0 and see if any piece is a bare identifier.
            depth = 0
            in_str = None
            parts, buf = [], ""
            for ch in stripped:
                if in_str:
                    if ch == in_str:
                        in_str = None
                    buf += ch
                elif ch in ('"', "'"):
                    in_str = ch
                    buf += ch
                elif ch == '(':
                    depth += 1
                    buf += ch
                elif ch == ')':
                    depth -= 1
                    buf += ch
                elif ch == '+' and depth == 0:
                    parts.append(buf.strip()); buf = ""
                else:
                    buf += ch
            parts.append(buf.strip())
            for p in parts:
                if p and not (p.startswith('"') or p.startswith("'") or p.isdigit()):
                    has_var_concat = True
                    break
        if has_interp or has_var_concat:
            preview = stripped[:80].replace('*/', '* /')
            lines.append(f'// [log.{level}] skipped (references Groovy-only vars): {preview}')
            consumed = True
            continue
        # Wrap in a literal only if not already a string
        if not (msg.startswith('"') or msg.startswith("'")):
            msg = '"' + msg.replace('"', '\\"') + '"'
        lines.append(f'LOG.{level}("{{}}", {msg});')
        _mark("log_swap")
        consumed = True

    # ---- Preserve original as commented block if nothing recognized
    coverage = "FULL" if consumed else "STUB"
    if not consumed:
        lines.append('// [groovy] NO PATTERN MATCHED -- test will run but this '
                     'block did not translate:')
        for src_line in (script or "").splitlines()[:20]:
            safe = src_line.replace("*/", "* /")
            lines.append(f'//     {safe}')
        lines.append('// [groovy] end of untranslated block '
                     '(no-op stub follows so compile succeeds)')
        lines.append('try { /* untranslated groovy above */ } '
                     'catch (Exception ignored) { LOG.warn("untranslated '
                     'groovy step '
                     f'{step_name_hint} skipped"); }}')
    # NOTE: no PARTIAL heuristic here. Marking PARTIAL based on the presence
    # of `if`/`for`/`assert` in the source is too aggressive -- those keywords
    # can appear in comments/strings or inside constructs my recognizers
    # already handled. If a user wants to know how thorough a translation
    # was, they should read _audit/groovy.csv's `patterns_matched` column
    # alongside `preview` for that block.

    preview = " ".join((script or "").split())[:80]
    meta = {
        "patterns_matched": patterns_matched,
        "coverage": coverage,
        "preview": preview,
    }
    return lines, meta
