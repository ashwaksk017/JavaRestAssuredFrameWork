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

# Phase 0 of the autofix loop: machine-readable failure records plus an
# accepted-failure baseline. Imported defensively and used only when asked
# for -- this file IS the gate, so it has to keep working unchanged if the
# helper is missing, half-written, or broken.
sys.path.insert(0, os.path.join(ROOT, "tools", "autofix"))
try:
    import failure_record as _fr
except Exception as _fr_err:  # pragma: no cover - degraded, not fatal
    _fr, _FR_ERR = None, _fr_err
else:
    _FR_ERR = None


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
    Check("phase-emit",
          [PY, "tools/ra_converter/test_phase_emit.py"],
          "a rendered body splits into spec + hook exactly; refs/chains/shells"),
    Check("db-phase-split",
          [PY, "tools/ra_converter/test_db_phase_split.py"],
          "DB-only groovy steps get their own SQL-derived phase; response-reading ones stay fused"),
    Check("consolidation",
          [PY, "tools/ra_converter/test_consolidation_invariants.py"],
          "folding a family is invisible to matching, resp fields and indexes"),
    Check("db-preamble",
          [PY, "tools/ra_converter/test_db_preamble_dropped.py"],
          "DB connection props are not bound as dead locals; other project refs still are"),
    Check("method-name-length",
          [PY, "tools/ra_converter/test_method_name_length.py"],
          "emitted @Test names stay inside the path budget, camelCase, and unique after truncation"),
    Check("chain-stop-loud",
          [PY, "tools/ra_converter/test_chain_stop_loud.py"],
          "a truncated chain reports itself; the scenario record is bound only when read"),
    Check("no-groovy-labels",
          [PY, "tools/ra_converter/test_no_groovy_labels.py"],
          "converter-invented labels say script, not groovy; signature/audit/provenance keep it"),
    Check("audit-completeness",
          [PY, "tools/ra_converter/test_audit_completeness.py"],
          "shared cluster members, provenance and console-only decisions reach the audit"),
    Check("dedup-report",
          [PY, "tools/ra_converter/test_dedup_report.py"],
          "same-call-different-data groups are found and audited"),
    Check("project-config",
          [PY, "tools/ra_converter/test_project_config.py"],
          "config defaults == emitter tables; identity/heuristics reach the runtime resource"),
    Check("diagram-png",
          [PY, "tools/ra_converter/test_mermaid_png.py"],
          "config layering; case selection; image rendering degrades cleanly"),
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
    Check("external-secrets",
          [PY, "tools/ra_converter/test_external_secrets.py"],
          "credentials reach the request from config, never from the XML"),
    Check("excel-cells",
          [PY, "tools/ra_converter/test_excel_cell_text.py"],
          "a spreadsheet cell arrives as the text ReadyAPI would send"),
    Check("csv-quoting",
          [PY, "tools/ra_converter/test_csv_quoting.py"],
          "a datasource-expanded row survives commas and newlines"),
    Check("workbook-cell-refs",
          [PY, "tools/ra_converter/test_workbook_cell_refs.py"],
          "a workbook cell holding a ReadyAPI reference resolves like one"),
    Check("readyapi-refs",
          [PY, "tools/ra_converter/test_readyapi_ref_translation.py"],
          "ReadyAPI syntax never reaches the runtime as data, by any path"),
    Check("lookup-datasource",
          [PY, "tools/ra_converter/test_lookup_datasource.py"],
          "a DataSource no loop drives contributes constants, not rows"),
    Check("groovy-assert-shapes",
          [PY, "tools/ra_converter/test_groovy_assert_shapes.py"],
          "common Groovy assertion shapes validate instead of stubbing"),
    Check("availability-probe",
          [PY, "tools/ra_converter/test_availability_probe.py"],
          "the availability search emits its first candidate and says the rest are not tried"),
    Check("near-miss-rules",
          [PY, "tools/test_near_miss_producers.py"],
          "the near-miss detector still finds the bugs that motivated it"),
    Check("service-routing",
          [PY, "tools/ra_converter/test_service_routing.py"],
          "a call reaches the service it was recorded against"),
    Check("auth-priming",
          [PY, "tools/ra_converter/test_auth_priming.py"],
          "a token is primed only for cases that cannot fetch one"),
    Check("hoist",
          [PY, "tools/ra_converter/test_shared_method_hoist.py"],
          "shared phases are inherited, not re-inlined per case"),
    Check("ctx-keys",
          [PY, "tools/ra_converter/test_ctx_key_conventions.py"],
          "producer and consumer agree on the ctx key"),
    Check("dataflow",
          [PY, "tools/check_ctx_dataflow.py"],
          "captured values reach the requests that read them"),
    Check("phase-order",
          [PY, "tools/check_phase_order.py"],
          "every chained phase resolves, in order, and none is unreachable"),
    Check("case-duplication",
          [PY, "tools/check_case_duplication.py"],
          "no two suites register the same case id (names the collisions)"),
    Check("case-duplication-rules",
          [PY, "tools/ra_converter/test_case_duplication_rules.py"],
          "the duplication detector reads every registration form, including shared specs"),
    Check("step-parity-rules",
          [PY, "tools/ra_converter/test_step_parity_rules.py"],
          "step-parity resolves spec ids per suite, not across the whole tree"),
    Check("step-parity",
          [PY, "tools/check_step_parity.py"],
          "every ReadyAPI REST step reaches a phase, verify or setup flow"),
    Check("substitution",
          [PY, "tools/check_substitutions.py"],
          "every placeholder resolves; no raw ${...} survives"),
    Check("near-miss",
          [PY, "tools/check_near_miss_producers.py"],
          "nothing resolves to null while its value sits under another name"),
    Check("request-schemas",
          [PY, "tools/check_request_schemas.py"],
          "a generated body satisfies the contract it is sent to (needs a spec)"),
    Check("request-schema-rules",
          [PY, "tools/test_request_schemas.py"],
          "the schema check finds real mismatches and invents none"),
    Check("csv-contract",
          [PY, "tools/check_csv_contracts.py"],
          "every emitted CSV column is one the framework can rebuild"),
    Check("autofix-records",
          [PY, "tools/autofix/test_failure_record.py"],
          "a failure fingerprint moves when the failure changes, not otherwise"),
    Check("autofix-accept",
          [PY, "tools/autofix/test_accept.py"],
          "a known-failure acceptance needs a real reason and an expiry"),
    Check("autofix-invariants",
          [PY, "tools/autofix/test_invariants.py"],
          "a candidate fix cannot edit generated output, a guard, or itself"),
    Check("autofix-mutations",
          [PY, "tools/autofix/test_mutations.py"],
          "the negative-fixture harness restores every file it mutates"),
    Check("autofix-ledger",
          [PY, "tools/autofix/test_ledger.py"],
          "autonomy is off by default; tree/runtime can never earn it; "
          "a guarded rejection is not scored as unreliability"),
    Check("autofix-propose",
          [PY, "tools/autofix/test_propose.py"],
          "a patch is verified to have applied; answer sections parse; "
          "a rejected proposal is restored"),
    Check("autofix-ladder",
          [PY, "tools/autofix/test_ladder.py"],
          "suites resolve to inputs; the full convert is authoritative; "
          "a known failure does not block the ladder"),
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
    ap.add_argument("--json", metavar="PATH",
                    help="also write machine-readable failure records to PATH "
                         "(fingerprint, implicated files, repro command, "
                         "autofix policy). Reading only -- it changes nothing "
                         "about what passes.")
    ap.add_argument("--baseline", action="store_true",
                    help="treat failures recorded in tools/autofix/baseline.json "
                         "as known artifacts and exit 0 for them. OFF by default, "
                         "so the exit code keeps meaning exactly what it always has.")
    args = ap.parse_args()

    if (args.json or args.baseline) and _fr is None:
        print(f"verify_all: --json/--baseline need tools/autofix/failure_record.py "
              f"({_FR_ERR}); continuing without them.\n")

    # Loaded whenever records are wanted, so a --json run is annotated with
    # baseline_state even though only --baseline lets an acceptance change
    # the verdict.
    baseline = (_fr.load_baseline()
                if (_fr and (args.baseline or args.json)) else None)

    selected = [c for c in CHECKS if args.full or not c.full_only]
    print(f"verify_all: {len(selected)} check(s)"
          f"{'  (--full)' if args.full else '  (fast; use --full for Java)'}\n")

    failed, accepted, skipped, records = [], [], [], []
    for c in selected:
        ok, secs, out = run(c)
        rec = None
        if _fr is not None:
            rec = _fr.build_record(c.name, c.why, c.cmd, ok, secs, out,
                                   baseline=baseline,
                                   suppress_accepted=bool(args.baseline))
            records.append(rec)
        state = rec["status"] if rec else ("pass" if ok else "fail")
        mark = {"pass": "PASS", "fail": "FAIL",
                "accepted": "KNOWN", "skipped": "SKIP"}[state]
        print(f"  [{mark}] {c.name:<14} {secs:6.1f}s   {c.why}")
        if state == "accepted":
            accepted.append((c, rec))
        elif state == "skipped":
            skipped.append((c, rec))
        elif not ok:
            failed.append((c, out, rec))
        elif args.verbose:
            print("\n".join("        " + l for l in out.splitlines()[-8:]))

    if args.json and _fr is not None:
        doc = _fr.build_document(records, "full" if args.full else "fast",
                                 bool(args.baseline))
        _fr.write_document(doc, args.json)
        print(f"\nrecords: {args.json}")

    # A check that exited 0 having checked nothing is not evidence that
    # what it guards holds. Said out loud rather than counted as a pass.
    for c, rec in skipped:
        print(f"\n[SKIP]  {c.name} -- exited 0 but checked nothing: "
              f"{rec.get('skip_reason', '')}\n"
              f"  \"{c.why}\" is NOT verified by this run.")

    for c, rec in accepted:
        b = rec.get("baseline") or {}
        print(f"\n[KNOWN] {c.name} -- accepted in baseline.json until "
              f"{b.get('expires', '?')}\n  {b.get('reason', '')}")

    # A baseline entry is only ever consulted when its check FAILS, so an
    # acceptance for a check that now passes is never looked at again and
    # sits in the file indefinitely. Not dangerous -- fingerprint matching
    # means it can only ever re-suppress the identical failure -- but it
    # is bookkeeping that rots, and nothing else would ever mention it.
    if baseline:
        sel = {c.name for c in selected}
        live = {r["name"] for r in records if r["status"] in ("fail", "accepted")}
        for e in baseline.get("accepted", []):
            if e.get("check") in sel and e.get("check") not in live:
                print(f"\n[DEAD]  {e['check']}: passes now, but baseline.json still "
                      f"accepts a failure for it -- remove the entry "
                      f"(recorded {e.get('recorded', '?')}).")

    # An acceptance that no longer describes the failure, or that has
    # aged out, is worse than no acceptance: it reads as handled. Say so
    # even when the run is otherwise green.
    odd = [r for r in records if r.get("baseline_state") in ("expired", "stale")]
    if odd:
        print()
        for r in odd:
            if r["baseline_state"] == "expired":
                print(f"[STALE] {r['name']}: baseline entry EXPIRED "
                      f"({(r.get('baseline') or {}).get('expires')}) -- "
                      f"re-confirm it or fix the failure.")
            else:
                print(f"[STALE] {r['name']}: baseline entry no longer matches "
                      f"this failure -- the failure CHANGED, so the recorded "
                      f"reason does not describe it.")

    if not failed:
        extra = []
        if accepted:
            extra.append(f"{len(accepted)} accepted from baseline")
        if skipped:
            extra.append(f"{len(skipped)} checked nothing")
        print("\nAll checks passed."
              + (f" ({'; '.join(extra)})" if extra else ""))
        if not args.full:
            print("Java compile + TestNG suite were skipped -- run with --full "
                  "before a reconvert or a commit.")
        return 1 if odd else 0

    print(f"\n{len(failed)} check(s) FAILED\n")
    for c, out, rec in failed:
        print(f"--- {c.name} ---")
        tail = out.strip().splitlines()
        for line in tail[-25:]:
            print("  " + line)
        loc = (rec or {}).get("locate") or {}
        if loc.get("suites"):
            # The smallest reconvert that can reproduce this: ~150s per
            # suite against ~20-25 min for all 15.
            print(f"  reproduces in: {', '.join(loc['suites'])}")
        if loc.get("cases"):
            shown = loc["cases"][:4]
            more = len(loc["cases"]) - len(shown)
            print(f"  cases: {', '.join(shown)}" + (f" (+{more})" if more > 0 else ""))
        print()

    # Failures the loop is not allowed to fix on its own. Surfaced here so
    # the choice is the author's, at the command line, with the options
    # spelled out rather than implied.
    prompts = [(c, r) for c, _o, r in failed
               if r and r.get("policy") == "prompt" and r.get("prompt_options")]
    if prompts:
        print("Not auto-fixable -- a fix here needs the source cross-checked "
              "first (a parity gap is ours until the ReadyAPI XML says "
              "otherwise). Choose per failure:\n")
        for c, rec in prompts:
            print(f"  {c.name}  [{rec['kind']}]  fingerprint {rec['salient_fingerprint'][:12]}")
            for i, o in enumerate(rec["prompt_options"], 1):
                print(f"    {i}) {o['label']}"
                      + ("   (default)" if o.get("default") else ""))
            print(f"    repro: {rec['repro']}\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
