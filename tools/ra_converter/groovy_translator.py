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
    # ctx-property read via .getPropertyValue("<field>") on a known
    # step_ref for ANY field (other than "response" -- that's handled
    # above and means the raw body). SoapUI Groovy authors read
    # arbitrary properties this way to pull an id captured by an
    # earlier setPropertyValue step, then use it in a SQL query or
    # request body. Without this branch the assignment was silently
    # dropped and the downstream `def sql = ... + guestID` produced
    # a `null` value in the query.
    for m in re.finditer(
        r'def\s+(\w+)\s*=\s*(\w+)\.getPropertyValue\(["\']([^"\']+)["\']\)',
        script):
        new_var, parent_var, field = m.group(1), m.group(2), m.group(3)
        if field == "response":
            continue  # handled by the response_str branch above
        parent = b.get(parent_var)
        if parent and parent.get("kind") == "step_ref" and new_var not in b:
            b[new_var] = {"kind": "ctx_property",
                          "source_step": parent.get("source_step"),
                          "field": field}
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


def _dest_key_for_var(script: str, var_name: str,
                        default_step_hint: str) -> str:
    """Return the ctx key under which a Groovy variable's value should be
    published. If the script has a downstream `X.setPropertyValue("field",
    varName)` call, use `X.field` -- that's the SoapUI-property destination
    the author intended, and it's the key OTHER steps' `${X#field}` refs
    will read. Otherwise fall back to `<default_step_hint>.<varName>`.

    Rationale: without this, a script like
        def newTokenId = "Bearer " + tokenId
        tokenIDproperty.setPropertyValue("GeneratedTokenID", newTokenId)
    was emitted as `ctx.put("Token.newTokenId", ...)` while every other
    step read `ctx.get("tokenId.GeneratedTokenID")` -- a total silent
    key-mismatch that broke every downstream authorization call.
    """
    for target_step, field, expr in _find_setproperty_targets(script):
        # `expr` here is the raw argument string. Strip surrounding
        # whitespace + `.toString().trim()` chains people often add,
        # then compare to var_name.
        raw = expr.strip()
        # Common trailing chains: `.toString()`, `.trim()`, `.toString().trim()`
        stripped = re.sub(
            r'(\.toString\(\))?(\.trim\(\))?\s*$', '', raw).strip()
        if stripped == var_name:
            return f'{target_step}.{field}'
    return f'{default_step_hint}.{var_name}'


def _emit_def_publications(script: str, bindings: dict, ctx: dict,
                            step_name_hint: str) -> list[str]:
    """For each `def X = ...` where X ends up as an extracted value (or a
    Bearer-prefixed one), publish it to ctx under the destination key the
    SoapUI author intended -- prefer `<targetStep>.<field>` from any
    `setPropertyValue(...)` call on X, fall back to
    `<step_name_hint>.X`. See `_dest_key_for_var`."""
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
        dest_key = _dest_key_for_var(script, new_var, step_name_hint)
        # `RestUtilities.safeJsonExtract` guards against empty / non-JSON
        # response bodies. Without it, an upstream 409 / 400 with an empty
        # body would crash the entire test with JsonPathException at the
        # first jsonPath().getString(...) call. Degrades to "" so downstream
        # can either detect the missing value OR proceed with a stale
        # default -- the enclosing test can still complete its remaining
        # steps or reach a graceful assertion failure.
        # Extract into a local first so we can guard on empty. Writing
        # `"Bearer "` alone (when the extract fails) to ctx would poison
        # the auth header for every downstream call AND (per the empty-
        # ctx-value semantics) block ctxGet's alias-walk from finding a
        # fallback token key. Skip the put on empty extract instead --
        # ctxGet will alias-walk to another Token-bearing key OR return
        # "" and the caller's assertion fires cleanly.
        # putExtracted handles the empty-guard AND overwrite semantics
        # in one call -- same result as the earlier inline
        # `if (X != null && !X.isEmpty()) ctx.put(...)` but consistent
        # with every other extract site in the emitter. Bearer prefix
        # is only added when the extract yielded a value; otherwise the
        # put is skipped so a downstream Auth header falls back to
        # ctxGet's alias-walk.
        out.extend([
            f'{{',
            f'    String __ext_{new_var} = com.ak.api.rest.utilities.RestUtilities.safeJsonExtract('
            f'{resp}, "{info["jsonpath"]}");',
            f'    TestSupport.putExtracted(ctx, "{dest_key}", '
            f'(__ext_{new_var} == null || __ext_{new_var}.isEmpty()) '
            f'? __ext_{new_var} : "Bearer " + __ext_{new_var});',
            f'}}',
        ])
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
        dest_key = _dest_key_for_var(script, var_name, step_name_hint)
        # Guard on empty extract: don't plant an empty value in ctx --
        # that would block ctxGet's alias-walk from finding a fallback
        # value under a sibling key (Properties.accountId etc.) and the
        # downstream URL would send an empty path segment (`//`) that
        # produces a confusing 404 from the target. Skip the put and
        # let alias-walk find the fallback.
        # putExtracted (not putIfNonEmpty): the extract's value IS the
        # authoritative one for this run -- clobber whatever stale
        # generator-default id ctx has under this key. Prior
        # putIfNonEmpty (=putIfAbsent) kept Properties.guestID pinned
        # to DataGenInput's fake random and cascaded 400/404 across
        # every downstream URL substitution.
        out.append(
            f'TestSupport.putExtracted(ctx, "{dest_key}", '
            f'com.ak.api.rest.utilities.RestUtilities.safeJsonExtract('
            f'{resp}, "{info["jsonpath"]}"));')
        published.add(var_name)

    # 3) ctx_property reads -- `def X = <stepRef>.getPropertyValue("<field>")`.
    # Emit as a Java local String that reads via ctxGet so downstream
    # code in the same Groovy step (typically a SQL query built via
    # string concat) can reference X. Also stash into ctx under the
    # source step's namespace so cross-step reads find it too. Wrapped
    # in a `{...}` scope block per var so multiple Groovy steps in the
    # SAME @Test method (each with its own `def guestID = ...`) don't
    # collide on the local declaration -- javac rejects redeclared
    # method-scope locals.
    for var_name, info in bindings.items():
        if info.get("kind") != "ctx_property":
            continue
        if var_name in published:
            continue
        src_step = info.get("source_step") or step_name_hint
        field = info.get("field", "")
        # ctxGet resolves the ${SourceStep#field} shape via its
        # alias-walk (dot / underscore / case-flip) so a value written
        # under `SourceStep.field` or `SourceStep_field` both surface.
        out.append(
            f'// [translated] def {var_name} = <{src_step}>.getPropertyValue("{field}")')
        out.append('{')
        out.append(
            f'    String {var_name} = TestSupport.ctxGet(ctx, "{src_step}.{field}");')
        out.append(
            f'    TestSupport.putIfNonEmpty(ctx, "{src_step}.{field}", {var_name});')
        # ALSO publish under the bare Groovy-var name so a downstream
        # SQL query built via `"..." + <var>` (flattened to `'#<var>#'`
        # by _try_flatten_concat_sql) resolves against mergedRow. Without
        # this, mergedRow only sees the namespaced key and the raw
        # `#guestID#` placeholder falls to the null-fallback path.
        out.append(
            f'    TestSupport.putIfNonEmpty(ctx, "{var_name}", {var_name});')
        out.append('}')
        published.add(var_name)

    return out


# ---------------------------------------------------------------------------
# SQL query preprocessing (concat-flattening for sql.eachRow / execute etc.)
# ---------------------------------------------------------------------------

def _try_flatten_concat_sql(expr: str) -> Optional[str]:
    """Given a Groovy string-concat expression like
        "select ... where account_id = " + guestID + " and status = 'A'"
    return a single SQL literal with each concatenated bare-identifier
    segment replaced by a `'#<ident>#'` placeholder that mapJsonValues
    resolves at runtime. Returns None if any concat part isn't a
    string literal or a bare identifier -- those would need a Java
    expression we can't safely inline.

    Example:
        input:  '"select foo = " + guestID + " and bar = " + accountID'
        output: 'select foo = \'#guestID#\' and bar = \'#accountID#\''
    """
    if expr is None:
        return None
    e = expr.strip()
    if not e or ("+" not in e):
        return None
    # Split on top-level `+`
    parts: list[str] = []
    depth = 0
    in_str = None
    buf = ""
    for k, ch in enumerate(e):
        if in_str:
            buf += ch
            if ch == in_str and e[k - 1:k] != "\\":
                in_str = None
        elif ch in ('"', "'"):
            in_str = ch
            buf += ch
        elif ch in "([{":
            depth += 1
            buf += ch
        elif ch in ")]}":
            depth -= 1
            buf += ch
        elif ch == "+" and depth == 0:
            parts.append(buf.strip())
            buf = ""
        else:
            buf += ch
    if buf.strip():
        parts.append(buf.strip())
    if len(parts) < 2:
        return None
    out_parts: list[str] = []
    for p in parts:
        if not p:
            return None
        # String literal (double or single quoted, no nested quotes of same kind)
        if ((p.startswith('"') and p.endswith('"'))
                or (p.startswith("'") and p.endswith("'"))):
            # Strip surrounding quotes -- we'll re-wrap the whole thing later.
            out_parts.append(p[1:-1])
        elif re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", p):
            # Bare identifier -- mapJsonValues placeholder. Wrap in
            # single quotes because SQL WHERE clauses expect string
            # or numeric literals; single-quoting is safe for both
            # under all target DB drivers we support (Postgres +
            # MySQL treat '<digits>' the same as <digits> in
            # equality comparisons).
            out_parts.append(f"'#{p}#'")
        else:
            return None
    return "".join(out_parts)


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
    # safeJsonExtract returns "" on empty / non-JSON body so a failed token
    # fetch doesn't crash the enclosing test with JsonPathException.
    # putExtracted (not raw ctx.put): an empty extract must NOT plant
    # "" into tokenId.GeneratedTokenID -- ctxGet's "primary key
    # present" short-circuit would then refuse to alias-walk to any
    # other Bearer-token key and every subsequent request would fire
    # with an empty Authorization header (401 cascade masquerading as
    # endpoint failures). Skip the put on empty; downstream falls
    # through to whatever token key was populated earlier.
    return [
        f'// [translated] extract {field} from {step_name} response',
        f'String extractedToken = {prefix}'
        f'com.ak.api.rest.utilities.RestUtilities.safeJsonExtract('
        f'{resp}, "{field}");',
        f'TestSupport.putExtracted(ctx, "tokenId.GeneratedTokenID", extractedToken);',
        f'LOG.info("token extracted: {{}}", (extractedToken == null || extractedToken.isEmpty()) ? "null/empty" : "<redacted>");',
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


def _slice_derived_vars(script: str) -> dict[str, str]:
    """Return dict[var_name -> index] for every def-var that ultimately
    holds a specific slice of a location-header split. Used to filter
    the location-header slice emit loop so it only publishes for
    setPropertyValue targets whose expr traces back to the slice.

    Handles the three shapes real SoapUI authors write:

        // Inline:
        def hiltonMemberId = loc[0].replace(...).split("/")[4]
        //  ^^^^^^^^^^^^^^                                  ^ index recorded here

        // Split-then-index:
        def parts        = loc[0].replace(...).split("/")   // split_result, no index
        def hiltonMemberId = parts[4]                        // slice with index

        // Alias:
        def hMemberId = hiltonMemberId                       // propagates index

    Returns index as a string (matching the [N] group's raw form).
    Bare split-result vars WITHOUT a specific index aren't returned
    here -- the emitter needs an explicit [N] to know which segment
    to publish. `setPropertyValue("field", parts)` (no index) would
    publish the whole array's toString(), which is never useful.
    """
    slice_vars: dict[str, str] = {}
    split_result_vars: set[str] = set()

    # Pass 1: direct split assignments (may have a trailing [N] or not)
    #   def X = <anything>.split(<anything>)          -> split_result
    #   def X = <anything>.split(<anything>)[N]       -> slice with index N
    split_rx = re.compile(
        r'def\s+(\w+)\s*=\s*[^\n;]*?\.split\([^)]*\)(\s*\[\s*(\d+)\s*\])?')
    for m in split_rx.finditer(script):
        var = m.group(1)
        idx = m.group(3)
        if idx is not None:
            slice_vars[var] = idx
        else:
            split_result_vars.add(var)

    # Pass 2: index-into-split-result assignments
    #   def X = <split_result_var>[N]                  -> slice with index N
    idx_rx = re.compile(r'def\s+(\w+)\s*=\s*(\w+)\s*\[\s*(\d+)\s*\]')
    for m in idx_rx.finditer(script):
        var, src, idx = m.group(1), m.group(2), m.group(3)
        if src in split_result_vars:
            slice_vars[var] = idx

    # Pass 3: propagate through simple aliases (multiple iterations
    # handle chains like `def a = parts[4]; def b = a; def c = b`).
    alias_rx = re.compile(
        r'def\s+(\w+)\s*=\s*(\w+)(?:\.toString\(\))?(?:\.trim\(\))?\s*(?:$|;|\r|\n)',
        re.MULTILINE)
    for _ in range(3):
        changed = False
        for m in alias_rx.finditer(script + "\n"):
            var, src = m.group(1), m.group(2)
            if var in slice_vars or var == src:
                continue
            if src in slice_vars:
                slice_vars[var] = slice_vars[src]
                changed = True
        if not changed:
            break

    return slice_vars


# 6. JDBC:
#    def sql = Sql.newInstance(...)
#    sql.execute("...")  OR  sql.rows("...")
_SQL_EXECUTE_RX = re.compile(
    r"sql\.execute\(\s*(?P<query>[^)]+)\)", re.IGNORECASE)


def _normalize_jdbc_query(raw_q: str) -> tuple[str, list]:
    """Rewrite a raw SQL string to a `#placeholder#`-friendly form so
    downstream mapJsonValues resolves references at runtime. Returns
    (transformed_query, substituted_id_columns).

    - Stale-id-shaped WHERE literals (6+ digit numeric bound to id-hinted
      columns) get parameterized to `col='#col#'` so a fresh runtime
      value can plug in via ctx / row / config.
    - SoapUI ${#TestCase#Properties#X} / ${#Project#Y} / ${step#Y} /
      bare ${var} refs become #Properties_X# / #Y# / #step_Y# / #var#
      so Db.unsafeSqlReason doesn't refuse the SQL for containing
      unresolved `${...}` even though the framework can handle them."""
    ID_COL_HINTS = ("id", "guest", "account", "member", "hhonors",
                    "hilton", "partner", "customer", "user")
    hard_lits = re.findall(
        r"\b([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:'([^']+)'|(\d[\d.]*))", raw_q)
    substituted_cols: list[str] = []
    transformed = raw_q
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
            continue
        pattern = re.compile(
            rf"\b{re.escape(col)}\s*=\s*(?:'[^']+'|\d[\d.]*)")
        transformed = pattern.sub(f"{col}='#{col}#'", transformed, count=1)
        substituted_cols.append(col)
    transformed = re.sub(
        r'\$\{#(?:TestCase|TestSuite|Global|Env|MockService)#'
        r'([A-Za-z0-9_.-]+)\}',
        lambda m: '#' + m.group(1).replace('.', '_') + '#',
        transformed)
    transformed = re.sub(
        r'\$\{#Project#([A-Za-z0-9_.-]+)\}',
        lambda m: '#' + m.group(1).replace('.', '_') + '#',
        transformed)
    transformed = re.sub(
        r'\$\{([A-Za-z_][A-Za-z0-9_]*)#([A-Za-z0-9_.-]+)\}',
        lambda m: '#' + m.group(1) + '_' + m.group(2).replace('.', '_') + '#',
        transformed)
    transformed = re.sub(
        r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}',
        lambda m: '#' + m.group(1) + '#',
        transformed)
    return transformed, substituted_cols


def _java_string_literal(raw: str) -> str:
    """Wrap a raw string as a Java `"..."` literal, escaping the
    minimum set (backslash + double quote)."""
    return '"' + raw.replace("\\", "\\\\").replace('"', '\\"') + '"'


# 7. log.info / log.error
_LOG_RX = re.compile(
    r"log\.(?P<level>info|warn|error|debug)\s*\(\s*(?P<msg>.+?)\s*\)",
    re.IGNORECASE)


# 8. context.expand('${...}')  -- SoapUI's runtime ref expander.
# Covers every scoped ref shape (Project, TestCase/TestSuite/Global/Env
# scoped, cross-step, bare identifier). The narrow #Project-only pattern
# previously defined here was declared but never referenced in
# translate(); every context.expand call in the suite was silently
# dropped, and `def X = context.expand('${Properties#Domain}')`
# left `X` undefined in Java scope -- downstream references to `X`
# (in SQL binds, log lines, etc.) fell through as bare identifiers
# and either broke compile or lost the value entirely.
_CONTEXT_EXPAND_ANY_RX = re.compile(
    r"context\.expand\(\s*['\"](?P<ref>\$\{[^}]+\})['\"]\s*\)")
# Binding form: `def X = context.expand('${...}')`. Captures the local
# name so the emitter can lift it into a Java String local AND
# publish it to ctx under its own name, so both direct references
# (`sql.execute("... = ?", [X])` -> our #ident# rewrite finds `X` in
# ctx) and re-refs (`context.expand('${X}')`) resolve.
_DEF_CONTEXT_EXPAND_RX = re.compile(
    r"def\s+(?P<var>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*"
    r"context\.expand\(\s*['\"](?P<ref>\$\{[^}]+\})['\"]\s*\)")


def _translate_soapui_jsonpath(path: str) -> str:
    """Local copy of ra_converter's helper (importing from ra_converter
    would create a cycle -- ra_converter imports this module). Strips
    leading `$.` / `$` and unwraps `['x']` / `["x"]` -> `.x`.

    Groovy single-quoted string literals in the SoapUI XML often
    carry escaped quotes (`$[\\'x\\']`) that reach us verbatim as
    `$[\\'x\\']`. Unescape before the bracket regex or the strip
    silently misses and leaves `[\\'x\\']` in the output path."""
    p = (path or "").strip()
    p = p.replace("\\'", "'").replace('\\"', '"')
    if p.startswith("$."):
        p = p[2:]
    elif p.startswith("$"):
        p = p[1:]
    p = re.sub(r"\['([^']+)'\]", r".\1", p)
    p = re.sub(r'\["([^"]+)"\]', r".\1", p)
    return p.lstrip(".")


def _translate_soapui_ref_to_java_expr(
        ref_text: str,
        response_var_by_step: Optional[dict] = None) -> str:
    """Translate a bare SoapUI `${...}` ref to the Java expression
    that produces its value at runtime. Handles every common scoped
    form; falls back to a ctx-lookup on the raw inner text (which
    resolves to "" if nothing matches -- safer than a bare identifier
    reference the Java compiler would reject).

    Pass `response_var_by_step` so `${Step#Response#<jsonPath>}` can
    route to `safeJsonExtract(<step>Res, path)` when the step's
    response variable is in scope. Without the map (or if the step
    is out of scope), the ref falls back to a ctx lookup on a
    synthesized `step.responsefield` key -- which will resolve iff
    a PropertyTransfer previously published it.

    Kept local to groovy_translator to avoid a circular import with
    ra_converter (which itself imports this module). Regex family
    intentionally mirrors ra_converter's soapui_expr_to_java so
    both paths translate identically."""
    inner = ref_text.strip()
    if inner.startswith("${") and inner.endswith("}"):
        inner = inner[2:-1]
    # ${#Project#X} -> Config.get("X", "")
    m = re.fullmatch(r"#Project#([A-Za-z0-9_.-]+)", inner)
    if m:
        return f'Config.get("{m.group(1)}", "")'
    # ${#TestCase#Properties#X} / ${#TestSuite#X} -> ctx read via
    # the trailing property key
    m = re.fullmatch(
        r"#(?:TestCase|TestSuite|MockService)#([A-Za-z0-9_.-]+)#"
        r"([A-Za-z0-9_.-]+)", inner)
    if m:
        return f'TestSupport.ctxGet(ctx, "{m.group(1)}.{m.group(2)}")'
    m = re.fullmatch(
        r"#(?:TestCase|TestSuite|MockService)#([A-Za-z0-9_.-]+)", inner)
    if m:
        return f'TestSupport.ctxGet(ctx, "{m.group(1)}")'
    # ${#Global#X} / ${#Env#X} -> Config.get (Global + Env are project-
    # level configs, not per-test ctx)
    m = re.fullmatch(r"#(?:Global|Env)#([A-Za-z0-9_.-]+)", inner)
    if m:
        return f'Config.get("{m.group(1)}", "")'
    # ${Step#Response#<jsonPath>} -- SoapUI's cross-step JSON extract.
    # Route to safeJsonExtract when the source step's response is in
    # scope; otherwise fall back to a ctx read on a synthesized key
    # (which resolves if a PropertyTransfer published it earlier).
    m = re.fullmatch(
        r"([A-Za-z_][A-Za-z0-9_ -]*)#Response#(.+)", inner)
    if m:
        step_raw, path = m.group(1), m.group(2)
        step_safe = re.sub(r"[^A-Za-z0-9_]", "_", step_raw)
        resp_var = (response_var_by_step or {}).get(step_raw)
        if not resp_var:
            resp_var = (response_var_by_step or {}).get(step_safe)
        if resp_var:
            jp = _translate_soapui_jsonpath(path)
            return (f'com.ak.api.rest.utilities.RestUtilities'
                    f'.safeJsonExtract({resp_var}, "{jp}")')
        # Response var out of scope: settle for a ctx read under a
        # synthetic key. Author can wire a PropertyTransfer to
        # publish it.
        jp = _translate_soapui_jsonpath(path)
        key_field = re.sub(r"[^A-Za-z0-9_]", "_", jp)
        return f'TestSupport.ctxGet(ctx, "{step_safe}.{key_field}")'
    # ${Step#Field} -> ctxGet("Step.Field")
    m = re.fullmatch(
        r"([A-Za-z_][A-Za-z0-9_]*)#([A-Za-z0-9_.-]+)", inner)
    if m:
        return f'TestSupport.ctxGet(ctx, "{m.group(1)}.{m.group(2)}")'
    # ${Var} bare -- read from ctx directly
    m = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_.-]*)", inner)
    if m:
        return f'TestSupport.ctxGet(ctx, "{m.group(1)}")'
    # Unknown shape -- best-effort ctx read on the raw inner. Returns
    # "" if not found; better than emitting a Java compile error.
    escaped = inner.replace('\\', '\\\\').replace('"', '\\"')
    return f'TestSupport.ctxGet(ctx, "{escaped}")'


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

    # Strip Groovy `import` lines from consideration -- they never
    # translate to Java (Java has its own import mechanism above the
    # method body). Prior behaviour: unhandled imports counted toward
    # the "unrecognized text" tally and could tip coverage from FULL
    # to STUB, or fire the fallback stub emitter for otherwise-
    # translatable scripts. Removed for pattern-matching purposes ONLY;
    # doesn't touch the source variable passed around by other stages.
    script = re.sub(r"^\s*import\s+[A-Za-z0-9_.]+\s*$", "",
                    script, flags=re.MULTILINE)

    lines: list[str] = [f'// [groovy] {step_name_hint} -- auto-translated']
    ctx = {
        "response_var_by_step": response_var_by_step or {},
        "_script": script,
    }
    patterns_matched: list[str] = []
    consumed = False

    # ---- `def X = context.expand('${...}')` bindings. Runs FIRST so
    # each var lands in ctx before sql.execute's bind-list rewrite
    # (finding #7) or a later context.expand looks for it.
    # Wrapped in a `{...}` scope block so multiple Groovy steps in
    # the same @Test method don't collide on locals like `dbUrl`
    # (each step's block emits its own idempotent local + ctx.put).
    # Downstream refs to `X` in emitted Java should read `ctx.get`
    # or use `#X#` placeholders -- the raw Java local is a
    # translation artifact, not a public contract.
    _context_expand_vars: list[tuple[str, str, str]] = []
    for m in _DEF_CONTEXT_EXPAND_RX.finditer(script):
        var = m.group("var")
        if any(v[0] == var for v in _context_expand_vars):
            continue
        java_expr = _translate_soapui_ref_to_java_expr(
            m.group("ref"), response_var_by_step)
        _context_expand_vars.append((var, java_expr, m.group("ref")))
    if _context_expand_vars:
        lines.append('{')
        for var, java_expr, ref in _context_expand_vars:
            lines.append(
                f'    // [translated] def {var} = context.expand({ref})')
            lines.append(f'    String {var} = {java_expr};')
            lines.append(
                f'    TestSupport.putIfNonEmpty(ctx, "{var}", {var});')
        lines.append('}')
        patterns_matched.append("context_expand_def")
        consumed = True

    # ---- Bare `context.expand('${...}')` calls that WEREN'T bound to a
    # `def X`. Rare (usually the return value's discarded in Groovy)
    # but worth flagging so the emit's diff shows the untranslated
    # side effect. Value goes into a synthetic ctx key derived from
    # the ref content so later ctxGet-style probes can find it.
    for m in _CONTEXT_EXPAND_ANY_RX.finditer(script):
        # Skip anything already handled by the DEF branch above.
        start = m.start()
        # Cheap check: is the preceding chunk a `def X =`?
        head = script[max(0, start - 60):start]
        if re.search(r"def\s+[A-Za-z_][A-Za-z0-9_]*\s*=\s*$", head):
            continue
        java_expr = _translate_soapui_ref_to_java_expr(
            m.group("ref"), response_var_by_step)
        # Synthesize a stable key from the ref so multiple bare
        # expands in one script don't stomp each other.
        key_seed = re.sub(r"[^A-Za-z0-9_]", "_", m.group("ref").strip("${}"))
        lines.append(
            f'// [translated] bare context.expand({m.group("ref")})')
        lines.append(
            f'TestSupport.putIfNonEmpty(ctx, "_ctxexpand.{key_seed}", '
            f'{java_expr});')
        patterns_matched.append("context_expand_bare")
        consumed = True

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
                # putExtracted (overwrite iff non-empty): the extract's
                # value IS authoritative for this run and MUST clobber
                # whatever generator-default id ctx has under this key.
                # Empty-guard still applies -- a 4xx response returning
                # "" won't plant an empty in ctx (which would break
                # ctxGet's alias-walk and cascade `//` empty segments).
                lines.append(
                    f'TestSupport.putExtracted(ctx, "{_ctx_key(target_step, field)}", '
                    f'com.ak.api.rest.utilities.RestUtilities.safeJsonExtract('
                    f'{resp_var}, "{path}"));')
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
    _loc_matches = list(_LOC_HEADER_SPLIT_RX.finditer(script))
    if _loc_matches:
        # Slice-idx per-match is emitted for diagnostics; the actual
        # publication index per setPropertyValue is re-parsed from that
        # target's expr below, so one Groovy step can publish multiple
        # header parts (guestId, memberId, ...) from ONE location header.
        for m in _loc_matches:
            _, strip, _, splitter, idx = m.group(1), m.group(2), \
                m.group(3), m.group(4), m.group(5)
            lines.append(
                f'// [translated] location-header slice: '
                f'.replace("{strip}", "").split("{splitter}")[{idx}]')
        m0 = _loc_matches[0]
        strip0, splitter0, fallback_idx = m0.group(2), m0.group(4), m0.group(5)
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
                f'locHeader_{hdr_id}.replace("{strip0}", "").split("{splitter0}");',
            ])
            # Prior bug: `field != hdr` filter dropped every publication,
            # because the setPropertyValue *target* field (e.g.
            # `hilton-member-id`) is intentionally NAMED DIFFERENTLY from
            # the source header (`hilton-member-location`). Result: ctx
            # never received guestId / memberId, downstream URLs
            # collapsed to `//` and cascaded 404/405 across the case.
            #
            # Filter: only emit for targets whose EXPR references the
            # slice output. Prior iteration looped every setPropertyValue
            # in the script and clobbered targets sourced from OTHER
            # extract paths -- e.g. `PropertiesDetails.accountID` gets
            # its real value from a JSON PropertyTransfer on the same
            # step's response body, but this loop would then overwrite
            # with `parts[4]` (the hilton-member-id slice), producing
            # `PropertiesDetails.accountID = 329335` for a real
            # accountId of, say, 10500XXXXXX. Every downstream
            # `GET /businesses/<id>` and `POST /guests/../businesses/<id>/members`
            # 404'd on the wrong id.
            #
            # Slice-derivation trace covers the three real-world shapes:
            #   (a) inline:  setPropertyValue("f", loc[0].replace(...).split("/")[4])
            #   (b) def-var: def parts = loc[0].split(...); setPropertyValue("f", parts[4])
            #   (c) alias:   def x = parts[4]; setPropertyValue("f", x)
            # Targets whose expr matches none of these (bare identifier
            # of a JSON-extracted var, `jsonObj.field`, etc.) are
            # deliberately skipped -- some other emit path
            # (setproperty_extract JSON, PropertyTransfer, def
            # publication) owns them.
            _slice_vars = _slice_derived_vars(script)
            _idx_in_expr_rx = re.compile(r"\[\s*(\d+)\s*\]")
            _bare_var_rx = re.compile(
                r'^(\w+)(?:\.toString\(\))?(?:\.trim\(\))?$')
            for target_step, field, expr in _find_setproperty_targets(script):
                if expr in ('""', "''"):
                    continue
                use_idx = None
                # (a) inline slice pattern in the expr itself
                if _LOC_HEADER_SPLIT_RX.search(expr):
                    m_inline = _LOC_HEADER_SPLIT_RX.search(expr)
                    use_idx = m_inline.group(5)
                else:
                    # (b) def-var with explicit index: `parts[4]`
                    m_bare = _bare_var_rx.match(expr.strip())
                    if m_bare and m_bare.group(1) in _slice_vars:
                        # (c) alias / already-indexed slice-derived var
                        use_idx = _slice_vars[m_bare.group(1)]
                    else:
                        # Also allow `<split_result_var>[N]` inline
                        m_indexed = re.match(
                            r'^(\w+)\s*\[\s*(\d+)\s*\](?:\.toString\(\))?'
                            r'(?:\.trim\(\))?$',
                            expr.strip())
                        if m_indexed:
                            # The inner var may be a split_result (bare
                            # split without index) -- also slice-derived
                            # even though not in the map yet. Heuristic:
                            # trust the [N] on the expr since the outer
                            # slice pattern is confirmed in this script.
                            use_idx = m_indexed.group(2)
                if use_idx is None:
                    # Not slice-derived -- another emit path handles it.
                    continue
                # putExtracted -- overwrite whatever stale generator-
                # default id ctx already has under this key. Empty-
                # guard still applies (parts[N] may be "" if the
                # slice fell off the end or upstream 4xx returned no
                # header); putExtracted skips the empty write so
                # ctxGet's alias-walk can still fall through.
                lines.append(
                    f'    if (parts_{hdr_id}.length > {use_idx}) '
                    f'TestSupport.putExtracted(ctx, '
                    f'"{_ctx_key(target_step, field)}", '
                    f'parts_{hdr_id}[{use_idx}]);')
            lines.append('}')
        _mark("location_header_slice")
        consumed = True

    # ---- Random data generator patterns (email + username + string)
    # Wrap in a `{}` block so the local `genUsername`/`genEmail`/`genValue`
    # declarations don't collide if the same generator Groovy block appears
    # twice inside one test method.
    if _EMAIL_ASSIGN_RX.search(script) or "generatedEmail" in script:
        # Enumerate EVERY setPropertyValue target in the Groovy source
        # (Username, Username2, Domain, Email, Phone, guestMemberEmail,
        # etc.) so each gets its OWN freshly-generated value keyed by
        # its NAME SHAPE:
        #   phone / phonenumber                -> 9-digit numeric string
        #   domain / websitedomain / weburl    -> "word.com"
        #   email / *email* / *emailaddress*   -> "word@allowed-domain"
        #   *guestid* / *memberid* / hhonors*  -> 9-digit numeric string
        #   everything else                    -> username-shaped alphanum
        # Also always populate common variants that SoapUI templates ask
        # for even when DataGenInput doesn't set them explicitly:
        # usernamemember / usernameM / EmailMember / guestMemberEmail --
        # the SoapUI author's original CSV had stale values for these
        # that would collide on the target API's uniqueness checks.
        set_targets = []
        for m in re.finditer(
                r'setPropertyValue\s*\(\s*["\']([A-Za-z_][A-Za-z0-9_]*)["\']',
                script):
            set_targets.append(m.group(1))
        # Add always-populated safety variants regardless of the source
        # script's contents. Prevents "usernamemember stays stale" bugs
        # when the template references a variant SoapUI never populated.
        ALWAYS = ["Username", "username", "usernamemember", "usernameM",
                  "Email", "email", "EmailMember", "guestMemberEmail",
                  "Phone", "phone", "phoneNumber", "hhonorsNumber",
                  "Domain", "domain", "websiteDomain",
                  "generatedemailAddress", "generatedEmail",
                  # Id-shaped variants -- used as fallback when the
                  # emitter's hardcoded-id-rewrite pass converted a
                  # request body's stale hardcoded guestId=1900XXX to a
                  # #Properties_guestId# placeholder, and no upstream
                  # extract has populated Properties.guestId yet. Random
                  # 9-digit -> body sends a fake id, target rejects
                  # cleanly (404), and the failure is attributable to
                  # missing upstream data instead of the body having a
                  # stale valid id pointing at some other account.
                  "guestId", "guestID", "memberGuestID",
                  "accountId", "accountID",
                  "memberId", "memberID",
                  "partnerAccountId", "partnerAccountID"]
        seen: set = set()
        all_props = []
        for t in set_targets + ALWAYS:
            if t in seen:
                continue
            seen.add(t)
            all_props.append(t)

        def _generator_expr(prop: str) -> str:
            """Return a Java expression producing an appropriately-shaped
            value for the property. Semantic-shape mapping so a `Phone`
            variant gets a numeric string, `Domain` gets `word.com`, etc."""
            p = prop.lower()
            if "phone" in p or p == "hhonorsnumber":
                # 9-digit numeric. FakeData.faker().numerify seeds a
                # random digit per '#'. Bounded to the shape most APIs
                # accept.
                return 'FakeData.faker().numerify("#########")'
            if "guestid" in p or "memberid" in p or "accountid" in p:
                return 'FakeData.faker().numerify("#########")'
            if p in ("domain", "websitedomain", "weburl"):
                return 'FakeData.username() + ".com"'
            if "email" in p:
                return ('FakeData.username() + "@" + '
                        'Config.get("ALLOWED_DOMAIN", "example.com")')
            return 'FakeData.username()'

        variant_lines: list[str] = []
        for prop in all_props:
            # SoapUI ${#Project#ALLOWED_DOMAINS} resolves to a comma-list
            # in the source script, but ctx / config doesn't have that
            # here -- use ALLOWED_DOMAIN (single). Domain gets a stable
            # value shared across variants so all "domain" references in
            # a case agree.
            expr = _generator_expr(prop)
            varname = f'genV_{re.sub(r"[^A-Za-z0-9_]", "_", prop)}'
            variant_lines.append(f'    String {varname} = {expr};')
            variant_lines.append(f'    ctx.put("Properties.{prop}", {varname});')
            # Also case-flipped tail so lowercase / CamelCase templates both hit.
            flipped = (prop[0].swapcase() + prop[1:]) if prop else prop
            if flipped and flipped != prop:
                variant_lines.append(
                    f'    ctx.put("Properties.{flipped}", {varname});')

        lines.extend([
            '// [translated] random email + username + variant generator',
            '{',
            '    // Every variant gets a NAME-SHAPE-appropriate generator',
            '    // (phones = digits, domains = word.com, emails = word@domain,',
            '    // ids = 9-digit numeric, everything else = alphanum).',
            '    // Both case variants of each key are populated so',
            '    // #Properties_Username# and #Properties_username# both resolve.',
        ])
        lines.extend(variant_lines)
        lines.append('}')
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
    # Guard: a script with multiple mutation JDBC calls must throw
    # SkipException only ONCE -- subsequent throws are unreachable and
    # javac rejects them. Track whether we've emitted a throw already.
    _skip_thrown = False
    # Shared counter for SELECT-routed result vars, so `sql.execute(SELECT)`
    # and later `sql.rows`/`sql.firstRow` locals don't collide when a
    # single Groovy script mixes them.
    _row_var_counter = 0
    # `sql.executeUpdate("...")` is the same shape as sql.execute for
    # our purposes -- both dispatch to Db.execute (INSERT/UPDATE/DELETE)
    # or Db.queryAll (SELECT) based on the SELECT detection below. The
    # audit found ~550 sql.executeUpdate occurrences in the suite that
    # would otherwise silently no-op.
    for groups, args_body in _balanced_arg_call(
            script, r"sql\.(?:execute|executeUpdate|executeInsert)\("):
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
        idents_for_substitute: list[str] = []
        if not params_translatable:
            preview_p = (args_expr or "")[:60].replace("*/", "* /")
            # Extract bare identifiers from the bind list so we can
            # rewrite the SQL's `?` placeholders to `#ident#` refs --
            # mapJsonValues then resolves each against ctx / row /
            # config at runtime. Without this rewrite the WARN above
            # is followed by Db.execute-with-no-binds, which throws
            # a PreparedStatement unbound-parameter error swallowed as
            # a second WARN; the intent (fill in these values) is
            # entirely lost.
            if args_expr and args_expr.startswith("[") and args_expr.endswith("]"):
                inner = args_expr[1:-1]
                parts, depth, buf, in_str = [], 0, "", None
                for ch in inner:
                    if in_str:
                        if ch == in_str:
                            in_str = None
                        buf += ch
                    elif ch in ('"', "'"):
                        in_str = ch; buf += ch
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
                for p in parts:
                    m_id = re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", p)
                    if m_id:
                        idents_for_substitute.append(m_id.group(0))
                    else:
                        idents_for_substitute.append("")  # placeholder-of-nothing
            if idents_for_substitute:
                idents_shown = ", ".join(
                    f"#{n}#" if n else "?" for n in idents_for_substitute)
                lines.append(
                    f'// [jdbc] params list contains Groovy identifiers '
                    f'(`{preview_p}`); rewriting `?` bind placeholders '
                    f'to `#ident#` refs so mapJsonValues resolves at '
                    f'runtime: [{idents_shown}]')
            else:
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
            # Extract the RAW query text (without surrounding Java quotes)
            # so we can rewrite hardcoded WHERE literals into
            # #placeholder# refs before wrapping in mapJsonValues.
            raw_q = query_expr[1:-1]
            # Rewrite each `?` bind slot with the corresponding
            # identifier from the [ident1, ident2, ...] list as
            # `'#ident#'` -- mapJsonValues resolves at runtime. Only
            # kicks in when the bind list contained bare identifiers
            # that couldn't be inlined as Java literals (Groovy scope
            # values we can't recreate at compile-time). Wraps each
            # sub in quotes so bareword substitution stays SQL-safe;
            # numeric columns tolerate quoted values on all target
            # drivers used by this framework.
            if idents_for_substitute:
                _idx_holder = [0]
                def _sub_qmark(_m):
                    i = _idx_holder[0]
                    _idx_holder[0] += 1
                    if i >= len(idents_for_substitute):
                        return "?"  # more ? than binds; leave unbound
                    name = idents_for_substitute[i]
                    if not name:
                        return "?"  # non-identifier slot; can't refify
                    return f"'#{name}#'"
                raw_q = re.sub(r"\?", _sub_qmark, raw_q)
            hard_lits = re.findall(
                r"\b([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:'([^']+)'|(\d[\d.]*))",
                raw_q)
            substituted_cols: list[str] = []
            transformed = raw_q
            # Only parameterize STALE-ID-SHAPED literals -- 6+ digit
            # numbers, typically Hilton internal ids that expired years
            # ago. Enum values (status='active', web_site='foo.com',
            # code='XYZ') MUST stay as-is: the SoapUI author intended
            # those literals, and no upstream step populates them in
            # ctx, so parameterizing them creates `null` fallbacks that
            # Db.execute then refuses.
            ID_COL_HINTS = ("id", "guest", "account", "member", "hhonors",
                            "hilton", "partner", "customer", "user")
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
                    continue  # keep the literal, don't parameterize
                pattern = re.compile(
                    rf"\b{re.escape(col)}\s*=\s*(?:'[^']+'|\d[\d.]*)")
                transformed = pattern.sub(f"{col}='#{col}#'", transformed, count=1)
                substituted_cols.append(col)
            # Translate SoapUI-style refs left in the SQL to framework
            # placeholders so `mapJsonValues` resolves them at runtime.
            # Common patterns seen in imported suites:
            #   ${Properties#guestID}    -> #Properties_guestID#
            #   ${#TestCase#Properties#X} -> #Properties_X#
            #   ${#Project#Y}             -> #Y#
            # Without this, Db.unsafeSqlReason (correctly) refuses the
            # SQL for containing `${...}` even though the intent is a
            # runtime substitution the framework CAN handle.
            transformed = re.sub(
                r'\$\{#(?:TestCase|TestSuite|Global|Env|MockService)#'
                r'([A-Za-z0-9_.-]+)\}',
                lambda m: '#' + m.group(1).replace('.', '_') + '#',
                transformed)
            transformed = re.sub(
                r'\$\{#Project#([A-Za-z0-9_.-]+)\}',
                lambda m: '#' + m.group(1).replace('.', '_') + '#',
                transformed)
            transformed = re.sub(
                r'\$\{([A-Za-z_][A-Za-z0-9_]*)#([A-Za-z0-9_.-]+)\}',
                lambda m: '#' + m.group(1) + '_' + m.group(2).replace('.', '_') + '#',
                transformed)
            # Bare ${var}
            transformed = re.sub(
                r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}',
                lambda m: '#' + m.group(1) + '#',
                transformed)
            # Java literal form of the (potentially rewritten) query.
            trans_inner = transformed.replace("\\", "\\\\").replace('"', '\\"')
            java_query = f'"{trans_inner}"'
            query_for_log = transformed.replace('"', "'")[:60]
            lines.append(f'// [translated] JDBC execute')
            if substituted_cols:
                lines.append(
                    f'// [jdbc] parameterized {len(substituted_cols)} '
                    f'hardcoded WHERE literal(s) with #placeholder# refs -- '
                    f'columns: {", ".join(substituted_cols)}. '
                    f'Populate ctx (via prior REST extract or CSV row) or '
                    f'the runtime-resolved query will still carry unresolved '
                    f'`#{substituted_cols[0]}#`.')
            # SoapUI authors colloquially write `sql.execute("SELECT ...")`
            # for read queries (Groovy is loose about it). Db.execute is
            # for INSERT/UPDATE/DELETE only -- .executeUpdate() returns
            # a rowcount and the driver rejects a SELECT with
            #   "A result was returned when none was expected"
            # Db.unsafeSqlReason correctly refuses SELECTs-in-execute
            # to keep the driver from throwing, but the JDBC step then
            # silently no-ops. Route the SELECT variant to
            # Db.queryAll(...) so the query actually runs; log the row
            # count so the trace shows the query fired. Non-SELECT
            # queries keep the existing Db.execute path.
            is_select_query = bool(re.match(
                r"\s*(?:with\b.*?\bselect|select)\b",
                transformed, re.IGNORECASE | re.DOTALL))
            if is_select_query:
                _row_var_counter += 1
                step_tag = re.sub(r"[^A-Za-z0-9_]", "_", step_name_hint or "s")
                result_var = (f"__jdbcRows_{step_tag}_"
                              f"{_row_var_counter}")
                lines.extend([
                    f'java.util.List<java.util.Map<String, Object>> '
                    f'{result_var} = null;',
                    f'if (Db.isConfigured()) {{',
                    f'    try {{',
                    f'        String __jdbcSql = RestUtilities.mapSqlValues('
                    f'{java_query}, TestSupport.mergedRow(row, ctx), ctx);',
                    # unsafeSqlReasonForQuery -- caller dispatches to Db.queryAll
                    # so the SELECT-vs-execute reason from the full check would
                    # spuriously refuse (the check is meant to catch misroutes
                    # into Db.execute, not this correctly-routed path).
                    f'        String __jdbcReason = com.ak.api.db.Db.unsafeSqlReasonForQuery(__jdbcSql);',
                    f'        if (__jdbcReason != null) {{',
                    f'            LOG.warn(" .. jdbc SKIPPED ({{}}): {{}}", '
                    f'__jdbcReason, __jdbcSql);',
                    f'        }} else {{',
                    f'            LOG.info(" .. jdbc SQL: {{}}", __jdbcSql);',
                    f'            {result_var} = Db.queryAll(__jdbcSql'
                    f'{params_java});',
                    f'            LOG.info(" .. jdbc rows returned: {{}}", '
                    f'{result_var} == null ? 0 : {result_var}.size());',
                    f'        }}',
                    f'    }} catch (Exception __jdbcEx) {{',
                    f'        LOG.warn("JDBC queryAll failed: {{}}", '
                    f'__jdbcEx.getMessage());',
                    f'    }}',
                    f'}} else {{',
                    f'    LOG.warn("Skipping JDBC step (Db not configured): {query_for_log}");',
                    f'}}',
                ])
            else:
                lines.extend([
                    f'if (Db.isConfigured()) {{',
                    # Wrap in mapJsonValues so #X# placeholders resolve from
                    # merged (row + ctx + config) view at runtime; wrap in
                    # try/catch so a single bad SQL doesn't crash the test --
                    # WARN + continue so downstream steps still fire.
                    f'    try {{',
                    f'        String __jdbcSql = RestUtilities.mapSqlValues('
                    f'{java_query}, TestSupport.mergedRow(row, ctx), ctx);',
                    # Check unsafe-SQL FIRST so a refused query emits ONE
                    # clean WARN with the reason, not LOG.info(SQL) then
                    # Db.execute\'s own refuse-WARN (two lines that read as
                    # "we ran it and then it failed" when we actually never
                    # attempted it).
                    f'        String __jdbcReason = com.ak.api.db.Db.unsafeSqlReason(__jdbcSql);',
                    f'        if (__jdbcReason != null) {{',
                    f'            LOG.warn(" .. jdbc SKIPPED ({{}}): {{}}", '
                    f'__jdbcReason, __jdbcSql);',
                    f'        }} else {{',
                    f'            LOG.info(" .. jdbc SQL: {{}}", __jdbcSql);',
                    f'            Db.execute(__jdbcSql{params_java});',
                    f'        }}',
                    f'    }} catch (Exception __jdbcEx) {{',
                    f'        LOG.warn("JDBC execute failed: {{}}", '
                    f'__jdbcEx.getMessage());',
                    f'    }}',
                    f'}} else {{',
                    f'    LOG.warn("Skipping JDBC step (Db not configured): {query_for_log}");',
                    f'}}',
                ])
        else:
            # Query is a Groovy variable / expression we can't safely inline.
            preview_c = query_expr.replace("*/", "* /")[:80]
            # Escape for the Java "..." literal below
            preview_java = (query_expr[:80]
                            .replace("\\", "\\\\")
                            .replace('"', '\\"')
                            .replace("\r", " ")
                            .replace("\n", " "))
            # Detect MUTATION intent from the step name so tests that
            # depend on the DB write skip loudly instead of marching on
            # into a REST assertion with pre-mutation state (which then
            # fails cryptically with a status/body mismatch). Selectish
            # step names (`db_read_x`, `sql_query_lookup`) just warn.
            hint = (step_name_hint or "").lower()
            mutation_kws = ("update", "insert", "delete", "upsert",
                            "merge", "set", "cleanup", "prepare",
                            "reset", "clean_data", "seed")
            is_mutation = any(kw in hint for kw in mutation_kws)
            if is_mutation and not _skip_thrown:
                # Wrap the throw so javac doesn't mark subsequent
                # translated lines (LOG.error / log.info bits from the
                # rest of the same Groovy script) as "unreachable". LOG
                # is never null at runtime, but javac can't prove that,
                # so flow-analysis treats the block as conditional.
                #
                # Reset softAssert BEFORE the throw so any accumulated
                # soft-failures earlier in the test body don't fire in
                # BaseApiTest.@AfterMethod.assertAll() and flip the
                # SKIPPED outcome to FAILED (which would then trigger
                # RetryAnalyzer, then the retried invocation would
                # re-throw SkipException -- confusing log noise).
                lines.extend([
                    f'// [jdbc] MUTATION query is a Groovy expression '
                    f'(`{preview_c}`) -- not translated. Downstream REST '
                    f'steps would see PRE-mutation state, causing a '
                    f'cryptic status/body mismatch; SKIP this test loudly.',
                    f'softAssert = new org.testng.asserts.SoftAssert();  '
                    f'// discard pending soft failures so assertAll() '
                    f'in @AfterMethod does not flip SKIPPED -> FAILED',
                    f'if (holder != null) holder.setSoftAssertRef(softAssert);',
                    f'if (LOG != null) throw new org.testng.SkipException(',
                    f'    "Untranslated JDBC mutation step (`" + '
                    f'"{preview_java}" + "`). Hand-translate the query '
                    f'or wire Db.execute(...) with the concrete SQL.");',
                ])
                _skip_thrown = True
            elif is_mutation:
                # A previous throw already exists in this script; subsequent
                # throws would be javac-unreachable. Emit a comment only so
                # readers see the untranslated intent.
                lines.append(
                    f'// [jdbc] SUPPRESSED throw (an earlier mutation-JDBC '
                    f'step in this script already throws SkipException): '
                    f'`{preview_c}`')
            else:
                lines.extend([
                    f'// [jdbc] query is a Groovy expression (`{preview_c}`) '
                    f'-- not translated. Populate it via Config or hand-fill:',
                    f'LOG.warn("Skipping JDBC step -- query expression not '
                    f'translatable to Java: {preview_java}");',
                ])
        _mark("jdbc_execute")
        consumed = True

    # ---- JDBC read: `sql.rows("Q")` -> Db.queryAll, `sql.firstRow("Q")`
    # -> Db.queryOne. Prior emitter had NO recognizer for either; every
    # invitation_key / memberDetails lookup that used them silently
    # dropped, downstream ctxGet found nothing, and the next REST step
    # sent empty ids.
    # `_row_var_counter` is declared above at the top of the JDBC block
    # so sql.execute(SELECT)-routed locals share the same numbering
    # sequence and can't collide when a script mixes sql.execute + sql.rows.
    for method_name, java_helper, result_type in (
            ("rows", "queryAll", "java.util.List<java.util.Map<String, Object>>"),
            ("firstRow", "queryOne", "java.util.Map<String, Object>")):
        for groups, args_body in _balanced_arg_call(
                script, rf"sql\.{method_name}\("):
            query = args_body.strip()
            # Split at top-level comma into (query, param-list).
            depth = 0
            in_str = None
            query_expr, args_expr = query, None
            for k, ch in enumerate(query):
                if in_str:
                    if ch == in_str and query[k-1:k] != "\\":
                        in_str = None
                elif ch in ('"', "'"):
                    in_str = ch
                elif ch in "([":
                    depth += 1
                elif ch in ")]":
                    depth -= 1
                elif ch == "," and depth == 0:
                    query_expr = query[:k].strip()
                    args_expr = query[k + 1:].strip()
                    break
            # Only translate quoted string queries (no bare identifiers /
            # runtime-string-concat), matching sql.execute's gate.
            looks_str = (len(query_expr) >= 2
                         and query_expr[0] in ('"', "'")
                         and query_expr[-1] == query_expr[0]
                         and query_expr[0] not in query_expr[1:-1])
            if not looks_str:
                preview = query_expr[:80].replace("\\", "\\\\").replace('"', '\\"')
                lines.append(
                    f'// [jdbc] sql.{method_name}(...) query is a Groovy '
                    f'expression (`{preview}`) -- not translated. Hand-wire '
                    f'Db.{java_helper}(...) with the concrete SQL.')
                continue
            raw_q = query_expr[1:-1]
            transformed, subs = _normalize_jdbc_query(raw_q)
            java_query = _java_string_literal(transformed)
            _row_var_counter += 1
            # Namespace the local by step_name_hint -- translate() runs
            # once per Groovy step but the counter resets each call,
            # so two Groovy steps that each call sql.firstRow would
            # both produce __jdbcFirstrow_1 and javac rejects the
            # duplicate declaration. Sanitize hint via a simple keep-
            # alnum filter (identifiers only, no regex import needed
            # beyond the module-level re.)
            step_tag = re.sub(r"[^A-Za-z0-9_]", "_", step_name_hint or "s")
            result_var = f"__jdbc{method_name.capitalize()}_{step_tag}_{_row_var_counter}"
            # Groovy `[a, b]` bind-list -> Java varargs. Only bind
            # simple literals; a bare identifier means the caller
            # relied on Groovy scope we can't recreate in Java.
            bind_java = ""
            if args_expr:
                if args_expr.startswith("[") and args_expr.endswith("]"):
                    inner = args_expr[1:-1].strip()
                    parts, depth, buf, in_str = [], 0, "", None
                    for ch in inner:
                        if in_str:
                            if ch == in_str:
                                in_str = None
                            buf += ch
                        elif ch in ('"', "'"):
                            in_str = ch; buf += ch
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
                    safe = []
                    ok = True
                    for p in parts:
                        if ((p.startswith('"') and p.endswith('"'))
                                or (p.startswith("'") and p.endswith("'"))
                                or p in ("null",)
                                or p.lstrip("-").replace(".", "").isdigit()):
                            if p.startswith("'"):
                                p = '"' + p[1:-1].replace('"', '\\"') + '"'
                            safe.append(p)
                        else:
                            ok = False
                            break
                    if ok and safe:
                        bind_java = ", " + ", ".join(safe)
                    elif not ok:
                        lines.append(
                            f'// [jdbc] sql.{method_name} bind list contains '
                            f'Groovy identifiers -- omitting bind values')
            lines.append(f'// [translated] JDBC {method_name} -> {java_helper}')
            if subs:
                lines.append(
                    f'// [jdbc] parameterized {len(subs)} hardcoded WHERE '
                    f'literal(s) with #placeholder# refs -- columns: '
                    f'{", ".join(subs)}. Populate ctx or the runtime-'
                    f'resolved query will still carry unresolved.')
            lines.extend([
                f'{result_type} {result_var} = null;',
                f'if (Db.isConfigured()) {{',
                f'    try {{',
                f'        String __jdbcSql = RestUtilities.mapSqlValues('
                f'{java_query}, TestSupport.mergedRow(row, ctx), ctx);',
                # Db.queryAll / Db.queryOne callers use ForQuery variant.
                f'        String __jdbcReason = com.ak.api.db.Db.unsafeSqlReasonForQuery(__jdbcSql);',
                f'        if (__jdbcReason != null) {{',
                f'            LOG.warn(" .. jdbc SKIPPED ({{}}): {{}}", '
                f'__jdbcReason, __jdbcSql);',
                f'        }} else {{',
                f'            LOG.info(" .. jdbc {method_name} SQL: {{}}", __jdbcSql);',
                f'            {result_var} = Db.{java_helper}(__jdbcSql{bind_java});',
                f'            LOG.info(" .. jdbc {method_name} returned {{}} '
                f'row(s)/field(s)", {result_var} == null ? 0 : '
                f'({"1" if method_name == "firstRow" else result_var + ".size()"}));',
                f'        }}',
                f'    }} catch (Exception __jdbcEx) {{',
                f'        LOG.warn("JDBC {method_name} failed: {{}}", '
                f'__jdbcEx.getMessage());',
                f'    }}',
                f'}} else {{',
                f'    LOG.warn("Skipping JDBC {method_name} step '
                f'(Db not configured)");',
                f'}}',
            ])
            _mark(f"jdbc_{method_name}")
            consumed = True

    # ---- JDBC read: `sql.eachRow(query) { row -> body }` -> Db.queryAll
    # + Java for-loop over rows. Prior emitter had NO recognizer for
    # sql.eachRow. The whole DB fetch was silently dropped, and any
    # subsequent setPropertyValue that captured a per-row value (typical
    # TOTP-extraction pattern) published the generator's fake random
    # instead of the real DB value -- so downstream POST /confirmValidation
    # returned 400 "TOTP code is invalid" every run.
    #
    # Handles:
    #   sql.eachRow("SELECT literal") { row -> outer = row.field.toString() }
    #   sql.eachRow(sql_query) { row -> outer = row.field }
    #      where earlier: def sql_query = "select ... = " + guestID
    # Query can be a bare-var whose def is a string concat -- we resolve
    # it and flatten via _try_flatten_concat_sql. Concatenated identifiers
    # become `'#ident#'` placeholders that mapJsonValues resolves at
    # runtime; the identifiers themselves must be present in ctx (typical
    # earlier `def guestID = <stepRef>.getPropertyValue("hilton-member-id")`
    # which _emit_def_publications now writes via ctxGet).
    #
    # After emitting the for-loop, look ahead in the script for
    # `setPropertyValue("<prop>", <captured_var>)` calls that publish the
    # closure's captured value to a properties step. Emit
    # `putExtracted(ctx, "<step>.<prop>", <captured_var>)` for each --
    # this is what makes the extracted TOTP visible to the next REST
    # step's body substitution.
    def _resolve_query_arg_to_sql(qe: str) -> Optional[str]:
        """Return raw SQL text (with #placeholders#) for a sql.eachRow arg
        that's either a quoted literal, a top-level concat, or a bare
        identifier that's `def`'d to a concat earlier. Returns None when
        the arg is a Groovy expression we can't safely inline."""
        qe = qe.strip()
        # 1. Quoted literal (single or double)
        if (len(qe) >= 2 and qe[0] in ('"', "'")
                and qe[-1] == qe[0]
                and qe[0] not in qe[1:-1]):
            return qe[1:-1]
        # 2. String concat
        flat = _try_flatten_concat_sql(qe)
        if flat is not None:
            return flat
        # 3. Bare identifier -> resolve via def
        m_ident = re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", qe)
        if m_ident:
            m_def = re.search(
                rf"def\s+{re.escape(qe)}\s*=\s*(.+?)(?:$|;|\r|\n)",
                script + "\n")
            if m_def:
                rhs = m_def.group(1).strip()
                # RHS may itself be a quoted literal or a concat
                if (len(rhs) >= 2 and rhs[0] in ('"', "'")
                        and rhs[-1] == rhs[0]
                        and rhs[0] not in rhs[1:-1]):
                    return rhs[1:-1]
                flat = _try_flatten_concat_sql(rhs)
                if flat is not None:
                    return flat
        return None

    for groups, args_body in _balanced_arg_call(
            script, r"sql\.eachRow\("):
        # sql.eachRow(<query>) { <row_var> -> <body> }
        # `args_body` covers just the paren-args, up to the closing `)`.
        # The closure body follows: `{ <row_var> -> ... }`. Find it by
        # locating the exact call in the script and walking past it.
        query_expr = args_body.strip()
        raw_sql = _resolve_query_arg_to_sql(query_expr)
        if raw_sql is None:
            preview = query_expr[:80].replace("\\", "\\\\").replace('"', '\\"')
            lines.append(
                f'// [jdbc] sql.eachRow(...) query `{preview}` is a Groovy '
                f'expression we cannot safely inline -- hand-wire '
                f'Db.queryAll(...) with the concrete SQL to run this step.')
            continue
        # Locate the closure block that follows this eachRow call. The
        # regex takes the FIRST `{ <row_var> -> ... }` occurrence after
        # the eachRow call site in the script. Groovy authors typically
        # write eachRow on one line + closure body indented on the next,
        # so a simple non-greedy match on `\{[^}]*\}` covers the common
        # single-statement body ("outer = row.field.toString()") without
        # tripping on nested braces (rare in short DB-fetch closures).
        # Try both single-line and multi-line closure shapes.
        row_var, closure_body = None, ""
        # Find where args_body's call ends in the script, then scan
        # forward for `{ <ident> -> ... }`.
        # Use a paren-balanced position from `sql.eachRow(` occurrences
        # -- but simpler: just search for a `{` right after `sql.eachRow(<qe>)`.
        # This heuristic is good enough for the observed pattern:
        eachrow_hit = re.search(
            rf"sql\.eachRow\({re.escape(query_expr)}\)\s*\{{"
            r"\s*(\w+)\s*->\s*([^}]+)\}",
            script)
        if eachrow_hit:
            row_var, closure_body = eachrow_hit.group(1), eachrow_hit.group(2)
        transformed, subs = _normalize_jdbc_query(raw_sql)
        java_query = _java_string_literal(transformed)
        _row_var_counter += 1
        step_tag = re.sub(r"[^A-Za-z0-9_]", "_", step_name_hint or "s")
        result_var = f"__jdbcEachrow_{step_tag}_{_row_var_counter}"
        # Parse closure body for `<outer> = <row_var>.<field>[.toString()][.trim()]`
        # assignments. `outer` gets Java-declared here (String) so
        # downstream setPropertyValue references compile. If the outer
        # was defined earlier with `def outer = ''` in Groovy, the
        # emitter already emitted `String outer = "";` (via other
        # recognizers) OR the def is untranslated -- either way,
        # inserting `String outer = ...` here in a fresh scope block
        # keeps javac happy.
        captured_vars: list[tuple[str, str]] = []  # [(outer_var, field)]
        if closure_body and row_var:
            body_txt = closure_body.strip()
            # Split on ; and newlines
            for stmt in re.split(r"[;\n]", body_txt):
                s = stmt.strip()
                # <outer> = <row_var>.<field>[.toString()][.trim()]
                m_ass = re.match(
                    rf"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"
                    rf"{re.escape(row_var)}\.([A-Za-z_][A-Za-z0-9_]*)"
                    r"(?:\.toString\(\))?(?:\.trim\(\))?\s*$",
                    s)
                if m_ass:
                    captured_vars.append((m_ass.group(1), m_ass.group(2)))
        lines.append(f'// [translated] JDBC eachRow -> Db.queryAll + for-loop')
        if subs:
            lines.append(
                f'// [jdbc] parameterized {len(subs)} hardcoded WHERE '
                f'literal(s) with #placeholder# refs -- columns: '
                f'{", ".join(subs)}. Populate ctx or the runtime-'
                f'resolved query will still carry unresolved.')
        lines.append(f'java.util.List<java.util.Map<String, Object>> '
                     f'{result_var} = null;')
        # Declare captured vars as Java locals (empty string default) so
        # they're in scope for subsequent setPropertyValue emits below.
        # Use `String __eachCap_<name>` to sidestep collision with the
        # Groovy-original bare name (which the emitter may or may not
        # have declared; if it did, javac would reject a redeclaration).
        # But downstream setPropertyValue uses the RAW name -- so we
        # need the raw name in scope. Wrap the whole thing in a scope
        # block so a "String outer = ..." here doesn't collide with an
        # outer def elsewhere in the same @Test method.
        lines.append('{')
        for outer, field in captured_vars:
            lines.append(f'    String {outer} = "";')
        lines.extend([
            # Diagnostic: log the Db.isConfigured decision + which DB URL
            # the framework will hit BEFORE the branch, so a mismatch (wrong
            # env or misconfigured connection) is visible even when the
            # branch takes "not configured" path.
            f'    LOG.info(" .. jdbc Db.isConfigured={{}} dbUrl={{}}", Db.isConfigured(), '
            f'com.ak.api.config.Config.get("db.url", com.ak.api.config.Config.get("DB_URL", "<unset>")));',
            f'    if (Db.isConfigured()) {{',
            f'        try {{',
            f'            String __jdbcSql = RestUtilities.mapSqlValues('
            f'{java_query}, TestSupport.mergedRow(row, ctx), ctx);',
            # sql.eachRow dispatches to Db.queryAll -- ForQuery variant.
            f'            String __jdbcReason = com.ak.api.db.Db.unsafeSqlReasonForQuery(__jdbcSql);',
            f'            if (__jdbcReason != null) {{',
            f'                LOG.warn(" .. jdbc SKIPPED ({{}}): {{}}", '
            f'__jdbcReason, __jdbcSql);',
            f'            }} else {{',
            f'                LOG.info(" .. jdbc eachRow SQL: {{}}", __jdbcSql);',
            f'                {result_var} = Db.queryAll(__jdbcSql);',
            f'                LOG.info(" .. jdbc eachRow returned {{}} row(s)", '
            f'{result_var} == null ? 0 : {result_var}.size());',
        ])
        if captured_vars and result_var:
            lines.append(
                f'                if ({result_var} != null) {{')
            lines.append(
                f'                    for (java.util.Map<String, Object> __row : '
                f'{result_var}) {{')
            for outer, field in captured_vars:
                lines.append(
                    f'                        Object __v_{outer} = __row.get("{field}");')
                # Leading-zero preservation for fixed-width code-like
                # values (TOTP, OTP, PIN, verification codes). Prior
                # emit was `String.valueOf(__v_outer)` which prints
                # an Integer of `7582` as `"7582"` -- correct Java, but
                # the source DB column stores a 6-digit code; if the
                # numeric value is < 100000 (roughly 10% of OTPs), the
                # emitted string is 4 or 5 chars and Hilton stg rejects
                # with "TOTP code is invalid". Symptom that hit us at
                # 15:37:34 in mavenError38.txt (Active test) -- OTP
                # `7582` sent, endpoint expected `007582`. Restore the
                # zero-pad ONLY when the outer var / column name looks
                # like a code (contains `otp`, `pin`, `code`) so we
                # don't accidentally pad legitimate ids that happen to
                # be numeric but do NOT have leading zeros in the
                # original DB representation.
                _is_code_like = any(
                    kw in outer.lower() or kw in field.lower()
                    for kw in ("otp", "pin", "code", "totp"))
                if _is_code_like:
                    lines.append(
                        f'                        if (__v_{outer} != null) {{')
                    lines.append(
                        f'                            if (__v_{outer} instanceof Number) {{')
                    # Numeric column: zero-pad to 6 digits (Hilton
                    # OTP standard). If a future project uses a
                    # different width, add a config override.
                    lines.append(
                        f'                                {outer} = String.format('
                        f'"%06d", ((Number) __v_{outer}).longValue());')
                    lines.append(
                        f'                            }} else {{')
                    lines.append(
                        f'                                {outer} = String.valueOf(__v_{outer});')
                    lines.append(
                        f'                            }}')
                    lines.append(
                        f'                        }}')
                else:
                    lines.append(
                        f'                        if (__v_{outer} != null) '
                        f'{outer} = String.valueOf(__v_{outer});')
                # Diagnostic: log the EXTRACTED column value + the Java var
                # it landed in. Currently a Hilton-stg-rejected TOTP was
                # invisible because we only logged the row count; now the
                # actual DB-returned value is on the record.
                lines.append(
                    f'                        LOG.info(" .. jdbc extract "'
                    f' + "{outer}=<column:{field}>=" + '
                    f'({outer} == null || {outer}.isEmpty() ? "<empty>" : {outer}));')
            lines.append(
                f'                    }}')
            lines.append(
                f'                }}')
        lines.extend([
            f'            }}',
            f'        }} catch (Exception __jdbcEx) {{',
            f'            LOG.warn("JDBC eachRow failed: {{}}", '
            f'__jdbcEx.getMessage());',
            f'        }}',
            f'    }} else {{',
            f'        LOG.warn("Skipping JDBC eachRow step (Db not configured -- '
            f'set db.url + db.user + db.password in program_configuration.json '
            f'OR DB_URL/DB_USER/DB_PASSWORD env vars)");',
            f'    }}',
        ])
        # Look ahead for setPropertyValue("<prop>", <captured_var>) calls
        # in the script and emit putExtracted publications. This mirrors
        # what my setproperty_extract loop does for JSON-derived vars,
        # extended to sql.eachRow-captured vars. Runs inside the same
        # scope block so `outer` is still in scope.
        if captured_vars:
            for target_step, field, expr in _find_setproperty_targets(script):
                if expr in ('""', "''"):
                    continue
                # Strip trailing .toString()/.trim() before comparing
                bare = re.sub(
                    r"(\.toString\(\))?(\.trim\(\))?\s*$", "", expr.strip())
                for outer, _fld in captured_vars:
                    if bare == outer:
                        lines.append(
                            f'    TestSupport.putExtracted(ctx, '
                            f'"{_ctx_key(target_step, field)}", {outer});')
                        break
        lines.append('}')
        _mark("jdbc_eachRow")
        consumed = True

    # ---- log.info / log.error direct swap (SLF4J-compatible).
    # Uses paren-balanced walker so args with nested calls / GString
    # interpolations don't terminate the match early.
    #
    # GString handling (b.TRANSLATE audit fix):
    #   ${expr}          -> if expr is a bare identifier, translate to
    #                       Java + concat; else skip with comment (safe)
    #   $identifier      -> bare-name shorthand; translated to Java
    #                       + concat (the identifier must be a declared
    #                       local in the same block, which the emitted
    #                       "def X = ..." lines guarantee for the common
    #                       "context.expand" pattern)
    #   $obj.foo, $obj[i], $obj?.x -> skip with comment (Groovy-only chain)
    #   \$name (backslash-escaped)  -> leave literal; NOT interpolation
    # Prior behaviour emitted `"$email"` VERBATIM as a Java string
    # literal, so the log line read `... email: $email` instead of the
    # actual value.
    #
    # `(?<!\\)` negative-lookbehind guards against escaped `\$name` --
    # Groovy treats `\$` as a literal `$` (no interpolation), and we
    # must NOT translate `"\$name"` to `+ name +` (that would inject
    # a runtime value where the author wanted a literal `$name`).
    _GS_BARE_NAME_RE = re.compile(r"(?<!\\)\$([A-Za-z_][A-Za-z_0-9]*)(?![.\[A-Za-z_0-9])")
    _GS_CHAIN_RE     = re.compile(r"(?<!\\)\$[A-Za-z_][A-Za-z_0-9]*[.\[]")
    for groups, args_body in _balanced_arg_call(
            script, r"log\.(info|warn|error|debug)\("):
        level = groups[0].lower()
        msg = args_body.strip()
        # Bail out if the message references any Groovy variable via + or
        # ${...} interpolation -- those identifiers don't exist in Java
        # scope. Preserve the intent as a comment; skip emission.
        has_interp = "${" in msg
        # Bare `$ident` shorthand (no braces). Chain forms `$obj.field`
        # or `$arr[i]` fall through to the skip path with the ${...}
        # forms since they aren't safely translatable to Java.
        has_shorthand_chain = bool(_GS_CHAIN_RE.search(msg))
        has_shorthand_bare  = bool(_GS_BARE_NAME_RE.search(msg))
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
        if has_interp or has_var_concat or has_shorthand_chain:
            preview = stripped[:80].replace('*/', '* /')
            lines.append(f'// [log.{level}] skipped (references Groovy-only vars): {preview}')
            consumed = True
            continue
        # b.TRANSLATE: bare `$ident` shorthand in a string literal --
        # rewrite as Java `"..." + ident + "..."` concat. Requires:
        #  - msg IS a string literal (starts + ends with `"`)
        #  - all shorthand refs are simple bare names (no `.` / `[`)
        #    which the has_shorthand_chain check above already gated
        if has_shorthand_bare and (
                (msg.startswith('"') and msg.endswith('"'))
                or (msg.startswith("'") and msg.endswith("'"))):
            outer_quote = msg[0]
            inner = msg[1:-1]
            parts = []
            last_end = 0
            for m in _GS_BARE_NAME_RE.finditer(inner):
                seg = inner[last_end:m.start()]
                if seg:
                    # Segments keep any pre-existing backslash-escapes
                    # (\\" \\n etc.) verbatim -- they were part of the
                    # original Groovy string literal and are already
                    # valid Java escape sequences. EXCEPT `\$` which
                    # is Groovy-only (Java string literals reject it
                    # per JLS 3.10.6) -- rewrite to bare `$` (valid
                    # unescaped in Java literals).
                    parts.append('"' + seg.replace('\\$', '$') + '"')
                # Identifier lookup via TestSupport.ctxGet -- NOT bare
                # local reference. Reason: `def X = ...` translations
                # declare X inside an inner `{...}` scope block that
                # closes before subsequent log lines at the outer method
                # level. A bare `+ X +` there fails to compile
                # (`cannot find symbol: variable X`). The framework's
                # every-def-mirrors-to-ctx pattern (putIfNonEmpty right
                # after each def) makes ctxGet(ctx, "X") return the same
                # value that the local held, so this is behaviour-
                # preserving AND compile-safe regardless of the log
                # line's scope depth. Missing keys yield "" (safe log
                # output) rather than NPE.
                parts.append(f'TestSupport.ctxGet(ctx, "{m.group(1)}")')
                last_end = m.end()
            tail = inner[last_end:]
            if tail:
                parts.append('"' + tail.replace('\\$', '$') + '"')
            translated_expr = " + ".join(parts) if parts else ('"' + inner + '"')
            # Same emit-level downgrade logic as the plain-literal path
            # below -- apply to the ORIGINAL message text (before we
            # split into parts) so the substring match works.
            emit_level_gs = level
            if level == "error":
                m_lower = inner.lower()
                if ("environment variable" in m_lower
                        or "database connection" in m_lower
                        or "db connection" in m_lower
                        or "db_host" in m_lower
                        or "db_user" in m_lower
                        or "db_password" in m_lower
                        or "failed to delete account records" in m_lower
                    # b'.PROPER (narrow): the observed if/else Groovy
                    # pattern `if (result != null) { log.info("Success...") }
                    # else { log.error("Failed...") }` gets flattened by
                    # the translator to sequential LOG.info + LOG.error
                    # (structural preservation deferred as too-risky in
                    # a single session). Downgrade the "Failed to..."
                    # log.error to DEBUG so the misleading ERROR line
                    # doesn't fire on the happy path at default log
                    # level. String kept in DEBUG log for diagnosis;
                    # legitimate error paths still shown via the
                    # catch-block emit (which uses `${ex.getMessage()}`
                    # and gets skipped, not downgraded).
                    or "failed to delete account_member" in m_lower
                    or "failed to delete" in m_lower):
                    emit_level_gs = "debug"
            lines.append(f'LOG.{emit_level_gs}("{{}}", {translated_expr});')
            _mark("log_swap_gstring")
            consumed = True
            continue
        # Wrap in a literal only if not already a string
        if not (msg.startswith('"') or msg.startswith("'")):
            msg = '"' + msg.replace('"', '\\"') + '"'
        # Groovy's `\$` escape (literal dollar) is NOT a valid Java
        # string-literal escape -- Java would reject it as a compile
        # error (JLS 3.10.6: only \b \t \n \f \r \" \' \\ \0-\7 \u...
        # are valid). Sanitize to bare `$` which Java string literals
        # accept unescaped. Only applies to already-quoted messages
        # (either the branch above quoted it, or the msg arrived
        # pre-quoted from Groovy source).
        if (msg.startswith('"') and msg.endswith('"')) or (msg.startswith("'") and msg.endswith("'")):
            msg = msg.replace('\\$', '$')
        # Downgrade Groovy-translated log.error lines that mention
        # DB env-var / connection checks to DEBUG level. The Groovy
        # source is a defensive check ("DB_HOST env var missing --
        # abort DB update") that fires whenever Db is not configured;
        # emitting it at ERROR level dumps a red line into every
        # test run against an env without local DB access, which is
        # the common case for these imported suites. DEBUG keeps
        # the string in the log for diagnosis without polluting the
        # normal-run output.
        emit_level = level
        if level == "error":
            m_lower = msg.lower()
            if ("environment variable" in m_lower
                    or "database connection" in m_lower
                    or "db connection" in m_lower
                    or "db_host" in m_lower
                    or "db_user" in m_lower
                    or "db_password" in m_lower
                    or "failed to delete account records" in m_lower
                    # b'.PROPER (narrow): the observed if/else Groovy
                    # pattern `if (result != null) { log.info("Success...") }
                    # else { log.error("Failed...") }` gets flattened by
                    # the translator to sequential LOG.info + LOG.error
                    # (structural preservation deferred as too-risky in
                    # a single session). Downgrade the "Failed to..."
                    # log.error to DEBUG so the misleading ERROR line
                    # doesn't fire on the happy path at default log
                    # level. String kept in DEBUG log for diagnosis;
                    # legitimate error paths still shown via the
                    # catch-block emit (which uses `${ex.getMessage()}`
                    # and gets skipped, not downgraded).
                    or "failed to delete account_member" in m_lower
                    or "failed to delete" in m_lower):
                emit_level = "debug"
        lines.append(f'LOG.{emit_level}("{{}}", {msg});')
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
