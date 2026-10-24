"""The stories of one release, for the Automate tab to pick from.

    versions(project_key, project)            -> the Jira project's versions, newest first
    stories(project_key, version, project)    -> its stories in that release (fixVersion)

Asked for: reading the stories of a release, not every story of the Jira
project. The query is `project = KEY AND fixVersion = "R" AND issuetype =
Story` -- the release's own, nothing else read. The page lists them, the
person ticks some, and their keys go into the story box, where "Read it"
makes one job a story (as several pasted keys always have).

Read-only, answered by the server itself (no job: the stories become jobs
only once picked), with the Jira of the workbench project chosen on the
page (B2B or GOAL: tools/jira/projectconfig.py), or the stand-in Jira in
mock mode -- search.connect decides that, as for every other Jira read.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "jira"))
import search  # noqa: E402

projectconfig = search.projectconfig

# The list is for picking by eye; a release bigger than this is narrowed
# with the type box, not scrolled through.
MAX_STORIES = 500
STORY_FIELDS = ["summary", "status", "issuetype", "fixVersions", "labels", "updated"]


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


def stories(project_key: str, version: str, project: str = "", issue_type: str = "Story",
            limit: int = MAX_STORIES, transport=None) -> dict:
    """The release's issues of `issue_type` (Story unless changed), each
    {key, summary, status, type, fix_versions, labels}, and how much of the
    release was read -- a list cut short says so."""
    key = _key(project_key)
    version = str(version or "").strip()
    if not version:
        raise Refused("choose the release (fixVersion)")
    try:
        limit = max(1, min(int(limit), MAX_STORIES))
    except (TypeError, ValueError):
        limit = MAX_STORIES
    jql = search.fix_version_jql(key, version, (issue_type or "").strip())
    with projectconfig.using(_project(project)):
        try:
            conn = search.connect()
            got = search.search(conn, jql, fields=STORY_FIELDS, max_results=limit, transport=transport)
        except (search.Refused, search.JiraError) as e:
            raise Refused(str(e)) from None
    issues = [search.slim(i) for i in got.get("issues") or []]
    return {"project_key": key, "version": version, "type": (issue_type or "").strip(),
            "jql": got.get("jql") or jql, "total": got.get("total"), "read": len(issues),
            "complete": bool(search.usable(got)), "why_incomplete": got.get("why_incomplete") or "",
            "limited": bool(got.get("limited")), "limit": limit, "issues": issues}
