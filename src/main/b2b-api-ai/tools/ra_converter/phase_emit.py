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

_CTXGET = re.compile(r'^(?:TestSupport|ImportedScenario|com\.ak\.api\.support\.\w+\.TestSupport)\.ctxGet\(ctx,\s*"((?:[^"\\]|\\.)*)"\)$')
_CTX_DEFAULT = re.compile(r'^ctx\.getOrDefault\("((?:[^"\\]|\\.)*)",\s*""\)$')
_RESP = re.compile(r'^(?:com\.ak\.api\.rest\.utilities\.)?RestUtilities\.safeJsonExtract\((\w+),\s*"((?:[^"\\]|\\.)*)"\)$')
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
    r'(?:com\.ak\.api\.rest\.utilities\.)?RestUtilities\.safeJsonExtract\((\w+),\s*"((?:[^"\\]|\\.)*)"\)\);\s*$')
_EXTRACT_WHOLE = re.compile(
    r'^\s*(?:TestSupport|ImportedScenario)\.putExtracted\(ctx,\s*"((?:[^"\\]|\\.)*)",\s*'
    r'(?:com\.ak\.api\.rest\.utilities\.)?RestUtilities\.getResponseAsString\((\w+)\)\);\s*$')
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


def hook_blockers(leftover: list[str]) -> str | None:
    """Why these lines cannot live in a hook (None = they can)."""
    joined = "\n".join(leftover)
    if "__stopAfter" in joined or "__restStepIdx" in joined:
        return "prefix-merged stop marker"
    if re.search(r"\breturn this;|\breturn self\(\);", joined):
        return "early return"
    if "RestStep.exec(" in joined:
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
    seen = set()
    for key, kind, path in spec.extracts:
        if (key, kind) in seen:
            continue
        seen.add((key, kind))
        if kind == "json":
            parts.append(f".extract({jstr(key)}, {jstr(path)})")
        elif kind == "whole":
            parts.append(f".extractWhole({jstr(key)})")
        elif kind == "rawreq":
            parts.append(f".extractRawRequest({jstr(key)})")
        else:
            parts.append(f".extractRawRequestPath({jstr(key)}, {jstr(path)})")
    for key, path, kind in split.extracts:
        if (key, kind) in seen:
            continue
        seen.add((key, kind))
        parts.append(f".extract({jstr(key)}, {jstr(path)})" if kind == "json"
                     else f".extractWhole({jstr(key)})")
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
        com.ak.api.rest.utilities.RestLoggerUtilityDataHolder holder = c.holder;
        String testCaseId = c.testCaseId;
        com.ak.api.support.ImportedRestClient client = c.client;
        com.ak.api.domain.DomainApis __domainApis = com.ak.api.domain.DomainApis.bind(client);
        com.ak.api.domain.guest.GuestApi guests = __domainApis.guests();
        com.ak.api.domain.account.ProgramAccountApi accounts = __domainApis.accounts();
        com.ak.api.domain.member.MemberApi members = __domainApis.members();
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
        out.append(f".{vocab}()" if counts[vocab] == 1 and vocab not in force_step
                   else f".{vocab}({jstr(step)})")
    return out


def phases_class_java(pkg: str, cls: str, imports: list[str], cases: list[dict], hooks: list[str]) -> str:
    """One `<TestClass>Phases` file.

    cases: [{"case": id, "entries": [{"vocab","step","verify","spec_java"}]}]
    hooks: rendered hook methods (from hook_java)

    Cluster members share one @Test and therefore one entry list; they
    register through one loop. Within a class, an identical builder is
    emitted once as a private factory and referenced by every entry.
    """
    lines = [f"package {pkg};", ""]
    for i in sorted(set(imports)):
        lines.append(f"import {i};")
    lines += ["", "/**",
              " * Generated by ra_converter: the phases of every case in the matching",
              " * test class, as data. One engine per call runs them (see Calls);",
              " * the hooks below are the translated Groovy those phases carried.",
              " */",
              f"public final class {cls} {{",
              "",
              f"    private static final Logger LOG = LoggerFactory.getLogger({cls}.class);",
              "    private static volatile boolean registered;",
              "",
              f"    private {cls}() {{",
              "    }",
              ""]
    # identical builders -> one factory
    factories: dict[str, str] = {}
    def factory(spec_java: str) -> str:
        name = factories.get(spec_java)
        if name is None:
            name = f"spec{len(factories) + 1}"
            factories[spec_java] = name
        return name
    # identical entry lists -> one registration loop
    groups: dict[tuple, list[str]] = {}
    order: list[tuple] = []
    for c in cases:
        key = tuple((e["vocab"], e["step"], bool(e["verify"]), factory(e["spec_java"])) for e in c["entries"])
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
            body.append("        for (String id : new String[] {" + ", ".join(jstr(i) for i in ids) + "}) {")
            body.append("            CaseRegistry.register(id)")
            pad = "                "
        for idx, (vocab, step, verify, fname) in enumerate(key):
            kind = "verify" if verify else "phase"
            end = ";" if idx == len(key) - 1 else ""
            body.append(f"{pad}.{kind}({jstr(vocab)}, {jstr(step)}, {cls}::{fname}){end}")
        if not key:
            body[-1] += ";"
        if len(ids) > 1:
            body.append("        }")
    body.append("    }")
    lines += body
    lines.append("")
    for spec_java, name in factories.items():
        lines.append(f"    private static PhaseSpec {name}() {{")
        lines.append("        return " + spec_java + ";")
        lines.append("    }")
        lines.append("")
    for h in hooks:
        lines.append(h)
    lines.append("}")
    return "\n".join(lines) + "\n"


def hooks_class_java(pkg: str, imports: list[str], hooks: list[str]) -> str:
    """support/<suite>/cases/Hooks.java: each distinct translated block once."""
    lines = [f"package {pkg};", ""]
    for i in sorted(set(imports)):
        lines.append(f"import {i};")
    lines += ["", "/**",
              " * Generated by ra_converter: the translated Groovy / transfers / script",
              " * assertions that follow a REST call, one method per distinct block. A",
              " * PhaseSpec names the one it needs with `.after(Hooks::hookN_step)`.",
              " */",
              "public final class Hooks {",
              "",
              "    private static final Logger LOG = LoggerFactory.getLogger(Hooks.class);",
              "",
              "    private Hooks() {",
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
             "import com.ak.api.rest.utilities.phase.PhaseContext;",
             "import com.ak.api.rest.utilities.phase.PhaseRunner;",
             "import com.ak.api.rest.utilities.phase.PhaseSpec;",
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


def vocab_methods_java(vocabs: list[str], taken=()) -> str:
    """`enrollGuest()` / `enrollGuest(String step)` on ScenarioSteps, once per name.

    A name in `taken` is also a text-path method (no-arg); only the step
    overload is emitted for it, and chains always pass the step.
    """
    out = []
    for v in sorted(set(vocabs)):
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
