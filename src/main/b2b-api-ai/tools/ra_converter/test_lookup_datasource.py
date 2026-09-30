"""A DataSource no loop drives is a lookup, and its row has to be read.

`goal09_Get_shop_200_emptyResponse` holds two DataSources: the loop's
driver (goal09.xlsx) and a shared sheet of properties and dates
(datesAndProperties.xlsx). Only the driver was ever imported, so
`${PropertiesAndDates#arrDateValue}` had no producer, the shop query went
out as `arrivalDate=` and the server answered "Invalid Query Parameter
Value".

Reading the lookup as ROWS would be worse than not reading it -- the
suite would be multiplied by the lookup sheet's length. ReadyAPI runs a
DataSource that no loop drives exactly once, so its properties hold ONE
row: constants for the case.

Six cases in the imported goal suite carry this shape.
"""

import os

import ra_converter


def _wb(tmp_path, name, rows):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "sheet1"
    for r in rows:
        ws.append(r)
    f = tmp_path / name
    wb.save(f)
    return str(f)


def _ds(name, filename):
    return ra_converter.DataSourceStep(
        step_name=name, ds_type="Excel", columns=[],
        file_path=filename, worksheet="sheet1", start_cell="A2",
        ignore_empty=True)


def _case(tmp_path, name="c"):
    driver = _ds("DataSource", "driver.xlsx")
    lookup = _ds("PropertiesAndDates", "lookup.xlsx")
    c = ra_converter.TestCase(id="1", name=name, description="")
    c.steps = [lookup, driver]
    c.ds_loops = [{"loop": "Loop", "source": "DataSource", "target": ""}]
    return c, driver, lookup


def test_the_lookup_row_is_read_and_the_driver_still_drives(tmp_path):
    _wb(tmp_path, "driver.xlsx", [["propCode"], ["AAAAA"], ["BBBBB"]])
    _wb(tmp_path, "lookup.xlsx",
        [["arrDateValue", "depDateValue"], ["2026-10-31", "2026-11-01"]])
    c, _driver, _lookup = _case(tmp_path)

    ra_converter._import_datasource_rows([c], str(tmp_path / "x.xml"),
                                         str(tmp_path))

    # the driver still decides how many rows the case runs
    assert len(c.ds_rows) == 2, c.ds_rows
    # and the lookup contributes constants, not iterations
    assert c.ds_lookup["PropertiesAndDates"]["arrDateValue"] == "2026-10-31"
    assert c.ds_lookup["PropertiesAndDates"]["depDateValue"] == "2026-11-01"


def test_a_lookup_never_multiplies_the_suite(tmp_path):
    """The reason the importer ignored these in the first place. A lookup
    sheet with four property rows must not turn 2 cases into 8."""
    _wb(tmp_path, "driver.xlsx", [["propCode"], ["AAAAA"], ["BBBBB"]])
    _wb(tmp_path, "lookup.xlsx", [["hcrs"], ["A"], ["B"], ["C"], ["D"]])
    c, _d, _l = _case(tmp_path)

    ra_converter._import_datasource_rows([c], str(tmp_path / "x.xml"),
                                         str(tmp_path))

    assert len(c.ds_rows) == 2, "the lookup must not add rows"
    assert c.ds_lookup["PropertiesAndDates"]["hcrs"] == "A", "row 1 only"


def test_a_case_with_only_a_driver_is_unchanged(tmp_path):
    _wb(tmp_path, "driver.xlsx", [["propCode"], ["AAAAA"]])
    c = ra_converter.TestCase(id="1", name="c", description="")
    c.steps = [_ds("DataSource", "driver.xlsx")]
    c.ds_loops = [{"loop": "Loop", "source": "DataSource", "target": ""}]

    ra_converter._import_datasource_rows([c], str(tmp_path / "x.xml"),
                                         str(tmp_path))

    assert len(c.ds_rows) == 1
    assert c.ds_lookup == {}


def test_a_missing_lookup_workbook_is_not_fatal(tmp_path):
    """The driver's data must still import when the lookup sheet is absent
    -- which is every tree that has some workbooks but not all."""
    _wb(tmp_path, "driver.xlsx", [["propCode"], ["AAAAA"]])
    c, _d, _l = _case(tmp_path)

    ra_converter._import_datasource_rows([c], str(tmp_path / "x.xml"),
                                         str(tmp_path))

    assert len(c.ds_rows) == 1
    assert c.ds_lookup == {}


class _Emitter:
    """Just enough object to exercise the row filler."""
    _fill_lookup_datasource_cells = (
        ra_converter.SuiteEmitter._fill_lookup_datasource_cells
        if hasattr(ra_converter, "SuiteEmitter") else None)


def _fill(cols, rows, cluster):
    # bound-method call without constructing the whole emitter
    fn = None
    for attr in dir(ra_converter):
        obj = getattr(ra_converter, attr)
        if isinstance(obj, type) and hasattr(obj, "_fill_lookup_datasource_cells"):
            fn = obj._fill_lookup_datasource_cells
            break
    assert fn is not None, "filler not found on any emitter class"
    return fn(None, cols, rows, cluster)


def test_an_empty_cell_is_filled_from_the_lookup():
    case = ra_converter.TestCase(id="1", name="c", description="")
    case.ds_lookup = {"PropertiesAndDates": {"arrDateValue": "2026-10-31"}}
    cols = ["test_case_id", "PropertiesAndDates_arrDateValue", "other"]

    out = _fill(cols, ["t1,,x"], [case])

    assert out[0].split(",")[1] == "2026-10-31", out


def test_a_cell_that_already_has_a_value_is_left_alone():
    """A more specific rule put it there and outranks a shared sheet."""
    case = ra_converter.TestCase(id="1", name="c", description="")
    case.ds_lookup = {"PropertiesAndDates": {"arrDateValue": "2026-10-31"}}
    cols = ["test_case_id", "PropertiesAndDates_arrDateValue"]

    out = _fill(cols, ["t1,ALREADY"], [case])

    assert out[0].split(",")[1] == "ALREADY", out


def test_a_column_nothing_references_is_not_invented():
    case = ra_converter.TestCase(id="1", name="c", description="")
    case.ds_lookup = {"PropertiesAndDates": {"unused": "v"}}
    cols = ["test_case_id"]

    assert _fill(cols, ["t1"], [case]) == ["t1"]


def test_a_case_with_no_lookup_is_returned_untouched():
    case = ra_converter.TestCase(id="1", name="c", description="")
    assert _fill(["a"], ["x"], [case]) == ["x"]


def test_a_value_with_a_comma_is_quoted():
    case = ra_converter.TestCase(id="1", name="c", description="")
    case.ds_lookup = {"S": {"f": "a,b"}}
    cols = ["id", "S_f"]

    out = _fill(cols, ["t1,"], [case])

    import csv, io
    assert next(csv.reader(io.StringIO(out[0])))[1] == "a,b", out
