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
        row = {"present": confidence == "explicit", "confidence": confidence,
               "source": source, "evidence": evidence.strip(),
               "reasons": reasons}
        if best is None or (row["present"] and not best["present"]):
            best = row
    return best


# --- fetching ----------------------------------------------------------
def _urllib_transport(url: str, token: str, timeout: int) -> dict:
    import urllib.error
    import urllib.request
    req = urllib.request.Request(url, method="GET")
    req.add_header("Accept", "application/json")
    if token:
        # Header, never the URL: a URL reaches proxies, logs and history.
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")[:400]
        except Exception:
            pass
        raise RuntimeError(f"Jira returned {e.code} {e.reason}. {body}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"could not reach Jira: {e.reason}") from None


def fetch_issue(key: str, base_url: str, token: str, api_path: str = DEFAULT_API_PATH,
                timeout: int = 30, transport=None) -> dict:
    url = (f"{base_url.rstrip('/')}{api_path}/issue/{key}"
           f"?expand=changelog,renderedFields")
    fn = transport or _urllib_transport
    return fn(url, token, timeout)


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
    ap.add_argument("--raw", action="store_true",
                    help="also write the unmodified Jira response beside it")
    args = ap.parse_args(argv)

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

    print(f"fetch : {key} from {base}{args.api_path}/issue/{key}")
    try:
        issue = fetch_issue(key, base, token, args.api_path, timeout)
    except RuntimeError as e:
        print(f"failed: {e}")
        return 1

    ac = acceptance_criteria(issue, cfg.get("acceptance_criteria_fields") or [])
    summary = summarise(issue, ac)

    out = args.json or os.path.join(ROOT, "target", "jira", key, "story.json")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    payload = {"schema": 1, "summary": summary, "issue": issue}
    io.open(out, "w", encoding="utf-8", newline="\n").write(
        json.dumps(payload, indent=1, ensure_ascii=False) + "\n")

    print(f"\n{key}  {summary['issue_type']}  [{summary['status']}]")
    print(f"  {summary['summary'][:110]}")
    print(f"  revision   : {summary['story_revision']}")
    print(f"  attachments: {len(summary['attachments'])}"
          + (f"  ({', '.join(a['name'] or '?' for a in summary['attachments'][:4])})"
             if summary["attachments"] else ""))
    print(f"  snapshot   : {out}")

    print(f"\nacceptance criteria: {ac['confidence'].upper()}"
          + (f"  (field: {ac['source']})" if ac.get("source") else ""))
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
