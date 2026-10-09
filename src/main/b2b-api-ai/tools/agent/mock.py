"""Stand-ins for Jira and Cursor, switched by a file.

    tools/agent/mock.json      {"jira": true, "cursor": true}

The workbench talks to two services nobody wants to meet for the first
time by pressing a button: a Jira that holds real tickets, and Cursor,
which costs money and writes code. With a switch ON the workbench
answers from this file instead: the same buttons, the same commands,
the same checks, the same result on the page -- and nothing leaves the
machine. Set the switch to `false` and the same button reaches the real
service. There is no other difference to configure.

    "jira":   true   every Jira request is answered here: the token
                     check, versions, searches, a bug's edit screen,
                     and a WRITE -- which is remembered in
                     target/agent/mock-jira.json and goes nowhere else.
                     No jira_config, host or token is needed or read.
    "cursor": true   every Cursor call is answered here: test design,
                     defect reasons, and the Agent loop -- which writes
                     one small sample test so that the checks, the
                     review and Discard can be tried. What a mock agent
                     wrote can never be pushed: Approve refuses it.

IT MUST NEVER PASS FOR THE REAL THING
* The page shows a banner on every tab while a switch is on.
* Every command says MOCK in its log, and every result file carries
  `"mock": true`.
* Sample data says it is sample data: summaries, reasons-why and the
  name the token check reports all say MOCK.
* The file must hold exactly the two switches, each exactly `true` or
  `false`. A misspelt name (`"cursur"`), a missing one, an extra one or
  any other value is an error, not a guess: a typo must not quietly
  select the real service. Only a file that is not there at all means
  everything is real.

The environment variable WORKBENCH_MOCK overrides the file for one
process: `0` turns every switch off (the tests do this), `1` turns
every switch on. Any other value is an error.

The sample data is invented: a project with three releases, some
stories, bugs and tests. It answers for whatever project key is typed,
so the page behaves as it would for yours.
"""
from __future__ import annotations

import io
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CONFIG = os.path.join(HERE, "mock.json")
SERVICES = ("jira", "cursor")
FIELD_ID = "customfield_10500"
FIELD_NAME = "Failure Reason"
REASONS = ["Code defect", "Test data", "Environment", "Requirement gap"]
VERSIONS = [("Release 1.0", True, "2026-01-15"), ("Release 1.1", True, "2026-03-02"),
            ("Release 2.0", False, "")]


class MockConfigError(RuntimeError):
    """mock.json says something that is neither true nor false."""


def switches(path: str = "") -> dict:
    """{"jira": bool, "cursor": bool}. See the module docstring."""
    env = os.environ.get("WORKBENCH_MOCK", "").strip()
    if env in ("0", "1"):
        return {s: env == "1" for s in SERVICES}
    if env:
        raise MockConfigError(f"WORKBENCH_MOCK is {env!r}; it must be 0 or 1 (or not set)")
    try:
        with io.open(path or CONFIG, encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {s: False for s in SERVICES}          # no file: everything is real
    except OSError as e:
        raise MockConfigError(f"tools/agent/mock.json could not be read "
                              f"({type(e).__name__})") from None
    except ValueError as e:
        raise MockConfigError(f"tools/agent/mock.json is not valid JSON ({e})") from None
    why = ("A mock that is on by accident, or off by accident, is the one mistake "
           "this file must not make.")
    if not isinstance(data, dict):
        raise MockConfigError(f"tools/agent/mock.json must be an object with the switches "
                              f"{', '.join(SERVICES)}. {why}")
    unknown = sorted(k for k in data if k not in SERVICES)
    if unknown:
        raise MockConfigError(f"tools/agent/mock.json has a switch this does not know: "
                              f"{', '.join(map(repr, unknown))}. The switches are "
                              f"{', '.join(SERVICES)}, in lower case. {why}")
    out = {}
    for s in SERVICES:
        if s not in data:
            raise MockConfigError(f"tools/agent/mock.json has no \"{s}\" switch. {why}")
        v = data[s]
        if v is not True and v is not False:
            raise MockConfigError(
                f"tools/agent/mock.json: \"{s}\" must be true or false, not {v!r}. {why}")
        out[s] = v
    return out


def on(service: str) -> bool:
    return switches().get(service, False)


# =============================== Jira ======================================

CONN = {"base": "https://mock-jira.invalid", "bases": ["https://mock-jira.invalid"],
        "token": "mock-token-not-a-credential", "timeout": 5, "api": "/rest/api/2",
        "cleartext": False, "mock": True}
_STORE = os.path.join("target", "agent", "mock-jira.json")


def _written(root: str) -> dict:
    try:
        with io.open(os.path.join(root, _STORE), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _remember(root: str, key: str, value) -> None:
    data = _written(root)
    data[key] = value
    path = os.path.join(root, _STORE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with io.open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)


def _issue(project: str, n: int, kind: str, summary: str, version: str, status: str = "Open",
           reason: str = "", description: str = "", component: str = "Booking") -> dict:
    return {"key": f"{project}-{n}", "id": str(10000 + n), "fields": {
        "summary": f"[MOCK] {summary}", "description": description,
        "status": {"name": status}, "issuetype": {"name": kind},
        "priority": {"name": "Medium"}, "components": [{"name": component}],
        "labels": ["mock", "api"], "fixVersions": [{"name": version}] if version else [],
        "resolution": None, "updated": "2026-01-01T00:00:00.000+0000",
        "comment": {"comments": []}, FIELD_ID: ({"value": reason} if reason else None)}}


def _issues(project: str) -> list:
    """The sample project, under whatever key was asked for."""
    p = project
    npe = ("NullPointerException in ReservationService. POST /reservations/create "
           "returned status 500 for a valid request.")
    return [
        _issue(p, 1, "Story", "As a booker I can create a reservation", "Release 1.0", "Done"),
        _issue(p, 2, "Story", "As a booker I can cancel a reservation", "Release 1.0", "Done"),
        _issue(p, 3, "Story", "As a booker I can see negotiated rates", "Release 1.1", "Done"),
        _issue(p, 4, "Story", "As an admin I can invite a member", "Release 1.1", "In Progress"),
        _issue(p, 5, "Story", "As a booker I can search availability by date", "Release 2.0"),
        _issue(p, 6, "Story", "As an admin I can change a member's role", "Release 2.0"),
        _issue(p, 101, "Bug", "Booking fails with 500 when creating a reservation", "Release 2.0",
               reason="Test data", description=npe),
        _issue(p, 102, "Bug", "Rate plan missing in shop response for a corporate account",
               "Release 2.0", component="Shop",
               description="The CSV row uses an account with no negotiated rate."),
        _issue(p, 103, "Bug", "Timeout on availability search in stage", "Release 2.0",
               reason="Environment", component="Shop",
               description="SocketTimeoutException, status 504 from the stage gateway."),
        _issue(p, 104, "Bug", "Currency shown as USD for a EUR property", "Release 1.1",
               reason="Code defect", component="Rates"),
        _issue(p, 150, "Bug", "Booking fails with 500 on create reservation", "Release 1.0",
               "Closed", reason="Code defect", description=npe),
        _issue(p, 151, "Bug", "Shop response has no rate plan for a corporate account",
               "Release 1.0", "Closed", reason="Test data", component="Shop",
               description="The account had no negotiated rate in the data."),
        _issue(p, 152, "Bug", "Availability search times out in stage", "Release 1.1", "Closed",
               reason="Environment", component="Shop",
               description="SocketTimeoutException status 504."),
        _issue(p, 201, "Test", "Create a reservation returns 201", "Release 1.0", "Done"),
        _issue(p, 202, "Test", "Cancel a reservation returns 204", "Release 1.0", "Done"),
        _issue(p, 203, "Test", "Negotiated rates are returned for a corporate account",
               "Release 1.1", "Done"),
    ]


def _with_writes(issue: dict, root: str) -> dict:
    written = _written(root)
    if issue["key"] in written:
        issue["fields"][FIELD_ID] = written[issue["key"]]
    return issue


_PROJECT_RX = re.compile(r"(?i)\bproject\s*(?:=|in\s*\()\s*\"?([A-Za-z][A-Za-z0-9_]+)")
_KEYS_RX = re.compile(r"(?i)\bissuekey\s+in\s*\(([^)]*)\)")
_TYPE_RX = re.compile(r"(?i)\bissuetype\s*=\s*\"?([A-Za-z ]+?)\"?(?=\s+(?:AND|OR|ORDER)\b|\s*$)")
_VERSION_RX = re.compile(r"(?i)\bfixVersion\s*=\s*\"((?:[^\"\\]|\\.)*)\"")


class Unsupported(Exception):
    """A query the mock Jira cannot answer. Said, not answered wrongly."""


_KNOWN = (r"(?i)\bproject\s*=\s*\"?[A-Za-z][A-Za-z0-9_]+\"?",
          r"(?i)\bproject\s+in\s*\([^)]*\)",
          r"(?i)\bissuekey\s+in\s*\([^)]*\)",
          r"(?i)\bissuetype\s*=\s*(?:\"[^\"]*\"|[A-Za-z]+)",
          r"(?i)\bfixVersion\s*=\s*\"(?:[^\"\\]|\\.)*\"",
          r"(?i)(?:cf\[[0-9]+\]|\"[^\"]+\")\s+is\s+not\s+EMPTY",
          r"(?i)\border\s+by\b.*$")


def _check_supported(jql: str) -> None:
    """Dropping a clause it does not understand would return MORE than was
    asked for and call it the answer. The mock refuses instead, as Jira
    refuses a query it cannot parse."""
    left = jql
    for rx in _KNOWN:
        left = re.sub(rx, " ", left)
    left = re.sub(r"(?i)\bAND\b|[()\s]", " ", left).strip()
    if left:
        raise Unsupported(
            "the MOCK Jira understands only: project = / in (...), issuekey in (...), "
            "issuetype = , fixVersion = \"...\" and <field> is not EMPTY, joined by AND. "
            f"It cannot answer: {' '.join(left.split())[:120]}. Set \"jira\": false in "
            "tools/agent/mock.json to ask the real Jira.")


def _search(jql: str, root: str) -> list:
    """Enough of JQL for what the workbench sends: a project, keys, an
    issue type, a fix version, and "has a reason"."""
    _check_supported(jql)
    keys = _KEYS_RX.search(jql)
    if keys:
        wanted = [k.strip().strip('"').upper() for k in keys.group(1).split(",") if k.strip()]
        out = []
        for k in wanted:
            project, _, n = k.partition("-")
            known = {i["key"]: i for i in _issues(project)}
            out.append(known.get(k) or _issue(project, int(n or 0), "Story",
                                              f"a story the sample data does not have ({k})", ""))
        return [_with_writes(i, root) for i in out]
    m = _PROJECT_RX.search(jql)
    found = _issues((m.group(1) if m else "DEMO").upper())
    t = _TYPE_RX.search(jql)
    if t:
        found = [i for i in found if i["fields"]["issuetype"]["name"].lower() == t.group(1).strip().lower()]
    v = _VERSION_RX.search(jql)
    if v:
        want = v.group(1).replace('\\"', '"').lower()
        found = [i for i in found if any(want == fv["name"].lower() for fv in i["fields"]["fixVersions"])]
    found = [_with_writes(i, root) for i in found]
    if re.search(r"(?i)is\s+not\s+EMPTY", jql):
        found = [i for i in found if i["fields"].get(FIELD_ID)]
    return found


def jira_transport(method: str, url: str, token: str, timeout: int, body=None, root: str = ""):
    """The mock Jira. Same call as the real transport; no network."""
    root = root or ROOT
    path = url.split("/rest/api/2", 1)[-1]
    if method == "GET" and path == "/myself":
        return {"name": "mock.user", "displayName": "MOCK USER (no Jira was contacted)"}
    if method == "GET" and path == "/field":
        return [{"id": "summary", "name": "Summary"}, {"id": FIELD_ID, "name": FIELD_NAME}]
    if method == "GET" and re.fullmatch(r"/project/[^/]+/versions", path):
        return [{"id": str(n), "name": name, "released": released, "archived": False,
                 **({"releaseDate": date} if date else {})}
                for n, (name, released, date) in enumerate(VERSIONS, 1)]
    if method == "POST" and path == "/search":
        found = _search(str((body or {}).get("jql") or ""), root)
        at, size = int(body.get("startAt") or 0), int(body.get("maxResults") or 0)
        return {"issues": found[at:at + size], "total": len(found), "startAt": at}
    m = re.fullmatch(r"/issue/([A-Za-z][A-Za-z0-9_]+-[0-9]+)(/editmeta|\?fields=.*)?", path)
    if m and method in ("GET", "PUT"):
        key = m.group(1).upper()
        if (m.group(2) or "").startswith("/editmeta"):
            return {"fields": {FIELD_ID: {"schema": {"type": "option"}, "name": FIELD_NAME,
                                          "allowedValues": [{"value": r} for r in REASONS]}}}
        if method == "PUT":
            _remember(root, key, ((body or {}).get("fields") or {}).get(FIELD_ID))
            return None
        project = key.split("-")[0]
        known = {i["key"]: i for i in _issues(project)}
        issue = _with_writes(known.get(key) or _issue(project, 0, "Bug", "unknown", ""), root)
        return {"key": key, "fields": {FIELD_ID: issue["fields"].get(FIELD_ID)}}
    raise ValueError(f"the mock Jira has no answer for {method} {path[:80]}")


def defects_settings() -> dict:
    """What jira_config.defects would hold, for the mock Jira. Writing is
    ON here: it changes a file under target/, and the point of the mock
    is that the whole flow can be tried."""
    return {"field": FIELD_NAME, "reasons": list(REASONS), "issue_type": "Bug",
            "write_back": True}


# ============================== Cursor ======================================

def _design_reply(prompt: str) -> str:
    listed = prompt.split("===== ENDPOINTS =====", 1)[-1].split("=====", 1)[0]
    endpoints = [l.split(" -- ")[0].strip() for l in listed.splitlines()
                 if re.match(r"^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+/", l.strip())]
    m = re.search(r"At most (\d+) test cases", prompt)
    limit = int(m.group(1)) if m else 10
    cases = []
    for e in endpoints:
        verb = e.split()[0]
        ok = {"POST": "201", "DELETE": "204"}.get(verb, "200")
        cases.append({"title": f"[MOCK] {e} succeeds for a valid request", "endpoint": e,
                      "type": "positive", "priority": "high", "preconditions": "",
                      "steps": [{"action": f"Send {e} with valid values",
                                 "data": "a valid request", "expected": f"status {ok}"}],
                      "requirement_refs": ["mock"]})
        cases.append({"title": f"[MOCK] {e} is refused without authorisation", "endpoint": e,
                      "type": "security", "priority": "medium", "preconditions": "",
                      "steps": [{"action": f"Send {e} with no token",
                                 "data": "no Authorization header", "expected": "status 401"}],
                      "requirement_refs": ["mock"]})
    for n, c in enumerate(cases[:limit], 1):
        c["id"] = f"TC-{n:03d}"
    return json.dumps({
        "service": "mock", "test_cases": cases[:limit],
        "summary": "MOCK DESIGN: these cases were produced by the workbench's stand-in "
                   "for Cursor, two per endpoint, to show the flow. They are not a design.",
        "open_questions": ["This is mock output. Set \"cursor\": false in "
                           "tools/agent/mock.json for a real design."]})


_HINTS = (("data", ("csv", "data", "account", "row", "fixture")),
          ("environment", ("timeout", "504", "503", "stage", "gateway", "environment")),
          ("code", ("exception", "nullpointer", "500", "stack")),
          ("requirement", ("requirement", "spec", "story says", "expected behaviour")))


def _defects_reply(prompt: str) -> str:
    head, _, body = prompt.partition("===== DEFECTS =====")
    reasons = [l[2:].strip() for l in head.splitlines() if l.startswith("- ")]
    out = []
    parts = re.split(r"(?m)^--- ([A-Z][A-Z0-9_]+-[0-9]+) ---$", body)
    for key, text in zip(parts[1::2], parts[2::2]):
        # The ticket's own lines only: the similar defects listed under it
        # name their reasons, and "Test data" is not a hint about this one.
        low = text.split("Similar earlier defects", 1)[0].lower()
        pick, why = "NONE", "MOCK: nothing in the ticket matched a keyword."
        for word, hints in _HINTS:
            hit = next((h for h in hints if h in low), "")
            reason = next((r for r in reasons if word in r.lower()), "")
            if hit and reason:
                pick, why = reason, f"MOCK: the ticket mentions \"{hit}\" (keyword match, not a judgement)."
                break
        out.append({"key": key, "reason": pick, "confidence": "medium" if pick != "NONE" else "low",
                    "why": why, "evidence": "mock"})
    return json.dumps({"defects": out})


def cursor(prompt: str, followup=None) -> dict:
    """The mock Cursor for the steps that ask a question and take JSON back."""
    if "===== DEFECTS =====" in prompt:
        return {"result": _defects_reply(prompt), "model": "mock", "status": "finished"}
    return {"result": _design_reply(prompt), "model": "mock", "status": "finished"}


SAMPLE_TEST = "src/test/java/com/hi/api/tests/jira/MockAgentSampleTest.java"
SAMPLE_BODY = """package com.hi.api.tests.jira;

/**
 * MOCK. Written by the workbench's stand-in for Cursor (tools/agent/mock.py)
 * so that the checks, the review and Discard can be tried without Cursor.
 * It tests nothing and is never pushed: Approve refuses a mock run.
 * Discard removes it.
 */
public class MockAgentSampleTest {

    @org.testng.annotations.Test(enabled = false, description = "mock agent sample")
    public void mockAgentWroteThis() {
        // nothing: see the class comment
    }
}
"""


def loop_agent(root: str):
    """The mock Cursor for the Agent loop: it writes ONE file, inside the
    paths a new test may be written to, and says so."""
    def agent(prompt: str) -> dict:
        path = os.path.join(root, SAMPLE_TEST.replace("/", os.sep))
        try:
            with io.open(path, encoding="utf-8") as fh:
                there = fh.read()
        except OSError:
            there = None
        if there is not None:
            # Left by an earlier mock run that was never discarded, or a
            # file of the same name that is somebody's own. Either way it
            # is not this run's to write over, or to be discarded with it.
            whose = ("an earlier mock run left it; delete it"
                     if there == SAMPLE_BODY else "it is not the mock's file")
            return {"result": f"MOCK AGENT: {SAMPLE_TEST} is already there ({whose}). "
                              f"Nothing was written. Cursor was not called.",
                    "status": "finished", "id": "mock", "model": "mock"}
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(SAMPLE_BODY)
        return {"result": f"MOCK AGENT: created {SAMPLE_TEST} (a placeholder that tests "
                          f"nothing). Cursor was not called. This run cannot be approved; "
                          f"use Discard.",
                "status": "finished", "id": "mock", "model": "mock"}
    return agent
