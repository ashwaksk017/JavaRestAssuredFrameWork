"""Every proposal, and what autonomy the record earns -- Phase 4.

WHY A LEDGER BEFORE ANY AUTONOMY
--------------------------------
"Let it apply its own fixes once it is reliable" is only a decision if
reliability is a number. Without a record, the question gets answered by
whoever last saw it work, which is how a loop ends up trusted on the
strength of one good afternoon.

So every run of propose.py appends one line here -- accepted, rejected,
failed-gate, errored, all of it -- and autonomy is computed from that
history against thresholds stated in autonomy.json. A class the loop has
no track record for is NOT eligible, and the refusal says what is
missing.

    python tools/autofix/ledger.py --stats
    python tools/autofix/ledger.py --eligibility

WHAT CANNOT BE EARNED
---------------------
Three classes stay out whatever the numbers say:

  tree    -- a static check reads the generated tree, and Phase 2
             measured that only a whole-directory convert is
             authoritative for it (~40 min). A single-suite convert
             leaves a hybrid tree, so a green `static` rung is a signal.
             Auto-applying on a signal is how a wrong fix lands.
  runtime -- the TestNG guard suite IS the guard.
  prompt  -- step-parity and friends need the ReadyAPI XML cross-checked,
             which is judgement and belongs to a person.

The ledger is append-only and the policy file is in invariants'
SELF_WEAKENING list, so the loop cannot write itself a licence.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import failure_record as fr  # noqa: E402

ROOT = fr.ROOT
LEDGER_REL = "target/autofix-ledger.jsonl"
POLICY_REL = "tools/autofix/autonomy.json"

# Outcomes that count as the loop having produced something usable.
GOOD = frozenset({"proposed", "applied"})
# Outcomes that show the guards working rather than the loop failing.
GUARDED = frozenset({"rejected", "held", "not-attempted", "will-not-apply"})

NEVER_AUTO_KINDS = ("tree", "runtime")
NEVER_AUTO_POLICY = ("prompt",)

DEFAULT_POLICY = {
    "schema": 1,
    "_comment": [
        "Thresholds that let the loop apply a fix without a person reading",
        "it first. Conservative on purpose: the cost of a wrong auto-apply",
        "is a bad commit on a branch plus the time to find it, while the",
        "cost of staying propose-only is one review.",
        "",
        "`min_proposals` is per CHECK KIND, counted from",
        "target/autofix-ledger.jsonl. `min_accept_rate` is good / (good +",
        "failed), ignoring runs the guards stopped -- a rejection is the",
        "system working, not the agent failing.",
        "",
        "`max_blast_radius` is generated files changed. A fix to the",
        "emitter that rewrites thousands of files may be right, but it is",
        "never the kind of thing to land unread."
    ],
    "enabled": False,
    "min_proposals": 10,
    "min_accept_rate": 0.8,
    "max_invariant_rejections": 0,
    "max_blast_radius": 50,
    "auto_kinds": ["unit", "compile"],
}


def ledger_path() -> str:
    return os.path.join(ROOT, LEDGER_REL.replace("/", os.sep))


def policy_path() -> str:
    return os.path.join(ROOT, POLICY_REL.replace("/", os.sep))


def load_policy(path: str | None = None) -> dict:
    p = path or policy_path()
    if not os.path.exists(p):
        return dict(DEFAULT_POLICY)
    try:
        with open(p, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return dict(DEFAULT_POLICY)
    out = dict(DEFAULT_POLICY)
    out.update({k: v for k, v in raw.items() if k in DEFAULT_POLICY})
    return out


def append(entry: dict, path: str | None = None) -> str:
    p = path or ledger_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    row = dict(entry)
    row.setdefault("at", datetime.now(timezone.utc)
                   .strftime("%Y-%m-%dT%H:%M:%SZ"))
    row.setdefault("git", fr.git_info())
    with open(p, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return p


def load(path: str | None = None) -> list[dict]:
    p = path or ledger_path()
    if not os.path.exists(p):
        return []
    rows: list[dict] = []
    with open(p, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue        # a torn line is not a reason to lose the rest
    return rows


def stats(rows: list[dict]) -> dict:
    """Per-kind and per-check counts, plus the accept rate.

    Guarded outcomes are excluded from the denominator on purpose: a
    rejection means an invariant did its job, and counting it against the
    agent would make the guards look like unreliability and create
    pressure to loosen them.
    """
    by: dict[str, dict] = {}

    def bucket(d: dict, key: str) -> dict:
        return d.setdefault(key, {"good": 0, "failed": 0, "guarded": 0,
                                  "rejections": 0, "total": 0,
                                  "max_blast": 0})

    for r in rows:
        outcome = r.get("outcome", "")
        kind = r.get("kind", "?")
        check = r.get("check", "?")
        for d, k in ((by.setdefault("kinds", {}), kind),
                     (by.setdefault("checks", {}), check)):
            b = bucket(d, k)
            b["total"] += 1
            if outcome in GOOD:
                b["good"] += 1
            elif outcome in GUARDED:
                b["guarded"] += 1
            else:
                b["failed"] += 1
            if outcome == "rejected":
                b["rejections"] += 1
            b["max_blast"] = max(b["max_blast"],
                                 int((r.get("blast_radius") or {}).get("total") or 0))
    for group in by.values():
        for b in group.values():
            judged = b["good"] + b["failed"]
            b["judged"] = judged
            b["accept_rate"] = round(b["good"] / judged, 3) if judged else None
    return by


def eligible(kind: str, policy: dict, rows: list[dict]) -> tuple[bool, str]:
    """Whether this check kind may be auto-applied, and why not."""
    if kind in NEVER_AUTO_KINDS:
        return False, (
            f"kind '{kind}' is never auto-applied: a static check reads the "
            f"generated tree and only a whole-directory convert is "
            f"authoritative for it, so a green single-suite rung is a signal, "
            f"not a verdict" if kind == "tree" else
            f"kind '{kind}' is never auto-applied: the guard suite is the guard")
    if not policy.get("enabled"):
        return False, ("autonomy is off: set \"enabled\": true in "
                       "tools/autofix/autonomy.json once the numbers below "
                       "justify it")
    if kind not in (policy.get("auto_kinds") or []):
        return False, f"kind '{kind}' is not in auto_kinds {policy.get('auto_kinds')}"
    s = stats(rows).get("kinds", {}).get(kind)
    if not s:
        return False, (f"no history for kind '{kind}'. Run propose-only until "
                       f"there are {policy['min_proposals']} judged runs")
    if s["judged"] < policy["min_proposals"]:
        return False, (f"{s['judged']} judged run(s) for '{kind}', "
                       f"{policy['min_proposals']} required")
    if s["rejections"] > policy["max_invariant_rejections"]:
        return False, (f"{s['rejections']} invariant rejection(s) for '{kind}'; "
                       f"at most {policy['max_invariant_rejections']} allowed. "
                       f"An agent that trips the guards is not ready to run "
                       f"without them")
    if (s["accept_rate"] or 0) < policy["min_accept_rate"]:
        return False, (f"accept rate {s['accept_rate']} for '{kind}' is below "
                       f"{policy['min_accept_rate']}")
    return True, (f"{s['judged']} judged run(s), accept rate "
                  f"{s['accept_rate']}, {s['rejections']} rejection(s)")


def blast_within(policy: dict, blast: dict | None) -> tuple[bool, str]:
    total = int((blast or {}).get("total") or 0)
    cap = policy.get("max_blast_radius", 0)
    if total > cap:
        return False, (f"{total} generated file(s) changed, cap is {cap}. A fix "
                       f"this wide may be right, but it is not the kind to "
                       f"land unread")
    return True, f"{total} generated file(s) changed, within the cap of {cap}"


def format_stats(by: dict) -> str:
    out: list[str] = []
    for group in ("kinds", "checks"):
        rows = by.get(group) or {}
        if not rows:
            continue
        out.append(f"== by {group}")
        out.append(f"  {'name':<24} {'runs':>5} {'good':>5} {'fail':>5} "
                   f"{'guard':>6} {'rate':>6} {'maxblast':>9}")
        for name, b in sorted(rows.items()):
            rate = "-" if b["accept_rate"] is None else f"{b['accept_rate']:.2f}"
            out.append(f"  {name:<24} {b['total']:>5} {b['good']:>5} "
                       f"{b['failed']:>5} {b['guarded']:>6} {rate:>6} "
                       f"{b['max_blast']:>9}")
    return "\n".join(out) if out else "no runs recorded yet"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--stats", action="store_true")
    g.add_argument("--eligibility", action="store_true")
    g.add_argument("--init-policy", action="store_true",
                   help="write autonomy.json with conservative defaults")
    ap.add_argument("--ledger", default=None)
    args = ap.parse_args(argv)

    if args.init_policy:
        p = policy_path()
        if os.path.exists(p):
            print(f"{POLICY_REL} already exists -- not overwritten")
            return 1
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(DEFAULT_POLICY, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        print(f"wrote {POLICY_REL} (autonomy disabled)")
        return 0

    rows = load(args.ledger)
    if args.stats:
        print(f"{len(rows)} run(s) in {LEDGER_REL}\n")
        print(format_stats(stats(rows)))
        return 0

    policy = load_policy()
    print(f"policy: {POLICY_REL}  enabled={policy.get('enabled')}  "
          f"auto_kinds={policy.get('auto_kinds')}")
    print(f"ledger: {len(rows)} run(s)\n")
    for kind in ("unit", "compile", "tree", "runtime"):
        ok, why = eligible(kind, policy, rows)
        print(f"  {'AUTO' if ok else 'no  '}  {kind:<9} {why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
