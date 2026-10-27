"""Has the story changed since this job read it?

    python tools/agent/storycheck.py --job J

A job reads its story once (Read it) and everything after -- the plan,
the design, the review, the Xray tests, the automated change -- is made
from that reading. Jira does not tell the job when the story is edited.
This asks: it fetches the story again, through the same reader, and
compares what came back with what the job holds: Jira's revision of the
issue, and the story's own text. The answer is written to
`story-check.json` and shown on the page:

* the revision before and after;
* the text, as a line-by-line diff (bounded), and whether acceptance
  criteria are present now and were before;
* what in the job was made from the old reading and so is in question:
  a design, a review, tests created in Xray, a change waiting for review.

It changes nothing itself. Design again, review again, and the follow-up
to Cursor are the steps that act on what it says.
"""
from __future__ import annotations

import argparse
import difflib
import io
import json
import os
import shutil
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))


def _diag():
    import importlib.util as _ilu
    if "workbench_diag" not in sys.modules:
        _spec = _ilu.spec_from_file_location(
            "workbench_diag", os.path.join(os.path.dirname(HERE), "diag.py"))
        _mod = _ilu.module_from_spec(_spec)
        sys.modules["workbench_diag"] = _mod
        try:
            _spec.loader.exec_module(_mod)
        except BaseException:
            sys.modules.pop("workbench_diag", None)
            raise
    return sys.modules["workbench_diag"]


diag = _diag()


def _load(name: str, path: str):
    import importlib.util
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return mod


intake = _load("storycheck_intake", os.path.join(HERE, "intake.py"))
ROOT = intake.ROOT
RESULT = "story-check.json"
MAX_DIFF_LINES = 400


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(path: str) -> dict:
    try:
        with io.open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def at_stake(job_dir: str) -> list:
    """What this job holds that was made from the earlier reading."""
    out = []
    design = _json(os.path.join(job_dir, "design.json"))
    if design.get("test_cases"):
        out.append(f"a design of {len(design['test_cases'])} case(s) -- design again; cases whose "
                   f"text is unchanged keep their review and their Xray test")
    review = _json(os.path.join(job_dir, "case-review.json"))
    decided = sum(1 for e in (review.get("design") or {}).values()
                  if isinstance(e, dict) and e.get("state") in ("approved", "rejected"))
    if decided:
        out.append(f"a review of {decided} case(s) -- read the changed cases again")
    xray = _json(os.path.join(job_dir, "xray-tests.json"))
    keys = [k for k in (xray.get("tests") or {}) if str(k).startswith("design:")]
    if keys:
        out.append(f"{len(keys)} test(s) created in Xray -- a case that changed is a new case and "
                   f"gets a new test; the earlier test stays in Jira until closed by hand (nothing "
                   f"here edits or closes an issue)")
    agent = _json(os.path.join(job_dir, "review.json"))
    if agent.get("state") in ("pending-review", "verify-failed", "pushed", "push-failed",
                              "approved-local"):
        out.append(f"an automated change ({agent['state']}) -- ask AI Agent to change it with "
                   f"what changed, or run again after a new design")
    if os.path.isfile(os.path.join(job_dir, "plan.md")):
        out.append("a plan -- run 'is it automated?' again after Read it")
    return out


def check(job: str, root: str = "", connect=None, transport=None, attachments=None) -> dict:
    root = root or intake.ROOT
    job_dir = intake.job_dir(job, root)
    told = _json(os.path.join(job_dir, "intake.json"))
    story = told.get("story") if isinstance(told.get("story"), dict) else None
    out = {"kind": "story-check", "at": _now(), "job": job, "ok": False, "changed": None,
           "key": "", "before": {}, "after": {}, "diff": "", "diff_cut": 0, "at_stake": [],
           "message": ""}
    if not story or not story.get("key"):
        out["message"] = "this job has no story read from Jira; there is nothing to compare"
        return out
    key = str(story["key"])
    out["key"] = key
    before_text = str(told.get("story_text") or "")
    out["before"] = {"revision": str(story.get("revision") or ""), "summary": story.get("summary", ""),
                     "acceptance_criteria": bool((story.get("acceptance_criteria") or {}).get("present")),
                     "read_at": str(told.get("at") or "")}
    scratch = os.path.join(job_dir, "story-check")
    shutil.rmtree(scratch, ignore_errors=True)
    os.makedirs(scratch, exist_ok=True)
    diag.step(f"Reading {key} again to see whether it changed")
    try:
        try:
            got = intake.gather_jira(key, scratch, "now", connect=connect, transport=transport,
                                     attachments=(attachments if attachments is not None
                                                  else (lambda i, w: [])))
        except Exception as e:                           # noqa: BLE001
            diag.trace()
            out["message"] = (f"the story could not be read again ({type(e).__name__}); "
                              f"nothing is known")
            return out
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    if not got.get("ok"):
        out["message"] = f"the story could not be read again: {got.get('reason')}"
        return out
    now = got["story"]
    # The job holds the story's text as the brief has it, cut at the same
    # length; the text read now is cut the same way, or a long story would
    # always seem to have changed.
    after_text = str(got.get("own_text") or "")[:intake.MAX_STORY_CHARS]
    out["after"] = {"revision": str(now.get("revision") or ""), "summary": now.get("summary", ""),
                    "acceptance_criteria": bool((now.get("acceptance_criteria") or {}).get("present"))}
    same_text = before_text.strip() == after_text.strip()
    same_rev = _same_revision(out["before"]["revision"], out["after"]["revision"])
    out["changed"] = not (same_text and (same_rev or not out["before"]["revision"]))
    if not same_text:
        lines = list(difflib.unified_diff(before_text.splitlines(), after_text.splitlines(),
                                          fromfile=f"{key} as read {out['before']['read_at']}",
                                          tofile=f"{key} now", lineterm="", n=2))
        out["diff"] = "\n".join(lines[:MAX_DIFF_LINES])
        out["diff_cut"] = max(0, len(lines) - MAX_DIFF_LINES)
    out["ok"] = True
    if out["changed"]:
        out["at_stake"] = at_stake(job_dir)
        what = ("its text changed" if not same_text else
                "Jira's revision changed, though the text read here is the same (a field, a "
                "comment or an attachment)")
        out["message"] = (f"{key} has changed since this job read it: {what}."
                          + (" Acceptance criteria " + ("appeared" if out["after"]["acceptance_criteria"]
                                                       and not out["before"]["acceptance_criteria"]
                                                       else "disappeared")
                             if out["after"]["acceptance_criteria"] != out["before"]["acceptance_criteria"]
                             else "")
                          + (f" {len(out['at_stake'])} thing(s) in this job were made from the "
                             f"earlier reading." if out["at_stake"] else ""))
    else:
        out["message"] = f"{key} is as this job read it ({out['before']['read_at'] or 'earlier'})."
    return out


def _same_revision(before: str, after: str) -> bool:
    """A revision is `updated|hash`. The hash is of the whole issue, which
    moves when someone merely views or watches it; so when both sides
    carry Jira's `updated` time, that alone decides."""
    b, a = before.split("|", 1), after.split("|", 1)
    if b[0] and a[0]:
        return b[0] == a[0]
    return bool(before) and before == after


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--job", required=True)
    a = ap.parse_args(argv)
    try:
        job_dir = intake.job_dir(a.job)
    except ValueError as e:
        print(f"REFUSED {e}")
        return 2
    if not os.path.isdir(job_dir):
        print(f"REFUSED there is no job {a.job!r}")
        return 2
    r = check(a.job)
    try:
        with io.open(os.path.join(job_dir, RESULT), "w", encoding="utf-8") as fh:
            json.dump(r, fh, indent=1, ensure_ascii=False)
    except OSError:
        diag.trace()
    print((" ok   " if r["ok"] else "FAIL  ") + r["message"])
    for line in r["at_stake"]:
        print(f"  - {line}")
    if r["diff"]:
        print(r["diff"][:4000])
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(diag.run("storycheck", main))
