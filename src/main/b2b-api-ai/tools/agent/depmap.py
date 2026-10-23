"""What each endpoint a case calls depends on, and how this project already
makes it -- worked out, not guessed, for a NEW endpoint as much as for one
the converted suites already test:

* NEEDS -- its path parameters and the ids its request body requires
  (the API specification);
* WHO MAKES EACH -- an operation of the specification whose response
  carries that field, and the project helper that already calls that
  operation (StoryTestBase / GoalStoryTestBase, read from their
  `step("<name>: <METHOD> " + ApiRoutes.<ROUTE>` labels), followed back
  until nothing more is needed: the setup chain, in order;
* HOW IT IS TESTED TODAY -- the converted suites' chains that call it
  (flow catalog), or, for an endpoint nothing calls yet, the nearest
  endpoints of the same resource that are tested.

Handed to Design and to Automate as one short block per endpoint, so a test
of a brand-new call is built from the project's own parts. What it cannot
resolve it says, and the design turns that into an open question.

    python tools/agent/depmap.py --job J [--endpoint "POST /path"]...
"""
from __future__ import annotations

import io
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))

ROUTES_REL = "src/main/java/com/hi/api/rest/ApiRoutes.java"
BASES = {"b2b": "src/test/java/com/hi/api/tests/StoryTestBase.java",
         "goal": "src/test/java/com/hi/api/tests/GoalStoryTestBase.java"}
CATALOG = ".cursor/skills/jira-to-restassured/flows/{project}.json"
METHODS = ("get", "post", "put", "patch", "delete")
MAX_DEPTH = 4
MAX_CHARS = 14000
_ROUTE_RX = re.compile(r'static\s+final\s+String\s+([A-Z_][A-Z0-9_]*)\s*=\s*"([^"]+)"\s*;', re.S)
_STEP_RX = re.compile(r'step\(\s*"(\w+):\s*(GET|POST|PUT|PATCH|DELETE)\s*"\s*\+\s*ApiRoutes\.([A-Z_][A-Z0-9_]*)')
_METHOD_RX = re.compile(r"\n\s*(?:public|protected|private)\s+(?:static\s+)?[\w<>\[\], .]+\s+(\w+)\s*\(")
_ID_FIELD_RX = re.compile(r"(?:Id|ID|Key|Number|Code)$")


def _read(path: str) -> str:
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def norm(path: str) -> str:
    """A path as compared: parameters as {}, no trailing slash."""
    return re.sub(r"\{[^}]+\}", "{}", str(path or "").strip()).rstrip("/") or "/"


def same_path(a: str, b: str) -> bool:
    """The same route, also when one is written under a base path (/v1/...)."""
    na, nb = norm(a), norm(b)
    return na == nb or na.endswith(nb) or nb.endswith(na)


# ---- the project's helpers -----------------------------------------------------------

def routes(root: str) -> dict:
    """{ROUTE: path} of ApiRoutes.java (a constant split over two lines too)."""
    text = re.sub(r'"\s*\+\s*"', "", _read(os.path.join(root, *ROUTES_REL.split("/"))))
    return {k: v for k, v in _ROUTE_RX.findall(re.sub(r"=\s*\n\s*", "= ", text))}


TOKEN = {"b2b": "serviceToken", "goal": "goalToken"}
FLOWS = {"b2b": ".cursor/skills/jira-to-restassured/B2B_FLOWS.md",
         "goal": ".cursor/skills/jira-to-restassured/GOAL_FLOWS.md"}


def documented(root: str, project: str) -> dict:
    """{converted step name: the helper the flow guide tells a test to call}
    -- its building-blocks table (`enrollGuest` -> `enrollActor`)."""
    out = {}
    for line in _read(os.path.join(root, *(FLOWS.get(project) or FLOWS["b2b"]).split("/"))).splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3:
            continue
        call = re.search(r"\b([a-z]\w*)\(", cells[2])
        if not call:
            continue
        for step in re.findall(r"`(\w+)`", cells[1]):
            out.setdefault(step, call.group(1))
    return out


def guide_gives(root: str, project: str) -> dict:
    """{id field (lower case): [helpers]} from the flow guide's building-blocks
    table: the helper a test calls to get that id (`accountId` ->
    createAccountFor), whether or not the specification declares it."""
    out = {}
    for line in _read(os.path.join(root, *(FLOWS.get(project) or FLOWS["b2b"]).split("/"))).splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        call = re.search(r"\b([a-z]\w*)\(", cells[2])
        if not call:
            continue
        for field in re.findall(r"\b([a-z]\w*(?:Id|Key|Code|Date))\b", cells[3]):
            helpers_of = out.setdefault(field.lower(), [])
            if call.group(1) not in helpers_of:
                helpers_of.append(call.group(1))
    return out


def helpers(root: str, project: str) -> list:
    """[{helper, step, method, path}] -- each base-class helper and the call
    it makes, read from its `step("<name>: <METHOD> " + ApiRoutes.X` label;
    named as the flow guide names it for a test (enrollActor, not the
    enrollGuest it calls)."""
    rel = BASES.get(project) or BASES["b2b"]
    text = _read(os.path.join(root, *rel.split("/")))
    known = routes(root)
    guide = documented(root, project)
    starts = [(m.start(), m.group(1)) for m in _METHOD_RX.finditer(text)]
    out = []
    for m in _STEP_RX.finditer(text):
        owner = next((name for pos, name in reversed(starts) if pos < m.start()), "")
        path = known.get(m.group(3))
        if owner and path:
            out.append({"helper": guide.get(m.group(1), owner), "step": m.group(1),
                        "method": m.group(2), "path": path,
                        "base": os.path.basename(rel)[:-len(".java")]})
    return out


def helper_for(method: str, path: str, hs: list) -> dict:
    return next((h for h in hs if h["method"] == method.upper() and same_path(h["path"], path)), {})


# ---- the specification ---------------------------------------------------------------

def _resolve(spec: dict, node, depth: int = 0):
    if depth > 8 or not isinstance(node, dict):
        return node if isinstance(node, dict) else {}
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/"):
        cur = spec
        for part in ref[2:].split("/"):
            cur = cur.get(part, {}) if isinstance(cur, dict) else {}
        return _resolve(spec, cur, depth + 1)
    for key in ("allOf", "oneOf", "anyOf"):
        if isinstance(node.get(key), list):
            merged = {"properties": {}, "required": []}
            for part in node[key]:
                p = _resolve(spec, part, depth + 1)
                merged["properties"].update(p.get("properties") or {})
                merged["required"] += list(p.get("required") or [])
            return merged
    return node


def _fields(spec: dict, schema, depth: int = 0) -> set:
    """Property names of a schema, two levels down (an id is often nested)."""
    s = _resolve(spec, schema)
    if s.get("type") == "array" or "items" in s:
        s = _resolve(spec, s.get("items") or {})
    names = set()
    for name, sub in (s.get("properties") or {}).items():
        names.add(name)
        if depth < 1:
            names |= _fields(spec, sub, depth + 1)
    return names


def operations(spec: dict) -> list:
    """[{method, path, needs, gives}] of the specification."""
    out = []
    for path, item in ((spec or {}).get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        shared = item.get("parameters") or []
        for method in METHODS:
            op = item.get(method)
            if not isinstance(op, dict):
                continue
            needs = list(dict.fromkeys(re.findall(r"\{([^}]+)\}", path)))
            schema = {}
            for prm in shared + (op.get("parameters") or []):
                prm = _resolve(spec, prm)
                if prm.get("in") == "query" and prm.get("required") and _ID_FIELD_RX.search(str(prm.get("name") or "")):
                    needs.append(prm["name"])
                if prm.get("in") == "body":                       # Swagger 2.0
                    schema = prm.get("schema") or {}
            body = _resolve(spec, op.get("requestBody") or {})   # OpenAPI 3
            for media in (body.get("content") or {}).values():
                schema = (media or {}).get("schema") or schema
                break
            sch = _resolve(spec, schema)
            for name in sch.get("required") or []:
                if _ID_FIELD_RX.search(str(name)) and name not in needs:
                    needs.append(name)
            gives = set()
            for code, resp in (op.get("responses") or {}).items():
                if not str(code).startswith("2"):
                    continue
                resp = _resolve(spec, resp)
                for media in (resp.get("content") or {}).values():   # OpenAPI 3
                    gives |= _fields(spec, (media or {}).get("schema") or {})
                if resp.get("schema"):                                # Swagger 2.0
                    gives |= _fields(spec, resp["schema"])
                gives |= {str(h) for h in (resp.get("headers") or {})}
            out.append({"method": method.upper(), "path": path, "needs": needs, "gives": gives})
    return out


# ---- the converted suites --------------------------------------------------------------

def catalog(root: str, project: str) -> dict:
    p = os.path.join(root, *CATALOG.format(project=project).split("/"))
    try:
        with io.open(p, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def tested_chains(cat: dict, method: str, path: str, limit: int = 2) -> list:
    """Chains of the converted suites that call this endpoint (shortest first)."""
    chains = cat.get("chains") or []
    hits = []
    for key, idx in (cat.get("by_endpoint") or {}).items():
        m, _s, p = key.partition(" ")
        if m == method.upper() and same_path(p, path):
            hits += [chains[i] for i in idx if isinstance(i, int) and 0 <= i < len(chains)]
    def first_is_it(c):
        st = (c.get("steps") or [""])[0]
        return st.split(" ", 1)[0] == method.upper() and same_path(st.split(" ", 1)[-1], path)
    hits.sort(key=lambda c: (first_is_it(c), len(c.get("steps") or [])))
    return hits[:limit]


def nearest(cat: dict, path: str, limit: int = 4) -> list:
    """Tested endpoints sharing the longest leading part of the path."""
    target = [s for s in norm(path).split("/") if s]
    scored = []
    for key, idx in (cat.get("by_endpoint") or {}).items():
        _m, _s, p = key.partition(" ")
        segs = [s for s in norm(p).split("/") if s]
        common = 0
        for a, b in zip(target, segs):
            if a != b:
                break
            common += 1
        if common >= 2 and not same_path(p, path):
            scored.append((common, len(idx), key))
    scored.sort(key=lambda t: (-t[0], -t[1], t[2]))
    return [f"{k} (in {n} converted chain(s))" for _c, n, k in scored[:limit]]


# ---- one endpoint -------------------------------------------------------------------

def _producers(name: str, ops: list, hs: list, exclude: str) -> list:
    """Operations that give `name`, a project helper's first, POST before GET."""
    low = name.lower()
    got = [o for o in ops if any(g.lower() == low for g in o["gives"])
           and f"{o['method']} {o['path']}" != exclude]
    got.sort(key=lambda o: (not helper_for(o["method"], o["path"], hs),
                            {"POST": 0, "PUT": 1, "GET": 2}.get(o["method"], 3), len(o["path"])))
    return got


def chain_for(method: str, path: str, ops: list, hs: list, gives: dict = None) -> tuple:
    """(setup steps in order, what could not be resolved). The flow guide's
    helper for an id comes first (`gives`); else an operation of the
    specification whose response carries it. One step a call, with every
    id it gives; another helper that also gives an id is named beside it
    (the owner's memberId from createAccountFor, another member's from
    addMember)."""
    order, calls, open_, seen = [], {}, [], set()
    gives = gives or {}

    def need(name: str, depth: int, for_what: str):
        if name in seen:
            return
        seen.add(name)
        names = gives.get(name.lower()) or []
        h = next((x for x in hs if names and x["helper"] == names[0]), None)
        if names and not h:
            # A helper that makes its calls itself (GOAL's findAvailability):
            # the step is the helper, not one endpoint.
            key = f"`{names[0]}()`"
            if key not in calls:
                calls[key] = {"ids": [], "helper": None, "also": [], "bare": True}
                order.append(key)
            calls[key]["ids"].append(name)
            calls[key]["also"] += [f"`{other}()` for `{name}`" for other in names[1:]]
            return
        if h:
            spec_op = next((o for o in ops if o["method"] == h["method"] and same_path(o["path"], h["path"])), None)
            o = spec_op or {"method": h["method"], "path": h["path"],
                            "needs": re.findall(r"\{([^}]+)\}", h["path"])}
        else:
            found = _producers(name, ops, hs, for_what)
            if not found:
                open_.append(f"`{name}` (needed by {for_what}): no helper of the flow guide gives "
                             f"it, and no operation of the specification returns it")
                return
            o = found[0]
        key = f"{o['method']} {o['path']}"
        if depth < MAX_DEPTH:
            for n in o["needs"]:
                need(n, depth + 1, key)
        h = h or helper_for(o["method"], o["path"], hs)
        if key not in calls:
            calls[key] = {"ids": [], "helper": h, "also": []}
            order.append(key)
        calls[key]["ids"].append(name)
        calls[key]["also"] += [f"`{other}()` for `{name}`" for other in names[1:]]

    target = f"{method.upper()} {path}"
    op = next((o for o in ops if o["method"] == method.upper() and same_path(o["path"], path)), None)
    for n in (op["needs"] if op else re.findall(r"\{([^}]+)\}", path)):
        need(n, 1, target)
    steps = []
    for key in order:
        c = calls[key]
        line = f"{key} -> " + ", ".join(f"`{i}`" for i in c["ids"])
        if c.get("bare"):
            line += " -- a helper of the flow guide: it makes the calls itself"
        else:
            line += (f" -- the helper `{c['helper']['helper']}()` ({c['helper']['base']}) makes this call"
                     if c["helper"] else " -- no helper makes this call yet")
        if c["also"]:
            line += "; or " + ", ".join(c["also"]) + " when the case needs that one"
        steps.append(line)
    return steps, open_


def describe(method: str, path: str, ops: list, hs: list, cat: dict, gives: dict = None,
             token: str = "") -> str:
    target = f"{method.upper()} {path}"
    lines = [f"* {target}"]
    own = helper_for(method, path, hs)
    if own:
        lines.append(f"  made by the helper `{own['helper']}()` ({own['base']})")
    steps, open_ = chain_for(method, path, ops, hs, gives)
    if steps:
        lines.append("  setup, in order (each line: the call, the ids it gives, and its helper):")
        if token:
            lines.append(f"    0. `{token}()` -- the token, before any call")
        lines += [f"    {n}. {s}" for n, s in enumerate(steps, 1)]
    elif not open_:
        lines.append("  needs no id from an earlier call")
    for o in open_:
        lines.append(f"  NOT RESOLVED: {o} -- ask in open_questions")
    chains = tested_chains(cat, method, path)
    if chains:
        lines.append("  tested today by the converted suites, e.g.:")
        for c in chains:
            lines.append(f"    {c.get('suite', '?')} {c.get('case', '')}: " + " -> ".join(c.get("steps") or []))
    else:
        near = nearest(cat, path)
        lines.append("  no converted suite calls it yet" + (
            "; the nearest tested endpoints of the same resource (copy their setup and checks):"
            if near else "."))
        lines += [f"    {n}" for n in near]
    return "\n".join(lines)


def block(root: str, project: str, spec: dict, endpoints: list) -> str:
    """The DEPENDENCIES block for these endpoints (`METHOD /path` each)."""
    if not endpoints:
        return ""
    ops = operations(spec or {})
    hs = helpers(root, project)
    cat = catalog(root, project)
    gives = guide_gives(root, project)
    parts = []
    for ep in dict.fromkeys(endpoints):
        m = re.match(r"^\s*(GET|POST|PUT|PATCH|DELETE)\s+(\S+)", str(ep), re.I)
        if m:
            parts.append(describe(m.group(1), m.group(2), ops, hs, cat, gives, TOKEN.get(project, "")))
    if not parts:
        return ""
    text = ("===== DEPENDENCIES OF THE ENDPOINTS (worked out by the workbench from the API "
            "specification, this project's helpers and the converted suites) =====\n"
            "Each endpoint's setup chain: build a case's steps on it, in this order, with these "
            "helpers. A line marked NOT RESOLVED is a gap: ask, do not invent.\n\n"
            + "\n\n".join(parts))
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "\n[... more endpoints than fit; the rest are not shown ...]"
    return text + "\n"


def main(argv=None) -> int:
    import argparse
    import importlib.util
    import sys
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--job", required=True)
    ap.add_argument("--endpoint", action="append", default=[])
    a = ap.parse_args(argv)
    spec_ = importlib.util.spec_from_file_location("depmap_loop", os.path.join(HERE, "loop.py"))
    loop = importlib.util.module_from_spec(spec_)
    sys.modules["depmap_loop"] = loop
    spec_.loader.exec_module(loop)
    job_dir = loop.intake.job_dir(a.job, ROOT)
    spec, _where, _p = loop.job_spec(job_dir)
    eps = a.endpoint or [f"{o['method']} {o['path']}" for o in operations(spec or {})][:20]
    print(block(ROOT, loop.intake.job_project(job_dir), spec or {}, eps) or "(nothing to show)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
