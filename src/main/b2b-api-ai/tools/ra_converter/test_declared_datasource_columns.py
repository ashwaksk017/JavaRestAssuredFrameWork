"""A property a DataSource DECLARES has to get a CSV column.

`DataSourceStep.columns` was parsed at import and then only counted in
an audit line -- nothing turned it into columns. So an emitted row could
reference a column that does not exist. `goal494_Post_rateplan_400`
declares 13 properties and got 4, while its request body asks for
`#DataSource_inventoryCount#`: with no cell behind it the placeholder
went out as literal text, and `fill_datasource_csv.py` could not help
because there was nowhere to write the value.

Measured over the partialgoalregression CSVs: 182 references to a
DataSource column that did not exist, across 7 distinct names, dropping
to 7 across 3 after this. File count and row count are unchanged (28
CSVs, 318 rows), because this only widens the header.

The safety property these lock is that columns are APPENDED and never
reordered or removed -- `_expand_rows_for_datasource` maps workbook
headers onto CSV cells BY INDEX, so moving an existing column would put
data in the wrong cell.

    python tools/ra_converter/test_declared_datasource_columns.py
"""

import csv
import io

import ra_converter


def _add(cols, rows, cluster):
    # bound-method call without constructing the whole emitter
    fn = None
    for attr in dir(ra_converter):
        obj = getattr(ra_converter, attr)
        if isinstance(obj, type) and hasattr(
                obj, "_add_declared_datasource_columns"):
            fn = obj._add_declared_datasource_columns
            break
    assert fn is not None, "column adder not found on any emitter class"
    return fn(None, cols, rows, cluster)


def _case(step_name, props):
    c = ra_converter.TestCase(id="1", name="c", description="")
    ds = ra_converter.DataSourceStep(step_name=step_name, ds_type="Excel")
    ds.columns = list(props)
    c.steps = [ds]
    return c


def test_a_declared_property_gets_a_column():
    cols, rows = _add(["test_case_id"], ["t1"],
                      [_case("DataSource", ["inventoryCount"])])
    assert "DataSource_inventoryCount" in cols
    # the row grew by exactly one empty cell, so header and row still agree
    assert len(next(csv.reader(io.StringIO(rows[0])))) == len(cols)


def test_existing_columns_keep_their_index():
    """_expand_rows_for_datasource maps by INDEX, so order is load-bearing."""
    before = ["test_case_id", "DataSource_propCode", "expected"]
    cols, _ = _add(list(before), ["a,b,c"],
                   [_case("DataSource", ["inventoryCount"])])
    assert cols[:len(before)] == before, cols


def test_a_property_that_already_has_a_column_is_not_duplicated():
    cols, rows = _add(["DataSource_propCode"], ["HNLES"],
                      [_case("DataSource", ["propCode"])])
    assert cols == ["DataSource_propCode"], cols
    assert rows == ["HNLES"], "an unchanged row must not be padded"


def test_the_step_name_prefixes_the_column():
    """Two DataSource steps in one case do not share a namespace."""
    cols, _ = _add(["id"], ["x"], [_case("datasource_400", ["peakRoom"])])
    assert "datasource_400_peakRoom" in cols, cols


def test_two_steps_each_contribute_their_own_properties():
    c = ra_converter.TestCase(id="1", name="c", description="")
    a = ra_converter.DataSourceStep(step_name="DataSource", ds_type="Excel")
    a.columns = ["propCode"]
    b = ra_converter.DataSourceStep(step_name="PropertiesAndDates",
                                    ds_type="Excel")
    b.columns = ["arrDateValue"]
    c.steps = [a, b]
    cols, rows = _add(["id"], ["x"], [c])
    assert "DataSource_propCode" in cols
    assert "PropertiesAndDates_arrDateValue" in cols
    assert len(next(csv.reader(io.StringIO(rows[0])))) == len(cols)


def test_a_case_with_no_datasource_is_untouched():
    c = ra_converter.TestCase(id="1", name="c", description="")
    assert _add(["id"], ["x"], [c]) == (["id"], ["x"])


def test_a_blank_property_name_is_not_a_column():
    cols, _ = _add(["id"], ["x"], [_case("DataSource", ["", "  "])])
    assert cols == ["id"], cols


def test_every_row_is_padded_not_just_the_first():
    """A ragged row is worse than a missing column: csv would misalign."""
    cols, rows = _add(["id"], ["a", "b", "c"],
                      [_case("DataSource", ["x", "y"])])
    for r in rows:
        assert len(next(csv.reader(io.StringIO(r)))) == len(cols), r


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok  " + name)
            passed += 1
    print("%d passed" % passed)
