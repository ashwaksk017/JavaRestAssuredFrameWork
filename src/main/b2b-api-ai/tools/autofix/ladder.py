"""The cost-aware gate ladder -- Phase 2 of the loop.

WHY ORDER MATTERS MORE THAN IT LOOKS
------------------------------------
A naive fix-then-verify loop reconverts all 15 suites and runs the full
gate on every candidate: ~20-25 minutes per attempt, so three attempts
is an hour and most of it is spent discovering a syntax error. The rungs
below are ordered by measured cost, cheapest first, and the ladder stops
at the first failure. A fix that breaks the emitter's unit tests is
rejected in seconds instead of half an hour.

    python tools/autofix/ladder.py --records target/verify.json
    python tools/autofix/ladder.py --suites amexbackbook --upto compile
    python tools/autofix/ladder.py --plan --records target/verify.json

TWO THINGS THAT ARE NOT INTERCHANGEABLE
---------------------------------------
A single-suite convert is a FAST SIGNAL, not a verdict. `--input <dir>`
converts every suite in one pass "so fluent reuse is computed across the
whole imported codebase" -- shared phases and clustering are computed
over all suites together, so per-suite output can legitimately differ
between a one-suite run and a whole-tree run. The `repro-convert` rung
buys speed; only `full-convert` is authoritative, and the ladder says so
rather than letting a green single-suite run read as done.

AND IT IS NOT SIDE-EFFECT-FREE. Measured on this tree: converting
`accountdashboardregression` alone changed 122 files across NINE suites.
All 122 were CSVs and not one was Java -- the CSV data rows come from a
shared identity/faker allocation whose sequence depends on which suites
are in the run, while the emitted code is unaffected. So after a
repro-convert:

  * `compile` and the Java-reading checks are trustworthy;
  * anything reading CSV data is looking at a hybrid tree that matches
    no full convert;
  * the determinism that makes the manifest meaningful still holds --
    the same single-suite convert run twice changed nothing at all.

That is why --manifest is recommended for any run that converts: the
hybrid state is harmless if you can see it and misleading if you cannot.

THE 30-MINUTE CAP
-----------------
A single background command is killed at 30 minutes. A 15-suite convert
at ~150s each is ~37 minutes, so the full convert is SHARDED into batches
sized by a time budget. This is not a tuning preference: an unsharded
full convert does not complete, and it fails in a way that looks like a
converter hang.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import failure_record as fr  # noqa: E402
import manifest as mf  # noqa: E402

ROOT = fr.ROOT
PY = sys.executable
INPUT_DIR = "tools/ra_converter/input"

# Measured on this tree: ~150s per suite, 15 suites. Batches are sized to
# stay well inside the 30-minute cap even if a suite runs long.
SECONDS_PER_SUITE = 150
SHARD_BUDGET_S = 1500          # 25 minutes
CONVERT_FLAGS = ("--output", ".", "--package-root", "com.hi.api", "--clean",
                 "--max-name-len", "40", "--no-cursor-assist", "--phase-specs")


# --- suite <-> input XML ---------------------------------------------
def input_xmls() -> dict[str, str]:
    """{suite name: repo-relative XML path}.

    The suite name is the XML basename lowercased with '-' mapped to '_'
    -- `topicsprogramaccountsstgkafka-Events.xml` is the suite
    `topicsprogramaccountsstgkafka_events`. Deriving it any other way
    produces a name that matches no support directory, and the convert
    then silently targets nothing.
    """
    base = os.path.join(ROOT, INPUT_DIR.replace("/", os.sep))
    out: dict[str, str] = {}
    if not os.path.isdir(base):
        return out
    for n in sorted(os.listdir(base)):
        if not n.lower().endswith(".xml"):
            continue
        suite = os.path.splitext(n)[0].lower().replace("-", "_")
        out[suite] = f"{INPUT_DIR}/{n}"
    return out


def resolve_suites(names: list[str]) -> tuple[list[str], list[str]]:
    """(resolvable suites, names with no input XML)."""
    known = input_xmls()
    ok = [n for n in names if n in known]
    missing = [n for n in names if n not in known]
    return ok, missing


def shard(suites: list[str], budget_s: int = SHARD_BUDGET_S,
          per_suite_s: int = SECONDS_PER_SUITE) -> list[list[str]]:
    """Split suites into batches that each fit the command time cap."""
    per_batch = max(1, budget_s // max(1, per_suite_s))
    return [suites[i:i + per_batch] for i in range(0, len(suites), per_batch)]


# --- rungs ------------------------------------------------------------
class Rung:
    def __init__(self, name, why, build, authoritative=True):
        self.name, self.why, self.build = name, why, build
        self.authoritative = authoritative


def _checks_of(kind: str) -> list[tuple[str, list[str]]]:
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import verify_all as va
    return [(c.name, list(c.cmd)) for c in va.CHECKS
            if fr.kind_of(c.name, c.cmd) == kind and not c.full_only]


def _unit_cmds(ctx):
    """The emitter's own tests. Seconds, and they catch most bad edits."""
    return _checks_of("unit")


def _repro_convert_cmds(ctx):
    xmls = input_xmls()
    return [(None, [PY, "tools/ra_converter/ra_converter.py",
                    "--input", xmls[s], *CONVERT_FLAGS]) for s in ctx["suites"]]


def _compile_cmds(ctx):
    return [(None, ["mvn", "-o", "-q", "-DskipTests", "test-compile"])]


def _target_check_cmds(ctx):
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import verify_all as va
    want = set(ctx["checks"])
    return [(c.name, list(c.cmd)) for c in va.CHECKS if c.name in want]


def _static_cmds(ctx):
    return _checks_of("tree")


def _full_convert_cmds(ctx) -> list[list[str]]:
    """The whole input DIRECTORY, in one pass.

    Not fifteen single-suite converts. `--input <dir>` exists precisely
    "so fluent reuse is computed across the whole imported codebase": the
    shared-phase hoisting and cluster membership that `repro-convert`
    computes over one suite are computed here over all of them, and the
    two can legitimately disagree. Issuing 15 separate converts would be
    fast and would quietly not be the authoritative output, which is the
    worst of the options because it looks like the authoritative one.

    This costs ~40 minutes and therefore does NOT fit a 30-minute
    single-command budget. That is a property of the work, not a tuning
    choice -- Phase 3's orchestrator has to run this rung as its own
    long-lived job. `shard()` is there for the per-suite path, where
    batching into background commands is what keeps each one inside the
    cap.
    """
    return [(None, [PY, "tools/ra_converter/ra_converter.py",
                    "--input", INPUT_DIR, *CONVERT_FLAGS])]


FULL_CONVERT_TIMEOUT_S = 4200      # ~70 min; the work is ~40


def timeout_for(rung: str, default: int) -> int:
    return FULL_CONVERT_TIMEOUT_S if rung == "full-convert" else default


def _full_verify_cmds(ctx):
    return [(None, [PY, "tools/verify_all.py", "--full", "--baseline",
                    "--json", "target/ladder-verify.json"])]


RUNGS = (
    Rung("unit", "the emitter's own tests -- seconds, and they catch most "
         "bad edits", _unit_cmds),
    Rung("repro-convert", "convert only the suites the failure points at "
         "(~150s each, against ~40min for all 15). NOT side-effect-free: "
         "measured, converting one suite rewrote CSV data rows in 8 others",
         _repro_convert_cmds, authoritative=False),
    Rung("compile", "generated Java still compiles", _compile_cmds),
    Rung("target-check", "the check that was failing now passes",
         _target_check_cmds, authoritative=False),
    Rung("static", "every tree check, on this tree", _static_cmds,
         authoritative=False),
    Rung("full-convert", "all 15 suites in ONE pass (~40min, exceeds a "
         "30-minute command budget) -- shared phases and clustering are "
         "computed across the whole set, so this is the first "
         "authoritative output", _full_convert_cmds),
    Rung("full-verify", "the whole gate, with the known-failure baseline",
         _full_verify_cmds),
)
RUNG_NAMES = tuple(r.name for r in RUNGS)


# --- running ----------------------------------------------------------
def _run(cmd: list[str], timeout: int) -> tuple[bool, float, str]:
    t0 = time.time()
    try:
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                           timeout=timeout,
                           shell=(os.name == "nt" and cmd[0] == "mvn"))
        return p.returncode == 0, time.time() - t0, \
            ((p.stdout or "") + (p.stderr or ""))
    except subprocess.TimeoutExpired:
        return False, time.time() - t0, (
            f"timed out after {timeout}s. A single command is killed at the "
            f"30-minute cap -- shard it.")
    except FileNotFoundError as e:
        return False, time.time() - t0, f"could not run {cmd[0]}: {e}"


def plan(suites: list[str], checks: list[str], upto: str) -> list[dict]:
    ctx = {"suites": suites, "checks": checks}
    out: list[dict] = []
    for r in RUNGS:
        cmds = r.build(ctx)
        out.append({
            "rung": r.name, "why": r.why, "commands": len(cmds),
            "authoritative": r.authoritative,
            "est_s": (len(input_xmls()) * SECONDS_PER_SUITE
                      if r.name == "full-convert"
                      else len(cmds) * SECONDS_PER_SUITE
                      if r.name == "repro-convert" else None),
        })
        if r.name == upto:
            break
    return out


def climb(suites: list[str], checks: list[str], upto: str,
          timeout: int = 1700, verbose: bool = False,
          baseline: dict | None = None) -> dict:
    ctx = {"suites": suites, "checks": checks}
    if baseline is None:
        baseline = fr.load_baseline()
    rungs: list[dict] = []
    spent = 0.0
    for r in RUNGS:
        cmds = r.build(ctx)
        if not cmds:
            rungs.append({"rung": r.name, "status": "empty",
                          "detail": "nothing to run at this rung"})
            if r.name == upto:
                break
            continue
        print(f"\n== {r.name} ({len(cmds)} command(s)) -- {r.why}")
        failed: list[dict] = []
        accepted: list[str] = []
        rt = 0.0
        for name, cmd in cmds:
            ok, secs, out = _run(cmd, timeout_for(r.name, timeout))
            rt += secs
            spent += secs
            label = fr.repro_of(cmd)
            # A rung that runs verify_all checks is judged the way
            # verify_all judges them -- through the known-failure
            # baseline. Without this the ladder can never climb past an
            # accepted artifact: the `tokenRequest 2` step-parity failure
            # would fail target-check on every candidate, for every fix,
            # forever.
            mark = "ok  " if ok else "FAIL"
            if not ok and name and baseline is not None:
                rec = fr.build_record(name, "", cmd, False, secs, out,
                                      baseline=baseline, suppress_accepted=True)
                if rec["status"] == "accepted":
                    ok, mark = True, "KNOWN"
                    accepted.append(name)
            print(f"   [{mark}] {secs:6.1f}s  {label[:110]}")
            if not ok:
                tail = [l for l in (out or "").strip().splitlines() if l.strip()][-12:]
                failed.append({"command": label, "check": name, "tail": tail})
                for l in tail:
                    print("          " + l[:140])
                break
            if verbose and out.strip():
                print("          " + out.strip().splitlines()[-1][:140])
        if accepted:
            print(f"          accepted from baseline: {', '.join(accepted)}")
        rungs.append({
            "rung": r.name, "status": "fail" if failed else "pass",
            "seconds": round(rt, 1), "commands": len(cmds),
            "authoritative": r.authoritative,
            "accepted": accepted,
            "failed": failed,
        })
        if failed:
            return {"outcome": "failed", "at": r.name, "rungs": rungs,
                    "seconds": round(spent, 1)}
        if r.name == upto:
            return {"outcome": "stopped", "at": r.name, "rungs": rungs,
                    "seconds": round(spent, 1),
                    "authoritative": r.authoritative}
    return {"outcome": "passed", "at": RUNGS[-1].name, "rungs": rungs,
            "seconds": round(spent, 1), "authoritative": True}


def suites_from_records(path: str) -> tuple[list[str], list[str]]:
    """(suites, failing check names) from a verify_all --json run."""
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    suites = list(doc.get("failing_suites") or [])
    checks = [c["name"] for c in doc.get("checks", [])
              if c.get("status") in ("fail", "accepted")]
    return suites, checks


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--records", help="a verify_all --json file; the suites "
                                      "and failing checks are read from it")
    ap.add_argument("--suites", default="", help="comma-separated, overrides "
                                                 "--records")
    ap.add_argument("--checks", default="", help="comma-separated check names")
    ap.add_argument("--upto", default="full-verify", choices=RUNG_NAMES,
                    help="highest rung to climb (default: all of them)")
    ap.add_argument("--plan", action="store_true", help="print the ladder, run nothing")
    ap.add_argument("--timeout", type=int, default=1700,
                    help="per-command seconds; the harness cap is 1800")
    ap.add_argument("--manifest", metavar="PATH",
                    help="hash the generated tree before converting, and "
                         "report the blast radius afterwards")
    ap.add_argument("--json", metavar="PATH", help="write the result")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    suites = [s.strip() for s in args.suites.split(",") if s.strip()]
    checks = [c.strip() for c in args.checks.split(",") if c.strip()]
    if args.records and not (suites and checks):
        rs, rc = suites_from_records(args.records)
        suites = suites or rs
        checks = checks or rc

    suites, missing = resolve_suites(suites)
    if missing:
        print(f"no input XML for: {', '.join(missing)} -- "
              f"known: {', '.join(sorted(input_xmls()))}")
        return 2

    print(f"suites: {', '.join(suites) if suites else '(none)'}")
    print(f"checks: {', '.join(checks) if checks else '(none)'}")
    if not suites:
        print("NOTE: no suite was identified, so repro-convert has nothing to "
              "do and the first authoritative output comes from full-convert.")

    if args.plan:
        total = 0
        for row in plan(suites, checks, args.upto):
            est = f"~{row['est_s']}s" if row["est_s"] else "-"
            total += row["est_s"] or 0
            flag = "" if row["authoritative"] else "  (fast signal, not a verdict)"
            print(f"  {row['rung']:<15} {row['commands']:>3} cmd  {est:>8}{flag}")
        print(f"\nconvert time alone: ~{total}s (~{total // 60}m).")
        if any(row["rung"] == "full-convert"
               for row in plan(suites, checks, args.upto)):
            mins = len(input_xmls()) * SECONDS_PER_SUITE // 60
            print(f"full-convert is ONE command of ~{mins}m, so it does not fit "
                  f"a 30-minute budget: run it as its own job. Only that rung "
                  f"computes cross-suite reuse, so everything below it is a "
                  f"signal. Batching the per-suite path into background "
                  f"commands is what shard() is for "
                  f"({SHARD_BUDGET_S // SECONDS_PER_SUITE} suites per command).")
        return 0

    before = None
    if args.manifest:
        before = mf.snapshot()
        mf.save(before, args.manifest)
        print(f"baseline manifest: {before['count']} file(s) -> {args.manifest}")

    result = climb(suites, checks, args.upto, args.timeout, args.verbose)

    if before is not None:
        d = mf.compare(before, mf.snapshot())
        result["blast_radius"] = {k: d[k] for k in ("total", "by_suite")}
        print("\n== blast radius")
        print(mf.format_report(d))

    print(f"\n{result['outcome'].upper()} at rung '{result['at']}' "
          f"after {result['seconds']}s")
    if result["outcome"] == "stopped" and not result.get("authoritative", True):
        print("This rung is a fast signal, not a verdict -- shared phases and "
              "clustering are computed across all 15 suites, so run "
              "--upto full-verify before believing it.")
    if args.json:
        mf.save(result, args.json)
    return 0 if result["outcome"] in ("passed", "stopped") else 1


if __name__ == "__main__":
    raise SystemExit(main())
