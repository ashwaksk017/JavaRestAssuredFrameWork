"""Each designed case described step by step in words, for audit: the
Description column of the Automate Test Case tab, and the description of
the case's Test issue in Xray.

Two descriptions, one after the other:

* PLANNED -- from the design, before any code: each step's action, data
  and check, where it came from (the API specification the job was given --
  a YAML or JSON file -- or the story), and which flow specification and
  golden test the test will be written from.
* AS AUTOMATED -- once Cursor has written the test: the Audit comment it
  puts above the test method (the methods called, in order, with their
  class; the payloads; what is captured; the golden test, flow and API
  operation it followed). Read back from the code of the change, never
  taken on trust: a method it names that is defined nowhere in the
  project is marked so.

    /**
     * Audit: TC-001
     * Step 1: serviceToken() (StoryTestBase) -- the service token
     * Step 2: enrollActor() (StoryTestBase) -- enrolls the owner; payload templates/enroll.json
     * Reference: golden test StoryTestExample; B2B_FLOWS.md "Enroll an owner"; spec POST /enroll
     */
"""
from __future__ import annotations

import io
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))

MAX_LINE = 600
MAX_LINES = 40
# `Audit: TC-001` -- or `Audit: TC-001, TC-002` for a data-driven method
# that tests several cases -- first line of a /** ... */ block; after `--`,
# words about it
_AUDIT_RX = re.compile(r"^Audit:\s*(.*)$")
_ID_RX = re.compile(r"\b[A-Za-z][A-Za-z0-9_]*-[A-Za-z0-9_.]*[0-9][A-Za-z0-9_.]*\b")
_BLOCK_RX = re.compile(r"/\*\*(.*?)\*/", re.S)
# a method named with its class, as the Audit lines are asked to: `name() (Class)`.
# Only a method of a PROJECT class is checked: `statusCode() (RestAssured)` is
# a library's, and is not this project's to define.
_NAMED_RX = re.compile(r"(?<![\w.])([a-z_]\w*)\([^()]*\)\s*\((?:in\s+)?([A-Z]\w*)\)")
# a definition: a type before the name, a parameter list, then its body
_DEF_RX = re.compile(r"[\w>\]]\s+([a-z_]\w*)\s*\([^()]*\)\s*(?:throws\s+[\w.,\s]+)?\{")
_NOT_METHODS = {"if", "for", "while", "switch", "catch", "return", "new", "synchronized"}
SOURCE_WORDS = {"spec": "the API specification", "story": "the story", "notes": "the tester's notes"}


def _one(v, cap: int = MAX_LINE) -> str:
    return " ".join(str(v if v is not None else "").split())[:cap]


def _json(path: str) -> dict:
    try:
        with io.open(path, encoding="utf-8-sig") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


# ---- the job's context: its project's flows and golden test, its spec --------------

def context(root: str, job_dir: str) -> dict:
    """What the planned description names: the project's flow specification
    and golden test (policy.json), and the API specification of the job."""
    project = str(_json(os.path.join(job_dir, "project.json")).get("project") or "b2b").lower()
    proj = (_json(os.path.join(HERE, "policy.json")).get("projects") or {}).get(project) or {}
    golden = [os.path.basename(p)[:-len(".java")] if str(p).endswith(".java") else os.path.basename(p)
              for p in proj.get("pattern_example") or []]
    spec = ""
    try:
        import importlib.util
        spec_ = importlib.util.spec_from_file_location("casedescribe_specsource",
                                                        os.path.join(HERE, "specsource.py"))
        mod = importlib.util.module_from_spec(spec_)
        spec_.loader.exec_module(mod)
        if os.path.isdir(os.path.join(job_dir, "spec")):
            spec = _one((mod.current(job_dir, root) or {}).get("name"), 200)
    except Exception:                                    # noqa: BLE001 -- the name only
        spec = ""
    return {"project": project, "label": str(proj.get("label") or project.upper()),
            "flows": os.path.basename(str(proj.get("flows") or "")),
            "base_class": str(proj.get("base_class") or ""), "golden": golden, "spec": spec}


# ---- planned: from the design --------------------------------------------------------

def planned(case: dict, ctx: dict) -> list:
    """The case as designed, in words, step by step, with where each part
    comes from. `case`: a design case (steps with action/data/expected and,
    when the design said, source)."""
    lines = []
    endpoint = _one(case.get("endpoint"), 300)
    spec = ctx.get("spec") or ""
    if endpoint:
        if case.get("endpoint_in_spec") is True:
            lines.append(f"Endpoint {endpoint} -- from the API specification {spec or '(the job’s)'}.")
        elif case.get("endpoint_in_spec") is False:
            lines.append(f"Endpoint {endpoint} -- NOT in the API specification{' ' + spec if spec else ''}: "
                         f"from the story.")
        else:
            lines.append(f"Endpoint {endpoint} -- from the story (no API specification was given).")
    if _one(case.get("preconditions")):
        lines.append(f"Before step 1: {_one(case.get('preconditions'))}")
    for n, s in enumerate(case.get("steps") or [], 1):
        if not isinstance(s, dict):
            continue
        text = f"Step {n}: {_one(s.get('action'))}"
        if _one(s.get("data")):
            text += f", with {_one(s.get('data'))}"
        if _one(s.get("expected")):
            text += f". Checks: {_one(s.get('expected'))}"
        src = _one(s.get("source"), 200)
        if src:
            word = src.split(":", 1)[0].strip().lower()
            text += f" (from {SOURCE_WORDS.get(word, src)}" + (
                f": {src.split(':', 1)[1].strip()}" if ":" in src and word in SOURCE_WORDS else "") + ")"
        lines.append(text)
    refs = [_one(r, 200) for r in case.get("requirement_refs") or case.get("refs") or [] if _one(r)]
    if refs:
        lines.append("Covers: " + "; ".join(refs[:6]))
    how = f"When automated: Cursor follows the {ctx.get('label', '')} flow specification"
    how += f" ({ctx['flows']})" if ctx.get("flows") else ""
    if ctx.get("golden"):
        how += f" and the golden test {', '.join(ctx['golden'])}"
    if ctx.get("base_class"):
        how += f"; the test extends {ctx['base_class']}"
    lines.append(how + ".")
    return lines[:MAX_LINES]


# ---- as automated: the Audit comment of the test ----------------------------------

def audits(java: str) -> dict:
    """{case id: [lines]} of the Audit comments in a Java source."""
    out = {}
    for m in _BLOCK_RX.finditer(java or ""):
        body = [re.sub(r"^\s*\*\s?", "", ln).rstrip() for ln in m.group(1).splitlines()]
        body = [ln.strip() for ln in body if ln.strip()]
        if not body:
            continue
        head = _AUDIT_RX.match(body[0])
        if not head:
            continue
        ids_part, _sep, words = head.group(1).partition("--")
        ids = list(dict.fromkeys(_ID_RX.findall(ids_part)))
        if not ids:
            continue
        lines = ([words.strip()] if words.strip() else []) + body[1:]
        lines = [_one(ln) for ln in lines if not ln.startswith("@")][:MAX_LINES]
        for case_id in ids:
            out.setdefault(case_id, []).extend(lines)
    return out


_INDEX: dict = {}                                       # root -> (signature, index)


def _java_files(root: str) -> list:
    out = []
    for top in ("src/main/java", "src/test/java"):
        for here, _dirs, files in os.walk(os.path.join(root, *top.split("/"))):
            out.extend(os.path.join(here, f) for f in files if f.endswith(".java"))
    return out


def code_index(root: str) -> dict:
    """{"methods": every method name defined in the project's Java,
    "classes": every class (file) name} -- read again only when a Java file
    was added, removed or changed (the table asks on every redraw)."""
    files = _java_files(root)
    sig = (len(files), max((os.path.getmtime(f) for f in files if os.path.exists(f)), default=0))
    hit = _INDEX.get(root)
    if hit and hit[0] == sig:
        return hit[1]
    names = set()
    for p in files:
        try:
            with io.open(p, encoding="utf-8", errors="replace") as fh:
                names.update(n for n in _DEF_RX.findall(fh.read()) if n not in _NOT_METHODS)
        except OSError:
            pass
    index = {"methods": names, "classes": {os.path.basename(p)[:-len(".java")] for p in files}}
    _INDEX[root] = (sig, index)
    return index


def defined_methods(root: str) -> set:
    """Every method name defined in the project's Java (src/main, src/test)."""
    return code_index(root)["methods"]


def checked(lines: list, index: dict) -> list:
    """The lines, each method of a project class that is defined nowhere in
    the project marked."""
    out = []
    for ln in lines:
        unknown = sorted({m for m, cls in _NAMED_RX.findall(ln)
                          if cls in index["classes"] and m not in index["methods"]})
        out.append(ln + (f"  [not found in the code: {', '.join(n + '()' for n in unknown)}]"
                         if unknown else ""))
    return out


def as_automated(root: str, java_files: list, index: dict = None) -> dict:
    """{case id: {"lines": [...], "file": rel}} from the Audit comments of
    the given Java files (paths relative to `root`)."""
    out = {}
    names = None
    for rel in java_files or []:
        if not str(rel).endswith(".java"):
            continue
        try:
            with io.open(os.path.join(root, *str(rel).split("/")), encoding="utf-8", errors="replace") as fh:
                found = audits(fh.read())
        except OSError:
            continue
        if found and names is None:
            names = index if index is not None else code_index(root)
        for case_id, lines in found.items():
            if case_id in out:                           # two tests of one case: both said
                out[case_id]["lines"] += ["", f"Also in {rel}:"] + checked(lines, names)
                continue
            out[case_id] = {"lines": checked(lines, names), "file": str(rel)}
    return out


# ---- the job's cases ----------------------------------------------------------------

_LIVE = ("pending-review", "verify-failed", "pushed", "approved-local", "push-failed")


def change_files(job_dir: str) -> list:
    """The Java files of the job's change, while there is one."""
    review = _json(os.path.join(job_dir, "review.json"))
    if review.get("state") not in _LIVE:
        return []
    files = review.get("files") or {}
    return [f for f in (files.get("created") or []) + (files.get("modified") or []) if str(f).endswith(".java")]


def describe(root: str, job_dir: str, cases: list = None) -> dict:
    """{case id: {"planned": [...], "automated": [...], "file": rel}} for the
    job's designed cases (`cases`: the design's, reviewed; read from
    design.json when not given)."""
    root = root or ROOT
    if cases is None:
        cases = _json(os.path.join(job_dir, "design.json")).get("test_cases") or []
    # A person's corrected steps (the review) are the plan, not the design's.
    review = _json(os.path.join(job_dir, "case-review.json")).get("design") or {}
    ctx = context(root, job_dir)
    done = as_automated(root, change_files(job_dir))
    out = {}
    for c in cases:
        if not isinstance(c, dict) or not c.get("id"):
            continue
        cid = str(c["id"])
        fixed = (review.get(cid) or {}).get("steps") if isinstance(review.get(cid), dict) else None
        if isinstance(fixed, list) and fixed:
            c = dict(c, steps=fixed)
        got = done.get(cid) or {}
        out[cid] = {"planned": planned(c, ctx), "automated": got.get("lines") or [],
                    "file": got.get("file") or ""}
    return out


def digest(lines: list) -> str:
    """A description's fingerprint: Update in Xray sends it again only when
    it changed since it was last sent."""
    import hashlib
    return hashlib.sha1("\n".join(lines or []).encode("utf-8")).hexdigest()[:16]


def lines_for(entry: dict) -> list:
    """What goes into Xray: as automated when it is, with the plan after."""
    if entry.get("automated"):
        return (["As automated (" + entry.get("file", "") + "):"] + entry["automated"]
                + ["", "As planned:"] + entry.get("planned", []))
    return ["As planned:"] + entry.get("planned", [])
