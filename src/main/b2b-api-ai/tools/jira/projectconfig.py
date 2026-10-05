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

import io
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_REL = "src/main/resources/program_configuration.json"
PROPS_REL = "src/main/resources/application.properties"

SECRET_HINTS = ("pat", "token", "secret", "password", "assertion", "key")


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
    p = os.path.join(ROOT, CONFIG_REL.replace("/", os.sep))
    if not os.path.isfile(p):
        return {}, (f"{CONFIG_REL} is absent -- copy "
                    f"program_configuration.example.json and fill it in "
                    f"(env `{env}`, chosen by {src})")
    try:
        with io.open(p, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as e:
        return {}, f"{CONFIG_REL} could not be read: {e}"
    block = doc.get(env)
    if not isinstance(block, dict):
        have = ", ".join(k for k in doc if not k.startswith("_")) or "(none)"
        return {}, (f"no `{env}` block in {CONFIG_REL} (env chosen by {src}); "
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
