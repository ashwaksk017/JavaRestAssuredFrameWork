"""The two-sided gate: proof that a check still catches what it exists for.

WHY
---
A check that passes tells you nothing on its own. It passes on a correct
tree and it passes if someone quietly made it unable to fail -- and this
repo has both scars: `check_csv_contracts`' inlined-columns guard was a
rubber stamp once (a bare method name is a substring of the renamed one),
and `check_ctx_dataflow` still passes for the wrong reason on phase-spec
trees. "Always revert-test a guard" is the lesson; this automates it.

So every check the loop is allowed to EDIT must first have a negative
fixture: a mutation that breaks what it guards, with the requirement that
the check FAILS while the mutation is applied. Weakening the check then
breaks its own fixture, and the shortcut closes.

    python tools/autofix/mutations.py --list
    python tools/autofix/mutations.py --check phase-order
    python tools/autofix/mutations.py --all

Fixtures land LAZILY, by design: the first time the loop wants to touch
`check_X.py`, X must grow a fixture. Paying for all 47 up front would buy
46 fixtures nobody needed.

SAFETY
------
Mutations edit real files in the tree, one at a time, and restore from an
in-memory copy with a sha1 comparison afterwards. Generated files are
regenerable, but a half-restored tree would be a nasty surprise, so a
failed restore is reported as a BROKEN result and the file is named. The
runner never leaves more than one file mutated at a time.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import failure_record as fr  # noqa: E402

ROOT = fr.ROOT
SUPPORT = os.path.join(ROOT, "src", "main", "java", "com", "hi", "api", "support")

# A located mutation: absolute path, the exact text to replace, and what
# to replace it with.
Site = tuple[str, str, str]


@dataclass
class Mutation:
    name: str
    check: str                      # the verify_all check this must break
    locate: Callable[[], Site | None]
    breaks: str                     # what property is being broken, in words


# --- locators ---------------------------------------------------------
# Each finds its own site at runtime. Hardcoding a file and a line would
# make every fixture rot at the next reconvert, and a rotted fixture
# reports SKIP -- which is indistinguishable from "no fixture", i.e. the
# guard silently stops existing.
def _walk(pattern: str, under: str = SUPPORT):
    rx = re.compile(pattern)
    for base, _dirs, files in os.walk(under):
        for name in sorted(files):
            if not name.endswith(".java"):
                continue
            fp = os.path.join(base, name)
            try:
                src = io.open(fp, encoding="utf-8", newline="").read()
            except OSError:
                continue
            m = rx.search(src)
            if m:
                yield fp, src, m


IMPORTED_TESTS = os.path.join(ROOT, "src", "test", "java", "com", "hi",
                              "api", "tests", "imported")


def _loc_phase_unresolvable() -> Site | None:
    """Start a case id that nothing registers.

    The first attempt at this renamed a `PhaseSpec.phase("...")` inside a
    Specs class and phase-order correctly ignored it: that string is the
    spec's own LABEL, not a chain link. What phase-order resolves is the
    id in `Onboarding.start(row, "<case id>")` against the registrations,
    so breaking that is what exercises it. The fixture was wrong, not the
    check -- worth keeping written down, because "the check missed it" and
    "the fixture was aimed at the wrong thing" look identical in a report.
    """
    for fp, _src, m in _walk(r'\.start\(row,\s*"([^"]+)"\)', under=IMPORTED_TESTS):
        return fp, m.group(0), '.start(row, "zzz_fixture_unregistered_case")'
    return None


def _loc_unqualified_register() -> Site | None:
    """Drop the suite argument -- the silent cross-suite overwrite."""
    for fp, _src, m in _walk(r'CaseRegistry\.register\("([^"]+)",\s*"([^"]+)"\)'):
        return fp, m.group(0), f'CaseRegistry.register("{m.group(2)}")'
    return None


TEMPLATES = os.path.join(ROOT, "src", "main", "resources", "templates")


def _loc_raw_placeholder() -> Site | None:
    """Put an UNRESOLVABLY-SHAPED ReadyAPI ref in a generated request body.

    The shape is the whole point, and it took two wrong attempts to get
    right. `${Properties#zzzFixture}` is NOT a bug: PlaceholderResolver
    handles templates as well as CSV cells and maps '#' to '.', so a
    well-shaped ref is deferred to runtime and check_substitutions is
    correct to pass it. Only a ref whose shape the resolver's key pattern
    cannot match -- brackets and quotes, as in the real
    `${step#Respons['accountID']}` typo -- is genuinely stuck as literal
    text on the wire. That is what this injects.
    """
    rx = re.compile(r'"([A-Za-z][A-Za-z0-9_]{2,})"\s*:\s*"([^"$#{]{3,})"')
    for base, _dirs, files in os.walk(TEMPLATES):
        for name in sorted(files):
            if not name.endswith(".json"):
                continue
            fp = os.path.join(base, name)
            try:
                src = io.open(fp, encoding="utf-8", newline="").read()
            except OSError:
                continue
            m = rx.search(src)
            if m:
                return fp, m.group(0), \
                    '"%s": "${zzzFixture#Respons[\'accountID\']}"' % m.group(1)
    return None


def _loc_drop_csv_reader() -> Site | None:
    """Remove the reader the generated comment promises is row-overridable.

    This is the revert-test that was done by hand when the guard was
    widened: delete the reader, the check must fail again.
    """
    fp = os.path.join(ROOT, "src", "main", "java", "com", "hi", "api",
                      "rest", "utilities", "ResponseAsserts.java")
    if not os.path.exists(fp):
        return None
    src = io.open(fp, encoding="utf-8", newline="").read()
    needle = "public static void invalidStatus(SoftAssert softAssert, Response res"
    if needle not in src:
        return None
    return fp, needle, "public static void invalidStatusRenamedByFixture(" \
                       "SoftAssert softAssert, Response res"


MUTATIONS = (
    Mutation("phase-points-nowhere", "phase-order", _loc_phase_unresolvable,
             "a chained phase name that nothing defines must not resolve"),
    Mutation("register-loses-suite", "case-duplication", _loc_unqualified_register,
             "an unqualified register() is how the cross-suite overwrite returns"),
    Mutation("placeholder-left-raw", "substitution", _loc_raw_placeholder,
             "a ${...} the resolver cannot parse must never reach the wire"),
    Mutation("csv-reader-renamed", "csv-contract", _loc_drop_csv_reader,
             "an advertised CSV column must have a reader that reads it"),
)


def check_name_for_script(rel: str) -> str | None:
    """The verify_all check name that runs this script.

    `tools/check_phase_order.py` is the check `phase-order`, NOT
    `phase_order`. Deriving it from the filename got that wrong and made
    the fixture requirement report "no fixture exists" for a check that
    has one -- so it is read from the only place the mapping is defined.
    """
    want = rel.replace("\\", "/")
    tools = os.path.join(ROOT, "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    try:
        import verify_all as va
    except Exception:
        return None
    for c in va.CHECKS:
        for part in c.cmd[1:]:
            if part.replace("\\", "/") == want:
                return c.name
    return None


def mutations_for(check: str) -> tuple[Mutation, ...]:
    return tuple(m for m in MUTATIONS if m.check == check)


def has_fixture(check: str) -> bool:
    return bool(mutations_for(check))


# --- the harness ------------------------------------------------------
def _sha1_file(path: str) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        h.update(fh.read())
    return h.hexdigest()


def _check_cmd(check: str) -> list[str] | None:
    """The command verify_all uses for this check -- one definition only."""
    tools = os.path.join(ROOT, "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    try:
        import verify_all as va
    except Exception:
        return None
    for c in va.CHECKS:
        if c.name == check:
            return list(c.cmd)
    return None


@dataclass
class Result:
    mutation: str
    check: str
    outcome: str          # CAUGHT | MISSED | SKIP | BROKEN | NO-CHECK
    detail: str = ""
    path: str = ""

    @property
    def ok(self) -> bool:
        return self.outcome in ("CAUGHT", "SKIP")


def run_mutation(m: Mutation, verbose: bool = False) -> Result:
    site = m.locate()
    if site is None:
        return Result(m.name, m.check, "SKIP",
                      "no site for this mutation in the current tree -- "
                      "reconvert, or the locator needs updating")
    path, old, new = site
    rel = os.path.relpath(path, ROOT).replace("\\", "/")
    cmd = _check_cmd(m.check)
    if cmd is None:
        return Result(m.name, m.check, "NO-CHECK",
                      f"verify_all has no check named {m.check!r}", rel)

    original = io.open(path, encoding="utf-8", newline="").read()
    if old not in original:
        return Result(m.name, m.check, "SKIP", "located text vanished", rel)
    before = _sha1_file(path)
    try:
        io.open(path, "w", encoding="utf-8", newline="").write(
            original.replace(old, new, 1))
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                           shell=(os.name == "nt" and cmd[0] == "mvn"))
        caught = p.returncode != 0
        out = ((p.stdout or "") + (p.stderr or "")).strip()
    finally:
        io.open(path, "w", encoding="utf-8", newline="").write(original)

    if _sha1_file(path) != before:
        return Result(m.name, m.check, "BROKEN",
                      f"restore did not reproduce the original file. Reconvert "
                      f"this suite before trusting the tree", rel)
    if caught:
        return Result(m.name, m.check, "CAUGHT",
                      out.splitlines()[-1][:160] if (verbose and out) else "", rel)
    return Result(m.name, m.check, "MISSED",
                  f"{m.check} still passed with the mutation applied -- it does "
                  f"not actually verify that {m.breaks}", rel)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--list", action="store_true")
    g.add_argument("--check", help="run the fixtures for one check")
    g.add_argument("--all", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    if args.list:
        print(f"{len(MUTATIONS)} fixture(s):")
        for m in MUTATIONS:
            print(f"  {m.name:<24} -> {m.check:<16} {m.breaks}")
        covered = sorted({m.check for m in MUTATIONS})
        print(f"\ncovered checks: {', '.join(covered)}")
        print("Fixtures land lazily: a check gets one the first time the loop "
              "wants to edit it.")
        return 0

    todo = MUTATIONS if args.all else mutations_for(args.check)
    if not todo:
        print(f"no fixture for {args.check!r}. A change confined to that check "
              f"cannot be accepted until one exists -- add a Mutation here.")
        return 1

    results = [run_mutation(m, args.verbose) for m in todo]
    for r in results:
        line = f"  [{r.outcome:<8}] {r.mutation:<24} {r.check}"
        if r.path:
            line += f"\n             site: {r.path}"
        if r.detail:
            line += f"\n             {r.detail}"
        print(line)

    broken = [r for r in results if r.outcome == "BROKEN"]
    missed = [r for r in results if r.outcome in ("MISSED", "NO-CHECK")]
    if broken:
        print(f"\nBROKEN: {len(broken)} file(s) may not have been restored.")
        return 2
    if missed:
        print(f"\n{len(missed)} check(s) did not catch their mutation.")
        return 1
    skips = sum(1 for r in results if r.outcome == "SKIP")
    print(f"\nAll {len(results) - skips} applicable fixture(s) were caught."
          + (f" {skips} skipped." if skips else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
