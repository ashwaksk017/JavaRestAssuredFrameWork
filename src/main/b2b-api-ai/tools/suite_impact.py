"""Which suites does a converter change actually move?

    python tools/suite_impact.py --suites eadkafkaevents,partialgoalregression
    python tools/suite_impact.py --all                      # every input XML
    python tools/suite_impact.py --suites mfrstage --base 944d4a3

WHY
---
Each suite now has its own generated classes, so converting one suite
cannot break another. That removes one cause of "fixed one suite, broke
another" and leaves the other untouched: the suites share one CONVERTER.
A translator rule written for one suite's Groovy fires on every suite's
Groovy. Nothing fails when that happens -- the other suite converts, and
compiles, and sends a different request.

So the question worth asking before a commit is not "did my suite get
fixed" but "what ELSE moved". This answers it by measurement:

1. the converter as committed at --base (default HEAD) goes into one
   scratch copy of the module, the working tree into another;
2. the same suites are converted in both, cold, with the same arguments;
3. the generated output is compared suite by suite.

A suite you did not mean to touch that shows up here is the finding.

It is OPT-IN and never part of the gate, because it converts every named
suite twice. A small suite takes seconds; the largest take minutes; --all
is the whole input directory, twice, and was roughly 25 minutes a side on
the machine this was written on. Name the suites the change is for plus a
few it should NOT affect.

WHAT IS COMPARED, AND WHAT IS MASKED
------------------------------------
Spec and hook numbering is positional: add one spec early in a suite and
every later `spec38` becomes `spec39`, in every file that names it.
Comparing text would report the whole suite as changed. So:

* `<Suite>Specs<N>` / `<Suite>Hooks<N>` are read as a SET of method
  bodies with the numbers masked. A body that is in one tree and not the
  other is a real change; a body that only moved is not.
* every other file is compared as text with the same masking.

Masking can hide two specs swapping places. That does not change what a
case runs -- the registration names its spec, and both sides are masked
alike -- but it is the limit of what this proves.

Exit code is 0 unless --fail-on-unexpected is given and a suite outside
--expect changed.
"""
from __future__ import annotations

import argparse
import glob
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable

# Positional numbering, masked on both sides before comparing.
_MASKS = (
    (re.compile(r"\b(\w*?Specs)\d+\b"), r"\1#"),
    (re.compile(r"\b(\w*?Hooks)\d+\b"), r"\1#"),
    (re.compile(r"\bspec\d+\b"), "spec#"),
    (re.compile(r"\bhook\d+_"), "hook#_"),
)
_CHUNK_RX = re.compile(r"^\w*?(Specs|Hooks)\d+\.java$")
_SPEC_BODY_RX = re.compile(
    r"static PhaseSpec spec\d+\(\)\s*\{(.*?)\n    \}", re.S)
_HOOK_SPLIT_RX = re.compile(r"\n    (?:@SuppressWarnings\(\"unused\"\)\s*\n    )?static void hook\d+_")

# Where one suite's generated output lives, relative to the module.
_SUITE_DIRS = (
    ("java", "src/main/java/com/hi/api/support/{suite}"),
    ("java", "src/main/java/com/hi/api/templates/{suite}"),
    ("test", "src/test/java/com/hi/api/tests/imported/{suite}"),
    ("csv", "src/test/resources/csv/{suite}"),
    ("template", "src/main/resources/templates/{suite}"),
)
# Generated files every suite shares. A change here reaches all of them.
_SHARED = (
    "src/main/java/com/hi/api/support/scenario",
    "src/main/java/com/hi/api/support/ImportedRestClient.java",
)


def mask(text: str) -> str:
    for rx, sub in _MASKS:
        text = rx.sub(sub, text)
    return text


def _read(path: str) -> str:
    with io.open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _files(base: str) -> dict:
    """{relative path: absolute path} under `base` (a file or a directory)."""
    if os.path.isfile(base):
        return {os.path.basename(base): base}
    out = {}
    for dirpath, _dirs, names in os.walk(base):
        for n in names:
            if n.endswith(".stale.orig"):
                continue
            full = os.path.join(dirpath, n)
            out[os.path.relpath(full, base).replace(os.sep, "/")] = full
    return out


def chunk_bodies(directory: str, kind: str) -> list:
    """Masked method bodies of every `<prefix>{kind}<N>.java` under `directory`.

    A list, not a set: two cases can carry an identical hook, and losing
    one of two identical bodies is still a change.
    """
    bodies = []
    for f in glob.glob(os.path.join(directory, "**", "*%s*.java" % kind),
                       recursive=True):
        m = _CHUNK_RX.match(os.path.basename(f))
        if not m or m.group(1) != kind:
            continue
        src = _read(f)
        if kind == "Specs":
            bodies += [mask(b.strip()) for b in _SPEC_BODY_RX.findall(src)]
        else:
            parts = _HOOK_SPLIT_RX.split(src)
            bodies += [mask("hook#_" + p.rstrip().rstrip("}").rstrip())
                       for p in parts[1:]]
    return sorted(bodies)


def _multiset_diff(a: list, b: list) -> tuple:
    """(only in a, only in b) as counts, respecting duplicates."""
    import collections
    ca, cb = collections.Counter(a), collections.Counter(b)
    return (sum((ca - cb).values()), sum((cb - ca).values()))


def compare_suite(base_root: str, work_root: str, suite: str) -> dict:
    """What differs for one suite between two converted trees."""
    out = {"suite": suite, "changed": [], "added": [], "removed": [],
           "specs": (0, 0), "hooks": (0, 0), "by_kind": {}}
    for kind, rel in _SUITE_DIRS:
        rel = rel.format(suite=suite)
        fa = _files(os.path.join(base_root, rel)) if os.path.exists(
            os.path.join(base_root, rel)) else {}
        fb = _files(os.path.join(work_root, rel)) if os.path.exists(
            os.path.join(work_root, rel)) else {}
        # chunk files are compared as bodies below, never as files
        norm = lambda d: {mask(k): v for k, v in d.items()
                          if not _CHUNK_RX.match(os.path.basename(k))}
        fa, fb = norm(fa), norm(fb)
        for k in sorted(set(fa) | set(fb)):
            where = rel + "/" + k
            if k not in fb:
                out["removed"].append(where)
            elif k not in fa:
                out["added"].append(where)
            elif mask(_read(fa[k])) != mask(_read(fb[k])):
                out["changed"].append(where)
            else:
                continue
            out["by_kind"][kind] = out["by_kind"].get(kind, 0) + 1
    cases_a = os.path.join(base_root, "src/main/java/com/hi/api/support", suite)
    cases_b = os.path.join(work_root, "src/main/java/com/hi/api/support", suite)
    out["specs"] = _multiset_diff(chunk_bodies(cases_a, "Specs"),
                                  chunk_bodies(cases_b, "Specs"))
    out["hooks"] = _multiset_diff(chunk_bodies(cases_a, "Hooks"),
                                  chunk_bodies(cases_b, "Hooks"))
    out["moved"] = bool(out["changed"] or out["added"] or out["removed"]
                        or any(out["specs"]) or any(out["hooks"]))
    return out


def compare_shared(base_root: str, work_root: str) -> list:
    changed = []
    for rel in _SHARED:
        fa = _files(os.path.join(base_root, rel)) if os.path.exists(
            os.path.join(base_root, rel)) else {}
        fb = _files(os.path.join(work_root, rel)) if os.path.exists(
            os.path.join(work_root, rel)) else {}
        is_file = (os.path.isfile(os.path.join(work_root, rel))
                   or os.path.isfile(os.path.join(base_root, rel)))
        for k in sorted(set(fa) | set(fb)):
            if k not in fa or k not in fb or mask(_read(fa[k])) != mask(_read(fb[k])):
                changed.append(rel if is_file else rel + "/" + k)
    return changed


# ---------------------------------------------------------------- scratch

def _git(*args, cwd=None) -> str:
    return subprocess.run(["git"] + list(args), cwd=cwd or ROOT, check=True,
                          capture_output=True, text=True).stdout


def _repo_root() -> str:
    return _git("rev-parse", "--show-toplevel").strip()


def _module_rel() -> str:
    return os.path.relpath(ROOT, _repo_root()).replace(os.sep, "/")


def _extract(rev: str, dest: str) -> str:
    """The module as committed at `rev`, extracted under `dest`."""
    rel = _module_rel()
    tar_path = os.path.join(dest, "module.tar")
    with open(tar_path, "wb") as fh:
        subprocess.run(["git", "archive", rev, rel], cwd=_repo_root(),
                       check=True, stdout=fh)
    with tarfile.open(tar_path) as tf:
        try:
            tf.extractall(dest, filter="data")
        except TypeError:               # Python < 3.12 has no filter argument
            tf.extractall(dest)
    os.remove(tar_path)
    return os.path.join(dest, *rel.split("/"))


def _overlay_working_tree(module: str) -> int:
    """Copy tracked-but-modified and untracked files over a HEAD extract."""
    repo, rel = _repo_root(), _module_rel()
    names = set()
    # --untracked-files=all: the default lists a new DIRECTORY as one entry,
    # which is not a file and would be skipped -- a new tool or test in a
    # new folder would silently not be part of the "work" tree.
    for line in _git("status", "--porcelain", "--untracked-files=all",
                     "--", rel, cwd=repo).splitlines():
        path = line[3:].strip().strip('"')
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        names.add(path)
    n = 0
    for path in sorted(names):
        src = os.path.join(repo, path)
        dst = os.path.join(module, os.path.relpath(path, rel))
        if os.path.isfile(src):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)
            n += 1
        elif not os.path.exists(src) and os.path.isfile(dst):
            os.remove(dst)              # deleted in the working tree
            n += 1
    return n


def _convert(module: str, xmls: list, data_dir, log: str) -> int:
    cmd = [PY, os.path.join("tools", "ra_converter", "ra_converter.py"),
           "--input", ",".join(xmls) if len(xmls) > 1 else xmls[0],
           "--output", ".", "--package-root", "com.hi.api", "--clean",
           "--max-name-len", "40", "--no-cursor-assist", "--skip-self-test"]
    if data_dir:
        cmd += ["--data-dir", data_dir]
    env = dict(os.environ, RA_CONVERTER_TIMESTAMP="2000-01-01 00:00:00Z",
               PYTHONHASHSEED="0")
    with open(log, "w", encoding="utf-8") as fh:
        return subprocess.run(cmd, cwd=module, env=env, stdout=fh,
                              stderr=subprocess.STDOUT).returncode


def _suite_of(xml: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_",
                  os.path.splitext(os.path.basename(xml))[0].lower()).strip("_")


def _resolve_suites(arg, input_dir: str) -> list:
    xmls = sorted(glob.glob(os.path.join(input_dir, "*.xml")))
    if arg is None:
        return xmls
    by_suite = {_suite_of(x): x for x in xmls}
    picked, missing = [], []
    for name in [s.strip() for s in arg.split(",") if s.strip()]:
        key = _suite_of(name)
        (picked if key in by_suite else missing).append(by_suite.get(key, name))
    if missing:
        raise SystemExit("[suite-impact] no input XML for: %s (looked in %s)"
                         % (", ".join(missing), input_dir))
    return picked


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--suites", help="comma-separated suite names to convert "
                   "in both trees")
    g.add_argument("--all", action="store_true",
                   help="every XML in the input directory, twice (slow)")
    ap.add_argument("--base", default="HEAD",
                    help="commit to compare the working tree against")
    ap.add_argument("--input", default=os.path.join(ROOT, "tools", "ra_converter", "input"))
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--expect", default="",
                    help="suites this change is MEANT to move")
    ap.add_argument("--fail-on-unexpected", action="store_true",
                    help="exit 1 when a suite outside --expect moved")
    ap.add_argument("--keep", action="store_true",
                    help="keep the two scratch trees for inspection")
    args = ap.parse_args(argv)

    xmls = _resolve_suites(None if args.all else args.suites, args.input)
    suites = [_suite_of(x) for x in xmls]
    expect = {_suite_of(s) for s in args.expect.split(",") if s.strip()}
    print("[suite-impact] %d suite(s), converted twice: %s"
          % (len(suites), ", ".join(suites[:8]) + (" ..." if len(suites) > 8 else "")))
    print("[suite-impact] base = %s, compared with the working tree"
          % _git("rev-parse", "--short", args.base).strip())

    scratch = tempfile.mkdtemp(prefix="si")
    try:
        trees = {}
        for label, rev in (("base", args.base), ("work", "HEAD")):
            dest = os.path.join(scratch, label)
            os.makedirs(dest)
            module = _extract(rev, dest)
            if label == "work":
                n = _overlay_working_tree(module)
                print("[suite-impact] working tree: %d uncommitted file(s) "
                      "applied over HEAD" % n)
            t0 = time.time()
            rc = _convert(module, xmls, args.data_dir,
                          os.path.join(scratch, label + ".log"))
            print("[suite-impact] %s convert: exit %d in %ds"
                  % (label, rc, int(time.time() - t0)))
            if rc != 0:
                print("[suite-impact] the %s convert FAILED -- see %s"
                      % (label, os.path.join(scratch, label + ".log")))
                args.keep = True
                return 2
            trees[label] = module

        moved, unexpected = [], []
        print()
        for suite in suites:
            r = compare_suite(trees["base"], trees["work"], suite)
            if not r["moved"]:
                print("  unchanged  %s" % suite)
                continue
            moved.append(suite)
            if expect and suite not in expect:
                unexpected.append(suite)
            kinds = ", ".join("%d %s" % (n, k) for k, n in sorted(r["by_kind"].items()))
            print("  MOVED      %s%s" % (suite, "   <-- NOT in --expect"
                                         if suite in unexpected else ""))
            print("             files: %d changed, %d added, %d removed%s"
                  % (len(r["changed"]), len(r["added"]), len(r["removed"]),
                     (" (" + kinds + ")") if kinds else ""))
            print("             spec bodies: %d gone, %d new    hook bodies: "
                  "%d gone, %d new" % (r["specs"] + r["hooks"]))
            for f in (r["changed"] + r["added"] + r["removed"])[:5]:
                print("               " + f)
        shared = compare_shared(trees["base"], trees["work"])
        print()
        if shared:
            print("  SHARED generated files changed (these reach every suite):")
            for f in shared[:10]:
                print("    " + f)
        else:
            print("  shared generated files: unchanged")
        print()
        print("[suite-impact] %d of %d suite(s) moved%s"
              % (len(moved), len(suites),
                 (": " + ", ".join(moved)) if moved else ""))
        if args.keep:
            print("[suite-impact] trees kept under " + scratch)
        if unexpected and args.fail_on_unexpected:
            print("[suite-impact] moved but not expected: " + ", ".join(unexpected))
            return 1
        return 0
    finally:
        if not args.keep:
            shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
