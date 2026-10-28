"""Scrubbed reference examples of converted tests, committed, for Cursor.

    python tools/agent/refexamples.py build     # from THIS machine's converted suites
    python tools/agent/refexamples.py check     # scan the committed examples again

Asked for: references for writing tests on the Automate Test Case tab that
are there on every machine. The converted tests cannot be committed: the
repository is public and they carry ticket keys, the company's hosts,
e-mail addresses and property codes -- and alone they would not compile
(they import their suite's generated classes, which are not in git).

So, for a few chosen converted cases (picks.json: suite + test method, never
a case name -- case names carry ticket keys), this writes a Markdown example
Maven never compiles, under .cursor/skills/jira-to-restassured/reference/:

  * the chain of calls, and the test method that runs it;
  * each step's phase definition (the call: path, path arguments and which
    earlier response feeds them, token, expected status, checks, extracts);
  * each step's request template, every text value replaced by a
    placeholder -- enum words ("none", "business"), small numbers and
    true/false kept: a 0 for every number was copied into a story body,
    and a hidden enum was invented ("mandatory" for "none"), and the
    server refused both;
  * the data sheet's columns -- no row.

Every value is scrubbed (ticket keys -> ABC-n, hosts -> host.invalid,
e-mails -> example.test, the company name, short upper-case codes, the
values of the private configuration), and the result is scanned again: an
example that still holds any of that is NOT written, and the build fails.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
OUT = os.path.join(".cursor", "skills", "jira-to-restassured", "reference")
PICKS = os.path.join(OUT, "picks.json")
INDEX = os.path.join(OUT, "index.json")
SUPPORT = "src/main/java/com/hi/api/support"
MAX_SPEC_LINES = 40
SMALL_NUMBER = 10000                   # a body number below this is shown as it is

# Not \b after the number: a key runs on into a case name ("ABC-105_post_...").
_KEY_RX = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d+(?![0-9])")
_URL_RX = re.compile(r"https?://[^\s\"'<>)]+")
_EMAIL_RX = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_HOST_RX = re.compile(r"(?:https?://|@)?((?:[a-z0-9-]+\.)+[a-z]{2,})", re.I)
# Host labels that name a provider or a placeholder, not the company.
_NOT_COMPANY = {"example", "localhost", "invalid", "amazonaws", "azure", "google", "github", "gitlab",
                "atlassian", "cursor", "microsoft", "salesforce", "force", "cloudfront", "herokuapp"}


def company_words(private: list) -> list:
    """The company's name, as its own hosts carry it (jira.<name>.com): the
    label before the top-level domain of each host in the private
    configuration. Learned at run time -- the name is never written in this
    public file."""
    words = set()
    for v in private:
        for host in _HOST_RX.findall(v):
            labels = host.lower().split(".")
            if len(labels) >= 2 and len(labels[-2]) >= 4 and labels[-2] not in _NOT_COMPANY:
                words.add(labels[-2])
    return sorted(words)


def _company_rx(private: list):
    words = company_words(private)
    # A whole word only: "force" (a host on force.com) inside travelPolicyEnforcement
    # and salesforceId turned them into nonsense.
    return re.compile("(?i)(?<![a-z])(" + "|".join(re.escape(w) for w in words) + ")(?![a-z])") if words else None
# A ticket key inside an identifier: goal9901PostSnap..., b2b_905_post_..., B2b99002SpendDetail...
_IDENT_KEY_RX = re.compile(r"(?i)(?<![A-Za-z])(b2b|goal|h4b|ead)[-_]?(\d{2,})")
# A short upper-case code in quotes: a property, an operator location, a
# source code. Longer ones are API enum values ("PROGRAMACCOUNTS") and stay.
_CODE_RX = re.compile(r"\"([A-Z][A-Z0-9]{2,6})\"")
_CODE_KEEP = {"GET", "POST", "PUT", "PATCH", "DELETE", "TRUE", "FALSE", "NULL", "NONE", "ADMIN",
              "OWNER", "GUEST", "ACTIVE", "MEMBER", "USD", "JSON", "UTF8", "CODE"}
_PLACEHOLDER_RX = re.compile(r"^(#[^#]+#|\$\{[^}]+\}|<<[^<>]+>>|@[^@]+@|%[^%]+%)$")
# A body text value shown as it is: a lowercase enum word ("none", "retailStoreFront").
_ENUM_WORD_RX = re.compile(r"^[a-z][A-Za-z_-]{0,30}$")
# ...unless its field can hold a person, a login or a secret.
_PERSONAL_KEY_RX = re.compile(r"(?i)user|name|pass|login|mail|secret|token|key|code|card|phone|addr|city|"
                              r"street|id$|domain|site|url|link|comment|note|"
                              r"source|vendor|provider|partner")   # a source names a vendor the company uses
_ENUM_KEY_RX = re.compile(r"(Type|Kind|Mode|Status)$")       # addressType, phoneType: enums all the same


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _read(path: str, cap: int = 2_000_000) -> str:
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read(cap)
    except OSError:
        return ""


def private_values(root: str) -> list:
    """The values of the private configuration (hosts, users, secrets) -- six
    characters or more -- that must not appear in an example. Never printed."""
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(HERE), "jira"))
        pc = _load("refexamples_projectconfig", os.path.join(os.path.dirname(HERE), "jira", "projectconfig.py"))
        paths = pc.all_config_paths(root)
    except Exception:                                    # noqa: BLE001
        paths = [os.path.join(root, "src", "main", "resources", n)
                 for n in ("program_configuration.json", "program_configuration.goal.json")]
    out = set()

    def walk(node):
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif isinstance(node, str):
            v = node.strip()
            # A plain lowercase word ("password", the grant type) is a field
            # name in every body, not a secret: hiding it hid the field.
            if len(v) >= 6 and not v.startswith("__") and not re.fullmatch(r"[a-z_]+", v):
                out.add(v)
    for p in paths:
        try:
            walk(json.loads(_read(p) or "{}"))
        except ValueError:
            pass
    return sorted(out, key=len, reverse=True)


class Scrubber:
    """One example's scrubbing: a ticket key keeps one stand-in throughout."""

    def __init__(self, private: list):
        self.keys: dict = {}
        self.private = private
        self.company = _company_rx(private)

    def _key(self, m) -> str:
        return self.keys.setdefault(m.group(0), f"ABC-{len(self.keys) + 1}")

    def _ident(self, m) -> str:
        n = self.keys.setdefault(f"{m.group(1).upper()}-{m.group(2)}", f"ABC-{len(self.keys) + 1}")
        return ("abc" if m.group(1).islower() else "Abc" if m.group(1)[0].isupper() and m.group(1)[1:].islower()
                else "ABC") + n.split("-")[1]

    def text(self, s: str) -> str:
        for v in self.private:
            if v in s:
                s = s.replace(v, "<private>")
        s = _URL_RX.sub("https://host.invalid", s)
        s = _EMAIL_RX.sub("user@example.test", s)
        s = _KEY_RX.sub(self._key, s)
        s = _IDENT_KEY_RX.sub(self._ident, s)
        if self.company:
            s = self.company.sub("Acme", s)
        s = _CODE_RX.sub(lambda m: m.group(0) if m.group(1) in _CODE_KEEP else '"CODE"', s)
        return s

    def template(self, raw: str) -> str:
        """The body's shape: every value a placeholder, the keys as they are."""
        text = re.sub(r"([:\[,]\s*)([@%][A-Za-z0-9_.]+[@%])", r'\1"\2"', raw)
        try:
            data = json.loads(text)
        except ValueError:
            return "(not JSON: its shape cannot be shown safely)"

        def walk(node, key=""):
            if isinstance(node, dict):
                return {self.text(k): walk(v, k) for k, v in node.items()}
            if isinstance(node, list):
                return [walk(v, key) for v in node[:3]]
            if isinstance(node, str):
                if _PLACEHOLDER_RX.match(node.strip()):
                    return self.text(node)
                # An enum word is what the server checks the field against:
                # hidden, it was invented ("mandatory" where every converted
                # body sends "none") and refused. Never a person's field.
                if _ENUM_WORD_RX.match(node) and (_ENUM_KEY_RX.search(key) or not _PERSONAL_KEY_RX.search(key)):
                    return self.text(node)
                return "<text>"
            if isinstance(node, bool) or node is None:
                return node
            if isinstance(node, (int, float)):
                # A small number is an enum or a count the API checks: written
                # as 0, it was copied, and employeeCountRange 0 was refused
                # (400, code 31) where every converted body sends 1. A long
                # one is an id or a phone number: not shown.
                return node if abs(node) < SMALL_NUMBER else "<number>"
            return "<value>"
        return json.dumps(walk(data), indent=2, ensure_ascii=False)


def leaks(text: str, private: list) -> list:
    """What in a scrubbed example could still be company data -- named, never quoted."""
    found = []
    company = _company_rx(private)
    if company and company.search(text):
        found.append("the company's name")
    if any(not e.lower().endswith("@example.test") for e in _EMAIL_RX.findall(text)):
        found.append("an e-mail address")
    if any(not k.startswith("ABC-") for k in _KEY_RX.findall(text)) or _IDENT_KEY_RX.search(text):
        found.append("a ticket key")
    if any(not re.match(r"https?://(host\.invalid|[a-z0-9.-]*example\.(test|com))", u) for u in _URL_RX.findall(text)):
        found.append("a host")
    if any(m.group(1) not in _CODE_KEEP for m in _CODE_RX.finditer(text)):
        found.append("a short upper-case code")
    if any(v in text for v in private):
        found.append("a value of the private configuration")
    return found


def method_text(java: str, method: str) -> str:
    """The test method, from its @Test to its closing brace."""
    at = java.find(f" void {method}(")
    if at < 0:
        return ""
    start = java.rfind("@Test", 0, at)
    start = start if start >= 0 and at - start < 2000 else java.rfind("\n", 0, at) + 1
    depth, i = 0, java.find("{", at)
    while 0 <= i < len(java):
        depth += {"{": 1, "}": -1}.get(java[i], 0)
        if depth == 0:
            return java[start:i + 1]
        i += 1
    return ""


def _annotations_cut(method: str) -> str:
    """Only @Test and the method: @XrayTest / @Story / @Description name tickets and links."""
    lines, out, skipping = method.splitlines(), [], False
    for line in lines:
        s = line.strip()
        if s.startswith(("@XrayTest", "@Story", "@Description", "@Epic", "@Feature", "@Owner")):
            skipping = not s.endswith(")") or s.startswith("@Description(\"\"\"")
            continue
        if skipping:
            if s.endswith(")") or s.endswith('""")'):
                skipping = False
            continue
        out.append(line)
    return "\n".join(out)


def phase_chain(root: str, suite: str, case: str, calls: list) -> list:
    """[(phase name, step, [spec names])] of the case's registration whose
    phases are the test method's calls, from support/<suite>/cases/*Phases.java."""
    base = os.path.join(root, *SUPPORT.split("/"), suite, "cases")
    reg = re.compile(r'CaseRegistry\.register\("' + re.escape(suite) + r'",\s*"' + re.escape(case) + r'"\)(.*?);', re.S)
    # .phase(...) makes a call; .verify(...) makes one too, to check it --
    # a spend-detail example lost its spend-detail calls without these.
    phase = re.compile(r'\.(?:phase|verify)\("([^"]+)",\s*"([^"]+)"((?:,\s*\w+::\w+)*)\)')
    best = []
    for name in sorted(os.listdir(base)) if os.path.isdir(base) else []:
        if not name.endswith("Phases.java"):
            continue
        for m in reg.finditer(_read(os.path.join(base, name))):
            chain = [(p.group(1), p.group(2), re.findall(r"(\w+)::(\w+)", p.group(3))) for p in phase.finditer(m.group(1))]
            if [c[0] for c in chain] == calls:
                return chain
            best = best or chain
    return best


def spec_text(root: str, suite: str, cls: str, name: str) -> str:
    d = os.path.join(root, *SUPPORT.split("/"), suite)
    for dirpath, _dirs, files in os.walk(d):
        if cls + ".java" in files:
            java = _read(os.path.join(dirpath, cls + ".java"))
            m = re.search(r"static PhaseSpec " + re.escape(name) + r"\(\)\s*\{(.*?)\n    \}", java, re.S)
            if m:
                lines = [l.rstrip() for l in m.group(1).strip().splitlines()]
                return "\n".join(lines[:MAX_SPEC_LINES])
    return ""


def resolve_method(root: str, pick: dict) -> str:
    """The test method of the converted case of `pick` -- its suite and its
    exact chain of calls (picks.json names no method: a method name can
    carry a ticket number) -- on this machine, or ""."""
    fc = _load("refexamples_flowcatalog", os.path.join(HERE, "flowcatalog.py"))
    for ch in (fc.load(root, pick["project"]).get("chains") or []):
        if ch.get("suite") == pick["suite"] and ch.get("steps") == pick["steps"]:
            for f in fc.files_for(root, ch):
                m = re.search(r"\(test, method (\w+)\)$", f)
                if m:
                    return m.group(1)
    return ""


def build_one(root: str, pick: dict, private: list) -> tuple:
    """(markdown, calls, why-not) for one pick."""
    suite = pick["suite"]
    method = pick.get("method") or resolve_method(root, pick)
    if not method:
        return "", [], f"{suite}: no converted case with the chain {pick.get('steps')} on this machine"
    rows = list(csv.DictReader(io.StringIO(_read(os.path.join(root, "_audit", suite, "case_to_method_mapping.csv")))))
    row = next((r for r in rows if r.get("java_method") == method), None)
    if not row:
        return "", [], f"{suite}.{method} is not in this machine's audit (convert {suite} first)"
    case, fqn = row.get("soapui_case") or "", row.get("java_class_fqn") or ""
    java = _read(os.path.join(root, "src", "test", "java", *fqn.split(".")) + ".java") if fqn else ""
    body = _annotations_cut(method_text(java, method))
    if not body:
        return "", [], f"{fqn}#{method} was not found"
    calls = re.findall(r"^\s*\.(\w+)\(\)", body, re.M)
    calls = [c for c in calls if c not in ("complete",)]
    chain = phase_chain(root, suite, case, calls)
    templates = {}
    for r in csv.DictReader(io.StringIO(_read(os.path.join(root, "src", "main", "resources", "templates", suite, "_index.csv")))):
        if r.get("case") == case:
            templates.setdefault(r.get("step") or "", r.get("template") or "")
    sheet = (row.get("csv_path") or "").replace("\\", "/")
    header = _read(os.path.join(root, *sheet.split("/")), 20000).splitlines()[:1] if sheet else []
    n_rows = max(0, len(_read(os.path.join(root, *sheet.split("/"))).splitlines()) - 1) if sheet else 0

    s = Scrubber(private)
    verbs = []
    out = [f"# {s.text(pick.get('title') or method)}", "",
           f"Project: **{pick['project'].upper()}**. A converted test, scrubbed: ticket keys are `ABC-n`, hosts "
           "`host.invalid`, e-mails `example.test`, short codes `\"CODE\"`, body text `<text>`; enum words, small "
           "numbers and true/false are what the converted body sends (`<number>`: a long one, not shown). A "
           "REFERENCE for the shape of a test -- the calls, their order, what each passes on and checks -- "
           "not values to copy: a body's values come from the converted body itself "
           "(src/main/resources/templates/). Generated by tools/agent/refexamples.py; do not edit by hand.", ""]
    out += ["## The test method (converted, phase mode)", "", "```java", s.text(body), "```", ""]
    out += ["## Its steps, in order", ""]
    for phase_name, step, specs in chain:
        out += [f"### `{phase_name}` (ReadyAPI step `{s.text(step)}`)", ""]
        for cls, name in specs:
            text = spec_text(root, suite, cls, name)
            if not text:
                continue
            m = re.search(r'\.(get|post|put|patch|delete)\("([^"]+)"', text)
            if m:
                verbs.append(f"{m.group(1).upper()} {m.group(2)}")
            out += ["```java", s.text(text), "```", ""]
        t = templates.get(step)
        if t:
            out += ["Request body (`" + s.text(t.split("/")[-1]) + "`), its shape:", "", "```json",
                    s.template(_read(os.path.join(root, "src", "main", "resources", *t.split("/")))), "```", ""]
    if header:
        cols = next(csv.reader([header[0]]), [])
        out += ["## Its data sheet: the columns (no row; it holds " + str(n_rows) + ")", "",
                ", ".join(f"`{s.text(c)}`" for c in cols), ""]
    helpers = ("`goalToken`, `findAvailability`, `createGroup`, `payGroup`, `confirmGroup` "
               "(GoalStoryTestBase)" if pick["project"] == "goal" else
               "`enrollActor`, `createAccountFor`, `activateAccount`, `addMember`, `setMemberRole` "
               "(StoryTestBase)")
    out += ["## How a story test does the same", "",
            f"A story test (tests/jira/) makes the same calls with the helpers -- {helpers}, "
            "`route(ApiRoutes.X, ...)`, `ManualBody.render(template, row, ctx)` -- in the same order, "
            "with the same expected statuses and checks; its body templates keep every field the "
            "converted bodies above send.", ""]
    text = "\n".join(out)
    found = leaks(text, private)
    if found:
        return "", verbs, f"{suite}.{method}: still holds " + ", ".join(sorted(set(found))) + " after scrubbing"
    return text, verbs, ""


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "example"


def build(root: str = "") -> int:
    root = root or ROOT
    try:
        picks = json.loads(_read(os.path.join(root, PICKS)) or "[]")
    except ValueError:
        print(f"FAIL {PICKS} is not JSON")
        return 1
    private = private_values(root)
    index, failed = [], 0
    for pick in picks:
        text, calls, why = build_one(root, pick, private)
        if why:
            print(f"FAIL {why} -- not written")
            failed += 1
            continue
        rel = os.path.join(OUT, pick["project"], _slug(pick.get("title") or pick["method"]) + ".md")
        os.makedirs(os.path.join(root, os.path.dirname(rel)), exist_ok=True)
        with io.open(os.path.join(root, rel), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        index.append({"project": pick["project"], "title": pick.get("title") or pick["method"],
                      "file": rel.replace(os.sep, "/"), "calls": calls})
        print(f"  wrote {rel.replace(os.sep, '/')}  ({len(calls)} call(s))")
    with io.open(os.path.join(root, INDEX), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(index, fh, indent=2)
        fh.write("\n")
    print(f"{len(index)} example(s) written, {failed} refused")
    return 1 if failed else 0


def check(root: str = "") -> int:
    """Every committed example scanned again (after a hand edit, or a new pick)."""
    root = root or ROOT
    private, bad = private_values(root), 0
    for dirpath, _dirs, files in os.walk(os.path.join(root, OUT)):
        for f in files:
            if f.endswith(".md"):
                found = leaks(_read(os.path.join(dirpath, f)), private)
                if found:
                    bad += 1
                    print(f"FAIL {os.path.relpath(os.path.join(dirpath, f), root)}: " + ", ".join(sorted(set(found))))
    print("the examples hold no company data" if not bad else f"{bad} example(s) hold company data")
    return 1 if bad else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=("build", "check"))
    ap.add_argument("--root", default="")
    a = ap.parse_args(argv)
    return build(a.root) if a.command == "build" else check(a.root)


if __name__ == "__main__":
    sys.exit(main())
