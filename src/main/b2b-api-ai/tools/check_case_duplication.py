#!/usr/bin/env python3
"""No two suites may register the same case id.

`CaseRegistry.register(id)` does a plain `CASES.put(id, case)` -- no
duplicate detection, and the key carries no suite. So when two converted
projects contain a case of the same name, both emit a registration for
it, and the suite whose static initializer runs LAST silently wins. The
other suite's phase chain is replaced by its namesake's. Nothing fails:
the tree compiles, the tests run, and they run the wrong chain.

This is neither hypothetical nor rare. The ReadyAPI projects overlap by
design -- `PartialGoalRegression` is a cross-cutting extract and
`smokePROD` is a smoke subset, so both re-use cases from the per-API
projects. 26 of PartialGoalRegression's 37 cases appear in at least one
other project, four of them in two.

`check_phase_order.py` sees the symptom but files it under "registration
forms this parser did not understand", which buries it. This check names
the colliding cases and the suites they came from, because that is the
information needed to decide what to do about it.

Reads the EMITTED tree, not the source XMLs: what collides at runtime is
what was emitted. The source projects are consulted only to report which
ReadyAPI file each suite came from.
"""
import os
import re
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUPPORT = os.path.join(ROOT, "src", "main", "java", "com", "hi", "api", "support")

# register("literal")
REGISTER_RX = re.compile(r'CaseRegistry\.register\(\s*"((?:[^"\\]|\\.)*)"\s*\)')

# Cases that share one spec are emitted as a loop over an array, so the id
# reaching register() is a VARIABLE:
#     for (String id : new String[] {"a", "b"}) { CaseRegistry.register(id)
# Missing this form is exactly how check_phase_order ends up calling these
# "registration forms this parser did not understand".
LOOP_RX = re.compile(
    r"for\s*\(\s*String\s+(\w+)\s*:\s*new\s+String\s*\[\s*\]\s*\{([^}]*)\}\s*\)")

STRING_LITERAL_RX = re.compile(r'"((?:[^"\\]|\\.)*)"')

# Every call site, whatever supplies the id. Used to prove nothing was
# skipped in silence.
ANY_REGISTER_RX = re.compile(r"CaseRegistry\.register\(")


def _unescape(java_literal):
    """Java string literal -> the id the registry actually keys on."""
    return java_literal.replace('\\"', '"').replace("\\\\", "\\")


def collect():
    """({case id: {suite: [file, ...]}}, registrations, unreadable forms)."""
    found = defaultdict(lambda: defaultdict(list))
    total = 0
    unparsed = []
    if not os.path.isdir(SUPPORT):
        return found, total, unparsed

    for suite in sorted(os.listdir(SUPPORT)):
        cases_dir = os.path.join(SUPPORT, suite, "cases")
        if not os.path.isdir(cases_dir):
            continue            # framework types live directly under support/
        for base, _dirs, files in os.walk(cases_dir):
            for name in sorted(files):
                if not name.endswith(".java"):
                    continue
                path = os.path.join(base, name)
                try:
                    with open(path, encoding="utf-8") as fh:
                        src = fh.read()
                except OSError:
                    continue
                rel = os.path.relpath(
                    path, os.path.join(SUPPORT, suite)).replace(os.sep, "/")

                call_sites_explained = 0

                for m in REGISTER_RX.finditer(src):
                    call_sites_explained += 1
                    total += 1
                    found[_unescape(m.group(1))][suite].append(rel)

                for m in LOOP_RX.finditer(src):
                    var, array = m.group(1), m.group(2)
                    if not re.search(
                            r"CaseRegistry\.register\(\s*" + re.escape(var)
                            + r"\s*\)", src):
                        continue        # array is for something else
                    call_sites_explained += 1   # the one register(var) site
                    for lit in STRING_LITERAL_RX.finditer(array):
                        total += 1
                        found[_unescape(lit.group(1))][suite].append(
                            rel + " (shared spec)")

                sites = len(ANY_REGISTER_RX.findall(src))
                if sites > call_sites_explained:
                    # Never under-report in silence: that is the exact
                    # failure mode this check exists to replace.
                    unparsed.append(
                        "%s/%s: %d register() call(s) in a form this check "
                        "cannot read" % (suite, rel, sites - call_sites_explained))
    return found, total, unparsed


def source_project(suite):
    """The ReadyAPI project a suite was converted from, when discoverable."""
    audit = os.path.join(ROOT, "_audit", suite, "case_to_method_mapping.csv")
    try:
        with open(audit, encoding="utf-8") as fh:
            fh.readline()               # header
            first = fh.readline()
        if first:
            return first.split(",", 1)[0].strip() or "?"
    except OSError:
        pass
    return "?"


def main():
    found, total, unparsed = collect()
    if not found and not unparsed:
        print("case duplication: nothing converted in this tree yet -- "
              "run the converter first.")
        return 0

    suites = sorted({s for per in found.values() for s in per})
    print("case duplication: %d registration(s), %d distinct case id(s), "
          "%d suite(s)" % (total, len(found), len(suites)))
    for s in suites:
        print("  %-28s from %s" % (s, source_project(s)))

    if unparsed:
        print("\n!! %d file(s) hold a register() form this check cannot read "
              "-- counts below may be LOW" % len(unparsed))
        for u in unparsed:
            print("   " + u)

    cross = {cid: per for cid, per in found.items() if len(per) > 1}
    within = {}
    for cid, per in found.items():
        for suite, files in per.items():
            if len(files) > 1:
                within.setdefault(cid, {})[suite] = files

    if within:
        print("\n!! %d case id(s) registered TWICE WITHIN one suite" % len(within))
        print("   One suite emitting the same id twice is a converter bug on")
        print("   its own: the second registration overwrites the first.")
        for cid in sorted(within):
            print("\n   %s" % cid)
            for suite, files in sorted(within[cid].items()):
                for f in files:
                    print("       %-28s %s" % (suite, f))

    if cross:
        print("\n!! %d case id(s) registered by MORE THAN ONE suite" % len(cross))
        print("   register() does a plain put(), so the suite whose static")
        print("   initializer runs LAST wins and the other suite's chain is")
        print("   silently replaced. Compilation and the test run both pass.")
        for cid in sorted(cross, key=lambda c: (-len(found[c]), c)):
            owners = sorted(found[cid])
            print("\n   %s   (%d suites)" % (cid, len(owners)))
            for suite in owners:
                for f in found[cid][suite]:
                    print("       %-28s %s" % (suite, f))

    if not cross and not within:
        if unparsed:
            return 1
        print("\nevery case id is registered exactly once. Nothing collides.")
        return 0

    print("\nTwo ways out, and they are not equivalent:")
    print("  - namespace the registry key by suite, so overlapping projects")
    print("    can coexist (chained-by-name lookups must then resolve within")
    print("    a suite); or")
    print("  - make register() refuse a duplicate loudly, which turns a")
    print("    silent wrong-chain into a fast failure but means two")
    print("    overlapping projects cannot share one tree.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
