"""Confluence storage XHTML -> readable text, keeping the payloads.

WHY THE ORDER MATTERS
---------------------
A Confluence page body is XHTML carrying Confluence's own macros:

    <ac:structured-macro ac:name="code">
      <ac:parameter ac:name="language">json</ac:parameter>
      <ac:plain-text-body><![CDATA[ {"propCode": "ABC"} ]]></ac:plain-text-body>
    </ac:structured-macro>

The request payload -- the single thing the next stage exists to read --
lives inside that macro, inside CDATA. Strip tags first and the CDATA
goes with them, leaving a page that reads fine and contains nothing
useful. So code macros are lifted into fenced blocks BEFORE any tag
removal, which is the same order `tools/jira/fetch.py` uses when it
pulls fenced blocks and `{code}` sections out of Jira markup.

Tables are kept as pipe rows rather than flattened: on these pages the
contract is often a table of field/value, and flattening turns it into
an unreadable run of words.

No HTML parser dependency. The input is machine-generated XHTML from one
product, not the open web, and this repository keeps its tooling to the
standard library.
"""
from __future__ import annotations

import html
import re

# <ac:structured-macro ac:name="code"> ... </ac:structured-macro>
_MACRO = re.compile(
    r'<ac:structured-macro[^>]*ac:name="(?P<name>[^"]+)"[^>]*>'
    r'(?P<inner>.*?)</ac:structured-macro>',
    re.S | re.I)
_PLAIN_BODY = re.compile(
    r"<ac:plain-text-body[^>]*>(?P<body>.*?)</ac:plain-text-body>", re.S | re.I)
_RICH_BODY = re.compile(
    r"<ac:rich-text-body[^>]*>(?P<body>.*?)</ac:rich-text-body>", re.S | re.I)
_PARAM = re.compile(
    r'<ac:parameter[^>]*ac:name="(?P<k>[^"]+)"[^>]*>(?P<v>.*?)</ac:parameter>',
    re.S | re.I)
_CDATA = re.compile(r"<!\[CDATA\[(?P<t>.*?)\]\]>", re.S)

_BR = re.compile(r"<br\s*/?>", re.I)
_P_END = re.compile(r"</(p|div|h[1-6]|li|tr)\s*>", re.I)
_LI = re.compile(r"<li[^>]*>", re.I)
_TD = re.compile(r"</(td|th)\s*>", re.I)
_TR_START = re.compile(r"<tr[^>]*>", re.I)
_TAG = re.compile(r"<[^>]+>")
_BLANKS = re.compile(r"\n{3,}")

# Macros whose body is noise in a spec: navigation, layout, decoration.
_DROP_MACROS = frozenset({
    "toc", "children", "excerpt-include", "pagetree", "livesearch",
    "recently-updated", "contributors", "anchor",
})

CODE_MACROS = frozenset({"code", "noformat", "codeblock"})


def _cdata(text: str) -> str:
    m = _CDATA.search(text or "")
    return m.group("t") if m else (text or "")


def _macro_params(inner: str) -> dict:
    return {m.group("k").lower(): _strip_tags(m.group("v"))
            for m in _PARAM.finditer(inner or "")}


def _strip_tags(fragment: str) -> str:
    return html.unescape(_TAG.sub("", fragment or "")).strip()


def extract_code_blocks(body: str) -> list[dict]:
    """Every code/noformat macro, in document order.

    Returned before stripping so a caller can hand the payloads to the
    extractor without re-parsing the rendered text.
    """
    out = []
    for m in _MACRO.finditer(body or ""):
        name = (m.group("name") or "").lower()
        if name not in CODE_MACROS:
            continue
        inner = m.group("inner") or ""
        pb = _PLAIN_BODY.search(inner)
        text = _cdata(pb.group("body")) if pb else _cdata(inner)
        params = _macro_params(inner)
        out.append({
            "language": params.get("language", ""),
            "title": params.get("title", ""),
            "text": html.unescape(text).strip(),
        })
    return out


def _render_macros(body: str) -> str:
    """Replace macros with something readable, keeping code fenced."""
    def repl(m):
        name = (m.group("name") or "").lower()
        inner = m.group("inner") or ""
        if name in _DROP_MACROS:
            return "\n"
        if name in CODE_MACROS:
            pb = _PLAIN_BODY.search(inner)
            text = _cdata(pb.group("body")) if pb else _cdata(inner)
            lang = _macro_params(inner).get("language", "")
            return "\n```" + lang + "\n" + html.unescape(text).strip() + "\n```\n"
        rb = _RICH_BODY.search(inner)
        if rb:
            return "\n" + rb.group("body") + "\n"
        pb = _PLAIN_BODY.search(inner)
        if pb:
            return "\n" + _cdata(pb.group("body")) + "\n"
        return "\n"
    # Repeat: a rich-text body can itself hold a macro.
    text = body or ""
    for _ in range(5):
        new = _MACRO.sub(repl, text)
        if new == text:
            break
        text = new
    return text


def to_text(body: str) -> str:
    """Storage XHTML -> plain text with fenced code and pipe tables."""
    text = _render_macros(body or "")
    text = _BR.sub("\n", text)
    text = _LI.sub("- ", text)
    text = _TD.sub(" | ", text)
    text = _TR_START.sub("\n", text)
    text = _P_END.sub("\n", text)
    text = _TAG.sub("", text)
    text = html.unescape(text)
    # Tidy the pipe rows the table rules leave behind.
    lines = []
    for line in text.split("\n"):
        line = line.rstrip()
        if line.endswith("|"):
            line = line[:-1].rstrip()
        lines.append(line.strip())
    text = "\n".join(lines)
    return _BLANKS.sub("\n\n", text).strip()
