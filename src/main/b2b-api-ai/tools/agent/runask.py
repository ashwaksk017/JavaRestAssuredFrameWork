"""Ask Cursor to fix what the last run of the Run tests tab left failing.

The Run tests tab shows which tests failed; until now the person then had
to go to another tab, find the right box and retype the failures there.
This module decides which of the two existing ways applies to the run,
and writes the request the person reads and edits before it is sent:

* **The job's own tests** -- the run was the job's suite
  (`Suites/jira/<job>.xml`), or every failing class is in that suite or
  in the change waiting for the job's review. The request goes where the
  Automate tab's "Ask Cursor" sends a change: the job's
  `agent-followup.txt`, run by `loop.py followup` (cmd_ask), which works
  on the waiting change or starts one.
* **A converted suite's tests** -- every failing class is under
  `com.hi.api.tests.imported.<suite>.`. Those are generated files, held to
  one suite; the Failures tab's "Fix with Cursor" already writes the brief
  for them (fixbrief.py: surefire's message, the last request and
  response, the converter's notes). The same brief is written, from THIS
  run's failures rather than the latest recorded run, in a new job of its
  own, and the Migration tab's run (`agent-generate`, scope `converted`)
  works on it.

Anything else (another job's tests, failures across suites, a mix) is
said, not guessed: a request sent to the wrong job's change, or a brief
about the wrong suite, sends Cursor to the wrong files.

Everything the request quotes from a run -- messages, failure lines --
passes through the same redaction as every other prompt (design.redact
over loop.private_config, then design.hide_hosts) before it is shown,
and again before it is sent: the person may have pasted something in.
"""
from __future__ import annotations

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


design = _load("runask_design", os.path.join(HERE, "design.py"))
loop = design.loop
intake = loop.intake

RESULT = "mvnrun-result.json"          # what mvnrun.py cmd_test writes into the run's job
RUN_LOG = "mvn-test.log"               # the run's command log (jobs.log_path)
IMPORTED = "com.hi.api.tests.imported."
JOB_SUITES = "Suites/jira/"
DIGEST = os.path.join("target", "failure-digest.txt")
DIGEST_SECTION = "== every failing (test, row), one line each =="
WAITING = ("pending-review", "verify-failed")
MAX_LISTED = 20                        # failed tests named in a job's request
MAX_CONVERTED = 10                     # fixbrief.MAX_CASES: what one fix job takes
MAX_LINES_PER_TEST = 3
MAX_MESSAGE = 400
_CASE_RX = re.compile(r"\b(jira_xray_id|xray_key|xray_id|case_id|test_case_id|tc_id)=([A-Za-z0-9_.-]{1,40})")
_SUITE_RX = re.compile(r"^[a-z0-9_]{1,80}$")
_CLASS_RX = re.compile(r'<class\s+name="([A-Za-z0-9_.$]+)"')


def _read_json(path: str) -> dict:
    try:
        with io.open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _text(path: str, cap: int = 2_000_000) -> str:
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read(cap)
    except OSError:
        return ""


# ---- what failed, and whose it is ------------------------------------------------------

def read_result(job_dir: str) -> dict:
    return _read_json(os.path.join(job_dir, RESULT))


def failed_tests(result: dict) -> list:
    """The run's FAILED tests (a failed setup method included: the tests
    behind it did not run as written). Skipped ones are not asked about:
    most often a configuration key is missing, which Cursor cannot set."""
    return [t for t in (result or {}).get("tests") or []
            if isinstance(t, dict) and str(t.get("status") or "").upper() == "FAIL"]


def case_id(params: str) -> str:
    """The case id a data row carries ({jira_xray_id=ABC-1, ...}), or ""."""
    m = _CASE_RX.search(str(params or ""))
    return m.group(2) if m else ""


def converted_suite(cls: str) -> str:
    """The converted suite of a test class (com.hi.api.tests.imported.<suite>.X), or ""."""
    cls = str(cls or "")
    if not cls.startswith(IMPORTED):
        return ""
    suite = cls[len(IMPORTED):].split(".", 1)[0]
    return suite if _SUITE_RX.match(suite) else ""


def suite_job(suite_rel: str) -> str:
    """The job a story suite file is for (Suites/jira/<job>.xml), or "" --
    a group's suite (group-<name>.xml) is no one job's."""
    rel = str(suite_rel or "").replace("\\", "/")
    if not rel.startswith(JOB_SUITES) or not rel.endswith(".xml") or "/" in rel[len(JOB_SUITES):]:
        return ""
    name = rel[len(JOB_SUITES):-4]
    if name.startswith("group-"):
        return ""
    try:
        return intake.safe_job(name)
    except ValueError:
        return ""


def job_classes(root: str, job: str) -> dict:
    """{class: where} of the test classes that are the job's: those its
    suite file names, and those of the change waiting for its review."""
    out = {}
    for c in _CLASS_RX.findall(_text(os.path.join(root, *(JOB_SUITES + job + ".xml").split("/")))):
        out[c] = f"its suite {JOB_SUITES}{job}.xml"
    review = _read_json(os.path.join(intake.job_dir(job, root), "review.json"))
    if review.get("state") in WAITING:
        files = review.get("files") or {}
        for rel in list(files.get("created") or []) + list(files.get("modified") or []):
            rel = str(rel).replace("\\", "/")
            if rel.startswith("src/test/java/") and rel.endswith(".java"):
                out.setdefault(rel[len("src/test/java/"):-len(".java")].replace("/", "."),
                               "the change waiting for its review")
    return out


def blocked(root: str, job: str) -> str:
    """Why the job's change cannot take a request now, or "" -- said here,
    before the person writes anything, in cmd_ask's own words."""
    review = _read_json(os.path.join(intake.job_dir(job, root), "review.json"))
    state = review.get("state")
    if state == "generating":
        return f"a Cursor run of job {job} has not finished: wait for it, or Discard it on the Commit and push tab"
    if state in WAITING and review.get("mock"):
        return (f"the change waiting for job {job} was made by the stand-in (mock Cursor): Discard it on the "
                f"Commit and push tab first")
    if state in ("pushed", "approved-local"):
        return (f"job {job}'s change was already approved" + (f" and pushed as {review.get('branch')}"
                if review.get("branch") else "") + ": ask in a new job, or once its pull request is merged")
    if state == "push-failed":
        return f"job {job}'s change was committed but the push failed: push it again on the Commit and push tab first"
    return ""


def _what_ran(result: dict) -> str:
    return (result.get("suite") or (f"-Dtest={result['test']}" if result.get("test") else "")
            or (f"the group {result['group']}" if result.get("group") else "the tests"))


def classify(root: str, run_job: str, result: dict) -> dict:
    """{kind, why, ...}: `job` (the job's own tests: `job` names it),
    `converted` (one converted suite: `suite`), or `none` (why says what
    to do instead). `failed` is how many tests failed; 0 means there is
    nothing to ask about."""
    result = result or {}
    if result.get("what") != "test":
        return {"kind": "none", "failed": 0, "why": "no tests have been run from this tab for this job yet: "
                                                    "run them, then ask Cursor to fix what failed"}
    failed = failed_tests(result)
    if not failed:
        why = ("the last run had no failed test" if result.get("tests") is not None else
               f"the last run left no test results ({result.get('verdict') or 'no verdict'}): there is no "
               f"failed test to fix -- the log above says what stopped it")
        return {"kind": "none", "failed": 0, "why": why}
    run = {"what": _what_ran(result), "env": str(result.get("env") or ""), "at": str(result.get("at") or ""),
           "project": str(result.get("project") or "")}
    base = {"failed": len(failed), "run": run, "run_job": run_job}
    classes = sorted({str(t.get("class") or "") for t in failed})
    suites = {converted_suite(c) for c in classes}
    if all(suites):
        if len(suites) == 1:
            suite = next(iter(suites))
            return dict(base, kind="converted", suite=suite, why=(
                f"the failing tests are converted ReadyAPI tests of suite {suite} ({IMPORTED}{suite}). "
                f"As Fix with Cursor on the Failures tab does, a fix brief is written from this run -- for "
                f"each case surefire's message, the last request and response, and what the converter "
                f"noted -- in a new job of its own, and Cursor may change only that suite's converted files"
                + (f". One job takes at most {MAX_CONVERTED} cases; the others are named in the brief"
                   if len(failed) > MAX_CONVERTED else "") + "."))
        return dict(base, kind="none", why=(
            f"the failing tests are in {len(suites)} converted suites ({', '.join(sorted(suites))}); a fix "
            f"is one suite's -- run one suite's file, then ask"))
    if any(suites):
        return dict(base, kind="none", why=(
            "the failing tests are converted ReadyAPI tests and hand-written ones together: run them "
            "apart (one suite file at a time), then ask"))
    candidates = []
    named = suite_job(result.get("suite"))
    if named and os.path.isdir(intake.job_dir(named, root)):
        candidates.append(named)
    if run_job and run_job not in candidates and os.path.isdir(intake.job_dir(run_job, root)):
        candidates.append(run_job)
    for job in candidates:
        own = job_classes(root, job)
        if all(c in own for c in classes):
            where = sorted({own[c] for c in classes})
            return dict(base, kind="job", job=job, blocked=blocked(root, job), why=(
                f"the failing tests are job {job}'s own ({'; '.join(where)}). As Ask Cursor on the Automate "
                f"Test Case tab does, the request goes to that job: Cursor changes the change waiting for "
                f"its review, or starts one when none is waiting; then it is checked and waits for your "
                f"review on the Commit and push tab."))
    who = (f"job {candidates[0]}'s" if candidates else "the open job's")
    return dict(base, kind="none", why=(
        f"the failing tests ({', '.join(c.rsplit('.', 1)[-1] for c in classes[:5])}"
        + (", ..." if len(classes) > 5 else "") + f") are not {who} tests and not a converted suite's: "
        f"open the job that wrote them (its suite is Suites/jira/<job>.xml), run its tests and ask "
        f"there -- or ask on the Automate Test Case tab in your own words"))


# ---- what the run said about each failure ----------------------------------------------

def _since(path: str, at: str) -> bool:
    """Was the file written by this run (not before it started)?"""
    try:
        started = datetime.strptime(at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
        return os.path.getmtime(path) >= started - 5
    except (OSError, ValueError):
        return False


def failure_lines(root: str, run_dir: str, tests: list, at: str) -> dict:
    """{"Class.method": [lines]}: the failure digest's lines for each test,
    when this run wrote the digest, then the run log's [ERROR] lines
    that name it (surefire's failure block)."""
    keys = {f"{str(t.get('class') or '').rsplit('.', 1)[-1]}.{t.get('method') or ''}" for t in tests}
    out = {k: [] for k in keys}
    digest = os.path.join(root, DIGEST)
    sources = []
    if os.path.isfile(digest) and _since(digest, at):
        text = _text(digest)
        if DIGEST_SECTION in text:
            sources.append([l for l in text.split(DIGEST_SECTION, 1)[1].split("\n") if l.strip()])
    sources.append([l for l in _text(os.path.join(run_dir, RUN_LOG)).split("\n") if "[ERROR]" in l])
    for lines in sources:
        for line in lines:
            line = " ".join(line.replace("\t", "  ").split())
            for k in keys:
                if len(out[k]) < MAX_LINES_PER_TEST and (k + " " in line + " " or k + ":" in line
                                                         or k + "(" in line) and line not in out[k]:
                    out[k].append(line[:300])
    return out


# ---- the request ----------------------------------------------------------------------

def private(root: str) -> dict:
    """The values that must not reach Cursor. A configuration file that
    cannot be read is a refusal: "nothing private" from a broken file is a
    guess (loop.private_config, strict)."""
    try:
        return loop.private_config(root, strict=True)
    except ValueError as e:
        raise ValueError(f"the configuration file {e} cannot be read, so its private values could not "
                         f"be hidden: nothing is sent to Cursor until it can be read") from None


def redact(text: str, values: dict) -> str:
    text, _n = design.redact(str(text or ""), values)
    return design.hide_hosts(text)[0]


def _name(t: dict) -> str:
    cid = case_id(t.get("params"))
    return (f"{str(t.get('class') or '').rsplit('.', 1)[-1]}.{t.get('method') or ''}"
            + (" (setup)" if t.get("config") else "") + (f" [case {cid}]" if cid else ""))


def prefill(info: dict, result: dict, lines: dict) -> str:
    """The request the person reads and edits: the failed tests, their
    case ids, why each failed, the run's own lines about them."""
    failed = failed_tests(result)
    run = info["run"]
    shown = failed[:MAX_CONVERTED if info["kind"] == "converted" else MAX_LISTED]
    L = []
    if info["kind"] == "converted":
        L.append(f"Make the {len(failed)} failing case(s) of the converted suite {info['suite']} pass "
                 f"(the last run of {run['what']} on {run['env']}, {run['at']}).")
    else:
        L.append(f"Fix the tests of job {info['job']} that failed in the last run of {run['what']} on "
                 f"{run['env']} ({run['at']}): {len(failed)} failed.")
    L += ["", "Failed:"]
    for t in shown:
        why = " ".join(str(t.get("message") or "no message").split())[:MAX_MESSAGE]
        L.append(f"- {_name(t)}: {why}")
        key = f"{str(t.get('class') or '').rsplit('.', 1)[-1]}.{t.get('method') or ''}"
        for line in lines.get(key, []):
            if line not in why:
                L.append(f"    {line}")
    if len(failed) > len(shown):
        L.append(f"- ... and {len(failed) - len(shown)} more"
                 + (" (one fix job takes at most 10; the brief names the others)" if info["kind"] == "converted" else ""))
    L.append("")
    if info["kind"] == "converted":
        L.append("The brief adds, for each case, surefire's message and stack, the last request and "
                 "response, and what the converter noted about it.")
    L.append("Make these tests pass. Where a test or its data is wrong, correct it; where the API's answer "
             "is wrong, do not change the expected value to match it -- say so instead.")
    return "\n".join(L)


def preview(root: str, run_job: str) -> dict:
    """What the Run tests tab shows under a failed run: which way applies
    and why, and the request, redacted, for the person to edit."""
    run_dir = intake.job_dir(run_job, root)
    result = read_result(run_dir)
    info = classify(root, run_job, result)
    if info["kind"] in ("job", "converted"):
        values = private(root)
        lines = failure_lines(root, run_dir, failed_tests(result), info["run"]["at"])
        info["text"] = redact(prefill(info, result, lines), values)
    return info


def fixbrief_failures(result: dict, suite: str) -> list:
    """The run's failures of one converted suite, as fixbrief reads a
    digest's: [{test, row, signature}] -- the class qualified, so the
    brief finds its file without guessing among suites."""
    out = []
    for t in failed_tests(result):
        if converted_suite(t.get("class")) != suite:
            continue
        out.append({"test": f"{t.get('class')}.{t.get('method')}",
                    "row": case_id(t.get("params")) or "-",
                    "signature": " ".join(str(t.get("message") or "FAIL").split())[:600]})
    return out


def fix_job_name(root: str, suite: str, now: datetime = None) -> str:
    """A new job for the fix: fix-<suite>-<time>, never one that exists."""
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    base = f"fix-{suite[:40]}-{stamp}"
    name, n = base, 1
    while os.path.exists(intake.job_dir(name, root)):
        n += 1
        name = f"{base}-{n}"
    return intake.safe_job(name)
