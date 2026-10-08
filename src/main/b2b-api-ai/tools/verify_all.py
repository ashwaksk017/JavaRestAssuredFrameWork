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
    def __init__(self, name, cmd, why, full_only=False, phase_only=False):
        self.name, self.cmd, self.why, self.full_only = name, cmd, why, full_only
        # Reads the phase structure -- phases, specs, chains, the
        # shared/suite-local split. A --classic tree has none of it, so
        # this check cannot say anything true about one. Reported as
        # N/A rather than run, because a check that passes having found
        # nothing to look at is worse than one that did not run: it gets
        # counted as evidence.
        self.phase_only = phase_only


# Names of the checks above that only mean something for a phase tree.
PHASE_ONLY = {
    "phase-model", "phase-emit", "db-phase-split", "consolidation",
    "chain-stop-loud", "hoist", "ctx-keys", "dataflow", "phase-order",
    "step-parity", "step-parity-rules", "vocabulary", "emit-shape",
    "groovy-assert-shapes", "case-duplication", "case-duplication-rules",
    "shape-index", "near-miss",
}


def classic_suites(root):
    """Suites in this tree emitted by --classic, from the mode marker."""
    import json as _json
    try:
        with open(os.path.join(root, "_audit", "emit_mode.json"),
                  encoding="utf-8") as fh:
            modes = (_json.load(fh).get("suites") or {})
    except (OSError, ValueError):
        return []
    return sorted(k for k, v in modes.items() if v == "classic")


CHECKS = [
    Check("contracts",
          [PY, "tools/ra_converter/test_converter_fixes.py"],
          "converter behaviours previously regressed"),
    Check("python-defs",
          [PY, "tools/check_python_defs.py"],
          "no tool module uses a name it never imports -- a NameError "
          "fires only when that line runs, which was on a user's machine, "
          "mid-convert, after 40 files had been written"),
    Check("classic-mode",
          [PY, "tools/ra_converter/test_classic_mode.py"],
          "--classic refuses to leave the tree half inlined and half "
          "phase-shared -- support/scenario/ is shared, so converting one "
          "suite in the other mode broke 28 suites the run never touched "
          "and still exited 0"),
    Check("input-selection",
          [PY, "tools/ra_converter/test_input_selection.py"],
          "--input converts the suite it was given, not every sibling -- "
          "naming one XML in input/ used to convert all 29, which also made "
          "the ladder's cheap repro rung secretly run the whole tree"),
    Check("execute-flag",
          [PY, "tools/ra_converter/test_execute_flag_carryforward.py"],
          "a row switched off with execute=N survives the next convert -- "
          "generated row files are overwritten, so a lost flag silently "
          "runs a test someone chose to stop running"),
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
    Check("classic-shape",
          [PY, "tools/check_classic_shape.py"],
          "every inline exchange routes through config, builds its "
          "Authorization with the idempotent bearer, and merges headers "
          "once -- all three of those got this wrong once and COMPILED, "
          "so nothing else in the gate would have caught them"),
    Check("no-duplicate-methods",
          [PY, "tools/check_no_duplicate_methods.py"],
          "no emitted class declares the same method signature twice -- "
          "`method-name-length` unit-tests the name BUILDER against "
          "strings it invents and never reads the tree, so a collision "
          "that survives the builder reached javac and nothing before it"),
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
    Check("wrapped-sql-local-dates",
          [PY, "tools/ra_converter/test_wrapped_sql_and_local_dates.py"],
          "a query wrapped after `+` still converts to a Db read, and a "
          "LocalDate a GString reads is in ctx before the SQL that uses it "
          "-- both emitted nothing, passed the convert, and sent a stale "
          "OTP and a literal `#olderDate#` at run time"),
    Check("db-collect-and-pick",
          [PY, "tools/ra_converter/test_db_collect_and_pick.py"],
          "a script that reads a list from the database and picks one entry "
          "reads the database, runs before the setup script that uses the "
          "pick, and hands the pick on -- the read was a comment reported "
          "FULL, and the script that needed it had been hoisted ahead of it"),
    Check("jdbc-literal-bind",
          [PY, "tools/ra_converter/test_jdbc_literal_bind.py"],
          "a bind parameter that is a literal in the same script reaches the "
          "SQL -- it became a ctx reference nothing publishes, resolved to "
          "`null`, and the start-of-test cleanup was skipped every run"),
    Check("cluster-placeholder-layout",
          [PY, "tools/ra_converter/test_cluster_placeholder_layout.py"],
          "cases share a @Test only when their bodies take properties at "
          "the same places -- a row was sent through another case's "
          "template and its own cells, as `null`, in 20 methods"),
    Check("compare-partitions",
          [PY, "tools/ra_converter/test_compare_partitions.py"],
          "the partition whose offset moved is computed from this run's two "
          "listings, and the waits beside them really wait -- 169 steps "
          "read a partition and offset saved by an old ReadyAPI run"),
    Check("suite-vocab-isolation",
          [PY, "tools/ra_converter/test_suite_vocab_isolation.py"],
          "a suite's chain methods live on its OWN steps class -- they were "
          "emitted on the shared ScenarioSteps from whichever suites were in "
          "the run, so converting one XML removed the methods 28 others "
          "call; the shared class must be the same whoever wrote it"),
    Check("suite-impact-rules",
          [PY, "tools/ra_converter/test_suite_impact.py"],
          "the suite-impact comparison reports a real change and ignores "
          "renumbering -- spec numbers are positional, so a text diff calls "
          "the whole suite changed every time and the one real change is "
          "lost in it"),
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
    Check("skill-api",
          [PY, "tools/check_skill_api.py"],
          "a Cursor skill never names a helper, class or path that does "
          "not exist -- an agent follows it literally"),
    Check("jira-fetch",
          [PY, "tools/jira/test_fetch.py"],
          "a story URL is untrusted input: an unapproved host is refused "
          "and the token never leaves a header; no clear AC stops the run"),
    Check("jira-extract",
          [PY, "tools/jira/test_extract.py"],
          "a request is READ from a story or not produced at all; a concrete "
          "path is matched back to the recorded template, so an already "
          "automated call is never reported as new"),
    Check("shape-match",
          [PY, "tools/jira/test_shape_match.py"],
          "a story's request resolves to a verdict and a concrete target; "
          "a single call is found inside a recorded flow; several targets "
          "are reported, never guessed between"),
    Check("jira-packet",
          [PY, "tools/jira/test_packet.py"],
          "the agent's brief evaluates every stop condition itself and "
          "leaves nothing to re-decide; story text is fenced as data"),
    Check("shape-index",
          [PY, "tools/jira/test_shapes.py"],
          "two different requests never share a signature; a placeholder "
          "and a literal do"),
    Check("tracked-csv",
          [PY, "tools/check_tracked_csv.py"],
          "no generated row file is tracked -- they carry customer emails, "
          "account ids and internal hostnames, and this repo is public"),
    Check("xml-wellformed",
          [PY, "tools/check_xml_wellformed.py"],
          "every committed XML parses (a broken suite once passed 15/15)"),
    Check("generated",
          [PY, "tools/check_generated_output.py"],
          "emitted tree is structurally sound"),
    Check("emitted-java",
          [PY, "tools/check_emitted_java.py"],
          "the author-editable Java the emitter carries compiles -- "
          "skip-if-exists means nothing else ever compiles it",
          full_only=True),
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
        # PYTHONDONTWRITEBYTECODE, because a stale __pycache__ entry makes
        # this gate lie in both directions. CPython validates a cached
        # .pyc on (mtime, size), so an edit that changes neither -- `&`
        # to `|`, a one-character revert, two writes inside the same
        # second -- leaves the OLD bytecode running. That reported a
        # failure for code that was already correct; the same mechanism
        # could as easily report a pass for code that is not.
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        p = subprocess.run(check.cmd, cwd=ROOT, capture_output=True,
                           text=True, env=env,
                           shell=(os.name == "nt"
                                  and check.cmd[0] == "mvn"))
        out = (p.stdout or "") + (p.stderr or "")
        return p.returncode == 0, time.time() - t0, out
    except FileNotFoundError as e:
        return False, time.time() - t0, f"could not run {check.cmd[0]}: {e}"


# --- what family a check belongs to -----------------------------------
#
# Derived from the COMMAND, not from a hand-kept list of 64 names: a list
# goes stale the moment someone adds a check, and an ungrouped check is
# exactly the one nobody can place. The group is only ever presentation.
GROUPS = (
    ("CONVERTER", "the converter's own unit tests -- run before any tree is "
                  "read, so a broken emitter is caught before its output is "
                  "graded"),
    ("EMITTED TREE", "read the generated Java / CSV / XML a convert produced. "
                     "These are the checks a PARTIAL convert makes unreliable"),
    ("JIRA / XRAY", "the story-to-test tooling: fetching a story, extracting "
                    "its request, matching it, and the brief handed to Cursor"),
    ("AUTOFIX LOOP", "the machinery that lets an agent propose a fix -- what "
                     "it may touch, what it may accept, what it must "
                     "re-verify"),
    ("REPO HYGIENE", "what may be committed. This repository is public"),
    ("JAVA (--full)", "compile the emitted tree and run the framework's own "
                      "tests. Slow, so they need --full"),
)


def group_of(check) -> str:
    cmd = " ".join(str(x) for x in check.cmd).replace("\\", "/")
    if check.cmd and str(check.cmd[0]) == "mvn":
        return "JAVA (--full)"
    if "tools/autofix/" in cmd:
        return "AUTOFIX LOOP"
    if "tools/jira/" in cmd or "check_skill_api" in cmd:
        return "JIRA / XRAY"
    if "check_tracked_csv" in cmd:
        return "REPO HYGIENE"
    if "tools/ra_converter/test_" in cmd:
        return "CONVERTER"
    return "EMITTED TREE"


def print_catalogue(checks) -> None:
    """Every check, grouped, with what it runs and why it exists.

    `--list` prints this and runs nothing. The per-result line during a
    run is one terse sentence by necessity; someone asking "what is this
    actually checking?" needs the command too, and needs to see that the
    64 fall into six families rather than one undifferentiated list.
    """
    print("verify_all -- what each check is for\n")
    print(f"{len(checks)} check(s). Each runs a script and passes only if "
          f"that script exits 0.\n")
    for title, why_group in GROUPS:
        mine = [c for c in checks if group_of(c) == title]
        if not mine:
            continue
        print(f"{title}  ({len(mine)})")
        print(f"  {why_group}.")
        for c in mine:
            runs = " ".join(str(x) for x in c.cmd[1:]) or " ".join(
                str(x) for x in c.cmd)
            print(f"\n  {c.name}")
            print(f"    checks : {c.why}")
            print(f"    runs   : {runs}")
            if c.full_only:
                print("    note   : only with --full")
        print()


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
    ap.add_argument("--list", "--explain", dest="list_only",
                    action="store_true",
                    help="print every check -- grouped, with what it runs and "
                         "why it exists -- and run nothing")
    ap.add_argument("--baseline", action="store_true",
                    help="treat failures recorded in tools/autofix/baseline.json "
                         "as known artifacts and exit 0 for them. OFF by default, "
                         "so the exit code keeps meaning exactly what it always has.")
    ap.add_argument("--impact", metavar="SUITES",
                    help="ALSO convert these comma-separated suites twice -- "
                         "with the converter at HEAD and with the working "
                         "tree, in scratch copies -- and list the ones whose "
                         "generated output differs. Nothing in this tree is "
                         "touched. Asked for, never run by default: it "
                         "converts every named suite twice. Use `all` for "
                         "every input XML (slow).")
    ap.add_argument("--impact-expect", metavar="SUITES", default="",
                    help="with --impact: the suites the change is MEANT to "
                         "move. Any other suite that moves fails the run.")
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
    if args.list_only:
        print_catalogue(CHECKS)
        return 0

    # A --classic tree has no phases, specs or chains, so the checks that
    # read them cannot say anything true about it. Held back by name and
    # LISTED, not quietly dropped: the reader has to know which part of
    # the gate did not cover this tree, or a shorter green run reads as a
    # cleaner one.
    _classic = classic_suites(ROOT)
    _held = []
    if _classic:
        _held = [c for c in selected if c.name in PHASE_ONLY]
        selected = [c for c in selected if c.name not in PHASE_ONLY]

    print(f"verify_all: {len(selected)} check(s)"
          f"{'  (--full)' if args.full else '  (fast; use --full for Java)'}")
    print("Each check runs a script and passes only if that script exits 0. "
          "`--list` explains every one without running it.\n")
    if _classic:
        print(f"[CLASSIC TREE] {len(_classic)} suite(s) were emitted with "
              f"--classic: " + ", ".join(_classic[:6])
              + (" ..." if len(_classic) > 6 else ""))
        print(f"  {len(_held)} phase-structural check(s) are N/A and were "
              f"NOT run: " + ", ".join(sorted(c.name for c in _held)))
        print("  They read phases, specs and chains, which classic does "
              "not emit. Running them would report a pass for having "
              "found nothing, which is not evidence.")
        print("  What still covers a classic tree: java-compile, "
              "java-tests, substitution, csv-contract, generated, "
              "tracked-csv, service-routing.\n")

    failed, accepted, skipped, records = [], [], [], []
    _shown_group = None
    _impact_failed = False
    if args.impact:
        # Run FIRST and printed in full: its answer is a list of suites, not
        # a pass/fail, and it is the slow part -- better to wait for it
        # before the checks than to scroll past them looking for it.
        cmd = [PY, os.path.join("tools", "ra_converter", "suite_impact.py")]
        cmd += (["--all"] if args.impact.strip().lower() == "all"
                else ["--suites", args.impact])
        if args.impact_expect:
            cmd += ["--expect", args.impact_expect, "--fail-on-unexpected"]
        print("  -- SUITE IMPACT " + "-" * 40)
        print("  what the working tree's converter changes, measured in "
              "scratch copies\n")
        # The child writes to the same stream. Unflushed, a redirected run
        # shows its output ABOVE this heading and the heading above nothing.
        sys.stdout.flush()
        _rc = subprocess.run(cmd, cwd=ROOT).returncode
        _impact_failed = _rc != 0
        print()
    for c in selected:
        _g = group_of(c)
        if _g != _shown_group:
            print(f"\n  -- {_g} " + "-" * max(4, 56 - len(_g)))
            _shown_group = _g
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

    # What a fast run did NOT check, named from the list itself.
    #
    # This used to say "Java compile + TestNG suite were skipped", which
    # was true when there were two full_only checks and wrong once there
    # were three: `emitted-java` went unmentioned, and that is the one
    # check for a failure which surfaces only on a clean-tree convert --
    # exactly what a reader of a green fast run needs told. Derived now,
    # so adding a full_only check cannot leave this sentence behind.
    #
    # Printed whether or not something failed: a fast run that fails also
    # skipped these, and the reader is owed both facts.
    def _partial_convert_notice() -> None:
        """Say when this tree is the product of a PARTIAL convert.

        A subset convert recomputes the SHARED phase state from the suites
        in that run, and a chained phase name resolves by name against it.
        So converting one suite can stop OTHER suites' chains resolving --
        observed as ~4,596 `phase-order` findings in a suite nobody
        converted, which reads as a converter bug rather than as "this
        tree is half-built".

        Printed before the skip notice, so the first thing a reader meets
        is the reason the rest of the output may be noise.
        """
        marker = os.path.join(ROOT, "_audit", "partial_convert.json")
        if not os.path.isfile(marker):
            return
        try:
            import json as _json
            with open(marker, encoding="utf-8") as fh:
                d = _json.load(fh)
        except (OSError, ValueError):
            return
        done, left = d.get("converted") or [], d.get("not_converted") or []
        if d.get("isolated"):
            # Every suite carries its own vocabulary, so that convert left
            # the other suites as they were: a failure above is a real
            # finding, not noise from a half-built tree.
            print(f"\n[SUBSET CONVERT] the last convert covered {len(done)} "
                  f"suite(s) of {len(done) + len(left)} "
                  f"(at {d.get('at', '?')}): " + ", ".join(done[:6])
                  + (" ..." if len(done) > 6 else "") + ".")
            print("  The others were not touched by it -- each suite carries "
                  "its own vocabulary -- so the results above stand as they "
                  "are. An input with no generated suite shows as "
                  "SUITE-NOT-EMITTED under `generated`.")
            return
        print(f"\n[PARTIAL TREE] the last convert covered {len(done)} "
              f"suite(s) and left {len(left)} un-converted "
              f"(at {d.get('at', '?')}).")
        print("  converted    : " + ", ".join(done[:6])
              + (" ..." if len(done) > 6 else ""))
        print("  NOT converted: " + ", ".join(left[:6])
              + (" ..." if len(left) > 6 else ""))
        print("  Shared phase state was recomputed from the converted "
              "suites only, so a tree-wide failure above may be an "
              "artefact of that rather than a real fault -- phase-order, "
              "step-parity, substitution and shape-index especially.")
        print("  Convert the whole input directory before trusting them:")
        # The folder THAT RUN used, not the conventional one. A reader
        # given a path they do not convert from runs it, gets a different
        # tree, and trusts the result.
        where = d.get("input_dir") or "tools/ra_converter/input"
        if " " in where:
            where = '"' + where + '"'
        print("    python tools/ra_converter/ra_converter.py --input "
              + where + " --output . --clean --data-dir <workbooks>")

    def _catalog_rebuild_notice() -> None:
        """The catalog's own flag, which the marker file does not cover.

        Two signals, written together but living in different trees: the
        marker under --output, the flag inside fluent_catalog.json next to
        the converter. Convert to a different --output, or clear _audit,
        and the marker is gone while the flag remains -- so the gate would
        go quiet about a tree whose next convert is still going to rebuild
        every phase from scratch. Reading both costs one open.
        """
        cat = os.path.join(ROOT, "tools", "ra_converter",
                           "fluent_catalog.json")
        if not os.path.isfile(cat):
            return
        try:
            import json as _json
            with open(cat, encoding="utf-8") as fh:
                d = _json.load(fh)
        except (OSError, ValueError):
            return
        if not d.get("incomplete"):
            return
        why = d.get("incompleteReason") or []
        print()
        print("[CATALOG INCOMPLETE] fluent_catalog.json is flagged "
              + ("(" + ", ".join(str(r) for r in why[:3]) + ")" if why
                 else "")
              + ".")
        print("  The next convert will rebuild its phase names from "
              "scratch rather than inherit votes computed from part of "
              "the tree. Expect phase names to MOVE on that run, and do "
              "not read the difference as a regression.")

    def _last_convert_impact_notice() -> None:
        """Which suites the LAST convert changed, as that convert recorded it.

        The suites share one converter, so a rule written for one suite's
        Groovy fires on every suite's -- and nothing fails when it does.
        Every convert fingerprints what it wrote per suite and compares it
        with that suite's previous convert; this repeats the result, so a
        reader of the gate sees what moved without scrolling a convert log.

        Read-only and defensive: this file IS the gate, and a report that
        is missing, old or malformed must not change what it says.
        """
        path = os.path.join(ROOT, "_audit", "suite_impact.json")
        if not os.path.isfile(path):
            return
        try:
            import json as _json
            with open(path, encoding="utf-8") as fh:
                report = _json.load(fh)
            sys.path.insert(0, os.path.join(ROOT, "tools", "ra_converter"))
            import suite_impact as _si
            lines = _si.render_report(report, "[LAST CONVERT]")
        except Exception:
            return
        if lines:
            print()
            print("\n".join(lines))
            print("[LAST CONVERT]   (at %s; to measure a change BEFORE "
                  "converting your tree: --impact <suites>)"
                  % report.get("at", "?"))

    def _skip_notice() -> None:
        _partial_convert_notice()
        _catalog_rebuild_notice()
        _last_convert_impact_notice()
        if args.full:
            return
        names = [c.name for c in CHECKS if c.full_only]
        print(f"\n{len(names)} check(s) need --full and did not run: "
              f"{', '.join(names)}.")
        print("Run --full before a reconvert or a commit. `emitted-java` in "
              "particular catches a fault that otherwise appears only when "
              "someone converts into a clean tree.")

    if not failed:
        extra = []
        if accepted:
            extra.append(f"{len(accepted)} accepted from baseline")
        if skipped:
            extra.append(f"{len(skipped)} checked nothing")
        print("\nAll checks passed."
              + (f" ({'; '.join(extra)})" if extra else ""))
        _skip_notice()
        if _impact_failed:
            # Every check passed and the run still fails: a suite outside
            # --impact-expect moved, or a scratch convert did not finish.
            # That is what --impact was asked to find.
            print("\n--impact FAILED: see SUITE IMPACT at the top of this run.")
        return 1 if (odd or _impact_failed) else 0

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
    _skip_notice()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
