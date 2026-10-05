"""No tool module may use a name it never defines or imports.

WHY THIS EXISTS
---------------
`_record_convert_scope` in ra_converter.py was written with `io.open(...)`
in a module that imports no `io`. Python binds names at call time, so the
file imported fine, every test passed, and the gate was green. The failure
arrived on a user's machine, mid-convert, AFTER 40 files had been written
and the suite inventory had printed clean:

    NameError: name 'io' is not defined

Nothing could have caught it except executing that line, and nothing did:
the only coverage was of the READING side -- the marker file was
hand-written in a test and the gate parsed it. A path no test executes is
a path that ships broken.

Tests are the real answer and they now exist. This is the cheap net under
them, because the next unexecuted line will be written by someone who did
not read this comment.

    python tools/check_python_defs.py [--root .]

Uses pyflakes when it is installed, and says so plainly when it is not --
a check that quietly verifies nothing is worse than no check, so it exits
0 with a `nothing to check` line that `verify_all` reports as SKIP rather
than PASS.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

# Only the tooling. The emitted tree is Java, and `tools/ra_converter/
# input` holds customer XML.
SCAN_DIRS = ("tools",)
SKIP_PARTS = ("__pycache__", os.sep + "input" + os.sep, "_snapshot",
              "single_input")


def py_files(root: str):
    for base in SCAN_DIRS:
        top = os.path.join(root, base)
        if not os.path.isdir(top):
            continue
        for dirpath, _dirs, files in os.walk(top):
            for fn in sorted(files):
                if not fn.endswith(".py"):
                    continue
                p = os.path.join(dirpath, fn)
                if any(s in p for s in SKIP_PARTS):
                    continue
                yield p


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=".")
    args = ap.parse_args()

    files = list(py_files(args.root))
    if not files:
        print("check_python_defs: no tool modules found -- nothing to check")
        return 0

    try:
        import pyflakes  # noqa: F401
    except ImportError:
        print("check_python_defs: pyflakes is not installed -- nothing to "
              "check. `pip install pyflakes` to enable it. The tests that "
              "execute these paths are the real guard; this is the net "
              "under them.")
        return 0

    proc = subprocess.run([sys.executable, "-m", "pyflakes", *files],
                          capture_output=True, text=True)
    out = (proc.stdout or "") + (proc.stderr or "")

    # ONLY undefined names. pyflakes also reports unused imports and
    # f-strings without placeholders; those are style, they are already
    # numerous in this tree, and failing a gate on them would train
    # everyone to ignore it.
    bad = [l for l in out.splitlines() if "undefined name" in l.lower()]
    print(f"check_python_defs: {len(files)} tool module(s) scanned")
    if bad:
        print()
        for l in bad:
            print("  FAIL  " + l.strip())
        print(f"\n{len(bad)} undefined name(s). Each one is a NameError that "
              f"fires only when that line runs -- which may be on someone "
              f"else's machine, mid-convert, after work has been written.")
        return 1
    print("No tool module uses a name it does not define or import.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
