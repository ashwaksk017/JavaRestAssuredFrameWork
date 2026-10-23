"""Open the workbench in a real browser and check what is on the page.

    python tools/agent/check_ui.py

The unit tests check the commands, the allowlist and the result files.
None of them runs the page. This does: it starts the real server on a
spare port, puts real results in a throwaway job, loads each tab in a
headless browser (Edge or Chrome, whichever is installed) and looks at
the page the browser ends up with.

WHAT IS REAL AND WHAT IS NOT
* The server, the allowlist, the artifact route, the page and its
  script, the browser: real.
* The Jira, defect and failure-history commands: real code, run here in
  this process -- Jira against a stand-in (no network, no token), Cursor
  as a function that answers, failure history in a temporary directory.
  Their result files are then put in the job directory, exactly where
  the page reads them.
* One command goes through the server itself, to prove that path:
  `failures-list`, which only reads.

* Mock mode is checked the way it is used: a second server is started
  with the stand-ins switched ON, and the commands are run THROUGH it --
  the token check, versions, a release comparison, loading defects,
  suggesting reasons, writing one back, and a test design -- with the
  page loaded after each. That part is end to end with nothing replaced:
  it is exactly what pressing the buttons does in mock mode.

So this never touches a live Jira, and never writes this project's
`.failure-history/`. It does not CLICK: a button's request is checked
by sending the same request, and the page by loading it. A dead click
handler is the one thing this cannot see.

Exits 0 when every check passes, 1 when one fails, 2 when no browser
could be found (nothing was checked).
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
JOB = "check-ui-throwaway"
BROWSERS = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)


def renders(browser: str) -> bool:
    """Does this browser, headless, give a DOM back at all? Edge has been
    seen to exit 0 with nothing on stdout for every page (an update in
    flight, or a running instance taking the call); then every check
    would fail for a reason that is not the page's."""
    try:
        done = subprocess.run(
            [browser, "--headless=new", "--disable-gpu", "--no-first-run",
             "--disable-extensions", "--dump-dom", "data:text/html,<p id=probe>x</p>"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return b"probe" in done.stdout


def find_browser() -> str:
    """The first installed browser that renders; the names are tried
    after the known paths."""
    candidates = [p for p in BROWSERS if os.path.isfile(p)]
    for name in ("msedge", "chrome", "google-chrome", "chromium"):
        found = shutil.which(name)
        if found and found not in candidates:
            candidates.append(found)
    for path in candidates:
        if renders(path):
            return path
        print(f"skipping {os.path.basename(path)}: headless, it renders nothing")
    return ""


def _load_module(name: str, path: str):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Page:
    def __init__(self, browser: str, base: str):
        self.browser, self.base = browser, base

    def headers(self, path: str) -> dict:
        """The response headers of a GET, lower-cased."""
        try:
            with urllib.request.urlopen(self.base + path, timeout=20) as r:
                return {k.lower(): v for k, v in r.headers.items()}
        except urllib.error.HTTPError as e:
            return {k.lower(): v for k, v in e.headers.items()}

    def call(self, path: str, payload=None, raw: bool = False) -> tuple:
        req = urllib.request.Request(self.base + path)
        data = None
        if payload is not None:
            data = json.dumps(payload).encode()
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, data, timeout=20) as r:
                text = r.read().decode("utf-8", "replace")
                return r.status, text if raw else json.loads(text)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8", "replace") or "{}")

    def dom(self, tab: str, job: str = JOB) -> str:
        # Its own profile: with the person's Edge open, the shared one is
        # locked and a headless run waits on it until the timeout.
        with tempfile.TemporaryDirectory(prefix="check-ui-edge-",
                                         ignore_cleanup_errors=True) as profile:
            done = subprocess.run(
                [self.browser, "--headless=new", "--disable-gpu", "--no-first-run",
                 "--disable-extensions", f"--user-data-dir={profile}",
                 "--virtual-time-budget=9000", "--dump-dom",
                 f"{self.base}/?job={job}#{tab}"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=120)
        return done.stdout.decode("utf-8", "replace")


def main() -> int:
    browser = find_browser()
    if not browser:
        print("no Edge or Chrome was found: nothing was checked")
        return 2
    sys.path.insert(0, os.path.join(ROOT, "tools", "jira"))
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import failure_history
    import search

    job_dir = os.path.join(ROOT, ".workbench", JOB)
    scratch = tempfile.mkdtemp(prefix="check_ui_")
    port = free_port()
    page = Page(browser, f"http://127.0.0.1:{port}")
    results = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append(bool(ok))
        print(("PASS " if ok else "FAIL ") + name + (f"  -- {detail[:300]}" if detail and not ok else ""))

    def quiet(fn, *args, **kw):
        old, sys.stdout = sys.stdout, io.StringIO()
        try:
            return fn(*args, **kw)
        finally:
            sys.stdout = old

    # The page is checked with the stand-ins OFF: what is shown is what the
    # real paths produce. Mock mode has its own section further down.
    os.environ["WORKBENCH_MOCK"] = "0"
    srv = subprocess.Popen([sys.executable, "-B", os.path.join("tools", "agent", "server.py"),
                            "--port", str(port)], cwd=ROOT,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    saved = (search.connect, search.ROOT)
    try:
        for _ in range(80):
            try:
                page.call("/api/jobs")
                break
            except Exception:                            # noqa: BLE001
                time.sleep(0.25)
        os.makedirs(job_dir, exist_ok=True)

        declared = page.call("/api/runnables")[1]
        wanted = ("jira-verify", "jira-versions", "jira-release", "jira-tests", "jira-paste",
                  "failures-record", "failures-list", "failures-show", "failures-note",
                  "defects-load", "defects-suggest", "defects-apply")
        check("every command behind the Jira, Defects and Failures tabs is declared",
              all(r in declared for r in wanted), str([r for r in wanted if r not in declared]))

        # ---- what the page may not ask for ---------------------------------
        for name, options, label in (
                ("failures-record", {"--digest": "src/main/resources/program_configuration.json"},
                 "the failures tab cannot name a file"),
                ("jira-paste", {"--base": "https://evil.example"}, "the Jira tab cannot name a host"),
                ("jira-verify", {"--token": "x"}, "the Jira tab cannot pass a token"),
                ("defects-load", {"--base": "https://evil.example"}, "the Defects tab cannot name a host"),
                ("defects-apply", {"--field": "summary"}, "the Defects tab cannot choose the field")):
            code, body = page.call("/api/start", {"job": JOB, "runnable": name, "options": options})
            check(label, code == 400 and "does not accept" in body.get("error", ""), str(body))
        code, body = page.call(f"/api/artifact?job={JOB}&name=../../.failure-history/notes.json")
        check("only named result files can be read back", code == 400, str(body))

        # ---- one command through the server, end to end ---------------------
        code, body = page.call("/api/start", {"job": JOB, "runnable": "failures-list", "options": {}})
        state = {}
        for _ in range(120):
            state = page.call(f"/api/status?job={JOB}&runnable=failures-list")[1]
            if state.get("state") != "running":
                break
            time.sleep(0.25)
        text = page.call(f"/api/artifact?job={JOB}&name=failures-result.json")[1].get("text") or ""
        for path in ("/", "/app.js", "/app.css", "/api/mock", "/api/status?job=none"):
            check(f"nothing is cached by the browser: {path}",
                  page.headers(path).get("cache-control") == "no-store")
        check("server -> command -> result file", code == 200 and state.get("exit_code") == 0
              and json.loads(text or "{}").get("kind") == "runs", str(state))
        html = page.dom("failures")
        check("the Failures tab shows that result", "Recorded runs" in html)

        # ---- what a command says it is doing ---------------------------------
        # A command's steps are marked lines of its output (tools/diag.py).
        # The page lifts them out: the latest beside the button, all of them
        # above the log -- and as text, whatever a story's title holds.
        def running(state, lines, code=0):
            with io.open(os.path.join(job_dir, "intake.log"), "w", encoding="utf-8",
                         newline="\n") as fh:
                fh.write("".join(line + "\n" for line in lines))
            with io.open(os.path.join(job_dir, "intake.status.json"), "w", encoding="utf-8") as fh:
                json.dump({"state": state, "exit_code": code, "runnable": "intake"}, fh)
            return subprocess.run(
                [browser, "--headless=new", "--disable-gpu", "--no-first-run",
                 "--disable-extensions", "--virtual-time-budget=6000", "--dump-dom",
                 f"{page.base}/?job={JOB}&follow=intake#new"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=120
            ).stdout.decode("utf-8", "replace")
        said = ["an ordinary line", "==> Fetching a linked Confluence page",
                "==> Reading <img src=x onerror=alert(1)> the story", "==> Writing the brief",
                "2 request(s) read"]
        html = running("running", said)
        check("a running command shows its steps, the last one as the current one",
              '<li class="done">Fetching a linked Confluence page</li>' in html
              and '<li class="now">Writing the brief</li>' in html, html[-600:])
        check("the label beside the button says the current step and for how long",
              re.search(r'id="new-state"[^>]*class="state run"[^>]*>Writing the brief… \d+:\d\d<', html)
              is not None)
        check("a step is shown as text, whatever it holds",
              "<img src=x" not in html and "Reading &lt;img src=x onerror=alert(1)&gt; the story" in html)
        check("an ordinary line of output is not a step",
              ">an ordinary line</li>" not in html and ">2 request(s) read</li>" not in html)
        html = running("done", said)
        # Only the command's own step list: the flow strip at the top of the
        # tab uses the same done/now classes for the job's stages.
        steps_only = html[html.find('class="steps"'):]
        steps_only = steps_only[:steps_only.find("</ol>")]
        check("a finished command ticks every step and says how long it took",
              steps_only.count('<li class="done">') == 3 and 'class="now"' not in steps_only
              and re.search(r'class="state ok"[^>]*>finished in \d+:\d\d<', html) is not None,
              steps_only[:400])
        html = running("running", [
            "==> Asking Cursor to write the change (this can take several minutes)",
            "==> Running the check check_workbench.py"])
        steps_only = html[html.find('class="steps"'):]
        steps_only = steps_only[:steps_only.find("</ol>")]
        check("once Cursor has returned, the page says the code is written and the check is not a test yet",
              "Cursor has finished writing the code change" in steps_only
              and "Asking Cursor to write the change" not in steps_only
              and "not ready to test until this passes" in steps_only
              and 'class="now"' in steps_only, steps_only[:500])
        html = running("done", [
            "==> Asking Cursor to write the change (this can take several minutes)",
            "==> Running the check check_workbench.py",
            "PENDING REVIEW -- read proposed.diff."])
        check("a change held for review says it is ready to test",
              re.search(r'class="state ok"[^>]*>Ready to test\.', html) is not None, html[-400:])
        html = running("failed", said[:2], code=1)
        check("a command that failed says the step it stopped at",
              '<li class="stopped">Fetching a linked Confluence page</li>' in html
              and "stopped at: Fetching a linked Confluence page" in html)
        html = running("running", ["no step yet"])
        check("before a command has said a step, the label says what the command is for",
              "Read the pasted story and links… " in html)
        for name in ("intake.log", "intake.status.json"):
            os.remove(os.path.join(job_dir, name))
        check("nothing is in flight on a page at rest: the activity strip is hidden",
              re.search(r'id="activity"[^>]*hidden', page.dom("new")) is not None)

        # ---- the failures tab, with a real comparison ------------------------
        refused = ("FlowStopped | expected status for Create Booking expected [200] but found "
                   "[403] -- flow stopped here: POST Create Booking was rejected")
        section = failure_history.SECTION

        def digest(*rows):
            return "\n".join(["FAILURE DIGEST -- Demo",
                              f"unique failing (test, row) pairs: {len(rows)}", "", section]
                             + ["\t".join(r) for r in rows]) + "\n"
        os.makedirs(os.path.join(scratch, "target"))
        where = os.path.join(scratch, "target", "failure-digest.txt")

        def run_of(*rows, label="", written=0):
            with io.open(where, "w", encoding="utf-8") as fh:
                fh.write(digest(*rows))
            os.utime(where, (written, written))
            return quiet(failure_history.main, ["record", "--label", label, "--job", JOB],
                         root=scratch)
        run_of(("A.same", "-", "AssertionError | <*> expected [true] but found [false]"),
               ("B.changes", "row 3", refused), ("C.goes", "-", "AssertionError | gone soon"),
               label="before the fix", written=1_767_225_600)
        quiet(failure_history.main, ["note", "--id", failure_history.signature_id(refused),
                                     "--text", "the token was for <b>another</b> account",
                                     "--job", JOB], root=scratch)
        rc = run_of(("A.same", "-", "AssertionError | <*> expected [true] but found [false]"),
                    ("B.changes", "row 3", "AssertionError | status after polling: still PENDING"),
                    ("D.new", "-", "AssertionError | expected status for Create Booking expected "
                                   "[200] but found [403]"),
                    label="after the fix", written=1_767_312_000)
        shutil.copy(os.path.join(scratch, ".workbench", JOB, failure_history.JOB_RESULT),
                    os.path.join(job_dir, failure_history.JOB_RESULT))
        html = page.dom("failures")
        check("a recorded run exits 0", rc == 0)
        for needle in ("Suite Demo", "after the fix", "Compared with the run of",
                       "New — not failing before", "D.new",
                       "Failing differently", "B.changes [row 3]", "still PENDING",
                       "No longer failing — passed, or did not run", "C.goes",
                       "Signatures in this run", "RESEMBLES", "A pointer, not a diagnosis",
                       "Save note", "Every run it was in"):
            check(f"Failures tab shows: {needle}", needle in html)
        check("a signature offers Fix with Cursor, for the suite's Java and for the converter",
              "Fix with Cursor (this suite" in html and "Fix the converter with Cursor" in html)
        check("a note is shown as text, not run as markup",
              "the token was for &lt;b&gt;another&lt;/b&gt; account" in html
              and "<b>another</b>" not in html)

        # ---- the Jira tab, against a stand-in Jira ----------------------------
        def issue(n, status="Open"):
            return {"key": f"ABC-{n}", "fields": {
                "summary": f"story {n} <img src=x onerror=alert(1)>", "status": {"name": status},
                "issuetype": {"name": "Story"}, "fixVersions": [{"name": "6.02"}],
                "labels": ["api"]}}

        def jira(method, url, token, timeout, body=None):
            if url.endswith("/myself"):
                return {"displayName": "A. Tester"}
            if "/versions" in url:
                return [{"name": "Release 6.01", "released": True, "releaseDate": "2026-01-01"},
                        {"name": "Release 6.02"}, {"name": "Old 4.9", "archived": True}]
            jql = body["jql"]
            if '"6.01"' in jql:
                pool = [issue(2), issue(3, "Done")]
            elif '"6.02"' in jql:
                pool = [issue(1), issue(2)]
            else:
                pool = [issue(n) for n in range(30)]
            at, size = body["startAt"], body["maxResults"]
            return {"issues": pool[at:at + size], "total": len(pool)}

        search.connect = lambda: {"base": "https://jira.example.com",
                                  "bases": ["https://jira.example.com"],
                                  "token": "not-a-real-token-0001", "timeout": 5,
                                  "api": "/rest/api/2", "cleartext": False}
        search.ROOT = scratch

        def jira_cmd(*argv):
            rc = quiet(search.main, list(argv) + ["--job", JOB], transport=jira)
            src = os.path.join(scratch, ".workbench", JOB, search.JOB_RESULT)
            shutil.copy(src, os.path.join(job_dir, search.JOB_RESULT))
            with io.open(src, encoding="utf-8") as fh:
                return rc, fh.read(), page.dom("jira")

        rc, raw, html = jira_cmd("verify")
        check("Jira tab: the token check", rc == 0 and "Token accepted" in html and "A. Tester" in html)
        rc, raw, html = jira_cmd("versions", "--project", "abc")
        check("Jira tab: versions, newest first", "Versions of ABC" in html
              and html.index("Release 6.02") < html.index("Release 6.01") and "Old 4.9" not in html)
        rc, raw, html = jira_cmd("fix-version", "--project", "abc", "--version", "6.02",
                                 "--compare", "6.01")
        check("Jira tab: both sides of a comparison", rc == 0 and all(
            s in html for s in ("Only in 6.02", "Only in 6.01", "In both", "ABC-1", "ABC-3")))
        check("text from Jira is shown as text, not run as markup",
              "<img src=x" not in html and "&lt;img src=x onerror=alert(1)&gt;" in html)
        rc, raw, html = jira_cmd("tests", "--project", "abc", "--max", "10")
        check("Jira tab: a list cut at the limit says LIMIT above the list",
              rc == 1 and "LIMIT" in html and html.index("LIMIT") < html.index("<table"))
        rc, raw, html = jira_cmd("paste", "--text", "https://evil.example/browse/ABC-1")
        check("Jira tab: a paste naming another host is refused, and says which",
              rc == 2 and "Refused" in html and "evil.example" in html)
        check("the token is in no result file and nowhere on the page",
              "not-a-real-token-0001" not in raw and "not-a-real-token-0001" not in html)

        # ---- the Defects tab ------------------------------------------------------
        code, body = page.call("/api/start", {"job": JOB, "runnable": "defects-apply", "options": {
            "--key": "ABC-1", "--reason": "Code defect", "--confirm-key": "ABC-1"}})
        state = {}
        for _ in range(120):
            state = page.call(f"/api/status?job={JOB}&runnable=defects-apply")[1]
            if state.get("state") != "running":
                break
            time.sleep(0.25)
        check("a write to Jira asked for out of the blue is refused before any request",
              code == 200 and state.get("exit_code") == 2, str(state))

        defects = _load_module("check_ui_defects", os.path.join(HERE, "defects.py"))
        fid = "customfield_10500"
        reasons = ["Code defect", "Test data", "Environment"]

        def bug(n, summary, reason=None, description=""):
            return {"key": f"ABC-{n}", "fields": {
                "summary": summary, "description": description, "status": {"name": "Open"},
                "issuetype": {"name": "Bug"}, "priority": {"name": "High"},
                "components": [{"name": "Booking"}], "labels": ["api"],
                fid: ({"value": reason} if reason else None)}}
        bugs = {b["key"]: b for b in (
            bug(1, "Booking fails with 500 <img src=x onerror=alert(2)>", "Test data",
                "NullPointerException, POST /reservations/create returned 500"),
            bug(2, "Rate plan missing in shop response"))}
        earlier = [bug(50, "Booking fails with 500 on create reservation", "Code defect",
                       "NullPointerException, POST /reservations/create returned 500")]

        def defect_jira(method, url, token, timeout, body=None):
            path = url.split("/rest/api/2", 1)[1]
            if path == "/field":
                return [{"id": fid, "name": "Failure Reason"}]
            if path.endswith("/editmeta"):
                return {"fields": {fid: {"schema": {"type": "option"},
                                         "allowedValues": [{"value": r} for r in reasons]}}}
            if method == "POST":
                pool = earlier if "is not EMPTY" in body["jql"] else list(bugs.values())
                return {"issues": pool[body["startAt"]:body["startAt"] + body["maxResults"]],
                        "total": len(pool)}
            key = path.split("/")[2].split("?")[0]
            if method == "PUT":
                bugs[key]["fields"][fid] = body["fields"][fid]
                return None
            return {"key": key, "fields": {fid: bugs[key]["fields"][fid]}}

        def settings(write_back):
            return {"base_urls": ["https://jira.example.com"], "pat": "x",
                    "defects": {"field": "Failure Reason", "reasons": reasons,
                                "write_back": write_back}}
        conn = search.connect()
        defects.ROOT = scratch
        shown = os.path.join(scratch, ".workbench", JOB, defects.RESULT)

        def show():
            shutil.copy(shown, os.path.join(job_dir, defects.RESULT))
            return page.dom("defects")

        quiet(defects.cmd_load, JOB, text="project = ABC", transport=defect_jira,
              cfg=settings(False), conn=conn)
        html = show()
        check("Defects tab: the loaded bugs, the reason each has, and a similar earlier one",
              all(s in html for s in ("ABC-1", "ABC-2", "Test data", "ABC-50", "Code defect",
                                      "exception NullPointerException")))
        check("Defects tab: writing is OFF and there is no button to write",
              "Writing to Jira is OFF" in html and "Write to Jira</button>" not in html)
        check("a bug's summary is shown as text, not run as markup",
              "<img src=x" not in html and "&lt;img src=x onerror=alert(2)&gt;" in html)

        def cursor(prompt, followup):
            return {"result": json.dumps({"defects": [
                {"key": "ABC-1", "reason": "Code defect", "confidence": "high",
                 "why": "a null pointer in the service", "evidence": "NullPointerException"},
                {"key": "ABC-2", "reason": "Made up reason", "confidence": "high", "why": "x"}]})}
        quiet(defects.cmd_suggest, JOB, None, agent=cursor, root=scratch)
        html = show()
        check("Defects tab: a suggestion that differs from the current reason is marked",
              "DIFFERS from the current reason" in html and "a null pointer in the service" in html)
        check("Defects tab: an answer outside the list is not a suggestion",
              "the answer was not one of the allowed reasons" in html and "it answered: Made up reason" in html)

        quiet(defects.cmd_load, JOB, text="project = ABC", transport=defect_jira,
              cfg=settings(True), conn=conn)
        quiet(defects.cmd_suggest, JOB, None, agent=cursor, root=scratch)
        html = show()
        check("Defects tab: with writing switched on, each row can be written, and the page says so",
              "Writing to Jira is ON" in html and html.count("Write to Jira</button>") == 2)
        quiet(defects.cmd_apply, JOB, "ABC-1", "Code defect", "ABC-1", transport=defect_jira,
              cfg=settings(True), conn=conn)
        html = show()
        check("Defects tab: a write is read back and shown with what the bug held before",
              "is now 'Code defect'" in html and "was Test data" in html
              and "agrees with the current reason" in html)
        try:
            quiet(defects.main, ["apply", "--job", JOB, "--key", "ABC-2", "--reason",
                                 "Environment", "--confirm-key", "ABC-9"], transport=defect_jira)
        except SystemExit:
            pass
        defects.tell_error(JOB, "--confirm must repeat the key of the defect being changed", True)
        html = show()
        check("Defects tab: a refused write leaves the table on the page, under the refusal",
              "Refused — --confirm must repeat the key" in html and "ABC-2" in html)

        # ---- a job with no result shows none: not another job's ----------------
        other = page.dom("jira", job="check-ui-no-such-job")
        check("another job's Jira tab is empty",
              "Only in 6.02" not in other and "Refused —" not in other
              and 'id="jira-result" class="result"></div>' in other)
        other = page.dom("failures", job="check-ui-no-such-job")
        check("another job's Failures tab is empty",
              "Suite Demo" not in other and "Signatures in this run" not in other)

        # ---- the last result is cleared when a command starts ------------------
        with io.open(os.path.join(job_dir, search.JOB_RESULT), "w") as fh:
            fh.write('{"kind": "verify", "ok": true, "message": "stale"}')
        code, body = page.call("/api/start", {"job": JOB, "runnable": "jira-tests",
                                              "options": {"--project": "ABC", "--max": "abc"}})
        for _ in range(120):
            state = page.call(f"/api/status?job={JOB}&runnable=jira-tests")[1]
            if state.get("state") != "running":
                break
            time.sleep(0.25)
        left = page.call(f"/api/artifact?job={JOB}&name=jira-result.json")[1].get("text") or ""
        check("a command that dies on a bad argument leaves no earlier result behind",
              state.get("exit_code") == 2 and "stale" not in left, f"{state} {left[:80]}")

        # ---- the tabs that were there before ----------------------------------
        # ---- the Convert tab: a file is chosen, not a path typed -----------------
        upload_dir = os.path.join(ROOT, "target", "convert-input")
        had_uploads = os.path.isdir(upload_dir)
        aside = None
        if had_uploads and os.listdir(upload_dir):
            aside = tempfile.mkdtemp(prefix="check_ui_uploads_")     # somebody's own: put back
            for n in os.listdir(upload_dir):
                shutil.move(os.path.join(upload_dir, n), os.path.join(aside, n))
        try:
            def send(name, data, ctype="application/octet-stream"):
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/upload?name={urllib.parse.quote(name)}",
                    data=data, headers={"Content-Type": ctype}, method="POST")
                try:
                    with urllib.request.urlopen(req, timeout=20) as r:
                        return r.status, json.loads(r.read().decode())
                except urllib.error.HTTPError as e:
                    return e.code, json.loads(e.read().decode() or "{}")
            code, got = send("Check Ui Suite.xml", b"<?xml version='1.0'?><p/>")
            check("a chosen file is kept and answered with a path, forward slashes only",
                  code == 200 and got.get("path") == "target/convert-input/Check Ui Suite.xml",
                  f"HTTP {code} {got}")
            code, got = send("x.xml", b"<p/>", "text/plain")
            check("a file sent the way another site's form could send it is refused", code == 403)
            code, got = send("../../pom.xml", b"<p/>")
            check("a name cannot place the file anywhere else",
                  code == 200 and got.get("path") == "target/convert-input/pom.xml")
            code, got = send("notes.txt", b"<p/>")
            check("only a .xml is taken", code != 200 and ".xml" in str(got.get("error")))
            html = page.dom("convert")
            # Only the Convert tab's own part of the page: another tab's
            # help text names some of these options.
            # The converter's form: the Migration tab up to its Ask Cursor part.
            html = html[html.index('id="tab-convert"'):html.index('id="migration-ask-part"')]
            check("the Convert tab lists what was chosen, with where it was put",
                  "Check Ui Suite.xml" in html
                  and "target/convert-input/Check Ui Suite.xml" in html)
            for hidden in ("--output", "--package-root", "--envs", "--config", "--cursor-config",
                           "--service-name", "--diagram-cases", "--max-name-len", "--bootstrap",
                           "--skip-dataflow-check", "--keep-dead-props", "--no-reapply-patches"):
                if f'data-opt="{hidden}"' in html or f">{hidden}<" in html:
                    check(f"{hidden} is not on the Convert tab", False)
                    break
            else:
                check("the options that keep their defaults are not on the Convert tab", True)
            check("diagrams, phase specs and Cursor assist are each one choice",
                  html.count('name="cv-diagrams"') == 3 and html.count('name="cv-phase"') == 2
                  and html.count('name="cv-cursor"') == 2)
            check("Verify all is a tick box, and the buttons are in the middle",
                  'id="convert-verify" type="checkbox"' in html
                  and 'class="row center"' in html
                  and html.index('class="row center"') < html.index('id="run-convert"')
                  < html.index('id="run-token"') < html.index('id="convert-state"'))
            check("what is still shown is there to be set",
                  all(f'data-opt="{o}"' in html for o in
                      ("--data-dir", "--clean", "--classic", "--skip-self-test")))
            check("the suite's name is not asked for: the converter takes it from the file's name",
                  'data-opt="--suite-name"' not in html)
            code, got = page.call("/api/upload-clear", {})
            check("Remove forgets the chosen files",
                  code == 200 and got.get("removed") == 2
                  and page.call("/api/uploads")[1] == {"files": []})
        finally:
            for n in (os.listdir(upload_dir) if os.path.isdir(upload_dir) else []):
                if n.endswith(".xml") and os.path.isfile(os.path.join(upload_dir, n)):
                    os.remove(os.path.join(upload_dir, n))
            if aside:
                for n in os.listdir(aside):
                    shutil.move(os.path.join(aside, n), os.path.join(upload_dir, n))
                shutil.rmtree(aside, ignore_errors=True)
            elif not had_uploads:
                shutil.rmtree(upload_dir, ignore_errors=True)
        # ---- the specification as a file ------------------------------------
        def send_spec(name, data, ctype="application/octet-stream"):
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/spec-upload?job={JOB}"
                f"&name={urllib.parse.quote(name)}",
                data=data, headers={"Content-Type": ctype}, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    return r.status, json.loads(r.read().decode())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read().decode() or "{}")
        orders = json.dumps({"openapi": "3.0.1", "paths": {
            "/orders": {"get": {}, "post": {}}, "/orders/{id}": {"get": {}}}}).encode()
        code, got = send_spec("Orders API.json", orders)
        spec = got.get("spec") or {}
        check("a specification chosen as a file is kept with the job and its path is told",
              code == 200 and spec.get("path") == f".workbench/{JOB}/spec/Orders API.json"
              and spec.get("endpoints") == 3, f"HTTP {code} {got}")
        code, got = send_spec("notes.json", b'{"hello": 1}')
        check("a file that is not a specification is not kept, and the page is told why",
              code != 200 and "not an API specification" in str(got.get("error")), f"{code} {got}")
        code, got = send_spec("x.json", orders, "text/plain")
        check("a specification sent the way another site's form could send it is refused",
              code == 403)
        html = page.dom("new")
        check("tab 1 shows the kept specification, where it is and what is in it",
              "Orders API.json" in html and "3 endpoint(s), JSON" in html
              and f".workbench/{JOB}/spec/Orders API.json" in html
              and 'id="design-spec-link"' in html and 'id="design-spec-fetch"' in html)
        check("the page can start the Confluence fetch, with a link and a name only",
              declared.get("spec-confluence", {}).get("options")
              == {"--job": "text", "--link": "text", "--name": "text"})
        code, got = page.call("/api/spec-clear", {"job": JOB})
        html = page.dom("new")
        check("Remove forgets the kept specification",
              code == 200 and got.get("removed") is True and "Orders API.json" not in html)
        check("tab 1 still has its buttons", 'id="run-intake"' in html and 'id="run-design"' in html)
        html = page.dom("agent")
        check("tab 3 still opens", 'id="tab-agent" class="tab" role="tabpanel" hidden' not in html)
        check("the title, and no mock banner while the stand-ins are off",
              "<h1>AI Enabled Test WorkBench</h1>" in html
              and 'id="mock-banner" class="mockbar" hidden' in html)

        # ---- mock mode, through a server that has it switched on ----------------
        srv.terminate()
        store = os.path.join(ROOT, ".workbench", "mock-jira.json")
        kept = None
        if os.path.isfile(store):                 # somebody's own mock writes: put back after
            with io.open(store, encoding="utf-8") as fh:
                kept = fh.read()
            os.remove(store)
        os.environ["WORKBENCH_MOCK"] = "1"
        port2 = free_port()
        srv = subprocess.Popen([sys.executable, "-B", os.path.join("tools", "agent", "server.py"),
                                "--port", str(port2)], cwd=ROOT,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        mocked = Page(browser, f"http://127.0.0.1:{port2}")
        try:
            for _ in range(80):
                try:
                    mocked.call("/api/jobs")
                    break
                except Exception:                        # noqa: BLE001
                    time.sleep(0.25)

            def press(runnable, options, route="/api/start", extra=None):
                body = dict({"job": JOB}, **(extra or {}))
                if route == "/api/start":
                    body.update(runnable=runnable, options=options)
                code, _ = mocked.call(route, body)
                state = {}
                for _ in range(240):
                    state = mocked.call(f"/api/status?job={body['job']}&runnable={runnable}")[1]
                    if state.get("state") != "running":
                        break
                    time.sleep(0.25)
                return code, state.get("exit_code")

            check("mock mode: the page is told", mocked.call("/api/mock")[1]
                  == {"jira": True, "cursor": True, "error": ""})
            html = mocked.dom("jira")
            check("mock mode: every tab carries the banner",
                  "<strong>MOCK MODE</strong></div>" in html
                  and 'id="mock-banner" class="mockbar" hidden' not in html)

            code, rc = press("jira-verify", {})
            html = mocked.dom("jira")
            check("mock mode: Check the token works with no token, and says MOCK",
                  rc == 0 and "MOCK USER" in html and "sample data from this machine" in html)
            code, rc = press("jira-versions", {"--project": "ABC"})
            html = mocked.dom("jira")
            check("mock mode: Versions", rc == 0 and "Versions of ABC" in html
                  and html.index("Release 2.0") < html.index("Release 1.0"))
            code, rc = press("jira-release", {"--project": "ABC", "--version": "Release 2.0",
                                              "--compare": "Release 1.1"})
            html = mocked.dom("jira")
            check("mock mode: What is in this version, compared with another",
                  rc == 0 and "Only in Release 2.0" in html and "Only in Release 1.1" in html
                  and "ABC-5" in html)

            # A story given by its link: read with what is around it.
            code, rc = press("intake", None, route="/api/intake",
                             extra={"text": "", "links": ["https://jira.example.test/browse/ABC-123"]})
            brief = mocked.call(f"/api/artifact?job={JOB}&name=brief.md")[1].get("text") or ""
            check("mock mode: a Jira story link is read, with its parent and linked stories",
                  rc == 0 and "## The story: ABC-123 (MOCK" in brief
                  and "### ABC-9000 -- parent" in brief and "### ABC-9001 -- is blocked by" in brief
                  and "POST /guests/123/businesses" in brief, f"rc={rc} {brief[:300]}")
            html = mocked.dom("new")
            check("a page the fetcher could not read can be pasted",
                  'id="pages"' in mocked.dom("new"))
            # Several stories at once: one job each, named by its key. The
            # page starts one intake per story; here the same requests are made.
            for key in ("ABC-201", "ABC-202"):
                mocked.call("/api/intake", {"job": key, "text": "", "links": [key]})
            for key in ("ABC-201", "ABC-202"):
                for _ in range(240):
                    if mocked.call(f"/api/status?job={key}&runnable=intake")[1].get("state") != "running":
                        break
                    time.sleep(0.25)
            check("several stories make one job each, named by the key",
                  all(json.loads(mocked.call(f"/api/artifact?job={k}&name=intake.json")[1]
                                 .get("text") or "{}").get("story", {}).get("key") == k
                      for k in ("ABC-201", "ABC-202")))
            code, rc = press("story-check", {}, extra={"job": "ABC-201"})
            got = json.loads(mocked.call("/api/artifact?job=ABC-201&name=story-check.json")[1]
                             .get("text") or "{}")
            check("mock mode: the story can be read again and compared with what the job holds",
                  rc == 0 and got.get("ok") is True and got.get("changed") is False, str(got)[:300])
            read_tab = mocked.dom("new", job="ABC-201")
            check("the story box offers the check once the job has read its story",
                  'id="story-check"' in read_tab and 'id="story-check-row"' in read_tab
                  and 'id="story-check-row" hidden' not in read_tab)
            check("a job that has read no story is not offered the check",
                  'id="story-check-row" hidden' in mocked.dom("new", job="no-story-yet"))
            html = mocked.dom("new")
            check("the story is given by link or pasted, one box at a time, link first",
                  'name="story-from" value="link" checked' in html
                  and 'id="story-from-paste" hidden' in html and 'id="story-from-link" hidden' not in html)
            check("the Confluence pages likewise: links first, the paste box hidden",
                  'name="pages-from" value="link" checked' in html and 'id="pages-from-paste" hidden' in html)
            check("the specification is uploaded, pasted or fetched, one at a time, upload first",
                  'name="spec-from" value="upload" checked' in html and 'id="spec-from-paste" hidden' in html
                  and 'id="spec-from-confluence" hidden' in html and 'id="spec-from-upload" hidden' not in html)
            check("the Jira token is not a button of its own on this tab (Read it checks it)",
                  'id="run-jira-verify"' not in html)
            check("Design the tests comes after the specification, before the requirements",
                  html.find('name="spec-from"') < html.find('id="run-design"') < html.find('id="design-requirements"'))
            check("a design can be cleared", 'id="clear-design"' in html)
            check("the review's steps can be edited in place", "stepEditor" in io.open(
                os.path.join(HERE, "ui", "app.js"), encoding="utf-8").read())
            agent_html = mocked.dom("agent")
            strip = agent_html.split('id="repo-where"', 1)[-1].split('id="repo-branches"', 1)[0]
            check("the repository strip names the remote but never its address, and never prints null",
                  "pushes to origin" in strip and "://" not in strip and "null" not in strip, strip[:300])
            code, info = mocked.call("/api/repo")
            check("the page is never sent the remote's address", "remote_url" not in info and "has_remote" in info,
                  str(sorted(info))[:200])
            check("the design's options are folded away, closed, their defaults kept",
                  '<details class="more" id="design-options">' in html
                  and 'value="balanced" selected' in html and 'value="plan" selected' in html)
            check("the story's existing Xray tests are a choice of the Jira story, not a button of their own",
                  'name="story-from" value="xray"' in html and 'id="run-xray-import"' not in html
                  and 'id="story-xray-note"' in html)
            check("the requirements are uploaded or pasted, one box at a time, upload first",
                  'name="req-from" value="upload" checked' in html and 'id="req-from-paste" hidden' in html
                  and 'id="req-from-upload" hidden' not in html)
            code, rc = press("intake", None, route="/api/intake", extra={"job": "ABC-201", "again": True})
            told = json.loads(mocked.call("/api/artifact?job=ABC-201&name=intake.json")[1].get("text") or "{}")
            check("mock mode: a job is read again with the links it was given, no box needed",
                  code == 200 and rc == 0 and told.get("ok") is True
                  and (told.get("story") or {}).get("key") == "ABC-201", f"{code} {rc} {str(told)[:200]}")
            code, body = mocked.call("/api/intake", {"job": "never-read-job", "again": True})
            check("a job never read has nothing to read again, and is told so",
                  code >= 400 and "nothing to read again" in json.dumps(body), f"{code} {body}")
            check("the box takes several stories and has a place for them",
                  '<textarea id="story"' in mocked.dom("new") and 'id="stories"' in mocked.dom("new"))
            check("the Automate Test Case tab has a place for the Jira story",
                  'id="story"' in html and 'for="story"' in html and "Confluence pages" in html)

            bar = html[html.find('class="tabs"'):html.find("</nav>")]
            check("the tabs are named for what is done on them, in order",
                  0 < bar.find("Automate Test Case") < bar.find("Migration from ReadyAPI")
                  < bar.find("Commit and push") < bar.find("Manual tests")
                  and "Agentic Test Creation<" not in bar and "Convert ReadyAPI<" not in bar)
            check("Cursor is run from where the cases are, and setup is not a button",
                  'id="auto-selected"' in html and 'id="agent-ask"' in html and 'id="cursor-log"' in html
                  and 'id="agent-setup"' not in html and 'id="agent-generate"' not in html)
            mig = mocked.dom("convert")
            mig = mig[mig.find('id="migration-ask-part"'):]
            check("the Migration tab asks Cursor about a converted suite or the converter, nothing else",
                  'id="mig-run"' in mig and 'value="converted"' in mig and 'value="converter"' in mig
                  and 'value="new-test"' not in mig and 'id="converted-suites"' in mig)
            check("the design can be asked for the whole specification",
                  'id="design-whole"' in html)
            code, r = mocked.call("/api/repo")
            check("the repository is read for the page: a branch, a clean flag, the branches",
                  code == 200 and r.get("is_repo") is True and r.get("branch")
                  and isinstance(r.get("local_branches"), list) and "clean" in r, str(r)[:200])
            code, r = mocked.call("/api/repo-checkout", {"branch": "main", "confirm": "nope"})
            check("a checkout needs the branch named twice", code == 400 and "twice" in r.get("error", ""))
            code, r = mocked.call("/api/repo-checkout", {"branch": "-x", "confirm": "-x"})
            check("a checkout refuses a name that is not a branch name", code == 400, str(r))
            html = mocked.dom("agent")
            check("the Agentic tab shows the repository and offers the base and the branch",
                  'id="repo"' in html and 'id="agent-base"' in html and 'id="agent-branch"' in html)
            agent_js = io.open(os.path.join(HERE, "ui", "app.js"), encoding="utf-8").read()
            check("a job named in the address is kept on the Commit and push tab",
                  "focusUnfinishedWorkbench" in agent_js
                  and 'location.search).get("job")' in agent_js
                  and "do not swap the box" in agent_js)
            check("both tabs that produce cases have a Create in Xray block",
                  'id="xray-design-create"' in html and 'id="xray-manual-create"' in html)

            code, rc = press("defects-load", {"--project": "ABC", "--version": "Release 2.0"})
            html = mocked.dom("defects")
            check("mock mode: Load defects", rc == 0 and "ABC-101" in html and "ABC-150" in html
                  and "the defects are sample data" in html)
            code, rc = press("defects-suggest", {})
            html = mocked.dom("defects")
            check("mock mode: Suggest reasons, without Cursor",
                  rc == 0 and "DIFFERS from the current reason" in html
                  and "a keyword match, not Cursor" in html)
            code, rc = press("defects-apply", {"--key": "ABC-101", "--reason": "Code defect",
                                               "--confirm-key": "ABC-101"})
            html = mocked.dom("defects")
            written = {}
            if os.path.isfile(store):
                with io.open(store, encoding="utf-8") as fh:
                    written = json.load(fh)
            check("mock mode: Write to Jira changes a file on this machine and nothing else",
                  rc == 0 and written == {"ABC-101": {"value": "Code defect"}}
                  and "is now 'Code defect'" in html, f"rc={rc} store={written}")

            # Creating the designed cases in the mock Xray: one key a case,
            # kept for the job, and nothing created twice.
            # A design is used only when made from the brief the job has
            # now; the fixture says it was.
            fingerprint = _load_module("check_ui_loop", os.path.join(HERE, "loop.py")).brief_fingerprint
            with io.open(os.path.join(job_dir, "design.json"), "w", encoding="utf-8") as fh:
                json.dump({"kind": "design", "mock": True, "brief": fingerprint(job_dir), "test_cases": [
                    {"id": "TC-001", "title": "A case", "endpoint": "GET /x", "type": "positive",
                     "priority": "high", "preconditions": "",
                     "steps": [{"action": "do", "data": "", "expected": "200"}],
                     "requirement_refs": [], "endpoint_in_spec": True}]}, fh)
            code, body = mocked.call("/api/start", {"job": JOB, "runnable": "xray-create", "options": {
                "--what": "design", "--story": "ABC-123", "--confirm-story": "ABC-123", "--expect": "mock"}})
            check("Create in Xray refuses until the cases are finalized",
                  code == 400 and "not finalized" in str(body), str(body)[:300])
            code, body = mocked.call("/api/case-review", {"job": JOB, "what": "design", "ids": ["TC-001"],
                                                          "state": "approved", "title": "A case, corrected"})
            check("a decision on a case is kept with the job",
                  code == 200 and body.get("written", {}).get("TC-001", {}).get("state") == "approved", str(body))
            code, body = mocked.call("/api/case-review", {"job": JOB, "what": "design", "ids": ["TC-001"],
                                                          "state": "maybe"})
            check("a decision that is not one of the three words is refused", code == 400, str(body))
            before = mocked.dom("new")
            check("a case's Automate waits for Finalize, and says so",
                  "Automate takes finalized cases only" in before, before[before.find('class="automate-cell"'):][:300])
            code, body = mocked.call("/api/workflow", {"job": JOB, "action": "finalize"})
            check("the approved cases can be finalized", code == 200 and body.get("finalized", {}).get("cases") == ["TC-001"],
                  str(body))
            html = mocked.dom("new")
            check("the Automate Test Case tab shows the review table and where the job is",
                  'id="design-review"' in html and "Approve all" in html and 'id="flow"' in html
                  and "Review (1 approved)" in html, html[html.find('id="flow"'):][:400])
            strip = html[html.find('id="flow"'):html.find("</ol>", html.find('id="flow"'))]
            check("each step of the strip is a link to where it is done",
                  strip.count('class="step-link"') == 10
                  and 'data-tab="new" data-target="auto-selected"' in strip
                  and 'data-tab="agent" data-target="agent-push"' in strip
                  and 'data-tab="new" data-target="design-review"' in strip, strip[:400])
            check("once finalized, a case's Automate can be pressed",
                  'data-ready="1">Automate</button>' in html and "Automate takes finalized cases only" not in html,
                  html[html.find('class="automate-cell"'):][:300])
            check("each approved case can be automated from its row",
                  ">Automate</button>" in html or ">Again</button>" in html)
            commit_tab = mocked.dom("agent")
            commit_tab = commit_tab[commit_tab.find('id="tab-agent"'):]
            check("the Commit and push tab has a Stop of its own",
                  'id="agent-commit-stop"' in commit_tab)
            code, st = mocked.call("/api/stop", {"job": JOB, "runnable": "agent-review"})
            check("Stop with nothing running says so, and harms nothing", code == 200 and not st.get("stopped"), str(st))
            tab_new = mocked.dom("new")
            tab_new = tab_new[tab_new.find('id="tab-new"'):tab_new.find('id="tab-convert"')]
            # Asked for 2026-10-21: each area's logs under its own button.
            check("each area of the Automate Test Case tab has its own collapsible logs, under its button",
                  all(f'id="{a}-logs"' in tab_new for a in ("design", "refine", "automate", "ask"))
                  and tab_new.count('class="log cursor"') == 4
                  and tab_new.find('id="design-logs"') < tab_new.find('id="design-review"')
                  and tab_new.find('id="refine-run"') < tab_new.find('id="refine-logs"')
                  and tab_new.find('id="auto-selected"') < tab_new.find('id="automate-logs"') < tab_new.find('id="agent-ask"')
                  and tab_new.find('id="agent-ask-send"') < tab_new.find('id="ask-logs"')
                  and 'id="design-cursor-go"' not in tab_new)
            check("each field of a step can be removed, and a step as a whole",
                  'class="drop d-do"' in html and 'class="drop d-expect"' in html
                  and ">Remove step</button>" in html, html[html.find('class="steps-edit"'):][:300])
            check("the Migration tab's Cursor log follows its own file, not the Automate tab's",
                  'migration: "migration-cursor"' in agent_js
                  and 'migration: { el: "mig-cursor-log", src: "migration" }' in agent_js)
            check("each Cursor pane reads its own file, from its own place in it",
                  "runnable=${CURSOR_FILES[p.src]}&offset=${p.offset}" in agent_js
                  and "runnable=${CURSOR_FILES[p.src]}&end=1" in agent_js)
            check("Ask Cursor is one box for a change, a new request or a question",
                  '>Ask Cursor</button>' in html and 'id="agent-ask-send" disabled' not in tab_new)
            logs_tab = mocked.dom("logs")
            check("the Logs tab can ask Cursor to change the workbench itself",
                  'id="wb-run"' in logs_tab and 'id="wb-cursor-log"' in logs_tab and 'id="wb-review"' in logs_tab)
            code, st = mocked.call("/api/workbench-job")
            check("the workbench's own job is named, not taken from the job box",
                  code == 200 and str(st.get("job", "")).startswith("workbench-"), str(st))
            check("a run's results can be published to Xray from the Commit and push tab",
                  'id="agent-xray"' in mocked.dom("agent"))
            check("the change's tests run on stg before it can be pushed",
                  'id="agent-run"' in mocked.dom("agent") and 'id="agent-run-result"' in mocked.dom("agent"))
            set_tab = mocked.dom("settings")
            check("the Settings tab takes the tokens, as password boxes, and shows none back",
                  'id="set-jira-pat"' in set_tab and 'type="password"' in set_tab and 'id="settings-save"' in set_tab)
            check("the Settings tab takes the Cursor key (a password box) and its models",
                  'id="set-cursor-key"' in set_tab and 'id="set-cursor-model"' in set_tab
                  and 'id="set-cursor-design-model"' in set_tab)
            code, s = mocked.call("/api/settings")
            check("the Settings tab sets each workflow stage: Create in Xray, the mapping, the run",
                  'id="set-xc-on"' in set_tab and 'id="set-sm-rules"' in set_tab and 'id="set-run-env"' in set_tab)
            check("the settings say what is set, never a token", code == 200 and "jira" in s
                  and all(not str(v).startswith("set, ") or "characters" in str(v)
                          for v in (s.get("jira") or {}).values()), str(s)[:200])
            cfg = s.get("config") or {}
            check("every value of an environment block can be edited, a secret only as set or not set",
                  'id="cfg-form"' in set_tab and 'id="cfg-env"' in set_tab and 'id="cfg-save"' in set_tab
                  and ("fields" in cfg or "problem" in cfg)
                  and all("value" not in f for f in cfg.get("fields") or [] if f.get("kind") == "secret")
                  and ('class="cfg-box' in set_tab or not cfg.get("fields")), str(cfg)[:200])
            check("the cases can be refined with Cursor from the review",
                  'id="refine-run"' in html and 'id="refine-ask"' in html and 'id="wf-finalize"' in html)
            check("Update in Xray previews before it sends",
                  'id="xray-update-preview"' in html and 'id="xray-update-send"' in html)
            check("Publish to Xray can name an existing Test Execution",
                  'id="agent-xray"' in html and 'id="agent-xray-exec"' in html)
            check("a screenshot can be pasted into each box whose words go to Cursor",
                  all(f'id="{b}-pasted"' in html for b in
                      ("refine-ask", "design-notes", "agent-ask", "mig-ask", "wb-ask", "manual-notes"))
                  and "Ctrl+V pastes a screenshot for Cursor" in html)
            check("a run can set the suite's parallel threads, 1 to 6 at most",
                  'id="run-threads"' in html and '<option value="6">6</option>' in html
                  and '<option value="7">' not in html)
            check("each case's suites show on its row, from the rules",
                  ">Suites<" in html and 'class="groups"' in html and "from the rules" in html)
            check("each case's Description shows on its row: the plan, step by step",
                  ">Description<" in html and 'class="desc"' in html and "Step 1:" in html)
            bare = subprocess.run(
                [mocked.browser, "--headless=new", "--disable-gpu", "--no-first-run",
                 "--disable-extensions", "--virtual-time-budget=9000", "--dump-dom", f"{mocked.base}/#new"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=120).stdout.decode("utf-8", "replace")
            check("Automate the approved cases cannot be pressed on a job with no cases",
                  'id="auto-selected" class="go" disabled' in bare or 'id="auto-selected" disabled' in bare,
                  bare[bare.find('id="auto-selected"') - 10:][:120])
            check("a fresh start opens a new, empty job: no earlier job's cases show",
                  "no design cases in this job yet" in bare and 'class="automate-cell"' not in bare
                  and 'id="known-jobs"' in bare, bare[bare.find('id="design-review"'):][:300])
            code, rc = press("xray-create", {"--what": "design", "--story": "ABC-123",
                                             "--confirm-story": "ABC-123", "--expect": "mock"})
            got = json.loads(mocked.call(f"/api/artifact?job={JOB}&name=xray-result.json")[1]
                             .get("text") or "{}")
            check("the corrected title is what reaches Xray",
                  rc == 0 and [c.get("title") for c in got.get("created", [])] == ["A case, corrected"],
                  str(got)[:300])
            check("mock mode: Create in Xray makes one mock test a case and keeps its key",
                  rc == 0 and got.get("kind") == "xray-create" and got.get("mock") is True
                  and [c["key"] for c in got.get("created", [])] == ["ABC-9001"], str(got)[:300])
            code, rc = press("xray-create", {"--what": "design", "--story": "ABC-123",
                                             "--confirm-story": "ABC-123", "--expect": "mock"})
            got = json.loads(mocked.call(f"/api/artifact?job={JOB}&name=xray-result.json")[1]
                             .get("text") or "{}")
            check("mock mode: a second Create in Xray creates nothing twice",
                  rc == 0 and got.get("created") == [] and len(got.get("kept", [])) == 1, str(got)[:300])
            code, rc = press("xray-status", {})
            st = json.loads(mocked.call(f"/api/artifact?job={JOB}&name=xray-status.json")[1]
                            .get("text") or "{}")
            check("mock mode: the Xray status names the key the case has",
                  rc == 0 and st.get("keys", {}).get("design") == {"TC-001": "ABC-9001"}, str(st)[:300])
            html = mocked.dom("new")
            check("the Automate Test Case tab shows the case's Xray key",
                  "ABC-9001" in html and "Already in the mock Xray" in html)

            spec = json.dumps({"openapi": "3.0.1", "paths": {
                "/groups/{id}/rates": {"get": {"summary": "List"}, "post": {"summary": "Create"}}}})
            code, rc = press("agent-design", None, route="/api/design",
                             extra={"swagger": spec, "service": "rates", "speed": "fast"})
            html = mocked.dom("new")
            check("mock mode: Design the tests, without Cursor",
                  rc == 0 and "MOCK. This is not a design." in html
                  and "[MOCK] POST /groups/{id}/rates succeeds" in html, f"rc={rc}")
            code, rc = press("agent-setup", {})
            check("mock mode: Check Cursor setup needs no SDK and no key", rc == 0)

            # ---- the review of a proposed change -----------------------------
            # A change waiting for a person, as the Agentic Test Creation tab leaves it. The
            # review reads these two files and nothing else.
            def waiting(started):
                with io.open(os.path.join(job_dir, "review.json"), "w", encoding="utf-8") as fh:
                    json.dump({"state": "pending-review", "scope": "new-test",
                               "started": started,
                               "files": {"created": ["src/test/java/NewTest.java"]}}, fh)
            with io.open(os.path.join(job_dir, "proposed.diff"), "w", encoding="utf-8") as fh:
                fh.write("diff --git a/src/test/java/NewTest.java b/src/test/java/NewTest.java\n"
                         "new file\n--- /dev/null\n+++ b/src/test/java/NewTest.java\n"
                         "@@ -0,0 +1,4 @@\n+public class NewTest {\n"
                         "+    @Test(enabled = false)\n+    public void nothing() { }\n+}\n")
            waiting("2026-10-10T10:00:00Z")
            html = mocked.dom("agent")
            check("Review the change is offered for a change that is waiting",
                  ">Review the change</button>" in html
                  and 'id="agent-review" disabled' not in html
                  and f'id="agent-review" data-job="{JOB}"' in html,
                  "the button is off or missing")
            code, rc = press("agent-review", {})
            html = mocked.dom("agent")
            check("mock mode: Review the change lists a finding at the line the diff gives",
                  rc == 0 and "[MOCK] the test is switched off" in html
                  and "src/test/java/NewTest.java:2" in html
                  and "This is not a review." in html, f"rc={rc}")
            check("the review's report can be taken away",
                  'id="dl-review" class="dl" hidden' not in html
                  and "name=code-review.md" in html)
            code, body = mocked.call(f"/api/download?job={JOB}&name=code-review.md", raw=True)
            check("the report is Markdown and says it is a mock",
                  code == 200 and "# Code review of job" in body and "**MOCK.**" in body,
                  f"HTTP {code}")
            check("a review of the change on screen carries no warning that it is not",
                  "EARLIER run" not in html and "WRITTEN AGAIN" not in html)
            with io.open(os.path.join(job_dir, "proposed.diff"), "a", encoding="utf-8") as fh:
                fh.write("--- a/later.txt\n+++ b/later.txt\n@@ -1 +1 @@\n-a\n+b\n")
            html = mocked.dom("agent")
            html = html[html.find('id="tab-agent"'):]          # the Commit and push tab only
            check("a diff written again after the review is said not to be the one read",
                  "WRITTEN AGAIN" in html
                  and html.index("WRITTEN AGAIN") < html.index("[MOCK] the test is switched off"))
            waiting("2026-10-10T11:11:11Z")
            html = mocked.dom("agent")
            html = html[html.find('id="tab-agent"'):]
            check("a review of an earlier run says so, above what it found",
                  "EARLIER run" in html
                  and html.index("EARLIER run") < html.index("[MOCK] the test is switched off"))
            with io.open(os.path.join(job_dir, "code-review.json"), "w", encoding="utf-8") as fh:
                json.dump({"kind": "code-review", "job": JOB, "run_started": "2026-10-10T11:11:11Z",
                           "mock": False, "message": "1 finding(s).", "failures": [],
                           "files": [{"file": "a.java", "status": "not reviewed",
                                      "why": "the reply did not mention this file"}],
                           "findings": [{"file": "a.java", "line": 3, "where": "added",
                                         "severity": "high", "category": "bug",
                                         "title": "<img src=x onerror=alert(1)>",
                                         "why": "<b>w</b>", "fix": "f", "evidence": "<i>e</i>"}],
                           "unconfirmed": [], "dropped": 0, "over": 0}, fh)
            html = mocked.dom("agent")
            check("what Cursor wrote reaches the page as text, never as markup",
                  "&lt;img src=x onerror=alert(1)&gt;" in html and "<img src=x" not in html
                  and "&lt;i&gt;e&lt;/i&gt;" in html)
            check("files that were not read are said above the findings",
                  "NOT ALL OF THE CHANGE WAS REVIEWED" in html
                  and html.index("NOT ALL OF THE CHANGE") < html.index("&lt;img src=x"))
            # A result with nothing in it (hand-made, or from an older build)
            # must not stop the page, nor leave the review before it standing.
            with io.open(os.path.join(job_dir, "code-review.json"), "w", encoding="utf-8") as fh:
                json.dump({"kind": "code-review", "run_started": "2026-10-10T11:11:11Z"}, fh)
            html = mocked.dom("agent")
            check("a result file with fields missing empties the panel instead of breaking it",
                  "&lt;img src=x" not in html and "No findings is not a sign-off" in html)
            os.remove(os.path.join(job_dir, "code-review.json"))
            with io.open(os.path.join(job_dir, "agent-review.status.json"), "w",
                         encoding="utf-8") as fh:
                json.dump({"state": "failed", "exit_code": 1}, fh)
            html = mocked.dom("agent")
            check("a review that ended without a result says so, not nothing",
                  "The last review did not finish" in html)
            press("agent-review", {})
            with io.open(os.path.join(job_dir, "review.json"), "w", encoding="utf-8") as fh:
                json.dump({"state": "discarded", "scope": "new-test",
                           "started": "2026-10-10T11:11:11Z"}, fh)
            html = mocked.dom("agent")
            check("after Discard the review says the change is no longer waiting",
                  "no longer waiting (the job is DISCARDED)" in html
                  and 'id="agent-review" disabled' in html)
            # ---- an old outcome: worded by what happened, and put away ---------
            with io.open(os.path.join(job_dir, "review.json"), "w", encoding="utf-8") as fh:
                json.dump({"state": "rejected", "scope": "new-test", "why": "timeout", "undone": True,
                           "reason": "Cursor call failed: Cursor did not finish within 2700s",
                           "updated": "2026-10-10T12:00:00Z"}, fh)
            html = mocked.dom("agent")
            check("a timeout is not called a broken rule, and says when it was",
                  "did not finish in time. No rule was broken" in html
                  and "(as of 2026-10-10T12:00:00Z)" in html and "broke a rule" not in html)
            with io.open(os.path.join(job_dir, "review.json"), "w", encoding="utf-8") as fh:
                json.dump({"state": "generating", "scope": "new-test"}, fh)
            html = mocked.dom("agent")
            check("a story job that did not finish does not mention the workbench on Ask Cursor",
                  "change to the workbench did not finish" not in html
                  and "A run of this job did not finish" in html)
            with io.open(os.path.join(job_dir, "review.json"), "w", encoding="utf-8") as fh:
                json.dump({"state": "generating", "scope": "workbench"}, fh)
            html = mocked.dom("agent")
            check("a workbench change that did not finish says so on Ask Cursor",
                  "change to the workbench did not finish" in html)
            with io.open(os.path.join(job_dir, "review.json"), "w", encoding="utf-8") as fh:
                json.dump({"state": "rejected", "scope": "new-test", "why": "timeout", "undone": True,
                           "reason": "Cursor call failed: Cursor did not finish within 2700s",
                           "updated": "2026-10-10T12:00:00Z"}, fh)
            html = mocked.dom("agent")
            check("an outcome with nothing in the tree offers Dismiss",
                  'id="agent-dismiss" title=' in html and 'id="agent-dismiss" disabled' not in html)
            with io.open(os.path.join(job_dir, "cursor.log"), "w", encoding="utf-8") as fh:
                fh.write("2026-10-10 12:00:00  an old conversation with Cursor\n")
            with io.open(os.path.join(job_dir, "agent-generate.log"), "w", encoding="utf-8") as fh:
                fh.write("FAIL an old command log\n")
            with io.open(os.path.join(job_dir, "agent-prompt-1.txt"), "w", encoding="utf-8") as fh:
                fh.write("an old prompt\n")
            code, got = mocked.call("/api/agent-dismiss", {"job": JOB})
            html = mocked.dom("agent")
            check("Dismiss puts the outcome away and the tab starts clean",
                  code == 200 and got.get("dismissed") is True and got.get("was") == "rejected"
                  and "No agent run for this job yet" in html
                  and os.path.isfile(os.path.join(job_dir, "review-dismissed.json")), f"{code} {got}")
            archive = os.path.join(job_dir, str(got.get("archive") or "none"))
            check("the run's logs, prompts and answers went with it, into one folder",
                  {"agent-generate.log", "agent-prompt-1.txt", "cursor.log"} <= set(got.get("moved") or [])
                  and os.path.isfile(os.path.join(archive, "cursor.log"))
                  and not os.path.exists(os.path.join(job_dir, "cursor.log"))
                  and "an old conversation" not in html and "an old command log" not in html,
                  f"{got.get('moved')} {html.count('an old')}")
            waiting("2026-10-10T13:00:00Z")
            code, got = mocked.call("/api/agent-dismiss", {"job": JOB})
            check("a change waiting for review cannot be dismissed, only discarded or approved",
                  code >= 400 and "Use Discard" in json.dumps(got), f"{code} {got}")
            check("Dismiss is disabled while a change waits", 'id="agent-dismiss" disabled' in mocked.dom("agent"))
            # ---- manual tests: documents, existing tests, the delta ------------
            def send_doc(name, data, ctype="application/octet-stream"):
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port2}/api/doc-upload?job={JOB}"
                    f"&name={urllib.parse.quote(name)}",
                    data=data, headers={"Content-Type": ctype}, method="POST")
                try:
                    with urllib.request.urlopen(req, timeout=30) as r:
                        return r.status, json.loads(r.read().decode())
                except urllib.error.HTTPError as e:
                    return e.code, json.loads(e.read().decode() or "{}")
            code, got = send_doc("Booking Rules.md", b"# Rules\nOver 9 rooms is a group booking.\n")
            check("a requirements file is kept with the job and its path is told",
                  code == 200 and (got.get("file") or {}).get("path")
                  == f".workbench/{JOB}/docs/Booking Rules.md", f"HTTP {code} {got}")
            code, got = send_doc("tool.exe", b"MZ")
            check("a file of a kind that is not read is not kept", code != 200)
            code, got = send_doc("x.md", b"x", "text/plain")
            check("a document sent the way another site's form could send it is refused",
                  code == 403)
            html = mocked.dom("manual")
            check("the Manual tests tab lists the file and where it was put",
                  "Booking Rules.md" in html
                  and f".workbench/{JOB}/docs/Booking Rules.md" in html
                  and html.count('role="tab"') == 11)
            code, rc = press("manual-inventory", {"--project": "ABC"})
            html = mocked.dom("manual")
            check("mock mode: Read existing tests, with their steps, and says it is sample data",
                  rc == 0 and "existing test(s) of ABC read" in html and "with their Xray steps" in html
                  and "No Jira was contacted" in html, f"rc={rc}")
            code, rc = press("manual-generate", None, route="/api/manual",
                             extra={"app": "Booking", "speed": "fast", "notes": "the fee changed"})
            html = mocked.dom("manual")
            check("mock mode: Write the test cases gives new cases and an update to a test that was read",
                  rc == 0 and "[MOCK] A rule from Booking Rules.md is applied" in html
                  and "MT-001" in html and "Existing tests to update" in html
                  and "They are not test cases." in html, f"rc={rc}")
            check("the manual results can be taken away",
                  'id="dl-manual-md" class="dl" hidden' not in html
                  and "name=manual-new-cases.csv" in html)
            code, body = mocked.call(f"/api/download?job={JOB}&name=manual-tests.md", raw=True)
            check("the manual report is Markdown and says it is a mock",
                  code == 200 and "# Manual test cases" in body and "**MOCK.**" in body)
            with io.open(os.path.join(job_dir, "manual-result.json"), "w", encoding="utf-8") as fh:
                json.dump({"kind": "manual-tests", "message": "1 new case(s).",
                           "inventory": {"used": False}, "failures": ["the reply was cut off"],
                           "new_cases": [{"id": "MT-001", "title": "<img src=x onerror=alert(1)>",
                                          "steps": [{"action": "<b>a</b>", "expected": "<i>e</i>"}],
                                          "flags": ["<u>f</u>"], "source": "<s>"}],
                           "updates": []}, fh)
            html = mocked.dom("manual")
            check("what a document or Cursor wrote reaches the Manual tests tab as text",
                  "&lt;img src=x onerror=alert(1)&gt;" in html and "<img src=x" not in html
                  and "&lt;b&gt;a&lt;/b&gt;" in html and "&lt;u&gt;f&lt;/u&gt;" in html)
            check("what is not whole is said above the cases, and no updates without existing tests",
                  html.index("the reply was cut off") < html.index("&lt;img src=x")
                  and "no existing tests were read for this job" in html)
            # ---- publish: the tab that writes, against the stand-ins ------------
            conf_store = os.path.join(ROOT, ".workbench", "mock-confluence.json")
            conf_kept = None
            if os.path.isfile(conf_store):
                with io.open(conf_store, encoding="utf-8") as fh:
                    conf_kept = fh.read()
                os.remove(conf_store)
            try:
                code, rc = press("publish-status", {})
                html = mocked.dom("publish")
                check("the Publish tab says what is switched on, and that these are stand-ins",
                      rc == 0 and "Attaching to Jira is ON" in html
                      and "Creating Confluence pages is ON for space(s) QA" in html
                      and "contacts nobody" in html and html.count('role="tab"') == 11, f"rc={rc}")
                check("only this job's own results are offered, by name",
                      '<option value="manual-tests.md">' in html
                      and '<option value="manual">Manual test cases</option>' in html
                      and "cursor.log" not in html[html.index('id="tab-publish"'):])
                code, rc = press("publish-attach", {"--key": "ABC-12", "--file": "manual-tests.md",
                                                    "--confirm-key": "ABC-12"})
                html = mocked.dom("publish")
                check("mock mode: Attach to the issue changes a file on this machine and says so",
                      rc == 0 and f"{JOB}-manual-tests.md is attached to ABC-12" in html
                      and "Nothing left this machine." in html, f"rc={rc}")
                code, rc = press("publish-attach", {"--key": "ABC-12", "--file": "manual-tests.md",
                                                    "--confirm-key": "ABC-13"})
                html = mocked.dom("publish")
                check("a key that is not typed the same twice writes nothing",
                      rc == 2 and "Refused — nothing was written." in html
                      and "given twice" in html, f"rc={rc}")
                code, rc = press("publish-attach", {"--key": "ABC-12", "--file": "cursor.log",
                                                    "--confirm-key": "ABC-12"})
                check("a file that is not one of the results cannot be named", rc == 2)
                code, rc = press("publish-attach", {"--key": "ABC-77", "--file": "manual-tests.md",
                                                    "--confirm-key": "ABC-77", "--expect": "real"})
                html = mocked.dom("publish")
                check("when the page said real and it is a stand-in (or the reverse), nothing is written",
                      rc == 2 and "Nothing was written" in html, f"rc={rc}")
                check("the record of writes is on the page, and asking what is on does not erase the last outcome",
                      "ATTEMPT attach" in html and "DONE attach" in html
                      and "Refused — nothing was written." in html)
                code, rc = press("publish-attach", {"--key": "ABC-12", "--file": "manual-tests.md",
                                                    "--confirm-key": "ABC-12"})
                check("the same file is not attached to the same issue twice", rc == 2)
                page_opts = {"--what": "manual", "--space": "QA", "--parent": "4242",
                             "--title": "Check UI page", "--confirm-title": "Check UI page"}
                code, rc = press("publish-page", page_opts)
                html = mocked.dom("publish")
                check("mock mode: Create the page",
                      rc == 0 and "was created in QA" in html
                      and "Nothing left this machine." in html, f"rc={rc}")
                code, rc = press("publish-page", page_opts)
                html = mocked.dom("publish")
                check("a page with a title the space already has is not created again",
                      rc == 2 and "never edits" in html, f"rc={rc}")
                code, rc = press("publish-page", dict(page_opts, **{"--space": "DEV",
                                                                    "--title": "Other",
                                                                    "--confirm-title": "Other"}))
                check("a space that is not listed is refused", rc == 2)
                written = []
                if os.path.isfile(conf_store):
                    with io.open(conf_store, encoding="utf-8") as fh:
                        written = json.load(fh)
                check("exactly one page was written down by the stand-in",
                      [p.get("title") for p in written] == ["Check UI page"], str(written))

                # ---- the Logs tab: what ran, and what went wrong -----------------
                code, got = mocked.call("/api/client-error", {
                    "message": "<img src=x onerror=alert(1)> is not a function",
                    "source": "app.js", "line": "12", "tab": "publish"})
                check("a fault in the page's own script is taken down", code == 200 and got.get("noted"))
                code, got = mocked.call(f"/api/diag?job={JOB}&which=job")
                record = got.get("text") or ""
                check("every command run for the job is on record, with how it ended",
                      "[publish] START attach " in record and "END exit 0" in record
                      and "[manualtests] START generate" in record, record[-300:])
                check("what a command said as it refused is kept, though its own log was written over",
                      "the end of what it said:" in record
                      and "REFUSED the key must be given twice" in record)
                code, got = mocked.call("/api/diag?which=server")
                server_log = got.get("text") or ""
                check("the server's record has what was started and what was refused, and no polling",
                      "POST /api/start -> 200" in server_log and "PAGE ERROR on tab publish" in server_log
                      and "/api/status" not in server_log.split("PAGE ERROR")[-1])
                code, got = mocked.call(f"/api/diag?job=../../x&which=job")
                check("the record of a job that cannot exist is not read", code == 400)
                code, got = mocked.call("/api/diag?which=run")
                run_log = got.get("text") or ""
                check("the Maven run log explains when Surefire did not refresh the file",
                      code == 200 and "b2b-full-run.log" in run_log and "Surefire" in run_log)
                check("the Maven run log traces why Xray did or did not update",
                      "XRAY TRACE" in run_log and "xray.enabled" in run_log)
                html = mocked.dom("logs")
                check("the Logs tab shows both, as text",
                      "[publish] START" in html and "POST /api/start" in html
                      and "&lt;img src=x onerror=alert(1)&gt;" in html and "<img src=x" not in html
                      and f".workbench/{JOB}/diagnostics.log" in html)
                check("the Logs tab includes the Maven test run log section",
                      "Maven test run log" in html and "b2b-full-run.log" in html)
            finally:
                try:
                    os.remove(conf_store)
                except OSError:
                    pass
                if conf_kept is not None:
                    with io.open(conf_store, "w", encoding="utf-8") as fh:
                        fh.write(conf_kept)
                for name in ("publish-result.json", "publish-status.json", "publish.log"):
                    try:
                        os.remove(os.path.join(job_dir, name))
                    except OSError:
                        pass
            code, got = mocked.call("/api/doc-remove", {"job": JOB, "name": "Booking Rules.md"})
            check("Remove forgets the requirements file", code == 200 and got.get("removed") == 1)
            for name in ("manual-result.json", "manual-pull.json", "manual-inventory.json",
                         "manual-tests.md", "manual-new-cases.csv", "manual-xray-updates.csv",
                         "manual-notes.txt"):
                try:
                    os.remove(os.path.join(job_dir, name))
                except OSError:
                    pass
            for name in ("review.json", "proposed.diff", "code-review.json", "code-review.md",
                         "agent-review.status.json"):
                try:
                    os.remove(os.path.join(job_dir, name))
                except OSError:
                    pass
        finally:
            try:
                os.remove(store)
            except OSError:
                pass
            if kept is not None:
                with io.open(store, "w", encoding="utf-8") as fh:
                    fh.write(kept)
            os.environ["WORKBENCH_MOCK"] = "0"
        check("the Suite box shows for a converted suite, the Migration tab's default",
              'id="agent-suite-field" hidden' not in html and 'id="agent-suite-field"' in html)
    finally:
        srv.terminate()
        search.connect, search.ROOT = saved
        shutil.rmtree(job_dir, ignore_errors=True)
        shutil.rmtree(scratch, ignore_errors=True)
    print(f"\n{sum(results)} of {len(results)} checks passed  (browser: {os.path.basename(browser)})")
    return 0 if results and all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
