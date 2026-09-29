"""A DataSource-expanded CSV row must survive commas and newlines.

The DataSource expansion turns one CSV row into one row per workbook
row, and it does that by post-processing the FINISHED row text: parse it
with csv.reader, swap the DataSource_ cells, re-join. csv.reader returns
UNQUOTED values, so the re-join has to put the quoting back -- and it
did not. It quoted only the cells it had just replaced.

Any other cell holding a comma or a newline therefore split the row. A
workbook's description column holds both, so on a project where all the
workbooks were present the row grew an extra field, every later column
shifted by one, and `test_case_id` came out holding a fragment of a
sentence:

    no phase table for case ` and meeting facilities confirmation
    \\nis returned if included in the request.` (wanted `readPropsGroups`)

That message points at the phase registry, which is not where the fault
is -- 11 of 22 failures in one run read that way, plus 3 more where a
shifted column made `expected_tokenRequest_status_code` read 400.

It stayed hidden here because the one workbook on this machine has no
commas in its description column. The fixture below has both a comma and
a newline, which is the shape that actually occurs.

    python tools/ra_converter/test_csv_quoting.py
"""
from __future__ import annotations

import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ra_converter as R  # noqa: E402

NL = chr(10)

# A real description column value: a comma AND a hard line break.
DESC = ("Confirms the group, and meeting facilities confirmation " + NL
        + "is returned if included in the request.")

COLS = ["description", "test_case_id", "DataSource_propCode"]


class _Case:
    ds_rows = [{"propCode": "AAAAA"}, {"propCode": "BBBBB"}]
    ds_driver_cols = ["propCode"]


def _expand(cols, rows, cluster):
    """The expansion is a method; call it unbound, it uses no state."""
    return R.Emitter._expand_rows_for_datasource(None, cols, rows, cluster)


def _row_text():
    return ",".join([R._csv_cell(DESC), "goal58_Post_confirm_200", "OLD"])


def test_the_source_row_is_quoted_to_begin_with():
    """_csv_cell already did its job -- the bug is downstream of it."""
    assert len(next(csv.reader([_row_text()]))) == len(COLS)


def test_expansion_keeps_every_row_at_the_header_width():
    out = _expand(COLS, [_row_text()], [_Case()])
    assert len(out) == 2, out
    for r in out:
        assert len(next(csv.reader([r]))) == len(COLS), (
            "row split into %d cells: %r"
            % (len(next(csv.reader([r]))), r))


def test_test_case_id_is_not_a_fragment_of_the_description():
    """The symptom, pinned directly."""
    for r in _expand(COLS, [_row_text()], [_Case()]):
        cells = next(csv.reader([r]))
        assert cells[1] == "goal58_Post_confirm_200", cells[1]
        assert not cells[1].startswith(" "), cells[1]


def test_the_datasource_cell_is_actually_replaced_per_row():
    got = [next(csv.reader([r]))[2]
           for r in _expand(COLS, [_row_text()], [_Case()])]
    assert got == ["AAAAA", "BBBBB"], got


def test_the_description_survives_intact():
    """Not merely 'the right number of cells' -- the same text."""
    for r in _expand(COLS, [_row_text()], [_Case()]):
        assert next(csv.reader([r]))[0] == DESC


def test_a_replaced_value_containing_a_comma_is_quoted_too():
    """The DataSource value itself can hold a comma.

    _csv_cell quotes it; the re-join must not then quote it a SECOND
    time, or the cell arrives at the test wrapped in stray quotes.
    """
    class C:
        ds_rows = [{"propCode": "AAA,BBB"}]
        ds_driver_cols = ["propCode"]

    out = _expand(COLS, [_row_text()], [C()])
    assert next(csv.reader([out[0]]))[2] == "AAA,BBB"


def test_a_row_with_no_workbook_is_left_alone():
    class C:
        ds_rows = []
        ds_driver_cols = []

    rows = [_row_text()]
    assert _expand(COLS, rows, [C()]) == rows


def test_csv_quote_does_not_transform_only_quotes():
    """_csv_cell rewrites values; _csv_quote must not.

    Re-joining runs over cells that _csv_cell already processed, so a
    second semantic pass would rewrite an id twice or empty a literal
    that was meant to survive.
    """
    assert R._csv_quote("null") == "null"
    assert R._csv_quote("2000016128") == "2000016128"
    assert R._csv_quote("${Properties#x}") == "${Properties#x}"
    assert R._csv_quote("a,b") == '"a,b"'
    assert R._csv_quote('say "hi"') == '"say ""hi"""'
    assert R._csv_quote(None) == ""


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"ok  {fn.__name__}")
        except Exception as ex:
            failed += 1
            print(f"FAIL {fn.__name__}: {ex}")
    if failed:
        sys.exit(1)
    print(f"{len(tests)} passed")
