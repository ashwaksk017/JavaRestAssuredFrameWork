"""The checks a change to the workbench itself must pass before a person
reviews it (the "workbench" scope of tools/agent/policy.json).

    python tools/agent/check_workbench.py            # all of them
    python tools/agent/check_workbench.py --quick    # without the page checks

1. Every Python file of tools/agent compiles.
2. ui/app.js parses (node --check), when node is on PATH.
3. The workbench's quick unit tests pass (seconds each).
4. The page checks (check_ui.py: a headless browser against the real
   server) pass, when a browser is found -- a few minutes. A page that has
   not finished drawing when it is read fails a check for no reason (seen
   four times in ten runs, 2026-10-30), so a failed run of the page checks
   is run ONCE more: passing the second time passes, and the first run's
   failures are said as timing. Failing twice fails.
5. What the change touched gets its own tests (2026-10-30):
   - loop.py or cursor_call.py -- the AI Agent's own rules: test_loop.py
     (about three minutes, in parallel processes);
   - a test file changed or added: that test;
   - a Python file outside tools/agent (tools/jira, the converter...):
     its test_<name>.py beside it, else the tests beside it that name it.
   What changed is what git sees as changed or new under the project.
Exit 0 when everything that could run passed.
"""
from __future__ import annotations

import os
import py_compile
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
QUICK_TESTS = ("test_jobs.py", "test_casereview.py", "test_suitemap.py", "test_intake.py",
               "test_design.py", "test_mock.py", "test_xraytests.py", "test_xrayimport.py",
               "test_storycheck.py", "test_repo.py", "test_fixbrief.py", "test_execution.py",
               "test_settings.py", "test_xraypublish.py", "test_workflow.py", "test_runlog.py",
               "test_xraytrace.py", "test_devcode.py", "test_suitesuggest.py", "test_mvnrun.py",
               "test_partest.py", "test_convertfix.py", "test_ask.py", "test_runask.py",
               "test_storykeys.py", "test_projects.py",
               "test_flowcatalog.py", "test_safe_output.py", "test_release.py", "test_cursorignore.py",
               "test_refexamples.py", "test_check_workbench.py", "test_screens.py")


PROJECT = os.path.dirname(os.path.dirname(HERE))
RULE_FILES = ("tools/agent/loop.py", "tools/agent/cursor_call.py")


def _run(argv, timeout=900) -> tuple:
    # A test run: its throwaway jobs stay out of the real run log and
    # activity.log (diag._throwaway).
    env = dict(os.environ, WORKBENCH_TEST_RUN="1")
    try:
        r = subprocess.run(argv, cwd=PROJECT, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=timeout, env=env)
        return r.returncode, r.stdout.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"


def changed_files(runner=None) -> list:
    """The project's files git sees as changed or new, relative to the project
    (forward slashes). [] when git cannot say -- then only the fixed checks run."""
    run = runner or _run
    out = []
    for argv in (["git", "diff", "--name-only", "--relative", "HEAD"],
                 ["git", "ls-files", "--others", "--exclude-standard"]):
        rc, text = run(argv, 60)
        if rc == 0:
            out += [l.strip().replace("\\", "/") for l in text.splitlines() if l.strip()]
    return sorted(set(out))


def tests_for(changed: list, project: str = PROJECT) -> list:
    """The extra tests what changed calls for: [(test path relative to the
    project, why)]. The fixed list (QUICK_TESTS) is not repeated."""
    out = []

    def add(rel, why):
        if rel not in [t for t, _w in out]:
            out.append((rel, why))
    if any(p in RULE_FILES for p in changed):
        add("tools/agent/test_loop.py", "the AI Agent's own rules changed (loop.py / cursor_call.py)")
    for p in changed:
        if not p.endswith(".py") or not os.path.isfile(os.path.join(project, p)):
            continue
        folder, name = os.path.split(p)
        if name.startswith("test_"):
            if not (folder == "tools/agent" and name in QUICK_TESTS):
                add(p, "a test that was changed or added")
            continue
        if folder == "tools/agent":
            continue                                     # the fixed list covers tools/agent
        stem = name[:-3]
        own = f"{folder}/test_{stem}.py" if folder else f"test_{stem}.py"
        if os.path.isfile(os.path.join(project, own)):
            add(own, f"{p} changed")
            continue
        here = os.path.join(project, folder)
        for t in sorted(os.listdir(here)) if os.path.isdir(here) else []:
            if t.startswith("test_") and t.endswith(".py"):
                try:
                    with open(os.path.join(here, t), encoding="utf-8", errors="replace") as fh:
                        named = stem in fh.read()
                except OSError:
                    named = False
                if named:
                    add(f"{folder}/{t}" if folder else t, f"{p} changed (it names {stem})")
    return out


def _page_summary(out: str) -> list:
    return [l for l in out.splitlines() if "checks passed" in l or l.startswith("FAIL")
            or l.startswith("Traceback") or "Error:" in l]


def page_checks(runner=None) -> str:
    """The page checks, once more when they fail: "" when they passed, the
    name of what failed when they failed twice."""
    run = runner or _run
    first_rc, first = run([sys.executable, "-B", os.path.join(HERE, "check_ui.py")], 1200)
    if first_rc == 2 and "nothing was checked" in first:
        print("skip the page checks: no Edge or Chrome was found")
        return ""
    if first_rc == 0:
        print("ok  the page checks: " + ("; ".join(_page_summary(first)[-6:]) or first[-300:]))
        return ""
    print("..  the page checks failed once: " + "; ".join(_page_summary(first)[-6:])[:1500]
          + "\n..   run again: a page read before it finished drawing fails for no reason")
    rc, out = run([sys.executable, "-B", os.path.join(HERE, "check_ui.py")], 1200)
    if rc == 0:
        print("ok  the page checks, on the second run: " + "; ".join(_page_summary(out)[-3:])
              + " -- the first run's failures were timing, not this change")
        return ""
    print("FAIL the page checks, twice: " + ("; ".join(_page_summary(out)[-8:]) or out[-300:]))
    return "the page checks"


def main(argv=None) -> int:
    quick = "--quick" in (argv if argv is not None else sys.argv[1:])
    failed = []

    bad = []
    for name in sorted(os.listdir(HERE)):
        if name.endswith(".py"):
            try:
                py_compile.compile(os.path.join(HERE, name), doraise=True,
                                   cfile=os.devnull if os.name != "nt" else None)
            except py_compile.PyCompileError as e:
                bad.append(f"{name}: {e.msg.strip().splitlines()[-1]}")
    print(f"{'ok  ' if not bad else 'FAIL'} python compiles" + (": " + "; ".join(bad) if bad else ""))
    if bad:
        failed.append("python compiles")

    node = shutil.which("node")
    if node:
        rc, out = _run([node, "--check", os.path.join(HERE, "ui", "app.js")], 60)
        print(f"{'ok  ' if rc == 0 else 'FAIL'} ui/app.js parses" + ("" if rc == 0 else "\n" + out[-2000:]))
        if rc:
            failed.append("ui/app.js parses")
    else:
        print("skip ui/app.js parses: node is not on PATH")

    for test in QUICK_TESTS:
        path = os.path.join(HERE, test)
        if not os.path.isfile(path):
            print(f"FAIL {test}: missing -- a test of the workbench was removed")
            failed.append(test)
            continue
        rc, out = _run([sys.executable, "-B", path], 600)
        last = [l for l in out.strip().splitlines() if l.strip()][-1:] or [""]
        print(f"{'ok  ' if rc == 0 else 'FAIL'} {test}: {last[0][:120]}")
        if rc:
            print(out[-3000:])
            failed.append(test)

    for test, why in tests_for(changed_files()):
        rc, out = _run([sys.executable, "-B", os.path.join(PROJECT, *test.split("/"))], 900)
        last = [l for l in out.strip().splitlines() if l.strip()][-1:] or [""]
        print(f"{'ok  ' if rc == 0 else 'FAIL'} {test} ({why}): {last[0][:120]}")
        if rc:
            print(out[-3000:])
            failed.append(test)

    if quick:
        print("skip the page checks (--quick)")
    else:
        verdict = page_checks()
        if verdict:
            failed.append(verdict)

    if failed:
        print("FAILED: " + ", ".join(failed))
        return 1
    print("all workbench checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
