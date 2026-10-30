"""Pictures of the page after a change to the workbench, for its review.

    python tools/agent/screens.py --job <job>

The page checks (check_ui.py) read the page's HTML; nothing LOOKS at it.
A section that renders full-width, buttons out of line, a tab that is
suddenly empty -- seen on 2026-10-30 only by opening screenshots. So when
an AI Agent change to the workbench touched the page (tools/agent/ui/),
each tab of the top bar is opened in a headless browser and kept as a
picture the review on the Commit and push tab shows, before Approve.

The pictures are taken from a server started here, on a free port, with
the stand-ins ON: opening a tab contacts nothing real. They are kept in
.workbench/<job>/screens/<tab>.png, listed in screens.json; a change that
did not touch the page has none (old ones are removed). Stdlib only.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
UI_PREFIX = "tools/agent/ui/"
SIZE = (1440, 2400)
LIST = "screens.json"
DIR = "screens"
TAB_RX = re.compile(r'data-tab="([a-z]+)">\s*<span class="name">([^<]+)</span>')


def _load(name: str, path: str):
    import importlib.util
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def tabs(root: str = "") -> list:
    """[(tab id, its name)] of the top bar, as the page has them now."""
    try:
        with io.open(os.path.join(root or ROOT, "tools", "agent", "ui", "index.html"), encoding="utf-8") as fh:
            nav = fh.read().split('<nav class="tabs"', 1)[-1].split("</nav>", 1)[0]
    except OSError:
        return []
    return [(t, re.sub(r"\s+", " ", n).strip()) for t, n in TAB_RX.findall(nav)]


def touched_page(job_dir: str) -> bool:
    """Did the job's change touch the page (tools/agent/ui/)?"""
    try:
        with io.open(os.path.join(job_dir, "review.json"), encoding="utf-8") as fh:
            files = (json.load(fh) or {}).get("files") or {}
    except (OSError, ValueError, AttributeError):
        return False
    return any(str(p).replace("\\", "/").startswith(UI_PREFIX)
               for key in ("modified", "created") for p in files.get(key) or [])


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _clear(out: str) -> None:
    shutil.rmtree(os.path.join(out, DIR), ignore_errors=True)
    try:
        os.remove(os.path.join(out, LIST))
    except OSError:
        pass


def capture(job: str, root: str = "", browser: str = "", force: bool = False) -> dict:
    """{"tabs": [{tab, name, file}], "browser", "at"} or {"skipped": why}."""
    root = root or ROOT
    out = os.path.join(root, ".workbench", job)
    if not force and not touched_page(out):
        _clear(out)
        return {"skipped": "the change did not touch the page"}
    wanted = tabs(root)
    if not wanted:
        return {"skipped": "the page's tabs could not be read"}
    if not browser:
        check_ui = _load("screens_check_ui", os.path.join(HERE, "check_ui.py"))
        browser = check_ui.find_browser()
    if not browser:
        return {"skipped": "no Edge or Chrome was found to take the pictures"}
    _clear(out)
    os.makedirs(os.path.join(out, DIR), exist_ok=True)
    port = _free_port()
    env = dict(os.environ, WORKBENCH_MOCK="1")
    srv = subprocess.Popen([sys.executable, "-B", os.path.join("tools", "agent", "server.py"), "--port", str(port)],
                           cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    taken = []
    try:
        for _ in range(80):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/jobs", timeout=2).read()
                break
            except OSError:
                time.sleep(0.25)
        for tab, name in wanted:
            path = os.path.join(out, DIR, f"{tab}.png")
            with tempfile.TemporaryDirectory(prefix="screens-edge-", ignore_cleanup_errors=True) as profile:
                try:
                    subprocess.run([browser, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                                    "--no-first-run", "--disable-extensions", f"--user-data-dir={profile}",
                                    f"--window-size={SIZE[0]},{SIZE[1]}", "--virtual-time-budget=6000",
                                    f"--screenshot={path}", f"http://127.0.0.1:{port}/?job={job}#{tab}"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
                except (OSError, subprocess.SubprocessError):
                    continue
            if os.path.isfile(path) and os.path.getsize(path) > 0:
                taken.append({"tab": tab, "name": name, "file": f"{tab}.png"})
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=10)
        except subprocess.TimeoutExpired:
            srv.kill()
    result = {"tabs": taken, "browser": os.path.basename(browser),
              "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    with io.open(os.path.join(out, LIST), "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=1)
    return result


def picture(job_dir: str, tab: str) -> bytes:
    """One kept picture, for the server; b"" when there is none. The tab is
    a lower-case word: no path reaches the file system."""
    if not re.fullmatch(r"[a-z]{1,30}", tab or ""):
        return b""
    try:
        with open(os.path.join(job_dir, DIR, f"{tab}.png"), "rb") as fh:
            return fh.read()
    except OSError:
        return b""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--job", required=True)
    ap.add_argument("--force", action="store_true", help="take them although the page was not touched")
    a = ap.parse_args(argv)
    intake = _load("screens_intake", os.path.join(HERE, "intake.py"))
    try:
        job = intake.safe_job(a.job)
    except ValueError as e:
        print(f"FAIL {e}")
        return 1
    r = capture(job, force=a.force)
    if r.get("skipped"):
        print(f"skip pictures of the page: {r['skipped']}")
        return 0
    print(f" ok  {len(r['tabs'])} picture(s) of the page, for the review: "
          + ", ".join(t["name"] for t in r["tabs"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
