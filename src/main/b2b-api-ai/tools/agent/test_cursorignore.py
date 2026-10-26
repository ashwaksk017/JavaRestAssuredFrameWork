"""What Cursor may read (.cursorignore), and the converted files it is
pointed at (flowcatalog.files_for).

    python tools/agent/test_cursorignore.py

A file Cursor opens itself goes to Cursor's servers without the redaction
the prompts get. Before .cursorignore, a sentence in the prompt was all
that kept program_configuration.json -- the Jira token, the client
secrets -- from being read.
"""
from __future__ import annotations

import csv
import fnmatch
import importlib.util
import io
import os
import shutil
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
spec = importlib.util.spec_from_file_location("flowcatalog_under_test", os.path.join(HERE, "flowcatalog.py"))
fc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fc)


def patterns(path: str) -> list:
    with io.open(path, encoding="utf-8") as fh:
        return [l.strip() for l in fh if l.strip() and not l.lstrip().startswith("#")]


def ignored(rel: str, pats: list) -> bool:
    """gitignore-style, enough for these patterns: the last match decides."""
    rel = rel.replace("\\", "/")
    out = False
    for p in pats:
        neg = p.startswith("!")
        p = p[1:] if neg else p
        base = p.rstrip("/")
        hit = (fnmatch.fnmatch(rel, base) or fnmatch.fnmatch(rel, base + "/*")
               or rel.startswith(base + "/") or fnmatch.fnmatch(os.path.basename(rel), base)
               or fnmatch.fnmatch(rel, "*/" + base.lstrip("*/")))
        if hit:
            out = not neg
    return out


class WhatCursorMayRead(unittest.TestCase):

    def setUp(self):
        self.pats = patterns(os.path.join(ROOT, ".cursorignore"))

    def test_the_credentials_are_kept_from_it(self):
        for rel in ("src/main/resources/program_configuration.json",
                    "src/main/resources/program_configuration.goal.json",
                    "tools/cursor_agent.json", "tools/ra_converter/cursor_agent.json",
                    "src/main/resources/application-stg.properties", ".env",
                    ".workbench/job-1/design-cursor.log", "target/b2b-full-run.log"):
            self.assertTrue(ignored(rel, self.pats), rel)

    def test_what_it_is_meant_to_read_stays_readable(self):
        for rel in ("src/main/resources/program_configuration.example.json",
                    "tools/cursor_agent.json.example",
                    "src/main/resources/application-qa.properties",
                    "target/convert-input/Suite.xml",
                    "src/test/java/com/hi/api/tests/StoryTestBase.java",
                    "_audit/suite/facade_endpoints.md"):
            self.assertFalse(ignored(rel, self.pats), rel)

    def test_every_secret_the_gitignore_names_is_named_here(self):
        # The .gitignore's secret entries, mirrored: a credential file added
        # there must not be readable by Cursor here.
        git = patterns(os.path.join(ROOT, ".gitignore"))
        wanted = [p for p in git if any(w in p.lower() for w in
                                        ("program_configuration", "cursor_agent", "credential", ".env",
                                         "secret", "application-"))]
        self.assertTrue(wanted)
        for p in wanted:
            self.assertIn(p, self.pats, f".gitignore keeps {p} out of git; .cursorignore must keep it from Cursor")


class TheFilesToOpen(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        fc._LOCAL.clear()
        audit = os.path.join(self.root, "_audit", "s1")
        os.makedirs(audit)
        with io.open(os.path.join(audit, "steps.csv"), "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["prefix", "case", "step_name", "step_type", "method_or_kind", "endpoint_or_query",
                        "coverage", "gap_detail"])
            w.writerow(["", "ABC-1_activate", "a", "REST", "POST", "/businesses/{id}/activate", "FULL", ""])
        with io.open(os.path.join(audit, "case_to_method_mapping.csv"), "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["soapui_suite", "soapui_case", "xray_key", "java_class_fqn", "java_method", "csv_path"])
            w.writerow(["s1", "ABC-1_activate", "", "com.hi.api.tests.imported.s1.ActivateTest", "activateTest",
                        "src/test/resources/csv/s1/ActivateTest/activateTest.csv"])
        io.open(os.path.join(audit, "facade_endpoints.md"), "w").close()
        for rel in ("src/test/java/com/hi/api/tests/imported/s1/ActivateTest.java",
                    "src/test/resources/csv/s1/ActivateTest/activateTest.csv"):
            p = os.path.join(self.root, *rel.split("/"))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            io.open(p, "w").close()
        os.makedirs(os.path.join(self.root, "src/main/java/com/hi/api/support/s1"))
        self.chain = {"suite": "s1", "case": "#1", "steps": ["POST /businesses/{id}/activate"]}

    def test_a_chain_names_its_own_test_sheet_phases_and_map(self):
        got = fc.files_for(self.root, self.chain)
        self.assertEqual(got[0], "src/test/java/com/hi/api/tests/imported/s1/ActivateTest.java (method activateTest)")
        self.assertIn("src/test/resources/csv/s1/ActivateTest/activateTest.csv (its data sheet)", got)
        self.assertTrue(any(g.startswith("src/main/java/com/hi/api/support/s1/") for g in got), got)
        self.assertIn("_audit/s1/facade_endpoints.md", got)

    def test_only_files_that_exist_here_are_named(self):
        os.remove(os.path.join(self.root, *"src/test/resources/csv/s1/ActivateTest/activateTest.csv".split("/")))
        got = fc.files_for(self.root, self.chain)
        self.assertFalse(any("data sheet" in g for g in got), got)

    def test_a_machine_with_no_converted_suite_says_so(self):
        self.assertTrue(fc.converted_here(self.root))
        shutil.rmtree(os.path.join(self.root, "src", "test", "java", "com", "hi", "api", "tests", "imported"))
        self.assertFalse(fc.converted_here(self.root))


if __name__ == "__main__":
    unittest.main(verbosity=1)
