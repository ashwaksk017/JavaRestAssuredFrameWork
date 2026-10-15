"""The Settings tab: the Jira, Confluence and Xray connection of this
machine -- written into the active environment's block of
src/main/resources/program_configuration.json (gitignored), where the
readers already look:

    <env>.jira_config.base_urls / .pat
    <env>.confluence_config.base_urls / .pat
    <env>.xray.baseUrl / .token / .projectKey / .enabled
    <env>.xray.regressionKey      (the Test Set or Test Plan regression cases join)

A token is never read back: the page is told "set, N characters" or "not
set". A token field left empty leaves the token as it is; `clear` removes
it. Every save keeps a copy of the file as it was, in .workbench/settings/
(gitignored), the last five.
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CONFIG_REL = "src/main/resources/program_configuration.json"
BACKUPS = os.path.join(".workbench", "settings")
KEEP = 5

_URL_RX = re.compile(r"^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?(/[A-Za-z0-9._~/-]*)?$")
_TOKEN_RX = re.compile(r"^[\x21-\x7e]{8,500}$")
_PROJECT_RX = re.compile(r"^[A-Z][A-Z0-9_]{0,19}$")
_KEY_RX = re.compile(r"^[A-Z][A-Z0-9_]{0,19}-[0-9]{1,9}$")


def _projectconfig():
    sys.path.insert(0, os.path.join(ROOT, "tools", "jira"))
    import projectconfig                                 # noqa: E402
    return projectconfig


def _path(root: str = "") -> str:
    return os.path.join(root or ROOT, *CONFIG_REL.split("/"))


def _read(root: str = "") -> dict:
    p = _path(root)
    if not os.path.isfile(p):
        return {}
    with io.open(p, encoding="utf-8-sig") as fh:
        doc = json.load(fh)
    if not isinstance(doc, dict):
        raise ValueError(f"{CONFIG_REL} is not a JSON object")
    return doc


def _env(root: str = "") -> tuple:
    pc = _projectconfig()
    if root:
        pc.ROOT = root
    return pc.active_env()


def _token_state(value) -> str:
    v = str(value or "").strip()
    return f"set, {len(v)} characters" if v else "not set"


def status(root: str = "") -> dict:
    """What is set, for the page -- never a token's value."""
    env, source = _env(root)
    try:
        doc = _read(root)
    except (OSError, ValueError) as e:
        return {"env": env, "env_from": source, "problem": f"{CONFIG_REL} cannot be read: {e}"}
    block = doc.get(env) if isinstance(doc.get(env), dict) else {}
    jira = block.get("jira_config") if isinstance(block.get("jira_config"), dict) else {}
    conf = block.get("confluence_config") if isinstance(block.get("confluence_config"), dict) else {}
    xray = block.get("xray") if isinstance(block.get("xray"), dict) else {}
    return {
        "env": env, "env_from": source, "file": CONFIG_REL, "exists": os.path.isfile(_path(root)),
        "jira": {"base_urls": [str(u) for u in jira.get("base_urls") or []],
                 "pat": _token_state(jira.get("pat"))},
        "confluence": {"base_urls": [str(u) for u in conf.get("base_urls") or []],
                       "pat": _token_state(conf.get("pat"))},
        "xray": {"base_url": str(xray.get("baseUrl") or ""), "token": _token_state(xray.get("token")),
                 "project_key": str(xray.get("projectKey") or ""),
                 "regression_key": str(xray.get("regressionKey") or ""),
                 "enabled": str(xray.get("enabled") or "").lower() == "true",
                 "same_as_jira": bool(str(xray.get("token") or "").strip())
                 and str(xray.get("token") or "") == str(jira.get("pat") or "")},
    }


def _urls(value) -> list:
    if isinstance(value, str):
        value = [v for v in re.split(r"[\s,]+", value) if v]
    out = []
    for u in value or []:
        u = str(u).strip().rstrip("/")
        if not _URL_RX.match(u):
            raise ValueError(f"{u!r} is not an https:// address of a server")
        if u not in out:
            out.append(u)
    return out


def _token(value, what: str) -> str:
    v = str(value or "").strip()
    if not _TOKEN_RX.match(v):
        raise ValueError(f"the {what} token does not look like one: 8 to 500 characters, no spaces")
    return v


def save(changes: dict, root: str = "", busy=None) -> dict:
    """Write what the page sent into the active environment's block. Fields
    missing from `changes` stay as they are; a token sent empty stays as it
    is; `<what>_clear: true` removes that token. Refused while `busy`
    (anything running): a Cursor run guards this file, and a run reading it
    half-written would read nonsense."""
    if busy:
        raise ValueError(f"{busy} is running: save the settings when nothing is running")
    if not isinstance(changes, dict):
        raise ValueError("send the settings as an object")
    env, _source = _env(root)
    doc = _read(root)
    block = doc.setdefault(env, {})
    if not isinstance(block, dict):
        raise ValueError(f"`{env}` in {CONFIG_REL} is not an object")
    jira = block.setdefault("jira_config", {})
    conf = block.setdefault("confluence_config", {})
    xray = block.setdefault("xray", {})
    for sec, name in ((jira, "jira_config"), (conf, "confluence_config"), (xray, "xray")):
        if not isinstance(sec, dict):
            raise ValueError(f"`{env}.{name}` in {CONFIG_REL} is not an object")
    said = []
    if "jira_base_urls" in changes:
        jira["base_urls"] = _urls(changes["jira_base_urls"])
        said.append("Jira address")
    if changes.get("jira_pat_clear"):
        jira.pop("pat", None)
        said.append("Jira token removed")
    elif str(changes.get("jira_pat") or "").strip():
        jira["pat"] = _token(changes["jira_pat"], "Jira")
        said.append("Jira token")
    if "confluence_base_urls" in changes:
        conf["base_urls"] = _urls(changes["confluence_base_urls"])
        said.append("Confluence address")
    if changes.get("confluence_pat_clear"):
        conf.pop("pat", None)
        said.append("Confluence token removed")
    elif str(changes.get("confluence_pat") or "").strip():
        conf["pat"] = _token(changes["confluence_pat"], "Confluence")
        said.append("Confluence token")
    if "xray_base_url" in changes:
        u = _urls(changes["xray_base_url"])
        if len(u) > 1:
            raise ValueError("Xray has one address")
        if u:
            xray["baseUrl"] = u[0]
        else:
            xray.pop("baseUrl", None)
        said.append("Xray address")
    if changes.get("xray_token_clear"):
        xray.pop("token", None)
        said.append("Xray token removed")
    elif changes.get("xray_same_as_jira"):
        if not str(jira.get("pat") or "").strip():
            raise ValueError("there is no Jira token to use for Xray: set the Jira token first")
        xray["token"] = jira["pat"]
        said.append("Xray token (the Jira token)")
    elif str(changes.get("xray_token") or "").strip():
        xray["token"] = _token(changes["xray_token"], "Xray")
        said.append("Xray token")
    for field, key, rx, what in (("xray_project_key", "projectKey", _PROJECT_RX, "a project key like ABC"),
                                 ("xray_regression_key", "regressionKey", _KEY_RX,
                                  "an issue key like ABC-123")):
        if field in changes:
            v = str(changes.get(field) or "").strip().upper()
            if v and not rx.match(v):
                raise ValueError(f"{v!r} is not {what}")
            if v:
                xray[key] = v
            else:
                xray.pop(key, None)
            said.append(key)
    if "xray_enabled" in changes:
        xray["enabled"] = "true" if changes.get("xray_enabled") else "false"
        said.append("publishing to Xray " + ("on" if changes.get("xray_enabled") else "off"))
    if not said:
        return {"saved": False, "said": "nothing to change", "status": status(root)}
    _write(doc, root)
    return {"saved": True, "said": ", ".join(said), "env": env, "status": status(root)}


def _write(doc: dict, root: str = "") -> None:
    p = _path(root)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    keep = os.path.join(root or ROOT, BACKUPS)
    if os.path.isfile(p):
        os.makedirs(keep, exist_ok=True)
        shutil.copy2(p, os.path.join(keep, "program_configuration-"
                                     + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".json"))
        old = sorted(n for n in os.listdir(keep) if n.startswith("program_configuration-"))
        for name in old[:-KEEP]:
            try:
                os.remove(os.path.join(keep, name))
            except OSError:
                pass
    tmp = p + ".part"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, p)
