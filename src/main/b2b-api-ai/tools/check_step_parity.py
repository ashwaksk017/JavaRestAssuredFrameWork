#!/usr/bin/env python3
"""Every ReadyAPI REST step must reach something that executes it.

A converted test does not read like its ReadyAPI case. The token is fetched
inside `.start()`, trailing read-backs run inside a verify, and clustered
cases share one registration -- so counting the calls in a chain undercounts
the requests the test makes, by about one in six on the suite this was
written against. That is a legibility problem, and it has twice been
mistaken for a conversion bug.

This check answers the underlying question mechanically: for each case, is
there a phase, a verify, or a bootstrap setup flow that performs each
enabled REST step of the ReadyAPI case? A step that reaches NONE of them is
a request the converted suite will never send -- silently, because nothing
else in the output says so.

It found exactly that: cases registering no bootstrap at all, whose
`tokenRequest` therefore never runs, leaving every later call in them to go
out unauthenticated and fail as a 401 far from the cause.

Name comparison is normalised (`-`, ` `, `_` all fold together) because the
emitter sanitises step names into Java identifiers, and a raw comparison
reports hundreds of differences that are only spelling.

Exit 0 when every enabled REST step is reachable, 1 otherwise.
"""
import argparse
import collections
import glob
import io
import os
import re
import sys


def norm(name: str) -> str:
    """Fold a ReadyAPI step name to the form the emitter would sanitise it to."""
    return re.sub(r"[^A-Za-z0-9]+", "_", name or "").strip("_").lower()


def read(path: str) -> str:
    with io.open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def rest_steps_by_case(xml_path: str) -> dict:
    """{case name: [enabled REST step names]} from a ReadyAPI project XML."""
    src = read(xml_path)
    out = {}
    for m in re.finditer(r'<con:testCase[^>]*name="([^"]+)"', src):
        name = m.group(1)
        end = src.find("</con:testCase>", m.start())
        block = src[m.start():end if end > 0 else len(src)]
        steps = []
        for t in re.finditer(r"<con:testStep\s([^>]*)>", block):
            attrs = t.group(1)
            ty = re.search(r'type="(\w+)"', attrs)
            nm = re.search(r'name="([^"]*)"', attrs)
            if not ty or not nm or ty.group(1) != "restrequest":
                continue
            if 'disabled="true"' in attrs:
                continue          # ReadyAPI skips it too
            steps.append(nm.group(1))
        if steps:
            out[name] = steps
    return out


def setup_flow_steps(root: str) -> dict:
    """{suite: REST step names that suite's SetupHelper performs}.

    Per suite, not one global set. The union credited
    programaccounthonorssuite -- whose SetupHelper performs no REST step
    at all -- with programaccountregression's `tokenRequest`.
    """
    out = {}
    for f in glob.glob(os.path.join(root, "src/main/java/**/SetupHelper.java"),
                       recursive=True):
        per = out.setdefault(suite_of(f), set())
        for s in re.findall(r"==== REST step: (\S+)", read(f)):
            per.add(norm(s))
    return out


def hook_steps(root: str) -> dict:
    """{suite: {hook method: REST step names that hook performs}}.

    A bootstrap is often emitted as
    `hookOnly("bootstrap", Hooks1::hook9_bootstrap)`, which puts its REST
    calls in Hooks1.java. Reading only SetupHelper.java misses them
    entirely and the case looks like it never authenticates.
    """
    out = {}
    for f in glob.glob(os.path.join(root, "src/main/java/**/cases/Hooks*.java"),
                       recursive=True):
        src = read(f)
        per = out.setdefault(suite_of(f), {})
        # split on method headers so a step is attributed to ITS hook
        bounds = [(m.group(1), m.start()) for m in
                  re.finditer(r"static void (\w+)\(", src)]
        for i, (name, start) in enumerate(bounds):
            end = bounds[i + 1][1] if i + 1 < len(bounds) else len(src)
            steps = {norm(x) for x in
                     re.findall(r"==== REST step: (\S+)", src[start:end])}
            if steps:
                per.setdefault(name, set()).update(steps)
    return out


def spec_hooks(root: str) -> dict:
    """{suite: {specNN: [hook methods that spec references]}}."""
    out = {}
    for f in glob.glob(os.path.join(root, "src/main/java/**/cases/Specs*.java"),
                       recursive=True):
        src = read(f)
        per = out.setdefault(suite_of(f), {})
        for m in re.finditer(
                r"static PhaseSpec (spec\d+)\(\)\s*\{(.*?)\n    \}", src, re.S):
            hooks = re.findall(r"Hooks\d+::(\w+)", m.group(2))
            if hooks:
                per.setdefault(m.group(1), []).extend(hooks)
    return out


def suite_of(path: str) -> str:
    """The suite a generated file belongs to: .../support/<suite>/cases/X.java"""
    parts = os.path.normpath(path).split(os.sep)
    try:
        return parts[parts.index("support") + 1]
    except (ValueError, IndexError):
        return ""


def spec_step_names(root: str) -> dict:
    """{suite: {specNN: the step name that spec performs}}.

    Keyed BY SUITE, and that is the whole point. Every suite numbers its
    specs from 1, so once more than one suite is converted the ids
    collide: with 15 suites in the tree `spec67` is POST_Confirm_2 in
    group360createstage, GET_Groups_SingleProp in partialgoalregression
    and sf_token_Request in programaccountregression. A single flat map
    kept whichever file was globbed last, so coverage for one suite was
    judged against another suite's step names and real calls were
    reported unreachable.
    """
    out = {}
    for f in glob.glob(os.path.join(root, "src/main/java/**/cases/Specs*.java"),
                       recursive=True):
        src = read(f)
        per = out.setdefault(suite_of(f), {})
        for m in re.finditer(
                r"static PhaseSpec (spec\d+)\(\)\s*\{(.*?)\n    \}", src, re.S):
            nm = re.search(r'PhaseSpec\.(?:phase|hookOnly)\("([^"]+)"',
                           m.group(2))
            if nm:
                per[m.group(1)] = norm(nm.group(1))
    return out


HOOK_STEPS: dict = {}
SPEC_HOOKS: dict = {}


def covered_by_case(root: str, specs: dict, setup: dict) -> dict:
    """{case id: {normalised step names the generated code will execute}}."""
    out = {}
    for f in glob.glob(os.path.join(root, "src/main/java/**/cases/*Phases.java"),
                       recursive=True):
        src = read(f)
        # resolve Specs ids against THIS file's suite, never the whole tree
        suite = suite_of(f)
        suite_specs = specs.get(suite, {})
        suite_hooks = HOOK_STEPS.get(suite, {})
        suite_spec_hooks = SPEC_HOOKS.get(suite, {})
        for m in re.finditer(r"CaseRegistry\.register\((.*?)\)\n(.*?);\n",
                             src, re.S):
            target, body = m.group(1), m.group(2)
            ids = re.findall(r'"([^"]+)"', target)
            if not ids:
                # for (String id : new String[] {...}) -- the ids are above
                arrays = re.findall(r"new String\[\] \{([^}]*)\}", src[:m.start()])
                ids = re.findall(r'"([^"]+)"', arrays[-1]) if arrays else []
            hit = set()
            for p in re.finditer(r'\.(?:phase|verify)\("[^"]+",\s*"([^"]+)"', body):
                hit.add(norm(p.group(1)))
            for sid in re.findall(r"Specs\d+::(spec\d+)", body):
                if sid in suite_specs:
                    hit.add(suite_specs[sid])
                # ...and whatever REST calls that spec's own hooks make.
                # A bootstrap is usually hookOnly(...), so its token
                # request lives in a hook, not in the spec.
                for hk in suite_spec_hooks.get(sid, ()):
                    hit |= suite_hooks.get(hk, set())
            if ".bootstrap(" in body:
                hit |= setup.get(suite, set())
            for cid in ids:
                out.setdefault(cid, set()).update(hit)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=".", help="tree holding the generated output")
    ap.add_argument("--input", default="tools/ra_converter/input",
                    help="ReadyAPI XML, or a directory of them")
    ap.add_argument("--baseline", default="tools/step_parity_baseline.json",
                    help="Cases already known to lose steps. Only NEW ones "
                         "fail, so the gate catches regressions while the "
                         "known set is worked through -- the same shape as "
                         "tools/ctx_dataflow_baseline.json.")
    ap.add_argument("--write-baseline", action="store_true",
                    help="Record the current findings as the baseline.")
    args = ap.parse_args()

    root = os.path.abspath(args.root)
    xmls = ([args.input] if args.input.lower().endswith(".xml")
            else sorted(glob.glob(os.path.join(args.input, "*.xml"))))
    if not xmls:
        print("[step-parity] no input XML -- nothing to compare (ok)")
        return 0

    specs = spec_step_names(root)
    setup = setup_flow_steps(root)
    # module-level so covered_by_case can reach them without
    # threading two more arguments through every caller
    globals()['HOOK_STEPS'] = hook_steps(root)
    globals()['SPEC_HOOKS'] = spec_hooks(root)
    covered = covered_by_case(root, specs, setup)
    if not covered:
        print("[step-parity] no generated *Phases.java -- convert first (ok)")
        return 0

    total = unreachable = 0
    findings = []
    for xml in xmls:
        for case, steps in rest_steps_by_case(xml).items():
            have = covered.get(case)
            if have is None:
                # A case can be absorbed into a sibling's registration; the
                # phase-order check owns that, not this one.
                continue
            total += len(steps)
            miss = [s for s in steps if norm(s) not in have]
            if miss:
                unreachable += len(miss)
                findings.append((case, len(steps), miss))

    print("[step-parity] %d enabled REST step(s) across %d registered case(s)"
          % (total, len(covered)))
    if not findings:
        print("[step-parity] every REST step reaches a phase, verify or setup flow")
        return 0

    import json
    known = {}
    if os.path.exists(args.baseline):
        with io.open(args.baseline, encoding="utf-8") as fh:
            known = json.load(fh).get("cases", {})
    if args.write_baseline:
        with io.open(args.baseline, "w", encoding="utf-8") as fh:
            json.dump({"_comment":
                       "Cases whose REST steps reach nothing that executes "
                       "them. Recorded so the check fails on NEW losses while "
                       "these are worked through. Shrink it; never grow it.",
                       "cases": {c: sorted(m) for c, _n, m in findings}},
                      fh, indent=2, sort_keys=True)
        print("[step-parity] baseline written: %d case(s)" % len(findings))
        return 0

    fresh = [(c, n, m) for c, n, m in findings
             if sorted(m) != sorted(known.get(c, []))]
    if not fresh:
        print("[step-parity] %d step(s) in %d case(s) unreachable -- all "
              "baselined in %s"
              % (unreachable, len(findings), os.path.basename(args.baseline)))
        return 0
    findings = fresh
    print("[step-parity] %d case(s) lose steps that the baseline does not "
          "cover:" % len(findings))
    reasons = collections.Counter()
    for case, n, miss in findings:
        print("    %-58s %d/%d step(s) unreachable: %s"
              % (case[:58], len(miss), n, ", ".join(miss[:4])))
        for s in miss:
            reasons[norm(s)] += 1
    print()
    print("  most common unreachable step: %s"
          % ", ".join("%s x%d" % kv for kv in reasons.most_common(3)))
    print("  A step listed here is a request ReadyAPI sends and the converted")
    print("  suite does not. `tokenRequest` here means the case registers no")
    print("  bootstrap, so every later call in it goes out unauthenticated.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
