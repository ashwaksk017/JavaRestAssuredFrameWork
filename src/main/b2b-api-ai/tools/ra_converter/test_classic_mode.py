"""--classic emits inline RestAssured, and refuses to half-do it.

WHY THIS EXISTS
---------------
Converting ONE suite with --classic into the 29-suite phase tree exited 0
and produced ~100 compile errors -- none of them in the suite that was
converted. `support/scenario/` is shared across suites and its shape
depends on the emit mode, so the last run wins those classes and every
suite in the other mode stops compiling. The convert had no idea.

So these cover two things that are easy to get wrong and expensive to
discover: the refusal (a mode clash must abort BEFORE emitting, because
finding it at java-compile costs a full reconvert to undo) and the
call-shape decisions that make classic output faithful rather than merely
classic-looking.

    python tools/ra_converter/test_classic_mode.py
"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import ra_converter as rc          # noqa: E402


class _Tree:
    """An --output tree holding `phase` suites, `classic` suites, or both."""

    def __init__(self, phase=(), classic=(), marker=True):
        self.phase = list(phase)
        self.classic = list(classic)
        self.marker = marker

    def __enter__(self):
        self.root = tempfile.mkdtemp()
        for suite in self.phase:
            # The structural marker the inference reads: Specs*.java under
            # support/<suite>/cases/ is emitted by the phase path only.
            d = os.path.join(self.root, "src", "main", "java", "com", "hi",
                             "api", "support", suite, "cases")
            os.makedirs(d, exist_ok=True)
            io.open(os.path.join(d, "Specs1.java"), "w").close()
        if self.marker:
            modes = {s: "phase" for s in self.phase}
            modes.update({s: "classic" for s in self.classic})
            d = os.path.join(self.root, "_audit")
            os.makedirs(d, exist_ok=True)
            with io.open(os.path.join(d, "emit_mode.json"), "w",
                         encoding="utf-8") as fh:
                json.dump({"schema": 1, "suites": modes}, fh)
        return self

    def __exit__(self, *exc):
        shutil.rmtree(self.root, ignore_errors=True)
        return False


class RefusesAMixedTree(unittest.TestCase):

    def test_classic_into_a_phase_tree_is_refused(self):
        with _Tree(phase=["alpha", "beta"]) as t:
            with self.assertRaises(SystemExit) as cm:
                rc._refuse_mixed_emit_mode(t.root, ["gamma"], classic=True)
        msg = str(cm.exception)
        self.assertIn("REFUSING", msg)
        self.assertIn("alpha", msg, "the clashing suites must be named")
        self.assertIn("--classic", msg, "and the way out must be printed")

    def test_phase_into_a_classic_tree_is_refused_too(self):
        """Symmetry is the point: the damage is the shared scenario
        classes being rewritten, and that happens whichever direction the
        run goes."""
        with _Tree(classic=["alpha"]) as t:
            with self.assertRaises(SystemExit) as cm:
                rc._refuse_mixed_emit_mode(t.root, ["beta"], classic=False)
        self.assertIn("REFUSING", str(cm.exception))

    def test_converting_everything_is_allowed(self):
        """The whole tree in one mode is the supported way to switch, so
        it must not trip the guard that exists to push people to it."""
        with _Tree(phase=["alpha", "beta"]) as t:
            rc._refuse_mixed_emit_mode(t.root, ["alpha", "beta"],
                                       classic=True)

    def test_same_mode_partial_convert_is_allowed(self):
        with _Tree(phase=["alpha", "beta"]) as t:
            rc._refuse_mixed_emit_mode(t.root, ["alpha"], classic=False)

    def test_an_empty_tree_is_allowed(self):
        with _Tree() as t:
            rc._refuse_mixed_emit_mode(t.root, ["alpha"], classic=True)


class InfersTheModeOfAnOlderTree(unittest.TestCase):
    """Every tree that exists today predates the marker file, so a guard
    that only reads the marker protects nobody until after the damage it
    exists to prevent."""

    def test_a_phase_suite_is_recognised_without_any_marker(self):
        with _Tree(phase=["alpha", "beta"], marker=False) as t:
            self.assertEqual(
                rc._infer_phase_suites_on_disk(t.root), {"alpha", "beta"})

    def test_and_that_is_enough_to_refuse(self):
        with _Tree(phase=["alpha"], marker=False) as t:
            with self.assertRaises(SystemExit):
                rc._refuse_mixed_emit_mode(t.root, ["beta"], classic=True)

    def test_absence_means_unknown_not_classic(self):
        """Inventing a mode that was never recorded is how a correct run
        gets refused, so only the positive marker counts."""
        with _Tree(marker=False) as t:
            os.makedirs(os.path.join(t.root, "src", "main", "java"),
                        exist_ok=True)
            self.assertEqual(rc._infer_phase_suites_on_disk(t.root), set())
            rc._refuse_mixed_emit_mode(t.root, ["beta"], classic=False)

    def test_a_recorded_mode_wins_over_the_inferred_one(self):
        """The marker is written by the run that did the emitting; the
        inference is a fallback for trees that have none."""
        with _Tree(phase=["alpha"]) as t:
            path = os.path.join(t.root, "_audit", "emit_mode.json")
            with io.open(path, "w", encoding="utf-8") as fh:
                json.dump({"schema": 1, "suites": {"alpha": "classic"}}, fh)
            # alpha reads `classic` from the marker, so a classic run has
            # nothing to clash with even though Specs1.java is on disk.
            rc._refuse_mixed_emit_mode(t.root, ["beta"], classic=True)


class RecordsWhatItEmitted(unittest.TestCase):

    def _with_output(self, root, *suites):
        """Give each suite emitted files, so pruning leaves it alone."""
        for suite in suites:
            os.makedirs(os.path.join(root, "src", "main", "java", "com",
                                     "hi", "api", "support", suite),
                        exist_ok=True)

    def test_the_mode_round_trips(self):
        with _Tree(marker=False) as t:
            self._with_output(t.root, "alpha", "beta")
            rc._record_emit_modes(t.root, ["alpha"], classic=True)
            rc._record_emit_modes(t.root, ["beta"], classic=False)
            self.assertEqual(rc._read_emit_modes(t.root),
                             {"alpha": "classic", "beta": "phase"})

    def test_reconverting_a_suite_updates_its_mode(self):
        with _Tree(marker=False) as t:
            self._with_output(t.root, "alpha")
            rc._record_emit_modes(t.root, ["alpha"], classic=True)
            rc._record_emit_modes(t.root, ["alpha"], classic=False)
            self.assertEqual(rc._read_emit_modes(t.root), {"alpha": "phase"})

    def test_a_suite_with_no_files_left_loses_its_entry(self):
        """A suite dropped from the input directory kept its mode
        forever, and a stale `classic` would then refuse a phase convert
        on behalf of a suite that was not on disk to clash with."""
        with _Tree(marker=False) as t:
            self._with_output(t.root, "alpha", "beta")
            rc._record_emit_modes(t.root, ["alpha", "beta"], classic=True)
            shutil.rmtree(os.path.join(t.root, "src", "main", "java", "com",
                                       "hi", "api", "support", "beta"))
            rc._record_emit_modes(t.root, ["alpha"], classic=True)
            self.assertEqual(rc._read_emit_modes(t.root), {"alpha": "classic"})

    def test_a_suite_that_still_has_files_keeps_its_entry(self):
        """The other half: pruning must not forget a suite that is still
        on disk just because this run did not convert it -- that is the
        very suite the refusal exists to protect."""
        with _Tree(marker=False) as t:
            self._with_output(t.root, "alpha", "beta")
            rc._record_emit_modes(t.root, ["alpha", "beta"], classic=False)
            rc._record_emit_modes(t.root, ["alpha"], classic=False)
            self.assertEqual(sorted(rc._read_emit_modes(t.root)),
                             ["alpha", "beta"])

    def test_an_unwritable_target_is_reported_not_raised(self):
        """A convert that did its work must not die on bookkeeping."""
        rc._record_emit_modes(os.path.join(os.sep, "no", "such", "root"),
                              ["alpha"], classic=True)


class InlinedBodiesUseLocalsNotFields(unittest.TestCase):
    """Bodies are rendered FOR a scenario class, where a response read by
    a later phase has to outlive the call and is a field. Inlined into one
    method they are all in scope, and the field form would not compile:
    the @Test class declares no such field and classic emits no class that
    does."""

    def test_the_first_assignment_declares_the_local(self):
        got = rc.Emitter._classic_locals_not_fields(
            None, ["        this.fooRes = given().get(url);"], set())
        self.assertEqual(got, ["        Response fooRes = given().get(url);"])

    def test_a_repeated_name_is_assigned_not_redeclared(self):
        """A step name can repeat inside one case, and two declarations of
        the same local do not compile."""
        got = rc.Emitter._classic_locals_not_fields(
            None,
            ["        this.fooRes = a();", "        this.fooRes = b();"],
            set())
        self.assertEqual(got, ["        Response fooRes = a();",
                               "        fooRes = b();"])

    def test_indentation_survives(self):
        got = rc.Emitter._classic_locals_not_fields(
            None, ["            this.xRes = a();"], set())
        self.assertTrue(got[0].startswith("            Response xRes"))

    def test_other_lines_are_untouched(self):
        lines = ["        softAssert.assertAll();",
                 "        // this.notAnAssignment",
                 "        LOG.info(\"x\");"]
        self.assertEqual(
            rc.Emitter._classic_locals_not_fields(
                None, list(lines), set()),
            lines)


class DanglingResponsesAreDeclared(unittest.TestCase):
    """A ReadyAPI case can read the response of a step that is DISABLED
    in the source, so nothing emits that exchange. The scenario class
    absorbed it -- every response var is a field there, so the read
    compiled and was null. Inlined into a @Test there are no fields and
    the case stopped compiling with `cannot find symbol: variable
    http_request_shop_get_200_success_2Res`.

    Declaring them null keeps classic's behaviour identical to the phase
    tree's. The point is not to invent a difference -- and not to hide
    one either, which is why the generated code says why it is null.
    """

    def _emitter(self):
        e = rc.Emitter.__new__(rc.Emitter)

        class _Ledger:
            def __init__(self):
                self.findings = []

            def add_preflight_finding(self, sev, kind, case, msg):
                self.findings.append((sev, kind, case, msg))

        e.ledger = _Ledger()
        e._current_case = "case1"
        return e

    def test_a_read_without_an_assignment_is_declared(self):
        e = self._emitter()
        out = e._classic_declare_dangling_responses(
            ["doThing(barRes);"], set())
        self.assertIn("Response barRes = null;", out)

    def test_an_assigned_response_is_not_redeclared(self):
        """Declaring it twice is the bug this would otherwise create."""
        e = self._emitter()
        out = e._classic_declare_dangling_responses(
            ["Response fooRes = given().get(u);", "doThing(fooRes);"],
            {"fooRes"})
        self.assertEqual(
            [l for l in out if l.startswith("Response fooRes")],
            ["Response fooRes = given().get(u);"])

    def test_a_name_inside_a_string_is_not_a_reference(self):
        """`putExtracted(ctx, "fooRes", ...)` names a ctx KEY, not a
        variable, and declaring a local for it would be noise at best."""
        e = self._emitter()
        out = e._classic_declare_dangling_responses(
            ['putExtracted(ctx, "notAVarRes", x);'], set())
        self.assertFalse([l for l in out if l.startswith("Response ")])

    def test_a_commented_name_is_not_a_reference(self):
        e = self._emitter()
        out = e._classic_declare_dangling_responses(
            ["// see bazRes for the shape"], set())
        self.assertFalse([l for l in out if l.startswith("Response ")])

    def test_nothing_is_prepended_when_there_is_nothing_to_declare(self):
        e = self._emitter()
        lines = ["Response fooRes = given().get(u);"]
        self.assertEqual(
            e._classic_declare_dangling_responses(list(lines), {"fooRes"}),
            lines)

    def test_it_is_recorded_not_just_patched(self):
        """A null response reaching translated Groovy is worth knowing
        about even though it is pre-existing."""
        e = self._emitter()
        e._classic_declare_dangling_responses(["doThing(barRes);"], set())
        kinds = [f[1] for f in e.ledger.findings]
        self.assertIn("classic-null-response", kinds)


class ClassicFallsBackRatherThanGuess(unittest.TestCase):
    """Rendering inline is only correct when classic reproduces what the
    typed client would have done. Where it does not, the RestStep form
    is kept and the reason is recorded.

    The Salesforce case is the one that matters. `_is_salesforce_step`
    (service names Salesforce) triggers a host AND path rewrite in the
    client emitter, because ReadyAPI stores those paths relative to a My
    Domain host. `_is_salesforce_oauth_step` is only the token POST.
    Falling back for the narrow set alone sent Salesforce DATA calls to
    baseUrl -- the wrong host, with nothing in the output saying so.
    """

    class _Step:
        def __init__(self, service="", method_name="", resource_path="/x",
                     attachments=()):
            self.service = service
            self.method_name = method_name
            self.resource_path = resource_path
            self.attachments = list(attachments)
            self.step_name = "step"
            self.original_uri = ""
            self.http_method = "GET"

    def _render(self, step):
        e = rc.Emitter.__new__(rc.Emitter)

        class _Ledger:
            def __init__(self):
                self.findings = []

            def add_preflight_finding(self, sev, kind, case, msg):
                self.findings.append((sev, kind, case, msg))

        e.ledger = _Ledger()
        e._locals_in_method = set()
        e.classic_enabled = True
        out = e._render_rest_call_classic(
            step=step, sid="step", suf="", verb="GET",
            resolved_path_expr='"/x"', response_var="stepRes",
            token_expr='""', query_entries=[], header_entries=[],
            template_expr=None, expected_status=200,
            needs_regen=False, poll_spec=None)
        return out, e.ledger.findings

    def test_an_ordinary_step_renders_inline(self):
        out, _ = self._render(self._Step())
        self.assertIsNotNone(out, "nothing here needs the RestStep form")
        self.assertTrue(any("RestAssured.given()" in l for l in out))

    def test_a_salesforce_DATA_step_falls_back(self):
        """The regression this class exists for: not an OAuth step, so
        the narrow check passed it through to be routed at baseUrl."""
        out, findings = self._render(
            self._Step(service="Salesforce", method_name="getAccount",
                       resource_path="/data/v55.0/sobjects/Account"))
        self.assertIsNone(out, "a Salesforce step must keep RestStep")
        self.assertTrue(any("host/path rewrite" in f[3] for f in findings),
                        findings)

    def test_a_salesforce_oauth_step_falls_back(self):
        out, findings = self._render(
            self._Step(service="Salesforce", method_name="auth",
                       resource_path="/oauth2/token"))
        self.assertIsNone(out)
        self.assertTrue(any("OAuth" in f[3] for f in findings), findings)

    def test_a_multipart_step_falls_back(self):
        out, findings = self._render(
            self._Step(attachments=[{"name": "f.pdf"}]))
        self.assertIsNone(out)
        self.assertTrue(any("multipart" in f[3] for f in findings), findings)

    def test_regen_and_poll_no_longer_fall_back(self):
        """Stage 2: both run through RestStep.execute now, so neither is
        a reason to refuse. 3,503 and 844 findings respectively before."""
        e_out, _ = self._render(self._Step())
        self.assertIsNotNone(e_out)
        out, findings = self._render(self._Step())
        self.assertIsNotNone(out)
        self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
