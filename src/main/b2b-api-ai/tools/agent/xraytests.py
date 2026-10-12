"""Create a job's designed or manual test cases as Xray Test issues in Jira.

    python tools/agent/xraytests.py status --job J
    python tools/agent/xraytests.py create --job J --what design --story ABC-12 --confirm ABC-12
    python tools/agent/xraytests.py create --job J --what manual --story ABC-12 --confirm ABC-12

Design the API tests, and Manual tests, each leave a list of cases. Until
now the way into Xray was a CSV for its importer, done by hand, and the
keys Xray gave the tests never came back. This creates them, one Jira
issue of the Test type per case, with the steps, linked to the story --
and keeps the keys in `xray-tests.json`, where the Agentic Test Creation
step reads them so that the row it writes for a case carries that key in
its `jira_xray_id` column.

IT WRITES TO JIRA, so the same rules as publish.py:

* OFF unless `jira_config.publish.create_tests` is exactly `true`.
  `projects`, when set, is the list of projects it may create in.
* The story key is given twice (`--confirm`). Tests are created in the
  story's project and linked to it.
* A result made by a stand-in is not sent to a real Jira; `--expect`
  says what the page told the person the target is.
* Every case is recorded BEFORE its issue is created, and its key is
  written down the moment Jira answers: a run that dies half way leaves
  the keys it got, and the next run creates only what has none.
* A case that already has a key is not created again. A case whose text
  changed since is said, not updated (nothing here edits an issue).
* Text from a model goes out as text: summary and steps, no markup of
  the workbench's own, and nothing from the private configuration.

XRAY SERVER / DATA CENTER ONLY, for now. Steps are added through Xray's
own REST API (`/rest/raven/1.0/api/test/<key>/step`), which Xray Cloud
does not have; Cloud is refused with a reason. The step call has not been
run against a live Xray: if yours answers differently, `xray_step_call`
in `jira_config.publish` names the verb and path to use.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))


def _diag():
    import importlib.util as _ilu
    if "workbench_diag" not in sys.modules:
        _spec = _ilu.spec_from_file_location(
            "workbench_diag", os.path.join(os.path.dirname(HERE), "diag.py"))
        _mod = _ilu.module_from_spec(_spec)
        sys.modules["workbench_diag"] = _mod
        try:
            _spec.loader.exec_module(_mod)
        except BaseException:
            sys.modules.pop("workbench_diag", None)
            raise
    return sys.modules["workbench_diag"]


diag = _diag()
TOOLS = os.path.dirname(HERE)


def _load(name: str, path: str):
    import importlib.util
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return mod


publish = _load("agent_publish", os.path.join(HERE, "publish.py"))
loop = publish.loop
search = publish.search
Refused = publish.Refused
ROOT = loop.ROOT

KEYS = "xray-tests.json"             # the keys Jira gave, kept for the job
RESULT = "xray-result.json"          # the last create (or its refusal)
STATUS = "xray-status.json"
LOG = "xray-tests.log"
WHATS = ("design", "manual")
MAX_SUMMARY = 240
MAX_STEP = 4000
MAX_CASES = 200
DEFAULT_TYPE = "Test"
DEFAULT_LINK = "Tests"
DEFAULT_STEP_CALL = "PUT /rest/raven/1.0/api/test/{key}/step"
_KEY_RX = re.compile(r"^[A-Z][A-Z0-9_]+-[0-9]+$")
_STEP_CALL_RX = re.compile(r"^(PUT|POST)\s+(/[A-Za-z0-9_./{}-]*\{key\}[A-Za-z0-9_./-]*)$")


def say(msg: str = "") -> None:
    print(msg, flush=True)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _text(value, cap: int) -> str:
    """Text from a model or a result, as Jira will show it: one value, no
    control characters, bounded."""
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(value if value is not None else ""))
    s = s.strip()
    return s[:cap] + ("..." if len(s) > cap else "")


def _one(value, cap: int = 200) -> str:
    return re.sub(r"\s+", " ", _text(value, cap))


# ---- what is switched on ------------------------------------------------------

def switch(cfg: dict = None, mocked: bool = False) -> dict:
    """{on, projects, issue_type, link_type, step_call} from
    jira_config.publish. A stand-in connection has it on."""
    out = {"on": False, "projects": [], "issue_type": DEFAULT_TYPE, "link_type": DEFAULT_LINK,
           "step_call": DEFAULT_STEP_CALL}
    if mocked:
        out["on"] = True
        return out
    if cfg is None:
        cfg, _ = search.projectconfig.section("jira_config")
    p = publish._switch_block(cfg, "jira_config")
    out["on"] = p.get("create_tests") is True
    if "projects" in p:
        listed = p["projects"]
        if (not isinstance(listed, list) or not listed
                or not all(isinstance(x, str) and publish._PROJECT_RX.match(x.strip().upper())
                           for x in listed)):
            raise Refused("jira_config.publish.projects must be a list of project keys, like "
                          "[\"ABC\"]. Leave the setting out altogether to allow any project "
                          "the token can write to.")
        out["projects"] = [x.strip().upper() for x in listed]
    for name, key in (("issue_type", "test_issue_type"), ("link_type", "test_link_type")):
        v = p.get(key)
        if v is not None:
            if not isinstance(v, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9 _-]{0,40}", v.strip()):
                raise Refused(f"jira_config.publish.{key} must be a name like \"Test\"")
            out[name] = v.strip()
    v = p.get("xray_step_call")
    if v is not None:
        if not isinstance(v, str) or not _STEP_CALL_RX.match(v.strip()):
            raise Refused("jira_config.publish.xray_step_call must be like "
                          f"\"{DEFAULT_STEP_CALL}\" ({{key}} stands for the test's key)")
        out["step_call"] = v.strip()
    return out


def flavour(cfg: dict = None) -> str:
    """cloud | server, from the `xray` section: stated, or read from the
    host. Unstated and no host: server, because the Jira this talks to is
    one a token signs in to, and that is Server / Data Center."""
    if cfg is None:
        cfg, _ = search.projectconfig.section("xray")
    cfg = cfg if isinstance(cfg, dict) else {}
    stated = str(cfg.get("flavour") or "").strip().lower()
    if stated in ("cloud", "server"):
        return stated
    host = str(cfg.get("baseUrl") or "").lower()
    if "getxray.app" in host or "xray.cloud" in host:
        return "cloud"
    if cfg.get("clientId") and cfg.get("clientSecret") and not cfg.get("token"):
        return "cloud"
    return "server"


# ---- the cases ---------------------------------------------------------------------

def cases_of(data: dict, what: str) -> list:
    """The cases of a result, in one shape: [{id, title, preconditions,
    endpoint, priority, steps: [{action, data, expected}], refs}]."""
    rows = data.get("test_cases" if what == "design" else "new_cases")
    out = []
    for n, c in enumerate(rows if isinstance(rows, list) else [], 1):
        if not isinstance(c, dict):
            continue
        steps = []
        for s in c.get("steps") if isinstance(c.get("steps"), list) else []:
            if isinstance(s, dict):
                steps.append({"action": _text(s.get("action"), MAX_STEP),
                              "data": _text(s.get("data"), MAX_STEP),
                              "expected": _text(s.get("expected"), MAX_STEP)})
        case = {"id": _one(c.get("id"), 40) or f"{'TC' if what == 'design' else 'MT'}-{n:03d}",
                "title": _one(c.get("title"), MAX_SUMMARY),
                "preconditions": _text(c.get("preconditions"), MAX_STEP),
                "endpoint": _one(c.get("endpoint"), 300),
                "priority": _one(c.get("priority"), 20),
                "type": _one(c.get("type") or c.get("category"), 40),
                "steps": steps,
                "refs": [_one(r, 200) for r in (c.get("requirement_refs") or [])[:10]
                         if isinstance(r, str)]}
        if case["title"] and steps:
            out.append(case)
    seen, twice = set(), []
    for case in out:
        (twice if case["id"] in seen else seen).add(case["id"]) if case["id"] not in seen \
            else twice.append(case["id"])
    if twice:
        raise Refused(f"the {what} result gives one id to more than one case "
                      f"({', '.join(sorted(set(twice))[:5])}); a key is kept by id, so each "
                      f"case needs its own. Design (or generate) again.")
    return out[:MAX_CASES]


def fingerprint(case: dict) -> str:
    body = json.dumps([case["title"], case["preconditions"], case["steps"]], sort_keys=True)
    return hashlib.sha1(body.encode("utf-8")).hexdigest()[:16]


def description_of(case: dict, story: str, what: str) -> str:
    """The issue's description, in Jira's own wiki markup -- built here from
    plain text, so that nothing a model wrote becomes markup."""
    def plain(s: str) -> str:
        return re.sub(r"[{}\[\]|*_^~!#+-]", lambda m: "\\" + m.group(0), s)
    lines = [f"Created by the test workbench from the "
             f"{'API test design' if what == 'design' else 'manual test cases'} for {story}.", ""]
    if case["endpoint"]:
        lines += [f"Endpoint: {plain(case['endpoint'])}", ""]
    if case["preconditions"]:
        lines += ["Preconditions:", plain(case["preconditions"]), ""]
    if case["refs"]:
        lines += ["Covers:"] + [f"- {plain(r)}" for r in case["refs"]] + [""]
    lines += [f"Case {plain(case['id'])}"
              + (f", {plain(case['type'])}" if case["type"] else "")
              + (f", priority {plain(case['priority'])}" if case["priority"] else "") + "."]
    return "\n".join(lines)


# ---- the keys kept for the job -----------------------------------------------------

def read_keys(job_dir: str) -> dict:
    data = publish._json(os.path.join(job_dir, KEYS))
    if not isinstance(data, dict) or not isinstance(data.get("tests"), dict):
        return {"kind": "xray-tests", "tests": {}}
    return data


def write_keys(job_dir: str, data: dict) -> None:
    data["kind"] = "xray-tests"
    data["at"] = _now()
    publish._write(os.path.join(job_dir, KEYS), json.dumps(data, indent=1, ensure_ascii=True))


def keys_for(job_dir: str, what: str, mocked: bool = None) -> dict:
    """{case id: key} for the cases of `what` that have one. With `mocked`
    given, only the keys of that kind: a stand-in's key is never handed to
    a real run, nor a real key to a stand-in's."""
    out = {}
    for ident, entry in read_keys(job_dir)["tests"].items():
        if not isinstance(entry, dict) or not ident.startswith(what + ":"):
            continue
        if mocked is not None and bool(entry.get("mock")) != bool(mocked):
            continue
        key = str(entry.get("key") or "").upper()
        if _KEY_RX.match(key):
            out[ident.split(":", 1)[1]] = key
    return out


# ---- create ---------------------------------------------------------------------------

def _log(job_dir: str, line: str) -> None:
    """One line on the record. Never the reason a create fails: by the
    time it is written, Jira may already hold the issue."""
    line = re.sub(r"[\r\n\x00-\x08\x0b\x0c\x0e-\x1f]+", " ", str(line))[:600]
    try:
        with io.open(os.path.join(job_dir, LOG), "a", encoding="utf-8") as fh:
            fh.write(f"{_now()}  {line}\n")
    except OSError:
        diag.trace()


def create(job: str, what: str, story: str, confirm: str, root: str = "", conn=None,
           cfg: dict = None, xray_cfg: dict = None, transport=None, expect: str = "",
           on_attempt=None) -> dict:
    root = root or ROOT
    job_dir = loop.intake.job_dir(job, root)
    story = (story or "").strip().upper()
    if what not in WHATS:
        raise Refused(f"--what is one of: {', '.join(WHATS)}")
    if not _KEY_RX.match(story):
        raise Refused(f"{story!r} is not an issue key like ABC-12")
    if (confirm or "").strip().upper() != story:
        raise Refused("the story key must be given twice, the same: this writes to Jira")
    conn = conn or search.connect()
    mocked = bool(conn.get("mock"))
    publish.expected(expect, mocked, "Jira")
    if mocked and transport is None:
        transport = lambda m, u, t, s, b=None: publish._mock().jira_transport(m, u, t, s, b, root)
    if not mocked and flavour(xray_cfg) == "cloud":
        raise Refused("the xray configuration says Xray CLOUD. Cloud creates tests through "
                      "another API that this does not speak yet; import xray.csv / "
                      "manual-new-cases.csv with Xray's importer for now.")
    opts = switch(cfg, mocked)
    if not opts["on"]:
        raise Refused("creating tests in Jira is switched off. To allow it, set "
                      "jira_config.publish.create_tests to true in program_configuration.json.")
    if not mocked:
        publish.https_only(conn["base"], "Jira")
    result = publish.result_for(job_dir, what)
    if not mocked and not publish.provably_real(result, what):
        raise Refused("this result was made by a stand-in (mock), or from sample data, or does "
                      "not say how it was made. It is not created in a real Jira. Run the step "
                      "again for real first.")
    cases = cases_of(result, what)
    if not cases:
        raise Refused(f"the {what} result holds no case with a title and steps")
    private = publish.private_values(root)
    found = publish.leaks([json.dumps(c, ensure_ascii=False) for c in cases], private)
    if found:
        raise Refused("the cases contain private values and are not sent: "
                      + "; ".join(found[:4]))

    # The story, as Jira has it: it exists, and which project it is in.
    issue = search._call(conn, "GET", f"/issue/{story}?fields=summary,project,issuetype", None,
                         transport)
    if not isinstance(issue, dict) or str(issue.get("key") or "").upper() != story:
        raise Refused(f"Jira did not answer with {story} when asked for it; nothing was created")
    fields = issue.get("fields") if isinstance(issue.get("fields"), dict) else {}
    project = str(((fields.get("project") or {}) if isinstance(fields.get("project"), dict)
                   else {}).get("key") or story.split("-")[0]).upper()
    if not publish._PROJECT_RX.match(project):
        raise Refused(f"Jira names the story's project as {_one(project, 40)!r}, which is not "
                      f"a project key; nothing was created")
    if opts["projects"] and project not in opts["projects"]:
        raise Refused(f"project {project} is not in jira_config.publish.projects "
                      f"({', '.join(opts['projects'])})")

    kept = read_keys(job_dir)
    done = {"kind": "xray-create", "what": what, "story": story, "project": project,
            "mock": mocked, "created": [], "kept": [], "changed": [], "failed": [],
            "ok": None, "at": _now()}
    # ...and what this job already has, checked against Jira: a test that
    # was deleted there is created again, and said.
    # The same case under another id -- a regeneration numbered the cases
    # differently -- is the same test. Found by its text, not its number,
    # over ALL the cases first: moving one entry while reading the next
    # handed one key to two cases.
    mine = {ident: entry for ident, entry in kept["tests"].items()
            if isinstance(entry, dict) and ident.startswith(what + ":")
            and bool(entry.get("mock")) == mocked and _KEY_RX.match(str(entry.get("key") or ""))}
    by_text = {}
    for ident, entry in mine.items():
        if entry.get("fingerprint"):
            by_text.setdefault(entry["fingerprint"], ident)
    assigned, taken = {}, set()
    for case in cases:                                   # first, the same text
        ident, fp = f"{what}:{case['id']}", fingerprint(case)
        same = ident if mine.get(ident, {}).get("fingerprint") == fp else by_text.get(fp)
        if same and same not in taken:
            assigned[ident] = (same, mine[same])
            taken.add(same)
    for case in cases:                                   # then, the same id with other text
        ident = f"{what}:{case['id']}"
        if ident not in assigned and ident in mine and ident not in taken:
            assigned[ident] = (ident, mine[ident])
            taken.add(ident)
    moved_any = False
    for ident, (was, entry) in assigned.items():
        if was != ident:
            entry = dict(entry, moved_from=was.split(":", 1)[1])
            assigned[ident] = (was, entry)
            _log(job_dir, f"MOVED {entry['key']}: the case was {was} and is now {ident}")
            moved_any = True
        kept["tests"][ident] = entry
    for was in list(mine):                                # an id no case has now
        if was not in assigned and was in taken:
            kept["tests"].pop(was, None)
    if moved_any:
        write_keys(job_dir, kept)
    todo = []
    for case in cases:
        ident = f"{what}:{case['id']}"
        entry = assigned.get(ident, (None, None))[1]
        if entry and bool(entry.get("mock")) == mocked and _KEY_RX.match(str(entry.get("key") or "")):
            key = str(entry["key"]).upper()
            try:
                there = search._call(conn, "GET", f"/issue/{key}?fields=summary", None, transport)
                exists = isinstance(there, dict) and str(there.get("key") or "").upper() == key
            except search.JiraError as e:
                if e.status == 404:
                    exists = False
                else:
                    raise
            if exists:
                row = {"id": case["id"], "key": key, "title": case["title"]}
                if entry.get("moved_from"):
                    row["note"] = (f"this case was {entry['moved_from']} when {key} was created; "
                                   f"the key now follows it as {case['id']}")
                if entry.get("fingerprint") != fingerprint(case):
                    row["note"] = (f"the case changed since {key} was created; the issue was "
                                   f"NOT updated (nothing here edits an issue)")
                    done["changed"].append(row)
                else:
                    done["kept"].append(row)
                continue
            _log(job_dir, f"GONE {key} ({ident}) is no longer in Jira; it will be created again")
        todo.append(case)

    verb, path_tpl = opts["step_call"].split(None, 1)
    raven = dict(conn, api="")
    target = "MOCK (a file on this machine)" if mocked else conn["base"]
    _log(job_dir, f"ATTEMPT create {len(todo)} test(s) of {what} for {story} in {project} "
                  f"at {target}")
    if on_attempt and todo:
        on_attempt(dict(done, message=(
            f"Creating {len(todo)} test(s) in Jira for {story} was started and its outcome is "
            f"not known (the command did not finish). xray-tests.json holds the keys that "
            f"came back; look in Jira for the rest before trying again.")))
    for n, case in enumerate(todo, 1):
        ident = f"{what}:{case['id']}"
        diag.step(f"Creating test {n} of {len(todo)} in Jira")
        body = {"fields": {"project": {"key": project},
                           "issuetype": {"name": opts["issue_type"]},
                           "summary": _one(case["title"], MAX_SUMMARY),
                           "description": description_of(case, story, what)}}
        _log(job_dir, f"ATTEMPT issue {ident}: {_one(case['title'], 120)}")
        try:
            made = search._call(conn, "POST", "/issue", body, transport)
        except search.JiraError as e:
            _log(job_dir, f"REFUSED BY JIRA issue {ident}: HTTP {e.status}")
            done["failed"].append({"id": case["id"], "title": case["title"],
                                   "message": f"Jira refused (HTTP {e.status}): "
                                              f"{publish._words(e, 240)}"})
            break                                        # the rest would meet the same answer
        key = str((made or {}).get("key") if isinstance(made, dict) else "").upper()
        if not _KEY_RX.match(key):
            _log(job_dir, f"UNCLEAR issue {ident}: Jira answered without a key")
            done["failed"].append({"id": case["id"], "title": case["title"],
                                   "message": "Jira answered without an issue key; the test MAY "
                                              "have been created: look in Jira"})
            break
        row = {"id": case["id"], "key": key, "title": case["title"], "steps": 0,
               "steps_of": len(case["steps"]), "linked": False}
        kept["tests"][ident] = {"key": key, "title": case["title"], "fingerprint": fingerprint(case),
                                "story": story, "mock": mocked, "at": _now()}
        write_keys(job_dir, kept)                        # the moment it is known
        _log(job_dir, f"DONE issue {ident} -> {key}")
        for s in case["steps"]:
            try:
                search._call(raven, verb, path_tpl.replace("{key}", key),
                             {"step": s["action"], "data": s["data"], "result": s["expected"]},
                             transport)
                row["steps"] += 1
            except search.JiraError as e:
                _log(job_dir, f"REFUSED BY JIRA step for {key}: HTTP {e.status}")
                row["note"] = (f"{row['steps']} of {len(case['steps'])} steps were added; Xray "
                               f"refused the next (HTTP {e.status}). Add the rest in Jira, or "
                               f"set jira_config.publish.xray_step_call if your Xray's API "
                               f"differs.")
                break
        try:
            search._call(conn, "POST", "/issueLink",
                         {"type": {"name": opts["link_type"]},
                          "inwardIssue": {"key": story}, "outwardIssue": {"key": key}}, transport)
            row["linked"] = True
        except search.JiraError as e:
            _log(job_dir, f"REFUSED BY JIRA link {key} -> {story}: HTTP {e.status}")
            row["note"] = (row.get("note", "") + f" Not linked to {story} (HTTP {e.status}); "
                           f"link it in Jira, or set jira_config.publish.test_link_type.").strip()
        done["created"].append(row)
    made_n = len(done["created"])
    done["ok"] = not done["failed"] if (made_n or done["kept"] or done["changed"]) else False
    if done["failed"]:
        done["ok"] = False
    bits = [f"{made_n} test(s) created in {project} for {story}"]
    if done["kept"]:
        bits.append(f"{len(done['kept'])} already there and unchanged")
    if done["changed"]:
        bits.append(f"{len(done['changed'])} already there but changed since (not updated)")
    if done["failed"]:
        bits.append(f"stopped at {done['failed'][0]['id']}: {done['failed'][0]['message']}")
    short = [r for r in done["created"] if r["steps"] < r["steps_of"] or not r["linked"]]
    if short:
        bits.append(f"{len(short)} created with steps or the link missing (see each)")
    done["message"] = ("; ".join(bits) + "."
                       + (" MOCK: a file on this machine was changed; no Jira was contacted."
                          if mocked else "")
                       + (" The keys are in xray-tests.json; the Agentic Test Creation step "
                          "puts them in the jira_xray_id column." if made_n or done["kept"] else ""))
    _log(job_dir, f"END {done['message']}")
    return done


def status(job: str, root: str = "") -> dict:
    """What this job could create, what it already has, and what is on."""
    root = root or ROOT
    job_dir = loop.intake.job_dir(job, root)
    problem, opts, mocked, flav = "", switch({}, False), False, "server"
    try:
        mocked = bool(search.mock_jira())
        opts = switch(None, mocked)
        flav = "server" if mocked else flavour()
    except (Refused, search.Refused, RuntimeError) as e:
        diag.trace()
        problem = str(e)
        opts = switch({}, False)                         # a broken setting is OFF
    story = ""
    told = publish._json(os.path.join(job_dir, "intake.json"))
    if isinstance(told.get("story"), dict):
        story = _one(told["story"].get("key"), 40).upper()
    out = {"kind": "xray-status", "at": _now(), "job": job, "problem": problem,
           "create_tests": opts["on"] and flav != "cloud", "projects": opts["projects"],
           "flavour": flav, "mock_jira": mocked, "story": story if _KEY_RX.match(story) else "",
           "cases": {}, "keys": {}, "real": {}, "notes": {}}
    for what in WHATS:
        try:
            data = publish.result_for(job_dir, what)
            out["cases"][what] = len(cases_of(data, what))
            out["real"][what] = publish.provably_real(data, what)
        except Refused as e:
            out["cases"][what] = 0
            out["real"][what] = False
            if "nothing to publish" not in str(e):
                out["notes"][what] = str(e)
        out["keys"][what] = keys_for(job_dir, what, mocked)
    return out


def main(argv=None, **replace) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("status")
    p.add_argument("--job", required=True)
    p = sub.add_parser("create")
    p.add_argument("--job", required=True)
    p.add_argument("--what", required=True)
    p.add_argument("--story", required=True)
    p.add_argument("--confirm", "--confirm-story", dest="confirm", required=True)
    p.add_argument("--expect", default="")
    a = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    try:
        job_dir = loop.intake.job_dir(a.job)
    except ValueError as e:
        say(f"REFUSED {e}")
        return 2
    if not os.path.isdir(job_dir):
        say(f"REFUSED there is no job {a.job!r}")
        return 2
    mine = STATUS if a.cmd == "status" else RESULT

    def tell(payload: dict) -> None:
        try:
            publish._write(os.path.join(job_dir, mine), json.dumps(payload, ensure_ascii=True))
        except (OSError, ValueError):
            pass
    attempted = []

    def on_attempt(payload: dict) -> None:
        attempted.append(payload)
        tell(payload)
    try:
        if a.cmd == "status":
            r = status(a.job)
            tell(r)
            say(json.dumps({k: r[k] for k in ("create_tests", "flavour", "mock_jira", "story",
                                              "cases", "keys", "problem")}, indent=2))
            return 0
        r = create(a.job, a.what, a.story, a.confirm, expect=a.expect, on_attempt=on_attempt,
                   **replace)
    except (Refused, search.Refused) as e:
        say(f"REFUSED {e}")
        tell({"kind": "error", "refused": True, "message": str(e), "at": _now()})
        return 2
    except search.JiraError as e:
        say(f"FAIL {e}")
        tell({"kind": "error", "refused": False, "at": _now(),
              "message": f"{publish._words(e, 300)} Look at xray-tests.json for what was "
                         f"created before this."})
        return 1
    except Exception as e:                               # noqa: BLE001
        diag.trace()
        say(f"FAIL unexpected {type(e).__name__} in xraytests.py "
            f"(line {e.__traceback__.tb_lineno if e.__traceback__ else '?'})")
        if not attempted:
            tell({"kind": "error", "refused": False, "at": _now(),
                  "message": f"unexpected {type(e).__name__} before anything was sent. "
                             f"Nothing was written."})
        return 1
    tell(r)
    say({True: " ok   ", False: "FAIL  ", None: "UNCLEAR "}[r["ok"]]
        + ("MOCK " if r["mock"] else "") + r["message"])
    for row in r["created"]:
        say(f"  {row['id']:>8}  {row['key']}  {row['title'][:80]}"
            + (f"  -- {row['note']}" if row.get("note") else ""))
    for row in r["changed"]:
        say(f"  {row['id']:>8}  {row['key']}  -- {row['note']}")
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(diag.run("xraytests", main))
