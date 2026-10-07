"""A non-default --package-root may move only what the converter GENERATES.

The bundled framework files were re-rooted with a blanket
`content.replace("com.hi.api", package_root)`. That also rewrote their
imports of `auth`, `config`, `context`, `data`, `db`, `domain` and
`rest.utilities` -- 40 hand-maintained files committed at com.hi.api
that never move. So `--package-root com.hi.api.kafka` emitted a tree
importing `com.hi.api.kafka.config.Config`, `...kafka.auth.*`,
`...kafka.db.*`, `...kafka.data.FakeData` and
`...kafka.rest.utilities.RestLoggerUtilityDataHolder`, none of which
exist.

The build then failed with 28 `cannot find symbol` and a wall of
`package ... does not exist`, every one of them pointing at generated
files rather than at the line that rewrote the import -- and the errors
outlived the convert, because Maven keeps compiling an orphaned package
tree that no later run regenerates or removes.

    python tools/ra_converter/test_package_root_rerooting.py
"""

import glob
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
FRAMEWORK = os.path.join(HERE, "framework")

sys.path.insert(0, HERE)

import ra_converter  # noqa: E402

_EMITTER = next(v for v in vars(ra_converter).values()
                if isinstance(v, type)
                and hasattr(v, "_GENERATED_PACKAGE_SEGMENTS"))

GENERATED = _EMITTER._GENERATED_PACKAGE_SEGMENTS
NEW_ROOT = "com.hi.api.kafka"

# `com.hi.api.rest.utilities` must be read as `rest.utilities`, not
# `rest`, or the two-segment packages are misclassified.
_TWO = ("rest.utilities", "rest.clients", "db.repo")


def _segment(ref):
    tail = ref[len("com.hi.api."):]
    for two in _TWO:
        if tail == two or tail.startswith(two + "."):
            return two
    return tail.split(".")[0]


def _framework_files():
    return sorted(glob.glob(os.path.join(FRAMEWORK, "*.java")))


def _referenced_segments():
    segs = set()
    for f in _framework_files():
        text = io.open(f, encoding="utf-8", errors="replace").read()
        for ref in re.findall(r'com\.hi\.api\.[A-Za-z0-9_.]+', text):
            segs.add(_segment(ref))
    return segs


def _reroot(content, root):
    """Exactly what emit_framework_support does."""
    for seg in GENERATED:
        content = content.replace("com.hi.api." + seg, root + "." + seg)
    return content


def test_generated_segments_are_actually_generated():
    """Each listed segment must be a gitignored/generated tree, not a
    committed one -- listing a committed package here would move its
    imports and break the build in the other direction."""
    for seg in GENERATED:
        d = os.path.join(ROOT, "src", "main", "java", "com", "hi", "api",
                         *seg.split("."))
        if not os.path.isdir(d):
            continue                 # nothing converted in this checkout
        tracked = os.path.join(d, ".gitkeep")
        assert not os.path.exists(tracked), seg


def test_every_referenced_segment_is_classified():
    """The guard that catches the NEXT one.

    If a new bundled framework file imports a package nobody has
    classified, it is silently treated as pinned. That is the safe
    default only if the package really is committed; if it is a new
    GENERATED package, a non-default root breaks again exactly as
    before. Fail loudly instead.
    """
    unknown = []
    for seg in sorted(_referenced_segments()):
        if seg in GENERATED:
            continue
        d = os.path.join(ROOT, "src", "main", "java", "com", "hi", "api",
                         *seg.split("."))
        if not os.path.isdir(d):
            unknown.append(seg)
    assert not unknown, (
        "a bundled framework file references com.hi.api.%s, which is "
        "neither in _GENERATED_PACKAGE_SEGMENTS nor a committed package "
        "on disk -- classify it, or a non-default --package-root emits a "
        "broken import" % ", com.hi.api.".join(unknown))


def test_the_committed_packages_are_never_re_rooted():
    """The actual bug: these must still say com.hi.api after re-rooting."""
    pinned = ("auth", "config", "context", "data", "db", "domain",
              "rest.utilities")
    bad = []
    for f in _framework_files():
        moved = _reroot(
            io.open(f, encoding="utf-8", errors="replace").read(), NEW_ROOT)
        for seg in pinned:
            if NEW_ROOT + "." + seg in moved:
                bad.append("%s: %s.%s" % (os.path.basename(f), NEW_ROOT, seg))
    assert not bad, (
        "a non-default --package-root rewrote imports of committed "
        "packages that never move: " + "; ".join(sorted(set(bad))))


def test_the_generated_packages_ARE_re_rooted():
    """The other half -- a fix that moves nothing is also wrong, and
    would leave every framework file declaring the default package."""
    scenario = os.path.join(FRAMEWORK, "ImportedScenario.java")
    if not os.path.exists(scenario):
        return
    moved = _reroot(
        io.open(scenario, encoding="utf-8", errors="replace").read(), NEW_ROOT)
    assert ("package " + NEW_ROOT + ".support;") in moved, (
        "the framework file must declare the re-rooted support package")


def test_the_default_root_changes_nothing():
    """emit_framework_support skips the replace entirely at the default,
    so the bundled bytes must survive untouched."""
    for f in _framework_files():
        text = io.open(f, encoding="utf-8", errors="replace").read()
        assert _reroot(text, "com.hi.api") == text, os.path.basename(f)


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok  " + name)
            passed += 1
    print("%d passed" % passed)
