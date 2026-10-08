"""Tab 3: let Cursor change the code a plan describes, then hold it for review.

    python tools/agent/loop.py setup    --job <job>
    python tools/agent/loop.py generate --job <job> --scope new-test
    python tools/agent/loop.py generate --job <job> --scope converted --suite <suite>
    python tools/agent/loop.py generate --job <job> --scope converter
    python tools/agent/loop.py approve  --job <job> --confirm <job>
    python tools/agent/loop.py discard  --job <job>

THREE SCOPES -- what Cursor may write is chosen per job, never guessed
---------------------------------------------------------------------
new-test    a hand-written test: tests/jira/ and csv/manual/. Tracked.
            Approve = commit to branch agent/<job> and push it.
converted   the Java, templates and CSVs the converter produced for ONE
            suite. Gitignored, so approve cannot push them: it keeps them
            on this machine and saves converted.patch in the job folder.
            A reconvert of that suite overwrites them.
converter   the converter itself and the hand-maintained runtime under
            it. Tracked. Verified by the whole gate. Approve = branch +
            push.

    generate:  brief.md + plan.md  ->  Cursor  ->  guardrails  ->
               verify (one repair attempt)  ->  PENDING REVIEW
    approve:   new-test / converter: secret scan -> branch -> commit -> push
               converted:            kept locally, patch saved

THE GUARDRAILS ARE CODE, NOT PROMPT
-----------------------------------
The prompt tells the agent where it may write. That is advisory. What is
enforced is checked AFTER the agent returns, against the files on disk:

* a changed path outside the scope's write roots fails the job;
* a deleted file fails the job -- this pipeline does not delete;
* a moved HEAD or a switched branch fails the job: the agent commits
  nothing, this tool does.

On a failure everything is put back: tracked files from git (or from the
copy taken of your own uncommitted version), and the suite's generated
files from the copy taken before the run. The one thing that cannot be
put back is a generated file OUTSIDE the suite in scope -- nothing copied
it -- so the job names the suite to reconvert.

THE CURSOR LOG
--------------
Everything said to Cursor and everything it answered goes to
`target/agent/<job>/cursor.log`, separate from the command log: which
scope, which model, how long, the run id, the answer, the files it
changed and the verdict on them. The API key is never written.

The repository is PUBLIC. A push scans the diff for credentials and for
every host and secret value in the gitignored
`program_configuration.json`, and refuses rather than warns.
"""
from __future__ import annotations

import argparse
import difflib
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
import time
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

_MVN = ["mvn", "-q", "-DskipTests", "test-compile"]

DEFAULT_POLICY = {
    # Converter output. Gitignored, so git cannot see a change here; these
    # are watched by size+mtime around the agent run instead.
    "generated_roots": [
        "src/main/java/com/hi/api/support/",
        "src/main/java/com/hi/api/rest/clients/",
        "src/test/java/com/hi/api/tests/imported/",
        "src/test/java/com/hi/api/tests/classic/",
        "src/main/resources/templates/",
        "src/test/resources/csv/",
    ],
    # Tracked paths that sit inside a generated root.
    "generated_exclude": ["src/test/resources/csv/manual/"],
    "scopes": {
        "new-test": {
            "label": "A new hand-written test",
            "write_roots": ["src/test/java/com/hi/api/tests/jira/",
                            "src/test/resources/csv/manual/"],
            "pushable": True,
            "compile": _MVN,
            "checks": [["tools/check_no_duplicate_methods.py"]],
            "skill": ".cursor/skills/jira-to-restassured/SKILL.md",
        },
        "converted": {
            "label": "Converted Java of one suite (stays on this machine)",
            "needs_suite": True,
            # {suite} is the lower-case suite directory, {Suite} its
            # capitalised form (the client class name).
            "write_roots": [
                "src/main/java/com/hi/api/support/{suite}/",
                "src/test/java/com/hi/api/tests/imported/{suite}/",
                "src/main/resources/templates/{suite}/",
                "src/test/resources/csv/{suite}/",
                "src/main/java/com/hi/api/rest/clients/{Suite}Client.java",
            ],
            "pushable": False,
            "compile": _MVN,
            "checks": [["tools/check_no_duplicate_methods.py"]],
            "skill": ".cursor/skills/converted-app-environment/SKILL.md",
        },
        "converter": {
            "label": "The converter and the runtime under it",
            "write_roots": [
                "tools/ra_converter/",
                "tools/verify_all.py",
                "src/main/java/com/hi/api/rest/utilities/",
                "src/main/java/com/hi/api/rest/ApiRoutes.java",
                "src/main/java/com/hi/api/retry/",
                "src/main/java/com/hi/api/config/",
                "src/main/java/com/hi/api/data/",
                "src/main/java/com/hi/api/db/",
                "src/test/java/com/hi/api/tests/framework/",
                "src/test/resources/testng-guards.xml",
                "CreateTestCase.md",
            ],
            "pushable": True,
            "compile": _MVN,
            "checks": [],
            # The whole gate, run BEFORE the agent and again after it. The
            # change verifies when no check that passed before fails
            # after. (A plain pass/fail would never pass on a tree that
            # already has known failures -- a partly converted one does.)
            "gate": True,
            "skill": ".cursor/skills/readyapi-restassured-migration/SKILL.md",
        },
    },
    "repair_attempts": 1,
    "branch_prefix": "agent/",
    "remote": "origin",
    "protected_branches": ["main", "master"],
}

STATES = ("none", "generating", "rejected", "verify-failed",
          "pending-review", "pushed", "push-failed", "approved-local",
          "discarded")
_SUITE_RX = re.compile(r"^[a-z0-9_]{1,80}$")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def say(msg: str) -> None:
    print(msg, flush=True)


# ---- the cursor log --------------------------------------------------

class CursorLog:
    """Append-only record of the conversation with Cursor for one job."""

    def __init__(self, job_dir: str, secret: str = ""):
        self.path = os.path.join(job_dir, "cursor.log")
        self.secret = secret or ""
        os.makedirs(job_dir, exist_ok=True)

    def write(self, text: str = "") -> None:
        if self.secret and self.secret in text:
            text = text.replace(self.secret, "<redacted>")
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with io.open(self.path, "a", encoding="utf-8") as fh:
            for line in (text.splitlines() or [""]):
                fh.write(f"{stamp}  {line}\n")

    def block(self, title: str, body: str, cap: int = 20000) -> None:
        self.write(f"----- {title} -----")
        body = body or "(empty)"
        if len(body) > cap:
            body = body[:cap] + f"\n... ({len(body) - cap} more characters)"
        self.write(body)
        self.write(f"----- end {title} -----")


# ---- policy and state ------------------------------------------------

def _norm(roots) -> list:
    out = []
    for r in roots:
        r = r.replace("\\", "/")
        # A directory root ends in "/"; a single file is kept as it is.
        out.append(r if r.endswith("/") or "." in r.rsplit("/", 1)[-1] else r + "/")
    return out


def load_policy(path: str = "") -> dict:
    policy = json.loads(json.dumps(DEFAULT_POLICY))
    p = path or POLICY_FILE
    if os.path.isfile(p):
        with io.open(p, encoding="utf-8") as fh:
            loaded = json.load(fh) or {}
        scopes = loaded.pop("scopes", None) or {}
        policy.update(loaded)
        for name, spec in scopes.items():
            policy["scopes"].setdefault(name, {}).update(spec)
    policy["generated_roots"] = _norm(policy["generated_roots"])
    policy["generated_exclude"] = _norm(policy.get("generated_exclude") or [])
    for spec in policy["scopes"].values():
        spec["write_roots"] = _norm(spec.get("write_roots") or [])
    return policy


def resolve_scope(policy: dict, scope: str, suite: str, root: str) -> dict:
    """The chosen scope with {suite} filled in. Raises ValueError with a
    message for the page when the choice is not usable."""
    spec = policy["scopes"].get(scope)
    if spec is None:
        raise ValueError(f"unknown scope {scope!r}. Known: "
                         f"{', '.join(sorted(policy['scopes']))}")
    out = dict(spec, name=scope, suite="")
    if spec.get("needs_suite"):
        suite = (suite or "").strip().lower()
        if not _SUITE_RX.match(suite):
            raise ValueError(
                f"scope `{scope}` needs --suite: the lower-case suite "
                f"directory, e.g. amexbackbook. Got {suite!r}.")
        cap = suite[:1].upper() + suite[1:]
        roots = [r.replace("{suite}", suite).replace("{Suite}", cap)
                 for r in spec["write_roots"]]
        if not any(os.path.exists(os.path.join(root, r)) for r in roots):
            raise ValueError(
                f"suite `{suite}` has no converted files here (looked for "
                f"{roots[0]}). Convert it first, or check the name.")
        out.update(write_roots=roots, suite=suite)
    return out


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
    out["model"] = cfg.model
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


def in_roots(rel: str, roots) -> bool:
    rel = rel.replace("\\", "/")
    return any(rel == r or (r.endswith("/") and rel.startswith(r)) for r in roots)


def snapshot_generated(root: str, policy: dict) -> dict:
    """(size, mtime) of every file in the generated tree. git cannot see
    these, so this is the only way to notice the agent touched one."""
    snap = {}
    for gen in policy["generated_roots"]:
        base = os.path.join(root, gen)
        for dirpath, _dirs, files in os.walk(base):
            for name in files:
                p = os.path.join(dirpath, name)
                rel = os.path.relpath(p, root).replace("\\", "/")
                if in_roots(rel, policy["generated_exclude"]):
                    continue
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                snap[rel] = (st.st_size, st.st_mtime_ns)
    return snap


def backup(root: str, paths, dest: str) -> None:
    """Copy files so a violation can put back exactly what was there."""
    shutil.rmtree(dest, ignore_errors=True)
    for rel in paths:
        src = os.path.join(root, rel)
        if os.path.isfile(src):
            out = os.path.join(dest, rel)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            shutil.copy2(src, out)


def classify_tracked(before: dict, after: dict, roots) -> dict:
    """What changed among the paths git can see."""
    out = {"created": [], "modified": [], "deleted": [], "outside": []}
    for rel in sorted(set(before) | set(after)):
        b, a = before.get(rel, "absent"), after.get(rel, "absent")
        if b == a:
            continue
        if not in_roots(rel, roots):
            out["outside"].append(rel)
        elif a is None:
            out["deleted"].append(rel)
        elif rel not in before:
            out["created"].append(rel)      # or a clean tracked file, see below
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


def classify_generated(before: dict, after: dict, roots) -> dict:
    """What changed in the generated tree, from two stat snapshots."""
    out = {"created": [], "modified": [], "deleted": [], "outside": []}
    for rel in sorted(set(before) | set(after)):
        if before.get(rel) == after.get(rel):
            continue
        if not in_roots(rel, roots):
            out["outside"].append(rel)
        elif rel not in after:
            out["deleted"].append(rel)
        elif rel not in before:
            out["created"].append(rel)
        else:
            out["modified"].append(rel)
    return out


def restore_tracked(root: str, rels, before: dict, backup_dir: str) -> list:
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


def restore_generated(root: str, rels, backup_dir: str) -> list:
    """Generated files: from the copy taken before the run, or -- when the
    agent created the file -- removed."""
    notes = []
    for rel in rels:
        full = os.path.join(root, rel)
        saved = os.path.join(backup_dir, rel)
        if os.path.isfile(saved):
            os.makedirs(os.path.dirname(full), exist_ok=True)
            shutil.copy2(saved, full)
            notes.append(f"restored {rel}")
        elif os.path.isfile(full):
            os.remove(full)
            notes.append(f"removed {rel} (created by the agent)")
    return notes


# ---- the prompt ------------------------------------------------------

def _read(path: str, cap: int = 60000) -> str:
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read(cap)
    except OSError:
        return ""


_SCOPE_BRIEFING = {
    "new-test": (
        "You are writing an API test in this repository's own framework "
        "(Java 17, REST Assured, TestNG). Read CreateTestCase.md for how a "
        "hand-written test is laid out."),
    "converted": (
        "You are changing Java that the ReadyAPI converter already "
        "generated for suite `{suite}` (Java 17, REST Assured, TestNG). "
        "Make the smallest change that does what the plan asks; keep the "
        "generated structure (Specs, Hooks, Phases, Steps, CSV columns) "
        "and its naming. These files are overwritten by the next convert "
        "of this suite, so do not restructure them."),
    "converter": (
        "You are changing the ReadyAPI-to-REST-Assured converter "
        "(tools/ra_converter, Python) or the hand-maintained Java runtime "
        "under it. Fix the emitter, never a generated copy. A bundled "
        "file under tools/ra_converter/framework/ carries a "
        "`ra_converter-framework-rev: N` line: bump it and update the "
        "matching hash in tools/ra_converter/test_converter_fixes.py when "
        "you edit one. Add or extend a test_*.py for the change and "
        "register a new one in tools/verify_all.py."),
}


def build_prompt(job_dir: str, policy: dict, scope: dict, repair: str = "") -> str:
    brief = _read(os.path.join(job_dir, "brief.md"))
    plan = _read(os.path.join(job_dir, "plan.md"))
    roots = "\n".join(f"  - {r}{'**' if r.endswith('/') else ''}"
                      for r in scope["write_roots"])
    gen = "\n".join(f"  - {r}**" for r in policy["generated_roots"]
                    if not in_roots(r, scope["write_roots"]))
    briefing = _SCOPE_BRIEFING.get(scope["name"], _SCOPE_BRIEFING["new-test"])
    parts = [
        briefing.replace("{suite}", scope.get("suite") or ""),
        f"Follow the skill at {scope.get('skill') or '.cursor/skills/'} "
        f"and any app skill it points to.",
        "",
        "WHERE YOU MAY WRITE -- this is enforced after you finish, and a "
        "single file outside it fails the whole job and is reverted:",
        roots,
        "",
        "NEVER touch:",
        gen + ("\n  (other than the paths listed above)" if scope["name"] == "converted" else ""),
        "",
        "Do not delete any file. Do not run git commit, git push, git "
        "checkout or git reset -- this tool commits, after a person has "
        "reviewed your work. Do not put a hostname, token, password or "
        "any value from src/main/resources/program_configuration.json in "
        "a file: read it through Config at run time. The repository is "
        "public.",
        "",
        "Act on the plan below. For a request the plan marks UPSTREAM or "
        "STOP, write nothing for it and say why.",
        "",
        "When you finish, reply with: the files you created or changed, "
        "one line each on what changed and why, and anything in the plan "
        "you could not do.",
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

def gate_failures(root: str, out_json: str, runner=None) -> dict:
    """Run the gate; {check name: how to reproduce} for every failing check."""
    run = runner or (lambda argv: subprocess.run(
        argv, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT))
    try:
        os.remove(out_json)
    except OSError:
        pass
    run([sys.executable, "-B", os.path.join("tools", "verify_all.py"),
         "--json", out_json])
    try:
        with io.open(out_json, encoding="utf-8") as fh:
            checks = json.load(fh).get("checks") or []
    except (OSError, ValueError) as e:
        raise RuntimeError(f"the gate produced no result file ({e}). Run "
                           f"`python tools/verify_all.py` by hand to see why.")
    return {c.get("name", "?"): c.get("repro", "") for c in checks
            if c.get("status") == "fail"}


def run_verify(root: str, scope: dict, runner=None, gate_before=None,
               gate_json: str = "") -> dict:
    """Compile, then the scope's checks. {ok, steps:[{name, rc, tail}]}."""
    run = runner or (lambda argv: subprocess.run(
        argv, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT))
    steps = []
    compile_cmd = list(scope.get("compile") or [])
    if compile_cmd:
        exe = shutil.which(compile_cmd[0])
        if not exe:
            steps.append({"name": "compile", "rc": 127,
                          "tail": f"`{compile_cmd[0]}` is not on PATH. The "
                                  f"change cannot be called verified without "
                                  f"a compile."})
            return {"ok": False, "steps": steps}
        say(f" .. verify: {' '.join(compile_cmd)}")
        r = run([exe] + compile_cmd[1:])
        steps.append({"name": "compile", "rc": r.returncode,
                      "tail": _tail(r.stdout)})
        if r.returncode != 0:
            return {"ok": False, "steps": steps}
    for check in scope.get("checks") or []:
        say(f" .. verify: python {' '.join(check)}")
        r = run([sys.executable, "-B"] + list(check))
        steps.append({"name": " ".join(check), "rc": r.returncode,
                      "tail": _tail(r.stdout)})
        if r.returncode != 0:
            return {"ok": False, "steps": steps}
    if scope.get("gate"):
        say(" .. verify: the gate (python tools/verify_all.py), compared "
            "with the run taken before the agent")
        try:
            after = gate_failures(root, gate_json, runner)
        except RuntimeError as e:
            steps.append({"name": "gate", "rc": 1, "tail": str(e)})
            return {"ok": False, "steps": steps}
        new = {n: r for n, r in after.items() if n not in (gate_before or {})}
        tail = "\n".join(f"NEW failing check: {n}\n    {r}" for n, r in sorted(new.items()))
        known = sorted(set(after) - set(new))
        if known:
            tail += ("\n" if tail else "") + "already failing before the change: " + ", ".join(known)
        steps.append({"name": "gate: no check that passed before fails now",
                      "rc": 1 if new else 0, "tail": tail})
        if new:
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
    CursorLog(job_dir, cfg.api_key).write(
        f"setup ok: cursor-sdk {how}, model {cfg.model}")
    return 0


def cmd_generate(job_dir: str, root: str, policy: dict, scope_name: str = "new-test",
                 suite: str = "", agent=None, verifier=None) -> int:
    """Cursor writes; the files are checked; the result is held for review."""
    prior = read_review(job_dir)
    if prior.get("state") == "pending-review":
        say("FAIL this job already has changes waiting for review. Approve "
            "them or discard them before generating again.")
        return 1
    if not _read(os.path.join(job_dir, "plan.md")).strip():
        say("FAIL no plan.md for this job. Run tab 1 (read it, then locate) "
            "first: the plan is what the agent is asked to act on.")
        return 1
    try:
        scope = resolve_scope(policy, scope_name, suite, root)
    except ValueError as e:
        say(f"FAIL {e}")
        return 1
    secret = ""
    if agent is None:
        try:
            ensure_sdk(root)
            secret = cursor_config(root)[1].api_key
        except RuntimeError as e:
            say(f"FAIL {e}")
            return 1
    call = agent or (lambda prompt: call_cursor(prompt, root))
    clog = CursorLog(job_dir, secret)
    roots = scope["write_roots"]
    gate_before = {}
    if verifier is None and scope.get("gate"):
        say(" .. running the gate once BEFORE the agent, to know what "
            "already fails (a minute or two)")
        try:
            gate_before = gate_failures(root, os.path.join(job_dir, "gate-before.json"))
        except RuntimeError as e:
            say(f"FAIL {e}")
            return 1
        say(f" .. {len(gate_before)} check(s) fail before the change: "
            + (", ".join(sorted(gate_before)) or "none"))
    verify = verifier or (lambda: run_verify(
        root, scope, gate_before=gate_before,
        gate_json=os.path.join(job_dir, "gate-after.json")))

    commit, branch = head(root)
    before = dirty_paths(root)
    stale = [p for p in before if in_roots(p, roots)]
    if stale:
        say("FAIL there are uncommitted changes inside the paths the agent "
            "writes to. Commit or remove them first -- otherwise its work "
            "and yours could not be told apart at review:\n  "
            + "\n  ".join(sorted(stale)[:20]))
        return 1
    backup_dir = os.path.join(job_dir, "pre")
    backup(root, before, backup_dir)
    gen_before = snapshot_generated(root, policy)
    # The generated files this scope may change, copied so they can be
    # diffed and put back. Only the suite in scope: a few megabytes.
    gen_backup = os.path.join(job_dir, "pre-generated")
    backup(root, [p for p in gen_before if in_roots(p, roots)], gen_backup)

    review = write_review(job_dir, {
        "state": "generating", "scope": scope["name"], "suite": scope["suite"],
        "pushable": bool(scope.get("pushable")), "base_commit": commit,
        "base_branch": branch, "started": _now()})
    clog.write("=" * 72)
    clog.write(f"generate  job={os.path.basename(job_dir)}  scope={scope['name']}"
               + (f"  suite={scope['suite']}" if scope["suite"] else "")
               + f"  base={branch}@{commit[:8]}")
    clog.write("may write: " + ", ".join(roots))

    def undo() -> list:
        after = dirty_paths(root)
        touched = [p for p in sorted(set(before) | set(after))
                   if before.get(p, "absent") != after.get(p, "absent")]
        notes = restore_tracked(root, touched, before, backup_dir)
        g = classify_generated(gen_before, snapshot_generated(root, policy), roots)
        notes += restore_generated(
            root, g["created"] + g["modified"] + g["deleted"], gen_backup)
        return notes

    def reject(reason: str, restore: bool = True) -> int:
        if restore:
            for note in undo():
                say(f" .. {note}")
                clog.write(f"undo: {note}")
        clog.write(f"REJECTED: {reason}")
        write_review(job_dir, dict(review, state="rejected", reason=reason))
        return 1

    attempts = 1 + max(0, int(policy.get("repair_attempts", 1)))
    repair = ""
    for attempt in range(1, attempts + 1):
        say(f" .. calling Cursor (attempt {attempt} of {attempts}) -- "
            f"the conversation is in cursor.log")
        prompt = build_prompt(job_dir, policy, scope, repair)
        with io.open(os.path.join(job_dir, f"agent-prompt-{attempt}.txt"),
                     "w", encoding="utf-8") as fh:
            fh.write(prompt)
        clog.write(f"attempt {attempt} of {attempts}: prompt {len(prompt)} "
                   f"characters (agent-prompt-{attempt}.txt)")
        clog.block("prompt", prompt, cap=6000)
        started = time.time()
        try:
            answer = call(prompt)
        except Exception as e:                           # noqa: BLE001
            say(f"FAIL Cursor call failed: {e}")
            clog.write(f"call FAILED after {time.time() - started:.1f}s: {e}")
            return reject(f"Cursor call failed: {e}")
        text = str(answer.get("result") or "")
        with io.open(os.path.join(job_dir, f"agent-answer-{attempt}.md"),
                     "w", encoding="utf-8") as fh:
            fh.write(text)
        clog.write(f"answered in {time.time() - started:.1f}s  "
                   f"status={answer.get('status', '')}  "
                   f"run id={answer.get('id', '')}  "
                   f"model={answer.get('model', '')}")
        clog.block("answer", text)
        say(" .. Cursor finished; checking what it changed")

        commit_now, branch_now = head(root)
        if (commit_now, branch_now) != (commit, branch):
            msg = (f"HEAD moved ({branch}@{commit[:8]} -> {branch_now}@"
                   f"{commit_now[:8]}). The agent must not commit or switch "
                   f"branches. Nothing was reverted for this: check `git "
                   f"reflog` and put the branch back by hand.")
            say(f"FAIL {msg}")
            return reject(msg, restore=False)

        tracked = split_created(root, classify_tracked(before, dirty_paths(root), roots))
        gen = classify_generated(gen_before, snapshot_generated(root, policy), roots)
        changes = {k: sorted(tracked[k] + gen[k]) for k in
                   ("created", "modified", "deleted")}
        clog.write(f"changed: {len(changes['created'])} created, "
                   f"{len(changes['modified'])} modified, "
                   f"{len(changes['deleted'])} deleted, "
                   f"{len(tracked['outside']) + len(gen['outside'])} outside")
        for kind in ("created", "modified", "deleted"):
            for p in changes[kind]:
                clog.write(f"  {kind:9s} {p}")

        problems = []
        if gen["outside"]:
            suites = sorted({_suite_of(p) for p in gen["outside"]} - {""})
            problems.append(
                f"{len(gen['outside'])} generated file(s) outside this "
                f"scope were changed, e.g. {gen['outside'][0]}. Nothing "
                f"copied them, so they cannot be put back: reconvert "
                f"{', '.join(suites) or 'the affected suite'}.")
        if tracked["outside"]:
            problems.append("files changed outside the allowed paths: "
                            + ", ".join(tracked["outside"][:10]))
        if changes["deleted"]:
            problems.append("files deleted (this pipeline does not delete): "
                            + ", ".join(changes["deleted"][:10]))
        if problems:
            for p in problems:
                say(f"FAIL {p}")
            return reject(" | ".join(problems))
        if not changes["created"] and not changes["modified"]:
            say("FAIL the agent changed no file. Its answer is in "
                "cursor.log -- it usually says why.")
            return reject("the agent changed no file", restore=False)

        say(f" ok  {len(changes['created'])} created, "
            f"{len(changes['modified'])} modified, all inside the allowed paths")
        generated = sorted(gen["created"] + gen["modified"])
        result = verify()
        for step in result["steps"]:
            clog.write(f"verify {'ok  ' if step['rc'] == 0 else 'FAIL'} {step['name']}")
        _write_diff(root, job_dir, changes, generated, gen_backup)
        files = changes["created"] + changes["modified"]
        review = dict(review, files={k: changes[k] for k in ("created", "modified")},
                      generated=generated, verify=result,
                      hashes={f: _sha(os.path.join(root, f)) for f in files})
        if result["ok"]:
            say(" ok  verified")
            write_review(job_dir, dict(review, state="pending-review"))
            clog.write("PENDING REVIEW")
            say("PENDING REVIEW -- read proposed.diff. Nothing is committed "
                "and nothing has left this machine.")
            return 0
        failed = result["steps"][-1]
        say(f"FAIL verify step `{failed['name']}` exited {failed['rc']}")
        clog.block(f"verify output: {failed['name']}", failed["tail"], cap=8000)
        repair = f"$ {failed['name']}\n{failed['tail']}"
        # The repair attempt edits the files just written. `before` stays
        # the ORIGINAL snapshot, so the next classification still covers
        # everything the agent has done since the job started.
    write_review(job_dir, dict(review, state="verify-failed"))
    clog.write("VERIFY FAILED after the repair attempt")
    say("VERIFY FAILED after the repair attempt. The files are still in "
        "place so you can look at them; `discard` puts them back.")
    return 1


def _suite_of(rel: str) -> str:
    m = re.search(r"/(?:support|imported|classic|templates|csv)/([^/]+)/", "/" + rel)
    return m.group(1) if m else ""


def _write_diff(root: str, job_dir: str, changes: dict, generated=(),
                gen_backup: str = "") -> str:
    """proposed.diff. Tracked edits as git sees them; new files in full;
    generated files against the copy taken before the run."""
    generated = set(generated)
    chunks = []
    tracked_mod = [p for p in changes["modified"] if p not in generated]
    if tracked_mod:
        _, out = git(root, "diff", "--", *tracked_mod)
        chunks.append(out)
    for rel in changes["modified"]:
        if rel in generated:
            old = _read(os.path.join(gen_backup, rel), 2_000_000).splitlines()
            new = _read(os.path.join(root, rel), 2_000_000).splitlines()
            chunks.append("\n".join(difflib.unified_diff(
                old, new, f"a/{rel}", f"b/{rel}", lineterm="")) + "\n")
    for rel in changes["created"]:
        lines = _read(os.path.join(root, rel), 400000).splitlines()
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
    generated = set(review.get("generated") or [])
    gen_backup = os.path.join(job_dir, "pre-generated")
    for rel in files.get("created") or []:
        full = os.path.join(root, rel)
        if os.path.isfile(full):
            os.remove(full)
            say(f" .. removed {rel}")
    for rel in files.get("modified") or []:
        if rel in generated:
            for note in restore_generated(root, [rel], gen_backup):
                say(f" .. {note}")
            continue
        rc, out = git(root, "checkout", "--", rel)
        say(f" .. restored {rel}" if rc == 0
            else f" .. COULD NOT restore {rel}: {out.strip()[:120]}")
    write_review(job_dir, dict(review, state="discarded"))
    CursorLog(job_dir).write("DISCARDED by the reviewer")
    say("discarded")
    return 0


def cmd_approve(job_dir: str, root: str, policy: dict, job: str, confirm: str,
                config_path: str = "") -> int:
    """The review button. A pushable scope is committed to its own branch
    and pushed; converted Java is kept locally with a saved patch."""
    review = read_review(job_dir)
    if review.get("state") not in ("pending-review", "push-failed"):
        say(f"FAIL this job is not waiting for review (state: "
            f"{review.get('state')}). Only a pending review can be approved.")
        return 1
    if confirm != job:
        say("FAIL approve needs --confirm with the job id: it is the approval.")
        return 1
    files = (review.get("files") or {})
    paths = list(files.get("created") or []) + list(files.get("modified") or [])
    if not paths:
        say("FAIL the review lists no files.")
        return 1
    clog = CursorLog(job_dir)
    generated = review.get("generated") or []
    gen_backup = os.path.join(job_dir, "pre-generated")
    diff = _write_diff(root, job_dir, files, generated, gen_backup)

    if not review.get("pushable", True):
        patch = os.path.join(job_dir, "converted.patch")
        with io.open(patch, "w", encoding="utf-8") as fh:
            fh.write(diff)
        write_review(job_dir, dict(review, state="approved-local",
                                   approved=_now(), patch="converted.patch"))
        clog.write(f"APPROVED (kept locally): {len(paths)} file(s), "
                   f"patch saved as converted.patch")
        say(f"APPROVED -- {len(paths)} converted file(s) kept on this "
            f"machine. They are gitignored, so nothing was committed or "
            f"pushed. A reconvert of `{review.get('suite')}` will overwrite "
            f"them; the change is saved as "
            f"target/agent/{job}/converted.patch.")
        return 0

    branch = policy["branch_prefix"] + job
    if branch in policy["protected_branches"] or not policy["branch_prefix"]:
        say(f"FAIL refusing to push to `{branch}`.")
        return 1
    _commit, base = head(root)
    if base == "HEAD":
        say("FAIL detached HEAD: check out a branch first.")
        return 1

    # Scan BEFORE anything is committed: what is scanned is what is on
    # disk now, which may have been edited since the review.
    known = config_secrets(config_path or os.path.join(
        root, "src", "main", "resources", "program_configuration.json"))
    findings = scan_secrets(diff, known)
    if findings:
        say(f"FAIL secret scan: {len(findings)} finding(s). Nothing was "
            f"committed. This repository is public.")
        for line, why in findings[:20]:
            say(f"   {why}: {line}")
        clog.write(f"push REFUSED: secret scan, {len(findings)} finding(s)")
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
        clog.write(f"PUSHED {branch}")
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
        subject = f"{'fix' if review.get('scope') == 'converter' else 'test'}(agent): {job}"
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
            clog.write(f"push FAILED; commit {sha.strip()[:8]} kept on {branch}")
            say(f"FAIL push failed; the commit is kept on local branch "
                f"{branch}:\n{out.strip()[-400:]}")
            return 1
        write_review(job_dir, dict(review, state="pushed", branch=branch,
                                   commit=sha.strip(), pushed=_now()))
        clog.write(f"APPROVED and PUSHED {branch} ({sha.strip()[:8]})")
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
    ap.add_argument("command",
                    choices=("setup", "generate", "approve", "push", "discard"))
    ap.add_argument("--job", required=True)
    ap.add_argument("--scope", default="new-test")
    ap.add_argument("--suite", default="")
    ap.add_argument("--confirm", default="")
    args = ap.parse_args(argv)
    job_dir = intake.job_dir(args.job)
    os.makedirs(job_dir, exist_ok=True)
    policy = load_policy()
    if args.command == "setup":
        return cmd_setup(job_dir, ROOT)
    if args.command == "generate":
        return cmd_generate(job_dir, ROOT, policy, args.scope, args.suite)
    if args.command == "discard":
        return cmd_discard(job_dir, ROOT)
    return cmd_approve(job_dir, ROOT, policy, intake.safe_job(args.job),
                       args.confirm)


if __name__ == "__main__":
    sys.exit(main())
