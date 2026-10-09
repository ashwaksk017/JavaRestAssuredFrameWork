"""Compare this run's failures with the runs before it.

    python tools/failure_history.py record            # after a test run
    python tools/failure_history.py record --label "after the token fix"
    python tools/failure_history.py list
    python tools/failure_history.py show <signature id>
    python tools/failure_history.py note <signature id> "what it turned out to be"

`target/failure-digest.txt` (FailureDigestListener) already answers
"what failed in THIS run, and in how many distinct ways". It cannot
answer the questions asked the morning after:

* which of these are NEW since the last run, and which were there
  before;
* which tests still fail but DIFFERENTLY -- the fix worked and exposed
  the next problem, which reads as "still red" in a count;
* has this way of failing been seen before, and what did it turn out to
  be last time.

`record` keeps one small snapshot per run -- (test, row, signature) for
each failure, taken from the digest -- and prints the comparison with
the previous snapshot of the same suite. `note` attaches what a
signature turned out to be; the note is shown every time that signature
comes back, which is the point: the second person to meet it does not
start from nothing.

WHAT IT WILL NOT CLAIM
----------------------
* A test that failed before and is not failing now is "no longer
  failing", not "fixed": the digest lists failures, not what ran. A run
  of one suite makes every other suite's failures disappear. When the
  two runs executed a different number of tests, that is said.
* "Seen before" means the SAME signature, character for character. The
  digest masks long ids, e-mail addresses, known domains and timestamps,
  so most repeats do match -- but not all: a short number, a date, a
  name or a value cut off at the digest's length limit stays in the
  signature, and then the same failure reads as a new one. That is why
  a new signature is checked for a RESEMBLANCE as well.
* A resemblance is the closest earlier signature that fails the same
  WAY: at the same step, with the same status code RETURNED (the one
  that was expected is not evidence -- nearly every step expects 200),
  with the same specific exception, on the same path when one is given.
  It is shown with what the two share and its score. Wording alone never
  makes one, and neither does a class that says only THAT a test failed
  (`AssertionError`, `FlowStopped`). It is a pointer to read, never a
  diagnosis: two failures can read alike and fail for different reasons.
  Scoring is counting shared facets, so a score can always be explained.
* A run is dated by its digest file, not by when `record` was typed, and
  the same failures on a later date are a later run. It is compared with
  the run before it IN TIME, so a saved digest from last week recorded
  today is compared with what came before last week, not with yesterday.
  When the digest is much older or newer than the test report beside it,
  that is said: one of the two is left over from another run. For a
  digest given with `--digest`, the test report beside this project's
  last run says nothing about it and is not consulted.

WHAT IT CANNOT SEE, because the digest does not have it
-------------------------------------------------------
* The digest is written once per JVM, when the first `<test>` block of
  the suite file finishes. A suite file with several blocks is digested
  up to the first; failures in later blocks are not in it, and so not
  here. If the number of failing pairs looks low for the run, look at
  the suite file before believing a "no longer failing".
* A data row with no `test_case_id` is recorded as `-`. Several such
  rows of one test are one entry, and which of them it shows can differ
  between runs, which reads as "failing differently".

WHERE IT IS KEPT
----------------
`.failure-history/` in the project directory, which git ignores. Not
under `target/`: `mvn clean` would take the history with it. Signatures
come from the digest's masked section; notes are whatever was typed, so
the directory is ignored rather than trusted to be publishable.

The resemblance scoring is the idea of a sibling tool's defect
fingerprint (facets and two questions: "same kind?" and "fails the same
way?"). Its weights were tuned on one team's tickets and are not used.

Stdlib only. Reads the digest and the surefire summary; writes only
`.failure-history/`.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STORE = ".failure-history"
DIGEST = os.path.join("target", "failure-digest.txt")
SUREFIRE = os.path.join("target", "surefire-reports", "TEST-TestSuite.xml")
SECTION = "== every failing (test, row), one line each =="
KEEP = 60                     # snapshots per suite
RESEMBLES_AT = 0.5            # below this a resemblance is not worth showing
STALE_SECONDS = 180           # digest and test report further apart than this
MAX_ROWS = 40                 # lines printed per group

# A status code is a number SAID to be one: "answered 403", "status ...
# [403]", "HTTP 403". A bare 443 is a port, 300 a wait, 120 a row count.
_STATUS_SAID_RX = re.compile(
    r"(?i)(?:answered|returned|responded(?:\s+with)?|but\s+got|http(?:/[0-9.]+)?"
    r"|status(?:\s+code)?)\s*[:=]?\s*\[?([1-5][0-9]{2})(?![0-9.])")
# The status that came BACK. `expected [200]` is what the test hoped for.
_STATUS_ASSERT_RX = re.compile(r"(?i)found\s*\[([1-5][0-9]{2})\]")
# "expected status for <step> expected [..]": how both the status assertion
# and FlowStopped name the step. Real signatures carry the step, not a path.
_STEP_RX = re.compile(r"(?i)\bstatus for (.+?) expected \[")
# FlowStopped's own sentence, the same in every one of them.
_BOILERPLATE_RX = re.compile(r"(?is)\s--\sflow stopped here:.*?(?=\sServer said:|$)")
# A path starts with a segment that has a letter in it. `12/31/2026` is a date.
_PATH_RX = re.compile(r"(?<![A-Za-z0-9.:])/[A-Za-z][A-Za-z0-9{}_.-]*"
                      r"(?:/(?:<\*>|[A-Za-z0-9{}_.-]+))+")
_EXC_RX = re.compile(r"\b[A-Z][A-Za-z0-9_]*(?:Exception|Error|Fault|Stopped)\b")
_WORD_RX = re.compile(r"[A-Za-z][A-Za-z_]{2,}")
# Classes that say a test failed, not how.
GENERIC = frozenset({"AssertionError", "RuntimeException", "Exception", "Error",
                     "IllegalStateException", "SoftAssertError", "TestException",
                     "FlowStopped"})
_STOP = frozenset("""the and but for not was were with that this from have has had
    are its expected found true false null got did does should could would into
    than then when what which while your our their there here""".split())


def say(msg: str = "") -> None:
    print(msg, flush=True)


class Unusable(RuntimeError):
    """The digest is missing or is not one this can read."""


# ---- reading a run ---------------------------------------------------------

def signature_id(signature: str) -> str:
    return hashlib.sha1(signature.encode("utf-8")).hexdigest()[:10]


def parse_digest(text: str) -> dict:
    """{suite, failures: [{test, row, signature}], said} from a failure
    digest. `said` is the number of failing pairs its own header gives,
    or None."""
    text = text.lstrip("\ufeff")
    # Lines end at \n only. splitlines() also breaks at form feeds and the
    # Unicode separators, which can sit inside a signature.
    lines = [l.rstrip("\r") for l in text.split("\n")]
    first = lines[0].strip() if lines else ""
    if not first.startswith("FAILURE DIGEST"):
        raise Unusable("that file is not a failure digest (its first line is "
                       "not `FAILURE DIGEST -- <suite>`)")
    suite = first.partition("--")[2].strip() or "(unnamed)"
    m = re.search(r"unique failing \(test, row\) pairs:\s*([0-9]+)", text)
    said = int(m.group(1)) if m else None
    if SECTION not in lines:
        if said == 0:
            return {"suite": suite, "failures": [], "said": 0}
        raise Unusable(f"the digest has no `{SECTION}` section. It was written "
                       f"by a digest version this does not read.")
    failures, seen = [], set()
    for line in lines[lines.index(SECTION) + 1:]:
        if not line.strip():
            if failures:
                break
            continue
        if line.startswith("== "):
            break
        parts = line.split("\t")
        if len(parts) < 3:
            continue                         # a wrapped or explanatory line
        test, row, sig = parts[0].strip(), parts[1].strip(), "\t".join(parts[2:]).strip()
        if not test or (test, row) in seen:
            continue
        seen.add((test, row))
        failures.append({"test": test, "row": row, "signature": sig})
    return {"suite": suite, "failures": failures, "said": said}


def _mtime(path: str):
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def tests_run(root: str):
    """How many tests the run executed, from surefire, or None."""
    try:
        with io.open(os.path.join(root, SUREFIRE), encoding="utf-8", errors="replace") as fh:
            head = fh.read(4000)
    except OSError:
        return None
    m = re.search(r'<testsuite\b[^>]*\btests="([0-9]+)"', head)
    return int(m.group(1)) if m else None


# ---- the store --------------------------------------------------------------

def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")[:60] or "suite"


def store_dir(root: str) -> str:
    return os.path.join(root, STORE)


def snapshots(root: str, suite: str = "") -> list:
    """Every readable snapshot, oldest first; of one suite when named."""
    d = store_dir(root)
    out = []
    try:
        names = sorted(os.listdir(d)) if os.path.isdir(d) else []
    except OSError:
        names = []
    for name in names:
        if not name.endswith(".json") or name == "notes.json":
            continue
        try:
            with io.open(os.path.join(d, name), encoding="utf-8") as fh:
                snap = json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(snap, dict) or not isinstance(snap.get("failures"), list) \
                or not isinstance(snap.get("at"), str) or not isinstance(snap.get("suite"), str):
            continue
        if suite and snap.get("suite") != suite:
            continue
        snap["_file"] = name
        out.append(snap)
    # By when the run was, then by file: two runs in one second keep the
    # order they were recorded in.
    out.sort(key=lambda s: (s["at"], s["_file"]))
    return out


def notes(root: str) -> dict:
    """{signature id: {text, signature, at}}. An entry that is not that
    shape -- the file is plain JSON and can be edited -- is left out."""
    try:
        with io.open(os.path.join(store_dir(root), "notes.json"), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items()
            if isinstance(v, dict) and isinstance(v.get("text"), str) and v["text"].strip()}


def _write_json(path: str, payload) -> None:
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        with io.open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=1, ensure_ascii=False)
        os.replace(tmp, path)
    finally:
        try:
            os.remove(tmp)                 # only still there when the write failed
        except OSError:
            pass


def _before(snaps: list, at: str, file: str = "~") -> list:
    """The snapshots that come before (at, file) in time."""
    return [s for s in snaps if (s["at"], s["_file"]) < (at, file)]


def record(root: str, digest_text: str, label: str = "", now: str = "",
           written: float = None, own_run: bool = True) -> tuple:
    """(snapshot, the snapshot before it in time or None, was it new).

    `written` is when the digest file was written (its mtime). It dates
    the run, and it is what tells two runs with identical failures apart
    from one run recorded twice: the digest carries no date of its own.
    `own_run` is false for a digest that is not this project's last run;
    the test report in target/ then says nothing about it.
    """
    parsed = parse_digest(digest_text)
    sha = hashlib.sha1(digest_text.encode("utf-8")).hexdigest()
    stamp = int(written) if written else None
    earlier = snapshots(root, parsed["suite"])
    for old in earlier:
        # ANY recorded run, not only the newest: an older digest recorded
        # again must not become a new run each time. A snapshot with no
        # date of writing (or a digest with none) is matched on content.
        same_time = old.get("written") == stamp or old.get("written") is None or stamp is None
        if old.get("digest") == sha and same_time:
            before = _before(earlier, old["at"], old["_file"])
            return old, (before[-1] if before else None), False
    if not now:
        when = datetime.fromtimestamp(stamp, timezone.utc) if stamp else datetime.now(timezone.utc)
        now = when.strftime("%Y%m%dT%H%M%SZ")
    report_at = _mtime(os.path.join(root, SUREFIRE)) if own_run else None
    snap = {"suite": parsed["suite"], "at": now, "label": label.strip()[:120],
            "digest": sha, "written": stamp,
            "tests_run": tests_run(root) if own_run else None,
            "said": parsed["said"],
            "report_gap": (round(abs(report_at - written)) if report_at and written else None),
            "failures": parsed["failures"]}
    d = store_dir(root)
    try:
        os.makedirs(d, exist_ok=True)
        base = f"{now}-{_safe(parsed['suite'])}"
        # Always numbered: `x-002.json` sorts BEFORE `x.json`, and the order
        # of the files is the order of the runs.
        name, n = f"{base}-001.json", 1
        while os.path.exists(os.path.join(d, name)):   # two runs in one second
            n += 1
            name = f"{base}-{n:03d}.json"
        _write_json(os.path.join(d, name), snap)
    except OSError as e:
        raise Unusable(f"{STORE}/ could not be written ({type(e).__name__})") from None
    snap["_file"] = name
    for old in earlier[:max(0, len(earlier) + 1 - KEEP)]:
        try:
            os.remove(os.path.join(d, old["_file"]))
        except OSError:
            pass
    before = _before(earlier, now, name)
    return snap, (before[-1] if before else None), True


# ---- comparing ----------------------------------------------------------------

def _by_test(snap: dict) -> dict:
    return {(f.get("test", ""), f.get("row", "")): f.get("signature", "")
            for f in snap.get("failures") or [] if isinstance(f, dict)}


def compare(current: dict, previous: dict) -> dict:
    """What moved between two runs of one suite."""
    now, before = _by_test(current), _by_test(previous)
    return {
        "new": sorted(k for k in now if k not in before),
        "gone": sorted(k for k in before if k not in now),
        "changed": sorted(k for k in now if k in before and now[k] != before[k]),
        "same": sorted(k for k in now if k in before and now[k] == before[k]),
        "ran_now": current.get("tests_run"), "ran_before": previous.get("tests_run"),
    }


def seen_before(signature: str, earlier: list) -> dict:
    """How often and when exactly this signature failed in earlier runs."""
    runs = [s for s in earlier
            if any(isinstance(f, dict) and f.get("signature") == signature
                   for f in s.get("failures") or [])]
    return {"runs": len(runs), "first": runs[0]["at"] if runs else "",
            "last": runs[-1]["at"] if runs else ""}


_FACETS = {}


def facets(signature: str) -> dict:
    """What makes a way of failing recognisable, as sets.

    step       the step the status assertion or FlowStopped names
    status     the status code that came back, where the signature says
    exception  the specific classes it names; never the generic ones
    path       request paths, with ids and masked values as {}
    words      the rest of its wording
    """
    got = _FACETS.get(signature)
    if got is not None:
        return got
    exceptions = set(_EXC_RX.findall(signature))
    head = signature.split("|")[0].strip()
    if head and " " not in head:
        exceptions.add(head)
    paths = set()
    for p in _PATH_RX.findall(signature):
        p = re.sub(r"<\*>|\{[^}]*\}", "{}", p)
        p = "/".join("{}" if re.fullmatch(r"[0-9]+|[0-9a-f-]{16,}", seg) else seg
                     for seg in p.split("/"))
        paths.add(p.rstrip(".,;:"))
    steps = {re.sub(r"\s+", " ", s).strip().lower() for s in _STEP_RX.findall(signature)}
    text = _BOILERPLATE_RX.sub(" ", _PATH_RX.sub(" ", signature))
    status = set(_STATUS_SAID_RX.findall(text))
    if re.search(r"(?i)\bstatus\b", text):
        status |= set(_STATUS_ASSERT_RX.findall(text))
    step_words = {w.lower() for s in steps for w in _WORD_RX.findall(s)}
    got = {"step": steps - {""}, "status": status, "exception": exceptions - GENERIC,
           "path": paths,
           "words": {w.lower() for w in _WORD_RX.findall(text)} - _STOP - step_words
                    - {e.lower() for e in exceptions}}
    if len(_FACETS) < 50_000:
        _FACETS[signature] = got
    return got


# How the failure fails counts for more than how its message is worded.
_WEIGHTS = {"step": 0.30, "status": 0.25, "path": 0.20, "exception": 0.15, "words": 0.10}
DISTINCTIVE = ("step", "status", "path", "exception")


def _overlap(a: set, b: set, both: bool = False):
    """Shared / all. None -- no evidence either way -- when neither has
    any; with `both`, also when only one does."""
    if not (a or b) or (both and not (a and b)):
        return None
    return len(a & b) / len(a | b)


def resemblance(a: str, b: str) -> tuple:
    """(score 0..1, [what they share]).

    Only facets that can be compared take part: two bare assertion
    messages are not credited for both lacking a status code, and a
    failure reported by a step (`FlowStopped`) is not penalised against
    the same failure reported by an assertion, which names no class of
    its own. A resemblance needs a step, a returned status, a path or a
    specific exception in common: the same wording is how a message
    reads, not how the test failed.
    """
    fa, fb = facets(a), facets(b)
    if not any(fa[n] & fb[n] for n in DISTINCTIVE):
        return 0.0, []
    score = weight = 0.0
    shared = []
    for name, w in _WEIGHTS.items():
        o = _overlap(fa[name], fb[name], both=(name == "exception"))
        if o is None:
            continue
        weight += w
        score += w * o
        common = sorted(fa[name] & fb[name])
        if common:
            shared.append(f"{name} {', '.join(common[:6 if name == 'words' else 3])}")
    return (round(score / weight, 2), shared) if weight else (0.0, [])


def closest(signature: str, known: list) -> tuple:
    """(score, earlier signature, what they share) for the most alike
    earlier signature, or (0, "", [])."""
    best = (0.0, "", [])
    for other in known:
        if other == signature:
            continue
        score, shared = resemblance(signature, other)
        if score > best[0]:
            best = (score, other, shared)
    return best if best[0] >= RESEMBLES_AT else (0.0, "", [])


# ---- the report -----------------------------------------------------------------

def _flat(text: str, cap: int = 150) -> str:
    s = re.sub(r"\s+", " ", str(text)).strip()
    return s[:cap] + (" ..." if len(s) > cap else "")


def report(root: str, current: dict, previous) -> list:
    """The comparison, as lines."""
    earlier = _before(snapshots(root, current["suite"]), current["at"],
                      current.get("_file", "~"))
    kept = notes(root)
    now = _by_test(current)
    lines = [f"suite {current['suite']}: {len(now)} failing (test, row) pair(s), "
             f"{len(set(now.values()))} distinct signature(s)"
             + (f", {current['tests_run']} test(s) run" if current.get("tests_run") else "")]
    said = current.get("said")
    if isinstance(said, int) and said != len(now):
        lines.append(f"  WARN the digest's own header says {said} failing pair(s) and "
                     f"{len(now)} could be read from it. The ones not read will "
                     f"look like they stopped failing.")
    gap = current.get("report_gap")
    if isinstance(gap, (int, float)) and gap > STALE_SECONDS:
        lines.append(f"  WARN the digest and the test report beside it were written "
                     f"{int(gap)} seconds apart: one of them is left over from "
                     f"another run. If the last run stopped before its tests, this "
                     f"is the run before it.")
    if previous is None:
        lines += ["", "This is the first recorded run of this suite: nothing to "
                      "compare with yet."]
    else:
        d = compare(current, previous)
        lines += ["", f"compared with the run of {previous['at']}"
                      + (f" ({previous['label']})" if previous.get("label") else "") + ":"]
        if d["ran_now"] and d["ran_before"] and d["ran_now"] != d["ran_before"]:
            lines.append(f"  NOTE the two runs executed a different number of tests "
                         f"({d['ran_before']} then, {d['ran_now']} now). A failure "
                         f"that is gone may simply not have run.")
        elif not (d["ran_now"] and d["ran_before"]):
            lines.append("  NOTE how many tests each run executed is not known, so "
                         "a failure that is gone may simply not have run.")
        before = _by_test(previous)
        for title, key in (("NEW (not failing before)", "new"),
                           ("FAILING DIFFERENTLY (same test, another signature)", "changed"),
                           ("still failing the same way", "same"),
                           ("no longer failing (passed, or did not run)", "gone")):
            rows = d[key]
            lines.append(f"  {title}: {len(rows)}")
            show = rows if key != "same" else []
            for test, row in show[:MAX_ROWS]:
                lines.append("      " + (f"{test} [{row}]" if row and row != "-" else test))
                if key == "gone":
                    continue
                if key == "changed":
                    lines.append(f"          was: {_flat(before[(test, row)])}")
                lines.append(f"          now: {_flat(now[(test, row)])}")
            if len(show) > MAX_ROWS:
                lines.append(f"      ... and {len(show) - MAX_ROWS} more")

    lines += ["", "signatures in this run:"]
    # One pass over the history: which runs each signature failed in.
    runs_of = {}
    for s in earlier:
        for sig in {f.get("signature") for f in s["failures"] if isinstance(f, dict)}:
            if sig:
                runs_of.setdefault(sig, []).append(s["at"])
    known = sorted(runs_of)
    counts = {}
    for sig in now.values():
        counts[sig] = counts.get(sig, 0) + 1
    for sig, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        sid = signature_id(sig)
        lines.append(f"  [{sid}] {n} failure(s)  {_flat(sig)}")
        runs = runs_of.get(sig) or []
        if runs:
            lines.append(f"      seen in {len(runs)} earlier run(s), first {runs[0]}, "
                         f"last {runs[-1]}")
        else:
            lines.append("      not seen in any earlier run")
            score, other, shared = closest(sig, known)
            if other:
                lines.append(f"      RESEMBLES [{signature_id(other)}] ({int(score * 100)}%: "
                             f"{'; '.join(shared)}) -- a pointer, not a diagnosis:")
                lines.append(f"          {_flat(other)}")
                if kept.get(signature_id(other)):
                    lines.append(f"          its note: {_flat(kept[signature_id(other)]['text'], 300)}")
        if kept.get(sid):
            lines.append(f"      NOTE ({kept[sid].get('at', '')}): {_flat(kept[sid]['text'], 300)}")
    if not counts:
        lines.append("  (none: nothing failed)")
    return lines


def add_note(root: str, sid: str, text: str) -> str:
    """Attach what a signature turned out to be. Returns the signature."""
    sid = sid.strip().strip("[]").lower()
    if not re.fullmatch(r"[0-9a-f]{10}", sid):
        raise Unusable("a signature id is the ten characters in [brackets] that "
                       "`record` prints")
    found = ""
    for snap in snapshots(root):
        for f in snap["failures"]:
            if isinstance(f, dict) and signature_id(f.get("signature", "")) == sid:
                found = f["signature"]
    if not found:
        raise Unusable(f"no recorded failure has the signature id {sid}")
    if not text.strip():
        raise Unusable("the note is empty")
    all_notes = notes(root)
    all_notes[sid] = {"text": text.strip()[:2000], "signature": found,
                      "at": datetime.now(timezone.utc).strftime("%Y-%m-%d")}
    try:
        os.makedirs(store_dir(root), exist_ok=True)
        _write_json(os.path.join(store_dir(root), "notes.json"), all_notes)
    except OSError as e:
        raise Unusable(f"{STORE}/ could not be written ({type(e).__name__})") from None
    return found


def main(argv=None, root: str = "") -> int:
    root = root or ROOT
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("record", help="keep this run's failures and compare with the last run")
    p.add_argument("--digest", default=DIGEST, help="default %(default)s")
    p.add_argument("--label", default="", help="a few words about this run")
    sub.add_parser("list", help="the runs recorded so far")
    p = sub.add_parser("show", help="every run a signature failed in")
    p.add_argument("signature")
    p = sub.add_parser("note", help="write down what a signature turned out to be")
    p.add_argument("signature")
    p.add_argument("text")
    a = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    try:
        if a.cmd == "record":
            path = a.digest if os.path.isabs(a.digest) else os.path.join(root, a.digest)
            try:
                with io.open(path, encoding="utf-8-sig", errors="replace") as fh:
                    text = fh.read()
            except OSError:
                raise Unusable(f"{a.digest} is not there. It is written when a test "
                               f"run finishes; run the tests first.") from None
            own = os.path.normcase(os.path.abspath(path)) == \
                os.path.normcase(os.path.abspath(os.path.join(root, DIGEST)))
            snap, previous, fresh = record(root, text, a.label, written=_mtime(path),
                                           own_run=own)
            if not fresh:
                say(f"this digest was already recorded ({snap['at']}); nothing new "
                    f"was kept. Run the tests again for a new one.")
            if not own:
                say(f"NOTE recorded from {a.digest}, not from this project's last run")
            for line in report(root, snap, previous):
                say(line)
            say()
            say(f"kept in {STORE}/ (ignored by git). To write down what a signature "
                f"turned out to be: python tools/failure_history.py note <id> \"...\"")
            return 0
        if a.cmd == "list":
            snaps = snapshots(root)
            for s in snaps:
                sigs = len({f.get("signature") for f in s["failures"] if isinstance(f, dict)})
                say(f"  {s['at']}  {s['suite']:<24} {len(s['failures']):>4} failing, "
                    f"{sigs} signature(s)" + (f"  -- {s['label']}" if s.get("label") else ""))
            say(f"  {len(snaps)} run(s) recorded")
            return 0
        if a.cmd == "note":
            sig = add_note(root, a.signature, a.text)
            say(f"noted for: {_flat(sig)}")
            return 0
        sid = a.signature.strip().strip("[]").lower()
        if not re.fullmatch(r"[0-9a-f]{10}", sid):
            raise Unusable("a signature id is the ten characters in [brackets] that "
                           "`record` prints")
        hits = 0
        for s in snapshots(root):
            tests = [f for f in s["failures"] if isinstance(f, dict)
                     and signature_id(f.get("signature", "")) == sid]
            if tests:
                hits += 1
                say(f"  {s['at']}  {s['suite']}: {len(tests)} failure(s)")
                for f in tests[:8]:
                    say(f"      {f['test']}" + (f" [{f['row']}]" if f.get("row") not in ("", "-") else ""))
                if hits == 1:
                    say(f"      signature: {_flat(tests[0]['signature'], 300)}")
        note = notes(root).get(sid)
        if note:
            say(f"  NOTE ({note.get('at', '')}): {note['text']}")
        if not hits:
            say(f"  no recorded failure has the signature id {sid}")
            return 1
        return 0
    except Unusable as e:
        say(f"refused: {e}")
        return 2
    except Exception as e:                               # noqa: BLE001
        say(f"FAIL unexpected {type(e).__name__} in failure_history.py "
            f"(line {e.__traceback__.tb_lineno if e.__traceback__ else '?'})")
        return 1


if __name__ == "__main__":
    sys.exit(main())
