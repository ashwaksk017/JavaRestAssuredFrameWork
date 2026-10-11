"""The files attached to a Confluence page: list them, read one.

    python tools/confluence/attachments.py --url https://wiki.example.com/x/AbCdEf
    python tools/confluence/attachments.py --id 12345

A page's specification is often not on the page but attached to it -- an
`openapi.yaml` under the paperclip. `fetch.py` reads the page; this reads
what hangs off it.

Everything `fetch.py` refuses, this refuses, by the same code: a link
that is not a page, a host that is not in `confluence_config.base_urls`,
no token. And for the file itself:

* the address of an attachment comes from Confluence's own answer, and is
  used only when it is a path on the approved host. A full URL to
  somewhere else is refused -- the token would go with the request;
* a redirect that leaves the approved host is refused, for the same
  reason (the opener `fetch.py` uses);
* a file larger than the limit is not read into memory;
* nothing here writes: reading is GET only. Where the bytes go is the
  caller's business.
"""
from __future__ import annotations

import argparse
import http.client
import importlib.util
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return mod


# By path and under its own name: tools/jira has a `fetch` too.
fetch = _load("confluence_fetch_for_attachments", os.path.join(HERE, "fetch.py"))
pageref = fetch.pageref

MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
MAX_LISTED = 200


class Refused(RuntimeError):
    """This will not be done: configuration, input, or what came back."""


def connect(link: str = "", cfg: dict = None) -> dict:
    """{base, token, timeout, api, ref} for the Confluence a link names.

    The same checks, in the same order, as `fetch.py` makes before it
    asks for a page.
    """
    ref, why = pageref.parse(link)
    if ref is None:
        raise Refused(why or "that is not a Confluence page link or id")
    if cfg is None:
        cfg, _note = fetch.projectconfig.section("confluence_config")
    if not cfg:
        raise Refused("no confluence_config in program_configuration.json. Add one "
                      "shaped like jira_config: base_urls (a LIST), pat, timeout_seconds.")
    token = str(cfg.get("pat") or "").strip()
    if not token:
        raise Refused("confluence_config.pat is empty. The token belongs in "
                      "program_configuration.json and nowhere else.")
    allowlist = cfg.get("base_urls") or []
    if isinstance(allowlist, str):
        raise Refused("confluence_config.base_urls is a STRING. It must be a list.")
    api = str(cfg.get("api_path") or fetch.DEFAULT_API_PATH).strip()
    if not api.startswith("/") or api.startswith("//") or re.search(r"[@\\\s?#]", api):
        # Joined onto the approved address as it stands. `rest/api` would
        # run into the host name, and `@other.host/rest` would BE the host.
        raise Refused("confluence_config.api_path must be a path like /rest/api/content")
    base = fetch.base_for(ref, allowlist, api).rstrip("/")
    # Where attachments are downloaded from: always the context address,
    # even when api_path carries the context itself.
    site = fetch.base_for(ref, allowlist, "").rstrip("/")
    if site and urllib.parse.urlparse(site).path in ("", "/") and api.find("/rest/") > 0:
        site += api[:api.find("/rest/")]
    if not base:
        raise Refused("a bare page id needs confluence_config.base_urls to say which "
                      "Confluence to ask.")
    ok, reason = fetch.host_allowed(base, allowlist)
    if not ok:
        raise Refused(reason)
    try:
        timeout = int(cfg.get("timeout_seconds") or fetch.DEFAULT_TIMEOUT)
    except (TypeError, ValueError):
        timeout = fetch.DEFAULT_TIMEOUT
    for address in (base + api, site):
        got = urllib.parse.urlparse(address)
        if not any((got.scheme, got.netloc.lower()) ==
                   (a.scheme, a.netloc.lower())
                   for a in (urllib.parse.urlparse(str(x)) for x in allowlist)):
            raise Refused("the address that would be asked is not an approved Confluence")
    return {"base": base, "site": site, "token": token, "timeout": timeout, "api": api,
            "ref": ref, "more": False}


def page_id(conn: dict) -> str:
    """The id of the page the link names (a title or a tiny link costs a request)."""
    try:
        pid, why = fetch.resolve(conn["ref"], conn["base"], conn["token"], conn["api"],
                                 conn["timeout"])
    except RuntimeError as e:
        raise Refused(str(e)) from None
    if not pid:
        raise Refused(why or "the link did not resolve to a page")
    if not str(pid).isdigit():
        raise Refused("the page id Confluence gave is not a number")
    return str(pid)


def list_attachments(conn: dict, pid: str, get=None) -> list:
    """[{id, title, bytes, media, download}] -- what is under the paperclip."""
    get = get or fetch._get
    q = urllib.parse.urlencode({"limit": MAX_LISTED, "expand": "version"})
    try:
        data = get(f"{conn['base']}{conn['api']}/{pid}/child/attachment?{q}",
                   conn["token"], conn["timeout"])
    except RuntimeError as e:
        raise Refused(str(e)) from None
    # More than one answer's worth: said, so that "the only one that fits"
    # is not claimed about a list that was not all seen.
    links = (data or {}).get("_links") if isinstance((data or {}).get("_links"), dict) else {}
    conn["more"] = bool(links.get("next"))
    out = []
    for row in (data or {}).get("results") or []:
        if not isinstance(row, dict):
            continue
        ext = row.get("extensions") if isinstance(row.get("extensions"), dict) else {}
        links = row.get("_links") if isinstance(row.get("_links"), dict) else {}
        try:
            size = int(ext.get("fileSize") or 0)
        except (TypeError, ValueError):
            size = 0
        out.append({"id": str(row.get("id") or ""), "title": str(row.get("title") or ""),
                    "bytes": size, "media": str(ext.get("mediaType") or ""),
                    "download": str(links.get("download") or "")})
    return out


def download_url(conn: dict, attachment: dict) -> str:
    """Where one attachment is read from: a path on the approved host.

    Confluence answers with a path (`/download/attachments/...`). Anything
    else -- a full address, a path that climbs out, a scheme -- is not
    followed: the Authorization header goes wherever the request goes.
    """
    link = attachment.get("download") or ""
    if (not link.startswith("/") or link.startswith("//") or "\\" in link
            or re.search(r"[^\x21-\x7e]", link)):
        raise Refused(f"the address Confluence gave for {attachment.get('title')!r} is not "
                      f"a path on this Confluence; it is not followed")
    path = link.split("?", 1)[0]
    plain = urllib.parse.unquote(path)
    if (not path.startswith("/download/") or "\\" in plain or "//" in plain
            or any(seg in ("..", ".") for seg in plain.split("/"))):
        raise Refused(f"the address Confluence gave for {attachment.get('title')!r} is not "
                      f"a download path; it is not followed")
    if ";" in plain:
        raise Refused(f"{attachment.get('title')!r} has a `;` in its name, which is not "
                      f"followed in an address. Download it and use From a file.")
    site = conn.get("site") or conn["base"]
    url = site + link
    want = urllib.parse.urlparse(site)
    got = urllib.parse.urlparse(url)
    if (got.scheme, got.netloc.lower()) != (want.scheme, want.netloc.lower()):
        raise Refused("the attachment's address is not on the approved host")
    return url


def read(conn: dict, attachment: dict, limit: int = MAX_ATTACHMENT_BYTES, opener=None) -> bytes:
    """The bytes of one attachment, or Refused."""
    if attachment.get("bytes", 0) > limit:
        raise Refused(f"{attachment.get('title')!r} is {attachment['bytes']:,} bytes; "
                      f"the limit is {limit:,}")
    url = download_url(conn, attachment)
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {conn['token']}")
    try:
        with (opener or fetch._opener(url)).open(req, timeout=conn["timeout"]) as resp:
            data = resp.read(limit + 1)
    except urllib.error.HTTPError as e:
        raise Refused(f"HTTP {e.code} from Confluence for {attachment.get('title')!r}") from None
    except urllib.error.URLError as e:
        raise Refused(f"could not read {attachment.get('title')!r}: {e.reason}") from None
    except (OSError, http.client.HTTPException, ValueError) as e:
        # A timeout, a reset, a reply cut short. The type is enough: the
        # text of some of these quotes the request.
        raise Refused(f"could not read {attachment.get('title')!r} "
                      f"({type(e).__name__})") from None
    if len(data) > limit:
        raise Refused(f"{attachment.get('title')!r} is larger than {limit:,} bytes; "
                      f"it was not read")
    return data


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default="", help="a Confluence page link")
    ap.add_argument("--id", default="", help="a page id, if you have it")
    a = ap.parse_args(argv)
    try:
        conn = connect(a.url or a.id)
        rows = list_attachments(conn, page_id(conn))
    except Refused as e:
        print(f"refused: {e}")
        return 1
    print(json.dumps([{k: r[k] for k in ("title", "bytes", "media")} for r in rows], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
