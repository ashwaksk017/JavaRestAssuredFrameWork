"""The Java test packages still exist.

WHY THIS EXISTS
---------------
A commit labelled as a one-line README fix deleted 62 files and 10,670
lines -- the whole of `tests/framework`, `tests/samples`,
`tests/security` and more -- and it was pushed. Nothing objected. The
Java suite quietly went from 395 tests to 96, and the only reason it
surfaced was someone reading the test count on an unrelated run.

That is the dangerous shape: `mvn surefire:test` with a class-name
PATTERN does not fail when the classes are gone. It runs whatever
matches, reports BUILD SUCCESS, and a smaller number scrolls past. A
deleted test cannot fail, so the safety net disappears silently and
every later green run is less green than it looks.

This asserts the packages are present and not obviously gutted. It is
deliberately a floor, not an exact count -- tests get added and
removed, and a brittle number would be edited away the first time it
complained. The floors are set well under the real counts so only
real damage trips them.

    python tools/test_test_tree_intact.py
"""

import glob
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TESTS = os.path.join(ROOT, "src", "test", "java", "com", "hi", "api")

# package -> the fewest *Test.java files it should ever hold.
# Set from the counts at restore time, minus generous headroom.
FLOORS = {
    os.path.join("tests", "framework"): 20,
    os.path.join("dsl",): 5,
    os.path.join("rest", "utilities"): 2,
    os.path.join("support",): 1,
}


def _count(rel):
    d = os.path.join(TESTS, rel)
    if not os.path.isdir(d):
        return None
    return len(glob.glob(os.path.join(d, "*Test.java")))


def test_no_java_test_package_has_been_emptied():
    missing, thin = [], []
    for rel, floor in FLOORS.items():
        n = _count(rel)
        if n is None:
            missing.append("%s (DIRECTORY GONE)" % rel.replace(os.sep, "/"))
        elif n < floor:
            thin.append("%s has %d *Test.java, expected at least %d"
                        % (rel.replace(os.sep, "/"), n, floor))
    if missing or thin:
        raise AssertionError(
            "Java test sources are missing. A surefire run with a class "
            "PATTERN will still say BUILD SUCCESS with these gone -- it "
            "just runs fewer tests -- so this is the only thing that "
            "notices.\n  " + "\n  ".join(missing + thin)
            + "\n\nIf the removal was deliberate, lower the floor in "
              "tools/test_test_tree_intact.py in the SAME commit, so the "
              "change is visible in review rather than silent.")


def test_the_floors_name_packages_that_exist():
    """A floor for a package nobody has would never fire."""
    for rel in FLOORS:
        d = os.path.join(TESTS, rel)
        assert os.path.isdir(d), (
            "floor set for %s but that directory does not exist -- either "
            "the package moved and the floor is now dead, or the tree is "
            "already damaged" % rel.replace(os.sep, "/"))


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok  " + name)
            passed += 1
    print("%d passed" % passed)
