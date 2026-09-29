"""A spreadsheet cell must arrive as the text ReadyAPI would have sent.

openpyxl returns TYPED values, and str() on them is not what the sheet
shows. A date column comes back as a datetime, so str() produced

    2026-12-17 00:00:00

a Python repr, with a space in it. That went straight into a JSON body
and a query string, and the server refused it as MALFORMED rather than
out of range -- a different error code from a stale date, which is what
made it findable:

    "startDate":  "2026-12-17 00:00:00"  -> code 31 Invalid JSON Parameter
    "cutOffDate": "2026-12-17 00:00:00"  -> code 31 Invalid JSON Parameter
    arrivalDate=2026-12-17 00:00:00      -> code 33 Invalid Query Parameter

Five failures across three date fields in one run.

It could not be seen on the machine that wrote the converter: the single
workbook available there stores those columns as ${...} reference
STRINGS, so no cell in it is typed as a date. It needed the full set of
workbooks from the project that owns them.

Excel has no date-only type -- a plain date is a datetime at midnight --
so rendering midnight as a date is the right call for a date column. The
trade is a genuine midnight timestamp losing its zeroed time, which is
rare in test data and still yields a valid prefix of what was meant.

    python tools/ra_converter/test_excel_cell_text.py
"""
from __future__ import annotations

import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ra_converter as R  # noqa: E402


def test_a_date_column_renders_as_a_date():
    """THE bug. Excel stores a plain date as midnight."""
    assert R._excel_cell_text(datetime.datetime(2026, 12, 17)) == "2026-12-17"
    assert R._excel_cell_text(datetime.date(2026, 12, 18)) == "2026-12-18"


def test_a_real_timestamp_keeps_its_time_in_ISO_form():
    """A space is what the server rejected; ISO-T is unambiguous."""
    got = R._excel_cell_text(datetime.datetime(2026, 12, 17, 14, 30, 5))
    assert got == "2026-12-17T14:30:05", got
    assert " " not in got


def test_microseconds_are_dropped():
    got = R._excel_cell_text(datetime.datetime(2026, 12, 17, 14, 30, 5, 123456))
    assert got == "2026-12-17T14:30:05", got


def test_a_whole_float_renders_as_an_integer():
    """Every Excel numeric cell is a float.

    peakRooms 12 arriving as "12.0" fails an integer field just as surely
    as a malformed date fails a date one.
    """
    assert R._excel_cell_text(12.0) == "12"
    assert R._excel_cell_text(12.5) == "12.5"
    assert R._excel_cell_text(0.0) == "0"


def test_a_boolean_is_json_shaped_not_python_shaped():
    """`True` would be sent literally, and JSON has no `True`."""
    assert R._excel_cell_text(True) == "true"
    assert R._excel_cell_text(False) == "false"


def test_a_time_and_a_duration_render_readably():
    assert R._excel_cell_text(datetime.time(9, 5)) == "09:05:00"
    assert R._excel_cell_text(datetime.timedelta(hours=1, minutes=2,
                                                 seconds=3)) == "01:02:03"


def test_text_and_empty_cells_are_untouched():
    assert R._excel_cell_text("ABCDE") == "ABCDE"
    assert R._excel_cell_text("${generatedDatesAndProps#hcrs}") \
        == "${generatedDatesAndProps#hcrs}"
    assert R._excel_cell_text(None) == ""
    assert R._excel_cell_text("") == ""


def test_end_to_end_through_the_workbook_reader():
    """Not just the formatter -- the reader that calls it."""
    try:
        import openpyxl
    except ImportError:
        return                      # reader already reports this itself
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "S1"
        ws.append(["arrivalDate", "peakRooms", "propCode"])
        ws.append([datetime.datetime(2026, 12, 17), 12.0, "  ABCDE  "])
        path = os.path.join(tmp, "probe.xlsx")
        wb.save(path)

        class _DS:
            file_path = "probe.xlsx"
            worksheet = "S1"
            start_cell = "A2"
            ignore_empty = True
            step_name = "DataSource"

        rows, gap = R._read_datasource_rows(_DS(), [tmp])
        assert not gap, gap
        assert rows[0] == {"arrivalDate": "2026-12-17",
                           "peakRooms": "12",
                           "propCode": "ABCDE"}, rows[0]


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
