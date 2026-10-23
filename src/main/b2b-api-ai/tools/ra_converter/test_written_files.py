"""The files a run wrote, by kind: every suite, test class and data sheet
named by its path, the rest counted per folder.

    python tools/ra_converter/test_written_files.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import written_files as wf  # noqa: E402

TESTS = "src/test/java/com/hi/api/tests/"
WRITTEN = [
    "Suites/Demo_Regression.xml", "Suites/Demo_Smoke.xml",
    TESTS + "demo/DemoTest.java", TESTS + "demo/DemoSteps.java", TESTS + "demo/DemoHooks1.java",
    "src/main/java/com/hi/api/clients/DemoClient.java",
    "src/test/resources/csv/demo/case_1.csv", "src/test/resources/csv/demo/case_2.csv",
    "src/test/resources/templates/demo/a.json", "src/test/resources/templates/demo/b.json",
    "docs/diagrams/demo.mmd",
    ".\\src\\test\\resources\\csv\\demo\\case_3.csv",
]


class ByKind(unittest.TestCase):
    def test_suites_test_classes_and_sheets_are_named_and_the_rest_counted(self):
        lines = wf.render(WRITTEN)
        text = "\n".join(lines)
        self.assertIn("TestNG suites (2)", lines)
        self.assertIn("Test classes (1)", lines)
        self.assertIn("Data sheets (3)", lines)
        self.assertIn("Supporting classes (3)", lines)
        self.assertIn("Other files (3), by folder", lines)
        for p in ("Suites/Demo_Smoke.xml", TESTS + "demo/DemoTest.java", TESTS + "demo/DemoSteps.java",
                  "src/test/resources/csv/demo/case_3.csv"):
            self.assertIn("    " + p, lines, p)
        self.assertIn("   2  src/test/resources/templates/demo", text)
        self.assertNotIn("a.json", text, "other files are counted, not listed")
        self.assertLess(text.index("TestNG suites"), text.index("Test classes"))
        self.assertLess(text.index("Test classes"), text.index("Data sheets"))

    def test_changed_files_are_marked_and_kept_ones_counted_not_listed(self):
        lines = wf.render(WRITTEN, changed=["Suites/Demo_Smoke.xml"],
                          kept=["src/main/java/com/hi/api/clients/DemoClient.java"])
        self.assertIn("    Suites/Demo_Smoke.xml  (changed)", lines)
        self.assertIn("Supporting classes (2)", lines)
        self.assertTrue(any(ln.startswith("Kept as they were (1)") for ln in lines))

    def test_a_job_suite_and_a_testng_file_are_suites_and_nothing_is_said_for_nothing(self):
        self.assertEqual(wf.kind_of("Suites/jira/job-1.xml"), "suites")
        self.assertEqual(wf.kind_of("src/test/resources/testng-jira.xml"), "suites")
        self.assertEqual(wf.kind_of("src/test/resources/other.xml"), "other")
        self.assertEqual(wf.kind_of("src/test/java/x/ThingIT.java"), "tests")
        self.assertEqual(wf.kind_of("src/main/java/x/ThingTest.java"), "classes", "main code is not a test")
        self.assertEqual(wf.render([]), [])


if __name__ == "__main__":
    unittest.main(verbosity=1)
