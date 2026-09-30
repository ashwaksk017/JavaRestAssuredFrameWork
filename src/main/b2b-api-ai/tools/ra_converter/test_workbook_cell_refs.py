"""A workbook cell can hold a ReadyAPI reference instead of a value.

These sheets are parameterised the same way a request body is:
`${generatedDatesAndProps#arrivalDate}` sits in the arrivalDate column and
ReadyAPI resolves it when something reads the DataSource. The importer used
to copy that expression into the CSV verbatim, and nothing downstream spoke
ReadyAPI syntax, so the same cell died three different ways depending on
which layer touched it first:

  * on the wire as its own text,
  * as an empty query parameter (`Ref.resolveArg` blanks any value still
    carrying a `#` rather than send it),
  * as the four characters `null` in a JSON body, once `RestUtilities` read
    that blank as unresolved and applied its fallback.

The third is the one that cost real runs: the server answered
`{"fields":["startDate"],"message":"Invalid JSON Parameter Value"}`, naming
the field and never the value, so the digest could only report that
something about startDate was wrong.

What made it survive so long is that it is invisible in a sheet that holds
literals. `peakRooms` was `12` and resolved; `arrivalDate` beside it was a
reference and did not.
"""

import ra_converter


T = ra_converter._translate_workbook_cell


def test_a_plain_value_is_returned_untouched():
    """Ordinary data must not round-trip through a translator."""
    for plain in ("2026-10-31", "AAAAA", "12", "", "  ", "SMRF",
                  "a description, with a comma", "1.5", "true"):
        assert T(plain) == plain


def test_a_step_property_reference_becomes_a_placeholder():
    assert (T("${generatedDatesAndProps#arrivalDate}")
            == "#generatedDatesAndProps_arrivalDate#")


def test_a_dotted_property_is_flattened_like_every_other_reference():
    """The same spelling the XML path produces, so a reference means one
    thing wherever it is written."""
    assert T("${someStep#a.b}") == "#someStep_a_b#"


def test_the_translation_matches_the_xml_path_exactly():
    """If these two ever disagree, a value resolves in a body and not in a
    cell, which is worse than either being wrong on its own."""
    for expr in ("${generatedDates#arrivalDate}",
                 "${groupid#roomTypeCode}",
                 "${DataSource#propCode}"):
        from_xml, _ = ra_converter.soapui_body_to_placeholders(expr)
        assert T(expr) == from_xml


def test_a_reference_embedded_in_text_translates_only_the_reference():
    assert (T("prop-${generatedDates#hcrs}-suffix")
            == "prop-#generatedDates_hcrs#-suffix")


def test_several_references_in_one_cell_all_translate():
    got = T("${a#one}/${b#two}")
    assert got == "#a_one#/#b_two#"


def test_an_untranslatable_groovy_cell_is_left_raw():
    """`#groovy_expr#/*preview*/` is a note to a human reading generated
    source. Writing it into a data cell would ship the converter's own
    marker to the server -- the failure the external-secrets fix exists to
    stop -- so the raw expression is kept instead."""
    cell = "${= new Date().format('yyyy-MM-dd') }"
    assert T(cell) == cell
    assert "groovy_expr" not in T(cell)


def test_an_external_secret_reference_still_resolves_to_its_config_key():
    """The one `${=...}` shape that IS translatable stays translatable: it
    names a key in the secrets file, which is config, not code.

    It has to look like a properties-FILE read to qualify -- a bare
    `props.getProperty("x")` could be reading anything, and inventing a
    config key from it would be a guess."""
    cell = ('${= def p = new Properties(); '
            'p.load(new FileInputStream("env.properties")); '
            'p.getProperty("client_id") }')
    got = T(cell)
    assert got == "#client_id#", got


def test_a_bare_getproperty_cell_is_not_guessed_at():
    """Conservative on purpose: without the file read there is nothing to
    say the key names external config, so the cell is left alone rather
    than turned into a config lookup that resolves to nothing."""
    cell = '${= props.getProperty("client_id") }'
    assert T(cell) == cell
    assert "groovy_expr" not in T(cell)


def test_none_and_empty_are_safe():
    assert T("") == ""
    assert T(None) is None


def _workbook(tmp_path, rows):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "sheet1"
    for r in rows:
        ws.append(r)
    f = tmp_path / "wb.xlsx"
    wb.save(f)
    return str(f)


def test_end_to_end_a_sheet_of_mixed_cells(tmp_path):
    """The shape of the real workbook: a literal beside a reference. This is
    why the bug looked intermittent -- peakRooms resolved and the date in
    the next column did not."""
    path = _workbook(tmp_path, [
        ["propCode", "arrivalDate", "peakRooms"],
        ["${generatedDatesAndProps#hcrs}",
         "${generatedDatesAndProps#arrivalDate}", 12],
        ["BBBBB", "2026-10-31", 11],
    ])
    import os
    ds = ra_converter.DataSourceStep(
        step_name="DataSource", ds_type="Excel",
        columns=["propCode", "arrivalDate", "peakRooms"],
        file_path=os.path.basename(path), worksheet="sheet1",
        start_cell="A2", ignore_empty=True)
    rows, gap = ra_converter._read_datasource_rows(ds, [str(tmp_path)])

    assert gap == "", gap
    assert len(rows) == 2, rows
    assert rows[0]["propCode"] == "#generatedDatesAndProps_hcrs#"
    assert rows[0]["arrivalDate"] == "#generatedDatesAndProps_arrivalDate#"
    assert rows[0]["peakRooms"] == "12"          # literal, untouched
    # the row of plain values is not disturbed at all
    assert rows[1] == {"propCode": "BBBBB", "arrivalDate": "2026-10-31",
                       "peakRooms": "11"}


def test_an_untranslatable_cell_is_recorded_for_the_convert_report(tmp_path):
    """Silence is what made the original bug expensive. A cell the
    translator cannot render must cost one console line, not one run."""
    import os
    ra_converter._WORKBOOK_CELLS_UNTRANSLATED.clear()
    path = _workbook(tmp_path, [
        ["arrivalDate"],
        ["${= someUntranslatableGroovy() }"],
    ])
    ds = ra_converter.DataSourceStep(
        step_name="DataSource", ds_type="Excel", columns=["arrivalDate"],
        file_path=os.path.basename(path), worksheet="sheet1",
        start_cell="A2", ignore_empty=True)
    ra_converter._read_datasource_rows(ds, [str(tmp_path)])

    assert ra_converter._WORKBOOK_CELLS_UNTRANSLATED, \
        "an untranslated cell was not recorded"
    recorded = ra_converter._WORKBOOK_CELLS_UNTRANSLATED
    assert any("arrivalDate" in cols for cols in recorded.values()), recorded
    ra_converter._WORKBOOK_CELLS_UNTRANSLATED.clear()


def test_a_sheet_of_literals_records_nothing(tmp_path):
    import os
    ra_converter._WORKBOOK_CELLS_UNTRANSLATED.clear()
    path = _workbook(tmp_path, [["propCode"], ["BBBBB"], ["AAAAA"]])
    ds = ra_converter.DataSourceStep(
        step_name="DataSource", ds_type="Excel", columns=["propCode"],
        file_path=os.path.basename(path), worksheet="sheet1",
        start_cell="A2", ignore_empty=True)
    ra_converter._read_datasource_rows(ds, [str(tmp_path)])

    assert ra_converter._WORKBOOK_CELLS_UNTRANSLATED == {}
