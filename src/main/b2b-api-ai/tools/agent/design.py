"""Design the API test cases for a job, before any code is written.

    python tools/agent/design.py --job J [--service NAME]
        [--swagger-file F] [--requirements-file F] [--notes-file F]
        [--speed fast|balanced|thorough]

Tab 1 reads a story and decides create / update / upstream for the
requests in it. That answers "is it automated already?". It does not
answer "what should be tested?". This step does: it gives Cursor the API
specification, the requirements and the story, and gets back a list of
test cases -- each with an endpoint, steps and a checkable expected
result -- for a person to read before tab 3 turns any of it into Java.

It writes into target/agent/<job>/ and nowhere else:

    design.json      everything, for the next stage and for the page
    design.md        the same, to read
    test-cases.csv   one row per step, `;` separated
    xray.csv         one row per step, grouped by test, for Xray's importer

WHAT IS CHECKED HERE RATHER THAN TRUSTED
* The endpoint list is read out of the specification by this program,
  not by the agent, and every designed case is compared with it. A case
  on an endpoint the specification does not have is kept and MARKED.
* A case with no step that has an expected result is dropped, and said
  so: it cannot be automated and reads as coverage.
* The reply is parsed by jsonfix.py, which says how it had to be read.
  A reply cut off half way gives the complete cases and the word
  "partial", not an error and not a guess.
* Each input has a size limit. What was left out is reported; nothing is
  shortened silently.
* Every endpoint of the specification is accounted for: the ones no
  designed case touches are listed. "40 cases" says nothing about which
  third of the API they are all on.

A SPECIFICATION TOO LARGE FOR ONE PROMPT is split before it is cut. Its
paths go into parts, each sent with only the schemas it refers to, one
call after another, and the cases are put together. Each part may
propose its share of the case limit and no more. A part that fails is
named, the design is labelled PARTIAL, and the other parts are kept; a
failure that would repeat (no key, no connection, a call that timed
out) stops the remaining parts instead of paying for each to fail.

There is a ceiling. Past MAX_PARTS calls the parts are made larger, and
a part that still does not fit loses its examples, then its long
descriptions, and then is cut at the size limit. Each of those is
reported for the part it happened to. A specification of several
megabytes is designed from what fits, and says so.

Each call has a time limit (policy.json, `cursor_deadline_seconds`). A
call that does not come back is reported as stuck instead of leaving the
job "running" for ever.

THE SAME QUESTION IS NOT PAID FOR TWICE. A reply that was read whole
and gave at least one usable case is kept in the job directory under a
hash of exactly what was sent -- the prompt after everything was removed
from it, the mode, the model -- and of CACHE_VERSION, which changes when
the way a reply is read does. Run the step again and, if the prompt
comes out the same, that reply is used; the log and design.md say so
with its date. Anything that changes what is sent asks again: the
material, the notes, the speed, the prompt template. (What is NOT sent
changes nothing: text past a size limit, the layout of a specification's
JSON, the date the story was read.) Each part of a large specification
is kept as soon as it is read, so a run that stops half way does not pay
for its finished parts again. A reply that was cut off, had to be asked
for twice, or gave no case that can be used -- none at all, or none on
an endpoint the specification has -- is never kept; a kept reply that
can no longer be read is asked for again. `--fresh` forgets every reply
kept for the job and asks. A run answered entirely from kept replies
needs neither the Cursor SDK nor a key.

Cursor is asked in PLAN mode (it proposes, it does not edit or run) and
is started in an empty temporary directory. That is a starting point,
not a sandbox: it is still a program on this machine. So the reply is
not trusted either -- host and credential values of the private
configuration are removed from it before anything is written, and the
working tree is compared before and after; a difference is reported.

Removed from the prompt, because test design needs none of it: the
specification's server, host, contact and external-documentation
entries at every level, the host of every URL anywhere in the material,
and every host or credential value found in the private configuration.

`design.json` records which brief it was designed from. Tab 3 uses the
cases only while the job's brief is still that one.

Came from the sibling test-case generator (aimanualtc): the idea, the
size limits, the speed presets and the CSV layout. Its prompts are not
copied -- they name systems this public repository must not.

PyYAML is used to read a YAML specification when it is installed. It is
not required: without it a YAML specification is sent as text and the
endpoint check is skipped, and the log says so.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import io
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from urllib.parse import unquote, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return mod


jsonfix = _load("design_jsonfix", os.path.join(HERE, "jsonfix.py"))
loop = _load("design_loop", os.path.join(HERE, "loop.py"))

ROOT = loop.ROOT
TEMPLATE = os.path.join(HERE, "prompts", "api_test_design.md")

# Characters, not tokens: a limit a person can check with a text editor.
SECTION_LIMITS = {"swagger": 80_000, "requirements": 100_000,
                  "story": 40_000, "notes": 20_000, "endpoints": 30_000}
TOTAL_LIMIT = 200_000
SPEEDS = {"fast": 25, "balanced": 40, "thorough": 80}
VERBS = ("get", "post", "put", "patch", "delete", "head", "options")
ARTIFACTS = ("design.json", "design.md", "test-cases.csv", "xray.csv")
MODES = ("plan", "agent")
MAX_PARTS = 6                 # calls to Cursor for one design, at most
CACHE_DIR = "design-cache"
CACHE_KEEP = 24               # replies kept per job
CACHE_VERSION = 1             # bump when a reply would be READ differently
DEFAULT_DEADLINE = 900        # seconds for one call, when the policy has none
# What the page pasted, written by the server next to the job's other files.
PASTED = {"swagger": "design-swagger.txt", "requirements": "design-requirements.txt",
          "notes": "design-notes.txt"}
CASE_KEYS = ("test_cases", "testCases", "testcases", "cases", "tests")

JSON_ONLY = (
    "Your reply could not be read as JSON. Reply again with ONLY the JSON "
    "object described in the instructions: the same content, no sentence "
    "before or after it, no code fence. If it was too long to finish, keep "
    "the highest-priority test cases and leave the rest out.")


def say(msg: str) -> None:
    loop.say(msg)


def _read(path: str) -> str:
    if not path or not os.path.isfile(path):
        return ""
    with io.open(path, encoding="utf-8-sig", errors="replace") as fh:
        return fh.read()


# ---- the specification ------------------------------------------------

def read_spec(text: str) -> tuple:
    """(spec dict, how) or (None, why not). JSON first, YAML when PyYAML
    is installed. Only a mapping with a `paths` mapping counts."""
    text = (text or "").strip()
    if not text:
        return None, "empty"
    data, how = None, ""
    try:
        data, how = json.loads(text), "JSON"
    except ValueError:
        try:
            import yaml
        except ImportError:
            return None, ("not JSON, and PyYAML is not installed so YAML "
                          "cannot be read (pip install pyyaml)")
        try:
            data, how = yaml.safe_load(text), "YAML"
        except Exception as e:                           # noqa: BLE001
            return None, f"neither JSON nor YAML ({type(e).__name__})"
    if not isinstance(data, dict) or not isinstance(data.get("paths"), dict):
        return None, f"{how or 'text'} without a `paths` section"
    return data, how


def _path_item(spec: dict, item) -> dict:
    """A path item, following a local `$ref` (a document split into
    `components.pathItems` writes every path that way)."""
    for _ in range(4):
        if not isinstance(item, dict) or not isinstance(item.get("$ref"), str):
            break
        ref = item["$ref"]
        if not ref.startswith("#/"):
            return {}
        node = spec
        for part in ref[2:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            node = node.get(part) if isinstance(node, dict) else None
            if node is None:
                return {}
        item = node
    return item if isinstance(item, dict) else {}


def endpoints_of(spec: dict) -> list:
    """["VERB /path -- summary", ...] in the order the specification has them."""
    out = []
    for path, item in (spec.get("paths") or {}).items():
        for verb, op in _path_item(spec, item).items():
            if str(verb).lower() not in VERBS:
                continue
            summary = ""
            if isinstance(op, dict):
                summary = str(op.get("summary") or op.get("operationId") or "").strip()
            line = f"{str(verb).upper()} {path}"
            out.append(line + (f" -- {summary[:100]}" if summary else ""))
    return out


def base_paths(spec: dict) -> list:
    """Path prefixes the specification puts in front of every path:
    Swagger 2 `basePath`, and the path part of an OpenAPI 3 server URL.
    A case written as `/v1/groups` is the endpoint `/groups`."""
    found = []
    if isinstance(spec.get("basePath"), str):
        found.append(spec["basePath"])
    for server in spec.get("servers") or []:
        url = server.get("url") if isinstance(server, dict) else None
        if isinstance(url, str):
            found.append(urlparse(url).path if "://" in url else url)
    out = []
    for p in found:
        p = "/" + p.strip("/")
        if p != "/" and p not in out:
            out.append(p)
    return out


def match_lines(endpoints: list, prefixes: list) -> list:
    """Every way an endpoint of the specification may be written."""
    out = list(endpoints)
    for line in endpoints:
        head = line.split(" -- ")[0]
        verb, _, path = head.partition(" ")
        out += [f"{verb} {p}{path}" for p in prefixes]
    return out


_PARAM_RX = re.compile(r"\{[^/}]*\}|:[A-Za-z_][A-Za-z0-9_]*")


def endpoint_key(line: str) -> str:
    """`post /a/{id}/ -- text`, `` `POST /a/{propId}` `` and
    `POST https://host/a/{x}` are the same endpoint. "" when the text is
    not a verb and a path."""
    head = (line or "").split(" -- ")[0].strip().strip("`'\"").strip()
    parts = head.split(None, 1)
    if len(parts) != 2 or parts[0].lower() not in VERBS:
        return ""
    path = parts[1].strip().strip("`'\"")
    if "://" in path:
        path = urlparse(path.split("?")[0]).path or "/"
    if not path.startswith("/"):
        return ""
    path = _PARAM_RX.sub("{}", path.split("?")[0].strip()).rstrip("/") or "/"
    return f"{parts[0].upper()} {path}"


# Keys that hold a host, a person or a link, wherever they appear.
_HOST_KEYS = ("servers", "host", "externalDocs", "contact", "termsOfService")
# Maps whose KEYS are names the author chose. A schema property called
# `host` or `example` is part of the contract, not one of the above.
_NAME_MAPS = ("properties", "patternProperties", "schemas", "definitions",
              "parameters", "responses", "headers", "requestBodies",
              "securitySchemes", "securityDefinitions", "callbacks", "links",
              "paths", "pathItems")


def _strip(node, drop_examples: bool, shorten: int, dropped: list, names: bool = False):
    """`names`: the keys of THIS mapping are author-chosen names."""
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            key = str(k)
            if not names:
                if key in _HOST_KEYS:
                    dropped.append(key)
                    continue
                if drop_examples and (key in ("example", "examples") or key.startswith("x-")):
                    continue
                if shorten and key == "description" and isinstance(v, str) and len(v) > shorten:
                    v = v[:shorten].rstrip() + " ..."
            out[k] = _strip(v, drop_examples, shorten, dropped,
                            names=(not names and key in _NAME_MAPS and isinstance(v, dict)))
        return out
    if isinstance(node, list):
        return [_strip(v, drop_examples, shorten, dropped) for v in node]
    return node


def spec_text(spec: dict, limit: int) -> tuple:
    """The specification as it is sent, and what was done to make it fit.

    Server, host, contact and external-documentation entries are dropped
    always and at every level: they are where the host names are, and a
    test design needs none. Then, only as far as needed: indentation,
    examples and vendor extensions, long descriptions.
    """
    notes, dropped = [], []
    body = _strip(spec, False, 0, dropped)
    if dropped:
        notes.append("the specification's " + ", ".join(sorted(set(dropped)))
                     + " entries were not sent")
    dump = lambda node, pretty: json.dumps(
        node, ensure_ascii=False, default=str,
        indent=1 if pretty else None,
        separators=None if pretty else (",", ":"))
    text = dump(body, True)
    if len(text) <= limit:
        return text, notes
    text = dump(body, False)
    if len(text) <= limit:
        return text, notes
    body = _strip(body, True, 0, [])
    text = dump(body, False)
    notes.append("examples and x- extensions were left out of the "
                 "specification to fit the prompt")
    if len(text) <= limit:
        return text, notes
    body = _strip(body, True, 160, [])
    text = dump(body, False)
    notes.append("descriptions in the specification were shortened to 160 characters")
    return text, notes


def _refs(node, out: list) -> list:
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "$ref" and isinstance(v, str) and v.startswith("#/"):
                out.append(v)
            else:
                _refs(v, out)
    elif isinstance(node, list):
        for v in node:
            _refs(v, out)
    return out


def _pointer(ref: str) -> list:
    return [unquote(p).replace("~1", "/").replace("~0", "~") for p in ref[2:].split("/")]


def _child(node, part: str):
    """One step of a pointer. YAML gives `404:` as the number 404 and a
    pointer writes it as text; a list is indexed by position."""
    if isinstance(node, dict):
        if part in node:
            return part, node[part]
        for k, v in node.items():
            if str(k) == part:
                return k, v
    return None, None


def sub_spec(spec: dict, paths: list, missing: set = None) -> dict:
    """The specification with only `paths`, and only the schemas,
    parameters and responses those paths refer to -- directly or through
    one another. What a part of the API needs to be understood alone."""
    shared = ("paths", "components", "definitions", "parameters", "responses")
    out = {k: v for k, v in spec.items() if k not in shared}
    out["paths"] = {p: spec["paths"][p] for p in paths if p in spec["paths"]}
    # Named, not referred to: an operation says `security: [{oauth: []}]`.
    schemes = (spec.get("components") or {}).get("securitySchemes") \
        if isinstance(spec.get("components"), dict) else None
    if schemes:
        out["components"] = {"securitySchemes": schemes}
    todo, seen = _refs(out["paths"], []), set()
    unresolved = set()
    while todo:
        ref = todo.pop()
        if ref in seen:
            continue
        seen.add(ref)
        node, keys = spec, []
        for part in _pointer(ref):
            key, node = _child(node, part)
            if key is None:
                break
            keys.append(key)
        else:
            if keys and keys[0] != "paths":
                dest = out
                for key in keys[:-1]:
                    dest = dest.setdefault(key, {})
                dest[keys[-1]] = node
            _refs(node, todo)
            continue
        unresolved.add(ref)
    if unresolved and missing is not None:
        missing.update(unresolved)
    return out


def _size(spec: dict) -> int:
    return len(json.dumps(_strip(spec, False, 0, []), ensure_ascii=False,
                          default=str, separators=(",", ":")))


def plan_parts(spec: dict, limit: int, max_parts: int = MAX_PARTS) -> list:
    """[[path, ...], ...]: the paths of the specification, in order, in as
    few parts as fit `limit` characters each at full detail. One part
    when the whole specification fits."""
    paths = [p for p in (spec.get("paths") or {}) if _path_item(spec, spec["paths"][p])]
    if not paths or _size(spec) <= limit:
        return [paths]
    parts, current = [], []
    for p in paths:
        if current and _size(sub_spec(spec, current + [p])) > limit:
            parts.append(current)
            current = []
        current.append(p)
    parts.append(current)
    if len(parts) > max_parts:
        # Too many calls. Fewer, larger parts; spec_text() then drops
        # examples and long descriptions from each, and says so.
        per = -(-len(paths) // max_parts)
        parts = [paths[i:i + per] for i in range(0, len(paths), per)]
    return parts


def uncovered(endpoints: list, prefixes: list, cases: list) -> list:
    """The endpoints of the specification that no designed case is on."""
    touched = {endpoint_key(c.get("endpoint", "")) for c in cases}
    out = []
    for line in endpoints:
        if not ({endpoint_key(l) for l in match_lines([line], prefixes)} & touched):
            out.append(line.split(" -- ")[0])
    return out


_FENCE_RX = re.compile(r"```[A-Za-z0-9_-]*[ \t]*\r?\n(.*?)```", re.S)


def find_spec(job_dir: str) -> tuple:
    """(text, where) for a specification already in the job directory:
    a code block on a Confluence page the LAST intake fetched, or the
    pasted text itself. ("", "") when there is none.

    Only the directories intake.json lists are read. `sources/` keeps
    the pages of earlier runs of the same job, and a specification from
    a previous story is worse than none.
    """
    best = ("", "")
    try:
        links = json.loads(_read(os.path.join(job_dir, "intake.json")) or "{}").get("links") or []
    except ValueError:
        links = []
    for link in links:
        if not (isinstance(link, dict) and link.get("ok") and link.get("dir")):
            continue
        base = os.path.join(job_dir, "sources", os.path.basename(str(link["dir"])))
        for name in sorted(link.get("files") or []):
            path = os.path.join(base, os.path.basename(str(name)))
            page = _read(path)
            for block in [m.group(1) for m in _FENCE_RX.finditer(page)] + [page]:
                if len(block) > len(best[0]) and read_spec(block)[0] is not None:
                    rel = os.path.relpath(path, job_dir)
                    best = (block, f"a code block in {rel.replace(os.sep, '/')}")
    pasted = _read(os.path.join(job_dir, "pasted.txt"))
    if len(pasted) > len(best[0]) and read_spec(pasted)[0] is not None:
        best = (pasted, "the text pasted on the New test case tab")
    return best


# ---- the prompt -------------------------------------------------------

def _cut(text: str, limit: int) -> str:
    # The marker does not say HOW MUCH was left out: that number would make
    # text the agent never sees part of the prompt, and so of the cache
    # key. The amount is in the warnings.
    return text[:limit].rstrip() + "\n[... the rest was left out to fit the prompt ...]"


def fit(sections: dict, limits: dict = None, total: int = TOTAL_LIMIT) -> tuple:
    """Each section within its own limit, then all of them within the
    total. Returns (sections, [what was left out])."""
    limits = limits or SECTION_LIMITS
    out, notes = {}, []
    for name, text in sections.items():
        text = text or ""
        cap = limits.get(name)
        if cap and len(text) > cap:
            notes.append(f"{name}: {len(text)} characters, the first {cap} were sent")
            text = _cut(text, cap)
        out[name] = text
    for _ in range(len(out)):
        over = sum(len(t) for t in out.values()) - total
        if over <= 0:
            break
        name = max(out, key=lambda k: len(out[k]))
        keep = max(2000, len(out[name]) - over - 200)
        if keep >= len(out[name]):
            break
        notes.append(f"{name}: cut to {keep} characters so the whole prompt "
                     f"stays under {total}")
        out[name] = _cut(out[name], keep)
    return out, notes


_SLOT_RX = re.compile(r"\{\{([a-z_]+)\}\}")


def render(template: str, values: dict) -> str:
    """One pass: a `{{notes}}` inside the pasted material stays text."""
    return _SLOT_RX.sub(lambda m: str(values.get(m.group(1), m.group(0))), template)


_URL_HOST_RX = re.compile(r"""(https?://)([^/\s"'<>`)\]]+)""", re.I)


def hide_hosts(text: str) -> tuple:
    """`https://anything/path` -> `https://host.invalid/path`."""
    count = 0

    def swap(m):
        nonlocal count
        if m.group(2).lower() == "host.invalid":
            return m.group(0)
        count += 1
        return m.group(1) + "host.invalid"

    return _URL_HOST_RX.sub(swap, text), count


def redact(text: str, private: dict) -> tuple:
    """Remove the private configuration's hosts and credential values."""
    hits = 0
    for value in sorted(private, key=len, reverse=True):
        if len(value) < 6:
            continue
        text, n = re.subn(re.escape(value), "<redacted>", text, flags=re.I)
        hits += n
    return text, hits


# ---- what came back ---------------------------------------------------

def _norm_key(k) -> str:
    return re.sub(r"[\s_\-]", "", str(k)).lower()


def _first(d: dict, *names):
    """`expected_result`, `Expected Result` and `expectedResult` are one key."""
    have = {_norm_key(k): v for k, v in d.items()}
    for n in names:
        v = have.get(_norm_key(n))
        if v not in (None, "", [], {}):
            return v
    return ""


def cases_in(data):
    """The list of cases in a parsed reply, under any name it is known by."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        got = _first(data, *CASE_KEYS)
        if isinstance(got, list):
            return got
    return None


def read_reply(text: str, partial: bool = False) -> tuple:
    """(data, how) when the reply holds at least one case object; else
    ValueError. An object that parses but carries no cases is NOT a
    usable reply: asked for again, never accepted."""
    last = ValueError("no test cases in the reply")
    for key in (CASE_KEYS if partial else ("",)):
        try:
            data, how = jsonfix.loads(text, partial_key=key, list_key="test_cases")
        except ValueError as e:
            last = e
            continue
        cases = cases_in(data)
        if cases and any(isinstance(c, dict) for c in cases):
            return data, how
    raise last


def _text(v) -> str:
    if isinstance(v, (list, tuple)):
        return "; ".join(_text(x) for x in v if _text(x))
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False)
    return str(v if v is not None else "").strip()


def _steps(case: dict) -> list:
    raw = _first(case, "steps", "test_steps")
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        raw = [case] if _first(case, "step", "action") else []
    out = []
    for s in raw:
        if isinstance(s, str):
            s = {"action": s}
        if not isinstance(s, dict):
            continue
        step = {"action": _text(_first(s, "action", "step")),
                "data": _text(_first(s, "data", "test_data")),
                "expected": _text(_first(s, "expected", "expected_result", "result"))}
        if step["action"] or step["expected"]:
            out.append(step)
    # Steps given as plain sentences with ONE expected result for the case.
    whole = _text(_first(case, "expected", "expected_result"))
    if out and whole and not any(s["expected"] for s in out):
        out[-1]["expected"] = whole
    return out


def normalise(raw_cases, known_endpoints=(), max_cases: int = 0) -> tuple:
    """(cases, warnings). Accepts the shape the prompt asks for and the
    column-named shape of the tool this came from."""
    known = {endpoint_key(e) for e in known_endpoints} - {""}
    cases, warnings, seen = [], [], set()
    for n, raw in enumerate(raw_cases if isinstance(raw_cases, list) else [], 1):
        if not isinstance(raw, dict):
            warnings.append(f"item {n} is not an object and was dropped")
            continue
        title = _text(_first(raw, "title", "summary", "name"))
        steps = _steps(raw)
        label = _text(_first(raw, "id", "testid", "test_id")) or f"item {n}"
        if not title:
            warnings.append(f"{label} has no title and was dropped")
            continue
        if not any(s["expected"] for s in steps):
            warnings.append(f"{label} ({title[:60]}) has no step with an "
                            f"expected result and was dropped")
            continue
        refs = _first(raw, "requirement_refs", "br_refs", "requirement", "requirements")
        if not isinstance(refs, (list, tuple)):
            refs = [refs] if refs not in (None, "") else []
        endpoint = _text(_first(raw, "endpoint", "api", "operation"))
        case = {
            "id": _text(_first(raw, "id", "testid", "test_id")),
            "title": title,
            "endpoint": endpoint,
            "type": (_text(_first(raw, "type", "test_type")) or "positive").lower(),
            "priority": (_text(_first(raw, "priority")) or "medium").lower(),
            "preconditions": _text(_first(raw, "preconditions", "precondition", "description")),
            "steps": steps,
            "requirement_refs": [_text(r) for r in (refs or []) if _text(r)],
        }
        if known:
            case["endpoint_in_spec"] = endpoint_key(endpoint) in known
        cases.append(case)
    if max_cases and len(cases) > max_cases:
        warnings.append(f"{len(cases)} cases came back; the first {max_cases} "
                        f"were kept (the limit for this speed)")
        cases = cases[:max_cases]
    for n, case in enumerate(cases, 1):
        if not case["id"] or case["id"] in seen:
            new = f"TC-{n:03d}"
            while new in seen:
                new += "a"
            if case["id"]:
                warnings.append(f"id {case['id']} was used twice; the second is now {new}")
            case["id"] = new
        seen.add(case["id"])
    off = [c["id"] for c in cases if c.get("endpoint_in_spec") is False]
    if off:
        warnings.append(
            f"{len(off)} case(s) name an endpoint that is not in the "
            f"specification: {', '.join(off[:12])}"
            + (" ..." if len(off) > 12 else ""))
    return cases, warnings


# ---- writing it down --------------------------------------------------

def _cell(value) -> str:
    """A spreadsheet runs a cell that starts with = + - or @ as a formula.
    This text was written by an agent from pasted material."""
    s = _text(value)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t") else s


def write_csv(path: str, header: list, rows: list) -> None:
    with io.open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, delimiter=";", quoting=csv.QUOTE_MINIMAL)
        w.writerow(header)
        for row in rows:
            w.writerow([_cell(c) for c in row])


def write_outputs(out_dir: str, design: dict) -> None:
    cases = design["test_cases"]
    with io.open(os.path.join(out_dir, "design.json"), "w", encoding="utf-8") as fh:
        json.dump(design, fh, indent=2, ensure_ascii=False)

    rows, xray = [], []
    for n, c in enumerate(cases, 1):
        for i, s in enumerate(c["steps"]):
            rows.append([c["id"], c["title"], c["preconditions"], c["type"],
                         c["priority"], c["endpoint"], s["action"], s["data"],
                         s["expected"], "; ".join(c["requirement_refs"])])
            first = i == 0
            xray.append([n, c["title"] if first else "",
                         c["preconditions"] if first else "",
                         c["priority"] if first else "",
                         c["id"] if first else "",
                         s["action"], s["data"], s["expected"]])
    write_csv(os.path.join(out_dir, "test-cases.csv"),
              ["TestID", "Summary", "Description", "Test Type", "Priority",
               "Endpoint", "Step", "Data", "Expected Result", "Requirement"], rows)
    write_csv(os.path.join(out_dir, "xray.csv"),
              ["TCID", "Summary", "Description", "Priority", "Labels",
               "Action", "Data", "Expected Result"], xray)

    md = [f"# API test design: {design['service'] or design['job']}", ""]
    if design.get("mock"):
        md += ["**MOCK. This is not a design.** It was produced by the workbench's "
               "stand-in for Cursor (tools/agent/mock.json), two cases per endpoint, "
               "so the flow can be tried.", ""]
    md += [
          f"- job: {design['job']}", f"- at: {design['at']}",
          f"- test cases: {len(cases)}"
          + (" (PARTIAL: a reply was cut off, or a part of the API "
             "produced nothing)" if design.get("partial") else ""),
          f"- reply read: {design['parsed']}"]
    if design.get("reused_calls"):
        md.append(f"- Cursor was NOT asked again for {design['reused_calls']} of "
                  f"{design.get('calls', 1)} call(s): nothing in the request had "
                  f"changed since {', '.join(design.get('reused_from') or ['an earlier run'])}. "
                  f"Run with `--fresh` (the page: *Ask again*) for a new answer.")
    md.append("")
    if design.get("summary"):
        md += [design["summary"], ""]
    if design["warnings"]:
        md += ["## Read this first", ""] + [f"- {w}" for w in design["warnings"]] + [""]
    if design["open_questions"]:
        md += ["## Open questions", ""] + [f"- {q}" for q in design["open_questions"]] + [""]
    missing = design.get("uncovered_endpoints") or []
    if missing:
        md += [f"## Endpoints with no test case ({len(missing)} of "
               f"{len(design.get('endpoints') or [])})", "",
               "Nothing below is covered by this design. Run again with a "
               "higher case limit, or say in the notes which of these matter.", ""]
        md += [f"- `{e}`" for e in missing[:80]]
        if len(missing) > 80:
            md.append(f"- ... and {len(missing) - 80} more, in design.json")
        md.append("")
    md += ["## Test cases", ""]
    for c in cases:
        flag = "  [NOT IN THE SPECIFICATION]" if c.get("endpoint_in_spec") is False else ""
        md += [f"### {c['id']}  {c['title']}", "",
               f"- endpoint: `{c['endpoint'] or '(none named)'}`{flag}",
               f"- type: {c['type']}, priority: {c['priority']}"]
        if c["preconditions"]:
            md.append(f"- before: {c['preconditions']}")
        if c["requirement_refs"]:
            md.append(f"- covers: {'; '.join(c['requirement_refs'])}")
        md.append("")
        for i, s in enumerate(c["steps"], 1):
            md.append(f"{i}. {s['action'] or '(no action given)'}")
            if s["data"]:
                md.append(f"   - data: {s['data']}")
            md.append(f"   - expect: {s['expected'] or '(nothing stated)'}")
        md.append("")
    with io.open(os.path.join(out_dir, "design.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(md).rstrip() + "\n")


def clear_outputs(out_dir: str) -> None:
    """A failed run must not leave the previous run's design on the page."""
    for name in ARTIFACTS:
        try:
            os.remove(os.path.join(out_dir, name))
        except OSError:
            pass


# ---- the run ----------------------------------------------------------

def cache_key(prompt: str, mode: str, model: str) -> str:
    basis = json.dumps({"v": CACHE_VERSION, "prompt": prompt, "mode": mode,
                        "model": model}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def cache_get(out_dir: str, key: str):
    """{reply, at, model} for a question asked before, or None. A file
    that is damaged, or is not the answer to THIS question, is no hit."""
    try:
        with io.open(os.path.join(out_dir, CACHE_DIR, key + ".json"), encoding="utf-8") as fh:
            hit = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(hit, dict) or hit.get("key") != key or \
            not isinstance(hit.get("reply"), str) or not hit["reply"].strip():
        return None
    hit["at"] = hit["at"] if isinstance(hit.get("at"), str) else ""
    hit["model"] = hit["model"] if isinstance(hit.get("model"), str) else ""
    try:                                   # used now: not the next to be pruned
        os.utime(os.path.join(out_dir, CACHE_DIR, key + ".json"))
    except OSError:
        pass
    return hit


def cache_clear(out_dir: str, only_model: str = "") -> int:
    """Forget every kept reply of this job -- or, with `only_model`, only
    the ones that model gave. Returns how many were forgotten."""
    d = os.path.join(out_dir, CACHE_DIR)
    n = 0
    try:
        names = os.listdir(d)
    except OSError:
        return 0
    for name in names:
        if not name.endswith((".json", ".tmp")):
            continue
        if only_model and name.endswith(".json"):
            try:
                with io.open(os.path.join(d, name), encoding="utf-8") as fh:
                    if json.load(fh).get("model") != only_model:
                        continue
            except (OSError, ValueError, AttributeError):
                continue
        try:
            os.remove(os.path.join(d, name))
            n += name.endswith(".json")
        except OSError:
            pass
    return n


def cache_drop(out_dir: str, key: str) -> None:
    try:
        os.remove(os.path.join(out_dir, CACHE_DIR, key + ".json"))
    except OSError:
        pass


def cache_put(out_dir: str, key: str, reply: str, model: str) -> None:
    """Best effort: a design that cannot be remembered is still a design."""
    d = os.path.join(out_dir, CACHE_DIR)
    tmp = os.path.join(d, f"{key}.{os.getpid()}.tmp")
    try:
        os.makedirs(d, exist_ok=True)
        with io.open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"key": key, "model": model, "reply": reply,
                       "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}, fh)
        os.replace(tmp, os.path.join(d, key + ".json"))
        kept = sorted((n for n in os.listdir(d) if n.endswith(".json")),
                      key=lambda n: os.path.getmtime(os.path.join(d, n)), reverse=True)
        for old in kept[CACHE_KEEP:]:
            os.remove(os.path.join(d, old))
    except OSError:
        pass
    finally:
        try:
            os.remove(tmp)                 # only still there when the write failed
        except OSError:
            pass


def deadline_for(what: str, policy: dict = None) -> float:
    """Seconds one Cursor call of this kind may take (policy.json)."""
    try:
        policy = loop.load_policy() if policy is None else policy
    except (OSError, ValueError):
        policy = {}
    return loop.cursor_deadline(policy, what, DEFAULT_DEADLINE)


def shares(counts: list, total: int) -> list:
    """`total` cases divided between parts in proportion to their
    endpoints, summing to `total` exactly (largest remainder), each part
    getting at least one while there are enough to go round."""
    n = sum(counts) or 1
    exact = [total * c / n for c in counts]
    out = [int(x) for x in exact]
    for i in sorted(range(len(counts)), key=lambda i: exact[i] - out[i], reverse=True):
        if sum(out) >= total:
            break
        out[i] += 1
    for i, v in enumerate(out):
        if v == 0 and counts[i] and total >= len(counts):
            j = max(range(len(out)), key=lambda k: out[k])
            out[j] -= 1
            out[i] = 1
    return out


def call_cursor(prompt: str, root: str, on_event, followup, mode: str = "plan",
                deadline: float = DEFAULT_DEADLINE) -> dict:
    """Cursor, in plan mode, started in an empty directory."""
    ca, cfg = loop.cursor_config(root)
    hide = lambda s: ca.redact_for_log(str(s), cfg.api_key)
    # The SDK's child process can still hold the directory when this
    # block ends. A failed clean-up must not cost a reply already paid for.
    with tempfile.TemporaryDirectory(prefix="b2b-design-",
                                     ignore_cleanup_errors=True) as empty:
        cfg.cwd = empty
        try:
            out = loop.cursor_call.run(prompt, ca, cfg,
                                       on_event=lambda line: on_event(hide(line)),
                                       followup=followup, mode=mode,
                                       deadline_seconds=deadline)
        except loop.cursor_call.CursorCallError as e:
            raise loop.CursorFailed(hide(e) + ("\n" + hide(e.details) if e.details else ""),
                                    e.kind, e.cancelled) from e
    out["result"] = hide(out.get("result") or "")
    return out


def _tree_state(root: str):
    try:
        return loop.dirty_paths(root), loop.head(root)
    except Exception:                                    # noqa: BLE001
        return None


def design(job: str, service: str = "", swagger: str = "", requirements: str = "",
           notes: str = "", speed: str = "balanced", root: str = "",
           agent=None, mode: str = "plan", fresh: bool = False) -> int:
    """Run the step. `agent(prompt, followup) -> {"result": text}` replaces
    Cursor in the tests."""
    root = root or ROOT
    out_dir = loop.intake.job_dir(job, root)
    os.makedirs(out_dir, exist_ok=True)
    clear_outputs(out_dir)
    if speed not in SPEEDS:
        say(f"FAIL unknown speed {speed!r}. Known: {', '.join(SPEEDS)}")
        return 1
    if mode not in MODES:
        say(f"FAIL unknown mode {mode!r}. Known: {', '.join(MODES)}")
        return 1
    max_cases = SPEEDS[speed]
    warnings = []

    where = "given for this run"
    if not swagger.strip():
        swagger, where = find_spec(out_dir)
    spec, how = read_spec(swagger)
    endpoints, known, source, swagger_sent = [], [], "none", swagger
    if spec is not None:
        endpoints = endpoints_of(spec)
        prefixes = base_paths(spec)
        known = match_lines(endpoints, prefixes)
        source = "specification"
        swagger_sent, whole_notes = spec_text(spec, SECTION_LIMITS["swagger"])
        say(f" ok  specification ({how}, {where}): {len(endpoints)} endpoint(s)"
            + (f", base path {', '.join(prefixes)}" if prefixes else ""))
        if not endpoints:
            warnings.append("the specification has a `paths` section but no "
                            "endpoint could be read from it; the endpoints of "
                            "the designed cases were NOT checked")
    elif swagger.strip():
        warnings.append(f"the specification could not be read as a "
                        f"specification ({how}); it was sent as text, the "
                        f"endpoints of the designed cases were NOT checked, "
                        f"and only the hosts of URLs were removed from it")
        say(f" ..  specification: {how}; sent as text, endpoints not checked")
    else:
        say(" ..  no specification given or found in this job")

    # Without the line that says WHEN the story was read: it is not part
    # of the story, and it would make every re-read a new question.
    story = "\n".join(l for l in _read(os.path.join(out_dir, "brief.md")).splitlines()
                      if not l.startswith("- at:"))
    # The brief this design is made FROM, taken now. "Read it" can be run
    # again while a fifteen-minute design is in flight; stamped at the
    # end, the design would claim the new story.
    designed_from = loop.brief_fingerprint(out_dir)
    listed = "\n".join(endpoints)
    if spec is not None and base_paths(spec):
        listed = (f"(every path below is served under {', '.join(base_paths(spec))}; "
                  f"write the path as listed, without it)\n" + listed)
    if not endpoints:
        try:
            got = json.loads(_read(os.path.join(out_dir, "intake.json")) or "{}")
        except ValueError:
            got = {}
        endpoints = [f"{r.get('verb', '').upper()} {r.get('raw_path', '')}"
                     for r in got.get("requests") or []
                     if isinstance(r, dict) and r.get("verb") and r.get("raw_path")]
        if endpoints:
            # Requests as somebody sent them: real values where the
            # contract has a parameter. Useful to the designer, useless
            # as a list to hold its answer to.
            source = "intake requests"
            listed = ("(there is no specification. These are the requests in "
                      "the story, with real values in their paths. Use the "
                      "same paths, writing {name} where a value stands.)\n"
                      + "\n".join(endpoints))
            warnings.append("there was no specification: the endpoints of the "
                            "designed cases were NOT checked against a contract")
            say(f" ok  {len(endpoints)} endpoint(s) taken from the requests intake read")
    if not (swagger.strip() or requirements.strip() or endpoints):
        say("FAIL nothing to design from. Give a specification or "
            "requirements here, or run 'Read it' first with a story that "
            "carries a request.")
        return 1

    prefixes = base_paths(spec) if spec is not None else []
    parts = [None]
    if spec is not None and endpoints:
        if len(swagger) > 4 * SECTION_LIMITS["swagger"]:
            say(" ..  working out how to split the specification (this can "
                "take a moment for a large one)")
        parts = plan_parts(spec, SECTION_LIMITS["swagger"])
        if len(parts) > 1:
            say(f" ..  the specification does not fit one prompt at full "
                f"detail: {len(parts)} calls, one part of the API each")
    if len(parts) == 1 and spec is not None:
        warnings += whole_notes
    part_counts = [len(endpoints_of(sub_spec(spec, p))) for p in parts] \
        if len(parts) > 1 else [len(endpoints)]
    part_share = shares(part_counts, max_cases) if len(parts) > 1 else [max_cases]
    unresolved_refs = set()
    asked_service = service.strip()

    private = loop.config_secrets(os.path.join(
        root, "src", "main", "resources", "program_configuration.json"))
    # The agent copies endpoints from the prompt. If a private value
    # happens to be a path segment, the copied form must still match.
    known += [redact(line, private)[0] for line in known]

    secret, asked_model = "", ""
    mocked = False
    if agent is None:
        try:
            mocked = loop.mock_cursor()
        except RuntimeError as e:
            say(f"FAIL {e}")
            return 1
    if mocked:
        # Its own cache key too: a stand-in's answer is never handed back
        # later as what Cursor said.
        agent, asked_model = loop.mock.cursor, "mock"
        say("MOCK CURSOR: the design below comes from the workbench's stand-in, "
            "two cases per endpoint, to show the flow. Cursor is not called. "
            "(\"cursor\": false in tools/agent/mock.json for a real design.)")
    if agent is None:
        try:
            _cfg = loop.cursor_config(root)[1]
            secret, asked_model = _cfg.api_key, str(getattr(_cfg, "model", "") or "")
        except Exception as e:                           # noqa: BLE001
            say(f"FAIL the Cursor configuration could not be read ({type(e).__name__})")
            return 1
    sdk_ready = []

    def need_cursor() -> None:
        """Only when a call is really about to be made: a run answered
        from kept replies needs no SDK and no key."""
        if sdk_ready:
            return
        try:
            loop.ensure_sdk(root)
        except RuntimeError as e:
            raise loop.CursorFailed(str(e), "setup") from e
        if not secret:
            raise loop.CursorFailed(
                "no Cursor API key. Set CURSOR_API_KEY, or put it in "
                "tools/ra_converter/cursor_agent.json (gitignored).", "setup")
        sdk_ready.append(True)

    reused = []
    if fresh:
        if agent is None:
            # Forgetting comes AFTER knowing a new answer can be asked for.
            # With no key, "Ask again" used to cost every kept reply and
            # get nothing for them.
            try:
                need_cursor()
            except loop.CursorFailed as e:
                say(f"FAIL {e}")
                return 1
        # The stand-in forgets only what the stand-in said: "Ask again" in
        # mock mode must not throw away replies Cursor was paid for.
        forgotten = cache_clear(out_dir, only_model="mock" if mocked else "")
        if forgotten:
            say(f" ..  --fresh: {forgotten} kept reply(ies) for this job were forgotten")
    clog = loop.CursorLog(out_dir, secret, list(private))
    clog.write(f"===== design the API tests: job {job}, speed {speed} "
               f"(at most {max_cases}), {len(parts)} call(s) =====")
    deadline = deadline_for("design")
    before = _tree_state(root) if agent is None else None
    removed = {"private": 0, "hosts": 0, "leaked": 0}

    def ask(prompt: str, label: str) -> tuple:
        """One call: (parsed reply, how it was read, model, keep). `keep`
        is (key, reply, model) when the reply may be kept for next time,
        else None. RuntimeError when the agent failed or nothing usable
        came back."""
        # The requirements and the story go out with every part; counting
        # their removals once per part would report six times what is there.
        prompt, n = redact(prompt, private)
        removed["private"] = max(removed["private"], n)
        prompt, n = hide_hosts(prompt)
        removed["hosts"] = max(removed["hosts"], n)
        clog.block(f"prompt{' ' + label if label else ''}", prompt)
        first = {}

        def followup(text: str):
            first["text"] = text
            try:
                read_reply(text)
                return None
            except ValueError:
                return JSON_ONLY

        key = cache_key(prompt, mode, asked_model)
        hit = None if fresh else cache_get(out_dir, key)
        if hit:
            try:
                read_reply(redact(hit["reply"], private)[0])
            except ValueError:
                # Kept by a version that read replies differently, or
                # damaged since. Asking again is right; failing is not.
                clog.write(f"the kept reply (design-cache/{key[:12]}...) can no "
                           f"longer be read; asking Cursor again")
                cache_drop(out_dir, key)
                hit = None
        if hit:
            # Exactly this was asked before and the answer was read whole.
            reused.append(hit["at"])
            got = {"result": hit["reply"], "model": hit["model"]}
            say(f" ..  {'for ' + label + ': ' if label else ''}nothing in the "
                f"request has changed since {hit['at'] or 'an earlier run'}; "
                f"that reply is used (--fresh asks again)")
            clog.write(f"reused the reply of {hit['at'] or '?'} "
                       f"(design-cache/{key[:12]}...); Cursor was not called")
        else:
            say(f" ..  asking Cursor{' for ' + label if label else ''} "
                f"({len(prompt)} characters, {mode} mode"
                + (f", up to {deadline:.0f}s" if deadline else "")
                + "); progress is in the Cursor log")
            if agent is not None:
                got = agent(prompt, followup)
            else:
                need_cursor()
                got = call_cursor(prompt, root, lambda line: clog.write("  cursor: " + line),
                                  followup, mode, deadline)
        # What comes back is as untrusted as what went in: the agent is a
        # program on this machine and may have read more than it was sent.
        # (A kept reply was redacted before it was kept; the private
        # configuration may have gained a value since.)
        reply, n = redact(str(got.get("result") or ""), private)
        removed["leaked"] += n
        if first.get("text") is not None:
            first["text"] = redact(str(first["text"]), private)[0]
        if hit:
            clog.write(f"(its {len(reply)} characters are in the log of the run that asked)")
        else:
            clog.block(f"reply{' ' + label if label else ''}", reply)
        # The last reply read whole; else the complete cases of whichever
        # reply has any. `first` is the answer before the "JSON only" turn.
        candidates = [reply] + ([first["text"]] if first.get("text") not in (None, reply) else [])
        for partial in (False, True):
            for text in candidates:
                try:
                    data, how = read_reply(text, partial)
                except ValueError:
                    continue
                # Kept only when it is the one reply to the one prompt and
                # was read to its end: a second turn's answer is an answer
                # to a different conversation, and a cut-off one is not an
                # answer worth repeating.
                one_turn = first.get("text") in (None, reply)
                keep = (key, reply, str(got.get("model", "") or asked_model)) \
                    if (not hit and not partial and text is reply and one_turn) else None
                return data, how, got.get("model", ""), keep
        raise RuntimeError("Cursor answered, but no test cases could be read "
                           "from the reply. It is in the Cursor log.")

    raw, how_read, failed, summaries, questions, model = [], [], [], [], [], ""
    fatal = ""
    is_partial = False
    for n, paths in enumerate(parts, 1):
        if len(parts) == 1:
            label, part_spec, part_listed, share = "", swagger_sent, listed, max_cases
        else:
            label = f"part {n} of {len(parts)}"
            sub = sub_spec(spec, paths, unresolved_refs)
            part_eps = endpoints_of(sub)
            part_spec, notes_ = spec_text(sub, SECTION_LIMITS["swagger"])
            warnings += [f"{label}: {x}" for x in notes_ if "entries were not sent" not in x]
            part_listed = (f"(this is {label} of the API; the other parts are "
                           f"designed separately)\n"
                           + (f"(every path below is served under {', '.join(prefixes)}; "
                              f"write the path as listed, without it)\n" if prefixes else "")
                           + "\n".join(part_eps))
            share = part_share[n - 1]
            if share < 1:
                failed.append(f"{label} ({len(paths)} path(s), from `{paths[0]}`) "
                              f"was not designed: the case limit of {max_cases} "
                              f"leaves it none. Use a higher limit.")
                continue
        sections, cut = fit({"swagger": part_spec, "requirements": requirements,
                             "story": story, "notes": notes, "endpoints": part_listed})
        for line in cut:
            line = f"{label}: {line}" if label else line
            if line not in warnings:
                warnings.append(line)
                say(f" ..  {line}")
        empty = "(none given)"
        prompt = render(_read(TEMPLATE), {
            "service": asked_service or job,
            "max_cases": share,
            "endpoints": sections["endpoints"] or
                         "(none could be read. Name an endpoint only as the "
                         "material itself writes it.)",
            "swagger": sections["swagger"] or empty,
            "requirements": sections["requirements"] or empty,
            "story": sections["story"] or empty,
            "notes": sections["notes"] or empty,
        })
        try:
            data, how, model, keep = ask(prompt, label)
        except RuntimeError as e:
            clog.write(f"FAILED{' ' + label if label else ''}: {e}")
            if len(parts) == 1:
                # Not `return`: what follows the loop checks whether the
                # repository changed and what leaked, and a call that
                # FAILED is the one most worth checking.
                fatal = str(e)
                break
            first_line = str(e).strip().splitlines()[0] if str(e).strip() else "failed"
            failed.append(f"{label} ({len(paths)} path(s), from `{paths[0]}`) "
                          f"produced nothing: {first_line}")
            say(f"WARN {failed[-1]}")
            if getattr(e, "kind", "") in ("setup", "startup", "timeout"):
                # The next part would meet the same key, the same network,
                # the same stuck service -- and wait just as long to say so.
                left = len(parts) - n
                if left:
                    failed.append(f"{left} further part(s) were not attempted: "
                                  f"the failure above would repeat")
                    say(f"WARN {failed[-1]}")
                break
            continue
        got_cases = [c for c in (cases_in(data) or []) if isinstance(c, dict)]
        if len(parts) > 1:
            for c in got_cases:          # each part numbers from TC-001
                for key in [k for k in c if _norm_key(k) in ("id", "testid")]:
                    c[key] = ""
            if len(got_cases) > share:
                warnings.append(f"{label}: {len(got_cases)} cases came back; its "
                                f"share of the limit is {share}, and the first "
                                f"{share} were kept")
                got_cases = got_cases[:share]
        # Kept now, not at the end: a run stopped during part 4 has paid
        # for parts 1 to 3. And only when THIS reply gave a usable case --
        # one whose cases are all dropped is not worth repeating.
        # ...nor one whose cases are all on endpoints the specification
        # does not have: tab 3 is handed none of those.
        if keep and any(c.get("endpoint_in_spec") is not False
                        for c in normalise(got_cases, known)[0]):
            cache_put(out_dir, *keep)
        raw += got_cases
        how_read.append(how)
        is_partial = is_partial or how.startswith("partial")
        if isinstance(data, dict):
            if _text(data.get("summary")):
                summaries.append(_text(data.get("summary")))
            if isinstance(data.get("open_questions"), list):
                questions += [_text(q) for q in data["open_questions"] if _text(q)]
            service = service.strip() or _text(data.get("service"))

    if before is not None and _tree_state(root) != before:
        msg = ("files in the repository changed while the design ran. This "
               "step asks Cursor to change nothing: look at `git status` "
               "before going on.")
        warnings.append(msg)
        clog.write("WARNING: " + msg)
        say(f"WARN {msg}")
    if removed["private"]:
        say(f" ..  {removed['private']} host or credential value(s) from the "
            f"private configuration were removed from the prompt")
    if removed["hosts"]:
        say(f" ..  the host of {removed['hosts']} URL(s) in the material was "
            f"replaced with host.invalid")
    if removed["leaked"]:
        warnings.append(f"{removed['leaked']} host or credential value(s) of the "
                        f"private configuration were in Cursor's reply and were removed")
        say(f"WARN {removed['leaked']} private value(s) were in the reply and were removed")
    if not raw:
        for line in failed:
            say(f" ..  {line}")
        say(f"FAIL {fatal}" if fatal else
            "FAIL no part of the design produced a test case.")
        return 1

    parsed = "; ".join(dict.fromkeys(how_read))
    if any(h != "as written" for h in how_read):
        say(f" ..  the reply was read after a repair: {parsed}")
    if is_partial:
        warnings.append("a reply was cut off before its end; only the "
                        "complete test cases are here. Run again with speed "
                        "'fast', or with less material.")
    if failed:
        is_partial = True
        warnings += failed
    if unresolved_refs:
        warnings.append(f"{len(unresolved_refs)} reference(s) in the specification "
                        f"point at something that is not in it and were sent "
                        f"unresolved: " + ", ".join(sorted(unresolved_refs)[:5])
                        + (" ..." if len(unresolved_refs) > 5 else ""))

    cases, more = normalise(raw, known, max_cases)
    warnings += more
    if not cases:
        for w in more[:8]:
            say(f" ..  {w}")
        say("FAIL the reply holds no usable test case.")
        return 1
    missing = uncovered(endpoints, prefixes, cases) if source == "specification" else []
    if missing:
        warnings.append(f"{len(missing)} of {len(endpoints)} endpoint(s) have "
                        f"no test case; they are listed in design.md")
    result = {
        "job": job,
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "service": service.strip(),
        "speed": speed,
        "model": model,
        "parsed": parsed,
        "partial": is_partial,
        "calls": len(parts),
        "reused_calls": len(reused),
        "reused_from": sorted(set(r for r in reused if r)),
        "failed_parts": failed,
        "summary": " ".join(dict.fromkeys(summaries)),
        "mode": mode if agent is None else "",
        "mock": mocked,
        "brief": designed_from,
        "endpoints": endpoints,
        "endpoint_source": source,
        "endpoints_checked": bool(known),
        "uncovered_endpoints": missing,
        "test_cases": cases,
        "open_questions": list(dict.fromkeys(questions)),
        "warnings": warnings,
    }
    write_outputs(out_dir, result)
    clog.write(f"design: {len(cases)} test case(s) written ({parsed})")
    for w in warnings:
        say(f" ..  {w}")
    say(f" ok  {len(cases)} test case(s) designed"
        + (" -- PARTIAL" if is_partial else "")
        + f". Read design.md, then the Agent loop tab can implement them.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--job", required=True)
    ap.add_argument("--service", default="")
    ap.add_argument("--swagger-file", default="")
    ap.add_argument("--requirements-file", default="")
    ap.add_argument("--notes-file", default="")
    ap.add_argument("--speed", default="balanced")
    ap.add_argument("--fresh", action="store_true",
                    help="forget every reply kept for this job, and ask Cursor")
    ap.add_argument("--mode", default="plan",
                    help="plan (default): Cursor proposes and changes nothing. "
                         "agent: only if plan mode gives no usable reply.")
    a = ap.parse_args(argv)
    for flag, path in (("--swagger-file", a.swagger_file),
                       ("--requirements-file", a.requirements_file),
                       ("--notes-file", a.notes_file)):
        if path and not os.path.isfile(path):
            say(f"FAIL {flag}: {path} is not a file")
            return 1
    try:
        out_dir = loop.intake.job_dir(a.job)
        # No file named: what the page pasted for this job, if anything.
        # The server removes these when a box is left empty.
        given = {"swagger": a.swagger_file, "requirements": a.requirements_file,
                 "notes": a.notes_file}
        text = {k: _read(v or os.path.join(out_dir, PASTED[k])) for k, v in given.items()}
        return design(a.job, a.service, text["swagger"], text["requirements"],
                      text["notes"], a.speed, mode=a.mode, fresh=a.fresh)
    except ValueError as e:                   # an unusable job id
        say(f"FAIL {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
