"""Get a JSON object out of what an agent actually replied.

    data, how = loads(text)

Asked for "only a JSON object", an agent still wraps it in a code fence,
puts a sentence before it, leaves a trailing comma, uses typographic
quotes, or runs out of room half way through the last element. Each of
those is tried in turn, cheapest first, and `how` says which one worked
so the log shows when an answer had to be repaired.

Raises ValueError when nothing recognisable is there. It never returns a
guess: a partial result is only what was actually parsed, and is
labelled "partial".

`json-repair` is used when it is installed and is not required. It is
only given a reply whose brackets all close. Given a reply that was cut
off, it closes the brackets itself and hands back a last item with half
its fields, labelled as repaired -- which is the guess this module
exists not to make. A cut-off reply goes to the partial path instead.
"""
from __future__ import annotations

import json
import re

_FENCE_RX = re.compile(r"```(?:json|JSON)?\s*\n?(.*?)```", re.S)
_SMART = {"“": '"', "”": '"', "‘": "'", "’": "'"}


def strip_fences(text: str) -> str:
    """The contents of the largest ``` block, or the text unchanged."""
    blocks = [m.group(1) for m in _FENCE_RX.finditer(text or "")]
    blocks = [b for b in blocks if "{" in b]
    return max(blocks, key=len) if blocks else (text or "")


def outer_object(text: str) -> str:
    """From the first `{` to the last `}`."""
    a, b = text.find("{"), text.rfind("}")
    return text[a:b + 1] if a != -1 and b > a else ""


def _matching(text: str, start: int) -> int:
    """Index of the bracket closing the one at `start`, or -1. Strings skipped."""
    pairs = {"{": "}", "[": "]"}
    stack, i, in_str = [], start, False
    while i < len(text):
        ch = text[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch in pairs:
            stack.append(pairs[ch])
        elif ch in "}]":
            if not stack or stack.pop() != ch:
                return -1
            if not stack:
                return i
        i += 1
    return -1


def _repairs(text: str):
    yield "as written", text
    no_trailing = re.sub(r",(\s*[}\]])", r"\1", text)
    yield "trailing commas removed", no_trailing
    smart = no_trailing
    for bad, good in _SMART.items():
        smart = smart.replace(bad, good)
    yield "typographic quotes replaced", smart


def partial_array(text: str, key: str) -> list:
    """The complete objects of `"key": [ ... ]` when the array (or the
    whole reply) was cut off before its end."""
    m = re.search(r'"%s"\s*:\s*\[' % re.escape(key), text)
    if not m:
        return []
    out, i = [], m.end()
    while i < len(text):
        while i < len(text) and text[i] in " \t\r\n,":
            i += 1
        if i >= len(text) or text[i] != "{":
            break
        end = _matching(text, i)
        if end == -1:
            break
        chunk = text[i:end + 1]
        for _how, candidate in _repairs(chunk):
            try:
                out.append(json.loads(candidate))
                break
            except ValueError:
                continue
        i = end + 1
    return out


def complete(body: str) -> bool:
    """Every bracket opened in `body` is closed, in order."""
    start = body.find("{")
    return start != -1 and _matching(body, start) != -1


def _as_list(text: str):
    """The reply when it is a bare array of objects, else None."""
    for source in (text, strip_fences(text)):
        s = source.strip()
        if not s.startswith("["):
            continue
        for _how, attempt in _repairs(s):
            try:
                data = json.loads(attempt)
            except ValueError:
                continue
            if isinstance(data, list) and any(isinstance(x, dict) for x in data):
                return data
    return None


def loads(text: str, partial_key: str = "", list_key: str = "") -> tuple:
    """(object, how it was obtained). See the module docstring.

    `list_key`: a reply that is a bare array of objects is returned as
    `{list_key: [...]}` instead of being refused.
    """
    if not (text or "").strip():
        raise ValueError("the reply is empty")
    if list_key:
        items = _as_list(text)
        if items is not None:
            return {list_key: items}, "a bare list, as written"
    candidates = []
    for label, source in (("", text), ("code fence removed; ", strip_fences(text))):
        body = outer_object(source)
        if body and body not in [c for _l, c in candidates]:
            candidates.append((label, body))
    for label, body in candidates:
        for how, attempt in _repairs(body):
            try:
                data = json.loads(attempt)
            except ValueError:
                continue
            if isinstance(data, dict):
                return data, label + how
    for label, body in candidates:
        if not complete(body) or not complete(text[text.find("{"):]):
            continue                        # cut off: see the docstring
        try:
            import json_repair
            data = json_repair.loads(body)
            if isinstance(data, dict) and data:
                return data, label + "repaired by json-repair"
        except Exception:                                # noqa: BLE001
            pass
    if partial_key:
        items = partial_array(text, partial_key)
        if items:
            return {partial_key: items}, (
                f"partial: the reply was cut off; {len(items)} complete "
                f"`{partial_key}` item(s) recovered")
    raise ValueError("no JSON object could be read from the reply")
