"""Start and stop the workbench without keeping a terminal open.

    python tools/agent/launch.py start     start it (if it is not running) and open the page
    python tools/agent/launch.py stop      stop it
    python tools/agent/launch.py status    say whether it is running

`start-workbench.bat` / `stop-workbench.bat` (Windows) and
`start-workbench.command` / `stop-workbench.command` (macOS) in the
project folder do nothing but call this, so both behave the same.

The port is 8787, or WORKBENCH_PORT.

WHICH PYTHON THE SERVER RUNS ON
-------------------------------
The server needs cursor-sdk. Seen live on a Mac: the launcher started it
on Homebrew's Python, which refuses `pip install` (PEP 668, "externally
managed"), so cursor-sdk could not be installed and Cursor was "NOT READY"
-- the key was fine. So the server's Python is chosen, in this order:

* the project's `.venv`, when it runs (cursor-sdk is installed into it
  when missing);
* else this Python, when it already has cursor-sdk (as before);
* else a `.venv` is made (`--system-site-packages`: what this Python has
  installed stays visible, the converter's packages too), cursor-sdk is
  installed into it, and the server runs on it.

A `.venv` that no longer runs (Homebrew upgraded the Python it was made
from) is made again. `.venv/` is gitignored: a pull neither brings nor
removes it.

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
import re
import os
import shutil
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
CONSOLE = os.path.join(ROOT, ".workbench", "server-console.log")


def move_old_state() -> None:
    """The workbench's state used to live under target/agent, which
    `mvn clean` deletes. Whatever is still there moves to the new folder
    before the server starts (tools/diag.py says where)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("launch_diag", os.path.join(ROOT, "tools", "diag.py"))
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
        mod.migrate_state(ROOT)
    except Exception:                                # noqa: BLE001
        pass
HOST = "127.0.0.1"
REQUIREMENTS = os.path.join(ROOT, "requirements-cursor.txt")


# ---- the server's Python ----------------------------------------------------------

def venv_dir(root: str = "") -> str:
    return os.path.join(root or ROOT, ".venv")


def venv_python(root: str = "") -> str:
    v = venv_dir(root)
    return os.path.join(v, "Scripts", "python.exe") if os.name == "nt" else os.path.join(v, "bin", "python")


def _ok(argv: list, run=None, timeout: float = 120) -> bool:
    """Did the command succeed? `run(argv) -> returncode` replaces it in the tests."""
    if run is not None:
        return run(argv) == 0
    try:
        return subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=timeout).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def runs(py: str, run=None) -> bool:
    return bool(py) and os.path.isfile(py) and _ok([py, "-c", "import sys"], run, 60)


def has_sdk(py: str, run=None) -> bool:
    return _ok([py, "-c", "import cursor_sdk"], run, 60)


def install_sdk(py: str, run=None) -> bool:
    req = ["-r", REQUIREMENTS] if os.path.isfile(REQUIREMENTS) else ["cursor-sdk"]
    return _ok([py, "-m", "pip", "install", "--disable-pip-version-check", "--no-input", *req], run, 900)


def make_venv(base: str = "", root: str = "", run=None) -> str:
    """Make the project's .venv from `base` (system site packages kept
    visible) and install cursor-sdk into it. Its python, or "" when it
    could not be made."""
    v = venv_dir(root)
    if os.path.isdir(v):
        shutil.rmtree(v, ignore_errors=True)
    if not _ok([base or sys.executable, "-m", "venv", "--system-site-packages", v], run, 600):
        return ""
    py = venv_python(root)
    if not runs(py, run):
        return ""
    install_sdk(py, run)
    return py


def server_python(root: str = "", base: str = "", run=None) -> tuple:
    """(the Python the server runs on, why that one)."""
    base = base or sys.executable
    py = venv_python(root)
    if os.path.isdir(venv_dir(root)):
        if runs(py, run):
            if not has_sdk(py, run):
                say(" .. cursor-sdk is not in the project's .venv: installing it there")
                install_sdk(py, run)
            return py, "the project's .venv"
        say(" .. the project's .venv no longer runs (the Python it was made from was upgraded "
            "or removed): it is made again")
    elif has_sdk(base, run):
        return base, "this Python (it has cursor-sdk)"
    else:
        say(" .. cursor-sdk is not in this Python, and this Python may refuse to install it "
            "(Homebrew's does): a .venv is made for the workbench and cursor-sdk is installed "
            "into it -- once, a minute or two")
    made = make_venv(base, root, run)
    if made:
        return made, "the project's .venv (made now)"
    say(" .. the .venv could not be made: the server runs on this Python, and the page says "
        "what is missing")
    return base, "this Python (no .venv could be made)"


ALL_REQUIREMENTS = os.path.join(ROOT, "requirements.txt")


def requirements(path: str = "") -> list:
    """(pip package, module to import) for each line of requirements.txt
    ending "# import <module>"."""
    out = []
    try:
        with io.open(path or ALL_REQUIREMENTS, encoding="utf-8") as fh:
            for line in fh:
                pkg, _, note = line.partition("#")
                pkg, note = pkg.strip(), note.strip()
                if pkg and note.startswith("import "):
                    out.append((pkg, note.split()[1]))
    except OSError:
        pass
    return out


def missing(py: str, reqs: list, run=None) -> list:
    """The packages of `reqs` that `py` cannot import."""
    return [(pkg, mod) for pkg, mod in reqs if not _ok([py, "-c", f"import {mod}"], run, 60)]


def ensure_requirements(py: str, path: str = "", run=None) -> list:
    """Install into `py` what requirements.txt lists and it lacks: all in one
    pip call, then one by one when that fails, so one package a proxy blocks
    does not keep the others out. Says what it did. Returns what is still
    missing -- the workbench starts anyway, and each feature that needs one
    says so where it is used."""
    need = missing(py, requirements(path), run)
    if not need:
        return []
    names = [p for p, _m in need]
    say(f" .. installing what this Python lacks from requirements.txt: {', '.join(names)} "
        "(once; a minute or two)")
    pip = [py, "-m", "pip", "install", "--disable-pip-version-check", "--no-input"]
    if not _ok(pip + names, run, 900):
        for pkg in names:
            _ok(pip + [pkg], run, 900)
    left = missing(py, need, run)
    if left:
        say(" .. NOT installed: " + ", ".join(p for p, _m in left) + " -- pip could not reach them "
            "(a proxy, no network?). The workbench starts; install them with "
            f"\"{py}\" -m pip install -r requirements.txt")
    else:
        say(" .. installed: " + ", ".join(names))
    return left


# Whole folder names: "OneDrive", "OneDrive - Corp", "OneDrive-Corp" -- not "onedrivetools".
SYNCED_FOLDERS = re.compile(r"/(library/cloudstorage|library/mobile documents|onedrive(?:[ -][^/]*)?"
                            r"|dropbox(?: \([^/]*\))?|google ?drive|icloud ?drive)/")


def synced_folder(root: str = "") -> bool:
    """The project is in a folder a sync client owns (OneDrive, iCloud,
    Dropbox, Google Drive). Seen on a Mac (~/Library/CloudStorage/
    OneDrive-...): a write to the job log timed out (Errno 60) while
    OneDrive held the file -- a design run crashed on a print, an intake
    lost a page it had fetched. Maven's target/ and git do the same there."""
    p = "/" + os.path.abspath(root or ROOT).replace("\\", "/").lower().strip("/") + "/"
    return bool(SYNCED_FOLDERS.search(p))


def warn_if_synced(root: str = "") -> None:
    if synced_folder(root):
        say("WARN this project is inside a synced folder (OneDrive / iCloud / Dropbox / Google Drive):\n"
            f"       {os.path.abspath(root or ROOT)}\n"
            "     The sync client holds files as they are written: job logs, target/ and .git time out\n"
            "     or go missing mid-run (seen: TimeoutError [Errno 60] on a Mac). Move the project to a\n"
            "     folder that is not synced -- e.g. ~/b2bworkbench (macOS) or C:\\b2bworkbench (Windows) --\n"
            "     with `git clone` there, then copy src/main/resources/program_configuration*.json across.")


def with_requirements(py: str, why: str, root: str = "", run=None) -> tuple:
    """`py` with requirements.txt installed. A Python that refuses pip (PEP
    668, Homebrew's) and is not the project's .venv: a .venv is made, and
    used when it has more of them."""
    left = ensure_requirements(py, os.path.join(root or ROOT, "requirements.txt"), run)
    if not left or py == venv_python(root):
        return py, why
    say(" .. this Python would not take them: a .venv is made for the workbench instead")
    made = make_venv(py, root, run)
    if made and len(ensure_requirements(made, os.path.join(root or ROOT, "requirements.txt"), run)) < len(left):
        return made, "the project's .venv (made now, for requirements.txt)"
    return py, why


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
    move_old_state()
    warn_if_synced()
    py, why = server_python()
    py, why = with_requirements(py, why)
    say(f" .. the server runs on {why}: {py}")
    os.makedirs(os.path.dirname(CONSOLE), exist_ok=True)
    log = io.open(CONSOLE, "ab")
    log.write(f"\n===== started {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n".encode())
    log.flush()
    kwargs = {"stdin": subprocess.DEVNULL, "stdout": log, "stderr": subprocess.STDOUT,
              "cwd": ROOT, "close_fds": True}
    if os.name == "nt":
        # No console window of its own, and not ended when this one closes.
        # A console of its own that is never shown -- not DETACHED_PROCESS,
        # which Windows lets win over CREATE_NO_WINDOW: a server with NO
        # console gave every console program it started (the Cursor bridge
        # through cmd.exe, git, mvn.cmd) a new, blank window that stayed open.
        # The children share this hidden one instead.
        kwargs["creationflags"] = (subprocess.CREATE_NEW_PROCESS_GROUP
                                   | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
    else:
        kwargs["start_new_session"] = True
    try:
        # -u: what it says reaches the console log as it says it, not when
        # a buffer fills -- which for a server that fails at once is never.
        proc = subprocess.Popen([py, "-B", "-u", SERVER, "--port", str(p)], **kwargs)
    except OSError as e:
        say(f"The workbench could not be started ({type(e).__name__}: {e}).")
        return 1
    finally:
        log.close()
    deadline = time.time() + wait
    while time.time() < deadline:
        there = who(p, 1.0)
        # This folder's workbench on a port that was free a moment ago: the
        # one just started. Not "its process id is proc.pid": on Windows a
        # .venv's python.exe is a stub that runs the real Python as a CHILD,
        # so the server's own id differs -- it was never seen as up, and was
        # ended after 20 seconds.
        if isinstance(there, dict) and same_folder(there["root"], ROOT):
            say(f"The workbench is running (process {there['pid']}): {url(p)}\n"
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
