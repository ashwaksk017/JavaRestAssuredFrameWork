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


def scan_tree(root: str, package_root: str) -> list[dict]:
    base = os.path.join(root, "src", "main", "java", *package_root.split("."), "support")
    recs: list[dict] = []
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
    return recs


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


def render(recs: list[dict], groups: list[dict], limit: int = 40) -> str:
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
    return "\n".join(o) + "\n"


def write_audit(root: str, package_root: str, suite_name: str) -> str | None:
    """Called by the converter at the end of a run.

    Writes ``_audit/<suite>/dedup_report.txt`` and appends a `Reuse` section
    to that suite's ``summary.md`` so the collapse candidates are part of
    the audit a conversion already produces, not a separate command to
    remember. Returns the headline line, or None when nothing was parsed.
    """
    recs = scan_tree(root, package_root)
    if not recs:
        return None
    groups = group_by_shape(recs, 2)
    text = render(recs, groups)
    audit_dir = os.path.join(root, "_audit", suite_name)
    os.makedirs(audit_dir, exist_ok=True)
    with open(os.path.join(audit_dir, "dedup_report.txt"), "w", encoding="utf-8") as fh:
        fh.write(text)
    single = [r for r in recs if r.get("rest_steps") == 1]
    in_groups = sum(len(g["members"]) for g in groups)
    headline = (f"{len(single)} single-call methods -> {len({r['shape'] for r in single})} "
                f"call shapes; {in_groups} methods in {len(groups)} groups are the same "
                f"call with different data")
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
    recs = scan_tree(a.root, a.package_root)
    if not recs:
        print(f"[dedup_report] no generated methods under {a.root} -- convert first")
        return 1
    groups = group_by_shape(recs, a.min)
    text = render(recs, groups, a.limit)
    print(text, end="")
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"[dedup_report] written to {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
