"""Pre-emit gate: reuse a fluent name when the method body already exists.

``ra_converter`` calls :func:`apply_before_emit` after ReadyAPI steps are
rendered and **before** Support / ScenarioSteps Java methods are written.
Identical bodies keep the unsuffixed ReadyAPI mapping (``createTravelAgency``)
instead of allocating ``createTravelAgency2``.

CLI (optional, against already-emitted Support):

    python tools/ra_converter/compare_reuse.py --output .
"""
from __future__ import annotations

import argparse
import os
import re
from collections import defaultdict
from pathlib import Path

from fluent_scenario import load_fluent_catalog, phase_body_key
from method_body_reuse import FluentMethodReuse

SKIP_NAMES = {
    "start", "existing", "fromBound", "complete", "bootstrap", "self",
    "current",
}

METHOD_START = re.compile(
    r"(?P<mods>(?:public|protected|private|static|final|synchronized)\s+)+"
    r"(?P<ret>[\w.<>,\[\] ?]+)\s+(?P<name>[A-Za-z_]\w*)\s*\(",
    re.M,
)


def _skip_string_and_comments(text: str, i: int) -> int:
    n = len(text)
    if i < n - 1 and text[i:i + 2] == "//":
        nl = text.find("\n", i)
        return n if nl < 0 else nl + 1
    if i < n - 1 and text[i:i + 2] == "/*":
        end = text.find("*/", i + 2)
        return n if end < 0 else end + 2
    ch = text[i]
    if ch in ('"', "'"):
        i += 1
        while i < n:
            if text[i] == "\\":
                i += 2
                continue
            if text[i] == ch:
                return i + 1
            i += 1
        return n
    return i + 1


def _matching(text: str, open_idx: int, opener: str, closer: str) -> int:
    depth = 0
    i = open_idx
    n = len(text)
    while i < n:
        c = text[i]
        if c in "\"'" or (c == "/" and i + 1 < n and text[i + 1] in "/*"):
            i = _skip_string_and_comments(text, i)
            continue
        if c == opener:
            depth += 1
        elif c == closer:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def extract_methods(path: Path) -> list[tuple[str, list[str]]]:
    """Return (method_name, body_lines) from a Support / ScenarioSteps file."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    out: list[tuple[str, list[str]]] = []
    for m in METHOD_START.finditer(text):
        name = m.group("name")
        if name in SKIP_NAMES or name[0].isupper():
            continue
        paren_open = m.end() - 1
        paren_close = _matching(text, paren_open, "(", ")")
        if paren_close < 0:
            continue
        after = text[paren_close + 1:].lstrip()
        if not after.startswith("{") and not after.startswith("throws"):
            continue
        brace = after.find("{")
        if brace < 0:
            continue
        body_open = paren_close + 1 + (len(text[paren_close + 1:]) - len(after)) + brace
        body_close = _matching(text, body_open, "{", "}")
        if body_close < 0:
            continue
        inner = text[body_open + 1:body_close]
        lines = [ln.rstrip() for ln in inner.splitlines()]
        if not any(ln.strip() for ln in lines):
            continue
        out.append((name, lines))
    return out


def support_java_dirs(output_dir: str, package_root: str) -> list[Path]:
    base = Path(output_dir) / "src/main/java" / package_root.replace(".", "/") / "support"
    if not base.is_dir():
        return []
    found = [base / "scenario"]
    for child in sorted(base.iterdir()):
        if child.is_dir() and child.name != "scenario":
            found.append(child / "scenario")
    return [d for d in found if d.is_dir()]


def seed_from_support_tree(output_dir: str, package_root: str,
                           reuse: FluentMethodReuse) -> int:
    """Register already-emitted Support methods so a reconvert can reuse them."""
    n = 0
    for folder in support_java_dirs(output_dir, package_root):
        for path in sorted(folder.glob("*Support.java")):
            for name, body in extract_methods(path):
                reuse.flow.seed(name, body)
                n += 1
        steps = folder / "ScenarioSteps.java"
        if steps.is_file():
            for name, body in extract_methods(steps):
                reuse.flow.seed(name, body)
                n += 1
    return n


def _remap_phase_votes(votes: dict, reuse: FluentMethodReuse) -> dict:
    remapped: dict = defaultdict(lambda: defaultdict(list))
    for fname, variants in (votes or {}).items():
        for key, infos in variants.items():
            body = (infos[0].get("body") if infos else None) or []
            canon = reuse.flow.reuse_or_allocate(fname, body)
            remapped[canon][key].extend(infos)
    return remapped


def _remap_verify_votes(votes: dict, reuse: FluentMethodReuse) -> dict:
    remapped: dict = defaultdict(lambda: defaultdict(list))
    for vkey, variants in (votes or {}).items():
        if isinstance(vkey, tuple) and len(vkey) == 2:
            vcls, vmeth = vkey
        else:
            parts = str(vkey).split(".", 1)
            vcls, vmeth = (parts + ["verifyStep"])[:2]
        for key, infos in variants.items():
            body = (infos[0].get("body") if infos else None) or []
            allocated = reuse.verify.reuse_or_allocate(f"{vcls}__{vmeth}", body)
            canon_meth = allocated.split("__", 1)[-1]
            remapped[(vcls, canon_meth)][key].extend(infos)
    return remapped


def duplicate_name_groups(votes: dict) -> list[tuple[str, list[str], int, str, list[str]]]:
    """Bodies in this convert that currently have more than one fluent name.

    Each row is (fingerprint, names, occurrences, preview, body_lines).
    """
    by_fp: dict[str, dict] = {}
    for fname, variants in (votes or {}).items():
        for key, infos in variants.items():
            body = list((infos[0].get("body") if infos else None) or [])
            fp = phase_body_key(body)
            rec = by_fp.setdefault(
                fp, {"names": set(), "n": 0, "preview": "", "body": body})
            rec["names"].add(fname)
            rec["n"] += len(infos)
            if not rec["preview"] and body:
                rec["preview"] = next((ln.strip() for ln in body if ln.strip()), "")
    groups = []
    for fp, rec in by_fp.items():
        names = sorted(rec["names"])
        if len(names) > 1:
            groups.append((fp, names, rec["n"], rec["preview"][:90], rec["body"]))
    groups.sort(key=lambda g: (-g[2], g[1][0]))
    return groups


def apply_before_emit(emitter) -> FluentMethodReuse:
    """Freeze body→name mapping, then remap votes. Call before writing Java."""
    if getattr(emitter, "_compare_reuse_applied", False):
        return getattr(emitter, "_fluent_method_reuse", None) or emitter._method_reuse()

    frozen = FluentMethodReuse()
    frozen.seed_catalog(load_fluent_catalog())
    output_dir = getattr(emitter, "output_dir", ".") or "."
    package_root = getattr(emitter, "package_root", "com.hi.api") or "com.hi.api"
    seeded = seed_from_support_tree(output_dir, package_root, frozen)

    before = duplicate_name_groups(getattr(emitter, "_fluent_phase_votes", {}) or {})
    emitter._fluent_phase_votes = _remap_phase_votes(
        getattr(emitter, "_fluent_phase_votes", {}) or {}, frozen)
    emitter._fluent_verify_votes = _remap_verify_votes(
        getattr(emitter, "_fluent_verify_votes", {}) or {}, frozen)
    emitter._fluent_method_reuse = frozen
    emitter._compare_reuse_applied = True

    after = duplicate_name_groups(emitter._fluent_phase_votes)
    print(f"[ra_converter] compare_reuse: seeded {seeded} existing Support "
          f"method(s); lookup hits={frozen.flow.reused} "
          f"allocated={frozen.flow.allocated}")
    if before:
        print(f"[ra_converter] compare_reuse: {len(before)} same-body/"
              f"different-name group(s) before freeze, {len(after)} after")
        for _fp, names, n, preview, body in before[:15]:
            canon = frozen.flow.lookup(body) or "?"
            print(f"  - {' / '.join(names)}  occ={n}  ->  {canon}")
        if after:
            print("[ra_converter] compare_reuse: remaining different names "
                  "share a fingerprint collision (unexpected):")
            for _fp, names, n, preview, _body in after[:10]:
                print(f"  - {' / '.join(names)}  occ={n}  {preview}")
    else:
        print("[ra_converter] compare_reuse: no same-body/different-name "
              "groups in this convert's rendered methods")
    return frozen


def _cli_scan(output_dir: str, package_root: str) -> int:
    reuse = FluentMethodReuse()
    reuse.seed_catalog(load_fluent_catalog())
    seeded = seed_from_support_tree(output_dir, package_root, reuse)
    by_fp: dict[str, dict] = defaultdict(lambda: {"old": set(), "new": set(), "n": 0})
    n_methods = 0
    for folder in support_java_dirs(output_dir, package_root):
        for path in sorted(folder.glob("*Support.java")):
            for name, body in extract_methods(path):
                n_methods += 1
                fp = phase_body_key(body)
                new = reuse.flow.reuse_or_allocate(name, body)
                by_fp[fp]["old"].add(name)
                by_fp[fp]["new"].add(new)
                by_fp[fp]["n"] += 1
    current = [(sorted(v["old"]), v["n"]) for v in by_fp.values() if len(v["old"]) > 1]
    after = [(sorted(v["new"]), v["n"]) for v in by_fp.values() if len(v["new"]) > 1]
    current.sort(key=lambda t: -t[1])
    print(f"Support methods={n_methods} seeded={seeded}")
    print(f"same-body different-name groups on disk: {len(current)}")
    print(f"after compare_reuse: {len(after)}")
    for names, n in current:
        fp_match = next(v for v in by_fp.values()
                        if sorted(v["old"]) == names)
        print(f"  {' / '.join(names):56} occ={n:4}  ->  "
              f"{' / '.join(sorted(fp_match['new']))}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--output", default=".", help="Framework root (contains src/)")
    p.add_argument("--package-root", default="com.hi.api")
    args = p.parse_args(argv)
    return _cli_scan(args.output, args.package_root)


if __name__ == "__main__":
    raise SystemExit(main())
