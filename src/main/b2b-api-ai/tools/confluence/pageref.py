"""Which Confluence page a pasted link means.

WHY THIS IS ITS OWN MODULE
--------------------------
Jira has one shape, `ABC-123`, and `tools/jira/fetch.py` reads it with a
single regex. Confluence links arrive in at least four shapes, and two
of them do not contain the page id at all:

    /pages/viewpage.action?pageId=12345          id, directly
    /spaces/SPACE/pages/12345/Some+Title         id, Cloud-style path
    /display/SPACE/Some+Title                    space + title, needs a lookup
    /x/AbCdEf                                    a tiny link, needs a redirect

A person pasting a link into the UI will not know or care which kind
they have, so the parse has to answer "what do I need to do next" rather
than "what is the id", and the two shapes that need another request have
to say so instead of guessing an id.

Nothing here performs a request. It returns an intent; the fetcher acts
on it, and the host allowlist is applied there -- parsing an unapproved
URL is harmless, fetching one is not.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, unquote, urlparse

# /pages/viewpage.action?pageId=12345
_VIEWPAGE = re.compile(r"/pages/viewpage\.action", re.I)
# /spaces/SPACE/pages/12345/Title  and  /wiki/spaces/SPACE/pages/12345
_SPACES_PAGES = re.compile(r"/spaces/([^/]+)/pages/(\d+)", re.I)
# /display/SPACE/Page+Title
_DISPLAY = re.compile(r"/display/([^/]+)/([^/?#]+)", re.I)
# /x/AbCdEf  -- a tiny link
_TINY = re.compile(r"/x/([A-Za-z0-9_-]+)/?$")
# a bare numeric id, pasted on its own
_BARE_ID = re.compile(r"^\d+$")


class PageRef:
    """What a link resolves to, and what the fetcher must do next.

    `kind` is one of:
        id      -- `page_id` is known; fetch it
        title   -- `space` + `title` are known; look the id up
        tiny    -- `tiny` is known; follow the redirect, then re-parse
    """

    __slots__ = ("kind", "page_id", "space", "title", "tiny", "base_url", "raw")

    def __init__(self, kind, raw, base_url="", page_id="", space="",
                 title="", tiny=""):
        self.kind = kind
        self.raw = raw
        self.base_url = base_url
        self.page_id = page_id
        self.space = space
        self.title = title
        self.tiny = tiny

    def __repr__(self):
        bits = [f"kind={self.kind}"]
        for name in ("page_id", "space", "title", "tiny"):
            v = getattr(self, name)
            if v:
                bits.append(f"{name}={v!r}")
        return "PageRef(" + ", ".join(bits) + ")"

    def __eq__(self, other):
        return isinstance(other, PageRef) and repr(self) == repr(other) \
            and self.base_url == other.base_url


def parse(value: str) -> tuple[PageRef | None, str]:
    """(ref, reason). `ref` is None when nothing usable could be read.

    A bare number is taken as a page id, which is what someone copying
    an id out of a URL will paste. Anything else without a recognisable
    Confluence path is refused by name rather than guessed at, because a
    guess here fetches the wrong page and the content looks plausible.
    """
    raw = (value or "").strip()
    if not raw:
        return None, "empty"

    if _BARE_ID.match(raw):
        return PageRef("id", raw, page_id=raw), ""

    try:
        u = urlparse(raw)
    except ValueError as e:
        return None, f"not a URL: {e}"
    if not u.scheme or not u.netloc:
        return None, ("not a URL and not a page id. Paste the full page "
                      "link, or just its numeric id.")

    base = f"{u.scheme}://{u.netloc}"

    if _VIEWPAGE.search(u.path):
        ids = parse_qs(u.query or "").get("pageId") or []
        pid = (ids[0] if ids else "").strip()
        if not pid.isdigit():
            return None, ("a viewpage.action link with no numeric pageId "
                          "parameter")
        return PageRef("id", raw, base_url=base, page_id=pid), ""

    m = _SPACES_PAGES.search(u.path)
    if m:
        return PageRef("id", raw, base_url=base,
                       space=unquote(m.group(1)), page_id=m.group(2)), ""

    m = _TINY.search(u.path)
    if m:
        return PageRef("tiny", raw, base_url=base, tiny=m.group(1)), ""

    m = _DISPLAY.search(u.path)
    if m:
        title = unquote(m.group(2)).replace("+", " ").strip()
        if not title:
            return None, "a /display/ link with no page title"
        return PageRef("title", raw, base_url=base,
                       space=unquote(m.group(1)), title=title), ""

    return None, ("not a recognised Confluence page link. Expected "
                  "/pages/viewpage.action?pageId=, /spaces/<SPACE>/pages/<id>, "
                  "/display/<SPACE>/<Title>, or /x/<tiny>.")
