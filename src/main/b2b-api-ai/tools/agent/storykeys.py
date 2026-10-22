"""Xray keys a story's EARLIER jobs hold, for a job that has none.

    python tools/agent/storykeys.py check --job J
    python tools/agent/storykeys.py take  --job J [--adopt TC-003=ABC-12,...]

Seen live: a second job for a story was designed and automated without
running Create in Xray. Its xray-tests.json did not exist, so Automate had
no key to hand Cursor or to put in the rows -- while an earlier job of the
same story had created the tests. Nothing said so.

* `earlier(...)`  -- the other jobs of the same story and the keys they
  hold (their xray-tests.json), read as plain files. Used by the Automate
  warning (loop.py) and by Create in Xray (xraytests.py), so it imports
  neither.
* `check`  -- this job's designed cases that have no key, matched against
  those tests with Create in Xray's own rule (xraytests.match_linked): the
  same title, or the same steps, is the same test; a title that only looks
  alike is HELD for a person. Writes story-keys.json for the page.
* `take`   -- writes the matched keys (and the ones a person chose with
  --adopt) into THIS job's xray-tests.json, marked with the job they came
  from. Nothing is sent to Jira; a case that has a key keeps it.

A stand-in's key and a real one never mix (the `mock` flag of an entry
must be the same kind as this job's Jira).
"""
from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
import re
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
KEYS = "xray-tests.json"
RESULT = "story-keys.json"
_KEY_RX = re.compile(r"^[A-Z][A-Z0-9_]+-[0-9]+$")
WHAT = "design"


def say(msg: str = "") -> None:
    print(msg, flush=True)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(path: str) -> dict:
    try:
        with io.open(path, encoding="utf-8-sig") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def story_of(job_dir: str) -> str:
    story = _json(os.path.join(job_dir, "intake.json")).get("story")
    key = str(story.get("key") or "").upper() if isinstance(story, dict) else ""
    return key if _KEY_RX.match(key) else ""


def _project_of(job_dir: str) -> str:
    """A job's project (project.json), b2b when it has none -- as intake.job_project."""
    p = _json(os.path.join(job_dir, "project.json")).get("project")
    return str(p or "b2b").strip().lower()


def _steps(case: dict) -> list:
    return [{"action": str(s.get("action") or ""), "data": str(s.get("data") or ""),
             "expected": str(s.get("expected") or "")}
            for s in (case.get("steps") or []) if isinstance(s, dict)]


def earlier(job_dir: str, story: str = "", mocked: bool = False) -> list:
    """[{job, at, tests: [{case, key, title, steps}]}] -- every OTHER job in
    the same .workbench folder whose story is `story` and whose
    xray-tests.json holds design keys of this kind (mock or real). Newest
    first. Steps come from that job's design.json (None when not there)."""
    story = (story or story_of(job_dir)).upper()
    if not story:
        return []
    here = os.path.abspath(job_dir)
    base = os.path.dirname(here)
    out = []
    try:
        names = sorted(os.listdir(base), reverse=True)
    except OSError:
        return []
    for name in names:
        other = os.path.join(base, name)
        if os.path.abspath(other) == here or not os.path.isdir(other):
            continue
        if story_of(other) != story or _project_of(other) != _project_of(here):
            continue                                       # another story, or the other project's Jira
        kept = _json(os.path.join(other, KEYS)).get("tests")
        if not isinstance(kept, dict):
            continue
        designed = {str(c.get("id")): c for c in
                    (_json(os.path.join(other, "design.json")).get("test_cases") or [])
                    if isinstance(c, dict)}
        tests = []
        for ident, entry in kept.items():
            if not isinstance(entry, dict) or not str(ident).startswith(WHAT + ":"):
                continue
            if bool(entry.get("mock")) != bool(mocked):
                continue
            key = str(entry.get("key") or "").upper()
            if not _KEY_RX.match(key):
                continue
            case = str(ident).split(":", 1)[1]
            steps = _steps(designed[case]) if case in designed else None
            if steps is None and isinstance(entry.get("base"), dict):
                steps = _steps(entry["base"])
            tests.append({"case": case, "key": key, "title": str(entry.get("title") or ""),
                          "steps": steps})
        if tests:
            out.append({"job": name, "at": str(_json(os.path.join(other, KEYS)).get("at") or ""),
                        "tests": tests})
    return out


def candidates(job_dir: str, story: str = "", mocked: bool = False, exclude=()) -> list:
    """The earlier jobs' tests as Create in Xray's `linked` list -- [{key,
    summary, job, steps}], one per key (the newest job's), less `exclude`."""
    exclude = {str(k).upper() for k in exclude}
    seen, out = set(), []
    for j in earlier(job_dir, story, mocked):
        for t in j["tests"]:
            if t["key"] in seen or t["key"] in exclude:
                continue
            seen.add(t["key"])
            out.append({"key": t["key"], "summary": t["title"], "status": "", "job": j["job"],
                        "steps": t["steps"]})
    return out


def warning(job_dir: str, case_ids, have: dict, mocked: bool = False) -> str:
    """One line for the Automate log, or "": these cases have no key in this
    job, and earlier jobs of the story hold keys."""
    missing = sorted(str(c) for c in case_ids if str(c) not in (have or {}))
    if not missing:
        return ""
    jobs = earlier(job_dir, "", mocked)
    if not jobs:
        return ""
    held = ", ".join(f"{j['job']} ({len(j['tests'])} key(s))" for j in jobs[:4])
    return (f"{len(missing)} case(s) to automate have no Xray key in this job ({', '.join(missing[:8])}), "
            f"but earlier job(s) of {story_of(job_dir)} hold keys: {held}. The tests go on without "
            f"keys, so their rows carry none and Publish has nothing for them. To use those keys: "
            f"\"Take keys from the story's earlier jobs\" (Automate Test Case tab, under Create in "
            f"Xray), then Automate again.")


# ---- check and take (they need Create in Xray's matching) ----------------------------

def _load(name: str, path: str):
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


def _xt():
    return _load("storykeys_xraytests", os.path.join(HERE, "xraytests.py"))


class Refused(RuntimeError):
    pass


def check(job: str, root: str = "") -> dict:
    """What this job could take: {story, mock, jobs, todo, same, held}."""
    xt = _xt()
    root = root or xt.ROOT
    job_dir = xt.loop.intake.job_dir(job, root)
    story = story_of(job_dir)
    if not story:
        raise Refused("this job has no Jira story key (the story was pasted): there is no story "
                      "to find earlier jobs of")
    mocked = bool(xt.search.mock_jira())
    try:
        cases = xt.cases_of(xt.publish.result_for(job_dir, WHAT), WHAT)
    except xt.Refused as e:
        raise Refused(f"this job has no designed cases to match: {e}") from e
    have = xt.keys_for(job_dir, WHAT, mocked)
    todo = [c for c in cases if c["id"] not in have]
    linked = candidates(job_dir, story, mocked, exclude=have.values())
    by_key = {t["key"]: t for t in linked}
    same, near = xt.match_linked(todo, linked, lambda key: (by_key.get(key) or {}).get("steps"))
    out = {"kind": "story-keys", "job": job, "story": story, "mock": mocked, "at": _now(),
           "jobs": [{"job": j["job"], "keys": len(j["tests"])} for j in earlier(job_dir, story, mocked)],
           "have": len(have), "todo": len(todo), "same": [], "held": []}
    for case in todo:
        if case["id"] in same:
            t, how = same[case["id"]]
            out["same"].append({"id": case["id"], "title": case["title"], "key": t["key"],
                                "xray_title": t["summary"], "how": how, "from_job": t["job"]})
        elif case["id"] in near:
            t, score = near[case["id"]]
            out["held"].append({"id": case["id"], "title": case["title"], "key": t["key"],
                                "xray_title": t["summary"], "score": score, "from_job": t["job"]})
    return out


def take(job: str, root: str = "", adopt: dict = None) -> dict:
    """Write the matched keys (and `adopt` {case: key}) into this job's
    xray-tests.json. Returns the check, with `taken`."""
    xt = _xt()
    root = root or xt.ROOT
    job_dir = xt.loop.intake.job_dir(job, root)
    r = check(job, root)
    adopt = {str(c): str(k).upper() for c, k in (adopt or {}).items()}
    mocked = r["mock"]
    cases = {c["id"]: c for c in xt.cases_of(xt.publish.result_for(job_dir, WHAT), WHAT)}
    have = xt.keys_for(job_dir, WHAT, mocked)
    pool = {t["key"]: t for t in candidates(job_dir, r["story"], mocked, exclude=have.values())}
    chosen = {row["id"]: (row["key"], row["how"], row["from_job"]) for row in r["same"]}
    for cid, key in adopt.items():
        if cid not in cases:
            raise Refused(f"{cid} is not a designed case of this job")
        if cid in have:
            raise Refused(f"{cid} already has the key {have[cid]} in this job; nothing was taken")
        if key not in pool:
            raise Refused(f"{key} is not a test an earlier job of {r['story']} holds (or another case "
                          f"of this job has it); nothing was taken")
        chosen[cid] = (key, "chosen", pool[key]["job"])
    used = [k for k, _h, _j in chosen.values()]
    twice = sorted({k for k in used if used.count(k) > 1})
    if twice:
        raise Refused(f"one key for two cases ({', '.join(twice)}); nothing was taken")
    kept = xt.read_keys(job_dir)
    taken = []
    for cid, (key, how, from_job) in sorted(chosen.items()):
        case = cases[cid]
        kept["tests"][f"{WHAT}:{cid}"] = {
            "key": key, "title": case["title"], "fingerprint": xt.fingerprint(case),
            "story": r["story"], "mock": mocked, "at": _now(), "found": f"earlier job ({how})",
            "from_job": from_job}
        xt._log(job_dir, f"TAKEN {WHAT}:{cid} -> {key} from job {from_job} ({how}); nothing sent to Jira")
        taken.append({"id": cid, "key": key, "how": how, "from_job": from_job})
    if taken:
        xt.write_keys(job_dir, kept)
    r["taken"] = taken
    r["held"] = [h for h in r["held"] if h["id"] not in chosen]
    return r


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("check", "take"):
        p = sub.add_parser(name)
        p.add_argument("--job", required=True)
        if name == "take":
            p.add_argument("--adopt", default="", help='"TC-003=ABC-12,..." -- a held look-alike is that test')
    a = ap.parse_args(argv)
    try:
        adopt = {}
        for part in [p.strip() for p in getattr(a, "adopt", "").split(",") if p.strip()]:
            cid, _, key = part.partition("=")
            if not cid.strip() or not _KEY_RX.match(key.strip().upper()):
                raise Refused(f"--adopt takes CASE=KEY pairs, like TC-003=ABC-12; not {part[:60]!r}")
            adopt[cid.strip()] = key.strip().upper()
        r = take(a.job, adopt=adopt) if a.cmd == "take" else check(a.job)
    except (Refused, ValueError) as e:
        say(f"REFUSED {e}")
        return 2
    xt = _xt()
    with io.open(os.path.join(xt.loop.intake.job_dir(a.job), RESULT), "w", encoding="utf-8") as fh:
        json.dump(r, fh, indent=1, ensure_ascii=False)
    if not r["jobs"]:
        say(f" ok  no earlier job of {r['story']} holds Xray keys: nothing to take. "
            f"Create in Xray makes them.")
        return 0
    say(f" ..  earlier job(s) of {r['story']}: "
        + ", ".join(f"{j['job']} ({j['keys']} key(s))" for j in r["jobs"]))
    say(f" ..  this job: {r['have']} case(s) with a key, {r['todo']} without")
    for row in r.get("taken") or []:
        say(f" ok  {row['id']} -> {row['key']} (from {row['from_job']}, {row['how']})")
    if a.cmd == "check":
        for row in r["same"]:
            say(f"  same  {row['id']} = {row['key']} ({row['how']}, job {row['from_job']}): "
                f"{row['title'][:90]}")
    for row in r["held"]:
        say(f"  HELD  {row['id']} looks like {row['key']} ({row['score']:.0%} of title words, job "
            f"{row['from_job']}): \"{row['title'][:70]}\" / \"{row['xray_title'][:70]}\" -- take it with "
            f"--adopt {row['id']}={row['key']}, or leave it for Create in Xray")
    if a.cmd == "take":
        say(f" ok  {len(r['taken'])} key(s) taken into this job; nothing was sent to Jira."
            + (" Automate (again) puts them in the rows." if r["taken"] else ""))
    elif r["same"]:
        say(f" ..  {len(r['same'])} can be taken: \"Take keys from the story's earlier jobs\"")
    return 0


if __name__ == "__main__":
    diag = _xt().diag
    sys.exit(diag.run("storykeys", main))
