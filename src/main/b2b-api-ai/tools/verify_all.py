"""One command that verifies everything this framework guarantees.

    python tools/verify_all.py            # fast checks only (~30s)
    python tools/verify_all.py --full     # + Java compile + Java tests (~5m)

WHY
---
The guarantees live in five different places -- converter unit tests, emit
shape tests, assertion coverage, the phase vocabulary self-test, a generated
tree scan, and a TestNG suite. Before this, verifying them meant remembering
all six commands, so in practice a change got partially checked and the gap
showed up 20 minutes later in a reconvert.

Every check here exists because something actually broke:

  contracts      converter behaviours that were regressed before and are now
                 pinned (last-write-wins, /remove is POST, existence polarity)
  emit shape     a regex extraction left a dangling block; 13 of 18 suites
                 failed to emit and ~390k lines vanished
  assertions     "HTTP Header Exists" silently converted to nothing while
                 coverage still reported FULL
  vocabulary     53 of 155 methods were named after the wrong operation
  generated      a crashed run poisoned the shared catalog, so the NEXT run
                 emitted different output with no error
  java           redaction, SQL guards, typed context, deferred delays

Exit code is 0 only when every selected check passes.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable


class Check:
    def __init__(self, name, cmd, why, full_only=False):
        self.name, self.cmd, self.why, self.full_only = name, cmd, why, full_only


CHECKS = [
    Check("contracts",
          [PY, "tools/ra_converter/test_converter_fixes.py"],
          "converter behaviours previously regressed"),
    Check("phase-model",
          [PY, "tools/ra_converter/test_phase_model.py"],
          "call shapes key on the call only; specs match the emitted Java"),
    Check("dedup-report",
          [PY, "tools/ra_converter/test_dedup_report.py"],
          "same-call-different-data groups are found and audited"),
    Check("cross-case",
          [PY, "tools/ra_converter/test_cross_case_contracts.py"],
          "identity pack / email overlay / existence polarity"),
    Check("emit-shape",
          [PY, "tools/ra_converter/test_jdbc_emit_shape.py"],
          "JDBC emit stays balanced and gated"),
    Check("assertions",
          [PY, "tools/ra_converter/test_assertion_coverage.py"],
          "no assertion silently converts to nothing"),
    Check("groovy-dates",
          [PY, "tools/ra_converter/test_groovy_datetime.py"],
          "java.time computation reaches ctx, not dropped"),
    Check("hoist",
          [PY, "tools/ra_converter/test_shared_method_hoist.py"],
          "shared phases are inherited, not re-inlined per case"),
    Check("ctx-keys",
          [PY, "tools/ra_converter/test_ctx_key_conventions.py"],
          "producer and consumer agree on the ctx key"),
    Check("dataflow",
          [PY, "tools/check_ctx_dataflow.py"],
          "captured values reach the requests that read them"),
    Check("substitution",
          [PY, "tools/check_substitutions.py"],
          "every placeholder resolves; no raw ${...} survives"),
    Check("csv-contract",
          [PY, "tools/check_csv_contracts.py"],
          "every emitted CSV column is one the framework can rebuild"),
    Check("vocabulary",
          [PY, "tools/ra_converter/phase_vocabulary.py"],
          "phase names derive from the full path"),
    Check("generic",
          [PY, "tools/check_generic.py"],
          "committed code compiles after converting ANY single XML"),
    Check("xml-wellformed",
          [PY, "tools/check_xml_wellformed.py"],
          "every committed XML parses (a broken suite once passed 15/15)"),
    Check("generated",
          [PY, "tools/check_generated_output.py"],
          "emitted tree is structurally sound"),
    Check("java-compile",
          ["mvn", "-q", "-DskipTests", "test-compile"],
          "all generated Java still compiles", full_only=True),
    Check("java-tests",
          ["mvn", "test", "-DsuiteXmlFile=src/test/resources/testng-guards.xml"],
          "redaction / SQL guards / typed ctx / deferred delays",
          full_only=True),
]


def run(check: Check) -> tuple[bool, float, str]:
    t0 = time.time()
    try:
        p = subprocess.run(check.cmd, cwd=ROOT, capture_output=True,
                           text=True, shell=(os.name == "nt"
                                             and check.cmd[0] == "mvn"))
        out = (p.stdout or "") + (p.stderr or "")
        return p.returncode == 0, time.time() - t0, out
    except FileNotFoundError as e:
        return False, time.time() - t0, f"could not run {check.cmd[0]}: {e}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true",
                    help="also compile Java and run the TestNG guard suite")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="print output even for checks that pass")
    args = ap.parse_args()

    selected = [c for c in CHECKS if args.full or not c.full_only]
    print(f"verify_all: {len(selected)} check(s)"
          f"{'  (--full)' if args.full else '  (fast; use --full for Java)'}\n")

    failed = []
    for c in selected:
        ok, secs, out = run(c)
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {c.name:<14} {secs:6.1f}s   {c.why}")
        if not ok:
            failed.append((c, out))
        elif args.verbose:
            print("\n".join("        " + l for l in out.splitlines()[-8:]))

    if not failed:
        print("\nAll checks passed.")
        if not args.full:
            print("Java compile + TestNG suite were skipped -- run with --full "
                  "before a reconvert or a commit.")
        return 0

    print(f"\n{len(failed)} check(s) FAILED\n")
    for c, out in failed:
        print(f"--- {c.name} ---")
        tail = out.strip().splitlines()
        for line in tail[-25:]:
            print("  " + line)
        print()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
