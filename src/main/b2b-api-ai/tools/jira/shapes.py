"""The shape index: every request this tree already covers.

WHY THIS AND NOT A NEW MATCHER
------------------------------
"Does the endpoint match? the body? the path params? the query params?"
is already one function in the converter: `_rest_shape_sig`, which keys a
case on (verb, path, media-type, body-shape, path-param binding,
query-param names) per REST step. Its own docstring states the rule that
makes "add a row" correct:

    Path/query *values* are not in the key -- they become CSV cells when
    the cluster has more than one ReadyAPI case.

So this module imports that function rather than re-deriving it. A second
definition of "same request" would drift from the first, and in this
repository that has already happened twice -- `_normalize_jdbc_query`
landed on two of three code paths, and a check name derived by string
surgery produced `phase_order` where the real name was `phase-order`.

TWO TIERS, AND WHY THE SECOND IS NOT A VERDICT
----------------------------------------------
`_shape_sig` captures keys and leaf TYPES, and an array's signature
carries its length. A ReadyAPI body holds placeholders where a Jira story
holds literal values:

    ReadyAPI   "peakRooms": "${Inputs#peakRooms}"   -> S
    Jira story "peakRooms": 5                       -> N

Identical request, different signature. Array arity does the same thing: a
two-item example will not match a three-item recording. So an exact
signature answers "is this provably the same call", and a LOOSE signature
-- scalar leaves collapsed to one token, array arity dropped -- answers
"is this probably the same call, modulo how the example was written".

Exact is a verdict. Loose is a candidate that a person confirms. Reporting
loose as exact would quietly attach a story to the wrong cluster, and in
this converter a wrong cluster means the new case inherits cluster[0]'s
body -- the most expensive error class in its history.

    python tools/jira/shapes.py --dump target/shape-index.json
    python tools/jira/shapes.py --selfcheck
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "tools", "ra_converter"))

import ra_converter as rc  # noqa: E402


class _Quiet:
    """Swallow the converter's progress reporting while parsing.

    `parse_test_suites` prints its disabled-step and disabled-case
    inventory -- hundreds of lines per suite. Useful during a convert,
    noise in a matcher a tester runs from a terminal. Errors still
    propagate; only stdout is held.
    """

    def __enter__(self):
        self._real = sys.stdout
        sys.stdout = io.StringIO()
        return self

    def __exit__(self, *exc):
        sys.stdout = self._real
        return False

INPUT_DIR = os.path.join(ROOT, "tools", "ra_converter", "input")
AUDIT_DIR = os.path.join(ROOT, "_audit")
LONG = "\\\\?\\"          # Windows extended-length prefix; paths here exceed 260


# --- signatures -------------------------------------------------------
def exact_sig(case) -> str:
    """The converter's own signature, as a stable string."""
    return json.dumps(rc._rest_shape_sig(case), default=list)


def _loose_node(node) -> str:
    """`_shape_sig`, with every scalar collapsed and array arity dropped.

    Mirrors the converter's walker instead of rewriting its output
    string. The string approach was wrong twice over:

      * `[SNB_]` also matches KEY names. `{Status:S,Name:S}` became
        `{Vtatus:V,Vame:V}` -- two different keys can then collide, and
        the tier stops meaning anything.
      * dropping only the `*N` suffix leaves `[{id:V}|{id:V}*]`, which
        still differs from a one-element `[{id:V}*]`. Arity has to go
        from the element list too, not just the count.
    """
    if isinstance(node, dict):
        return "{" + ",".join(
            f"{k}:{_loose_node(v)}" for k, v in sorted(node.items())) + "}"
    if isinstance(node, list):
        if not node:
            return "[]"
        # Distinct element shapes, order- and count-independent: a
        # two-item example and a three-item recording of the same thing
        # must land together.
        return "[" + "|".join(sorted({_loose_node(x) for x in node[:20]})) + "*]"
    return "V"          # every scalar, None included


def loose_body_key(step) -> str:
    """`_body_shape_key` for the loose tier.

    Reuses the converter's own `soapui_body_to_placeholders` so the body
    is translated identically; only the signature walker differs.
    """
    if not (step.request_body or "").strip():
        return "-"
    translated, _ = rc.soapui_body_to_placeholders(step.request_body)
    try:
        return "j:" + _loose_node(json.loads(translated))
    except (ValueError, TypeError):
        return "raw:" + re.sub(r"\s+", "", translated)[:120]


def loose_sig(case) -> str:
    """Per-step signature with the body loosened and nothing else.

    Verb, path, media type and param names stay exact: a story states
    those outright, and there is no reason to be generous about them.
    """
    out = []
    for s in case.steps:
        if not isinstance(s, rc.RestStep):
            continue
        out.append((
            s.http_method,
            s.resource_path,
            (s.media_type or "application/json").split(";")[0].strip().lower(),
            loose_body_key(s),
            rc._param_binding_sig(getattr(s, "path_params", None)),
            rc._param_names(getattr(s, "query_params", None)),
        ))
    return json.dumps(tuple(out), default=list)


# Expected status is read from the audit mapping, NOT from the parsed
# case. `RestStep` has no `expected_status` field and `Assertion` exposes
# only (type, name, disabled, config, elements), so an earlier version of
# this that dug through those attributes returned "" for all 1,161
# entries -- silently, because an empty string is a plausible answer. The
# converter already resolves this and writes it to
# `_audit/<suite>/case_to_method_mapping.csv`, where 744 of 1,161 rows
# carry a value.
#
# It is deliberately NOT part of either signature: a 200 and a 400 of the
# same call share one method and carry the difference per row in
# `expected_<step>_status_code`. It is recorded so a match can say WHICH
# row to add, and so a true duplicate (same shape, same values, same
# status) can be told from a new row.


# --- building a candidate from a story --------------------------------
def candidate_case(steps: list[dict], case_id: str = "CANDIDATE") -> "rc.TestCase":
    """A real TestCase of real RestSteps, built from extracted request(s).

    The classes are the converter's own dataclasses, not stand-ins, so
    `_body_shape_key` and `_param_binding_sig` see exactly what they see
    during a convert. That removes a whole category of "the shim was not
    faithful" wrongness, which would have looked like poor matching
    quality rather than a plumbing fault.
    """
    rest = []
    for i, s in enumerate(steps, 1):
        rest.append(rc.RestStep(
            step_name=s.get("step_name") or f"step{i}",
            service=s.get("service", ""),
            method_name=s.get("method_name", ""),
            resource_path=s["path"],
            http_method=(s.get("verb") or "POST").upper(),
            endpoint=s.get("endpoint", ""),
            original_uri=s.get("original_uri", ""),
            media_type=s.get("media_type") or "application/json",
            request_body=s.get("body") or "",
            headers=s.get("headers") or {},
            path_params=s.get("path_params") or {},
            query_params=s.get("query_params") or {},
        ))
    return rc.TestCase(id=case_id, name=case_id, description="", steps=rest)


# --- the audit join: where a matched case actually lives ---------------
def method_map() -> dict:
    """{(suite, soapui_case): {java_class_fqn, java_method, csv_path, ...}}

    From `_audit/<suite>/case_to_method_mapping.csv`, which the converter
    writes. This is what turns "the shape matches" into "add a row to
    THIS csv, read by THIS method".
    """
    out: dict = {}
    if not os.path.isdir(AUDIT_DIR):
        return out
    for suite in sorted(os.listdir(AUDIT_DIR)):
        p = os.path.join(AUDIT_DIR, suite, "case_to_method_mapping.csv")
        if not os.path.isfile(p):
            continue
        with io.open(LONG + os.path.abspath(p), encoding="utf-8-sig",
                     newline="") as fh:
            for row in csv.DictReader(fh):
                key = (suite, (row.get("soapui_case") or "").strip())
                out[key] = {k: (row.get(k) or "").strip() for k in
                            ("xray_key", "java_class_fqn", "java_method",
                             "csv_path", "cluster_size", "cluster_row_index",
                             "expected_status")}
    return out


# --- the index --------------------------------------------------------
def suites_from_inputs() -> list[tuple[str, str]]:
    """[(suite name, xml path)] -- lowercased, '-' to '_', as the tree is."""
    if not os.path.isdir(INPUT_DIR):
        return []
    out = []
    for n in sorted(os.listdir(INPUT_DIR)):
        if n.lower().endswith(".xml"):
            out.append((os.path.splitext(n)[0].lower().replace("-", "_"),
                        os.path.join(INPUT_DIR, n)))
    return out


def build(verbose: bool = False) -> dict:
    mmap = method_map()
    exact: dict = {}
    loose: dict = {}
    cases = 0
    rest_cases = 0
    no_rest = 0
    unmapped = 0

    for suite, xml in suites_from_inputs():
        try:
            with _Quiet():
                parsed = rc.parse_test_suites(xml)
        except Exception as e:                      # a bad XML is reported, not fatal
            if verbose:
                print(f"  !! {suite}: {type(e).__name__}: {e}")
            continue
        n = 0
        for _soapui_suite, suite_cases in parsed:
            for case in suite_cases:
                cases += 1
                n += 1
                if not any(isinstance(s, rc.RestStep) for s in case.steps):
                    no_rest += 1
                    continue
                rest_cases += 1
                m = mmap.get((suite, case.name)) or mmap.get((suite, case.id)) or {}
                if not m:
                    unmapped += 1
                entry = {
                    "suite": suite,
                    "case_id": case.id,
                    "case_name": case.name,
                    "rest_steps": sum(1 for s in case.steps
                                      if isinstance(s, rc.RestStep)),
                    "expected_status": m.get("expected_status", ""),
                    "java_class_fqn": m.get("java_class_fqn", ""),
                    "java_method": m.get("java_method", ""),
                    "csv_path": m.get("csv_path", ""),
                    "xray_key": m.get("xray_key", ""),
                }
                exact.setdefault(exact_sig(case), []).append(entry)
                loose.setdefault(loose_sig(case), []).append(entry)
        if verbose:
            print(f"  {suite:<40} {n} case(s)")

    return {
        "schema": 1,
        "counts": {"cases": cases, "with_rest_steps": rest_cases,
                   "no_rest_steps": no_rest, "unmapped_to_method": unmapped,
                   "exact_shapes": len(exact), "loose_shapes": len(loose)},
        "exact": exact,
        "loose": loose,
    }


def load(path: str) -> dict:
    with io.open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save(index: dict, path: str) -> str:
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(index, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dump", metavar="PATH")
    g.add_argument("--selfcheck", action="store_true",
                   help="every indexed case must match itself exactly")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    index = build(args.verbose)
    c = index["counts"]
    print(f"cases parsed              : {c['cases']}")
    print(f"  with REST steps         : {c['with_rest_steps']}")
    print(f"  no REST step (skipped)  : {c['no_rest_steps']}")
    print(f"  not mapped to a method  : {c['unmapped_to_method']}")
    print(f"distinct exact shapes     : {c['exact_shapes']}")
    print(f"distinct loose shapes     : {c['loose_shapes']}")

    if args.dump:
        save(index, args.dump)
        print(f"-> {args.dump}")
        return 0

    # --selfcheck: the spike. Re-score every indexed case through the
    # SAME path a Jira candidate takes -- rebuild it from its own step
    # fields via candidate_case() -- and require it to land on itself.
    # A failure here means the reconstruction is lossy, which would show
    # up later as unexplainable matching quality rather than a bug.
    exact = index["exact"]
    checked = ok = lost = 0
    examples = []
    for suite, xml in suites_from_inputs():
        try:
            with _Quiet():
                parsed = rc.parse_test_suites(xml)
        except Exception:
            continue
        for _s, suite_cases in parsed:
            for case in suite_cases:
                rest = [s for s in case.steps if isinstance(s, rc.RestStep)]
                if not rest:
                    continue
                checked += 1
                rebuilt = candidate_case([{
                    "step_name": s.step_name, "path": s.resource_path,
                    "verb": s.http_method, "media_type": s.media_type,
                    "body": s.request_body, "headers": s.headers,
                    "path_params": s.path_params,
                    "query_params": s.query_params,
                } for s in rest], case_id=case.id)
                if exact_sig(rebuilt) in exact:
                    ok += 1
                else:
                    lost += 1
                    if len(examples) < 5:
                        examples.append(f"{suite}/{case.name}")
    print(f"\nself-check: {ok}/{checked} case(s) matched themselves exactly")
    if lost:
        print(f"  {lost} did NOT. The reconstruction is lossy for:")
        for e in examples:
            print(f"    {e}")
        return 1
    print("  the candidate path is faithful: a story-derived request is "
          "scored by the same code that scored the recording.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
