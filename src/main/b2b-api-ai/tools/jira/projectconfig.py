"""Read program_configuration.json the way Config.java reads it.

The Java side resolves an ACTIVE ENV and then looks inside that block.
Python tooling that picked the first block, or a hardcoded `stg`, would
read a different configuration than the tests do -- and the difference
would show up as a story fetched against the wrong Jira, or a token that
"does not work" because it belongs to another environment.

Resolution order, matching Config.detectEnv():

    1. TEST_ENV environment variable
    2. `env` in src/main/resources/application.properties
    3. "qa"

The file itself is gitignored and is the ONLY place a credential lives.
Nothing here prints a value: `redacted()` exists so a caller can show
what was found without showing what it is.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_REL = "src/main/resources/program_configuration.json"
PROPS_REL = "src/main/resources/application.properties"

# One configuration file a project, same layout (env blocks), each with its
# own api / services / database AND jira / xray / confluence settings. The
# workbench names a job's project in WORKBENCH_PROJECT for every command it
# starts for that job; a test run passes -Dconfig.project=<project> to Java
# (Config.java), which loads the same file. No project, or b2b: the file
# every existing run has always used.
DEFAULT_PROJECT = "b2b"
PROJECT_FILES = {
    "b2b": CONFIG_REL,
    "goal": "src/main/resources/program_configuration.goal.json",
}


# ONE per process, shared by every copy of this module: the workbench loads
# it under several names (intake's agent_projectconfig, search's and
# settings' plain `projectconfig`, ...). A per-copy thread-local made a
# using("goal") block seen by intake and NOT by the Jira client -- the GOAL
# Check on the Settings tab checked B2B's token.
_LOCAL = getattr(sys, "_workbench_project_local", None)
if _LOCAL is None:
    _LOCAL = threading.local()
    sys._workbench_project_local = _LOCAL


def active_project() -> str:
    """The project of the code running now: a `using(...)` block of this
    thread, else the command's WORKBENCH_PROJECT, else b2b. An unknown name
    is an error, not B2B: a GOAL job reading B2B's Jira or gateway would
    look like it worked."""
    p = (getattr(_LOCAL, "project", "") or os.environ.get("WORKBENCH_PROJECT") or "").strip().lower()
    if not p:
        return DEFAULT_PROJECT
    if p not in PROJECT_FILES:
        raise ValueError(f"WORKBENCH_PROJECT={p!r} is not a project ({', '.join(sorted(PROJECT_FILES))})")
    return p


@contextlib.contextmanager
def using(project: str):
    """Read `project`'s configuration in THIS THREAD for the block's
    duration (a check the server runs itself, not a job's command, which
    gets WORKBENCH_PROJECT from jobs.start). Per thread, not the process's
    environment: the server answers requests on several threads at once."""
    p = str(project or DEFAULT_PROJECT).strip().lower()
    if p not in PROJECT_FILES:
        raise ValueError(f"{project!r} is not a project ({', '.join(sorted(PROJECT_FILES))})")
    before = getattr(_LOCAL, "project", "")
    _LOCAL.project = p
    try:
        yield p
    finally:
        _LOCAL.project = before


def config_rel(project: str = None) -> str:
    """The configuration file of a project (default: the active one), relative to the project folder."""
    project = (project or active_project()).lower()
    if project not in PROJECT_FILES:
        raise ValueError(f"{project!r} is not a project ({', '.join(sorted(PROJECT_FILES))})")
    return PROJECT_FILES[project]


def config_path(root: str = None, project: str = None) -> str:
    """The configuration file of a project (default: the active one), absolute."""
    return os.path.join(root or ROOT, *config_rel(project).split("/"))


def all_config_paths(root: str = None) -> list:
    """Every project's configuration file -- for what must cover them all:
    hiding their values from logs and prompts, putting them back after a run."""
    return [os.path.join(root or ROOT, *rel.split("/")) for rel in PROJECT_FILES.values()]

SECRET_HINTS = ("pat", "token", "secret", "password", "assertion", "key",
                # A client id is half a client-credentials pair, and it
                # matches none of the hints above -- `client_id` has no
                # "key" or "secret" in it. It was printing in full.
                "client_id", "username", "user")


def active_env() -> tuple[str, str]:
    """(env name, where it came from)."""
    env = (os.environ.get("TEST_ENV") or "").strip()
    if env:
        return env, "TEST_ENV"
    p = os.path.join(ROOT, PROPS_REL.replace("/", os.sep))
    if os.path.isfile(p):
        with io.open(p, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("env=") and not line.startswith("#"):
                    v = line[4:].strip()
                    if v:
                        return v, PROPS_REL
    return "qa", "built-in default"


def load() -> tuple[dict, str]:
    """(the active env's block, a note about where it came from)."""
    env, src = active_env()
    rel = config_rel()
    p = os.path.join(ROOT, rel.replace("/", os.sep))
    if not os.path.isfile(p):
        return {}, (f"{rel} is absent -- copy "
                    f"program_configuration.example.json and fill it in "
                    f"(project {active_project()}, env `{env}`, chosen by {src})")
    try:
        with io.open(p, encoding="utf-8-sig") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as e:
        return {}, f"{rel} could not be read: {e}"
    block = doc.get(env)
    if not isinstance(block, dict):
        have = ", ".join(k for k in doc if not k.startswith("_")) or "(none)"
        return {}, (f"no `{env}` block in {rel} (env chosen by {src}); "
                    f"blocks present: {have}")
    return block, f"env `{env}` (by {src})"


def section(name: str) -> tuple[dict, str]:
    """One named block, e.g. `jira_config`, from the active env."""
    block, note = load()
    sec = block.get(name)
    if not isinstance(sec, dict):
        return {}, (f"{note}; no `{name}` block. Add one -- the shape is in "
                    f"program_configuration.example.json")
    return sec, note


def redacted(section_dict: dict) -> dict:
    """The same keys, with anything credential-shaped replaced.

    So a tool can show WHICH settings it found without showing what they
    are. A length is enough to tell "set" from "set to the wrong thing".
    """
    out = {}
    for k, v in (section_dict or {}).items():
        if isinstance(v, str) and v and any(h in k.lower() for h in SECRET_HINTS):
            out[k] = f"<set, {len(v)} chars>"
        else:
            out[k] = v
    return out
