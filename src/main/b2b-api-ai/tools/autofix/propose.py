"""Ask an agent for a fix, then refuse to believe it -- Phase 3.

    python tools/autofix/propose.py --records target/verify.json --dry-run
    python tools/autofix/propose.py --records target/verify.json \
        --patch target/candidate.diff          # validate a patch by hand
    python tools/autofix/propose.py --records target/verify.json --agent cursor

PROPOSE-ONLY. The result is a patch, a report and (optionally) a branch.
Nothing is committed to the working branch and nothing is ever pushed. A
human reads the report and merges.

THE SEQUENCE, AND WHY IT IS THIS ORDER
--------------------------------------
  1. snapshot  -- so a rejected proposal leaves no trace
  2. packet    -- the failure, the repro, the rules, the answer format
  3. agent     -- one attempt
  4. invariants-- on the DIFF, before anything runs it. Cheapest rejection
                  there is, and it catches the dangerous proposals: a
                  generated-file edit, a deleted assertion, a credential.
  5. apply     -- only now does the tree change
  6. ladder    -- cheapest rung first, stop at the first failure
  7. manifest  -- measure the blast radius, compare it to what the agent
                  predicted
  8. accept or restore

The agent never runs steps 4-8 and cannot waive them. It also never sees
--waive: a loop that can waive its own holds has no holds.

WHAT IS NOT AUTOMATED
---------------------
A check whose policy is `prompt` (step-parity, dataflow, request-schemas,
java-tests) is not sent to an agent by default. Those need the ReadyAPI
XML cross-checked first, which is judgement. `--include-prompt-policy`
builds the packet anyway, for a human who has already decided.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import failure_record as fr  # noqa: E402
import invariants as inv  # noqa: E402
import ladder as L  # noqa: E402
import ledger as lg  # noqa: E402
import manifest as mf  # noqa: E402
import taskpacket as tp  # noqa: E402

ROOT = fr.ROOT
WORK = "target/autofix"
SOURCE_ROOTS = ("tools", "src/main/java", "src/test/java", "src/main/resources")
SKIP_PARTS = ("__pycache__", "/target/", "/node_modules/")


# --- workspace --------------------------------------------------------
def editable_source() -> list[str]:
    """Repo-relative source files a fix is allowed to touch."""
    out: list[str] = []
    for root in SOURCE_ROOTS:
        base = os.path.join(ROOT, root.replace("/", os.sep))
        if not os.path.isdir(base):
            continue
        for dirpath, dirs, names in os.walk(base):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for n in names:
                rel = os.path.relpath(os.path.join(dirpath, n), ROOT).replace("\\", "/")
                if any(p in f"/{rel}" for p in SKIP_PARTS) or rel.endswith(".pyc"):
                    continue
                if fr.is_editable(fr.classify_path(rel)):
                    out.append(rel)
    return sorted(out)


def _git(*args: str) -> tuple[int, str]:
    p = subprocess.run(("git",) + args, cwd=ROOT, capture_output=True, text=True)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _hash(rel: str) -> str | None:
    p = os.path.join(ROOT, rel.replace("/", os.sep))
    try:
        with open(p, "rb") as fh:
            return hashlib.sha1(fh.read()).hexdigest()
    except OSError:
        return None


def dirty_source() -> set[str]:
    """Source files git cannot restore: modified or untracked.

    These are the only ones worth copying. For everything else
    `git checkout --` is exact and free, which is what keeps a snapshot
    from costing 82MB per attempt.
    """
    rc, out = _git("status", "--porcelain", "--untracked-files=all")
    if rc != 0:
        return set()
    prefix = inv.repo_prefix()
    dirty: set[str] = set()
    for line in out.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip().strip('"')
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        path = path.replace("\\", "/")
        if prefix and path.startswith(prefix):
            path = path[len(prefix):]
        elif prefix:
            continue            # outside this project
        if path.endswith("/"):
            base = os.path.join(ROOT, path.replace("/", os.sep))
            for dp, _d, ns in os.walk(base):
                for n in ns:
                    r = os.path.relpath(os.path.join(dp, n), ROOT).replace("\\", "/")
                    if fr.is_editable(fr.classify_path(r)) and "__pycache__" not in r:
                        dirty.add(r)
        elif fr.is_editable(fr.classify_path(path)):
            dirty.add(path)
    return dirty


def snapshot_source(workdir: str) -> dict:
    """Hashes for every editable file, plus copies of the dirty ones."""
    files = editable_source()
    dirty = dirty_source()
    save = os.path.join(workdir, "dirty")
    os.makedirs(save, exist_ok=True)
    kept = 0
    for rel in sorted(dirty):
        src = os.path.join(ROOT, rel.replace("/", os.sep))
        if not os.path.isfile(src):
            continue
        dst = os.path.join(save, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        kept += 1
    return {
        "hashes": {rel: _hash(rel) for rel in files},
        "dirty": sorted(dirty),
        "copied": kept,
        "copy_dir": save,
    }


def changed_since(snap: dict) -> dict[str, list[str]]:
    now = {rel: _hash(rel) for rel in editable_source()}
    before = snap["hashes"]
    return {
        "added": sorted(set(now) - set(before)),
        "removed": sorted(set(before) - set(now)),
        "modified": sorted(r for r in (set(now) & set(before))
                           if now[r] != before[r]),
    }


def restore_source(snap: dict) -> tuple[bool, list[str]]:
    """Put every editable file back. Returns (ok, still-wrong)."""
    d = changed_since(snap)
    for rel in d["added"]:
        try:
            os.remove(os.path.join(ROOT, rel.replace("/", os.sep)))
        except OSError:
            pass
    dirty = set(snap["dirty"])
    for rel in d["modified"] + d["removed"]:
        if rel in dirty:
            src = os.path.join(snap["copy_dir"], rel.replace("/", os.sep))
            dst = os.path.join(ROOT, rel.replace("/", os.sep))
            if os.path.isfile(src):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
        else:
            _git("checkout", "--", rel)
    bad = [rel for rel, h in snap["hashes"].items() if _hash(rel) != h]
    return (not bad), bad


# --- agents -----------------------------------------------------------
def agent_cursor(prompt: str, workdir: str) -> dict:
    """Cursor, through the converter's existing SDK wrapper.

    Reuses cursor_assist's config, key resolution and Windows bridge
    patch rather than opening a second path to the same SDK -- a second
    copy is how the first one stops getting fixed.
    """
    sys.path.insert(0, os.path.join(ROOT, "tools", "ra_converter"))
    import cursor_assist as ca
    cfg = ca.load_config(None)
    if not cfg.api_key:
        return {"ok": False, "text": "",
                "error": "no Cursor API key (set CURSOR_API_KEY or apiKey in "
                         "tools/ra_converter/cursor_agent.json)"}
    cfg.cwd = cfg.cwd or ROOT
    try:
        res = ca._sdk_prompt(prompt, cfg)
    except Exception as e:                      # SDK missing, bridge, auth
        return {"ok": False, "text": "", "error": f"{type(e).__name__}: {e}"}
    return {"ok": True, "text": res.get("result", ""), "id": res.get("id", ""),
            "model": cfg.model}


def agent_file(prompt: str, workdir: str, path: str = "") -> dict:
    """Read an answer from disk. Used for --patch and for tests."""
    if not path or not os.path.isfile(path):
        return {"ok": False, "text": "", "error": f"no answer file at {path!r}"}
    return {"ok": True, "text": io.open(path, encoding="utf-8",
                                        errors="replace").read()}


# --- answer parsing ---------------------------------------------------
SECTIONS = ("DIAGNOSIS", "SIDE", "PATCH", "BLAST RADIUS", "EVIDENCE")


def extract_patch(text: str) -> str:
    """The unified diff out of an agent answer, fenced or not.

    Stops at the next known heading. Without that, an UNFENCED diff ran
    to the end of the answer and swallowed BLAST RADIUS and EVIDENCE into
    the patch -- prose handed to `git apply`. A heading line carries no
    diff prefix, so an unprefixed line matching one is never diff content.
    """
    lines = (text or "").replace("\r\n", "\n").split("\n")
    out: list[str] = []
    started = False
    for l in lines:
        if l.startswith("```"):
            if started and out:
                break
            continue
        if started and l.strip().rstrip(":").upper() in SECTIONS:
            break
        if l.startswith("diff --git ") or (not started and l.startswith("--- ")):
            started = True
        if started:
            out.append(l)
    # Drop trailing EMPTY lines only. rstrip() here was a real bug: a
    # context line for a blank source line is a single space, and
    # stripping it left a hunk one line shorter than its own @@ header
    # promised -- `git apply` then said "corrupt patch", which reads like
    # the agent produced nonsense.
    while out and out[-1] == "":
        out.pop()
    body = "\n".join(out)
    return body + "\n" if body else ""


def extract_section(text: str, name: str) -> str:
    """One section of the answer, up to the next known heading.

    The first version uppercased each line and then asked whether it was
    uppercase, which is true of any alphabetic line -- so every section
    ended at its own first line and `SIDE` always came back empty.
    Headings are matched against the known list instead of guessed from
    their case.
    """
    lines = (text or "").replace("\r\n", "\n").split("\n")
    want = name.strip().upper()
    body: list[str] = []
    on = False
    for l in lines:
        head = l.strip().rstrip(":").upper()
        if head in SECTIONS:
            if on:
                break
            on = head == want
            continue
        if on:
            body.append(l)
    return "\n".join(body).strip()


def apply_patch(patch_text: str, workdir: str, check_only: bool = False,
                before_hashes: dict[str, str | None] | None = None
                ) -> tuple[bool, str]:
    p = os.path.join(workdir, "candidate.diff")
    io.open(p, "w", encoding="utf-8", newline="\n").write(patch_text)
    rel_patch = os.path.relpath(p, ROOT).replace("\\", "/")
    before_hashes = before_hashes or {}

    # `git apply` resolves paths from the GIT ROOT, not from cwd, and this
    # project lives in a subdirectory. Without --directory it looked for
    # <gitroot>/tools/... , did not find it, printed "Skipped patch" and
    # EXITED 0. So the patch applied nothing, the ladder tested an
    # unchanged tree, and the proposal was blamed for the original failure.
    prefix = inv.repo_prefix().rstrip("/")
    args = ["apply", "--whitespace=nowarn"]
    if prefix:
        args += [f"--directory={prefix}"]
    if check_only:
        args.append("--check")
    rc, out = _git(*args, rel_patch)
    if rc != 0:
        return False, out
    if check_only:
        return True, out

    # Never trust the exit code on its own: see above. A patch that
    # changed nothing is a failure, whatever git said.
    targets = [fd.path for fd in inv.parse_diff(patch_text, inv.repo_prefix())
               if fd.path]
    if targets and all(_hash(t) == before_hashes.get(t) for t in targets):
        return False, ("git apply exited 0 but no target file changed"
                       + (f" ({out.strip()})" if out.strip() else "")
                       + ". The patch matched nothing -- check its paths.")
    return True, out


# --- the run ----------------------------------------------------------
def _note_stale_generated(result: dict, d: dict) -> None:
    """Say so when a rejected patch left its output in the generated tree.

    `restore_source` restores SOURCE. If the ladder got as far as
    `repro-convert`, the generated tree now holds output produced by a
    patch that was then rejected -- and the next `verify_all` measures
    THAT, so a failure caused by the discarded fix gets attributed to
    whatever is looked at next. Converts are deterministic, so
    reconverting from the restored source fixes it; the point is that
    nothing else would ever mention it.
    """
    total = int((d or {}).get("total") or 0)
    if not total:
        return
    suites = sorted(s for s in (d.get("by_suite") or {}) if s != "(shared)")
    result["generated_stale"] = {"files": total, "suites": suites}
    cmd = ("python tools/ra_converter/ra_converter.py --input "
           "tools/ra_converter/input/<suite>.xml --output . --package-root "
           "com.hi.api --clean --max-name-len 40 --no-cursor-assist "
           "--phase-specs")
    print(f"\nWARNING: the source was restored, but {total} generated file(s) "
          f"in the tree were produced by the REJECTED patch"
          + (f" ({', '.join(suites)})" if suites else "") + ".")
    print("         The next verify_all measures that tree, so reconvert "
          "before trusting it:")
    print(f"         {cmd}")


def rejection_feedback(result: dict) -> str:
    """What to tell the agent so a second attempt is not the first again."""
    bits = [f"outcome: {result.get('outcome')}",
            f"reason: {result.get('why', '')}".strip()]
    for v in result.get("violations", []) or []:
        bits.append(f"{v['verdict']} {v['rule']} ({v['path']}): {v['message']}")
    lad = result.get("ladder") or {}
    if lad.get("failed"):
        for f in lad["failed"]:
            bits.append(f"gate `{lad.get('at')}` failed: {f.get('command')}")
            bits += ["    " + l for l in (f.get("tail") or [])[-8:]]
    return "\n".join(b for b in bits if b)


def run(records: str, agent: str, patch_path: str, upto: str,
        include_prompt_policy: bool, check: str | None,
        branch: str, verbose: bool, mode: str = "propose",
        attempts: int = 1) -> dict:
    workdir = os.path.join(ROOT, WORK.replace("/", os.sep))
    os.makedirs(workdir, exist_ok=True)

    with open(records, encoding="utf-8") as fh:
        doc = json.load(fh)
    prompt, rec = tp.from_records(records, check)
    packet_rel = f"{WORK}/packet.txt"
    io.open(os.path.join(workdir, "packet.txt"), "w", encoding="utf-8",
            newline="\n").write(prompt)
    if rec.get("policy") == "prompt" and not include_prompt_policy:
        return {"outcome": "not-attempted", "check": rec["name"],
                "packet": packet_rel,
                "why": f"{rec['name']} is policy `prompt`: a fix there needs "
                       f"the ReadyAPI source cross-checked first, which is a "
                       f"human decision. The packet is written for review; "
                       f"pass --include-prompt-policy to attempt it anyway."}
    if agent == "dry-run":
        return {"outcome": "dry-run", "check": rec["name"],
                "packet": packet_rel, "packet_chars": len(prompt),
                "why": "no agent was called"}

    snap = snapshot_source(workdir)
    print(f"snapshot: {len(snap['hashes'])} source file(s) hashed, "
          f"{snap['copied']} dirty file(s) copied")

    # Attempt budget. The packet has always accepted `attempt`/`previous`
    # and nothing passed them, so a rejection was final even when it was
    # the kind an agent could act on ("you edited generated output"). Each
    # retry starts from the restored tree and carries the exact rejection.
    feedback = ""
    result: dict = {}
    for attempt in range(1, max(1, attempts) + 1):
        if attempt > 1:
            prompt = tp.build(rec, doc, attempt, feedback)
            io.open(os.path.join(workdir, f"packet-{attempt}.txt"), "w",
                    encoding="utf-8", newline="\n").write(prompt)
            print(f"\n-- attempt {attempt} of {attempts}")
        result = _attempt(prompt, agent, patch_path, workdir, rec, mode,
                          upto, verbose, snap, branch)
        result["attempt"] = attempt
        result["attempts_allowed"] = attempts
        if result.get("outcome") in ("proposed", "applied", "agent-failed",
                                     "not-attempted", "errored"):
            break
        if attempt < attempts:
            feedback = rejection_feedback(result)
            ok, bad = restore_source(snap)
            if not ok:
                result["unrestored"] = bad
                break
    return result


def _attempt(prompt: str, agent: str, patch_path: str, workdir: str,
             rec: dict, mode: str, upto: str, verbose: bool,
             snap: dict, branch: str) -> dict:
    if agent == "cursor":
        ans = agent_cursor(prompt, workdir)
    elif agent == "file":
        ans = agent_file(prompt, workdir, patch_path)
    else:
        return {"outcome": "agent-failed", "check": rec["name"],
                "why": f"unknown agent {agent!r}"}
    if not ans["ok"]:
        return {"outcome": "agent-failed", "check": rec["name"],
                "why": ans.get("error", "")}

    io.open(os.path.join(workdir, "answer.txt"), "w", encoding="utf-8",
            newline="\n").write(ans["text"])
    # Whichever convention the patch arrived in, make it ROOT-relative
    # before anything looks at it -- the invariants and `git apply` must
    # not disagree about what a path means.
    patch = inv.normalize_patch(extract_patch(ans["text"]), inv.repo_prefix())
    result = {
        "check": rec["name"],
        "kind": rec.get("kind", "?"),
        "policy": rec.get("policy", "?"),
        "fingerprint": rec.get("salient_fingerprint", ""),
        "mode": mode,
        "diagnosis": extract_section(ans["text"], "DIAGNOSIS"),
        "side": extract_section(ans["text"], "SIDE"),
        "predicted_blast": extract_section(ans["text"], "BLAST RADIUS"),
    }
    if not patch:
        result.update(outcome="no-patch",
                      why="the answer carried no unified diff")
        return result

    # 4. invariants, on the diff, before anything runs it.
    files = inv.parse_diff(patch, inv.repo_prefix())
    violations = inv.validate(files)
    result["files"] = [f.path for f in files]
    result["violations"] = [{"rule": v.rule, "verdict": v.verdict,
                             "path": v.path, "message": v.message}
                            for v in violations]
    if any(v.verdict == inv.REJECT for v in violations):
        result.update(outcome="rejected",
                      why="an invariant was violated; nothing was applied")
        return result
    held = [v for v in violations if v.verdict in (inv.NEEDS_FIXTURE, inv.NEEDS_GATE)]

    patch_targets = [f.path for f in files if f.path]
    pre = {t: _hash(t) for t in patch_targets}
    ok, out = apply_patch(patch, workdir, check_only=True, before_hashes=pre)
    if not ok:
        result.update(outcome="will-not-apply", why=out.strip()[:600])
        return result

    before = mf.snapshot()
    applied, out = apply_patch(patch, workdir, before_hashes=pre)
    if not applied:
        result.update(outcome="will-not-apply", why=out.strip()[:600])
        return result
    print(f"applied: {len(files)} file(s)")

    # From here the tree is modified, so every exit path has to restore.
    # An exception during the ladder must not leave the patch applied: the
    # next run would measure a tree it did not produce.
    try:
        climb = L.climb(list((rec.get("locate") or {}).get("suites") or []),
                        [rec["name"]], upto, verbose=verbose)
    except BaseException as e:
        restored, bad = restore_source(snap)
        result.update(outcome="errored", why=f"{type(e).__name__}: {e}",
                      restored=restored, unrestored=bad)
        return result

    result["ladder"] = {"outcome": climb["outcome"], "at": climb["at"],
                        "seconds": climb["seconds"]}
    if climb["outcome"] == "failed":
        result["ladder"]["failed"] = climb["rungs"][-1].get("failed")
    d = mf.compare(before, mf.snapshot())
    result["blast_radius"] = {"total": d["total"], "by_suite": d["by_suite"]}
    good = climb["outcome"] in ("passed", "stopped")

    if not good:
        restored, bad = restore_source(snap)
        result.update(outcome="failed-gate",
                      why=f"the ladder stopped at '{climb['at']}'",
                      restored=restored, unrestored=bad)
        _note_stale_generated(result, d)
        return result

    if held:
        restored, bad = restore_source(snap)
        _note_stale_generated(result, d)
        result.update(outcome="held",
                      why="; ".join(v.rule for v in held),
                      restored=restored, unrestored=bad)
        return result

    # Autonomy is decided here, AFTER the gate and the measurement --
    # never from the agent's own account of what it did.
    policy = lg.load_policy()
    rows = lg.load()
    may, why = lg.eligible(rec.get("kind", "?"), policy, rows)
    within, blast_why = lg.blast_within(policy, result.get("blast_radius"))
    result["autonomy"] = {"requested": mode, "eligible": may, "why": why,
                          "blast_ok": within, "blast_why": blast_why}

    if mode == "auto" and may and within:
        br = branch or f"autofix/{rec['name']}-{rec.get('salient_fingerprint', '')[:8]}"
        rc, out = _git("checkout", "-b", br)
        if rc != 0:
            result.update(outcome="proposed", branch="",
                          why=f"kept as a proposal: could not branch ({out.strip()[:200]})")
            return result
        _git("add", "--", *[f.path for f in files if f.path])
        msg = "\n".join((
            f"autofix: {rec['name']}",
            "",
            result.get("diagnosis", "").strip()[:1200],
            "",
            f"Applied without review under tools/autofix/autonomy.json "
            f"({why}).",
            f"Ladder: {result['ladder']['outcome']} at "
            f"{result['ladder']['at']}.",
            f"Blast radius: {blast_why}.",
            "",
        ))
        mp = os.path.join(workdir, "commitmsg.txt")
        io.open(mp, "w", encoding="utf-8", newline="\n").write(msg)
        rc, out = _git("commit", "-F", os.path.relpath(mp, ROOT).replace("\\", "/"))
        result["branch"] = br
        result["outcome"] = "applied" if rc == 0 else "proposed"
        result["note"] = (f"committed on {br}. NOT pushed, and the working "
                          f"branch was not touched." if rc == 0
                          else f"left on {br}, uncommitted: {out.strip()[:200]}")
        return result

    if mode == "auto":
        result["why"] = ("kept as a proposal: "
                         + (why if not may else blast_why))
    if branch and mode != "auto":
        rc, _out = _git("checkout", "-b", branch)
        if rc == 0:
            result["branch"] = branch
            result["note"] = ("left on this branch, uncommitted. Nothing was "
                              "committed and nothing was pushed.")
    result["outcome"] = "proposed"
    return result


def write_report(result: dict, path: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    L_ = result.get("ladder") or {}
    b = result.get("blast_radius") or {}
    lines = [
        "# autofix proposal",
        "",
        f"- check: `{result.get('check', '?')}`",
        f"- outcome: **{result.get('outcome', '?')}**",
        f"- why: {result.get('why', '')}" if result.get("why") else "",
        f"- side claimed: {result.get('side', '-')}",
        f"- ladder: {L_.get('outcome', '-')} at `{L_.get('at', '-')}` "
        f"({L_.get('seconds', '-')}s)" if L_ else "",
        "",
        "## diagnosis", result.get("diagnosis", "(none)"),
        "",
        "## blast radius",
        f"predicted: {result.get('predicted_blast', '(not stated)')}",
        f"measured: {b.get('total', '-')} file(s) "
        f"across {len(b.get('by_suite', {}))} suite(s)",
    ]
    for suite, c in (b.get("by_suite") or {}).items():
        lines.append(f"  - {suite}: " + ", ".join(f"{v} {k}" for k, v in c.items() if v))
    if result.get("violations"):
        lines += ["", "## invariants"]
        for v in result["violations"]:
            lines.append(f"- **{v['verdict']}** `{v['rule']}` {v['path']}")
    if result.get("files"):
        lines += ["", "## files in the patch"] + [f"- `{p}`" for p in result["files"]]
    st = result.get("generated_stale")
    if st:
        suites = ", ".join(st.get("suites") or []) or "(shared files)"
        lines += ["", "## generated tree is stale",
                  f"{st['files']} generated file(s) in the tree were produced "
                  f"by the REJECTED patch, in: {suites}.",
                  "The next verify_all measures that tree, so reconvert those "
                  "suites before trusting it. Converts are deterministic, so "
                  "reconverting from the restored source is sufficient."]
    if result.get("unrestored"):
        lines += ["", "## WARNING", "these files were NOT restored:"] + \
                 [f"- `{p}`" for p in result["unrestored"]]
    body = "\n".join(l for l in lines if l != "") + "\n"
    io.open(path, "w", encoding="utf-8", newline="\n").write(body)
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--records", required=True,
                    help="from `verify_all.py --json PATH`")
    ap.add_argument("--check", help="which failing check (default: cheapest)")
    ap.add_argument("--agent", default="dry-run",
                    choices=("dry-run", "cursor", "file"),
                    help="dry-run writes the packet and stops")
    ap.add_argument("--patch", default="",
                    help="with --agent file: the answer/patch to validate")
    ap.add_argument("--upto", default="static", choices=L.RUNG_NAMES,
                    help="highest ladder rung (default: static -- the "
                         "authoritative rungs cost ~40min)")
    ap.add_argument("--include-prompt-policy", action="store_true",
                    help="also attempt checks whose policy is `prompt`. Those "
                         "need the ReadyAPI XML checked first")
    ap.add_argument("--mode", default="propose",
                    choices=("propose", "auto"),
                    help="auto applies and commits on a new branch, but only "
                         "when tools/autofix/autonomy.json and the ledger "
                         "allow it. It never pushes and never touches the "
                         "working branch")
    ap.add_argument("--attempts", type=int, default=1,
                    help="retry budget. Each retry starts from the restored "
                         "tree and carries the exact rejection. Pointless "
                         "with --agent file, whose answer never changes")
    ap.add_argument("--no-ledger", action="store_true",
                    help="do not record this run (for experiments)")
    ap.add_argument("--branch", default="",
                    help="on success, leave the work on this new branch")
    ap.add_argument("--report", default="target/autofix-report.md")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    if args.agent == "file" and not args.patch:
        print("--agent file needs --patch PATH")
        return 2

    t0 = time.time()
    result = run(args.records, args.agent, args.patch, args.upto,
                 args.include_prompt_policy, args.check, args.branch,
                 args.verbose, args.mode, args.attempts)
    result["seconds"] = round(time.time() - t0, 1)
    # Record every run, including the refusals. A ledger that only holds
    # the successes cannot measure reliability, which is the one thing it
    # exists for.
    if not args.no_ledger and result.get("outcome") != "dry-run":
        lg.append({k: result.get(k) for k in
                   ("check", "kind", "policy", "mode", "fingerprint",
                    "outcome", "side", "seconds", "ladder", "blast_radius",
                    "autonomy", "branch")})
    path = write_report(result, args.report)
    print(f"\n{result['outcome'].upper()}: {result.get('why', '')}")
    print(f"report: {path}")
    if result.get("unrestored"):
        print("WARNING: some files were not restored -- see the report.")
        return 2
    return 0 if result["outcome"] in ("proposed", "dry-run", "not-attempted") else 1


if __name__ == "__main__":
    raise SystemExit(main())
