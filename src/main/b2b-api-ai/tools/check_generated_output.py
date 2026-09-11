"""Structural health check on the generated output already on disk.

WHY THIS EXISTS
---------------
Twice, a converter change produced broken or missing output and the only
signal was a 20-minute reconvert:

  * a regex extraction left a dangling block and an UnboundLocalError, so 13
    of 18 suites silently failed to emit and ~390k lines went missing;
  * a crashed run saved a partial shared catalog, and the NEXT run inherited
    it and emitted different output with nothing in the log to explain it.

Neither showed up in unit tests, because both were properties of the emitted
TREE, not of any single function. This scans that tree in seconds.

It checks INVARIANTS, not content, so it does not need updating when the
emitted wording changes:

  1. Every .java file's public type matches its filename. A mismatch means a
     case-insensitive filesystem clobbered one class with another.
  2. Braces balance per file.
  3. No unresolved Python f-string placeholders leaked into Java.
  4. No half-collapsed helper variables (declared but never used).
  5. No TODO / STUB markers left in generated code.
  6. Output volume is plausible -- a suite that emitted almost nothing is the
     signature of a crashed emit.
  7. No SQL reaches Db.execute without passing a guard.

    python tools/check_generated_output.py
    python tools/check_generated_output.py --root <dir>
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter

GENERATED_ROOTS = [
    os.path.join("src", "main", "java", "com", "ak", "api", "support"),
    os.path.join("src", "test", "java", "com", "ak", "api", "tests", "imported"),
]

CLASS_DECL = re.compile(r"^\s*public\s+(?:final\s+|abstract\s+)?"
                        r"(?:class|interface|enum|record)\s+(\w+)", re.M)
LEAKED_PLACEHOLDER = re.compile(
    r"\{(?:result_var|params_java|java_query|composed_java|composed_concat|"
    r"step_name|service_class_name|record_name|support_name)\}")
TODO_MARKER = re.compile(r"//\s*(?:TODO|STUB|FIXME)\b")

# Conversion holes that do NOT say TODO. Untranslated Groovy and unsupported
# SoapUI step types emit their own wording, so the TODO regex above missed
# them entirely -- the check meant to catch silent holes had one of its own.
UNTRANSLATED = re.compile(r"NO PATTERN MATCHED")
UNSUPPORTED_STEP = re.compile(r"UNSUPPORTED STEP TYPE:\s*(\w+)")

BASELINE_PATH = os.path.join("tools", "generated_baseline.json")


class Finding:
    def __init__(self, kind, path, detail):
        self.kind, self.path, self.detail = kind, path, detail

    def __str__(self):
        return f"  [{self.kind}] {self.path}\n      {self.detail}"


_B = chr(92)  # backslash, kept out of literals so escaping stays readable
_LONG_PREFIX = _B + _B + "?" + _B


def _openable(path: str) -> str:
    """Windows MAX_PATH escape hatch.

    Converter-generated names routinely exceed 260 characters once resolved
    to absolute. Such a file lists fine but open() raises FileNotFoundError,
    which reads like missing output rather than a path-length limit -- the
    exact confusion this checker exists to remove.
    """
    ap = os.path.abspath(path)
    if os.name == "nt" and not ap.startswith(_LONG_PREFIX):
        return _LONG_PREFIX + ap
    return ap

def _strip_literals(src: str) -> str:
    """Remove string/char literals and comments so brace counting is honest.

    A Java string containing '{' would otherwise look like an unbalanced
    brace, which would make check 2 useless noise.
    """
    out = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c == '"':
            # text block?
            if src.startswith('"""', i):
                j = src.find('"""', i + 3)
                i = n if j < 0 else j + 3
                continue
            i += 1
            while i < n:
                if src[i] == "\\":
                    i += 2
                    continue
                if src[i] == '"':
                    i += 1
                    break
                i += 1
            continue
        if c == "'":
            i += 1
            while i < n:
                if src[i] == "\\":
                    i += 2
                    continue
                if src[i] == "'":
                    i += 1
                    break
                i += 1
            continue
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def scan(root: str) -> tuple[list[Finding], Counter]:
    findings: list[Finding] = []
    stats = Counter()

    for gen_root in GENERATED_ROOTS:
        base = os.path.join(root, gen_root)
        if not os.path.isdir(base):
            findings.append(Finding("MISSING-TREE", gen_root,
                                    "generated tree does not exist"))
            continue
        for dp, _dns, fns in os.walk(base):
            for fn in fns:
                if not fn.endswith(".java"):
                    continue
                path = os.path.join(dp, fn)
                rel = os.path.relpath(path, root)
                try:
                    src = open(_openable(path), encoding="utf-8", errors="replace").read()
                except OSError as e:
                    findings.append(Finding("UNREADABLE", rel, str(e)))
                    continue
                stats["files"] += 1
                stats["lines"] += src.count("\n")

                # 1. filename vs public type
                m = CLASS_DECL.search(src)
                if m and m.group(1) != fn[:-5]:
                    findings.append(Finding(
                        "NAME-MISMATCH", rel,
                        f"file declares '{m.group(1)}' -- on a case-insensitive "
                        f"filesystem one class overwrote another"))

                code = _strip_literals(src)

                # 2. braces
                if code.count("{") != code.count("}"):
                    findings.append(Finding(
                        "UNBALANCED", rel,
                        f"{code.count('{')} open vs {code.count('}')} close braces"))

                # 3. leaked emit placeholders
                for pm in LEAKED_PLACEHOLDER.finditer(src):
                    findings.append(Finding(
                        "LEAKED-PLACEHOLDER", rel,
                        f"unresolved emit placeholder {pm.group(0)}"))
                    break

                # 4. half-collapsed helper variable
                declared = "String __jdbcReason" in src
                used = ("__jdbcReason !=" in src) or ("__jdbcReason," in src)
                if declared != used:
                    findings.append(Finding(
                        "HALF-COLLAPSED", rel,
                        f"__jdbcReason declared={declared} used={used}"))

                # 5b. Conversion holes that do not say TODO. Counted rather
                # than reported per-file: these are pre-existing and numerous,
                # and a check that always fails is a check nobody reads. The
                # baseline comparison below fails only when the count GROWS.
                stats["untranslated"] += len(UNTRANSLATED.findall(src))
                for um in UNSUPPORTED_STEP.finditer(src):
                    stats["unsupported_steps"] += 1
                    stats["unsupported_" + um.group(1)] += 1

                # 5. TODO / STUB left behind
                tm = TODO_MARKER.search(src)
                if tm:
                    stats["todo"] += 1
                    findings.append(Finding(
                        "TODO-LEFT", rel, tm.group(0)))

                # 7. ungated SQL
                if "Db.execute(" in code and "unsafeSqlReason" not in code \
                        and "executeComposed" not in code:
                    findings.append(Finding(
                        "UNGATED-SQL", rel,
                        "Db.execute reached without a guard or helper"))
    return findings, stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--update-baseline", action="store_true",
                    help="record current hole counts as the new baseline")
    # 500 was tuned when every ReadyAPI case got its own Support class. Cases
    # now enter through a shared entry class over a per-suite steps base, so
    # one suite emits 120 files where it used to emit 567 -- a 447-file drop
    # that is the point of the change, not a crashed emit. The floor still
    # catches a suite that fails to emit at all: a single suite lands at ~474.
    ap.add_argument("--min-files", type=int, default=20,
                    help="below this, assume a crashed emit")
    args = ap.parse_args()

    findings, stats = scan(args.root)

    print(f"generated files scanned : {stats['files']}")
    print(f"generated lines         : {stats['lines']}")
    print(f"untranslated groovy     : {stats['untranslated']}")
    print(f"unsupported steps       : {stats['unsupported_steps']}")

    # Baseline: hold the line on conversion holes without demanding that every
    # pre-existing one be fixed first. New holes fail; old ones are visible.
    baseline_file = os.path.join(args.root, BASELINE_PATH)
    tracked = {"untranslated": stats["untranslated"],
               "unsupported_steps": stats["unsupported_steps"]}
    if args.update_baseline:
        os.makedirs(os.path.dirname(baseline_file), exist_ok=True)
        with open(baseline_file, "w", encoding="utf-8") as fh:
            json.dump({**tracked,
                       "note": "counts of conversion holes; regenerate with "
                               "--update-baseline after deliberately changing them"},
                      fh, indent=2)
            fh.write("\n")
        print(f"\nbaseline written to {BASELINE_PATH}: {tracked}")
    elif os.path.isfile(baseline_file):
        try:
            with open(baseline_file, encoding="utf-8") as fh:
                base = json.load(fh)
            for key, now in tracked.items():
                was = base.get(key)
                if isinstance(was, int) and now > was:
                    findings.append(Finding(
                        "MORE-HOLES", "(tree)",
                        f"{key} rose {was} -> {now}: a change introduced "
                        f"{now - was} new conversion hole(s). Fix them, or "
                        f"re-baseline with --update-baseline if intended."))
                elif isinstance(was, int) and now < was:
                    print(f"  ({key} improved {was} -> {now}; "
                          f"re-baseline to lock it in)")
        except (OSError, json.JSONDecodeError) as e:
            print(f"  (baseline unreadable: {e})")
    else:
        print(f"  (no baseline yet -- create with --update-baseline)")

    # 6. every input XML produced a suite package -- the crashed-emit
    #    signature, tested directly.
    #
    #    This used to be a fixed file-count floor, which is not scale
    #    invariant: it has to be lowered every time a smaller set of suites
    #    is converted, and lowering a guard to make it pass is how a guard
    #    stops guarding. Three small suites emit 124 files; one large one
    #    emits 474. Neither number means anything on its own. What DOES
    #    mean something is a suite in input/ with no emitted package.
    import glob
    in_dir = os.path.join(args.root, "tools", "ra_converter", "input")
    support = os.path.join(args.root, "src", "main", "java",
                           "com", "ak", "api", "support")
    xmls = [x for x in glob.glob(os.path.join(in_dir, "*.xml"))]
    for xml in xmls:
        suite = os.path.splitext(os.path.basename(xml))[0].lower()
        if not os.path.isdir(_openable(os.path.join(support, suite))):
            findings.append(Finding(
                "SUITE-NOT-EMITTED", os.path.basename(xml),
                f"no support/{suite}/ package; this suite failed to emit"))
    if xmls and stats["files"] < args.min_files:
        findings.append(Finding(
            "TOO-FEW-FILES", "(tree)",
            f"only {stats['files']} generated files for {len(xmls)} suite(s) "
            f"(expected >= {args.min_files}); emit may be truncated"))

    by_kind = Counter(f.kind for f in findings)
    if not findings:
        print("\nOK -- generated output is structurally sound")
        return 0

    print(f"\n{len(findings)} finding(s): {dict(by_kind)}\n")
    for f in findings[:40]:
        print(f)
    if len(findings) > 40:
        print(f"  ... and {len(findings) - 40} more")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
