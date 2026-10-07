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
directory under `target/agent/<job>/` is the state, which is why a
refresh loses nothing and why every run is reproducible from the CLI.
"""
from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))

_spec = importlib.util.spec_from_file_location(
    "server_jobs", os.path.join(HERE, "jobs.py"))
jobs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(jobs)

ROOT = jobs.ROOT
UI_DIR = os.path.join(HERE, "ui")
MAX_BODY = 2 * 1024 * 1024


class Handler(BaseHTTPRequestHandler):
    server_version = "b2b-agent-ui"

    def log_message(self, fmt, *args):      # quieter than the default
        if "--verbose" in sys.argv:
            super().log_message(fmt, *args)

    # ---- plumbing ----------------------------------------------------
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # This page renders job output; keep it from being framed or
        # sniffed into something executable.
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload, code: int = 200) -> None:
        self._send(code, json.dumps(payload, ensure_ascii=False).encode(),
                   "application/json; charset=utf-8")

    def _fail(self, exc: Exception, code: int = 400) -> None:
        self._json({"error": str(exc), "kind": type(exc).__name__}, code)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        if n > MAX_BODY:
            raise ValueError(f"request body over {MAX_BODY} bytes")
        return json.loads(self.rfile.read(n).decode("utf-8"))

    # ---- routes ------------------------------------------------------
    def do_GET(self):                                    # noqa: N802
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
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
            if u.path == "/api/jobs":
                return self._json(jobs.list_jobs())
            if u.path == "/api/status":
                return self._json(jobs.read_status(q.get("job", ""),
                                                   q.get("runnable", "")))
            if u.path == "/api/log":
                return self._json(jobs.read_log(q.get("job", ""),
                                                q.get("runnable", ""),
                                                int(q.get("offset") or 0)))
            if u.path == "/api/artifact":
                return self._json({"text": jobs.read_artifact(
                    q.get("job", ""), q.get("name", ""))})
            return self._json({"error": "no such route"}, 404)
        except Exception as e:                           # noqa: BLE001
            return self._fail(e)

    def do_POST(self):                                   # noqa: N802
        u = urlparse(self.path)
        try:
            body = self._body()
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
            return self._json({"error": "no such route"}, 404)
        except Exception as e:                           # noqa: BLE001
            return self._fail(e)

    def _intake(self, body: dict) -> dict:
        job = body.get("job", "")
        out = jobs.job_dir(job)                 # validates the id
        os.makedirs(out, exist_ok=True)
        text_file = os.path.join(out, "pasted.txt")
        with io.open(text_file, "w", encoding="utf-8") as fh:
            fh.write(body.get("text") or "")
        options = {"--job": job, "--text-file": text_file}
        links = [l for l in (body.get("links") or []) if str(l).strip()]
        if links:
            options["--link"] = links
        return jobs.start(job, "intake", options)

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

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"b2b agent UI on http://{args.host}:{args.port}")
    print(f"  repository: {ROOT}")
    print(f"  jobs:       target/agent/<job>/")
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
