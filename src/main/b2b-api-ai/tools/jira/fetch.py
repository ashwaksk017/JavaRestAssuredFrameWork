"""Fetch a Jira story, snapshot it, and refuse to proceed without AC.

    python tools/jira/fetch.py --url https://jira.example.com/browse/B2B-1234
    python tools/jira/fetch.py --key B2B-1234
    python tools/jira/fetch.py --url ... --json target/jira/B2B-1234/story.json

Read-only. Nothing here writes to Jira.

THE HOST ALLOWLIST IS THE POINT OF TAKING A URL AT ALL
------------------------------------------------------
A story URL arrives from a person, a ticket, a chat message. Fetching
whatever host it names would make this a request-forgery tool that
helpfully attaches your Jira token. So the host must appear in
`jira_config.base_urls` in program_configuration.json, which is gitignored
and is the only place the token lives. A URL that does not match is
refused and the key is NOT fetched from somewhere else instead.

The token goes in an Authorization header. Never in a URL, never in a log
line, never in the snapshot.

THE ACCEPTANCE-CRITERIA GATE
----------------------------
A story with no clear acceptance criteria is the single worst input to
test generation: every downstream step will invent the rule it needs --
an expiry window, a status code, a validation message -- and each
invention looks exactly like a requirement by the time it reaches a test.

So this reports `present: false` and the caller stops. The detection is a
HEURISTIC and says so: it looks for an explicit heading, Given/When/Then,
or a list of two or more concrete statements, and it always prints the
evidence it matched. A reader can disagree with it in one glance, which
is the most that can honestly be claimed for a text rule.
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
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import projectconfig  # noqa: E402

ROOT = projectconfig.ROOT
DEFAULT_API_PATH = "/rest/api/2"

# Progress reporting. OFF by default so importing this module stays silent
# -- the 191 tests inject their own transport and would otherwise print a
# line per call -- and turned on by the CLI, where a run that fetches a
# three-deep parent chain and a handful of attachments at a 30s timeout
# can otherwise sit mute for a minute with nothing to say whether it is
# talking to Jira, waiting, or stuck.
VERBOSE = False


def say(msg: str) -> None:
    """One progress line, flushed.

    `flush=True` is the point: buffered progress arrives in a block after
    the work finishes, which is the same as having none.
    """
    if VERBOSE:
        print(f"[jira] {msg}", flush=True)


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB"):
        if n < 1024 or unit == "MB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.0f} B"

_KEY_RX = re.compile(r"\b([A-Z][A-Z0-9_]+-\d+)\b")
_BROWSE_RX = re.compile(r"/browse/([A-Z][A-Z0-9_]+-\d+)", re.I)

# Headings that name acceptance criteria outright.
_AC_HEADING_RX = re.compile(
    r"(?im)^\s*(?:h[1-6]\.\s*)?(?:\*{0,2}|#{0,6}\s*)"
    r"(acceptance\s+criteria|acceptance\s+criterion|AC)\b\s*:?\s*\*{0,2}\s*$")
_GWT_RX = re.compile(r"(?im)^\s*[-*#\d.)\s]*\b(given|when|then)\b")
_LIST_RX = re.compile(r"(?m)^\s*(?:[-*+]|\d+[.)])\s+(\S.*)$")


def normalize_issue_key(value: str) -> tuple[str, str]:
    """(ISSUE-KEY, base url or '') from a URL or a bare key.

    The base url is returned so the caller can check it against the
    allowlist. A key given on its own carries no host, so it is fetched
    from the configured base url -- which is the safe direction.
    """
    v = (value or "").strip()
    if not v:
        raise ValueError("no issue key or URL given")
    if "://" in v:
        u = urlparse(v)
        m = _BROWSE_RX.search(u.path) or _KEY_RX.search(u.path.upper())
        if not m:
            m = _KEY_RX.search((u.query or "").upper())
        if not m:
            raise ValueError(
                f"no issue key in {v!r}. Expected something like "
                f"https://<host>/browse/ABC-123")
        return m.group(1).upper(), f"{u.scheme}://{u.netloc}"
    m = _KEY_RX.fullmatch(v.upper())
    if not m:
        raise ValueError(f"{v!r} is not an issue key like ABC-123")
    return m.group(1), ""


def host_allowed(base_url: str, allowlist: list) -> tuple[bool, str]:
    """Exact scheme+host match against the configured base urls."""
    if not allowlist:
        return False, ("jira_config.base_urls is empty. Add the Jira base "
                       "URL there before fetching anything -- an empty "
                       "allowlist means every host is unapproved, not that "
                       "every host is fine")
    if not base_url:
        return True, "no host in the input; the configured base url is used"
    want = urlparse(base_url)
    for allowed in allowlist:
        a = urlparse(str(allowed).strip())
        if a.scheme == want.scheme and a.netloc.lower() == want.netloc.lower():
            return True, f"host matches an approved base url"
    return False, (f"{base_url} is not in jira_config.base_urls. Refusing to "
                   f"fetch: a story URL is untrusted input, and following it "
                   f"to an unapproved host would send the Jira token there")


# --- acceptance criteria ----------------------------------------------
def _text_of(value) -> str:
    """Jira fields are a string, or ADF, or null."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):          # Atlassian Document Format
        out = []
        if value.get("text"):
            out.append(value["text"])
        for child in value.get("content") or []:
            out.append(_text_of(child))
        return "\n".join(p for p in out if p)
    if isinstance(value, list):
        return "\n".join(_text_of(v) for v in value)
    return str(value)


def acceptance_criteria(issue: dict, ac_fields: list | None = None) -> dict:
    """Is there acceptance criteria, where, and what matched.

    Conservative on purpose. `weak` is not `present`: the caller stops on
    anything short of explicit, because the cost of guessing a rule is a
    test that encodes an invented requirement.
    """
    fields = (issue or {}).get("fields") or {}
    candidates: list[tuple[str, str]] = []
    for name in (ac_fields or []):
        t = _text_of(fields.get(name))
        if t.strip():
            candidates.append((name, t))
    desc = _text_of(fields.get("description"))
    if desc.strip():
        candidates.append(("description", desc))

    if not candidates:
        return {"present": False, "confidence": "absent", "source": "",
                "evidence": "",
                "reasons": ["no configured acceptance-criteria field carried "
                            "text, and the description is empty"]}

    best = None
    for source, text in candidates:
        reasons, evidence = [], ""
        heading = _AC_HEADING_RX.search(text)
        if heading:
            after = text[heading.end():]
            items = _LIST_RX.findall(after)
            reasons.append(f"an explicit `{heading.group(1).strip()}` heading")
            if len(items) >= 2:
                reasons.append(f"{len(items)} list items under it")
                evidence = "\n".join("  - " + i[:110] for i in items[:6])
            else:
                evidence = after.strip()[:300]
        gwt = len(set(m.group(1).lower() for m in _GWT_RX.finditer(text)))
        if gwt >= 2:
            reasons.append(f"Given/When/Then ({gwt} of 3 present)")
            if not evidence:
                evidence = "\n".join(
                    l for l in text.splitlines() if _GWT_RX.match(l))[:300]
        items_anywhere = _LIST_RX.findall(text)
        if not reasons and len(items_anywhere) >= 2:
            reasons.append(f"{len(items_anywhere)} list items, but no heading "
                           f"naming them as acceptance criteria")
            evidence = "\n".join("  - " + i[:110] for i in items_anywhere[:6])

        if heading and (len(items_anywhere) >= 2 or gwt >= 2):
            confidence = "explicit"
        elif heading or gwt >= 2:
            confidence = "explicit"
        elif reasons:
            confidence = "weak"
        else:
            confidence = "absent"
            reasons = ["no acceptance-criteria heading, no Given/When/Then, "
                       "and no list of statements"]
        # strip("\n"), not strip(): the list evidence prefixes every line
        # with "  - ", and a bare strip() removed it from the first line
        # only, so the quoted criteria rendered ragged.
        row = {"present": confidence == "explicit", "confidence": confidence,
               "source": source, "evidence": evidence.strip("\n").rstrip(),
               "reasons": reasons}
        if best is None or (row["present"] and not best["present"]):
            best = row
    return best


# --- fetching ----------------------------------------------------------
def _urllib_transport(url: str, token: str, timeout: int) -> dict:
    import time
    import urllib.error
    import urllib.request
    req = urllib.request.Request(url, method="GET")
    req.add_header("Accept", "application/json")
    if token:
        # Header, never the URL: a URL reaches proxies, logs and history.
        req.add_header("Authorization", f"Bearer {token}")
    # The URL is safe to print -- the token is a header, and `host_allowed`
    # has already approved the host. Printed BEFORE the call so a hang has
    # a visible cause instead of looking like the tool doing nothing.
    say(f"GET  {url}")
    say(f"     auth: {'Bearer token (header)' if token else 'NONE'}"
        f"   timeout: {timeout}s")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            ms = int((time.time() - t0) * 1000)
            say(f"     {r.status} {r.reason}  {_human(len(raw))} in {ms} ms")
            return json.loads(raw.decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        ms = int((time.time() - t0) * 1000)
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")[:400]
        except Exception:
            pass
        say(f"     {e.code} {e.reason} after {ms} ms")
        raise RuntimeError(f"Jira returned {e.code} {e.reason}. {body}") from None
    except urllib.error.URLError as e:
        ms = int((time.time() - t0) * 1000)
        say(f"     unreachable after {ms} ms: {e.reason}")
        raise RuntimeError(f"could not reach Jira: {e.reason}") from None


def fetch_issue(key: str, base_url: str, token: str, api_path: str = DEFAULT_API_PATH,
                timeout: int = 30, transport=None) -> dict:
    # `fields=*all` is not padding. Without it Jira returns `*navigable`,
    # which on Cloud leaves out `comment` entirely -- and this tool reads
    # comments for sample payloads, so it would have searched a field it
    # was never sent and reported "no payload found" with a straight face.
    url = (f"{base_url.rstrip('/')}{api_path}/issue/{key}"
           f"?expand=changelog,renderedFields&fields=*all")
    fn = transport or _urllib_transport
    return fn(url, token, timeout)


# Jira inlines only a slice of the comments. A sample payload is as
# likely to be in comment 40 as comment 2, so the rest are paged in.
MAX_COMMENT_PAGES = 10


def fetch_comments(key: str, base_url: str, token: str,
                   api_path: str = DEFAULT_API_PATH, timeout: int = 30,
                   transport=None, max_pages: int = MAX_COMMENT_PAGES) -> list:
    """Every comment on an issue, paged. Bounded both ways.

    `max_pages` caps the work, and an empty page stops the walk: a server
    that never advanced `startAt` would otherwise be followed forever.
    """
    fn = transport or _urllib_transport
    out: list = []
    at = 0
    for _ in range(max_pages):
        url = (f"{base_url.rstrip('/')}{api_path}/issue/{key}/comment"
               f"?startAt={at}&maxResults=100")
        say(f"  comment page at offset {at}")
        page = fn(url, token, timeout) or {}
        batch = page.get("comments") or []
        if not batch:
            say("  empty page -- stopping rather than following a "
                "server that never advances")
            break
        out.extend(batch)
        at += len(batch)
        if at >= int(page.get("total") or at):
            break
    return out


def _top_up_comments(issue: dict, key: str, base_url: str, token: str,
                     api_path: str, timeout: int, transport) -> None:
    """Replace the inlined comment slice with the full list when it is short.

    A read failure is recorded on the field rather than raised: a missing
    comment page must not lose the story, but it must not pass for
    "there were no more comments" either.
    """
    f = issue.get("fields")
    if not isinstance(f, dict):
        return
    c = f.get("comment")
    if not isinstance(c, dict):
        return
    have = len(c.get("comments") or [])
    total = int(c.get("total") or have)
    if total <= have:
        say(f"  comments: {have} of {total} -- all inlined, no paging needed")
        return
    say(f"  comments: Jira inlined {have} of {total} -- paging for the rest "
        f"(a sample payload is as likely to be in the last one)")
    try:
        every = fetch_comments(key, base_url, token, api_path, timeout, transport)
    except (RuntimeError, OSError, ValueError) as e:
        c["_error"] = f"only {have} of {total} comments could be read: {e}"
        return
    if len(every) >= have:
        c["comments"] = every
        say(f"  comments: {len(every)} read")
    if len(every) < total:
        c["_error"] = (f"{len(every)} of {total} comments read; the rest are "
                       f"past the {MAX_COMMENT_PAGES}-page cap")


# --- the parent chain --------------------------------------------------
MAX_PARENT_DEPTH = 3

# Story-level AC often lives on the parent, and a sub-task frequently
# carries none at all. Walking up is what stops "this story has no
# acceptance criteria" from being wrong whenever the team put them one
# level above.
def parent_key(issue: dict) -> str:
    f = (issue or {}).get("fields") or {}
    p = f.get("parent") or {}
    return str(p.get("key") or "").strip()


def fetch_chain(key: str, base_url: str, token: str,
                api_path: str = DEFAULT_API_PATH, timeout: int = 30,
                transport=None, max_depth: int = MAX_PARENT_DEPTH) -> list:
    """[story, parent, grandparent...], stopping at the first gap.

    Bounded, and it refuses to revisit a key: a parent link that loops
    would otherwise fetch forever, and Jira does not promise it cannot
    loop.
    """
    chain, seen = [], set()
    cur = key
    while cur and cur not in seen and len(chain) < max_depth:
        seen.add(cur)
        say(f"reading {'story' if not chain else 'parent'} {cur}"
            f"  (depth {len(chain) + 1}/{max_depth})")
        try:
            issue = fetch_issue(cur, base_url, token, api_path, timeout, transport)
        except RuntimeError as e:
            say(f"  {cur} could not be read: {e}")
            chain.append({"key": cur, "_error": str(e)})
            break
        _top_up_comments(issue, cur, base_url, token, api_path, timeout, transport)
        chain.append(issue)
        nxt = parent_key(issue)
        if not nxt:
            say(f"  {cur} has no parent -- chain ends here")
        elif nxt in seen:
            say(f"  {cur} points back at {nxt}, already read -- refusing to loop")
        elif len(chain) >= max_depth:
            say(f"  stopping at depth {max_depth} (--max-parent-depth); "
                f"{nxt} not read")
        cur = nxt
    return chain


# --- attachments -------------------------------------------------------
# Parsed for payload candidates. Everything else is downloaded and
# labelled, never opened.
TEXTUAL = (".json", ".yaml", ".yml", ".xml", ".txt", ".csv", ".har", ".md")
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024


def download_attachments(issue: dict, outdir: str, token: str, allowlist: list,
                         timeout: int = 30, fetcher=None,
                         max_bytes: int = MAX_ATTACHMENT_BYTES) -> list:
    """Save each attachment; report the ones not saved and why.

    The content URL comes from the Jira RESPONSE, so it is as untrusted as
    the story URL was: it is checked against the same allowlist before the
    token is sent to it. An attachment is DATA -- nothing here opens,
    executes or follows anything inside one.
    """
    out: list = []
    used: set = set()
    f = (issue or {}).get("fields") or {}
    issue_key = str((issue or {}).get("key") or "")
    _atts = list(f.get("attachment") or [])
    if _atts:
        say(f"  {len(_atts)} attachment(s) on {issue_key or '?'} -> {outdir}")
    for _n, a in enumerate(_atts, 1):
        name = str(a.get("filename") or "attachment")
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", name)[:120] or "attachment"
        if safe in used:
            # Two attachments can share a filename -- a revised contract
            # re-uploaded under the same name is the usual case. Writing
            # both to one path would lose the first while reporting it
            # "saved", so the id disambiguates.
            safe = f"{a.get('id') or len(used)}_{safe}"
        used.add(safe)
        size = int(a.get("size") or 0)
        url = str(a.get("content") or "")
        row = {"name": name, "issue": issue_key, "size": size,
               "mime": a.get("mimeType", ""), "saved_to": "", "status": ""}
        ok, why = host_allowed(f"{urlparse(url).scheme}://{urlparse(url).netloc}"
                               if url else "", allowlist)
        if not url:
            say(f"    [{_n}/{len(_atts)}] {name}: no content url in the "
                f"Jira response -- nothing to fetch")
            row["status"] = "no content url in the Jira response"
        elif not ok:
            say(f"    [{_n}/{len(_atts)}] REFUSED {name}: {why}")
            row["status"] = f"refused: {why}"
        elif size > max_bytes:
            say(f"    [{_n}/{len(_atts)}] skipped {name}: {_human(size)} "
                f"is over the {_human(max_bytes)} cap")
            row["status"] = (f"skipped: {size} bytes is over the "
                             f"{max_bytes} byte cap")
        else:
            try:
                say(f"    [{_n}/{len(_atts)}] downloading {name} "
                    f"({_human(size)}) from an approved host")
                data = (fetcher or _download)(url, token, timeout)
                os.makedirs(outdir, exist_ok=True)
                path = os.path.join(outdir, safe)
                with open(path, "wb") as fh:
                    fh.write(data)
                row["saved_to"] = path
                row["status"] = "saved"
                say(f"    [{_n}/{len(_atts)}] saved {_human(len(data))} "
                    f"-> {path}")
            except Exception as e:                       # report, never raise
                say(f"    [{_n}/{len(_atts)}] FAILED {name}: "
                    f"{type(e).__name__}: {e}")
                row["status"] = f"failed: {type(e).__name__}: {e}"
        out.append(row)
    return out


def _download(url: str, token: str, timeout: int) -> bytes:
    import urllib.request
    req = urllib.request.Request(url, method="GET")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# --- sample request payloads -------------------------------------------
_FENCE_RX = re.compile(r"```[a-zA-Z0-9]*\s*\n(.*?)```", re.S)
_CURL_RX = re.compile(r"(?is)\bcurl\b[^\n]*(?:\\\s*\n[^\n]*)*")
_NOFORMAT_RX = re.compile(r"(?s)\{(?:code|noformat)[^}]*\}(.*?)\{(?:code|noformat)\}")


def payload_candidates(issues: list, attachments: list | None = None) -> list:
    """Sample request payloads found in the story, with provenance.

    Collection only -- it does not decide which is THE request. That is
    extract.py's job, and keeping them apart means a payload that was
    found can be shown even when nothing could be concluded from it.
    """
    found: list = []

    def add(source: str, kind: str, text: str):
        text = (text or "").strip()
        if not text:
            return
        parsed, note = None, ""
        try:
            parsed = json.loads(text)
            note = "parses as JSON"
        except ValueError:
            note = "not JSON"
        found.append({"source": source, "kind": kind, "parses": parsed is not None,
                      "note": note, "chars": len(text),
                      "preview": text[:400]})

    for issue in issues or []:
        key = issue.get("key", "?")
        f = issue.get("fields") or {}
        texts = [("description", _text_of(f.get("description")))]
        for c in ((f.get("comment") or {}).get("comments") or []):
            texts.append((f"comment by {((c.get('author') or {}).get('displayName') or '?')}",
                          _text_of(c.get("body"))))
        for where, text in texts:
            if not text:
                continue
            for m in _FENCE_RX.finditer(text):
                add(f"{key} {where}", "fenced block", m.group(1))
            for m in _NOFORMAT_RX.finditer(text):
                add(f"{key} {where}", "{code} block", m.group(1))
            for m in _CURL_RX.finditer(text):
                add(f"{key} {where}", "curl", m.group(0))

    for a in attachments or []:
        path = a.get("saved_to")
        if not path or not os.path.isfile(path):
            continue
        where = f"attachment {a['name']}" + (f" on {a['issue']}" if a.get("issue") else "")
        if not path.lower().endswith(TEXTUAL):
            found.append({"source": where, "kind": "binary",
                          "parses": False,
                          "note": "not a textual type; downloaded, not parsed",
                          "chars": a.get("size", 0), "preview": ""})
            continue
        try:
            with io.open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as e:
            found.append({"source": where, "kind": "unreadable",
                          "parses": False, "note": str(e), "chars": 0,
                          "preview": ""})
            continue
        add(where, "attachment", text)
    return found


def story_revision(issue: dict) -> str:
    """A stable id for THIS version of the story.

    Jira's own `updated` is the honest answer when present; a content hash
    is the fallback, so a snapshot can always be compared with a later one
    and 'the story changed under us' is answerable.
    """
    f = (issue or {}).get("fields") or {}
    updated = f.get("updated") or ""
    body = json.dumps(issue, sort_keys=True, ensure_ascii=False)
    h = hashlib.sha1(body.encode("utf-8", "replace")).hexdigest()[:12]
    return f"{updated}|{h}" if updated else h


def acceptance_criteria_chain(chain: list, ac_fields: list | None = None) -> dict:
    """AC from the story, else inherited from a parent, saying which.

    A sub-task often carries none and the story above it carries them all.
    Reporting "absent" without looking up would stop a run for a story
    whose criteria are one level away -- and inheriting SILENTLY would be
    worse, because a reader could not tell whose rule a test encodes. So
    it is inherited and the owner is named.
    """
    first = None
    for depth, issue in enumerate(chain or []):
        if issue.get("_error"):
            continue
        ac = acceptance_criteria(issue, ac_fields)
        ac = dict(ac, found_on=issue.get("key", ""), depth=depth)
        if depth == 0:
            first = ac
        if ac["present"]:
            if depth:
                ac["reasons"] = list(ac["reasons"]) + [
                    f"inherited from {ac['found_on']}, {depth} level(s) up -- "
                    f"the story itself states none"]
            return ac
    return first or {"present": False, "confidence": "absent", "source": "",
                     "evidence": "", "reasons": ["no issue could be read"],
                     "found_on": "", "depth": 0}


def summarise(issue: dict, ac: dict) -> dict:
    f = (issue or {}).get("fields") or {}
    att = [{"name": a.get("filename"), "size": a.get("size"),
            "mime": a.get("mimeType")} for a in (f.get("attachment") or [])]
    return {
        "issue_key": issue.get("key", ""),
        "summary": f.get("summary", ""),
        "status": ((f.get("status") or {}).get("name", "")),
        "issue_type": ((f.get("issuetype") or {}).get("name", "")),
        "parent": ((f.get("parent") or {}).get("key", "")),
        "story_revision": story_revision(issue),
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "attachments": att,
        "comment_count": len(((f.get("comment") or {}).get("comments")) or []),
        "acceptance_criteria": ac,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--url", help="a Jira browse URL")
    g.add_argument("--key", help="an issue key, e.g. B2B-1234")
    ap.add_argument("--api-path", default=DEFAULT_API_PATH,
                    help="Jira REST base (default %(default)s; Cloud uses "
                         "/rest/api/3)")
    ap.add_argument("--json", metavar="PATH",
                    help="write the snapshot (default target/jira/<KEY>/story.json)")
    ap.add_argument("--no-attachments", action="store_true",
                    help="do not download attachments (they are still listed)")
    ap.add_argument("--quiet", action="store_true",
                    help="suppress the per-request progress lines. The "
                         "summary is always printed")
    ap.add_argument("--max-parent-depth", type=int, default=MAX_PARENT_DEPTH,
                    help="how far up the parent chain to read (default "
                         "%(default)s). Acceptance criteria are often on the "
                         "parent, so 1 is rarely enough")
    args = ap.parse_args(argv)

    global VERBOSE
    VERBOSE = not args.quiet

    cfg, note = projectconfig.section("jira_config")
    print(f"config: {note}")
    if not cfg:
        print("  no jira_config -- add it to program_configuration.json. "
              "The shape is in program_configuration.example.json.")
        return 2
    print(f"  {projectconfig.redacted(cfg)}")

    try:
        key, from_url = normalize_issue_key(args.url or args.key)
    except ValueError as e:
        print(f"refused: {e}")
        return 2

    allow, why = host_allowed(from_url, cfg.get("base_urls") or [])
    print(f"host  : {why}")
    if not allow:
        return 2

    base = from_url or str((cfg.get("base_urls") or [""])[0]).strip()
    token = str(cfg.get("pat") or "").strip()
    if not token:
        print("refused: jira_config.pat is empty. The token belongs in "
              "program_configuration.json and nowhere else.")
        return 2
    timeout = int(str(cfg.get("timeout_seconds") or "").strip() or 30)

    host = base.split("//")[-1].split("/")[0]
    cloud = host.endswith(".atlassian.net")
    say("-" * 68)
    say(f"connecting to Jira at {base}")
    say(f"  deployment : {'Cloud (*.atlassian.net)' if cloud else 'Server / Data Centre (self-hosted host)'}")
    say(f"  api        : {args.api_path}"
        + ("" if cloud or args.api_path != "/rest/api/3"
           else "   (v3 is Cloud-only; Server/DC stops at v2)"))
    say(f"  auth       : personal access token from jira_config.pat "
        f"({len(str(cfg.get('pat') or ''))} chars), sent as a header")
    say(f"  host check : {why}")
    say(f"  reading    : {key}, plus up to {args.max_parent_depth - 1} "
        f"parent(s), every comment"
        + ("" if args.no_attachments else ", and attachments"))
    say("-" * 68)
    print(f"fetch : {key} from {base}{args.api_path}/issue/{key}")
    chain = fetch_chain(key, base, token, args.api_path, timeout,
                        max_depth=args.max_parent_depth)
    if not chain or chain[0].get("_error"):
        print(f"failed: {chain[0].get('_error') if chain else 'nothing returned'}")
        return 1
    issue = chain[0]
    if len(chain) > 1:
        print("parents: " + " -> ".join(
            f"{i.get('key','?')}"
            + (" (unreadable)" if i.get("_error") else
               f" [{((i.get('fields') or {}).get('issuetype') or {}).get('name','?')}]")
            for i in chain[1:]))

    outdir = os.path.join(ROOT, "target", "jira", key)
    attachments = []
    if not args.no_attachments:
        # The whole chain, not just the story: a request contract attached
        # to the parent epic is the commonest place one lives, and reading
        # only the story would miss it while reporting "0 attachments".
        for n, iss in enumerate(chain):
            if iss.get("_error"):
                continue
            k = iss.get("key") or f"issue{n}"
            attachments.extend(download_attachments(
                iss, os.path.join(outdir, "attachments", k), token,
                cfg.get("base_urls") or [], timeout))

    payloads = payload_candidates(chain, attachments)
    ac = acceptance_criteria_chain(
        chain, cfg.get("acceptance_criteria_fields") or [])
    summary = summarise(issue, ac)
    summary["parent_chain"] = [i.get("key", "") for i in chain[1:]]
    summary["attachments"] = attachments or summary["attachments"]
    summary["payload_candidates"] = payloads

    out = args.json or os.path.join(outdir, "story.json")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    payload = {"schema": 1, "summary": summary, "issue": issue,
               "parents": chain[1:]}
    io.open(out, "w", encoding="utf-8", newline="\n").write(
        json.dumps(payload, indent=1, ensure_ascii=False) + "\n")

    print(f"\n{key}  {summary['issue_type']}  [{summary['status']}]")
    print(f"  {summary['summary'][:110]}")
    print(f"  revision   : {summary['story_revision']}")
    print(f"  snapshot   : {out}")

    print(f"\nattachments: {len(attachments)}")
    for a in attachments:
        print(f"  {a['status']:<10} {(a.get('issue') or '?'):<12} "
              f"{a['name'][:46]:<46} {a['size']} bytes")
    if any(a["status"] != "saved" for a in attachments):
        print("  Anything not saved is reported, never skipped silently -- an "
              "unread attachment is a requirement nobody saw.")

    parsed = [p for p in payloads if p["parses"]]
    print(f"\nsample request payloads: {len(payloads)} candidate(s), "
          f"{len(parsed)} parse as JSON")
    for p in payloads[:8]:
        print(f"  [{'JSON' if p['parses'] else p['kind']:<14}] {p['source'][:46]:<46} "
              f"{p['chars']} chars")
    if payloads and not parsed:
        print("  None parsed. A request shape cannot be derived from these, so "
              "extract.py will need the story read by a person or an agent.")

    print(f"\nacceptance criteria: {ac['confidence'].upper()}"
          + (f"  (field: {ac['source']})" if ac.get("source") else "")
          + (f"  on {ac['found_on']}" if ac.get("found_on") else ""))
    for r in ac.get("reasons") or []:
        print(f"  - {r}")
    if ac.get("evidence"):
        print("  evidence:")
        for line in ac["evidence"].splitlines()[:8]:
            print(f"    {line}")

    if not ac["present"]:
        print("\nCLARIFICATION_REQUIRED -- stopping here.\n"
              "Nothing downstream may run: with no clear acceptance criteria "
              "every later step invents the rule it needs, and an invented "
              "rule is indistinguishable from a requirement once it is in a "
              "test. This is a text heuristic, so if you disagree with it, "
              "the evidence above is what it saw.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
