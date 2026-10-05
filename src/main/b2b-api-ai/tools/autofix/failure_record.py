"""Machine-readable failure records -- Phase 0 of the autofix loop.

WHY
---
`verify_all` prints 47 checks for a human to read. The loop needs three
things printing cannot give it:

  1. a STABLE IDENTITY per failure, so "still the same failure" is
     distinguishable from "a new one". Without it the loop either
     re-fixes what it already fixed, or declares victory because a
     DIFFERENT failure now occupies the same check.
  2. the files a failure implicates, split into source and generated, so
     an agent gets an excerpt instead of a 20k-line emitter -- and so
     Phase 1 can reject a diff that edits generated output.
  3. a declared POLICY per check: whether a failure there may be fixed
     without a human. `step-parity` drift is the class that produced the
     cluster[0] body bugs; it needs the ReadyAPI XML cross-check, which
     is judgment, so it is never auto-fixed -- the loop asks instead.

Fingerprints
------------
Converts are byte-for-byte deterministic, so a checker's output over a
fixed tree is stable and can be hashed directly. Two are recorded:

  fingerprint          sha1 of the whole normalized output. Changes on any
                       detail change -- answers "did this failure change
                       at all?"
  salient_fingerprint  sha1 of the diagnostic lines only. Survives
                       incidental noise, so this is what baseline.json
                       matches on: an accepted failure stays accepted when
                       an unrelated line moves.

Normalization strips what varies between runs over the same tree --
absolute paths, durations, timestamps, hex ids, memory addresses. It
deliberately does NOT strip counts. "1 of 17 steps unreachable" becoming
"2 of 17" is a different failure and has to read as one.

Nothing here changes what verify_all decides. It observes and labels.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import date, datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCHEMA = 1
BASELINE_REL = "tools/autofix/baseline.json"

# --- what may never be edited by a fix -------------------------------
# Verbatim from .gitignore. A fix that lands in generated output
# evaporates on the next convert: `converter-v2-cursor-handedits` is four
# generated files that were hand-edited and then overwritten. Phase 1
# rejects a diff touching these; Phase 0 only labels them.
GENERATED_PREFIXES = (
    "_audit/",
    "_flows/",
    "Suites/",
    "src/main/java/com/hi/api/rest/clients/",
    "src/main/java/com/hi/api/support/",
    "src/main/java/com/hi/api/templates/",
    "src/main/resources/templates/",
    "src/main/resources/test_data_defaults/",
    "src/main/resources/config/",
    "src/test/java/com/hi/api/tests/imported/",
    "src/test/resources/csv/",
)
GENERATED_FILES = ("src/main/resources/converter_identity.json",)

# Hand-written, off-limits for a different reason: the author's own manual
# suites, which are never staged.
NEVER_TOUCH_PREFIXES = ("src/main/java/com/hi/api/rest/manual/",)
NEVER_TOUCH_FILES = ("src/test/java/com/hi/api/tests/manual/GuestEnrollTest.java",)

# Verbatim from ra_converter._AUTHOR_EDITABLE_BASENAMES. Written
# SKIP-IF-EXISTS, so a convert never overwrites them -- and six of them
# live INSIDE the generated root, which would otherwise make them look
# untouchable. They are editable; the catch is that the emitted copy is
# not the source, so an edit to it alone survives in this tree and is
# absent from the next clean convert.
AUTHOR_EDITABLE_BASENAMES = frozenset({
    "AuthHelper.java",
    "ManualClient.java",
    "PerMethodCsvDataProvider.java",
    "PlaceholderResolver.java",
    "ProgressLogListener.java",
    "CtxFields.java",
    "ImportedScenario.java",
    "ImportedTemplates.java",
    "ImportedTestdataCleanup.java",
    "TestThreadState.java",
    "IdentityVocabulary.java",
})


def classify_path(rel: str) -> str:
    """'never-touch' | 'author-editable' | 'generated' | 'source'.

    Order matters: ManualClient.java is author-editable AND lives under
    the manual/ tree, and never-touch has to win.
    """
    p = rel.replace("\\", "/").lstrip("./")
    if p in NEVER_TOUCH_FILES or p.startswith(NEVER_TOUCH_PREFIXES):
        return "never-touch"
    if os.path.basename(p) in AUTHOR_EDITABLE_BASENAMES:
        return "author-editable"
    if p in GENERATED_FILES or p.startswith(GENERATED_PREFIXES):
        return "generated"
    return "source"


def is_editable(kls: str) -> bool:
    """Whether a fix may land here at all (content rules still apply)."""
    return kls in ("source", "author-editable")


# --- check taxonomy ---------------------------------------------------
# `kind` is derived from the command so the 47 Check() entries stay as
# they are; only the handful that do not follow the naming convention are
# named here. Editing 47 call sites to add two fields is how a typo gets
# into a gate.
_KIND_OVERRIDE = {
    "java-compile": "compile",
    "java-tests": "runtime",
    "vocabulary": "unit",       # phase_vocabulary.py self-tests on import
}

# A failure in these is NEVER fixed without a human choosing. Reasons
# differ and both matter:
#   step-parity / dataflow / request-schemas -- the fix needs the ReadyAPI
#     XML (or a vendor spec) cross-checked first. Standing rule: a parity
#     failure is OUR gap, so it must be verified against the source, not
#     explained away. That verification is judgment.
#   java-tests -- the TestNG guard suite IS the guard. "Fixing" a failing
#     guard test is the purest form of weakening the thing that tells us
#     we are right.
_PROMPT_CHECKS = frozenset({
    "step-parity",
    "dataflow",
    "request-schemas",
    "java-tests",
})

# What the loop offers when a prompt-policy check fails. Carried in the
# record so the CLI and the agent read one list, not two that drift.
PROMPT_OPTIONS = (
    {"id": "inspect",  "label": "Cross-check the ReadyAPI XML myself, then decide",
     "acts": False, "default": True},
    {"id": "report",   "label": "Agent investigates and writes a findings report only (no code changes)",
     "acts": False, "default": False},
    {"id": "patch",    "label": "Agent proposes a patch on a branch for review",
     "acts": True,  "default": False},
    {"id": "accept",   "label": "Record in baseline.json as a known artifact (needs a reason + expiry)",
     "acts": False, "default": False},
    {"id": "skip",     "label": "Skip for this run",
     "acts": False, "default": False},
)


def kind_of(name: str, cmd: list[str]) -> str:
    if name in _KIND_OVERRIDE:
        return _KIND_OVERRIDE[name]
    script = ""
    for part in cmd[1:]:
        if part.endswith(".py"):
            script = part.replace("\\", "/")
            break
    base = os.path.basename(script)
    if base.startswith("check_"):
        return "tree"           # scans the generated tree
    if base.startswith("test_"):
        return "unit"           # pure-Python emitter tests
    if cmd and cmd[0] == "mvn":
        return "compile"
    return "unit"


def policy_of(name: str) -> str:
    """'auto' | 'prompt'. ('never' is reserved for Phase 4 and unused.)"""
    return "prompt" if name in _PROMPT_CHECKS else "auto"


# --- normalization ----------------------------------------------------
_ROOT_FWD = ROOT.replace("\\", "/")

_DROP_LINE_RX = re.compile(
    r"^\s*(?:"
    r"Downloading|Downloaded|Progress \(|"
    r"\[INFO\] Total time|\[INFO\] Finished at|"
    r"Picked up (?:JAVA_TOOL_OPTIONS|_JAVA_OPTIONS)|"
    r"OpenJDK 64-Bit Server VM warning"
    r")", re.I)

_SUBS = (
    # durations: "12.3s", "1234 ms", "Ran 153 tests in 0.456s"
    (re.compile(r"\b\d+(?:\.\d+)?\s*(?:ms|s|sec|secs|seconds)\b", re.I), "<dur>"),
    (re.compile(r"\b\d+(?:\.\d+)?\s*(?:min|mins|minutes)\b", re.I), "<dur>"),
    # timestamps
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"), "<ts>"),
    (re.compile(r"\b\d{2}:\d{2}:\d{2}(?:[.,]\d+)?\b"), "<ts>"),
    # object ids / hashes / addresses
    (re.compile(r"0x[0-9a-fA-F]+"), "<addr>"),
    (re.compile(r"\b[0-9a-f]{12,}\b", re.I), "<hash>"),
)


# Redaction. These records are built to be handed to an external agent,
# so a credential that reaches one has left the machine. Checker output is
# mostly paths and counts, but `substitution` prints resolved placeholder
# values and a failing Java test can print a request body, so the value is
# reachable in principle -- and "in principle" is the only standard that
# matters for a secret.
#
# Applied INSIDE normalize(), which means redact-then-fingerprint: no
# secret ever enters the hash input, and a rotated credential does not
# move the fingerprint and invalidate an acceptance.
_REDACT = (
    (re.compile(r"(?i)\b(jdbc:[a-z0-9+.\-]+://)[^\s\"']+"), r"\1<redacted>"),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=\-]{8,}"), r"\1 <redacted>"),
    (re.compile(r"\beyJ[A-Za-z0-9._\-]{10,}"), "<redacted-jwt>"),
    (re.compile(r"(?i)\b(pass(?:word|wd)?|secret|client_secret|c_sec|"
                r"api[_-]?key|apikey|access_token|assertion|credential)s?"
                r"(\s*[:=]\s*\"?|\"\s*:\s*\")"
                r"[^\s\"',}]+"), r"\1\2<redacted>"),
)


def redact(line: str) -> str:
    for rx, rep in _REDACT:
        line = rx.sub(rep, line)
    return line


def normalize(out: str) -> list[str]:
    """Normalized, non-empty, REDACTED lines of a check's output.

    Order is preserved: a checker's output order is deterministic over a
    fixed tree, and reordering would hide a real change.
    """
    lines: list[str] = []
    for raw in (out or "").splitlines():
        line = raw.replace("\\", "/")
        # absolute -> repo-relative, case-insensitively (Windows)
        line = re.sub(re.escape(_ROOT_FWD) + r"/?", "", line, flags=re.I)
        line = re.sub(r"(?i)\b[a-z]:/[^\s:\"']*/(?:temp|tmp)/[^\s:\"']+", "<tmp>", line)
        for rx, rep in _SUBS:
            line = rx.sub(rep, line)
        line = redact(line)
        line = re.sub(r"[ \t]+", " ", line).strip()
        if not line or _DROP_LINE_RX.match(line):
            continue
        lines.append(line)
    return lines


_VERDICT_RX = re.compile(
    r"(?:FAIL|FAILED|FAILURE|ERROR|Error:|error:|Traceback|AssertionError|"
    r"Exception|MISSING|missing|unreachable|unresolved|collision|duplicate|"
    r"mismatch|drift|dropped|not found|NOT FOUND|BUILD FAILURE|\[ERROR\]|"
    r"expected .* got|does not)", re.I)

# Informational lines, kept to what has actually been OBSERVED in this
# repo's checker output rather than what seemed plausible. An earlier
# draft listed scanned/checking/reading/wrote/analysed; none of them occur,
# and an unjustified entry here loosens a fingerprint for no gain.
#
# What does occur, 416 times in one fast run, is a per-test pass line from
# the converter's own test runner: `ok  test_some_name`. Those churn the
# fingerprint whenever an unrelated test is added or renamed.
#
# Deliberately NOT noise: counter lines. `registered cases : 1160` is
# context and `missing : 0` is phase-order's verdict, and the two are
# structurally identical -- no pattern can drop one and keep the other. So
# both stay salient, and a tree check's fingerprint moves when the tree
# grows. That is correct: a cached acceptance may genuinely not describe
# the new tree.
_NOISE_RX = re.compile(r"^ok\s+[A-Za-z_][A-Za-z0-9_]*(?:\s+<dur>)?$")


def _is_noise(line: str) -> bool:
    """Only an `ok <identifier>` pass line counts as noise.

    The verdict-word veto that guards the general case is deliberately NOT
    applied here: test names legitimately contain `fail`, `missing` and
    `does_not`, and vetoing on those would keep hundreds of pass lines
    salient -- the churn this exists to remove. The pattern is anchored
    tightly enough (`ok` + one identifier + optional scrubbed duration)
    that a real verdict line cannot match it.
    """
    return bool(_NOISE_RX.match(line))


def salient(lines: list[str], tail: int = 25) -> list[str]:
    """Everything except lines known to be purely informational.

    FAIL-CLOSED, and it has to be. The first version of this kept only
    lines matching a keyword allowlist, which inverted the risk: a second
    failing case printed in a checker's own vocabulary ("dropped 3 of 9
    steps" -- no FAIL, no ERROR) matched nothing, so the fingerprint did
    not move and the loop would have read a NEW failure as the accepted
    one. Across 47 heterogeneous checkers no allowlist can cover every
    verdict phrasing, so an unrecognized line now counts as salient.

    The residual risk runs the safe way: an unanticipated progress line
    invalidates an acceptance and the failure gets looked at again. That
    costs a re-investigation. The other direction costs a silent miss.

    The tail fallback keeps an all-noise output from fingerprinting as
    empty, which would collide with every other empty one.
    """
    keep = [l for l in lines if not _is_noise(l)]
    return keep if keep else lines[-tail:]


def _sha1(lines: list[str]) -> str:
    h = hashlib.sha1()
    for l in lines:
        h.update(l.encode("utf-8", "replace"))
        h.update(b"\n")
    return h.hexdigest()


def fingerprints(out: str) -> tuple[str, str, list[str], list[str]]:
    """(fingerprint, salient_fingerprint, normalized, salient_lines)."""
    norm = normalize(out)
    sal = salient(norm)
    return _sha1(norm), _sha1(sal), norm, sal


# --- implicated files -------------------------------------------------
_PATH_RX = re.compile(
    r"\b(?:src|tools|Suites|_audit|_flows)/[A-Za-z0-9_./\-]+"
    r"\.(?:java|py|json|csv|xml|properties|md)\b")


def implicated(out: str, cmd: list[str]) -> dict[str, list[str]]:
    """Repo-relative paths named in the output, split by editability.

    The checker's own script is always included under `source`: for a
    tree check the fix is either in the emitter or in the checker, and
    which side it is cannot be decided from the output alone.
    """
    found: set[str] = set()
    for m in _PATH_RX.finditer((out or "").replace("\\", "/")):
        found.add(m.group(0))
    for part in cmd[1:]:
        if part.endswith(".py"):
            found.add(part.replace("\\", "/"))
    # Three buckets, by what an agent may do with them. An
    # author-editable file is reported as source because it IS a fix site;
    # that its source lives elsewhere is an invariant, enforced in Phase 1.
    buckets: dict[str, list[str]] = {"source": [], "generated": [], "never_touch": []}
    for p in sorted(found):
        kls = classify_path(p)
        buckets["never_touch" if kls == "never-touch"
                else ("source" if is_editable(kls) else "generated")].append(p)
    return buckets


# --- where did it fail: suites and cases ------------------------------
# Phase 2 picks the SMALLEST suite that reproduces a failure, because a
# full 15-suite reconvert is ~20-25 min and a single suite is ~150s. It
# cannot do that from file paths alone: step-parity names case ids and no
# files at all.
SUPPORT_REL = "src/main/java/com/hi/api/support"

# A LAST RESORT only. Measured against the real registry, this shape
# matches 869 of 1155 ids: it misses the 286 that carry a space
# (`... _200 2`), a bracketed bug ref (`...[Bug-B2B-1874]`), or no ticket
# prefix at all (`ActivateProgramAccount`). It is also B2B-flavoured, and
# GOAL ids need not look like this. So ids are matched from the REGISTRY
# first -- literal strings, no shape assumption -- and this only catches
# an id the registry does not know.
_CASE_RX = re.compile(r"\b([A-Z][A-Z0-9]*-\d+(?:_[A-Za-z0-9_]+)?)\b")

_suites_cache: tuple[str, ...] | None = None
_case_index_cache: dict[str, list[str]] | None = None
_case_rx_cache: "re.Pattern[str] | None" = None


def reset_caches() -> None:
    """Forget the suite list, the registry index and its matcher.

    Phase 2 reconverts between evaluations in one process; without this
    it would keep matching against the tree as it was before the convert.
    """
    global _suites_cache, _case_index_cache, _case_rx_cache
    _suites_cache = _case_index_cache = _case_rx_cache = None


def _registered_case_matcher() -> "re.Pattern[str] | None":
    """One alternation over every registered id, longest first.

    Longest-first matters: `B2B-1329_..._200` and `B2B-1329_..._200 2` are
    both real ids, and the shorter one would otherwise shadow the longer.
    The lookarounds stop an id matching inside a longer token; they allow a
    trailing `]` or space because real ids contain them.
    """
    global _case_rx_cache
    if _case_rx_cache is None:
        ids = sorted(case_index(), key=len, reverse=True)
        if not ids:
            return None
        _case_rx_cache = re.compile(
            r"(?<![A-Za-z0-9_-])(" + "|".join(re.escape(i) for i in ids)
            + r")(?![A-Za-z0-9_-])")
    return _case_rx_cache


def known_suites() -> tuple[str, ...]:
    """Converted suite directories, newest listing each call is not worth it.

    A suite is a directory under support/ holding a `cases/` dir --
    the same definition check_case_duplication uses. Framework types
    (CtxFields.java, scenario/) live there too and are not suites.
    """
    global _suites_cache
    if _suites_cache is None:
        base = os.path.join(ROOT, SUPPORT_REL.replace("/", os.sep))
        names: list[str] = []
        if os.path.isdir(base):
            for d in sorted(os.listdir(base)):
                if os.path.isdir(os.path.join(base, d, "cases")):
                    names.append(d)
        _suites_cache = tuple(names)
    return _suites_cache


def case_index() -> dict[str, list[str]]:
    """{case id: [suite, ...]} by reusing check_case_duplication.collect().

    Imported rather than reimplemented on purpose: a second copy of "what
    a registration looks like" is how the `_normalize_jdbc_query` fix
    landed on two of three code paths. That module already reads every
    registration form, including the shared-spec loop.

    Degrades to {} rather than raising -- this is an annotation, and
    verify_all is the gate.
    """
    global _case_index_cache
    if _case_index_cache is None:
        idx: dict[str, list[str]] = {}
        try:
            tools = os.path.join(ROOT, "tools")
            if tools not in sys.path:
                sys.path.insert(0, tools)
            import check_case_duplication as ccd  # noqa: PLC0415
            found, _total, _unparsed, _unqual = ccd.collect()
            for cid, by_suite in found.items():
                idx[cid] = sorted(by_suite.keys())
        except Exception:
            idx = {}
        _case_index_cache = idx
    return _case_index_cache


def locate(out: str, files: dict[str, list[str]] | None = None) -> dict:
    """Suites and case ids a failure points at.

    Three independent sources, because different checkers name different
    things: suite names printed outright, case ids resolved through the
    registration index, and `support/<suite>/` in any implicated path.
    """
    text = (out or "").replace("\\", "/")
    suites: set[str] = set()
    for s in known_suites():
        if re.search(r"\b" + re.escape(s) + r"\b", text):
            suites.add(s)

    idx = case_index()
    found: set[str] = set()
    # Primary: ids the registry actually holds, matched literally.
    rx = _registered_case_matcher()
    if rx is not None:
        found.update(m.group(1) for m in rx.finditer(text))
    # Secondary: anything id-shaped the registry does NOT hold. UNION, not
    # intersection -- an earlier version intersected, which dropped exactly
    # the interesting case: an id a checker names because it failed to
    # register at all.
    found.update(m.group(1) for m in _CASE_RX.finditer(text)
                 if m.group(1) not in idx)
    cases = sorted(found)
    for cid in cases:
        suites.update(idx.get(cid, ()))

    for group in (files or {}).values():
        for p in group:
            m = re.search(re.escape(SUPPORT_REL) + r"/([^/]+)/", p)
            if m and m.group(1) in known_suites():
                suites.add(m.group(1))

    return {"suites": sorted(suites), "cases": cases[:40]}


# --- a pass that checked nothing --------------------------------------
# `request-schemas` exits 0 and says so when no OpenAPI spec is present
# (the vendor contract is gitignored by design on a public repo). That is
# an honest skip, not a rubber stamp -- but the loop must not read it as
# evidence that generated bodies satisfy the contract.
#
# Named explicitly, one check at a time. A generic "looks like a skip"
# regex matched three unit-test NAMES containing the word "absent" when it
# was tried, which is how a guard becomes a rubber stamp.
_SKIP_PATTERNS = {
    "request-schemas": re.compile(r"no OpenAPI spec in .* nothing to check", re.I),
    # An unzipped copy has no git, so there is nothing tracked to inspect.
    # Exit 0 is right (a copy cannot commit anything) but PASS would be a
    # lie: the protection is absent, not satisfied.
    "tracked-csv": re.compile(r"not a git repository -- nothing to check", re.I),
    # Both of these already PRINT "nothing to check" and were still
    # counted as passes -- the exact lie the rest of this table exists to
    # stop. `phase-order` checks nothing before the first convert, and
    # `skill-api` checks nothing in a tree with no .cursor/skills, so in
    # both cases the guard is absent rather than satisfied.
    "phase-order": re.compile(r"nothing to check", re.I),
    "skill-api": re.compile(r"nothing to check", re.I),
}


def skipped(name: str, out: str) -> bool:
    rx = _SKIP_PATTERNS.get(name)
    return bool(rx and rx.search(out or ""))


# --- baseline ---------------------------------------------------------
def baseline_path() -> str:
    return os.path.join(ROOT, BASELINE_REL.replace("/", os.sep))


def load_baseline(path: str | None = None) -> dict:
    p = path or baseline_path()
    if not os.path.exists(p):
        return {"schema": SCHEMA, "accepted": []}
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def match_baseline(baseline: dict, name: str, salient_fp: str,
                   today: date | None = None) -> tuple[dict | None, str]:
    """Find an accepted-failure entry for this check.

    Returns (entry, state) where state is one of:
      'accepted'  -- matches and is in date
      'expired'   -- matches but past `expires`; does NOT suppress, so an
                     accepted failure cannot quietly become permanent
      'stale'     -- an entry exists for this check but the fingerprint
                     moved: the failure CHANGED and the acceptance no
                     longer describes it
      'none'      -- nothing recorded
    """
    today = today or date.today()
    for_check = [e for e in baseline.get("accepted", []) if e.get("check") == name]
    if not for_check:
        return None, "none"
    for e in for_check:
        if e.get("salient_fingerprint") != salient_fp:
            continue
        exp = e.get("expires")
        if exp:
            try:
                if date.fromisoformat(exp) < today:
                    return e, "expired"
            except ValueError:
                return e, "expired"
        return e, "accepted"
    return for_check[0], "stale"


# --- record + document ------------------------------------------------
def repro_of(cmd: list[str]) -> str:
    """A command a human or an agent can paste, run from the repo root.

    The interpreter is collapsed to `python`: sys.executable is an
    absolute path to this machine's venv, which is noise in a report and
    wrong on the remote machine.
    """
    if not cmd:
        return ""
    head = cmd[0].replace("\\", "/")
    first = "python" if os.path.basename(head).lower().startswith("python") else head
    return " ".join([first] + [p.replace("\\", "/") for p in cmd[1:]])


def build_record(name: str, why: str, cmd: list[str], ok: bool, secs: float,
                 out: str, baseline: dict | None = None,
                 today: date | None = None,
                 suppress_accepted: bool = False) -> dict:
    """One record for one check.

    `baseline` ANNOTATES (always, when supplied): `baseline_state` says
    whether an acceptance matches, has aged out, or no longer describes
    the failure. `suppress_accepted` is what makes an acceptance change
    the verdict to "accepted". Keeping those separate is what lets a
    records-only run be informative without the file claiming a status
    the exit code disagrees with.
    """
    rec = {
        "name": name,
        "why": why,
        "kind": kind_of(name, cmd),
        "policy": policy_of(name),
        "repro": repro_of(cmd),
        "status": "pass" if ok else "fail",
        "duration_s": round(secs, 2),
    }
    if ok:
        # A check that exited 0 having checked nothing is not evidence.
        if skipped(name, out):
            rec["status"] = "skipped"
            rec["skip_reason"] = (normalize(out) or ["checked nothing"])[0]
        return rec
    fp, sfp, norm, sal = fingerprints(out)
    rec["fingerprint"] = fp
    rec["salient_fingerprint"] = sfp
    rec["implicated_files"] = implicated(out, cmd)
    rec["locate"] = locate(out, rec["implicated_files"])
    rec["salient"] = sal[:40]
    rec["output_tail"] = norm[-25:]
    rec["baseline"] = None
    if baseline is not None:
        entry, state = match_baseline(baseline, name, sfp, today)
        rec["baseline_state"] = state
        if entry is not None:
            rec["baseline"] = {
                "reason": entry.get("reason", ""),
                "recorded": entry.get("recorded", ""),
                "expires": entry.get("expires", ""),
                "owner": entry.get("owner", ""),
            }
        if state == "accepted" and suppress_accepted:
            rec["status"] = "accepted"
    if rec["policy"] == "prompt":
        rec["prompt_options"] = [dict(o) for o in PROMPT_OPTIONS]
    return rec


_in_repo_cache: bool | None = None


def in_git_repo() -> bool:
    """Whether ROOT is inside a git working tree.

    Three checks assumed it was, because every machine they were written
    on had one. A copy unzipped from a release is not a repository, and
    there `git check-ignore` and `git checkout --` fail for a reason that
    has nothing to do with what is being guarded -- so the guards reported
    findings that were really just "no git here". Asking once, plainly,
    lets each of them say that instead.
    """
    global _in_repo_cache
    if _in_repo_cache is None:
        import subprocess
        try:
            p = subprocess.run(
                ("git", "rev-parse", "--is-inside-work-tree"), cwd=ROOT,
                capture_output=True, text=True, timeout=10)
            _in_repo_cache = (p.returncode == 0
                              and p.stdout.strip().lower() == "true")
        except Exception:
            _in_repo_cache = False
    return _in_repo_cache


def git_info() -> dict:
    """Branch, short head and dirty flag, or {} if git is unavailable.

    Phase 3 hands a records file to an agent working in a worktree; "which
    commit was this measured against" is the first thing a reviewer asks
    of a proposed fix, and reconstructing it later is guesswork.
    """
    import subprocess  # local: keeps `import failure_record` cheap
    def _g(*a):
        try:
            p = subprocess.run(("git",) + a, cwd=ROOT, capture_output=True,
                               text=True, timeout=10)
            return p.stdout.strip() if p.returncode == 0 else ""
        except Exception:
            return ""
    head = _g("rev-parse", "--short", "HEAD")
    if not head:
        return {}
    return {
        "head": head,
        "branch": _g("rev-parse", "--abbrev-ref", "HEAD"),
        # Generated output is gitignored, so this reports SOURCE edits only
        # -- which is the question that matters for attributing a fix.
        "dirty": bool(_g("status", "--porcelain", "--untracked-files=no")),
    }


def build_document(records: list[dict], mode: str, baseline_applied: bool,
                   git: dict | None = None) -> dict:
    counts: dict[str, int] = {}
    for r in records:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {
        "schema": SCHEMA,
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "root": ROOT.replace("\\", "/"),
        "mode": mode,
        "baseline_applied": baseline_applied,
        "git": git if git is not None else git_info(),
        "totals": {
            "selected": len(records),
            "passed": counts.get("pass", 0),
            "failed": counts.get("fail", 0),
            "accepted": counts.get("accepted", 0),
            "skipped": counts.get("skipped", 0),
        },
        # Suites any failure points at, unioned -- Phase 2's starting set
        # for "what is the smallest thing that reproduces this?".
        "failing_suites": sorted({
            s for r in records
            if r["status"] in ("fail", "accepted")
            for s in (r.get("locate") or {}).get("suites", [])
        }),
        "checks": records,
    }


def write_document(doc: dict, path: str) -> str:
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return path
