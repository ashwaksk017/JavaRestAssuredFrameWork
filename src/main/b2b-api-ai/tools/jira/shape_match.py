"""Does this story's request already exist? -- index hit to concrete target.

Takes a candidate request extracted from a Jira story, scores it
with the CONVERTER'S OWN signature, and answers with somewhere to put the
test rather than an opinion.

    python tools/jira/shape_match.py --candidate candidate.json
    python tools/jira/shape_match.py --candidate candidate.json --json out.json
    python tools/jira/shape_match.py --candidate candidate.json --on-duplicate skip

WHAT A MATCH MEANS
------------------
`_rest_shape_sig` keys on (verb, path, media-type, body-shape, path-param
binding, query-param names) and deliberately leaves VALUES out, because
values become CSV cells. So an exact match says: this call is already
automated, and a story that differs only in data is a new ROW, not a new
test.

A MATCH IS NOT A UNIQUE DESTINATION
-----------------------------------
Measured: of 153 shapes shared by more than one case, only 25 map to a
single method. The other 128 split across classes, because the converter
groups by business area as well as by call shape. So the same request can
legitimately be automated in several places, and picking one would be a
guess dressed as an answer. When there are several, they are all listed
and a person chooses.

DUPLICATE
---------
Shape plus expected status matching an existing row is a SUSPECTED
duplicate, not a proven one: the index holds no values, by design, so this
cannot prove the data is the same. It prompts. `--on-duplicate` makes the
answer explicit for a pipeline that cannot be asked, and defaults to
failing there rather than guessing.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import shapes  # noqa: E402

ROOT = shapes.ROOT

NEW = "NEW"
EXACT_ONE = "EXACT_ONE"
EXACT_MANY = "EXACT_MANY"
LOOSE_ONLY = "LOOSE_ONLY"
DUPLICATE_SUSPECT = "DUPLICATE_SUSPECT"

ACTION = {
    NEW: "create a new test -- nothing automated covers this call",
    EXACT_ONE: "add a data row to the existing test",
    EXACT_MANY: "add a data row -- but choose which of these it belongs to",
    LOOSE_ONLY: "confirm first: the shape matches only once literals and "
                "array lengths are ignored",
    DUPLICATE_SUSPECT: "possible duplicate -- same call AND same expected "
                       "status already present",
}


def load_candidate(path: str) -> dict:
    with io.open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    steps = doc.get("steps")
    if not isinstance(steps, list) or not steps:
        raise SystemExit(f"{path}: needs a non-empty `steps` array")
    for i, s in enumerate(steps, 1):
        for required in ("verb", "path"):
            if not s.get(required):
                raise SystemExit(f"{path}: step {i} has no `{required}`. A "
                                 f"request with no {required} cannot be "
                                 f"matched against anything")
    return doc


def destination(entry: dict) -> str:
    """Where a change to this target would actually have to be made.

    `csv/manual/` is the one tracked, hand-written row-file location, so
    it must NOT be read as generated. Treating every path under `csv/` as
    generated sent an author to the ReadyAPI XML for a row that belonged
    in the file right next to their test.
    """
    cls = (entry.get("java_class_fqn") or "")
    csv = (entry.get("csv_path") or "").replace("\\", "/")
    if "/csv/manual/" in f"/{csv}" or cls.startswith("com.hi.api.tests.jira."):
        return "this repository (hand-written test)"
    if ".tests.imported." in cls or "/csv/" in f"/{csv}":
        return ("ReadyAPI XML (generated: the next convert overwrites any "
                "edit here, so the row belongs upstream in the suite's XML)")
    return "this repository (hand-written test)"


def match(candidate: dict, index: dict) -> dict:
    steps = candidate["steps"]
    case = shapes.candidate_case(steps, candidate.get("issue_key", "CANDIDATE"))
    ex, lo = shapes.exact_sig(case), shapes.loose_sig(case)

    exact_hits = index.get("exact", {}).get(ex, [])
    loose_hits = index.get("loose", {}).get(lo, [])

    # Expected status is not in either signature -- a 200 and a 400 of the
    # same call share one method and differ per row. It is used only to
    # tell "another row" from "a row that may already be there".
    want = [str(s.get("expected_status") or "").strip() for s in steps]
    want_status = next((w for w in want if w), "")

    result = {
        "issue_key": candidate.get("issue_key", ""),
        "exact_fingerprint": ex,
        "expected_status": want_status,
        "exact_matches": exact_hits,
        "loose_matches": [h for h in loose_hits if h not in exact_hits],
    }

    if not exact_hits and not loose_hits:
        result["verdict"] = NEW
    elif not exact_hits:
        result["verdict"] = LOOSE_ONLY
    else:
        same_status = [h for h in exact_hits
                       if want_status and h.get("expected_status") == want_status]
        if same_status:
            result["verdict"] = DUPLICATE_SUSPECT
            result["duplicate_candidates"] = same_status
        elif len(exact_hits) == 1:
            result["verdict"] = EXACT_ONE
        else:
            result["verdict"] = EXACT_MANY

    result["action"] = ACTION[result["verdict"]]
    targets = result.get("duplicate_candidates") or exact_hits or result["loose_matches"]
    result["targets"] = [{
        "suite": t.get("suite", ""),
        "case_name": t.get("case_name", ""),
        "java_class_fqn": t.get("java_class_fqn", ""),
        "java_method": t.get("java_method", ""),
        "csv_path": t.get("csv_path", ""),
        "expected_status": t.get("expected_status", ""),
        "destination": destination(t),
    } for t in targets[:25]]
    result["target_count"] = len(targets)
    return result


def report(result: dict) -> str:
    out: list[str] = []
    key = result.get("issue_key") or "(no issue key)"
    out.append(f"{key}  ->  {result['verdict']}")
    out.append(f"  {result['action']}")
    if result.get("expected_status"):
        out.append(f"  expected status in the story: {result['expected_status']}")
    out.append(f"  shape fingerprint: {result['exact_fingerprint'][:60]}...")
    if not result["targets"]:
        out.append("\n  no existing test matches this call.")
        return "\n".join(out)

    shown = result["targets"]
    out.append(f"\n  {result['target_count']} existing target(s)"
               + (f", first {len(shown)} shown" if result["target_count"] > len(shown) else "")
               + ":")
    for t in shown:
        out.append(f"    {t['suite']}  {t['case_name']}")
        out.append(f"      method : {t['java_class_fqn']}#{t['java_method']}")
        out.append(f"      csv    : {t['csv_path'] or '(none)'}")
        out.append(f"      status : {t['expected_status'] or '(not recorded)'}")
        out.append(f"      lands  : {t['destination']}")
    if result["verdict"] == EXACT_MANY:
        out.append("\n  The same call is automated in more than one place. That "
                   "is legitimate -- the converter groups by business area as "
                   "well as by call shape -- so choose, rather than letting a "
                   "tool guess.")
    if result["verdict"] == LOOSE_ONLY:
        out.append("\n  Loose only: these agree once literal TYPES and array "
                   "LENGTHS are ignored. A recording holds \"${Inputs#n}\" (a "
                   "string) where a story holds 5 (a number). Confirm the call "
                   "really is the same before adding a row to it.")
    return "\n".join(out)


def resolve_duplicate(result: dict, mode: str) -> str:
    """'create' | 'skip', deciding or asking."""
    if result["verdict"] != DUPLICATE_SUSPECT:
        return "create"
    if mode == "create":
        return "create"
    if mode == "skip":
        return "skip"
    if mode == "fail":
        return "fail"
    if not sys.stdin.isatty():
        # A pipeline that cannot be asked must not pick on its own.
        return "fail"
    print("\n  This looks like a duplicate: the same call with the same "
          "expected status is already covered above.")
    while True:
        answer = input("  [c]reate anyway / [s]kip ? ").strip().lower()
        if answer in ("c", "create"):
            return "create"
        if answer in ("s", "skip"):
            return "skip"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--candidate", required=True,
                    help="JSON with an `steps` array: verb, path, body, "
                         "path_params, query_params, expected_status")
    ap.add_argument("--index", default="target/shape-index.json",
                    help="from `python tools/jira/shapes.py --dump PATH`")
    ap.add_argument("--on-duplicate", default="prompt",
                    choices=("prompt", "skip", "create", "fail"),
                    help="prompt at a terminal; fail in a pipeline, which is "
                         "the default when stdin is not a tty")
    ap.add_argument("--json", metavar="PATH", help="write the verdict")
    args = ap.parse_args(argv)

    index_path = args.index
    if not os.path.isabs(index_path):
        index_path = os.path.join(ROOT, index_path)
    if not os.path.isfile(index_path):
        print(f"no shape index at {args.index}. Build it first:\n"
              f"  python tools/jira/shapes.py --dump {args.index}")
        return 2

    candidate = load_candidate(args.candidate)
    index = shapes.load(index_path)
    result = match(candidate, index)
    print(report(result))

    decision = resolve_duplicate(result, args.on_duplicate)
    result["duplicate_decision"] = decision
    if result["verdict"] == DUPLICATE_SUSPECT:
        print(f"\n  decision: {decision}")

    if args.json:
        shapes.save({"schema": 1, "match": result}, args.json)
        print(f"\n  -> {args.json}")

    if decision == "fail":
        print("  Refusing to choose: this is a suspected duplicate and "
              "nothing can be asked here. Re-run with --on-duplicate "
              "create or skip once a person has decided.")
        return 1
    if decision == "skip":
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
