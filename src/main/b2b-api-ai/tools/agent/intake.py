"""Whatever was pasted, turned into one brief a human can argue with.

    python tools/agent/intake.py --job j1 --text-file story.txt
    python tools/agent/intake.py --job j1 --text "POST /props/ABC/groups ..."
    python tools/agent/intake.py --job j1 --link https://wiki/x/AbCdEf --link ABC-123

Stage 2 of the story-to-test plan. The UI will call this with one blob
of pasted text and a list of links; the CLI exists so the same thing is
reproducible without the UI, which is how every other stage here works.

WHAT IT PRODUCES
----------------
    .workbench/<job>/brief.md        the readable summary
    .workbench/<job>/intake.json     the same thing, structured
    .workbench/<job>/sources/        one snapshot per link

WHAT IT REFUSES
---------------
No request could be read, from the pasted text or from any source. The
brief is still written -- with the sources listed and the reason on line
one -- because "I read these four things and found no request in them"
is a useful answer, and a silent empty result is not.

It does NOT invent a request from prose that merely describes one. The
extractor reads curl commands and `VERB /path` lines; a sentence saying
"the endpoint creates a rate plan" is a description, not a request, and
guessing its shape is how a test ends up asserting something nobody
specified.

LINKS ARE CLASSIFIED, NOT ASSUMED
---------------------------------
A pasted link is routed to the Jira fetcher or the Confluence fetcher by
what it looks like. Neither fetcher is asked to handle the other's URLs,
and a link that is neither is reported as such rather than attempted.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))


def _diag():
    """tools/diag.py, once for the whole process: the record of what this
    command did, kept for finding out what went wrong."""
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
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise SystemExit(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    # Registered before it runs: a @dataclass under postponed annotations
    # looks its own module up in sys.modules while it is being defined.
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return mod


# Loaded by path: tools/jira and tools/confluence both contain a module
# called `fetch`, so putting either directory on sys.path makes the name
# ambiguous -- which already caused one confusing failure in Stage 1.
_JIRA = os.path.join(TOOLS, "jira")
_CONF = os.path.join(TOOLS, "confluence")

sys.path.insert(0, _JIRA)          # extract.py imports its siblings
projectconfig = _load("agent_projectconfig", os.path.join(_JIRA, "projectconfig.py"))
jira_extract = _load("agent_jira_extract", os.path.join(_JIRA, "extract.py"))
jira_query = _load("agent_jira_query", os.path.join(_JIRA, "query.py"))
sys.path.insert(0, _CONF)
conf_pageref = _load("agent_conf_pageref", os.path.join(_CONF, "pageref.py"))
conf_fetch = _load("agent_conf_fetch", os.path.join(_CONF, "fetch.py"))
jira_fetch = _load("agent_jira_fetch", os.path.join(_JIRA, "fetch.py"))
jira_search = _load("agent_jira_search", os.path.join(_JIRA, "search.py"))

ROOT = projectconfig.ROOT

JIRA, JIRA_QUERY, CONFLUENCE, UNKNOWN = "jira", "jira-query", "confluence", "unknown"
_JIRA_BROWSE_RX = re.compile(r"/browse/[A-Z][A-Z0-9_]+-[0-9]+", re.I)


def classify(link: str) -> str:
    """Which fetcher a pasted link belongs to.

    Stories -- one key, several, or the address of one -- are JIRA. A
    JQL query, or the address of a Jira search or saved filter, is
    JIRA_QUERY: a list of stories, which is a different question and is
    not answered here. What each one IS comes from tools/jira/query.py.
    """
    s = (link or "").strip()
    if not s:
        return UNKNOWN
    # One link a line: which of two was meant is a guess. An address
    # INSIDE another's query string (`?src=https://...`) is not a second.
    if len(re.findall(r"(?i)(?:^|[\s;,)>]<?)https?://", s)) > 1:
        return UNKNOWN
    try:
        what = jira_query.parse(s)
    except Exception:                                    # noqa: BLE001
        diag.trace()
        what = {"kind": jira_query.NONE}    # one bad line must not lose the job
    ref, _why = conf_pageref.parse(s)
    confluence = (ref is not None and ref.kind in ("id", "title", "tiny")
                  and "://" in s)
    # A bare key, or keys, is Jira. An ADDRESS that the wiki reader
    # recognises is the wiki's even when a story key appears in it
    # (`.../pages/123/browse/ABC-1`, `?selectedIssue=ABC-1` on a page).
    if what["kind"] == jira_query.KEYS and not (confluence and what.get("hosts")):
        return JIRA
    if confluence:
        return CONFLUENCE
    # As before this module used query.py: an address with /browse/KEY
    # anywhere in the line, whatever is written around it.
    if _JIRA_BROWSE_RX.search(s):
        return JIRA
    if what["kind"] == jira_query.JQL:
        return JIRA_QUERY
    return UNKNOWN


_SAFE_JOB_RX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def safe_job(job: str) -> str:
    """A job id that cannot leave .workbench/.

    This comes from a UI text box. `os.path.join` lets an ABSOLUTE
    second argument win outright, so a job id of `C:/Windows/Temp` or
    `/etc` does not land under the job root at all, and `../..` walks
    out of it. Validate rather than sanitise: a silently-rewritten id
    makes two different jobs share a directory.
    """
    j = (job or "").strip()
    if not _SAFE_JOB_RX.match(j) or j in (".", ".."):
        raise ValueError(
            f"unusable job id {job!r}. Letters, digits, dot, dash and "
            f"underscore only, up to 64 characters -- it names a directory.")
    return j


def job_dir(job: str, root: str = "") -> str:
    return os.path.join(root or ROOT, ".workbench", safe_job(job))


PROJECT_FILE = "project.json"


def job_project(job_dir_path: str) -> str:
    """The project a job is for (b2b, goal, ...): its project.json, else
    b2b -- every job made before projects is a B2B job."""
    try:
        with io.open(os.path.join(job_dir_path, PROJECT_FILE), encoding="utf-8") as fh:
            p = str(json.load(fh).get("project") or "").strip().lower()
    except (OSError, ValueError, AttributeError):
        p = ""
    return p if p in projectconfig.PROJECT_FILES else projectconfig.DEFAULT_PROJECT


def set_job_project(job_dir_path: str, project: str) -> str:
    """Record the job's project; an unknown one is a ValueError."""
    p = str(project or "").strip().lower()
    if p not in projectconfig.PROJECT_FILES:
        raise ValueError(f"{project!r} is not a project ({', '.join(sorted(projectconfig.PROJECT_FILES))})")
    os.makedirs(job_dir_path, exist_ok=True)
    tmp = os.path.join(job_dir_path, PROJECT_FILE + ".tmp")
    with io.open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"project": p}, fh)
    os.replace(tmp, os.path.join(job_dir_path, PROJECT_FILE))
    return p


_BRACE_ESCAPE_RX = re.compile(r"\\([{}\[\]])")
# Everything Jira's wiki markup escapes with a backslash to show it as
# itself. A backslash before a line break (a curl command carried over a
# line) or before a quote (JSON) is not among them and is left alone.
_WIKI_ESCAPE_RX = re.compile(r"\\([{}\[\]*_^~|!#+-])")


def _unwiki(text: str) -> str:
    """Jira's text as a person reads it on the page: `\\{id}` is `{id}`."""
    return _WIKI_ESCAPE_RX.sub(r"\1", text or "")


def _requests_from(text: str, source: str) -> list:
    """Requests in a block of text, each carrying its expected status.

    `from_text` does not return one, and without it shape_match's
    `want_status` is always empty -- which silently disables
    DUPLICATE_SUSPECT, one of the two stops the next stage exists to
    raise. `extract.expected_status` reads it from the same prose, so
    the information is there; it was simply being dropped.
    """
    # `/guests/\{guest_id}`: Jira's and Confluence's own markup needs a
    # backslash before a brace or a bracket to show one, and the raw text
    # carries it. It is not part of the path -- and left in, the parameter
    # is not seen as one, so the request matches nothing.
    text = _BRACE_ESCAPE_RX.sub(r"\1", text or "")
    try:
        steps = jira_extract.from_text(text or "", source) or []
        status, evidence = jira_extract.expected_status(text or "")
        for step in steps:
            step.setdefault("expected_status", status or "")
            if evidence:
                step.setdefault("expected_status_evidence", evidence)
        return steps
    except Exception as e:  # a malformed paste must not kill the job
        diag.trace()
        return [{"verb": "", "raw_path": "", "error": str(e),
                 "provenance": f"{source}: could not be read"}]


def gather_confluence(link: str, sources: str, slot: str,
                      api_path: str = "") -> dict:
    """Fetch one Confluence page into its OWN directory under sources/.

    An earlier version diffed the sources directory before and after the
    fetch to find the new file. That is wrong twice: two links to the
    same page produce no new file the second time, and RE-RUNNING a job
    produces none at all because the file is already on disk. Both read
    as "fetched fine, nothing in it", and re-running is the normal thing
    to do from a UI. A directory per link makes the answer positional
    instead of inferred.
    """
    outdir = os.path.join(sources, slot)
    os.makedirs(outdir, exist_ok=True)
    argv = ["--url", link, "--out", outdir]
    if api_path:
        argv += ["--api-path", api_path]
    said = io.StringIO()
    try:
        diag.step("Fetching a linked Confluence page")
        # What the fetcher says is the reason it gives: kept, so that a
        # refusal reaches the brief instead of "run it directly to see why".
        try:
            with contextlib.redirect_stdout(said):
                rc = conf_fetch.main(argv)
        finally:
            sys.stdout.write(said.getvalue())            # a copy, not a sink: the job log keeps it
            sys.stdout.flush()
    except SystemExit as e:           # argparse inside a library call
        rc = int(getattr(e, "code", 1) or 1)
    except Exception as e:
        diag.trace()
        return {"link": link, "kind": CONFLUENCE, "ok": False,
                "reason": f"{type(e).__name__}: {e}"}
    if rc != 0:
        lines = [l.strip() for l in said.getvalue().splitlines() if l.strip()]
        why = next((l for l in lines if l.lower().startswith(("refused", "fail", "error"))),
                   lines[-1] if lines else "")
        return {"link": link, "kind": CONFLUENCE, "ok": False,
                "reason": _plain_line(conf_fetch.redact(why), 400) if why
                else "the fetcher refused without saying why"}
    md = sorted(n for n in os.listdir(outdir) if n.endswith(".md"))
    if not md:
        return {"link": link, "kind": CONFLUENCE, "ok": False,
                "reason": "the fetcher reported success but wrote no page"}
    return {"link": link, "kind": CONFLUENCE, "ok": True,
            "dir": slot, "files": md,
            "text_file": os.path.join(slot, md[0])}


# ---- a Jira story -----------------------------------------------------------
MAX_STORY_CHARS = 8000            # of the story's text, in the brief
# Headings of brief.md that other tools cut on (loop.trimmed_brief).
BRIEF_STORY_HEAD = "## The story"
BRIEF_PAGES_HEAD = "## Confluence pages"
BRIEF_REQUESTS_HEAD = "## Requests read"
MAX_ATTACHMENT_TEXT = 200_000     # of one textual attachment, read for requests
MAX_PAGE_CHARS = 40000            # of one Confluence page, in the brief
MAX_PAGES_TOTAL = 90000           # of all of them together
MAX_PAGE_BLOCK = 1500             # a code block longer than this is named, not shown
MAX_STORY_LINKS = 6               # Confluence links found in the story and read unasked
_URL_IN_TEXT_RX = re.compile(r"https?://[^\s<>\"'|\]\[)]+")


def confluence_links_in(text: str, given: list) -> list:
    """The Confluence page links a story's text names, in order, without
    the ones already given. Jira writes a link as `[url]` or `[text|url]`;
    what trails it (`]`, `.`, `,`) is not part of it."""
    out = []
    have = {g.strip().rstrip("/").lower() for g in given}
    for m in _URL_IN_TEXT_RX.finditer(text or ""):
        url = m.group(0).rstrip(".,;:)")
        if classify(url) != CONFLUENCE:
            continue
        key = url.rstrip("/").lower()
        if key in have:
            continue
        have.add(key)
        out.append(url)
    return out[:MAX_STORY_LINKS]
_PAGE_FENCE_RX = re.compile(r"(```|~~~)[^\n]{0,40}\n(.*?)\1", re.S)


def _page_for_brief(body: str, budget: int) -> tuple:
    """(text, how much was left out) of a fetched page, for the brief.

    A page is read for what it SAYS -- the rule, the table of who may do
    what. A page that carries a whole specification in a code block would
    drown that, and the specification is handed over on its own anyway,
    so a long block is named and left out.
    """
    def named(m):
        if len(m.group(2)) <= MAX_PAGE_BLOCK:
            return m.group(0)
        return (f"(a code block of {len(m.group(2))} characters is not shown here; "
                f"it is in the job's sources folder)")
    text = _PAGE_FENCE_RX.sub(named, body or "").strip()
    cap = max(0, min(MAX_PAGE_CHARS, budget))
    return text[:cap], max(0, len(text) - cap)
# Every linked story and sub-task is read, and each story around this one
# -- parents, linked -- is in the brief whole. Asked for (2026-10-21): it was
# the first 8 linked stories, 1500 characters each, 12000 in all. The brief
# is part of what Cursor is sent, so a story with many long neighbours makes
# a longer prompt.
_KEY_IN_LINK_RX = re.compile(r"\b([A-Za-z][A-Za-z0-9_]+-[0-9]+)\b")


def _plain_line(value, cap: int = 400) -> str:
    """One line, control characters out, everything else kept: for a reason
    that names an address a person must be able to read. It is only ever
    written indented under a list item, where it cannot become structure."""
    s = re.sub(r"[\x00-\x1f\x7f\s]+", " ", str(value if value is not None else "")).strip()
    return s[:cap] + ("..." if len(s) > cap else "")


def _one_line(value, cap: int = 200) -> str:
    """Text from Jira as one short line that cannot be read as structure."""
    s = re.sub(r"[`<>|*#\[\]]", "", str(value if value is not None else ""))
    s = re.sub(r"[\x00-\x1f\x7f\s]+", " ", s).strip()
    return s[:cap] + ("..." if len(s) > cap else "")


def _story_text(issue: dict) -> str:
    """What one issue says: its summary, its description, its comments."""
    f = issue.get("fields") if isinstance(issue.get("fields"), dict) else {}
    parts = [f"{issue.get('key', '')}: {jira_fetch._text_of(f.get('summary'))}",
             jira_fetch._text_of(f.get("description"))]
    held = f.get("comment") if isinstance(f.get("comment"), dict) else {}
    for c in _listed(held.get("comments")):
        parts.append(jira_fetch._text_of(c.get("body") if isinstance(c, dict) else c))
    return _unwiki("\n\n".join(p.strip() for p in parts if p and p.strip()))


def _about(issue: dict) -> str:
    """What an issue is about: its description, without its comments."""
    return _unwiki(jira_fetch._text_of((issue.get("fields") or {}).get("description"))).strip()


def _listed(value) -> list:
    return value if isinstance(value, list) else []


def _named(value) -> str:
    """The name of a Jira field that is usually {"name": ...}."""
    return value.get("name") if isinstance(value, dict) else (value if isinstance(value, str)
                                                              else "")


def _links_of(issue: dict) -> list:
    """[(key, how it is related)] for the stories Jira links to this one,
    and its sub-tasks, in Jira's order."""
    f = issue.get("fields") if isinstance(issue.get("fields"), dict) else {}
    out = []
    for link in _listed(f.get("issuelinks")):
        if not isinstance(link, dict):
            continue
        kind = link.get("type") if isinstance(link.get("type"), dict) else {}
        if isinstance(link.get("outwardIssue"), dict):
            other, how = link["outwardIssue"], kind.get("outward") or kind.get("name")
        elif isinstance(link.get("inwardIssue"), dict):
            other, how = link["inwardIssue"], kind.get("inward") or kind.get("name")
        else:
            continue
        out.append((str(other.get("key") or ""), _one_line(how or "linked to", 40)))
    for sub in _listed(f.get("subtasks")):
        if isinstance(sub, dict):
            out.append((str(sub.get("key") or ""), "sub-task of this story"))
    seen, keep = set(), []
    for key, how in out:
        key = key.upper()
        if _KEY_IN_LINK_RX.fullmatch(key) and key not in seen:
            seen.add(key)
            keep.append((key, how))
    return keep


def _around(issue: dict, how: str) -> dict:
    """One story near this job's story, as the brief will show it: whole."""
    f = issue.get("fields") or {}
    text = conf_fetch.redact(_about(issue))
    return {"key": _one_line(issue.get("key"), 40), "how": how, "ok": True,
            "type": _one_line(_named(f.get("issuetype")), 40),
            "status": _one_line(_named(f.get("status")), 40),
            "summary": _one_line(conf_fetch.redact(jira_fetch._text_of(f.get("summary"))), 300),
            "text": text, "cut": 0}


_TOKEN_REFUSED_RX = re.compile(r"\b(401|403)\b|unauthori[sz]ed|forbidden|anonymous", re.I)


def token_verdict(conn: dict, transport=None) -> str:
    """What Jira says of the configured token, one line; '' when it could
    not be asked. Only the real transport is used: a reader's stand-in
    answers for issues, not for /myself.

    "not accepted: ..." only when Jira said so (401, 403, or an anonymous
    answer). Jira down, a 5xx or a 429 is "could not be checked: ...": the
    token is not to blame for those."""
    if transport is not None:
        return ""
    try:
        accepted, said = jira_search.verify(conn)
    except Exception as e:                               # noqa: BLE001
        diag.trace()
        return f"could not be checked ({type(e).__name__})"
    if accepted:
        return _one_line(said, 300)
    if _TOKEN_REFUSED_RX.search(str(said)):
        return _one_line(f"not accepted: {said}", 300)
    return _one_line(f"could not be checked: {said}", 300)


def gather_jira(link: str, sources: str, slot: str, connect=None, transport=None,
                attachments=None) -> dict:
    """Read ONE story from the configured Jira into sources/<slot>/.

    The story, every comment, its parents (acceptance criteria are often
    one level up) and its attachments -- through tools/jira/fetch.py,
    which is the reader the story chain already uses.

    AND WHAT IS AROUND IT: the stories Jira links to it (blocks, relates
    to, is cloned by...) and its sub-tasks, one read each, not followed
    further. They are there for the bigger picture a story read alone
    does not give. They are CONTEXT: their text is shown beside the story
    and is never searched for requests or acceptance criteria, because a
    request in a linked story is that story's to test.

    WHICH JIRA is never the link's to say. The request goes to the base
    url in jira_config and nowhere else; a link that names another host is
    refused rather than read from the configured one, because the person
    who pasted it meant a different story than the one that would come
    back. The token is a header and is written nowhere.

    Returns what it read, or why it did not: {link, kind, ok, reason?,
    key?, dir?, text_file?, story?}.
    """
    out = {"link": link, "kind": JIRA, "ok": False}
    try:
        keys = [str(k).upper() for k in (jira_query.parse(link).get("keys") or [])]
    except Exception:                                    # noqa: BLE001
        diag.trace()
        keys = []
    if not keys:
        keys = [m.upper() for m in _KEY_IN_LINK_RX.findall(link)][:1]
    if not keys:
        out["reason"] = "no story key (like ABC-123) could be read from this"
        return out
    key = keys[0]
    out["key"] = key
    if len(keys) > 1:
        out["also"] = keys[1:6]
    try:
        conn = (connect or jira_search.connect)()
    except jira_search.Refused as e:
        out["reason"] = str(e)
        return out
    mocked = bool(conn.get("mock"))
    if "://" in link and not mocked:
        # An address is only read when it is the address of a story on THE
        # Jira that will be asked -- the first approved base, as everywhere
        # else. One that cannot be parsed is refused, not waved through,
        # and so is one on a second approved host: the answer would come
        # from the first, and be a different story.
        try:
            _key, named = jira_fetch.normalize_issue_key(link.strip())
        except ValueError as e:
            out["reason"] = _one_line(f"this is not the address of a Jira story: {e}", 300)
            return out
        allowed, why = jira_fetch.host_allowed(named, [conn["base"]])
        if not allowed or not named:
            out["reason"] = (why if not allowed else
                             "no host could be read from this address")
            return out
    if mocked and transport is None:
        transport = lambda url, token, timeout: jira_search.mock().story_transport(url)
    diag.step(f"Reading the sample story {key} (mock mode)" if mocked
              else f"Asking Jira for the story {key}, its comments and its parents")
    chain = jira_fetch.fetch_chain(key, conn["base"], conn["token"], conn["api"],
                                   conn["timeout"], transport)
    chain = [i for i in chain if isinstance(i, dict)]
    if not chain or chain[0].get("_error"):
        why = chain[0].get("_error") if chain else "nothing came back"
        out["reason"] = _one_line(conf_fetch.redact(f"{key} could not be read: {why}"), 400)
        # An expired or wrong token comes back from Jira as "no such
        # story", which sends a person after the wrong mistake. When the
        # story cannot be read, the token is asked about, and what Jira
        # says of it goes first. (The page had a button for this.)
        if not mocked:
            diag.step("The story could not be read; checking the Jira token")
            out["token"] = token_verdict(conn, transport)
            if out["token"].startswith("not accepted"):
                out["reason"] = _one_line(conf_fetch.redact(
                    f"the Jira token was not accepted ({out['token']}): put a current one in "
                    f"program_configuration.json (jira_config) and read again. ({out['reason']})"), 500)
            elif out["token"].startswith("could not be checked"):
                out["reason"] = _one_line(conf_fetch.redact(
                    f"{out['reason']} (the Jira token {out['token']})"), 500)
        return out
    read = [i for i in chain if not i.get("_error")]
    outdir = os.path.join(sources, slot)
    os.makedirs(outdir, exist_ok=True)

    saved = []
    if attachments is None and not mocked:
        attachments = lambda issue, where: jira_fetch.download_attachments(
            issue, where, conn["token"], conn.get("bases") or [conn["base"]], conn["timeout"])
    if attachments is not None:
        diag.step("Downloading the story's attachments")
        for n, issue in enumerate(read):
            # The key is Jira's ANSWER. It names a folder only when it is a
            # key: `../../x` or an absolute path would write outside the job.
            answered = str(issue.get("key") or "").upper()
            folder = answered if _KEY_IN_LINK_RX.fullmatch(answered) else f"issue{n}"
            try:
                saved += attachments(issue, os.path.join(outdir, "attachments", folder)) or []
            except Exception as e:                       # noqa: BLE001
                diag.trace()
                out.setdefault("warnings", []).append(
                    f"the attachments of {folder} could not be read ({type(e).__name__})")

    fields = []
    if not mocked:
        cfg, _note = projectconfig.section("jira_config")
        fields = (cfg or {}).get("acceptance_criteria_fields") or []
    ac = jira_fetch.acceptance_criteria_chain(chain, fields)
    own_fields = read[0].get("fields") or {}

    # ---- what is around the story ---------------------------------------
    around = []
    for depth, issue in enumerate(read[1:], 1):
        around.append(_around(issue, "parent" if depth == 1 else "parent of the parent"))
    linked = [(k, how) for k, how in _links_of(read[0])
              if k not in {str(i.get("key") or "").upper() for i in chain}]
    if linked:
        diag.step(f"Reading {len(linked)} linked stor{'y' if len(linked) == 1 else 'ies'} "
                  f"for the bigger picture")
    for other, how in linked:
        try:
            issue = jira_fetch.fetch_issue(other, conn["base"], conn["token"], conn["api"],
                                           conn["timeout"], transport)
        except RuntimeError as e:
            around.append({"key": other, "how": how, "ok": False,
                           "reason": _one_line(conf_fetch.redact(str(e)), 200)})
            continue
        except Exception as e:                           # noqa: BLE001
            diag.trace()
            around.append({"key": other, "how": how, "ok": False,
                           "reason": f"{type(e).__name__}: Jira's answer could not be read"})
            continue
        if not isinstance(issue, dict) or not isinstance(issue.get("fields"), dict):
            around.append({"key": other, "how": how, "ok": False,
                           "reason": "Jira's answer was not an issue"})
            continue
        issue["key"] = other                             # the key that was ASKED for
        around.append(_around(issue, how))
    not_read = []                                        # every linked story is read

    # Requests are looked for in the story and its parents (a contract kept
    # on the epic is the commonest place one lives) -- not in linked stories.
    own = conf_fetch.redact(_story_text(read[0]))
    text = "\n\n".join(t for t in (_story_text(i) for i in read) if t)
    # Sample payloads attached as files are as likely to hold the request
    # as the description is.
    extra = []
    for a in saved:
        path = a.get("saved_to") or ""
        if path.lower().endswith(jira_fetch.TEXTUAL) and os.path.isfile(path):
            try:
                with io.open(path, encoding="utf-8", errors="replace") as fh:
                    extra.append((str(a.get("name") or "attachment"),
                                  fh.read(MAX_ATTACHMENT_TEXT)))
            except OSError:
                continue
    text = conf_fetch.redact(text)
    with io.open(os.path.join(outdir, "story.md"), "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
    with io.open(os.path.join(outdir, "around.json"), "w", encoding="utf-8") as fh:
        json.dump(around, fh, indent=2, ensure_ascii=False)
    comments = (read[0].get("fields") or {}).get("comment")
    comments = comments if isinstance(comments, dict) else {}
    out.update({
        "ok": True, "dir": slot, "text_file": os.path.join(slot, "story.md"),
        "mock": mocked,
        "story": {
            "key": key,
            "type": _one_line(_named(own_fields.get("issuetype")), 40),
            "status": _one_line(_named(own_fields.get("status")), 40),
            "summary": _one_line(conf_fetch.redact(
                jira_fetch._text_of(own_fields.get("summary"))), 300),
            "parents": [_one_line(i.get("key"), 40) for i in chain[1:]],
            "unread_parents": [_one_line(i.get("key"), 40) for i in chain[1:]
                               if i.get("_error")],
            "comments": len(_listed(comments.get("comments"))),
            "comments_note": _one_line(comments.get("_error") or "", 200),
            "attachments": [{"name": _one_line(a.get("name"), 120),
                             "status": _one_line(a.get("status"), 60)} for a in saved[:20]],
            "acceptance_criteria": {
                "present": bool(ac.get("present")),
                "confidence": _one_line(ac.get("confidence"), 40),
                "found_on": _one_line(ac.get("found_on"), 40),
                "reasons": [_one_line(r, 200) for r in (ac.get("reasons") or [])[:5]],
            },
            "chars": len(text),
            "linked_not_read": not_read[:20],
            # Jira's own id of this version of the story, so that a later
            # check can tell whether it changed (storycheck.py).
            # `updated|hash`, kept as is: _one_line would strip the bar.
            "revision": str(jira_fetch.story_revision(read[0]) or "")[:80],
        },
        "own_text": own,
        "around": around,
        # The STORY's text is searched for requests. A parent's is too, but
        # apart, and only to be named: an epic lists the calls of every
        # story under it, and taking them all would plan this job tests
        # that belong to its siblings.
        "_request_texts": [(key, conf_fetch.redact(_story_text(read[0])))],
        "_parent_texts": [(_one_line(i.get("key"), 40), conf_fetch.redact(_story_text(i)))
                          for i in read[1:]],
        "_extra_texts": [(name, conf_fetch.redact(body)) for name, body in extra],
    })
    return out


def build(job: str, text: str = "", links=None, root: str = "",
          api_path: str = "", pages_text: str = "") -> dict:
    """Run the intake. Returns the structured result it also writes.

    `pages_text` is a page somebody pasted -- a Confluence page the
    fetcher refused, a table from a document. It is read the way a
    fetched page is: for requests, and into the brief under the pages.
    """
    links = [l for l in (links or []) if (l or "").strip()]
    out = job_dir(job, root)
    sources = os.path.join(out, "sources")
    os.makedirs(sources, exist_ok=True)

    found, notes = [], []
    story, story_body, around = None, "", []
    pages, page_budget = [], MAX_PAGES_TOTAL
    found += _requests_from(text, "pasted text")

    gathered = []
    for n, link in enumerate(links, 1):
        kind = classify(link)
        if kind == CONFLUENCE:
            got = gather_confluence(link, sources, f"{n:02d}-confluence",
                                    api_path)
            gathered.append(got)
            if got.get("ok") and got.get("text_file"):
                with io.open(os.path.join(sources, got["text_file"]),
                             encoding="utf-8", errors="replace") as fh:
                    body = fh.read()
                found += _requests_from(body, f"confluence {link}")
                shown, cut = _page_for_brief(body, page_budget)
                page_budget -= len(shown)
                pages.append({"link": link, "text": shown, "cut": cut})
            elif not got.get("ok"):
                notes.append(f"{link}: {got.get('reason')}")
        elif kind == JIRA:
            # The story is READ here -- its text, comments, parents and
            # attachments -- so that a person can give a link instead of
            # pasting. Nothing is decided from it here: tools/jira/run.py
            # remains the gated chain that turns a story into a test.
            if story is not None:
                gathered.append({"link": link, "kind": JIRA, "ok": False,
                                 "reason": f"one story at a time: {story['key']} is the "
                                           f"story of this job"})
                notes.append(f"{link}: not read -- one story at a time, and "
                             f"{story['key']} was given first")
                continue
            try:
                got = gather_jira(link, sources, f"{n:02d}-jira")
            except Exception as e:                       # noqa: BLE001
                diag.trace()
                # Whatever Jira answered, the pasted text and the other
                # links are still read: one source must not lose the job.
                got = {"link": link, "kind": JIRA, "ok": False,
                       "reason": f"the story could not be read ({type(e).__name__}); "
                                 f"the Logs tab has the detail"}
            extra_texts = got.pop("_extra_texts", [])
            gathered.append(got)
            if not got.get("ok"):
                for private in ("_request_texts", "_parent_texts", "own_text", "around"):
                    got.pop(private, None)
                notes.append(f"{link}: {got.get('reason')}")
                continue
            story = got["story"]
            around = got.pop("around", [])
            story_body = got.pop("own_text", "")
            # The Confluence pages the story itself points at are read too,
            # unasked: "Table updated to reflect such - [url]" is the page the
            # tests depend on. The fetcher's allowlist still decides whether
            # a host is read at all.
            for k, extra in enumerate(confluence_links_in(story_body, links), 1):
                # Its own slot: the given links take `NN-confluence` by their
                # position, and a page found in the story must not share one.
                slot = f"{n:02d}s{k}-confluence"
                page = gather_confluence(extra, sources, slot, api_path)
                page["from_story"] = story["key"]
                gathered.append(page)
                if page.get("ok") and page.get("text_file"):
                    with io.open(os.path.join(sources, page["text_file"]), encoding="utf-8",
                                 errors="replace") as fh:
                        body = fh.read()
                    found += _requests_from(body, f"confluence {extra} (named in {story['key']})")
                    shown, cut = _page_for_brief(body, page_budget)
                    page_budget -= len(shown)
                    pages.append({"link": extra, "text": shown, "cut": cut,
                                  "from_story": story["key"]})
                else:
                    notes.append(f"{extra} (a link in {story['key']}): {page.get('reason')}")
            for issue_key, issue_text in got.pop("_request_texts", []):
                found += _requests_from(issue_text, f"jira {_one_line(issue_key, 40)}")
            for issue_key, issue_text in got.pop("_parent_texts", []):
                theirs = [f"{s.get('verb')} {_one_line(s.get('raw_path'), 120)}"
                          for s in _requests_from(issue_text, "parent")
                          if s.get("verb") and s.get("raw_path")]
                if theirs:
                    notes.append(f"{story['key']}: its parent {issue_key} holds "
                                 f"{len(theirs)} request(s) -- {', '.join(theirs[:5])}. They "
                                 f"are NOT taken as this story's (a parent lists the calls "
                                 f"of every story under it): paste the one that applies")
            for a in around:
                if not a.get("ok"):
                    notes.append(f"{story['key']}: {a['how']} {a['key']} could not be "
                                 f"read ({a.get('reason')})")
            for name, body in extra_texts:
                found += _requests_from(body, f"jira {story['key']} attachment "
                                              f"{_one_line(name, 80)}")
            for w in got.get("warnings") or []:
                notes.append(f"{story['key']}: {w}")
            if got.get("also"):
                notes.append(f"{link}: several stories were named; only {story['key']} "
                             f"was read ({', '.join(got['also'])} were not)")
            if story["unread_parents"]:
                notes.append(f"{story['key']}: its parent "
                             f"{', '.join(story['unread_parents'])} could not be read")
            if story["comments_note"]:
                notes.append(f"{story['key']}: {story['comments_note']}")
            if not story["acceptance_criteria"]["present"]:
                notes.append(f"{story['key']}: NO ACCEPTANCE CRITERIA were found on the "
                             f"story or its parents (looked for a heading, Given/When/"
                             f"Then, or a list). What should be tested is then a guess: "
                             f"read the story before trusting a design made from it")
        elif kind == JIRA_QUERY:
            # A query names many stories. This stage reads one request to
            # automate; which of a release's stories that is, is a
            # person's choice, made from the list.
            gathered.append({"link": link, "kind": JIRA_QUERY, "ok": False,
                             "reason": "a Jira query, not a story: list what it "
                                       "holds with `python tools/jira/search.py "
                                       "paste`, then give the story here"})
            notes.append(f"{link[:80]}: a Jira query -- list it with "
                         f"tools/jira/search.py paste, then paste the story")
        else:
            gathered.append({"link": link, "kind": UNKNOWN, "ok": False,
                             "reason": "not a Jira key or a Confluence page link"})
            notes.append(f"{link}: not recognised as Jira or Confluence")

    if (pages_text or "").strip():
        pasted_page = conf_fetch.redact(_unwiki(pages_text))
        found += _requests_from(pasted_page, "pasted page text")
        # Its own room, whatever the fetched pages used: it was pasted
        # because it matters.
        shown, cut = _page_for_brief(pasted_page, MAX_PAGE_CHARS)
        pages.append({"link": "pasted on the Automate Test Case tab (a page the fetcher "
                              "could not read, or notes)", "text": shown, "cut": cut,
                      "pasted": True})

    # Extraction failures were being dropped here: a step with no verb
    # is filtered out, so a paste the extractor choked on vanished
    # without a word. Surface them before filtering.
    for step in found:
        if step.get("error"):
            notes.append(f"{step.get('provenance', 'a source')}: "
                         f"{step['error']}")

    usable = [s for s in found if s.get("verb") and s.get("raw_path")]

    # The same request often arrives twice -- pasted AND on the page it
    # was pasted from. Left in, Stage 3 proposes two tests for one
    # request. Keep the first, which carries the earliest provenance.
    seen, deduped = set(), []
    for step in usable:
        key = (step.get("verb"), step.get("raw_path"),
               tuple(step.get("query_param_names") or ()),
               (step.get("body") or "").strip())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(step)
    if len(deduped) != len(usable):
        notes.append(f"{len(usable) - len(deduped)} duplicate request(s) "
                     f"collapsed")
    usable = deduped

    # A request body can carry a credential -- a token-request payload
    # is literally a client id and secret. The Confluence fetcher
    # redacts what IT reads, but pasted text reaches here unredacted,
    # and intake.json feeds both the UI and the agent prompt.
    for step in usable:
        if step.get("body"):
            step["body"] = conf_fetch.redact(step["body"])
    result = {
        "job": job,
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pasted_chars": len(text or ""),
        "pasted_page_chars": len((pages_text or "").strip()),
        "links": gathered,
        "requests": usable,
        "notes": notes,
        "ok": bool(usable),
        # The story this job is about, when one was read from Jira.
        "story": story,
        "story_text": story_body[:MAX_STORY_CHARS],
        "story_text_cut": max(0, len(story_body) - MAX_STORY_CHARS),
        # Its parents, the stories linked to it and its sub-tasks.
        "around": around,
        # What the Confluence pages given with it say.
        "pages": pages,
    }
    diag.step("Writing the brief")
    _write_brief(out, result)
    # Written beside and moved into place: a reader (the story check, the
    # page) sees the old file or the new one, never a half of either.
    with io.open(os.path.join(out, "intake.json.tmp"), "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, ensure_ascii=False)
    os.replace(os.path.join(out, "intake.json.tmp"), os.path.join(out, "intake.json"))
    return result


def _write_brief(out: str, r: dict) -> None:
    lines = []
    story = r.get("story")
    if not r["ok"] and story:
        lines += [f"# Intake: {story['key']} was read; it holds no request", "",
                  "The story's text is below, but nothing in it (or in what was "
                  "pasted) is a request this pipeline can act on: a curl command "
                  "or a `VERB /path` line. To go on from the story alone, use "
                  "**Design the API tests** and then locate: the designed "
                  "endpoints are checked instead.", ""]
    elif not r["ok"]:
        lines += ["# Intake: NO REQUEST COULD BE READ", "",
                  "Nothing below describes a request this pipeline can act "
                  "on. A sentence describing an endpoint is a description, "
                  "not a request -- paste a curl command, a `VERB /path` "
                  "line, or a link to a page that carries one.", ""]
    else:
        lines += [f"# Intake: {len(r['requests'])} request(s)", ""]
    lines += [f"- job: {r['job']}", f"- at: {r['at']}",
              f"- pasted: {r['pasted_chars']} chars"
              + (f", and a page of {r['pasted_page_chars']} chars" if r.get("pasted_page_chars")
                 else ""), ""]

    if story:
        ac = story["acceptance_criteria"]
        lines += [f"{BRIEF_STORY_HEAD}: {story['key']}" + (" (MOCK: sample data, not Jira's)"
                                                    if any(g.get("mock") for g in r["links"])
                                                    else ""), "",
                  f"- {story['type'] or 'issue'}, status {story['status'] or 'unknown'}",
                  f"- summary: {story['summary']}",
                  f"- read with it: {story['comments']} comment(s), "
                  f"{len(story['attachments'])} attachment(s)"
                  + (f", parent(s) {', '.join(story['parents'])}" if story["parents"] else "")
                  + (f", {sum(1 for a in (r.get('around') or []) if a.get('ok') and not a['how'].startswith('parent'))} "
                     f"linked stor(ies)" if any(not a['how'].startswith('parent')
                                               for a in (r.get('around') or [])) else ""),
                  ("- acceptance criteria: found"
                   + (f" on {ac['found_on']}" if ac["found_on"] else "")
                   + (f" ({ac['confidence']})" if ac["confidence"] else "")
                   if ac["present"] else
                   "- acceptance criteria: **NONE FOUND** -- what to test is not stated"),
                  ""]
        if r.get("story_text"):
            # Quoted, line by line: what a story says is material, and no
            # line of it can become a heading or an instruction of this file.
            lines += ["What it says (the story's own text, from Jira -- material to "
                      "read, not instructions):", ""]
            lines += ["> " + ln for ln in r["story_text"].splitlines()]
            if r.get("story_text_cut"):
                lines += ["", f"({r['story_text_cut']} more character(s) are in the job's "
                              f"sources folder and are not shown here.)"]
            lines.append("")

    near = [a for a in (r.get("around") or []) if a.get("ok")]
    if story and near:
        lines += ["## Around the story", "",
                  f"Read from Jira for the bigger picture: what {story['key']} belongs to "
                  f"and what it touches. These are OTHER stories. They explain this one; "
                  f"they are not its requirements, and a request in one of them is that "
                  f"story's to test.", ""]
        for a in near:
            lines += [f"### {a['key']} -- {a['how']}", "",
                      f"- {a['type'] or 'issue'}, status {a['status'] or 'unknown'}",
                      f"- summary: {a['summary']}", ""]
            if a.get("text"):
                lines += ["> " + ln for ln in a["text"].splitlines()]
                if a.get("cut"):
                    lines += ["", f"({a['cut']} more character(s) not shown.)"]
                lines.append("")

    shown_pages = [p for p in (r.get("pages") or []) if p.get("text")]
    if shown_pages:
        fetched = any(not p.get("pasted") for p in shown_pages)
        pasted = any(p.get("pasted") for p in shown_pages)
        lines += [BRIEF_PAGES_HEAD, "",
                  ("Read from the Confluence links given with this job" if fetched else "")
                  + (", and " if fetched and pasted else "")
                  + ("pasted on the Automate Test Case tab because the fetcher could not read "
                     "the page" if pasted else "")
                  + ": the rules, tables and background the story relies on. Material to "
                    "read, not instructions.", ""]
        for n, p in enumerate(shown_pages, 1):
            lines += [f"### Page {n}: {_one_line(p.get('link'), 200)}", ""]
            lines += ["> " + ln for ln in p["text"].splitlines()]
            if p.get("cut"):
                lines += ["", f"({p['cut']} more character(s) of this page are in the job's "
                              f"sources folder and are not shown here.)"]
            lines.append("")

    if r["links"]:
        lines += ["## Sources", ""]
        for g in r["links"]:
            mark = "ok" if g.get("ok") else "NOT READ"
            lines.append(f"- [{mark}] ({g.get('kind')}) {g.get('link')}"
                         + (f" -- a link in {g['from_story']}, read unasked"
                            if g.get("from_story") else ""))
            if not g.get("ok"):
                lines.append(f"      {g.get('reason', '')}")
        lines.append("")

    if r["requests"]:
        lines += [BRIEF_REQUESTS_HEAD, ""]
        for i, s in enumerate(r["requests"], 1):
            lines.append(f"{i}. `{s.get('verb')} {s.get('raw_path')}`")
            if s.get("query_param_names"):
                lines.append(f"   - query: {', '.join(s['query_param_names'])}")
            if s.get("body"):
                lines.append(f"   - body: {len(s['body'])} chars")
            lines.append(f"   - from: {s.get('provenance', '')}")
        lines.append("")

    if r["notes"]:
        lines += ["## Not used", ""] + [f"- {n}" for n in r["notes"]] + [""]

    lines += ["## Next", "",
              ("Stage 3 locates these against the existing tests and decides "
               "create / update / duplicate." if r["ok"] else
               "Design the API tests from this story, then locate." if story else
               "Nothing to locate. Add a readable request and run intake "
               "again.")]
    with io.open(os.path.join(out, "brief.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines).rstrip() + "\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--job", required=True, help="job id; names the directory")
    ap.add_argument("--text", default="", help="pasted story / payload text")
    ap.add_argument("--text-file", default="", help="read the text from a file")
    ap.add_argument("--pages-file", default="",
                    help="a file holding the text of a page somebody pasted (a Confluence "
                         "page the fetcher could not read); read like a fetched page")
    ap.add_argument("--pasted", action="store_true",
                    help="read what the page pasted for this job from the job's own files "
                         "(pasted.txt, pasted-pages.txt); it wins over --text-file. The page "
                         "uses this, never a path: a "
                         "path confined to the repository still reaches the private "
                         "configuration, and whatever is read here is quoted to Cursor")
    ap.add_argument("--link", action="append", default=[],
                    help="a Jira story (link or key) or a Confluence page link; "
                         "repeatable. One story a job")
    ap.add_argument("--api-path", default="")
    ap.add_argument("--root", default="")
    args = ap.parse_args(argv)

    try:
        safe_job(args.job)
    except ValueError as e:
        print(f"refused: {e}")
        return 2

    text = args.text
    if args.text_file:
        if not os.path.isfile(args.text_file):
            print(f"refused: no such file: {args.text_file}")
            return 2
        with io.open(args.text_file, encoding="utf-8",
                     errors="replace") as fh:
            text = fh.read()
    pages_text = ""
    if args.pasted:
        here = job_dir(args.job, args.root)
        for name, where in (("pasted.txt", "text"), ("pasted-pages.txt", "pages")):
            path = os.path.join(here, name)
            if os.path.isfile(path):
                with io.open(path, encoding="utf-8", errors="replace") as fh:
                    if where == "text":
                        text = fh.read()
                    else:
                        pages_text = fh.read()
    if args.pages_file:
        if not os.path.isfile(args.pages_file):
            print(f"refused: no such file: {args.pages_file}")
            return 2
        with io.open(args.pages_file, encoding="utf-8", errors="replace") as fh:
            pages_text = fh.read()
    if not text.strip() and not args.link and not pages_text.strip():
        print("refused: give --text, --text-file, --pasted (with something pasted) or at "
              "least one --link")
        return 2

    r = build(args.job, text=text, links=args.link, root=args.root,
              api_path=args.api_path, pages_text=pages_text)
    out = job_dir(args.job, args.root)
    print(f"{len(r['requests'])} request(s) read, "
          f"{sum(1 for g in r['links'] if g.get('ok'))}/{len(r['links'])} "
          f"source(s) fetched")
    for n in r["notes"]:
        print(f"  note: {n}")
    print(f"  {os.path.join(os.path.relpath(out, ROOT), 'brief.md')}")
    if r.get("story"):
        s = r["story"]
        print(f"  story {s['key']}: {s['summary'][:100]}")
        print("  acceptance criteria: "
              + ("found" if s["acceptance_criteria"]["present"] else "NONE FOUND"))
        near = [a for a in (r.get("around") or []) if a.get("ok")]
        if near:
            print("  read around it: " + ", ".join(f"{a['key']} ({a['how']})" for a in near))
    # "Is it already automated?" -- run now, not left to a button nobody may
    # press: without it Automate wrote a new test for a call a converted or
    # hand-written test already covers. With no request read (a prose
    # story), it is run after Design, on the designed endpoints.
    if r["requests"]:
        auto_locate(args.job, args.root)
    else:
        forget_locate(out)
    # A story that was read is a result, with or without a request in it:
    # the tests can be designed from it.
    return 0 if r["ok"] or r.get("story") else 1


LOCATE_RESULTS = ("locate.json", "plan.md")


def forget_locate(out: str) -> None:
    """An earlier reading's verdicts, gone: they were about requests this
    reading did not find, and would stop (or wave through) the wrong ones."""
    for name in LOCATE_RESULTS:
        try:
            os.remove(os.path.join(out, name))
        except OSError:
            pass


def auto_locate(job: str, root: str = "", run=None) -> int:
    """tools/agent/locate.py for this job, as the "Is it already automated?"
    button runs it, its words in this log. Its exit 1 is a request that
    STOPS (upstream, or a person decides) -- the stage working. Never the
    reason the read fails. Returns its exit code (-1: it could not run)."""
    out = job_dir(job, root)
    forget_locate(out)                       # this reading's verdicts, not the last one's
    if run is None and os.environ.get("WORKBENCH_NO_AUTO_LOCATE") == "1":
        return -1                            # a test of reading alone
    argv = [sys.executable, "-B", os.path.join(HERE, "locate.py"), "--job", job, "--rebuild-if-stale"]
    if root:
        argv += ["--root", root]
    print("==> Is it already automated? (run after every read; the shape index is built "
          "the first time, which can take minutes)")
    sys.stdout.flush()
    try:
        got = (run or (lambda a: subprocess.run(a, cwd=root or ROOT, stdout=subprocess.PIPE,
                                                stderr=subprocess.STDOUT, timeout=1800)))(argv)
        text = got.stdout.decode("utf-8", "replace") if isinstance(got.stdout, bytes) else (got.stdout or "")
        rc = got.returncode
    except (OSError, subprocess.SubprocessError) as e:
        print(f"  note: it could not be run ({type(e).__name__}: {e}); press \"Then: is it "
              "already automated?\" to run it")
        return -1
    for line in text.splitlines():
        print("  " + line)
    if rc == 1:
        print("  WARN a request stops here (UPSTREAM or CONFIRM): see plan.md -- Automate waits "
              "for it, or for \"go ahead anyway\"")
    elif rc not in (0,):
        print(f"  note: it ended with exit {rc}; press \"Then: is it already automated?\" to run it again")
    return rc


if __name__ == "__main__":
    sys.exit(diag.run("intake", main))
