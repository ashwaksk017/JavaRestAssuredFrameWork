"""tools/safe_output.py: every workbench command runs through it, so a job
log that times out (a project in a OneDrive folder, seen on a Mac) cannot
end the command.

    python tools/agent/test_safe_output.py
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.join(os.path.dirname(HERE), "safe_output.py")

SCRIPT = r'''
import os, sys
import helper                              # its own folder is on sys.path
print("name", __name__)
print("argv", sys.argv[1:])
print("file", os.path.basename(__file__))
sys.exit(int(sys.argv[1]) if len(sys.argv) > 1 else 0)
'''

# A stdout that times out on its first writes, as the OneDrive file did.
FLAKY = r'''
import runpy, sys
class Flaky:
    def __init__(self, real):
        self.real, self.left = real, 2
    def write(self, t):
        if self.left:
            self.left -= 1
            raise TimeoutError(60, "Operation timed out")
        return self.real.write(t)
    def flush(self):
        return self.real.flush()
sys.stdout = Flaky(sys.stdout)
sys.argv = [sys.argv[1]] + sys.argv[2:]
runpy.run_path(sys.argv[0], run_name="__main__")
'''


class SafeOutput(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.script = os.path.join(self.dir, "cmd.py")
        with open(self.script, "w", encoding="utf-8") as fh:
            fh.write(SCRIPT)
        with open(os.path.join(self.dir, "helper.py"), "w", encoding="utf-8") as fh:
            fh.write("X = 1\n")

    def run_(self, *args):
        return subprocess.run([sys.executable, "-B", RUNNER, self.script, *args],
                              capture_output=True, text=True, timeout=60)

    def test_the_script_runs_as_it_would_on_its_own(self):
        r = self.run_("0", "--flag")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("name __main__", r.stdout)
        self.assertIn("argv ['0', '--flag']", r.stdout)
        self.assertIn("file cmd.py", r.stdout)

    def test_its_exit_code_is_the_commands(self):
        self.assertEqual(self.run_("3").returncode, 3)

    def test_a_log_that_times_out_does_not_end_the_command(self):
        # The guard is installed by safe_output around the flaky stream.
        flaky = os.path.join(self.dir, "flaky_start.py")
        with open(flaky, "w", encoding="utf-8") as fh:
            fh.write(FLAKY)
        r = subprocess.run([sys.executable, "-B", flaky, RUNNER, self.script, "0"],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("TimeoutError", r.stderr)
        self.assertIn("file cmd.py", r.stdout)


class EveryJobUsesIt(unittest.TestCase):

    def test_a_python_command_is_started_through_it(self):
        spec = importlib.util.spec_from_file_location("jobs_safe", os.path.join(HERE, "jobs.py"))
        sys.path.insert(0, HERE)
        jobs = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(jobs)
        got = jobs.safe_output([jobs.PY, "-B", os.path.join("tools", "verify_all.py"), "--x"])
        self.assertEqual(got[:3], [jobs.PY, "-B", jobs.SAFE_OUTPUT])
        self.assertEqual(got[3:], [os.path.join("tools", "verify_all.py"), "--x"])
        self.assertEqual(jobs.safe_output(["git", "status"]), ["git", "status"], "not python: as it is")


if __name__ == "__main__":
    unittest.main(verbosity=1)
