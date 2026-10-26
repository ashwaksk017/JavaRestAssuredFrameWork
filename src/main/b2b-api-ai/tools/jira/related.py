"""Which Jira issues are related to a story, and why.

Two readers ask this:

  * reading a story (tools/agent/intake.py): of the issues Jira links to
    it, which are read into the brief as the story's bigger picture;
  * the release picker (tools/agent/release.py): of a release's stories,
    which are related to the story in question.

Asked for (2026-10-26): a story's every link was read, of every kind, in
both directions -- clones from an older release, Xray tests, bugs, issues
of other projects -- and the brief Cursor is sent filled with stories that
were not this one's. Now a link is read when it is a kind that means "these
two go together" and the issue is a story of the same project and release.
What is left out is said, with the reason.

The rules are jira_config's `linked_stories` block, or the defaults:

    "linked_stories": {
      "link_types":  ["block", "depend", "relate"],   words in the link's name
      "issue_types": ["Story", "Task", "Sub-task", "Subtask"],
      "same_project": true,
      "same_release": true
    }

A link type is matched by WORD against the link's name and both its
phrases ("blocks" / "is blocked by"), so "relate" takes "Relates" and
"relates to" alike. Sub-tasks of the story are always read: they are the
story, split.
"""
from __future__ import annotations

import re

DEFAULT_LINK_WORDS = ("block", "depend", "relate")
DEFAULT_ISSUE_TYPES = ("story", "task", "sub-task", "subtask")

_KEY_RX = re.compile(r"[A-Za-z][A-Za-z0-9_]*-[0-9]+")


def _words(value, default) -> tuple:
    if not isinstance(value, list):
        return tuple(default)
    got = tuple(str(v).strip().lower() for v in value if str(v or "").strip())
    return got or tuple(default)


def rules(cfg) -> dict:
    """The rules from jira_config (a dict, or nothing for the defaults)."""
    block = (cfg or {}).get("linked_stories") if isinstance(cfg, dict) else None
    block = block if isinstance(block, dict) else {}
    return {"link_words": _words(block.get("link_types"), DEFAULT_LINK_WORDS),
            "issue_types": _words(block.get("issue_types"), DEFAULT_ISSUE_TYPES),
            "same_project": block.get("same_project") is not False,
            "same_release": block.get("same_release") is not False}


def project_of(key) -> str:
    key = str(key or "").strip().upper()
    return key.rsplit("-", 1)[0] if _KEY_RX.fullmatch(key) else ""


def _name(value) -> str:
    return str(value.get("name") or "") if isinstance(value, dict) else (
        value if isinstance(value, str) else "")


def releases(fields) -> set:
    """The fixVersion names of an issue's fields."""
    f = fields if isinstance(fields, dict) else {}
    vs = f.get("fixVersions") if isinstance(f.get("fixVersions"), list) else []
    return {_name(v).strip().lower() for v in vs if _name(v).strip()}


def link_kind_ok(rule: dict, *names) -> bool:
    """Whether a link of this kind (its name, its phrases) is one to read."""
    said = " ".join(str(n or "") for n in names).lower()
    return any(w in said for w in rule["link_words"])


def type_ok(rule: dict, issue_type) -> bool:
    t = str(issue_type or "").strip().lower()
    return not t or t in rule["issue_types"]


def links(fields) -> list:
    """Every link Jira gives on an issue's fields, as dicts:
    {key, how, kind, type, fields} -- `how` the phrase from this issue's
    side ("is blocked by"), `kind` the link type's name, `type` the other
    issue's type when Jira sent it ('' when not), in Jira's order."""
    f = fields if isinstance(fields, dict) else {}
    out = []
    for link in f.get("issuelinks") if isinstance(f.get("issuelinks"), list) else []:
        if not isinstance(link, dict):
            continue
        kind = link.get("type") if isinstance(link.get("type"), dict) else {}
        if isinstance(link.get("outwardIssue"), dict):
            other, how = link["outwardIssue"], kind.get("outward") or kind.get("name")
        elif isinstance(link.get("inwardIssue"), dict):
            other, how = link["inwardIssue"], kind.get("inward") or kind.get("name")
        else:
            continue
        theirs = other.get("fields") if isinstance(other.get("fields"), dict) else {}
        out.append({"key": str(other.get("key") or "").strip().upper(),
                    "how": str(how or "linked to"), "kind": str(kind.get("name") or ""),
                    "type": _name(theirs.get("issuetype")), "fields": theirs})
    return out


def skip_link(rule: dict, story_key: str, link: dict) -> str:
    """Why a linked issue is not read, from the link alone ('' = read it).
    Asked before the issue is fetched: what is left out costs no call."""
    if not link_kind_ok(rule, link.get("kind"), link.get("how")):
        return f"a '{link.get('how') or link.get('kind')}' link, not one of {', '.join(rule['link_words'])}"
    if rule["same_project"] and project_of(link.get("key")) != project_of(story_key):
        return "another Jira project"
    if not type_ok(rule, link.get("type")):
        return f"a {link.get('type')}, not a story"
    return ""


def skip_issue(rule: dict, story_fields, issue_fields) -> str:
    """Why a linked issue, now read, is still left out ('' = keep it):
    what the link alone did not say -- its type, and its release."""
    f = issue_fields if isinstance(issue_fields, dict) else {}
    kind = _name(f.get("issuetype"))
    if not type_ok(rule, kind):
        return f"a {kind}, not a story"
    if rule["same_release"]:
        mine, theirs = releases(story_fields), releases(f)
        if mine and theirs and not mine & theirs:
            return f"another release ({', '.join(sorted(theirs))[:60]})"
    return ""


# ---- the epic --------------------------------------------------------------

def epic_of(issue: dict, epic_field: str = "") -> str:
    """The key of an issue's parent or epic: `parent` (Jira Cloud, and
    sub-tasks everywhere), else the Epic Link field (Jira Server / Data
    Center keep the epic there, in a custom field whose id differs per
    Jira -- named in jira_config `epic_link_field`, or found by its name
    when the answer carries `names`)."""
    issue = issue if isinstance(issue, dict) else {}
    f = issue.get("fields") if isinstance(issue.get("fields"), dict) else {}
    p = f.get("parent")
    if isinstance(p, dict) and _KEY_RX.fullmatch(str(p.get("key") or "").strip()):
        return str(p["key"]).strip().upper()
    ids = [epic_field] if epic_field else []
    names = issue.get("names") if isinstance(issue.get("names"), dict) else {}
    ids += [fid for fid, n in names.items() if str(n).strip().lower() == "epic link"]
    for fid in ids:
        v = f.get(fid)
        v = v.get("key") if isinstance(v, dict) else v
        if isinstance(v, str) and _KEY_RX.fullmatch(v.strip()):
            return v.strip().upper()
    return ""


# ---- the release picker ------------------------------------------------------

def reasons(rule: dict, story: dict, other: dict, epic_field: str = "") -> list:
    """Why `other` (an issue of the release) is related to `story` (the
    issue in question), strongest first; [] when nothing says it is.

      linked      -- Jira links the two, by a link kind that is read
      same epic   -- the same parent or epic (or one is the other's)
      component   -- a Jira component in common
      label       -- a label in common (the weakest: labels are loose)
    """
    sk, ok = str(story.get("key") or "").upper(), str(other.get("key") or "").upper()
    sf = story.get("fields") if isinstance(story.get("fields"), dict) else {}
    of = other.get("fields") if isinstance(other.get("fields"), dict) else {}
    out = []
    for side, them, fields in ((sk, ok, sf), (ok, sk, of)):
        for link in links(fields):
            if link["key"] == them and link_kind_ok(rule, link["kind"], link["how"]):
                out.append(f"linked: {side} {link['how']} {them}")
                break
        if out:
            break
    for sub in sf.get("subtasks") if isinstance(sf.get("subtasks"), list) else []:
        if isinstance(sub, dict) and str(sub.get("key") or "").upper() == ok:
            out.append(f"sub-task of {sk}")
    se, oe = epic_of(story, epic_field), epic_of(other, epic_field)
    if se and se == oe:
        out.append(f"same epic {se}")
    elif oe and oe == sk:
        out.append(f"under {sk}")
    elif se and se == ok:
        out.append(f"{sk}'s parent")
    listed = lambda v: v if isinstance(v, list) else []
    comps = lambda f: {_name(c).strip() for c in listed(f.get("components")) if _name(c).strip()}
    shared = sorted(comps(sf) & comps(of))
    if shared:
        out.append("component " + ", ".join(shared[:3]))
    labels = lambda f: {l.strip() for l in listed(f.get("labels"))
                        if isinstance(l, str) and l.strip()}
    shared = sorted(labels(sf) & labels(of))
    if shared:
        out.append("label " + ", ".join(shared[:3]))
    return out
