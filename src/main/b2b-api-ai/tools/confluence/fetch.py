"""Fetch a Confluence page, snapshot it, and refuse what it cannot do.

    python tools/confluence/fetch.py --url https://wiki.example.com/x/AbCdEf
    python tools/confluence/fetch.py --id 12345 --out target/agent/job1/sources
    python tools/confluence/fetch.py --url <url> --children 1 --comments

The host must be in `confluence_config.base_urls` in
program_configuration.json, which is gitignored and is the only place
the token lives. A host that is not on that list is REFUSED and the page
is not fetched from somewhere else instead. The token goes in an
Authorization header -- never in a URL, never in a log.

WHAT IT REFUSES, AND WHAT IT DOES NOT
-------------------------------------
`tools/jira/fetch.py` refuses a story with no acceptance criteria,
because acceptance criteria are what make a story testable. A Confluence
page has no such field, so copying that rule would invent one. This
refuses only what it genuinely cannot do:

    * a link it cannot resolve to a page
    * a host that is not allowlisted
    * no token configured
    * a page whose body is empty

"No request could be read" is the NEXT stage's gate. Two stages refusing
the same thing is how a pipeline starts arguing with itself.

Server/DC is the default (`/rest/api/content/<id>`), matching the Jira
side of this project. Cloud's `/wiki/api/v2` differs; pass --api-path.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import pageref  # noqa: E402
import storage  # noqa: E402

# `tools/jira` also contains a module called `fetch`. Putting that
# directory on sys.path makes `import fetch` ambiguous -- it resolved to
# the Jira one here and inside the tests, which is the kind of breakage
# that only shows up as a confusing AttributeError. Load the one module
# that IS shared under an explicit name instead.
_JIRA_DIR = os.path.join(os.path.dirname(HERE), "jira")


def _load_module(name, path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise SystemExit(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


projectconfig = _load_module(
    "jira_projectconfig", os.path.join(_JIRA_DIR, "projectconfig.py"))

ROOT = projectconfig.ROOT
DEFAULT_API_PATH = "/rest/api/content"
DEFAULT_TIMEOUT = 30
MAX_CHILD_DEPTH = 3
MAX_CHILDREN = 50
MAX_TOTAL_CHILDREN = 100
MAX_COMMENTS = 50

VERBOSE = False


def say(msg: str) -> None:
    if VERBOSE:
        print(msg)


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB"):
        if n < 1024 or unit == "MB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}MB"


def host_allowed(base_url: str, allowlist: list) -> tuple[bool, str]:
    """Exact scheme+host match against the configured base urls.

    The same rule `tools/jira/fetch.py` applies, stated here rather than
    imported, because that module's refusal names `jira_config` and a
    Confluence user told to fix `jira_config.base_urls` will look in the
    wrong place. `test_fetch.py` asserts the two agree on the same
    inputs, so the duplication cannot drift silently.
    """
    if not allowlist:
        return False, ("confluence_config.base_urls is empty. Add the "
                       "Confluence base url there. An empty allowlist means "
                       "every host is unapproved, not that every host is "
                       "fine.")
    want = urllib.parse.urlparse(base_url)
    for allowed in allowlist:
        a = urllib.parse.urlparse(str(allowed))
        if a.scheme == want.scheme and a.netloc.lower() == want.netloc.lower():
            return True, ""
    return False, (f"{base_url} is not in confluence_config.base_urls. "
                   f"Refusing to fetch from an unapproved host.")


MAX_RESPONSE_BYTES = 20 * 1024 * 1024


class _SameHostRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse a redirect that leaves the approved host.

    urllib copies every header except content-length and content-type
    onto the redirected request, so the Authorization header -- the
    Confluence PAT -- follows a 302 ANYWHERE. A tiny link is resolved by
    following a redirect on purpose, so an open redirect on the wiki, or
    a wiki that proxies to another domain, would hand the token to
    whatever host answered. Refusing is right rather than merely
    stripping the header: a redirect off the approved host means the
    content would not have come from the approved host either.
    """

    def __init__(self, allowed_netloc: str):
        self.allowed = (allowed_netloc or "").lower()

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlparse(newurl).netloc.lower() != self.allowed:
            raise urllib.error.URLError(
                f"refusing a redirect to {urllib.parse.urlparse(newurl).netloc} "
                f"-- it leaves the approved host {self.allowed}, and the "
                f"Authorization header would travel with it")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _opener(base_url: str):
    return urllib.request.build_opener(
        _SameHostRedirect(urllib.parse.urlparse(base_url).netloc))


def _get(url: str, token: str, timeout: int) -> dict:
    """One GET, JSON back. The token only ever goes in the header."""
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/json")
    try:
        with _opener(url).open(req, timeout=timeout) as resp:
            raw = resp.read(MAX_RESPONSE_BYTES + 1).decode(
                "utf-8", errors="replace")
        if len(raw) > MAX_RESPONSE_BYTES:
            raise RuntimeError(
                f"Confluence returned more than {_human(MAX_RESPONSE_BYTES)}; "
                f"refusing to hold it in memory. Fetch a narrower page.")
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        raise RuntimeError(f"HTTP {e.code} from Confluence: {body}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"could not reach Confluence: {e.reason}") from e
    try:
        return json.loads(raw)
    except ValueError as e:
        raise RuntimeError(f"Confluence did not return JSON: {e}") from e


def resolve(ref, base_url: str, token: str, api_path: str,
            timeout: int) -> tuple[str, str]:
    """(page_id, reason). Performs the extra request a link may need."""
    if ref.kind == "id":
        return ref.page_id, ""
    if ref.kind == "title":
        q = urllib.parse.urlencode({"spaceKey": ref.space, "title": ref.title})
        data = _get(f"{base_url}{api_path}?{q}", token, timeout)
        results = (data or {}).get("results") or []
        if not results:
            return "", (f"no page titled {ref.title!r} in space "
                        f"{ref.space!r}. Titles are exact; check for a "
                        f"rename, or paste the /pages/viewpage.action link.")
        if len(results) > 1:
            # Ambiguity resolves to NOTHING, not to a guess. Two pages
            # can share a title across versions or archived copies, and
            # the wrong one reads as entirely plausible.
            ids = ", ".join(str(r.get("id")) for r in results[:5])
            return "", (f"{len(results)} pages titled {ref.title!r} in space "
                        f"{ref.space!r} (ids: {ids}). Paste the "
                        f"/pages/viewpage.action link for the one you mean.")
        return str(results[0].get("id") or ""), ""
    if ref.kind == "tiny":
        # The tiny link redirects to a real page URL; follow it and
        # re-parse. Same host, so the allowlist decision still holds.
        url = f"{base_url}/x/{ref.tiny}"
        req = urllib.request.Request(url, method="GET")
        req.add_header("Authorization", f"Bearer {token}")
        try:
            # Same opener as every other call: this is the ONE request
            # that follows a redirect by design, so it is the one that
            # most needs the cross-host refusal.
            with _opener(base_url).open(req, timeout=timeout) as resp:
                final = resp.geturl()
        except urllib.error.URLError as e:
            return "", f"could not follow the tiny link: {e}"
        again, why = pageref.parse(final)
        if again is None or again.kind == "tiny":
            return "", (why or "the tiny link did not resolve to a page URL")
        return resolve(again, base_url, token, api_path, timeout)
    return "", f"unsupported link kind {ref.kind!r}"


def fetch_page(page_id: str, base_url: str, token: str, api_path: str,
               timeout: int) -> dict:
    q = urllib.parse.urlencode({"expand": "body.storage,version,space,ancestors"})
    return _get(f"{base_url}{api_path}/{page_id}?{q}", token, timeout)


def fetch_children(page_id: str, base_url: str, token: str, api_path: str,
                   timeout: int, limit: int = MAX_CHILDREN) -> list:
    q = urllib.parse.urlencode({"expand": "body.storage,version", "limit": limit})
    url = f"{base_url}{api_path}/{page_id}/child/page?{q}"
    try:
        data = _get(url, token, timeout)
    except RuntimeError as e:
        say(f"  children unavailable: {e}")
        return []
    return (data or {}).get("results") or []


def fetch_comments(page_id: str, base_url: str, token: str, api_path: str,
                   timeout: int, limit: int = MAX_COMMENTS) -> list:
    q = urllib.parse.urlencode({"expand": "body.storage", "limit": limit,
                                "depth": "all"})
    url = f"{base_url}{api_path}/{page_id}/child/comment?{q}"
    try:
        data = _get(url, token, timeout)
    except RuntimeError as e:
        say(f"  comments unavailable: {e}")
        return []
    return (data or {}).get("results") or []


def _body_of(page: dict) -> str:
    return (((page or {}).get("body") or {}).get("storage") or {}).get("value") or ""


def page_revision(page: dict) -> str:
    """Enough to tell a later run that the page changed under it."""
    v = ((page or {}).get("version") or {})
    return f"v{v.get('number', '?')} {v.get('when', '')}".strip()


# Credential-shaped text that pages carry inline more often than anyone
# would like. Redacted on the way OUT, so the snapshot on disk, the UI
# and the agent prompt all see the same masked text.
_SECRET_RX = [
    re.compile(r"(?i)\b(authorization\s*:\s*)(bearer|basic)\s+\S+"),
    # The `"?` after the key name matters: in JSON -- which is exactly
    # how a Confluence code block carries one -- the key is quoted, so
    # `"client_secret": "..."` has a closing quote before the colon and
    # a pattern without it matches nothing at all.
    re.compile(r"(?i)\b(client[_-]?secret|password|passwd|api[_-]?key|"
               r"secret|token|pat)\b(\"?\s*[:=]\s*)(\"?)([^\s\"',;]{6,})"),
]


def redact(text: str) -> str:
    out = text or ""
    out = _SECRET_RX[0].sub(r"\1\2 <redacted>", out)
    out = _SECRET_RX[1].sub(r"\1\2\3<redacted>", out)
    return out


def render(page: dict, comments: list | None = None) -> str:
    """One readable markdown snapshot of a page."""
    title = page.get("title") or "(untitled)"
    space = ((page.get("space") or {}).get("key")) or ""
    body = storage.to_text(_body_of(page))
    parts = [f"# {title}", ""]
    meta = [f"- page id: {page.get('id', '')}", f"- revision: {page_revision(page)}"]
    if space:
        meta.append(f"- space: {space}")
    parts += meta + ["", body]
    for c in comments or []:
        ctext = storage.to_text(_body_of(c)).strip()
        if ctext:
            parts += ["", "---", "", "## comment", "", ctext]
    return redact("\n".join(parts).rstrip() + "\n")


def code_blocks(page: dict, comments: list | None = None) -> list:
    out = storage.extract_code_blocks(_body_of(page))
    for c in comments or []:
        out += storage.extract_code_blocks(_body_of(c))
    for b in out:
        b["text"] = redact(b.get("text", ""))
    return out


def _slug(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", (text or "page")).strip("-").lower()
    return (s or "page")[:60]


def main(argv=None) -> int:
    global VERBOSE
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default="", help="a Confluence page link")
    ap.add_argument("--id", default="", help="a page id, if you have it")
    ap.add_argument("--out", default="", help="directory for the snapshot")
    ap.add_argument("--api-path", default=DEFAULT_API_PATH)
    ap.add_argument("--children", type=int, default=0,
                    help=f"child depth to follow (0 = none, max {MAX_CHILD_DEPTH})")
    ap.add_argument("--comments", action="store_true",
                    help="include page comments; they often carry the contract")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    VERBOSE = args.verbose

    target = args.url or args.id
    if not target:
        print("refused: give --url or --id")
        return 2

    ref, why = pageref.parse(target)
    if ref is None:
        print(f"refused: {why}")
        return 1

    cfg, note = projectconfig.section("confluence_config")
    say(f"config: {note}")
    if not cfg:
        print("refused: no confluence_config in program_configuration.json. "
              "Add one shaped like jira_config: base_urls (a LIST), pat, "
              "timeout_seconds.")
        return 1
    say(f"  {projectconfig.redacted(cfg)}")

    token = (cfg.get("pat") or "").strip()
    if not token:
        print("refused: confluence_config.pat is empty. The token belongs in "
              "program_configuration.json and nowhere else.")
        return 1

    allowlist = cfg.get("base_urls") or []
    if isinstance(allowlist, str):
        print("refused: confluence_config.base_urls is a STRING. It must be a "
              "list -- a bare string iterates as characters and every host "
              "then fails the allowlist.")
        return 1

    base_url = ref.base_url or (allowlist[0] if allowlist else "")
    if not base_url:
        print("refused: a bare page id needs confluence_config.base_urls to "
              "say which Confluence to ask.")
        return 1

    ok, reason = host_allowed(base_url, allowlist)
    if not ok:
        print(f"refused: {reason}")
        return 1

    timeout = int(cfg.get("timeout_seconds") or DEFAULT_TIMEOUT)
    try:
        page_id, why = resolve(ref, base_url, token, args.api_path, timeout)
        if not page_id:
            print(f"refused: {why}")
            return 1
        say(f"page id {page_id}")
        page = fetch_page(page_id, base_url, token, args.api_path, timeout)
        comments = (fetch_comments(page_id, base_url, token, args.api_path,
                                   timeout) if args.comments else [])
        kids = []
        if args.children > 0:
            depth = min(args.children, MAX_CHILD_DEPTH)
            # Dedup and a global cap: a space can be a diamond rather
            # than a tree, so the same child is reachable twice, and
            # "depth 3" on a wide page is a crawl of the space rather
            # than a read of a spec.
            seen = {page_id}
            frontier = [page_id]
            for _ in range(depth):
                nxt = []
                for pid in frontier:
                    for child in fetch_children(pid, base_url, token,
                                                args.api_path, timeout):
                        cid = str(child.get("id") or "")
                        if not cid or cid in seen:
                            continue
                        seen.add(cid)
                        kids.append(child)
                        nxt.append(cid)
                        if len(kids) >= MAX_TOTAL_CHILDREN:
                            break
                    if len(kids) >= MAX_TOTAL_CHILDREN:
                        break
                if len(kids) >= MAX_TOTAL_CHILDREN:
                    say(f"  stopped at {MAX_TOTAL_CHILDREN} child pages")
                    break
                frontier = nxt
                if not frontier:
                    break
    except RuntimeError as e:
        print(f"refused: {e}")
        return 1

    if not _body_of(page).strip():
        print(f"refused: page {page_id} has an empty body. Nothing to read.")
        return 1

    text = render(page, comments)
    blocks = code_blocks(page, comments)
    for child in kids:
        text += "\n\n---\n\n" + render(child)
        blocks += code_blocks(child)

    outdir = args.out or os.path.join(ROOT, "target", "confluence")
    os.makedirs(outdir, exist_ok=True)
    stem = f"confluence-{page_id}-{_slug(page.get('title'))}"
    md_path = os.path.join(outdir, stem + ".md")
    with io.open(md_path, "w", encoding="utf-8") as fh:
        fh.write(text)
    meta_path = os.path.join(outdir, stem + ".json")
    with io.open(meta_path, "w", encoding="utf-8") as fh:
        json.dump({
            "page_id": page_id,
            "title": page.get("title"),
            "revision": page_revision(page),
            "space": (page.get("space") or {}).get("key"),
            "children": len(kids),
            "comments": len(comments),
            "code_blocks": blocks,
            "source": ref.raw,
        }, fh, indent=2, ensure_ascii=False)

    print(f"{page.get('title')} ({page_revision(page)})")
    print(f"  {len(text)} chars, {len(blocks)} code block(s), "
          f"{len(kids)} child page(s), {len(comments)} comment(s)")
    print(f"  {os.path.relpath(md_path, ROOT)}")
    if not blocks:
        print("  note: no code block on this page. The next stage decides "
              "whether a request can be read from the prose.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
