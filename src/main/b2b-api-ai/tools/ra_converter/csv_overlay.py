"""Durable per-cell overrides for the generated row files.

The generated CSVs under `src/test/resources/csv/` are rewritten by every
convert and nothing in them survives except the `execute` flag. A tester
who corrects a stale expectation in a cell loses the correction on the
next convert, so corrections were being re-applied by hand after each
run -- or not re-applied, which is how a row expecting the ReadyAPI
author's 2019 status code kept failing against today's server.

An OVERLAY is a small tracked CSV that mirrors one generated file:

    tools/ra_converter/csv_overrides/<suite>/<group>/<TestClass>/<method>.csv

                 mirrors

    src/test/resources/csv/<suite>/<group>/<TestClass>/<method>.csv

Header rules
------------
* `test_case_id`           required; matched exactly against the generated
                           row's `test_case_id` cell.
* `key:<generated column>` zero or more extra MATCH columns. A generated
                           row matches an overlay row only when every key
                           cell is exactly equal as well.
* anything else            an OVERRIDE column; its name must be a column
                           of the generated file. A name that is not is an
                           error that names the overlay file and the
                           column, so a typo never silently does nothing.

Cell rules
----------
* An empty override cell means "no override for this column on this row".
* Every generated row that matches gets every non-empty override cell.
* Two overlay rows that match the same generated row apply in file order;
  the later one wins for any column they both set.
* An overlay row that matches NO generated row is reported, not fatal.

Everything else about the generated file -- row order, the header, cells
that are not overridden -- is left byte for byte as the converter wrote it.

This module holds the pure merge; `ra_converter.Emitter._apply_csv_overlay`
locates the file, splits the already-joined rows, calls `merge_overlay`
and re-joins only the rows that changed.
"""

import csv
import io
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OVERLAY_ROOT = os.path.join(HERE, "csv_overrides")

ID_COL = "test_case_id"
KEY_PREFIX = "key:"
GENERATED_PREFIX = "src/test/resources/csv/"


class OverlayError(ValueError):
    """A malformed overlay: missing id column, unknown column name."""


class Applied(object):
    """One cell the overlay changed. `row` is the 0-based index into the
    generated rows (header excluded)."""
    __slots__ = ("row", "test_case_id", "column", "old", "new")

    def __init__(self, row, test_case_id, column, old, new):
        self.row = row
        self.test_case_id = test_case_id
        self.column = column
        self.old = old
        self.new = new

    def __repr__(self):
        return "Applied(row=%r, id=%r, col=%r, %r -> %r)" % (
            self.row, self.test_case_id, self.column, self.old, self.new)


class MergeResult(object):
    __slots__ = ("rows", "applied", "unmatched", "changed_rows")

    def __init__(self, rows, applied, unmatched, changed_rows):
        self.rows = rows                  # list[list[str]], same order
        self.applied = applied            # list[Applied]
        self.unmatched = unmatched        # list[dict]: overlay rows w/o a match
        self.changed_rows = changed_rows  # set[int]: indexes that changed


def overlay_rel_for(generated_rel):
    """`src/test/resources/csv/a/b/C/m.csv` -> `a/b/C/m.csv`, or None when
    the path is not a generated row file."""
    rel = (generated_rel or "").replace(chr(92), "/")
    if not rel.startswith(GENERATED_PREFIX):
        return None
    return rel[len(GENERATED_PREFIX):]


def overlay_path_for(generated_rel, root=None):
    """Absolute overlay path mirroring one generated row file, or None."""
    tail = overlay_rel_for(generated_rel)
    if tail is None:
        return None
    return os.path.join(root or OVERLAY_ROOT, *tail.split("/"))


def read_overlay(path, display_name=None):
    """Parse one overlay file.

    Returns (key_cols, override_cols, rows): `key_cols` always starts with
    `test_case_id` and then holds the `key:`-prefixed names WITHOUT the
    prefix; `rows` is a list of dicts keyed by the header as written
    (`key:` prefix kept), values as read.

    `path` is the path to open (the caller may have made it long-path
    safe); `display_name` is what error messages call it.
    """
    name = display_name or path
    with io.open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        try:
            header = next(reader)
        except StopIteration:
            raise OverlayError("csv overlay %s is empty (no header)" % name)
        header = [h.strip() for h in header]
        if ID_COL not in header:
            raise OverlayError(
                "csv overlay %s has no `%s` column (header: %s)"
                % (name, ID_COL, header))
        seen = set()
        for h in header:
            if h in seen:
                raise OverlayError(
                    "csv overlay %s names column `%s` twice" % (name, h))
            seen.add(h)
        key_cols = [ID_COL] + [h[len(KEY_PREFIX):].strip()
                               for h in header if h.startswith(KEY_PREFIX)]
        override_cols = [h for h in header
                         if h != ID_COL and not h.startswith(KEY_PREFIX)]
        rows = []
        for lineno, cells in enumerate(reader, start=2):
            if not any(c.strip() for c in cells):
                continue                       # blank line: ignore
            if len(cells) != len(header):
                raise OverlayError(
                    "csv overlay %s line %d has %d cell(s), header has %d"
                    % (name, lineno, len(cells), len(header)))
            rows.append(dict(zip(header, cells)))
    return key_cols, override_cols, rows


def merge_overlay(cols, rows, overlay_rows, key_cols, override_cols,
                  overlay_name="<overlay>"):
    """Apply overlay rows to generated rows.

    `cols`          generated header, unquoted names, in file order.
    `rows`          generated rows as lists of UNQUOTED cell strings; the
                    input lists are not mutated.
    `overlay_rows`  dicts from `read_overlay`.
    `key_cols`      match columns, `test_case_id` first (generated names).
    `override_cols` override columns (generated names).

    Raises OverlayError when any key or override column is not in `cols`.
    Returns a MergeResult whose `rows` preserve order and every cell that
    was not overridden.
    """
    cols = [(c or "").strip().strip('"') for c in cols]
    index = {}
    for i, c in enumerate(cols):
        index.setdefault(c, i)

    missing = [c for c in key_cols + override_cols if c not in index]
    if missing:
        raise OverlayError(
            "csv overlay %s names column(s) the generated file does not "
            "have: %s. Generated header: %s"
            % (overlay_name, ", ".join("`%s`" % m for m in missing), cols))

    key_idx = [index[c] for c in key_cols]
    id_idx = index[ID_COL]

    def overlay_key_value(orow, col):
        # The first key column is `test_case_id` (no prefix); the rest are
        # written with the `key:` prefix in the overlay header.
        if col == ID_COL:
            return orow.get(ID_COL, "")
        return orow.get(KEY_PREFIX + col, "")

    out = [list(r) for r in rows]
    applied = []
    unmatched = []
    changed = set()
    for orow in overlay_rows:
        wanted = [overlay_key_value(orow, c) for c in key_cols]
        hit = False
        for ri, cells in enumerate(out):
            if len(cells) != len(cols):
                continue                   # ragged row: never touch it
            if [cells[i] for i in key_idx] != wanted:
                continue
            hit = True
            for col in override_cols:
                new = orow.get(col, "")
                if new == "":
                    continue               # empty cell = no override
                ci = index[col]
                old = cells[ci]
                if old == new:
                    continue               # already that value: no-op
                cells[ci] = new
                changed.add(ri)
                applied.append(Applied(ri, cells[id_idx], col, old, new))
        if not hit:
            unmatched.append(dict(orow))
    return MergeResult(out, applied, unmatched, changed)
