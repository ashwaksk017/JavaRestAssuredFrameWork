"""Ask Cursor to fix what the last run of the Run tests tab left failing.

The Run tests tab shows which tests failed; until now the person then had
to go to another tab, find the right box and retype the failures there.
This module decides which of the two existing ways applies to the run,
and writes the request the person reads and edits before it is sent:

* **The job's own tests** -- the run was the job's suite
  (`Suites/jira/<job>.xml`), or every failing class is in that suite or
  in the change waiting for the job's review. The request goes where the
  Automate tab's "Ask Me" sends a change: the job's
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


CLASSIC = "com.hi.api.tests.classic."            # --classic converts write here


def converted_suite(cls: str) -> str:
    """The converted suite of a test class (com.hi.api.tests.imported.<suite>.X,
    or tests.classic.<suite>.X after a classic convert), or ""."""
    cls = str(cls or "")
    for prefix in (IMPORTED, CLASSIC):
        if cls.startswith(prefix):
            suite = cls[len(prefix):].split(".", 1)[0]
            return suite if _SUITE_RX.match(suite) else ""
    return ""


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


def _candidates(root: str, run_job: str, result: dict) -> list:
    """The jobs a run's failures may be: the job whose suite was run, then
    the job the run was started from -- each only when it exists."""
    out = []
    named = suite_job(result.get("suite"))
    if named and os.path.isdir(intake.job_dir(named, root)):
        out.append(named)
    if run_job and run_job not in out and os.path.isdir(intake.job_dir(run_job, root)):
        out.append(run_job)
    return out


# ---- a compile that failed -------------------------------------------------------------
# Compile, a run's compile first, or a run that did not compile: no test
# ran, so there is no failed test -- but the errors name their files, and
# the files say whose they are. Generated and converted files are not
# Cursor's to edit: the next convert writes them again.

SUPPORT = "src/main/java/com/hi/api/support/"
_ERR_RX = re.compile(r"^((?:src|tools)/[^\s:]+\.java):(\d+): ")
_MORE_RX = re.compile(r"^\.\.\. and (\d+) more error")


def compile_errors(result: dict) -> list:
    """The `file:line: message` lines of the run's failed compile, or []."""
    result = result or {}
    c = result.get("compile") if isinstance(result.get("compile"), dict) else {}
    errors = c.get("errors") if c and not c.get("ok") else None
    if not errors and result.get("what") == "test" and result.get("tests") is None:
        errors = result.get("errors")
    return [str(e) for e in errors or [] if isinstance(e, str) and e.strip()]


def error_file(line: str) -> str:
    """The error's file, from the project root. Maven names it from the
    first `src/` in its absolute path, and this project may itself sit
    under a `src/` (.../src/main/b2b-api-ai/): what counts is the last
    `src/main/java/` or `src/test/java/`."""
    m = _ERR_RX.match(str(line or ""))
    if not m:
        return ""
    rel = m.group(1)
    at = max(rel.rfind("src/main/java/"), rel.rfind("src/test/java/"))
    return rel[at:] if at > 0 else rel


def error_count(errors: list) -> int:
    n = 0
    for e in errors:
        m = _MORE_RX.match(e)
        n += int(m.group(1)) if m else (1 if error_file(e) else 0)
    return n


def _dotted(rel: str) -> str:
    for base in ("src/test/java/", "src/main/java/"):
        if rel.startswith(base) and rel.endswith(".java"):
            return rel[len(base):-len(".java")].replace("/", ".")
    return ""


def job_files(root: str, job: str) -> dict:
    """{file: where} of the source files that are the job's: its suite's
    classes, and every Java file of the change waiting for its review."""
    out = {f"src/test/java/{c.replace('.', '/')}.java": w for c, w in job_classes(root, job).items()}
    review = _read_json(os.path.join(intake.job_dir(job, root), "review.json"))
    if review.get("state") in WAITING:
        files = review.get("files") or {}
        for rel in list(files.get("created") or []) + list(files.get("modified") or []):
            rel = str(rel).replace("\\", "/")
            if rel.endswith(".java"):
                out.setdefault(rel, "the change waiting for its review")
    return out


# A converted suite's own files: the places convertfix.py fixes, one suite
# at a time (its _SUITE_RX; test_runask checks the two agree).
CONVERTED_RX = (
    re.compile(r"^src/test/java/com/hi/api/tests/imported/([A-Za-z0-9_]+)/"),
    re.compile(r"^src/test/java/com/hi/api/tests/classic/([A-Za-z0-9_]+)/"),
    re.compile(r"^src/main/java/com/hi/api/support/([A-Za-z0-9_]+)/"),
    re.compile(r"^src/main/resources/templates/([A-Za-z0-9_]+)/"),
    re.compile(r"^src/main/java/com/hi/api/rest/clients/([A-Za-z0-9_]+)Client\.java$"),
)
SHARED_SUPPORT = ("scenario",)          # support/scenario/: wrappers every suite shares


def converted_owner(rel: str) -> str:
    """The converted suite a file belongs to, or "" for shared code."""
    rel = str(rel or "").replace("\\", "/")
    for rx in CONVERTED_RX:
        m = rx.match(rel)
        if m:
            s = m.group(1).lower()
            return "" if s in SHARED_SUPPORT else s
    return ""


def compile_fix_job(run_job: str, suite: str) -> str:
    """convertfix.fix_job: the job a suite's compile fix is made in."""
    name = f"{run_job}-fix-{suite}"
    return name if re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$", name) else f"{run_job[:40]}-fix-{suite[:16]}"


def fix_jobs_waiting(root: str, run_job: str, suites: list) -> list:
    """The suites' fix jobs that already hold a fix (convertfix skips them)."""
    out = []
    for s in suites:
        name = compile_fix_job(run_job, s)
        state = _read_json(os.path.join(intake.job_dir(name, root), "review.json")).get("state")
        if state in WAITING + ("generating",):
            out.append(name)
    return out


def verified_error_files(review: dict) -> set:
    """The files the change's own check (run_verify) found with errors."""
    out = set()
    for step in ((review.get("verify") or {}).get("steps") or []):
        if isinstance(step, dict):
            out |= {f for f in map(error_file, step.get("errors") or []) if f}
    return out


def classify_compile(root: str, run_job: str, result: dict, errors: list) -> dict:
    """classify() for a run whose compile failed: `failed` counts errors."""
    what = "Compile" if result.get("what") == "compile" else _what_ran(result)
    run = {"what": what, "env": str(result.get("env") or ""), "at": str(result.get("at") or ""),
           "project": str(result.get("project") or "")}
    base = {"failed": error_count(errors) or len(errors), "compile": True, "run": run, "run_job": run_job}
    files = sorted({f for f in map(error_file, errors) if f})
    if not files:
        return dict(base, kind="none", why="the compile errors name no source file: the log above says what "
                                           "stopped it")
    generated = [f for f in files if f.startswith(SUPPORT)]
    suites = sorted({s for s in (converted_suite(_dotted(f)) for f in files) if s})
    candidates = _candidates(root, run_job, result)
    if generated or suites:
        where = ", ".join(([f"suite {', '.join(suites)}"] if suites else [])
                          + ([f"{len(generated)} generated support file(s)"] if generated else []))
        theirs = set(generated) | {f for f in files if converted_suite(_dotted(f))}
        before = ""
        for job in candidates:
            # A change waiting for review can break a converted suite it
            # never wrote -- a shared helper it changed. Its own check
            # compiled the tree before and after: when it failed on these
            # files, or the job's own files fail too, the change is the fix.
            review = _read_json(os.path.join(intake.job_dir(job, root), "review.json"))
            if review.get("state") not in WAITING:
                continue
            own = job_files(root, job)
            mine = [f for f in files if f in own]
            broke = review.get("state") == "verify-failed" and theirs & verified_error_files(review)
            if mine or broke:
                return dict(base, kind="job", job=job, blocked=blocked(root, job), generated=sorted(theirs),
                            why=(f"the errors are in files the converter writes ({where})"
                                 + (f" and in job {job}'s own files" if mine else "")
                                 + (f"; job {job}'s change failed its check on them, so the change is "
                                    f"what broke them" if broke else "")
                                 + f". The request goes to job {job}'s Ask Me: Cursor changes what the job "
                                   f"changed so they compile again, and leaves the generated files alone."))
            before = (f" They failed before job {job}'s change too: its check compiled the tree before "
                      f"and after, so the change is not the cause. Until they compile, nothing on this "
                      f"machine compiles or runs -- job {job}'s new tests included.")
        owned = sorted({s for s in map(converted_owner, files) if s})
        if owned:
            # Not the job's doing: what a convert's own check does when its
            # suites do not compile (convertfix.py) -- each suite in a fix job
            # of its own, held for review only once that suite compiles.
            shared = sorted(f for f in files if not converted_owner(f))
            waiting = fix_jobs_waiting(root, run_job, owned)
            return dict(base, kind="convert-fix", suites=owned, shared=shared,
                        fix_jobs=[compile_fix_job(run_job, s) for s in owned],
                        blocked=(f"every suite already has a fix waiting ({', '.join(waiting)}): review or "
                                 f"discard it on the Commit and push tab first" if len(waiting) == len(owned)
                                 else ""),
                        why=(f"the errors are in the converted files of suite {', '.join(owned)}, and no change "
                             f"waiting for review caused them.{before} As after a convert, Cursor makes each "
                             f"suite compile in a fix job of its own ({compile_fix_job(run_job, owned[0])}"
                             + (", ..." if len(owned) > 1 else "") + "), changing only that suite's converted "
                             f"files, and the fix waits for review only once the suite compiles. A suite's "
                             f"fix is lost on its next convert: when the converter is the cause, fix it on the "
                             f"Migration tab (Ask Me, scope converter)"
                             + (f". {len(shared)} other file(s) with errors are not a suite's "
                                f"({', '.join(f.rsplit('/', 1)[-1] for f in shared[:4])}): a person fixes "
                                f"those" if shared else "")))
        return dict(base, kind="none", why=(
            f"the errors are in files the converter writes ({where}); an edit there is lost on the next "
            f"convert.{before} Convert the suite again on the Migration from ReadyAPI tab -- a generated "
            f"wrapper left from an older convert is removed on the next Compile by itself. If the errors "
            f"stay, the converter needs the fix: the Migration tab's Ask Me, scope converter"))
    for job in candidates:
        own = job_files(root, job)
        if all(f in own for f in files):
            where = sorted({own[f] for f in files})
            return dict(base, kind="job", job=job, blocked=blocked(root, job), why=(
                f"the errors are in job {job}'s own files ({'; '.join(where)}). As Ask Me on the Automate "
                f"Test Case tab does, the request goes to that job: Cursor changes the change waiting for "
                f"its review, or starts one when none is waiting; then it is checked and waits for your "
                f"review on the Commit and push tab."))
    who = (f"job {candidates[0]}'s" if candidates else "the open job's")
    return dict(base, kind="none", why=(
        f"the errors are in files that are not {who} and not a converted suite's "
        f"({', '.join(f.rsplit('/', 1)[-1] for f in files[:5])}" + (", ..." if len(files) > 5 else "")
        + "): open the job that wrote them and compile there -- or ask on the Automate Test Case tab in "
          "your own words"))


def prefill_compile(info: dict, errors: list) -> str:
    run = info["run"]
    head = (f"the last Compile ({run['at']})" if run["what"] == "Compile"
            else f"the last run of {run['what']}" + (f" on {run['env']}" if run["env"] else "") + f" ({run['at']})")
    L = [f"Fix the compile errors in job {info['job']}'s files: {head} did not compile -- "
         f"{info['failed']} error(s).", "", "Errors (file:line: message):"]
    L += [f"- {e[:400]}" for e in errors[:MAX_LISTED * 2]]
    L += ["", f"Make the project compile. Change only job {info['job']}'s files; where an error comes from "
              f"a class the job did not write, say so instead of changing that class."]
    if info.get("generated"):
        L += [f"Some errors are in files the converter writes ({', '.join(info['generated'][:5])}"
              + (", ..." if len(info["generated"]) > 5 else "") + "). Do not edit those: change what this "
              f"job changed (a helper, a shared class) so they compile again as they did before it."]
    return "\n".join(L)


def classify(root: str, run_job: str, result: dict) -> dict:
    """{kind, why, ...}: `job` (the job's own tests: `job` names it),
    `converted` (one converted suite: `suite`), or `none` (why says what
    to do instead). `failed` is how many tests failed -- or, with
    `compile`, how many compile errors; 0 means there is nothing to ask
    about."""
    result = result or {}
    errors = compile_errors(result)
    if errors:
        return classify_compile(root, run_job, result, errors)
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
    candidates = _candidates(root, run_job, result)
    for job in candidates:
        own = job_classes(root, job)
        if all(c in own for c in classes):
            where = sorted({own[c] for c in classes})
            return dict(base, kind="job", job=job, blocked=blocked(root, job), why=(
                f"the failing tests are job {job}'s own ({'; '.join(where)}). As Ask Me on the Automate "
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
    if info.get("compile") and info["kind"] == "job":
        info["text"] = redact(prefill_compile(info, compile_errors(result)), private(root))
    elif info["kind"] in ("job", "converted"):
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
