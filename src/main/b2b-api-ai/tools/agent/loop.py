"""Tab 3: let Cursor write the test a plan describes, then hold it for review.

    python tools/agent/loop.py setup    --job <job>
    python tools/agent/loop.py generate --job <job>
    python tools/agent/loop.py push     --job <job> --confirm <job>
    python tools/agent/loop.py discard  --job <job>

    generate:  brief.md + plan.md  ->  Cursor writes  ->  guardrails  ->
               compile + checks (one repair attempt)  ->  PENDING REVIEW
    push:      secret scan  ->  branch agent/<job>  ->  commit  ->  push

Nothing is committed and nothing leaves the machine until `push`, and
`push` only runs on a job a person has looked at: the page shows the diff
and the button is the review.

THE GUARDRAILS ARE CODE, NOT PROMPT
-----------------------------------
The prompt tells the agent where it may write. That is advisory. What is
enforced is checked AFTER the agent returns, against the working tree:

* a changed path outside `policy.json: write_roots` fails the job and is
  put back the way it was;
* a deleted file fails the job and is restored -- this pipeline does not
  delete in v1;
* a change inside the GENERATED tree (converter output, gitignored) fails
  the job. It cannot be put back -- git does not track it -- so the job
  says which suite to reconvert. Those files could not be reviewed or
  pushed either, which is why they are not a write root;
* a moved HEAD or a switched branch fails the job: the agent commits
  nothing, this tool does.

The repository is PUBLIC. `push` scans the staged diff for credentials
and for every host and secret value in the gitignored
`program_configuration.json`, and refuses rather than warns.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


intake = _load("loop_intake", os.path.join(HERE, "intake.py"))
ROOT = intake.ROOT
POLICY_FILE = os.path.join(HERE, "policy.json")
REQUIREMENTS = "requirements-cursor.txt"

DEFAULT_POLICY = {
    # The only places the agent may create or modify a file. Tracked
    # paths only: a gitignored file cannot be reviewed or pushed.
    "write_roots": [
        "src/test/java/com/hi/api/tests/jira/",
        "src/test/resources/csv/manual/",
    ],
    # Converter output. Gitignored, so git cannot see a change here; these
    # are stat-snapshotted around the agent run instead.
    "generated_roots": [
        "src/main/java/com/hi/api/support/",
        "src/main/java/com/hi/api/rest/clients/",
        "src/test/java/com/hi/api/tests/imported/",
        "src/test/java/com/hi/api/tests/classic/",
        "src/main/resources/templates/",
    ],
    "compile": ["mvn", "-q", "-DskipTests", "test-compile"],
    "checks": [["tools/check_no_duplicate_methods.py"]],
    "repair_attempts": 1,
    "branch_prefix": "agent/",
    "remote": "origin",
    "protected_branches": ["main", "master"],
}

STATES = ("none", "generating", "rejected", "verify-failed",
          "pending-review", "pushed", "push-failed", "discarded")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def say(msg: str) -> None:
    print(msg, flush=True)


# ---- policy and state ------------------------------------------------

def load_policy(path: str = "") -> dict:
    policy = json.loads(json.dumps(DEFAULT_POLICY))
    p = path or POLICY_FILE
    if os.path.isfile(p):
        with io.open(p, encoding="utf-8") as fh:
            policy.update(json.load(fh) or {})
    for key in ("write_roots", "generated_roots"):
        policy[key] = [r.replace("\\", "/").rstrip("/") + "/" for r in policy[key]]
    return policy


def review_path(job_dir: str) -> str:
    return os.path.join(job_dir, "review.json")


def read_review(job_dir: str) -> dict:
    try:
        with io.open(review_path(job_dir), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"state": "none"}


def write_review(job_dir: str, review: dict) -> dict:
    assert review.get("state") in STATES, review.get("state")
    review["updated"] = _now()
    os.makedirs(job_dir, exist_ok=True)
    with io.open(review_path(job_dir), "w", encoding="utf-8") as fh:
        json.dump(review, fh, indent=2, ensure_ascii=False)
    return review


# ---- cursor-sdk ------------------------------------------------------

def ensure_sdk(root: str = "", pip=None) -> str:
    """Import cursor-sdk, installing it once if it is missing.

    Returns "present" or "installed". Raises RuntimeError with a message a
    person can act on -- never a pip traceback -- when it cannot be
    installed (no network, no permission, a locked-down index).
    """
    try:
        importlib.import_module("cursor_sdk")
        return "present"
    except ImportError:
        pass
    req = os.path.join(root or ROOT, REQUIREMENTS)
    argv = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check"]
    argv += ["-r", req] if os.path.isfile(req) else ["cursor-sdk"]
    say(f" .. cursor-sdk is not installed -- installing: {' '.join(argv[1:])}")
    run = pip or (lambda a: subprocess.run(
        a, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True))
    done = run(argv)
    if done.returncode != 0:
        tail = "\n".join((done.stdout or "").strip().splitlines()[-6:])
        raise RuntimeError(
            "cursor-sdk could not be installed automatically "
            f"(pip exited {done.returncode}). Install it by hand with "
            f"`{sys.executable} -m pip install cursor-sdk`, then run this "
            f"again.\n{tail}")
    importlib.invalidate_caches()
    try:
        importlib.import_module("cursor_sdk")
    except ImportError as e:
        raise RuntimeError(
            "pip reported success but cursor_sdk still does not import "
            f"from {sys.executable}: {e}") from e
    return "installed"


def cursor_assist():
    return _load("loop_cursor_assist", os.path.join(
        ROOT, "tools", "ra_converter", "cursor_assist.py"))


def cursor_config(root: str):
    """The converter's own Cursor config: one key, one place.

    CURSOR_API_KEY, else `apiKey` in tools/ra_converter/cursor_agent.json
    (gitignored). `enabled` is the converter's switch for its own assist
    and is not consulted here: starting this loop is the opt-in.
    """
    ca = cursor_assist()
    cfg = ca.load_config(None)
    cfg.cwd = root
    return ca, cfg


def call_cursor(prompt: str, root: str) -> dict:
    ca, cfg = cursor_config(root)
    if not cfg.api_key:
        raise RuntimeError(
            "no Cursor API key. Set the CURSOR_API_KEY environment variable, "
            "or copy tools/ra_converter/cursor_agent.json.example to "
            "cursor_agent.json (gitignored) and put the key in `apiKey`.")
    try:
        out = ca._sdk_prompt(prompt, cfg)
    except Exception as e:                               # noqa: BLE001
        raise RuntimeError(ca.redact_for_log(str(e), cfg.api_key)) from e
    out["result"] = ca.redact_for_log(str(out.get("result") or ""), cfg.api_key)
    return out


# ---- the working tree ------------------------------------------------

def git(root: str, *args: str) -> tuple:
    r = subprocess.run(["git", *args], cwd=root, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT)
    return r.returncode, r.stdout.decode("utf-8", errors="replace")


def repo_prefix(root: str) -> str:
    """`root` relative to the git top level, "" or ending in "/"."""
    rc, out = git(root, "rev-parse", "--show-prefix")
    return out.strip() if rc == 0 else ""


def _sha(path: str):
    try:
        with io.open(path, "rb") as fh:
            return hashlib.sha1(fh.read()).hexdigest()
    except OSError:
        return None


def dirty_paths(root: str) -> dict:
    """Every tracked-and-changed or untracked path under `root`:
    {relative path: content hash or None when the file is gone}."""
    rc, out = git(root, "status", "--porcelain=v1", "-z",
                  "--untracked-files=all", "--", ".")
    if rc != 0:
        raise RuntimeError(f"git status failed: {out.strip()[:300]}")
    prefix = repo_prefix(root)
    found = {}
    fields = out.split("\0")
    i = 0
    while i < len(fields):
        entry = fields[i]
        i += 1
        if len(entry) < 4:
            continue
        code, path = entry[:2], entry[3:]
        if code[0] in "RC":          # rename/copy: the next field is the source
            src = fields[i] if i < len(fields) else ""
            i += 1
            if src.startswith(prefix):
                found[src[len(prefix):]] = _sha(os.path.join(root, src[len(prefix):]))
        if path.startswith(prefix):
            rel = path[len(prefix):]
            found[rel] = _sha(os.path.join(root, rel))
    return found


def head(root: str) -> tuple:
    _, commit = git(root, "rev-parse", "HEAD")
    _, branch = git(root, "rev-parse", "--abbrev-ref", "HEAD")
    return commit.strip(), branch.strip()


def snapshot_generated(root: str, policy: dict) -> dict:
    """(size, mtime) of every file in the generated tree. git cannot see
    these, so this is the only way to notice the agent touched one."""
    snap = {}
    for gen in policy["generated_roots"]:
        base = os.path.join(root, gen)
        for dirpath, _dirs, files in os.walk(base):
            for name in files:
                p = os.path.join(dirpath, name)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                snap[os.path.relpath(p, root).replace("\\", "/")] = (
                    st.st_size, st.st_mtime_ns)
    return snap


def backup(root: str, paths, dest: str) -> None:
    """Copy files that were ALREADY changed before the agent ran, so a
    violation can put back exactly what was there, not just HEAD."""
    shutil.rmtree(dest, ignore_errors=True)
    for rel in paths:
        src = os.path.join(root, rel)
        if os.path.isfile(src):
            out = os.path.join(dest, rel)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            shutil.copy2(src, out)


def in_roots(rel: str, roots) -> bool:
    rel = rel.replace("\\", "/")
    return any(rel.startswith(r) for r in roots)


def classify(before: dict, after: dict, policy: dict) -> dict:
    """What the agent did, from two `dirty_paths` snapshots."""
    out = {"created": [], "modified": [], "deleted": [], "outside": []}
    for rel in sorted(set(before) | set(after)):
        b, a = before.get(rel, "absent"), after.get(rel, "absent")
        if b == a:
            continue
        if not in_roots(rel, policy["write_roots"]):
            out["outside"].append(rel)
        elif a is None:
            out["deleted"].append(rel)
        elif rel not in before:
            # Untracked now and absent before: the agent created it. A
            # tracked file it edited also appears here for the first time,
            # which `git ls-files` tells apart below.
            out["created"].append(rel)
        else:
            out["modified"].append(rel)
    return out


def split_created(root: str, changes: dict) -> dict:
    """Move tracked files out of `created`: they were clean, now edited."""
    still_new = []
    for rel in changes["created"]:
        rc, _ = git(root, "ls-files", "--error-unmatch", "--", rel)
        (changes["modified"] if rc == 0 else still_new).append(rel)
    changes["created"] = still_new
    changes["modified"].sort()
    return changes


def restore(root: str, rels, before: dict, backup_dir: str) -> list:
    """Put each path back to what it was before the agent ran."""
    notes = []
    for rel in rels:
        full = os.path.join(root, rel)
        saved = os.path.join(backup_dir, rel)
        if os.path.isfile(saved):                 # was already changed: exact copy
            os.makedirs(os.path.dirname(full), exist_ok=True)
            shutil.copy2(saved, full)
            notes.append(f"restored {rel} (your earlier uncommitted version)")
            continue
        rc, _ = git(root, "ls-files", "--error-unmatch", "--", rel)
        if rc == 0:                               # tracked and was clean
            rc2, out = git(root, "checkout", "--", rel)
            notes.append(f"restored {rel}" if rc2 == 0
                         else f"COULD NOT restore {rel}: {out.strip()[:120]}")
        elif rel not in before and os.path.isfile(full):   # the agent made it
            os.remove(full)
            notes.append(f"removed {rel} (created by the agent)")
    return notes


def generated_changes(before: dict, after: dict) -> list:
    return sorted(p for p in set(before) | set(after)
                  if before.get(p) != after.get(p))


# ---- the prompt ------------------------------------------------------

def _read(path: str, cap: int = 60000) -> str:
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read(cap)
    except OSError:
        return ""


def build_prompt(job_dir: str, policy: dict, repair: str = "") -> str:
    brief = _read(os.path.join(job_dir, "brief.md"))
    plan = _read(os.path.join(job_dir, "plan.md"))
    roots = "\n".join(f"  - {r}**" for r in policy["write_roots"])
    gen = "\n".join(f"  - {r}**" for r in policy["generated_roots"])
    parts = [
        "You are writing an API test in this repository's own framework "
        "(Java 17, REST Assured, TestNG). Follow the skill at "
        ".cursor/skills/jira-to-restassured/SKILL.md and the app skill it "
        "points to; read CreateTestCase.md for how a hand-written test is "
        "laid out.",
        "",
        "WHERE YOU MAY WRITE -- this is enforced after you finish, and a "
        "single file outside it fails the whole job and is reverted:",
        roots,
        "",
        "NEVER touch (converter output, regenerated on every convert):",
        gen,
        "  - src/test/resources/csv/<suite>/** other than csv/manual/",
        "",
        "Do not delete any file. Do not run git commit, git push, git "
        "checkout or git reset -- this tool commits, after a person has "
        "reviewed your work. Do not put a hostname, token, password or "
        "any value from src/main/resources/program_configuration.json in "
        "a file: read it through Config at run time. The repository is "
        "public.",
        "",
        "Act on the plan below. For a request the plan marks UPDATE, "
        "change the existing test it names only if that file is inside "
        "the paths above; otherwise write a new test and say so. For one "
        "it marks UPSTREAM or STOP, write nothing for it and say why.",
        "",
        "When you finish, reply with: the files you created or changed, "
        "one line each on what the test asserts, and anything in the "
        "plan you could not do.",
        "",
        "===== brief.md =====",
        brief or "(empty)",
        "",
        "===== plan.md =====",
        plan or "(empty)",
    ]
    if repair:
        parts += ["", "===== YOUR PREVIOUS ATTEMPT DID NOT VERIFY =====",
                  "Fix only what this output reports, within the same "
                  "paths. Do not start over.", "", repair[-12000:]]
    return "\n".join(parts)


# ---- verify ----------------------------------------------------------

def run_verify(root: str, policy: dict, runner=None) -> dict:
    """Compile, then the repository checks. {ok, steps:[{name, rc, tail}]}."""
    run = runner or (lambda argv: subprocess.run(
        argv, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT))
    steps = []
    compile_cmd = list(policy.get("compile") or [])
    if compile_cmd:
        exe = shutil.which(compile_cmd[0])
        if not exe:
            steps.append({"name": "compile", "rc": 127,
                          "tail": f"`{compile_cmd[0]}` is not on PATH. The "
                                  f"test cannot be called verified without "
                                  f"a compile."})
            return {"ok": False, "steps": steps}
        say(f" .. verify: {' '.join(compile_cmd)}")
        r = run([exe] + compile_cmd[1:])
        steps.append({"name": "compile", "rc": r.returncode,
                      "tail": _tail(r.stdout)})
        if r.returncode != 0:
            return {"ok": False, "steps": steps}
    for check in policy.get("checks") or []:
        say(f" .. verify: python {' '.join(check)}")
        r = run([sys.executable, "-B"] + list(check))
        steps.append({"name": check[0], "rc": r.returncode,
                      "tail": _tail(r.stdout)})
        if r.returncode != 0:
            return {"ok": False, "steps": steps}
    return {"ok": True, "steps": steps}


def _tail(raw, lines: int = 60) -> str:
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else (raw or "")
    return "\n".join(text.strip().splitlines()[-lines:])


# ---- the secret scan -------------------------------------------------

_SECRET_RX = [
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("bearer token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{24,}")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{6,}")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("JDBC URL with a host", re.compile(r"(?i)jdbc:[a-z0-9]+://[a-z0-9.-]+\.[a-z]{2,}")),
    ("credential assignment", re.compile(
        r"""(?ix)\b(pass(word|wd)?|secret|api[_-]?key|client[_-]?secret|token)\b
            \s*[:=]\s*["'][^"'\s${}#]{8,}["']""")),
]
_SECRET_KEY_RX = re.compile(
    r"(?i)(pass|secret|token|key|assertion|credential|c_sec|c_id|client_id)")
_HOST_RX = re.compile(r"(?i)\b(?:https?://)?((?:[a-z0-9-]+\.)+[a-z]{2,})\b")
_PUBLIC_HOSTS = ("example.com", "example.org", "localhost", "w3.org",
                 "apache.org", "github.com", "testng.org", "rest-assured.io")


def config_secrets(config_path: str) -> dict:
    """Values from the gitignored config that must never reach a commit:
    {value: why}. Hosts wherever they appear, and any value stored under a
    key that names a credential."""
    out = {}
    try:
        with io.open(config_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return out

    def walk(node, key=""):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, str(k))
        elif isinstance(node, list):
            for v in node:
                walk(v, key)
        elif isinstance(node, str):
            val = node.strip()
            for m in _HOST_RX.finditer(val):
                host = m.group(1).lower()
                if not host.endswith(_PUBLIC_HOSTS) and len(host) >= 8:
                    out[host] = f"host from program_configuration.json ({key})"
            if _SECRET_KEY_RX.search(key) and len(val) >= 8 \
                    and not val.startswith(("${", "#")):
                out[val] = f"value of `{key}` in program_configuration.json"

    walk(data)
    return out


def scan_secrets(diff: str, known: dict = None) -> list:
    """Findings in the ADDED lines of a diff: [(line, why)]. Empty = clean."""
    findings = []
    known = known or {}
    for raw in diff.splitlines():
        if not raw.startswith("+") or raw.startswith("+++"):
            continue
        line = raw[1:]
        low = line.lower()
        for label, rx in _SECRET_RX:
            if rx.search(line):
                findings.append((line.strip()[:120], label))
        for value, why in known.items():
            if value.lower() in low:
                findings.append((line.strip()[:120].replace(value, "<redacted>"), why))
    return findings


# ---- commands --------------------------------------------------------

def cmd_setup(job_dir: str, root: str) -> int:
    try:
        how = ensure_sdk(root)
        say(f" ok  cursor-sdk {how}")
    except RuntimeError as e:
        say(f"FAIL {e}")
        return 1
    _ca, cfg = cursor_config(root)
    if not cfg.api_key:
        say("FAIL no Cursor API key. Set CURSOR_API_KEY, or put it in "
            "tools/ra_converter/cursor_agent.json (gitignored; copy the "
            ".example beside it).")
        return 1
    say(f" ok  Cursor API key found ({len(cfg.api_key)} characters), "
        f"model {cfg.model}")
    rc, out = git(root, "rev-parse", "--is-inside-work-tree")
    if rc != 0:
        say(f"FAIL not a git work tree: {out.strip()[:200]}")
        return 1
    say(" ok  git work tree")
    return 0


def cmd_generate(job_dir: str, root: str, policy: dict, agent=None,
                 verifier=None) -> int:
    """Cursor writes; the tree is checked; the result is held for review."""
    prior = read_review(job_dir)
    if prior.get("state") == "pending-review":
        say("FAIL this job already has changes waiting for review. Approve "
            "and push them, or discard them, before generating again.")
        return 1
    if not _read(os.path.join(job_dir, "plan.md")).strip():
        say("FAIL no plan.md for this job. Run tab 1 (read it, then locate) "
            "first: the plan is what the agent is asked to act on.")
        return 1
    if agent is None:
        try:
            ensure_sdk(root)
        except RuntimeError as e:
            say(f"FAIL {e}")
            return 1
    call = agent or (lambda prompt: call_cursor(prompt, root))
    verify = verifier or (lambda: run_verify(root, policy))

    commit, branch = head(root)
    before = dirty_paths(root)
    stale = [p for p in before if in_roots(p, policy["write_roots"])]
    if stale:
        say("FAIL there are uncommitted changes inside the paths the agent "
            "writes to. Commit or remove them first -- otherwise its work "
            "and yours could not be told apart at review:\n  "
            + "\n  ".join(sorted(stale)[:20]))
        return 1
    backup_dir = os.path.join(job_dir, "pre")
    backup(root, before, backup_dir)
    gen_before = snapshot_generated(root, policy)
    review = write_review(job_dir, {
        "state": "generating", "base_commit": commit, "base_branch": branch,
        "started": _now()})

    attempts = 1 + max(0, int(policy.get("repair_attempts", 1)))
    repair = ""
    result = {}
    for attempt in range(1, attempts + 1):
        say(f" .. calling Cursor (attempt {attempt} of {attempts})")
        prompt = build_prompt(job_dir, policy, repair)
        with io.open(os.path.join(job_dir, f"agent-prompt-{attempt}.txt"),
                     "w", encoding="utf-8") as fh:
            fh.write(prompt)
        try:
            answer = call(prompt)
        except Exception as e:                           # noqa: BLE001
            say(f"FAIL Cursor call failed: {e}")
            _undo_everything(root, before, backup_dir, policy)
            write_review(job_dir, dict(review, state="rejected",
                                       reason=f"Cursor call failed: {e}"))
            return 1
        with io.open(os.path.join(job_dir, f"agent-answer-{attempt}.md"),
                     "w", encoding="utf-8") as fh:
            fh.write(str(answer.get("result") or ""))
        say(" .. Cursor finished; checking what it changed")

        problems = []
        commit_now, branch_now = head(root)
        if (commit_now, branch_now) != (commit, branch):
            problems.append(
                f"HEAD moved ({branch}@{commit[:8]} -> {branch_now}@"
                f"{commit_now[:8]}). The agent must not commit or switch "
                f"branches. Nothing was reverted for this: check `git "
                f"reflog` and put the branch back by hand.")
        gen_changed = generated_changes(gen_before, snapshot_generated(root, policy))
        if gen_changed:
            suites = sorted({_suite_of(p) for p in gen_changed} - {""})
            problems.append(
                f"{len(gen_changed)} file(s) in the generated tree were "
                f"changed, e.g. {gen_changed[0]}. git does not track them, "
                f"so they cannot be put back: reconvert "
                f"{', '.join(suites) or 'the affected suite'}.")
        changes = split_created(root, classify(before, dirty_paths(root), policy))
        if changes["outside"]:
            problems.append("files changed outside the allowed paths: "
                            + ", ".join(changes["outside"][:10]))
        if changes["deleted"]:
            problems.append("files deleted (this pipeline does not delete): "
                            + ", ".join(changes["deleted"][:10]))
        if problems:
            for p in problems:
                say(f"FAIL {p}")
            for note in _undo_everything(root, before, backup_dir, policy):
                say(f" .. {note}")
            write_review(job_dir, dict(review, state="rejected",
                                       reason=" | ".join(problems)))
            return 1
        if not changes["created"] and not changes["modified"]:
            say("FAIL the agent changed no file. Its answer is in "
                f"agent-answer-{attempt}.md -- it usually says why.")
            write_review(job_dir, dict(review, state="rejected",
                                       reason="the agent changed no file"))
            return 1

        files = changes["created"] + changes["modified"]
        say(f" ok  {len(changes['created'])} created, "
            f"{len(changes['modified'])} modified, all inside the allowed paths")
        result = verify()
        _write_diff(root, job_dir, changes)
        review = dict(review, files=changes, verify=result,
                      hashes={f: _sha(os.path.join(root, f)) for f in files})
        if result["ok"]:
            say(" ok  verified")
            write_review(job_dir, dict(review, state="pending-review"))
            say("PENDING REVIEW -- read proposed.diff. Nothing is committed "
                "and nothing has left this machine.")
            return 0
        failed = result["steps"][-1]
        say(f"FAIL verify step `{failed['name']}` exited {failed['rc']}")
        repair = f"$ {failed['name']}\n{failed['tail']}"
        # The repair attempt edits the files just written. `before` stays
        # the ORIGINAL snapshot, so the next classification still covers
        # everything the agent has done since the job started.
    write_review(job_dir, dict(review, state="verify-failed"))
    say("VERIFY FAILED after the repair attempt. The files are still in the "
        "working tree so you can look at them; `discard` removes them.")
    return 1


def _suite_of(rel: str) -> str:
    m = re.search(r"/(?:support|imported|classic|templates)/([^/]+)/", "/" + rel)
    return m.group(1) if m else ""


def _undo_everything(root: str, before: dict, backup_dir: str, policy: dict) -> list:
    """Put back every path the agent touched that git can see."""
    after = dirty_paths(root)
    touched = [p for p in sorted(set(before) | set(after))
               if before.get(p, "absent") != after.get(p, "absent")]
    return restore(root, touched, before, backup_dir)


def _write_diff(root: str, job_dir: str, changes: dict) -> str:
    """proposed.diff: tracked edits as git sees them, new files in full."""
    chunks = []
    if changes["modified"]:
        _, out = git(root, "diff", "--", *changes["modified"])
        chunks.append(out)
    for rel in changes["created"]:
        body = _read(os.path.join(root, rel), 400000)
        lines = body.splitlines()
        chunks.append(f"diff --git a/{rel} b/{rel}\nnew file\n--- /dev/null\n"
                      f"+++ b/{rel}\n@@ -0,0 +1,{len(lines)} @@\n"
                      + "\n".join("+" + l for l in lines) + "\n")
    text = "\n".join(chunks)
    with io.open(os.path.join(job_dir, "proposed.diff"), "w",
                 encoding="utf-8") as fh:
        fh.write(text)
    return text


def cmd_discard(job_dir: str, root: str) -> int:
    review = read_review(job_dir)
    if review.get("state") not in ("pending-review", "verify-failed"):
        say(f"FAIL nothing to discard (state: {review.get('state')})")
        return 1
    files = review.get("files") or {}
    for rel in files.get("created") or []:
        full = os.path.join(root, rel)
        if os.path.isfile(full):
            os.remove(full)
            say(f" .. removed {rel}")
    for rel in files.get("modified") or []:
        rc, out = git(root, "checkout", "--", rel)
        say(f" .. restored {rel}" if rc == 0
            else f" .. COULD NOT restore {rel}: {out.strip()[:120]}")
    write_review(job_dir, dict(review, state="discarded"))
    say("discarded")
    return 0


def cmd_push(job_dir: str, root: str, policy: dict, job: str, confirm: str,
             config_path: str = "") -> int:
    """Branch, commit and push what a person has reviewed. Never main."""
    review = read_review(job_dir)
    if review.get("state") not in ("pending-review", "push-failed"):
        say(f"FAIL this job is not waiting for review (state: "
            f"{review.get('state')}). Only a pending review can be pushed.")
        return 1
    if confirm != job:
        say("FAIL push needs --confirm with the job id: it is the approval.")
        return 1
    files = (review.get("files") or {})
    paths = list(files.get("created") or []) + list(files.get("modified") or [])
    if not paths:
        say("FAIL the review lists no files.")
        return 1
    branch = policy["branch_prefix"] + job
    if branch in policy["protected_branches"] or not policy["branch_prefix"]:
        say(f"FAIL refusing to push to `{branch}`.")
        return 1
    _commit, base = head(root)
    if base == "HEAD":
        say("FAIL detached HEAD: check out a branch first.")
        return 1

    # Scan BEFORE anything is committed: what is scanned is what is in the
    # working tree now, which may have been edited since the review.
    diff = _write_diff(root, job_dir, files)
    known = config_secrets(config_path or os.path.join(
        root, "src", "main", "resources", "program_configuration.json"))
    findings = scan_secrets(diff, known)
    if findings:
        say(f"FAIL secret scan: {len(findings)} finding(s). Nothing was "
            f"committed. This repository is public.")
        for line, why in findings[:20]:
            say(f"   {why}: {line}")
        write_review(job_dir, dict(review, state="pending-review",
                                   scan=[w for _l, w in findings]))
        return 1
    say(f" ok  secret scan clean ({len(known)} configured value(s) checked)")

    if review.get("state") == "push-failed" and review.get("branch") == branch:
        # The commit already exists on the local branch; only the push is owed.
        rc, out = git(root, "push", "-u", policy["remote"], branch)
        if rc != 0:
            say("FAIL push failed again:\n" + out.strip()[-400:])
            return 1
        write_review(job_dir, dict(review, state="pushed", pushed=_now()))
        say(f"PUSHED {branch} to {policy['remote']}.")
        return 0
    rc, out = git(root, "checkout", "-b", branch)
    if rc != 0:
        say(f"FAIL could not create branch {branch}: {out.strip()[:200]}. If "
            f"it is left from an earlier job, delete it or use a new job id.")
        return 1
    try:
        rc, out = git(root, "add", "--", *paths)
        if rc != 0:
            raise RuntimeError(f"git add failed: {out.strip()[:200]}")
        subject = f"test(agent): {job}"
        body = _commit_body(job_dir, files)
        rc, out = git(root, "commit", "-m", subject, "-m", body, "--", *paths)
        if rc != 0:
            raise RuntimeError(f"git commit failed: {out.strip()[:300]}")
        _, sha = git(root, "rev-parse", "HEAD")
        say(f" ok  committed {sha.strip()[:8]} on {branch}")
        rc, out = git(root, "push", "-u", policy["remote"], branch)
        if rc != 0:
            write_review(job_dir, dict(review, state="push-failed", branch=branch,
                                       commit=sha.strip(),
                                       reason=out.strip()[-400:]))
            say(f"FAIL push failed; the commit is kept on local branch "
                f"{branch}:\n{out.strip()[-400:]}")
            return 1
        write_review(job_dir, dict(review, state="pushed", branch=branch,
                                   commit=sha.strip(), pushed=_now()))
        say(f"PUSHED {branch} to {policy['remote']}. Open a pull request "
            f"from it; nothing was pushed to {base}.")
        return 0
    except RuntimeError as e:
        say(f"FAIL {e}")
        return 1
    finally:
        rc, out = git(root, "checkout", base)
        if rc != 0:
            say(f"WARN could not switch back to {base}: {out.strip()[:200]}")


def _commit_body(job_dir: str, files: dict) -> str:
    brief = _read(os.path.join(job_dir, "brief.md"), 1500).strip()
    listed = "\n".join(f"- {p}" for p in
                       (files.get("created") or []) + (files.get("modified") or []))
    return (f"Written by the Cursor agent from the workbench plan and "
            f"approved at review.\n\n{listed}\n\n{brief}").strip()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=("setup", "generate", "push", "discard"))
    ap.add_argument("--job", required=True)
    ap.add_argument("--confirm", default="")
    args = ap.parse_args(argv)
    job_dir = intake.job_dir(args.job)
    os.makedirs(job_dir, exist_ok=True)
    policy = load_policy()
    if args.command == "setup":
        return cmd_setup(job_dir, ROOT)
    if args.command == "generate":
        return cmd_generate(job_dir, ROOT, policy)
    if args.command == "discard":
        return cmd_discard(job_dir, ROOT)
    return cmd_push(job_dir, ROOT, policy, intake.safe_job(args.job), args.confirm)


if __name__ == "__main__":
    sys.exit(main())
