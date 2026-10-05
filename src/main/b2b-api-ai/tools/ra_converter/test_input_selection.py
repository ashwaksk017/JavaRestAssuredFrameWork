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
import os
import sys
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
