"""Drop emitted CSV columns that nothing can read.

A ReadyAPI `properties` step is a bag: the author's literals sit next to
slots the case never expanded. The converter carries the whole bag across,
so a generated CSV can open with dozens of columns no template, spec, hook
or framework class ever looks at. On this suite that was 81 of 317
`Properties.*` columns -- 71 of them `Pair_2_RatePlanCode` ..
`Pair_47_RatePlanCode` from one properties step with 40-odd unused slots.
They are not wrong, only noise, and noise in the first screen of a data file
is expensive: an author reading it cannot tell which columns drive anything.

    python tools/prune_dead_props.py [--root .] [--apply] [--suite NAME]

Dry run by default. `--apply` rewrites the files and records what it removed
in `_audit/<suite>/pruned_columns.csv`, so a prune is reviewable and can be
undone from the audit rather than from memory.

WHY USAGE-DRIVEN AND NOT PREFIX-DRIVEN
--------------------------------------
"Looks like a literal" is not a safe test. Generated identity columns
(`Properties.Email`, `Properties.Domain`) also look like literals, and
`regenRandomProperties` reads the row to decide whether to keep the CSV
value -- dropping one silently changes which identity a case sends. So a
column is kept unless NOTHING references it, in any spelling the framework
can resolve:

  * `#Properties_X#` / `@Properties_X@`  template and query placeholders
  * `"Properties.X"` / `"Properties_X"`  Ref.ctx, hooks, framework Java
  * `"X"` as a bare or trailing field     ImportedScenario.ctxGet falls back
                                          to a trailing-field walk, so a
                                          column can answer a lookup without
                                          ever being named in full

That last one is the trap this tool must not fall into. `ctxGet` resolves
`PropertiesDetails.accountID` by walking ctx for a key ENDING in
`accountID`, which a differently-prefixed column can satisfy. Matching only
the exact column name would call such a column dead and delete a value the
suite depends on.
"""
from __future__ import annotations

import argparse
import collections
import csv
import io
import os
import re
import sys

_PFX = "\\\\?\\"

# Columns the framework itself consumes by name. Never candidates, whatever
# the reference scan says -- they are read by machinery, not by templates.
_NEVER_PRUNE_EXACT = {
    "test_case_id", "_stop_after", "groups", "expected", "jira_xray_id",
    "template", "body", "dataFile",
}
_NEVER_PRUNE_PREFIX = (
    "expected_", "template_", "body_", "msg_", "qry_", "tpl_", "hdr_",
)


def _lp(path: str) -> str:
    ap = os.path.abspath(path)
    if os.name == "nt" and not ap.startswith(_PFX):
        return _PFX + ap
    return ap


def _read(path: str) -> str:
    try:
        with open(_lp(path), encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def _walk(base: str, suffixes):
    for dirpath, _dirs, files in os.walk(_lp(base)):
        for fn in sorted(files):
            if fn.endswith(suffixes):
                yield os.path.join(dirpath, fn)


# Places that CONSUME a column. Deliberately excludes
# src/main/resources/test_data_defaults/: that file is a fallback data
# PROVIDER, loaded by ImportedScenario when a row omits a key. It carries
# the same ReadyAPI properties bag as the CSV, so counting it as a reference
# makes dead data look alive -- with it included this tool found 5 dead
# columns instead of 81, because every Pair_N_RatePlanCode appears there too.
# A key sitting in two data files and read by neither is still dead.
_CONSUMER_TREES = (
    ("src/main/java", (".java",)),
    ("src/test/java", (".java",)),
    ("src/main/resources/templates", (".json", ".xml", ".txt")),
)


def reference_blob(root: str) -> str:
    """Everything that could READ a column: emitted Java and templates."""
    parts = []
    for base, sfx in _CONSUMER_TREES:
        d = os.path.join(root, base)
        if os.path.isdir(_lp(d)):
            for p in _walk(d, sfx):
                parts.append(_read(p))
    return "\n".join(parts)


def also_in_defaults(root: str, columns) -> set:
    """Dead columns that the bundled defaults JSON carries as well.

    Pruning the CSV alone leaves the same noise one file over, and the
    defaults are what fill a column the row omits -- so a reader who finds
    the key there will reasonably assume it matters.
    """
    d = os.path.join(root, "src/main/resources/test_data_defaults")
    if not os.path.isdir(_lp(d)):
        return set()
    blob = "\n".join(_read(p) for p in _walk(d, (".json",)))
    return {c for c in columns if '"%s"' % c in blob}


def is_referenced(column: str, blob: str) -> str | None:
    """How `column` is reachable, or None when nothing can read it."""
    under = column.replace(".", "_")
    field = column.split(".", 1)[1] if "." in column else column
    for pat, how in (
        ("#%s#" % under, "template/query placeholder"),
        ("@%s@" % under, "at-placeholder"),
        ('"%s"' % column, "named in Java"),
        ('"%s"' % under, "underscore spelling in Java"),
    ):
        if pat in blob:
            return how
    # trailing-field fallback: ctxGet walks ctx for a key ENDING in the field
    if field and re.search(r'"[A-Za-z0-9_.]*%s"' % re.escape(field), blob):
        return "trailing-field / alias"
    if field and ("#%s#" % field in blob or "@%s@" % field in blob):
        return "bare-field placeholder"
    return None


def csv_files(root: str, suite: str | None):
    base = os.path.join(root, "src/test/resources/csv")
    if not os.path.isdir(_lp(base)):
        return []
    out = []
    for p in _walk(base, (".csv",)):
        norm = p.replace("\\", "/")
        if suite and ("/csv/%s/" % suite) not in norm:
            continue
        out.append(p)
    return out


def header_of(path: str):
    try:
        with open(_lp(path), encoding="utf-8-sig", newline="") as fh:
            return next(csv.reader(fh), [])
    except OSError:
        return []


def prunable(column: str) -> bool:
    if column in _NEVER_PRUNE_EXACT:
        return False
    if any(column.startswith(p) for p in _NEVER_PRUNE_PREFIX):
        return False
    # Only the ReadyAPI properties bag is in scope. Everything else in a
    # generated CSV is emitted because some step asked for it.
    return column.startswith("Properties")


def rewrite(path: str, drop: set) -> int:
    """Remove `drop` columns, preserving row order and quoting. Rows kept."""
    with open(_lp(path), encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    if not rows:
        return 0
    header = rows[0]
    keep_idx = [i for i, h in enumerate(header) if h not in drop]
    if len(keep_idx) == len(header):
        return 0
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\n")
    for r in rows:
        w.writerow([r[i] if i < len(r) else "" for i in keep_idx])
    with open(_lp(path), "w", encoding="utf-8", newline="") as fh:
        fh.write(buf.getvalue())
    return len(header) - len(keep_idx)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=".")
    ap.add_argument("--suite", default=None,
                    help="limit to src/test/resources/csv/<suite>/")
    ap.add_argument("--apply", action="store_true",
                    help="rewrite the files (default: report only)")
    ap.add_argument("--show", type=int, default=15)
    args = ap.parse_args()

    # A scratch convert (--output somewhere/else) emits support/ and the CSVs
    # but NOT the committed framework, so the reference blob would be missing
    # every consumer in dsl/, context/, rest/utilities/ and the hand-written
    # tests. Every column they read would look unreferenced and be deleted.
    # Refuse rather than prune against a partial view -- the same reasoning
    # check_ctx_dataflow.py uses when its triage baseline is unreachable.
    marker = os.path.join(args.root, "src/main/java/com/ak/api/dsl/CustomerOnboarding.java")
    if not os.path.isfile(_lp(marker)):
        print("[prune-props] skipped: %s is not a full tree (no committed "
              "framework under src/main/java/com/ak/api/dsl). Pruning needs "
              "every consumer visible, or it would delete columns the "
              "framework reads." % args.root)
        return 0

    # The committed framework being present is NOT enough. A convert runs
    # --clean, which deletes the generated Java BEFORE re-emitting it; if it
    # then dies partway (a locked CSV will do it), the tree still has every
    # CSV but no Specs/Hooks to reference them. Measured on exactly that
    # state: unreferenced columns went from 85 to 121, so 36 live columns
    # would have been deleted for looking dead. The generated side has to be
    # there too, and it has to be there in proportion to the CSVs.
    cases_dirs = []
    support = os.path.join(args.root, "src/main/java/com/ak/api/support")
    if os.path.isdir(_lp(support)):
        for dirpath, dirs, _f in os.walk(_lp(support)):
            if os.path.basename(dirpath) == "cases":
                cases_dirs.append(dirpath)
    specs = sum(1 for d in cases_dirs
                for f in os.listdir(_lp(d))
                if f.startswith("Specs") and f.endswith(".java"))
    n_csv = len(csv_files(args.root, args.suite))
    if n_csv and specs == 0:
        print("[prune-props] REFUSING to prune: %d CSV file(s) but no "
              "Specs*.java under src/main/java/com/ak/api/support/*/cases/. "
              "That is a half-built tree -- a --clean convert that did not "
              "finish. Every column would look unreferenced. Re-run the "
              "convert, then prune." % n_csv)
        return 1

    files = csv_files(args.root, args.suite)
    if not files:
        print("[prune-props] no CSVs under %s -- nothing to do" % args.root)
        return 0

    cols = collections.Counter()
    for p in files:
        for h in header_of(p):
            if prunable(h):
                cols[h] += 1
    if not cols:
        print("[prune-props] no Properties* columns found")
        return 0

    blob = reference_blob(args.root)
    dead = sorted(c for c in cols if is_referenced(c, blob) is None)

    print("[prune-props] %d csv file(s); %d Properties* column(s); %d unreferenced"
          % (len(files), len(cols), len(dead)))
    if not dead:
        print("[prune-props] nothing to prune")
        return 0
    for c in dead[:args.show]:
        print("    %-46s in %d file(s)" % (c, cols[c]))
    if len(dead) > args.show:
        print("    ... and %d more" % (len(dead) - args.show))

    dupes = also_in_defaults(args.root, dead)
    if dupes:
        print("[prune-props] %d of these are also in "
              "src/main/resources/test_data_defaults/ -- that file is a "
              "fallback PROVIDER, not a reader, so they are dead there too. "
              "Pruned from the CSVs only; clean the defaults separately."
              % len(dupes))

    if not args.apply:
        print("[prune-props] dry run -- pass --apply to rewrite")
        return 0

    dropset = set(dead)
    touched = removed = 0
    for p in files:
        n = rewrite(p, dropset)
        if n:
            touched += 1
            removed += n

    suite = args.suite or "all"
    audit_dir = os.path.join(args.root, "_audit", suite)
    os.makedirs(_lp(audit_dir), exist_ok=True)
    audit = os.path.join(audit_dir, "pruned_columns.csv")
    with open(_lp(audit), "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["column", "csv_files_it_was_in", "reason"])
        for c in dead:
            w.writerow([c, cols[c], "no template/spec/hook/alias reference"])
    print("[prune-props] removed %d column instance(s) from %d file(s)"
          % (removed, touched))
    print("[prune-props] recorded in %s" % audit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
