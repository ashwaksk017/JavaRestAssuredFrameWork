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


def setup_flow_steps(root: str) -> set:
    """REST step names any bootstrap setup flow performs."""
    steps = set()
    for f in glob.glob(os.path.join(root, "src/main/java/**/SetupHelper.java"),
                       recursive=True):
        for s in re.findall(r"==== REST step: (\S+)", read(f)):
            steps.add(norm(s))
    return steps


def spec_step_names(root: str) -> dict:
    """{specNN: the step name that spec performs}."""
    out = {}
    for f in glob.glob(os.path.join(root, "src/main/java/**/cases/Specs*.java"),
                       recursive=True):
        src = read(f)
        for m in re.finditer(
                r"static PhaseSpec (spec\d+)\(\)\s*\{(.*?)\n    \}", src, re.S):
            nm = re.search(r'PhaseSpec\.(?:phase|hookOnly)\("([^"]+)"',
                           m.group(2))
            if nm:
                out[m.group(1)] = norm(nm.group(1))
    return out


def covered_by_case(root: str, specs: dict, setup: set) -> dict:
    """{case id: {normalised step names the generated code will execute}}."""
    out = {}
    for f in glob.glob(os.path.join(root, "src/main/java/**/cases/*Phases.java"),
                       recursive=True):
        src = read(f)
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
                if sid in specs:
                    hit.add(specs[sid])
            if ".bootstrap(" in body:
                hit |= setup
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
