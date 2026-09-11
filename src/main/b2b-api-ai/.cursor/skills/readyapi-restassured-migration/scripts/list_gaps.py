#!/usr/bin/env python3
"""List ra_converter gaps from _audit/<suite> CSVs.

Run from the repo root:

    python .cursor/skills/readyapi-restassured-migration/scripts/list_gaps.py
    python .cursor/skills/readyapi-restassured-migration/scripts/list_gaps.py --suite programaccountregression
"""
from __future__ import annotations

import argparse
import csv
import os
import sys

GAP_COVERAGE = {"TODO", "STUB", "PARTIAL"}
HIGH_SEV = {"HIGH", "BLOCKER"}

# Coverage column names seen in assertions.csv / groovy.csv / steps.csv
COVERAGE_KEYS = ("coverage", "Coverage", "result")


def repo_root() -> str:
    skill_scripts = os.path.dirname(os.path.abspath(__file__))
    # scripts -> skill -> skills -> .cursor -> repo
    return os.path.abspath(os.path.join(skill_scripts, "..", "..", "..", ".."))


def audit_suites(root: str) -> list[str]:
    audit = os.path.join(root, "_audit")
    if not os.path.isdir(audit):
        return []
    return sorted(
        n for n in os.listdir(audit)
        if os.path.isdir(os.path.join(audit, n))
    )


def _coverage(row: dict) -> str:
    for k in COVERAGE_KEYS:
        if k in row and row[k]:
            return row[k].strip().upper()
    return ""


def _read_csv(path: str) -> list[dict]:
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def summarize_coverage(rows: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        cov = _coverage(row)
        if cov in GAP_COVERAGE:
            counts[cov] = counts.get(cov, 0) + 1
    return counts


def print_suite(root: str, suite: str, limit: int) -> None:
    base = os.path.join(root, "_audit", suite)
    print(f"\n=== {suite} ===")
    for name in ("assertions.csv", "groovy.csv", "steps.csv"):
        rows = _read_csv(os.path.join(base, name))
        counts = summarize_coverage(rows)
        total_gap = sum(counts.values())
        print(f"  {name}: {total_gap} gap row(s) {counts or '-'}")
        shown = 0
        for row in rows:
            if _coverage(row) not in GAP_COVERAGE:
                continue
            case = row.get("case") or row.get("test_case") or row.get("name") or ""
            step = row.get("step") or row.get("step_name") or row.get("type") or ""
            print(f"    [{_coverage(row)}] {case} :: {step}")
            shown += 1
            if shown >= limit:
                print(f"    ... truncated at {limit}")
                break

    unmapped = _read_csv(os.path.join(base, "unmapped.csv"))
    if unmapped:
        print(f"  unmapped.csv: {len(unmapped)} row(s)")
        for row in unmapped[:limit]:
            print("   ", {k: row[k] for k in list(row)[:4]})

    pre = _read_csv(os.path.join(base, "preflight.csv"))
    high = [
        r for r in pre
        if (r.get("severity") or r.get("level") or "").upper() in HIGH_SEV
    ]
    if high:
        cats: dict[str, int] = {}
        for r in high:
            cat = r.get("category") or r.get("type") or "?"
            cats[cat] = cats.get(cat, 0) + 1
        print(f"  preflight HIGH/BLOCKER: {len(high)}  {cats}")
        for r in high[:limit]:
            print(f"    [{r.get('severity')}] {r.get('category')} :: {r.get('case')}")
        if len(high) > limit:
            print(f"    ... truncated at {limit}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--suite", help="Audit folder name (XML basename)")
    p.add_argument("--limit", type=int, default=15, help="Max detail rows per file")
    args = p.parse_args()
    root = repo_root()
    suites = [args.suite] if args.suite else audit_suites(root)
    if args.suite and args.suite not in audit_suites(root):
        print(f"No _audit/{args.suite} under {root}", file=sys.stderr)
        return 1
    if not suites:
        print(f"No _audit folders under {root}", file=sys.stderr)
        return 1
    print(f"repo: {root}")
    for s in suites:
        print_suite(root, s, args.limit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
