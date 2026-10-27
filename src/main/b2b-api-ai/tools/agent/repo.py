"""The repository as it is: which branch is checked out, whether the tree
is clean, which branches there are -- and a checkout, under conditions.

    python tools/agent/repo.py info
    python tools/agent/repo.py checkout --branch feature/x --confirm feature/x

The workbench pushes an agent's change to a branch of its own, built on
what the remote has. Until now it did that from whatever branch happened
to be checked out, and the page did not say which. This reads the
repository so the page can show it and offer a choice, and switches
branches only when that cannot lose anything:

* the working tree must be CLEAN (nothing changed, nothing untracked);
* no agent job may be running (the repository lock is taken);
* the branch is named twice, exists -- locally, or on the remote -- and
  is a plain branch name.

Everything else here is read-only. The remote's address is shown with
any credentials removed, because the page shows it.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
from urllib.parse import urlsplit, urlunsplit

HERE = os.path.dirname(os.path.abspath(__file__))


def _diag():
    import importlib.util as _ilu
    if "workbench_diag" not in sys.modules:
        _spec = _ilu.spec_from_file_location(
            "workbench_diag", os.path.join(os.path.dirname(HERE), "diag.py"))
        _mod = _ilu.module_from_spec(_spec)
        sys.modules["workbench_diag"] = _mod
        try:
            _spec.loader.exec_module(_mod)
        except BaseException:
            sys.modules.pop("workbench_diag", None)
            raise
    return sys.modules["workbench_diag"]


diag = _diag()
ROOT = os.path.dirname(os.path.dirname(HERE))
NO_WINDOW = ({"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
             if os.name == "nt" else {})
# A branch name as git allows it, minus what a page should not be handed:
# no leading dash (an option), no `..`, no `@{`, no control characters.
_BRANCH_RX = re.compile(r"^(?!-)(?!.*\.\.)(?!.*@\{)[A-Za-z0-9][A-Za-z0-9._/-]{0,120}(?<!\.lock)(?<!/)$")
MAX_BRANCHES = 200


class Refused(RuntimeError):
    pass


def git(root: str, *args: str) -> tuple:
    r = subprocess.run(["git", *args], cwd=root, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, **NO_WINDOW)
    return r.returncode, r.stdout.decode("utf-8", errors="replace")


def valid_branch(name: str) -> str:
    name = (name or "").strip()
    if not _BRANCH_RX.match(name):
        raise Refused(f"{name!r} is not a branch name this will use (letters, digits, "
                      f"`.`, `_`, `/`, `-`; no leading `-`, no `..`)")
    return name


def _shown_remote(url: str) -> str:
    """The remote's address without a user name or a token in it."""
    url = (url or "").strip()
    if "://" in url:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if parts.port:
            host += f":{parts.port}"
        return urlunsplit((parts.scheme, host, parts.path, "", ""))
    return re.sub(r"^[^@/\s]+@", "", url)             # git@host:path -> host:path


# Tracked files a machine is expected to have edited and keep across a
# switch: the mock switches are committed on, and turned off where the
# real Jira and Cursor are. git carries an edited file through a checkout
# when the branches hold the same version of it, and refuses when not.
CARRIED = ("tools/agent/mock.json",)
MAX_NAMED = 20


def info(root: str = "", remote: str = "origin") -> dict:
    """What the page shows: {branch, head, clean, dirty, modified, untracked,
    blocking, switchable, remote, remote_url, local_branches,
    remote_branches, upstream, ahead, behind, is_repo}.

    `clean` is the whole truth (nothing changed, nothing untracked).
    `switchable` is what a checkout needs: no tracked file changed other
    than the CARRIED ones. Untracked files never block; git leaves them."""
    root = root or ROOT
    out = {"is_repo": False, "branch": "", "head": "", "clean": True, "dirty": 0,
           "modified": [], "untracked": [], "blocking": [], "switchable": True,
           "remote": remote, "remote_url": "", "local_branches": [], "remote_branches": [],
           "upstream": "", "ahead": 0, "behind": 0, "note": ""}
    rc, top = git(root, "rev-parse", "--show-toplevel")
    if rc != 0:
        out["note"] = "this folder is not a git work tree"
        return out
    out["is_repo"] = True
    _rc, branch = git(root, "rev-parse", "--abbrev-ref", "HEAD")
    out["branch"] = branch.strip() if _rc == 0 else ""
    _rc, head = git(root, "rev-parse", "--short", "HEAD")
    out["head"] = head.strip() if _rc == 0 else ""
    rc, status = git(root, "status", "--porcelain=v1", "--untracked-files=all")
    # Porcelain paths are from the top of the repository, and this project
    # may sit below it (.../src/main/b2b-api-ai/): read from the project,
    # or an edited CARRIED file was never recognised and blocked the switch.
    _rc, prefix = git(root, "rev-parse", "--show-prefix")
    prefix = prefix.strip() if _rc == 0 else ""
    if rc == 0:
        lines = [l for l in status.splitlines() if l.strip()]
        out["dirty"], out["clean"] = len(lines), not lines
        for line in lines:
            code, path = line[:2], line[3:].strip().split(" -> ")[-1].strip('"')
            if prefix and path.startswith(prefix):
                path = path[len(prefix):]
            (out["untracked"] if code == "??" else out["modified"]).append(path)
        out["modified"], out["untracked"] = out["modified"][:MAX_NAMED], out["untracked"][:MAX_NAMED]
        out["blocking"] = [p for p in out["modified"] if p not in CARRIED]
        out["switchable"] = not out["blocking"]
    rc, url = git(root, "remote", "get-url", remote)
    out["remote_url"] = _shown_remote(url) if rc == 0 else ""
    rc, locals_ = git(root, "for-each-ref", "--format=%(refname:short)", "refs/heads/")
    if rc == 0:
        out["local_branches"] = [b.strip() for b in locals_.splitlines()
                                 if _BRANCH_RX.match(b.strip())][:MAX_BRANCHES]
    # The full name: git shortens refs/remotes/origin/HEAD (a pointer to the
    # default branch) to "origin", which was listed as a branch called origin.
    rc, remotes = git(root, "for-each-ref", "--format=%(refname)", f"refs/remotes/{remote}/")
    if rc == 0:
        names = []
        prefix = f"refs/remotes/{remote}/"
        for b in remotes.splitlines():
            b = b.strip()
            if not b.startswith(prefix):
                continue
            b = b[len(prefix):]
            if b != "HEAD" and _BRANCH_RX.match(b):
                names.append(b)
        out["remote_branches"] = names[:MAX_BRANCHES]
    rc, up = git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    if rc == 0 and up.strip():
        out["upstream"] = up.strip()
        rc, counts = git(root, "rev-list", "--left-right", "--count", f"{up.strip()}...HEAD")
        if rc == 0 and counts.strip():
            behind, ahead = (counts.split() + ["0", "0"])[:2]
            out["behind"], out["ahead"] = int(behind), int(ahead)
    return out


def fetch(root: str = "", remote: str = "origin", timeout: float = 90) -> dict:
    """`git fetch --prune <remote>`: the remote's branches as they are now.
    The list read only what this clone last fetched, so a branch pushed
    from another machine was never offered. Changes no file of the tree."""
    import subprocess
    root = root or ROOT
    try:
        r = subprocess.run(["git", "fetch", "--prune", remote], cwd=root, capture_output=True,
                           timeout=timeout, env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
    except subprocess.TimeoutExpired:
        return {"ok": False, "message": f"git fetch did not answer in {timeout:.0f}s"}
    except OSError as e:
        return {"ok": False, "message": f"git could not be run ({e})"}
    said = (r.stderr or b"").decode("utf-8", "replace").strip().splitlines()
    if r.returncode != 0:
        return {"ok": False, "message": "git fetch failed: " + (said[-1] if said else f"exit {r.returncode}")[:300]}
    return {"ok": True, "message": f"fetched {remote}"}


def checkout(root: str, branch: str, confirm: str, remote: str = "origin", lock=None) -> dict:
    """Switch the working tree to `branch`. Only on a clean tree, with no
    agent job running, and with the name given twice."""
    root = root or ROOT
    branch = valid_branch(branch)
    if (confirm or "").strip() != branch:
        raise Refused("the branch must be named twice, the same: this changes every file "
                      "of the working tree")
    state = info(root, remote)
    if not state["is_repo"]:
        raise Refused("this folder is not a git work tree")
    if not state["switchable"]:
        names = ", ".join(state["blocking"][:8]) + (" ..." if len(state["blocking"]) > 8 else "")
        raise Refused(f"{len(state['blocking'])} tracked file(s) are changed: {names}. A checkout "
                      f"could carry the change to the other branch or be refused half way. "
                      f"Commit, stash or discard them first. (Untracked files and an edited "
                      f"{', '.join(CARRIED)} do not stand in the way.)")
    if branch == state["branch"]:
        return {"ok": True, "branch": branch, "message": f"already on {branch}"}
    where = ("local" if branch in state["local_branches"]
             else "remote" if branch in state["remote_branches"] else "")
    if not where:
        raise Refused(f"there is no branch {branch} here or on {remote}. Fetch first if it "
                      f"was created elsewhere.")
    diag.step(f"Switching the working tree to {branch}")
    with (lock or _NoLock()):
        rc, out = git(root, "checkout", "--no-guess", branch) if where == "local" else \
            git(root, "checkout", "--track", "-b", branch, f"{remote}/{branch}")
    if rc != 0:
        raise Refused(f"git refused the checkout: {out.strip()[-300:]}")
    return {"ok": True, "branch": branch,
            "message": f"on {branch} now" + (f" (from {remote}/{branch})" if where == "remote" else "")}


class _NoLock:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info")
    p = sub.add_parser("checkout")
    p.add_argument("--branch", required=True)
    p.add_argument("--confirm", required=True)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "info":
            print(json.dumps(info(), indent=2))
            return 0
        r = checkout(ROOT, a.branch, a.confirm)
        print(f" ok   {r['message']}")
        return 0
    except Refused as e:
        print(f"REFUSED {e}")
        return 2


if __name__ == "__main__":
    sys.exit(diag.run("repo", main))
