"""The stories of one release, for the Automate tab to pick from.

    versions(project_key, project)            -> the Jira project's versions, newest first
    stories(project_key, version, project)    -> its stories in that release (fixVersion)

Asked for: reading the stories of a release, not every story of the Jira
project. The query is `project = KEY AND fixVersion = "R" AND issuetype =
Story` -- the release's own, nothing else read. The page lists them, the
person ticks some, and their keys go into the story box, where "Read it"
makes one job a story (as several pasted keys always have).

RELATED TO THE STORY IN QUESTION (asked for, 2026-10-26): a release holds
every team's stories. Given the story being worked on (`related_to`), each
of the release's stories says why it is related to it -- linked to it, the
same epic, a component or a label in common (tools/jira/related.py) -- or
nothing, and the page shows the related ones first. Read in the same one
search: no call per story.

Read-only, answered by the server itself (no job: the stories become jobs
only once picked), with the Jira of the workbench project chosen on the
page (B2B or GOAL: tools/jira/projectconfig.py), or the stand-in Jira in
mock mode -- search.connect decides that, as for every other Jira read.
"""
from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "jira"))
import search  # noqa: E402
import related  # noqa: E402

projectconfig = search.projectconfig

# The list is for picking by eye; a release bigger than this is narrowed
# with the type box, not scrolled through.
MAX_STORIES = 500
STORY_FIELDS = ["summary", "status", "issuetype", "fixVersions", "labels", "updated"]
# What says whether two stories go together (related.reasons).
RELATED_FIELDS = ["parent", "issuelinks", "subtasks", "components"]
_ISSUE_KEY_RX = re.compile(r"[A-Z][A-Z0-9_]*-[0-9]+")


class Refused(ValueError):
    """A question this cannot ask Jira: said to the person as it is."""


def _project(project: str) -> str:
    p = str(project or "").strip().lower() or projectconfig.DEFAULT_PROJECT
    if p not in projectconfig.PROJECT_FILES:
        raise Refused(f"{project!r} is not a workbench project ({', '.join(sorted(projectconfig.PROJECT_FILES))})")
    return p


def _key(project_key: str) -> str:
    try:
        return search.project_key(project_key)
    except Exception as e:                               # noqa: BLE001
        raise Refused(str(e) or "give the Jira project key, e.g. ABC") from None


def versions(project_key: str, project: str = "", include_archived: bool = False, transport=None) -> dict:
    """{project_key, versions: [{name, released, archived, release_date}]}, newest first."""
    key = _key(project_key)
    with projectconfig.using(_project(project)):
        try:
            conn = search.connect()
            got = search.list_versions(conn, key, include_archived, transport=transport)
        except (search.Refused, search.JiraError) as e:
            raise Refused(str(e)) from None
    return {"project_key": key, "versions": got}


def _epic_field(conn: dict, cfg: dict, transport=None) -> str:
    """The id of the Epic Link field (Jira Server / Data Center keep a
    story's epic there): jira_config `epic_link_field`, else the field Jira
    names "Epic Link", else '' (Jira Cloud: the epic is `parent`)."""
    named = str((cfg or {}).get("epic_link_field") or "").strip()
    if named:
        return named
    try:
        listed = search._call(conn, "GET", "/field", transport=transport)
    except Exception:                                    # noqa: BLE001
        return ""
    for f in listed if isinstance(listed, list) else []:
        if isinstance(f, dict) and str(f.get("name") or "").strip().lower() == "epic link":
            return str(f.get("id") or "")
    return ""


_STRONGEST_FIRST = ("this is", "linked", "sub-task", "same epic", "under", "component", "label")


def _strength(reasons: list) -> int:
    """0 the strongest; unrelated last. "<KEY>'s parent" ranks with the epic."""
    if not reasons:
        return len(_STRONGEST_FIRST) + 1
    first = reasons[0]
    return next((n for n, s in enumerate(_STRONGEST_FIRST) if first.startswith(s)), 4)


def stories(project_key: str, version: str, project: str = "", issue_type: str = "Story",
            limit: int = MAX_STORIES, transport=None, related_to: str = "") -> dict:
    """The release's issues of `issue_type` (Story unless changed), each
    {key, summary, status, type, fix_versions, labels, related}, and how
    much of the release was read -- a list cut short says so.

    `related_to`: the story in question. Each issue's `related` is then why
    it is related to that story (strongest first), [] when nothing says so."""
    key = _key(project_key)
    version = str(version or "").strip()
    if not version:
        raise Refused("choose the release (fixVersion)")
    try:
        limit = max(1, min(int(limit), MAX_STORIES))
    except (TypeError, ValueError):
        limit = MAX_STORIES
    related_to = str(related_to or "").strip().upper()
    if related_to and not _ISSUE_KEY_RX.fullmatch(related_to):
        raise Refused(f"{related_to!r} is not a story key (like ABC-123)")
    jql = search.fix_version_jql(key, version, (issue_type or "").strip())
    story, epic, note = None, "", ""
    with projectconfig.using(_project(project)):
        try:
            conn = search.connect()
            fields = list(STORY_FIELDS)
            if related_to:
                cfg = {} if conn.get("mock") else (projectconfig.section("jira_config")[0] or {})
                rule = related.rules(cfg)
                epic = _epic_field(conn, cfg, transport)
                fields += RELATED_FIELDS + ([epic] if epic else [])
                found = search.search(conn, f"issuekey in ({related_to})", fields=fields,
                                      max_results=1, transport=transport).get("issues") or []
                story = found[0] if found and isinstance(found[0], dict) else None
                if story is None:
                    note = f"{related_to} was not found in Jira: nothing is marked related"
            got = search.search(conn, jql, fields=fields, max_results=limit, transport=transport)
        except (search.Refused, search.JiraError) as e:
            raise Refused(str(e)) from None
    issues = []
    for raw in got.get("issues") or []:
        row = search.slim(raw)
        if story is not None:
            row["related"] = (["this is the story"] if row["key"].upper() == related_to
                              else related.reasons(rule, story, raw, epic))
        issues.append(row)
    if story is not None:
        # Related first, the strongest reason first; Jira's order otherwise.
        issues.sort(key=lambda i: _strength(i["related"]))   # stable: Jira's order within
    return {"project_key": key, "version": version, "type": (issue_type or "").strip(),
            "related_to": related_to if story is not None else "", "related_note": note,
            "related": sum(1 for i in issues if i.get("related")),
            "jql": got.get("jql") or jql, "total": got.get("total"), "read": len(issues),
            "complete": bool(search.usable(got)), "why_incomplete": got.get("why_incomplete") or "",
            "limited": bool(got.get("limited")), "limit": limit, "issues": issues}
