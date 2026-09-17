"""Every ctx value a chain READS must be WRITTEN earlier in that same chain.

Found after a remote run showed Salesforce tokens -- and other values captured
from responses -- arriving empty in later requests.

In ReadyAPI the choreography is implicit: a Groovy step scrapes the previous
step's response into a test-case property, and a later request expands that
property. The converter turns the scrape into `putExtracted(ctx, KEY)` and the
expansion into `ctxGet(ctx, KEY)`. Those two land in SEPARATE fluent methods,
and which methods a case chains is decided independently -- by body
fingerprint. So a chain can legitimately compile, and run, while reading a key
nothing in it ever wrote. `ctxGet` returns "" and the request goes out with an
empty bearer.

Nothing caught that: it is not a compile error, and a 401 in one suite looks
like an environment problem rather than a converter bug.

This walks every generated @Test chain in order, tracking what each method
writes, and reports any read with no producer ahead of it.

    python tools/check_ctx_dataflow.py [--root .] [--verbose]

Exit 1 if any unsatisfied read is found.
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


def _walk_java(root: str):
    for dirpath, _dirs, files in os.walk(_lp(root)):
        for fn in sorted(files):
            if fn.endswith(".java"):
                yield os.path.join(dirpath, fn)


# --------------------------------------------------------------- extraction
# Bodies are taken by matching braces, NOT by a non-greedy regex. A translated
# Groovy step wraps its work in a nested `{ ... }` block, so `(.*?)\n[ \t]*\}`
# stopped at the FIRST inner brace and truncated the body -- the seedFromRow
# and putExtracted calls after it went unseen, and every key they produce was
# reported missing.
_METHOD_HEAD_RX = re.compile(
    r"^[ \t]*public (?:S|CustomerOnboarding|\w+) ([a-zA-Z]\w*)\(\)[^\n{]*\{",
    re.M)
_BOOTSTRAP_HEAD_RX = re.compile(
    r"^[ \t]*protected \w+ bootstrap\(\)[^\n{]*\{", re.M)
_SETUP_HEAD_RX = re.compile(
    r"^[ \t]*public static void (\w+)\([^{]*\{", re.M)


def _balanced_body(text: str, open_idx: int) -> str:
    """Text between `open_idx` (the '{') and its matching '}'.

    Skips braces inside string/char literals and comments -- paths such as
    "/guests/{guestId}" appear constantly and would unbalance a naive count.
    """
    depth = 0
    i = open_idx
    n = len(text)
    while i < n:
        c = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if c == '"' or c == "'":
            quote = c
            i += 1
            while i < n:
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == quote:
                    break
                i += 1
        elif c == "/" and nxt == "/":
            i = text.find("\n", i)
            if i < 0:
                break
        elif c == "/" and nxt == "*":
            j = text.find("*/", i + 2)
            i = j + 1 if j >= 0 else n
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[open_idx + 1:i]
        i += 1
    return ""


def _bodies(text: str, head_rx, group: int = 1):
    """(name, body) for every header the regex matches."""
    for m in head_rx.finditer(text):
        body = _balanced_body(text, m.end() - 1)
        name = m.group(group) if group and m.lastindex else ""
        yield name, body

_WRITE_RX = re.compile(r'putExtracted\(\s*ctx\s*,\s*"([^"]+)"')
_WRITE_IF_RX = re.compile(r'putIfNonEmpty\(\s*ctx\s*,\s*"([^"]+)"')
# putEnvScoped publishes an environment-selected literal. It always writes
# (one branch is chosen, with a WARN + first-branch fallback when the active
# env matches none), so it is an unconditional producer for its key.
_WRITE_ENV_RX = re.compile(r'putEnvScoped\(\s*ctx\s*,\s*"([^"]+)"')
_READ_RX = re.compile(r'ctxGet\(\s*ctx\s*,\s*"([^"]+)"')
# asString/asLong take several fallback keys; ANY of them satisfying is enough.
_READ_MULTI_RX = re.compile(r'as(?:String|Long)\(\s*ctx\s*,\s*((?:"[^"]+"\s*,?\s*)+)\)')
_SEED_RX = re.compile(r'seedFromRow\(\s*ctx\s*,\s*row\s*,\s*"([^"]*)"')
_GEN_RX = re.compile(r'generateStandard\(\s*ctx\s*,\s*"([^"]*)"((?:\s*,\s*"[^"]+")*)\)')
_RUNSETUP_RX = re.compile(r'runSetup\(\s*"(\w+)"')
_SETUP_CALL_RX = re.compile(r'SetupHelper\.(\w+)\(')


def method_events(body: str) -> list:
    """Ordered [(offset, kind, payload)] for one method body.

    Order matters INSIDE a method, not just between them. One ReadyAPI Groovy
    step often computes a local and uses it in the same script -- the
    translator lifts the local to ctx, so the body both writes and reads the
    key. Applying a method's writes only after checking its reads reported
    150 phantom failures for `__groovy.randomNumber` alone, every one of them
    written a few lines above the read.
    """
    events = []
    for rx in (_WRITE_RX, _WRITE_IF_RX, _WRITE_ENV_RX):
        for m in rx.finditer(body):
            events.append((m.start(), "w", m.group(1)))
    for m in _SEED_RX.finditer(body):
        # Seeds every CSV column under this prefix; the columns are not known
        # statically, so record the prefix as a wildcard producer.
        events.append((m.start(), "w", m.group(1) + "*"))
    for m in _GEN_RX.finditer(body):
        prefix = m.group(1)
        for k in re.findall(r'"([^"]+)"', m.group(2)):
            for variant in (k, k[:1].upper() + k[1:], k[:1].lower() + k[1:]):
                # The generator writes both spellings so #Properties_Username#
                # and #Properties_username# both resolve.
                events.append((m.start(), "w", f"{prefix}.{variant}"))
    for m in _READ_RX.finditer(body):
        # A read inside a log statement is not a value reaching a request --
        # it is a translated Groovy local echoed for diagnostics. Counting
        # those made 10 log lines look like broken substitutions.
        _nl = chr(10)
        line_start = body.rfind(_nl, 0, m.start()) + 1
        _end = body.find(_nl, m.start())
        line = body[line_start:_end if _end >= 0 else len(body)].lstrip()
        if line.startswith(("LOG.", "log.")) or "Allure.step(" in line:
            continue
        events.append((m.start(), "r", (m.group(1),)))
    for m in _READ_MULTI_RX.finditer(body):
        alts = tuple(re.findall(r'"([^"]+)"', m.group(1)))
        if alts:
            # asString(ctx,"a","b") is satisfied by EITHER key; splitting them
            # into separate reads would invent failures that cannot happen.
            events.append((m.start(), "r", alts))
    for rx in (_RUNSETUP_RX, _SETUP_CALL_RX):
        for m in rx.finditer(body):
            events.append((m.start(), "s", m.group(1)))
    events.sort(key=lambda e: e[0])
    return events


def method_effects(body: str) -> tuple:
    """(writes, reads, setups) as sets -- for callers that ignore order."""
    ev = method_events(body)
    return ({p for _o, k, p in ev if k == "w"},
            {p for _o, k, p in ev if k == "r"},
            {p for _o, k, p in ev if k == "s"})


def build_registry(root: str) -> dict:
    """method name -> ordered event list, across every step layer."""
    reg: dict = {}
    for path in _walk_java(os.path.join(root, "src/main/java")):
        text = _read(path)
        if "ScenarioSteps" not in text and "SetupHelper" not in text:
            continue
        for name, body in _bodies(text, _METHOD_HEAD_RX):
            # Same name across layers is the same body by construction; keep
            # the richest we have seen so a per-case override cannot blank it.
            ev = method_events(body)
            if len(ev) >= len(reg.get(name, ())):
                reg[name] = ev
        for _n, body in _bodies(text, _BOOTSTRAP_HEAD_RX, group=0):
            key = "__bootstrap__" + os.path.basename(path)[:-5]
            ev = method_events(body)
            if len(ev) >= len(reg.get(key, ())):
                reg[key] = ev
    return reg


# --------------------------------------------------------------- chain parse
_CHAIN_RX = re.compile(
    r"(\w+)\.start\(row[^)]*\)((?:\s*\.\w+\(\))+)\s*\.complete\(\)", re.S)
_TESTNAME_RX = re.compile(r"public void (\w+)\(Map<String, String> row\)")


def chains_in(text: str) -> list:
    """(testMethod, entryClass, [step, ...]) for each fluent chain."""
    out = []
    for m in _CHAIN_RX.finditer(text):
        entry = m.group(1)
        steps = re.findall(r"\.(\w+)\(\)", m.group(2))
        name = "?"
        prior = text[:m.start()]
        tm = list(_TESTNAME_RX.finditer(prior))
        if tm:
            name = tm[-1].group(1)
        out.append((name, entry, steps))
    return out


def _short(path: str) -> str:
    """Trim the long-path prefix so findings read as repo-relative."""
    p = path.replace(_PFX, "")
    i = p.replace("\\", "/").find("src/test/java/")
    return p[i:].replace("\\", "/") if i >= 0 else p



# --phase-specs trees declare extracts as DATA in support/<suite>/cases/:
#   .extract("K", "$.path")  .extractWhole("K")  .extractRawRequest("K")
#   .extractRawRequestPath("K", "path")
# and the translated blocks that used to sit inside phase bodies live in
# Hooks.java. Neither is a `public S name()` body, so the walk above cannot
# see them; collect them here and treat them as produced.
_SPEC_EXTRACT_RX = re.compile(r'\.extract(?:Whole|RawRequest|RawRequestPath)?\(\s*"([^"]+)"')
_SPEC_WRITE_RX = re.compile(r'putExtracted\(\s*ctx\s*,\s*"([^"]+)"')


def spec_producers(root: str) -> set:
    keys: set = set()
    base = os.path.join(root, "src/main/java")
    for path in _walk_java(base):
        norm = path.replace(chr(92), "/")
        if "/support/" not in norm or "/cases/" not in norm:
            continue
        text = _read(path)
        keys.update(_SPEC_EXTRACT_RX.findall(text))
        keys.update(_SPEC_WRITE_RX.findall(text))
        # RestStep keeps the resolved request under <step>_RawRequest for
        # every phase; PhaseRunner does the same.
        keys.update(s + "_RawRequest" for s in re.findall(r'PhaseSpec\.phase\(\s*"([^"]+)"', text))
    return keys


def satisfied(alts: tuple, available: set, wildcards: set) -> bool:
    for key in alts:
        if key in available:
            return True
        for w in wildcards:
            if key.startswith(w):
                return True
    return False


def analyse(root: str) -> tuple:
    reg = build_registry(root)
    # SetupHelper flows: what a runSetup("flow_A") contributes.
    setup_writes: dict = {}
    for path in _walk_java(os.path.join(root, "src/main/java")):
        if not os.path.basename(path).startswith("SetupHelper"):
            continue
        text = _read(path)
        # The signature wraps across lines -- `public static void flow_A(`
        # and then its parameters. Anchoring the brace to the same line
        # matched nothing, and every token these flows produce was reported
        # missing: 489 phantom findings.
        # Key by SUITE + name. Every converted suite emits its own
        # SetupHelper, and flow names repeat across them (`flow_A` exists in
        # 3 of the 5 reference suites). Keyed by bare name the last file
        # walked won, so eadkafkaevents' flow_A -- which seeds Properties.*
        # -- was overwritten by programaccountregression's, which does not.
        # Every Properties.* read in the Kafka chains then looked
        # unsatisfied. Suite comes from .../support/<suite>/SetupHelper.java.
        suite = os.path.basename(os.path.dirname(path))
        for name, body in _bodies(text, _SETUP_HEAD_RX):
            w, _r, _s = method_effects(body)
            setup_writes[(suite, name)] = w

    findings = []
    stats = collections.Counter()

    def apply(keys, available, wildcards):
        for k in keys:
            (wildcards if k.endswith("*") else available).add(
                k[:-1] if k.endswith("*") else k)

    def run(events, available, wildcards, on_unsatisfied, suite=""):
        """Replay one method IN ORDER, so a key written a few lines above its
        own read counts as produced."""
        for _off, kind, payload in events:
            if kind == "w":
                apply([payload], available, wildcards)
            elif kind == "s":
                apply(setup_writes.get((suite, payload), set()),
                      available, wildcards)
            elif kind == "r" and not satisfied(payload, available, wildcards):
                on_unsatisfied(payload)

    # Converter output only. `tests/manual` holds a hand-written DSL chain
    # whose methods live on `dsl/CustomerOnboarding` and share names with
    # generated ones; a name-keyed registry conflates the two and invents
    # failures. That test is not a converter guarantee, so it is out of scope.
    # Keys a --phase-specs tree produces as data. Credited at chain start:
    # the check cannot see which chained name is a spec, and a spec's
    # extracts are, by construction, the extracts its old body had.
    spec_keys = spec_producers(root)
    tests_root = os.path.join(root, "src/test/java/com/ak/api/tests/imported")
    for path in _walk_java(tests_root):
        text = _read(path)
        if ".start(row" not in text:
            continue
        # .../tests/imported/<suite>/<area>/<Class>.java -- the suite segment
        # picks the right SetupHelper when flow names collide across suites.
        # Split on the marker, not relpath: _walk_java yields Windows
        # long-path (\?\C:\...) strings that relpath refuses to compare
        # against a plain root ("path is on mount ...").
        norm = path.replace(chr(92), "/")
        marker = "/tests/imported/"
        suite = ""
        if marker in norm:
            tail = norm.split(marker, 1)[1]
            suite = tail.split("/")[0] if "/" in tail else ""
        for tname, entry, steps in chains_in(text):
            stats["chains"] += 1
            available: set = set(spec_keys)
            wildcards: set = set()
            # The entry class bootstrap runs before the first chained step.
            # An index-0 entry class declares no override and inherits
            # ScenarioSteps.bootstrap(); crediting it with nothing made every
            # Properties.* read in those chains look unsatisfied.
            boot = reg.get("__bootstrap__" + entry,
                           reg.get("__bootstrap__ScenarioSteps", []))
            run(boot, available, wildcards, lambda _k: None, suite)
            for step in steps:
                stats["steps"] += 1

                def report(keys, _step=step, _tname=tname, _path=path,
                           _entry=entry):
                    findings.append({
                        "file": _short(_path), "test": _tname,
                        "entry": _entry, "step": _step, "keys": keys,
                    })
                    stats["unsatisfied"] += 1

                run(reg.get(step, []), available, wildcards, report, suite)
    return findings, stats, reg


def load_baseline(root: str) -> dict:
    """Keys already traced to the ReadyAPI source rather than the converter.

    Each entry carries the reason it was accepted. A key that is NOT listed
    fails the build -- that is the whole point: a new unsatisfied read means
    a value stopped reaching its request, and it must not pass silently the
    way the Salesforce token did.
    """
    path = os.path.join(root, "tools", "ctx_dataflow_baseline.json")
    try:
        import json
        with open(_lp(path), encoding="utf-8") as fh:
            return json.load(fh).get("accepted") or {}
    except (OSError, ValueError):
        return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--ignore-baseline", action="store_true",
                    help="report every unsatisfied read, accepted or not")
    args = ap.parse_args()

    findings, stats, _reg = analyse(args.root)
    baseline = {} if args.ignore_baseline else load_baseline(args.root)
    print(f"chains analysed      : {stats['chains']}")
    print(f"chained steps        : {stats['steps']}")
    print(f"unsatisfied reads    : {stats['unsatisfied']}")

    by_key = collections.Counter(f["keys"] for f in findings)
    accepted = {k: n for k, n in by_key.items() if k[0] in baseline}
    new = {k: n for k, n in by_key.items() if k[0] not in baseline}
    if baseline:
        print(f"  accepted (triaged) : {sum(accepted.values())} "
              f"across {len(accepted)} key(s)")
        print(f"  NEW                : {sum(new.values())} "
              f"across {len(new)} key(s)")

    if not new:
        print("\nEvery ctx key a chain reads is written earlier in that chain,"
              "\nexcept the triaged ReadyAPI-source cases in "
              "tools/ctx_dataflow_baseline.json.")
        return 0

    print("\nNEW unsatisfied reads -- a captured value is not reaching its request:")
    for keys, n in collections.Counter(new).most_common(args.top):
        label = keys[0] if len(keys) == 1 else " | ".join(keys)
        example = next(f for f in findings if f["keys"] == keys)
        print(f"  {n:5d}x  {label}")
        print(f"         first in {example['test']} -> .{example['step']}()")
    if args.verbose:
        print("\nall findings:")
        for f in findings:
            tag = "accepted" if f["keys"][0] in baseline else "NEW"
            print(f"  [{tag}] {f['file']}::{f['test']} "
                  f".{f['step']}() reads {f['keys']}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
