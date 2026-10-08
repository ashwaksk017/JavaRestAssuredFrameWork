"""Has this story been worked on here before? Ask the repository.

    python tools/agent/history.py --job j1

`locate.py` decides create / update / upstream from the SHAPE of a
request: is there a test that makes this call. That misses the other way
a story is already done -- somebody automated it under a different
shape, or half of it, and said so in a commit message or on the test
itself. A `CREATE` verdict next to three commits that name the story is
a verdict to read twice.

For each story key the job names, this finds:

* commits on any local or remote branch whose message mentions the key,
  with the files each one touched;
* test files in the working tree that mention the key now (an
  `@XrayTest("KEY")`, a CSV row, a comment) -- tracked, or new and not
  yet committed. Files git ignores, which is all converter output, are
  not searched.

It is EVIDENCE, shown beside the plan. It changes no decision: a commit
that mentions a key may have touched something unrelated, and a story
can legitimately need a second test. A person reads it.

WHERE THE KEYS COME FROM
------------------------
From the links of the last intake that are Jira stories, and from keys
written out in the pasted text. A pasted curl command is full of things
shaped like a key -- `UTF-8`, `SHA-256`, `ISO-8601` -- so a key from the
text counts only when its project is one a story LINK names, or, with no
link, when it is not one of the well-known names of that shape. Each key
is shown with where it came from.

`ABC-1` does not match `ABC-12` or `XABC-1`, in a commit or in a file:
the same boundary rule for both. Upper and lower case are the same key.

Read-only: `git log` and `git grep`, nothing that changes a ref, the
index or the working tree. No diff is read -- subjects and file names
only. The whole search has a time budget; when it runs out the section
says it is incomplete rather than "nothing found".

Every piece of text that comes out of the repository -- a subject, a
file name -- is put on one line, with backticks and runs of `=` removed,
before it goes into the plan. The plan is read by an agent; a branch
somebody pushed must not be able to write a section of it.

The idea is from a sibling tool that fed such commits to a model as
context. It used a git library; this uses the git that is already here.
"""
from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)

MAX_KEYS = 10
MAX_COMMITS = 8
MAX_FILES = 12
GIT_TIMEOUT = 30              # one git command
BUDGET = 60                   # the whole search
# Where a test, or a row of one, would be.
SEARCH_PATHS = ("src/test/java", "src/test/resources")

_KEY = r"[A-Z][A-Z0-9_]+-[0-9]+"
_BEFORE, _AFTER = r"(?<![A-Za-z0-9_-])", r"(?![A-Za-z0-9_])"
_KEY_RX = re.compile(_BEFORE + "(" + _KEY + ")" + _AFTER)
_SAFE_KEY_RX = re.compile(r"^" + _KEY + r"$")
_BROWSE_RX = re.compile(r"(?i)/browse/(" + _KEY + ")")
_HASH_RX = re.compile(r"^[0-9a-f]{4,40}$")
_DATE_RX = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
# Shaped like a story key, and never one.
NOT_PROJECTS = frozenset("""
    UTF UTF8 ISO RFC SHA SHA2 SHA3 MD HTTP HTTPS TLS SSL AES RSA DES HMAC PKCS
    COVID CVE CWE UTC GMT EST CST PST IEEE ANSI ECMA ASCII CP WINDOWS WIN LATIN
    BASE HS RS ES PS EC ED X OAUTH IPV IP TCP UDP US EU EN DE FR UK ID V VER
    VERSION JDK JAVA PYTHON NODE ES6 HTML CSS XML JSON YAML API REST SOAP GRPC
    S A B C D E F G H T P Q R N M K L J I O U W Y Z""".split())


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


jira_query = _load("history_jira_query", os.path.join(TOOLS, "jira", "query.py"))


def _read(path: str, cap: int = 2_000_000) -> str:
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read(cap)
    except OSError:
        return ""


def _flat(text, cap: int = 200) -> str:
    """Somebody's text, made safe to sit in a document an agent reads."""
    s = re.sub(r"[\x00-\x1f\x7f]", " ", str(text))
    s = re.sub(r"={3,}", "=", re.sub(r"\s+", " ", s)).replace("`", "'").strip()
    return s[:cap] + (" ..." if len(s) > cap else "")


def story_keys(job_dir: str) -> dict:
    """{keys: [{key, source}], dropped: n} -- the stories this job names.

    `source` is "link" or "text". See the module docstring for which
    keys from the text count.
    """
    try:
        intake = json.loads(_read(os.path.join(job_dir, "intake.json")) or "{}")
    except ValueError:
        intake = {}
    linked = []
    for link in intake.get("links") or []:
        if isinstance(link, dict) and link.get("kind") == "jira":
            text = str(link.get("link") or "")
            try:
                what = jira_query.parse(text)
            except Exception:                            # noqa: BLE001
                what = {"kind": jira_query.NONE}
            if what["kind"] == jira_query.KEYS:
                linked += what["keys"]
            else:                # an address inside a sentence: its /browse/KEY
                linked += [k.upper() for k in _BROWSE_RX.findall(text)]
    projects = {k.split("-")[0] for k in linked}
    written = []
    for k in _KEY_RX.findall(_read(os.path.join(job_dir, "pasted.txt"))):
        project = k.split("-")[0]
        if (project in projects) if projects else (project not in NOT_PROJECTS):
            written.append(k)
    out, seen = [], set()
    for key, source in [(k, "link") for k in linked] + [(k, "text") for k in written]:
        if _SAFE_KEY_RX.match(key) and key not in seen:
            seen.add(key)
            out.append({"key": key, "source": source})
    return {"keys": out[:MAX_KEYS], "dropped": max(0, len(out) - MAX_KEYS)}


class _Clock:
    def __init__(self, seconds: float):
        self.end = time.monotonic() + seconds

    def left(self) -> float:
        return self.end - time.monotonic()


def _git(root: str, args: list, clock: _Clock, ok_codes=(0,)) -> tuple:
    """(ok, stdout). Never raises: no git, not a repository, a timeout and
    an exhausted budget are all "could not be read"."""
    wait = min(GIT_TIMEOUT, clock.left())
    if wait <= 0:
        return False, ""
    try:
        done = subprocess.run(
            ["git", "-c", "core.quotepath=off", "-c", "grep.fullName=false", *args],
            cwd=root, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=wait, encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return False, ""
    return done.returncode in ok_codes, done.stdout or ""


def commits_for(root: str, key: str, clock: _Clock = None, limit: int = MAX_COMMITS):
    """(commits newest first, how many more there are), or None when the
    history could not be read."""
    if not _SAFE_KEY_RX.match(key):
        return [], 0
    clock = clock or _Clock(GIT_TIMEOUT)
    # The key is [A-Z0-9_-] only (checked above), so it is safe inside a
    # pattern. The boundaries keep ABC-1 from matching ABC-12 or XABC-1.
    pattern = f"(^|[^A-Za-z0-9_-]){key}([^A-Za-z0-9_]|$)"
    # Branches and remote branches: not the stash (its "WIP on ..." entries
    # repeat the subject of the commit under them) and not merges (they
    # list no files and say what their parents already said). NUL starts a
    # record: a commit message cannot contain one, so a subject cannot
    # forge a second commit. --relative: paths as seen from the project.
    ok, out = _git(root, ["log", "--branches", "--remotes", "--no-merges", "-E", "-i",
                          f"--grep={pattern}", f"--max-count={limit + 1}",
                          "--date=short", "--name-only", "--relative",
                          "--format=%x00%h%x1f%ad%x1f%s"], clock)
    if not ok:
        return None
    commits = []
    for block in out.split("\x00")[1:]:
        head, _, rest = block.partition("\n")
        parts = head.split("\x1f", 2)
        if len(parts) != 3 or not _HASH_RX.match(parts[0]) or not _DATE_RX.match(parts[1]):
            continue
        files = [f.strip() for f in rest.splitlines() if f.strip()]
        commits.append({"commit": parts[0], "date": parts[1], "subject": _flat(parts[2], 160),
                        "files": [_flat(f) for f in files[:MAX_FILES]],
                        "more_files": max(0, len(files) - MAX_FILES)})
    return commits[:limit], max(0, len(commits) - limit)


def files_naming(root: str, key: str, clock: _Clock = None, limit: int = MAX_FILES):
    """(test files that mention `key` now, how many more), or None when
    git could not say. Tracked files and new ones; not ignored ones."""
    if not _SAFE_KEY_RX.match(key):
        return [], 0
    clock = clock or _Clock(GIT_TIMEOUT)
    paths = [p for p in SEARCH_PATHS if os.path.isdir(os.path.join(root, p))]
    if not paths:
        return [], 0
    ok, out = _git(root, ["grep", "-l", "-I", "-i", "-F", "--untracked", "-e", key,
                          "--", *paths], clock, ok_codes=(0, 1))    # 1: no match
    if not ok:
        return None
    # -F finds the key inside ABC-120 as well; the boundary rule decides.
    exact = re.compile(_BEFORE + re.escape(key) + _AFTER, re.I)
    found = []
    for rel in out.splitlines():
        rel = rel.strip()
        if rel and exact.search(_read(os.path.join(root, rel))):
            found.append(_flat(rel))
    return found[:limit], max(0, len(found) - limit)


def build(job_dir: str, root: str, budget: float = BUDGET) -> dict:
    """{keys, stories: [{key, source, commits, more_commits, files,
    more_files}], dropped_keys, unreadable, found}."""
    named = story_keys(job_dir)
    clock = _Clock(budget)
    stories, unreadable = [], False
    for item in named["keys"]:
        commits = commits_for(root, item["key"], clock)
        files = files_naming(root, item["key"], clock)
        if commits is None or files is None:
            unreadable = True
        commits, more_c = commits or ([], 0)
        files, more_f = files or ([], 0)
        stories.append(dict(item, commits=commits, more_commits=more_c,
                            files=files, more_files=more_f))
    return {"keys": [i["key"] for i in named["keys"]], "stories": stories,
            "dropped_keys": named["dropped"], "unreadable": unreadable,
            "found": any(s["commits"] or s["files"] for s in stories)}


def render(history: dict) -> list:
    """Markdown lines for the plan. [] when the job names no story."""
    if history.get("error"):
        return ["## Earlier work that names this story", "",
                f"This could not be looked up ({_flat(history['error'], 60)}). It "
                f"is not evidence that nothing exists.", ""]
    if not history.get("keys"):
        return []
    lines = ["## Earlier work that names this story", ""]
    if history.get("unreadable"):
        lines += ["The git history could not be read in full (no git, not a "
                  "repository, or it took too long), so this section is "
                  "incomplete. It is not evidence that nothing exists.", ""]
    if history.get("dropped_keys"):
        lines += [f"{history['dropped_keys']} more story key(s) were named and "
                  f"not looked up (the first {MAX_KEYS} are).", ""]
    if not history.get("found"):
        lines += [f"No commit message on any branch, and no test file that git "
                  f"does not ignore, mentions {', '.join(history['keys'])}.", ""]
        return lines
    lines += ["Evidence, not a decision: read it before accepting a `CREATE` "
              "above. A commit that names a story may have automated it "
              "under a request this plan did not match. Generated tests are "
              "ignored by git and are not searched here; the decisions above "
              "cover those.", ""]
    for s in history["stories"]:
        origin = " (named in the pasted text)" if s.get("source") == "text" else ""
        if not (s["commits"] or s["files"]):
            lines += [f"- **{s['key']}**{origin}: nothing found", ""]
            continue
        lines.append(f"- **{s['key']}**{origin}")
        for rel in s["files"]:
            lines.append(f"    - mentioned now in '{rel}'")
        if s.get("more_files"):
            lines.append(f"    - ... and {s['more_files']} more file(s) mention it")
        for c in s["commits"]:
            lines.append(f"    - {c['commit']} {c['date']} -- {c['subject']}")
            for f in c["files"]:
                lines.append(f"        - {f}")
            if c["more_files"]:
                lines.append(f"        - ... and {c['more_files']} more file(s)")
        if s.get("more_commits"):
            lines.append(f"    - ... and at least {s['more_commits']} earlier commit(s)")
        lines.append("")
    return lines


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--job", required=True)
    a = ap.parse_args(argv)
    intake = _load("history_intake", os.path.join(HERE, "intake.py"))
    try:
        out = intake.job_dir(a.job)
    except ValueError as e:
        print(f"refused: {e}")
        return 2
    history = build(out, intake.ROOT)
    print("\n".join(render(history)) or "this job names no story key")
    return 0


if __name__ == "__main__":
    sys.exit(main())
