"""Which suites does a converter change actually move?

WHY
---
Each suite has its own generated classes, so converting one suite cannot
break another. That removes one cause of "fixed one suite, broke another"
and leaves the other untouched: the suites share one CONVERTER. A
translator rule written for one suite's Groovy fires on every suite's.
Nothing fails when that happens -- the other suite converts, and compiles,
and sends a different request.

So the question after a converter change is not "did my suite get fixed"
but "what ELSE moved". This module answers it two ways.

1. EVERY CONVERT, for free. The converter fingerprints each suite's
   generated output when it finishes and compares it with the fingerprint
   that suite's PREVIOUS convert left in `_audit/fingerprints/`. The last
   lines of the run say which suites moved, and `verify_all` repeats it.
   Nothing is converted twice; the comparison is of two files of hashes.

2. BEFORE A COMMIT, on request: `python tools/verify_all.py --impact a,b,c`
   converts the named suites with the converter at HEAD and with the
   working tree, in two scratch copies of the module, and compares those.
   It touches nothing in your tree. It converts each named suite twice, so
   it is asked for, never run by default.

A suite that moved and that the change was not meant for is the finding.

WHAT IS COMPARED, AND WHAT IS MASKED
------------------------------------
Spec and hook numbering is positional: add one spec early in a suite and
every later `spec38` becomes `spec39`, in every file that names it.
Comparing text would report the whole suite as changed. So:

* `<Suite>Specs<N>` / `<Suite>Hooks<N>` are read as a MULTISET of method
  bodies with the numbers masked. A body in one side and not the other is
  a real change; a body that only moved is not.
* every other file is compared as text with the same masking.

Masking can hide two specs swapping places. That does not change what a
case runs -- the registration names its spec, and both sides are masked
alike -- but it is the limit of what this proves.

A fingerprint also records what it was made FROM: a hash of the converter
sources and of the input XML. A suite can move because the converter
changed, because its XML changed, or both, and the report says which.
"""
from __future__ import annotations

import argparse
import collections
import glob
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))          # the module
PY = sys.executable

FINGERPRINT_DIR_REL = "_audit/fingerprints"
REPORT_REL = "_audit/suite_impact.json"

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
_HOOK_SPLIT_RX = re.compile(
    r"\n    (?:@SuppressWarnings\(\"unused\"\)\s*\n    )?static void hook\d+_")

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


def _lp(path: str) -> str:
    """Windows long-path form, so a deep generated tree can be read."""
    if os.name != "nt":
        return path
    p = os.path.abspath(path)
    return p if p.startswith("\\\\?\\") else "\\\\?\\" + p


def mask(text: str) -> str:
    for rx, sub in _MASKS:
        text = rx.sub(sub, text)
    return text


def _read(path: str) -> str:
    with io.open(_lp(path), encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _sha(text: str) -> str:
    # Line endings are not a change: the same convert writes \n here and a
    # checkout may hand back \r\n.
    return hashlib.sha1(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()[:16]


def _files(base: str) -> dict:
    """{relative path: absolute path} under `base` (a file or a directory)."""
    if os.path.isfile(_lp(base)):
        return {os.path.basename(base): base}
    out = {}
    for dirpath, _dirs, names in os.walk(_lp(base)):
        for n in names:
            if n.endswith(".stale.orig"):
                continue
            full = os.path.join(dirpath, n)
            rel = os.path.relpath(full, _lp(base)).replace(os.sep, "/")
            out[rel] = full
    return out


def chunk_bodies(directory: str, kind: str) -> list:
    """Masked method bodies of every `<prefix>{kind}<N>.java` under `directory`.

    A list, not a set: two cases can carry an identical hook, and losing
    one of two identical bodies is still a change.
    """
    bodies = []
    for dirpath, _dirs, names in os.walk(_lp(directory)):
        for n in names:
            m = _CHUNK_RX.match(n)
            if not m or m.group(1) != kind:
                continue
            src = _read(os.path.join(dirpath, n))
            if kind == "Specs":
                bodies += [mask(b.strip()) for b in _SPEC_BODY_RX.findall(src)]
            else:
                parts = _HOOK_SPLIT_RX.split(src)
                bodies += [mask("hook#_" + p.rstrip().rstrip("}").rstrip())
                           for p in parts[1:]]
    return sorted(bodies)


def fingerprint(module_root: str, suite: str) -> dict:
    """Everything `compare` needs to know about one suite's generated output.

    Hashes only, never content: the output holds request bodies and data
    rows, and this file is kept beside the audit.
    """
    files = {}
    for kind, rel in _SUITE_DIRS:
        rel = rel.format(suite=suite)
        base = os.path.join(module_root, rel)
        if not os.path.exists(_lp(base)):
            continue
        for k, full in _files(base).items():
            if _CHUNK_RX.match(os.path.basename(k)):
                continue                # compared as bodies, never as files
            files[rel + "/" + mask(k)] = [kind, _sha(mask(_read(full)))]
    cases = os.path.join(module_root, "src/main/java/com/hi/api/support", suite)
    return {
        "schema": 1,
        "suite": suite,
        "files": files,
        "specs": [_sha(b) for b in chunk_bodies(cases, "Specs")],
        "hooks": [_sha(b) for b in chunk_bodies(cases, "Hooks")],
    }


def _multiset_diff(a: list, b: list) -> tuple:
    """(only in a, only in b) as counts, respecting duplicates."""
    ca, cb = collections.Counter(a), collections.Counter(b)
    return (sum((ca - cb).values()), sum((cb - ca).values()))


def compare(old: dict, new: dict) -> dict:
    """What differs between two fingerprints of the same suite."""
    out = {"suite": new.get("suite") or old.get("suite"),
           "changed": [], "added": [], "removed": [], "by_kind": {}}
    fa, fb = old.get("files") or {}, new.get("files") or {}
    for k in sorted(set(fa) | set(fb)):
        if k not in fb:
            out["removed"].append(k)
            kind = fa[k][0]
        elif k not in fa:
            out["added"].append(k)
            kind = fb[k][0]
        elif fa[k][1] != fb[k][1]:
            out["changed"].append(k)
            kind = fb[k][0]
        else:
            continue
        out["by_kind"][kind] = out["by_kind"].get(kind, 0) + 1
    out["specs"] = _multiset_diff(old.get("specs") or [], new.get("specs") or [])
    out["hooks"] = _multiset_diff(old.get("hooks") or [], new.get("hooks") or [])
    out["moved"] = bool(out["changed"] or out["added"] or out["removed"]
                        or any(out["specs"]) or any(out["hooks"]))
    return out


def compare_suite(base_root: str, work_root: str, suite: str) -> dict:
    """What differs for one suite between two converted trees."""
    return compare(fingerprint(base_root, suite), fingerprint(work_root, suite))


def compare_shared(base_root: str, work_root: str) -> list:
    changed = []
    for rel in _SHARED:
        pa, pb = os.path.join(base_root, rel), os.path.join(work_root, rel)
        fa = _files(pa) if os.path.exists(_lp(pa)) else {}
        fb = _files(pb) if os.path.exists(_lp(pb)) else {}
        is_file = os.path.isfile(_lp(pa)) or os.path.isfile(_lp(pb))
        for k in sorted(set(fa) | set(fb)):
            if k not in fa or k not in fb or \
                    _sha(mask(_read(fa[k]))) != _sha(mask(_read(fb[k]))):
                changed.append(rel if is_file else rel + "/" + k)
    return changed


def describe(result: dict) -> list:
    """The two detail lines printed under a suite that moved."""
    kinds = ", ".join("%d %s" % (n, k) for k, n in sorted(result["by_kind"].items()))
    return [
        "files: %d changed, %d added, %d removed%s"
        % (len(result["changed"]), len(result["added"]), len(result["removed"]),
           (" (" + kinds + ")") if kinds else ""),
        "spec bodies: %d gone, %d new    hook bodies: %d gone, %d new"
        % (tuple(result["specs"]) + tuple(result["hooks"])),
    ]


# ----------------------------------------------- recorded on every convert

def converter_rev(converter_dir: str = HERE) -> str:
    """A hash of the converter as it is on disk right now.

    Sources only -- the .py files and the bundled framework -- so a suite
    can be told apart as "moved because the converter changed" or "moved
    although it did not". Not a git revision: an uncommitted edit is the
    usual reason anyone is looking at this.
    """
    h = hashlib.sha1()
    names = sorted(glob.glob(os.path.join(converter_dir, "*.py"))
                   + glob.glob(os.path.join(converter_dir, "framework", "*.java")))
    for path in names:
        base = os.path.basename(path)
        if base.startswith("test_") or base.startswith("_tmp"):
            continue
        h.update(base.encode("utf-8"))
        with open(path, "rb") as fh:
            h.update(fh.read().replace(b"\r\n", b"\n"))
    return h.hexdigest()[:12]


def _file_sha(path: str) -> str:
    try:
        h = hashlib.sha1()
        with open(_lp(path), "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                h.update(block)
        return h.hexdigest()[:12]
    except OSError:
        return ""


def record_convert(module_root: str, suites_to_xml: dict, extra: dict | None = None) -> dict:
    """Fingerprint each converted suite, compare with its previous convert.

    Returns the report and writes it to REPORT_REL. `suites_to_xml` maps a
    suite name to the XML it was converted from.

    The FIRST convert of a suite has nothing to be compared with and is
    recorded as a baseline. That is said, not hidden: "unchanged" would be
    a claim nothing here can support.
    """
    fdir = os.path.join(module_root, *FINGERPRINT_DIR_REL.split("/"))
    os.makedirs(_lp(fdir), exist_ok=True)
    rev = converter_rev()
    report = {"schema": 1, "converter": rev, "suites": {},
              "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    for suite in sorted(suites_to_xml):
        new = fingerprint(module_root, suite)
        new["converter"] = rev
        new["input"] = _file_sha(suites_to_xml[suite] or "")
        new.update(extra or {})
        path = os.path.join(fdir, suite + ".json")
        old = None
        try:
            with io.open(_lp(path), encoding="utf-8") as fh:
                old = json.load(fh)
        except (OSError, ValueError):
            old = None
        if old is None:
            entry = {"status": "baseline"}
        else:
            r = compare(old, new)
            entry = {
                "status": "moved" if r["moved"] else "unchanged",
                "converter_changed": old.get("converter") != rev,
                "input_changed": bool(old.get("input")) and bool(new["input"])
                and old.get("input") != new["input"],
                "options_changed": sorted(
                    k for k in (extra or {}) if old.get(k) != new.get(k)),
                "detail": describe(r) if r["moved"] else [],
                "sample": (r["changed"] + r["added"] + r["removed"])[:5],
            }
        report["suites"][suite] = entry
        with io.open(_lp(path), "w", encoding="utf-8", newline="\n") as fh:
            json.dump(new, fh, indent=0, sort_keys=True)
            fh.write("\n")
    with io.open(_lp(os.path.join(module_root, *REPORT_REL.split("/"))), "w",
                 encoding="utf-8", newline="\n") as fh:
        json.dump(report, fh, indent=1, sort_keys=True)
        fh.write("\n")
    return report


def render_report(report: dict, tag: str = "[suite-impact]") -> list:
    """The lines a convert prints, and verify_all repeats."""
    suites = report.get("suites") or {}
    moved = [s for s, e in suites.items() if e.get("status") == "moved"]
    same = [s for s, e in suites.items() if e.get("status") == "unchanged"]
    first = [s for s, e in suites.items() if e.get("status") == "baseline"]
    lines = []
    if not suites:
        return lines
    lines.append("%s %d suite(s) converted: %d moved since their previous "
                 "convert, %d unchanged, %d with no previous convert to "
                 "compare." % (tag, len(suites), len(moved), len(same), len(first)))
    for s in sorted(moved):
        e = suites[s]
        why = []
        if e.get("converter_changed"):
            why.append("the converter changed")
        if e.get("input_changed"):
            why.append("its XML changed")
        if e.get("options_changed"):
            why.append("options changed (%s)" % ", ".join(e["options_changed"]))
        lines.append("%s   MOVED  %-40s %s" % (
            tag, s, "; ".join(why) if why else
            "NEITHER the converter NOR its XML changed -- shared state?"))
        for d in e.get("detail") or []:
            lines.append("%s            %s" % (tag, d))
        for f in e.get("sample") or []:
            lines.append("%s              %s" % (tag, f))
    if moved:
        lines.append("%s A suite listed above that your change was not meant "
                     "for is the thing to look at." % tag)
    if first:
        lines.append("%s   baseline recorded for: %s%s" % (
            tag, ", ".join(sorted(first)[:8]), " ..." if len(first) > 8 else ""))
    return lines


# ------------------------------------------- two scratch trees, on request

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
            old, path = path.split(" -> ", 1)
            names.add(old)              # a rename: the old path must go
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
            os.remove(dst)              # deleted or renamed in the working tree
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


def suite_of(xml: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_",
                  os.path.splitext(os.path.basename(xml))[0].lower()).strip("_")


_suite_of = suite_of


def _resolve_suites(arg, input_dir: str) -> list:
    xmls = sorted(glob.glob(os.path.join(input_dir, "*.xml")))
    if arg is None:
        return xmls
    by_suite = {suite_of(x): x for x in xmls}
    picked, missing = [], []
    for name in [s.strip() for s in arg.split(",") if s.strip()]:
        key = suite_of(name)
        (picked if key in by_suite else missing).append(by_suite.get(key, name))
    if missing:
        raise SystemExit("[suite-impact] no input XML for: %s (looked in %s)"
                         % (", ".join(missing), input_dir))
    return picked


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Convert the named suites with the converter at --base and "
                    "with the working tree, in scratch copies, and list the "
                    "suites whose generated output differs. Normally reached "
                    "as `python tools/verify_all.py --impact a,b,c`.")
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
    suites = [suite_of(x) for x in xmls]
    expect = {suite_of(s) for s in args.expect.split(",") if s.strip()}
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
            print("  MOVED      %s%s" % (suite, "   <-- NOT in --expect"
                                         if suite in unexpected else ""))
            for d in describe(r):
                print("             " + d)
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
