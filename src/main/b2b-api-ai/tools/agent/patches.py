"""Approved changes to converted Java, kept so they survive a reconvert.

    python tools/agent/patches.py list
    python tools/agent/patches.py reapply --suite amexbackbook
    python tools/agent/patches.py forget  --suite amexbackbook --id 001-job1

The converter rewrites a suite's generated files on every convert. A
change Cursor made to them and a person approved would be gone -- with
nothing saying it went. So an approved change is stored here, per suite,
and put back after each convert of that suite.

WHAT IS STORED, AND WHY NOT A DIFF
----------------------------------
For every file the change touched: the file as the converter wrote it
(`base/`) and the file as approved (`after/`). Re-applying is a three-way
merge of

    base   what the converter wrote when the change was made
    after  what was approved
    now    what the converter has just written

A unified diff only applies while the surrounding lines still match. The
generated classes renumber (`spec81` becomes `spec80` when a phase moves),
so a diff taken last week fails on text that has nothing to do with the
change. A merge keeps the approved edit and the converter's new output
wherever they do not overlap.

WHEN THEY DO OVERLAP
--------------------
The file is LEFT as the converter wrote it, and the conflict is reported
with the path of the approved version. Nothing is half-applied: a merged
file with conflict markers would not compile, and one silently resolved
either way would be wrong without saying so.

ONE SUITE AT A TIME
-------------------
A stored change belongs to one suite and can only name files of that
suite. `reapply` refuses any other path, so a store entry cannot be used
to write into another suite's classes.

The store is `.agent-patches/` at the repository root and is gitignored:
it holds generated code, which this public repository does not track.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
STORE_DIR = ".agent-patches"
_SUITE_RX = re.compile(r"^[a-z0-9_]{1,80}$")
# NNN-<job>. The digits are required: without them `..` matches, and
# `forget --id ..` would remove the whole store.
_ID_RX = re.compile(r"^\d{3,}-[A-Za-z0-9_][A-Za-z0-9._-]{0,100}$")

# The only places a stored change may write, for suite {suite}.
SUITE_ROOTS = (
    "src/main/java/com/hi/api/support/{suite}/",
    "src/test/java/com/hi/api/tests/imported/{suite}/",
    "src/main/resources/templates/{suite}/",
    "src/test/resources/csv/{suite}/",
    "src/main/java/com/hi/api/rest/clients/{Suite}Client.java",
)


def suite_roots(suite: str) -> list:
    cap = suite[:1].upper() + suite[1:]
    return [r.replace("{suite}", suite).replace("{Suite}", cap) for r in SUITE_ROOTS]


def belongs(rel: str, suite: str) -> bool:
    rel = rel.replace("\\", "/")
    if ".." in rel.split("/") or rel.startswith("/") or ":" in rel:
        return False
    return any(rel == r or (r.endswith("/") and rel.startswith(r))
               for r in suite_roots(suite))


def store_root(root: str = "") -> str:
    return os.path.join(root or ROOT, STORE_DIR)


def _suite_dir(root: str, suite: str) -> str:
    if not _SUITE_RX.match(suite or ""):
        raise ValueError(f"unusable suite name {suite!r}")
    return os.path.join(store_root(root), suite)


def _read(path: str):
    try:
        with io.open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return None


def _write(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "wb") as fh:
        fh.write(data)


def entries(root: str, suite: str) -> list:
    """Stored changes for a suite, oldest first: [{id, dir, manifest}]."""
    base = _suite_dir(root, suite)
    out = []
    if not os.path.isdir(base):
        return out
    for name in sorted(os.listdir(base)):
        d = os.path.join(base, name)
        try:
            with io.open(os.path.join(d, "manifest.json"), encoding="utf-8") as fh:
                out.append({"id": name, "dir": d, "manifest": json.load(fh)})
        except (OSError, ValueError):
            continue
    return out


def suites(root: str = "") -> list:
    base = store_root(root)
    if not os.path.isdir(base):
        return []
    return sorted(n for n in os.listdir(base)
                  if _SUITE_RX.match(n) and os.path.isdir(os.path.join(base, n)))


def save(root: str, suite: str, job: str, created, modified, base_dir: str) -> str:
    """Store an approved change. `base_dir` holds the files as they were
    before the agent ran (same relative paths). Returns the entry id."""
    bad = [p for p in list(created) + list(modified) if not belongs(p, suite)]
    if bad:
        raise ValueError(f"not files of suite {suite}: {', '.join(bad[:5])}")
    sdir = _suite_dir(root, suite)
    # Highest number in use plus one -- not a count: after `forget 001`
    # a count would hand out 002 again, and order is what re-apply uses.
    used = [int(m.group(1)) for m in
            (re.match(r"^(\d+)-", n) for n in
             (os.listdir(sdir) if os.path.isdir(sdir) else [])) if m]
    seq = 1 + max(used, default=0)
    entry = f"{seq:03d}-{job}"
    if not _ID_RX.match(entry):
        raise ValueError(f"unusable entry id {entry!r}")
    d = os.path.join(sdir, entry)
    for rel in modified:
        before = _read(os.path.join(base_dir, rel))
        after = _read(os.path.join(root, rel))
        if before is None or after is None:
            raise ValueError(f"cannot store {rel}: its before or after copy is missing")
        _write(os.path.join(d, "base", rel), before)
        _write(os.path.join(d, "after", rel), after)
    for rel in created:
        after = _read(os.path.join(root, rel))
        if after is None:
            raise ValueError(f"cannot store {rel}: the file is gone")
        _write(os.path.join(d, "after", rel), after)
    manifest = {"suite": suite, "job": job, "created": sorted(created),
                "modified": sorted(modified),
                "saved": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    _write(os.path.join(d, "manifest.json"),
           json.dumps(manifest, indent=2).encode("utf-8"))
    return entry


def _same(a: bytes, b: bytes) -> bool:
    """Equal apart from line endings: a convert on Windows writes CRLF."""
    return a is not None and b is not None and \
        a.replace(b"\r\n", b"\n") == b.replace(b"\r\n", b"\n")


def merge3(base: bytes, after: bytes, now: bytes):
    """(merged bytes, clean?) -- `git merge-file` on three versions.

    Line endings are taken out of the comparison first. A convert on
    Windows writes CRLF and an editor may save LF; left in, every line
    differs on one side and every merge is a conflict. The result gets
    the ending the converter's current file uses.
    """
    crlf = b"\r\n" in now
    lf = lambda b: b.replace(b"\r\n", b"\n")
    tmp = tempfile.mkdtemp(prefix="agentmerge_")
    try:
        paths = {}
        for name, data in (("now", now), ("base", base), ("after", after)):
            paths[name] = os.path.join(tmp, name)
            _write(paths[name], lf(data))
        try:
            r = subprocess.run(
                ["git", "merge-file", "-p", "--diff3", paths["now"], paths["base"],
                 paths["after"]], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError:
            return now, False               # no git: nothing can be merged
        merged = r.stdout.replace(b"\n", b"\r\n") if crlf else r.stdout
        return merged, r.returncode == 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def reapply(root: str, suite: str, only: str = "") -> dict:
    """Put every stored change for `suite` back, oldest first.

    {applied: [...], already: [...], conflicts: [...], refused: [...]} --
    each item is "<entry id>: <path>". A conflict leaves the file exactly
    as the converter wrote it.
    """
    out = {"applied": [], "already": [], "conflicts": [], "refused": []}
    # A file one stored change could not be applied to. Later changes to
    # the same file were made ON TOP of that one, so merging them alone
    # would put half a sequence into the file.
    blocked = set()
    for e in entries(root, suite):
        if only and e["id"] != only:
            continue
        m = e["manifest"]
        for rel in m.get("created", []) + m.get("modified", []):
            tag = f"{e['id']}: {rel}"
            if not belongs(rel, suite):
                out["refused"].append(tag + "  (not a file of this suite)")
                continue
            if rel in blocked:
                out["conflicts"].append(
                    tag + "  (not applied: an earlier stored change to this "
                          "file did not apply, and this one builds on it)")
                continue
            before_count = len(out["conflicts"])
            _apply_one(root, suite, e, rel, tag, out)
            if len(out["conflicts"]) > before_count:
                blocked.add(rel)
    return out


def _apply_one(root: str, suite: str, e: dict, rel: str, tag: str, out: dict) -> None:
    """Put ONE stored file back, or record why not."""
    m = e["manifest"]
    after = _read(os.path.join(e["dir"], "after", rel))
    if after is None:
        out["refused"].append(tag + "  (the stored copy is missing)")
        return
    target = os.path.join(root, rel)
    now = _read(target)
    if now is not None and _same(now, after):
        out["already"].append(tag)
        return
    if rel in m.get("created", []):
        if now is None:
            _write(target, after)
            out["applied"].append(tag)
        else:
            out["conflicts"].append(
                tag + "  (the converter now writes a file of this name)")
        return
    base = _read(os.path.join(e["dir"], "base", rel))
    if now is None:
        out["conflicts"].append(tag + "  (the converter no longer writes this file)")
    elif base is None:
        out["conflicts"].append(tag + "  (no stored base to merge from)")
    elif _same(now, base):
        _write(target, after)
        out["applied"].append(tag)
    else:
        merged, clean = merge3(base, after, now)
        if clean:
            _write(target, merged)
            out["applied"].append(tag + "  (merged with the converter's new output)")
        else:
            approved = os.path.join(STORE_DIR, suite, e["id"], "after", rel).replace("\\", "/")
            out["conflicts"].append(
                tag + "  (overlaps what the converter changed; the approved "
                      "version is " + approved + ")")


def report(suite: str, result: dict) -> str:
    lines = []
    n = sum(len(result[k]) for k in result)
    if not n:
        return ""
    lines.append(f"[agent-patches] {suite}: {len(result['applied'])} re-applied, "
                 f"{len(result['already'])} already in place, "
                 f"{len(result['conflicts'])} CONFLICT, "
                 f"{len(result['refused'])} refused")
    for kind, label in (("applied", "re-applied"), ("conflicts", "CONFLICT -- left as converted"),
                        ("refused", "REFUSED")):
        for item in result[kind]:
            lines.append(f"[agent-patches]   {label}: {item}")
    if result["conflicts"]:
        lines.append("[agent-patches]   A conflicting file was NOT changed. Run "
                     "the workbench job again for that suite, or copy the "
                     "approved version by hand.")
    return "\n".join(lines)


def reapply_after_convert(root: str, suite_names) -> int:
    """Called by the converter. Never raises; returns the conflict count."""
    conflicts = 0
    for suite in suite_names:
        try:
            if not _SUITE_RX.match(suite or "") or not entries(root, suite):
                continue
            result = reapply(root, suite)
            text = report(suite, result)
            if text:
                print(text, flush=True)
            conflicts += len(result["conflicts"]) + len(result["refused"])
        except Exception as e:                           # noqa: BLE001
            print(f"[agent-patches] {suite}: could not re-apply stored "
                  f"changes ({e}). The converted files are as the converter "
                  f"wrote them.", flush=True)
            conflicts += 1
    return conflicts


def forget(root: str, suite: str, entry: str) -> bool:
    if not _ID_RX.match(entry or ""):
        raise ValueError(f"unusable entry id {entry!r}")
    d = os.path.join(_suite_dir(root, suite), entry)
    if not os.path.isdir(d):
        return False
    shutil.rmtree(d)
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=("list", "reapply", "forget"))
    ap.add_argument("--suite", default="")
    ap.add_argument("--id", default="")
    args = ap.parse_args(argv)
    if args.command == "list":
        names = [args.suite] if args.suite else suites(ROOT)
        if not names:
            print("no stored changes")
        for s in names:
            for e in entries(ROOT, s):
                m = e["manifest"]
                print(f"{s}  {e['id']}  saved {m.get('saved', '?')}  "
                      f"{len(m.get('created', []))} new, "
                      f"{len(m.get('modified', []))} modified")
                for rel in m.get("created", []) + m.get("modified", []):
                    print(f"    {rel}")
        return 0
    if not args.suite:
        print("--suite is required")
        return 2
    try:
        if args.command == "forget":
            ok = forget(ROOT, args.suite, args.id)
            print("forgotten" if ok else "no such entry")
            return 0 if ok else 1
        if not entries(ROOT, args.suite):
            print(f"no stored changes for suite {args.suite}")
            return 0
        result = reapply(ROOT, args.suite, args.id)
        print(report(args.suite, result) or "nothing to do")
        return 1 if result["conflicts"] or result["refused"] else 0
    except ValueError as e:
        print(f"FAIL {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
