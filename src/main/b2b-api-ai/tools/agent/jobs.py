"""Run one command for one job, and keep every byte of it.

The UI needs three things from a long-running command: start it without
blocking, read the log while it runs, and know afterwards whether it
worked. All three come from the same place here -- the job directory --
so the page holds no state of its own and a refresh loses nothing.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
It does not accept a command STRING. Every runnable is named here, and
the caller chooses arguments from a declared set. A UI that posted a
shell string would be a remote shell on whatever machine serves the
page, and the page is meant to be reachable by a team.

Each runnable also declares which arguments are PATHS. Those are
resolved against the repository root and refused if they escape it, so
`--output ../../somewhere` cannot be used to write outside the tree.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))

_spec = importlib.util.spec_from_file_location(
    "jobs_intake", os.path.join(HERE, "intake.py"))
intake = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(intake)

ROOT = intake.ROOT
PY = sys.executable

# Every command the UI may start, by name. The value is the argv prefix;
# the caller may only append arguments the runnable declares.
RUNNABLES = {
    "convert": {
        "argv": [PY, "-B", os.path.join("tools", "ra_converter",
                                        "ra_converter.py")],
        "label": "Convert ReadyAPI suites",
        # name -> kind. "path" is resolved and confined to the repo.
        "options": {
            "--input": "path", "--output": "path", "--data-dir": "path",
            "--config": "path", "--cursor-config": "path",
            "--package-root": "text", "--service-name": "text",
            "--envs": "text", "--suite-name": "text",
            "--diagram-cases": "text", "--max-name-len": "text",
            "--clean": "flag", "--classic": "flag", "--diagram-png": "flag",
            "--diagrams-only": "flag", "--phase-specs": "flag",
            "--no-phase-specs": "flag", "--cursor-assist": "flag",
            "--no-cursor-assist": "flag", "--skip-self-test": "flag",
            "--bootstrap": "flag", "--skip-dataflow-check": "flag",
            "--keep-dead-props": "flag", "--no-reapply-patches": "flag",
        },
    },
    "intake": {
        "argv": [PY, "-B", os.path.join("tools", "agent", "intake.py")],
        "label": "Read the pasted story and links",
        "options": {"--job": "text", "--text-file": "path", "--link": "text",
                    "--api-path": "text"},
    },
    "locate": {
        "argv": [PY, "-B", os.path.join("tools", "agent", "locate.py")],
        "label": "Decide create / update / upstream",
        "options": {"--job": "text", "--index": "path",
                    "--rebuild-index": "flag"},
    },
    "audit-service-keys": {
        "argv": [PY, "-B", os.path.join("tools", "audit_service_keys.py")],
        "label": "Audit service keys",
        "options": {"--config": "path", "--input": "path", "--root": "path"},
    },
    "audit-token-chain": {
        "argv": [PY, "-B", os.path.join("tools", "audit_token_chain.py")],
        "label": "Audit the token chain",
        "options": {"--config": "path", "--root": "path"},
    },
    # Reads only, and writes only the job directory: Cursor runs in an
    # empty temporary directory. The three files are written by the
    # server from what was pasted (see /api/design).
    "agent-design": {
        "argv": [PY, "-B", os.path.join("tools", "agent", "design.py")],
        "label": "Design the API test cases (Cursor, read-only)",
        "options": {"--job": "text", "--service": "text", "--speed": "text",
                    "--swagger-file": "path", "--requirements-file": "path",
                    "--notes-file": "path"},
    },
    # Tab 3. Fixed sub-commands of one script; the page chooses which,
    # names the job, and for `generate` picks a scope the script itself
    # validates. `agent-approve` is the only runnable that can reach the
    # network, and it refuses without --confirm <job>.
    "agent-setup": {
        "argv": [PY, "-B", os.path.join("tools", "agent", "loop.py"), "setup"],
        "label": "Check Cursor: SDK, key, git",
        "options": {"--job": "text"},
    },
    "agent-generate": {
        "argv": [PY, "-B", os.path.join("tools", "agent", "loop.py"), "generate"],
        "label": "Cursor makes the change, then it is verified",
        "options": {"--job": "text", "--scope": "text", "--suite": "text"},
    },
    "agent-approve": {
        "argv": [PY, "-B", os.path.join("tools", "agent", "loop.py"), "approve"],
        "label": "Approve: push a branch, or keep converted Java locally",
        "options": {"--job": "text", "--confirm": "text"},
    },
    "agent-reapply": {
        "argv": [PY, "-B", os.path.join("tools", "agent", "patches.py"), "reapply"],
        "label": "Put a suite's approved changes back (after a convert)",
        "options": {"--suite": "text"},
    },
    "agent-patches": {
        "argv": [PY, "-B", os.path.join("tools", "agent", "patches.py"), "list"],
        "label": "List the approved changes that are stored",
        "options": {"--suite": "text"},
    },
    "agent-discard": {
        "argv": [PY, "-B", os.path.join("tools", "agent", "loop.py"), "discard"],
        "label": "Discard what the agent wrote",
        "options": {"--job": "text"},
    },
}

# Runnables that write the working tree or judge it by what changed. A
# convert during an agent run would look like the agent rewriting every
# suite; an agent run during a convert would "put back" half-written
# files.
EXCLUSIVE = {"convert", "agent-generate", "agent-approve", "agent-discard",
             "agent-reapply"}

# The only options that may be given more than once.
REPEATABLE = {"--link"}

_RUNNING: dict = {}
_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def confine(path_value: str) -> str:
    """A caller-supplied path, resolved and kept inside the repository.

    The UI is a form, and a form field reaches this as text. Without
    this, `--output ../../..` writes wherever the server process can.
    """
    raw = (path_value or "").strip()
    if not raw:
        raise ValueError("empty path")
    candidate = raw if os.path.isabs(raw) else os.path.join(ROOT, raw)
    resolved = os.path.realpath(candidate)
    root = os.path.realpath(ROOT)
    if resolved != root and not resolved.startswith(root + os.sep):
        raise ValueError(
            f"{raw!r} resolves outside the repository. Paths are confined to "
            f"{root} -- give one relative to it.")
    return resolved


def build_argv(runnable: str, options: dict) -> list:
    """argv for a declared runnable, from declared options only."""
    spec = RUNNABLES.get(runnable)
    if spec is None:
        raise ValueError(f"unknown runnable {runnable!r}. "
                         f"Known: {', '.join(sorted(RUNNABLES))}")
    argv = list(spec["argv"])
    for name, value in (options or {}).items():
        kind = spec["options"].get(name)
        if kind is None:
            raise ValueError(
                f"{runnable} does not accept {name!r}. A command is never "
                f"assembled from free text here.")
        if kind == "flag":
            if value:
                argv.append(name)
            continue
        if isinstance(value, list) and name not in REPEATABLE:
            raise ValueError(f"{name} takes one value")
        values = value if isinstance(value, list) else [value]
        for v in values:
            if v is None or str(v).strip() == "":
                continue
            argv.append(name)
            argv.append(confine(str(v)) if kind == "path" else str(v))
    return argv


def job_dir(job: str) -> str:
    return intake.job_dir(job)


# Logs that are not a runnable's own output but may be read like one.
EXTRA_LOGS = {"cursor"}       # cursor.log: the conversation with Cursor


def _known(runnable: str) -> str:
    """`runnable` becomes part of a file name, and it arrives from a query
    string. Only declared names: `../x` must not pick another file."""
    if runnable not in RUNNABLES and runnable not in EXTRA_LOGS:
        raise ValueError(f"unknown runnable {runnable!r}")
    return runnable


def status_path(job: str, runnable: str) -> str:
    return os.path.join(job_dir(job), f"{_known(runnable)}.status.json")


def log_path(job: str, runnable: str) -> str:
    return os.path.join(job_dir(job), f"{_known(runnable)}.log")


def read_status(job: str, runnable: str) -> dict:
    _known(runnable)
    try:
        with io.open(status_path(job, runnable), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"state": "none"}


def read_log(job: str, runnable: str, offset: int = 0) -> dict:
    """Bytes from `offset` on, so the page can poll without re-reading.

    A re-run TRUNCATES the log, so a client still holding the previous
    run's offset asks for a byte past the end and is handed nothing --
    it watches an empty pane and concludes the run is doing nothing.
    A file shorter than the offset means the log restarted, so the read
    restarts too and says it did.
    """
    p = log_path(job, runnable)
    if not os.path.isfile(p):
        return {"offset": 0, "text": "", "restarted": False}
    size = os.path.getsize(p)
    start_at = max(0, offset)
    restarted = start_at > size
    if restarted:
        start_at = 0
    with io.open(p, "rb") as fh:
        fh.seek(start_at)
        chunk = fh.read()
    return {"offset": start_at + len(chunk), "restarted": restarted,
            "text": chunk.decode("utf-8", errors="replace")}


def start(job: str, runnable: str, options: dict) -> dict:
    """Launch a runnable. Returns immediately; the log grows on disk."""
    options = dict(options or {})
    spec = RUNNABLES.get(runnable) or {}
    if "--job" in (spec.get("options") or {}):
        # The job a command acts on is the job it was started FOR. Taking
        # `--job` from the caller let one request name two: logged and
        # locked as one job, run against another.
        options["--job"] = job
        if "--confirm" in options and options["--confirm"] != job:
            raise ValueError("--confirm must be the id of the job being approved")
    argv = build_argv(runnable, options)      # validates before any mkdir
    out = job_dir(job)                        # validates the job id
    os.makedirs(out, exist_ok=True)

    key = (job, runnable)
    with _LOCK:
        running = _RUNNING.get(key)
        if running and running.poll() is None:
            raise RuntimeError(
                f"{runnable} is already running for job {job!r}. Wait for it "
                f"or start a different job -- two converts into one output "
                f"would interleave their writes.")
        if runnable in EXCLUSIVE:
            for (other_job, other), proc in _RUNNING.items():
                if other in EXCLUSIVE and proc.poll() is None:
                    raise RuntimeError(
                        f"{other} is running for job {other_job!r}. It "
                        f"rewrites or checks the same files this would, so "
                        f"only one of convert / agent run / approve / "
                        f"discard / re-apply runs at a time. Wait for it.")

    status = {"state": "running", "runnable": runnable, "job": job,
              "argv": argv[2:], "started": _now()}
    _write(status_path(job, runnable), status)

    logf = io.open(log_path(job, runnable), "wb")
    try:
        proc = subprocess.Popen(argv, cwd=ROOT, stdout=logf,
                                stderr=subprocess.STDOUT)
    except Exception as e:
        # Without this the status file says "running" for a process that
        # never existed, and the page polls it forever.
        try:
            logf.write(f"could not start: {e}\n".encode())
            logf.close()
        except Exception:
            pass
        failed = dict(status)
        failed.update({"state": "failed", "exit_code": -1,
                       "finished": _now(), "error": str(e)})
        _write(status_path(job, runnable), failed)
        raise
    with _LOCK:
        _RUNNING[key] = proc

    def wait():
        rc = proc.wait()
        try:
            logf.close()
        except Exception:
            pass
        done = dict(status)
        done.update({"state": "done" if rc == 0 else "failed",
                     "exit_code": rc, "finished": _now()})
        _write(status_path(job, runnable), done)

    threading.Thread(target=wait, daemon=True).start()
    return status


def stop(job: str, runnable: str) -> bool:
    with _LOCK:
        proc = _RUNNING.get((job, runnable))
    if proc is None or proc.poll() is not None:
        return False
    proc.terminate()
    return True


def _write(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)


def list_jobs() -> list:
    base = os.path.join(ROOT, "target", "agent")
    if not os.path.isdir(base):
        return []
    out = []
    for name in sorted(os.listdir(base)):
        d = os.path.join(base, name)
        if not os.path.isdir(d):
            continue
        out.append({
            "job": name,
            "modified": time.strftime("%Y-%m-%d %H:%M",
                                      time.localtime(os.path.getmtime(d))),
            "has_brief": os.path.isfile(os.path.join(d, "brief.md")),
            "has_plan": os.path.isfile(os.path.join(d, "plan.md")),
        })
    return out


def read_artifact(job: str, name: str) -> str:
    """One named artifact from a job directory.

    The name is checked against a list rather than joined, so a job's
    directory cannot be used to read arbitrary files through the API.
    """
    allowed = {"brief.md", "plan.md", "intake.json", "locate.json",
               "proposed.diff", "review.json",
               "design.json", "design.md", "test-cases.csv", "xray.csv"}
    if name not in allowed:
        raise ValueError(f"{name!r} is not a readable artifact. "
                         f"Known: {', '.join(sorted(allowed))}")
    p = os.path.join(job_dir(job), name)
    if not os.path.isfile(p):
        return ""
    with io.open(p, encoding="utf-8", errors="replace") as fh:
        return fh.read()
