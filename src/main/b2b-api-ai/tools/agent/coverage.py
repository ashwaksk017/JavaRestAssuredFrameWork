"""What the project already covers, beyond what the converter recorded.

`locate.py` decides -- per request of a story -- create, update, upstream
or confirm, by matching the request against the SHAPE INDEX: every call in
the converted ReadyAPI suites, signed the way the converter signs them.
That leaves three things unsaid, and this module says them.

1. HAND-WRITTEN TESTS. The shape index is built from the ReadyAPI
   sources. A call covered only by a test somebody (or the Agentic Test Creation tab)
   wrote was not in it, so the second story about that call was answered
   "create" again. `handwritten()` reads the committed test code and
   lists the calls it makes: verb, path, the test method, its data file.

   It is read every time, not kept: there is nothing to go stale.

   A call is found three ways: a path written in the code, a client
   method whose endpoint the converter wrote down, or a step of the
   committed DSL followed to such a client method. The last two need a
   converted suite on the machine; without one, the tests that could not
   be seen into are named (`unresolved()`).

   What it can see is the verb and the PATH. It cannot see the body, so a
   match here is a match of the CALL, and is reported as that -- "matched
   by verb and path; the body was not compared".

2. THE DATA. "Add a row" is the answer when the call is already
   automated. `examine()` opens the test's CSV and says which of three
   things is true:

       row-exists    every field has a column, and a row already carries
                     this request's values
       same-inputs   a row carries the same values and expects another
                     status
       add-row       every field of the request has a column: a new row
                     is enough
       needs-more    some field has no column. A row cannot change it --
                     it is fixed in the template or the test -- so the
                     test must change as well, or a new one be written

   Fields are paired with columns by NAME (`peakRooms` with `peakRooms`,
   `tpl_peakRooms`, `Properties.peakRooms`), and a row is "the same" when
   every paired column holds the request's value. That is evidence, not
   proof: a column named otherwise is not found, and it says which fields
   it could not place.

3. A STORY WITH NO REQUEST IN IT. A story written as prose gives the
   matcher nothing. When "Design the tests" has run, the endpoints of the
   designed cases are requests -- `VERB /path`, with no body.
   `design_requests()` hands those over. A request with no body can only
   be matched as a call, so it never authorises writing over a generated
   test: that case is CONFIRM.

Nothing here writes anything, calls anything, or changes a decision by
itself. It returns findings; `locate.py` folds them in.
"""
from __future__ import annotations

import csv
from decimal import Decimal
import io
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))

# Where committed, hand-written API tests live. NOT everything under
# tests/: that also holds the framework's own unit tests, whose calls to
# `/guests/1/businesses` test the framework and cover no story. The list
# is `handwritten_roots` in tools/agent/policy.json; these are the
# defaults when it has none.
DEFAULT_ROOTS = ("src/test/java/com/hi/api/tests/manual/",
                 "src/test/java/com/hi/api/tests/jira/",
                 "src/main/java/com/hi/api/dsl/")
# Never read, wherever the list points: these are the converter's.
GENERATED = ("/tests/imported/", "/tests/classic/", "/support/", "/rest/clients/")
CSV_ROOT = ("src", "test", "resources", "csv")
MAX_JAVA_BYTES = 2_000_000
MAX_CSV_ROWS = 5000
MAX_FIELDS = 60
MAX_DESIGN_ENDPOINTS = 40
MAX_DESIGN_PATH = 300
MAX_DATA_FILES = 12

# `.post("/a/{id}/b"` -- RestStep and REST Assured both end a call this way.
_CALL_RX = re.compile(r"\.\s*(get|post|put|patch|delete)\s*\(\s*\"(/[^\"\\\n]*)\"\s*([,)+])")
_PACKAGE_RX = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.M)
# A method declaration, read from text whose comments and string contents
# are already blanked (`_layers`). Found from its NAME onward -- parameters
# may hold one level of brackets, `@Optional("stg") String env` -- and
# then what stands before the name on its line must be a declaration's
# head: modifiers and a return type. (One pattern for both, anchored at
# the line, took 40 seconds over the generated clients.)
_SIG_RX = re.compile(r"(?<!\w)(\w+)\s*\((?:[^()]|\([^()]*\))*\)\s*(?:throws\s+[\w., \n]+?)?\s*\{")
_HEAD_RX = re.compile(
    r"[ \t]*(?:@\w[\w.]*(?:\([^()]*\))?\s*)*"
    r"(?:(?:public|protected|private|static|final|abstract|synchronized|default)\s+)*"
    r"(?:<[^<>()]*>\s+)?"
    r"(?!(?:new|return|throw|else|case|assert)\b)[\w<>\[\],.? ]*[\w>\]]\s+")
# A head that holds one of these is a statement or another kind of
# declaration: `return new Foo() {`, `record Point(...) {`.
_NOT_A_HEAD_RX = re.compile(r"\b(?:new|return|throw|else|case|assert|record|class|interface|enum)\b")
_MODIFIER_RX = re.compile(r"\b(?:public|protected|private|static|final|abstract|synchronized|default)\b")
_TEST_RX = re.compile(r"@(?:[\w.]+\.)?Test\b")
# Methods TestNG runs that are not tests.
_SETUP_RX = re.compile(r"@(?:[\w.]+\.)?(?:Before|After)\w*\b|@(?:[\w.]+\.)?(?:DataProvider|Factory)\b")
_NOT_METHODS = ("if", "for", "while", "switch", "catch", "return", "new", "try",
                "synchronized", "do", "else")
# Comments, text blocks, strings, character literals -- found TOGETHER, in
# the order they open. Taking comments out first read the `//` of
# `"http://host"` as a comment, which swallowed the closing quote and
# handed a method everything declared after it.
_TOKEN_RX = re.compile(r'//[^\n]*|/\*.*?\*/|"""(?:.*?)"""|"(?:[^"\\\n]|\\.)*"'
                       r"|'(?:[^'\\\n]|\\.)*'", re.S)
# Names too common to stand for a step of the DSL: `map.get("k")` is not
# `Onboarding.get()`. A step is credited by its NAME (the receiver's type
# is not known here), so a name anything might have is never credited.
COMMON_NAMES = frozenset("""
    get set put post patch delete add remove build with of then and when given create read
    update list find save load open close run call apply accept test name value body
    header headers path param params query log all extract as status size is has equals
    toString hashCode valueOf format append clear contains stream map filter forEach
    collect join split trim parse send request response execute start stop reset init
    setUp tearDown before after main assertThat assertEquals assertTrue assertFalse
    """.split())


def plain(value, cap: int = 60) -> str:
    """Text that did not come from this project -- a field name of a
    pasted body, a cell of a CSV -- as one short line that cannot be
    read as structure: no line breaks, no backticks, no markup."""
    s = re.sub(r"[`<>|*#\[\]]", "", str(value if value is not None else ""))
    s = re.sub(r"[\x00-\x1f\x7f\s]+", " ", s).strip()
    return s[:cap] + ("..." if len(s) > cap else "")


def _read(path: str, cap: int = MAX_JAVA_BYTES) -> str:
    try:
        with io.open(path, encoding="utf-8-sig", errors="replace") as fh:
            return fh.read(cap)
    except OSError:
        return ""


def _norm_path(p: str) -> tuple:
    """A path as segments, with every `{name}` reduced to one marker."""
    p = (p or "").split("?", 1)[0].strip()
    return tuple("{}" if re.fullmatch(r"\{[^{}]*\}", s) or re.fullmatch(r"#[^#]+#", s) else s
                 for s in p.strip("/").split("/")) if p.strip("/") else ()


path_key = _norm_path


def path_fits(recorded: str, asked: str) -> bool:
    """Is `asked` the path `recorded`, or an instance of it?

    `/props/{propCode}/groups` fits `/props/{propCode}/groups` and
    `/props/ABCDE/groups`. Segment for segment; a parameter in the
    recorded path takes any ONE segment of the asked one.
    """
    r, a = _norm_path(recorded), _norm_path(asked)
    if not r or len(r) != len(a):
        return False
    return all(x == "{}" or x == y for x, y in zip(r, a))


# ---- 1. the calls hand-written tests make -------------------------------------

def _csv_for(root: str, simple: str, method: str) -> str:
    """The data file `PerMethodCsvDataProvider` would read for a method of
    a hand-written class, when there is one: csv/<Class>/<method>.csv, or
    the same under csv/manual/ (where the Agentic Test Creation tab writes)."""
    base = os.path.join(root, *CSV_ROOT)
    for parts in ((simple, f"{method}.csv"), ("manual", simple, f"{method}.csv"),
                  ("manual", f"{method}.csv")):
        p = os.path.join(base, *parts)
        if os.path.isfile(p):
            return os.path.relpath(p, root).replace(os.sep, "/")
    return ""


def roots(policy_path: str = "") -> list:
    """The folders read for hand-written tests, relative to the project."""
    try:
        with io.open(policy_path or os.path.join(HERE, "policy.json"), encoding="utf-8") as fh:
            listed = json.load(fh).get("handwritten_roots")
    except (OSError, ValueError, AttributeError):
        listed = None
    if not isinstance(listed, list) or not listed:
        return list(DEFAULT_ROOTS)
    out = []
    for r in listed:
        r = str(r).replace("\\", "/").strip().strip("/")
        # Inside the project, and not a way out of it. A colon is a drive
        # (`C:Windows` is "relative", and joining it discards the project).
        if r and ":" not in r and not os.path.isabs(r) and ".." not in r.split("/"):
            out.append(r + "/")
    return out or list(DEFAULT_ROOTS)


def _java_files(root: str, only: list = None):
    """(listed root, file) for every Java file under the listed folders.
    Never a generated folder, and never a folder that is not really
    inside the project (a link out of it is not followed)."""
    real_root = os.path.realpath(root)
    for rel in (only if only is not None else roots()):
        top = os.path.join(root, rel.replace("/", os.sep))
        real_top = os.path.realpath(top)
        if not os.path.isdir(top) or not (real_top + os.sep).startswith(real_root + os.sep):
            continue
        for base, dirs, files in os.walk(top):
            here = "/" + os.path.relpath(base, root).replace(os.sep, "/") + "/"
            dirs[:] = sorted(d for d in dirs
                             if not any(g in here + d + "/" for g in GENERATED))
            if any(g in here for g in GENERATED):
                continue
            for name in sorted(files):
                if name.endswith(".java"):
                    yield rel, os.path.join(base, name)


AUDIT_DIR = "_audit"
_FACADE_ROW_RX = re.compile(r"^\|\s*\w+\s*\|\s*`(\w+)`\s*\|\s*(GET|POST|PUT|PATCH|DELETE)\s+(/\S*)\s*\|",
                            re.M)
# `apis.guests().hHonorsEnroll(` / `client.goalShopSingleProp(` / `MasterClass.guests().x(`
_CLIENT_CALL_RX = re.compile(r"(?:\b\w*[cC]lient(?:\(\))?|\bapis\.\w+\(\)|\bMasterClass\.\w+\(\))"
                             r"\s*\.\s*(\w+)\s*\(")
_ANY_CALL_RX = re.compile(r"(?<![\w.])(\w+)\s*\(|\.\s*(\w+)\s*\(")
_LOCAL_CALL_RX = re.compile(r"(?:(?<![\w.])|\bthis\s*\.\s*)(\w+)\s*\(")

CLIENTS_DIR = ("src", "main", "java")
_FILL_RX = re.compile(r"ApiRoutes\.fill\(\s*\"(/[^\"\\\n]*)\"")
_VERB_RX = re.compile(r"\.\s*(get|post|put|patch|delete)\s*\(")


def _layers(text: str) -> tuple:
    """(code, bare): the same text twice, the same length and line for
    line. `code` has its comments blanked. `bare` has, as well, the inside
    of every string and character literal blanked -- so that a brace, a
    method name or a `//` inside a string is not read as code."""
    code, bare = list(text), list(text)
    for m in _TOKEN_RX.finditer(text):
        tok = m.group(0)
        comment = tok.startswith("/")
        block = tok.startswith('"""')
        edge = 0 if comment else (3 if block else 1)
        for k in range(m.start() + edge, m.end() - edge):
            if text[k] != "\n":
                bare[k] = " "
                # A text block is a payload: a `.post("/x")` written in one
                # is not a call.
                if comment or block:
                    code[k] = " "
    return "".join(code), "".join(bare)


def _body_end(bare: str, opened: int) -> int:
    """The index just past the `}` that closes the `{` at `opened`, in
    text whose strings are blanked."""
    depth = 0
    for i in range(opened, len(bare)):
        ch = bare[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i + 1
    return len(bare)


def _annotations(bare: str, line_start: int) -> str:
    """What stands between the member before a declaration and the
    declaration's own line: its annotations, when they are on lines of
    their own."""
    before = bare[max(0, line_start - 4000):line_start]
    before = before[before.rfind(";") + 1:]
    for _ in range(3):                                   # `@Test(groups = {"a"})`
        before = re.sub(r"\([^()]*\)", "", before)
    return before[max(before.rfind("}"), before.rfind("{")) + 1:]


def _methods(bare: str) -> list:
    """[(name, start, end, is a @Test, what is declared before the name)]
    for each method of a Java source. The span is the method's own braces
    and no more: taking "up to the next method" gave a helper the calls of
    everything declared after it."""
    out = []
    for m in _SIG_RX.finditer(bare):
        if m.group(1) in _NOT_METHODS:
            continue
        line_start = bare.rfind("\n", 0, m.start()) + 1
        head = bare[line_start:m.start()]
        if len(head) > 300 or not _HEAD_RX.fullmatch(head) or _NOT_A_HEAD_RX.search(head):
            continue
        # A constructor has modifiers and no type: `public Foo(...) {`.
        if not _MODIFIER_RX.sub("", re.sub(r"@\w[\w.]*(?:\([^()]*\))?", "", head)).strip():
            continue
        declared = _annotations(bare, line_start) + head
        out.append((m.group(1), line_start, _body_end(bare, m.end() - 1),
                    bool(_TEST_RX.search(declared)), declared))
    return out


def client_endpoints(root: str = "") -> dict:
    """{client method: [(VERB, path), ...]} read from the GENERATED
    clients (`rest/clients/*Client.java`), where a method is a path and a
    verb. They exist only after a convert; a method no facade wraps
    (`client.goalShopSingleProp(...)`) is mapped nowhere else."""
    root = root or ROOT
    out: dict = {}
    for base, _dirs, files in os.walk(os.path.join(root, *CLIENTS_DIR)):
        here = base.replace(os.sep, "/") + "/"
        if "/rest/clients/" not in here:
            continue
        for name in sorted(files):
            if not name.endswith("Client.java"):
                continue
            code, bare = _layers(_read(os.path.join(base, name)))
            for method, start, end, _t, _d in _methods(bare):
                path = _FILL_RX.search(code, start, end)
                if not path:
                    continue
                verb = _VERB_RX.search(bare, path.end(), end)
                if not verb:
                    continue
                got = out.setdefault(method, [])
                pair = (verb.group(1).upper(), path.group(1))
                if pair not in got:
                    got.append(pair)
    return out


def facade_endpoints(root: str = "") -> dict:
    """{client or facade method: [(VERB, path), ...]} from every
    `_audit/<suite>/facade_endpoints.md` the converter has written.

    A hand-written test in this framework rarely holds a path. It says
    `.enrollOwner()`, which calls `apis.guests().hHonorsEnroll(...)`,
    and only the GENERATED client knows that is `POST /realms/guests/
    enroll`. The converter writes that answer down on every convert; this
    reads it. With no convert there is no such file, and a fluent call
    cannot be turned into an endpoint -- which is said, not guessed.
    """
    root = root or ROOT
    out: dict = {}
    base = os.path.join(root, AUDIT_DIR)
    try:
        suites = sorted(os.listdir(base))
    except OSError:
        return out
    for suite in suites:
        text = _read(os.path.join(base, suite, "facade_endpoints.md"))
        for name, verb, path in _FACADE_ROW_RX.findall(text):
            got = out.setdefault(name, [])
            if (verb, path) not in got:
                got.append((verb, path))
    return out


def endpoint_map(root: str = "") -> dict:
    """Every method name whose endpoint the converter has written down:
    the facades' (`_audit/*/facade_endpoints.md`) and the clients' own."""
    out = facade_endpoints(root)
    for name, pairs in client_endpoints(root).items():
        got = out.setdefault(name, [])
        for pair in pairs:
            if pair not in got:
                got.append(pair)
    return out


def _certain(endpoints: dict) -> dict:
    """The part of the map that can be used to credit a call: names that
    mean ONE endpoint. `create` is three different calls in three suites;
    which one a test makes cannot be told from the name, and crediting all
    three would say "covered" of two calls it does not make."""
    return {name: pairs for name, pairs in (endpoints or {}).items() if len(pairs) == 1}


def fluent_methods(root: str, endpoints: dict, only: list = None) -> dict:
    """{fluent method: [(VERB, path, via), ...]} for the committed DSL
    classes: what each chain method ends up calling, followed through
    the methods it delegates to (`enrollOwner()` -> `enrollGuest(...)`)."""
    endpoints = _certain(endpoints)
    direct, calls = {}, {}
    for rel, path in _java_files(root, only):
        if "/dsl/" not in "/" + rel:
            continue
        code, bare = _layers(_read(path))
        for method, start, end, _t, _d in _methods(bare):
            body = bare[start:end]
            for m in _CALL_RX.finditer(code, start, end):
                got = direct.setdefault(method, [])
                if "+" != m.group(3) and (m.group(1).upper(), m.group(2), "") not in got:
                    got.append((m.group(1).upper(), m.group(2), ""))
            for client_method in _CLIENT_CALL_RX.findall(body):
                for verb, call_path in endpoints.get(client_method, []):
                    got = direct.setdefault(method, [])
                    if (verb, call_path, client_method) not in got:
                        got.append((verb, call_path, client_method))
            calls.setdefault(method, set()).update(
                a or b for a, b in _ANY_CALL_RX.findall(body[body.find("{"):]))
    out = {k: list(v) for k, v in direct.items()}
    for _ in range(3):                                   # delegation, a few levels deep
        for method, called in calls.items():
            for other in called:
                if other == method or other not in out:
                    continue
                for verb, call_path, via in out[other]:
                    got = out.setdefault(method, [])
                    if not any((verb, call_path) == (v, pth) for v, pth, _x in got):
                        got.append((verb, call_path,
                                    f"{other}() -> {via}" if via else f"{other}()"))
    return {name: found for name, found in out.items() if name not in COMMON_NAMES}


def scan(root: str = "", only: list = None) -> dict:
    """One reading of the committed hand-written code:

        {"calls": [...], "blind": {"tests": [...], "reason": str}}

    `calls` is every call a method was seen to make:
    {verb, path, partial, how, via, file, line, java_class_fqn,
     java_method, is_test, csv_path}. `how` says how it was found:
      literal   a path written in the code: `.post("/a/{id}/b", ...)`
      client    a client or facade method whose ONE endpoint the
                converter wrote down: `client.goalShopSingleProp(...)`
      fluent    a step of the committed DSL, followed to the call
                behind it: `.enrollOwner()`
      helper    a method of the same class that makes the call
    `is_test` is true of a @Test method; anything else is a step or a
    helper, which covers nothing by itself.

    `blind` names every @Test method no call could be read from. Such a
    test may still cover the story (a path held in a constant, a client
    name that means several endpoints), so that it was not seen into is
    said rather than passed over.
    """
    root = root or ROOT
    endpoints = endpoint_map(root)
    certain = _certain(endpoints)
    fluent = fluent_methods(root, endpoints, only)
    calls, blind = [], []
    for rel, path in _java_files(root, only):
        found, unseen = _calls_in(root, path, certain,
                                  None if "/dsl/" in "/" + rel else fluent)
        calls += found
        if "/dsl/" not in "/" + rel:
            blind += unseen
    reason = ""
    if blind:
        reason = ("there is no converted suite on this machine, so the calls behind "
                  "`.enrollOwner()`-style steps and client methods cannot be read: "
                  "convert a suite first"
                  if not endpoints else
                  "they make no call that could be read: a path that is not written "
                  "out where it is used, or a method the converter has not written "
                  "one endpoint down for")
    return {"calls": calls, "blind": {"tests": blind, "reason": reason}}


def handwritten(root: str = "", only: list = None) -> list:
    """The calls of `scan()`, in a stable order."""
    return scan(root, only)["calls"]


def unresolved(root: str = "", only: list = None) -> dict:
    """The `blind` of `scan()`: {tests: ["file#method", ...], reason}."""
    return scan(root, only)["blind"]


def _calls_in(root: str, path: str, endpoints: dict = None, fluent: dict = None) -> tuple:
    """(calls, @Test methods no call was read from) for one Java file."""
    raw = _read(path)
    if not raw:
        return [], []
    code, bare = _layers(raw)
    package = (_PACKAGE_RX.search(bare) or [None, ""])[1]
    cls = os.path.splitext(os.path.basename(path))[0]
    rel = os.path.relpath(path, root).replace(os.sep, "/")
    fqn = f"{package}.{cls}" if package else cls
    methods = _methods(bare)
    # TestNG allows @Test on the class: then every public method is a test,
    # whether or not some of them say so themselves -- except the ones it
    # runs as set-up.
    flat = bare
    for _ in range(3):                                   # `@Test(groups = {"smoke"})`
        flat = re.sub(r"\([^()]*\)", "", flat)
    whole_class = bool(re.search(r"@(?:[\w.]+\.)?Test\b[^{;(]*\bclass\b", flat))
    out, seen = [], set()

    def add(verb, call_path, partial, how, method, is_test, at, via=""):
        if not _norm_path(call_path):
            return                                       # `"/" + something`: says nothing
        key = (verb, call_path, method, how)
        if key in seen:
            return
        seen.add(key)
        out.append({
            "kind": "handwritten", "verb": verb, "path": call_path,
            # `"/guests/" + id + "/x"`: only the start of the path is in
            # the string. It is a likeness, and is never called a match.
            "partial": partial, "how": how, "via": via,
            "file": rel, "line": bare.count("\n", 0, at) + 1,
            "java_class_fqn": fqn, "java_method": method, "is_test": is_test,
            "csv_path": _csv_for(root, cls, method) if is_test and method else "",
        })

    spans = []
    for method, start, end, tested, declared in methods:
        is_test = tested or (whole_class and bool(re.search(r"\bpublic\b", declared))
                             and not _SETUP_RX.search(declared))
        spans.append((method, start, end, is_test))
        for m in _CALL_RX.finditer(code, start, end):
            add(m.group(1).upper(), m.group(2), m.group(3) == "+", "literal", method,
                is_test, m.start())
        for m in _CLIENT_CALL_RX.finditer(bare, start, end):
            for verb, call_path in (endpoints or {}).get(m.group(1), []):
                add(verb, call_path, False, "client", method, is_test, m.start(),
                    f"{m.group(1)}()")
        if fluent:                                       # not inside the DSL itself
            for m in re.finditer(r"\.\s*(\w+)\s*\(", bare[start:end]):
                for verb, call_path, via in (fluent or {}).get(m.group(1), []):
                    add(verb, call_path, False, "fluent", method, is_test, start + m.start(),
                        f"{m.group(1)}() -> {via}" if via else f"{m.group(1)}()")
    # A test that calls a method of its own class which makes the call.
    own = {}
    for c in out:
        if not c["is_test"]:
            own.setdefault(c["java_method"], []).append(c)
    for method, start, end, is_test in spans:
        if not is_test:
            continue
        body_at = bare.find("{", start)
        for m in _LOCAL_CALL_RX.finditer(bare, body_at, end):
            if bare[body_at:m.start()].rstrip().endswith("new"):
                continue                                 # `new Foo(...)` calls no method
            for c in own.get(m.group(1), []) if m.group(1) != method else []:
                add(c["verb"], c["path"], c["partial"], "helper", method, True, m.start(),
                    f"{m.group(1)}()" + (f" -> {c['via']}" if c["via"] else ""))
    credited = {c["java_method"] for c in out if c["is_test"]}
    unseen = [f"{rel}#{method}" for method, _s, _e, is_test in spans
              if is_test and method not in credited]
    # ...and a @Test whose method was not recognised at all (its name on
    # the line after its type, say) is not read, so it is said.
    lost = len(_TEST_RX.findall(bare)) - (1 if whole_class else 0) \
        - sum(1 for _m, _s, _e, tested, _d in methods if tested)
    if lost > 0:
        unseen.append(f"{rel} ({lost} @Test method(s) whose declaration could not be read)")
    return out, unseen


def handwritten_matches(calls: list, verb: str, path: str) -> list:
    """The hand-written calls that are this verb and this path."""
    verb = (verb or "").upper()
    out = []
    for c in calls:
        if c["verb"] != verb:
            continue
        if c["partial"]:
            head, asked = _norm_path(c["path"]), _norm_path(path)
            if head and asked[:len(head)] == head:
                out.append(c)
        elif path_fits(c["path"], path):
            out.append(c)
    return out


def handwritten_templates(calls: list) -> list:
    """Their paths, as templates a pasted concrete path can be read against."""
    return sorted({c["path"] for c in calls if not c["partial"] and "{" in c["path"]},
                  key=lambda p: (-len(p.split("/")), p))


# ---- the same question of the generated index: is this CALL there at all? -----

def call_index(index: dict) -> dict:
    """{(VERB, normalised path): [entries]} from the shape index's
    per-step table -- the call, whatever its body."""
    out: dict = {}
    for sig, entries in ((index or {}).get("step_exact") or {}).items():
        try:
            step = json.loads(sig)[0]
            verb, path = str(step[0]).upper(), str(step[1])
        except (ValueError, IndexError, TypeError):
            continue
        out.setdefault((verb, _norm_path(path)), []).extend(entries)
    return out


def generated_call_matches(calls: dict, verb: str, path: str) -> list:
    """Generated tests that make this call, one per test method."""
    asked = _norm_path(path)
    seen, out = set(), []
    for (v, recorded), entries in calls.items():
        if v != (verb or "").upper() or len(recorded) != len(asked):
            continue
        if not all(x == "{}" or x == y for x, y in zip(recorded, asked)):
            continue
        for e in entries:
            key = (e.get("java_class_fqn"), e.get("java_method"), e.get("csv_path"))
            if key not in seen:
                seen.add(key)
                out.append(e)
    return out


# ---- 2. the data ---------------------------------------------------------------

def _key(name) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


def _leaves(node, out: list, name: str = "") -> list:
    """(field name, value) for every scalar in a request body."""
    if isinstance(node, dict):
        for k, v in node.items():
            _leaves(v, out, str(k))
        if not node and name:
            out.append((name, ""))
    elif isinstance(node, list):
        for v in node[:20]:
            _leaves(v, out, name)
        if not node and name:
            out.append((name, ""))                       # `[]`: said, and empty
    elif isinstance(node, bool) and name:
        out.append((name, "true" if node else "false"))
    elif name:
        # `null` is something the request says. Dropping it let a row
        # "match" on what was left.
        out.append((name, "" if node is None else str(node)))
    return out


def request_fields(request: dict, examples: dict = None) -> list:
    """What a request says, as (field, value): its body's scalars and the
    literals that filled its path parameters. Values that are themselves
    placeholders (`${...}`, `#...#`) say nothing and are left out."""
    fields = []
    body = request.get("body") or ""
    if isinstance(body, str) and body.strip():
        try:
            body = json.loads(body)
        except ValueError:
            body = None
    if isinstance(body, (dict, list)):
        _leaves(body, fields)
    for name, value in (examples or {}).items():
        if str(value).strip():
            fields.append((str(name), str(value)))
    out, seen = [], set()
    for name, value in fields:
        value = value.strip()
        # `{guestId}` is a path written as a template: the name of a value,
        # not one.
        if (re.search(r"\$\{|#[^#]+#|\{\{", value)
                or re.fullmatch(r"\{[^{}]*\}", value) or (_key(name), value) in seen):
            continue
        seen.add((_key(name), value))
        out.append((name, value))
    return out[:MAX_FIELDS]


def _column_for(field: str, columns: list) -> str:
    """The column that carries a field, by name: the field itself, or the
    field behind one of this framework's prefixes."""
    want = _key(field)
    if len(want) < 2:
        return ""
    exact = [c for c in columns if _key(c) == want]
    if exact:
        return exact[0]
    for c in columns:
        k = _key(c)
        for prefix in ("tpl", "properties", "inputs", "data"):
            if k == prefix + want:
                return c
    # ...or the last part of a dotted / underscored name: `guest.peakRooms`.
    tails = [c for c in columns if _key(re.split(r"[._#]", str(c))[-1]) == want]
    return tails[0] if len(tails) == 1 else ""


_NUMBER_RX = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?")


def _same(cell, value: str) -> bool:
    a, b = str(cell if cell is not None else "").strip(), value.strip()
    if a.lower() == b.lower():
        return True
    # 5 and 5.0 are one number. `01234` is not 1234 -- it is a code -- and
    # neither is `1_234`, which Python would read as one.
    if _NUMBER_RX.fullmatch(a) and _NUMBER_RX.fullmatch(b):
        return Decimal(a) == Decimal(b)                  # exact: an id of 20 digits is not a float
    return False


def examine(root: str, csv_path: str, request: dict, examples: dict = None) -> dict:
    """What the data file of a matched test says about a request.

    {csv, verdict, rows, carried, not_carried, same_rows, closest, note}
    """
    root = root or ROOT
    out = {"csv": csv_path or "", "verdict": "unknown", "rows": 0, "carried": [],
           "not_carried": [], "same_rows": [], "closest": None, "note": ""}
    if not csv_path:
        out["note"] = "this test has no data file that could be found"
        return out
    base = os.path.realpath(os.path.join(root, "src", "test", "resources"))
    full = os.path.realpath(os.path.join(root, csv_path.replace("/", os.sep)))
    if not (full == base or full.startswith(base + os.sep)):
        out["note"] = "the data file named for this test is not under src/test/resources"
        return out
    if not full.lower().endswith(".csv"):
        out["note"] = "the data file is not a CSV; it was not read"
        return out
    if not os.path.isfile(full):
        out["note"] = ("the data file is not on this machine (generated files are not "
                       "committed: convert the suite to have it)")
        return out
    try:
        with io.open(full, encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            columns = [c for c in (reader.fieldnames or []) if c]
            rows = []
            for row in reader:
                rows.append(row)
                if len(rows) >= MAX_CSV_ROWS:
                    out["note"] = f"only the first {MAX_CSV_ROWS} rows were read"
                    break
    except (OSError, csv.Error, UnicodeError) as e:
        out["note"] = f"the data file could not be read ({type(e).__name__})"
        return out
    out["rows"] = len(rows)
    fields = request_fields(request, examples)
    if not fields:
        out["note"] = (out["note"] or "the request carries no values to compare (no body, "
                                      "or only placeholders)")
        return out
    pairs = []
    for name, value in fields:
        col = _column_for(name, columns)
        if col:
            pairs.append((name, value, col))
        else:
            out["not_carried"].append(plain(name))
    # Two fields of one name (`owner.email`, `member.email`; two items of a
    # list) land on one column, which holds one value. A row cannot carry
    # both, so neither is called carried.
    held = {}
    for _name, value, col in pairs:
        held.setdefault(col, set()).add(value.strip().lower())
    for name, _value, col in pairs:
        if len(held[col]) > 1:
            label = plain(name) + " (more than one value, one column)"
            if label not in out["not_carried"]:
                out["not_carried"].append(label)
    pairs = [p for p in pairs if len(held[p[2]]) == 1]
    out["carried"] = [{"field": plain(name), "column": plain(col)} for name, _v, col in pairs]
    status = str(request.get("expected_status") or "").strip()
    status_col = next((c for c in columns if _key(c) in ("expectedstatuscode", "expectedstatus")), "")
    if status and not status_col and pairs:
        # The status the story expects is part of the case. With no column
        # for it, "the same row" cannot be said.
        out["not_carried"].append("the expected status")
    execute_col = next((c for c in columns if _key(c) == "execute"), "")
    best, full, other_status = (0, None), [], []
    for n, row in enumerate(rows, 2):                    # row 1 is the header
        if str(row.get(execute_col) or "").strip().upper() in ("N", "NO", "FALSE", "0"):
            continue
        equal = sum(1 for _name, value, col in pairs if _same(row.get(col), value))
        ident = plain(row.get("test_case_id") or row.get(columns[0] if columns else "") or "")
        status_ok = not (status and status_col) or _same(row.get(status_col), status)
        if pairs and equal == len(pairs) and status_ok:
            full.append({"row": n, "id": ident})
        elif pairs and equal == len(pairs):
            other_status.append({"row": n, "id": ident,
                                 "status": plain(row.get(status_col), 12)})
        elif equal > best[0]:
            best = (equal, {"row": n, "id": ident, "equal": equal, "of": len(pairs)})
    out["closest"] = best[1]
    if full and not out["not_carried"]:
        out["same_rows"] = full[:5]
        out["verdict"] = "row-exists"
    elif full:
        # Equal in every column there is -- but the request says something
        # no column holds, and what the test sends for THAT is fixed where
        # this cannot read it. "The data is already there" would be a guess.
        out["closest"] = dict(full[0], equal=len(pairs), of=len(pairs))
        out["verdict"] = "needs-more"
    elif other_status and not out["not_carried"]:
        # The same inputs, expecting something else. A second row would be
        # one case with two expectations: a person says which is meant.
        out["same_rows"] = other_status[:5]
        out["verdict"] = "same-inputs"
    elif not pairs:
        out["verdict"] = "needs-more"
        out["note"] = out["note"] or "none of the request's fields has a column in this file"
    elif out["not_carried"]:
        out["verdict"] = "needs-more"
    else:
        out["verdict"] = "add-row"
    return out


def describe(found: dict) -> str:
    """One finding as a sentence for the plan."""
    v, csv_name = found["verdict"], plain(found["csv"], 200) or "its data file"
    carried = ", ".join(f"{c['field']} ({c['column']})" for c in found["carried"][:8])
    missing = ", ".join(found["not_carried"][:8])
    if v == "row-exists":
        rows = ", ".join(f"row {r['row']}" + (f" ({r['id']})" if r["id"] else "")
                         for r in found["same_rows"])
        return f"THE DATA IS ALREADY THERE: {csv_name} has {rows} with these values ({carried})."
    if v == "same-inputs":
        rows = ", ".join(f"row {r['row']}" + (f" ({r['id']})" if r["id"] else "")
                         + f" expects {r.get('status') or 'no status'}"
                         for r in found["same_rows"])
        return (f"THE SAME INPUTS ARE THERE WITH ANOTHER EXPECTED STATUS: {csv_name} has "
                f"{rows} for these values ({carried}).")
    if v == "add-row":
        return (f"A NEW ROW IS ENOUGH: every field of the request has a column in {csv_name} "
                f"({carried}), and no row has these values"
                + (f" -- the nearest is row {found['closest']['row']}, equal in "
                   f"{found['closest']['equal']} of {found['closest']['of']}"
                   if found.get("closest") else "") + ".")
    if v == "needs-more":
        return (f"A ROW ALONE IS NOT ENOUGH: {csv_name} has no column for {missing or 'the fields of this request'}"
                + (f" (it has: {carried})" if carried else "")
                + (f". Row {found['closest']['row']} holds this request's values in every "
                   f"column there is" if (found.get("closest") or {}).get("of")
                   and found["closest"]["equal"] == found["closest"]["of"] else "")
                + ". A row cannot change those -- they are fixed in the template or the "
                  "test -- so if the story needs them different, the test changes too.")
    return f"The data was not compared: {plain(found['note'], 200) or 'nothing to compare'}."


# ---- 3. a story with no request in it --------------------------------------------

_DESIGN_ENDPOINT_RX = re.compile(
    r"^[\s`*_'\"]*(GET|POST|PUT|PATCH|DELETE)[\s`*_]+(?:https?://[^/\s`]+)?(/[\w\-./{}:~%]*)", re.I)


def design_requests(job_dir: str) -> tuple:
    """([request, ...], note) from the job's design, for a story whose own
    text held no request. One per distinct endpoint; no body, because a
    design names the call and not its payload.

    A design was written by a model. An endpoint is taken only as a verb
    and a path made of path characters; what was left out is counted in
    the note, so four designed cases never quietly become one."""
    path = os.path.join(job_dir, "design.json")
    try:
        with io.open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError, RecursionError):
        return [], ""
    if not isinstance(data, dict):
        return [], ""
    if data.get("mock"):
        return [], ("the design of this job is a MOCK (tools/agent/mock.json); its endpoints "
                    "are not used")
    cases = data.get("test_cases")
    if not isinstance(cases, list):
        return [], "the design holds no list of test cases"
    seen, out, unread, over = {}, [], 0, 0
    for case in cases:
        endpoint = case.get("endpoint") if isinstance(case, dict) else None
        m = _DESIGN_ENDPOINT_RX.match(endpoint) if isinstance(endpoint, str) else None
        if not m or len(m.group(2)) > MAX_DESIGN_PATH or not _norm_path(m.group(2)):
            unread += 1
            continue
        key = (m.group(1).upper(), m.group(2))
        if key not in seen and len(seen) >= MAX_DESIGN_ENDPOINTS:
            over += 1
            continue
        seen[key] = seen.get(key, 0) + 1
    for (verb, raw), n in seen.items():
        out.append({"verb": verb, "raw_path": raw, "body": "", "query_param_names": [],
                    "expected_status": "", "from_design": True,
                    "provenance": f"the design (design.json): {n} designed case(s) for this "
                                  f"endpoint. No request was found in the story itself"})
    notes = []
    if unread:
        notes.append(f"{unread} designed case(s) name no endpoint that can be read as "
                     f"VERB /path, and are not located")
    if over:
        notes.append(f"{over} more are past the first {MAX_DESIGN_ENDPOINTS} endpoints, and "
                     f"are not located")
    if not out and not notes:
        notes.append("the design names no endpoint that can be read as VERB /path")
    return out, "; ".join(notes)
