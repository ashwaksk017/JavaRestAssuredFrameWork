"""Emit phases as DATA (Stage 1b, behind ``--phase-specs``).

Pure text generators over the specs ``phase_model`` captures. Nothing here
touches the converter's state; ``ra_converter`` calls these with what it
already knows and writes the files.

What comes out, per converted suite:

* ``support/<suite>/cases/<TestClass>Phases.java`` -- one per test class:
  registers each case's phases and verifies in ``CaseRegistry`` as
  ``PhaseSpec`` builders, plus one static hook method per phase that
  carried translated Groovy / transfers / assertions outside the closed
  set (the "leftover" lines of the old body, verbatim, under a prelude of
  locals so they compile unchanged).
* ``support/<suite>/cases/CaseIndex.java`` -- loads every Phases class.
* ``support/<suite>/Calls.java`` -- one switch over the suite's typed
  client operations: the ONLY per-shape code left.
* vocabulary methods for ``ScenarioSteps`` -- ``enrollGuest()`` and
  ``enrollGuest(String step)`` once per name, cross-suite.

Unit-testable alone: ``python tools/ra_converter/test_phase_emit.py``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Java string literal
# ---------------------------------------------------------------------------

def jstr(s: str) -> str:
    s = "" if s is None else str(s)
    s = (s.replace("\\", "\\\\").replace('"', '\\"')
          .replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t"))
    return '"' + s + '"'


# ---------------------------------------------------------------------------
# Refs: the Java expression the renderer emitted -> a Ref constructor
# ---------------------------------------------------------------------------

_CTXGET = re.compile(r'^(?:TestSupport|ImportedScenario|com\.hi\.api\.support\.\w+\.TestSupport)\.ctxGet\(ctx,\s*"((?:[^"\\]|\\.)*)"\)$')
_CTX_DEFAULT = re.compile(r'^ctx\.getOrDefault\("((?:[^"\\]|\\.)*)",\s*""\)$')
_RESP = re.compile(r'^(?:com\.hi\.api\.rest\.utilities\.)?RestUtilities\.safeJsonExtract\((\w+),\s*"((?:[^"\\]|\\.)*)"\)$')
_ROW = re.compile(r'^row\.getOrDefault\("((?:[^"\\]|\\.)*)",\s*"((?:[^"\\]|\\.)*)"\)$')
_LIT = re.compile(r'^"((?:[^"\\]|\\.)*)"$')
_RES_VAR = re.compile(r"\b([A-Za-z_]\w*Res(?:_\d+)?)\b")


def _unj(s: str) -> str:
    """Undo Java escaping of a string literal body."""
    return s.replace('\\"', '"').replace("\\\\", "\\")


def parse_ref(expr: str, var_to_step: dict[str, str]) -> tuple:
    """(kind, ...) for one path-arg / token expression.

    kinds: ('ctx', key) ('resp', step, jsonPath) ('row', col, fallback)
           ('lit', value) ('expr', javaExpression)
    """
    e = (expr or "").strip()
    while e.startswith("(") and e.endswith(")") and _balanced(e[1:-1]):
        e = e[1:-1].strip()
    m = _CTXGET.match(e) or _CTX_DEFAULT.match(e)
    if m:
        return ("ctx", _unj(m.group(1)))
    m = _RESP.match(e)
    if m and m.group(1) in var_to_step:
        return ("resp", var_to_step[m.group(1)], _unj(m.group(2)))
    m = _ROW.match(e)
    if m:
        return ("row", _unj(m.group(1)), _unj(m.group(2)))
    m = _LIT.match(e)
    if m:
        return ("lit", _unj(m.group(1)))
    return ("expr", e)


def _balanced(s: str) -> bool:
    depth = 0
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def ref_java(ref: tuple, var_to_step: dict[str, str] | None = None) -> str:
    kind = ref[0]
    if kind == "ctx":
        return f"Ref.ctx({jstr(ref[1])})"
    if kind == "resp":
        return f"Ref.resp({jstr(ref[1])}, {jstr(ref[2])})"
    if kind == "row":
        return f"Ref.row({jstr(ref[1])}, {jstr(ref[2])})"
    if kind == "lit":
        return f"Ref.literal({jstr(ref[1])})"
    expr = ref[1]
    body = rewrite_res_vars(expr, var_to_step or {})
    desc = expr if len(expr) <= 60 else expr[:57] + "..."
    return ("Ref.expr(" + jstr(desc) + ", c -> { "
            "java.util.Map<String, String> ctx = c.ctx; java.util.Map<String, String> row = c.row; "
            f"return String.valueOf({body}); }})")


def rewrite_res_vars(java: str, var_to_step: dict[str, str]) -> str:
    """`http_request_200_enroll_guestRes` -> `c.response("http_request_200_enroll_guest")`."""
    def sub(m):
        v = m.group(1)
        if v in var_to_step:
            return f"c.response({jstr(var_to_step[v])})"
        return v
    return _RES_VAR.sub(sub, java)


# ---------------------------------------------------------------------------
# Splitting a rendered REST body: chain -> spec, checks/extracts -> spec, rest -> hook
# ---------------------------------------------------------------------------

_CHAIN_START = re.compile(r"^\s*(?:this\.\w+|Response \w+) = RestStep\.exec\(")
_CHAIN_END = re.compile(r"\)\);\s*$")
_DROP = (
    "// ==== REST step:", "// [auth override]", "// [Valid HTTP Status Codes]",
    "// [auto-extract]", "// [auto-extract-rawreq]",
)
_RAWREQ = re.compile(r'putExtracted\(ctx,\s*"(\w+)_RawRequest",\s*RestStep\.lastResolvedBody\(\)')
_EXTRACT = re.compile(
    r'^\s*(?:TestSupport|ImportedScenario)\.putExtracted\(ctx,\s*"((?:[^"\\]|\\.)*)",\s*'
    r'(?:com\.hi\.api\.rest\.utilities\.)?RestUtilities\.safeJsonExtract\((\w+),\s*"((?:[^"\\]|\\.)*)"\)\);\s*$')
_EXTRACT_WHOLE = re.compile(
    r'^\s*(?:TestSupport|ImportedScenario)\.putExtracted\(ctx,\s*"((?:[^"\\]|\\.)*)",\s*'
    r'(?:com\.hi\.api\.rest\.utilities\.)?RestUtilities\.getResponseAsString\((\w+)\)\);\s*$')
_A_EQ = re.compile(r'^\s*ResponseAsserts\.jsonEquals\(softAssert,\s*(\w+),\s*ctx,\s*row,\s*"((?:[^"\\]|\\.)*)",\s*"((?:[^"\\]|\\.)*)",\s*"((?:[^"\\]|\\.)*)"\);\s*$')
_A_TREE = re.compile(r'^\s*ResponseAsserts\.jsonTreeEquals\(softAssert,\s*(\w+),\s*ctx,\s*row,\s*"((?:[^"\\]|\\.)*)",\s*"((?:[^"\\]|\\.)*)",\s*"((?:[^"\\]|\\.)*)"\);\s*$')
_A_EXISTS = re.compile(r'^\s*ResponseAsserts\.jsonExists\(softAssert,\s*(\w+),\s*row,\s*"((?:[^"\\]|\\.)*)",\s*"((?:[^"\\]|\\.)*)"\);\s*$')
_A_ABSENT = re.compile(r'^\s*ResponseAsserts\.jsonAbsent\(softAssert,\s*(\w+),\s*"((?:[^"\\]|\\.)*)"\);\s*$')
_A_COUNT = re.compile(r'^\s*ResponseAsserts\.jsonCount\(softAssert,\s*(\w+),\s*row,\s*"((?:[^"\\]|\\.)*)",\s*"((?:[^"\\]|\\.)*)",\s*(-?\d+)\);\s*$')
_A_VALUE = re.compile(r'^\s*ResponseAsserts\.valueInResponse\(softAssert,\s*(\w+),\s*"((?:[^"\\]|\\.)*)",\s*"((?:[^"\\]|\\.)*)",\s*"((?:[^"\\]|\\.)*)"\);\s*$')


@dataclass
class Split:
    checks: list = field(default_factory=list)     # (kind, jsonPath, expected)
    extracts: list = field(default_factory=list)   # (ctxKey, jsonPath|'' , kind)
    leftover: list = field(default_factory=list)   # verbatim Java lines for the hook
    chain_seen: bool = False


def split_rest_body(lines: list[str], res_var: str, step_sid: str) -> Split:
    """Classify the lines the old renderer emitted for ONE REST step."""
    out = Split()
    in_chain = False
    for raw in lines:
        line = raw.rstrip()
        s = line.strip()
        if in_chain:
            if _CHAIN_END.search(s):
                in_chain = False
            continue
        if _CHAIN_START.match(line):
            out.chain_seen = True
            if not _CHAIN_END.search(s):
                in_chain = True
            continue
        if not s:
            continue
        if any(s.startswith(d) for d in _DROP):
            continue
        m = _RAWREQ.search(s)
        if m and m.group(1) == step_sid:
            continue
        m = _EXTRACT.match(line)
        if m and m.group(2) == res_var:
            out.extracts.append((_unj(m.group(1)), _unj(m.group(3)), "json"))
            continue
        m = _EXTRACT_WHOLE.match(line)
        if m and m.group(2) == res_var:
            out.extracts.append((_unj(m.group(1)), "", "whole"))
            continue
        m = _A_EQ.match(line)
        if m and m.group(1) == res_var and m.group(2) == step_sid:
            out.checks.append(("equals", _unj(m.group(3)), _unj(m.group(4))))
            continue
        m = _A_TREE.match(line)
        if m and m.group(1) == res_var and m.group(2) == step_sid:
            out.checks.append(("treeEquals", _unj(m.group(3)), _unj(m.group(4))))
            continue
        m = _A_EXISTS.match(line)
        if m and m.group(1) == res_var and m.group(2) == step_sid:
            out.checks.append(("exists", _unj(m.group(3)), ""))
            continue
        m = _A_ABSENT.match(line)
        if m and m.group(1) == res_var:
            out.checks.append(("absent", _unj(m.group(2)), ""))
            continue
        m = _A_COUNT.match(line)
        if m and m.group(1) == res_var and m.group(2) == step_sid:
            out.checks.append(("count", _unj(m.group(3)), m.group(4)))
            continue
        m = _A_VALUE.match(line)
        if m and m.group(1) == res_var:
            out.checks.append(("valueInResponse", _unj(m.group(3)), _unj(m.group(2))))
            continue
        out.leftover.append(line)
    return out


def hook_blockers(leftover: list[str], allow_rest: bool = False) -> str | None:
    """Why these lines cannot live in a hook (None = they can).

    `allow_rest` is for the BOOTSTRAP hook only. A REST call is refused
    in a PHASE hook because that hook runs after a phase's response, and
    a second call there breaks the step accounting (__restStepIdx) and
    the response wiring. A bootstrap hook has neither problem: it runs
    before any phase, and its generated prelude already declares ctx /
    row / softAssert / holder / testCaseId / client -- exactly what
    RestStep.exec needs.

    Refusing it there cost 26 cases their tokenRequest. A case whose
    setup matches a shared SetupHelper flow calls runSetup(...), which is
    a method call and passes; one with no matching flow keeps the token
    inline, hit this blocker, and had its ENTIRE bootstrap emitted as
    nothing -- so every call in it went out unauthenticated.

    The stop-marker and early-return blockers still apply either way:
    those genuinely cannot be expressed in a hook.
    """
    joined = "\n".join(leftover)
    if "__stopAfter" in joined or "__restStepIdx" in joined:
        return "prefix-merged stop marker"
    if re.search(r"\breturn this;|\breturn self\(\);", joined):
        return "early return"
    if not allow_rest and "RestStep.exec(" in joined:
        return "a second REST call"
    return None


# ---------------------------------------------------------------------------
# Java generators
# ---------------------------------------------------------------------------

def spec_builder_java(spec, split: Split, template_java_expr: str | None,
                      var_to_step: dict[str, str], hook_ref: str | None,
                      indent: int = 20) -> str:
    """`PhaseSpec.phase(...)....build()` for one captured phase_model.PhaseSpec."""
    pad = " " * indent
    parts = [f"PhaseSpec.phase({jstr(spec.sid)})"]
    parts.append(f".engine({jstr(spec.engine_id)})")
    parts.append(f".{spec.verb.lower() if spec.verb.upper() in ('GET','POST','PUT','PATCH','DELETE') else 'post'}({jstr(spec.path)})")
    if spec.path_refs:
        parts.append(".args(" + ", ".join(ref_java(r, var_to_step) for r in spec.path_refs) + ")")
    parts.append(f".token({ref_java(spec.token_ref, var_to_step)})")
    if template_java_expr:
        parts.append(f".template({template_java_expr})")
    if spec.regen:
        parts.append(".regenIdentity()")
    parts.append(f".expect({spec.expected_status})")
    if spec.poll:
        f, key, dflt = spec.poll
        parts.append(f".pollUntilPresent({jstr(f)}, {jstr(key)}, {dflt}L)")
    for k, v in spec.query:
        parts.append(f".query({jstr(k)}, {jstr(v)})")
    for k, v in spec.headers:
        parts.append(f".header({jstr(k)}, {jstr(v)})")
    # A key written twice in the old body kept the LAST value (putExtracted
    # overwrites). Keep the last occurrence, in the old order: the auto-
    # extracts of the REST block first, then the transfer steps after it.
    ordered: list[tuple] = []
    for key, kind, path in spec.extracts:
        ordered.append((key, kind, path))
    for key, path, kind in split.extracts:
        ordered.append((key, kind, path))
    last: dict[str, int] = {}
    for i, (key, _k, _p) in enumerate(ordered):
        last[key] = i
    for i, (key, kind, path) in enumerate(ordered):
        if last[key] != i:
            continue
        if kind == "json":
            parts.append(f".extract({jstr(key)}, {jstr(path)})")
        elif kind == "whole":
            parts.append(f".extractWhole({jstr(key)})")
        elif kind == "rawreq":
            parts.append(f".extractRawRequest({jstr(key)})")
        else:
            parts.append(f".extractRawRequestPath({jstr(key)}, {jstr(path)})")
    for kind, path, expected in split.checks:
        if kind == "equals":
            parts.append(f".equals({jstr(path)}, {jstr(expected)})")
        elif kind == "treeEquals":
            parts.append(f".treeEquals({jstr(path)}, {jstr(expected)})")
        elif kind == "exists":
            parts.append(f".exists({jstr(path)})")
        elif kind == "absent":
            parts.append(f".absent({jstr(path)})")
        elif kind == "count":
            parts.append(f".count({jstr(path)}, {expected})")
        else:
            parts.append(f".valueInResponse({jstr(expected)}, {jstr(path)})")
    if hook_ref:
        parts.append(f".after({hook_ref})")
    parts.append(".build()")
    return ("\n" + pad).join(parts)


_HOOK_PRELUDE = """        java.util.Map<String, String> ctx = c.ctx;
        java.util.Map<String, String> row = c.row;
        org.testng.asserts.SoftAssert softAssert = c.softAssert;
        com.hi.api.rest.utilities.RestLoggerUtilityDataHolder holder = c.holder;
        String testCaseId = c.testCaseId;
        com.hi.api.support.ImportedRestClient client = c.client;
        com.hi.api.domain.DomainApis __domainApis = com.hi.api.domain.DomainApis.bind(client);
        com.hi.api.domain.guest.GuestApi guests = __domainApis.guests();
        com.hi.api.domain.account.ProgramAccountApi accounts = __domainApis.accounts();
        com.hi.api.domain.member.MemberApi members = __domainApis.members();
        Expected exp = Expected.from(row == null ? null : row.get("expected"));"""


def hook_java(name: str, leftover: list[str], res_var: str, var_to_step: dict[str, str]) -> str:
    """A static hook method: the leftover lines verbatim under a prelude of locals."""
    used = set(_RES_VAR.findall("\n".join(leftover)))
    decls = []
    if res_var in used:
        decls.append(f"        io.restassured.response.Response {res_var} = res;")
    for v in sorted(used):
        if v != res_var and v in var_to_step:
            decls.append(f"        io.restassured.response.Response {v} = c.response({jstr(var_to_step[v])});")
    body = "\n".join(("        " + ln) if ln else "" for ln in leftover)
    return (f"    @SuppressWarnings(\"unused\")\n"
            f"    static void {name}(io.restassured.response.Response res, PhaseContext c) throws Exception {{\n"
            f"{_HOOK_PRELUDE}\n" + ("\n".join(decls) + "\n" if decls else "")
            + f"{body}\n    }}\n")


def chain_calls(entries: list[tuple[str, str]], force_step=()) -> list[str]:
    """`.vocab()` or `.vocab("step")` per the repeat rule: any repeat names ALL of them.

    `force_step`: names that ALSO exist as a text-path method (a compound
    phase the vote allocator named `readProgramAccount`); the no-arg call
    would resolve to that one, so these always name their step.
    """
    counts: dict[str, int] = {}
    for vocab, _step in entries:
        counts[vocab] = counts.get(vocab, 0) + 1
    out = []
    for vocab, step in entries:
        name = safe_vocab(vocab)
        out.append(f".{name}()" if counts[vocab] == 1 and vocab not in force_step
                   else f".{name}({jstr(step)})")
    return out


def phases_class_java(pkg: str, cls: str, imports: list[str], cases: list[dict], hooks: list[str]) -> str:
    """One `<TestClass>Phases` file: registration only, no builders.

    `cases` entries carry REFERENCES (`Specs1::spec37`), not Java. Each
    builder lives once, suite-wide, in a `Specs<N>` class.

    Before this, factories were private to each class, so a builder used
    by twelve classes was emitted twelve times -- 3,099 factories for
    1,378 distinct builders, 17,948 redundant lines.

    Registration keys on the case id, never the class name, so how cases
    group into files is free to change.
    """
    lines = [f"package {pkg};", ""]
    for i in sorted(set(imports)):
        lines.append(f"import {i};")
    lines += ["", "/**",
              " * Generated by ra_converter: which phases each case runs, as data.",
              " * The specs themselves live in Specs<N>; the engines in Calls.",
              " */",
              f"public final class {cls} {{",
              "",
              "    private static volatile boolean registered;",
              "",
              f"    private {cls}() {{",
              "    }",
              ""]
    # identical entry lists -> one registration loop
    groups: dict[tuple, list[str]] = {}
    order: list[tuple] = []
    for c in cases:
        key = tuple((e["vocab"], e["step"], bool(e["verify"]),
                     tuple(e.get("spec_javas") or [e["spec_java"]]))
                    for e in c["entries"])
        boot = tuple(c.get("bootstrap") or [])
        key = (("__bootstrap__", str(c.get("rest_offset", 0)), True, boot),) + key
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(c["case"])
    body = ["    public static synchronized void register() {",
            "        if (registered) {",
            "            return;",
            "        }",
            "        registered = true;"]
    for key in order:
        ids = groups[key]
        if len(ids) == 1:
            body.append(f"        CaseRegistry.register({jstr(ids[0])})")
            pad = "            "
        else:
            body.append("        for (String id : new String[] {"
                        + ", ".join(jstr(i) for i in ids) + "}) {")
            body.append("            CaseRegistry.register(id)")
            pad = "                "
        for idx, (vocab, step, verify, refs_t) in enumerate(key):
            end = ";" if idx == len(key) - 1 else ""
            refs = ", ".join(refs_t)
            if vocab == "__bootstrap__":
                if refs_t:
                    body.append(f"{pad}.bootstrap({refs})")
                if step != "0":
                    body.append(f"{pad}.restOffset({step})")
                if end:
                    body[-1] += end
                continue
            kind = "verify" if verify else "phase"
            body.append(f"{pad}.{kind}({jstr(vocab)}, {jstr(step)}, {refs}){end}")
        if not key:
            body[-1] += ";"
        if len(ids) > 1:
            body.append("        }")
    body.append("    }")
    lines += body
    lines.append("")
    for h in hooks:
        lines.append(h)
    lines.append("}")
    return "\n".join(lines) + "\n"


def specs_class_java(pkg: str, cls: str, imports: list[str], specs: list) -> str:
    """support/<suite>/cases/Specs<N>.java: every DISTINCT builder, once.

    `specs` is [(name, builder_java)]. Package-private so the Phases
    classes beside it can take `Specs1::spec37` method references.
    """
    lines = [f"package {pkg};", ""]
    for i in sorted(set(imports)):
        lines.append(f"import {i};")
    lines += ["", "/**",
              " * Generated by ra_converter: one method per DISTINCT phase spec in",
              " * this suite. Phases classes reference these, so a builder shared by",
              " * many cases is stored once rather than copied per class.",
              " */",
              f"public final class {cls} {{",
              "",
              f"    private {cls}() {{",
              "    }",
              ""]
    for name, java in specs:
        lines.append(f"    static PhaseSpec {name}() {{")
        lines.append("        return " + java + ";")
        lines.append("    }")
        lines.append("")
    lines.append("}")
    return "\n".join(lines) + "\n"


def hooks_class_java(pkg: str, imports: list[str], hooks: list[str], cls: str = "Hooks") -> str:
    """support/<suite>/cases/Hooks<N>.java: each distinct translated block once,
    150 per file so no file nears IntelliJ's code-insight size limit."""
    lines = [f"package {pkg};", ""]
    for i in sorted(set(imports)):
        lines.append(f"import {i};")
    lines += ["", "/**",
              " * Generated by ra_converter: the translated Groovy / transfers / script",
              " * assertions that follow a REST call, one method per distinct block. A",
              f" * PhaseSpec names the one it needs with `.after({cls}::hookN_step)`.",
              " */",
              f"public final class {cls} {{",
              "",
              f"    private static final Logger LOG = LoggerFactory.getLogger({cls}.class);",
              "",
              f"    private {cls}() {{",
              "    }",
              ""]
    for h in hooks:
        lines.append(h)
    lines.append("}")
    return "\n".join(lines) + "\n"


def case_index_java(pkg: str, phases_classes: list[str]) -> str:
    lines = [f"package {pkg};", "",
             "/** Generated by ra_converter: loads every <TestClass>Phases of this suite. */",
             "public final class CaseIndex {",
             "    private static volatile boolean loaded;",
             "",
             "    private CaseIndex() {",
             "    }",
             "",
             "    public static synchronized void ensureLoaded() {",
             "        if (loaded) {",
             "            return;",
             "        }",
             "        loaded = true;"]
    for c in sorted(set(phases_classes)):
        lines.append(f"        {c}.register();")
    lines += ["    }", "}"]
    return "\n".join(lines) + "\n"


def engine_id(java_name: str, n_path: int, takes_query: bool, takes_headers: bool, takes_body: bool) -> str:
    return f"{java_name}/{n_path}{'q' if takes_query else ''}{'h' if takes_headers else ''}{'b' if takes_body else ''}"


def calls_java(pkg: str, ops: list[tuple[str, int, bool, bool, bool]]) -> str:
    """`Calls.call(c, p)`: the switch over this suite's typed client operations.

    ops: (javaName, pathParamCount, takesQuery, takesHeaders, takesBody)
    """
    lines = [f"package {pkg};", "",
             "import com.hi.api.rest.utilities.phase.PhaseContext;",
             "import com.hi.api.rest.utilities.phase.PhaseRunner;",
             "import com.hi.api.rest.utilities.phase.PhaseSpec;",
             "import io.restassured.response.Response;", "",
             "/**",
             " * Generated by ra_converter: one call per typed client operation of this",
             " * suite. This is the only per-shape code left; everything a case adds",
             " * (ids, template, status, checks) arrives in the PhaseSpec.",
             " */",
             "public final class Calls {",
             "",
             "    private Calls() {",
             "    }",
             "",
             "    public static Response call(PhaseContext c, PhaseSpec p) throws Exception {",
             "        switch (p.engine) {"]
    seen = set()
    for java_name, n_path, q, h, b in sorted(set(ops)):
        eid = engine_id(java_name, n_path, q, h, b)
        if eid in seen:
            continue
        seen.add(eid)
        args = ["p.token.resolve(c)"] + [f"p.arg({i}).resolve(c)" for i in range(n_path)]
        if q:
            args.append("q")
        if h:
            args.append("h")
        if b:
            args.append("body")
        lines.append(f"            case {jstr(eid)}:")
        lines.append(f"                return PhaseRunner.run(p, c, (body, q, h) -> c.client.{java_name}({', '.join(args)}));")
    lines += ["            default:",
              "                throw new IllegalStateException(\"no engine for `\" + p.engine",
              "                        + \"` -- the client and the phase table were emitted by different runs\");",
              "        }", "    }", "}"]
    return "\n".join(lines) + "\n"


# ScenarioSteps' own members; a vocabulary name that lands on one of these
# would override it. The vocabulary is business verbs + nouns, so this is
# defensive -- but `start`/`complete` are plausible business words.
RESERVED_VOCAB = frozenset({
    "start", "bootstrap", "self", "complete", "current", "runPhase", "runVerify",
    "dispatch", "phaseContext", "requirePhases", "runParts", "toString", "hashCode",
    "equals", "getClass", "notify", "notifyAll", "wait",
})


def safe_vocab(v: str) -> str:
    return v + "Phase" if v in RESERVED_VOCAB else v


def verify_vocab_methods_java(vocabs: list[str]) -> str:
    """`verifyProgramAccount()` on ScenarioSteps, returning S so it chains.

    A verify runs the same way a phase does -- runVerify -> runParts ->
    dispatch -- but it was only reachable as a static call AFTER the chain:

        Onboarding.start(row, id).enrollGuest()....complete();
        Insights.verifyProgramAccount(scenario, expected);

    so the trailing read-back it performs was invisible to anyone counting
    the chain against the ReadyAPI case. Returning S puts it where it
    happens, in order, with the steps around it.

    The step overload matters: runVerify(vocab, null) resolves through
    only(vocab), which throws when a case registers two verifies under one
    vocabulary. Callers pass the step name in that case.
    """
    out = []
    for v in sorted({safe_vocab(x) for x in vocabs}):
        out.append(
            f"    public S {v}() throws Exception {{\n"
            f"        runVerify({jstr(v)}, null);\n"
            f"        return self();\n"
            f"    }}\n")
        out.append(
            f"    public S {v}(String step) throws Exception {{\n"
            f"        runVerify({jstr(v)}, step);\n"
            f"        return self();\n"
            f"    }}\n")
    return "\n".join(out)


def vocab_methods_java(vocabs: list[str], taken=()) -> str:
    """`enrollGuest()` / `enrollGuest(String step)` on ScenarioSteps, once per name.

    A name in `taken` is also a text-path method (no-arg); only the step
    overload is emitted for it, and chains always pass the step.
    """
    out = []
    for v in sorted({safe_vocab(x) for x in vocabs}):
        if v not in taken:
            out.append(
                f"    public S {v}() throws Exception {{\n"
                f"        return runPhase({jstr(v)}, null);\n"
                f"    }}\n")
        out.append(
            f"    public S {v}(String step) throws Exception {{\n"
            f"        return runPhase({jstr(v)}, step);\n"
            f"    }}\n")
    return "\n".join(out)
