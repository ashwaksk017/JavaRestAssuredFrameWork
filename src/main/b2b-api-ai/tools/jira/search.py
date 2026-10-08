"""Ask Jira questions that are about more than one story.

    python tools/jira/search.py verify
    python tools/jira/search.py versions    --project ABC
    python tools/jira/search.py search      --jql "project = ABC AND labels = api"
    python tools/jira/search.py fix-version --project ABC --version 6.02 [--compare 6.01]
    python tools/jira/search.py tests       --project ABC
    python tools/jira/search.py paste "<a key, keys, a query, or a Jira address>"

Read-only. Nothing here writes to Jira.

`fetch.py` reads ONE story and its parents. This reads lists: is the
token good, which releases does a project have, what is in a release and
what changed since the last one, which tests already exist.

THE SAME RULES AS fetch.py
--------------------------
The host comes from `jira_config.base_urls` in the gitignored
program_configuration.json and from nowhere else. There is no option to
name another host: every request here carries the token, and a query is
pasted text. The token is a header, never a URL, never a log line.

WHAT A LIST FROM JIRA CAN AND CANNOT BE TRUSTED FOR
---------------------------------------------------
Jira search pages by offset. An issue that is edited while the pages are
being read can move, so it comes back twice and another one is never
seen. Three things are done about that, and the third is the one that
matters:

* a query with no ORDER BY gets `ORDER BY key ASC`, which does not move
  when an issue is edited;
* an issue seen twice is kept once;
* the result says so. `complete` is true only when the number of
  distinct issues read equals what Jira said the query holds (or the
  limit asked for), and that number did not change between pages. When
  it is false the reason is printed, and the caller is told the list is
  short -- not handed a shorter list that looks whole.

The command exits 1 for a list that is incomplete, cut at `--max`, or a
comparison that cannot be relied on. A script that checks only the exit
code is told the same thing a person reading the output is.

A redirect is not followed to another host, and a redirected POST is
not followed at all (safehttp.py): either would take the token, or the
query, somewhere the configuration never named.

The query is sent as a POST body. A long JQL in a query string is cut
off by proxies at a length that depends on the proxy.

A project's versions are ordered by the NUMBER in their names, newest
first. `releaseDate` is optional in Jira and many projects never set it;
ordering by it puts every undated release in one undefined heap. This
is a reading of names, with limits: a name with no dotted number
(`Release 7`, `Sprint June30`) sorts below every name that has one.

The search endpoint is `<api>/search`, the one Server and Data Centre
have. Jira Cloud replaced it; against Cloud this reports the refusal
instead of returning nothing.

The idea for the version ordering, the POST search and the duplicate
check came from a sibling tool (a Jira defect-triage app). Its code is
not copied: it sent the token to whatever host it was given.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from urllib.parse import quote

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import fetch  # noqa: E402
import projectconfig  # noqa: E402
import query as pasted  # noqa: E402
import safehttp  # noqa: E402

ROOT = projectconfig.ROOT
DEFAULT_API_PATH = fetch.DEFAULT_API_PATH
PAGE_SIZE = 100               # Jira caps a search response at this
DEFAULT_MAX = 500
HARD_MAX = 5000               # a mistyped query must not walk a whole instance
LIST_FIELDS = ["summary", "status", "issuetype", "fixVersions", "labels",
               "updated", "priority"]

_PROJECT_RX = re.compile(r"^[A-Z][A-Z0-9_]{1,19}$")
_BASE_RX = re.compile(r"^(https?)://([A-Za-z0-9.-]+|\[[0-9A-Fa-f:]+\])(:\d+)?(/[A-Za-z0-9/_.~-]*)?$")
_TOKEN_RX = re.compile(r"^[\x21-\x7e]+$")
_YEAR_FIRST_RX = re.compile(r"^(?:19|20)\d\d\.")
_DOTTED_RX = re.compile(r"\d+(?:\.\d+)+")
_DIGITS_RX = re.compile(r"\d+")
_QUOTED_RX = re.compile(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'')
_ORDER_BY_RX = re.compile(r"\border\s+by\b", re.I)


def say(msg: str) -> None:
    print(msg, flush=True)


class JiraError(RuntimeError):
    """Jira answered, and the answer was not the data asked for.

    status: the HTTP status, or 0 when Jira could not be reached at all.
    """

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class Refused(RuntimeError):
    """This tool will not make the request (configuration, not Jira)."""


# ---- the connection ----------------------------------------------------

def connect(cfg: dict = None, note: str = "") -> dict:
    """{base, token, timeout, api} from jira_config, or Refused.

    The base is the FIRST approved base url. There is deliberately no
    way to pass another.
    """
    if cfg is None:
        cfg, note = projectconfig.section("jira_config")
    if not cfg:
        raise Refused(f"no jira_config ({note}). Add it to "
                      f"program_configuration.json; the shape is in "
                      f"program_configuration.example.json.")
    bases = [str(b).strip() for b in (cfg.get("base_urls") or []) if str(b).strip()]
    if not bases:
        raise Refused("jira_config.base_urls is empty. Add the Jira base URL "
                      "there: it is the only host the token is ever sent to.")
    base = bases[0].rstrip("/")
    m = _BASE_RX.match(base)
    if not m:
        # No user:password@, no ?query: the first would be printed, and
        # either makes every URL built from this one wrong.
        raise Refused("jira_config.base_urls[0] is not a plain "
                      "http(s)://host[:port][/path] address.")
    token = str(cfg.get("pat") or "").strip()
    if not token:
        raise Refused("jira_config.pat is empty. The token belongs in "
                      "program_configuration.json and nowhere else.")
    if not _TOKEN_RX.match(token):
        # A line break or a space in a header value is refused by the
        # HTTP library with an error that QUOTES the value.
        raise Refused("jira_config.pat contains a space, a line break or a "
                      "character that cannot be sent in a header. Paste the "
                      "token again.")
    try:
        timeout = int(str(cfg.get("timeout_seconds") or "").strip() or 30)
    except ValueError:
        timeout = 30
    if not 1 <= timeout <= 600:
        timeout = 30
    api = str(cfg.get("api_path") or DEFAULT_API_PATH).strip() or DEFAULT_API_PATH
    if not re.match(r"^/[A-Za-z0-9/_.-]+$", api) or ".." in api.split("/"):
        raise Refused("jira_config.api_path is not a path like /rest/api/2.")
    return {"base": base, "bases": bases, "token": token, "timeout": timeout,
            "api": api.rstrip("/"), "cleartext": m.group(1) == "http"}

def _urllib_transport(method: str, url: str, token: str, timeout: int, body=None):
    """One request; parsed JSON back. Raises JiraError."""
    import urllib.error
    import urllib.request
    data = None
    req = urllib.request.Request(url, method=method)
    req.add_header("Accept", "application/json")
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    # Header, never the URL: a URL reaches proxies, logs and history.
    req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.data = data
    try:
        with safehttp.open(req, timeout) as r:
            raw, status = r.read(), r.status
    except safehttp.Redirected as e:
        raise JiraError(0, f"Jira {e}. Put the address Jira actually "
                           f"answers on in jira_config.base_urls.") from None
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")
        except Exception:                                # noqa: BLE001
            pass
        raise JiraError(e.code, _error_text(e.code, e.reason, detail)) from None
    except urllib.error.URLError as e:
        raise JiraError(0, f"could not reach Jira: {e.reason}") from None
    except (TimeoutError, OSError) as e:
        raise JiraError(0, f"could not reach Jira: {e}") from None
    except Exception as e:                               # noqa: BLE001
        # The TYPE only. http.client puts the header it rejected into its
        # message, and the header is the token.
        raise JiraError(0, f"the request to Jira failed ({type(e).__name__})") from None
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        # A single-sign-on page answers 200 with HTML. That is a refused
        # token, and reading it as "no results" would be the worst reading.
        raise JiraError(status, "Jira answered with a page that is not JSON "
                                "(usually a sign-in page): the token was not "
                                "accepted") from None


def _error_text(status: int, reason: str, body: str) -> str:
    said = []
    try:
        doc = json.loads(body)
    except ValueError:
        doc = None
    if isinstance(doc, dict):
        for part in (doc.get("errorMessages"), doc.get("errors"), doc.get("message")):
            if isinstance(part, str):
                said.append(part)
            elif isinstance(part, dict):
                said += [str(v) for v in part.values()]
            elif isinstance(part, list):
                said += [str(v) for v in part]
    said = "; ".join(s for s in said if s.strip())[:400]
    hint = {401: "the token was rejected: expired, revoked or mistyped",
            403: "the token is valid but may not do this (permission, or a "
                 "CAPTCHA lock after failed sign-ins)",
            404: "not found -- or hidden from this token, which Jira reports "
                 "the same way",
            410: "this endpoint has been removed on this Jira (Cloud replaced "
                 "`search`)",
            429: "Jira is rate-limiting; wait and run again"}.get(status, "")
    parts = [f"Jira returned {status} {reason}".strip()]
    if hint:
        parts.append(hint)
    if said:
        parts.append(f"Jira said: {said}")
    return ". ".join(parts)


def _call(conn: dict, method: str, path: str, body=None, transport=None):
    fn = transport or _urllib_transport
    return fn(method, f"{conn['base']}{conn['api']}{path}", conn["token"],
              conn["timeout"], body)


# ---- credential check ---------------------------------------------------

def verify(conn: dict, transport=None) -> tuple:
    """(ok, message). Asks Jira who the token belongs to."""
    try:
        me = _call(conn, "GET", "/myself", transport=transport)
    except JiraError as e:
        return False, str(e)
    if not isinstance(me, dict) or not (me.get("name") or me.get("accountId")
                                        or me.get("displayName") or me.get("key")):
        return False, ("Jira answered, but not with a user: the token was "
                       "probably treated as anonymous")
    who = str(me.get("displayName") or me.get("name") or "").strip()
    return True, "the token is accepted" + (f", as {who}" if who else "")


# ---- versions -----------------------------------------------------------

def version_sort_key(name: str) -> tuple:
    """`Release 6.02` -> (1, (6, 2)).

    Numbers, not text: 4.10 is after 4.9, and `6.02.02.03` is after
    `6.02`. A dotted number outranks a name without one, so a date-style
    name such as `Sprint June30` is not read as version 30 and put above
    `6.19`.
    """
    groups = _DOTTED_RX.findall(name or "")
    # `2024.06.30 Release 6.1` has two; the one that starts with a year
    # is the date, when there is another to choose.
    versions = [g for g in groups if not _YEAR_FIRST_RX.match(g)] or groups
    if versions:
        return 1, tuple(int(n) for n in versions[0].split(".")[:6])
    return 0, tuple(int(n) for n in _DIGITS_RX.findall(name or "")[:6])


def project_key(value: str) -> str:
    key = (value or "").strip().upper()
    if not _PROJECT_RX.match(key):
        raise Refused(f"{value!r} is not a project key like ABC. It becomes "
                      f"part of a URL and of a query.")
    return key


def list_versions(conn: dict, project: str, include_archived: bool = False,
                  transport=None) -> list:
    """A project's versions, newest first by the number in the name."""
    key = project_key(project)
    data = _call(conn, "GET", f"/project/{quote(key)}/versions", transport=transport)
    if not isinstance(data, list):
        raise JiraError(200, "Jira did not answer with a list of versions")
    out = []
    for v in data:
        if not isinstance(v, dict):
            continue
        name = str(v.get("name") or "").strip()
        if not name or (v.get("archived") and not include_archived):
            continue
        out.append({"name": name, "id": str(v.get("id") or ""),
                    "released": bool(v.get("released")),
                    "archived": bool(v.get("archived")),
                    "release_date": str(v.get("releaseDate") or "")})
    out.sort(key=lambda v: (version_sort_key(v["name"]), v["release_date"], v["name"]),
             reverse=True)
    return out


# ---- search --------------------------------------------------------------

def jql_quote(value: str) -> str:
    """A value as a JQL string literal. Text from a form goes through here."""
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def stable_order(jql: str) -> tuple:
    """(jql, changed). Adds an ordering that does not move while paging."""
    bare = _QUOTED_RX.sub('""', jql or "")
    if _ORDER_BY_RX.search(bare):
        return jql, False
    return f"{jql.strip()} ORDER BY key ASC", True


def search(conn: dict, jql: str, fields: list = None, max_results: int = DEFAULT_MAX,
           transport=None, on_page=None) -> dict:
    """Every issue a query holds, up to `max_results`.

    Returns {jql, issues, total, read, duplicates, complete, why_incomplete,
    ordered_by_tool}. See the module docstring for what `complete` means.
    """
    if not (jql or "").strip():
        raise Refused("an empty query would return every issue the token can see")
    if int(max_results) < 1:
        raise Refused("--max must be at least 1")
    wanted = min(int(max_results), HARD_MAX)
    jql, ordered = stable_order(jql)
    fields = list(fields or LIST_FIELDS)
    issues, seen, duplicates, read, unidentified = [], set(), 0, 0, 0
    total, first_total, moved, why, page_cap = None, None, False, "", PAGE_SIZE
    # Every turn either adds an issue not seen before or ends the walk,
    # so this is bounded by `wanted` whatever the server does: page sizes
    # smaller than asked, the same page for ever, a total that is a lie.
    while len(issues) < wanted:
        size = min(PAGE_SIZE, wanted - len(issues))
        page = _call(conn, "POST", "/search",
                     {"jql": jql, "startAt": read, "maxResults": size, "fields": fields},
                     transport=transport)
        if not isinstance(page, dict):
            raise JiraError(200, "Jira did not answer a search with an object")
        if isinstance(page.get("total"), int) and not isinstance(page.get("total"), bool) \
                and page["total"] >= 0:
            total = page["total"]
            if first_total is None:
                first_total = total
            elif total != first_total:
                moved = True
        batch = [i for i in (page.get("issues") or []) if isinstance(i, dict)]
        if on_page:
            on_page(read, len(batch), total)
        if not batch:
            break
        if len(batch) < size and (total is None or read + len(batch) < total):
            page_cap = min(page_cap, len(batch))
        read += len(batch)
        fresh = 0
        for issue in batch:
            ident = str(issue.get("key") or issue.get("id") or "")
            if not ident:
                unidentified += 1
                continue
            if ident in seen:
                duplicates += 1
                continue
            seen.add(ident)
            issues.append(issue)
            fresh += 1
        if not fresh or (total is not None and read >= total):
            break
    issues = issues[:wanted]
    expected = wanted if total is None else min(total, wanted)
    if total is None:
        why = "Jira did not say how many issues the query holds"
    elif moved:
        why = (f"the number of issues the query holds changed while the "
               f"pages were read (from {first_total} to {total}), so some "
               f"were read from a list that had shifted. Run it again.")
    elif unidentified:
        why = (f"{unidentified} issue(s) came back with neither a key nor "
               f"an id and were left out")
    elif len(seen) > total:
        why = (f"{len(seen)} distinct issue(s) were read but Jira said the "
               f"query holds {total}; its count cannot be relied on")
    elif len(issues) < expected:
        why = (f"{len(issues)} distinct issue(s) were read where {expected} "
               f"were expected"
               + (f"; {duplicates} came back twice, so as many were probably "
                  f"never seen (issues moved while the pages were read)"
                  if duplicates else "; Jira stopped returning pages early")
               + ". Run it again.")
    return {"jql": jql, "ordered_by_tool": ordered, "issues": issues,
            "total": total, "read": read, "duplicates": duplicates,
            "limit": wanted, "limited": total is not None and total > wanted,
            "page_size": page_cap,
            "complete": not why, "why_incomplete": why}


def count(conn: dict, jql: str, transport=None) -> int:
    """How many issues a query holds, without reading any."""
    if not (jql or "").strip():
        raise Refused("an empty query would count every issue the token can see")
    page = _call(conn, "POST", "/search",
                 {"jql": jql, "startAt": 0, "maxResults": 0, "fields": ["key"]},
                 transport=transport)
    if not isinstance(page, dict) or not isinstance(page.get("total"), int):
        raise JiraError(200, "Jira did not say how many issues the query holds")
    return page["total"]


def _list(value) -> list:
    return value if isinstance(value, list) else []


def slim(issue: dict) -> dict:
    """One issue as a row: what a list needs and nothing a list should carry."""
    f = issue.get("fields") if isinstance(issue.get("fields"), dict) else {}
    name = lambda v: str((v or {}).get("name") or "") if isinstance(v, dict) else ""
    return {"key": str(issue.get("key") or ""),
            "summary": str(f.get("summary") or "").strip(),
            "type": name(f.get("issuetype")),
            "status": name(f.get("status")),
            "priority": name(f.get("priority")),
            "labels": [l for l in _list(f.get("labels")) if isinstance(l, str)],
            "fix_versions": [name(v) for v in _list(f.get("fixVersions")) if name(v)],
            "updated": str(f.get("updated") or "")}


# ---- questions built on search --------------------------------------------

def fix_version_jql(project: str, version: str, issue_type: str = "") -> str:
    parts = [f"project = {project_key(project)}", f"fixVersion = {jql_quote(version)}"]
    if issue_type.strip():
        parts.append(f"issuetype = {jql_quote(issue_type.strip())}")
    return " AND ".join(parts)


def compare(new: dict, old: dict) -> dict:
    """What a release holds that the one before did not, and the reverse.

    `reliable` is false when either list is incomplete or was cut at the
    limit: an issue "added" may simply be one that was not read on the
    other side.
    """
    a = {i["key"]: i for i in map(slim, new["issues"])}
    b = {i["key"]: i for i in map(slim, old["issues"])}
    return {"added": [a[k] for k in a if k not in b],
            "removed": [b[k] for k in b if k not in a],
            "continuing": [a[k] for k in a if k in b],
            "reliable": usable(new) and usable(old)}


def tests_jql(project: str, issue_type: str = "Test", extra: str = "") -> str:
    """The tests a project already has (Xray stores a test as an issue)."""
    jql = f"project = {project_key(project)} AND issuetype = {jql_quote(issue_type)}"
    if extra.strip():
        jql += f" AND ({contained(extra)})"
    return jql


def contained(extra: str) -> str:
    """A further condition that stays INSIDE the brackets it is put in.

    `1=1) OR (project = OTHER` closes the bracket and widens the query to
    another project, and the result is still filed as this project's.
    """
    extra = extra.strip()
    bare = _QUOTED_RX.sub('""', extra)
    if bare.count('"') % 2 or bare.count("'") % 2:
        raise Refused("the extra condition has an unclosed quote")
    depth = 0
    for ch in bare:
        depth += ch == "("
        depth -= ch == ")"
        if depth < 0:
            break
    if depth != 0:
        raise Refused("the extra condition has unbalanced brackets; it must "
                      "be one condition that can be ANDed on")
    if _ORDER_BY_RX.search(bare):
        raise Refused("the extra condition may not contain ORDER BY")
    return extra


def usable(result: dict) -> bool:
    """Whole: every issue the query holds, read once."""
    return bool(result["complete"]) and not result["limited"]


# ---- output ----------------------------------------------------------------

def _out_dir() -> str:
    d = os.path.join(ROOT, "target", "jira", "search")
    os.makedirs(d, exist_ok=True)
    return d


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")[:80] or "result"


def write(name: str, payload: dict) -> str:
    """target/jira/search/<name>.json -- under target/, which is ignored."""
    path = os.path.join(_out_dir(), _safe(name) + ".json")
    payload = dict(payload, at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    with io.open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
    return os.path.relpath(path, ROOT).replace(os.sep, "/")


def report(result: dict, label: str = "issues") -> None:
    total = "?" if result["total"] is None else result["total"]
    say(f"  query : {result['jql']}")
    if result["ordered_by_tool"]:
        say("          (ORDER BY key ASC was added so paging cannot reshuffle)")
    say(f"  {label}: {len(result['issues'])} read of {total}")
    if result["limited"]:
        say(f"  LIMIT : only the first {result['limit']} were read. Raise --max "
            f"(up to {HARD_MAX}) or narrow the query.")
    if result["duplicates"]:
        say(f"  note  : {result['duplicates']} issue(s) came back twice and were kept once")
    if result.get("page_size", PAGE_SIZE) < PAGE_SIZE:
        say(f"  note  : this Jira returns at most {result['page_size']} issues a page")
    if not result["complete"]:
        say(f"  INCOMPLETE: {result['why_incomplete']}")


def _rows(issues: list, limit: int = 40) -> None:
    for i in issues[:limit]:
        say(f"    {i['key']:<14} {i['status'][:14]:<14} {i['summary'][:80]}")
    if len(issues) > limit:
        say(f"    ... {len(issues) - limit} more in the file")


def main(argv=None, transport=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("verify", help="is the Jira token accepted?")
    p = sub.add_parser("versions", help="a project's versions, newest first")
    p.add_argument("--project", required=True)
    p.add_argument("--all", action="store_true", help="include archived versions")
    p = sub.add_parser("search", help="every issue a JQL query holds")
    p.add_argument("--jql", required=True)
    p.add_argument("--max", type=int, default=DEFAULT_MAX)
    p.add_argument("--name", default="", help="file name for the result")
    p = sub.add_parser("fix-version", help="what is in a release, and what changed")
    p.add_argument("--project", required=True)
    p.add_argument("--version", required=True)
    p.add_argument("--compare", default="", help="an earlier version to compare with")
    p.add_argument("--type", default="", help="issue type, e.g. Story")
    p.add_argument("--max", type=int, default=DEFAULT_MAX)
    p = sub.add_parser("paste", help="keys, a query or a Jira address, as pasted")
    p.add_argument("text")
    p.add_argument("--max", type=int, default=DEFAULT_MAX)
    p = sub.add_parser("tests", help="the tests a project already has")
    p.add_argument("--project", required=True)
    p.add_argument("--type", default="Test", help="the issue type tests are stored as")
    p.add_argument("--jql", default="", help="a further condition, ANDed on")
    p.add_argument("--max", type=int, default=DEFAULT_MAX)
    a = ap.parse_args(argv)
    # A story summary can hold any character; a Windows console or a pipe
    # often cannot print it. A row that will not print must not cost the run.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

    t0 = time.time()
    try:
        conn = connect()
        say(f"jira : {conn['base']}{conn['api']}  (token: header, "
            f"{len(conn['token'])} chars)")
        if conn.get("cleartext"):
            say("WARN the base URL is http://: the token is sent unencrypted. "
                "Use the https:// address in jira_config.base_urls.")
        progress = lambda at, n, total: say(
            f"  page at {at}: {n} issue(s)" + (f" of {total}" if total is not None else ""))

        if a.cmd == "verify":
            ok, msg = verify(conn, transport)
            say(f"{' ok ' if ok else 'FAIL'} {msg}")
            return 0 if ok else 1

        if a.cmd == "versions":
            versions = list_versions(conn, a.project, a.all, transport)
            for v in versions:
                flags = ", ".join(x for x in ("released" if v["released"] else "unreleased",
                                              "archived" if v["archived"] else "",
                                              v["release_date"]) if x)
                say(f"    {v['name']:<40} {flags}")
            say(f"  {len(versions)} version(s), newest first by the number in the name")
            say(f"  written: {write(f'{project_key(a.project)}-versions', {'versions': versions})}")
            return 0

        if a.cmd == "paste":
            what = pasted.parse(a.text)
            # Where the paste says it is from is checked, then ignored:
            # the request goes to the configured host either way.
            # ...and "the configured host" is ONE host, the first base URL.
            # A second approved Jira is approved for fetch.py, which goes
            # where the address says; a search here would answer a paste
            # from that one with results from this one.
            for host in what["hosts"]:
                allowed, why = fetch.host_allowed(host, [conn["base"]])
                if not allowed:
                    raise Refused(f"{why} (searches go to {conn['base']})")
            if what["kind"] == pasted.NONE:
                raise Refused(what["why"])
            if what["kind"] == pasted.KEYS:
                say(f"  {len(what['keys'])} story key(s): {', '.join(what['keys'])}")
                if not all(re.fullmatch(r"[A-Z][A-Z0-9_]+-[0-9]+", k) for k in what["keys"]):
                    raise Refused("a key in the paste is not a plain story key")
                jql = "issuekey in (" + ", ".join(what["keys"]) + ")"
            else:
                jql = what["jql"]
            a.jql, a.name, a.cmd = jql, "paste", "search"

        if a.cmd == "search":
            r = search(conn, a.jql, max_results=a.max, transport=transport, on_page=progress)
            report(r)
            rows = [slim(i) for i in r["issues"]]
            say(f"  written: {write(a.name or 'search', dict(r, issues=rows))}")
            _rows(rows)
            return 0 if usable(r) else 1

        if a.cmd == "tests":
            r = search(conn, tests_jql(a.project, a.type, a.jql), max_results=a.max,
                       transport=transport, on_page=progress)
            report(r, "tests ")
            rows = [slim(i) for i in r["issues"]]
            say(f"  written: {write(f'{project_key(a.project)}-tests', dict(r, issues=rows))}")
            _rows(rows)
            return 0 if usable(r) else 1

        new = search(conn, fix_version_jql(a.project, a.version, a.type),
                     max_results=a.max, transport=transport, on_page=progress)
        report(new)
        out = {"project": project_key(a.project), "version": a.version,
               "result": dict(new, issues=[slim(i) for i in new["issues"]])}
        ok = usable(new)
        if a.compare.strip():
            old = search(conn, fix_version_jql(a.project, a.compare, a.type),
                         max_results=a.max, transport=transport, on_page=progress)
            report(old)
            delta = compare(new, old)
            out.update(compared_with=a.compare,
                       old=dict(old, issues=[slim(i) for i in old["issues"]]), delta=delta)
            ok = ok and delta["reliable"]
            for title, key in (("only in " + a.version, "added"),
                               ("only in " + a.compare, "removed"),
                               ("in both", "continuing")):
                say(f"  {title}: {len(delta[key])}")
                _rows(delta[key], 15)
            if not delta["reliable"]:
                say("  NOT RELIABLE: one of the two lists is incomplete or was "
                    "cut at the limit, so an issue shown on one side only may "
                    "just not have been read on the other.")
        else:
            _rows(out["result"]["issues"])
        say(f"  written: {write(f'{project_key(a.project)}-fixversion-{a.version}', out)}")
        return 0 if ok else 1
    except Refused as e:
        say(f"refused: {e}")
        return 2
    except JiraError as e:
        say(f"FAIL {e}")
        return 1
    except Exception as e:                               # noqa: BLE001
        # No message and no traceback: this output is a log the workbench
        # serves, and an exception text can quote a header.
        say(f"FAIL unexpected {type(e).__name__} in search.py "
            f"(line {e.__traceback__.tb_lineno if e.__traceback__ else '?'})")
        return 1
    finally:
        say(f"  ({time.time() - t0:.1f}s)")


if __name__ == "__main__":
    sys.exit(main())
