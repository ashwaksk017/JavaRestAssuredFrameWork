"""No emitted class may declare the same method twice.

WHY THIS EXISTS
---------------
Nothing checked this. `method-name-length` unit-tests the name BUILDER
-- truncation, camelCase, disambiguation after truncation -- against
strings it makes up. It never reads the emitted tree, so a collision
that survives the builder reaches javac and nothing before it.

That is not hypothetical. The same class of fault already shipped one
level down: a classic convert failed with `variable httpRequest2002Headers
is already defined`, because `to_camel_case` is lossy and two step names
that `sanitize_identifier` keeps apart collapsed into one identifier.
Locals were caught by the compiler at --full. Methods would be too --
but only if someone runs --full, and only after a 40-minute convert.

This reads the tree directly and costs a second.

    python tools/check_no_duplicate_methods.py [--root .]

Overloads are legal, so the key is name + parameter TYPES, not name
alone. Only declarations at class-body depth count: a method inside a
nested or anonymous class legitimately repeats an outer name, and
counting those would make this cry wolf until someone switched it off.
"""
from __future__ import annotations

import argparse
import os
import re
import sys

SCAN = (os.path.join("src", "main", "java"),
        os.path.join("src", "test", "java"))

# `public void foo(Map<String, String> row) throws Exception {`
# `private static String bar(int a, String b) {`
# `GetInventoryTest() {`  (constructor)
DECL = re.compile(
    r"^\s*(?:@\w+\s+)*"
    r"(?:(?:public|protected|private|static|final|abstract|synchronized|"
    r"native|default|strictfp)\s+)*"
    r"(?:<[^>]+>\s+)?"
    r"(?:[\w.$<>\[\], ?]+\s+)?"
    r"(\w+)\s*\(([^)]*)\)\s*"
    r"(?:throws\s+[\w.$, ]+)?\s*\{\s*$")

NOT_A_METHOD = {"if", "for", "while", "switch", "catch", "synchronized",
                "try", "do", "else", "return", "new", "case"}


def param_types(raw: str) -> str:
    """Parameter TYPES only, so overloads stay distinct and renamed
    parameters do not read as a new method."""
    raw = raw.strip()
    if not raw:
        return ""
    parts, depth, cur = [], 0, ""
    for ch in raw:
        if ch in "<([":
            depth += 1
        elif ch in ">)]":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    out = []
    for p in parts:
        p = p.strip().replace("final ", "")
        if not p:
            continue
        # drop the parameter NAME: last whitespace-separated token
        bits = p.rsplit(" ", 1)
        out.append(bits[0].strip() if len(bits) == 2 else p)
    return ",".join(re.sub(r"\s+", "", t) for t in out)


def methods_of(path: str):
    """(name, paramtypes, line) for each method declared in the class
    body, i.e. at brace depth 1."""
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            lines = fh.read().split("\n")
    except OSError:
        return
    depth = 0
    in_block_comment = False
    for n, line in enumerate(lines, 1):
        stripped = line.strip()
        if in_block_comment:
            if "*/" in stripped:
                in_block_comment = False
            continue
        if stripped.startswith("/*"):
            if "*/" not in stripped:
                in_block_comment = True
            continue
        if not stripped.startswith("//"):
            m = DECL.match(line)
            if m and depth == 1 and m.group(1) not in NOT_A_METHOD:
                yield m.group(1), param_types(m.group(2)), n
        depth += line.count("{") - line.count("}")


def java_files(root: str):
    for sub in SCAN:
        base = os.path.join(root, sub)
        if not os.path.isdir(base):
            continue
        for dirpath, _d, files in os.walk(base):
            for fn in sorted(files):
                if fn.endswith(".java"):
                    yield os.path.join(dirpath, fn)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=".")
    args = ap.parse_args()

    files = list(java_files(args.root))
    if not files:
        print("check_no_duplicate_methods: no emitted Java found -- "
              "nothing to check")
        return 0

    problems, scanned = [], 0
    for path in files:
        scanned += 1
        seen: dict = {}
        for name, types, line in methods_of(path):
            key = (name, types)
            if key in seen:
                rel = os.path.relpath(path, args.root).replace(os.sep, "/")
                problems.append(
                    "%s declares %s(%s) twice -- lines %d and %d"
                    % (rel, name, types, seen[key], line))
            else:
                seen[key] = line

    print("check_no_duplicate_methods: %d emitted file(s) scanned"
          % scanned)
    if problems:
        print()
        for p in problems[:40]:
            print("  FAIL  " + p)
        if len(problems) > 40:
            print("  ... and %d more" % (len(problems) - 40))
        print("\n%d duplicate method declaration(s). This does not "
              "compile, and the convert that produced it exited 0."
              % len(problems))
        return 1
    print("No emitted class declares the same method signature twice.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
