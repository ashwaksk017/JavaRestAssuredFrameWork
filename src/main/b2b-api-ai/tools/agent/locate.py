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

THREE THINGS THE SHAPE INDEX DOES NOT KNOW (coverage.py)
--------------------------------------------------------
* HAND-WRITTEN TESTS are not in the shape index, which is built from the
  ReadyAPI sources. They are read from the committed code each run. A
  request nothing generated covers, that a hand-written test makes, is
  `update` -- matched by verb and path, which is all code can show.
  Only a @Test method counts; a step or a helper no test uses, and a
  path the code only starts (`"/a/" + id`), are notes and decide nothing.
  A pasted path is read against the RECORDED templates first, so a
  hand-written template can never hide a generated match.
* THE DATA. For each test a request matches, its CSV is read: is a row
  with these values already there, is a new row enough, or does the
  request carry a field no column can hold? "The data is already there"
  turns an `update` into `confirm`: adding it again is the duplicate this
  stage exists to stop.
* A STORY WITH NO REQUEST IN IT. When the story's own text gave none and
  "Design the tests" has run, the designed endpoints are the requests.
  They have no body, so they are matched as calls; one that a generated
  test makes is `confirm`, never permission to write.

None of this turns a stop into a go: an `upstream` or a `confirm` from
the shape index stays what it was.
"""
from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
import re
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))


def _diag():
    """tools/diag.py, once for the whole process: the record of what this
    command did, kept for finding out what went wrong."""
    import importlib.util as _ilu
    if "workbench_diag" not in sys.modules:
        _spec = _ilu.spec_from_file_location(
            "workbench_diag", os.path.join(os.path.dirname(HERE), "diag.py"))
        _mod = _ilu.module_from_spec(_spec)
        sys.modules["workbench_diag"] = _mod
        try:
            _spec.loader.exec_module(_mod)
        except BaseException:
            sys.modules.pop("workbench_diag", None)
            raise
    return sys.modules["workbench_diag"]


diag = _diag()
TOOLS = os.path.dirname(HERE)
_JIRA = os.path.join(TOOLS, "jira")


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise SystemExit(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    # Registered before it runs: a @dataclass under postponed annotations
    # looks its own module up in sys.modules while it is being defined.
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(name, None)
        raise
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
    CONFIRM: "matched only loosely, looks like a duplicate, or the path "
             "fits several recorded templates -- a human decides this one",
}


# Loaded ONCE. This was re-executing intake.py on every job_dir call --
# four call sites per run -- which re-runs its module-level code each
# time for no gain.
intake = _load("locate_intake", os.path.join(HERE, "intake.py"))
coverage = _load("locate_coverage", os.path.join(HERE, "coverage.py"))
# Evidence shown beside the plan. Deciding does not depend on it, so a
# fault in it must not stop a decision from being made.
try:
    history = _load("locate_history", os.path.join(HERE, "history.py"))
    _HISTORY_ERROR = ""
except Exception as _e:                                  # noqa: BLE001
    diag.trace()
    history, _HISTORY_ERROR = None, type(_e).__name__


def story_history(out: str, root: str) -> dict:
    """Commits and test files that name this job's story (history.py), or
    a record that the lookup failed -- never an exception, never silence."""
    if history is None:
        return {"keys": [], "error": _HISTORY_ERROR}
    try:
        return history.build(out, root)
    except Exception as e:                               # noqa: BLE001
        diag.trace()
        return {"keys": [], "error": type(e).__name__}


def _history_lines(plan: dict) -> list:
    found = plan.get("history") or {}
    if history is None:
        return (["## Earlier work that names this story", "",
                 f"This could not be looked up ({found.get('error', 'unavailable')}). "
                 f"It is not evidence that nothing exists.", ""] if found else [])
    return history.render(found)


def job_dir(job: str, root: str = "") -> str:
    # Reuse intake's validation rather than restate it: the id names a
    # directory and arrives from a UI text box.
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
            # Without this shape_match's want_status is empty and
            # DUPLICATE_SUSPECT can never fire -- one of the two stops
            # this stage exists to raise, silently disabled.
            "expected_status": request.get("expected_status") or "",
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


def decide(result: dict, ambiguous_path: bool = False) -> tuple[str, list]:
    """(decision, the entries it was based on)."""
    entries = _entries(result)
    verdict = result.get("verdict")

    if verdict == shape_match.NEW or not entries:
        # A path that fits SEVERAL recorded templates was handed to
        # shape_match in its concrete form, because nothing could narrow
        # it to one. That matches nothing and reads as NEW -- but we know
        # two or more recorded calls look like this, so "write a new
        # test" is the one answer we are sure is unsafe.
        if ambiguous_path:
            return CONFIRM, []
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


def newest_emitted_test(root: str = "") -> float:
    """mtime of the most recently written generated test, or 0."""
    base = os.path.join(root or ROOT, "src", "test", "java", "com", "hi",
                        "api", "tests")
    newest = 0.0
    for sub in ("imported", "classic"):
        d = os.path.join(base, sub)
        if not os.path.isdir(d):
            continue
        for dirpath, _dirs, files in os.walk(d):
            for fn in files:
                if not fn.endswith(".java"):
                    continue
                try:
                    newest = max(newest, os.path.getmtime(
                        os.path.join(dirpath, fn)))
                except OSError:
                    pass
    return newest


def staleness(index_file: str) -> str:
    """A warning when the tree was converted after the index was built.

    The index is a SNAPSHOT of what was converted when it was made. A
    convert since then means every verdict here is answered against the
    old tree -- and the direction of that error is the dangerous one: a
    call that IS now covered reads as NEW, which is permission to write
    a duplicate. The existing note said "rebuild after a convert"; this
    checks instead of asking.
    """
    try:
        built = os.path.getmtime(index_file)
    except OSError:
        return ""
    newest = newest_emitted_test()
    if newest and newest > built:
        return (" -- STALE: the converted tree has changed since this index "
                "was built. Re-run with --rebuild-index, or every verdict "
                "here describes the previous tree")
    return ""


def load_index(index_path: str, rebuild: bool = False) -> tuple[dict, str]:
    """(index, a note about where it came from). Builds when absent."""
    path = (index_path if os.path.isabs(index_path)
            else os.path.join(ROOT, index_path.replace("/", os.sep)))
    if not rebuild and os.path.isfile(path):
        return (shapes.load(path),
                f"loaded {os.path.relpath(path, ROOT)}{staleness(path)}")
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
    requests_note = ""
    if not requests:
        # A story written as prose. If the tests have been designed, the
        # designed endpoints are the requests.
        requests, requests_note = coverage.design_requests(out)
        if not requests:
            raise SystemExit(
                f"the intake for job {job!r} found no request"
                + (f", and {requests_note}" if requests_note else "")
                + ". There is nothing to locate -- see brief.md for what it "
                  "looked at. For a story with no request in it, use Design "
                  "the tests first: the endpoints it proposes are then "
                  "checked here.")
        requests_note = ("The story held no request. These are the endpoints "
                         "of the designed test cases (design.json); they carry "
                         "no body, so each is matched as a CALL."
                         + (f" Note: {requests_note}." if requests_note else ""))

    diag.step("Rebuilding the shape index from the ReadyAPI sources (this can take minutes)" if rebuild else "Loading the shape index of the converted suites")
    index, index_note = load_index(index_path, rebuild)
    here = root or ROOT
    diag.step("Reading the hand-written tests")
    seen = coverage.scan(here)
    hand_calls = seen["calls"]
    generated_calls = coverage.call_index(index or {})

    # A pasted concrete path is read against the RECORDED templates first,
    # exactly as before. Only a path none of them fits is then read against
    # the templates hand-written tests use. Offering both at once let a
    # hand-written `/v1/guests/{guestId}/businesses` claim a pasted
    # `/v1/guests/123/businesses` before the recorded `/guests/{guestId}/
    # businesses` (which fits once the prefix is trimmed) was tried -- and
    # an UPSTREAM became an UPDATE.
    templates = jira_extract.path_templates(index or {})
    hand_templates = [p for p in coverage.handwritten_templates(hand_calls)
                      if p not in templates]

    diag.step(f"Comparing {len(requests)} request(s) with what is already automated")
    decisions = []
    for i, req in enumerate(requests, 1):
        # `/guests/123/businesses/` is `/guests/123/businesses`. Left as
        # pasted, the extra slash made it fit no template, the shape index
        # was asked about a path it had never seen, and said NEW.
        raw = str(req.get("raw_path") or "")
        tidy = re.sub(r"/{2,}", "/", raw.split("?", 1)[0]).rstrip("/") or raw
        if tidy != raw:
            req = dict(req, raw_path=tidy)
        cand = candidate_from(req, job, templates)
        t = cand["_templatized"]
        # Whether the recorded templates left the path undecided is settled
        # HERE, before any hand-written template is looked at: that stop is
        # not one a hand-written test can lift.
        ambiguous = bool(t["alternatives"]) and not t["templated"]
        if not t["templated"] and hand_templates:
            again = candidate_from(req, job, hand_templates)
            if again["_templatized"]["templated"]:
                again["_templatized"]["alternatives"] = (
                    t["alternatives"] + again["_templatized"]["alternatives"])
                cand = again
            elif again["_templatized"]["alternatives"]:
                # Several hand-written templates fit and none could be
                # chosen: as undecided as it is among recorded ones.
                ambiguous = True
                t["alternatives"] = t["alternatives"] + again["_templatized"]["alternatives"]
        t = cand.pop("_templatized")
        result = shape_match.match(cand, index)
        decision, entries = decide(result, ambiguous_path=ambiguous)
        unsure = ""
        if decision == UPDATE and ambiguous:
            unsure = ("the path fits more than one template, so which call this is cannot "
                      "be told; a person decides")
        elif decision == UPDATE and not any(e.get("java_class_fqn") for e in entries):
            unsure = ("it matches a recorded case that is mapped to no test, so there is "
                      "nothing to add to; a person decides")
        why, notes = WHY[decision], []
        if unsure:
            decision, why = CONFIRM, unsure
        has_body = bool(str(req.get("body") or "").strip())
        asked_path = req.get("raw_path") or t["path"]
        made = (coverage.handwritten_matches(hand_calls, req.get("verb"), t["path"])
                or coverage.handwritten_matches(hand_calls, req.get("verb"), asked_path))
        # A TEST that makes the call covers it. A step of the DSL that makes
        # it, used by no test, covers nothing -- but is what a new test
        # should be written with.
        # ...and a path the code only STARTS (`"/guests/" + id + ...`) is a
        # likeness: it is said, and decides nothing.
        hand = [c for c in made if c.get("is_test") and not c.get("partial")]
        steps = [c for c in made if not c.get("is_test") and not c.get("partial")]
        alike = [c for c in made if c.get("is_test") and c.get("partial")]
        also = (coverage.generated_call_matches(generated_calls, req.get("verb"), t["path"])
                if decision == CREATE else [])
        if decision == CREATE and also and req.get("from_design"):
            # Named by the design, with no request behind it: "the same
            # call" is all that can be said, and a generated test makes it.
            # Not permission to write. (A PASTED request with no body -- a
            # GET -- was compared in full by the shape index, and its
            # CREATE stands.)
            decision, entries = CONFIRM, also
            why = ("the design named this endpoint, with no request to compare, and "
                   "generated test(s) already call it. Whether the story is covered cannot "
                   "be told from a name: a person decides (if it is covered, the change "
                   "belongs upstream)")
            if hand:
                notes.append(f"{len(hand)} hand-written test(s) make this call as well, e.g. "
                             f"{hand[0]['java_class_fqn']}#{hand[0]['java_method']}")
        elif decision == CREATE and len({coverage.path_key(c["path"]) for c in hand}) > 1:
            # Tests of DIFFERENT paths all fit what was pasted
            # (`/guests/{guestId}/businesses` and `/guests/123/{kind}`).
            decision, entries = CONFIRM, hand
            why = ("hand-written tests of more than one path fit this request, so which "
                   "of them it belongs to cannot be told; a person decides")
        elif decision == CREATE and hand:
            decision, entries = UPDATE, hand
            why = ("a hand-written test already makes this call (matched by verb and "
                   "path; the body was not compared) -- add to it rather than writing "
                   "a second class")
            if also:
                notes.append(f"{len(also)} generated test(s) call this endpoint too, in "
                             f"another shape, e.g. "
                             f"{also[0].get('java_class_fqn') or also[0].get('suite')}"
                             f"#{also[0].get('java_method') or ''}. Those are the "
                             f"converter's: the addition goes in the hand-written test")
        elif decision == CREATE and also:
            notes.append(f"the same endpoint is called by {len(also)} generated test(s) "
                         f"with a different {'body' if has_body else 'set of parameters'}, e.g. "
                         f"{also[0].get('java_class_fqn') or also[0].get('suite')}"
                         f"#{also[0].get('java_method') or ''}. This request is "
                         f"not one of theirs, so it is new")
        elif decision != CREATE and hand:
            notes.append(f"{len(hand)} hand-written test(s) also make this call, e.g. "
                         f"{hand[0]['java_class_fqn']}#{hand[0]['java_method']}")
        if alike and not hand:
            notes.append(f"{len(alike)} hand-written test(s) build a path in code that STARTS "
                         f"like this one, e.g. {alike[0]['java_class_fqn']}"
                         f"#{alike[0]['java_method']} ({alike[0]['file']}:{alike[0]['line']}). "
                         f"Whether it is this call cannot be read: look before writing")
        if steps and not hand:
            notes.append("no hand-written test makes this call, but the committed code "
                         "already has a step for it: "
                         + ", ".join(f"{c['java_class_fqn']}#{c['java_method']}()"
                                     for c in steps[:3])
                         + " -- write the test with it rather than a second copy of the call")

        # The data: what each matched test's CSV says about this request.
        # Every DISTINCT data file, not the first few tests: the row may be
        # in the last one, and one test can be matched more than once.
        data, files, not_examined = [], set(), 0
        for e in entries:
            name = e.get("csv_path") or ""
            if name in files:
                continue
            files.add(name)
            if len(data) >= coverage.MAX_DATA_FILES:
                not_examined += 1
                continue
            found = coverage.examine(here, name, req, t.get("examples"))
            found["test"] = f"{e.get('java_class_fqn') or e.get('suite') or '?'}" \
                            f"#{e.get('java_method') or ''}"
            data.append(found)
        verdicts = [f["verdict"] for f in data]
        if decision == UPDATE and "same-inputs" in verdicts and "row-exists" not in verdicts:
            decision = CONFIRM
            why = ("the call is automated AND a row with this request's values is already "
                   "in its data file, expecting ANOTHER status. One set of inputs with two "
                   "expectations: a person says which the story means")
        elif decision == UPDATE and "row-exists" in verdicts:
            decision = CONFIRM
            why = ("the call is automated AND a row with this request's values is already "
                   "in its data file. Adding it again would be a duplicate: a person says "
                   "whether the story means a different case")
        elif decision == UPDATE and verdicts and all(v == "add-row" for v in verdicts):
            why += ". A new row is enough: every field of the request has a column"
        elif decision == UPDATE and "needs-more" in verdicts:
            why += (". A new row alone is NOT enough: the request carries a field the data "
                    "file has no column for, so the test (its template or its assertions) "
                    "changes too")
        decisions.append({
            "n": i,
            "verb": req.get("verb"),
            "path": t["path"],
            "pasted_path": req.get("raw_path"),
            "templated": t["templated"],
            "path_alternatives": t["alternatives"][:5],
            "provenance": req.get("provenance", ""),
            "from_design": bool(req.get("from_design")),
            "decision": decision,
            "why": why,
            "notes": notes,
            "verdict": result.get("verdict"),
            "match_level": result.get("match_level"),
            "matches": [{
                "java_class_fqn": e.get("java_class_fqn"),
                "java_method": e.get("java_method"),
                "csv_path": e.get("csv_path"),
                "suite": e.get("suite"),
                "kind": e.get("kind") or "generated",
                "destination": (f"hand-written: {e.get('file')}:{e.get('line')}"
                                + (f" -- found through {e['via']}" if e.get("via") else "")
                                + (f"; data in {e['csv_path']}" if e.get("csv_path") else "")
                                if e.get("kind") == "handwritten"
                                else shape_match.destination(e)),
            } for e in entries[:5]],
            "data": data,
            "data_not_examined": not_examined,
        })

    plan = {
        "job": job,
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "index": index_note,
        "requests_note": requests_note,
        "handwritten_calls": sum(1 for c in hand_calls if c.get("is_test")),
        # Tests no call could be read from: a "create" below does not
        # rule THEM out.
        "handwritten_blind": seen["blind"],
        "decisions": decisions,
        "stops": [d for d in decisions if d["decision"] in STOPS],
    }
    plan["ok"] = not plan["stops"]
    # Evidence beside the decisions, never an input to them: commits and
    # test files that name the story this job is about. See history.py.
    plan["history"] = story_history(out, root or ROOT)
    diag.step("Writing the plan")
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
    if "STALE" in (plan.get("index") or ""):
        lines += ["**The shape index is stale.** It was built before the "
                  "last convert, so a call that is now covered can read as "
                  "`CREATE`. Re-run with `--rebuild-index`.", ""]
    if plan["stops"]:
        lines += [f"**{len(plan['stops'])} request(s) stop here.** Nothing is "
                  f"written until they are resolved.", ""]
    lines += [f"- job: {plan['job']}", f"- at: {plan['at']}",
              f"- index: {plan['index']}",
              f"- hand-written tests read: {plan.get('handwritten_calls', 0)} call(s) found "
              f"in the committed test code", ""]
    blind = plan.get("handwritten_blind") or {}
    if blind.get("tests"):
        lines += [f"**{len(blind['tests'])} hand-written test(s) could not be seen "
                  f"into** -- {blind['reason']}. A `CREATE` below does not rule them out:",
                  ""] + [f"- `{name}`" for name in blind["tests"][:12]]
        if len(blind["tests"]) > 12:
            lines.append(f"- ...and {len(blind['tests']) - 12} more (all of them are in "
                         f"locate.json)")
        lines.append("")
    if plan.get("requests_note"):
        lines += [f"**{plan['requests_note']}**", ""]

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
        for note in d.get("notes") or []:
            lines.append(f"- note: {note}")
        if d.get("data"):
            lines.append("- the data:")
            for f in d["data"]:
                lines.append(f"    - `{f.get('test', '?')}`: {coverage.describe(f)}")
            if d.get("data_not_examined"):
                lines.append(f"    - {d['data_not_examined']} more data file(s) of matched "
                             f"tests were NOT examined (the first "
                             f"{coverage.MAX_DATA_FILES} are)")
        lines.append("")

    lines += _history_lines(plan)
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
    sys.exit(diag.run("locate", main))
