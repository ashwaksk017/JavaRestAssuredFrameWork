"""Which suites an automated case belongs to.

A suite here is a TestNG group. Each case of a design gets its groups from
the rules in suite-map.json (by the case's type and priority), or from a
person, who can set them on the review table. Cursor is told each case's
groups and puts them on the test method:

    @Test(groups = {"jira", "regression", "smoke"})

and each group has a suite file that runs every test of the jira package
in that group -- no list of classes to keep up to date:

    mvn -o test -DsuiteXmlFile=Suites/jira/group-smoke.xml

The suite files are written by this tool (not by Cursor) when a job first
uses a group, and are part of that job's change.

    python tools/agent/suitemap.py --job <job>     # the groups of each case
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MAP_FILE = os.path.join(HERE, "suite-map.json")
SUITES_DIR = "Suites/jira/"
PACKAGE = "com.hi.api.tests.jira"
GROUP_RX = re.compile(r"^[a-z][a-z0-9_-]{0,30}$")
MAX_GROUPS = 8
DEFAULT = {"always": ["jira", "regression"], "rules": []}


def _words(value) -> list:
    if isinstance(value, str):
        value = [value]
    return [str(v).strip().lower() for v in value or [] if str(v).strip()]


def load(path: str = MAP_FILE) -> dict:
    """The mapping, checked. A file that cannot be read or holds a bad
    group name is not half-used: the default (jira, regression) is, and
    `problem` says why."""
    try:
        with io.open(path, encoding="utf-8-sig") as fh:
            raw = json.load(fh)
    except (OSError, ValueError) as e:
        return dict(DEFAULT, problem=f"{os.path.basename(path)} could not be read ({type(e).__name__})")
    always = _words(raw.get("always") if isinstance(raw, dict) else None)
    rules = []
    for r in (raw.get("rules") if isinstance(raw, dict) else None) or []:
        if not isinstance(r, dict):
            continue
        rules.append({"group": str(r.get("group") or "").strip().lower(),
                      "type": _words(r.get("type")), "priority": _words(r.get("priority"))})
    bad = [g for g in always + [r["group"] for r in rules] if not GROUP_RX.match(g)]
    if bad:
        return dict(DEFAULT, problem=f"not a group name: {bad[0]!r} (lower-case letters, digits, - and _)")
    if "jira" not in always:
        always = ["jira"] + always          # testng-jira.xml runs the group jira
    return {"always": always, "rules": rules}


def clean_groups(groups) -> list:
    """A person's groups for a case: checked names, jira first, no repeats."""
    if isinstance(groups, str):
        groups = groups.split(",")
    if not isinstance(groups, list):
        raise ValueError("groups must be a list of names")
    out = []
    for g in groups:
        g = str(g).strip().lower()
        if not g:
            continue
        if not GROUP_RX.match(g):
            raise ValueError(f"{g!r} is not a group name (lower-case letters, digits, - and _)")
        if g not in out:
            out.append(g)
    if "jira" in out:
        out.remove("jira")
    out = ["jira"] + out
    if len(out) > MAX_GROUPS:
        raise ValueError(f"at most {MAX_GROUPS} groups for a case")
    return out


def groups_for(case: dict, mapping: dict = None, chosen=None) -> list:
    """The groups of one case: a person's choice when there is one, the
    rules' otherwise."""
    if chosen:
        try:
            return clean_groups(chosen)
        except ValueError:
            pass
    mapping = mapping or load()
    kind = str(case.get("type") or "").strip().lower()
    prio = str(case.get("priority") or "").strip().lower()
    out = list(mapping["always"])
    for r in mapping["rules"]:
        if (not r["type"] or kind in r["type"]) and (not r["priority"] or prio in r["priority"]):
            if r["group"] not in out:
                out.append(r["group"])
    return clean_groups(out)


def for_job(job_dir: str, mapping: dict = None) -> dict:
    """{case id: {"groups": [...], "chosen": bool}} for the job's design."""
    try:
        with io.open(os.path.join(job_dir, "design.json"), encoding="utf-8") as fh:
            cases = json.load(fh).get("test_cases") or []
    except (OSError, ValueError, AttributeError):
        return {}
    try:
        with io.open(os.path.join(job_dir, "case-review.json"), encoding="utf-8") as fh:
            entries = (json.load(fh).get("design") or {})
    except (OSError, ValueError, AttributeError):
        entries = {}
    mapping = mapping or load()
    out = {}
    for c in cases:
        if not isinstance(c, dict) or not c.get("id"):
            continue
        chosen = (entries.get(str(c["id"])) or {}).get("groups") if isinstance(entries, dict) else None
        out[str(c["id"])] = {"groups": groups_for(c, mapping, chosen), "chosen": bool(chosen)}
    return out


def suite_rel(group: str) -> str:
    return f"{SUITES_DIR}group-{group}.xml"


def suite_xml(group: str) -> str:
    rel = suite_rel(group)
    return "\n".join([
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<!DOCTYPE suite SYSTEM "https://testng.org/testng-1.0.dtd">',
        "",
        f"<!-- Every story test (package {PACKAGE}) in the group {group}.",
        "     Written by the test workbench (tools/agent/suitemap.py) the first",
        "     time a job's cases were mapped to this group; run with",
        f"       mvn -o test -DsuiteXmlFile={rel}",
        "     Which cases are in it: tools/agent/suite-map.json, or the case's",
        "     groups set by hand on the review table. -->",
        f'<suite name="jira-{group}" verbose="1">',
        "    <listeners>",
        '        <listener class-name="io.qameta.allure.testng.AllureTestNg"/>',
        "    </listeners>",
        f'    <test name="jira-{group}">',
        "        <groups>",
        "            <run>",
        f'                <include name="{group}"/>',
        "            </run>",
        "        </groups>",
        "        <packages>",
        f'            <package name="{PACKAGE}"/>',
        "        </packages>",
        "    </test>",
        "</suite>", ""])


def write_suites(root: str, groups) -> list:
    """Write the suite file of each group that has none yet; returns the
    paths written. `jira` has testng-jira.xml already."""
    written = []
    for g in sorted(set(groups) - {"jira"}):
        if not GROUP_RX.match(g):
            continue
        rel = suite_rel(g)
        path = os.path.join(root, rel.replace("/", os.sep))
        if os.path.isfile(path):
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(suite_xml(g))
        written.append(rel)
    return written


_GROUPS_IN_CODE_RX = re.compile(r"groups\s*=\s*\{([^}]*)\}")


def groups_in_code(texts) -> set:
    """Every group named in a `groups = {...}` of the given Java sources."""
    out = set()
    for text in texts:
        for m in _GROUPS_IN_CODE_RX.finditer(text or ""):
            out |= {g.strip().strip('"') for g in m.group(1).split(",") if g.strip().strip('"')}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--job", required=True)
    args = ap.parse_args(argv)
    sys.path.insert(0, HERE)
    import intake                                       # noqa: E402
    mapping = load()
    if mapping.get("problem"):
        print(f"WARN {mapping['problem']}; every case gets: {', '.join(mapping['always'])}")
    for case_id, g in for_job(intake.job_dir(intake.safe_job(args.job)), mapping).items():
        print(f"{case_id}: {', '.join(g['groups'])}{' (set by hand)' if g['chosen'] else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
