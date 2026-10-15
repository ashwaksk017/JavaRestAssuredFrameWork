"""Publish a run's results to Xray (Server / Data Center), and add its
regression tests to the regression Test Set or Test Plan.

    python tools/agent/xraypublish.py --job <job>

Reads the job's last run (execution.json, written by `loop.py run`) and the
Xray settings (the Settings tab: xray.baseUrl, .token, .projectKey,
.regressionKey in program_configuration.json).

- One NEW Test Execution per publish, in xray.projectKey, labelled with
  the run's environment: POST <baseUrl>/rest/raven/1.0/import/execution.
- Each result's Xray key: its data row's `jira_xray_id`, else
  @XrayTest("KEY") on its method. A key run for several rows gets the worst
  of them (FAIL over TODO over PASS). A result with no key is listed as not
  published -- never guessed.
- The tests in the `regression` group are added to xray.regressionKey: a
  Test Set or a Test Plan, whichever Jira says it is; with a Test Plan the
  execution is put in it too.
- Mock Jira (tools/agent/mock.json): what would be sent is recorded and
  nothing is contacted.

Writes xray-publish.json in the job. Never verified against a live Xray:
the route and the status words are Xray Server's documented import
format -- the first real publish is the proof.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))


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


diag = _load("workbench_diag", os.path.join(os.path.dirname(HERE), "diag.py"))
xt = _load("agent_xraytests", os.path.join(HERE, "xraytests.py"))
settings = _load("agent_settings", os.path.join(HERE, "settings.py"))
loop = xt.loop
search = xt.search
ROOT = loop.intake.ROOT

IMPORT_PATH = "/rest/raven/1.0/import/execution"
STATUS = {"PASS": "PASS", "FAIL": "FAIL", "SKIP": "TODO"}
WORST = {"FAIL": 3, "TODO": 2, "PASS": 1}
_KEY_RX = re.compile(r"^[A-Z][A-Z0-9_]{0,19}-[0-9]{1,9}$")
_ROW_KEY_RX = re.compile(r"jira_xray_id=([A-Z][A-Z0-9_]{0,19}-[0-9]{1,9})\b")
_ANN_RX = re.compile(r'@XrayTest\(\s*"([A-Z][A-Z0-9_]{0,19}-[0-9]{1,9})"\s*\)')


class Refused(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def annotation_key(root: str, cls: str, method: str) -> str:
    """@XrayTest("KEY") on `method` of test class `cls`, from its source."""
    path = os.path.join(root, "src", "test", "java", *cls.split(".")) + ".java"
    text = loop._read(path, 2_000_000)
    m = re.search(r"\bvoid\s+" + re.escape(method) + r"\s*\(", text)
    if not m:
        return ""
    head = text[:m.start()]
    # String literals set aside first: a `}` or `;` inside one (the SKILL's
    # own @Description("... /businesses/{accountId}/...")) is not the end of
    # the method before.
    strings = []

    def keep(match):
        strings.append(match.group(0))
        return f"\x00{len(strings) - 1}\x00"
    plain = re.sub(r'"""[\s\S]*?"""|"(?:\\.|[^"\\\n])*"', keep, head)
    plain = plain[max(plain.rfind("}"), plain.rfind(";")) + 1:]
    found = re.findall(r"@XrayTest\(\s*\x00(\d+)\x00\s*\)", plain)
    if not found:
        return ""
    key = strings[int(found[-1])].strip('"')
    return key if _KEY_RX.match(key) else ""


def keyed(root: str, tests: list) -> tuple:
    """({key: status}, [not published: {test, why}])."""
    by_key, unkeyed = {}, []
    for t in tests:
        if t.get("config") or t.get("switched_off") or "switched off in the data sheet" in str(t.get("message") or ""):
            continue                        # the author's choice, not a test that did not run
        status = STATUS.get(str(t.get("status") or "").upper(), "TODO")
        m = _ROW_KEY_RX.search(str(t.get("params") or ""))
        key = m.group(1) if m else annotation_key(root, t.get("class", ""), t.get("method", ""))
        name = f"{t.get('class', '').rsplit('.', 1)[-1]}.{t.get('method', '')}"
        if not key:
            unkeyed.append({"test": name, "why": "no jira_xray_id in its row and no @XrayTest on it"})
            continue
        if WORST[status] > WORST.get(by_key.get(key, ""), 0):
            by_key[key] = status
    return by_key, unkeyed


def _xray_settings(root: str) -> dict:
    env, _src = settings._env(root if root != ROOT else "")
    doc = settings._read(root if root != ROOT else "")
    block = doc.get(env) if isinstance(doc.get(env), dict) else {}
    xray = block.get("xray") if isinstance(block.get("xray"), dict) else {}
    return {"env": env, "base": str(xray.get("baseUrl") or "").rstrip("/"),
            "token": str(xray.get("token") or "").strip(),
            "project": str(xray.get("projectKey") or "").strip(),
            "regression": str(xray.get("regressionKey") or "").strip()}


def run(job: str, root: str = "", transport=None, conf: dict = None, mocked: bool = None) -> dict:
    root = root or ROOT
    job_dir = loop.intake.job_dir(job, root)
    try:
        with io.open(os.path.join(job_dir, "execution.json"), encoding="utf-8") as fh:
            ran = json.load(fh)
    except (OSError, ValueError):
        raise Refused("this job has no run to publish: Run the tests first (Commit and push tab)")
    try:
        with io.open(os.path.join(job_dir, "xray-publish.json"), encoding="utf-8") as fh:
            before = json.load(fh)
    except (OSError, ValueError):
        before = {}
    if before.get("ok") and before.get("run_at") == ran.get("at") and not before.get("mock"):
        raise Refused(f"this run was published already, as {before.get('execution')}: run the tests "
                      f"again for a new Test Execution")
    mocked = search.mock_jira() if mocked is None else mocked
    conf = conf or _xray_settings(root)
    if not mocked:
        missing = [n for n, k in (("address", "base"), ("token", "token"), ("project", "project"))
                   if not conf.get(k)]
        if missing:
            raise Refused("Xray is not set up: " + ", ".join(missing) + " missing (Settings tab)")
        if "atlassian.net" in conf["base"] or "getxray.app" in conf["base"]:
            raise Refused("Xray Cloud is not published to from here: Server / Data Center only")
    by_key, unkeyed = keyed(root, ran.get("tests") or [])
    if not by_key:
        raise Refused("none of the run's tests carries an Xray key (a jira_xray_id column, or "
                      "@XrayTest): there is nothing to publish to")
    story = loop.story_key_of(job_dir)
    info = {"summary": f"{story or job}: automated run on {ran.get('env')} ({ran.get('at')})",
            "description": (f"Run by the test workbench, job {job}: {ran.get('passed')} passed, "
                            f"{ran.get('failed')} failed, {ran.get('skipped')} skipped."),
            "project": conf.get("project") or "MOCK", "testEnvironments": [str(ran.get("env") or "")]}
    out = {"kind": "xray-publish", "at": _now(), "job": job, "mock": bool(mocked),
           "run_at": ran.get("at"), "published": by_key, "not_published": unkeyed}
    in_regression = set(_regression_keys(root, ran, job_dir, mocked))
    regression_keys = sorted(k for k in by_key if k in in_regression)
    xconn = {"base": conf.get("base") or "https://xray.mock", "api": "", "token": conf.get("token") or "",
             "timeout": 60}
    reg = conf.get("regression")
    reg_type = ""
    if reg:
        # Looked up whatever the run holds: a Test Plan takes the execution
        # even when no regression test is added this time.
        if mocked:
            reg_type = "Test Set"
        else:
            issue = search._call(dict(xconn, api="/rest/api/2"), "GET", f"/issue/{reg}?fields=issuetype",
                                 None, transport)
            reg_type = str(((issue or {}).get("fields") or {}).get("issuetype", {}).get("name") or "")
            if reg_type not in ("Test Set", "Test Plan"):
                raise Refused(f"{reg} is a {reg_type or 'issue of an unknown type'}, not a Test Set or a "
                              f"Test Plan (Settings tab)")
        if reg_type == "Test Plan":
            info["testPlanKey"] = reg
    body = {"info": info, "tests": [{"testKey": k, "status": s,
                                     "comment": f"workbench job {job}, {ran.get('env')}"}
                                    for k, s in sorted(by_key.items())]}
    if mocked:
        out.update(execution="MOCK-EXEC", sent=body, note="mock Jira: nothing was contacted")
    else:
        diag.step("Creating the Test Execution in Xray")
        answer = search._call(xconn, "POST", IMPORT_PATH, body, transport)
        key = str(((answer or {}).get("testExecIssue") or {}).get("key") or "")
        out["execution"] = key
        if not key:
            out["warning"] = "Xray answered without the key of a Test Execution: " + json.dumps(answer)[:300]
    out["ok"] = bool(out.get("execution"))
    _keep(job_dir, out)                     # the execution exists now, whatever follows
    if reg and regression_keys:
        kind = "testplan" if reg_type == "Test Plan" else "testset"
        out["regression"] = {"key": reg, "type": reg_type, "added": regression_keys}
        if not mocked:
            diag.step(f"Adding the regression tests to {reg}")
            try:
                search._call(xconn, "POST", f"/rest/raven/1.0/api/{kind}/{reg}/test",
                             {"add": regression_keys}, transport)
            except search.JiraError as e:
                out["regression"] = {"key": reg, "type": reg_type, "added": [],
                                     "error": f"Xray refused adding them (HTTP {e.status}): {str(e)[:200]}"}
    elif regression_keys:
        out["regression"] = {"key": "", "added": [],
                             "note": "no regression Test Set or Test Plan is set (Settings tab)"}
    _keep(job_dir, out)
    return out


def _keep(job_dir: str, out: dict) -> None:
    with io.open(os.path.join(job_dir, "xray-publish.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)


def _regression_keys(root: str, ran: dict, job_dir: str = "", mocked: bool = False) -> list:
    """The Xray keys of the cases a person CONFIRMED as regression
    (workflow.json); for a job from before that step, the run's tests in
    the `regression` group."""
    try:
        with io.open(os.path.join(job_dir, "workflow.json"), encoding="utf-8") as fh:
            confirmed = (json.load(fh).get("regression") or {}).get("cases")
    except (OSError, ValueError, AttributeError):
        confirmed = None
    if isinstance(confirmed, list):
        keys = xt.keys_for(job_dir, "design", mocked)
        return [keys[c] for c in confirmed if c in keys]
    tests = [t for t in ran.get("tests") or [] if "regression" in (t.get("groups") or [])]
    return list(keyed(root, tests)[0])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--job", required=True)
    args = ap.parse_args(argv)
    try:
        r = run(loop.intake.safe_job(args.job))
    except (Refused, search.Refused) as e:
        print(f"FAIL {e}")
        return 1
    except search.JiraError as e:
        print(f"FAIL Xray refused it (HTTP {e.status}): {str(e)[:300]}")
        return 1
    print((" ok  " if r["ok"] else "WARN ") + f"Test Execution {r.get('execution') or '(none)'}"
          + (" (mock: nothing was contacted)" if r["mock"] else "")
          + f": {len(r['published'])} test(s) published")
    for key, status in sorted(r["published"].items()):
        print(f"      {status:<4} {key}")
    for n in r["not_published"]:
        print(f" ..  not published: {n['test']} -- {n['why']}")
    reg = r.get("regression") or {}
    if reg.get("error"):
        print(f"WARN the Test Execution was made, but {reg['error']}")
    elif reg.get("added"):
        print(f" ok  {len(reg['added'])} regression test(s) added to {reg['key']} ({reg['type']})")
    elif reg.get("note"):
        print(f" ..  {reg['note']}")
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(diag.run("xraypublish", main))
