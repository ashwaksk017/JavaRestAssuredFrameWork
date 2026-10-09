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
written out in the pasted text, in capitals as Jira writes them. A
pasted request is full of things shaped like a key -- `UTF-8`,
`SHA-256`, `ISO-8601`, `SKU-100` -- so a key from the text counts only
when its project is one a story LINK names. With no link there is less
to go on: lines that are part of a request (a curl command, a header, a
URL, JSON) are not read for keys at all, well-known names of that shape
are left out, and what remains is taken most-mentioned first. Each key
is shown with where it came from, so a wrong one is seen for what it is.

`ABC-1` does not match `ABC-12` or `XABC-1`, in a commit or in a file:
the same boundary rule for both. In commits and files, upper and lower
case are the same key.

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
    ERR ERROR CODE ROOM PO SKU FY Q H S EC OAUTH P ES RS HS PS
    MON TUE WED THU FRI SAT SUN JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC
    S A B C D E F G H T P Q R N M K L J I O U W Y Z""".split())
# A line that is part of a request rather than of a sentence about one.
_REQUEST_LINE_RX = re.compile(
    r"""(?ix) \bcurl\b | ://
        | ^\s*-{1,2}[A-Za-z]            # -H, --data
        | ^\s*[{\[]\s*(?:"|$) | ^\s*[}\]]      # JSON
        | "\s*:                         # "field": value
        | ^\s*[A-Za-z][A-Za-z0-9-]*:\s*\S+/\S   # Header: type/subtype
        | ^\s*(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+/ """)


_CUE_RX = re.compile(r"(?i)\b(?:story|stories|ticket|jira|issue|epic|bug|defect)\b")


# A key a request line may still give: at its start (`BOOK-41: curl below`,
# `[BOOK-41] title`), or in a story's own address.
_LEADING_KEY_RX = re.compile(r"^\s*\[?(" + _KEY + r")\]?(?![A-Za-z0-9_])")


def _not_a_project(project: str) -> bool:
    """`SHA256`, `TLS1`, `OAUTH2` are `SHA`, `TLS`, `OAUTH` with a version.
    Only for a stem of two letters or more: `P2`, `X1`, `S3` are real
    project keys somewhere, and one letter says too little to rule on."""
    stem = project.rstrip("0123456789")
    return project in NOT_PROJECTS or (len(stem) >= 2 and stem in NOT_PROJECTS) \
        or not project.rstrip("0123456789_")


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
    counts = {}
    for line in _read(os.path.join(job_dir, "pasted.txt")).splitlines():
        # ...unless the line itself says it is talking about a story. Even
        # then a request line gives up the key it starts with and the key
        # of a story address in it; only the rest of the line is not read.
        found = _KEY_RX.findall(line)
        if not projects and _REQUEST_LINE_RX.search(line) and not _CUE_RX.search(line):
            found = [m.group(1) for m in [_LEADING_KEY_RX.match(line)] if m] \
                + [k.upper() for k in _BROWSE_RX.findall(line)]
        for k in found:
            project = k.split("-")[0]
            if (project in projects) if projects else not _not_a_project(project):
                counts[k] = counts.get(k, 0) + 1
    # Most-mentioned first (dicts keep first-seen order for ties): the
    # story is named more often than a stray part number, and the limit
    # below must not be spent on the strays.
    written = sorted(counts, key=lambda k: -counts[k])
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


def _run(argv: list, cwd: str, wait: float) -> tuple:
    """(return code or None on timeout, stdout). The whole process TREE is
    ended on timeout: on Windows `git` is often a launcher that starts the
    real git, and killing the launcher leaves the child holding the pipe,
    so a plain subprocess timeout returns only when the child does."""
    windows = os.name == "nt"
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if windows else 0
    proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            encoding="utf-8", errors="replace", creationflags=flags,
                            start_new_session=not windows)
    try:
        out, _ = proc.communicate(timeout=wait)
        return proc.returncode, out or ""
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            try:
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               stdin=subprocess.DEVNULL, timeout=10)
            except (OSError, subprocess.SubprocessError):
                pass
        else:
            try:
                import signal
                os.killpg(proc.pid, signal.SIGKILL)       # its own session: the group
            except (OSError, AttributeError):
                pass
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.communicate(timeout=3)
        except (subprocess.SubprocessError, OSError, ValueError):
            pass
        return None, ""


def _git(root: str, args: list, clock: _Clock, ok_codes=(0,)) -> tuple:
    """(ok, stdout). Never raises: no git, not a repository, a timeout and
    an exhausted budget are all "could not be read"."""
    wait = min(GIT_TIMEOUT, clock.left())
    if wait <= 0:
        return False, ""
    try:
        code, out = _run(["git", "-c", "core.quotepath=off", "-c", "grep.fullName=false",
                          *args], root, wait)
    except (OSError, subprocess.SubprocessError, ValueError):
        return False, ""
    return code in ok_codes, out


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
    # HEAD as well: a commit made on a detached HEAD is on no branch. Only
    # when there IS one -- in a repository with no commit yet, naming HEAD
    # is an error, and "nothing found" would read as "could not look".
    has_head, _ = _git(root, ["rev-parse", "-q", "--verify", "HEAD^{commit}"], clock)
    ok, out = _git(root, ["log", *(["HEAD"] if has_head else []),
                          "--branches", "--remotes", "--no-merges", "-E", "-i",
                          f"--grep={pattern}", f"--max-count={limit + 1}",
                          "--date=short", "--name-only", "--relative",
                          "--format=%x00%h%x1f%ad%x1f%s", "--"], clock)
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
        missed = commits is None or files is None
        unreadable = unreadable or missed
        commits, more_c = commits or ([], 0)
        files, more_f = files or ([], 0)
        stories.append(dict(item, commits=commits, more_commits=more_c,
                            files=files, more_files=more_f, unreadable=missed))
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
        if history.get("unreadable"):
            # "Nothing found" after "could not look" is two answers.
            lines += [f"Nothing was found for {', '.join(history['keys'])} in "
                      f"what could be read.", ""]
        else:
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
            lines += [f"- **{s['key']}**{origin}: "
                      + ("NOT LOOKED UP (git could not be read for it)"
                         if s.get("unreadable") else "nothing found"), ""]
            continue
        lines.append(f"- **{s['key']}**{origin}"
                     + (" -- incomplete: part of this could not be read"
                        if s.get("unreadable") else ""))
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
