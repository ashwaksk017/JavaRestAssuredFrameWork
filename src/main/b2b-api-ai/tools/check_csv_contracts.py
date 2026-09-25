"""Every emitted CSV column must be readable by the framework that consumes it.

The converter writes the CSV; the Java framework reads it back by rebuilding
the column name at runtime. Nothing checks that the two agree, and when they
drift the failure is silent: the override is ignored and the assertion falls
back to the literal baked in at emit time -- which, for a clustered @Test, is
some OTHER ReadyAPI case's expected value.

That is exactly what happened with msgcontent. The emitter numbers those
columns by element ordinal (`expected_<step>_msgcontent_<N>_<field>`); the
framework rebuilt them without the ordinal, so all 1,668 of them were
unreachable across the reference suite.

Run: python tools/check_csv_contracts.py [--root .]
"""
import argparse
import csv
import io
import os
import re
import sys

BS = chr(92)
LONG = BS + BS + "?" + BS


def read(path):
    for cand in (path, LONG + os.path.abspath(path).replace("/", BS)):
        try:
            with io.open(cand, encoding="utf8", newline="") as fh:
                return fh.read()
        except OSError:
            continue
    return ""


def csv_files(root):
    base = os.path.join(root, "src", "test", "resources", "csv")
    for dirpath, _dirs, files in os.walk(base):
        for fn in files:
            if fn.endswith(".csv"):
                yield os.path.join(dirpath, fn)


# Column kinds the FRAMEWORK rebuilds at runtime from (step, path). Anything
# not matched here must instead be read verbatim by the emitted Java -- see
# inlined_columns() -- or nothing reads it at all.
REBUILT = (
    re.compile(r"^expected_.+_status_code$"),
    re.compile(r"^expected_status_code$"),
    re.compile(r"^expected$"),
    re.compile(r"^expected_.+_jsonpath_.+$"),
    re.compile(r"^expected_.+_exists_.+$"),
    re.compile(r"^expected_.+_count_.+$"),
    re.compile(r"^expected_.+_header_.+$"),
    # indexed; resolved by ResponseAsserts.msgContentColumn
    re.compile(r"^expected_.+_msgcontent_\d+_.+$"),
)


def inlined_columns(root):
    """Columns the emitted Java reads verbatim, e.g. row.get("expected_x_y").

    datameta / contains / notcontains / sla_ms columns are consumed this way:
    the emitter knows the exact name and inlines it, so no runtime rebuild is
    involved and no mismatch is possible.
    """
    found = set()
    rx = re.compile(r'row\.get\("(expected_[^"]+)"\)')
    for sub in ("support", "data", "rest"):
        base = os.path.join(root, "src", "main", "java", "com", "hi", "api", sub)
        for dirpath, _d, files in os.walk(base):
            for fn in files:
                if fn.endswith(".java"):
                    found.update(rx.findall(read(os.path.join(dirpath, fn))))
    return found


# Guards against silently reverting the fix: these must stay in the Java.
# Needles are full declarations/call sites on purpose: a bare name like
# "msgContentColumn" is still a substring of "msgContentColumnX", so renaming
# the method away would NOT have tripped this guard.
JAVA_GUARDS = (
    ("ResponseAsserts.java", "static String msgContentColumn(Map<String, String> row",
     "msgcontent columns carry an element ordinal; the lookup must scan for it"),
    ("ResponseAsserts.java", "colMsg = msgContentColumn(row, step, lastSegment(jsonPath))",
     "jsonEquals must resolve the INDEXED msgcontent column, not build a bare one"),
    ("ResponseAsserts.java", "static boolean rowSaysSkip(Map<String, String> row",
     "a present-but-empty cell means the case asserts nothing -- not "
     "'use the sibling case's literal'"),
    ("RestStep.java", "ResponseAsserts.msgContentColumn(row, stepName, \"salesforceId\")",
     "the salesforce-id poller must resolve the indexed column, or it never arms"),
    # RestStep is on EVERY request path; the reporting filter demonstrably is
    # not (it recorded zero verdicts on a real run). Keep the guaranteed
    # producer, or the digest silently reports no auth failures again.
    ("RestStep.java", "AuthDiagnostics.record(verdict)",
     "RestStep must record the auth verdict -- the reporting filter alone "
     "recorded nothing on a real run"),
    # A rejected token must be dropped, or one revocation 401s the whole
    # suite for the cache TTL (observed: 7,554 rejections in one run).
    ("RestStep.java", "AuthDiagnostics.invalidateCachedTokens(",
     "a rejected token must be cleared, or one revocation cascades"),
    ("AuthDiagnostics.java", "com.hi.api.auth.TokenCache.clear()",
     "must clear TokenCache.HELD -- clearing only AuthUtilities' cache "
     "misses where this suite's token actually lives"),
    # Asserting a literal "#Key#" against a body can only ever be false.
    ("ResponseAsserts.java", "looksUnresolvedPlaceholder(want)",
     "an expectation that is still a raw placeholder must be skipped, "
     "not asserted"),
)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=".")
    args = ap.parse_args()
    root = args.root

    problems = []

    # 1. Java-side guards.
    jroot = os.path.join(root, "src", "main", "java", "com", "hi", "api")
    for fname, needle, why in JAVA_GUARDS:
        hit = False
        for dirpath, _d, files in os.walk(jroot):
            if fname in files and needle in read(os.path.join(dirpath, fname)):
                hit = True
                break
        if not hit:
            problems.append("MISSING in %s: %r -- %s" % (fname, needle, why))

    # 2. Every emitted expected_* column must be READ by something: either
    #    rebuilt by the framework, or inlined verbatim in the emitted Java.
    inlined = inlined_columns(root)
    files = cols = 0
    unknown = {}
    for p in csv_files(root):
        text = read(p)
        if not text:
            continue
        files += 1
        for h in next(csv.reader(io.StringIO(text)), []):
            if not h or not h.startswith("expected"):
                continue
            cols += 1
            if h in inlined:
                continue
            if not any(rx.match(h) for rx in REBUILT):
                shape = re.sub(r"\d+", "N", h)
                unknown.setdefault(shape, 0)
                unknown[shape] += 1
    for shape, n in sorted(unknown.items(), key=lambda kv: -kv[1]):
        problems.append("UNREADABLE column shape (%dx): %s" % (n, shape))

    print("check_csv_contracts: %d CSV file(s), %d expected_* column(s)" % (files, cols))
    if problems:
        print()
        for p in problems:
            print("  FAIL  " + p)
        print()
        print("%d problem(s)." % len(problems))
        return 1
    print("Every expected_* column is read -- rebuilt by the framework or")
    print("inlined verbatim by the emitter -- and the fix guards are in place.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
