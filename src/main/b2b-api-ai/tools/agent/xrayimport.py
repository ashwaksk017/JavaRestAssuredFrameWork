"""A story's existing Xray tests become the cases to automate.

    python tools/agent/xrayimport.py --job J [--story ABC-123]

Design the tests asks Cursor to propose cases; Create in Xray makes them
Jira tests. When the tests already exist in Jira -- written by a person,
linked to the story -- proposing new ones would duplicate them. This
reads them instead:

* the story's issue links of the test link type (jira_config.publish.
  test_link_type, "Tests" by default: the story "is tested by" each);
* each test's steps -- action, data, expected result -- from Xray
  (Server / Data Center);
* and writes them as the job's design (design.json, design.md), one
  case a test, with the test's key recorded in xray-tests.json.

From there it is the usual flow: Review the cases, then the Agentic Test
Creation step writes REST Assured tests with the real keys in the
jira_xray_id column, so results report to the tests that exist. Create
in Xray finds each one already there and creates nothing.

A test with no steps is listed as not used. A job that already has a
design is refused: Clear the design first.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
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
xt = _load("agent_xraytests", os.path.join(HERE, "xraytests.py"))
mt = _load("agent_manualtests", os.path.join(HERE, "manualtests.py"))
loop = xt.loop
search = xt.search
publish = xt.publish

ROOT = loop.intake.ROOT
RESULT = "xray-import.json"
MAX_TESTS = 40
MAX_STEPS = 40
MAX_TEXT = 2000
_KEY_RX = re.compile(r"^[A-Z][A-Z0-9_]{0,19}-[0-9]{1,9}$")
_STEP_ENDPOINT_RX = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE)[:\s]+(/[^\s\"'`,;)]+)")


def endpoint_in_steps(steps: list) -> str:
    """The first `VERB /path` a test's steps name, or ""."""
    for s in steps:
        for field in ("action", "data", "expected"):
            m = _STEP_ENDPOINT_RX.search(str(s.get(field) or ""))
            if m:
                return f"{m.group(1)} {m.group(2).rstrip('.')}"
    return ""


def spec_keys(job_dir: str) -> set:
    """The endpoints of the job's specification, as endpoint_key writes
    them; an empty set when there is none."""
    spec, _where, _rel = loop.job_spec(job_dir)
    if spec is None:
        return set()
    d = loop._design_module()
    lines = d.match_lines(d.endpoints_of(spec), d.base_paths(spec))
    return {d.endpoint_key(line) for line in lines} - {""}


class Refused(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _one(value, cap: int) -> str:
    return " ".join(str(value or "").split())[:cap]


def _text(value, cap: int) -> str:
    return str(value or "").strip()[:cap]


def story_of(job_dir: str) -> str:
    try:
        with io.open(os.path.join(job_dir, "intake.json"), encoding="utf-8") as fh:
            story = json.load(fh).get("story")
        return str(story.get("key") or "").upper() if isinstance(story, dict) else ""
    except (OSError, ValueError, AttributeError):
        return ""


def linked_tests(issue: dict, link_type: str, test_type: str, other_way: list = None) -> list:
    """[{key, summary, status}] of the issues linked to the story by the
    test link type, in Jira's order -- every one; the caller caps them.
    The name of the link type, or its wording ("is tested by" / "tests"),
    decides; an issue whose type is named and is not the test type is
    left out.

    The direction counts. Read from the story, a test that tests it sits
    on the link's INWARD side (the story "is tested by" it). An issue on
    the outward side is one the story itself "tests": not taken, and --
    when `other_way` is given -- added to it as {key, why}, so a link
    made the wrong way round is named, not lost."""
    fields = issue.get("fields") if isinstance(issue.get("fields"), dict) else {}
    out, seen = [], set()
    for link in fields.get("issuelinks") or []:
        if not isinstance(link, dict):
            continue
        kind = link.get("type") if isinstance(link.get("type"), dict) else {}
        words = {str(kind.get(k) or "").strip().lower() for k in ("name", "inward", "outward")}
        if link_type.lower() not in words and not ({"is tested by", "tests"} & words):
            continue
        inward = isinstance(link.get("inwardIssue"), dict)
        other = link.get("inwardIssue") if inward else link.get("outwardIssue")
        if not isinstance(other, dict):
            continue
        key = str(other.get("key") or "").upper()
        if not _KEY_RX.match(key) or key in seen:
            continue
        f = other.get("fields") if isinstance(other.get("fields"), dict) else {}
        itype = f.get("issuetype") if isinstance(f.get("issuetype"), dict) else {}
        if itype.get("name") and str(itype["name"]).lower() != test_type.lower():
            continue
        if not inward:
            if other_way is not None:
                seen.add(key)
                phrase = _one(kind.get("outward"), 40) or "tests"
                other_way.append({"key": key, "why": f"it is linked the other way round (the story "
                                                     f"\"{phrase}\" it); link the test to the story "
                                                     f"in Jira to take it"})
            continue
        status = f.get("status") if isinstance(f.get("status"), dict) else {}
        seen.add(key)
        out.append({"key": key, "summary": _one(f.get("summary"), 300),
                    "status": _one(status.get("name"), 60)})
    return out


def design_md(design: dict) -> str:
    L = [f"# Cases from the Xray tests of {design['service']}", "",
         design["summary"], ""]
    for c in design["test_cases"]:
        L += [f"## {c['id']} [Xray {c['xray_key']}] {c['title']}", ""]
        if c.get("endpoint"):
            L += [f"Endpoint: `{c['endpoint']}`", ""]
        for n, s in enumerate(c["steps"], 1):
            L.append(f"{n}. {s['action']}" + (f" -- data: {s['data']}" if s["data"] else ""))
            L.append(f"   - expect: {s['expected'] or '(nothing stated)'}")
        L.append("")
    if design["not_used"]:
        L += ["## Not used", ""] + [f"- {n['key']}: {n['why']}" for n in design["not_used"]] + [""]
    if design.get("warnings"):
        L += ["## Warnings", ""] + [f"- {w}" for w in design["warnings"]] + [""]
    return "\n".join(L).rstrip() + "\n"


def run(job: str, root: str = "", story: str = "", conn=None, transport=None, cfg: dict = None) -> dict:
    root = root or ROOT
    job_dir = loop.intake.job_dir(job, root)
    if os.path.isfile(os.path.join(job_dir, "design.json")):
        raise Refused("this job already has a design: Clear the design first, then take the story's "
                      "Xray tests (the review decisions and Xray keys stay)")
    story = (story or story_of(job_dir)).strip().upper()
    if not _KEY_RX.match(story):
        raise Refused("no story for this job: Read it with the story's link or key first (or give --story)")
    conn = conn or search.connect()
    mocked = bool(conn.get("mock"))
    if mocked and transport is None:
        transport = lambda m, u, t, s, b=None: publish._mock().jira_transport(m, u, t, s, b, root)
    try:
        opts = xt.switch(cfg, mocked)
    except (xt.Refused, search.Refused):
        opts = {"link_type": xt.DEFAULT_LINK, "issue_type": xt.DEFAULT_TYPE}
    diag.step(f"Asking Jira for the tests linked to {story} ({opts['link_type']})")
    issue = search._call(conn, "GET", f"/issue/{story}?fields=summary,issuelinks", None, transport)
    other_way = []
    tests = linked_tests(issue if isinstance(issue, dict) else {}, opts["link_type"], opts["issue_type"],
                         other_way)
    beyond = tests[MAX_TESTS:]
    tests = tests[:MAX_TESTS]
    if not tests and other_way:
        raise Refused(f"every test linked to {story} is linked the other way round: "
                      + ", ".join(o["key"] for o in other_way[:8])
                      + f". In Jira, link each test to the story (the story \"is tested by\" it).")
    if not tests:
        raise Refused(f"{story} has no issue linked as a test (link type {opts['link_type']!r}). "
                      f"If your Jira links tests another way, set jira_config.publish.test_link_type "
                      f"to that link type's name.")
    private = loop.config_secrets(os.path.join(root, "src", "main", "resources", "program_configuration.json"))
    hide = lambda s: _redact(s, private)
    cases, not_used = [], list(other_way)
    # Over the cap: named, one line each, so nobody thinks the story has
    # only the tests that were taken.
    not_used += [{"key": b["key"], "why": f"over the first {MAX_TESTS} linked tests; not read"} for b in beyond]
    known = spec_keys(job_dir)
    off_spec = []
    for t in tests:
        diag.step(f"Reading the steps of {t['key']}")
        try:
            steps = mt.steps_of(conn, t["key"], transport)
        except search.JiraError as e:
            not_used.append({"key": t["key"], "why": f"its steps could not be read ({e.status})"})
            continue
        except Exception as e:                           # noqa: BLE001
            diag.trace()
            not_used.append({"key": t["key"], "why": f"its steps could not be read ({type(e).__name__})"})
            continue
        if not steps:
            not_used.append({"key": t["key"], "why": "it has no steps in Xray"})
            continue
        endpoint = endpoint_in_steps(steps)
        in_spec = None
        if endpoint and known:
            in_spec = loop._design_module().endpoint_key(endpoint) in known
            if not in_spec:
                off_spec.append(f"{t['key']}: {endpoint}")
        cases.append({
            "id": f"TC-{len(cases) + 1:03d}", "title": hide(t["summary"]) or t["key"],
            "endpoint": hide(endpoint), "type": "xray", "priority": "", "preconditions": "",
            "steps": [{"action": hide(_text(s.get("action"), MAX_TEXT)),
                       "data": hide(_text(s.get("data"), MAX_TEXT)),
                       "expected": hide(_text(s.get("expected"), MAX_TEXT))} for s in steps[:MAX_STEPS]],
            "requirement_refs": [story], "xray_key": t["key"], "xray_status": t["status"]})
        if in_spec:
            # Only ever True: a test from Xray is automated even when its
            # endpoint is not in the specification -- that is said, below.
            cases[-1]["endpoint_in_spec"] = True
    if not cases:
        raise Refused(f"none of the {len(tests)} test(s) linked to {story} has steps to automate: "
                      + "; ".join(f"{n['key']} ({n['why']})" for n in not_used[:8]))
    number_cases(job_dir, cases)
    design = {"kind": "design", "source": "xray", "mock": mocked, "at": _now(), "job": job,
              "service": story, "brief": loop.brief_fingerprint(job_dir),
              "summary": (f"{len(cases)} case(s) taken from the Xray tests linked to {story}, as "
                          f"written in Xray" + (f"; {len(not_used)} not used" if not_used else "") + "."),
              "test_cases": cases, "open_questions": [],
              "warnings": ([f"{len(off_spec)} test(s) name an endpoint the specification does not have: "
                            + "; ".join(off_spec[:8])] if off_spec else []),
              "not_used": not_used}
    os.makedirs(job_dir, exist_ok=True)
    with io.open(os.path.join(job_dir, "design.json"), "w", encoding="utf-8") as fh:
        json.dump(design, fh, indent=1, ensure_ascii=False)
    with io.open(os.path.join(job_dir, "design.md"), "w", encoding="utf-8") as fh:
        fh.write(design_md(design))
    # The keys, as Create in Xray would have kept them: the Agentic step hands
    # them to Cursor, and Create in Xray finds each test already there.
    kept = xt.read_keys(job_dir)
    shaped = xt.cases_of(design, "design")
    for c, s in zip(cases, shaped):
        kept["tests"][f"design:{c['id']}"] = {"key": c["xray_key"], "title": s["title"],
                                              "fingerprint": xt.fingerprint(s), "story": story,
                                              "mock": mocked, "at": _now(), "imported": True}
    xt.write_keys(job_dir, kept)
    return {"kind": "xray-import", "ok": True, "story": story, "link_type": opts["link_type"],
            "count": len(cases), "keys": [c["xray_key"] for c in cases], "not_used": not_used,
            "mock_jira": mocked,
            "message": design["summary"] + " Review them, then Automate them."}


def number_cases(job_dir: str, cases: list) -> None:
    """Give each imported case its id. A test this job already keyed to a
    case (Create in Xray, or an earlier import) gets THAT case's id back,
    and with it the review decision about that same test. Every other
    case takes an id no decision or Xray key is held under: TC-001 of a
    cleared design was approved and edited as a different case, and an
    import that reused the number inherited both."""
    reserved = loop._design_module().reserved_ids(job_dir)
    by_key = {}
    for name, entry in (xt.read_keys(job_dir).get("tests") or {}).items():
        if str(name).startswith("design:") and isinstance(entry, dict) and entry.get("key"):
            by_key.setdefault(str(entry["key"]), str(name).split(":", 1)[1])
    used, n = set(), 0
    for c in cases:
        old = by_key.get(c["xray_key"])
        if old and old not in used:
            c["id"] = old
            used.add(old)
    for c in cases:
        if c["id"] in used and by_key.get(c["xray_key"]) == c["id"]:
            continue
        while True:
            n += 1
            candidate = f"TC-{n:03d}"
            if candidate not in reserved and candidate not in used:
                break
        c["id"] = candidate
        used.add(candidate)


def _redact(text: str, private: dict) -> str:
    for value in sorted((v for v in private if isinstance(v, str) and len(v) >= 4), key=len, reverse=True):
        text = text.replace(value, "<redacted>")
    return loop.intake.conf_fetch.redact(text)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--job", required=True)
    ap.add_argument("--story", default="", help="the story key; the job's story when left out")
    a = ap.parse_args(argv)
    try:
        job_dir = loop.intake.job_dir(a.job)
    except ValueError as e:
        print(f"REFUSED {e}")
        return 2
    try:
        r = run(a.job, story=a.story)
    except (Refused, search.Refused, search.JiraError) as e:
        print(f"REFUSED {e}")
        try:
            os.makedirs(job_dir, exist_ok=True)
            with io.open(os.path.join(job_dir, RESULT), "w", encoding="utf-8") as fh:
                json.dump({"kind": "error", "ok": False, "message": str(e), "at": _now()}, fh)
        except OSError:
            pass
        return 1
    with io.open(os.path.join(job_dir, RESULT), "w", encoding="utf-8") as fh:
        json.dump(r, fh, indent=1, ensure_ascii=False)
    print(f" ok   {r['message']}")
    for n in r["not_used"]:
        print(f"  not used: {n['key']} -- {n['why']}")
    return 0


if __name__ == "__main__":
    sys.exit(diag.run("xrayimport", main))
