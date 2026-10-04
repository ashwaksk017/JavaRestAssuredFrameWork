"""Record a known failure in baseline.json, from a real run.

This is option 4 of the choices verify_all offers for a failure it will
not fix on its own ("Record in baseline.json as a known artifact").

WHY A TOOL AND NOT AN EDITOR
----------------------------
The entry is keyed by a 40-character fingerprint. Hand-copying it has two
silent failure modes and no loud ones: paste the wrong fingerprint and the
entry matches nothing (the failure keeps being reported, and the reason
nobody can find is a typo); paste a STALE one and verify_all reports the
acceptance as not describing the failure. Either way the file says
something true-looking that is wrong. Reading it out of the records file
removes the step entirely.

    python tools/verify_all.py --json target/verify.json
    python tools/autofix/accept.py --records target/verify.json \
        --check step-parity --expires 2027-01-15 \
        --reason "what it is and why it is tolerable"

Every check here is fail-closed: an acceptance is a hole in the gate, so
it is better to refuse a vague one than to record it.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import failure_record as fr  # noqa: E402

# An acceptance has to survive being read by someone else in three
# months. These say nothing, so they are refused rather than stored.
_EMPTY_REASONS = frozenset({
    "known", "known issue", "known failure", "todo", "tbd", "wip",
    "flaky", "flaky test", "ignore", "skip", "later", "fixme", "n/a", "na",
    "pre-existing", "preexisting", "legacy", "expected",
})
_MIN_REASON_CHARS = 40
_MAX_HORIZON_DAYS = 400


def _fail(msg: str) -> int:
    print(f"refused: {msg}")
    return 2


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--records", required=True,
                    help="a records file from `verify_all.py --json PATH`")
    ap.add_argument("--check", required=True, help="the check to accept")
    ap.add_argument("--reason", required=True,
                    help="what the failure IS and why it is tolerable")
    ap.add_argument("--expires", required=True, help="YYYY-MM-DD")
    ap.add_argument("--owner", default="")
    ap.add_argument("--replace", action="store_true",
                    help="replace an existing entry for this check whose "
                         "fingerprint differs (i.e. the failure changed)")
    ap.add_argument("--baseline", default=fr.baseline_path())
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--today", default="", help=argparse.SUPPRESS)  # tests
    args = ap.parse_args(argv)

    today = date.fromisoformat(args.today) if args.today else date.today()

    if not os.path.exists(args.records):
        return _fail(f"no records file at {args.records} -- run "
                     f"`verify_all.py --json {args.records}` first")
    with open(args.records, encoding="utf-8") as fh:
        doc = json.load(fh)

    rec = next((c for c in doc.get("checks", []) if c.get("name") == args.check), None)
    if rec is None:
        names = ", ".join(sorted(c.get("name", "") for c in doc.get("checks", [])))
        return _fail(f"no check named {args.check!r} in that run. Present: {names}")

    # Only a failure can be accepted. A passing or skipped check has no
    # fingerprint, and recording one would create an entry that silently
    # suppresses the FIRST failure that ever appears there.
    if rec.get("status") not in ("fail", "accepted"):
        return _fail(f"{args.check} has status {rec.get('status')!r} in that run; "
                     f"only a failing check can be accepted")
    fp = rec.get("salient_fingerprint")
    if not fp:
        return _fail(f"{args.check} carries no fingerprint in that run")

    reason = " ".join(args.reason.split())
    if reason.strip().lower().rstrip(".") in _EMPTY_REASONS or len(reason) < _MIN_REASON_CHARS:
        return _fail(f"reason is too thin to be useful later ({len(reason)} chars). "
                     f"Say what the failure is and why it is tolerable -- someone "
                     f"has to decide in three months whether it still applies")

    try:
        exp = date.fromisoformat(args.expires)
    except ValueError:
        return _fail(f"--expires {args.expires!r} is not YYYY-MM-DD")
    if exp <= today:
        return _fail(f"--expires {exp} is not in the future; an acceptance that "
                     f"is already expired is reported as a failure anyway")
    if exp - today > timedelta(days=_MAX_HORIZON_DAYS):
        return _fail(f"--expires {exp} is more than {_MAX_HORIZON_DAYS} days out; "
                     f"that is a permanent hole with a date on it")

    bl = fr.load_baseline(args.baseline)
    entries = bl.setdefault("accepted", [])
    same = [e for e in entries if e.get("check") == args.check]
    if any(e.get("salient_fingerprint") == fp for e in same):
        print(f"already accepted: {args.check} @ {fp[:12]} -- nothing to do")
        return 0
    if same and not args.replace:
        return _fail(
            f"{args.check} already has an entry, for fingerprint "
            f"{same[0].get('salient_fingerprint', '?')[:12]}, and this run "
            f"fingerprints {fp[:12]}. The failure CHANGED. Confirm the new one "
            f"is the same artifact and pass --replace, or remove the old entry")

    entry = {
        "check": args.check,
        "salient_fingerprint": fp,
        "reason": reason,
        "recorded": today.isoformat(),
        "expires": exp.isoformat(),
    }
    if args.owner:
        entry["owner"] = args.owner
    loc = rec.get("locate") or {}
    if loc.get("suites"):
        entry["suites"] = loc["suites"]
    if doc.get("git", {}).get("head"):
        entry["recorded_at_commit"] = doc["git"]["head"]

    if args.replace:
        entries[:] = [e for e in entries if e.get("check") != args.check]
    entries.append(entry)
    entries.sort(key=lambda e: (e.get("check", ""), e.get("recorded", "")))

    if args.dry_run:
        print(json.dumps(entry, indent=2))
        return 0

    with open(args.baseline, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(bl, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print(f"accepted {args.check} @ {fp[:12]} until {exp} -> {args.baseline}")
    print("verify_all will report it as KNOWN with --baseline, and as a "
          "failure without it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
