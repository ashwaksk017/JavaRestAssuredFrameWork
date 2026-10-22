"""The checks a change to the workbench itself must pass before a person
reviews it (the "workbench" scope of tools/agent/policy.json).

    python tools/agent/check_workbench.py            # all of them
    python tools/agent/check_workbench.py --quick    # without the page checks

1. Every Python file of tools/agent compiles.
2. ui/app.js parses (node --check), when node is on PATH.
3. The workbench's quick unit tests pass (seconds each).
4. The page checks (check_ui.py: a headless browser against the real
   server) pass, when a browser is found -- a few minutes.

test_loop.py (about two minutes, in parallel processes -- partest.py) is
not run here; the review says so.
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
               "test_partest.py", "test_convertfix.py", "test_ask.py",
               "test_storykeys.py", "test_projects.py",
               "test_flowcatalog.py")


def _run(argv, timeout=900) -> tuple:
    try:
        r = subprocess.run(argv, cwd=os.path.dirname(os.path.dirname(HERE)), stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=timeout)
        return r.returncode, r.stdout.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"


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

    if quick:
        print("skip the page checks (--quick)")
    else:
        rc, out = _run([sys.executable, "-B", os.path.join(HERE, "check_ui.py")], 1200)
        summary = [l for l in out.splitlines() if "checks passed" in l or l.startswith("FAIL")]
        if rc == 2 and "nothing was checked" in out:
            print("skip the page checks: no Edge or Chrome was found")
        else:
            print(f"{'ok  ' if rc == 0 else 'FAIL'} the page checks: " + ("; ".join(summary[-6:]) or out[-300:]))
            if rc:
                failed.append("the page checks")

    print("test_loop.py was not run here (about two minutes): run it before approving a change "
          "to loop.py or cursor_call.py")
    if failed:
        print("FAILED: " + ", ".join(failed))
        return 1
    print("all workbench checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
