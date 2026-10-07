# csv_overrides -- durable cell overrides for generated row files

Every convert rewrites `src/test/resources/csv/**/*.csv` from the ReadyAPI
XML, so a cell you correct by hand is gone on the next run. Put the
correction here instead. Files in this directory are tracked in git and
applied on every convert, to every suite, with no per-suite code.

## Where a file goes

Mirror the generated path, minus the `src/test/resources/csv/` prefix:

    generated: src/test/resources/csv/<suite>/<group>/<TestClass>/<method>.csv
    overlay:   tools/ra_converter/csv_overrides/<suite>/<group>/<TestClass>/<method>.csv

## File format

A normal CSV. The header decides what each column does:

| header                  | role                                                            |
|-------------------------|-----------------------------------------------------------------|
| `test_case_id`          | REQUIRED match column -- exact match on the generated cell       |
| `key:<generated column>`| extra match column; the row matches only if this cell is equal too |
| any other name          | override column; must be a column of the generated file          |

Rules:

* A generated row matches an overlay row when `test_case_id` and every
  `key:` cell are exactly equal. Every matching row gets the override cells.
* An empty override cell means "leave that column alone on this row".
* An override column that the generated file does not have is an ERROR
  (the convert stops and names the file and the column). A typo must not
  silently do nothing.
* An overlay row that matches no generated row is a WARN on the console and
  a `csv-overlay-unmatched` finding in `_audit/<suite>/preflight.md`.
* Every applied cell is a `csv-overlay-applied` INFO finding (old and new
  value) and the convert prints one line per file:
  `[csv-overlay] <generated path>: N cell(s) in M row(s) from <overlay>`.
* Row order, the header and every other cell are written exactly as the
  converter built them.
* `key:DataSource_*` columns exist only when the workbook was found
  (`--data-dir`), so an overlay keyed on one needs that flag.

## Seeded examples

`partialgoalregression/groups/GetGroupsTest/scenario1PeakRooms5SingleAndTest.csv`
keys on the workbook value so rows of one case can differ:

    test_case_id,key:DataSource_peakRooms,expected_GOAL_Single_Shop_status_code,expected_GOAL_Multi_Prop_Shop_status_code
    Scenario 1 - peakRooms < 5_SingleAndMultiProduct,1,400,400
    ...
    Scenario 2 - peakRooms_between5And9_SingleAndMultiProduct,5,200,200

`partialgoalregression/groups/CreateGroupsTest/goal10PostRateplancreateTest.csv`
replaces two query literals from the XML with references the runtime
resolves through the merged row (ctx wins), on every row of the case:

    test_case_id,qry_GET_Groups_SingleProp_arrivalDate,qry_GET_Groups_SingleProp_departureDate
    goal10_Post_rateplancreate_400,#generatedDatesAndProps_arrivalDate#,#generatedDatesAndProps_departureDate#

The status codes in the seeded files are OBSERVED server behaviour at the
time they were written (minimum 5 peakRooms on both shop endpoints; LONME
serving 200 for 26-40), not something the ReadyAPI author asserted.

Tests: `python -B tools/ra_converter/test_csv_overlay.py`.
