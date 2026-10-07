"""A cell corrected in `tools/ra_converter/csv_overrides/` survives a reconvert.

The generated row files are rewritten by every convert. `csv_overlay.py`
merges tracked override files into them at write time; `Emitter.
_apply_csv_overlay` is the single hook every per-method CSV passes
through. These tests pin the merge rule (match by `test_case_id` plus any
`key:` columns, empty cell = no override, unknown column is an error,
unmatched row is a warning, everything else byte-identical) and, when the
tree holds CSVs this converter wrote, that the seeded overrides landed.

    python -B tools/ra_converter/test_csv_overlay.py
"""

import csv
import glob
import inspect
import io
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)

import csv_overlay  # noqa: E402
import ra_converter as rc  # noqa: E402

COLS = ["description", "test_case_id", "execute", "DataSource_peakRooms",
        "qry_GET_arrivalDate", "expected_Shop_status_code"]
ROWS = [
    ["first, with comma", "Scenario 1", "", "1", "2026-11-01", "400"],
    ["second", "Scenario 1", "", "5", "2026-11-01", "400"],
    ["third", "Scenario 2", "", "7", "2026-11-01", "400"],
    ["fourth", "Scenario 3", "", "9", "2026-11-01", "200"],
]


def _merge(overlay_header, overlay_rows, cols=COLS, rows=ROWS):
    key_cols = [csv_overlay.ID_COL] + [
        h[len(csv_overlay.KEY_PREFIX):] for h in overlay_header
        if h.startswith(csv_overlay.KEY_PREFIX)]
    override_cols = [h for h in overlay_header
                     if h != csv_overlay.ID_COL
                     and not h.startswith(csv_overlay.KEY_PREFIX)]
    orows = [dict(zip(overlay_header, r)) for r in overlay_rows]
    return csv_overlay.merge_overlay(cols, rows, orows, key_cols,
                                     override_cols, overlay_name="t.csv")


def test_match_by_test_case_id_overrides_every_matching_row():
    res = _merge(["test_case_id", "expected_Shop_status_code"],
                 [["Scenario 1", "200"]])
    assert [r[5] for r in res.rows] == ["200", "200", "400", "200"]
    assert sorted(a.row for a in res.applied) == [0, 1]
    assert res.unmatched == []


def test_an_extra_key_column_narrows_the_match():
    res = _merge(["test_case_id", "key:DataSource_peakRooms",
                  "expected_Shop_status_code"],
                 [["Scenario 1", "5", "200"]])
    assert [r[5] for r in res.rows] == ["400", "200", "400", "200"]
    assert [(a.row, a.column, a.old, a.new) for a in res.applied] == [
        (1, "expected_Shop_status_code", "400", "200")]


def test_an_empty_overlay_cell_is_no_override():
    res = _merge(["test_case_id", "qry_GET_arrivalDate",
                  "expected_Shop_status_code"],
                 [["Scenario 2", "", "201"]])
    assert res.rows[2][4] == "2026-11-01"
    assert res.rows[2][5] == "201"
    assert len(res.applied) == 1


def test_an_unknown_override_column_is_a_loud_error_naming_file_and_column():
    try:
        _merge(["test_case_id", "expected_Shop_status_cod"],
               [["Scenario 1", "200"]])
    except csv_overlay.OverlayError as e:
        msg = str(e)
        assert "t.csv" in msg and "`expected_Shop_status_cod`" in msg, msg
    else:
        raise AssertionError("a typo in an override column passed silently")


def test_an_unknown_key_column_is_a_loud_error_too():
    try:
        _merge(["test_case_id", "key:DataSource_peekRooms",
                "expected_Shop_status_code"], [["Scenario 1", "1", "200"]])
    except csv_overlay.OverlayError as e:
        assert "`DataSource_peekRooms`" in str(e), str(e)
    else:
        raise AssertionError("a typo in a key column passed silently")


def test_an_unmatched_overlay_row_warns_and_changes_nothing():
    res = _merge(["test_case_id", "expected_Shop_status_code"],
                 [["Scenario 9", "200"]])
    assert res.rows == ROWS
    assert res.applied == []
    assert [u["test_case_id"] for u in res.unmatched] == ["Scenario 9"]


def test_order_and_untouched_cells_are_identical_and_input_is_not_mutated():
    before = [list(r) for r in ROWS]
    res = _merge(["test_case_id", "expected_Shop_status_code"],
                 [["Scenario 3", "204"], ["Scenario 1", "200"]])
    assert ROWS == before, "merge mutated its input"
    assert [r[1] for r in res.rows] == [r[1] for r in ROWS], "row order moved"
    for i, (got, was) in enumerate(zip(res.rows, ROWS)):
        for ci in range(len(COLS)):
            if ci == 5:
                continue
            assert got[ci] == was[ci], (i, ci)
    assert [r[5] for r in res.rows] == ["200", "200", "400", "204"]


def test_later_overlay_rows_win_on_the_same_cell():
    res = _merge(["test_case_id", "expected_Shop_status_code"],
                 [["Scenario 1", "200"], ["Scenario 1", "202"]])
    assert [r[5] for r in res.rows][:2] == ["202", "202"]


def test_read_overlay_requires_test_case_id_and_tolerates_a_bom():
    d = tempfile.mkdtemp()
    try:
        bad = os.path.join(d, "bad.csv")
        with io.open(bad, "w", encoding="utf-8", newline="") as fh:
            fh.write("case,expected_Shop_status_code" + chr(10) + "x,1" + chr(10))
        try:
            csv_overlay.read_overlay(bad, display_name="bad.csv")
        except csv_overlay.OverlayError as e:
            assert "bad.csv" in str(e) and "test_case_id" in str(e)
        else:
            raise AssertionError("an overlay without test_case_id was accepted")
        good = os.path.join(d, "good.csv")
        with io.open(good, "w", encoding="utf-8-sig", newline="") as fh:
            fh.write("test_case_id,key:DataSource_peakRooms,expected_Shop_status_code"
                     + chr(10) + "Scenario 1,1,200" + chr(10) + chr(10))
        key_cols, override_cols, rows = csv_overlay.read_overlay(good)
        assert key_cols == ["test_case_id", "DataSource_peakRooms"]
        assert override_cols == ["expected_Shop_status_code"]
        assert rows == [{"test_case_id": "Scenario 1",
                         "key:DataSource_peakRooms": "1",
                         "expected_Shop_status_code": "200"}]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_overlay_path_mirrors_the_generated_path():
    rel = "src/test/resources/csv/suite/grp/KlassTest/method.csv"
    assert csv_overlay.overlay_rel_for(rel) == "suite/grp/KlassTest/method.csv"
    p = csv_overlay.overlay_path_for(rel, root="R")
    assert p.replace(os.sep, "/") == "R/suite/grp/KlassTest/method.csv"
    assert csv_overlay.overlay_path_for("src/main/resources/x.csv") is None


# ---- the converter hook -------------------------------------------------

def _emitter():
    e = rc.Emitter.__new__(rc.Emitter)
    e.suite_name = "suite"
    e.ledger = rc.AuditLedger()
    e.output_dir = tempfile.mkdtemp()
    return e


def _joined(rows):
    return [",".join(rc._csv_quote(c) for c in r) for r in rows]


def test_emitter_hook_rewrites_only_the_rows_it_changed_and_logs_findings():
    root = tempfile.mkdtemp()
    try:
        rel = "src/test/resources/csv/suite/grp/KlassTest/method.csv"
        ovl = csv_overlay.overlay_path_for(rel, root)
        os.makedirs(os.path.dirname(ovl))
        with io.open(ovl, "w", encoding="utf-8", newline="") as fh:
            fh.write("test_case_id,key:DataSource_peakRooms,expected_Shop_status_code"
                     + chr(10) + "Scenario 1,1,200" + chr(10)
                     + "Scenario 9,1,200" + chr(10))
        e = _emitter()
        rows = _joined(ROWS)
        out = e._apply_csv_overlay(rel, COLS, rows, overlay_root=root)
        assert out[1:] == rows[1:], "rows without an override were rewritten"
        got = next(csv.reader([out[0]]))
        assert got == ["first, with comma", "Scenario 1", "", "1",
                       "2026-11-01", "200"], got
        assert out[0].startswith('"first, with comma",'), out[0]
        pre = getattr(e.ledger, "preflight", [])
        applied = [p for p in pre if p[1] == "csv-overlay-applied"]
        unmatched = [p for p in pre if p[1] == "csv-overlay-unmatched"]
        assert len(applied) == 1 and applied[0][0] == "INFO", pre
        assert "old='400' new='200'" in applied[0][3], applied[0][3]
        assert "expected_Shop_status_code" in applied[0][3]
        assert len(unmatched) == 1 and unmatched[0][2] == "Scenario 9", pre
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_emitter_hook_is_inert_without_an_overlay_file():
    root = tempfile.mkdtemp()
    try:
        e = _emitter()
        rows = _joined(ROWS)
        rel = "src/test/resources/csv/suite/grp/KlassTest/none.csv"
        assert e._apply_csv_overlay(rel, COLS, rows, overlay_root=root) == rows
        assert not getattr(e.ledger, "preflight", [])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_emitter_hook_raises_on_an_unknown_column():
    root = tempfile.mkdtemp()
    try:
        rel = "src/test/resources/csv/suite/grp/KlassTest/method.csv"
        ovl = csv_overlay.overlay_path_for(rel, root)
        os.makedirs(os.path.dirname(ovl))
        with io.open(ovl, "w", encoding="utf-8", newline="") as fh:
            fh.write("test_case_id,expected_Shop_status_cod" + chr(10)
                     + "Scenario 1,200" + chr(10))
        e = _emitter()
        try:
            e._apply_csv_overlay(rel, COLS, _joined(ROWS), overlay_root=root)
        except csv_overlay.OverlayError as err:
            assert "KlassTest/method.csv" in str(err)
            assert "`expected_Shop_status_cod`" in str(err)
        else:
            raise AssertionError("unknown overlay column did not raise")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_emit_csv_per_method_applies_the_overlay_after_the_rows_are_complete():
    src = inspect.getsource(rc.Emitter.emit_csv_per_method)
    i_expand = src.index("self._expand_rows_for_datasource(cols, rows, cluster)")
    i_overlay = src.index("self._apply_csv_overlay(rel, cols, rows)")
    i_write = src.index("self._write(rel, content)")
    assert i_expand < i_overlay < i_write, (i_expand, i_overlay, i_write)


# ---- the real tree ------------------------------------------------------

def _generated_for(overlay_path):
    tail = os.path.relpath(overlay_path, csv_overlay.OVERLAY_ROOT)
    return os.path.join(ROOT, "src", "test", "resources", "csv", tail)


def _read(path):
    try:
        with io.open(path, encoding="utf-8-sig", newline="") as fh:
            return list(csv.reader(fh))
    except OSError:
        return None                        # Windows MAX_PATH: not evidence


def test_every_seeded_overlay_is_fully_applied_in_a_fresh_tree():
    """For every overlay file whose generated counterpart this converter
    wrote: re-merging the overlay into the written file changes nothing
    (every override already landed) and matches every overlay row."""
    converter_mtime = os.path.getmtime(os.path.join(HERE, "ra_converter.py"))
    checked = 0
    for ovl in glob.glob(os.path.join(csv_overlay.OVERLAY_ROOT, "**", "*.csv"),
                         recursive=True):
        gen = _generated_for(ovl)
        try:
            if not os.path.exists(gen) or os.path.getmtime(gen) < converter_mtime:
                continue
        except OSError:
            continue
        table = _read(gen)
        if not table:
            continue
        key_cols, override_cols, orows = csv_overlay.read_overlay(ovl)
        res = csv_overlay.merge_overlay(table[0], table[1:], orows, key_cols,
                                        override_cols, overlay_name=ovl)
        assert res.applied == [], (gen, res.applied[:5])
        assert res.unmatched == [], (gen, res.unmatched[:5])
        checked += 1
    print("    (%d fresh generated file(s) checked)" % checked)


def _fresh_rows(rel_tail):
    gen = os.path.join(ROOT, "src", "test", "resources", "csv", *rel_tail.split("/"))
    try:
        if not os.path.exists(gen):
            return None
        if os.path.getmtime(gen) < os.path.getmtime(os.path.join(HERE, "ra_converter.py")):
            return None
    except OSError:
        return None
    return _read(gen)


def test_scenario1_fresh_cells_follow_peakrooms():
    table = _fresh_rows("partialgoalregression/groups/GetGroupsTest/"
                        "scenario1PeakRooms5SingleAndTest.csv")
    if not table:
        return                             # older converter wrote it, or absent
    h = table[0]
    i_pr = h.index("DataSource_peakRooms")
    i_s = h.index("expected_GOAL_Single_Shop_status_code")
    i_m = h.index("expected_GOAL_Multi_Prop_Shop_status_code")
    assert len(table) > 1
    for r in table[1:]:
        want = "400" if int(r[i_pr]) < 5 else "200"
        assert (r[i_s], r[i_m]) == (want, want), (r[i_pr], r[i_s], r[i_m])


def test_goal10_fresh_dates_follow_the_availability_search():
    table = _fresh_rows("partialgoalregression/groups/CreateGroupsTest/"
                        "goal10PostRateplancreateTest.csv")
    if not table:
        return
    h = table[0]
    i_a = h.index("qry_GET_Groups_SingleProp_arrivalDate")
    i_d = h.index("qry_GET_Groups_SingleProp_departureDate")
    assert len(table) > 1
    for r in table[1:]:
        assert r[i_a] == "#generatedDatesAndProps_arrivalDate#", r[i_a]
        assert r[i_d] == "#generatedDatesAndProps_departureDate#", r[i_d]


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok  " + name)
            passed += 1
    print("%d passed" % passed)
