"""Exact blast radius of a candidate fix -- Phase 2 of the loop.

WHY THIS WORKS HERE
-------------------
Converts are byte-for-byte deterministic: two runs over the same inputs
produce identical output, verified by sha1. That turns "what did this fix
change?" from a judgement into a measurement -- hash the generated tree
before and after and the answer is exact, per file and per suite.

That matters because the recurring bug in this converter is a fix whose
reach is wider than its author thought: the cluster[0]-body family, where
a change meant for one negative case silently rewrote the body of every
case merged into its cluster. A fix that claims to touch one case and
rewrites 400 files is that bug reappearing, and this is what makes the
claim checkable rather than plausible.

    python tools/autofix/manifest.py --save target/before.json
    ... apply a fix, reconvert ...
    python tools/autofix/manifest.py --save target/after.json
    python tools/autofix/manifest.py --compare target/before.json target/after.json

Exit 0 when a comparison finds no change, 1 when it finds any. Saving
always exits 0.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import failure_record as fr  # noqa: E402

ROOT = fr.ROOT
SCHEMA = 1

# The code-bearing outputs of a convert. Deliberately NOT _audit/ or
# _flows/: those are reports about the run, so including them answers
# "did the report change" alongside "did the code change", and the two
# are not the same question. --include-reports adds them back.
MANIFEST_ROOTS = (
    "src/main/java/com/hi/api/support",
    "src/main/java/com/hi/api/rest/clients",
    "src/main/java/com/hi/api/templates",
    "src/main/resources/templates",
    "src/main/resources/test_data_defaults",
    "src/main/resources/config",
    "src/test/java/com/hi/api/tests/imported",
    "src/test/resources/csv",
    "Suites",
)
REPORT_ROOTS = ("_audit", "_flows")

_SUITE_RX = re.compile(
    r"^(?:src/main/java/com/hi/api/support|"
    r"src/test/java/com/hi/api/tests/imported|"
    r"src/main/resources/templates|"
    r"src/test/resources/csv|"
    r"_audit|_flows)/([^/]+)/")


def suite_of(rel: str) -> str:
    """The suite a generated path belongs to, or '' for shared files.

    Shared is a real answer, not a gap: support/CtxFields.java and the
    framework types under support/ belong to no suite, and attributing
    them to one would misreport a blast radius as suite-local.
    """
    m = _SUITE_RX.match(rel)
    if not m:
        return ""
    name = m.group(1)
    return "" if name.endswith(".java") else name


def snapshot(include_reports: bool = False) -> dict:
    roots = MANIFEST_ROOTS + (REPORT_ROOTS if include_reports else ())
    files: dict[str, str] = {}
    for root in roots:
        base = os.path.join(ROOT, root.replace("/", os.sep))
        if not os.path.isdir(base):
            continue
        for dirpath, _dirs, names in os.walk(base):
            for n in sorted(names):
                fp = os.path.join(dirpath, n)
                rel = os.path.relpath(fp, ROOT).replace("\\", "/")
                try:
                    with open(fp, "rb") as fh:
                        files[rel] = hashlib.sha1(fh.read()).hexdigest()
                except OSError:
                    continue
    return {
        "schema": SCHEMA,
        "taken": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git": fr.git_info(),
        "include_reports": bool(include_reports),
        "roots": list(roots),
        "count": len(files),
        "files": files,
    }


def compare(before: dict, after: dict) -> dict:
    b, a = before.get("files", {}), after.get("files", {})
    bk, ak = set(b), set(a)
    added = sorted(ak - bk)
    removed = sorted(bk - ak)
    changed = sorted(k for k in (bk & ak) if b[k] != a[k])
    by_suite: dict[str, dict[str, int]] = {}
    for kind, paths in (("added", added), ("removed", removed), ("changed", changed)):
        for p in paths:
            s = suite_of(p) or "(shared)"
            by_suite.setdefault(s, {"added": 0, "removed": 0, "changed": 0})
            by_suite[s][kind] += 1
    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "total": len(added) + len(removed) + len(changed),
        "by_suite": dict(sorted(by_suite.items())),
        "before_count": before.get("count", len(b)),
        "after_count": after.get("count", len(a)),
    }


def format_report(d: dict, top: int = 12) -> str:
    out: list[str] = []
    if not d["total"]:
        out.append(f"No change. {d['before_count']} generated file(s) identical.")
        return "\n".join(out)
    out.append(f"{d['total']} file(s) differ "
               f"({len(d['added'])} added, {len(d['removed'])} removed, "
               f"{len(d['changed'])} changed) "
               f"across {len(d['by_suite'])} suite(s):")
    for suite, c in d["by_suite"].items():
        bits = ", ".join(f"{v} {k}" for k, v in c.items() if v)
        out.append(f"  {suite:<40} {bits}")
    for kind in ("added", "removed", "changed"):
        paths = d[kind]
        if not paths:
            continue
        out.append(f"\n  {kind}:")
        for p in paths[:top]:
            out.append(f"    {p}")
        if len(paths) > top:
            out.append(f"    ... and {len(paths) - top} more")
    return "\n".join(out)


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save(doc: dict, path: str) -> str:
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(doc, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--save", metavar="PATH", help="hash the generated tree now")
    g.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"))
    ap.add_argument("--include-reports", action="store_true",
                    help="also hash _audit/ and _flows/")
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--json", metavar="PATH", help="write the comparison as JSON")
    args = ap.parse_args(argv)

    if args.save:
        doc = snapshot(args.include_reports)
        save(doc, args.save)
        print(f"{doc['count']} generated file(s) hashed -> {args.save}")
        if not doc["count"]:
            print("NOTE: nothing was hashed. The generated tree is absent, so a "
                  "comparison against this would report every file as added.")
        return 0

    before, after = load(args.compare[0]), load(args.compare[1])
    if before.get("include_reports") != after.get("include_reports"):
        print("refused: one snapshot includes reports and the other does not; "
              "every report file would read as added or removed")
        return 2
    d = compare(before, after)
    print(format_report(d, args.top))
    if args.json:
        save({"schema": SCHEMA, "comparison": d}, args.json)
    return 1 if d["total"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
