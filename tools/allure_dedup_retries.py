"""
Deduplicate Allure retry attempts so the Overview headline count reads
truthfully: one row per unique test, not one row per invocation.

WHY THIS EXISTS
---------------
Every emitted @Test carries `retryAnalyzer = RetryAnalyzer.class` and
`retry.maxCount=2` in application.properties, so a single failing test
can produce up to 3 result files under `target/allure-results/`:
    <uuid1>-result.json   (attempt 1 -- failed)
    <uuid2>-result.json   (attempt 2 -- failed)
    <uuid3>-result.json   (attempt 3 -- terminal outcome)

Allure's Overview dashboard sums result files, so 14 real failures with
retries can inflate the top-line count by 14-28 entries. Suites view is
correct (deduplicated by historyId internally), but stakeholders read
the Overview number.

Choice: STRIP old attempts from disk BEFORE running `mvn allure:report`
/ `allure:serve`. Report dashboard then shows honest counts. Trade-off
per Round-13 discussion: retry telemetry (how flaky a test was) is
lost. If you need to keep the telemetry, don't run this script -- use
the Suites view for the honest count and read the Overview as
"attempts, not tests".

WHAT IT DOES
------------
1. Scan `target/allure-results/*-result.json` (default) or a path passed
   as argv[1].
2. Group results by `historyId` (Allure's stable per-test hash). Fall
   back to `fullName + parameters` for the rare result missing a
   historyId (older listeners, custom filters).
3. Within each group, keep the ONE with the largest `stop` timestamp
   (the terminal attempt). Everything older is a retry that failed.
4. For each dropped result: delete its JSON, plus every attachment file
   named in its `attachments[].source` -- SKIPPING any attachment that
   the surviving result (or any *-container.json) also references, so
   the survivor's UI stays intact.

USAGE
-----
    python tools/allure_dedup_retries.py
    python tools/allure_dedup_retries.py path/to/allure-results

Exits 0 on success, 2 on missing directory, 3 on unreadable JSON.
Prints a summary block at the end. Idempotent -- rerunning a second
time is a no-op because there's nothing left to dedupe.

NOT RUN AUTOMATICALLY -- opt-in. See README for the recommended flow:
    mvn test "-DsuiteXmlFile=Suites/Accountmemberregression_Regression.xml"
    python tools/allure_dedup_retries.py
    mvn allure:serve
"""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from typing import Any


DEFAULT_RESULTS_DIR = os.path.join("target", "allure-results")


def _load_result(path: str) -> dict[str, Any] | None:
    """Read one -result.json. None if the file is corrupt -- we log
    and skip rather than aborting the whole dedup pass on a single
    bad file (interrupted test runs sometimes leave truncated JSON)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"[dedup] SKIP unreadable result: {path} ({e})",
              file=sys.stderr)
        return None


def _group_key(result: dict[str, Any]) -> str:
    """Prefer historyId (Allure's stable per-test hash) so a test with
    identical parameters across runs groups correctly. Fall back to
    (fullName, sorted params) so results missing a historyId still
    dedupe -- happens when a custom TestNG filter strips history
    metadata."""
    hid = result.get("historyId")
    if hid:
        return f"h:{hid}"
    full = result.get("fullName", "") or result.get("name", "")
    params = result.get("parameters") or []
    # Deterministic: (name, value) tuples sorted by name.
    param_key = ",".join(
        f"{p.get('name','')}={p.get('value','')}"
        for p in sorted(params, key=lambda p: p.get("name", ""))
    )
    return f"n:{full}|{param_key}"


def _stop_millis(result: dict[str, Any]) -> int:
    """Latest `stop` wins. Missing stop -> -1 so it sorts before
    anything real (i.e., a result with a real timestamp preempts
    one that never finished writing)."""
    v = result.get("stop")
    return int(v) if isinstance(v, int) else -1


def _collect_attachment_sources(result: dict[str, Any]) -> set[str]:
    """Every filename this result claims under attachments[].source,
    plus the same walk over steps[].attachments and any before/after
    fixture attachments inline in the result. Empty set on missing
    fields -- Allure schema is loose."""
    out: set[str] = set()
    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            for att in node.get("attachments") or []:
                src = att.get("source") if isinstance(att, dict) else None
                if src:
                    out.add(src)
            for step in node.get("steps") or []:
                _walk(step)
            for k in ("befores", "afters"):
                for fx in node.get(k) or []:
                    _walk(fx)
        elif isinstance(node, list):
            for item in node:
                _walk(item)
    _walk(result)
    return out


def main(argv: list[str]) -> int:
    results_dir = argv[1] if len(argv) > 1 else DEFAULT_RESULTS_DIR
    if not os.path.isdir(results_dir):
        print(f"[dedup] ERROR: no such directory: {results_dir}",
              file=sys.stderr)
        return 2

    # 1. Load every *-result.json
    result_paths: list[str] = []
    for name in os.listdir(results_dir):
        if name.endswith("-result.json"):
            result_paths.append(os.path.join(results_dir, name))
    if not result_paths:
        print(f"[dedup] no *-result.json in {results_dir} -- nothing to do")
        return 0

    parsed: list[tuple[str, dict[str, Any]]] = []
    for p in result_paths:
        d = _load_result(p)
        if d is not None:
            parsed.append((p, d))

    # 2. Group by historyId (with fullName+params fallback)
    groups: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for path, d in parsed:
        groups[_group_key(d)].append((path, d))

    # 3. Pick winners; enumerate losers
    losers: list[tuple[str, dict[str, Any]]] = []
    dedup_groups = 0
    for key, entries in groups.items():
        if len(entries) < 2:
            continue
        dedup_groups += 1
        # Sort ascending by stop; winner is last (largest stop).
        entries.sort(key=lambda e: _stop_millis(e[1]))
        winner = entries[-1]
        for entry in entries[:-1]:
            losers.append(entry)

    if not losers:
        print(f"[dedup] scanned {len(parsed)} result(s) in {results_dir} -- "
              "no retry duplicates to remove")
        return 0

    # 4. Collect ALL surviving attachment sources -- across every result
    # that WON its group AND across every container.json in the dir.
    # Deletion of an attachment shared with a survivor would break the
    # survivor's UI, so those attachments must stay even if a losing
    # result references them.
    winners_by_key = {
        key: sorted(entries, key=lambda e: _stop_millis(e[1]))[-1][1]
        for key, entries in groups.items()
    }
    surviving_attachments: set[str] = set()
    for wd in winners_by_key.values():
        surviving_attachments |= _collect_attachment_sources(wd)
    for name in os.listdir(results_dir):
        if name.endswith("-container.json"):
            cd = _load_result(os.path.join(results_dir, name))
            if cd is not None:
                surviving_attachments |= _collect_attachment_sources(cd)

    # 5. Delete losing results + their orphan attachments
    deleted_results = 0
    deleted_attachments = 0
    skipped_shared = 0
    for path, d in losers:
        for src in _collect_attachment_sources(d):
            if src in surviving_attachments:
                skipped_shared += 1
                continue
            att_path = os.path.join(results_dir, src)
            try:
                os.remove(att_path)
                deleted_attachments += 1
            except FileNotFoundError:
                pass  # already gone; that's fine
            except OSError as e:
                print(f"[dedup] WARN could not remove attachment "
                      f"{att_path}: {e}", file=sys.stderr)
        try:
            os.remove(path)
            deleted_results += 1
        except OSError as e:
            print(f"[dedup] WARN could not remove result {path}: {e}",
                  file=sys.stderr)

    print()
    print("[dedup] summary")
    print(f"        results scanned:      {len(parsed)}")
    print(f"        unique tests:         {len(groups)}")
    print(f"        groups with retries:  {dedup_groups}")
    print(f"        results removed:      {deleted_results}")
    print(f"        attachments removed:  {deleted_attachments}")
    print(f"        attachments shared:   {skipped_shared} "
          f"(kept -- also referenced by a surviving result)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
