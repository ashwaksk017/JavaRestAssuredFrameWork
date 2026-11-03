"""The job's issue branch on GitLab, and its merge request.

    python tools/agent/issuebranch.py start --job J [--prefix issue/]
    python tools/agent/issuebranch.py mr --job J

The team works in issue branches: before Automate, `issue/<story key>`
(or `feature/<story key>`) is made from `develop` ON GITLAB, then fetched
and checked out here, so the job's change -- and every later change of the
same story -- goes into it. Approve & push then commits on top of
`origin/issue/<key>` and pushes to it (loop.cmd_approve). Once the change
is pushed there with a green run, `mr` raises the merge request from the
issue branch to `develop` (asked for 2026-11-02).

`testrepo_config` in the project's program_configuration.json (Settings
tab, "Test repository (GitLab)"):

    "testrepo_config": {"base_url": "https://gitlab.example.com",
                        "project": "qa/api-tests", "target": "develop",
                        "token": "<a personal access token with api scope>"}

The token is sent to `base_url` and nowhere else, as PRIVATE-TOKEN.
An existing branch is reused (the story's jobs collect in it); an open
merge request for the same branches is shown, not made twice. The job
keeps `issue-branch.json` and `issue-mr.json`.
"""
from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
BRANCH_FILE = "issue-branch.json"
MR_FILE = "issue-mr.json"
PREFIXES = ("issue/", "feature/")
PROTECTED = ("develop", "main", "master")
_KEY_RX = re.compile(r"^[A-Z][A-Z0-9_]{0,19}-[0-9]{1,8}$")
_PROJECT_RX = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+$|^[0-9]+$")
_BRANCH_RX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")


class Refused(Exception):
    """Said on the page, nothing done."""


def _load(name: str, path: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _tool(name: str):
    return _load(f"issuebranch_{name}", os.path.join(HERE, f"{name}.py"))


def say(line: str) -> None:
    print(line, flush=True)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(path: str) -> dict:
    try:
        with io.open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path: str, data: dict) -> None:
    tmp = f"{path}.{os.getpid()}.tmp"
    with io.open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)
    os.replace(tmp, path)


# ---- the configuration --------------------------------------------------------

def config(root: str = "", project: str = "", block: dict = None) -> dict:
    """{base_url, project, target, token} of the project's active environment;
    Refused when what is needed is not set."""
    if block is None:
        st = _tool("settings")
        env, _src = st._env(root or ROOT)
        doc = st._read(root or ROOT, project)
        block = doc.get(env) if isinstance(doc.get(env), dict) else {}
    cfg = block.get("testrepo_config") if isinstance(block.get("testrepo_config"), dict) else {}
    base = str(cfg.get("base_url") or "").strip().rstrip("/")
    proj = str(cfg.get("project") or "").strip().strip("/")
    target = str(cfg.get("target") or "develop").strip()
    token = str(cfg.get("token") or "").strip()
    missing = [w for w, v in (("its address", base), ("the project path", proj), ("a token", token)) if not v]
    if missing:
        raise Refused("the test repository (GitLab) is not set up: " + ", ".join(missing)
                      + " -- the Settings tab, \"Test repository (GitLab)\"")
    if not base.startswith("https://"):
        raise Refused(f"the test repository's address must be https://, not {base!r}")
    if not _PROJECT_RX.match(proj):
        raise Refused(f"{proj!r} is not a GitLab project path (group/project) or id")
    if not _BRANCH_RX.match(target):
        raise Refused(f"{target!r} is not a branch name")
    return {"base_url": base, "project": proj, "target": target, "token": token}


def branch_name(prefix: str, key: str) -> str:
    prefix = (prefix or "issue/").strip()
    if prefix not in PREFIXES:
        raise Refused(f"the branch starts with {' or '.join(PREFIXES)}, not {prefix!r}")
    key = (key or "").strip().upper()
    if not _KEY_RX.match(key):
        raise Refused("the job has no Jira story key (e.g. B2B-1234): read the story by its key first. "
                      "The issue branch is named after it")
    name = prefix + key
    if name in PROTECTED:
        raise Refused(f"refusing to use {name}")
    return name


# ---- GitLab ---------------------------------------------------------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """urllib copies every header -- the token too -- to wherever a 30x
    points, another host included. A redirect is refused instead: GitLab's
    API does not send one, a sign-in page in front of it does."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _http(method: str, url: str, token: str, body: dict = None, timeout: float = 30) -> tuple:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "PRIVATE-TOKEN": token, "Accept": "application/json",
        **({"Content-Type": "application/json"} if data is not None else {})})
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "null")
    except urllib.error.HTTPError as e:
        try:
            said = json.loads(e.read().decode("utf-8", "replace") or "null")
        except ValueError:
            said = None
        return e.code, said
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise Refused(f"GitLab could not be reached ({type(e).__name__}: {str(e)[:160]})") from None


def call(cfg: dict, method: str, path: str, body: dict = None, transport=None) -> tuple:
    """(status, json) of the GitLab API; the token goes to cfg's address only."""
    url = f"{cfg['base_url']}/api/v4/projects/{urllib.parse.quote(cfg['project'], safe='')}{path}"
    if urllib.parse.urlparse(url).netloc != urllib.parse.urlparse(cfg["base_url"]).netloc:
        raise Refused("refusing to send the token to another host")
    return (transport or _http)(method, url, cfg["token"], body)


def _said(status: int, data) -> str:
    msg = data.get("message") if isinstance(data, dict) else None
    if isinstance(msg, (dict, list)):
        msg = json.dumps(msg)
    return f"HTTP {status}" + (f": {str(msg)[:200]}" if msg else "")


def _why_http(status: int, data, what: str) -> str:
    if 300 <= status < 400:
        return (f"GitLab's address answered with a redirect (HTTP {status}), which is not followed with "
                f"the token ({what}): set the API's own address on the Settings tab")
    if status == 401:
        return f"GitLab refused the token ({what}): set a token with api scope on the Settings tab"
    if status == 403:
        return f"the token may not {what} in this project (HTTP 403): it needs api scope and Developer role"
    if status == 404:
        return f"GitLab does not know the project or branch ({what}, HTTP 404): check the project path"
    return f"GitLab answered {_said(status, data)} ({what})"


def ensure_branch(cfg: dict, name: str, transport=None) -> dict:
    """{created: bool, commit} -- made from cfg['target'] when it is not there."""
    q = urllib.parse.quote(name, safe="")
    status, data = call(cfg, "GET", f"/repository/branches/{q}", transport=transport)
    if status == 200:
        return {"created": False, "commit": ((data or {}).get("commit") or {}).get("id", "")}
    if status != 404:
        raise Refused(_why_http(status, data, f"read the branch {name}"))
    status, data = call(cfg, "POST", f"/repository/branches?branch={q}&ref="
                        f"{urllib.parse.quote(cfg['target'], safe='')}", transport=transport)
    if status not in (200, 201):
        if status == 400 and "ref" in json.dumps(data or {}).lower():
            raise Refused(f"there is no branch {cfg['target']} on GitLab to start {name} from")
        raise Refused(_why_http(status, data, f"create the branch {name} from {cfg['target']}"))
    return {"created": True, "commit": ((data or {}).get("commit") or {}).get("id", "")}


# ---- start: the issue branch, on GitLab then here ------------------------------------

def cmd_start(job: str, prefix: str = "issue/", root: str = "", transport=None, local=None) -> dict:
    root = root or ROOT
    intake = _tool("intake")
    jd = intake.job_dir(intake.safe_job(job), root)
    key = _tool("loop").story_key_of(jd)
    name = branch_name(prefix, key)
    cfg = config(root, intake.job_project(jd))
    say(f" ..  {name} on GitLab ({cfg['project']}), from {cfg['target']} when it is not there yet")
    got = ensure_branch(cfg, name, transport)
    say(f" ok  {name} " + (f"created on GitLab from {cfg['target']}" if got["created"]
                           else "is on GitLab already: this job adds to it"))
    rec = {"branch": name, "created": got["created"], "target": cfg["target"],
           "project": cfg["project"], "at": _now(), "local": "", "local_note": ""}
    # Then here: fetch it and switch the working tree to it -- only on a
    # clean tree, with no job running (repo.checkout). A refusal is said; the
    # branch on GitLab stands, and Approve pushes to it either way.
    try:
        if local is not None:
            note = local(root, name)
        else:
            repo = _tool("repo")
            f = repo.fetch(root)
            if not f.get("ok"):
                raise Refused(f.get("message") or "git fetch failed")
            note = repo.checkout(root, name, name, lock=_tool("jobs").repo_lock(f"issue branch {name}"))["message"]
            # Checked out already (an earlier job of the story): brought up to
            # what GitLab has, fast-forward only -- else Approve refuses it as
            # behind. Never a merge of local work.
            with _tool("jobs").repo_lock(f"issue branch {name}"):
                rc, out = repo.git(root, "merge", "--ff-only", "-q", f"origin/{name}")
            if rc != 0:
                raise Refused(f"on {name}, but it could not be brought up to origin/{name} "
                              f"(fast-forward only): {out.strip()[-200:]}")
        rec["local"] = name
        say(f" ok  here: {note}")
    except Exception as e:                               # noqa: BLE001 -- said, the branch on GitLab stands
        rec["local_note"] = str(e)[:400]
        say(f"WARN not switched here: {rec['local_note']}. The job's change is still pushed to {name} "
            f"(Approve builds on origin/{name}); switch on the Commit and push tab when the tree is clean.")
    _write(os.path.join(jd, BRANCH_FILE), rec)
    return rec


def issue_branch(job_dir: str) -> str:
    """The job's issue branch, or ""."""
    b = str(_json(os.path.join(job_dir, BRANCH_FILE)).get("branch") or "")
    return b if any(b.startswith(p) for p in PREFIXES) and _BRANCH_RX.match(b) else ""


# ---- mr: issue branch -> develop -----------------------------------------------------

def _mr_text(job: str, jd: str, key: str, review: dict) -> tuple:
    summary = ""
    try:
        story = _json(os.path.join(jd, "intake.json")).get("story") or {}
        summary = str(story.get("summary") or story.get("title") or "").strip()
    except AttributeError:
        pass
    title = f"{key}: {summary}" if summary else f"{key}: automated API tests"
    ran = review.get("execution") or {}
    files = [str(p) for k in ("created", "modified") for p in (review.get("files") or {}).get(k) or []]
    lines = [f"Automated API tests for {key} (workbench job `{job}`).", "",
             f"**Run:** {ran.get('passed', 0)} passed, {ran.get('failed', 0)} failed, "
             f"{ran.get('skipped', 0)} skipped on `{ran.get('env', '')}` ({ran.get('at', '')}).",
             f"**Commit:** {str(review.get('commit') or '')[:12]}"]
    if files:
        lines += ["", "**Files:**"] + [f"- `{p}`" for p in files[:40]]
    return title[:250], "\n".join(lines)


def cmd_mr(job: str, root: str = "", transport=None) -> dict:
    root = root or ROOT
    intake = _tool("intake")
    jd = intake.job_dir(intake.safe_job(job), root)
    branch = issue_branch(jd)
    if not branch:
        raise Refused("this job has no issue branch: create it on the Automate Test Case tab, before Automate")
    review = _json(os.path.join(jd, "review.json"))
    if review.get("state") != "pushed" or review.get("branch") != branch:
        raise Refused(f"the change is not pushed to {branch} yet: Approve & push first")
    ran = review.get("execution") or {}
    if not ran.get("ok"):
        raise Refused("the merge request waits for a green run of the pushed change: Run the tests, "
                      "every test passing")
    cfg = config(root, intake.job_project(jd))
    key = _tool("loop").story_key_of(jd)
    status, data = call(cfg, "GET", "/merge_requests?state=opened&source_branch="
                        f"{urllib.parse.quote(branch, safe='')}&target_branch="
                        f"{urllib.parse.quote(cfg['target'], safe='')}", transport=transport)
    if status != 200:
        raise Refused(_why_http(status, data, "list the merge requests"))
    if isinstance(data, list) and data:
        mr = data[0]
        rec = {"url": mr.get("web_url", ""), "iid": mr.get("iid"), "made": False, "at": _now(),
               "source": branch, "target": cfg["target"]}
        say(f" ok  an open merge request from {branch} to {cfg['target']} exists already: {rec['url']}")
    else:
        title, description = _mr_text(job, jd, key, review)
        status, mr = call(cfg, "POST", "/merge_requests", {
            "source_branch": branch, "target_branch": cfg["target"], "title": title,
            "description": description, "remove_source_branch": False}, transport=transport)
        if status not in (200, 201):
            raise Refused(_why_http(status, mr, f"raise the merge request {branch} -> {cfg['target']}"))
        rec = {"url": (mr or {}).get("web_url", ""), "iid": (mr or {}).get("iid"), "made": True,
               "at": _now(), "source": branch, "target": cfg["target"], "title": title}
        say(f" ok  merge request raised: {branch} -> {cfg['target']}: {rec['url']}")
    _write(os.path.join(jd, MR_FILE), rec)
    return rec


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start")
    s.add_argument("--job", required=True)
    s.add_argument("--prefix", default="issue/")
    m = sub.add_parser("mr")
    m.add_argument("--job", required=True)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "start":
            cmd_start(a.job, a.prefix)
        else:
            cmd_mr(a.job)
        return 0
    except (Refused, ValueError) as e:
        say(f"REFUSED {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
