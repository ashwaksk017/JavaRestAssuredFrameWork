"""After a convert: does it compile? If not, Cursor fixes it before anyone reviews it.

    python tools/agent/convertfix.py --job J              # compile; Cursor fixes what fails
    python tools/agent/convertfix.py --job J --no-cursor  # compile and say, nothing more

Asked for (2026-10-21): people were handed converted suites that did not
compile. Now the Convert tab runs this when a convert ends:

1. `mvn test-compile` (offline first).
2. It compiles: said, and that is the end -- the suites are ready.
3. It does not: each error is placed. An error in a converted suite's own
   files (imported/<suite>/, support/<suite>/, templates/<suite>/,
   clients/<Suite>Client.java) is that suite's; any other file is shared
   framework code, which is said and left to a person.
4. For each suite with errors, Cursor is asked to make it compile -- the
   Migration tab's "converted suite" scope, in a job of its own
   (<job>-fix-<suite>): Cursor writes, the tree is compiled, Cursor is
   asked again while the errors change, and the change is held for
   review ONLY once that suite's files compile (`must_fix`: they are not
   "already broken before", whatever the baseline). A suite that still
   does not compile ends `verify-failed`, not in review.

The conversation with Cursor of every fix is also written to the convert
job's Cursor log (WORKBENCH_CURSOR_LOG_MIRROR), which the Convert tab shows.
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

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name: str, path: str):
    import importlib.util
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return mod


diag = _load("workbench_diag", os.path.join(os.path.dirname(HERE), "diag.py"))
loop = _load("convertfix_loop", os.path.join(HERE, "loop.py"))
ROOT = loop.ROOT
RESULT = "convert-fix.json"
COMPILE = ["mvn", "-q", "-DskipTests", "test-compile"]

# Where a converted suite's own files are (policy.json, scope "converted").
_SUITE_RX = (
    re.compile(r"^src/test/java/com/hi/api/tests/imported/([A-Za-z0-9_]+)/"),
    re.compile(r"^src/main/java/com/hi/api/support/([A-Za-z0-9_]+)/"),
    re.compile(r"^src/main/resources/templates/([A-Za-z0-9_]+)/"),
    re.compile(r"^src/main/java/com/hi/api/rest/clients/([A-Za-z0-9_]+)Client\.java$"),
)
_JOB_RX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def say(msg: str = "") -> None:
    print(msg, flush=True)


def suite_of(rel: str) -> str:
    """The converted suite a file belongs to, or "" for shared code."""
    rel = rel.replace("\\", "/")
    for rx in _SUITE_RX:
        m = rx.match(rel)
        if m:
            return m.group(1).lower()
    return ""


def fix_job(job: str, suite: str) -> str:
    name = f"{job}-fix-{suite}"
    if not _JOB_RX.match(name):
        name = f"{job[:40]}-fix-{suite[:16]}"
    return name


def compile_tree(root: str, runner=None) -> tuple:
    """(rc, output) of the project's test-compile, offline first."""
    exe = shutil.which(COMPILE[0])
    if runner is None and not exe:
        return 127, "mvn is not on PATH"
    run = runner or (lambda argv: subprocess.run(
        argv, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **loop.NO_WINDOW))
    r, _how = loop.run_build(run, exe or COMPILE[0], COMPILE)
    out = r.stdout.decode("utf-8", "replace") if isinstance(r.stdout, bytes) else (r.stdout or "")
    return r.returncode, out


def brief_for(suite: str, errors: list) -> str:
    return "\n".join([
        f"# Make the converted suite `{suite}` compile",
        "",
        "The ReadyAPI convert wrote this suite's Java, and the project does not",
        "compile. These are the errors in the suite's own files:",
        "",
        *[f"- {e}" for e in errors],
        "",
        "Fix the generated Java of this suite so that it compiles. Keep what",
        "each test does: the same requests, the same data, the same checks.",
        "Do not delete a test, a step or an assertion to make the errors go",
        "away; do not change shared framework code (support/ImportedScenario,",
        "rest/utilities, ...). When a fix needs a framework method that does",
        "not exist, say so in your reply instead of inventing one.",
    ])


def write_result(job_dir: str, result: dict) -> None:
    os.makedirs(job_dir, exist_ok=True)
    with io.open(os.path.join(job_dir, RESULT), "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=1)


def run(job: str, root: str = "", use_cursor: bool = True, runner=None, generate=None) -> int:
    root = root or ROOT
    job_dir = loop.intake.job_dir(job, root)
    os.makedirs(job_dir, exist_ok=True)
    result = {"kind": "convert-fix", "job": job, "compiles": False, "suites": {}, "shared": []}
    diag.step("Compiling the converted tree (mvn test-compile)")
    say(" ..  compiling what the convert wrote (mvn test-compile, offline first)")
    rc, out = compile_tree(root, runner)
    if rc == 0:
        result["compiles"] = True
        write_result(job_dir, result)
        say(" ok  the converted tree compiles: ready to review and run")
        return 0
    errors = loop.compile_errors(out)
    files = sorted(loop.compile_error_files(out))
    if not files:
        say("FAIL the compile failed without naming a file. The end of its output:")
        for line in out.strip().splitlines()[-25:]:
            say("    " + line[:300])
        write_result(job_dir, dict(result, why="the compile named no file"))
        return 1
    by_suite, shared = {}, []
    for rel in files:
        s = suite_of(rel)
        (by_suite.setdefault(s, []) if s else shared).append(rel)
    result["shared"] = shared
    say(f"WARN the converted tree does not compile: {len(files)} file(s) with errors"
        + (f", in suite(s) {', '.join(sorted(by_suite))}" if by_suite else "")
        + (f"; {len(shared)} shared framework file(s)" if shared else ""))
    for e in errors[:40]:
        say("    " + e[:300])
    if shared:
        say("WARN errors in shared framework files are not a converted suite's to fix: "
            + ", ".join(shared[:8]) + " -- a person fixes those (or Ask Cursor, scope "
            "\"the converter\", on the Migration tab)")
    if not by_suite:
        write_result(job_dir, result)
        return 1
    if not use_cursor:
        say("NOTE Cursor is off for this convert: nothing was fixed. Turn Cursor assist on, "
            "or use Ask Cursor on the Migration tab with the suite.")
        result["suites"] = {s: {"state": "not fixed (Cursor off)", "files": f} for s, f in by_suite.items()}
        write_result(job_dir, result)
        return 1
    policy = loop.load_policy()
    mirror = os.environ.get("WORKBENCH_CURSOR_LOG") or os.path.join(job_dir, "migration-cursor.log")
    os.environ["WORKBENCH_CURSOR_LOG_MIRROR"] = mirror
    gen = generate or loop.cmd_generate
    all_ok = not shared
    for suite, own in sorted(by_suite.items()):
        sub = fix_job(job, suite)
        sub_dir = loop.intake.job_dir(sub, root)
        os.makedirs(sub_dir, exist_ok=True)
        state = loop.read_review(sub_dir).get("state") or "none"
        if state in ("pending-review", "verify-failed", "generating"):
            say(f"NOTE {suite}: job {sub} already holds a fix ({state}); review or discard it on the "
                f"Commit and push tab first -- not asked again")
            result["suites"][suite] = {"job": sub, "state": state, "files": own}
            all_ok = all_ok and state == "pending-review"
            continue
        mine = [e for e in errors if any(e.replace("\\", "/").startswith(f) for f in own)] or errors[:40]
        text = brief_for(suite, mine)
        for name in ("brief.md", "plan.md"):
            with io.open(os.path.join(sub_dir, name), "w", encoding="utf-8") as fh:
                fh.write(text + "\n")
        diag.step(f"Cursor is making suite {suite} compile")
        say(f"==> {suite}: Cursor makes it compile (job {sub}); the change is held for review only "
            f"once these {len(own)} file(s) compile")
        rc_g = gen(sub_dir, root, policy, "converted", suite, must_fix=own)
        state = loop.read_review(sub_dir).get("state") or "none"
        result["suites"][suite] = {"job": sub, "state": state, "files": own, "rc": rc_g}
        if state == "pending-review":
            say(f" ok  {suite} compiles: the fix is waiting for review (job {sub}, Commit and push tab)")
        else:
            say(f"FAIL {suite} still does not compile after Cursor's attempts ({state}); "
                f"job {sub} holds what it tried -- see its Cursor log")
            all_ok = False
    write_result(job_dir, result)
    return 0 if all_ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--job", required=True)
    ap.add_argument("--no-cursor", action="store_true")
    a = ap.parse_args(argv)
    if not _JOB_RX.match(a.job):
        say(f"REFUSED {a.job!r} is not a job name")
        return 2
    return run(a.job, use_cursor=not a.no_cursor)


if __name__ == "__main__":
    sys.exit(diag.run("convertfix", main))
