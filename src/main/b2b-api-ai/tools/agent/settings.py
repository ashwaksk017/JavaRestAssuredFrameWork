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


WORKBENCH_FILE = os.path.join(".workbench", "workbench-settings.json")
# Cursor's key and models: the converter's own file (gitignored), where
# loop.cursor_config and design.design_model read them. CURSOR_API_KEY, when
# set, wins over the file's key.
CURSOR_REL = "tools/ra_converter/cursor_agent.json"
_MODEL_RX = re.compile(r"^[A-Za-z0-9._:-]{1,80}$")


def _cursor_path(root: str = "") -> str:
    return os.path.join(root or ROOT, *CURSOR_REL.split("/"))


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


def environments(root: str = "") -> list:
    """The environment blocks of program_configuration.json."""
    try:
        doc = _read(root)
    except (OSError, ValueError):
        return []
    return sorted(k for k, v in doc.items() if isinstance(v, dict) and not k.startswith("_")
                  and _ENV_RX.match(k))


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
    cur = _cursor_read(root)
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
    publish = jira.get("publish") if isinstance(jira.get("publish"), dict) else {}
    wb = workbench_settings(root)
    sm = _suitemap()
    local_map = os.path.join(root, ".workbench", "suite-map.json") if root else sm.LOCAL_MAP
    mapping = sm.load(local_map if os.path.isfile(local_map) else sm.MAP_FILE)
    return {
        "xray_create": {"on": publish.get("create_tests") is True,
                        "projects": [str(p) for p in publish.get("projects") or []],
                        "issue_type": str(publish.get("test_issue_type") or "Test"),
                        "link_type": str(publish.get("test_link_type") or "Tests")},
        "run": {"env": str(wb.get("run_env") or "stg"), "envs": environments(root),
                "timeout_min": int(wb.get("run_timeout_min") or 45)},
        "suite_map": {"always": mapping.get("always") or [], "rules": mapping.get("rules") or [],
                      "local": os.path.isfile(local_map), "problem": mapping.get("problem", "")},
        "env": env, "env_from": source, "file": CONFIG_REL, "exists": os.path.isfile(_path(root)),
        "jira": {"base_urls": [str(u) for u in jira.get("base_urls") or []],
                 "pat": _token_state(jira.get("pat"))},
        "confluence": {"base_urls": [str(u) for u in conf.get("base_urls") or []],
                       "pat": _token_state(conf.get("pat"))},
        "cursor": {"key": _token_state(cur.get("apiKey") or cur.get("api_key")),
                   "key_from_env": bool((os.environ.get("CURSOR_API_KEY") or "").strip()),
                   "model": str(cur.get("model") or ""), "design_model": str(cur.get("designModel") or ""),
                   "file": CURSOR_REL},
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
        said.append("Cursor key removed")
    elif str(changes.get("cursor_key") or "").strip():
        cur.pop("api_key", None)
        cur["apiKey"] = _token(changes["cursor_key"], "Cursor API")
        said.append("Cursor key")
    for field, key, what in (("cursor_model", "model", "Cursor model"),
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
        if v not in environments(root):
            raise ValueError(f"{v!r} is not an environment of {CONFIG_REL}")
        wb["run_env"] = v
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
    unchanged = json.dumps(doc, sort_keys=True) == json.dumps(_read(root), sort_keys=True)
    if not said or (unchanged and not wb_changed and map_change is None and not cur_changed):
        return {"saved": False, "said": "nothing to change", "status": status(root)}
    if not unchanged:
        _write(doc, root)
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
    return {"saved": True, "said": ", ".join(said), "env": env, "status": status(root)}


_SECRET_KEYS = ("pat", "token", "password", "secret", "clientsecret", "apikey", "api_key")


def _without_tokens(node):
    if isinstance(node, dict):
        return {k: ("<removed from the copy>" if any(s in str(k).lower().replace("_", "")
                                                       for s in (x.replace("_", "") for x in _SECRET_KEYS))
                    and isinstance(v, str) and v else _without_tokens(v))
                for k, v in node.items()}
    if isinstance(node, list):
        return [_without_tokens(v) for v in node]
    return node


def _write(doc: dict, root: str = "") -> None:
    p = _path(root)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    keep = os.path.join(root or ROOT, BACKUPS)
    if os.path.isfile(p):
        os.makedirs(keep, exist_ok=True)
        # The copy keeps the file's shape and every setting -- but no token:
        # a token removed on the page must not live on in a copy.
        with io.open(p, encoding="utf-8-sig") as fh:
            before = json.load(fh)
        with io.open(os.path.join(keep, "program_configuration-"
                                  + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".json"),
                     "w", encoding="utf-8") as fh:
            json.dump(_without_tokens(before), fh, indent=2)
        old = sorted(n for n in os.listdir(keep) if n.startswith("program_configuration-"))
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
