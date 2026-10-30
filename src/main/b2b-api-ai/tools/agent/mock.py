"""Stand-ins for Jira and Cursor, switched by a file.

    tools/agent/mock.json      {"jira": false, "cursor": false}   (off as committed)

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
                     .workbench/mock-jira.json and goes nowhere else.
                     No jira_config, host or token is needed or read.
    "cursor": true   every Cursor call is answered here: test design,
                     defect reasons, and the Agentic Test Creation tab -- which writes
                     one small sample test so that the checks, the
                     review and Discard can be tried. What a mock agent
                     wrote can never be pushed: Approve refuses it.

IT MUST NEVER PASS FOR THE REAL THING
* The page shows a banner on every tab while a switch is on.
* Every command says MOCK in its log, and every result file carries
  `"mock": true`.
* Sample data says it is sample data: summaries, reasons-why and the
  name the token check reports all say MOCK.
* (A third switch, `"confluence"`, MAY be added. Left out, Confluence
  follows `jira`.)
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


# A switch that MAY be in the file. Left out, Confluence follows Jira:
# the two are one vendor's products and are normally real or not together.
OPTIONAL = ("confluence",)


def switches(path: str = "", with_optional: bool = False) -> dict:
    """{"jira": bool, "cursor": bool}. See the module docstring."""
    env = os.environ.get("WORKBENCH_MOCK", "").strip()
    if env in ("0", "1"):
        return {s: env == "1" for s in SERVICES + (OPTIONAL if with_optional else ())}
    if env:
        raise MockConfigError(f"WORKBENCH_MOCK is {env!r}; it must be 0 or 1 (or not set)")
    try:
        with io.open(path or CONFIG, encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except FileNotFoundError:                        # no file: everything is real
        return {s: False for s in SERVICES + (OPTIONAL if with_optional else ())}
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
    unknown = sorted(k for k in data if k not in SERVICES + OPTIONAL)
    if unknown:
        raise MockConfigError(f"tools/agent/mock.json has a switch this does not know: "
                              f"{', '.join(map(repr, unknown))}. The switches are "
                              f"{', '.join(SERVICES)} (and, optionally, "
                              f"{', '.join(OPTIONAL)}), in lower case. {why}")
    out = {}
    for s in SERVICES:
        if s not in data:
            raise MockConfigError(f"tools/agent/mock.json has no \"{s}\" switch. {why}")
        v = data[s]
        if v is not True and v is not False:
            raise MockConfigError(
                f"tools/agent/mock.json: \"{s}\" must be true or false, not {v!r}. {why}")
        out[s] = v
    for s in OPTIONAL:
        v = data.get(s, out["jira"])
        if v is not True and v is not False:
            raise MockConfigError(
                f"tools/agent/mock.json: \"{s}\" must be true or false, not {v!r}. {why}")
        if with_optional:
            out[s] = v
    return out


def on(service: str) -> bool:
    return switches(with_optional=True).get(service, False)


# =============================== Jira ======================================

CONN = {"base": "https://mock-jira.invalid", "bases": ["https://mock-jira.invalid"],
        "token": "mock-token-not-a-credential", "timeout": 5, "api": "/rest/api/2",
        "cleartext": False, "mock": True}
_STORE = os.path.join(".workbench", "mock-jira.json")


def _written(root: str) -> dict:
    try:
        with io.open(os.path.join(root, _STORE), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def jira_attach(root: str, key: str, filename: str, size: int) -> None:
    """The mock Jira's "attach": the name is written down, nothing is sent."""
    have = _written(root).get("__attachments__")
    have = have if isinstance(have, dict) else {}
    have.setdefault(key, []).append({"filename": filename, "size": size})
    _remember(root, "__attachments__", have)


CONFLUENCE_SPACES = ("QA",)
_CONF_STORE = os.path.join(".workbench", "mock-confluence.json")


def _pages(root: str) -> list:
    try:
        with io.open(os.path.join(root, _CONF_STORE), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def confluence(method: str, url: str, body=None, root: str = "") -> tuple:
    """The mock Confluence, for publishing: (status, answer). Every parent
    page exists and is in space QA; a created page is a line in a file."""
    root = root or ROOT
    path, _, query = url.split("/rest/api/content", 1)[-1].partition("?")
    pages = _pages(root)
    if method == "GET" and re.fullmatch(r"/[0-9]+", path):
        return 200, {"id": path[1:], "title": "[MOCK] parent page",
                     "space": {"key": CONFLUENCE_SPACES[0]}}
    if method == "GET" and path == "":
        from urllib.parse import parse_qs
        q = {k: v[0] for k, v in parse_qs(query).items()}
        return 200, {"results": [p for p in pages if p["space"] == q.get("spaceKey")
                                 and p["title"] == q.get("title")]}
    if method == "POST" and path == "":
        page = {"id": str(90000 + len(pages) + 1), "title": str((body or {}).get("title")),
                "space": str(((body or {}).get("space") or {}).get("key")),
                "parent": str((((body or {}).get("ancestors") or [{}])[0]).get("id")),
                "chars": len(str((((body or {}).get("body") or {}).get("storage") or {})
                                 .get("value") or ""))}
        pages.append(page)
        dest = os.path.join(root, _CONF_STORE)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        tmp = f"{dest}.{os.getpid()}.tmp"
        with io.open(tmp, "w", encoding="utf-8") as fh:
            json.dump(pages, fh)
        os.replace(tmp, dest)
        return 200, {"id": page["id"], "_links": {"webui": f"/pages/viewpage.action?pageId={page['id']}"}}
    raise ValueError(f"the mock Confluence has no answer for {method} {path[:60]}")


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
        # statusCategory, as Jira sends it: the Defects per release chart
        # counts open/closed by it, never by the status's own name.
        "status": {"name": status, "statusCategory": {
            "key": "done" if status in ("Done", "Closed", "Resolved") else
                   ("indeterminate" if status == "In Progress" else "new")}},
        "issuetype": {"name": kind},
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


STORY_TEXT = """As a member, I can add a business to my profile, so that my stays count for it.

Acceptance Criteria
- Adding a business with a name returns 201 and the new business id
- Adding the same name a second time returns 409
- A name longer than 80 characters returns 400

Example request:

curl -X POST "https://api.example.test/guests/123/businesses" -H "Content-Type: application/json" -d '{"businessName": "Acme"}'

Expect 201.
"""


def story_transport(url: str) -> dict:
    """The mock Jira, asked for ONE story the way tools/jira/fetch.py asks:
    the whole issue, then its comment pages. Any key gets the same story."""
    if re.search(r"/issue/[^/?]+/comment\b", url):
        return {"comments": [], "total": 0, "startAt": 0, "maxResults": 100}
    m = re.search(r"/issue/([A-Za-z][A-Za-z0-9_]+-[0-9]+)", url)
    key = (m.group(1) if m else "MOCK-1").upper()
    project, number = key.split("-")[0], int(key.split("-")[1])
    # The stories around it: a parent epic, one it is blocked by, one it
    # relates to. Asked for by key, each is a story of its own.
    near = {9000: ("Epic", "Businesses on a member's profile",
                   "Members can keep the businesses they travel for on their profile, "
                   "so that stays are attributed without a call to the service desk."),
            9001: ("Story", "Business names are unique per member",
                   "Two businesses of one member cannot share a name. The API answers 409 "
                   "and names the existing business."),
            9002: ("Story", "Show a member's businesses on the profile page",
                   "The profile page lists the member's businesses, newest first.")}
    if number in near:
        kind, summary, text = near[number]
        issue = _issue(project, number, kind, summary, "", description=text)
        issue["fields"]["comment"] = {"comments": [], "total": 0}
        return issue
    issue = _issue(project, number, "Story",
                   "Add a business to a member's profile", "", description=STORY_TEXT)
    issue["key"] = key
    issue["fields"]["comment"] = {"comments": [], "total": 0}
    issue["fields"]["parent"] = {"key": f"{project}-9000"}
    issue["fields"]["issuelinks"] = [
        {"type": {"name": "Blocks", "inward": "is blocked by", "outward": "blocks"},
         "inwardIssue": {"key": f"{project}-9001"}},
        {"type": {"name": "Relates", "inward": "relates to", "outward": "relates to"},
         "outwardIssue": {"key": f"{project}-9002"}}]
    return issue


def jira_transport(method: str, url: str, token: str, timeout: int, body=None, root: str = ""):
    """The mock Jira. Same call as the real transport; no network."""
    root = root or ROOT
    if method == "GET" and re.search(r"/rest/raven/2\.0/api/test/[A-Za-z][A-Za-z0-9_]+-[0-9]+/steps$", url):
        # What Xray keeps for a test. The same three steps for every one.
        return [{"index": n, "step": {"raw": step}, "data": {"raw": data},
                 "result": {"raw": seen}} for n, (step, data, seen) in enumerate((
                     ("[MOCK] Open the booking page", "", "The page opens"),
                     ("[MOCK] Enter the dates and search", "2 nights", "Rooms are listed"),
                     ("[MOCK] Choose a room and confirm", "", "A confirmation number is shown")), 1)]
    if method in ("PUT", "POST") and re.search(r"/rest/raven/[0-9.]+/api/test/[A-Za-z][A-Za-z0-9_]+-[0-9]+/steps?$", url):
        return {"id": 1, "index": 1}                     # a step was "added"
    path = url.split("/rest/api/2", 1)[-1]
    if method == "POST" and path == "/issue":
        # An issue is created: the next free number in the project, kept
        # with the sample data so that it is then found when asked for.
        fields = (body or {}).get("fields") if isinstance(body, dict) else {}
        fields = fields if isinstance(fields, dict) else {}
        project = str(((fields.get("project") or {}) if isinstance(fields.get("project"), dict)
                       else {}).get("key") or "MOCK").upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,19}", project):
            raise Unsupported("the project key is not a key")
        made = _written(root).get("__created__")
        made = made if isinstance(made, dict) else {}
        number = 9000 + sum(1 for k in made if k.startswith(project + "-")) + 1
        key = f"{project}-{number}"
        kind = ((fields.get("issuetype") or {}) if isinstance(fields.get("issuetype"), dict)
                else {}).get("name") or "Test"
        made[key] = {"summary": str(fields.get("summary") or "")[:240], "type": str(kind)[:40],
                     "description": str(fields.get("description") or "")[:4000]}
        _remember(root, "__created__", made)
        return {"id": str(number), "key": key, "self": f"{CONN['base']}/rest/api/2/issue/{key}"}
    if method == "POST" and path == "/issueLink":
        links = _written(root).get("__links__")
        links = links if isinstance(links, list) else []
        links.append(body if isinstance(body, dict) else {})
        _remember(root, "__links__", links)
        return None
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
        made = _written(root).get("__created__")
        if isinstance(made, dict) and key in made and method == "GET":
            # One this mock created: a Test, in the story's project.
            row = made[key]
            return {"key": key, "fields": {"summary": row.get("summary"),
                                           "issuetype": {"name": row.get("type") or "Test"},
                                           "project": {"key": project},
                                           "status": {"name": "Open"}}}
        if method == "GET" and "issuelinks" in (m.group(2) or ""):
            # A story with two Xray tests linked to it, and one other link.
            # Their steps are the mock's three steps (the raven answer above).
            tested = {"name": "Tests", "inward": "is tested by", "outward": "tests"}
            return {"key": key, "fields": {"summary": f"[MOCK] story {key}",
                                           "project": {"key": project},
                                           "issuetype": {"name": "Story"}, "issuelinks": [
                {"type": tested, "inwardIssue": {"key": f"{project}-201", "fields": {
                    "summary": "[MOCK] A booking can be made and confirmed",
                    "issuetype": {"name": "Test"}, "status": {"name": "Open"}}}},
                {"type": tested, "inwardIssue": {"key": f"{project}-202", "fields": {
                    "summary": "[MOCK] A booking for past dates is refused",
                    "issuetype": {"name": "Test"}, "status": {"name": "Open"}}}},
                {"type": {"name": "Blocks", "inward": "is blocked by", "outward": "blocks"},
                 "inwardIssue": {"key": f"{project}-5", "fields": {"summary": "[MOCK] another story",
                                                                   "issuetype": {"name": "Story"}}}}]}}
        if method == "GET" and "project" in (m.group(2) or ""):
            return {"key": key, "fields": {"summary": f"[MOCK] story {key}",
                                           "project": {"key": project},
                                           "issuetype": {"name": "Story"}}}
        issue = _with_writes(known.get(key) or _issue(project, 0, "Bug", "unknown", ""), root)
        if "attachment" in (m.group(2) or ""):
            had = _written(root).get("__attachments__")
            return {"key": key, "fields": {
                "summary": issue["fields"].get("summary"),
                "attachment": list((had if isinstance(had, dict) else {}).get(key) or [])}}
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
    if not cases:
        # Designed from a story alone: there is no endpoint to hang a case
        # on, and a real designer would read one out of the story.
        cases.append({"title": "[MOCK] the story's main path succeeds",
                      "endpoint": "POST /mock/from-the-story", "type": "positive",
                      "priority": "high", "preconditions": "",
                      "steps": [{"action": "Do what the story describes, with valid values",
                                 "data": "a valid request", "expected": "status 201"}],
                      "requirement_refs": ["mock"]})
    for n, c in enumerate(cases[:limit], 1):
        c["id"] = f"TC-{n:03d}"
    return json.dumps({
        "service": "mock", "test_cases": cases[:limit],
        "summary": "MOCK DESIGN: these cases were produced by the workbench's stand-in "
                   "for AI Agent, two per endpoint, to show the flow. They are not a design.",
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


_REVIEW_FILE_RX = re.compile(
    r"^----- file \d+ of \d+ \[[0-9a-f]+\]: (.+?) -----\n(.*?)^----- end of file \d+ ",
    re.M | re.S)
_REVIEW_WORDS = (
    ("enabled = false", "low", "assertion", "the test is switched off",
     "It is compiled and never run, so it can never fail.", "Enable it, or remove it."),
    ("Thread.sleep", "medium", "bug", "a fixed sleep instead of waiting for the result",
     "The test is slow when the service is fast and fails when it is slow.",
     "Poll for the state the test needs."),
    ("printStackTrace", "medium", "error handling", "a failure is printed and then ignored",
     "The test carries on and can pass after something went wrong.",
     "Let the exception fail the test."),
    ("TODO", "low", "maintainability", "unfinished work left in the change",
     "Somebody has to find out later what was meant.", "Finish it or take it out."),
)


def _review_reply(prompt: str) -> str:
    """A keyword match on the added lines of each file. Not a review."""
    files, findings = [], []
    for path, body in _REVIEW_FILE_RX.findall(prompt):
        hit = False
        for line in body.splitlines():
            if not line.startswith("+") or line.startswith("+++"):
                continue
            for word, sev, cat, title, why, fix in _REVIEW_WORDS:
                if word in line:
                    hit = True
                    findings.append({"file": path, "evidence": line[1:].strip(),
                                     "severity": sev, "category": cat,
                                     "title": f"[MOCK] {title}", "why": why, "fix": fix})
                    break
        files.append({"file": path, "verdict": "findings" if hit else "ok",
                      "note": "MOCK: keyword match only"})
    return json.dumps({"files": files, "findings": findings})


def _manual_reply(prompt: str) -> str:
    """One made-up case per document sent, and an update to the first
    existing test listed. Not test design."""
    docs = re.findall(r"^----- document \d+ \[[0-9a-f]+\]: (.+?) -----$", prompt, re.M)
    listed = prompt.split("----- existing tests [", 1)[-1].split("-----", 2)
    keys = re.findall(r"^([A-Z][A-Z0-9_]+-[0-9]+) \| ", listed[1] if len(listed) > 1 else "",
                      re.M)
    cases = [{"title": f"[MOCK] A rule from {name} is applied", "module": "Booking",
              "category": "functional positive", "priority": "P2",
              "preconditions": "A signed-in user",
              "steps": [{"action": "Open the screen the rule is about", "data": "",
                         "expected": "The screen opens"},
                        {"action": "Do what the rule describes", "data": "sample values",
                         "expected": "The result the rule states is shown"}],
              "source": name, "coverage_note": "MOCK: no comparison was made"}
             for name in docs] or [
        {"title": "[MOCK] The noted behaviour works", "module": "", "category": "functional",
         "priority": "P3", "preconditions": "",
         "steps": [{"action": "Follow the notes", "data": "", "expected": "It behaves as noted"}],
         "source": "notes", "coverage_note": "MOCK"}]
    updates = [{"key": keys[0], "reason": "[MOCK] a rule in the documents changed",
                "changes": "MOCK: change the expected result of the last step",
                "source": docs[0] if docs else "notes", "priority": "P2"}] if keys else []
    return json.dumps({"new_cases": cases, "updates": updates})


def _devcode_reply(prompt: str) -> str:
    """One gap per file the diff touches that no case line names. Not a review."""
    diff = prompt.split("===== DEVELOPER CHANGE =====", 1)[-1].split("===== THE CASES =====", 1)
    cases = (diff[1] if len(diff) > 1 else "").lower()
    files = re.findall(r"^\+\+\+ b/(.+)$", diff[0], re.M)
    gaps = []
    for path in dict.fromkeys(files):
        stem = os.path.splitext(os.path.basename(path))[0]
        if stem.lower() in cases or "/test/" in path.replace("\\", "/"):
            continue
        gaps.append({"title": f"[MOCK] the change to {stem} has no case",
                     "why": "MOCK: the file name is not in any case (name match, not a judgement)",
                     "evidence": path, "endpoint": "", "severity": "low",
                     "suggested_case": {"title": f"[MOCK] {stem} behaves as the change says",
                                        "type": "positive",
                                        "steps": [{"action": "Call the endpoint the change is in",
                                                   "data": "", "expected": "status 200"}]}})
    return json.dumps({"gaps": gaps[:5], "summary": "MOCK: a file-name match, not a coverage review."})


def _suites_reply(prompt: str) -> str:
    """smoke for the first high-priority positive case of each endpoint,
    regression for every case. A rule, not a judgement."""
    body = prompt.split("===== CASES =====", 1)[-1]
    out, smoke_for = [], set()
    for line in body.splitlines():
        m = re.match(r"^(TC-[0-9]{3,4}) \[([a-z]*)/([a-z]*)\] (\S+ \S+)", line.strip())
        if not m:
            continue
        cid, kind, prio, endpoint = m.groups()
        groups = ["regression"]
        if kind == "positive" and prio == "high" and endpoint not in smoke_for:
            smoke_for.add(endpoint)
            groups.insert(0, "smoke")
        out.append({"id": cid, "groups": groups, "confidence": "medium",
                    "why": "MOCK: " + ("the first high-priority positive case of its endpoint"
                                       if "smoke" in groups else "every case runs in regression")})
    return json.dumps({"suggestions": out})


def cursor(prompt: str, followup=None) -> dict:
    """The mock Cursor for the steps that ask a question and take JSON back."""
    if "===== DEVELOPER CHANGE =====" in prompt:
        return {"result": _devcode_reply(prompt), "model": "mock", "status": "finished"}
    if "===== SUITE ASSIGNMENT =====" in prompt:
        return {"result": _suites_reply(prompt), "model": "mock", "status": "finished"}
    if "===== MANUAL TEST DESIGN =====" in prompt:
        return {"result": _manual_reply(prompt), "model": "mock", "status": "finished"}
    if "===== CHANGE UNDER REVIEW =====" in prompt and _REVIEW_FILE_RX.search(prompt):
        return {"result": _review_reply(prompt), "model": "mock", "status": "finished"}
    if "===== DEFECTS =====" in prompt:
        return {"result": _defects_reply(prompt), "model": "mock", "status": "finished"}
    return {"result": _design_reply(prompt), "model": "mock", "status": "finished"}


SAMPLE_TEST = "src/test/java/com/hi/api/tests/jira/MockAgentSampleTest.java"
SAMPLE_BODY = """package com.hi.api.tests.jira;

import com.hi.api.tests.StoryTestBase;

/**
 * MOCK. Written by the workbench's stand-in for AI Agent (tools/agent/mock.py)
 * so that the checks, the review and Discard can be tried without AI Agent.
 * It tests nothing and is never pushed: Approve refuses a mock run.
 * Discard removes it.
 *
 * DATA_INVENTORY: no HTTP steps, no CONFIG keys, no EXTRACTs.
 */
public class MockAgentSampleTest extends StoryTestBase {

    @org.testng.annotations.Test(groups = {"jira"}, enabled = false, description = "mock agent sample")
    public void mockAgentWroteThis() {
        // nothing: see the class comment
    }
}
"""


def loop_agent(root: str):
    """The mock Cursor for the Agentic Test Creation tab: it writes ONE file, inside the
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
                              f"Nothing was written. AI Agent was not called.",
                    "status": "finished", "id": "mock", "model": "mock"}
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(SAMPLE_BODY)
        return {"result": f"MOCK AGENT: created {SAMPLE_TEST} (a placeholder that tests "
                          f"nothing). AI Agent was not called. This run cannot be approved; "
                          f"use Discard.",
                "status": "finished", "id": "mock", "model": "mock"}
    return agent
