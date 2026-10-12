"""A record of what the workbench's commands did, for finding out what went wrong.

Every workbench command prints what a person needs and, on an unexpected
error, only the error's TYPE -- its text can quote what was being read,
and the page serves that output. That is the right default for a page and
the wrong one for whoever has to find the fault. This module keeps the
rest, somewhere that is not served as command output:

    target/agent/<job>/diagnostics.log   every command run for the job:
                                         when, with what options, how long,
                                         its exit code, and the full
                                         traceback of every error -- the
                                         ones it handled as well as the
                                         ones it did not
    target/agent/server.log              the web server: requests that
                                         failed, every request that starts
                                         or changes something, errors in
                                         the page's own script

Both are under target/, which is gitignored. Both are appended to and
rolled over at 2 MB (one earlier file is kept, as `.1`).

WHAT IS NOT WRITTEN
-------------------
Secrets. Before a line is written, every credential value in the private
configuration (program_configuration.json), the Cursor key, and anything
that looks like `Bearer <token>` or an `Authorization:` header is
replaced with `<redacted>`. An unreadable configuration means the values
cannot be known, and then only the pattern-based redaction applies -- the
log says so on its first line.

SWITCHING IT
------------
On unless the environment says otherwise:

    WORKBENCH_LOG=off      nothing is written
    WORKBENCH_LOG=debug    also every request the page makes while polling

This module never raises: a log that cannot be written must not be the
reason a command fails.
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import threading
import time
import traceback
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                     # .../b2b-api-ai
MAX_BYTES = 2 * 1024 * 1024
MAX_LINE = 4000
JOB_LOG = "diagnostics.log"
SERVER_LOG = "server.log"
_JOB_RX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SECRET_KEY_RX = re.compile(r"(?i)(pass|secret|token|key|credential|c_sec|client_id|(^|[_.\-])pat$)")
_BEARER_RX = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{8,}")
_AUTH_RX = re.compile(r"(?i)((?:authorization|x-api-key|api[_-]?key|password|passwd|secret)"
                      r"[\"']?\s*[:=]\s*[\"']?(?:(?:basic|bearer|token|negotiate)\s+)?)"
                      r"(?!<redacted>)[^\"'\s,;}]{6,}")

_LOCK = threading.Lock()
_STATE = {"path": "", "module": "", "seen": {}, "secrets": None, "secrets_note": ""}


def level() -> str:
    v = os.environ.get("WORKBENCH_LOG", "").strip().lower()
    if v in ("off", "0", "false", "no"):
        return "off"
    return "debug" if v == "debug" else "on"


def job_log(job: str, root: str = "") -> str:
    """Where a job's record is kept, or "" for something that is not a job id."""
    if not _JOB_RX.match(job or "") or job in (".", ".."):
        return ""
    return os.path.join(root or ROOT, "target", "agent", job, JOB_LOG)


def server_log(root: str = "") -> str:
    return os.path.join(root or ROOT, "target", "agent", SERVER_LOG)


# ---- what must not be written ----------------------------------------------

def _config_values(root: str) -> tuple:
    """(values, note). The credential values of the private configuration."""
    out, note = set(), ""
    for rel in (os.path.join("src", "main", "resources", "program_configuration.json"),
                os.path.join("tools", "ra_converter", "cursor_agent.json")):
        path = os.path.join(root, rel)
        if not os.path.isfile(path):
            continue
        try:
            with io.open(path, encoding="utf-8-sig") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            note = (f"{os.path.basename(path)} could not be read: its values are not "
                    f"known, so only token-shaped text is redacted here")
            continue

        def walk(node, key=""):
            if isinstance(node, dict):
                for k, v in node.items():
                    walk(v, str(k))
            elif isinstance(node, list):
                for v in node:
                    walk(v, key)
            elif isinstance(node, str) and len(node.strip()) >= 6 and _SECRET_KEY_RX.search(key):
                v = node.strip()
                if not v.startswith(("${", "/", "<")):
                    out.add(v)
        walk(data)
    env = os.environ.get("CURSOR_API_KEY", "").strip()
    if len(env) >= 6:
        out.add(env)
    return sorted(out, key=len, reverse=True), note


def redact(text: str, root: str = "") -> str:
    """`text` with credentials taken out. Never raises."""
    try:
        if _STATE["secrets"] is None:
            _STATE["secrets"], _STATE["secrets_note"] = _config_values(root or ROOT)
        for value in _STATE["secrets"]:
            if value in text:
                text = text.replace(value, "<redacted>")
            # ...and as it is written inside a JSON string.
            esc = json.dumps(value)[1:-1]
            if esc != value and esc in text:
                text = text.replace(esc, "<redacted>")
        text = _BEARER_RX.sub(lambda m: m.group(1) + "<redacted>", text)
        return _AUTH_RX.sub(lambda m: m.group(1) + "<redacted>", text)
    except Exception:                                    # noqa: BLE001
        return "<a line that could not be checked for secrets was not written>"


# ---- writing ----------------------------------------------------------------

def _append(path: str, lines: list, root: str = "") -> None:
    if not path or level() == "off":
        return
    try:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        out = []
        for line in lines:
            for part in str(line).splitlines() or [""]:
                part = redact(part, root)
                if len(part) > MAX_LINE:
                    part = part[:MAX_LINE] + f" ... ({len(part) - MAX_LINE} more characters)"
                out.append(f"{stamp}  {part}\n")
        with _LOCK:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            fresh = not os.path.isfile(path)
            try:
                if not fresh and os.path.getsize(path) > MAX_BYTES:
                    os.replace(path, path + ".1")
                    fresh = True
            except OSError:
                pass
            with io.open(path, "a", encoding="utf-8", errors="replace") as fh:
                if fresh and _STATE["secrets_note"]:
                    fh.write(f"{stamp}  NOTE {_STATE['secrets_note']}\n")
                fh.writelines(out)
    except Exception:                                    # noqa: BLE001
        pass


def _argv_text(argv) -> str:
    parts = []
    for a in argv:
        a = str(a)
        parts.append(a if len(a) <= 200 else a[:200] + f"...({len(a)} chars)")
    return " ".join(parts)


def _job_from(argv) -> str:
    argv = list(argv)
    for i, a in enumerate(argv):
        if a == "--job" and i + 1 < len(argv):
            return str(argv[i + 1])
        if str(a).startswith("--job="):
            return str(a)[6:]
    return ""


def job_note(job: str, lines, root: str = "") -> None:
    """Lines in a job's record, written by something other than the
    command itself (the runner that started it)."""
    path = job_log(job, root)
    if path and os.path.isdir(os.path.dirname(path)):
        _append(path, [lines] if isinstance(lines, str) else list(lines), root)


def note(message: str, module: str = "") -> None:
    """One line in the current command's record. Nothing, outside a command."""
    if _STATE["path"]:
        _append(_STATE["path"], [f"[{module or _STATE['module']}] {message}"])


STEP_MARK = "==> "
_LAST_STEP = [""]


def step(message) -> None:
    """One plain line saying what the command is doing NOW, for a person
    watching the page: "Asking Jira", "Reading the hand-written tests".

    It is an ordinary line of the command's output with a mark in front,
    so it is in the log like everything else; the page lifts the marked
    lines out and shows the latest beside the button. Said once: a loop
    that asks Jira forty times says "Asking Jira" one time. Never the
    reason a command fails.
    """
    text = " ".join(str(message).split())[:200]
    if not text or text == _LAST_STEP[0]:
        return
    _LAST_STEP[0] = text
    note(f"step: {text}")                                # kept, whether or not it fails
    for line in (text, text.encode("ascii", "replace").decode("ascii")):
        try:
            print(STEP_MARK + line, flush=True)
            return
        except (UnicodeError, OSError, ValueError):
            continue


def trace(where: str = "", module: str = "") -> None:
    """Call inside `except`: the error being handled, with its traceback.

    A command that handles an error and carries on has, as far as its own
    output goes, only said the error's type. Here is the rest. The same
    error at the same place is written five times in one run and then
    counted: a loop that fails two hundred times says so once.
    """
    if not _STATE["path"]:
        return
    try:
        etype, exc, tb = sys.exc_info()
        if etype is None:
            return
        last = traceback.extract_tb(tb)[-1] if tb else None
        spot = (etype.__name__, last.filename if last else "", last.lineno if last else 0)
        n = _STATE["seen"][spot] = _STATE["seen"].get(spot, 0) + 1
        head = (f"[{module or _STATE['module']}] HANDLED {etype.__name__}"
                + (f" while {where}" if where else ""))
        if n > 5:
            if n in (6, 50, 500):
                _append(_STATE["path"], [f"{head} -- the same error at the same place, "
                                         f"{n} times so far; not written out again"])
            return
        _append(_STATE["path"], [head] + traceback.format_exception(etype, exc, tb))
    except Exception:                                    # noqa: BLE001
        pass


class _Tee:
    """Standard output, and a memory of the last lines written to it: what
    a command said as it failed is the first thing anyone asks for."""

    def __init__(self, stream, keep: int = 30):
        self._stream, self._keep, self.lines, self._part = stream, keep, [], ""

    def write(self, text):
        try:
            self._part += str(text)
            *whole, self._part = self._part.split("\n")
            if whole:
                self.lines = (self.lines + whole)[-self._keep:]
        except Exception:                                # noqa: BLE001
            pass
        return self._stream.write(text)

    def said(self) -> list:
        return [l for l in self.lines + ([self._part] if self._part else []) if l.strip()]

    def __getattr__(self, name):
        return getattr(self._stream, name)


def _ended(path: str, module: str, rc, started: float, tee, root: str) -> None:
    lines = [f"[{module}] END exit {rc!r} after {time.time() - started:.1f}s"]
    if rc not in (0, None) and tee is not None and tee.said():
        lines.append(f"[{module}] the end of what it said:")
        lines += ["    " + l for l in tee.said()]
    _append(path, lines, root)


def run(module: str, main, argv=None, root: str = ""):
    """Run a command's `main()` with its start, its end and anything that
    escapes it on record. Returns what `main` returned."""
    argv = list(sys.argv[1:] if argv is None else argv)
    path = job_log(_job_from(argv), root) or ""
    if path and not os.path.isdir(os.path.dirname(path)):
        path = ""                    # a mistyped job id does not become a job by being logged
    _STATE.update(path=path, module=module, seen={})
    started = time.time()
    _append(path, [f"[{module}] START {_argv_text(argv)}  (pid {os.getpid()}, "
                   f"python {sys.version.split()[0]}, {sys.platform})"], root)
    tee, real = None, sys.stdout
    if path and level() != "off":
        tee = sys.stdout = _Tee(real)
    try:
        rc = main()
    except SystemExit as e:
        _ended(path, module, e.code, started, tee, root)
        raise
    except BaseException:
        _append(path, [f"[{module}] CRASH after {time.time() - started:.1f}s"]
                + traceback.format_exc().splitlines(), root)
        raise
    finally:
        if tee is not None and sys.stdout is tee:
            sys.stdout = real
    _ended(path, module, rc, started, tee, root)
    return rc


# ---- the web server ---------------------------------------------------------

_QUIET = ("/api/log", "/api/status", "/api/mock", "/api/whoami", "/api/artifact", "/api/jobs", "/api/docs",
          "/api/spec", "/api/uploads", "/api/runnables", "/api/diag")


def request(method: str, path: str, status: int, seconds: float, root: str = "") -> None:
    """One request. What the page asks for over and over while it waits is
    left out unless it failed (or WORKBENCH_LOG=debug)."""
    try:
        bare = path.split("?", 1)[0]
        ok = 200 <= int(status) < 400
        if ok and level() != "debug" and (method == "GET" and (
                bare in _QUIET or not bare.startswith("/api/"))):
            return
        _append(server_log(root), [f"{method} {path[:300]} -> {status} in {seconds * 1000:.0f} ms"],
                root)
    except Exception:                                    # noqa: BLE001
        pass


def server_trace(what: str, root: str = "") -> None:
    """Call inside `except` in the server: the error, with its traceback."""
    try:
        _append(server_log(root), [f"ERROR {what}"] + traceback.format_exc().splitlines(), root)
    except Exception:                                    # noqa: BLE001
        pass


def server_note(message: str, root: str = "") -> None:
    _append(server_log(root), [message], root)


def tail(path: str, lines: int = 400, max_bytes: int = 400_000) -> str:
    """The end of a log, for the page."""
    try:
        size = os.path.getsize(path)
        with io.open(path, "rb") as fh:
            fh.seek(max(0, size - max_bytes))
            text = fh.read().decode("utf-8", "replace")
    except OSError:
        return ""
    rows = text.splitlines()
    if size > max_bytes and rows:
        rows = rows[1:]                               # the first is probably cut
    return "\n".join(rows[-max(1, int(lines)):])
