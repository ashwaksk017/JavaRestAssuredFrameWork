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
import re
import subprocess
import sys

# On Windows a child started from a process that has no console of its own
# (the workbench started by its .bat, or by pythonw) opens a console window
# of its own -- one per command, one per git call. These keep it from.
NO_WINDOW = ({"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
             if os.name == "nt" else {})
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
        # name -> kind. "path" is resolved and confined to the repo;
        # "paths" is several of them, comma separated, each confined.
        # The page shows only some of these (see ui/index.html); the rest
        # keep the converter's own defaults, except --output: the
        # converter's default is a directory called `output`, and this
        # project is converted into itself.
        "defaults": {"--output": "."},
        "options": {
            "--input": "paths", "--output": "path", "--data-dir": "path",
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
    # Takes the specification attached to a Confluence page into the job
    # directory. A link and an attachment name; no path. Reads Confluence
    # (GET only), writes target/agent/<job>/spec/.
    "spec-confluence": {
        "argv": [PY, "-B", os.path.join("tools", "agent", "specsource.py"), "confluence"],
        "label": "Take the API specification attached to a Confluence page",
        "options": {"--job": "text", "--link": "text", "--name": "text"},
    },
    # Manual UI tests as a delta against the tests in Jira. `inventory`
    # reads Jira (a project key, a type, one further condition -- each
    # validated or quoted by the command); `generate` asks Cursor in plan
    # mode. Neither takes a path: documents are sent to /api/doc-upload.
    "manual-inventory": {
        "argv": [PY, "-B", os.path.join("tools", "agent", "manualtests.py"), "inventory"],
        "label": "Read the existing tests, with their steps",
        "options": {"--job": "text", "--project": "text", "--type": "text",
                    "--extra": "text", "--max": "text"},
    },
    "manual-generate": {
        "argv": [PY, "-B", os.path.join("tools", "agent", "manualtests.py"), "generate"],
        "label": "Write new manual test cases and updates to existing ones",
        "options": {"--job": "text", "--app": "text", "--speed": "text"},
    },
    # The two commands that write outside this machine other than a git
    # push: a file attached to a Jira issue, a page created in Confluence.
    # Off unless the private configuration switches them on; the file is a
    # name from a fixed list, never a path; the key and the title are
    # given twice. See publish.py.
    "publish-status": {
        "argv": [PY, "-B", os.path.join("tools", "agent", "publish.py"), "status"],
        "label": "What can be published from this job, and what is switched on",
        "options": {"--job": "text"},
    },
    "publish-attach": {
        "argv": [PY, "-B", os.path.join("tools", "agent", "publish.py"), "attach"],
        "label": "Attach one result file to one Jira issue",
        "options": {"--job": "text", "--key": "text", "--file": "text",
                    "--confirm-key": "text", "--expect": "text"},
    },
    "publish-page": {
        "argv": [PY, "-B", os.path.join("tools", "agent", "publish.py"), "page"],
        "label": "Create one Confluence page from a result",
        "options": {"--job": "text", "--what": "text", "--space": "text",
                    "--parent": "text", "--title": "text", "--confirm-title": "text",
                    "--expect": "text"},
    },
    # Xray Test issues from a job's cases. One story, named twice; the
    # result the cases come from is one of two fixed names. See xraytests.py.
    "xray-status": {
        "argv": [PY, "-B", os.path.join("tools", "agent", "xraytests.py"), "status"],
        "label": "What this job could create in Xray, and what it already has",
        "options": {"--job": "text"},
    },
    "xray-create": {
        "argv": [PY, "-B", os.path.join("tools", "agent", "xraytests.py"), "create"],
        "label": "Create the cases as Xray Test issues in Jira, linked to the story",
        "options": {"--job": "text", "--what": "text", "--story": "text",
                    "--confirm-story": "text", "--expect": "text"},
    },
    # The whole gate, as `python tools/verify_all.py`. No options: what it
    # checks is this tree. Offered after a convert.
    "verify-all": {
        "argv": [PY, "-B", os.path.join("tools", "verify_all.py")],
        "label": "Run every check of the gate",
        "options": {},
    },
    "intake": {
        "argv": [PY, "-B", os.path.join("tools", "agent", "intake.py")],
        "label": "Read the pasted story and links",
        # No path: what the page pasted is written by the server under fixed
        # names in the job directory and read with --pasted. A path confined
        # to the repository still reaches the private configuration, and
        # what intake reads is quoted into the brief Cursor is given.
        "options": {"--job": "text", "--pasted": "flag", "--link": "text",
                    "--api-path": "text"},
    },
    "locate": {
        "argv": [PY, "-B", os.path.join("tools", "agent", "locate.py")],
        "label": "Decide create / update / upstream",
        "options": {"--job": "text", "--index": "path",
                    "--rebuild-index": "flag"},
    },
    # Tab 4. Read-only questions to the ONE configured Jira. No runnable
    # here takes a host, a token or a path: what the page can send is a
    # project key, a version name, an issue type, a limit and a query, and
    # search.py validates or quotes each of them.
    "jira-verify": {
        "argv": [PY, "-B", os.path.join("tools", "jira", "search.py"), "verify"],
        "label": "Check the Jira token",
        "options": {"--job": "text"},
    },
    "jira-versions": {
        "argv": [PY, "-B", os.path.join("tools", "jira", "search.py"), "versions"],
        "label": "A project's versions, newest first",
        "options": {"--job": "text", "--project": "text", "--all": "flag"},
    },
    "jira-release": {
        "argv": [PY, "-B", os.path.join("tools", "jira", "search.py"), "fix-version"],
        "label": "What a release holds, and what changed since another",
        "options": {"--job": "text", "--project": "text", "--version": "text",
                    "--compare": "text", "--type": "text", "--max": "text"},
    },
    "jira-tests": {
        "argv": [PY, "-B", os.path.join("tools", "jira", "search.py"), "tests"],
        "label": "The tests a project already has",
        "options": {"--job": "text", "--project": "text", "--type": "text",
                    "--jql": "text", "--max": "text"},
    },
    "jira-paste": {
        "argv": [PY, "-B", os.path.join("tools", "jira", "search.py"), "paste"],
        "label": "List what was pasted: keys, a query, or a Jira address",
        "options": {"--job": "text", "--text": "text", "--max": "text"},
    },
    # The Defects tab. `load` and `suggest` read. `apply` is the ONE
    # runnable that changes Jira: one field of one bug, refused by the
    # script unless jira_config.defects.write_back is exactly true, the
    # bug was loaded by this job, the reason is an allowed value and
    # --confirm-key repeats the key. No host, token or path here either.
    "defects-load": {
        "argv": [PY, "-B", os.path.join("tools", "agent", "defects.py"), "load"],
        "label": "Read defects from Jira, with the reason each has",
        "options": {"--job": "text", "--text": "text", "--project": "text",
                    "--version": "text", "--max": "text"},
    },
    "defects-suggest": {
        "argv": [PY, "-B", os.path.join("tools", "agent", "defects.py"), "suggest"],
        "label": "Cursor proposes a reason for each loaded defect",
        "options": {"--job": "text", "--keys": "text"},
    },
    "defects-apply": {
        "argv": [PY, "-B", os.path.join("tools", "agent", "defects.py"), "apply"],
        "label": "Write one reason to one defect in Jira",
        "options": {"--job": "text", "--key": "text", "--reason": "text",
                    "--confirm-key": "text"},
    },
    # Tab 5. Reads target/failure-digest.txt, writes .failure-history/
    # (ignored) and the job directory. `record` takes no path from the
    # page: it records this project's last run.
    "failures-record": {
        "argv": [PY, "-B", os.path.join("tools", "failure_history.py"), "record"],
        "label": "Keep this run's failures and compare with the run before",
        "options": {"--job": "text", "--label": "text"},
    },
    "failures-list": {
        "argv": [PY, "-B", os.path.join("tools", "failure_history.py"), "list"],
        "label": "The runs recorded so far",
        "options": {"--job": "text"},
    },
    "failures-show": {
        "argv": [PY, "-B", os.path.join("tools", "failure_history.py"), "show"],
        "label": "Every run a failure signature appeared in",
        "options": {"--job": "text", "--id": "text"},
    },
    "failures-note": {
        "argv": [PY, "-B", os.path.join("tools", "failure_history.py"), "note"],
        "label": "Write down what a failure turned out to be",
        "options": {"--job": "text", "--id": "text", "--text": "text"},
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
    # Writes only the job directory. It takes NO file path: what it reads
    # is what the server wrote into the job directory from the page (see
    # /api/design). A path option here was a way to have any file of the
    # repository -- the private configuration included -- sent to Cursor
    # and copied into a log this API serves.
    "agent-design": {
        "argv": [PY, "-B", os.path.join("tools", "agent", "design.py")],
        "label": "Design the API test cases (Cursor, plan mode)",
        "options": {"--job": "text", "--service": "text", "--speed": "text",
                    "--mode": "text", "--fresh": "flag", "--whole-spec": "flag"},
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
    # Reads the job's proposed.diff and asks Cursor about it, in plan mode
    # in an empty directory. It changes nothing: not the tree, not the
    # job's state. No path and no text is taken from the page.
    "agent-review": {
        "argv": [PY, "-B", os.path.join("tools", "agent", "codereview.py")],
        "label": "Cursor reads the proposed change and lists what looks wrong",
        "options": {"--job": "text"},
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
             "agent-reapply", "verify-all"}

# The only options that may be given more than once.
REPEATABLE = {"--link"}

_RUNNING: dict = {}
_STOPPED: set = set()            # (job, runnable) a person stopped, until it has ended
_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _diag():
    """tools/diag.py, once for the whole process."""
    if "workbench_diag" not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            "workbench_diag", os.path.join(os.path.dirname(HERE), "diag.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules["workbench_diag"] = mod
        try:
            spec.loader.exec_module(mod)
        except BaseException:
            sys.modules.pop("workbench_diag", None)
            raise
    return sys.modules["workbench_diag"]


diag = _diag()


def read_diag(job: str, which: str = "job", lines: int = 400) -> str:
    """The end of a job's diagnostics, or of the server's log, for the page."""
    if which == "server":
        return diag.tail(diag.server_log(ROOT), lines)
    if which != "job":
        raise ValueError("which is `job` or `server`")
    return diag.tail(os.path.join(job_dir(job), diag.JOB_LOG), lines)


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
    options = dict(options or {})
    for name, value in (spec.get("defaults") or {}).items():
        if str(options.get(name) or "").strip() == "":
            options[name] = value
    for name, value in options.items():
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
            if kind == "paths":
                parts = [p.strip() for p in str(v).split(",") if p.strip()]
                if not parts:
                    continue
                argv.append(name)
                argv.append(",".join(confine(p) for p in parts))
                continue
            if kind != "path" and str(v).lstrip().startswith("-"):
                # `--text -flaky` reads to the command as an option called
                # -flaky and no text. Joined with `=`, it is the text.
                argv.append(f"{name}={v}")
                continue
            argv.append(name)
            argv.append(confine(str(v)) if kind == "path" else str(v))
    return argv


# ---- files chosen on the Convert tab -------------------------------------
# A browser does not tell a page where a chosen file lives, so the page
# cannot hand the converter a path. It hands over the file: the bytes are
# written here, under the gitignored target/, and the converter is given
# THAT path. The name is kept, because the suite is named after it.
UPLOAD_DIR = ("target", "convert-input")
MAX_UPLOAD = 300 * 1024 * 1024
_UPLOAD_NAME_RX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()+-]{0,150}\.xml$", re.I)
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
             *(f"lpt{i}" for i in range(1, 10))}


def upload_dir() -> str:
    return os.path.join(ROOT, *UPLOAD_DIR)


def upload_name(name: str) -> str:
    """The file's own name, and nothing of where it came from."""
    base = str(name or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if (not _UPLOAD_NAME_RX.match(base) or ".." in base
            or base.rsplit(".", 1)[0].strip(" .").lower() in _RESERVED):
        raise ValueError(
            f"{base or name!r} cannot be used as a file name here. A ReadyAPI project "
            f"file ends in .xml, and its name may hold letters, digits, spaces and "
            f". _ - ( ) + only.")
    return base


def _upload_entry(name: str) -> dict:
    # Forward slashes whatever the machine: the same text is a valid path
    # on Windows, macOS and Linux, and it is relative to the project.
    return {"name": name, "path": "/".join((*UPLOAD_DIR, name)),
            "bytes": os.path.getsize(os.path.join(upload_dir(), name))}


def list_uploads() -> list:
    try:
        names = sorted(n for n in os.listdir(upload_dir())
                       if _UPLOAD_NAME_RX.match(n)
                       and os.path.isfile(os.path.join(upload_dir(), n)))
    except OSError:
        return []
    return [_upload_entry(n) for n in names]


def clear_uploads() -> int:
    """Forget what was chosen before. Only files this put there."""
    n = 0
    try:
        names = os.listdir(upload_dir())
    except OSError:
        return 0
    for name in names:
        p = os.path.join(upload_dir(), name)
        if os.path.isfile(p) and (_UPLOAD_NAME_RX.match(name) or name.endswith(".part")):
            try:
                os.remove(p)
                n += 1
            except OSError:
                pass
    return n


def save_upload(name: str, stream, length: int) -> dict:
    """Write `length` bytes of `stream` as target/convert-input/<name>."""
    base = upload_name(name)
    if length <= 0:
        raise ValueError(f"{base} is empty")
    if length > MAX_UPLOAD:
        raise ValueError(f"{base} is {length:,} bytes; the limit is {MAX_UPLOAD:,}")
    os.makedirs(upload_dir(), exist_ok=True)
    dest = os.path.join(upload_dir(), base)
    tmp = f"{dest}.{os.getpid()}.part"
    left, head = length, b""
    try:
        with io.open(tmp, "wb") as fh:
            while left > 0:
                chunk = stream.read(min(left, 1 << 20))
                if not chunk:
                    raise ValueError(f"{base} arrived incomplete "
                                     f"({length - left:,} of {length:,} bytes)")
                if len(head) < 64:
                    head += chunk[:64 - len(head)]
                fh.write(chunk)
                left -= len(chunk)
        # Not a parse: only that it is the kind of file it says it is. A
        # workbook or a zip renamed to .xml is caught here, not an hour
        # into a convert.
        start = head[3:] if head.startswith(b"\xef\xbb\xbf") else head
        if not start.lstrip().startswith(b"<"):
            raise ValueError(f"{base} does not look like XML (it does not start with `<`)")
        os.replace(tmp, dest)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    return _upload_entry(base)


# ---- a specification chosen on the page -----------------------------------
MAX_SPEC_UPLOAD = 20 * 1024 * 1024
_TOOLS = {}
_SPECSOURCE_LOCK = threading.Lock()


def _tool(name: str):
    """tools/agent/<name>.py, loaded when first needed: these bring the
    design step with them, which the server otherwise never loads."""
    # One thread loads it; the others wait for the WHOLE module. Handing
    # out a module that is still being executed gave "no attribute".
    with _SPECSOURCE_LOCK:
        if name not in _TOOLS:
            spec = importlib.util.spec_from_file_location(
                f"jobs_{name}", os.path.join(HERE, f"{name}.py"))
            mod = importlib.util.module_from_spec(spec)
            sys.modules[f"jobs_{name}"] = mod
            try:
                spec.loader.exec_module(mod)
            except BaseException:
                sys.modules.pop(f"jobs_{name}", None)
                raise
            _TOOLS[name] = mod
        return _TOOLS[name]


def _specsource():
    return _tool("specsource")


# ---- requirement documents chosen on the Manual tests tab -----------------
def _read_body(stream, length: int, limit: int) -> bytes:
    if length <= 0:
        raise ValueError("the file is empty")
    if length > limit:
        raise ValueError(f"the file is {length:,} bytes; the limit is {limit:,}")
    data = b""
    while len(data) < length:
        chunk = stream.read(min(length - len(data), 1 << 20))
        if not chunk:
            raise ValueError(f"the file arrived incomplete ({len(data):,} of {length:,} bytes)")
        data += chunk
    return data


def save_doc(job: str, name: str, stream, length: int) -> dict:
    """A chosen file becomes target/agent/<job>/docs/<name>."""
    out = job_dir(job)                        # validates the job id first
    tool = _tool("manualtests")
    try:
        tool.doc_name(name)                   # before a byte of it is read
        data = _read_body(stream, length, tool.MAX_DOC_BYTES)
        os.makedirs(out, exist_ok=True)
        return tool.store_doc(out, name, data, ROOT)
    except tool.Refused as e:
        raise ValueError(str(e)) from None


def list_docs(job: str) -> list:
    out = job_dir(job)
    if not os.path.isdir(os.path.join(out, "docs")):
        return []
    return _tool("manualtests").list_docs(out, ROOT)


def remove_doc(job: str, name: str = "") -> int:
    tool = _tool("manualtests")
    try:
        return tool.remove_doc(job_dir(job), name)
    except tool.Refused as e:
        raise ValueError(str(e)) from None


def save_spec(job: str, name: str, stream, length: int) -> dict:
    """A chosen file becomes target/agent/<job>/spec/<name>, if it is a
    specification. Answers with where it is and what is in it."""
    out = job_dir(job)                        # validates the job id first
    if length <= 0:
        raise ValueError("the file is empty")
    if length > MAX_SPEC_UPLOAD:
        raise ValueError(f"the file is {length:,} bytes; the limit is {MAX_SPEC_UPLOAD:,}")
    src = _specsource()
    try:
        src.spec_name(name)                   # before a byte of it is read
    except src.Refused as e:
        raise ValueError(str(e)) from None
    data = b""
    while len(data) < length:
        chunk = stream.read(min(length - len(data), 1 << 20))
        if not chunk:
            raise ValueError(f"the file arrived incomplete ({len(data):,} of {length:,} bytes)")
        data += chunk
    try:
        os.makedirs(out, exist_ok=True)
        return src.store(out, name, data, "chosen on the page", ROOT)
    except src.Refused as e:
        raise ValueError(str(e)) from None


def spec_state(job: str) -> dict:
    out = job_dir(job)
    if not os.path.isdir(os.path.join(out, "spec")):
        return {}
    return _specsource().current(out, ROOT)


def _casereview():
    spec = importlib.util.spec_from_file_location("jobs_casereview", os.path.join(HERE, "casereview.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


casereview = _casereview()


def spec_clear(job: str) -> bool:
    return _specsource().clear(job_dir(job))


def mock_state() -> dict:
    """{"jira": bool, "cursor": bool, "error": str} from tools/agent/mock.json.
    An unreadable file is reported, and counted as "on": the page then
    shows its banner rather than letting anyone think the services are
    real when nobody can tell."""
    spec = importlib.util.spec_from_file_location("jobs_mock", os.path.join(HERE, "mock.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    try:
        return dict(mod.switches(), error="")
    except mod.MockConfigError as e:
        return {"jira": True, "cursor": True, "error": str(e)}


# The file each of these commands writes for the page, in the job
# directory. It is removed BEFORE the command starts: a command that dies
# before it can write -- a bad argument, a crash, Stop -- must leave no
# result, not the previous command's result under this one's exit code.
RESULT_FILES = {"jira-": "jira-result.json", "failures-": "failures-result.json",
                "defects-": "defects-result.json", "agent-review": "code-review.json",
                "spec-": "spec-result.json", "manual-inventory": "manual-pull.json",
                "manual-generate": "manual-result.json",
                # Not publish-status: asking what is switched on must never
                # remove the record of the last write.
                "publish-attach": "publish-result.json",
                "publish-page": "publish-result.json",
                "xray-create": "xray-result.json"}


def clear_result(job: str, runnable: str) -> None:
    for prefix, name in RESULT_FILES.items():
        if runnable.startswith(prefix):
            # The report goes with the result it is the report of.
            for each in (name, "code-review.md") if name == "code-review.json" else (name,):
                try:
                    os.remove(os.path.join(job_dir(job), each))
                except OSError:
                    pass


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
    clear_result(job, runnable)

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
                                stderr=subprocess.STDOUT, **NO_WINDOW)
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
        diag.job_note(job, f"[jobs] {runnable} could not be started: {type(e).__name__}: {e}", ROOT)
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
        if rc != 0:
            # Its own log is written over by the next run of the same
            # command; the job's record is not. (What it SAID as it failed
            # is put there by the command itself, under diag.run.)
            try:
                diag.job_note(job, f"[jobs] {runnable} exited {rc}"
                              + (" (stopped from the page, or killed)" if rc < 0 or rc == 1
                                 and (job, runnable) in _STOPPED else ""), ROOT)
            except Exception:                            # noqa: BLE001
                pass
            _STOPPED.discard((job, runnable))

    threading.Thread(target=wait, daemon=True).start()
    return status


def stop(job: str, runnable: str) -> bool:
    with _LOCK:
        proc = _RUNNING.get((job, runnable))
    if proc is None or proc.poll() is not None:
        return False
    _STOPPED.add((job, runnable))
    if os.name == "nt":
        # terminate() ends the Python and leaves what it started -- the
        # Cursor bridge, a Maven compile -- running and holding files, so
        # the Discard that follows could not put them back.
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **NO_WINDOW)
    else:
        proc.terminate()
    diag.job_note(job, f"[jobs] {runnable} was told to stop (and what it started with it)", ROOT)
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


def diff_sha(job: str) -> str:
    """sha256 of the job's proposed.diff as it is on disk, or "". A review
    of the change records the same number; when they differ the review is
    of another diff."""
    import hashlib
    h = hashlib.sha256()
    try:
        with io.open(os.path.join(job_dir(job), "proposed.diff"), "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def read_artifact(job: str, name: str) -> str:
    """One named artifact from a job directory.

    The name is checked against a list rather than joined, so a job's
    directory cannot be used to read arbitrary files through the API.
    """
    allowed = {"brief.md", "plan.md", "intake.json", "locate.json",
               "proposed.diff", "review.json",
               "design.json", "design.md", "test-cases.csv", "xray.csv",
               "jira-result.json", "failures-result.json", "defects-result.json",
               "code-review.json", "code-review.md", "spec-result.json",
               "manual-result.json", "manual-pull.json", "manual-tests.md",
               "manual-new-cases.csv", "manual-xray-updates.csv", "publish-result.json",
               "xray-tests.json", "xray-result.json", "xray-status.json", "case-review.json",
               "publish-status.json", "publish.log"}
    if name not in allowed:
        raise ValueError(f"{name!r} is not a readable artifact. "
                         f"Known: {', '.join(sorted(allowed))}")
    p = os.path.join(job_dir(job), name)
    if not os.path.isfile(p):
        return ""
    with io.open(p, encoding="utf-8", errors="replace") as fh:
        return fh.read()
