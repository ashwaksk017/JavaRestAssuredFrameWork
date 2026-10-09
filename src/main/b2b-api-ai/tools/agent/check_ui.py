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
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
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


def find_browser() -> str:
    for path in BROWSERS:
        if os.path.isfile(path):
            return path
    for name in ("msedge", "chrome", "google-chrome", "chromium"):
        found = shutil.which(name)
        if found:
            return found
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

    def call(self, path: str, payload=None) -> tuple:
        req = urllib.request.Request(self.base + path)
        data = None
        if payload is not None:
            data = json.dumps(payload).encode()
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, data, timeout=20) as r:
                return r.status, json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8", "replace") or "{}")

    def dom(self, tab: str, job: str = JOB) -> str:
        done = subprocess.run(
            [self.browser, "--headless=new", "--disable-gpu", "--no-first-run",
             "--disable-extensions", "--virtual-time-budget=9000", "--dump-dom",
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

    job_dir = os.path.join(ROOT, "target", "agent", JOB)
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
        check("server -> command -> result file", code == 200 and state.get("exit_code") == 0
              and json.loads(text or "{}").get("kind") == "runs", str(state))
        html = page.dom("failures")
        check("the Failures tab shows that result", "Recorded runs" in html)

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
        shutil.copy(os.path.join(scratch, "target", "agent", JOB, failure_history.JOB_RESULT),
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
            src = os.path.join(scratch, "target", "agent", JOB, search.JOB_RESULT)
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
        shown = os.path.join(scratch, "target", "agent", JOB, defects.RESULT)

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
        html = page.dom("convert")
        check("tab 2 still builds its form from the server", 'data-opt="--input"' in html)
        html = page.dom("new")
        check("tab 1 still has its buttons", 'id="run-intake"' in html and 'id="run-design"' in html)
        html = page.dom("agent")
        check("tab 3 still opens", 'id="tab-agent" class="tab" role="tabpanel" hidden' not in html)
        check("the title, and no mock banner while the stand-ins are off",
              "<h1>AI Enabled Test WorkBench</h1>" in html
              and 'id="mock-banner" class="mockbar" hidden' in html)

        # ---- mock mode, through a server that has it switched on ----------------
        srv.terminate()
        store = os.path.join(ROOT, "target", "agent", "mock-jira.json")
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
                    state = mocked.call(f"/api/status?job={JOB}&runnable={runnable}")[1]
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
        finally:
            try:
                os.remove(store)
            except OSError:
                pass
            if kept is not None:
                with io.open(store, "w", encoding="utf-8") as fh:
                    fh.write(kept)
            os.environ["WORKBENCH_MOCK"] = "0"
        check("a field the page hid stays hidden (the Suite box, for a new test)",
              'id="agent-suite-field" hidden' in html or 'id="agent-suite-field" hidden=""' in html)
    finally:
        srv.terminate()
        search.connect, search.ROOT = saved
        shutil.rmtree(job_dir, ignore_errors=True)
        shutil.rmtree(scratch, ignore_errors=True)
    print(f"\n{sum(results)} of {len(results)} checks passed  (browser: {os.path.basename(browser)})")
    return 0 if results and all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
