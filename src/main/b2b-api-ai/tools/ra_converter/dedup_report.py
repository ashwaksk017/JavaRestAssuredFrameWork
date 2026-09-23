"""Find generated methods that are the SAME CALL with different data.

    python tools/ra_converter/dedup_report.py [--root .] [--package-root com.ak.api]
                                              [--out target/dedup-report.txt] [--min 2]

Runs on the EMITTED Java after a conversion -- no converter state needed,
so it works on any checkout, including a machine that only pulled and
converted. It parses every generated phase / verify / setup method,
reduces each single-call method to its call shape (verb, path template,
query and header key sets, body or not) and groups by that. A group of two
or more is one engine method after Stage 1b; the report says what the
members differ in (expected status, query sources, assertions, extracts),
which is exactly the data a phase spec has to carry.

Example that motivated it: `runVerifyVerify8` and `runVerifyVerify9`
both GET /guests/{guestId}/businesses/verify with the same eleven query
keys; they differ in where `emailDomain` comes from and in their
assertion sets. Two 30-line methods, one call.

Safe to paste: method names, paths, column names and JSON paths only.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from phase_model import normalize_path  # noqa: E402

_METHOD = re.compile(
    r"\n    public (?:S|void|static void) (\w+)\((?P<params>[^)]*)\)(?: throws Exception)? \{\n(?P<body>.*?)\n    \}\n",
    re.S)
_STEP = re.compile(r"// ==== REST step: (?P<name>.+?)  \((?P<verb>[A-Z]+) (?P<path>\S+)\) ====")
_QUERY = re.compile(r'\.query\("([^"]+)", "([^"]*)"\)')
_HEADER = re.compile(r'\.header\("([^"]+)", "([^"]*)"\)')
_STATUS = re.compile(r"\.expectedStatus\((-?\d+)\)")
_TEMPLATE = re.compile(r"\.template\(")
_EXTRACT = re.compile(r'putExtracted\(ctx, "([^"]+)"')
_POLL = re.compile(r"\.pollUntil\w+\(")
_TRANSLATED = re.compile(r"// \[(groovy|translated|transfer step|properties step|jdbc)\]")

# Entry classes: `Onboarding`, `OnboardingFlowAGuestidmember3`, ... -- one
# `start(row)` each, differing only in what their bootstrap() sets up.
_ENTRY_CLASS = re.compile(r"public final class (\w+) extends (\w+)<")
_ENTRY_START = re.compile(r"public static \w+ start\(Map<String, String> row\)")
_BOOTSTRAP = re.compile(r"protected \w+ bootstrap\(\) throws Exception \{\n(?P<body>.*?)\n    \}\n", re.S)
_FLOW = re.compile(r'runSetup\("(\w+)"|SetupHelper\.(\w+)\(')
_PACK = re.compile(r'generateStandard\(ctx, "(\w+)", ([^)]*)\)')
_PICK = re.compile(r'putExtracted\(ctx, "([^"]+)", [^;]*?\.oneOf\(([^)]*)\)')
# the whole statement, so it can leave the shape (a pick is data, like a field)
_PICK_STMT = re.compile(r'[^\n;]*putExtracted\(ctx, "[^"]+", [^;]*?\.oneOf\([^)]*\)\);')


def parse_entry(java: str, file_label: str) -> dict | None:
    """One dict for a shared entry class, or None for any other file."""
    m = _ENTRY_CLASS.search(java)
    if not m or not _ENTRY_START.search(java):
        return None
    cls, base = m.group(1), m.group(2)
    b = _BOOTSTRAP.search(java)
    body = b.group("body") if b else ""
    flow = next((a or c for a, c in _FLOW.findall(body)), "")
    packs = tuple((ns, tuple(x.strip().strip('"') for x in fields.split(",") if x.strip()))
                  for ns, fields in _PACK.findall(body))
    picks = tuple((key, vals.replace('"', "").replace(" ", "")) for key, vals in _PICK.findall(body))
    # the setup SHAPE: the body with the class name, the generated field
    # lists and the picked literals taken out -- what is left is the flow
    shape = body.replace(cls, "<SELF>")
    shape = _PACK.sub(r'generateStandard(ctx, "\1", <PACK>)', shape)
    # a picked literal is data like a generated field: the whole statement
    # leaves the shape, so a class WITH a pick and one WITHOUT still group
    shape = _PICK_STMT.sub("", shape)
    shape = re.sub(r"\s+", " ", shape).strip()
    return {"file": file_label, "entry": cls, "base": base, "flow": flow or "-",
            "packs": packs, "picks": picks, "shape": (flow or "-", shape),
            "lines": body.count("\n") + 1 if body else 0}


def group_entries(entries: list[dict], minimum: int = 2) -> list[dict]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for e in entries:
        groups[e["shape"]].append(e)
    out = []
    for shape, members in groups.items():
        if len(members) < minimum:
            continue
        varies = {}
        if len({m["packs"] for m in members}) > 1:
            fields = sorted({f for m in members for _ns, fs in m["packs"] for f in fs})
            varies["generated fields"] = fields
        if len({m["picks"] for m in members}) > 1:
            varies["picked values"] = sorted({k for m in members for k, _ in m["picks"]})
        out.append({"shape": shape, "members": members, "varies": varies,
                    "lines": sum(m["lines"] for m in members)})
    out.sort(key=lambda g: (-len(g["members"]), g["shape"][0]))
    return out


def parse_methods(java: str, file_label: str) -> list[dict]:
    """One dict per generated method that wraps exactly one REST step."""
    out = []
    for m in _METHOD.finditer(java):
        body = m.group("body")
        steps = _STEP.findall(body)
        rec = {
            "file": file_label, "method": m.group(1), "rest_steps": len(steps),
            "translated": bool(_TRANSLATED.search(body)), "lines": body.count("\n") + 1,
        }
        if len(steps) != 1:
            out.append(rec)
            continue
        name, verb, path = steps[0]
        queries = _QUERY.findall(body)
        headers = _HEADER.findall(body)
        st = _STATUS.search(body)
        rec.update({
            "step": name, "verb": verb, "path": path,
            "shape": (verb, normalize_path(path),
                      tuple(sorted(k for k, _ in queries)),
                      tuple(sorted(k for k, _ in headers)),
                      bool(_TEMPLATE.search(body))),
            "query": dict(queries), "headers": dict(headers),
            "status": int(st.group(1)) if st else None,
            "asserts": tuple(sorted(_normalise_asserts(body))),
            "extracts": tuple(sorted(k for k in _EXTRACT.findall(body) if not k.endswith("_RawRequest"))),
            "poll": bool(_POLL.search(body)),
        })
        out.append(rec)
    return out


def _normalise_asserts(body: str) -> list[tuple]:
    """(kind, jsonPath, expectedLiteral) per ResponseAsserts call."""
    found = []
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("ResponseAsserts."):
            continue
        kind = line[len("ResponseAsserts."):line.index("(")]
        args = re.findall(r'"((?:[^"\\]|\\.)*)"', line)
        if not args:
            continue
        # helpers take (.., stepName, jsonPath[, expected]) or (.., jsonPath)
        if kind in ("jsonAbsent",):
            found.append((kind, args[-1], ""))
        elif kind in ("jsonEquals", "valueInResponse", "jsonTreeEquals") and len(args) >= 3:
            found.append((kind, args[-2], args[-1]))
        else:
            found.append((kind, args[-1], ""))
    return found


def template_stats(root: str) -> dict | None:
    """Request templates: files, distinct shapes, and same-shape families
    that share their placeholders (mergeable into one template + tpl_*
    CSV columns) -- the reuse a case can get without any code.

    Placeholder detection MUST be the converter's own `_leaf_is_placeholder`.
    A local regex here drifted from it and reported two families as
    "still mergeable" that the merger had correctly refused:

      #Properties_username##Properties_Hardcodeddomain#  (concatenated -- an
          inner `#` broke `#[^#]+#`, so it read as a literal)
      @Properties_guestIDmember2@ vs @Properties_memberGuestID@  (`@...@` was
          not matched at all, so two different identity slots looked equal)

    Both would have merged a member's guest id with another member's -- the
    same failure that made MemberHHonorsEnroll 409 on a duplicate email.
    """
    import glob
    import hashlib
    import json
    # Function-level: ra_converter imports this module (write_audit), so a
    # module-level import back would be circular.
    from ra_converter import _leaf_is_placeholder
    base = os.path.join(root, "src", "main", "resources", "templates")
    files = glob.glob(os.path.join(base, "**", "*.json"), recursive=True)
    if not files:
        return None

    def walk(o, path=""):
        if isinstance(o, dict):
            for k, v in o.items():
                yield from walk(v, f"{path}.{k}" if path else k)
        elif isinstance(o, list):
            for i, v in enumerate(o):
                yield from walk(v, f"{path}[{i}]")
        else:
            yield path, o

    shapes: dict[tuple, list[str]] = defaultdict(list)
    families: dict[tuple, list[str]] = defaultdict(list)
    merged = 0
    for f in files:
        name = os.path.basename(f)
        if "_merged_" in name:
            merged += 1
        try:
            with open(f, encoding="utf-8") as fh:
                tree = json.load(fh)
        except (OSError, ValueError):
            continue
        leaves = list(walk(tree))
        sig = tuple((p, type(v).__name__) for p, v in leaves)
        phs = tuple((p, v) for p, v in leaves if _leaf_is_placeholder(v))
        shapes[sig].append(name)
        families[(sig, phs)].append(name)
    mergeable = [v for v in families.values() if len(v) > 1]
    return {"files": len(files), "merged": merged, "shapes": len(shapes),
            "families": len(families),
            "mergeable_files": sum(len(v) for v in mergeable),
            "mergeable_groups": len(mergeable),
            "largest": sorted(mergeable, key=len, reverse=True)[:5]}


def render_templates(ts: dict | None) -> str:
    if not ts:
        return ""
    o = ["", "== request templates ==",
         f"template files: {ts['files']} ({ts['merged']} already merged)  ->  distinct JSON shapes: {ts['shapes']}  "
         f"->  same shape AND same placeholders: {ts['families']} families",
         f"still mergeable: {ts['mergeable_files']} files in {ts['mergeable_groups']} families "
         f"(same shape, same placeholders, different literals -> one template + tpl_* columns)"]
    for fam in ts["largest"]:
        o.append(f"   {len(fam)}x  " + ", ".join(fam[:4]) + (" ..." if len(fam) > 4 else ""))
    return "\n".join(o) + "\n"


def scan_tree(root: str, package_root: str) -> list[dict]:
    recs, _entries = scan_tree_full(root, package_root)
    return recs


def scan_tree_full(root: str, package_root: str) -> tuple[list[dict], list[dict]]:
    """(phase/verify/setup methods, entry classes) under support/."""
    base = os.path.join(root, "src", "main", "java", *package_root.split("."), "support")
    recs: list[dict] = []
    entries: list[dict] = []
    for dp, _dn, fn in os.walk(base):
        for f in fn:
            if not f.endswith(".java"):
                continue
            p = os.path.join(dp, f)
            try:
                with open(p, encoding="utf-8") as fh:
                    java = fh.read()
            except OSError:
                continue
            label = os.path.relpath(p, base).replace(os.sep, "/")
            recs.extend(parse_methods(java, label))
            e = parse_entry(java, label)
            if e:
                entries.append(e)
    return recs, entries


def group_by_shape(recs: list[dict], minimum: int = 2) -> list[dict]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in recs:
        if r.get("shape"):
            groups[r["shape"]].append(r)
    out = []
    for shape, members in groups.items():
        if len(members) < minimum:
            continue
        varies = {}
        statuses = {m["status"] for m in members}
        if len(statuses) > 1:
            varies["expected status"] = sorted(str(s) for s in statuses)
        for k in shape[2]:
            vals = {m["query"].get(k, "") for m in members}
            if len(vals) > 1:
                varies[f"query {k}"] = sorted(vals)
        if len({m["asserts"] for m in members}) > 1:
            varies["assertions"] = [f"{n} check(s)" for n in sorted({len(m["asserts"]) for m in members})]
        if len({m["extracts"] for m in members}) > 1:
            varies["extracts"] = [f"{n} value(s)" for n in sorted({len(m["extracts"]) for m in members})]
        if len({m["poll"] for m in members}) > 1:
            varies["poll"] = ["yes", "no"]
        if any(m["translated"] for m in members):
            varies["translated code in"] = [str(sum(1 for m in members if m["translated"]))]
        out.append({"shape": shape, "members": members, "varies": varies,
                    "lines": sum(m["lines"] for m in members)})
    out.sort(key=lambda g: (-len(g["members"]), g["shape"]))
    return out


def render_entries(entries: list[dict], egroups: list[dict], limit: int = 12) -> str:
    if not entries:
        return ""
    flows = len({e["shape"] for e in entries})
    in_groups = sum(len(g["members"]) for g in egroups)
    o = ["", "== entry classes (start(row) + bootstrap) ==",
         f"entry classes: {len(entries)} -> distinct setup shapes: {flows}; "
         f"{in_groups} classes in {len(egroups)} groups differ only in generated fields / picked values",
         "   (a setup shape = which SetupHelper flow runs + the same translated steps; the"
         " field lists are data a property-pack spec carries)"]
    for g in egroups[:limit]:
        names = sorted(m["entry"] for m in g["members"])
        o.append(f"\n[{len(g['members'])} classes, {g['lines']} bootstrap lines]  flow={g['shape'][0]}")
        for k, v in g["varies"].items():
            o.append(f"      varies in {k}: " + ", ".join(v[:8]) + (" ..." if len(v) > 8 else ""))
        o.append("      " + ", ".join(names[:8]) + (f", ... +{len(names) - 8}" if len(names) > 8 else ""))
    if len(egroups) > limit:
        o.append(f"\n... and {len(egroups) - limit} more entry groups")
    return "\n".join(o) + "\n"


def render(recs: list[dict], groups: list[dict], limit: int = 40,
           entries: list[dict] | None = None, root_for_templates: str | None = None) -> str:
    single = [r for r in recs if r.get("rest_steps") == 1]
    compound = [r for r in recs if r.get("rest_steps", 0) > 1]
    none = [r for r in recs if r.get("rest_steps", 0) == 0]
    in_groups = sum(len(g["members"]) for g in groups)
    lines_in_groups = sum(g["lines"] for g in groups)
    o = ["DEDUP REPORT -- generated methods that are the same call with different data", ""]
    o.append(f"methods parsed: {len(recs)}  (single REST call: {len(single)}, "
             f"several calls: {len(compound)}, no REST call: {len(none)})")
    o.append(f"single-call methods: {len(single)} -> distinct call shapes: "
             f"{len({r['shape'] for r in single})}")
    o.append(f"collapsible: {in_groups} methods in {len(groups)} groups "
             f"({lines_in_groups} body lines) -> {len(groups)} engine methods after Stage 1b")
    o.append("")
    o.append(f"== groups (largest first, top {limit}) ==")
    for g in groups[:limit]:
        verb, path, qk, hk, body = g["shape"]
        q = ("?" + ",".join(qk)) if qk else ""
        o.append(f"\n[{len(g['members'])} methods, {g['lines']} lines]  {verb} {path}{q}"
                 f"{'  +body' if body else ''}")
        for k, v in g["varies"].items():
            shown = ", ".join(v[:6]) + (" ..." if len(v) > 6 else "")
            o.append(f"      varies in {k}: {shown}")
        if not g["varies"]:
            o.append("      varies in: nothing detectable -- identical call and checks")
        names = sorted(m["method"] for m in g["members"])
        o.append("      " + ", ".join(names[:10]) + (f", ... +{len(names) - 10}" if len(names) > 10 else ""))
    if len(groups) > limit:
        o.append(f"\n... and {len(groups) - limit} more groups")
    o.append("")
    o.append(f"== compound methods (several REST calls; Stage 1b keeps these as chains) ==")
    o.append(f"  {len(compound)} methods, {sum(r['lines'] for r in compound)} lines")
    text = "\n".join(o) + "\n"
    if entries:
        text += render_entries(entries, group_entries(entries, 2))
    if root_for_templates:
        text += render_templates(template_stats(root_for_templates))
    return text


def write_audit(root: str, package_root: str, suite_name: str) -> str | None:
    """Called by the converter at the end of a run.

    Writes ``_audit/<suite>/dedup_report.txt`` and appends a `Reuse` section
    to that suite's ``summary.md`` so the collapse candidates are part of
    the audit a conversion already produces, not a separate command to
    remember. Returns the headline line, or None when nothing was parsed.
    """
    recs, entries = scan_tree_full(root, package_root)
    if not recs:
        return None
    groups = group_by_shape(recs, 2)
    egroups = group_entries(entries, 2)
    text = render(recs, groups, entries=entries, root_for_templates=root)
    audit_dir = os.path.join(root, "_audit", suite_name)
    os.makedirs(audit_dir, exist_ok=True)
    with open(os.path.join(audit_dir, "dedup_report.txt"), "w", encoding="utf-8") as fh:
        fh.write(text)
    single = [r for r in recs if r.get("rest_steps") == 1]
    in_groups = sum(len(g["members"]) for g in groups)
    headline = (f"{len(single)} single-call methods -> {len({r['shape'] for r in single})} "
                f"call shapes; {in_groups} methods in {len(groups)} groups are the same "
                f"call with different data")
    if entries:
        headline += (f"; {len(entries)} entry classes -> "
                     f"{len({e['shape'] for e in entries})} setup shapes")
    ts = template_stats(root)
    if ts:
        headline += (f"; {ts['files']} templates -> {ts['families']} shape+placeholder families "
                     f"({ts['mergeable_files']} still mergeable)")
    summary = os.path.join(audit_dir, "summary.md")
    section = ["", "## Reuse (same call, different data)", "",
               headline + ".",
               "Each group is one engine method after the parameterised-phase refactor;",
               "the members differ only in expected status, query sources, assertions",
               "or extracts -- data a phase spec carries. Full list: `dedup_report.txt`.", ""]
    for g in groups[:8]:
        verb, path, qk, _hk, body = g["shape"]
        q = ("?" + ",".join(qk)) if qk else ""
        section.append(f"- {len(g['members'])} methods, {g['lines']} lines: `{verb} {path}{q}`"
                       f"{' +body' if body else ''} -- varies in "
                       + (", ".join(g["varies"]) if g["varies"] else "nothing detectable"))
    for g in egroups[:4]:
        section.append(f"- {len(g['members'])} entry classes on setup flow `{g['shape'][0]}` -- varies in "
                       + (", ".join(g["varies"]) if g["varies"] else "nothing detectable"))
    try:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("\n".join(section) + "\n")
    except OSError:
        pass  # the txt report still exists
    return headline


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--root", default=".")
    p.add_argument("--package-root", default="com.ak.api")
    p.add_argument("--out", default=None)
    p.add_argument("--min", type=int, default=2, help="group size to report (default 2)")
    p.add_argument("--limit", type=int, default=40)
    a = p.parse_args(argv)
    recs, entries = scan_tree_full(a.root, a.package_root)
    if not recs:
        print(f"[dedup_report] no generated methods under {a.root} -- convert first")
        return 1
    groups = group_by_shape(recs, a.min)
    text = render(recs, groups, a.limit, entries=entries, root_for_templates=a.root)
    print(text, end="")
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"[dedup_report] written to {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
