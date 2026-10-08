"""Open a URL that carries the Jira token, without letting it be led away.

    with safehttp.open(request, timeout) as response: ...

urllib follows a redirect by sending the same request to wherever
`Location` points, and it copies the headers -- `Authorization`
included. So a base URL written as `http://` that the server answers
with a redirect to its single-sign-on host hands the token to that host;
and a POST is re-sent as a GET with no body, which a search endpoint
answers as "no query".

The host allowlist is checked before the first request. A redirect is a
second request the allowlist never saw. Here it is followed only when it
stays on the same scheme, host and port, and only for a GET. Anything
else raises `Redirected`, naming where it pointed, and the caller says
to fix the configured base URL.
"""
from __future__ import annotations

import urllib.request
from urllib.parse import urljoin, urlparse


class Redirected(Exception):
    """The server tried to send the request somewhere it may not go."""

    def __init__(self, target: str, why: str):
        try:
            u = urlparse(target)
            self.target = f"{u.scheme}://{u.netloc}" if u.netloc else "(no host)"
        except ValueError:
            self.target = "(an unreadable address)"
        super().__init__(f"redirected to {self.target}: {why}")


def _origin(url: str) -> tuple:
    try:
        u = urlparse(url)
        port = u.port or {"http": 80, "https": 443}.get(u.scheme)
    except ValueError:                     # `Location: http://host:abc/`
        raise Redirected(url, "the address it gave cannot be read") from None
    return u.scheme, (u.hostname or "").lower(), port


class _SameOriginOnly(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        newurl = urljoin(req.full_url, newurl)
        if _origin(newurl) != _origin(req.full_url):
            raise Redirected(newurl, "the token is only sent to the "
                                     "configured Jira host")
        if req.get_method() != "GET":
            raise Redirected(newurl, "a redirected POST is re-sent without "
                                     "its body")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_SameOriginOnly)


def open(request, timeout):                               # noqa: A001
    return _OPENER.open(request, timeout=timeout)
