"""A --package-root the committed code cannot follow is refused up front.

`--package-root` moves only what the converter generates. The committed
framework imports `<root>.support.*` in 30 files and nothing rewrites
them, so a foreign root emits the support package where none of those
files can see it. The tree then fails, and keeps failing: Maven goes on
compiling the orphaned package tree that no later convert owns or
removes, with errors naming generated files and never the root that
caused them.

The Maven enforcer catches this on a COLD tree. On a tree already
converted once at the committed root it does not -- `<root>.support` is
present, so the precondition passes and the 28 cryptic errors come back
instead.

The first version of the guard regexed `import X.support.` to find
candidate roots and read `com.hi.api.tests.support` as a root named
`com.hi.api.tests`. That is a nested package, and the misreading would
have refused a legitimately RENAMED repo. A check that fires on correct
input gets switched off, taking its real assertion with it, so the
rename case is tested here first.

    python tools/ra_converter/test_package_root_refusal.py
"""

import io
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))

sys.path.insert(0, HERE)

import ra_converter as rc  # noqa: E402


class _Args(object):
    def __init__(self, output, package_root):
        self.output = output
        self.package_root = package_root


def _tree(root_pkg, importer_pkg=None):
    """A throwaway source tree declaring `root_pkg`, importing support."""
    d = tempfile.mkdtemp(prefix="ra_root_")
    imp = importer_pkg or root_pkg
    main = os.path.join(d, "src", "main", "java", *root_pkg.split("."))
    os.makedirs(os.path.join(main, "domain"))
    io.open(os.path.join(main, "domain", "DomainApis.java"), "w",
            encoding="utf-8").write(
        "package %s.domain;%simport %s.support.ImportedRestClient;%s"
        "public class DomainApis {}%s"
        % (root_pkg, chr(10), imp, chr(10), chr(10)))
    os.makedirs(os.path.join(main, "dsl"))
    io.open(os.path.join(main, "dsl", "SuiteName.java"), "w",
            encoding="utf-8").write(
        "package %s.dsl;%spublic class SuiteName {}%s"
        % (root_pkg, chr(10), chr(10)))
    return d


# --------------------------------------------- the false-positive first

def test_a_renamed_repo_is_NOT_refused():
    """Every declaration and import moved together, so the root is
    followed rather than refused. This is the case v1 got wrong."""
    d = _tree("com.acme.api")
    try:
        assert rc._committed_package_root(d) == "com.acme.api", (
            rc._committed_package_root(d))
        rc._refuse_unfollowable_package_root(_Args(d, "com.acme.api"))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_a_nested_support_package_is_not_mistaken_for_a_root():
    """`com.hi.api.tests.support` is a nested package. Reading it as a
    root named `com.hi.api.tests` is what made v1 refuse valid input."""
    d = _tree("com.hi.api", importer_pkg="com.hi.api.tests")
    try:
        # No file imports `com.hi.api.support`, so nothing is blocked.
        rc._refuse_unfollowable_package_root(_Args(d, "com.hi.api"))
        assert rc._committed_package_root(d) == "com.hi.api"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_committed_code_that_does_not_import_support_is_not_refused():
    """Nothing would be orphaned, so moving the generated tree is fine."""
    d = tempfile.mkdtemp(prefix="ra_root_")
    try:
        p = os.path.join(d, "src", "main", "java", "com", "acme", "dsl")
        os.makedirs(p)
        io.open(os.path.join(p, "Thing.java"), "w", encoding="utf-8").write(
            "package com.acme.dsl;" + chr(10) + "public class Thing {}")
        rc._refuse_unfollowable_package_root(_Args(d, "com.other"))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_the_guard_fails_open_on_an_empty_tree():
    """A guard that blocks a run it cannot reason about is worse than
    no guard."""
    d = tempfile.mkdtemp(prefix="ra_root_")
    try:
        assert rc._committed_package_root(d) == ""
        rc._refuse_unfollowable_package_root(_Args(d, "anything.at.all"))
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ------------------------------------------------- then the real refusal

def test_a_foreign_root_is_refused():
    d = _tree("com.hi.api")
    try:
        raised = None
        try:
            rc._refuse_unfollowable_package_root(_Args(d, "com.hi.api.kafka"))
        except SystemExit as exc:
            raised = str(exc)
        assert raised is not None, "a foreign root must be refused"
        assert "REFUSING" in raised and "com.hi.api.kafka" in raised, raised
        assert "Re-run with --package-root com.hi.api" in raised, raised
        assert "nothing was written" in raised, raised
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_the_message_names_the_files_that_would_be_orphaned():
    """A refusal that does not say which files block it gets overridden
    or worked around."""
    d = _tree("com.hi.api")
    try:
        try:
            rc._refuse_unfollowable_package_root(_Args(d, "com.zzz"))
            raise AssertionError("should have refused")
        except SystemExit as exc:
            assert "DomainApis.java" in str(exc), str(exc)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_the_real_tree_refuses_a_foreign_root_and_allows_its_own():
    """Against this checkout, when it has committed sources."""
    if not os.path.isdir(os.path.join(ROOT, "src", "main", "java")):
        return
    found = rc._committed_package_root(ROOT)
    if not found:
        return
    rc._refuse_unfollowable_package_root(_Args(ROOT, found))   # quiet
    try:
        rc._refuse_unfollowable_package_root(_Args(ROOT, found + ".kafka"))
        raise AssertionError("a foreign root must be refused")
    except SystemExit:
        pass


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok  " + name)
            passed += 1
    print("%d passed" % passed)
