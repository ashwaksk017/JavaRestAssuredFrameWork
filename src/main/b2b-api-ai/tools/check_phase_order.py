"""Every chained phase name resolves, in registration order, to a real phase.

CaseRegistry resolves a chained name against the case's registered phases:

    .g()        -> Case.only(vocab)  : throws if the case has 0, or 2+, `g`
    .g("step")  -> Case.named(...)   : throws if no `g` has that step

Resolution is BY NAME, never positional, so four things can go wrong and
only two of them are loud at run time:

    MISSING    a chained call matches no registered phase        -> throws
    AMBIGUOUS  bare .g() where the case registers `g` twice      -> throws
    ORPHAN     a registered phase no chain call ever reaches      -> SILENT
    INVERTED   the chain reaches phases out of registration order -> SILENT

ORPHAN and INVERTED are the dangerous pair: the test passes having skipped
a step, or having run steps in an order the ReadyAPI case never had.

Why this is not covered by check_ctx_dataflow.py: on a --phase-specs tree
that guard seeds `available` with every key produced ANYWHERE in the suite
before the first step runs (its own comment concedes it "cannot see which
chained name is a spec"). It therefore checks "written somewhere in the
suite", not "written earlier in this chain", and cannot detect an ordering
fault at all.

    python tools/check_phase_order.py [--root .] [--show N]

Exit 1 if anything is found.
"""
from __future__ import annotations

import argparse
import collections
import os
import re
import sys

# --------------------------------------------------------------- file access
_PFX = "\\\\?\\"


def _lp(path: str) -> str:
    """Windows long-path form: the generated tree blows past MAX_PATH."""
    ap = os.path.abspath(path)
    if os.name == "nt" and not ap.startswith(_PFX):
        return _PFX + ap
    return ap


def _read(path: str) -> str:
    try:
        with open(_lp(path), encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def _walk(base: str, suffix: str = ".java"):
    for dirpath, _dirs, files in os.walk(_lp(base)):
        for fn in sorted(files):
            if fn.endswith(suffix):
                yield os.path.join(dirpath, fn)


def _short(path: str) -> str:
    p = path.replace(_PFX, "").replace("\\", "/")
    for marker in ("/src/main/java/", "/src/test/java/"):
        i = p.find(marker)
        if i >= 0:
            return p[i + 1:]
    return p


# ---------------------------------------------------------------- patterns
_ENTRY_RX = re.compile(r'\.(phase|verify)\(\s*"([^"]+)"\s*,\s*"([^"]*)"')
_START_RX = re.compile(r'(\w+)\.start\(\s*row\s*(?:,\s*"([^"]*)")?\s*\)')
_CALL_RX = re.compile(r'\.(\w+)\(\s*(?:"([^"]*)")?\s*\)')
_INS_RX = re.compile(r'Insights\.(\w+)\(\s*\w+\s*,\s*\w+\s*(?:,\s*"([^"]*)")?\s*\)')
_STRARR_RX = re.compile(r"new String\[\]\s*\{([^}]*)\}")
_LIT_RX = re.compile(r'"((?:[^"\\]|\\.)*)"')
_MARK = "CaseRegistry.register("


def _register_arg(text: str, open_paren: int):
    """The register( argument, and the index just past its closing paren."""
    depth, i = 1, open_paren + 1
    while i < len(text) and depth:
        depth += (text[i] == "(") - (text[i] == ")")
        i += 1
    return text[open_paren + 1:i - 1].strip(), i


def build_registry(root: str):
    """caseId -> ordered [(kind, vocab, step)], plus the file it came from.

    Two shapes exist and BOTH must be read. Missing the second one is not a
    small error: `CaseRegistry.register(id)` inside
    `for (String id : new String[] {...})` covers whole families of cases,
    and a regex that only sees string literals silently attributes those
    entries to the preceding literal case -- which reported ~290 phantom
    findings the first time this was written.
    """
    registry: dict = {}
    reg_file: dict = {}
    unparsed: list = []
    base = os.path.join(root, "src/main/java")
    for path in _walk(base):
        if not path.endswith("Phases.java"):
            continue
        text = _read(path)
        pos = 0
        while True:
            at = text.find(_MARK, pos)
            if at < 0:
                break
            op = at + len(_MARK) - 1
            arg, after = _register_arg(text, op)
            end = text.find(";", after)
            block = text[after:end if end > 0 else len(text)]
            if arg.startswith('"'):
                ids = [_LIT_RX.match(arg).group(1)]
            else:
                last = None
                for last in _STRARR_RX.finditer(text[:at]):
                    pass
                if last is None:
                    unparsed.append((path, text[at:at + 90].replace("\n", " ")))
                    pos = after
                    continue
                ids = _LIT_RX.findall(last.group(1))
            entries = [(k, v, s) for k, v, s in _ENTRY_RX.findall(block)]
            for cid in ids:
                if cid in registry:
                    unparsed.append((path, "duplicate registration of " + cid))
                registry[cid] = entries
                reg_file[cid] = path
            pos = end if end > 0 else after
    return registry, reg_file, unparsed


def chains_in(root: str):
    """(path, testMethod, defaultCaseId, [(kind, name, step), ...]) per @Test."""
    out = []
    base = os.path.join(root, "src/test/java/com/ak/api/tests/imported")
    for path in _walk(base):
        text = _read(path)
        if ".start(row" not in text:
            continue
        for seg in re.split(r"\n    @Test", text)[1:]:
            sm = _START_RX.search(seg)
            if not sm:
                continue
            body = seg[sm.end():]
            cut = body.find(".complete()")
            head = body[:cut] if cut >= 0 else body
            # Trailing-GET verifies are invoked AFTER complete(), through
            # Insights.verifyX(scenario, expected). Counting only the
            # pre-complete() calls reports every verify phase as an orphan.
            tail = body[cut:] if cut >= 0 else ""
            # `.bootstrap()` is a chain call but not a registered phase: it
            # runs the case's setup (the token request and whatever else
            # precedes the first business step). It is in the chain so that
            # request is visible; the registry describes it as a bootstrap,
            # not as a phase, so matching it against phases reports every
            # test as MISSING.
            calls = [("phase", n, s) for n, s in _CALL_RX.findall(head)
                     if n != "bootstrap"]
            calls += [("verify", n, s) for n, s in _INS_RX.findall(tail)]
            tn = re.search(r"public void (\w+)\(", seg)
            out.append((path, tn.group(1) if tn else "?",
                        sm.group(2) or "", calls))
    return out


def analyse(root: str):
    registry, reg_file, unparsed = build_registry(root)
    chains = chains_in(root)
    findings = collections.defaultdict(list)
    reached = collections.defaultdict(set)

    def grade(cid, calls, path, tname, record):
        entries = registry.get(cid)
        if entries is None:
            return
        seq = []
        for kind, name, step in calls:
            hits = [i for i, (k, v, _s) in enumerate(entries)
                    if k == kind and v == name]
            if not hits and kind == "phase":
                # A verify now chains BEFORE complete() where it used to be
                # a static call after it, so position no longer tells the two
                # apart. Resolve by what the registry holds: a pre-complete
                # call naming a registered VERIFY is that verify, not a
                # missing phase. Kept as a fallback rather than a rename so a
                # genuinely missing phase is still reported as one.
                hits = [i for i, (k, v, _s) in enumerate(entries)
                        if k == "verify" and v == name]
                if hits:
                    kind = "verify"
            if step:
                hits = [i for i in hits if entries[i][2] == step]
            if not hits:
                if record:
                    findings["MISSING"].append(
                        (cid, path, tname, "%s %s(%s)" % (kind, name, step or "")))
                continue
            if len(hits) > 1 and not step:
                if record:
                    alts = ", ".join(sorted({entries[i][2] for i in hits}))
                    findings["AMBIGUOUS"].append(
                        (cid, path, tname,
                         "%s %s() matches %d: %s" % (kind, name, len(hits), alts)))
                continue
            seq.append(hits[0])
            reached[cid].add(hits[0])
        if record and seq != sorted(seq):
            findings["INVERTED"].append(
                (cid, path, tname, "visits registered indexes %s" % seq))

    for path, tname, cid, calls in chains:
        if cid and cid in registry:
            grade(cid, calls, path, tname, record=True)
        elif cid:
            findings["NO_REGISTRATION"].append((cid, path, tname, ""))
            continue
        # A prefix-merged CLUSTER shares one @Test across several cases: the
        # CSV `test_case_id` column picks which one runs, `_stop_after`
        # truncates it. Those siblings live in the same Phases file, so
        # credit what this chain can reach in them too -- otherwise every
        # cluster member's phases look unreached.
        home = reg_file.get(cid)
        if home:
            for other, other_home in reg_file.items():
                if other_home == home and other != cid:
                    grade(other, calls, path, tname, record=False)

    for cid, entries in registry.items():
        for i, (k, v, s) in enumerate(entries):
            if i not in reached.get(cid, ()):
                findings["ORPHAN"].append(
                    (cid, reg_file[cid], "", '%s %s("%s") at #%d' % (k, v, s, i)))
    return findings, registry, chains, unparsed


_ORDER = ["MISSING", "AMBIGUOUS", "INVERTED", "ORPHAN", "NO_REGISTRATION"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=".")
    ap.add_argument("--show", type=int, default=12)
    args = ap.parse_args()

    cases_root = os.path.join(args.root, "src/main/java")
    if not os.path.isdir(_lp(cases_root)):
        print("[phase-order] no src/main/java under %s -- nothing to check"
              % args.root)
        return 0

    findings, registry, chains, unparsed = analyse(args.root)
    if not registry and not chains:
        print("[phase-order] no *Phases.java and no chained tests -- "
              "nothing to check (expected before the first convert)")
        return 0

    print("registered cases : %d" % len(registry))
    print("chains analysed  : %d" % len(chains))
    print("chain calls      : %d" % sum(len(c[3]) for c in chains))
    print()
    total = 0
    for key in _ORDER:
        n = len(findings.get(key, []))
        total += n
        print("%-16s : %d" % (key.lower(), n))
    if unparsed:
        print()
        print("!! %d registration form(s) this parser did not understand -- "
              "the counts above may be wrong:" % len(unparsed))
        for path, snippet in unparsed[:10]:
            print("   %s: %s" % (_short(path), snippet))
    print()
    for key in _ORDER:
        hits = findings.get(key, [])
        if not hits:
            continue
        print("--- %s (%d) ---" % (key, len(hits)))
        for cid, path, tname, detail in hits[:args.show]:
            where = ("  [%s]" % tname) if tname else ""
            print("  %s%s\n      %s\n      %s"
                  % (cid, where, detail, _short(path)))
        if len(hits) > args.show:
            print("  ... and %d more (--show N)" % (len(hits) - args.show))
        print()

    if total or unparsed:
        print("A chained name resolves by NAME, so a phase nothing reaches is "
              "skipped silently and a chain that visits phases out of order "
              "still runs. Fix the converter, or name the step explicitly: "
              "readProgramAccount(\"http_request_200_2\").")
        return 1
    print("Every chain resolves, in registration order, to every phase.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
