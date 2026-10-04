"""Reject a candidate fix before anything runs it -- Phase 1 of the loop.

These rules are checked against the DIFF alone, so they cost nothing and
run before a convert, a compile or a gate. Each one exists because of a
way a green gate can be bought instead of earned.

The ordering principle: when the gate fails there are always two ways to
make it pass -- fix the converter, or weaken what measures it. The second
is faster and an agent optimising for green will find it. Prompting
against that is not a control. These are.

    python tools/autofix/invariants.py --staged
    python tools/autofix/invariants.py --range HEAD~1..HEAD
    python tools/autofix/invariants.py --diff-file /tmp/candidate.patch

Exit 0 when a diff may proceed, 1 when it may not, 2 on a usage error.
A verdict of NEEDS_FIXTURE or NEEDS_GATE is not a rejection: it says the
diff may proceed only once that evidence exists, which is Phase 2's job
to produce.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import failure_record as fr  # noqa: E402

ROOT = fr.ROOT

# --- author-editable framework files ---------------------------------
# Verbatim from ra_converter._AUTHOR_EDITABLE_BASENAMES. These are written
# SKIP-IF-EXISTS, so the emitted copy is never overwritten -- which is
# exactly why editing only the emitted copy looks like it worked and then
# vanishes the first time someone converts into a clean tree.
#
# Six are file-backed under tools/ra_converter/framework/; the rest have
# their source inline in ra_converter.py. Either way a fix has to land in
# BOTH places. This cost a round once already (PlaceholderResolver).
AUTHOR_EDITABLE = fr.AUTHOR_EDITABLE_BASENAMES
FRAMEWORK_DIR = "tools/ra_converter/framework"
EMITTER = "tools/ra_converter/ra_converter.py"


def framework_source_for(rel: str) -> str:
    """Where the real source of an author-editable file lives."""
    base = os.path.basename(rel)
    cand = f"{FRAMEWORK_DIR}/{base}"
    if os.path.exists(os.path.join(ROOT, cand.replace("/", os.sep))):
        return cand
    return EMITTER


# --- files the loop must never be able to edit ------------------------
# A loop that can edit these can declare itself correct. baseline.json is
# the sharpest: one line there turns any failure into an accepted one.
SELF_WEAKENING = ("tools/autofix/",)
# The WHOLE directory, not a list of files. The list was five entries long
# and left thirteen editable, including:
#   failure_record.py -- make fingerprints constant and every failure
#                        matches the baseline
#   manifest.py       -- under-report the blast radius
#   accept.py         -- weaken what an acceptance requires
#   test_*.py         -- delete the assertions that pin all of the above
# Enumerating the guards meant the enumeration itself had to be kept
# complete, and it was not. A prefix cannot be incomplete.
#
# The cost is that the loop can never fix a genuine bug in its own
# tooling, which is the right trade: that is a person's job precisely
# because the loop cannot be the judge of it.
# Project plumbing: an agent fixing a converter bug has no business here,
# and a change would be felt far outside the failure it was sent to fix.
INFRASTRUCTURE = (
    "pom.xml",
    ".gitignore",
    ".github/",
    "tools/ra_converter/converter.config.json",
)

_CHECK_RX = re.compile(r"^\s*Check\(")
_TESTDEF_RX = re.compile(r"^\s*(?:def\s+(test_\w+)|@Test\b)")
_ASSERT_RX = re.compile(
    r"\b(?:assert\w*|self\.assert\w+|Assert\.\w+|assertThat|softAssert\.\w+)\b")
_URL_RX = re.compile(r"\b(?:https?://|jdbc:[a-z0-9+.\-]+://)([A-Za-z0-9._\-]+)")
_HOSTLIKE_RX = re.compile(
    r"\b([a-z0-9][a-z0-9\-]*(?:\.[a-z0-9][a-z0-9\-]*)+\.[a-z]{2,})\b", re.I)
_SECRET_RX = (
    re.compile(r"(?i)\b(?:password|passwd|secret|client_secret|api[_-]?key|"
               r"apikey|access_token|assertion|credential)s?\s*[:=]\s*"
               r"[\"']?[^\s\"',;}]{6,}"),
    re.compile(r"\beyJ[A-Za-z0-9._\-]{20,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=\-]{20,}"),
)
# Placeholders and obvious non-values that the pattern above would
# otherwise flag in committed example files and docs.
_SECRET_OK_RX = re.compile(
    r"(?i)(<redacted|__SET_|changeme|example|placeholder|\$\{|#\{|"
    r"\"\"|''|xxx+|\.\.\.|your[_-]?|TODO|getenv|System\.getProperty|"
    r"Config\.get|row\.get|ctx\.get)")

REJECT, NEEDS_FIXTURE, NEEDS_GATE, WARN = "REJECT", "NEEDS_FIXTURE", "NEEDS_GATE", "WARN"

# --- waivers ----------------------------------------------------------
# The rules stay strict and a HUMAN overrides them, rather than the rules
# being loosened until nothing is held. `verify_all.py` counts as a
# checker, so adding a Check -- which strengthens the gate -- is held too.
# Narrowing the rule to cover that case would also narrow it for the case
# it exists to catch, so the override lives here instead.
#
# A waiver is an action, not a configuration: it needs a reason, it is
# reported as WAIVED rather than dropped, and it is appended to
# target/autofix-waivers.jsonl with the commit it applied to.
#
# PHASE 3 NOTE: the orchestrator must never pass --waive on an agent's
# behalf. A loop that can waive its own holds has no holds. The flag is
# for the person reading the report.
WAIVER_LOG = "target/autofix-waivers.jsonl"

# Two rules are never waivable, because both restate a standing
# constraint that does not have exceptions: a credential must never be
# visible outside program_configuration.json, and the author's manual
# suites are never touched.
UNWAIVABLE = frozenset({"secret-added", "never-touch"})


@dataclass
class Violation:
    rule: str
    verdict: str
    path: str
    message: str
    line: str = ""

    def __str__(self) -> str:
        tail = f"\n        {self.line.strip()[:120]}" if self.line else ""
        return f"[{self.verdict}] {self.rule}: {self.path}\n        {self.message}{tail}"


@dataclass
class FileDiff:
    path: str
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    new_file: bool = False
    deleted: bool = False


# --- diff parsing -----------------------------------------------------
def repo_prefix() -> str:
    """Path from the git root down to ROOT, e.g. 'src/main/b2b-api-ai/'.

    Diff paths are git-root-relative while every rule here is
    ROOT-relative; without this they simply never match and the whole
    file becomes a very confident no-op.
    """
    try:
        p = subprocess.run(("git", "rev-parse", "--show-prefix"), cwd=ROOT,
                           capture_output=True, text=True, timeout=10)
        return p.stdout.strip().replace("\\", "/") if p.returncode == 0 else ""
    except Exception:
        return ""


def _strip_prefix(p: str, prefix: str) -> str:
    p = p.replace("\\", "/").strip()
    p = re.sub(r"^[ab]/", "", p)
    if prefix and p.startswith(prefix):
        p = p[len(prefix):]
    return p


def normalize_patch(diff_text: str, prefix: str = "") -> str:
    """Rewrite header paths to be ROOT-relative.

    `git diff` emits paths relative to the GIT ROOT, and this project
    lives in a subdirectory -- so the most likely patch an agent produces
    (it will run git diff) arrives as `a/src/main/b2b-api-ai/tools/...`.
    Validated as-is, every rule here missed: `generated-output` did not
    recognise a support/ path behind the extra prefix, and the diff passed
    clean. It then failed to apply, so nothing landed -- but the guard had
    already been bypassed, and the report blamed the wrong thing.

    Normalising once, up front, means validation and `git apply` both see
    the same paths whichever convention the patch arrived in.
    """
    out: list[str] = []
    for line in (diff_text or "").replace("\r\n", "\n").split("\n"):
        m = re.match(r"^diff --git (\S+) (\S+)$", line)
        if m:
            out.append(f"diff --git a/{_strip_prefix(m.group(1), prefix)} "
                       f"b/{_strip_prefix(m.group(2), prefix)}")
            continue
        m = re.match(r"^(---|\+\+\+) (\S+)(.*)$", line)
        if m and m.group(2) != "/dev/null":
            side = "a/" if m.group(1) == "---" else "b/"
            out.append(f"{m.group(1)} {side}"
                       f"{_strip_prefix(m.group(2), prefix)}{m.group(3)}")
            continue
        out.append(line)
    body = "\n".join(out)
    return body if body.endswith("\n") or not body else body + "\n"


def parse_diff(diff_text: str, prefix: str = "") -> list[FileDiff]:
    files: list[FileDiff] = []
    cur: FileDiff | None = None
    for raw in (diff_text or "").splitlines():
        if raw.startswith("diff --git "):
            m = re.match(r"diff --git [ab]/(.+?) [ab]/(.+)$", raw)
            path = (m.group(2) if m else "").replace("\\", "/")
            if prefix and path.startswith(prefix):
                path = path[len(prefix):]
            cur = FileDiff(path=path)
            files.append(cur)
        elif cur is None:
            # A plain unified diff with no `diff --git` line at all, which
            # is what a hand-written or agent-written patch usually is.
            if raw.startswith("--- ") and not raw.endswith("/dev/null"):
                p = re.sub(r"^[ab]/", "",
                           raw[4:].strip().split("\t")[0].replace("\\", "/"))
                if prefix and p.startswith(prefix):
                    p = p[len(prefix):]
                cur = FileDiff(path=p)
                files.append(cur)
            continue
        elif raw.startswith("--- ") and raw.endswith("/dev/null"):
            cur.new_file = True
        elif raw.startswith("+++ ") and raw.endswith("/dev/null"):
            cur.deleted = True
        elif not cur.path and raw[:4] in ("+++ ", "--- "):
            # Recover the path from the ---/+++ header when `diff --git`
            # was missing or in a shape the pattern did not expect. An
            # unparsed header used to leave path="" and EVERY rule then
            # matched nothing: the diff validated clean while touching
            # whatever it liked. Found by a real malformed patch, which is
            # the only kind that would ever exploit it.
            p = raw[4:].strip().split("\t")[0].replace("\\", "/")
            p = re.sub(r"^[ab]/", "", p)
            if prefix and p.startswith(prefix):
                p = p[len(prefix):]
            if p and p != "/dev/null":
                cur.path = p
        elif raw.startswith("+") and not raw.startswith("+++"):
            cur.added.append(raw[1:])
        elif raw.startswith("-") and not raw.startswith("---"):
            cur.removed.append(raw[1:])
    return files


# --- known hostnames, from COMMITTED files only -----------------------
_hosts_cache: frozenset[str] | None = None


def published_hosts() -> frozenset[str]:
    """Hostnames already visible in TRACKED files.

    Tracked is the whole point: the generated tree is full of vendor
    hostnames, so scanning it would make "already present" true for every
    host the agent might add, and the rule would protect nothing. The
    question is "is this host already public?", and git answers it.
    """
    global _hosts_cache
    if _hosts_cache is None:
        hosts: set[str] = set()
        try:
            p = subprocess.run(("git", "ls-files"), cwd=ROOT,
                               capture_output=True, text=True, timeout=60)
            names = [n for n in p.stdout.splitlines() if n.strip()] if p.returncode == 0 else []
        except Exception:
            names = []
        for rel in names:
            if os.path.splitext(rel)[1].lower() in (".png", ".jpg", ".gif", ".jar",
                                                    ".zip", ".class", ".ico"):
                continue
            fp = os.path.join(ROOT, rel.replace("/", os.sep))
            try:
                with open(fp, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError:
                continue
            hosts.update(h.lower() for h in _HOSTLIKE_RX.findall(text))
        _hosts_cache = frozenset(hosts)
    return _hosts_cache


def reset_caches() -> None:
    global _hosts_cache
    _hosts_cache = None
    fr.reset_caches()


# --- the rules --------------------------------------------------------
def _rule_paths(fd: FileDiff) -> list[Violation]:
    out: list[Violation] = []
    rel, base = fd.path, os.path.basename(fd.path)

    # Fail closed on a header this parser could not read. Every other rule
    # keys on the path, so an empty one silently satisfies all of them --
    # the diff reads as clean while changing anything it likes.
    if not rel:
        out.append(Violation(
            "unparseable-diff", REJECT, "(unknown)",
            "a file header this parser could not read. Every rule here keys "
            "on the path, so validating an unknown one would pass the diff "
            "without checking it. Re-emit the patch with standard "
            "`diff --git a/<path> b/<path>` headers",
            (fd.added or fd.removed or [""])[0]))
        return out

    # Fail closed on anything that is not plainly a path inside this
    # project. An absolute path, a drive letter or a `..` segment all
    # passed every rule before: classify_path called them 'source', and
    # the only thing standing between that and a write outside the tree
    # was whatever `git apply` happened to refuse.
    norm = rel.replace("\\", "/")
    if (norm.startswith("/") or re.match(r"^[A-Za-z]:", norm)
            or ".." in norm.split("/")):
        out.append(Violation(
            "path-escape", REJECT, rel,
            "not a relative path inside the project. A fix lands in this "
            "tree or it does not land"))
        return out
    pfx = repo_prefix()
    if pfx and norm.startswith(pfx):
        out.append(Violation(
            "path-escape", REJECT, rel,
            f"still carries the repository prefix {pfx!r} after "
            f"normalisation, so it would resolve to {pfx}{norm}. Emit the "
            f"patch relative to {os.path.basename(ROOT)}/"))
        return out

    kls = fr.classify_path(rel)

    if kls == "never-touch":
        out.append(Violation(
            "never-touch", REJECT, rel,
            "the author's hand-written manual suite; never staged, never edited "
            "by the loop"))
        return out

    if kls == "generated":
        out.append(Violation(
            "generated-output", REJECT, rel,
            "generated output -- the next convert overwrites it, so a fix here "
            "looks like it worked and then vanishes. Fix the emitter instead"))
        return out

    for p in SELF_WEAKENING:
        if rel == p or (p.endswith("/") and rel.startswith(p)):
            out.append(Violation(
                "self-weakening", REJECT, rel,
                "the loop's own guard. A loop that can edit this can declare "
                "itself correct -- baseline.json especially: one entry turns any "
                "failure into an accepted one. Only a human, via accept.py"))
            return out

    for p in INFRASTRUCTURE:
        if rel == p or (p.endswith("/") and rel.startswith(p)):
            out.append(Violation(
                "infrastructure", REJECT, rel,
                "project plumbing, outside the blast radius of any converter "
                "fix. converter.config.json in particular must stay "
                "byte-identical to the emitter's built-in tables"))
            return out
    return out


def _rule_unpaired_framework_edit(files: list[FileDiff]) -> list[Violation]:
    """An author-editable file edited without its source."""
    touched = {fd.path for fd in files}
    out: list[Violation] = []
    for fd in files:
        base = os.path.basename(fd.path)
        if base not in AUTHOR_EDITABLE or fd.deleted:
            continue
        src = framework_source_for(fd.path)
        if fd.path == src or src in touched:
            continue
        out.append(Violation(
            "unpaired-framework-edit", REJECT, fd.path,
            f"{base} is written SKIP-IF-EXISTS, so this edit survives in THIS "
            f"tree and is absent from the next clean convert. Its source is "
            f"{src} -- patch both or neither"))
    return out


def _rule_removed_guards(fd: FileDiff) -> list[Violation]:
    out: list[Violation] = []
    rel = fd.path

    if rel == "tools/verify_all.py":
        gone = sum(1 for l in fd.removed if _CHECK_RX.match(l))
        added = sum(1 for l in fd.added if _CHECK_RX.match(l))
        if gone > added:
            out.append(Violation(
                "check-removed", REJECT, rel,
                f"{gone - added} Check(...) entr(y/ies) removed from the gate. "
                f"Deleting a check is not fixing what it caught"))

    if os.path.basename(rel).startswith("test_") or rel.endswith("Test.java"):
        gone = {m.group(1) for l in fd.removed
                if (m := _TESTDEF_RX.match(l)) and m.group(1)}
        kept = {m.group(1) for l in fd.added
                if (m := _TESTDEF_RX.match(l)) and m.group(1)}
        lost = gone - kept
        if lost and not fd.deleted:
            out.append(Violation(
                "test-removed", REJECT, rel,
                f"test(s) removed: {', '.join(sorted(lost)[:5])}. A failing test "
                f"is evidence, not an obstacle"))

    net = (sum(1 for l in fd.removed if _ASSERT_RX.search(l))
           - sum(1 for l in fd.added if _ASSERT_RX.search(l)))
    if net > 0 and not fd.deleted and fr.is_editable(fr.classify_path(rel)):
        out.append(Violation(
            "assertion-removed", REJECT, rel,
            f"net {net} assertion(s) removed. If an assertion is wrong, say so "
            f"and change what it asserts -- do not delete the measurement"))
    return out


def _rule_new_hostname(fd: FileDiff, hosts: frozenset[str]) -> list[Violation]:
    """No new vendor hostname in committed source. This repo is public."""
    if not fr.is_editable(fr.classify_path(fd.path)):
        return []
    out: list[Violation] = []
    seen: set[str] = set()
    for line in fd.added:
        for host in list(_URL_RX.findall(line)) + list(_HOSTLIKE_RX.findall(line)):
            h = host.lower()
            if h in hosts or h in seen:
                continue
            if h.endswith((".example.com", ".example.org", ".local", ".invalid",
                           ".localhost", ".test")) or h in ("example.com", "example.org"):
                continue
            seen.add(h)
            out.append(Violation(
                "new-hostname", REJECT, fd.path,
                f"{h!r} does not appear in any tracked file. This repo is "
                f"public; a vendor hostname must not arrive in it through a fix",
                line))
    return out


def _rule_secret_added(fd: FileDiff) -> list[Violation]:
    out: list[Violation] = []
    for line in fd.added:
        if _SECRET_OK_RX.search(line):
            continue
        for rx in _SECRET_RX:
            if rx.search(line):
                out.append(Violation(
                    "secret-added", REJECT, fd.path,
                    "looks like a literal credential. Credentials live in "
                    "program_configuration.json and reach code through "
                    "Config.get() -- nothing else may show the value", line))
                break
    return out


def _rule_checker_only(files: list[FileDiff]) -> list[Violation]:
    """A change confined to the checkers has to prove it still catches."""
    paths = [fd.path for fd in files]
    if not paths:
        return []
    def is_checker(p: str) -> bool:
        b = os.path.basename(p)
        return (p.startswith("tools/") and b.startswith("check_")) or p == "tools/verify_all.py"
    checkers = [p for p in paths if is_checker(p)]
    others = [p for p in paths if not is_checker(p)
              and not os.path.basename(p).startswith("test_")]
    if not (checkers and not others):
        return []
    # Name the fixture to run, or say one has to be written first. "Add a
    # fixture" with no command attached is advice; a command is a gate.
    try:
        import mutations as mut
        # The check NAME is not the file name: tools/check_phase_order.py is
        # the check `phase-order`. Deriving it by string surgery produced
        # `phase_order`, which has no fixture, so the gate reported "no
        # fixture exists" for a check that has one. verify_all.CHECKS is
        # the only place that mapping is actually defined.
        names = sorted({n for p in checkers if (n := mut.check_name_for_script(p))})
        covered = [n for n in names if mut.has_fixture(n)]
        missing = [n for n in names if not mut.has_fixture(n)]
    except Exception:
        covered, missing = [], []
    how = ""
    if covered:
        how += ("\n        run: python tools/autofix/mutations.py --check "
                + " --check ".join(covered))
    if missing:
        how += ("\n        no fixture exists for: " + ", ".join(missing)
                + " -- write one in tools/autofix/mutations.py first")
    return [Violation(
        "checker-only-change", NEEDS_FIXTURE, ", ".join(sorted(checkers)),
        "nothing outside the checkers changed, so this makes a failure go away "
        "by changing what is measured. It may proceed only with proof the check "
        "still FAILS on the tree it was built to catch." + how)]


def parse_waivers(specs: list[str] | None) -> list[tuple[str, str]]:
    """`rule` or `rule:path-fragment` into (rule, fragment) pairs."""
    out: list[tuple[str, str]] = []
    for s in specs or []:
        rule, _, frag = s.partition(":")
        out.append((rule.strip(), frag.strip()))
    return out


def apply_waivers(violations: list[Violation], waivers: list[tuple[str, str]]
                  ) -> tuple[list[Violation], list[Violation], list[str]]:
    """(still standing, waived, refused waiver reasons)."""
    standing: list[Violation] = []
    waived: list[Violation] = []
    refused: list[str] = []
    for v in violations:
        match = None
        for rule, frag in waivers:
            if rule != v.rule:
                continue
            if frag and frag not in v.path:
                continue
            match = rule
            break
        if match is None:
            standing.append(v)
        elif v.rule in UNWAIVABLE:
            refused.append(
                f"{v.rule} cannot be waived -- it restates a standing "
                f"constraint with no exceptions ({v.path})")
            standing.append(v)
        else:
            waived.append(v)
    return standing, waived, refused


def record_waivers(waived: list[Violation], reason: str) -> str | None:
    """Append to the waiver log. A waiver nobody can find later is a hole."""
    if not waived:
        return None
    import json
    from datetime import datetime, timezone
    path = os.path.join(ROOT, WAIVER_LOG.replace("/", os.sep))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    row = {
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git": fr.git_info(),
        "reason": reason,
        "waived": [{"rule": v.rule, "verdict": v.verdict, "path": v.path}
                   for v in waived],
    }
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return WAIVER_LOG


def validate(files: list[FileDiff], hosts: frozenset[str] | None = None) -> list[Violation]:
    hosts = published_hosts() if hosts is None else hosts
    out: list[Violation] = []
    for fd in files:
        path_v = _rule_paths(fd)
        out.extend(path_v)
        if any(v.verdict == REJECT for v in path_v):
            continue        # no point reading the contents of a file it may not touch
        out.extend(_rule_removed_guards(fd))
        out.extend(_rule_new_hostname(fd, hosts))
        out.extend(_rule_secret_added(fd))
    out.extend(_rule_unpaired_framework_edit(files))
    out.extend(_rule_checker_only(files))
    return out


# --- CLI --------------------------------------------------------------
def _git_diff(*args: str) -> str:
    p = subprocess.run(("git", "diff", "--unified=0", *args), cwd=ROOT,
                       capture_output=True, text=True)
    if p.returncode != 0:
        print(f"git diff failed: {p.stderr.strip()}")
        raise SystemExit(2)
    return p.stdout


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--staged", action="store_true", help="validate the index")
    g.add_argument("--worktree", action="store_true", help="validate unstaged edits")
    g.add_argument("--range", metavar="A..B", help="validate a commit range")
    g.add_argument("--diff-file", metavar="PATH", help="validate a saved patch")
    ap.add_argument("--quiet", action="store_true", help="print only the verdict")
    ap.add_argument("--waive", action="append", metavar="RULE[:PATH]",
                    help="a HUMAN override for one rule, optionally scoped to "
                         "a path fragment. Repeatable. Requires --waive-reason. "
                         "Never pass this on an agent's behalf")
    ap.add_argument("--waive-reason", default="",
                    help="why the override is correct; appended to "
                         + WAIVER_LOG)
    args = ap.parse_args(argv)

    waivers = parse_waivers(args.waive)
    if waivers and not args.waive_reason.strip():
        print("refused: --waive needs --waive-reason. An override with no "
              "stated reason is indistinguishable from the rule not existing.")
        return 2

    if args.diff_file:
        with open(args.diff_file, encoding="utf-8", errors="replace") as fh:
            diff = fh.read()
    elif args.staged:
        diff = _git_diff("--cached")
    elif args.range:
        diff = _git_diff(args.range)
    else:
        diff = _git_diff()

    files = parse_diff(diff, repo_prefix())
    if not files:
        print("no files in that diff -- nothing to validate")
        return 0

    all_violations = validate(files)
    violations, waived, refused = apply_waivers(all_violations, waivers)
    rejects = [v for v in violations if v.verdict == REJECT]
    pending = [v for v in violations if v.verdict in (NEEDS_FIXTURE, NEEDS_GATE)]

    if not args.quiet:
        print(f"{len(files)} file(s): " + ", ".join(fd.path for fd in files[:8])
              + (f" (+{len(files) - 8})" if len(files) > 8 else ""))
        for v in violations:
            print(v)
    for msg in refused:
        print(f"[UNWAIVABLE] {msg}")
    if waived:
        log = record_waivers(waived, args.waive_reason.strip())
        print(f"\n{len(waived)} violation(s) WAIVED by hand -- "
              f"recorded in {log}:")
        for v in waived:
            print(f"  [WAIVED] {v.rule} ({v.verdict}): {v.path}")
        print(f"  reason: {args.waive_reason.strip()}")

    if rejects:
        print(f"\nREJECTED: {len(rejects)} invariant(s) violated.")
        return 1
    if pending:
        print(f"\nHELD: {len(pending)} precondition(s) not yet met.")
        return 1
    print("\nOK: the diff may proceed to the gate ladder."
          + ("  (with waivers above)" if waived else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
