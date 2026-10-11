"""Start and stop the workbench without keeping a terminal open.

    python tools/agent/launch.py start     start it (if it is not running) and open the page
    python tools/agent/launch.py stop      stop it
    python tools/agent/launch.py status    say whether it is running

`start-workbench.bat` / `stop-workbench.bat` (Windows) and
`start-workbench.command` / `stop-workbench.command` (macOS) in the
project folder do nothing but call this, so both behave the same.

The port is 8787, or WORKBENCH_PORT.

WHAT "RUNNING" MEANS, AND WHAT STOP MAY END
-------------------------------------------
A server left running from days ago once kept answering beside a fresh
one, and the page talked to old code. So nothing here goes by a process
id written in a file, which says what WAS true. The server is asked who
it is: `GET /api/whoami` answers with its process id and the folder it
serves. Then:

* start: if this folder's workbench already answers on the port, it is
  not started again -- the page is opened. If something ELSE holds the
  port (another copy of the project, another program), nothing is started
  and it is said which.
* stop: ends only a process that answered, on this port, that it is the
  workbench of THIS folder. Another copy's workbench, or any other
  program on the port, is left alone and named.

A command the workbench had started (a convert, a Cursor run) is not
ended by stopping the server: it runs to its own end.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SERVER = os.path.join(HERE, "server.py")
CONSOLE = os.path.join(ROOT, "target", "agent", "server-console.log")
HOST = "127.0.0.1"


def say(msg: str = "") -> None:
    print(msg, flush=True)


def port() -> int:
    raw = os.environ.get("WORKBENCH_PORT", "").strip() or "8787"
    try:
        n = int(raw)
    except ValueError:
        n = 0
    if not 1024 <= n <= 65535:
        raise SystemExit(f"WORKBENCH_PORT is {raw!r}; it must be a number from 1024 to 65535")
    return n


def same_folder(a: str, b: str) -> bool:
    norm = lambda p: os.path.normcase(os.path.realpath(str(p or "")))
    return bool(a) and norm(a) == norm(b)


def who(p: int, timeout: float = 3.0):
    """What answers on the port: {"pid", "root"} for a workbench, "other"
    for anything else, None for nothing.

    Whether anything LISTENS is asked first, of the socket: on Windows a
    request to a port nobody holds does not fail at once, it times out,
    and a timeout must not be read as "something is there".
    """
    if not listening(p):
        return None
    try:
        with urllib.request.urlopen(f"http://{HOST}:{p}/api/whoami", timeout=timeout) as r:
            data = json.loads(r.read(4096).decode("utf-8", "replace"))
    except Exception:                                    # noqa: BLE001
        return "other"      # it holds the port and is not a workbench that says who it is
    if isinstance(data, dict) and isinstance(data.get("pid"), int) and data.get("root"):
        return {"pid": data["pid"], "root": str(data["root"])}
    return "other"


def listening(p: int) -> bool:
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(3.0)
    try:
        return s.connect_ex((HOST, p)) == 0
    except OSError:
        return False
    finally:
        s.close()


def url(p: int) -> str:
    return f"http://{HOST}:{p}/"


def start(open_page: bool = True, wait: float = 20.0) -> int:
    p = port()
    there = who(p)
    if isinstance(there, dict):
        if same_folder(there["root"], ROOT):
            say(f"The workbench is already running (process {there['pid']}): {url(p)}")
            if open_page:
                webbrowser.open(url(p))
            return 0
        say(f"Port {p} is held by ANOTHER copy of the workbench, serving\n  {there['root']}\n"
            f"Nothing was started. Stop that one from its own folder, or set WORKBENCH_PORT "
            f"to another port for this one.")
        return 1
    if there == "other":
        say(f"Port {p} is in use by another program (or by a workbench server from before "
            f"this launcher existed, which cannot say who it is).\nNothing was started. "
            f"Close that program -- for an old workbench, end its python process -- or set "
            f"WORKBENCH_PORT to another port.")
        return 1
    os.makedirs(os.path.dirname(CONSOLE), exist_ok=True)
    log = io.open(CONSOLE, "ab")
    log.write(f"\n===== started {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n".encode())
    log.flush()
    kwargs = {"stdin": subprocess.DEVNULL, "stdout": log, "stderr": subprocess.STDOUT,
              "cwd": ROOT, "close_fds": True}
    if os.name == "nt":
        # No console window of its own, and not ended when this one closes.
        kwargs["creationflags"] = (subprocess.CREATE_NEW_PROCESS_GROUP
                                   | getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
                                   | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
    else:
        kwargs["start_new_session"] = True
    try:
        # -u: what it says reaches the console log as it says it, not when
        # a buffer fills -- which for a server that fails at once is never.
        proc = subprocess.Popen([sys.executable, "-B", "-u", SERVER, "--port", str(p)], **kwargs)
    except OSError as e:
        say(f"The workbench could not be started ({type(e).__name__}: {e}).")
        return 1
    finally:
        log.close()
    deadline = time.time() + wait
    while time.time() < deadline:
        there = who(p, 1.0)
        if isinstance(there, dict) and there["pid"] == proc.pid:
            say(f"The workbench is running (process {proc.pid}): {url(p)}\n"
                f"To stop it: stop-workbench  (or: python tools/agent/launch.py stop)")
            if open_page:
                webbrowser.open(url(p))
            return 0
        if proc.poll() is not None:
            break
        time.sleep(0.3)
    say(f"The workbench did not come up. What it said is in\n  {CONSOLE}\nThe last lines:")
    for line in tail(CONSOLE, 12):
        say("  " + line)
    if proc.poll() is None:
        end(proc.pid)
    return 1


def tail(path: str, n: int) -> list:
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read().splitlines()[-n:]
    except OSError:
        return []


def end(pid: int) -> bool:
    """End one process. True when it is gone afterwards."""
    try:
        if os.name == "nt":
            # /T: with whatever it started as part of itself. /F: it has
            # no window to be asked nicely through.
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
        else:
            os.kill(pid, signal.SIGTERM)
    except (OSError, subprocess.SubprocessError):
        pass
    for _ in range(40):
        if not alive(pid):
            return True
        time.sleep(0.25)
    if os.name != "nt":
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
        time.sleep(0.5)
    return not alive(pid)


def alive(pid: int) -> bool:
    if os.name == "nt":
        try:
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                                 capture_output=True, text=True, timeout=20).stdout
        except (OSError, subprocess.SubprocessError):
            return True
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # Ended, but not yet collected by whoever started it.
    try:
        got, _ = os.waitpid(pid, os.WNOHANG)
        return got == 0
    except ChildProcessError:
        return True
    except OSError:
        return True


def stop() -> int:
    p = port()
    there = who(p)
    if there is None:
        say(f"The workbench is not running (nothing answers on port {p}).")
        return 0
    if there == "other":
        say(f"Something is on port {p}, and it is not a workbench that can say who it is. "
            f"It was left alone.\nIf it is a workbench server started before this launcher "
            f"existed, end its python process by hand once.")
        return 1
    if not same_folder(there["root"], ROOT):
        say(f"Port {p} is held by another copy of the workbench, serving\n  {there['root']}\n"
            f"It was left alone: stop it from its own folder.")
        return 1
    if end(there["pid"]) and who(p) is None:
        say(f"The workbench (process {there['pid']}) is stopped.\n"
            f"A command it had started keeps running to its own end.")
        return 0
    say(f"The workbench (process {there['pid']}) did not stop. End that process by hand.")
    return 1


def status() -> int:
    p = port()
    there = who(p)
    if there is None:
        say(f"Not running (nothing answers on port {p}).")
        return 1
    if there == "other":
        say(f"Port {p} is in use by something that is not a workbench that can say who it is.")
        return 1
    mine = same_folder(there["root"], ROOT)
    say(f"Running (process {there['pid']}): {url(p)}"
        + ("" if mine else f"\n  but it serves ANOTHER folder: {there['root']}"))
    return 0 if mine else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=("start", "stop", "status"))
    ap.add_argument("--no-browser", action="store_true", help="do not open the page")
    a = ap.parse_args(argv)
    if a.command == "start":
        return start(open_page=not a.no_browser)
    return stop() if a.command == "stop" else status()


if __name__ == "__main__":
    sys.exit(main())
