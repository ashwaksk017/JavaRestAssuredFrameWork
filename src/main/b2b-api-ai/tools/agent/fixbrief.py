"""A failing converted case becomes a job for Cursor.

    python tools/agent/fixbrief.py --job J --signature ID [--scope converted|converter]
    python tools/agent/fixbrief.py --job J --test FooTest.barTest [--suite amex]

The Agentic tab can already hold Cursor to one converted suite's files
(scope `converted`) or to the converter's emitters (scope `converter`),
with the right skill and the guardrails. What it could not do was say
WHAT is wrong: for a Jira story the brief comes from Read it; for a
failing converted case nobody had written one.

This writes that brief, from what the run left behind:

* the failing (test, row, signature) lines of the latest recorded run
  (`.failure-history/`, the Failures tab's Record), or of the digest
  `target/failure-digest.txt` when nothing was recorded;
* surefire's message and the head of the stack for the test;
* the last request/response log of the case (`logs/`), redacted;
* the converter's own notes on the case from `_audit/<suite>/`: which
  ReadyAPI case it is, its data row, what was skipped or left as a
  placeholder, what preflight said;
* the note a person attached to the signature, when there is one.

`brief.md` and `plan.md` go to the job; `fix-brief.json` says what was
gathered, and which scope and suite the Agentic tab should take. A job
that already holds a story's reading is refused: a fix is its own job.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name: str, path: str):
    import importlib.util
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return mod


diag = _load("workbench_diag", os.path.join(os.path.dirname(HERE), "diag.py"))
loop = _load("fixbrief_loop", os.path.join(HERE, "loop.py"))
intake = loop.intake
fh = _load("fixbrief_failure_history", os.path.join(os.path.dirname(HERE), "failure_history.py"))

ROOT = intake.ROOT
RESULT = "fix-brief.json"
SCOPES = ("converted", "converter")
MAX_CASES = 10
BRIEF_CAP = 50_000          # of brief.md; the prompt reads at most 60,000 and must not cut mid-case
MAX_LOG_LINES = 60
MAX_LOG_CHARS = 4000
MAX_STACK_LINES = 25
MAX_NOTE_LINES = 12
IMPORTED = "src/test/java/com/hi/api/tests/imported/"
_SUITE_RX = re.compile(r"^[a-z0-9_]{1,80}$")


class Refused(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _text(path: str, cap: int = 2_000_000) -> str:
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh_:
            return fh_.read(cap)
    except OSError:
        return ""


def _redactor(root: str):
    """Everything quoted from a run goes through this: the configured
    secrets and hosts, then the general redaction."""
    private = loop.private_config(root)
    values = sorted((v for v in private if isinstance(v, str) and len(v) >= 4), key=len, reverse=True)

    def host(m) -> str:
        # Only a host in a URL: a bare dotted name is as often a Java
        # package or class (com.hi.api.tests...SpendTest) as a host.
        name = m.group(2).lower()
        if name.endswith(loop._PUBLIC_HOSTS):
            return m.group(0)
        return m.group(1) + "<host>"

    def redact(text: str) -> str:
        for v in values:
            text = text.replace(v, "<redacted>")
        # A token quoted in an assertion message ("... for Bearer xyz") is
        # not in a header, where the general redaction looks for it; and a
        # host in a URL (a redirect, a JDBC step) is not in the config.
        text = _BEARER_RX.sub(r"\1<redacted>", text)
        text = _URL_HOST_RX.sub(host, text)
        return intake.conf_fetch.redact(text)
    return redact


_BEARER_RX = re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9._~+/=-]{16,}")
_URL_HOST_RX = re.compile(r"(?i)(https?://(?:[^/\s@]*@)?)([a-z0-9-]+(?:\.[a-z0-9-]+)+)")


# ---- what failed ----------------------------------------------------------------------

def failures_of(root: str, sid: str = "") -> dict:
    """The latest recorded run -- with the signature, when one is asked
    for: the Failures tab shows cards of more than one digest suite, and
    the newest run overall may be another suite's -- or the digest on
    disk when nothing was recorded. {source, at, label, suite, failures}."""
    snaps = fh.snapshots(root)
    if sid:
        snaps = [s for s in snaps if any(isinstance(f, dict) and fh.signature_id(f.get("signature", "")) == sid
                                         for f in s.get("failures", []))] or snaps
    if snaps:
        s = snaps[-1]
        return {"source": f".failure-history/{s.get('_file', '')}", "at": s.get("at", ""),
                "label": s.get("label", ""), "suite": s.get("suite", ""),
                "failures": [f for f in s.get("failures", []) if isinstance(f, dict)]}
    digest = os.path.join(root, fh.DIGEST)
    if os.path.isfile(digest):
        parsed = fh.parse_digest(_text(digest))
        return {"source": fh.DIGEST, "at": "", "label": "", "suite": parsed.get("suite", ""),
                "failures": parsed.get("failures", [])}
    raise Refused("no recorded run and no failure digest: run the tests, then Record this run on "
                  "the Failures tab (or leave target/failure-digest.txt where the run wrote it)")


def _split_test(test: str) -> tuple:
    """('com.x.FooTest' or 'FooTest' or '', 'barTest') from how the digest
    names a test: a method after the last dot, the class before it."""
    test = (test or "").strip()
    if "(" in test:
        test = test.split("(", 1)[0]
    if "." in test:
        cls, method = test.rsplit(".", 1)
        return cls, method
    return "", test


class Ambiguous(Refused):
    pass


def _candidates(root: str, cls: str) -> list:
    """[(qualified name, class file, suite)] for the class a digest names.
    The digest writes the SIMPLE name, and the converted tree holds the
    same simple name in several suites (AddMemberTest in six of them):
    every holder is a candidate until something says which."""
    if not cls:
        return []
    simple = cls.rsplit(".", 1)[-1]
    if "." in cls:
        rel = "src/test/java/" + cls.replace(".", "/") + ".java"
        if os.path.isfile(os.path.join(root, rel)):
            return [(cls, rel, _suite_of(rel))]
    out = []
    base = os.path.join(root, IMPORTED.replace("/", os.sep))
    for dirpath, _dirs, files in os.walk(base):
        if simple + ".java" in files:
            rel = os.path.relpath(os.path.join(dirpath, simple + ".java"), root).replace("\\", "/")
            fqn = rel[len("src/test/java/"):-len(".java")].replace("/", ".")
            out.append((fqn, rel, _suite_of(rel)))
    return sorted(out)


def _testcases(root: str) -> list:
    """Every <testcase> of the last run's reports. A suite run writes
    TEST-TestSuite.xml; a run of one class (-Dtest=...) writes
    TEST-<class>.xml beside it and in junitreports/: all are read, the
    suite's first, each test case once."""
    base = os.path.join(root, os.path.dirname(fh.SUREFIRE))
    files = [os.path.join(root, fh.SUREFIRE)]
    for sub in ("", "junitreports"):
        d = os.path.join(base, sub)
        try:
            files += sorted(os.path.join(d, n) for n in os.listdir(d)
                            if n.startswith("TEST-") and n.endswith(".xml"))
        except OSError:
            pass
    out, seen = [], set()
    for path in files:
        if not os.path.isfile(path) or path in seen:
            continue
        seen.add(path)
        try:
            tree = ET.parse(path)
        except (ET.ParseError, OSError):
            continue
        out += list(tree.iter("testcase"))
    return out


def _surefire_classnames(root: str, method: str, simple: str) -> set:
    """The qualified class names surefire ran `method` on, ending in the
    simple name."""
    return {tc.get("classname", "") for tc in _testcases(root)
            if tc.get("name") == method and tc.get("classname", "").endswith("." + simple)}


def _audit_knows(root: str, suite: str, method: str, fqn: str) -> bool:
    for r in _csv_rows(os.path.join(root, "_audit", suite, "case_to_method_mapping.csv")):
        if r.get("java_method") == method and r.get("java_class_fqn", "") == fqn:
            return True
    return False


def _resolve(root: str, cls: str, method: str, suite: str) -> tuple:
    """(qualified name or the simple one, class file or '', suite or '').
    One candidate: taken. Several: the named suite's, else the one surefire
    ran, else the one whose suite's audit maps the method; still several:
    refused, naming the suites, since a brief about the wrong suite would
    send Cursor to the wrong files."""
    cands = _candidates(root, cls)
    if suite:
        own = [c for c in cands if c[2] == suite]
        if own:
            return own[0]
    if len(cands) == 1:
        return cands[0]
    if not cands:
        return cls, "", ""
    simple = cls.rsplit(".", 1)[-1]
    ran = _surefire_classnames(root, method, simple)
    hits = [c for c in cands if c[0] in ran]
    if len(hits) == 1:
        return hits[0]
    hits = [c for c in cands if _audit_knows(root, c[2], method, c[0])]
    if len(hits) == 1:
        return hits[0]
    if suite:
        return cls, "", ""           # named a suite that does not hold it: the caller leaves it out
    raise Ambiguous(f"{cls}.{method} exists in suites {', '.join(sorted({c[2] for c in cands}))} and "
                    f"neither surefire nor the audit says which one failed: name the suite in the "
                    f"Suite box of the Migration from ReadyAPI tab (or --suite)")


def _suite_of(class_file: str) -> str:
    if class_file.startswith(IMPORTED):
        rest = class_file[len(IMPORTED):]
        suite = rest.split("/", 1)[0]
        return suite if _SUITE_RX.match(suite) else ""
    return ""


def cn_ok(cn: str, cls: str, simple: str) -> bool:
    return bool(cn) and (not cls or cn == cls or cn.endswith("." + simple))


def _surefire(root: str, cls: str, method: str, redact) -> dict:
    """{message, type, stack} for the test from surefire's report, or {}."""
    simple = cls.rsplit(".", 1)[-1] if cls else ""
    every = _testcases(root)
    if not every:
        return {}
    # The same case can be in two reports (suite and per class): one is enough,
    # and the one that failed is the one to show.
    cases, seen = [], set()
    for tc in sorted((tc for tc in every if tc.get("name") == method),
                     key=lambda tc: 0 if (tc.find("failure") is not None or tc.find("error") is not None) else 1):
        if tc.get("classname", "") not in seen:
            seen.add(tc.get("classname", ""))
            cases.append(tc)
    exact = [tc for tc in cases if tc.get("classname", "") == cls]
    if exact:
        cases = exact
    else:
        cases = [tc for tc in cases if cn_ok(tc.get("classname", ""), cls, simple)]
        if len(cases) != 1:
            return {}                # several classes ran a method of that name: nothing is guessed
    for tc in cases:
        cn = tc.get("classname", "")
        for kind in ("failure", "error"):
            node = tc.find(kind)
            if node is not None:
                stack = "\n".join((node.text or "").strip().splitlines()[:MAX_STACK_LINES])
                return {"message": redact(node.get("message") or "")[:1500], "type": node.get("type", ""),
                        "stack": redact(stack)[:3000], "classname": cn}
        return {"classname": cn, "message": "", "type": "", "stack": ""}
    return {}


def _row_log(root: str, cls: str, method: str, row: str, redact) -> dict:
    """The newest per-row log of the method under logs/, its tail
    redacted. {file, tail, of, maybe_other_row} or {}. A qualified class
    name matches its own files; a simple one matches `_Simple__method`,
    never a longer name that happens to end the same way. The row's own
    log cannot be told from the file name (it carries a hash), so with
    several logs of the method the brief says the one shown may be
    another row's."""
    base = os.path.join(root, "logs")
    if not cls or not os.path.isdir(base):
        return {}
    marker = ("_" if "." in cls else "") + cls.replace(".", "_") + "__" + method + "__"
    found = []
    try:
        for name in os.listdir(base):
            if name.endswith(".log") and (name.startswith(marker.lstrip("_")) if "." in cls else marker in name):
                p = os.path.join(base, name)
                found.append((os.path.getmtime(p), name))
    except OSError:
        return {}
    if not found:
        return {}
    name = max(found)[1]
    lines = _text(os.path.join(base, name)).splitlines()
    tail = "\n".join(lines[-MAX_LOG_LINES:])
    if len(tail) > MAX_LOG_CHARS:
        tail = tail[-MAX_LOG_CHARS:]
    return {"file": f"logs/{name}", "tail": redact(tail), "of": len(found),
            "maybe_other_row": len(found) > 1 and bool(row) and row != "-"}


def _csv_rows(path: str) -> list:
    try:
        with io.open(path, encoding="utf-8", errors="replace", newline="") as fh_:
            return [r for r in csv.DictReader(fh_)]
    except OSError:
        return []


def _audit(root: str, suite: str, method: str, redact) -> dict:
    """What the converter wrote down about this case."""
    out = {"case": "", "csv_path": "", "expected_status": "", "xray_key": "", "skips": [],
           "placeholders": [], "preflight": []}
    if not suite:
        return out
    base = os.path.join(root, "_audit", suite)
    for r in _csv_rows(os.path.join(base, "case_to_method_mapping.csv")):
        if r.get("java_method") == method:
            out["case"] = redact(r.get("soapui_case", ""))[:200]
            out["csv_path"] = redact(r.get("csv_path", ""))[:300]
            out["expected_status"] = redact(r.get("expected_status", ""))[:20]
            out["xray_key"] = redact(r.get("xray_key", ""))[:40]
            break
    for r in _csv_rows(os.path.join(base, "runtime_skips.csv")):
        if r.get("java_method") == method or (out["case"] and r.get("case") == out["case"]):
            out["skips"].append(redact(f"{r.get('reason', '')} (step: {r.get('source_step', '')})")[:400])
    if out["case"]:
        for r in _csv_rows(os.path.join(base, "placeholders.csv")):
            if r.get("case") == out["case"]:
                out["placeholders"].append(redact(f"{r.get('placeholder', '')} ({r.get('kind', '')})")[:120])
        pre = _text(os.path.join(base, "preflight.md"))
        out["preflight"] = [redact(l.strip())[:300] for l in pre.splitlines()
                            if out["case"] in l][:MAX_NOTE_LINES]
    out["skips"] = out["skips"][:MAX_NOTE_LINES]
    out["placeholders"] = out["placeholders"][:40]
    return out


def _suite_files(root: str, suite: str) -> list:
    base = os.path.join(root, "src", "main", "java", "com", "hi", "api", "support", suite)
    try:
        return sorted(n for n in os.listdir(base) if n.endswith(".java"))[:30]
    except OSError:
        return []


# ---- the brief ------------------------------------------------------------------------

def gather(root: str, signature: str = "", test: str = "", suite: str = "", scope: str = "converted") -> dict:
    if scope not in SCOPES:
        raise Refused(f"--scope is one of: {', '.join(SCOPES)}")
    if suite and not _SUITE_RX.match(suite):
        raise Refused(f"{suite!r} is not a suite directory name (lower case, digits, underscore)")
    sid = signature.strip().strip("[]").lower()
    run = failures_of(root, sid)
    chosen = run["failures"]
    if sid:
        chosen = [f for f in chosen if fh.signature_id(f.get("signature", "")) == sid]
        if not chosen:
            raise Refused(f"no failure of the latest run has the signature id {sid}; Record this run "
                          f"again if the run is newer than the record")
    elif test.strip():
        want = test.strip()
        chosen = [f for f in chosen if want in f.get("test", "")]
        if not chosen:
            raise Refused(f"no failure of the latest run names a test containing {want!r}")
    if not chosen:
        raise Refused("the latest run has no failure to fix")
    left_out = max(0, len(chosen) - MAX_CASES)
    left_out_names = [str(f.get("test", "")) for f in chosen[MAX_CASES:MAX_CASES + 10]]
    chosen = chosen[:MAX_CASES]
    redact = _redactor(root)
    cases, suites, other_suites = [], set(), []
    for f in chosen:
        cls0, method = _split_test(f.get("test", ""))
        cls, class_file, own = _resolve(root, cls0, method, suite)
        if suite and own and own != suite:
            # A signature can be shared across suites (the same 403 from
            # two of them). Named a suite, the job takes that suite's
            # cases and names the others as left out -- never relabels.
            other_suites.append(f"{f.get('test', '')} ({own})")
            continue
        found_suite = suite or own
        if found_suite:
            suites.add(found_suite)
        sf = _surefire(root, cls, method, redact)
        row = str(f.get("row", ""))
        cases.append({
            "test": redact(f.get("test", ""))[:200], "row": redact(row)[:80],
            "signature": redact(f.get("signature", ""))[:600],
            "signature_id": fh.signature_id(f.get("signature", "")),
            "class_file": class_file, "suite": found_suite, "method": method,
            "surefire": sf, "log": _row_log(root, sf.get("classname") or cls, method, row, redact),
            "audit": _audit(root, found_suite, method, redact),
        })
    if len(suites) > 1:
        raise Refused(f"the chosen failures span {len(suites)} suites ({', '.join(sorted(suites))}); "
                      f"a converted job is one suite -- name the suite in the Migration tab's Suite box "
                      f"(or --suite) and the job takes that suite's cases")
    if not cases:
        raise Refused(f"none of the chosen failures is in suite {suite}; they are in: "
                      + "; ".join(other_suites[:5]))
    the_suite = suite or (next(iter(suites)) if suites else "")
    if scope == "converted" and not the_suite:
        raise Refused("the suite could not be told from the failing tests (not under tests/imported/"
                      "<suite>/); name it with --suite, or choose scope converter")
    note = fh.notes(root).get(sid) if sid else None
    return {"kind": "fix-brief", "at": _now(), "scope": scope, "suite": the_suite,
            "signature": sid, "run": {k: run[k] for k in ("source", "at", "label", "suite")},
            "cases": cases, "left_out": left_out, "left_out_names": [redact(n)[:200] for n in left_out_names],
            "other_suites": [redact(o)[:300] for o in other_suites[:20]], "cut": [],
            "note": ({"text": redact(note["text"])[:2000], "at": str(note.get("at", ""))} if note else None),
            "suite_files": _suite_files(root, the_suite) if the_suite else []}


def brief_text(g: dict, job: str) -> str:
    L = [f"# Fix brief: suite {g['suite'] or '(not one suite)'} -- {len(g['cases'])} failing case(s)", "",
         f"- job: {job}", f"- at: {g['at']}", f"- scope: {g['scope']}",
         f"- from: {g['run']['source']}" + (f" (run of {g['run']['at']}" + (f", {g['run']['label']}" if g['run']['label'] else "") + ")" if g['run']['at'] else ""),
         f"- digest suite label: {g['run']['suite']}"]
    if g["signature"]:
        L.append(f"- signature id: [{g['signature']}]")
    if g["left_out"]:
        L.append(f"- {g['left_out']} more failing case(s) with the same choice were left out (at most "
                 f"{MAX_CASES} a job): " + "; ".join(g.get("left_out_names") or []))
    if g.get("cut"):
        L.append(f"- {len(g['cut'])} case(s) were cut from this brief to keep it readable in one prompt: "
                 + "; ".join(g["cut"]) + " -- they fail the same way; fix them too")
    if g.get("other_suites"):
        L.append(f"- the same choice also fails in other suites, not this job's: " + "; ".join(g["other_suites"][:5]))
    L.append("")
    if g["note"]:
        L += ["## What this signature turned out to be before", "",
              f"> {g['note']['text']}  ({g['note']['at']})", ""]
    for n, c in enumerate(g["cases"], 1):
        a = c["audit"]
        L += [f"## Case {n}: {c['test']}" + (f"  [row {c['row']}]" if c['row'] and c['row'] != '-' else ""), ""]
        if a["case"]:
            L.append(f"- ReadyAPI case: `{a['case']}`" + (f", expected status {a['expected_status']}" if a['expected_status'] else "")
                     + (f", Xray {a['xray_key']}" if a['xray_key'] else ""))
        if c["class_file"]:
            L.append(f"- test class: `{c['class_file']}`")
        if a["csv_path"]:
            L.append(f"- data row: `{a['csv_path']}`" + (f" (row {c['row']})" if c['row'] and c['row'] != '-' else ""))
        L.append(f"- failure signature: {c['signature']}")
        sf = c["surefire"]
        if sf.get("message") or sf.get("stack"):
            L += ["", "### What surefire recorded", ""]
            if sf.get("message"):
                L.append(f"{sf.get('type', '')}: {sf['message']}".strip(": "))
            if sf.get("stack"):
                L += ["", "```", sf["stack"], "```"]
        if c["log"]:
            L += ["", f"### The last request and response (`{c['log']['file']}`, tail)"
                      + (f" -- the newest of {c['log']['of']} logs of this method; it may be another row's"
                         if c["log"].get("maybe_other_row") else ""), "", "```",
                  c["log"]["tail"], "```"]
        if a["skips"] or a["placeholders"] or a["preflight"]:
            L += ["", "### What the converter noted about this case", ""]
            for s in a["skips"]:
                L.append(f"- skipped at run time: {s}")
            if a["placeholders"]:
                L.append(f"- placeholders: {', '.join(a['placeholders'])}")
            for p in a["preflight"]:
                L.append(f"- preflight: {p}")
        L.append("")
    if g["suite_files"]:
        L += [f"## This suite's generated classes (`src/main/java/com/hi/api/support/{g['suite']}/`)", "",
              ", ".join(g["suite_files"]), ""]
    L += ["## Next", "", "On the Migration from ReadyAPI tab, press Ask Cursor with the box empty: it runs on this brief."]
    return "\n".join(L).rstrip() + "\n"


def plan_text(g: dict) -> str:
    names = "; ".join(f"{c['test']}" + (f" [row {c['row']}]" if c['row'] and c['row'] != '-' else "")
                      for c in g["cases"])
    if g["scope"] == "converted":
        return "\n".join([
            f"# Plan: fix the converted Java of suite {g['suite']}", "",
            f"FIX -- make these {len(g['cases'])} case(s) pass: {names}", "",
            f"Change only this suite's generated files: support/{g['suite']}/ (Hooks, Specs, Phases, "
            f"Steps), tests/imported/{g['suite']}/, templates/{g['suite']}/, csv/{g['suite']}/, and "
            f"its client. The brief says what failed, what was sent and what came back, and what the "
            f"converter noted about the case.", "",
            "If the cause is in a class EVERY suite shares (ImportedScenario, ImportedRestClient, "
            "the runtime under rest/utilities), or in what the converter emits for every suite, write "
            "nothing for it and say so: that is a job for the scope 'The converter itself'.", "",
            "Do not change an expected value to match what the application returned: if the "
            "application is wrong, say so. Do not touch another suite. For each case, say what you "
            "changed and why, or why it cannot be fixed here.", ""])
    return "\n".join([
        f"# Plan: fix the converter for what suite {g['suite'] or '(see the brief)'} shows", "",
        f"FIX THE EMITTER -- these {len(g['cases'])} case(s) fail for a reason in what the converter "
        f"writes: {names}", "",
        "Change the emitter under tools/ra_converter/ (or the hand-maintained runtime under it), never "
        "a generated copy: the next convert overwrites those. Add or extend a test_*.py for the "
        "change. Name the suite(s) that must be converted again for the fix to reach them.", "",
        "Do not change an expected value to match what the application returned: if the application "
        "is wrong, say so.", ""])


def build(job: str, root: str = "", signature: str = "", test: str = "", suite: str = "",
          scope: str = "converted") -> dict:
    root = root or ROOT
    job_dir = intake.job_dir(job, root)
    # A fix is its own job. A story, a design, a review or Xray keys left
    # from another use would reach the agent's prompt (designed_cases reads
    # design.json; a review trims the brief) and send it elsewhere.
    held = [n for n in ("design.json", "case-review.json", "xray-tests.json")
            if os.path.isfile(os.path.join(job_dir, n))]
    if os.path.isfile(os.path.join(job_dir, "intake.json")):
        try:
            with io.open(os.path.join(job_dir, "intake.json"), encoding="utf-8") as fh_:
                if isinstance(json.load(fh_).get("story"), dict):
                    held.append("a story (intake.json)")
        except (OSError, ValueError):
            pass
    plan = _text(os.path.join(job_dir, "plan.md"), 400)
    if plan.strip() and not plan.lstrip().startswith("# Plan: fix"):
        held.append("a plan of another kind (plan.md)")
    if held:
        raise Refused(f"this job already holds {', '.join(held)}; a fix is its own job -- name a new "
                      f"job (top right) and run this again")
    diag.step("Reading the latest recorded run and what it left behind")
    g = gather(root, signature=signature, test=test, suite=suite, scope=scope)
    os.makedirs(job_dir, exist_ok=True)
    diag.step("Writing the fix brief and the plan")
    text = brief_text(g, job)
    while len(text) > BRIEF_CAP and len(g["cases"]) > 1:
        gone = g["cases"].pop()
        g["cut"].append(gone["test"] + (f" [row {gone['row']}]" if gone["row"] and gone["row"] != "-" else ""))
        text = brief_text(g, job)
    with io.open(os.path.join(job_dir, "brief.md"), "w", encoding="utf-8") as fh_:
        fh_.write(text)
    with io.open(os.path.join(job_dir, "plan.md"), "w", encoding="utf-8") as fh_:
        fh_.write(plan_text(g))
    out = dict(g, ok=True, job=job)
    with io.open(os.path.join(job_dir, RESULT), "w", encoding="utf-8") as fh_:
        json.dump(out, fh_, indent=1, ensure_ascii=False)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--job", required=True)
    ap.add_argument("--signature", default="", help="the [id] the Failures tab shows")
    ap.add_argument("--test", default="", help="part of a failing test's name, instead of a signature")
    ap.add_argument("--suite", default="", help="the converted suite, when it cannot be told from the test")
    ap.add_argument("--scope", default="converted", choices=SCOPES)
    a = ap.parse_args(argv)
    try:
        job_dir = intake.job_dir(a.job)
    except ValueError as e:
        print(f"REFUSED {e}")
        return 2
    try:
        g = build(a.job, signature=a.signature, test=a.test, suite=a.suite, scope=a.scope)
    except (Refused, fh.Unusable) as e:
        print(f"REFUSED {e}")
        try:
            os.makedirs(job_dir, exist_ok=True)
            with io.open(os.path.join(job_dir, RESULT), "w", encoding="utf-8") as fh_:
                json.dump({"kind": "error", "ok": False, "message": str(e), "at": _now()}, fh_)
        except OSError:
            pass
        return 1
    print(f" ok   {len(g['cases'])} failing case(s) of suite {g['suite'] or '-'} in the brief; "
          f"scope {g['scope']}. Next: the Migration from ReadyAPI tab, Ask Cursor.")
    for c in g["cases"]:
        print(f"  - {c['test']}" + (f" [row {c['row']}]" if c['row'] and c['row'] != '-' else ""))
    return 0


if __name__ == "__main__":
    sys.exit(diag.run("fixbrief", main))
