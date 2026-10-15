"""The UI layer: one local page, three tabs, over the existing tools.

    python tools/agent/server.py            # http://127.0.0.1:8787
    python tools/agent/server.py --port 9000 --host 127.0.0.1

Tab 1 reads a pasted story and decides create/update/upstream. Tab 2 is
a form over the converter's CLI. Tab 3 is the agent loop, and is the
only one that writes code.

BOUND TO LOOPBACK BY DEFAULT, ON PURPOSE
----------------------------------------
This process starts commands that write to the repository. Serving it on
0.0.0.0 puts that on the network with no authentication, so --host must
be given explicitly to do it and the banner says what that means. There
is no login here; if this needs to be reachable by a team, put it behind
something that has one.

Standard library only: no framework, no build step, no database. The job
directory under `.workbench/<job>/` is the state, which is why a
refresh loses nothing and why every run is reproducible from the CLI.
"""
from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
import socket
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))

_spec = importlib.util.spec_from_file_location(
    "server_jobs", os.path.join(HERE, "jobs.py"))
jobs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(jobs)

ROOT = jobs.ROOT
diag = jobs.diag
_PAGE_ERRORS = [0]               # how many the page has reported to this process
UI_DIR = os.path.join(HERE, "ui")
MAX_BODY = 8 * 1024 * 1024       # an OpenAPI document is pasted whole
DOWNLOADS = {"design.md": "text/markdown", "design.json": "application/json",
             "test-cases.csv": "text/csv", "xray.csv": "text/csv",
             "code-review.md": "text/markdown", "manual-tests.md": "text/markdown",
             "manual-new-cases.csv": "text/csv", "manual-xray-updates.csv": "text/csv"}


def given_links(job_dir: str) -> list:
    """The links a job was handed on its first read, from intake.json: the
    story and the Confluence pages given with it, not the pages the story
    itself named (marked from_story) -- those are found again."""
    try:
        with io.open(os.path.join(job_dir, "intake.json"), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    out = []
    for g in (data.get("links") or []) if isinstance(data, dict) else []:
        if not isinstance(g, dict) or g.get("from_story"):
            continue
        link = str(g.get("link") or "").strip()
        if link and link not in out and len(out) < 40:
            out.append(link)
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = "b2b-agent-ui"
    # A client that says it will send a body and then sends nothing would
    # otherwise hold a thread for as long as it likes.
    timeout = 120

    def log_message(self, fmt, *args):      # quieter than the default
        if "--verbose" in sys.argv:
            super().log_message(fmt, *args)

    # ---- the record (.workbench/server.log) ------------------------
    def send_response(self, code, message=None):
        self._status = code
        super().send_response(code, message)

    def _handled(self, method: str, route) -> None:
        import time as _time
        started = _time.time()
        self._status = 0
        try:
            route()
        finally:
            diag.request(method, self.path, self._status, _time.time() - started, ROOT)

    def do_GET(self):                                    # noqa: N802
        self._handled("GET", self._get)

    def do_POST(self):                                   # noqa: N802
        self._handled("POST", self._post)

    # ---- plumbing ----------------------------------------------------
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # This page renders job output; keep it from being framed or
        # sniffed into something executable.
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        # Nothing here is worth caching, and a cached app.js is the old
        # page running against a pulled server: every answer is fresh.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload, code: int = 200) -> None:
        self._send(code, json.dumps(payload, ensure_ascii=False).encode(),
                   "application/json; charset=utf-8")

    def _fail(self, exc: Exception, code: int = 400) -> None:
        # A refusal (bad input, a guard) is one line. Anything else is a
        # fault of this program, and its traceback is what finds it.
        if isinstance(exc, (ValueError, PermissionError, RuntimeError)):
            diag.server_note(f"REFUSED {self.command} {self.path[:200]}: "
                             f"{type(exc).__name__}: {str(exc)[:300]}", ROOT)
        else:
            diag.server_trace(f"{self.command} {self.path[:200]}", ROOT)
        self._json({"error": str(exc), "kind": type(exc).__name__}, code)

    def _guard(self, ctype_wanted: str = "application/json") -> None:
        """Refuse requests a web page could have made on the user's behalf.

        This process starts commands that write to the repository, and
        it listens on a port any page in the browser can reach. Two
        things made that exploitable:

        * `Content-Type: text/plain` is a SIMPLE request -- no CORS
          preflight -- and the body was parsed as JSON regardless of the
          declared type. A form on any site could therefore start a
          convert, including `--clean`. Requiring the JSON content type
          forces a preflight, which cross-origin cannot pass.
        * no `Host` check, so a domain resolving to 127.0.0.1 (DNS
          rebinding) was same-origin as far as the browser was
          concerned.

        None of this makes the server safe to expose. It closes the
        drive-by path; `--host` still prints a warning.
        """
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        if host.lower() not in ("127.0.0.1", "localhost", "::1", ""):
            raise PermissionError(
                f"Host header {host!r} is not loopback. Refusing -- a name "
                f"that resolves to 127.0.0.1 is how a page in the browser "
                f"reaches a local server it should not.")
        origin = (self.headers.get("Origin") or "").strip()
        if origin and urlparse(origin).hostname not in (
                "127.0.0.1", "localhost", "::1"):
            raise PermissionError(
                f"cross-origin request from {origin}. This API starts "
                f"commands; it answers only its own page.")
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
        if ctype.lower() != ctype_wanted:
            raise PermissionError(
                f"POST needs Content-Type: {ctype_wanted}. A text/plain "
                "body is a simple request that skips the CORS preflight, "
                "which is exactly how a page on another site would reach "
                "this.")

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        if n > MAX_BODY:
            raise ValueError(f"request body over {MAX_BODY} bytes")
        return json.loads(self.rfile.read(n).decode("utf-8"))

    # ---- routes ------------------------------------------------------
    def _get(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            # Host only: a GET reads, and the page itself sends no
            # Content-Type. Rebinding still has to be refused.
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
            if host.lower() not in ("127.0.0.1", "localhost", "::1", ""):
                raise PermissionError(
                    f"Host header {host!r} is not loopback")
            if u.path in ("/", "/index.html"):
                return self._file("index.html", "text/html; charset=utf-8")
            if u.path == "/app.js":
                return self._file("app.js", "text/javascript; charset=utf-8")
            if u.path == "/app.css":
                return self._file("app.css", "text/css; charset=utf-8")
            if u.path == "/api/runnables":
                return self._json({k: {"label": v["label"],
                                       "options": v["options"]}
                                   for k, v in jobs.RUNNABLES.items()})
            if u.path == "/api/converted-suites":
                return self._json({"suites": jobs.converted_suites()})
            if u.path == "/api/repo":
                # The page is told THAT there is a remote, never its address.
                info = jobs.repo.info(ROOT)
                info["has_remote"] = bool(info.pop("remote_url", ""))
                return self._json(info)
            if u.path == "/api/whoami":
                # Which process this is and which folder it serves, so that
                # the launcher stops THIS workbench and no other program
                # that happens to hold the port.
                return self._json({"pid": os.getpid(), "root": ROOT})
            if u.path == "/api/jobs":
                return self._json(jobs.list_jobs())
            if u.path == "/api/workbench-job":
                return self._json(jobs.workbench_job())
            if u.path == "/api/workflow":
                return self._json(jobs.workflow_status(q.get("job", "")))
            if u.path == "/api/settings":
                # What is set -- never a token's value.
                return self._json(jobs.settings.status())
            if u.path == "/api/suite-groups":
                # Which suites each designed case's test runs in.
                sm = jobs.casereview._suitemap()
                mapping = sm.load()
                return self._json({"cases": sm.for_job(jobs.job_dir(q.get("job", "")), mapping),
                                   "problem": mapping.get("problem", "")})
            if u.path == "/api/uploads":
                return self._json({"files": jobs.list_uploads()})
            if u.path == "/api/spec":
                return self._json({"spec": jobs.spec_state(q.get("job", ""))})
            if u.path == "/api/diag":
                return self._json({"text": jobs.read_diag(
                    q.get("job", ""), q.get("which", "job"))})
            if u.path == "/api/docs":
                return self._json({"files": jobs.list_docs(q.get("job", ""))})
            if u.path == "/api/mock":
                # Which services are stand-ins right now. Read on every
                # request: the file can be edited while the page is open.
                return self._json(jobs.mock_state())
            if u.path == "/api/status":
                return self._json(jobs.read_status(q.get("job", ""),
                                                   q.get("runnable", "")))
            if u.path == "/api/log":
                return self._json(jobs.read_log(q.get("job", ""),
                                                q.get("runnable", ""),
                                                int(q.get("offset") or 0)))
            if u.path == "/api/artifact":
                out = {"text": jobs.read_artifact(q.get("job", ""), q.get("name", ""))}
                if q.get("name") == "code-review.json":
                    # What the review must still be about, for the page to
                    # say when it is not.
                    out["diff_sha256"] = jobs.diff_sha(q.get("job", ""))
                return self._json(out)
            if u.path == "/api/download":
                return self._download(q.get("job", ""), q.get("name", ""))
            return self._json({"error": "no such route"}, 404)
        except PermissionError as e:
            return self._fail(e, 403)
        except Exception as e:                           # noqa: BLE001
            return self._fail(e)

    def _download(self, job: str, name: str) -> None:
        """A design artifact as a file. The same allowlist as /api/artifact,
        narrowed to the four a person takes away."""
        if name not in DOWNLOADS:
            raise ValueError(f"{name!r} cannot be downloaded")
        text = jobs.read_artifact(job, name)
        if not text:
            return self._json({"error": f"{name} does not exist for this job"}, 404)
        body = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", DOWNLOADS[name] + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Disposition",
                         f'attachment; filename="{jobs.intake.safe_job(job)}-{name}"')
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _post(self):
        u = urlparse(self.path)
        try:
            if u.path == "/api/upload":
                # The one route whose body is a file, not JSON. Its content
                # type is not a "simple" one either, so a page on another
                # site still cannot send it without a preflight it fails.
                self._guard("application/octet-stream")
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                return self._json(jobs.save_upload(
                    q.get("name", ""), self.rfile,
                    int(self.headers.get("Content-Length") or 0)))
            if u.path == "/api/doc-upload":
                self._guard("application/octet-stream")
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                return self._json({"file": jobs.save_doc(
                    q.get("job", ""), q.get("name", ""), self.rfile,
                    int(self.headers.get("Content-Length") or 0))})
            if u.path == "/api/spec-upload":
                self._guard("application/octet-stream")
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                return self._json({"spec": jobs.save_spec(
                    q.get("job", ""), q.get("name", ""), self.rfile,
                    int(self.headers.get("Content-Length") or 0))})
            self._guard()
            body = self._body()
            if u.path == "/api/client-error":
                # The page's own script failed. Written down, a bounded
                # number of times: a page stuck in a loop must not fill
                # the log.
                _PAGE_ERRORS[0] += 1
                if _PAGE_ERRORS[0] <= 200:
                    words = lambda k, n: " ".join(str(body.get(k) or "").split())[:n]
                    diag.server_note(f"PAGE ERROR on tab {words('tab', 20) or '?'}: "
                                     f"{words('message', 400)} "
                                     f"({words('source', 120)}:{words('line', 8)})", ROOT)
                return self._json({"noted": _PAGE_ERRORS[0] <= 200})
            if u.path == "/api/doc-remove":
                # One file, by name. "All of them" is not something a
                # missing field should mean.
                if not str(body.get("name") or "").strip():
                    raise ValueError("name the file to remove")
                return self._json({"removed": jobs.remove_doc(
                    body.get("job", ""), str(body["name"]))})
            if u.path == "/api/manual":
                return self._json(self._manual(body))
            if u.path == "/api/repo-checkout":
                # Switches the working tree: clean tree, no job running, the
                # branch named twice. Refusals come back as one line.
                try:
                    lock = jobs.repo_lock("checkout from the page")
                    return self._json(jobs.repo.checkout(
                        ROOT, str(body.get("branch") or ""), str(body.get("confirm") or ""),
                        lock=lock))
                except jobs.repo.Refused as e:
                    raise ValueError(str(e)) from None
            if u.path == "/api/design-clear":
                # The design and its kept replies, out of the job (kept in a
                # folder of their own); refused while a design runs.
                return self._json(jobs.clear_design(body.get("job", "")))
            if u.path == "/api/agent-dismiss":
                # The last outcome, put away: only one that left nothing in
                # the tree; refused while an agent command runs.
                return self._json(jobs.dismiss_review(body.get("job", "")))
            if u.path == "/api/migration-ask":
                # A request from the Migration from ReadyAPI tab: written as the
                # job's brief and plan, or added to the change already waiting.
                return self._json(jobs.migration_ask(body.get("job", ""), body.get("scope", ""),
                                                     body.get("suite", ""), body.get("text", "")))
            if u.path == "/api/settings":
                return self._json(jobs.settings_save(body.get("changes") or {}))
            if u.path == "/api/workflow":
                return self._json(jobs.workflow_act(body.get("job", ""), str(body.get("action") or "")))
            if u.path == "/api/settings-check":
                # Asks Jira who the saved token belongs to; the answer, never the token.
                conn = jobs.intake.jira_search.connect()
                return self._json({"jira": jobs.intake.token_verdict(conn) or "it could not be asked"})
            if u.path == "/api/workbench-ask":
                # A change to the workbench itself: its own job, and only
                # while nothing else runs (jobs.workbench_ask).
                return self._json(jobs.workbench_ask(body.get("text")))
            if u.path == "/api/agent-followup":
                # What the reviewer asks Cursor to change, kept with the job
                # under a fixed name; the command reads it from there.
                out = jobs.job_dir(body.get("job", ""))
                text = str(body.get("text") or "")
                if not text.strip():
                    raise ValueError("write what Cursor should change")
                if len(text) > 20000:
                    raise ValueError("that is more than 20,000 characters; keep the ask short")
                os.makedirs(out, exist_ok=True)
                with io.open(os.path.join(out, "agent-followup.txt"), "w", encoding="utf-8") as fh:
                    fh.write(text)
                return self._json(jobs.start(body.get("job", ""), "agent-followup",
                                             {"--job": body.get("job", "")}))
            if u.path == "/api/case-review":
                # A person's decision on proposed cases: validated field by
                # field in casereview.py, written only under the job.
                out = jobs.job_dir(body.get("job", ""))
                fields = {k: body.get(k) for k in ("state", "automate", "title", "steps", "note", "groups")
                          if k in body}
                what = str(body.get("what") or "")
                return self._json({"written": jobs.casereview.update(
                    out, what, body.get("ids") or body.get("id"),
                    known=jobs.casereview.current_ids(out, what), **fields)})
            if u.path == "/api/spec-clear":
                return self._json({"removed": jobs.spec_clear(body.get("job", ""))})
            if u.path == "/api/upload-clear":
                return self._json({"removed": jobs.clear_uploads()})
            if u.path == "/api/start":
                return self._json(jobs.start(body.get("job", ""),
                                             body.get("runnable", ""),
                                             body.get("options") or {}))
            if u.path == "/api/stop":
                return self._json({"stopped": jobs.stop(
                    body.get("job", ""), body.get("runnable", ""))})
            if u.path == "/api/intake":
                # Pasted text goes in a file rather than on a command
                # line: argv has a length limit and a story does not.
                return self._json(self._intake(body))
            if u.path == "/api/design":
                return self._json(self._design(body))
            return self._json({"error": "no such route"}, 404)
        except PermissionError as e:
            return self._fail(e, 403)
        except Exception as e:                           # noqa: BLE001
            return self._fail(e)

    def _intake(self, body: dict) -> dict:
        job = body.get("job", "")
        out = jobs.job_dir(job)                 # validates the id
        os.makedirs(out, exist_ok=True)
        if body.get("again"):
            # The story changed (or the page was reloaded): read again with
            # what this job was given the first time -- the links it was
            # handed, and the pasted text and page still in the job folder.
            # Links the story itself named are not repeated: the read finds
            # them again, and the story may name others now.
            links = given_links(out)
            pasted = os.path.join(out, "pasted.txt")
            if not links and not (os.path.isfile(pasted) and os.path.getsize(pasted) > 0):
                raise ValueError("this job has nothing to read again: no link and no pasted text "
                                 "were kept from its first read")
            options = {"--job": job, "--pasted": True}
            if links:
                options["--link"] = links
            return jobs.start(job, "intake", options)
        text_file = os.path.join(out, "pasted.txt")
        with io.open(text_file, "w", encoding="utf-8") as fh:
            fh.write(body.get("text") or "")
        options = {"--job": job, "--pasted": True}
        # A page pasted because the fetcher could not read it. The file is
        # written when there is text and removed when there is none, so an
        # earlier paste is not read again unseen.
        pages_file = os.path.join(out, "pasted-pages.txt")
        if str(body.get("pages") or "").strip():
            with io.open(pages_file, "w", encoding="utf-8") as fh:
                fh.write(str(body.get("pages")))
        elif os.path.isfile(pages_file):
            os.remove(pages_file)
        links = [l for l in (body.get("links") or []) if str(l).strip()]
        if links:
            options["--link"] = links
        return jobs.start(job, "intake", options)

    def _design(self, body: dict) -> dict:
        """Pasted material goes into the job directory under fixed names,
        which is where the command looks; it is given no path. A box left
        empty removes its file, so an earlier run's specification is not
        used again without anyone seeing it."""
        job = body.get("job", "")
        out = jobs.job_dir(job)                 # validates the id
        os.makedirs(out, exist_ok=True)
        options = {"--job": job}
        if str(body.get("refine") or "").strip():
            # Refine with Cursor: the cases as they are, and the request,
            # as the design's notes; always asked afresh.
            body = dict(body, fresh=True,
                        notes=jobs.refine_notes(job, body["refine"]) + "\n\n" + str(body.get("notes") or ""))
        for field in ("swagger", "requirements", "notes"):
            path = os.path.join(out, f"design-{field}.txt")
            text = body.get(field) or ""
            if str(text).strip():
                with io.open(path, "w", encoding="utf-8") as fh:
                    fh.write(str(text))
            elif os.path.isfile(path):
                os.remove(path)
        for field, option in (("service", "--service"), ("speed", "--speed"),
                              ("mode", "--mode")):
            if str(body.get(field) or "").strip():
                options[option] = str(body[field]).strip()
        if body.get("fresh") is True:
            options["--fresh"] = True
        if body.get("whole_spec") is True:
            options["--whole-spec"] = True
        return jobs.start(job, "agent-design", options)

    def _manual(self, body: dict) -> dict:
        """The notes go into the job directory under a fixed name, which is
        where the command looks; it is given no path. An empty box removes
        the file, so an earlier run's notes are not used again unseen."""
        job = body.get("job", "")
        out = jobs.job_dir(job)                 # validates the id
        os.makedirs(out, exist_ok=True)
        path = os.path.join(out, "manual-notes.txt")
        text = str(body.get("notes") or "")
        if text.strip():
            with io.open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
        elif os.path.isfile(path):
            os.remove(path)
        options = {"--job": job}
        for field, option in (("app", "--app"), ("speed", "--speed")):
            if str(body.get(field) or "").strip():
                options[option] = str(body[field]).strip()
        return jobs.start(job, "manual-generate", options)

    def _file(self, name: str, ctype: str) -> None:
        p = os.path.join(UI_DIR, name)
        if not os.path.isfile(p):
            return self._json({"error": f"{name} is missing"}, 404)
        with io.open(p, "rb") as fh:
            self._send(200, fh.read(), ctype)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--host", default="127.0.0.1",
                    help="loopback by default; this process writes to the "
                         "repository and has no authentication")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)
    moved = diag.migrate_state(ROOT)
    if moved:
        print(f"  moved {len(moved)} entr{'y' if len(moved) == 1 else 'ies'} from target/agent "
              f"to {diag.STATE_REL}/ (mvn clean no longer reaches them)")

    # On Windows the default (SO_REUSEADDR) lets a second server bind a port
    # an earlier one still holds, and the EARLIER one keeps answering: the
    # page then talks to old code while the console shows a fresh start.
    class Server(ThreadingHTTPServer):
        allow_reuse_address = os.name != "nt"

        def server_bind(self):
            if os.name == "nt":
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            super().server_bind()

    try:
        srv = Server((args.host, args.port), Handler)
    except OSError:
        print(f"Port {args.port} on {args.host} is already in use -- most likely an "
              f"earlier workbench server that was never stopped. Stop it first "
              f"(or pass --port), or the page would keep talking to the old one.")
        return 1
    print(f"b2b agent UI on http://{args.host}:{args.port}")
    print(f"  repository: {ROOT}")
    print(f"  jobs:       .workbench/<job>/")
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print("  WARNING: not bound to loopback. This process starts "
              "commands that write to the repository and has NO "
              "authentication. Put it behind something that does.")
    print("  Ctrl-C to stop")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
