"""Before writing a test, find out whether one already exists.

    python tools/agent/locate.py --job j1
    python tools/agent/locate.py --job j1 --index target/shape-index.json

Stage 3 of the story-to-test plan. Reads the brief's requests from
`target/agent/<job>/intake.json`, matches each against everything
already converted, and writes a plan saying -- per request -- whether to
create, update, or stop.

WHY THIS STAGE EXISTS AT ALL
----------------------------
Without it the agent's only possible verdict is "write a new test",
because that is the only thing it can do without knowing what is there.
That answer is wrong in the two cases that matter most:

  * the call is already covered by a GENERATED test, so the change
    belongs upstream in the ReadyAPI XML. Writing it here produces a
    hand-written test that the next convert does not know about and
    nobody reconciles.
  * the call is already covered by a HAND-WRITTEN test, so this is an
    update -- a new data row or an extra assertion -- not a second class
    asserting almost the same thing.

`tools/jira/shape_match.py` already answers this, using the converter's
own clustering signature rather than a guess. This stage is the adapter:
intake's requests in, a per-request decision out.

THE DECISIONS
-------------
    create    nothing covers this call
    update    a hand-written test covers it; add to that file
    upstream  a GENERATED test covers it; change the ReadyAPI XML
    confirm   matched only loosely, or looks like a duplicate -- ask

`upstream` and `confirm` are stops. A stop here is the point: this stage
exists to prevent writing, not to authorise it.
"""
from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
_JIRA = os.path.join(TOOLS, "jira")


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise SystemExit(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sys.path.insert(0, _JIRA)      # the jira modules import their siblings
projectconfig = _load("locate_projectconfig",
                      os.path.join(_JIRA, "projectconfig.py"))
shapes = _load("locate_shapes", os.path.join(_JIRA, "shapes.py"))
shape_match = _load("locate_shape_match", os.path.join(_JIRA, "shape_match.py"))
jira_extract = _load("locate_extract", os.path.join(_JIRA, "extract.py"))

ROOT = projectconfig.ROOT
DEFAULT_INDEX = os.path.join("target", "shape-index.json")

CREATE, UPDATE, UPSTREAM, CONFIRM = "create", "update", "upstream", "confirm"

STOPS = (UPSTREAM, CONFIRM)

WHY = {
    CREATE: "nothing automated covers this call",
    UPDATE: "a hand-written test covers it -- add a row or an assertion "
            "there rather than a second class",
    UPSTREAM: "a GENERATED test covers it. The change belongs in the "
              "ReadyAPI XML; a hand-written test here is invisible to the "
              "next convert",
    CONFIRM: "matched only loosely, or looks like a duplicate -- a human "
             "decides this one",
}


def job_dir(job: str, root: str = "") -> str:
    # Reuse intake's validation rather than restate it: the id names a
    # directory and arrives from a UI text box.
    intake = _load("locate_intake", os.path.join(HERE, "intake.py"))
    return intake.job_dir(job, root)


def templatize(raw_path: str, templates: list) -> dict:
    """A concrete path as the recorded template it is an instance of.

    THE STEP THAT CANNOT BE SKIPPED. A pasted curl carries
    `/props/LEXQQ/groups`; the converted suite records
    `/props/{propCode}/groups`. Those are different strings, so matching
    the concrete form against the index answers "nothing covers this
    call" for every path that has a parameter -- which is most of them.
    That is the worst available answer: it reads as permission to write a
    test that already exists.

    `tools/jira/extract.py` already does this for the story chain; this
    is the same call, against the same index.
    """
    out = {"path": raw_path, "templated": False, "alternatives": [],
           "path_params": {}, "examples": {}}
    if not raw_path or not templates:
        return out
    t = jira_extract.templatize(raw_path, templates)
    if t.get("matched"):
        out["path"] = t["matched"]
        out["templated"] = True
    out["alternatives"] = t.get("alternatives") or []
    # The literal that filled the template becomes a REF, not a value.
    # `_param_binding_sig` keys on the BINDING KIND -- "row" for a
    # literal, "ref" for a reference -- so a pasted `/props/LEXQQ/groups`
    # carrying `propCode=LEXQQ` signs as "row" while the recorded step
    # signs as "ref", and the two never match. extract.py makes the same
    # translation for the story chain, and says why: 8,748 of 9,073
    # recorded path params are refs, so the literal is a CSV cell rather
    # than structure. The literals are kept as examples.
    refs, examples = jira_extract._as_refs(t.get("params") or {})
    out["path_params"], out["examples"] = refs, examples
    return out


def candidate_from(request: dict, job: str, templates: list = ()) -> dict:
    """One intake request as a shape_match candidate.

    intake calls it `raw_path`; shape_match wants `path`. Query names
    become empty-valued params because the signature keys on the NAMES --
    a value would make two tests of the same call look different.
    """
    t = templatize(request.get("raw_path") or "", list(templates or []))
    return {
        "issue_key": job,
        "steps": [{
            "verb": (request.get("verb") or "").upper(),
            "path": t["path"],
            "body": request.get("body") or "",
            "media_type": request.get("media_type") or "application/json",
            "query_params": {n: "" for n in
                             (request.get("query_param_names") or [])},
            "path_params": t["path_params"],
        }],
        "_templatized": t,
    }


def is_generated(entry: dict) -> bool:
    """Does a matched test belong to the converter?

    `shape_match.destination` is the single source of truth for this --
    it already knows that `.tests.imported.` and a generated csv path
    mean the converter owns the test. Restating the rule here would let
    the two drift, and the direction it would drift in is the dangerous
    one: a generated test read as hand-written gets edited in place and
    silently overwritten by the next convert.
    """
    return "ReadyAPI XML" in (shape_match.destination(entry or {}) or "")


def _entries(result: dict) -> list:
    """Matched tests, strongest evidence first."""
    return (result.get("duplicate_candidates")
            or result.get("exact_matches")
            or result.get("loose_matches") or [])


def decide(result: dict) -> tuple[str, list]:
    """(decision, the entries it was based on)."""
    entries = _entries(result)
    verdict = result.get("verdict")

    if verdict == shape_match.NEW or not entries:
        return CREATE, []

    # A generated match wins over everything else. Writing a hand-written
    # test for a call the converter already emits is the one outcome this
    # stage exists to prevent, so it is checked before the duplicate and
    # loose cases rather than after.
    #
    # ASK shape_match rather than reading a `destination` key: match()
    # returns RAW index entries, which have no such key -- only its
    # report path adds one. Trusting the key classified every generated
    # test as hand-written, and the unit tests missed it because their
    # fixtures supplied the key that real entries lack.
    generated = [e for e in entries if is_generated(e)]
    if generated:
        return UPSTREAM, generated

    if verdict in (shape_match.DUPLICATE_SUSPECT, shape_match.LOOSE_ONLY):
        return CONFIRM, entries
    return UPDATE, entries


def load_index(index_path: str, rebuild: bool = False) -> tuple[dict, str]:
    """(index, a note about where it came from). Builds when absent."""
    path = (index_path if os.path.isabs(index_path)
            else os.path.join(ROOT, index_path.replace("/", os.sep)))
    if not rebuild and os.path.isfile(path):
        return shapes.load(path), f"loaded {os.path.relpath(path, ROOT)}"
    index = shapes.build()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    shapes.save(index, path)
    return index, (f"BUILT {os.path.relpath(path, ROOT)} -- it is a snapshot "
                   f"of what is converted now, so rebuild it after a convert")


def run(job: str, root: str = "", index_path: str = DEFAULT_INDEX,
        rebuild: bool = False) -> dict:
    out = job_dir(job, root)
    intake_path = os.path.join(out, "intake.json")
    if not os.path.isfile(intake_path):
        raise SystemExit(
            f"no intake for job {job!r}. Run tools/agent/intake.py first -- "
            f"this stage decides what to do with requests somebody already "
            f"read, it does not read them itself.")
    with io.open(intake_path, encoding="utf-8") as fh:
        intake = json.load(fh)

    requests = intake.get("requests") or []
    if not requests:
        raise SystemExit(
            f"the intake for job {job!r} found no request. There is nothing "
            f"to locate -- see brief.md for what it looked at.")

    index, index_note = load_index(index_path, rebuild)

    templates = jira_extract.path_templates(index or {})

    decisions = []
    for i, req in enumerate(requests, 1):
        cand = candidate_from(req, job, templates)
        t = cand.pop("_templatized")
        result = shape_match.match(cand, index)
        decision, entries = decide(result)
        decisions.append({
            "n": i,
            "verb": req.get("verb"),
            "path": t["path"],
            "pasted_path": req.get("raw_path"),
            "templated": t["templated"],
            "path_alternatives": t["alternatives"][:5],
            "provenance": req.get("provenance", ""),
            "decision": decision,
            "why": WHY[decision],
            "verdict": result.get("verdict"),
            "match_level": result.get("match_level"),
            "matches": [{
                "java_class_fqn": e.get("java_class_fqn"),
                "java_method": e.get("java_method"),
                "csv_path": e.get("csv_path"),
                "suite": e.get("suite"),
                "destination": shape_match.destination(e),
            } for e in entries[:5]],
        })

    plan = {
        "job": job,
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "index": index_note,
        "decisions": decisions,
        "stops": [d for d in decisions if d["decision"] in STOPS],
    }
    plan["ok"] = not plan["stops"]
    _write_plan(out, plan)
    with io.open(os.path.join(out, "locate.json"), "w", encoding="utf-8") as fh:
        json.dump(plan, fh, indent=2, ensure_ascii=False)
    return plan


def _write_plan(out: str, plan: dict) -> None:
    counts = {}
    for d in plan["decisions"]:
        counts[d["decision"]] = counts.get(d["decision"], 0) + 1
    summary = ", ".join(f"{n} {k}" for k, n in sorted(counts.items()))

    lines = [f"# Plan: {summary}", ""]
    if plan["stops"]:
        lines += [f"**{len(plan['stops'])} request(s) stop here.** Nothing is "
                  f"written until they are resolved.", ""]
    lines += [f"- job: {plan['job']}", f"- at: {plan['at']}",
              f"- index: {plan['index']}", ""]

    for d in plan["decisions"]:
        lines += [f"## {d['n']}. `{d['verb']} {d['path']}` -> **{d['decision'].upper()}**",
                  "",
                  f"{d['why']}.",
                  "",
                  f"- shape verdict: {d['verdict']}"
                  + (f" (at {d['match_level']} level)"
                     if d.get("match_level") else ""),
                  f"- read from: {d['provenance']}"]
        if d.get("templated"):
            lines.append(f"- pasted as `{d['pasted_path']}`, matched against "
                         f"the recorded template")
        if d.get("path_alternatives"):
            lines.append(f"- other templates it could be: "
                         + ", ".join(f"`{a}`" for a in d["path_alternatives"]))
        if d["matches"]:
            lines.append("- already covered by:")
            for m in d["matches"]:
                where = m.get("java_class_fqn") or m.get("suite") or "?"
                meth = m.get("java_method") or ""
                lines.append(f"    - `{where}#{meth}`")
                lines.append(f"      {m.get('destination', '')}")
        lines.append("")

    lines += ["## Next", ""]
    if plan["stops"]:
        lines.append("Resolve the stops above. `UPSTREAM` means the change "
                     "belongs in the ReadyAPI XML and then a reconvert; "
                     "`CONFIRM` means a person decides whether this is the "
                     "same call.")
    else:
        lines.append("Stage 4 proposes the edits for the create/update "
                     "requests. Nothing has been written yet.")
    with io.open(os.path.join(out, "plan.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines).rstrip() + "\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--job", required=True)
    ap.add_argument("--root", default="")
    ap.add_argument("--index", default=DEFAULT_INDEX)
    ap.add_argument("--rebuild-index", action="store_true",
                    help="the index is a snapshot of what is converted NOW; "
                         "rebuild it after a convert")
    args = ap.parse_args(argv)

    plan = run(args.job, root=args.root, index_path=args.index,
               rebuild=args.rebuild_index)
    for d in plan["decisions"]:
        print(f"  {d['decision']:>8}  {d['verb']} {d['path']}")
    out = job_dir(args.job, args.root)
    print(f"  {os.path.join(os.path.relpath(out, ROOT), 'plan.md')}")
    if plan["stops"]:
        print(f"\n{len(plan['stops'])} request(s) stop here -- see plan.md")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
