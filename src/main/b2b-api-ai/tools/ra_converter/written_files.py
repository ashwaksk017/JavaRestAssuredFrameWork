"""The files a run wrote, by kind -- for the end of a convert and of the
workbench's Automate run, so the log names every suite, test class and
data sheet that was made:

    TestNG suites (2)
        Suites/Demo_Regression.xml
    Test classes (3)
        src/test/java/.../DemoTest.java
    Data sheets (5)
        src/test/resources/csv/demo/case_1.csv
    Supporting classes (4)          steps, hooks, clients
        ...
    Other files (40), by folder     templates, specs, diagrams: counted
        12  src/test/resources/templates/demo

Paths are relative to the project, `/`-separated. Only the kinds a person
looks for are listed in full; the rest are counted per folder.
"""
from __future__ import annotations

import posixpath
import re

_TEST_CLASS_RX = re.compile(r"(?:Test|Tests|IT)\.java$")
_SHEET_EXT = (".csv", ".xlsx", ".xls")
KINDS = (("suites", "TestNG suites"), ("tests", "Test classes"), ("sheets", "Data sheets"),
         ("classes", "Supporting classes"))


def _norm(p) -> str:
    s = str(p).replace("\\", "/")
    return s[2:] if s.startswith("./") else s


def kind_of(path: str) -> str:
    p = _norm(path)
    name = posixpath.basename(p)
    low = p.lower()
    if low.endswith(".xml") and (low.startswith("suites/") or "/suites/" in low or name.lower().startswith("testng")):
        return "suites"
    if low.endswith(".java"):
        return "tests" if "src/test/java/" in p and _TEST_CLASS_RX.search(name) else "classes"
    if low.endswith(_SHEET_EXT):
        return "sheets"
    return "other"


def group(paths) -> dict:
    """{kind: sorted paths}, each path once."""
    out = {k: [] for k, _ in KINDS}
    out["other"] = []
    for p in dict.fromkeys(_norm(p) for p in paths or [] if str(p).strip()):
        out[kind_of(p)].append(p)
    return {k: sorted(v) for k, v in out.items()}


def render(paths, changed=(), kept=(), indent: str = "    ") -> list:
    """The lines to print. `changed`: paths that existed before (marked
    "(changed)"); `kept`: paths left as they were (not listed, counted)."""
    kept_set = {_norm(p) for p in kept or []}
    changed_set = {_norm(p) for p in changed or []}
    groups = group(p for p in paths or [] if _norm(p) not in kept_set)
    lines = []
    for key, title in KINDS:
        items = groups[key]
        if not items:
            continue
        lines.append(f"{title} ({len(items)})")
        lines.extend(f"{indent}{p}{'  (changed)' if p in changed_set else ''}" for p in items)
    other = groups["other"]
    if other:
        lines.append(f"Other files ({len(other)}), by folder")
        folders = {}
        for p in other:
            d = posixpath.dirname(p) or "."
            folders[d] = folders.get(d, 0) + 1
        lines.extend(f"{indent}{n:>4}  {d}" for d, n in sorted(folders.items()))
    if kept_set:
        lines.append(f"Kept as they were ({len(kept_set)}): files that already existed and are not rewritten")
    return lines
