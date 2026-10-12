"""What a person decided about each proposed case: approved, rejected, or
still proposed; whether it is to be automated; and the title or steps as
they corrected them.

Design the API tests and Manual tests each propose cases. Between a
proposal and anything that acts on it -- Create in Xray writes to Jira,
Agentic Test Creation writes code -- a person reads it and says which
cases stand. That decision lives here, in `case-review.json` in the job
directory:

    {"kind": "case-review", "at": "...",
     "design": {"TC-001": {"state": "approved", "automate": true,
                           "title": "...", "steps": [...], "note": "...", "at": "..."}},
     "manual": {"MT-001": {"state": "rejected", "note": "duplicate of ABC-12"}}}

A case with no entry is PROPOSED. Create in Xray takes approved cases
only; the Agentic step, once any case of a design has been reviewed,
takes the approved ones marked to automate. `title` and `steps` are kept
only when the person changed them, and replace the proposed ones
wherever the case is used.

Nothing here reads Jira or writes anywhere but the job directory. What
the page sends is validated, not trusted: a state is one of three words,
a title one line, steps a short list of three short strings.
"""
from __future__ import annotations

import io
import json
import os
import re
from datetime import datetime, timezone

FILE = "case-review.json"
STATES = ("proposed", "approved", "rejected")
WHATS = ("design", "manual")
MAX_TITLE = 240
MAX_STEP = 4000
MAX_STEPS = 40
MAX_NOTE = 500
MAX_IDS = 200
_ID_RX = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,39}$")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _one_line(value, cap: int) -> str:
    s = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value if value is not None else ""))
    return " ".join(s.split())[:cap]


def _text(value, cap: int) -> str:
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(value if value is not None else ""))
    return s.strip()[:cap]


def read(job_dir: str) -> dict:
    """The review of a job, whole; empty maps when there is none yet."""
    out = {"kind": "case-review", "at": "", "design": {}, "manual": {}}
    try:
        with io.open(os.path.join(job_dir, FILE), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return out
    if not isinstance(data, dict):
        return out
    out["at"] = str(data.get("at") or "")
    for what in WHATS:
        entries = data.get(what)
        if isinstance(entries, dict):
            out[what] = {str(k): v for k, v in entries.items()
                         if isinstance(v, dict) and _ID_RX.match(str(k))}
    return out


def write(job_dir: str, review: dict) -> None:
    review["kind"], review["at"] = "case-review", _now()
    os.makedirs(job_dir, exist_ok=True)
    path = os.path.join(job_dir, FILE)
    tmp = f"{path}.{os.getpid()}.tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(review, fh, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


def _clean_steps(steps) -> list:
    if not isinstance(steps, list):
        raise ValueError("steps must be a list")
    out = []
    for s in steps[:MAX_STEPS]:
        if not isinstance(s, dict):
            raise ValueError("each step must be an object with action, data, expected")
        step = {k: _text(s.get(k), MAX_STEP) for k in ("action", "data", "expected")}
        if step["action"] or step["expected"]:
            out.append(step)
    if not out:
        raise ValueError("a case needs at least one step with an action or an expected result")
    return out


def update(job_dir: str, what: str, ids, state: str = None, automate=None, title: str = None,
           steps=None, note: str = None) -> dict:
    """Record a decision for one case, or the same decision for several.
    Fields left None are left as they were. Returns the entries written."""
    if what not in WHATS:
        raise ValueError(f"what must be one of {', '.join(WHATS)}")
    ids = [ids] if isinstance(ids, str) else list(ids or [])
    if not ids or len(ids) > MAX_IDS:
        raise ValueError(f"give between 1 and {MAX_IDS} case ids")
    for case_id in ids:
        if not isinstance(case_id, str) or not _ID_RX.match(case_id):
            raise ValueError(f"{case_id!r} is not a case id like TC-001")
    if state is not None and state not in STATES:
        raise ValueError(f"state must be one of {', '.join(STATES)}")
    if automate is not None and automate is not True and automate is not False:
        raise ValueError("automate must be true or false")
    if title is not None and not _one_line(title, MAX_TITLE):
        raise ValueError("a title cannot be empty")
    if steps is not None:
        steps = _clean_steps(steps)
    review = read(job_dir)
    written = {}
    for case_id in ids:
        entry = dict(review[what].get(case_id) or {})
        if state is not None:
            entry["state"] = state
        if automate is not None:
            entry["automate"] = automate
        if title is not None:
            entry["title"] = _one_line(title, MAX_TITLE)
        if steps is not None:
            entry["steps"] = steps
        if note is not None:
            entry["note"] = _one_line(note, MAX_NOTE)
        entry.setdefault("state", "proposed")
        entry["at"] = _now()
        review[what][case_id] = entry
        written[case_id] = entry
    write(job_dir, review)
    return written


def apply(cases: list, entries: dict) -> list:
    """The cases with the person's decision on each: a `review` field
    {state, automate, note, edited}, and the title and steps as corrected."""
    out = []
    for case in cases:
        case = dict(case)
        entry = entries.get(str(case.get("id") or "")) if isinstance(entries, dict) else None
        entry = entry if isinstance(entry, dict) else {}
        edited = []
        if entry.get("title"):
            case["title"], edited = entry["title"], edited + ["title"]
        if isinstance(entry.get("steps"), list) and entry["steps"]:
            case["steps"], edited = entry["steps"], edited + ["steps"]
        case["review"] = {"state": entry.get("state") if entry.get("state") in STATES else "proposed",
                          "automate": entry.get("automate") is not False,
                          "note": str(entry.get("note") or ""), "edited": edited}
        out.append(case)
    return out


def reviewed(entries: dict) -> bool:
    """Has a person said anything about this list yet?"""
    return any(isinstance(e, dict) and e.get("state") in ("approved", "rejected")
               for e in (entries or {}).values())


def approved(cases: list, entries: dict, for_automation: bool = False) -> list:
    """The cases a person approved -- and, for automation, marked to automate."""
    out = []
    for case in apply(cases, entries):
        r = case["review"]
        if r["state"] == "approved" and (r["automate"] or not for_automation):
            out.append(case)
    return out


def summary(cases: list, entries: dict) -> dict:
    counts = {"total": len(cases), "approved": 0, "rejected": 0, "proposed": 0, "automate": 0}
    for case in apply(cases, entries):
        counts[case["review"]["state"]] += 1
        if case["review"]["state"] == "approved" and case["review"]["automate"]:
            counts["automate"] += 1
    counts["reviewed"] = reviewed(entries)
    return counts
