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

Cursor is run in an EMPTY temporary directory: this step reads nothing
from the repository and has nothing to write to.

The specification's `servers` / `host` entries and every host or
credential value found in the private configuration are removed from
the prompt. Test design does not need them.

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
import importlib.util
import io
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone

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
                  "story": 40_000, "notes": 20_000}
TOTAL_LIMIT = 200_000
SPEEDS = {"fast": 25, "balanced": 40, "thorough": 80}
VERBS = ("get", "post", "put", "patch", "delete", "head", "options")
ARTIFACTS = ("design.json", "design.md", "test-cases.csv", "xray.csv")

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


def endpoints_of(spec: dict) -> list:
    """["VERB /path -- summary", ...] in the order the specification has them."""
    out = []
    for path, item in (spec.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        for verb, op in item.items():
            if str(verb).lower() not in VERBS:
                continue
            summary = ""
            if isinstance(op, dict):
                summary = str(op.get("summary") or op.get("operationId") or "").strip()
            line = f"{str(verb).upper()} {path}"
            out.append(line + (f" -- {summary[:100]}" if summary else ""))
    return out


_PARAM_RX = re.compile(r"\{[^/}]*\}|:[A-Za-z_][A-Za-z0-9_]*")


def endpoint_key(line: str) -> str:
    """`post /a/{id}/ -- text` and `POST /a/{propId}` are the same endpoint."""
    head = (line or "").split(" -- ")[0].strip()
    parts = head.split(None, 1)
    if len(parts) != 2 or parts[0].lower() not in VERBS or not parts[1].startswith("/"):
        return ""
    verb, path = parts[0].upper(), parts[1].split("?")[0].strip()
    path = _PARAM_RX.sub("{}", path).rstrip("/") or "/"
    return f"{verb} {path}"


def _strip(node, drop_examples: bool, shorten: int):
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            key = str(k)
            if drop_examples and (key in ("example", "examples") or key.startswith("x-")):
                continue
            if shorten and key == "description" and isinstance(v, str) and len(v) > shorten:
                v = v[:shorten].rstrip() + " ..."
            out[k] = _strip(v, drop_examples, shorten)
        return out
    if isinstance(node, list):
        return [_strip(v, drop_examples, shorten) for v in node]
    return node


def spec_text(spec: dict, limit: int) -> tuple:
    """The specification as it is sent, and what was done to make it fit.

    `servers`, `host` and `basePath`-less host data are dropped always:
    they are where the host names are, and a test design needs none.
    Then, only as far as needed: indentation, examples and vendor
    extensions, long descriptions.
    """
    notes = []
    body = {k: v for k, v in spec.items() if k not in ("servers", "host")}
    if len(body) != len(spec):
        notes.append("the specification's server/host entries were not sent")
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
    body = _strip(body, True, 0)
    text = dump(body, False)
    notes.append("examples and x- extensions were left out of the "
                 "specification to fit the prompt")
    if len(text) <= limit:
        return text, notes
    body = _strip(body, True, 160)
    text = dump(body, False)
    notes.append("descriptions in the specification were shortened to 160 characters")
    return text, notes


_FENCE_RX = re.compile(r"```[A-Za-z0-9_-]*[ \t]*\r?\n(.*?)```", re.S)


def find_spec(job_dir: str) -> tuple:
    """(text, where) for a specification already in the job directory:
    a code block on a Confluence page intake fetched, or the pasted text
    itself. ("", "") when there is none."""
    best = ("", "")
    sources = os.path.join(job_dir, "sources")
    for base, _dirs, files in os.walk(sources):
        for name in sorted(files):
            if not name.endswith(".md"):
                continue
            page = _read(os.path.join(base, name))
            for block in [m.group(1) for m in _FENCE_RX.finditer(page)] + [page]:
                if len(block) > len(best[0]) and read_spec(block)[0] is not None:
                    rel = os.path.relpath(os.path.join(base, name), job_dir)
                    best = (block, f"a code block in {rel.replace(os.sep, '/')}")
    pasted = _read(os.path.join(job_dir, "pasted.txt"))
    if len(pasted) > len(best[0]) and read_spec(pasted)[0] is not None:
        best = (pasted, "the text pasted in tab 1")
    return best


# ---- the prompt -------------------------------------------------------

def _cut(text: str, limit: int) -> str:
    left = len(text) - limit
    return text[:limit].rstrip() + f"\n[... {left} more characters were left out to fit the prompt ...]"


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

def _first(d: dict, *names):
    lowered = {str(k).lower().replace(" ", "_"): v for k, v in d.items()}
    for n in names:
        v = lowered.get(n)
        if v not in (None, "", [], {}):
            return v
    return ""


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
        if isinstance(refs, str):
            refs = [refs]
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

    md = [f"# API test design: {design['service'] or design['job']}", "",
          f"- job: {design['job']}", f"- at: {design['at']}",
          f"- test cases: {len(cases)}"
          + (" (PARTIAL: the reply was cut off)" if design.get("partial") else ""),
          f"- reply read: {design['parsed']}", ""]
    if design.get("summary"):
        md += [design["summary"], ""]
    if design["warnings"]:
        md += ["## Read this first", ""] + [f"- {w}" for w in design["warnings"]] + [""]
    if design["open_questions"]:
        md += ["## Open questions", ""] + [f"- {q}" for q in design["open_questions"]] + [""]
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

def call_cursor(prompt: str, root: str, on_event, followup) -> dict:
    """Cursor, in an empty directory: nothing to read, nothing to change."""
    ca, cfg = loop.cursor_config(root)
    hide = lambda s: ca.redact_for_log(str(s), cfg.api_key)
    with tempfile.TemporaryDirectory(prefix="b2b-design-") as empty:
        cfg.cwd = empty
        try:
            out = loop.cursor_call.run(prompt, ca, cfg,
                                       on_event=lambda line: on_event(hide(line)),
                                       followup=followup)
        except loop.cursor_call.CursorCallError as e:
            raise RuntimeError(hide(e) + ("\n" + hide(e.details) if e.details else "")) from e
    out["result"] = hide(out.get("result") or "")
    return out


def design(job: str, service: str = "", swagger: str = "", requirements: str = "",
           notes: str = "", speed: str = "balanced", root: str = "",
           agent=None) -> int:
    """Run the step. `agent(prompt, followup) -> {"result": text}` replaces
    Cursor in the tests."""
    root = root or ROOT
    out_dir = loop.intake.job_dir(job, root)
    os.makedirs(out_dir, exist_ok=True)
    clear_outputs(out_dir)
    if speed not in SPEEDS:
        say(f"FAIL unknown speed {speed!r}. Known: {', '.join(SPEEDS)}")
        return 1
    max_cases = SPEEDS[speed]
    warnings = []

    where = "given for this run"
    if not swagger.strip():
        swagger, where = find_spec(out_dir)
    spec, how = read_spec(swagger)
    endpoints, swagger_sent = [], swagger
    if spec is not None:
        endpoints = endpoints_of(spec)
        swagger_sent, notes_ = spec_text(spec, SECTION_LIMITS["swagger"])
        warnings += notes_
        say(f" ok  specification ({how}, {where}): {len(endpoints)} endpoint(s)")
    elif swagger.strip():
        warnings.append(f"the specification could not be read as a "
                        f"specification ({how}); it was sent as text and "
                        f"the endpoints of the designed cases were NOT checked")
        say(f" ..  specification: {how}; sent as text, endpoints not checked")
    else:
        say(" ..  no specification given or found in this job")

    story = _read(os.path.join(out_dir, "brief.md"))
    if not endpoints:
        try:
            got = json.loads(_read(os.path.join(out_dir, "intake.json")) or "{}")
        except ValueError:
            got = {}
        endpoints = [f"{r.get('verb', '').upper()} {r.get('raw_path', '')}"
                     for r in got.get("requests") or []
                     if r.get("verb") and r.get("raw_path")]
        if endpoints:
            say(f" ok  {len(endpoints)} endpoint(s) taken from the requests intake read")
    if not (swagger.strip() or requirements.strip() or endpoints):
        say("FAIL nothing to design from. Give a specification or "
            "requirements here, or run 'Read it' first with a story that "
            "carries a request.")
        return 1

    sections, cut = fit({"swagger": swagger_sent, "requirements": requirements,
                         "story": story, "notes": notes})
    warnings += cut
    for line in cut:
        say(f" ..  {line}")
    empty = "(none given)"
    prompt = render(_read(TEMPLATE), {
        "service": service.strip() or job,
        "max_cases": max_cases,
        "endpoints": "\n".join(endpoints) if endpoints else
                     "(none could be read. Name an endpoint only as the "
                     "material itself writes it.)",
        "swagger": sections["swagger"] or empty,
        "requirements": sections["requirements"] or empty,
        "story": sections["story"] or empty,
        "notes": sections["notes"] or empty,
    })
    private = loop.config_secrets(os.path.join(
        root, "src", "main", "resources", "program_configuration.json"))
    prompt, hidden = redact(prompt, private)
    if hidden:
        say(f" ..  {hidden} host or credential value(s) from the private "
            f"configuration were removed from the prompt")

    secret = ""
    if agent is None:
        try:
            loop.ensure_sdk(root)
            secret = loop.cursor_config(root)[1].api_key
        except RuntimeError as e:
            say(f"FAIL {e}")
            return 1
    clog = loop.CursorLog(out_dir, secret, list(private))
    clog.write(f"===== design the API tests: job {job}, speed {speed} "
               f"(at most {max_cases}) =====")
    clog.block("prompt", prompt)

    first = {}

    def followup(text: str):
        first["text"] = text
        try:
            jsonfix.loads(text)
            return None
        except ValueError:
            return JSON_ONLY

    say(f" ..  asking Cursor ({len(prompt)} characters); progress is in the Cursor log")
    try:
        if agent is not None:
            got = agent(prompt, followup)
        else:
            got = call_cursor(prompt, root,
                              lambda line: clog.write("  cursor: " + line), followup)
    except RuntimeError as e:
        clog.write(f"FAILED: {e}")
        say(f"FAIL {e}")
        return 1
    reply = str(got.get("result") or "")
    clog.block("reply", reply)

    # The last reply read whole; else the complete cases of whichever
    # reply has any. `first` is the answer before the "JSON only" turn.
    data, parsed = None, ""
    candidates = [reply] + ([first["text"]] if first.get("text") not in (None, reply) else [])
    for partial in ("", "test_cases"):
        for text in candidates:
            try:
                data, parsed = jsonfix.loads(text, partial_key=partial)
                break
            except ValueError:
                continue
        if data is not None:
            break
    if data is None:
        say("FAIL Cursor answered, but no test cases could be read from the "
            "reply. It is in the Cursor log.")
        return 1
    is_partial = parsed.startswith("partial")
    if parsed != "as written":
        say(f" ..  the reply was read after a repair: {parsed}")
    if is_partial:
        warnings.append("the reply was cut off before its end; only the "
                        "complete test cases are here. Run again with speed "
                        "'fast', or with less material.")

    cases, more = normalise(data.get("test_cases"), endpoints, max_cases)
    warnings += more
    if not cases:
        for w in more[:8]:
            say(f" ..  {w}")
        say("FAIL the reply holds no usable test case.")
        return 1
    questions = data.get("open_questions")
    result = {
        "job": job,
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "service": service.strip() or _text(data.get("service")),
        "speed": speed,
        "model": got.get("model", ""),
        "parsed": parsed,
        "partial": is_partial,
        "summary": _text(data.get("summary")),
        "endpoints": endpoints,
        "endpoints_checked": bool(endpoints),
        "test_cases": cases,
        "open_questions": [_text(q) for q in questions if _text(q)]
                          if isinstance(questions, list) else [],
        "warnings": warnings,
    }
    write_outputs(out_dir, result)
    clog.write(f"design: {len(cases)} test case(s) written ({parsed})")
    for w in warnings:
        say(f" ..  {w}")
    say(f" ok  {len(cases)} test case(s) designed"
        + (" -- PARTIAL" if is_partial else "")
        + f". Read design.md, then tab 3 can implement them.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--job", required=True)
    ap.add_argument("--service", default="")
    ap.add_argument("--swagger-file", default="")
    ap.add_argument("--requirements-file", default="")
    ap.add_argument("--notes-file", default="")
    ap.add_argument("--speed", default="balanced")
    a = ap.parse_args(argv)
    for flag, path in (("--swagger-file", a.swagger_file),
                       ("--requirements-file", a.requirements_file),
                       ("--notes-file", a.notes_file)):
        if path and not os.path.isfile(path):
            say(f"FAIL {flag}: {path} is not a file")
            return 1
    try:
        return design(a.job, a.service, _read(a.swagger_file),
                      _read(a.requirements_file), _read(a.notes_file), a.speed)
    except ValueError as e:                   # an unusable job id
        say(f"FAIL {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
