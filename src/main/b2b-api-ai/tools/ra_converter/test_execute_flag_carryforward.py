"""A row switched off in the data sheet must survive the next convert.

THE GENERATED ROW FILES ARE OVERWRITTEN BY EVERY CONVERT. That is the
whole difficulty: a tester who sets `execute=N` on twenty rows and then
reconverts would, without carry-forward, find all twenty running again
with nothing in the output saying the flags had been dropped. The flag
exists to stop rows running, so losing it silently defeats the feature
in the one direction that matters.

Keyed by `test_case_id`, not by row position. Measured across the 1,064
generated row files in this tree: all 1,165 rows carry a non-blank id and
no file contains a duplicate, while positions move whenever a case joins
or leaves a cluster.

Every failure mode resolves to {} -- "everything runs". A carry-forward
that guessed would be worse than one that forgets: it would disable rows
nobody chose, which is exactly the silent coverage loss the fail-open
rule in ExecutionFlag.java exists to prevent.

    python tools/ra_converter/test_execute_flag_carryforward.py
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

REL = "src/test/resources/csv/suite/SomeTest/someMethod.csv"


class _Stub:
    """Just enough of an Emitter for the method under test.

    Bound off the real class so the test exercises the shipped code, not
    a copy of it -- a reimplementation here could pass while the
    converter's own method was broken.
    """

    def __init__(self, output_dir):
        self.output_dir = output_dir

    _existing_execute_flags = rc.Emitter._existing_execute_flags


def write(root, text, rel=REL):
    p = os.path.join(root, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with io.open(p, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return p


class CarryForward(unittest.TestCase):
    def flags(self, text=None, rel=REL):
        with tempfile.TemporaryDirectory() as td:
            if text is not None:
                write(td, text, rel)
            return _Stub(td)._existing_execute_flags(REL)

    def test_a_switched_off_row_is_remembered(self):
        got = self.flags("description,test_case_id,execute\n"
                         "d,CASE_A,N\n"
                         "d,CASE_B,\n")
        self.assertEqual(got, {"CASE_A": "N"})

    def test_every_recognised_value_is_carried_verbatim(self):
        got = self.flags("test_case_id,execute\nA,N\nB,Y\nC,no\nD,FALSE\n")
        self.assertEqual(got, {"A": "N", "B": "Y", "C": "no", "D": "FALSE"})

    def test_it_is_keyed_by_id_not_by_position(self):
        """A case joining a cluster shifts every later row down one. Keyed
        positionally, the flag would move to the wrong scenario -- and a
        flag on the wrong row is worse than none, because the row the
        tester disabled now runs and a different one does not."""
        got = self.flags("test_case_id,execute\nFIRST,\nSECOND,N\n")
        self.assertEqual(got, {"SECOND": "N"})

    def test_blank_flags_are_not_carried(self):
        """Carrying "" would make the next file full of empty cells that
        read identically to no cell at all -- and would grow the dict by
        1,165 useless entries."""
        self.assertEqual(self.flags("test_case_id,execute\nA,\nB,   \n"), {})

    def test_whitespace_around_a_value_is_trimmed(self):
        self.assertEqual(self.flags("test_case_id,execute\nA,  N  \n"),
                         {"A": "N"})

    def test_a_row_with_no_id_is_skipped(self):
        self.assertEqual(self.flags("test_case_id,execute\n,N\n"), {})

    # --- every one of these means "run everything" ------------------
    def test_a_first_ever_convert_has_no_file_and_no_flags(self):
        self.assertEqual(self.flags(None), {})

    def test_a_file_predating_the_column_yields_no_flags(self):
        """The 1,064 files already in this tree have no `execute` column."""
        self.assertEqual(self.flags("description,test_case_id\nd,CASE_A\n"), {})

    def test_a_file_with_no_test_case_id_yields_no_flags(self):
        self.assertEqual(self.flags("description,execute\nd,N\n"), {})

    def test_an_empty_file_yields_no_flags(self):
        self.assertEqual(self.flags(""), {})

    def test_a_header_only_file_yields_no_flags(self):
        self.assertEqual(self.flags("test_case_id,execute\n"), {})

    # --- shapes a real sheet actually has ---------------------------
    def test_a_retyped_header_is_still_read(self):
        """An author who edits the sheet in Excel can retype the header.
        Missing it would drop their flags on the next convert."""
        got = self.flags("test_case_id,Execute\nA,N\n")
        self.assertEqual(got, {"A": "N"})

    def test_a_byte_order_mark_does_not_hide_the_first_column(self):
        """Excel writes one. Read as utf-8 rather than utf-8-sig, the
        first header becomes `\\ufefftest_case_id` and no id is ever
        found -- so every flag in every Excel-edited sheet is lost."""
        got = self.flags("﻿test_case_id,execute\nA,N\n")
        self.assertEqual(got, {"A": "N"})

    def test_quoted_cells_with_commas_do_not_shift_the_columns(self):
        got = self.flags('description,test_case_id,execute\n'
                         '"a description, with a comma",CASE_A,N\n')
        self.assertEqual(got, {"CASE_A": "N"})

    def test_a_description_with_an_embedded_newline_is_handled(self):
        got = self.flags('description,test_case_id,execute\n'
                         '"line one\nline two",CASE_A,N\n')
        self.assertEqual(got, {"CASE_A": "N"})

    def test_the_column_may_sit_anywhere_in_the_header(self):
        got = self.flags("execute,description,test_case_id\nN,d,CASE_A\n")
        self.assertEqual(got, {"CASE_A": "N"})

    def test_an_unreadable_file_reports_and_runs_everything(self):
        with tempfile.TemporaryDirectory() as td:
            # A directory where the CSV should be: open() raises, and the
            # convert must continue rather than die on one row file.
            p = os.path.join(td, REL.replace("/", os.sep))
            os.makedirs(p, exist_ok=True)
            self.assertEqual(_Stub(td)._existing_execute_flags(REL), {})


def converter_source():
    with io.open(os.path.join(HERE, "ra_converter.py"), encoding="utf-8") as fh:
        return fh.read()


class TheColumnIsEmitted(unittest.TestCase):
    """The reader above is useless if the writer never emits the column."""

    def test_the_header_list_carries_execute(self):
        src = converter_source()
        self.assertIn('            "execute",\n', src,
                      "group_a_meta must declare the column")

    def test_the_row_builder_fills_it_from_the_carried_flags(self):
        src = converter_source()
        self.assertIn('_csv_cell(prior_execute.get(c.name, ""))', src,
                      "the cell must come from the carried-forward flags, "
                      "keyed by the case name that becomes test_case_id")

    def test_the_flags_are_read_before_the_rows_are_built(self):
        """`rel` has to be computed above the row loop, or the file being
        replaced cannot be read before it is replaced."""
        src = converter_source()
        read_at = src.index("prior_execute = self._existing_execute_flags(rel)")
        build_at = src.index('_csv_cell(prior_execute.get(c.name, ""))')
        self.assertLess(read_at, build_at)


class XPathToDotted(unittest.TestCase):
    """The EXTRACTION path for a ResponseAsXml ref.

    Leaf-only sent `safeJsonExtract(res, "sourceId")` for a field two
    levels down, which returns null: the placeholder then resolved to
    empty and the request went out without the value, reported by
    nothing. The quiet half of the same fault that the
    `#...#`-sent-verbatim case shows loudly.
    """

    def test_the_e_wrapper_becomes_an_index_on_its_parent(self):
        self.assertEqual(
            rc._xpath_to_dotted(
                "declare namespace ns1='https://example.com/x'; "
                "//ns1:Response[1]/ns1:roomTypeInventory[1]/ns1:e[1]"
                "/ns1:roomTypeCode[1]"),
            "roomTypeInventory[0].roomTypeCode")

    def test_a_positional_predicate_is_not_an_array_index(self):
        """Checked against the tree, not assumed: the emitted suite reads
        this field as `externalMatch.attestation.sourceId` in ten other
        places, so an index here would have been wrong."""
        self.assertEqual(
            rc._xpath_to_dotted(
                "//Response[1]/externalMatch[1]/attestation[1]/sourceId[1]"),
            "externalMatch.attestation.sourceId")

    def test_xpath_indices_are_1_based_and_jsonpath_is_0_based(self):
        self.assertEqual(
            rc._xpath_to_dotted("//Response/items[1]/e[3]/id"), "items[2].id")

    def test_the_xml_wrapper_root_is_dropped(self):
        self.assertEqual(rc._xpath_to_dotted("//Response[1]/groupId[1]"),
                         "groupId")
        self.assertEqual(rc._xpath_to_dotted("//Envelope/Body/result[1]/id"),
                         "result.id")

    def test_a_jdbc_shaped_path_is_left_alone_not_indexed(self):
        """A `jdbc` step's ref never reaches this code -- publishJdbcRow
        handles it under the leaf key -- but if one ever did, inventing
        array indices for Results/ResultSet/Row would be wrong."""
        self.assertEqual(
            rc._xpath_to_dotted("//Results[1]/ResultSet[1]/Row[1]/TBL.COL[1]"),
            "Results.ResultSet.Row.TBL.COL")

    def test_anything_that_is_not_a_plain_path_yields_nothing(self):
        """So the caller keeps its existing leaf behaviour rather than
        extracting from a path this did not understand."""
        for expr in ("count(//x)", "//a[@id='1']/b", "", "//a/text()"):
            self.assertEqual(rc._xpath_to_dotted(expr), "", expr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
