"""The workflow of a job, stage by stage, and the two decisions only a
person makes in it: the cases are FINAL, and these are the REGRESSION ones.

    generate -> review and refine -> finalize -> push to Xray -> automate
    -> regression -> execute (stg) -> push the feature branch -> publish

Most stages are read from what the job already holds (design.json,
case-review.json, xray-tests.json, review.json, execution.json,
xray-publish.json). The two decisions are kept in workflow.json:

- finalized: the approved cases, as they were -- id, title, steps and
  suites. It holds while they are the same; an edit after it reopens it.
- regression: the automated cases in the `regression` group, confirmed for
  the change waiting for review. A new change needs it again.

The gates (jobs.start): Create in Xray (design) and Automate need the cases
finalized; Run the tests needs the regression confirmed; Publish to Xray
needs the change pushed. Approve & push needs a green run (loop.py).
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from datetime import datetime, timezone

FILE = "workflow.json"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(job_dir: str, name: str) -> dict:
    try:
        with io.open(os.path.join(job_dir, name), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(job_dir: str, record: dict) -> None:
    with io.open(os.path.join(job_dir, FILE), "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=1)


def approved_cases(job_dir: str, casereview, suitemap) -> list:
    """The approved cases as they are now: [{id, title, steps, groups}]."""
    design = _json(job_dir, "design.json")
    cases = [c for c in design.get("test_cases") or [] if isinstance(c, dict) and c.get("id")]
    entries = casereview.read(job_dir)["design"]
    groups = suitemap.for_job(job_dir)
    out = []
    for c in casereview.approved(cases, entries):
        out.append({"id": str(c["id"]), "title": str(c.get("title") or ""),
                    "steps": c.get("steps") or [], "automate": c["review"]["automate"],
                    "groups": (groups.get(str(c["id"])) or {}).get("groups") or []})
    return out


def fingerprint(cases: list) -> str:
    return hashlib.sha1(json.dumps(cases, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def finalized(job_dir: str, casereview, suitemap) -> tuple:
    """(holds, why): are the approved cases the ones finalized?"""
    rec = _json(job_dir, FILE).get("finalized") or {}
    if not rec:
        return False, "the cases are not finalized yet (Review: Finalize the cases)"
    now = approved_cases(job_dir, casereview, suitemap)
    if fingerprint(now) != rec.get("fingerprint"):
        return False, ("the approved cases changed after they were finalized (a case, its steps "
                       "or its suites): finalize them again")
    return True, f"finalized {rec.get('at')}: {len(now)} case(s)"


def finalize(job_dir: str, casereview, suitemap) -> dict:
    cases = approved_cases(job_dir, casereview, suitemap)
    if not cases:
        raise ValueError("approve at least one case before finalizing")
    rec = _json(job_dir, FILE)
    rec["finalized"] = {"at": _now(), "fingerprint": fingerprint(cases), "cases": [c["id"] for c in cases]}
    _save(job_dir, rec)
    return rec["finalized"]


def regression_cases(job_dir: str, casereview, suitemap) -> list:
    """The automated cases (written into the change waiting) in `regression`."""
    review = _json(job_dir, "review.json")
    change = str(review.get("change") or "")
    done = _json(job_dir, "automated-cases.json")
    written = {i for i, e in done.items() if isinstance(e, dict) and e.get("written")
               and (not change or e.get("change") == change)}
    return [c["id"] for c in approved_cases(job_dir, casereview, suitemap)
            if c["id"] in written and "regression" in c["groups"]]


def regression_confirmed(job_dir: str) -> tuple:
    review = _json(job_dir, "review.json")
    rec = _json(job_dir, FILE).get("regression") or {}
    if not rec:
        return False, "confirm which automated cases are regression cases first (Commit and push tab)"
    if rec.get("change") != str(review.get("change") or ""):
        return False, "the regression cases were confirmed for an earlier change: confirm them again"
    if rec.get("run") != str(review.get("started") or ""):
        # A follow-up or an Automate added to the change since: the cases in
        # it, and so the regression ones, may differ.
        return False, "the change was added to after the regression cases were confirmed: confirm them again"
    return True, f"{len(rec.get('cases') or [])} regression case(s) confirmed {rec.get('at')}"


def confirm_regression(job_dir: str, casereview, suitemap) -> dict:
    review = _json(job_dir, "review.json")
    if review.get("state") != "pending-review":
        raise ValueError("there is no change waiting for review (and verified) to confirm the regression cases of")
    rec = _json(job_dir, FILE)
    rec["regression"] = {"at": _now(), "change": str(review.get("change") or ""),
                         "run": str(review.get("started") or ""),
                         "cases": regression_cases(job_dir, casereview, suitemap)}
    _save(job_dir, rec)
    return rec["regression"]


def stages(job_dir: str, casereview, suitemap) -> list:
    """[{name, done, detail}] in order, for the page's strip."""
    design = _json(job_dir, "design.json")
    entries = casereview.read(job_dir)["design"]
    cases = [c for c in design.get("test_cases") or [] if isinstance(c, dict)]
    approved = [c for c in casereview.approved(cases, entries)]
    fin, fin_why = finalized(job_dir, casereview, suitemap)
    keys = [k for k in (_json(job_dir, "xray-tests.json").get("tests") or {}) if str(k).startswith("design:")]
    review = _json(job_dir, "review.json")
    state = str(review.get("state") or "none")
    reg, reg_why = regression_confirmed(job_dir)
    ran = review.get("execution") or {}
    pub = _json(job_dir, "xray-publish.json")
    return [
        {"name": "Generate", "done": bool(cases), "detail": f"{len(cases)} case(s)" if cases else ""},
        {"name": "Review", "done": bool(approved), "detail": f"{len(approved)} approved" if approved else ""},
        {"name": "Finalize", "done": fin, "detail": fin_why},
        {"name": "Xray tests", "done": bool(keys), "detail": f"{len(keys)} in Xray" if keys else ""},
        {"name": "Automate", "done": state in ("pending-review", "pushed", "approved-local"),
         "detail": state if state != "none" else ""},
        {"name": "Regression", "done": reg, "detail": reg_why if reg else ""},
        {"name": "Run on stg", "done": bool(ran.get("ok")),
         "detail": (f"{ran.get('passed')} passed, {ran.get('failed')} failed" if ran else "")},
        {"name": "Push", "done": state == "pushed", "detail": review.get("branch") or ""},
        {"name": "Publish", "done": bool(pub.get("ok")), "detail": pub.get("execution") or ""},
    ]
