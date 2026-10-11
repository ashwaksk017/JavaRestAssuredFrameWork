"""Somebody else's text, written into Markdown or HTML as plain words.

A report this project writes is attached to merge requests and tickets
and pasted into wikis. What a model said, a ticket's summary, a file's
name -- none of it may become a link, an image, a mention that notifies
people, or markup, on the page it ends up on.
"""
from __future__ import annotations

import html
import re

_ZW = "\u200b"                # a zero-width space: it stops a word being recognised


def md(text) -> str:
    """Plain words in Markdown: no markup, entity, autolink (`www.`,
    `scheme://`, an e-mail address), mention (`@all`) or reference
    (`#12`, `!12`, `$12`, `%name`, `&12`, `:emoji:`)."""
    s = re.sub(r"\s+", " ", str(text if text is not None else "")).strip()
    # Characters that reorder what follows them on the line.
    s = re.sub(r"[\u202a-\u202e\u2066-\u2069]", "", s)
    s = s.replace("&", "&amp;")
    s = re.sub(r"([\\`*_{}\[\]<>#|!~])", r"\\\1", s)
    s = s.replace("@", "@" + _ZW).replace("://", ":" + _ZW + "//")
    s = re.sub(r"([#!$%])(?=\w)", lambda m: m.group(1) + _ZW, s)
    s = re.sub(r"&amp;(?=\d)", "&amp;" + _ZW, s)
    s = re.sub(r":(?=[a-z0-9_+-]+:)", ":" + _ZW, s)
    return re.sub(r"(?i)\bwww\.", lambda m: m.group(0)[:-1] + _ZW + ".", s)


def code(text) -> str:
    """A name in a code span it cannot close, in a table cell it cannot split."""
    s = re.sub(r"[\x00-\x1f\x7f\u2028\u2029\u202a-\u202e\u2066-\u2069]", "?",
               str(text if text is not None else ""))
    for bad, safe in (("`", "'"), ("|", "\u00a6"), ("<", "\u2039"), (">", "\u203a")):
        s = s.replace(bad, safe)
    return f"`{s}`"


def fence(text) -> str:
    """A code block no quoted text can close."""
    text = str(text if text is not None else "")
    ticks = "`" * max(3, max((len(m) for m in re.findall(r"`+", text)), default=0) + 1)
    return f"{ticks}\n{text}\n{ticks}"


def xhtml(text) -> str:
    """Text for a Confluence page body (storage format is XHTML): escaped,
    and with the characters XML does not allow taken out."""
    # ...and half of a character pair, which is what a cut-off emoji leaves.
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ufffe\uffff\ud800-\udfff]", "",
               str(text if text is not None else ""))
    return html.escape(s, quote=True)
