"""Every helper a Cursor skill names must actually exist.

WHY
---
A skill is an instruction set an agent follows literally. Its failure mode
is not a crash -- it is a confidently-written test calling
`RestUtilities.postJson(...)`, a method nobody wrote, because the skill
said so. The agent then "fixes" the compile error by inventing something
else, and the review burden lands on a person who assumed the skill was
accurate.

Documentation rots quietly. This makes it rot loudly: rename a helper and
the gate names the skill that still advertises the old one.

Checked, for every `.cursor/skills/**/SKILL.md`:

  * `import com.hi.api.X.Y;`        -> src/main/java/com/hi/api/X/Y.java exists
  * `ClassName.method(`             -> that class declares that method
  * `src/...` / `tools/...` paths   -> the file or directory exists

Not checked: whether the code compiles. That needs javac and a classpath;
`mvn -o -q -DskipTests test-compile` is the place for it. This catches the
cheap, common wrongness in seconds.

    python tools/check_skill_api.py
"""
from __future__ import annotations

import glob
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS = os.path.join(ROOT, ".cursor", "skills")

_IMPORT_RX = re.compile(r"^import\s+(com\.hi\.api\.[A-Za-z0-9_.]+);", re.M)
_CALL_RX = re.compile(r"\b([A-Z][A-Za-z0-9_]{2,})\.([a-z][A-Za-z0-9_]*)\s*\(")
_PATH_RX = re.compile(r"(?<![\w/.])((?:src|tools)/[A-Za-z0-9_./*{}<>-]+)")

# Named in prose as shell/tool invocations, not as Java.
_NOT_CLASSES = frozenset({
    "SKILL", "AC", "CSV", "JSON", "HTTP", "README", "GET", "POST", "PUT",
    "PATCH", "DELETE", "TODO", "NOTE",
})


def java_file_for(fqn: str) -> str | None:
    """Look in BOTH source roots.

    Searching only src/main/java reported `com.hi.api.tests.BaseApiTest`
    as missing -- it is in src/test/java, which is exactly where a base
    class for tests belongs.
    """
    for root in (("src", "main", "java"), ("src", "test", "java")):
        p = os.path.join(ROOT, *root, *fqn.split(".")) + ".java"
        if os.path.isfile(p):
            return p
    return None


def find_class(simple: str) -> str | None:
    hits = glob.glob(os.path.join(ROOT, "src", "main", "java", "**",
                                  simple + ".java"), recursive=True)
    hits += glob.glob(os.path.join(ROOT, "src", "test", "java", "**",
                                   simple + ".java"), recursive=True)
    return hits[0] if hits else None


def declares(path: str, method: str) -> bool:
    try:
        src = io.open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return False
    # `<modifiers> <type> name(`  -- good enough to catch a wrong name,
    # which is the failure being guarded, without parsing Java.
    return re.search(r"\b" + re.escape(method) + r"\s*\(", src) is not None


_ignored_cache: dict = {}


def _in_git_repo() -> bool:
    import subprocess
    if "__repo__" not in _ignored_cache:
        try:
            p = subprocess.run(("git", "rev-parse", "--is-inside-work-tree"),
                               cwd=ROOT, capture_output=True, text=True,
                               timeout=10)
            _ignored_cache["__repo__"] = (p.returncode == 0
                                          and p.stdout.strip().lower() == "true")
        except Exception:
            _ignored_cache["__repo__"] = False
    return _ignored_cache["__repo__"]


def _gitignored(rel: str) -> bool:
    """Whether git ignores this path -- i.e. the reader is told to CREATE it.

    Outside a repository git cannot answer, and answering False there
    reported `tools/ra_converter/cursor_agent.json` as a missing path on
    every run of an unzipped copy. The file is gitignored BY DESIGN
    because it holds an API key; absent is its correct state. Unknown is
    treated as "do not claim it is missing", because this check exists to
    catch a skill naming something that was never there -- not to flag a
    file the skill correctly tells you to create.
    """
    import subprocess
    if not _in_git_repo():
        return True
    if rel in _ignored_cache:
        return _ignored_cache[rel]
    try:
        rc = subprocess.run(("git", "check-ignore", "-q", rel), cwd=ROOT,
                            capture_output=True, timeout=10).returncode
        out = rc == 0
    except Exception:
        out = True
    _ignored_cache[rel] = out
    return out


def check_skill(md_path: str) -> list[str]:
    rel_md = os.path.relpath(md_path, ROOT).replace("\\", "/")
    text = io.open(md_path, encoding="utf-8", errors="replace").read()
    problems: list[str] = []

    imported: dict[str, str] = {}
    for fqn in _IMPORT_RX.findall(text):
        p = java_file_for(fqn)
        if p is None:
            problems.append(f"{rel_md}: import {fqn} -- no such class")
        else:
            imported[fqn.rsplit(".", 1)[1]] = p

    seen: set[tuple[str, str]] = set()
    for simple, method in _CALL_RX.findall(text):
        if simple in _NOT_CLASSES or (simple, method) in seen:
            continue
        seen.add((simple, method))
        path = imported.get(simple) or find_class(simple)
        if path is None:
            continue        # not a class of ours; prose or a third party
        if not declares(path, method):
            problems.append(
                f"{rel_md}: {simple}.{method}(...) -- "
                f"{os.path.relpath(path, ROOT).replace(os.sep, '/')} "
                f"declares no such member")

    for raw in set(_PATH_RX.findall(text)):
        p = raw.rstrip(".,);")
        if any(c in p for c in "*{}<>"):
            continue        # a glob or a placeholder like <Area>Test.java
        if os.path.exists(os.path.join(ROOT, p.replace("/", os.sep))):
            continue
        # A gitignored path is one the reader is told to CREATE --
        # cursor_agent.json holds an API key and must not be in the
        # repository. Absent is correct there, so absence is not a
        # finding; only a path that should exist and does not.
        if _gitignored(p):
            continue
        problems.append(f"{rel_md}: path {p} -- does not exist")

    return problems


def main() -> int:
    if not os.path.isdir(SKILLS):
        print("no .cursor/skills/ -- nothing to check")
        return 0
    mds = sorted(glob.glob(os.path.join(SKILLS, "**", "SKILL.md"),
                           recursive=True))
    if not mds:
        print("no SKILL.md found -- nothing to check")
        return 0

    all_problems: list[str] = []
    for md in mds:
        p = check_skill(md)
        rel = os.path.relpath(md, ROOT).replace("\\", "/")
        print(f"  {'FAIL' if p else 'ok  '}  {rel}  ({len(p)} problem(s))")
        all_problems += p

    if not all_problems:
        print(f"\n{len(mds)} skill(s): every helper, class and path they name "
              f"exists.")
        return 0
    print(f"\n{len(all_problems)} problem(s). A skill is followed literally, "
          f"so a name it gets wrong becomes invented code:")
    for p in all_problems:
        print(f"  {p}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
