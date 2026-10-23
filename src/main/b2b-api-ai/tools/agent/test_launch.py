"""The launcher starts this folder's workbench once, and stops only it.

    python tools/agent/test_launch.py

This starts a real server on a free port and stops it again.
"""
from __future__ import annotations

import http.server
import importlib.util
import io
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
_spec = importlib.util.spec_from_file_location("launch_under_test", os.path.join(HERE, "launch.py"))
launch = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(launch)


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    n = s.getsockname()[1]
    s.close()
    return n


def run(*argv) -> tuple:
    out = io.StringIO()
    with redirect_stdout(out):
        rc = launch.main(list(argv))
    return rc, out.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.port = free_port()
        self._env = {k: os.environ.get(k) for k in ("WORKBENCH_PORT", "WORKBENCH_MOCK")}
        os.environ["WORKBENCH_PORT"] = str(self.port)
        os.environ["WORKBENCH_MOCK"] = "1"
        self.addCleanup(self.restore)

    def restore(self):
        there = launch.who(self.port)
        if isinstance(there, dict) and launch.same_folder(there["root"], ROOT):
            launch.end(there["pid"])                     # whatever a failed test left
        for k, v in self._env.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v


class TheServersPython(unittest.TestCase):
    """Seen live on a Mac: the server ran on Homebrew's Python, which
    refuses pip (PEP 668), and Cursor was NOT READY. The project's .venv
    is used, made, or made again."""

    def setUp(self):
        import tempfile
        import shutil
        self.root = tempfile.mkdtemp(prefix="launch-venv-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.calls = []
        self.sdk_in = set()        # the pythons that have cursor-sdk
        self.broken = set()        # the pythons that no longer run

    def run_(self, argv):
        self.calls.append(argv)
        py = argv[0]
        if argv[1:3] == ["-m", "venv"]:
            exe = launch.venv_python(self.root)
            os.makedirs(os.path.dirname(exe), exist_ok=True)
            io.open(exe, "w").close()
            self.broken.discard(exe)
            return 0
        if argv[1:] == ["-c", "import sys"]:
            return 1 if py in self.broken else 0
        if argv[1:] == ["-c", "import cursor_sdk"]:
            return 0 if py in self.sdk_in else 1
        if argv[1:4] == ["-m", "pip", "install"]:
            self.sdk_in.add(py)
            return 0
        return 1

    def choose(self, base="/usr/bin/python3"):
        out = io.StringIO()
        with redirect_stdout(out):
            got = launch.server_python(self.root, base, self.run_)
        return got, out.getvalue()

    def test_a_python_with_the_sdk_and_no_venv_is_used_as_before(self):
        self.sdk_in.add("/usr/bin/python3")
        (py, why), _said = self.choose()
        self.assertEqual(py, "/usr/bin/python3")
        self.assertFalse(os.path.isdir(launch.venv_dir(self.root)), "no .venv made for nothing")

    def test_a_python_that_cannot_have_the_sdk_gets_a_venv_that_sees_its_packages(self):
        (py, why), said = self.choose("/opt/homebrew/bin/python3.15")
        self.assertEqual(py, launch.venv_python(self.root))
        made = next(c for c in self.calls if c[1:3] == ["-m", "venv"])
        self.assertEqual(made[0], "/opt/homebrew/bin/python3.15")
        self.assertIn("--system-site-packages", made, "the converter's packages stay visible")
        self.assertIn(py, self.sdk_in, "cursor-sdk is installed into it")
        self.assertIn("a .venv is made", said)

    def test_a_working_venv_is_used_and_mended_when_it_lacks_the_sdk(self):
        exe = launch.venv_python(self.root)
        os.makedirs(os.path.dirname(exe))
        io.open(exe, "w").close()
        self.sdk_in.add("/usr/bin/python3")
        (py, why), _ = self.choose()
        self.assertEqual(py, exe, "the project's .venv first")
        self.assertIn(exe, self.sdk_in)

    def test_a_venv_that_no_longer_runs_is_made_again(self):
        exe = launch.venv_python(self.root)
        os.makedirs(os.path.dirname(exe))
        io.open(exe, "w").close()
        self.broken.add(exe)
        (py, why), said = self.choose()
        self.assertEqual(py, exe)
        self.assertIn("no longer runs", said)
        self.assertTrue(any(c[1:3] == ["-m", "venv"] for c in self.calls))


class StartAndStop(Base):
    def test_it_starts_once_says_where_and_stops(self):
        self.assertEqual(run("status")[0], 1)
        rc, said = run("start", "--no-browser")
        self.assertEqual(rc, 0, said)
        self.assertIn(f"http://127.0.0.1:{self.port}/", said)
        first = launch.who(self.port)
        self.assertTrue(launch.same_folder(first["root"], ROOT))
        self.assertTrue(launch.alive(first["pid"]))

        rc, said = run("start", "--no-browser")
        self.assertEqual(rc, 0)
        self.assertIn("already running", said)
        self.assertEqual(launch.who(self.port)["pid"], first["pid"], "not a second server")
        rc, said = run("status")
        self.assertEqual(rc, 0)
        self.assertIn(f"process {first['pid']}", said)

        rc, said = run("stop")
        self.assertEqual(rc, 0, said)
        self.assertIn("is stopped", said)
        self.assertIsNone(launch.who(self.port))
        self.assertFalse(launch.alive(first["pid"]))
        rc, said = run("stop")
        self.assertEqual(rc, 0)
        self.assertIn("not running", said)

    def test_what_it_said_as_it_started_is_kept(self):
        run("start", "--no-browser")
        with io.open(launch.CONSOLE, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        self.assertIn("===== started ", text)
        self.assertIn(f"b2b agent UI on http://127.0.0.1:{self.port}", text)
        run("stop")

    def test_a_port_that_is_not_a_port(self):
        for bad in ("80", "abc", "70000"):
            os.environ["WORKBENCH_PORT"] = bad
            with self.assertRaises(SystemExit):
                launch.port()


class SomethingElseOnThePort(Base):
    def serve(self, answer):
        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):                            # noqa: N802
                code, body = answer(self.path)
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass
        srv = http.server.HTTPServer(("127.0.0.1", self.port), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return srv

    def test_another_copys_workbench_is_neither_stopped_nor_started_beside(self):
        self.serve(lambda path: (200, json.dumps({"pid": os.getpid(),
                                                  "root": os.path.join(ROOT, "..", "other-copy")}).encode()))
        rc, said = run("stop")
        self.assertEqual(rc, 1)
        self.assertIn("another copy", said)
        self.assertIn("left alone", said)
        self.assertTrue(launch.alive(os.getpid()), "this test, which was the 'server', lives")
        rc, said = run("start", "--no-browser")
        self.assertEqual(rc, 1)
        self.assertIn("Nothing was started", said)
        rc, said = run("status")
        self.assertEqual(rc, 1)
        self.assertIn("ANOTHER folder", said)

    def test_another_program_or_an_old_workbench_is_left_alone_and_said(self):
        for answer in (lambda path: (404, b'{"error": "no such route"}'),
                       lambda path: (200, b"<html>hello</html>"),
                       lambda path: (200, b'{"pid": "12", "root": ""}')):
            srv = self.serve(answer)
            for command in ("stop", "start"):
                rc, said = run(command, "--no-browser") if command == "start" else run(command)
                self.assertEqual(rc, 1, said)
                self.assertIn("left alone" if command == "stop" else "Nothing was started", said)
            srv.shutdown()
            srv.server_close()

    def test_a_port_held_by_something_that_does_not_speak_http(self):
        s = socket.socket()
        s.bind(("127.0.0.1", self.port))
        s.listen(1)
        self.addCleanup(s.close)
        rc, said = run("start", "--no-browser")
        self.assertEqual(rc, 1)
        self.assertIn("in use by another program", said)


class TheFilesThatCallIt(unittest.TestCase):
    def read(self, name):
        with io.open(os.path.join(ROOT, name), "rb") as fh:
            return fh.read()

    def test_each_launcher_calls_the_launcher_from_its_own_folder(self):
        for name, verb in (("start-workbench.bat", b"launch.py start"),
                           ("stop-workbench.bat", b"launch.py stop"),
                           ("start-workbench.command", b"launch.py start"),
                           ("stop-workbench.command", b"launch.py stop")):
            data = self.read(name)
            self.assertIn(verb, data, name)
            self.assertIn(b'cd /d "%~dp0"' if name.endswith(".bat") else b'cd "$(dirname "$0")"',
                          data, name)

    def test_their_line_endings_are_the_ones_that_run(self):
        for name in ("start-workbench.bat", "stop-workbench.bat"):
            data = self.read(name)
            self.assertEqual(data.count(b"\n"), data.count(b"\r\n"), f"{name}: CRLF throughout")
        for name in ("start-workbench.command", "stop-workbench.command"):
            data = self.read(name)
            self.assertNotIn(b"\r", data, f"{name}: a carriage return breaks the first line")
            self.assertTrue(data.startswith(b"#!/bin/bash\n"), name)
        with io.open(os.path.join(ROOT, ".gitattributes"), encoding="utf-8") as fh:
            rules = fh.read()
        self.assertIn("*.bat text eol=crlf", rules)
        self.assertIn("*.command text eol=lf", rules)


class Requirements(unittest.TestCase):
    """PyYAML (and the rest) were never installed: only cursor-sdk was.
    requirements.txt lists them all; what a Python lacks is installed."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.req = os.path.join(self.root, "requirements.txt")
        with io.open(self.req, "w", encoding="utf-8") as fh:
            fh.write("# a comment line\ncursor-sdk  # import cursor_sdk\nPyYAML  # import yaml -- specs\n"
                     "\nnot-checked\n")
        self.have = {"/py": {"cursor_sdk"}}
        self.calls = []
        self.pip_ok = True
        self.refuse = set()

    def run_(self, argv):
        self.calls.append(argv)
        py = argv[0]
        if argv[1:] == ["-c", "import sys"]:
            return 0
        if argv[1:2] == ["-c"] and argv[2].startswith("import "):
            return 0 if argv[2].split()[1] in self.have.get(py, set()) else 1
        if argv[1:4] == ["-m", "pip", "install"]:
            if not self.pip_ok or py in self.refuse:
                return 1
            mods = {"PyYAML": "yaml", "cursor-sdk": "cursor_sdk"}
            self.have.setdefault(py, set()).update(mods.get(p, p) for p in argv[6:])
            return 0
        if argv[1:3] == ["-m", "venv"]:
            exe = launch.venv_python(self.root)
            os.makedirs(os.path.dirname(exe), exist_ok=True)
            io.open(exe, "w").close()
            self.have[exe] = set(self.have.get(argv[0], set()))
            return 0
        if argv[1:] == ["-c", "import sys"]:
            return 0
        return 1

    def quiet(self, fn, *a):
        with redirect_stdout(io.StringIO()) as out:
            got = fn(*a)
        return got, out.getvalue()

    def test_each_line_names_its_package_and_the_import_that_shows_it(self):
        self.assertEqual(launch.requirements(self.req), [("cursor-sdk", "cursor_sdk"), ("PyYAML", "yaml")])
        shipped = dict(launch.requirements(os.path.join(ROOT, "requirements.txt")))
        for pkg in ("cursor-sdk", "PyYAML", "json-repair", "openpyxl", "pypdf"):
            self.assertIn(pkg, shipped, "the project's requirements.txt")

    def test_only_what_is_missing_is_installed(self):
        left, said = self.quiet(launch.ensure_requirements, "/py", self.req, self.run_)
        self.assertEqual(left, [])
        pip = [c for c in self.calls if c[1:4] == ["-m", "pip", "install"]]
        self.assertEqual(len(pip), 1)
        self.assertEqual(pip[0][-1:], ["PyYAML"], "cursor-sdk was there: not asked for again")
        self.assertIn("installed: PyYAML", said)
        self.calls.clear()
        self.assertEqual(self.quiet(launch.ensure_requirements, "/py", self.req, self.run_)[0], [])
        self.assertFalse([c for c in self.calls if "pip" in c], "nothing missing: pip is not run")

    def test_what_pip_cannot_get_is_said_and_the_start_goes_on(self):
        self.pip_ok = False
        left, said = self.quiet(launch.ensure_requirements, "/py", self.req, self.run_)
        self.assertEqual(left, [("PyYAML", "yaml")])
        self.assertIn("NOT installed: PyYAML", said)
        self.assertIn("-m pip install -r requirements.txt", said)

    def test_a_python_that_refuses_pip_gets_a_venv_with_them(self):
        self.refuse.add("/opt/homebrew/bin/python3")
        self.have["/opt/homebrew/bin/python3"] = {"cursor_sdk"}
        (py, why), said = self.quiet(launch.with_requirements, "/opt/homebrew/bin/python3", "this Python",
                                     self.root, self.run_)
        self.assertEqual(py, launch.venv_python(self.root))
        self.assertIn("yaml", self.have[py])
        self.assertIn("a .venv is made", said)

    def test_the_windows_python_check_has_no_block_a_parenthesis_ends(self):
        # Seen: `if ... ( py -c "...(3, 9)..." )` -- the ")" of "(3, 9)"
        # closed the block, so an installed Python was "not found".
        with io.open(os.path.join(ROOT, "start-workbench.bat"), encoding="utf-8") as fh:
            text = fh.read()
        sub = text[text.index("\n:find_python\n"):]
        for line in sub.splitlines():
            self.assertFalse(line.rstrip().endswith("("), f"a ( ) block in :find_python: {line}")
        self.assertIn("winget install", text)
        with io.open(os.path.join(ROOT, "start-workbench.command"), encoding="utf-8") as fh:
            mac = fh.read()
        self.assertIn("brew\" install python", mac.replace("$BREW", "brew"))
        self.assertIn("sys.version_info >= (3, 9)", mac)


if __name__ == "__main__":
    unittest.main(verbosity=1)
