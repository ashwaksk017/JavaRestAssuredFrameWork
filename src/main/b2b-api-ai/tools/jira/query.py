"""Work out what a pasted piece of Jira is: stories, a query, or neither.

    parse("ABC-12, ABC-13")                         -> keys
    parse("https://<jira>/browse/ABC-12")           -> keys, and the host
    parse("https://<jira>/issues/?jql=project%3DABC") -> jql, and the host
    parse("https://<jira>/issues/?filter=10400")    -> jql `filter = 10400`
    parse("project = ABC AND labels = api")         -> jql
    parse("please test the login")                  -> none

People paste whatever is in front of them: a key, several, the address
bar of a story, the address bar of a search. Each of those was a
different text box, or a refusal, before this.

TWO THINGS THIS DOES NOT DO
---------------------------
It does not fetch. It returns what the paste IS, and every host it
named. The caller checks those against the configured Jira before any
request -- a pasted URL is untrusted, and the only host the token is
sent to is the configured one. A query taken out of a URL is still only
ever run against that configured host.

It does not guess. Text is a query only when the WHOLE of it reads as
one: clauses of field, operator and value, joined by AND / OR / NOT,
with an optional ORDER BY (`is_jql`). Finding one such clause somewhere
in a sentence is not enough -- "expected status = 200 in the response"
has one. A paste that is neither keys nor a query is `none`, with a
reason, and the caller says so instead of searching for something.

What that cannot tell apart: a short remark that happens to BE a whole
query (`status = 200`, `flag is null`). Those are queries. Jira answers
them with "field does not exist", which is a better answer than a
guess made here.

A query is returned exactly as written. Splitting a paste on commas to
look for keys and joining it back would turn `project in (A, B)` into
something else.

Stdlib only. The idea came from a sibling tool's paste classifier; its
keyword test ("contains AND / status / type") is the guess described
above and is not what is used here.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, unquote, urlparse

KEYS, JQL, NONE = "keys", "jql", "none"
MAX_KEYS = 200
MAX_JQL = 8000

_KEY = r"[A-Za-z][A-Za-z0-9_]+-[0-9]+"
_KEY_RX = re.compile(rf"^{_KEY}$")
# Only where Jira puts a story's key. `/display/SPACE/Release-12` on a wiki
# has the same shape and is a page title.
_KEY_IN_PATH_RX = re.compile(rf"(?i)/(?:browse|issues)/({_KEY})(?=$|[/?#])")
_URL_RX = re.compile(r"(?i)^https?://\S+$")
_HAS_URL_RX = re.compile(r"(?i)\bhttps?://")

# ---- is the whole text a query? ---------------------------------------

_TOKEN_RX = re.compile(
    r'\s*(?:(?P<str>"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\')'
    r"|(?P<op>!=|>=|<=|!~|=|~|>|<)"
    r"|(?P<punct>[(),])"
    r"|(?P<word>[^\s(),=!~<>\"']+))")
_CONNECTIVES = {"and", "or"}
_RESERVED = _CONNECTIVES | {"not", "in", "is", "was", "changed", "order", "by",
                            "empty", "null"}
_PREDICATES = {"after", "before", "on", "during", "by", "from", "to"}


def _tokens(text: str):
    out, pos = [], 0
    text = text.rstrip()
    while pos < len(text):
        m = _TOKEN_RX.match(text, pos)
        if not m or m.end() == pos:
            return None                     # an unclosed quote, a stray character
        kind = m.lastgroup
        out.append((kind, m.group(kind)))
        pos = m.end()
    return out


class _Parser:
    """Recursive descent over JQL's shape. It decides only "is this a
    query", never what the query means."""

    def __init__(self, tokens):
        self.t, self.i = tokens, 0

    def peek(self, ahead=0):
        j = self.i + ahead
        return self.t[j] if j < len(self.t) else (None, None)

    def word(self, *names, ahead=0) -> bool:
        kind, value = self.peek(ahead)
        return kind == "word" and value.lower() in names

    def take(self, kind, value=None) -> bool:
        k, v = self.peek()
        if k == kind and (value is None or v == value):
            self.i += 1
            return True
        return False

    def take_word(self, *names) -> bool:
        if self.word(*names):
            self.i += 1
            return True
        return False

    def query(self) -> bool:
        if not self.word("order"):
            if not self.or_expr():
                return False
        if self.take_word("order"):
            if not (self.take_word("by") and self.order_field()):
                return False
            while self.take("punct", ","):
                if not self.order_field():
                    return False
        return self.i == len(self.t)

    def order_field(self) -> bool:
        if not self.field():
            return False
        self.take_word("asc", "desc")
        return True

    def or_expr(self) -> bool:
        if not self.not_expr():
            return False
        while self.take_word("and", "or"):
            if not self.not_expr():
                return False
        return True

    def not_expr(self) -> bool:
        if self.take_word("not"):
            return self.not_expr()
        if self.take("punct", "("):
            return self.or_expr() and self.take("punct", ")")
        return self.clause()

    def field(self) -> bool:
        kind, value = self.peek()
        if kind == "str":
            self.i += 1
            return True
        if kind == "word" and value.lower() not in _RESERVED and \
                re.match(r"^(?:[A-Za-z][\w.]*|cf\[[0-9]+\])$", value):
            self.i += 1
            return True
        return False

    def value(self) -> bool:
        kind, value = self.peek()
        if kind == "str":
            self.i += 1
            return True
        if kind != "word" or value.lower() in _RESERVED:
            return False
        self.i += 1
        if self.peek() == ("punct", "("):            # a function: openSprints()
            return self.list_()
        return True

    def list_(self) -> bool:
        if not self.take("punct", "("):
            return False
        if self.take("punct", ")"):
            return True
        if not self.value():
            return False
        while self.take("punct", ","):
            if not self.value():
                return False
        return self.take("punct", ")")

    def operand(self) -> bool:
        """`(a, b)` or one value, which may be a function."""
        return self.list_() if self.peek() == ("punct", "(") else self.value()

    def predicates(self) -> bool:
        # `by` here is CHANGED ... BY user. ORDER BY starts with `order`,
        # which is not a predicate, so the loop stops in front of it.
        while self.word(*_PREDICATES):
            self.i += 1
            if not self.operand():
                return False
        return True

    def clause(self) -> bool:
        if not self.field():
            return False
        if self.take("op"):
            return self.value()
        if self.take_word("not"):
            return self.take_word("in") and self.operand()
        if self.take_word("in"):
            return self.operand()
        if self.take_word("is"):
            self.take_word("not")
            return self.take_word("empty", "null")
        if self.take_word("was"):
            self.take_word("not")
            if self.take_word("in"):
                ok = self.operand()
            elif self.take_word("empty", "null"):
                ok = True
            else:
                ok = self.value()
            return ok and self.predicates()
        if self.take_word("changed"):
            return self.predicates()
        return False


def is_jql(text: str) -> bool:
    """The whole text reads as a JQL query. See the module docstring."""
    t = (text or "").strip()
    if not t or len(t) > MAX_JQL:
        return False
    tokens = _tokens(t)
    if not tokens:
        return False
    depth = deepest = 0
    for kind, value in tokens:
        depth += (value == "(") - (value == ")") if kind == "punct" else 0
        deepest = max(deepest, depth)
    if deepest > 40 or sum(1 for k, v in tokens if k == "word" and v.lower() == "not") > 40:
        return False                        # nobody pastes that; a parser recurses on it
    try:
        return _Parser(tokens).query()
    except RecursionError:
        return False


looks_like_jql = is_jql


# ---- what a paste is ----------------------------------------------------

def _result(kind: str, keys=(), jql: str = "", hosts=(), why: str = "") -> dict:
    hosts = sorted(set(h for h in hosts if h))
    return {"kind": kind, "keys": list(keys), "jql": jql, "hosts": hosts,
            "host": hosts[0] if len(hosts) == 1 else "", "why": why}


def _unique(keys) -> list:
    seen, out = set(), []
    for k in keys:
        k = k.upper()
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def _clean_url(token: str) -> str:
    """`<https://x/browse/ABC-1>.` is that address, pasted from a sentence."""
    t = token.strip().lstrip("<(").rstrip(">")
    while t and t[-1] in ".,;)>":
        if t[-1] == ")" and t.count("(") >= t.count(")"):
            break
        t = t[:-1]
    return t


def _from_url(raw: str) -> dict:
    try:
        u = urlparse(raw)
        netloc = u.netloc
        qs = parse_qs(u.query, keep_blank_values=False)
    except ValueError:                       # e.g. a broken [IPv6 literal
        return _result(NONE, why="that address cannot be read")
    if u.scheme.lower() not in ("http", "https") or not netloc:
        return _result(NONE, why="not an http(s) address")
    host = [f"{u.scheme.lower()}://{netloc}"]
    in_path = _KEY_IN_PATH_RX.findall(unquote(u.path))
    # A story's own address wins over anything in its query string:
    # `/browse/ABC-1?jql=...` is ABC-1, opened from a search.
    if in_path:
        return _result(KEYS, _unique(in_path[-1:]), hosts=host)
    selected = (qs.get("selectedIssue") or [""])[0].strip()
    if _KEY_RX.match(selected):
        return _result(KEYS, _unique([selected]), hosts=host)
    jql = (qs.get("jql") or [""])[0].strip()
    if jql:
        if len(jql) > MAX_JQL:
            return _result(NONE, hosts=host, why=f"the query in that address is over {MAX_JQL} characters")
        if not is_jql(jql):
            return _result(NONE, hosts=host, why="the `jql` in that address is not a query")
        return _result(JQL, jql=jql, hosts=host)
    saved = (qs.get("filter") or qs.get("filterId") or [""])[0].strip()
    if re.fullmatch(r"[0-9]{1,12}", saved):
        return _result(JQL, jql=f"filter = {saved}", hosts=host)
    return _result(NONE, hosts=host,
                   why="that address names no story, query or saved filter")


def parse(text: str) -> dict:
    """{kind, keys, jql, hosts, host, why}. See the module docstring.

    `hosts` is every host the paste named, whatever its kind -- the
    caller refuses a paste that names one it does not trust, even when
    this could make nothing of that address. `host` is the one host when
    there is exactly one.
    """
    raw = (text or "").strip()
    if not raw:
        return _result(NONE, why="nothing was pasted")
    if _URL_RX.match(_clean_url(raw)):
        return _from_url(_clean_url(raw))

    tokens = [t for t in re.split(r"[\s,;]+", raw) if t]
    seen_hosts, parts, only_keys = [], [], len(tokens) <= 4 * MAX_KEYS
    for t in tokens if only_keys else ():
        if _KEY_RX.match(t):
            parts.append(_result(KEYS, [t]))
            continue
        cleaned = _clean_url(t)
        if _URL_RX.match(cleaned):
            parts.append(_from_url(cleaned))
            seen_hosts += parts[-1]["hosts"]
            continue
        only_keys = False
    # Every address in the paste is reported, whether or not the paste
    # turned out to be all keys.
    for t in tokens[:4 * MAX_KEYS]:
        if _HAS_URL_RX.search(t) and not _KEY_RX.match(t):
            seen_hosts += _from_url(_clean_url(t))["hosts"]

    if only_keys and parts and all(p["kind"] == KEYS for p in parts):
        if len(set(seen_hosts)) > 1:
            return _result(NONE, hosts=seen_hosts,
                           why="those addresses are on more than one host")
        keys = _unique(k for p in parts for k in p["keys"])
        if len(keys) > MAX_KEYS:
            return _result(NONE, hosts=seen_hosts,
                           why=f"more than {MAX_KEYS} keys; use a query instead")
        return _result(KEYS, keys, hosts=seen_hosts)

    if _HAS_URL_RX.search(raw):
        # An address AND other text. Which of the two was meant is a guess.
        return _result(NONE, hosts=seen_hosts,
                       why="an address together with other text: paste the "
                           "address, or the query, on its own")
    if len(raw) > MAX_JQL:
        return _result(NONE, why=f"the query is over {MAX_JQL} characters")
    if is_jql(raw):
        return _result(JQL, jql=raw)
    return _result(NONE, why="neither story keys nor a query: a query is "
                             "clauses of field, operator and value, like "
                             "`project = ABC AND labels = api`")
