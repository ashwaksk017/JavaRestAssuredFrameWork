"""The Settings tab: the Jira, Confluence and Xray connection of this
machine -- written into the active environment's block of
src/main/resources/program_configuration.json (gitignored), where the
readers already look:

    <env>.jira_config.base_urls / .pat
    <env>.confluence_config.base_urls / .pat
    <env>.xray.baseUrl / .token / .projectKey / .enabled
    <env>.xray.regressionKey      (the Test Set or Test Plan regression cases join)
    <env>.devcode_config.base_urls / .token / .hosts
                                  (the developer's repository: devcode.py)

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


WORKBENCH_FILE = os.path.join(".workbench", "workbench-settings.json")
# Cursor's key and models (gitignored), shared by the workbench and the
# converter: tools/cursor_agent.json, or the converter's old place when only
# that one exists. CURSOR_API_KEY, when set, wins over the file's key.
CURSOR_REL = "tools/cursor_agent.json"
CURSOR_OLD_REL = "tools/ra_converter/cursor_agent.json"
_MODEL_RX = re.compile(r"^[A-Za-z0-9._:-]{1,80}$")


def cursor_rel(root: str = "") -> str:
    """The Cursor settings file in use, relative to the project."""
    root = root or ROOT
    if not os.path.isfile(os.path.join(root, *CURSOR_REL.split("/"))) and \
            os.path.isfile(os.path.join(root, *CURSOR_OLD_REL.split("/"))):
        return CURSOR_OLD_REL
    return CURSOR_REL


def _cursor_path(root: str = "") -> str:
    return os.path.join(root or ROOT, *cursor_rel(root).split("/"))


def _cursor_read(root: str = "") -> dict:
    try:
        with io.open(_cursor_path(root), encoding="utf-8-sig") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _cursor_write(data: dict, root: str = "") -> None:
    """Without a BOM (the converter reads it as plain UTF-8), and through a
    temporary file: a run reading it half-written would read no key."""
    p = _cursor_path(root)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, p)
_NAME_RX = re.compile(r"^[A-Za-z][A-Za-z0-9 _-]{0,40}$")
_ENV_RX = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


def _suitemap():
    import importlib.util
    name = "settings_suitemap"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, "suitemap.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[name]


def workbench_settings(root: str = "") -> dict:
    """This machine's workflow settings (run environment, time limit),
    kept in .workbench/ -- out of git, no secret in it."""
    try:
        with io.open(os.path.join(root or ROOT, WORKBENCH_FILE), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _rel(project: str = "") -> str:
    """The project's configuration file (tools/jira/projectconfig.py),
    relative. No project named: the ACTIVE one -- inside a GOAL job's
    command (xraypublish, xraytrace, a run) that is GOAL's file, not B2B's."""
    return _projectconfig().config_rel(project or None)


def _project(project: str = "") -> str:
    return (project or _projectconfig().active_project()).lower()


def environments(root: str = "", project: str = "") -> list:
    """The environment blocks of the project's configuration file."""
    try:
        doc = _read(root, project)
    except (OSError, ValueError):
        return []
    return sorted(k for k, v in doc.items() if isinstance(v, dict) and not k.startswith("_")
                  and _ENV_RX.match(k))


def _projectconfig():
    sys.path.insert(0, os.path.join(ROOT, "tools", "jira"))
    import projectconfig                                 # noqa: E402
    return projectconfig


def _path(root: str = "", project: str = "") -> str:
    return os.path.join(root or ROOT, *_rel(project).split("/"))


def _read(root: str = "", project: str = "") -> dict:
    p = _path(root, project)
    if not os.path.isfile(p):
        return {}
    with io.open(p, encoding="utf-8-sig") as fh:
        doc = json.load(fh)
    if not isinstance(doc, dict):
        raise ValueError(f"{_rel(project)} is not a JSON object")
    return doc


def run_env_of(wb: dict, project: str = "") -> str:
    """The run environment chosen on this tab for a project (B2B keeps the
    `run_env` it always had; another project has its own)."""
    project = _project(project)
    if project == "b2b":
        return str(wb.get("run_env") or "stg")
    return str(((wb.get("projects") or {}).get(project) or {}).get("run_env") or "stg")


def _env(root: str = "") -> tuple:
    pc = _projectconfig()
    if root:
        pc.ROOT = root
    return pc.active_env()


def _token_state(value) -> str:
    v = str(value or "").strip()
    return f"set, {len(v)} characters" if v else "not set"


def _cursor_runtime(root: str = "") -> dict:
    """Last Cursor startup check (workbench server start), not the key itself."""
    path = os.path.join(root or ROOT, ".workbench", "cursor-setup.json")
    try:
        with io.open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError("not an object")
        return {"ok": data.get("ok"), "mock": bool(data.get("mock")),
                "checking": bool(data.get("checking")),
                "at": str(data.get("at") or ""), "model": str(data.get("model") or ""),
                "problem": str(data.get("problem") or "")}
    except (OSError, ValueError):
        return {"ok": None, "mock": False, "at": "", "model": "",
                "problem": "not checked yet — restart the workbench server"}


def status(root: str = "", project: str = "") -> dict:
    """What is set, for the page -- never a token's value. `project`: whose
    configuration file (b2b: program_configuration.json)."""
    project = _project(project)
    rel = _rel(project)
    cur = _cursor_read(root)
    env, source = _env(root)
    try:
        doc = _read(root, project)
    except (OSError, ValueError) as e:
        return {"env": env, "env_from": source, "project": project, "file": rel,
                "problem": f"{rel} cannot be read: {e}"}
    block = doc.get(env) if isinstance(doc.get(env), dict) else {}
    jira = block.get("jira_config") if isinstance(block.get("jira_config"), dict) else {}
    conf = block.get("confluence_config") if isinstance(block.get("confluence_config"), dict) else {}
    xray = block.get("xray") if isinstance(block.get("xray"), dict) else {}
    dev = block.get("devcode_config") if isinstance(block.get("devcode_config"), dict) else {}
    dev_hosts = dev.get("hosts") if isinstance(dev.get("hosts"), dict) else {}
    publish = jira.get("publish") if isinstance(jira.get("publish"), dict) else {}
    wb = workbench_settings(root)
    sm = _suitemap()
    local_map = os.path.join(root, ".workbench", "suite-map.json") if root else sm.LOCAL_MAP
    mapping = sm.load(local_map if os.path.isfile(local_map) else sm.MAP_FILE)
    return {
        "xray_create": {"on": publish.get("create_tests", True) is not False
                        and str(publish.get("create_tests", True)).strip().lower() not in ("false", "no", "off", "0"),
                        "projects": [str(p) for p in publish.get("projects") or []],
                        "issue_type": str(publish.get("test_issue_type") or "Test"),
                        "link_type": str(publish.get("test_link_type") or "Tests")},
        "project": project,
        "run": {"env": run_env_of(wb, project), "envs": environments(root, project),
                "timeout_min": int(wb.get("run_timeout_min") or 45)},
        "suite_map": {"always": mapping.get("always") or [], "rules": mapping.get("rules") or [],
                      "local": os.path.isfile(local_map), "problem": mapping.get("problem", "")},
        "env": env, "env_from": source, "file": rel, "exists": os.path.isfile(_path(root, project)),
        "jira": {"base_urls": [str(u) for u in jira.get("base_urls") or []],
                 "pat": _token_state(jira.get("pat"))},
        "confluence": {"base_urls": [str(u) for u in conf.get("base_urls") or []],
                       "pat": _token_state(conf.get("pat"))},
        "devcode": {"base_urls": [str(u) for u in dev.get("base_urls") or []],
                    "token": _token_state(dev.get("token")),
                    "hosts": "\n".join(f"{h} {v.get('kind', '')}" + (f" {v['api']}" if v.get("api") else "")
                                       for h, v in dev_hosts.items() if isinstance(v, dict))},
        "cursor": {"key": _token_state(cur.get("apiKey") or cur.get("api_key")),
                   "key_from_env": bool((os.environ.get("CURSOR_API_KEY") or "").strip()),
                   "model": str(cur.get("model") or ""), "design_model": str(cur.get("designModel") or ""),
                   "file": cursor_rel(root)},
        "cursor_runtime": _cursor_runtime(root),
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


_HOST_RX = re.compile(r"^[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?$")


def _devcode_hosts(text) -> dict:
    """`host kind [api]` a line -> {host: {kind, api}}, as devcode.forge_of
    reads it. Only for a host that is neither github.com nor named "gitlab":
    those are known without it. kind is gitlab or github; api, when left
    out, is https://<host>/api/v4 (GitLab) or /api/v3 (GitHub Enterprise)."""
    out = {}
    for n, line in enumerate(str(text or "").splitlines(), 1):
        parts = line.split()
        if not parts:
            continue
        host = parts[0].lower()
        kind = parts[1].lower() if len(parts) > 1 else ""
        if not _HOST_RX.match(host):
            raise ValueError(f"line {n}: {parts[0]!r} is not a host name (no https://, no path)")
        if kind not in ("gitlab", "github") or len(parts) > 3:
            raise ValueError(f"line {n}: write it as `host gitlab|github [https://api address]`")
        entry = {"kind": kind}
        if len(parts) == 3:
            if not _URL_RX.match(parts[2].rstrip("/")):
                raise ValueError(f"line {n}: {parts[2]!r} is not an https:// address")
            entry["api"] = parts[2].rstrip("/")
        out[host] = entry
    return out


def _token(value, what: str) -> str:
    v = str(value or "").strip()
    if not _TOKEN_RX.match(v):
        raise ValueError(f"the {what} token does not look like one: 8 to 500 characters, no spaces")
    return v


def save(changes: dict, root: str = "", busy=None, project: str = "") -> dict:
    """Write what the page sent into the active environment's block. Fields
    missing from `changes` stay as they are; a token sent empty stays as it
    is; `<what>_clear: true` removes that token. Refused while `busy`
    (anything running): a Cursor run guards this file, and a run reading it
    half-written would read nonsense."""
    if busy:
        raise ValueError(f"{busy} is running: save the settings when nothing is running")
    if not isinstance(changes, dict):
        raise ValueError("send the settings as an object")
    project = _project(project)
    if "move_env" in changes:                            # on its own: two files change
        return _move_env(changes["move_env"], root, project)
    CONFIG_REL = _rel(project)                           # this save's file, in every message below
    env, _source = _env(root)
    doc = _read(root, project)
    had_block = env in doc
    block = doc.setdefault(env, {})
    had = set(block) if isinstance(block, dict) else set()
    if not isinstance(block, dict):
        raise ValueError(f"`{env}` in {CONFIG_REL} is not an object")
    jira = block.setdefault("jira_config", {})
    conf = block.setdefault("confluence_config", {})
    xray = block.setdefault("xray", {})
    dev = block.setdefault("devcode_config", {})
    for sec, name in ((jira, "jira_config"), (conf, "confluence_config"), (xray, "xray"),
                      (dev, "devcode_config")):
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
    # The developer's repository (devcode.py): the hosts that may be read --
    # the token goes only to these -- the token, and how a host that is not
    # github.com or a "gitlab" one answers for merge/pull requests.
    if "devcode_base_urls" in changes:
        u = _urls(changes["devcode_base_urls"])
        if u:
            dev["base_urls"] = u
        else:
            dev.pop("base_urls", None)
        said.append("repository hosts")
    if changes.get("devcode_token_clear"):
        dev.pop("token", None)
        said.append("repository token removed")
    elif str(changes.get("devcode_token") or "").strip():
        dev["token"] = _token(changes["devcode_token"], "repository")
        said.append("repository token")
    if "devcode_hosts" in changes:
        hosts = _devcode_hosts(changes["devcode_hosts"])
        if hosts:
            dev["hosts"] = hosts
        else:
            dev.pop("hosts", None)
        said.append("repository host kinds")
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
    # Stage 4: Create in Xray (jira_config.publish, where xraytests.py reads it)
    if any(k in changes for k in ("xray_create_on", "xray_create_projects", "xray_create_issue_type",
                                  "xray_create_link_type")):
        publish = jira.setdefault("publish", {})
        if not isinstance(publish, dict):
            raise ValueError(f"`{env}.jira_config.publish` in {CONFIG_REL} is not an object")
        if "xray_create_on" in changes:
            publish["create_tests"] = bool(changes["xray_create_on"])
            said.append("creating tests in Xray " + ("on" if changes["xray_create_on"] else "off"))
        if "xray_create_projects" in changes:
            keys = [k.strip().upper() for k in re.split(r"[\s,]+", str(changes["xray_create_projects"] or ""))
                    if k.strip()]
            bad = [k for k in keys if not _PROJECT_RX.match(k)]
            if bad:
                raise ValueError(f"{bad[0]!r} is not a project key like ABC")
            if keys:
                publish["projects"] = keys
            else:
                publish.pop("projects", None)
            said.append("projects")
        for field, key, default in (("xray_create_issue_type", "test_issue_type", "Test"),
                                    ("xray_create_link_type", "test_link_type", "Tests")):
            if field in changes:
                v = str(changes.get(field) or "").strip()
                if v and not _NAME_RX.match(v):
                    raise ValueError(f"{v!r} is not a name like {default!r}")
                if v and v != default:
                    publish[key] = v
                else:
                    publish.pop(key, None)
                said.append(key)
    if "xray_enabled" in changes:
        xray["enabled"] = "true" if changes.get("xray_enabled") else "false"
        said.append("publishing to Xray " + ("on" if changes.get("xray_enabled") else "off"))
    # Cursor: its own file, not program_configuration.json
    cur = _cursor_read(root)
    cur_before = json.dumps(cur, sort_keys=True)
    if changes.get("cursor_key_clear"):
        cur.pop("apiKey", None)
        cur.pop("api_key", None)
        said.append("AI Agent key removed")
    elif str(changes.get("cursor_key") or "").strip():
        cur.pop("api_key", None)
        cur["apiKey"] = _token(changes["cursor_key"], "AI Agent API")
        said.append("AI Agent key")
    for field, key, what in (("cursor_model", "model", "AI Agent model"),
                             ("cursor_design_model", "designModel", "design model")):
        if field in changes:
            v = str(changes.get(field) or "").strip()
            if v and not _MODEL_RX.match(v):
                raise ValueError(f"{v!r} is not a model name (letters, digits, . _ : -)")
            if v:
                cur[key] = v
            else:
                cur.pop(key, None)
            said.append(what)
    cur_changed = json.dumps(cur, sort_keys=True) != cur_before
    # Stage 7: running the tests -- this machine's, in .workbench/ (no secret)
    wb = workbench_settings(root)
    wb_changed = False
    if "run_env" in changes:
        v = str(changes.get("run_env") or "").strip()
        if v not in environments(root, project):
            raise ValueError(f"{v!r} is not an environment of {CONFIG_REL}")
        if project == "b2b":
            wb["run_env"] = v
        else:
            wb.setdefault("projects", {}).setdefault(project, {})["run_env"] = v
        wb_changed = True
        said.append(f"tests run against {v}")
    if "run_timeout_min" in changes:
        try:
            minutes = int(changes.get("run_timeout_min"))
        except (TypeError, ValueError):
            raise ValueError("the time limit is a number of minutes") from None
        if not 5 <= minutes <= 240:
            raise ValueError("the time limit is between 5 and 240 minutes")
        wb["run_timeout_min"] = minutes
        wb_changed = True
        said.append(f"time limit {minutes} min")
    # Stage 6: the regression mapping -- a local copy of the rules
    sm = _suitemap()
    local_map = os.path.join(root, ".workbench", "suite-map.json") if root else sm.LOCAL_MAP
    map_change = None
    if changes.get("suite_map_reset"):
        map_change = "reset"
        said.append("suite mapping back to the committed rules")
    elif "suite_map" in changes:
        m = changes.get("suite_map")
        if not isinstance(m, dict):
            raise ValueError("send the suite mapping as an object")
        always = sm.clean_groups(m.get("always") or [])
        rules = []
        for r in m.get("rules") or []:
            if not isinstance(r, dict):
                raise ValueError("each rule is an object")
            group = str(r.get("group") or "").strip().lower()
            if not sm.GROUP_RX.match(group):
                raise ValueError(f"{group!r} is not a group name (lower-case letters, digits, - and _)")
            words = lambda v: [w.strip().lower() for w in (v.split(",") if isinstance(v, str) else v or [])
                               if str(w).strip()]
            rules.append({"group": group, "type": words(r.get("type")), "priority": words(r.get("priority"))})
        map_change = {"about": "This machine's suite mapping, set on the Settings tab.",
                      "always": always, "rules": rules}
        said.append("suite mapping")
    # The sections made above only to be written into: dropped when left empty
    for name in ("jira_config", "confluence_config", "xray", "devcode_config"):
        if name not in had and block.get(name) == {}:
            block.pop(name)
    if not had_block and block == {}:
        doc.pop(env)
    # Every value of an environment block (the Environment values section)
    if "config" in changes:
        said.extend(_save_config(changes["config"], doc, root, project, CONFIG_REL))
    unchanged = json.dumps(doc, sort_keys=True) == json.dumps(_read(root, project), sort_keys=True)
    if not said or (unchanged and not wb_changed and map_change is None and not cur_changed):
        return {"saved": False, "said": "nothing to change", "status": status(root, project)}
    if not unchanged:
        _write(doc, root, project)
    if cur_changed:
        cur.setdefault("enabled", False)        # the converter's own assist stays as it was
        _cursor_write(cur, root)
    if wb_changed:
        p = os.path.join(root or ROOT, WORKBENCH_FILE)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with io.open(p, "w", encoding="utf-8") as fh:
            json.dump(wb, fh, indent=1)
    if map_change == "reset":
        try:
            os.remove(local_map)
        except OSError:
            pass
    elif map_change:
        os.makedirs(os.path.dirname(local_map), exist_ok=True)
        with io.open(local_map, "w", encoding="utf-8") as fh:
            json.dump(map_change, fh, indent=1)
    return {"saved": True, "said": ", ".join(said), "env": env, "status": status(root, project)}


# ---- Every value of an environment block ------------------------------------
# The Environment values section: each key of a block of the project's file,
# and each key the committed example lists (so a missing one can be filled
# in) -- except those the fields above already edit. A secret is never sent
# back, as a token above.

_COVERED = {("jira_config", "base_urls"), ("jira_config", "pat"),
            ("confluence_config", "base_urls"), ("confluence_config", "pat"),
            ("xray", "baseUrl"), ("xray", "token"), ("xray", "projectKey"),
            ("xray", "regressionKey"), ("xray", "enabled"),
            ("jira_config", "publish", "create_tests"), ("jira_config", "publish", "projects"),
            ("jira_config", "publish", "test_issue_type"), ("jira_config", "publish", "test_link_type"),
            ("devcode_config", "base_urls"), ("devcode_config", "token"), ("devcode_config", "hosts")}
# By the key's own name: `token_end_point`, `token_route`, `token_service`
# and `testExecutionKey` are not secrets; `token`, `access_token`, `pat`,
# `client_id` and `ui_username` are (as projectconfig.SECRET_HINTS: a user
# name is half a credential).
_SECRET_FIELD_RX = re.compile(r"(?:^|_)(?:pat|password|secret|assertion|token|username|user|apikey|api_key)$"
                              r"|^(?:client_?id|client_?secret|apikey)$", re.I)
_URL_FIELD_RX = re.compile(r"(?:url|end_?point)$", re.I)
_FIELD_KEY_RX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,59}$")
_VALUE_RX = re.compile(r"^[^\x00-\x1f\x7f]{0,2000}$")
_HTTP_RX = re.compile(r"^https?://[^\s]+$")
# Groups whose entries the page may add or remove: one per service host.
_OPEN_GROUPS = ("services",)


def example_rel(project: str = "") -> str:
    """The committed layout-only example of the project's file."""
    rel = _rel(project)
    return rel[:-len(".json")] + ".example.json"


def _example_block(root: str = "", project: str = "") -> dict:
    try:
        with io.open(os.path.join(root or ROOT, *example_rel(project).split("/")), encoding="utf-8-sig") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(doc, dict):
        return {}
    if isinstance(doc.get("stg"), dict):
        return doc["stg"]
    return next((v for k, v in doc.items() if isinstance(v, dict) and not k.startswith("_")), {})


def _secret_field(key: str) -> bool:
    return bool(_SECRET_FIELD_RX.search(str(key)))


def _kind(value) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, list):
        return "list" if all(isinstance(v, str) for v in value) else "json"
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return "boolstr"                                 # "true"/"false" as text: kept as text
    if isinstance(value, (dict, type(None))) or isinstance(value, str):
        return "text"
    return "json"


def _leaves(node: dict, pre: tuple = ()):
    """(path, value) of each value of a block, depth first; `_` keys are notes."""
    for k, v in node.items():
        if str(k).startswith("_"):
            continue
        if isinstance(v, dict):
            yield from _leaves(v, pre + (k,))
        else:
            yield pre + (k,), v


def _notes(block: dict, example: dict) -> dict:
    """A group's note: its `_comment`, or the block's `_<group>_comment`."""
    out = {}
    for src in (example, block):
        for k, v in src.items():
            if isinstance(v, dict) and isinstance(v.get("_comment"), str):
                out[k] = v["_comment"]
            m = re.match(r"^_(.+)_comment$", str(k))
            if m and isinstance(v, str):
                name = m.group(1)                        # _jira_comment: the jira_config group
                out[name + "_config" if (name + "_config") in src else name] = v
    return out


def _get(node, path):
    for k in path:
        if not isinstance(node, dict) or k not in node:
            return None, False
        node = node[k]
    return node, True


def config_fields(root: str = "", project: str = "", edit_env: str = "") -> dict:
    """The values of one environment block, for the page -- a secret only
    as "set, N characters" / "not set". Default: the block the tests run
    against (this tab's run environment), as the Java side reads these
    values from that one; else the active one."""
    project = _project(project)
    doc = _read(root, project)
    active = _env(root)[0]
    run = run_env_of(workbench_settings(root), project)
    env = edit_env or (run if isinstance(doc.get(run), dict) else active)
    if not _ENV_RX.match(env):
        raise ValueError(f"{env!r} is not an environment name (letters, digits, _ and -)")
    block = doc.get(env) if isinstance(doc.get(env), dict) else {}
    example = _example_block(root, project)
    fields = []
    for f in config_fields_from(block, example):
        path = tuple(f["path"])
        value, there = _get(block, path)
        f.update(group=path[0] if len(path) > 1 else "general", in_file=there)
        if f["kind"] == "secret":
            f["state"] = _token_state(value if there else "")
        elif not there:
            f["value"] = [] if f["kind"] == "list" else (False if f["kind"] == "bool" else
                                                         ("false" if f["kind"] == "boolstr" else ""))
        elif f["kind"] == "json":
            f["value"] = json.dumps(value)
        else:
            f["value"] = value if value is not None else ""
        fields.append(f)
    return {"env": env, "envs": environments(root, project), "new": env not in doc,
            "run_env": run, "active_env": active,
            "other_projects": [p for p in _projectconfig().PROJECT_FILES if p != project],
            "example": example_rel(project), "fields": fields,
            "notes": _notes(block, example), "open_groups": list(_OPEN_GROUPS)}


def _coerce(path: tuple, kind: str, raw):
    """The value the page sent, as the type the file holds there."""
    where = ".".join(path)
    if kind == "bool":
        if isinstance(raw, str):
            return raw.strip().lower() in ("true", "yes", "on", "1")
        return bool(raw)
    if kind == "number":
        s = str(raw if raw is not None else "").strip()
        try:
            return int(s)
        except ValueError:
            try:
                return float(s)
            except ValueError:
                raise ValueError(f"{where} is a number; {s!r} is not one") from None
    if kind == "list":
        items = raw if isinstance(raw, list) else re.split(r"[\n,]+", str(raw or ""))
        out = [str(v).strip() for v in items if str(v).strip()]
        for v in out:
            if not _VALUE_RX.match(v):
                raise ValueError(f"{where}: an entry has a line break or a control character")
        return out
    if kind == "boolstr":
        on = raw if isinstance(raw, bool) else str(raw or "").strip().lower() in ("true", "yes", "on", "1")
        return "true" if on else "false"
    if kind == "json":
        try:
            return json.loads(str(raw or "null"))
        except ValueError as e:
            raise ValueError(f"{where} is JSON here; it does not parse: {e}") from None
    s = str(raw if raw is not None else "").strip()
    if not _VALUE_RX.match(s):
        raise ValueError(f"{where}: up to 2000 characters, no line break or control character")
    if s and kind == "text":
        if (_URL_FIELD_RX.search(path[-1]) or path[0] in _OPEN_GROUPS) and not _HTTP_RX.match(s):
            raise ValueError(f"{where} is an address: http:// or https://..., no spaces")
        if path[-1].lower() == "port" and not re.match(r"^[0-9]{1,5}$", s):
            raise ValueError(f"{where} is a port number")
    return s


def _set(block: dict, path: tuple, value, env: str, rel: str) -> None:
    node = block
    for i, k in enumerate(path[:-1]):
        node = node.setdefault(k, {})
        if not isinstance(node, dict):
            raise ValueError(f"`{env}.{'.'.join(path[:i + 1])}` in {rel} is not an object")
    node[path[-1]] = value


def _save_config(change: dict, doc: dict, root: str, project: str, rel: str) -> list:
    """Apply the Environment values section to `doc`; what changed, by path
    (never a value)."""
    if not isinstance(change, dict):
        raise ValueError("send the environment values as an object")
    env = str(change.get("env") or "").strip() or _env(root)[0]
    if not _ENV_RX.match(env) or env.startswith("_"):
        raise ValueError(f"{env!r} is not an environment name (letters, digits, _ and -)")
    said = []
    if env not in doc:
        src = str(change.get("copy_from") or "").strip()
        if src:
            if not isinstance(doc.get(src), dict):
                raise ValueError(f"there is no environment {src!r} in {rel} to copy")
            doc[env] = json.loads(json.dumps(doc[src]))
            said.append(f"environment {env} (a copy of {src})")
        else:
            doc[env] = {}
            said.append(f"environment {env}")
    block = doc[env]
    if not isinstance(block, dict):
        raise ValueError(f"`{env}` in {rel} is not an object")
    known = {tuple(f["path"]): f for f in config_fields_from(block, _example_block(root, project))}

    def field(raw_path, adding=False):
        if not isinstance(raw_path, list) or not raw_path or \
                not all(isinstance(k, str) and _FIELD_KEY_RX.match(k) for k in raw_path):
            raise ValueError(f"{raw_path!r} is not a setting's path")
        path = tuple(raw_path)
        if path in _COVERED:
            raise ValueError(f"{'.'.join(path)} is set in the fields above, not here")
        if path in known:
            return path, known[path]
        if adding and len(path) == 2 and path[0] in _OPEN_GROUPS:
            if "." in path[-1]:
                raise ValueError(f"{path[-1]!r}: a name of letters, digits, _ and -, no dot")
            return path, {"kind": "secret" if _secret_field(path[-1]) else "text", "removable": True}
        raise ValueError(f"{'.'.join(path)} is not a setting of {env} or of {example_rel(project)}")

    for item in change.get("values") or []:
        if not isinstance(item, dict):
            raise ValueError("each value is an object: {path, value}")
        path, f = field(item.get("path"), adding=bool(item.get("add")))
        if f["kind"] == "secret":
            v = str(item.get("value") or "").strip()
            if not v:
                continue                                 # empty: kept as it is
            if not _VALUE_RX.match(v):
                raise ValueError(f"{'.'.join(path)}: no line break or control character")
        else:
            v = _coerce(path, f["kind"], item.get("value"))
        before, there = _get(block, path)
        if f["kind"] == "json" and there and before is not None and type(v) is not type(before):
            raise ValueError(f"{'.'.join(path)} holds a {type(before).__name__}; keep it one")
        if there and before == v and type(before) is type(v):
            continue
        _set(block, path, v, env, rel)
        said.append(".".join(path))
    for raw_path in change.get("clear") or []:
        path, f = field(raw_path)
        if f["kind"] != "secret":
            raise ValueError(f"{'.'.join(path)} is not a secret; type a new value instead")
        parent, there = _get(block, path[:-1])
        if there and isinstance(parent, dict) and path[-1] in parent:
            parent.pop(path[-1])
            said.append(f"{'.'.join(path)} removed")
    for raw_path in change.get("remove") or []:
        path, f = field(raw_path)
        if not f.get("removable"):
            raise ValueError(f"{'.'.join(path)} cannot be removed here; only an entry of "
                             + ", ".join(_OPEN_GROUPS))
        parent, there = _get(block, path[:-1])
        if there and isinstance(parent, dict) and path[-1] in parent:
            parent.pop(path[-1])
            said.append(f"{'.'.join(path)} removed")
    return [f"{env}: {s}" for s in said]


def _move_env(move: dict, root: str, project: str) -> dict:
    """Move one environment block from this project's file to another
    project's -- the blocks of one project left in the other's file from
    when the two shared program_configuration.json. Both files are copied
    to .workbench/settings/ first (_write). Already in the other file, the
    same: only removed here; different: refused, as neither is known right."""
    if not isinstance(move, dict):
        raise ValueError("send the move as an object: {env, to}")
    env = str(move.get("env") or "").strip()
    to = str(move.get("to") or "").strip().lower()
    projects = _projectconfig().PROJECT_FILES
    if to not in projects or to == project:
        raise ValueError(f"{to!r} is not another project ({', '.join(p for p in projects if p != project)})")
    src = _read(root, project)
    if not _ENV_RX.match(env) or not isinstance(src.get(env), dict):
        raise ValueError(f"there is no environment {env!r} in {_rel(project)}")
    dst = _read(root, to)
    if env in dst and dst[env] != src[env]:
        raise ValueError(f"{_rel(to)} already has an environment {env!r}, and it is not the same: "
                         f"compare the two by hand and remove one first")
    if env in dst:
        said = [f"{env} removed from {_rel(project)} ({_rel(to)} already has the same block)"]
    else:
        dst[env] = src[env]
        _write(dst, root, to)                            # the other file first: the block is never in neither
        said = [f"{env} moved from {_rel(project)} to {_rel(to)}"]
    src.pop(env)
    _write(src, root, project)
    # This project's tests no longer run against a block its file lacks; the
    # other project's, when its run environment is a block its file lacks,
    # run against the one it now has.
    wb = workbench_settings(root)
    changed = False
    if run_env_of(wb, project) == env:
        if project == "b2b":
            wb.pop("run_env", None)
        else:
            (wb.get("projects") or {}).get(project, {}).pop("run_env", None)
        changed = True
        said.append(f"{project.upper()}'s tests run against {run_env_of(wb, project)} again")
    if not isinstance(dst.get(run_env_of(wb, to)), dict):
        if to == "b2b":
            wb["run_env"] = env
        else:
            wb.setdefault("projects", {}).setdefault(to, {})["run_env"] = env
        changed = True
        said.append(f"{to.upper()}'s tests run against {env} (Settings: Running the tests)")
    if changed:
        p = os.path.join(root or ROOT, WORKBENCH_FILE)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with io.open(p, "w", encoding="utf-8") as fh:
            json.dump(wb, fh, indent=1)
    return {"saved": True, "said": "; ".join(said), "env": env, "status": status(root, project)}


def config_fields_from(block: dict, example: dict) -> list:
    """The settings a block can hold: its own and its example's (paths and
    kinds only -- what the save checks a path against)."""
    seen, out = set(), []
    for src in (example, block):
        for path, _v in _leaves(src):
            if path in seen or path in _COVERED:
                continue
            seen.add(path)
            value, there = _get(block, path)
            shape = value if there else _get(example, path)[0]
            out.append({"path": list(path),
                        "kind": "secret" if _secret_field(path[-1]) else _kind(shape),
                        "removable": len(path) == 2 and path[0] in _OPEN_GROUPS})
    return out


_SECRET_KEYS = ("pat", "token", "password", "secret", "clientsecret", "apikey", "api_key")


# token_end_point, token_route, token_service: where a token is asked for, not one
_ADDRESS_KEY_RX = re.compile(r"(?:url|end_?point|route|service)$", re.I)


def _without_tokens(node):
    """A copy with no secret: the token-like keys, and every key the
    Environment values section treats as a secret (client_id, assertion,
    user names)."""
    if isinstance(node, dict):
        return {k: ("<removed from the copy>" if (_secret_field(k) or (any(
                        s in str(k).lower().replace("_", "")
                        for s in (x.replace("_", "") for x in _SECRET_KEYS))
                        and not _ADDRESS_KEY_RX.search(str(k))))
                    and isinstance(v, str) and v else _without_tokens(v))
                for k, v in node.items()}
    if isinstance(node, list):
        return [_without_tokens(v) for v in node]
    return node


def _write(doc: dict, root: str = "", project: str = "") -> None:
    p = _path(root, project)
    # Backups of each project's file apart: program_configuration-<stamp>,
    # program_configuration.goal-<stamp>.
    prefix = os.path.basename(p)[:-len(".json")] + "-"
    os.makedirs(os.path.dirname(p), exist_ok=True)
    keep = os.path.join(root or ROOT, BACKUPS)
    if os.path.isfile(p):
        os.makedirs(keep, exist_ok=True)
        # The copy keeps the file's shape and every setting -- but no token:
        # a token removed on the page must not live on in a copy.
        with io.open(p, encoding="utf-8-sig") as fh:
            before = json.load(fh)
        # The clock alone does not name two saves apart: on Windows two
        # saves in a row can read the same time, and the second copy then
        # replaced the first.
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        n = 1                                   # numbered, so the names sort in order
        name = f"{prefix}{stamp}-{n:02d}.json"
        while os.path.exists(os.path.join(keep, name)):
            n += 1
            name = f"{prefix}{stamp}-{n:02d}.json"
        with io.open(os.path.join(keep, name), "w", encoding="utf-8") as fh:
            json.dump(_without_tokens(before), fh, indent=2)
        old = sorted(n for n in os.listdir(keep) if n.startswith(prefix))
        for name in old:
            full = os.path.join(keep, name)
            try:
                with io.open(full, encoding="utf-8-sig") as fh:
                    data = json.load(fh)
                clean = _without_tokens(data)
                if clean != data:
                    with io.open(full, "w", encoding="utf-8") as fh:
                        json.dump(clean, fh, indent=2)
            except (OSError, ValueError):
                pass
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
