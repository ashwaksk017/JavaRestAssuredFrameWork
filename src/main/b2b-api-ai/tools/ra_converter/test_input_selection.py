"""`--input` converts what it was given, and says what it did not.

Naming one XML used to convert EVERY sibling, whenever the file happened
to sit in a folder called `input`:

    if os.path.basename(parent).lower() == "input" and len(siblings) > 1:
        return siblings

So `--input tools/ra_converter/input/PartialGoalRegression.xml` converted
29 suites. Two things came of that:

  * the argument was ignored. A caller who named one file got the whole
    tree, and the only clue was one line in several thousand.
  * the autofix ladder's `repro-convert` rung -- whose entire purpose is
    "convert only the suites the failure points at (~150s each, against
    ~40min for all)" -- passed a single XML and therefore always ran the
    full convert. The cheap rung was the expensive one.

A partial convert IS still partial in a way that matters: shared phases
and clustering are computed across the whole set. That is why the note is
printed every time rather than the subset being quietly treated as
equivalent.

    python tools/ra_converter/test_input_selection.py
"""
from __future__ import annotations

import io
import json
import os
import sys
import shutil
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import ra_converter as rc  # noqa: E402

INPUT_DIR = os.path.join(HERE, "input")


def _names(paths):
    return [os.path.basename(p) for p in paths]


class _Quiet:
    """The selector prints a subset note; tests do not need to see it."""

    def __enter__(self):
        self._real = sys.stdout
        sys.stdout = io.StringIO()
        return self

    def __exit__(self, *exc):
        self.text = sys.stdout.getvalue()
        sys.stdout = self._real
        return False


def select(arg):
    with _Quiet() as q:
        out = rc._discover_input_xmls(arg)
    select.last_output = q.text
    return out


class OneSuite(unittest.TestCase):
    """The regression. Everything else here is about not breaking it."""

    def setUp(self):
        self.one = os.path.join(INPUT_DIR, "PartialGoalRegression.xml")
        if not os.path.isfile(self.one):
            self.skipTest("PartialGoalRegression.xml not in input/")

    def test_naming_one_xml_converts_exactly_that_suite(self):
        self.assertEqual(_names(select(self.one)),
                         ["PartialGoalRegression.xml"])

    def test_it_says_which_suites_it_did_not_convert(self):
        select(self.one)
        out = select.last_output
        self.assertIn("were NOT converted", out)
        self.assertIn("NOT side-effect-free", out,
                      "a subset convert still rewrites CSV rows in other "
                      "suites; saying nothing would let it read as whole")

    def test_a_bare_suite_name_resolves_against_the_drop_folder(self):
        self.assertEqual(_names(select("PartialGoalRegression")),
                         ["PartialGoalRegression.xml"])

    def test_the_xml_extension_is_optional(self):
        self.assertEqual(
            _names(select(os.path.join(INPUT_DIR, "PartialGoalRegression"))),
            ["PartialGoalRegression.xml"])


class SeveralSuites(unittest.TestCase):
    def setUp(self):
        self.have = [n for n in ("PartialGoalRegression", "InventorySTAGE",
                                 "MFRSTAGE")
                     if os.path.isfile(os.path.join(INPUT_DIR, n + ".xml"))]
        if len(self.have) < 2:
            self.skipTest("need two input XMLs to test a comma list")

    def test_a_comma_list_converts_each_one(self):
        got = _names(select(",".join(self.have)))
        self.assertEqual(len(got), len(self.have))
        for n in self.have:
            self.assertIn(n + ".xml", got)

    def test_whitespace_around_the_commas_is_tolerated(self):
        got = _names(select(" , ".join(self.have)))
        self.assertEqual(len(got), len(self.have))

    def test_the_same_suite_twice_is_converted_once(self):
        a = self.have[0]
        self.assertEqual(len(select(f"{a},{a}")), 1)

    def test_order_follows_the_command_line(self):
        got = _names(select(",".join(reversed(self.have))))
        self.assertEqual(got[0], self.have[-1] + ".xml")


class TheWholeTree(unittest.TestCase):
    def test_a_directory_still_converts_everything(self):
        if not os.path.isdir(INPUT_DIR):
            self.skipTest("no input/ directory")
        got = select(INPUT_DIR)
        on_disk = [n for n in os.listdir(INPUT_DIR) if n.lower().endswith(".xml")]
        self.assertEqual(len(got), len(on_disk))

    def test_a_directory_prints_no_subset_note(self):
        if not os.path.isdir(INPUT_DIR):
            self.skipTest("no input/ directory")
        select(INPUT_DIR)
        self.assertNotIn("were NOT converted", select.last_output,
                         "the authoritative run is not a subset of anything")


class Refusals(unittest.TestCase):
    def test_a_name_that_matches_nothing_is_refused(self):
        with self.assertRaises(SystemExit) as e:
            select("NoSuchSuiteAnywhere")
        self.assertIn("not found", str(e.exception))

    def test_a_list_naming_the_unknown_ones_is_refused_listing_them(self):
        """Converting the half it understood would be worse: the run would
        look complete and quietly skip what was asked for."""
        with self.assertRaises(SystemExit) as e:
            select("PartialGoalRegression,NoSuchSuite,AlsoMissing")
        msg = str(e.exception)
        self.assertIn("NoSuchSuite", msg)
        self.assertIn("AlsoMissing", msg)

    def test_an_empty_directory_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(SystemExit) as e:
                select(td)
            self.assertIn("no .xml files", str(e.exception))

    def test_a_literal_path_wins_over_a_comma_split(self):
        """A filename may legally contain a comma. Splitting a real file
        into two missing ones would be a worse failure than not
        supporting the name."""
        with tempfile.TemporaryDirectory() as td:
            odd = os.path.join(td, "a,b.xml")
            with io.open(odd, "w", encoding="utf-8") as fh:
                fh.write("<x/>")
            self.assertEqual(select(odd), [os.path.abspath(odd)])


class EmittedTestsAreVerified(unittest.TestCase):
    """A suite that emits no runnable test must fail the convert.

    `_verify_emitted_suite_support` checked only TestSupport.java and
    SetupHelper.java. Neither is a test, and both are emitted from the
    suite NAME while the tests come from its cases -- so the two fail
    apart. Observed: a run reported all 29 suites as
    `TestSupport=yes, SetupHelper=yes` and exited 0 while 11 of them,
    `programaccountregression` included, emitted zero test classes.
    """

    def _tree(self, td, suite, with_test):
        pkg = os.path.join("com", "hi", "api")
        sup = os.path.join(td, "src/main/java", pkg, "support", suite)
        os.makedirs(sup, exist_ok=True)
        for n in ("TestSupport.java", "SetupHelper.java"):
            with io.open(os.path.join(sup, n), "w", encoding="utf-8") as fh:
                fh.write("class X {}")
        fw = os.path.join(td, "src/main/java", pkg, "support")
        for n in getattr(rc.Emitter, "_FRAMEWORK_SUPPORT_FILES", ()):
            path = os.path.join(fw, n)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with io.open(path, "w", encoding="utf-8") as fh:
                fh.write("class X {}")
        tests = os.path.join(td, "src/test/java", pkg, "tests", "imported", suite)
        os.makedirs(tests, exist_ok=True)
        if with_test:
            with io.open(os.path.join(tests, "ThingTest.java"), "w",
                         encoding="utf-8") as fh:
                fh.write("class ThingTest {}")

    def test_a_suite_with_a_test_class_passes(self):
        with tempfile.TemporaryDirectory() as td:
            self._tree(td, "mysuite", with_test=True)
            with _Quiet():
                missing = rc._verify_emitted_suite_support(
                    td, "com.hi.api", [("mysuite.xml", "mysuite", "My")])
            self.assertEqual(missing, [])

    def test_a_suite_with_NO_test_class_is_reported_missing(self):
        with tempfile.TemporaryDirectory() as td:
            self._tree(td, "mysuite", with_test=False)
            with _Quiet() as q:
                missing = rc._verify_emitted_suite_support(
                    td, "com.hi.api", [("mysuite.xml", "mysuite", "My")])
            self.assertIn("mysuite", missing,
                          "support files alone are not a converted suite")
            self.assertIn("tests=NONE", q.text,
                          "the inventory line must say so, not just the exit code")


class _Drop:
    """A throwaway --input folder holding `names` as empty XML files.

    These tests used to run against the real `tools/ra_converter/input`
    and skip when it was absent -- which is every clone, because that
    folder is gitignored customer XML. So the checks guarding convert
    scope did not run for anyone but me.

    `_xmls_in_dir` lists by extension and `_default_suite_name` reads the
    basename; neither parses the file, so empty files are enough.
    """

    def __init__(self, *names):
        self.names = names

    def __enter__(self):
        self.td = tempfile.mkdtemp()
        for n in self.names:
            io.open(os.path.join(self.td, n + ".xml"), "w").close()
        return self

    def __exit__(self, *exc):
        shutil.rmtree(self.td, ignore_errors=True)
        return False

    def select(self, token=None):
        """Resolve --input the way a real run does, so the scope this run
        is judged against is the one discovery recorded -- not a guess
        made by the test."""
        with _Quiet():
            return rc._discover_input_xmls(token or self.td)

    def suites(self):
        return [rc._default_suite_name(x) for x in rc._xmls_in_dir(self.td)]


class ConvertScopeMarker(unittest.TestCase):
    """The marker is WRITTEN, not just read.

    This existed and shipped with `io.open` in a module that imports no
    `io`. Nothing caught it because the only coverage was of the READING
    side -- the marker was hand-written in a test and the gate parsed it.
    The write path first ran on a real convert, which had already emitted
    40 files and printed a clean inventory before falling over on the
    bookkeeping.

    So these call the function. A path no test executes is a path that
    ships broken.
    """

    def test_a_subset_convert_writes_the_marker(self):
        with _Drop("alpha", "beta", "gamma") as d:
            d.select(os.path.join(d.td, "alpha.xml"))
            with tempfile.TemporaryDirectory() as td:
                with _Quiet():
                    rc._record_convert_scope(td, ["alpha"])
                m = os.path.join(td, "_audit", "partial_convert.json")
                self.assertTrue(os.path.isfile(m),
                                "subset convert must record")
                with io.open(m, encoding="utf-8") as fh:
                    dd = json.load(fh)
        self.assertEqual(dd["converted"], ["alpha"])
        self.assertEqual(sorted(dd["not_converted"]), ["beta", "gamma"])
        self.assertIn("phase", dd["why_it_matters"].lower())

    def test_a_full_convert_removes_it(self):
        """A marker that outlived its condition would make every later
        green run look suspect."""
        with _Drop("alpha", "beta", "gamma") as d:
            with tempfile.TemporaryDirectory() as td:
                d.select(os.path.join(d.td, "alpha.xml"))
                with _Quiet():
                    rc._record_convert_scope(td, ["alpha"])
                d.select()                      # the whole folder
                with _Quiet():
                    rc._record_convert_scope(td, d.suites())
                self.assertFalse(
                    os.path.isfile(os.path.join(td, "_audit",
                                                "partial_convert.json")))

    def test_the_whole_of_ANY_folder_counts_as_a_full_convert(self):
        """The scope is judged against the folder --input resolved
        against, not against `tools/ra_converter/input`. Converting every
        XML in some other drop folder -- which is what a user with their
        own download directory does -- was reported as a partial convert,
        and the gate then refused tree-wide checks on a complete run."""
        with _Drop("alpha", "beta") as d:
            d.select()
            with tempfile.TemporaryDirectory() as td:
                with _Quiet() as q:
                    rc._record_convert_scope(td, ["alpha", "beta"])
                self.assertFalse(
                    os.path.isfile(os.path.join(td, "_audit",
                                                "partial_convert.json")),
                    "every suite in the named folder was converted")
        self.assertNotIn("PARTIAL convert recorded", q.text)

    def test_an_unjudgeable_scope_is_recorded_as_partial(self):
        """Fail CLOSED. "Complete" is a licence for the gate to run
        tree-wide checks, so a run that cannot establish what it was
        selecting from must not be granted it. The old code read an
        empty candidate set as "nothing left over, must be full", so on
        a clone -- where the drop folder is absent -- no partial convert
        was ever flagged."""
        rc._INPUT_SCOPE.clear()
        with tempfile.TemporaryDirectory() as td:
            with _Quiet() as q:
                rc._record_convert_scope(td, ["alpha"])
            self.assertTrue(
                os.path.isfile(os.path.join(td, "_audit",
                                            "partial_convert.json")),
                "unknown scope must not pass as complete")
        self.assertIn("UNKNOWN", q.text)

    def test_a_comma_list_spanning_two_folders_is_unjudgeable(self):
        """There is no single set to be complete against, so the run is
        not claimed to be complete."""
        with _Drop("alpha") as a, _Drop("beta") as b:
            with _Quiet():
                rc._note_input_scope(
                    [os.path.join(a.td, "alpha.xml"),
                     os.path.join(b.td, "beta.xml")], a.td)
            self.assertEqual(rc._INPUT_SCOPE.get("parent"), "")

    def test_it_says_so_on_the_console_too(self):
        with _Drop("alpha", "beta") as d:
            d.select(os.path.join(d.td, "alpha.xml"))
            with tempfile.TemporaryDirectory() as td:
                with _Quiet() as q:
                    rc._record_convert_scope(td, ["alpha"])
        self.assertIn("PARTIAL convert recorded", q.text)

    def test_an_unwritable_target_is_reported_not_raised(self):
        """A convert that did its work must not die on bookkeeping."""
        with _Drop("alpha", "beta") as d:
            d.select()
            with _Quiet() as q:
                rc._record_convert_scope(
                    os.path.join(os.sep, "no", "such", "root", "x"),
                    ["alpha"])
        self.assertNotIn("Traceback", q.text)


class EveryDiscoveryBranchRecordsItsScope(unittest.TestCase):
    """Whatever form `--input` took, the run must know what it selected
    FROM -- otherwise `_record_convert_scope` cannot judge completeness
    and falls back to calling the run partial.

    One test per accepted form rather than one for the form I happened to
    change, because the cost of a branch that forgets to record is not an
    error: it is a complete convert quietly reported as partial, and a
    gate that then declines to check the tree.
    """

    def setUp(self):
        rc._INPUT_SCOPE.clear()

    def _parent_after(self, token):
        with _Quiet():
            rc._discover_input_xmls(token)
        return rc._INPUT_SCOPE.get("parent")

    def test_a_directory(self):
        with _Drop("alpha", "beta") as d:
            self.assertEqual(os.path.normcase(self._parent_after(d.td)),
                             os.path.normcase(d.td))

    def test_a_single_file_path(self):
        with _Drop("alpha", "beta") as d:
            got = self._parent_after(os.path.join(d.td, "alpha.xml"))
            self.assertEqual(os.path.normcase(got), os.path.normcase(d.td))

    def test_a_comma_list_of_paths(self):
        with _Drop("alpha", "beta", "gamma") as d:
            got = self._parent_after(os.path.join(d.td, "alpha.xml") + ","
                                     + os.path.join(d.td, "beta.xml"))
            self.assertEqual(os.path.normcase(got), os.path.normcase(d.td))

    def test_a_bare_suite_name(self):
        """Resolved against the conventional drop folder, so the scope is
        that folder -- and a bare name is one suite out of many, which is
        exactly the case that must come out partial."""
        if not os.path.isdir(INPUT_DIR):
            self.skipTest("no input/ directory")
        name = rc._default_suite_name(rc._xmls_in_dir(INPUT_DIR)[0])
        got = self._parent_after(name)
        self.assertEqual(os.path.normcase(got or ""),
                         os.path.normcase(INPUT_DIR))

    def test_the_selection_is_recorded_too_not_only_the_folder(self):
        with _Drop("alpha", "beta") as d:
            self._parent_after(os.path.join(d.td, "alpha.xml"))
            self.assertEqual(
                [os.path.basename(x)
                 for x in rc._INPUT_SCOPE.get("selected") or []],
                ["alpha.xml"])


def _steps_file(out, suite, marked):
    """A suite's steps base as the converter writes it, with or without
    the per-suite vocabulary marker."""
    d = os.path.join(out, "src", "main", "java", "com", "hi", "api",
                     "support", suite, "scenario")
    os.makedirs(d)
    with io.open(os.path.join(d, suite.capitalize() + "Steps.java"), "w",
                 encoding="utf-8") as fh:
        fh.write("/** %s */ public abstract class %sSteps {}"
                 % (rc.SUITE_VOCAB_MARKER if marked else "an older layout",
                    suite.capitalize()))


class IsolatedSubsetConvert(unittest.TestCase):
    """A subset convert is a hazard only while something is still shared.

    Each suite now carries its own chain methods, so converting one leaves
    the others exactly as they were -- measured as 320 generated files
    byte-identical after two single-suite reconverts. The marker used to
    say "tree-wide checks are not meaningful" for every subset convert,
    which stopped being true and would have trained readers to ignore it.

    "Isolated" is claimed from positive evidence only. Every way of being
    unsure has a test here, and each must come out NOT isolated.
    """

    def setUp(self):
        self._modes = (rc._PHASE_SPECS, rc._CLASSIC)
        rc._PHASE_SPECS, rc._CLASSIC = True, False

    def tearDown(self):
        rc._PHASE_SPECS, rc._CLASSIC = self._modes

    def test_every_suite_marked_is_isolated(self):
        with tempfile.TemporaryDirectory() as td:
            _steps_file(td, "alpha", True)
            _steps_file(td, "beta", True)
            self.assertTrue(
                rc._subset_convert_is_isolated(td, "com.hi.api", {"alpha"}))

    def test_one_older_suite_on_disk_is_enough_to_say_no(self):
        with tempfile.TemporaryDirectory() as td:
            _steps_file(td, "alpha", True)
            _steps_file(td, "beta", False)     # resolves through the shared base
            self.assertFalse(
                rc._subset_convert_is_isolated(td, "com.hi.api", {"alpha"}))

    def test_a_converted_suite_with_no_marker_of_its_own_is_no(self):
        with tempfile.TemporaryDirectory() as td:
            _steps_file(td, "beta", True)
            # alpha was "converted" but nothing on disk says how
            self.assertFalse(
                rc._subset_convert_is_isolated(td, "com.hi.api", {"alpha"}))

    def test_the_other_emit_modes_are_never_isolated(self):
        with tempfile.TemporaryDirectory() as td:
            _steps_file(td, "alpha", True)
            rc._CLASSIC = True
            self.assertFalse(
                rc._subset_convert_is_isolated(td, "com.hi.api", {"alpha"}))
            rc._CLASSIC, rc._PHASE_SPECS = False, False
            self.assertFalse(
                rc._subset_convert_is_isolated(td, "com.hi.api", {"alpha"}))

    def test_unknown_root_empty_tree_and_empty_run_are_no(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertFalse(
                rc._subset_convert_is_isolated(td, "com.hi.api", {"alpha"}))
            _steps_file(td, "alpha", True)
            self.assertFalse(
                rc._subset_convert_is_isolated(td, None, {"alpha"}))
            self.assertFalse(
                rc._subset_convert_is_isolated(td, "com.hi.api", set()))

    def test_the_check_does_not_disturb_the_run_wide_suite_set(self):
        """It clears _SUITES_THIS_RUN to judge every suite on disk; the
        set must come back, or the next emit forgets what it converted."""
        with tempfile.TemporaryDirectory() as td:
            _steps_file(td, "alpha", True)
            saved = set(rc._SUITES_THIS_RUN)
            rc._SUITES_THIS_RUN.update({"alpha", "zeta"})
            try:
                rc._subset_convert_is_isolated(td, "com.hi.api", {"alpha"})
                self.assertTrue({"alpha", "zeta"} <= rc._SUITES_THIS_RUN)
            finally:
                rc._SUITES_THIS_RUN.clear()
                rc._SUITES_THIS_RUN.update(saved)

    def _record(self, td, marked_beta):
        _steps_file(td, "alpha", True)
        _steps_file(td, "beta", marked_beta)
        with _Quiet() as q:
            rc._record_convert_scope(td, ["alpha"], "com.hi.api")
        with io.open(os.path.join(td, "_audit", "partial_convert.json"),
                     encoding="utf-8") as fh:
            return json.load(fh), q.text

    def test_an_isolated_subset_is_recorded_as_such_and_spares_the_catalog(self):
        import fluent_scenario as fs
        if not os.path.isfile(fs.catalog_path()):
            self.skipTest("no catalog in this tree")
        before = io.open(fs.catalog_path(), encoding="utf-8").read()
        try:
            with _Drop("alpha", "beta", "gamma") as d:
                d.select(os.path.join(d.td, "alpha.xml"))
                with tempfile.TemporaryDirectory() as td:
                    dd, text = self._record(td, marked_beta=True)
            self.assertIs(dd["isolated"], True)
            self.assertEqual(dd["converted"], ["alpha"])
            self.assertEqual(sorted(dd["not_converted"]), ["beta", "gamma"])
            self.assertIn("left exactly as they were", dd["why_it_matters"])
            self.assertIn("NOT touched", text)
            self.assertNotIn("PARTIAL convert recorded", text)
            self.assertEqual(io.open(fs.catalog_path(), encoding="utf-8").read(),
                             before, "nothing was recomputed from a subset, so "
                             "the catalog must not be marked incomplete")
        finally:
            io.open(fs.catalog_path(), "w", encoding="utf-8",
                    newline="").write(before)

    def test_a_subset_beside_an_older_suite_keeps_the_loud_marker(self):
        """NEGATIVE CONTROL for the test above: same call, one suite on
        disk without the marker, and everything goes back to loud."""
        import fluent_scenario as fs
        if not os.path.isfile(fs.catalog_path()):
            self.skipTest("no catalog in this tree")
        before = io.open(fs.catalog_path(), encoding="utf-8").read()
        try:
            with _Drop("alpha", "beta", "gamma") as d:
                d.select(os.path.join(d.td, "alpha.xml"))
                with tempfile.TemporaryDirectory() as td:
                    dd, text = self._record(td, marked_beta=False)
            self.assertIs(dd["isolated"], False)
            self.assertIn("phase", dd["why_it_matters"].lower())
            self.assertIn("PARTIAL convert recorded", text)
            with io.open(fs.catalog_path(), encoding="utf-8") as fh:
                self.assertTrue(json.load(fh).get("incomplete"))
        finally:
            io.open(fs.catalog_path(), "w", encoding="utf-8",
                    newline="").write(before)


class CatalogAfterAPartialConvert(unittest.TestCase):
    """Votes from part of the tree must not be inherited as if whole.

    fluent_catalog.json accumulates phase votes ACROSS runs, and the
    shared / suite-local split is computed from them. An emitter change
    invalidates the catalog -- correctly: cached Java from a converter
    that no longer exists must be rebuilt. The danger is what runs NEXT.
    A partial convert repopulates the catalog from its own suites and
    leaves it looking complete, so a later run recomputes the split from
    a fraction of the votes while the suites that were NOT converted are
    still on disk, emitted against the previous split. That mismatch is
    what surfaces as thousands of `phase-order` findings in a suite
    nobody touched.
    """

    def _catalog(self):
        import fluent_scenario as fs
        with io.open(fs.catalog_path(), encoding="utf-8") as fh:
            return json.load(fh)

    def test_a_partial_convert_marks_the_catalog_incomplete(self):
        import fluent_scenario as fs
        if not os.path.isfile(fs.catalog_path()):
            self.skipTest("no catalog in this tree")
        before = io.open(fs.catalog_path(), encoding="utf-8").read()
        try:
            with _Drop("alpha", "beta", "gamma") as d:
                d.select(os.path.join(d.td, "alpha.xml"))
                with tempfile.TemporaryDirectory() as td:
                    with _Quiet():
                        rc._record_convert_scope(td, ["alpha"])
            dd = self._catalog()
            self.assertTrue(dd.get("incomplete"),
                            "a subset's votes must be rebuilt, not inherited")
            self.assertTrue(any("partial convert" in r for r in
                                dd.get("incompleteReason") or []))
        finally:
            io.open(fs.catalog_path(), "w", encoding="utf-8",
                    newline="").write(before)

    def test_a_full_convert_leaves_the_catalog_alone(self):
        import fluent_scenario as fs
        if not os.path.isfile(fs.catalog_path()):
            self.skipTest("no catalog in this tree")
        before = io.open(fs.catalog_path(), encoding="utf-8").read()
        try:
            with _Drop("alpha", "beta", "gamma") as d:
                d.select()
                with tempfile.TemporaryDirectory() as td:
                    with _Quiet():
                        rc._record_convert_scope(td, d.suites())
            self.assertFalse(self._catalog().get("incomplete"),
                             "a whole-tree convert's votes ARE complete")
        finally:
            io.open(fs.catalog_path(), "w", encoding="utf-8",
                    newline="").write(before)

    def test_the_flag_is_cleared_not_merely_left_unset(self):
        """The clear is the half that is easy to omit. Without it the flag
        is one-way: the first partial run sets it and every later convert
        rebuilds its phases from scratch forever, so the catalog -- whose
        whole purpose is accumulating votes across runs -- quietly stops
        doing its job."""
        import fluent_scenario as fs
        if not os.path.isfile(fs.catalog_path()):
            self.skipTest("no catalog in this tree")
        before = io.open(fs.catalog_path(), encoding="utf-8").read()
        try:
            with _Drop("alpha", "beta") as d:
                with tempfile.TemporaryDirectory() as td:
                    d.select(os.path.join(d.td, "alpha.xml"))
                    with _Quiet():
                        rc._record_convert_scope(td, ["alpha"])
                    self.assertTrue(self._catalog().get("incomplete"),
                                    "precondition: the flag is set")
                    d.select()
                    with _Quiet():
                        rc._record_convert_scope(td, d.suites())
            dd = self._catalog()
            self.assertFalse(dd.get("incomplete"))
            self.assertNotIn("incompleteReason", dd,
                             "a stale reason would outlive its flag")
        finally:
            io.open(fs.catalog_path(), "w", encoding="utf-8",
                    newline="").write(before)

    def test_an_emitter_change_keeps_the_suite_inventory(self):
        """`suites` is {name: {serviceName}} -- metadata, not a cached Java
        body, so the "rebuild rather than replay" reasoning does not apply.
        Dropping it let a partial convert rewrite the inventory to one
        suite while 28 others sat on disk."""
        import fluent_scenario as fs
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "fluent_catalog.json")
            doc = {
                "phases": {"readThing": {"key": "k", "body": "b", "cases": []}},
                "verifies": {}, "bootstrap": None,
                "suites": {"alpha": {"serviceName": "Alpha"},
                           "beta": {"serviceName": "Beta"}},
                "clientMethods": ["m"], "shapes": {"s": 1},
                "vocabularyVersion": "old", "emitterVersion": "DEFINITELY-STALE",
            }
            with io.open(path, "w", encoding="utf-8", newline="") as fh:
                json.dump(doc, fh)
            real = fs.catalog_path
            fs.catalog_path = lambda: path
            try:
                with _Quiet():
                    got = fs.load_fluent_catalog()
            finally:
                fs.catalog_path = real
        self.assertEqual(got.get("phases"), {},
                         "cached bodies from a dead converter must go")
        self.assertEqual(sorted(got.get("suites") or {}), ["alpha", "beta"],
                         "the suite inventory is metadata and must survive")
        self.assertEqual(got.get("clientMethods"), ["m"])


    def _load(self, doc):
        """Load `doc` as the catalog and hand back what the loader kept."""
        import fluent_scenario as fs
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "fluent_catalog.json")
            with io.open(path, "w", encoding="utf-8", newline="") as fh:
                json.dump(doc, fh)
            real = fs.catalog_path
            fs.catalog_path = lambda: path
            try:
                with _Quiet():
                    return fs.load_fluent_catalog()
            finally:
                fs.catalog_path = real

    def _stocked(self, **over):
        import fluent_scenario as fs
        doc = {
            "phases": {"readThing": {"key": "k", "body": "b", "cases": []}},
            "verifies": {}, "bootstrap": None,
            "suites": {"alpha": {"serviceName": "Alpha"},
                       "beta": {"serviceName": "Beta"}},
            "clientMethods": ["m"], "shapes": {"s": {"engine": "readThing"}},
            "vocabularyVersion": fs.phase_vocabulary.version(),
            "emitterVersion": fs.emitter_version(),
        }
        doc.update(over)
        return doc

    def test_the_incomplete_flag_keeps_the_suite_names_too(self):
        """The branch that reacts to the marker had the same hole as the
        emitter branch: it rebuilt phases AND wiped the inventory. A suite
        renamed on the next convert breaks every other suite on disk that
        calls the old client, which is a tree that does not compile rather
        than a cache that needs refilling."""
        got = self._load(self._stocked(incomplete=True,
                                       incompleteReason=["partial convert"]))
        self.assertEqual(got.get("phases"), {},
                         "votes from part of the tree must be rebuilt")
        self.assertEqual(sorted(got.get("suites") or {}), ["alpha", "beta"],
                         "the suite inventory must survive an incomplete flag")
        self.assertFalse(got.get("incomplete"),
                         "the rebuild satisfies the flag, so it must not "
                         "persist into the catalog this run saves")

    def test_a_vocabulary_bump_keeps_the_suite_names(self):
        got = self._load(self._stocked(vocabularyVersion="STALE"))
        self.assertEqual(got.get("phases"), {})
        self.assertEqual(sorted(got.get("suites") or {}), ["alpha", "beta"],
                         "a suite name is not a vocabulary name")

    def test_a_vocabulary_bump_still_drops_the_shapes(self):
        """Not every key survives, and this one must not: each shape entry
        carries `engine`, which IS a vocabulary name. Keeping shapes here
        would have been the mirror-image bug of dropping suites, so the
        uniform helper takes a flag instead of treating the keys alike."""
        got = self._load(self._stocked(vocabularyVersion="STALE"))
        self.assertEqual(got.get("shapes"), {},
                         "`engine` comes from phase_vocabulary.canonical_name")

    def test_an_emitter_bump_keeps_the_shapes(self):
        """The same flag, the other way: a new emitter changes Java bodies,
        not the names, so churning every client here would be gratuitous."""
        got = self._load(self._stocked(emitterVersion="DEFINITELY-STALE"))
        self.assertEqual(got.get("shapes"), {"s": {"engine": "readThing"}})

    def test_a_current_catalog_is_left_alone(self):
        """The guard on all of the above: if the stock document tripped an
        invalidation by itself, every assertion here would pass for the
        wrong reason."""
        got = self._load(self._stocked())
        self.assertIn("readThing", got.get("phases") or {},
                      "nothing was stale, so nothing should have been reset")


if __name__ == "__main__":
    unittest.main(verbosity=2)
