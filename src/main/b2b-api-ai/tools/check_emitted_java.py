"""The author-editable Java the converter carries is compiled by nothing.

WHY THIS GAP EXISTS
-------------------
`_AUTHOR_EDITABLE_BASENAMES` files are written SKIP-IF-EXISTS: a convert
into a tree that already has them leaves them alone. That is the point --
an author's edits survive. The consequence is that their SOURCE is never
exercised:

  * `verify_all --full` compiles `src/`, which holds the EMITTED copy, not
    the emitter's.
  * `check_generic` converts into the existing tree, so skip-if-exists
    skips them.
  * a convert into a clean tree WOULD write them, and that is the one
    situation nobody runs before pushing.

So a syntax error in the emitter's copy of PlaceholderResolver stays
invisible until someone clones the repository and converts -- at which
point the tree does not compile and the cause is a commit from weeks ago.

WHAT IS CHECKED
---------------
  inline      the four files the emitter holds as an f-string
              (AuthHelper, PerMethodCsvDataProvider, PlaceholderResolver,
              ProgressLogListener). Rendered by EVALUATING the emitter's
              own literal through the AST -- a regex extraction returns
              the literal's source text, where `\\uFEFF` and `\\"` are
              still escaped, and javac then reports errors that are
              artifacts of the extraction rather than faults in the
              emitter.
  file-backed the six under tools/ra_converter/framework/, compiled as
              they are.

NOT checked: whether the emitted copy and the framework copy agree. They
are author-editable, so divergence in a working tree is expected, and
Phase 1's `unpaired-framework-edit` invariant already governs editing one
without the other.

    python tools/check_emitted_java.py
"""
from __future__ import annotations

import ast
import glob
import io
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EMITTER = os.path.join(ROOT, "tools", "ra_converter", "ra_converter.py")
FRAMEWORK = os.path.join(ROOT, "tools", "ra_converter", "framework")
WORK = os.path.join(ROOT, "target", "emitted-java-check")
CP_FILE = os.path.join(ROOT, "target", "emitted-java-cp.txt")

# Discovered, not hardcoded: any emit_* that assigns `rel` ending in one
# of these basenames. A hardcoded file list is one more thing to keep in
# step, and this repository has already been bitten by an enumeration
# that fell behind what it enumerated.
PACKAGE_ROOT = "com.hi.api"

# Declared author-editable but reachable by neither mechanism here, each
# with the reason. This list is the whole point of the coverage assertion
# below: without it, an emitter refactor that broke rendering would leave
# `inline` empty and this check would still pass, quietly covering six
# files instead of ten. A guard that can shrink without saying so is the
# failure mode this repository keeps finding.
EXEMPT = {
    "ManualClient.java":
        "written by --bootstrap through a different code path (a %-format "
        "`stub_rel`, not an f-string `rel`) into COMMITTED space under "
        "rest/manual/, where maven compiles it whenever it is present. It "
        "is a scaffold meant to be edited beyond recognition, so the "
        "emitter's copy is not the thing worth compiling.",
}


class _Stub:
    """Stand-in for the Emitter while evaluating its literals."""

    package_root = PACKAGE_ROOT

    def __getattr__(self, name):          # unknown attributes read empty
        return ""


def author_editable_basenames() -> frozenset[str]:
    """Read `_AUTHOR_EDITABLE_BASENAMES` out of the emitter."""
    src = io.open(EMITTER, encoding="utf-8").read()
    i = src.find("_AUTHOR_EDITABLE_BASENAMES = frozenset({")
    if i < 0:
        return frozenset()
    block = src[i:src.index("})", i)]
    import re
    return frozenset(re.findall(r'"([^"]+\.java)"', block))


def render_inline(basenames: frozenset[str]) -> tuple[dict, list[str]]:
    """{basename: java source} for the author-editable inline files."""
    src = io.open(EMITTER, encoding="utf-8").read()
    tree = ast.parse(src)
    out: dict[str, str] = {}
    notes: list[str] = []

    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        ns: dict = {"self": _Stub()}
        rel = content = None
        for st in fn.body:
            if not (isinstance(st, ast.Assign) and len(st.targets) == 1
                    and isinstance(st.targets[0], ast.Name)):
                continue
            name = st.targets[0].id
            expr = ast.Expression(body=st.value)
            ast.fix_missing_locations(expr)
            try:
                ns[name] = eval(compile(expr, "<emitter>", "eval"), {}, ns)
            except Exception:
                continue        # a literal needing state we do not have
            if name == "rel":
                rel = ns[name]
            elif name == "content":
                content = ns[name]
        if not (isinstance(rel, str) and isinstance(content, str)):
            continue
        base = os.path.basename(rel)
        if base in basenames:
            out[base] = content
            notes.append(f"{fn.name} -> {base} ({content.count(chr(10))} lines)")
    return out, notes


def classpath() -> str:
    """Project dependencies, cached. Built with maven only when absent."""
    if os.path.isfile(CP_FILE):
        return io.open(CP_FILE, encoding="utf-8").read().strip()
    mvn = "mvn.cmd" if os.name == "nt" else "mvn"
    p = subprocess.run(
        [mvn, "-o", "-q", "dependency:build-classpath",
         f"-Dmdep.outputFile={CP_FILE}"],
        cwd=ROOT, capture_output=True, text=True,
        shell=(os.name == "nt"))
    if p.returncode != 0 or not os.path.isfile(CP_FILE):
        return ""
    return io.open(CP_FILE, encoding="utf-8").read().strip()


def main() -> int:
    basenames = author_editable_basenames()
    if not basenames:
        print("could not read _AUTHOR_EDITABLE_BASENAMES from the emitter")
        return 1
    print(f"author-editable basenames declared by the emitter: {len(basenames)}")

    inline, notes = render_inline(basenames)
    backed = sorted(glob.glob(os.path.join(FRAMEWORK, "*.java")))
    print(f"  rendered from an inline literal : {len(inline)}")
    for n in notes:
        print(f"      {n}")
    print(f"  file-backed under framework/    : {len(backed)}")

    # Coverage first. Compiling ten files proves nothing if the emitter
    # now holds eleven and this found four.
    covered = set(inline) | {os.path.basename(p) for p in backed}
    uncovered = sorted(basenames - covered - set(EXEMPT))
    for base, why in sorted(EXEMPT.items()):
        if base in basenames:
            print(f"  exempt: {base} -- {why}")
    if uncovered:
        print(f"\n{len(uncovered)} author-editable file(s) are declared by the "
              f"emitter and checked by nothing:")
        for b in uncovered:
            print(f"  {b}")
        print("Either it is emitted by a code path this check does not read "
              "(teach render_inline about it), or it is legitimately out of "
              "scope (add it to EXEMPT with the reason). Leaving it silent is "
              "how this check stops covering what it claims to.")
        return 1

    if not inline and not backed:
        print("\nnothing to compile")
        return 0

    if os.path.isdir(WORK):
        shutil.rmtree(WORK, ignore_errors=True)
    srcs: list[str] = []
    for base, content in sorted(inline.items()):
        # Package dir must match the declared package or javac objects.
        d = os.path.join(WORK, *PACKAGE_ROOT.split("."), _pkg_tail(content))
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, base)
        io.open(p, "w", encoding="utf-8", newline="\n").write(content)
        srcs.append(p)
    for p in backed:
        content = io.open(p, encoding="utf-8", errors="replace").read()
        d = os.path.join(WORK, *PACKAGE_ROOT.split("."), _pkg_tail(content))
        os.makedirs(d, exist_ok=True)
        q = os.path.join(d, os.path.basename(p))
        shutil.copyfile(p, q)
        srcs.append(q)

    cp = classpath()
    if not cp:
        print("\ncould not resolve a classpath (maven offline build-classpath "
              "failed), so nothing was compiled. Reported as a failure rather "
              "than a pass: the point of this check is that nothing else "
              "compiles these files.")
        return 1
    # Resolve the project's own classes FROM SOURCE, not from a previous
    # build. These files import com.hi.api.config.Config,
    # com.hi.api.data.FakeData, RestLoggerUtilityDataHolder and friends.
    # Relying on target/classes passed on a machine that had already built
    # and failed on a fresh tree with "package com.hi.api.config does not
    # exist" -- an artifact of build order (this check runs BEFORE
    # java-compile), reported as the emitter's Java being broken.
    #
    # -sourcepath lets javac compile what it needs on demand, so the check
    # answers its own question without depending on anything else having
    # run first. target/classes is still offered when present, which makes
    # the common case faster.
    classes = os.path.join(ROOT, "target", "classes")
    full = cp + (os.pathsep + classes if os.path.isdir(classes) else "")
    sourcepath = os.path.join(ROOT, "src", "main", "java")

    out_dir = os.path.join(ROOT, "target", "emitted-java-classes")
    shutil.rmtree(out_dir, ignore_errors=True)
    os.makedirs(out_dir, exist_ok=True)
    javac = [os.environ.get("JAVAC", "javac"), "-nowarn", "-cp", full,
             "-sourcepath", sourcepath + os.pathsep + WORK,
             "-d", out_dir] + srcs
    p = subprocess.run(javac, cwd=ROOT, capture_output=True, text=True)
    text = ((p.stdout or "") + (p.stderr or "")).strip()

    if p.returncode == 0:
        print(f"\n{len(srcs)} author-editable file(s) compile. A clean "
              f"convert will produce a tree that builds.")
        return 0
    print(f"\nTHE EMITTER'S OWN JAVA DOES NOT COMPILE. A clean convert would "
          f"produce a tree that does not build, and nothing else would say so:")
    for line in text.splitlines()[:30]:
        print(f"  {line}")
    return 1


def _pkg_tail(java_src: str) -> str:
    """Directory tail from the file's `package` line, below PACKAGE_ROOT."""
    for line in java_src.splitlines():
        line = line.strip()
        if line.startswith("package ") and line.endswith(";"):
            pkg = line[len("package "):-1].strip()
            if pkg.startswith(PACKAGE_ROOT + "."):
                return os.path.join(*pkg[len(PACKAGE_ROOT) + 1:].split("."))
            return ""
    return ""


if __name__ == "__main__":
    sys.exit(main())
