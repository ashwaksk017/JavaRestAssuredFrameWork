"""partest: a test file's tests in parallel processes, and a failure is
never lost in the crowd.

    python tools/agent/test_partest.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))

SAMPLE = '''import unittest


class A(unittest.TestCase):
    def test_one(self):
        pass

    def test_two(self):
        pass


class B(unittest.TestCase):
    def test_three(self):
        {three}

    @unittest.skip("not here")
    def test_four(self):
        pass


if __name__ == "__main__":
    import partest
    partest.main(__file__)
'''


class Parallel(unittest.TestCase):
    def run_sample(self, three="pass", serial=False):
        d = tempfile.mkdtemp(prefix="partest_")
        self.addCleanup(shutil.rmtree, d, True)
        shutil.copy(os.path.join(HERE, "partest.py"), d)
        with open(os.path.join(d, "test_sample.py"), "w", encoding="utf-8") as fh:
            fh.write(SAMPLE.format(three=three))
        env = dict(os.environ, WORKBENCH_TESTS_SERIAL="1" if serial else "0")
        r = subprocess.run([sys.executable, "test_sample.py"], cwd=d, env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        return r.returncode, r.stdout.decode("utf-8", "replace")

    def test_every_test_is_run_and_counted(self):
        rc, out = self.run_sample()
        self.assertEqual(rc, 0, out)
        self.assertIn("Ran 4 tests", out)
        self.assertIn("OK (skipped=1)", out)

    def test_a_failure_fails_the_run_and_is_shown_whole(self):
        rc, out = self.run_sample(three="self.assertEqual(1, 2, 'the reason')")
        self.assertEqual(rc, 1)
        self.assertIn("FAILED: test_sample.B", out)
        self.assertIn("the reason", out)
        self.assertIn("Ran 4 tests", out, "the passing ones still count")

    def test_an_import_error_is_a_failure_not_zero_tests(self):
        rc, out = self.run_sample(three="pass\n\nimport no_such_module_here")
        self.assertNotEqual(rc, 0, out)

    def test_serial_is_still_there(self):
        rc, out = self.run_sample(serial=True)
        self.assertEqual(rc, 0, out)
        self.assertNotIn("processes", out)


if __name__ == "__main__":
    unittest.main(verbosity=1)
