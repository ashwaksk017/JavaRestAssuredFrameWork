"""The fingerprinter is load-bearing for the autofix loop, so pin it.

Two failure modes are worth more than the rest, and both are silent:

  too LOOSE  -- a fingerprint that does not move when the failure changes.
                The loop then treats a new failure as the old one: it
                skips it as accepted, or claims a fix worked because a
                DIFFERENT failure now occupies the same check.
  too STRICT -- a fingerprint that moves between identical runs. Every
                failure reads as new, baseline.json never matches, and
                the loop re-investigates the same artifact forever.

The count tests are the loose case stated concretely: "1 of 17 steps
unreachable" and "2 of 17" must not share a fingerprint.
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import failure_record as fr  # noqa: E402


class Normalization(unittest.TestCase):
    def test_absolute_root_becomes_repo_relative(self):
        line = fr.ROOT.replace("\\", "/") + "/tools/check_step_parity.py:88: FAIL"
        self.assertEqual(fr.normalize(line), ["tools/check_step_parity.py:88: FAIL"])

    def test_backslash_paths_normalize_too(self):
        line = fr.ROOT + "\\tools\\check_phase_order.py FAILED"
        self.assertEqual(fr.normalize(line), ["tools/check_phase_order.py FAILED"])

    def test_durations_and_timestamps_are_scrubbed(self):
        a = fr.normalize("Ran 153 tests in 0.456s")
        b = fr.normalize("Ran 153 tests in 1.902s")
        self.assertEqual(a, b)
        self.assertEqual(fr.normalize("at 2026-10-03T05:04:09Z done"),
                         fr.normalize("at 2026-10-01T22:11:48Z done"))

    def test_test_count_survives_scrubbing(self):
        """`Ran 153 tests` must not be scrubbed along with the duration."""
        self.assertNotEqual(fr.normalize("Ran 153 tests in 0.4s"),
                            fr.normalize("Ran 154 tests in 0.4s"))

    def test_addresses_and_hashes_are_scrubbed(self):
        self.assertEqual(fr.normalize("<obj at 0xdeadbeef>"),
                         fr.normalize("<obj at 0x01234567>"))

    def test_maven_noise_lines_are_dropped(self):
        out = ("Downloading from central: foo.jar\n"
               "[INFO] Total time:  12.345 s\n"
               "[ERROR] COMPILATION ERROR\n")
        self.assertEqual(fr.normalize(out), ["[ERROR] COMPILATION ERROR"])

    def test_blank_lines_dropped_but_order_kept(self):
        self.assertEqual(fr.normalize("b\n\n\na\n"), ["b", "a"])


class Redaction(unittest.TestCase):
    """These records are built to be sent to an external agent."""

    def test_jdbc_url_is_redacted(self):
        got = fr.normalize("db.url=jdbc:postgresql://host.internal:5432/b2b-stg")[0]
        self.assertIn("jdbc:postgresql://<redacted>", got)
        self.assertNotIn("host.internal", got)

    def test_secretish_assignments_are_redacted(self):
        for line, leaked in (
            ("password=hunter2xyz", "hunter2xyz"),
            ("client_secret: s3cr3tvalue", "s3cr3tvalue"),
            ('"apiKey": "key_abcdef123"', "key_abcdef123"),
            ("access_token = abc123def456", "abc123def456"),
            ("assertion=eyJhbGciOiJSUzI1NiJ9.payload", "payload"),
        ):
            got = fr.normalize(line)[0]
            self.assertNotIn(leaked, got, f"leaked from: {line}")
            self.assertIn("redacted", got, f"not redacted: {line}")

    def test_bearer_header_is_redacted(self):
        got = fr.normalize("Authorization: Bearer abcDEF123456ghiJKL")[0]
        self.assertNotIn("abcDEF123456ghiJKL", got)

    def test_rotating_a_secret_does_not_move_the_fingerprint(self):
        """Redact-then-fingerprint: an acceptance survives a credential rotation."""
        a = "FAIL: auth\nclient_secret=oldsecretvalue\n"
        b = "FAIL: auth\nclient_secret=newsecretvalue\n"
        self.assertEqual(fr.fingerprints(a)[0], fr.fingerprints(b)[0])

    def test_ordinary_diagnostics_are_left_alone(self):
        line = "B2B-422_get_spendSummary 1/17 step(s) unreachable: tokenRequest 2"
        self.assertEqual(fr.normalize(line), [line],
                         "redaction must not eat the diagnostic it exists beside")

    def test_the_baselined_step_parity_output_is_not_redacted(self):
        """`tokenRequest` / `tokenrequest_2` must not trip the token pattern.

        If it did, adding redaction would silently invalidate the
        fingerprint already recorded in baseline.json.
        """
        for s in ("most common unreachable step: tokenrequest_2 x1",
                  "1/17 step(s) unreachable: tokenRequest 2"):
            self.assertNotIn("redacted", fr.normalize(s)[0], s)


class Fingerprints(unittest.TestCase):
    PARITY = ("[step-parity] B2B-422_get_spendSummary_usermembernotactive_403_suspended\n"
              "  tokenRequest 2 unreachable -- 1 of 17 steps\n"
              "FAIL: 1 case with unreachable steps\n")

    def test_identical_output_fingerprints_identically(self):
        a = fr.fingerprints(self.PARITY)
        b = fr.fingerprints(self.PARITY)
        self.assertEqual(a[0], b[0])
        self.assertEqual(a[1], b[1])

    def test_only_timing_differs_fingerprint_holds(self):
        with_time = self.PARITY + "elapsed 4.2s\n"
        other_time = self.PARITY + "elapsed 9.9s\n"
        self.assertEqual(fr.fingerprints(with_time)[0],
                         fr.fingerprints(other_time)[0])

    def test_a_changed_count_is_a_changed_failure(self):
        worse = self.PARITY.replace("1 of 17", "2 of 17")
        self.assertNotEqual(fr.fingerprints(self.PARITY)[1],
                            fr.fingerprints(worse)[1])

    def test_a_second_failing_case_is_a_changed_failure(self):
        worse = self.PARITY + "  B2B-999_other_case: dropped 3 of 9 steps\n"
        self.assertNotEqual(fr.fingerprints(self.PARITY)[1],
                            fr.fingerprints(worse)[1])

    def test_salient_survives_a_passing_test_line_while_full_moves(self):
        """Renaming an unrelated passing test must not invalidate an acceptance."""
        noisy = self.PARITY + "ok  test_something_unrelated\n"
        self.assertEqual(fr.fingerprints(self.PARITY)[1], fr.fingerprints(noisy)[1])
        self.assertNotEqual(fr.fingerprints(self.PARITY)[0], fr.fingerprints(noisy)[0],
                            "the strict fingerprint should still notice it")

    def test_a_counter_line_is_not_noise(self):
        """`registered cases : 1160` is context, `missing : 0` is the verdict.

        They are structurally identical, so neither can be dropped. A tree
        check's fingerprint therefore moves when the tree grows -- correct,
        because a cached acceptance may not describe the new tree.
        """
        a = "registered cases : 1160\nmissing : 0\n"
        b = "registered cases : 1180\nmissing : 0\n"
        self.assertNotEqual(fr.fingerprints(a)[1], fr.fingerprints(b)[1])

    def test_a_pass_line_with_a_failure_word_in_its_NAME_is_still_noise(self):
        """Test names contain `fail`/`missing`/`does_not`; vetoing on those
        would keep hundreds of pass lines salient, which is the churn the
        noise rule exists to remove."""
        for nm in ("ok  test_missing_producer_is_reported",
                   "ok  test_does_not_crash",
                   "ok  test_fail_closed_guard"):
            self.assertTrue(fr._is_noise(nm), nm)

    def test_a_real_verdict_line_cannot_masquerade_as_a_pass_line(self):
        for nm in ("ok but 3 cases FAILED",
                   "okay: missing 2",
                   "ok  test_a and more text"):
            self.assertFalse(fr._is_noise(nm), nm)

    def test_empty_output_does_not_collide_with_a_real_failure(self):
        self.assertNotEqual(fr.fingerprints("")[1], fr.fingerprints(self.PARITY)[1])

    def test_an_unrecognized_line_counts_as_salient(self):
        """Fail-closed: no allowlist can cover 47 checkers' phrasings."""
        a = "verdict: ok\n"
        b = "verdict: ok\nsome checker said something nobody anticipated\n"
        self.assertNotEqual(fr.fingerprints(a)[1], fr.fingerprints(b)[1])

    def test_a_noise_line_carrying_a_verdict_is_not_noise(self):
        base = "FAIL: 1 case\n"
        withv = base + "checking suite amexbackbook: FAILED 2 cases\n"
        self.assertNotEqual(fr.fingerprints(base)[1], fr.fingerprints(withv)[1])

    def test_salient_falls_back_to_tail_when_nothing_matches(self):
        plain = "verdict: 3 cases differ\nsummary written\n"
        _, sfp, norm, sal = fr.fingerprints(plain)
        self.assertEqual(sal, norm, "a plain verdict must still fingerprint distinctly")
        self.assertNotEqual(sfp, fr.fingerprints("verdict: 4 cases differ\nsummary written\n")[1])


class PathClassification(unittest.TestCase):
    def test_generated_roots_are_generated(self):
        for p in ("src/main/java/com/hi/api/support/amexbackbook/cases/Specs3.java",
                  "src/test/java/com/hi/api/tests/imported/Foo.java",
                  "src/test/resources/csv/b2b.csv",
                  "src/main/resources/test_data_defaults/amexbackbook.json",
                  "src/main/resources/converter_identity.json",
                  "_audit/ledger.json"):
            self.assertEqual(fr.classify_path(p), "generated", p)

    def test_emitter_and_checkers_are_source(self):
        for p in ("tools/ra_converter/ra_converter.py",
                  "tools/check_step_parity.py",
                  "src/main/java/com/hi/api/rest/utilities/ResponseAsserts.java"):
            self.assertEqual(fr.classify_path(p), "source", p)

    def test_manual_suites_are_never_touch(self):
        self.assertEqual(
            fr.classify_path("src/main/java/com/hi/api/rest/manual/client/ManualClient.java"),
            "never-touch")
        self.assertEqual(
            fr.classify_path("src/test/java/com/hi/api/tests/manual/GuestEnrollTest.java"),
            "never-touch")

    def test_two_files_in_the_same_generated_directory_differ(self):
        """The real trap, and it cuts both ways.

        Per-suite output under support/ is generated and a fix there
        evaporates. But support/ ALSO holds the author-editable framework
        types, which are written SKIP-IF-EXISTS and are legitimate fix
        sites -- so the directory cannot decide it alone. Phase 0 asserted
        CtxFields.java was simply generated, which would have had Phase 1
        forbidding a real fix site.
        """
        self.assertEqual(
            fr.classify_path("src/main/java/com/hi/api/support/amexbackbook/cases/Specs3.java"),
            "generated")
        self.assertEqual(
            fr.classify_path("src/main/java/com/hi/api/support/CtxFields.java"),
            "author-editable")
        self.assertTrue(fr.is_editable("author-editable"))
        self.assertFalse(fr.is_editable("generated"))


class Implicated(unittest.TestCase):
    def test_splits_generated_from_source_and_always_names_the_checker(self):
        out = ("src/main/java/com/hi/api/support/amexbackbook/cases/Specs3.java:1004 bad ref\n"
               "see tools/ra_converter/phase_emit.py\n")
        got = fr.implicated(out, ["/usr/bin/python", "tools/check_phase_order.py"])
        self.assertIn("src/main/java/com/hi/api/support/amexbackbook/cases/Specs3.java",
                      got["generated"])
        self.assertIn("tools/ra_converter/phase_emit.py", got["source"])
        self.assertIn("tools/check_phase_order.py", got["source"],
                      "the checker is a candidate fix site and must be listed")


class Taxonomy(unittest.TestCase):
    def test_kind_derives_from_the_command(self):
        self.assertEqual(fr.kind_of("phase-order", ["py", "tools/check_phase_order.py"]), "tree")
        self.assertEqual(fr.kind_of("contracts", ["py", "tools/ra_converter/test_converter_fixes.py"]), "unit")
        self.assertEqual(fr.kind_of("java-compile", ["mvn", "-q", "test-compile"]), "compile")
        self.assertEqual(fr.kind_of("java-tests", ["mvn", "test"]), "runtime")

    def test_judgment_checks_are_prompt_and_the_rest_auto(self):
        for n in ("step-parity", "dataflow", "request-schemas", "java-tests"):
            self.assertEqual(fr.policy_of(n), "prompt", n)
        for n in ("contracts", "phase-order", "case-duplication", "csv-contract"):
            self.assertEqual(fr.policy_of(n), "auto", n)

    def test_prompt_records_carry_the_options_and_exactly_one_default(self):
        rec = fr.build_record("step-parity", "why", ["py", "tools/check_step_parity.py"],
                              False, 1.0, Fingerprints.PARITY)
        self.assertEqual(rec["policy"], "prompt")
        ids = [o["id"] for o in rec["prompt_options"]]
        self.assertIn("inspect", ids)
        self.assertIn("accept", ids)
        self.assertEqual(sum(1 for o in rec["prompt_options"] if o.get("default")), 1)

    def test_auto_records_carry_no_options(self):
        rec = fr.build_record("phase-order", "why", ["py", "tools/check_phase_order.py"],
                              False, 1.0, "FAIL: something")
        self.assertNotIn("prompt_options", rec)

    def test_repro_is_pasteable_and_machine_independent(self):
        self.assertEqual(
            fr.repro_of([r"C:\Py\python.exe", "tools/check_step_parity.py"]),
            "python tools/check_step_parity.py")

    def test_a_passing_check_records_no_output(self):
        rec = fr.build_record("contracts", "why", ["py", "tools/ra_converter/test_converter_fixes.py"],
                              True, 2.0, "153 tests OK")
        self.assertEqual(rec["status"], "pass")
        for k in ("fingerprint", "salient", "output_tail", "implicated_files"):
            self.assertNotIn(k, rec)


class Baseline(unittest.TestCase):
    def _bl(self, fp, expires="2099-01-01"):
        return {"schema": 1, "accepted": [{
            "check": "step-parity", "salient_fingerprint": fp,
            "reason": "known artifact", "recorded": "2026-10-03",
            "expires": expires, "owner": "ashwa"}]}

    def test_matching_entry_in_date_is_accepted(self):
        fp = fr.fingerprints(Fingerprints.PARITY)[1]
        entry, state = fr.match_baseline(self._bl(fp), "step-parity", fp)
        self.assertEqual(state, "accepted")
        self.assertEqual(entry["reason"], "known artifact")

    def test_expired_entry_does_not_suppress(self):
        fp = fr.fingerprints(Fingerprints.PARITY)[1]
        _, state = fr.match_baseline(self._bl(fp, expires="2026-01-01"),
                                     "step-parity", fp, today=date(2026, 10, 3))
        self.assertEqual(state, "expired",
                         "an acceptance must not become permanent by neglect")

    def test_unparseable_expiry_is_treated_as_expired(self):
        fp = fr.fingerprints(Fingerprints.PARITY)[1]
        _, state = fr.match_baseline(self._bl(fp, expires="whenever"), "step-parity", fp)
        self.assertEqual(state, "expired")

    def test_changed_failure_under_an_accepted_check_reads_stale(self):
        fp = fr.fingerprints(Fingerprints.PARITY)[1]
        worse = fr.fingerprints(Fingerprints.PARITY.replace("1 of 17", "2 of 17"))[1]
        _, state = fr.match_baseline(self._bl(fp), "step-parity", worse)
        self.assertEqual(state, "stale",
                         "the acceptance describes a failure that no longer matches")

    def test_accepted_status_lands_on_the_record(self):
        fp = fr.fingerprints(Fingerprints.PARITY)[1]
        rec = fr.build_record("step-parity", "why", ["py", "tools/check_step_parity.py"],
                              False, 1.0, Fingerprints.PARITY, baseline=self._bl(fp),
                              suppress_accepted=True)
        self.assertEqual(rec["status"], "accepted")
        self.assertEqual(rec["baseline_state"], "accepted")

    def test_stale_and_expired_keep_status_fail(self):
        fp = fr.fingerprints(Fingerprints.PARITY)[1]
        worse = Fingerprints.PARITY.replace("1 of 17", "2 of 17")
        rec = fr.build_record("step-parity", "why", ["py", "tools/check_step_parity.py"],
                              False, 1.0, worse, baseline=self._bl(fp))
        self.assertEqual(rec["status"], "fail")
        self.assertEqual(rec["baseline_state"], "stale")

    def test_the_committed_baseline_file_parses_and_is_well_formed(self):
        bl = fr.load_baseline()
        self.assertIsInstance(bl.get("accepted"), list)
        for e in bl["accepted"]:
            for required in ("check", "salient_fingerprint", "reason", "expires"):
                self.assertIn(required, e, f"baseline entry missing {required}: {e}")
            self.assertTrue(e["reason"].strip(), "an acceptance needs a stated reason")


class Locate(unittest.TestCase):
    """Phase 2 needs the smallest reproducing suite, not just file paths."""

    def test_suite_names_printed_outright_are_found(self):
        got = fr.locate("leadspaceattestationsuite: 2 collisions with "
                        "programaccountregression")
        self.assertIn("leadspaceattestationsuite", got["suites"])
        self.assertIn("programaccountregression", got["suites"])

    def test_a_case_id_resolves_to_its_suite_through_the_registry(self):
        """step-parity names case ids and no files at all."""
        if not fr.case_index():
            self.skipTest("no generated tree present")
        got = fr.locate(Fingerprints.PARITY)
        self.assertIn("B2B-422_get_spendSummary_usermembernotactive_403_suspended",
                      got["cases"])
        self.assertTrue(got["suites"], "a known case id must name its suite")

    def test_support_paths_contribute_their_suite(self):
        files = {"generated": ["src/main/java/com/hi/api/support/amexbackbook/cases/Specs3.java"],
                 "source": [], "never_touch": []}
        self.assertIn("amexbackbook", fr.locate("", files)["suites"])

    def test_unknown_directory_names_are_not_invented_as_suites(self):
        got = fr.locate("tools/check_phase_order.py and src/main/resources")
        for s in got["suites"]:
            self.assertIn(s, fr.known_suites())

    def test_known_suites_excludes_framework_types(self):
        if not fr.known_suites():
            self.skipTest("no generated tree present")
        for not_a_suite in ("CtxFields.java", "scenario", "ImportedScenario.java"):
            self.assertNotIn(not_a_suite, fr.known_suites())

    def test_ids_the_shape_regex_would_miss_are_still_found(self):
        """Measured: _CASE_RX matches 869 of 1155 real ids. The registry
        holds all of them, so matching comes from there first -- which is
        also what makes this work for GOAL, whose ids need not look like
        B2B's."""
        idx = fr.case_index()
        if not idx:
            self.skipTest("no generated tree present")
        missed = [c for c in idx if not fr._CASE_RX.fullmatch(c)]
        if not missed:
            self.skipTest("this tree has no awkwardly-named ids")
        for cid in sorted(missed, key=len, reverse=True)[:5]:
            got = fr.locate(f"[step-parity] {cid} lost a step")
            self.assertIn(cid, got["cases"], cid)

    def test_an_unregistered_id_is_reported_not_dropped(self):
        """An earlier version intersected regex hits with the registry,
        which dropped exactly the interesting case: an id a checker names
        BECAUSE it failed to register."""
        got = fr.locate("case NEWCASE-77_never_registered failed to register")
        self.assertIn("NEWCASE-77_never_registered", got["cases"])

    def test_the_longer_of_two_overlapping_ids_wins(self):
        idx = fr.case_index()
        pair = next((c for c in idx
                     if c.endswith(" 2") and c[:-2].strip() in idx), None)
        if pair is None:
            self.skipTest("no overlapping id pair in this tree")
        got = fr.locate(f"lost: {pair}")
        self.assertIn(pair, got["cases"],
                      "the shorter id must not shadow the longer one")

    def test_reset_caches_lets_a_reconvert_be_seen(self):
        fr.known_suites(); fr.case_index(); fr._registered_case_matcher()
        fr.reset_caches()
        self.assertIsNone(fr._suites_cache)
        self.assertIsNone(fr._case_index_cache)
        self.assertIsNone(fr._case_rx_cache)
        self.assertTrue(fr.known_suites() or True)   # rebuilds without raising

    def test_locate_is_on_the_record(self):
        rec = fr.build_record("step-parity", "", ["py", "tools/check_step_parity.py"],
                              False, 1.0, Fingerprints.PARITY)
        self.assertIn("locate", rec)
        self.assertIn("suites", rec["locate"])


class SkippedChecks(unittest.TestCase):
    """A check that exits 0 having checked nothing is not evidence."""

    SPEC_SKIP = ("no OpenAPI spec in src/main/resources/openapi/ -- nothing to check.\n"
                 "A vendor contract is gitignored by design; drop one in to enable this.\n")

    def test_request_schemas_skip_is_recognized(self):
        rec = fr.build_record("request-schemas", "a generated body satisfies the contract",
                              ["py", "tools/check_request_schemas.py"], True, 0.1,
                              self.SPEC_SKIP)
        self.assertEqual(rec["status"], "skipped")
        self.assertIn("nothing to check", rec["skip_reason"])

    def test_the_same_check_passing_for_real_is_a_pass(self):
        rec = fr.build_record("request-schemas", "", ["py", "tools/check_request_schemas.py"],
                              True, 0.1, "412 bodies checked against the spec; 0 findings")
        self.assertEqual(rec["status"], "pass")

    def test_other_checks_are_not_swept_up_by_a_generic_skip_guess(self):
        """A generic 'looks like a skip' regex matched unit-test NAMES
        containing 'absent'. Only named checks may skip."""
        for name, out in (("contracts", "ok  test_rev_marker_absent_on_disk_is_stale"),
                          ("audit-completeness", "ok  test_reconciliation_absent_when_nothing_recorded"),
                          ("near-miss", "no unresolvable placeholder has a producer")):
            rec = fr.build_record(name, "", ["py", f"tools/{name}.py"], True, 0.1, out)
            self.assertEqual(rec["status"], "pass", name)


class BaselineAnnotationVsSuppression(unittest.TestCase):
    """A records-only run should be informative without the file claiming a
    status the exit code disagrees with."""

    def _bl(self, fp):
        return {"schema": 1, "accepted": [{
            "check": "step-parity", "salient_fingerprint": fp,
            "reason": "known", "recorded": "2026-10-03",
            "expires": "2099-01-01"}]}

    def test_annotates_without_suppressing_by_default(self):
        fp = fr.fingerprints(Fingerprints.PARITY)[1]
        rec = fr.build_record("step-parity", "", ["py", "tools/check_step_parity.py"],
                              False, 1.0, Fingerprints.PARITY, baseline=self._bl(fp))
        self.assertEqual(rec["baseline_state"], "accepted")
        self.assertEqual(rec["status"], "fail",
                         "annotation alone must not change the verdict")

    def test_suppresses_when_asked(self):
        fp = fr.fingerprints(Fingerprints.PARITY)[1]
        rec = fr.build_record("step-parity", "", ["py", "tools/check_step_parity.py"],
                              False, 1.0, Fingerprints.PARITY, baseline=self._bl(fp),
                              suppress_accepted=True)
        self.assertEqual(rec["status"], "accepted")


class Document(unittest.TestCase):
    def test_totals_count_accepted_separately_from_failed(self):
        recs = [
            fr.build_record("a", "", ["py", "tools/check_a.py"], True, 1.0, ""),
            fr.build_record("b", "", ["py", "tools/check_b.py"], False, 1.0, "FAIL x"),
        ]
        recs.append(dict(recs[1], name="c", status="accepted"))
        doc = fr.build_document(recs, "fast", True)
        self.assertEqual(doc["totals"], {"selected": 3, "passed": 1, "failed": 1,
                                         "accepted": 1, "skipped": 0})
        self.assertEqual(doc["schema"], fr.SCHEMA)

    def test_failing_suites_is_the_union_across_failures(self):
        a = fr.build_record("x", "", ["py", "tools/check_x.py"], False, 1.0,
                            "amexbackbook broke")
        b = fr.build_record("y", "", ["py", "tools/check_y.py"], False, 1.0,
                            "programaccountregression broke")
        doc = fr.build_document([a, b], "fast", False)
        if not fr.known_suites():
            self.skipTest("no generated tree present")
        self.assertEqual(doc["failing_suites"],
                         ["amexbackbook", "programaccountregression"])

    def test_git_provenance_is_populated_not_an_empty_promise(self):
        """'which commit was this measured against' is the first question
        asked of a proposed fix."""
        doc = fr.build_document([], "fast", False)
        g = doc["git"]
        if not g:
            self.skipTest("git unavailable")
        self.assertTrue(g.get("head"))
        self.assertIn("dirty", g)

    def test_a_passing_check_contributes_no_suites(self):
        p = fr.build_record("z", "", ["py", "tools/check_z.py"], True, 1.0,
                            "amexbackbook fine")
        self.assertEqual(fr.build_document([p], "fast", False)["failing_suites"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
