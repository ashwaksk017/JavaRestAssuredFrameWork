"""Fill the DataSource columns the converter leaves empty, from the workbooks.

    python tools/fill_datasource_csv.py --workbooks "C:/path/to/test-report" --dry-run
    python tools/fill_datasource_csv.py --workbooks "C:/path/to/test-report"

WHY THESE COLUMNS ARE EMPTY
---------------------------
A ReadyAPI DataSource step reads an Excel workbook at run time. This
framework does not run external DataSources -- the values have to live
in the generated CSV row -- so the converter emits the COLUMN and leaves
the cell blank. One full run showed what that costs: 1,056 unresolved
placeholders, 137 requests sent as `/props//groups` because a path
parameter resolved empty, and 246 assertions skipped because the value
they compare against was still `#DataSource_propCode#`.

The data is not missing. It is in the workbooks the XML names, and the
XML says exactly which sheet and which columns:

    <file>${projectDir}/.../goal09.xlsx</file>
    <worksheet>200_success</worksheet><cell>A2</cell>
    <con:property>propCode</con:property> ...

So this reads that declaration, opens the sheet, and writes the values
into the CSV column the emitter named: `<DataSource step name>_<property>`.

WHAT IT WILL NOT DO
-------------------
It does not invent a value. A column whose workbook, sheet or header is
missing is reported and left blank -- an invented propCode produces a
test that passes against the wrong property, which is worse than the
404 it replaces.

It does not overwrite a cell that already has a value, unless --force.
Someone may have filled a row by hand, and that hand-written row is the
one that has been verified.
"""
from __future__ import annotations

import argparse
import csv
import html
import io
import os
import re
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
INPUT_DIR = os.path.join(ROOT, "tools", "ra_converter", "input")
AUDIT_DIR = os.path.join(ROOT, "_audit")

# <con:testStep type="datasource" name="X"> ... <con:config ...>
_STEP = re.compile(
    r'<con:testStep[^>]*type="datasource"[^>]*name="(?P<name>[^"]+)"'
    r'(?P<body>.*?)</con:testStep>', re.S)
_FILE = re.compile(r"<file>([^<]*)</file>", re.I)
_SHEET = re.compile(r"<worksheet>([^<]*)</worksheet>", re.I)
_CELL = re.compile(r"<cell>([^<]*)</cell>", re.I)
_PROP = re.compile(r"<con:property>([^<]+)</con:property>")
# the enclosing test case, to know which CSV the rows belong to
_CASE = re.compile(r'<con:testCase[^>]*name="([^"]+)"')


class Spec:
    __slots__ = ("suite", "case", "step", "workbook", "sheet", "cell", "props")

    def __init__(self, suite, case, step, workbook, sheet, cell, props):
        self.suite, self.case, self.step = suite, case, step
        self.workbook, self.sheet, self.cell, self.props = (
            workbook, sheet, cell, props)

    def column_for(self, prop):
        """The CSV column the emitter names for one DataSource property.

        `<step>_<property>`: a step called `DataSource` gives
        `DataSource_propCode`, and one called `datasource_201` gives
        `datasource_201_houseAccountId` -- both spellings appear in the
        run log, which is what pins this rule down.
        """
        return f"{self.step}_{prop}"


# A cell can hold a ReadyAPI property expansion or an Excel formula
# rather than a value. `${generatedDatesAndProps#hcrs}` is a reference
# this framework does not evaluate -- it uses `#name#` -- so copying it
# into a CSV puts that text ON THE WIRE. That is worse than leaving the
# cell empty, because an empty path segment is reported loudly
# ("URL has an EMPTY path segment") while a literal `${...}` propCode
# just quietly 404s as if the property did not exist.
#
# Measured on the first run of this tool: 44 of 56 writes were
# expressions and only 12 were values.
_NOT_A_VALUE = re.compile(r"\$\{|^\s*=|^#[\w.]+#$")


def looks_like_an_expression(value: str) -> bool:
    return bool(_NOT_A_VALUE.search((value or "").strip()))


def suggested_translation(value: str) -> str:
    """What the converter's own CSVs use for the same reference.

    `${step#prop}` appears in emitted rows as `#step_prop#`. Naming the
    translation is useful; MAKING it here is not -- a wrong guess writes
    a value that looks deliberate.
    """
    m = re.match(r"^\$\{#?([\w.]+)#([\w.]+)\}$", (value or "").strip())
    return f"#{m.group(1)}_{m.group(2)}#" if m else ""


def _start_row(cell: str) -> int:
    m = re.search(r"(\d+)", cell or "")
    return max(1, int(m.group(1))) if m else 2


def specs_from_xml(path: str) -> list:
    try:
        with io.open(path, encoding="utf-8", errors="ignore") as fh:
            text = fh.read()
    except OSError:
        return []
    suite = os.path.splitext(os.path.basename(path))[0]
    out = []
    for m in _STEP.finditer(text):
        body = m.group("body")
        f = _FILE.search(body)
        if not f:
            continue                      # not an Excel-backed DataSource
        before = text[:m.start()]
        case = ""
        cases = _CASE.findall(before)
        if cases:
            # The attribute is XML-escaped; the audit mapping holds the
            # real name. `peakRooms &lt; 5` never matched `peakRooms < 5`.
            case = html.unescape(cases[-1])
        out.append(Spec(
            suite=suite, case=case, step=m.group("name"),
            workbook=os.path.basename(f.group(1).strip()),
            sheet=(_SHEET.search(body).group(1).strip()
                   if _SHEET.search(body) else ""),
            cell=(_CELL.search(body).group(1).strip()
                  if _CELL.search(body) else "A2"),
            props=_PROP.findall(body)))
    return out


def read_sheet(workbook_dir: str, name: str, sheet: str, start_row: int):
    """[{header: value}] from one worksheet, or (None, reason)."""
    path = os.path.join(workbook_dir, name)
    if not os.path.isfile(path):
        return None, f"{name} is not in the workbook directory"
    try:
        import openpyxl
    except ImportError:
        return None, "openpyxl is not installed (pip install openpyxl)"
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as e:                                # noqa: BLE001
        return None, f"{name} could not be opened: {e}"
    try:
        actual = sheet
        if sheet and sheet not in wb.sheetnames:
            # `400_Badrequest` in the XML, `400_badRequest` in the file.
            # Excel sheet names are case-insensitive to users, and this
            # is plainly the same sheet -- but only an exact-fold match
            # counts, never a fuzzy one.
            folded = {s.lower(): s for s in wb.sheetnames}
            actual = folded.get(sheet.lower())
            if actual is None:
                return None, (f"{name} has no sheet {sheet!r} "
                              f"(has: {', '.join(wb.sheetnames[:6])})")
        ws = wb[actual] if actual else wb[wb.sheetnames[0]]
        rows = list(ws.iter_rows(values_only=True))
    finally:
        wb.close()
    if not rows:
        return None, f"{name}[{sheet}] is empty"
    header_idx = max(0, start_row - 2)
    if header_idx >= len(rows):
        return None, f"{name}[{sheet}] has no row {start_row}"
    header = [str(c).strip() if c is not None else ""
              for c in rows[header_idx]]
    out = []
    for raw in rows[header_idx + 1:]:
        if raw is None or all(c is None or str(c).strip() == "" for c in raw):
            continue
        row = {}
        for i, h in enumerate(header):
            if not h:
                continue
            v = raw[i] if i < len(raw) else None
            row[h] = "" if v is None else str(v).strip()
        out.append(row)
    return out, ""


def near_miss(wanted: list, headers: list) -> str:
    """Name a header that differs from the wanted one by one character.

    `peekRooms` in the XML against `peakRooms` in the sheet is a typo in
    the ReadyAPI project, not something to correct silently -- filling
    the wrong column is worse than filling none. So it is REPORTED, with
    the likely intent, and a human decides.
    """
    import difflib
    for w in wanted:
        close = difflib.get_close_matches(w, headers, n=1, cutoff=0.8)
        if close and close[0] != w:
            return (f" -- did the project mean {close[0]!r}? "
                    f"(the XML asks for {w!r}; not corrected here)")
    return ""


def case_to_csv(suite_dir: str) -> dict:
    """soapui_case -> csv_path, from the converter's own audit mapping."""
    p = os.path.join(suite_dir, "case_to_method_mapping.csv")
    if not os.path.isfile(p):
        return {}
    out = {}
    with io.open(p, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            case = (row.get("soapui_case") or "").strip()
            rel = (row.get("csv_path") or "").strip()
            if case and rel:
                out.setdefault(case, rel)
    return out


def fill_csv(csv_path: str, values: dict, force: bool,
             write: bool = True) -> tuple:
    """(filled, skipped, missing_columns). Rewrites only when it changes.

    `write=False` is --dry-run: everything is decided identically and
    only the file is left alone."""
    if not os.path.isfile(csv_path):
        return 0, 0, []
    with io.open(csv_path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))

    if not rows:
        return 0, 0, []
    header = list(rows[0].keys())
    lower = {h.lower(): h for h in header if h}
    missing = [c for c in values if c.lower() not in lower]
    filled = skipped = 0
    for row in rows:
        for col, val in values.items():
            real = lower.get(col.lower())
            if real is None or val == "":
                continue
            if (row.get(real) or "").strip() and not force:
                skipped += 1
                continue
            row[real] = val
            filled += 1
    if filled and write:
        with io.open(csv_path, "w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=header)
            w.writeheader()
            w.writerows(rows)
    return filled, skipped, missing


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workbooks", required=True,
                    help="the directory holding the .xlsx the XML names")
    ap.add_argument("--input", default=INPUT_DIR)
    ap.add_argument("--audit", default=AUDIT_DIR)
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--suite", default="", help="limit to one suite")
    ap.add_argument("--dry-run", action="store_true",
                    help="say what would change and write nothing")
    ap.add_argument("--force", action="store_true",
                    help="also overwrite cells that already have a value")
    args = ap.parse_args(argv)

    if not os.path.isdir(args.workbooks):
        print(f"refused: {args.workbooks} is not a directory")
        return 2

    xmls = sorted(f for f in os.listdir(args.input) if f.endswith(".xml"))
    if args.suite:
        xmls = [f for f in xmls
                if os.path.splitext(f)[0].lower() == args.suite.lower()]

    total_specs = total_filled = total_skipped = 0
    problems = []
    sheet_cache = {}

    for xml in xmls:
        specs = specs_from_xml(os.path.join(args.input, xml))
        if not specs:
            continue
        suite_key = os.path.splitext(xml)[0].lower().replace("-", "_")
        mapping = case_to_csv(os.path.join(args.audit, suite_key))
        if not mapping:
            problems.append(f"{xml}: no case_to_method_mapping.csv under "
                            f"_audit/{suite_key}/ -- convert this suite first")
            continue

        for spec in specs:
            total_specs += 1
            key = (spec.workbook, spec.sheet, spec.cell)
            if key not in sheet_cache:
                sheet_cache[key] = read_sheet(
                    args.workbooks, spec.workbook, spec.sheet,
                    _start_row(spec.cell))
            rows, why = sheet_cache[key]
            if rows is None:
                problems.append(f"{spec.suite}/{spec.case}/{spec.step}: {why}")
                continue
            rel = mapping.get(spec.case)
            if not rel:
                problems.append(
                    f"{spec.suite}: case {spec.case!r} is not in the audit "
                    f"mapping -- it may not have been converted")
                continue
            # Row 1 of the sheet drives the generated row. A CSV holds one
            # row per converted case; the DataSource's other rows are the
            # loop ReadyAPI ran, which the converter represents as cases.
            first = rows[0]
            values = {}
            for prop in spec.props:
                if prop not in first:
                    continue
                cell = first[prop]
                if looks_like_an_expression(cell):
                    hint = suggested_translation(cell)
                    problems.append(
                        f"{spec.suite}/{spec.case}: {spec.workbook}"
                        f"[{spec.sheet}].{prop} holds {cell!r}, which is a "
                        f"ReadyAPI expression, not a value"
                        + (f" -- the emitted rows spell this {hint!r}; "
                           f"set it by hand if that is right" if hint else ""))
                    continue
                values[spec.column_for(prop)] = cell
            if not values:
                hint = near_miss(spec.props, list(first.keys()))
                problems.append(
                    f"{spec.suite}/{spec.case}: none of {spec.props[:4]} are "
                    f"headers in {spec.workbook}[{spec.sheet}]" + hint)
                continue
            path = os.path.join(args.root, rel.replace("/", os.sep))
            # --dry-run takes the SAME path and only withholds the write.
            # Counting intent instead of outcome made it predict 1,308
            # fills where the run made 134, and hide 122 problems that
            # only the write path reported. A dry run that does not
            # predict the run is worse than no dry run.
            filled, skipped, missing = fill_csv(
                path, values, args.force, write=not args.dry_run)
            total_filled += filled
            total_skipped += skipped
            if missing:
                problems.append(
                    f"{rel}: no column for {', '.join(missing[:4])}")

    print(f"\n{total_specs} DataSource step(s) read from {len(xmls)} XML(s)")
    if args.dry_run:
        print("  (a dry run can over-count slightly: two DataSource steps "
              "may target the same cell, and only the real run sees the "
              "first one fill it)")
    print(f"{total_filled} cell(s) {'would be ' if args.dry_run else ''}filled"
          + (f", {total_skipped} left alone (already had a value; --force "
             f"overrides)" if total_skipped else ""))
    if problems:
        print(f"\n{len(problems)} thing(s) this could NOT fill -- each is a "
              f"value it refuses to invent:")
        for p in problems[:25]:
            print("   " + p)
        if len(problems) > 25:
            print(f"   ... and {len(problems) - 25} more")
    return 0


if __name__ == "__main__":
    sys.exit(main())
