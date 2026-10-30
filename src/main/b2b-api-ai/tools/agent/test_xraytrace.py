"""Xray run tracing for the Logs tab and Run the tests.

    python tools/agent/test_xraytrace.py
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("xraytrace_under_test", os.path.join(HERE, "xraytrace.py"))
xt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(xt)


class XrayTrace(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="xraytrace-")
        self.addCleanup(shutil.rmtree, self.root, True)
        conf = os.path.join(self.root, "src", "main", "resources")
        os.makedirs(conf)
        with io.open(os.path.join(conf, "program_configuration.json"), "w", encoding="utf-8") as fh:
            json.dump({"stg": {"xray": {"enabled": "false", "baseUrl": "https://jira.example.test",
                                        "token": "secret-token-value"}}}, fh)
        suite = os.path.join(self.root, xt.JIRA_SUITE_REL)
        os.makedirs(os.path.dirname(suite), exist_ok=True)
        with io.open(suite, "w", encoding="utf-8") as fh:
            fh.write(f'<suite><listeners><listener class-name="{xt._LISTENER}"/></listeners></suite>')
        res = os.path.join(self.root, xt.RESULTS_REL)
        os.makedirs(os.path.dirname(res), exist_ok=True)
        with io.open(res, "w", encoding="utf-8") as fh:
            fh.write('<?xml version="1.0"?><testng-results total="1">'
                     '<class name="C"><test-method status="PASS" name="m">'
                     '<params><param><value>{jira_xray_id=NOT-A-KEY, test_case_id=r1}</value></param>'
                     '</params></test-method></class></testng-results>')

    def test_disabled_xray_is_called_out(self):
        info = xt.trace(self.root)
        self.assertFalse(info["would_publish"])
        self.assertTrue(any("xray.enabled is off" in h for h in info["hints"]))
        self.assertIn("XRAY TRACE", xt.text_section(self.root))

    def test_maven_output_lines_are_collected(self):
        out = "[XrayClient] skipped -- xray.enabled=false\n"
        info = xt.trace(self.root, maven_output=out)
        self.assertEqual(info["xray_client"], ["skipped -- xray.enabled=false"])

    def test_bad_jira_xray_id_is_explained(self):
        info = xt.trace(self.root)
        self.assertTrue(any("not a Jira key" in h for h in info["hints"]))

    def test_pass_without_row_params_is_explained_from_csv(self):
        csv_dir = os.path.join(self.root, "src", "test", "resources", "csv", "manual", "GuestProgramAccountTest")
        os.makedirs(csv_dir, exist_ok=True)
        with io.open(os.path.join(csv_dir, "createGuestProgramAccount_createAndReadBack_200.csv"),
                     "w", encoding="utf-8") as fh:
            fh.write("description,test_case_id,jira_xray_id,jira_issue,execute\n"
                     "row,TC-001,B2B-99771,ABC-99454,Y\n")
        res = os.path.join(self.root, xt.RESULTS_REL)
        with io.open(res, "w", encoding="utf-8") as fh:
            fh.write('<?xml version="1.0"?><testng-results total="1">'
                     '<class name="com.hi.api.tests.jira.GuestProgramAccountTest">'
                     '<test-method status="PASS" name="createGuestProgramAccount_createAndReadBack_200"/>'
                     '</class></testng-results>')
        info = xt.trace(self.root)
        joined = "\n".join(info["hints"])
        self.assertIn("B2B-99771", joined)
        self.assertIn("no jira_xray_id in params", joined)
        self.assertIn("PerMethodCsvDataProvider", joined)

    def test_maven_trace_file_is_read_on_logs_tab(self):
        trace = os.path.join(self.root, xt.MAVEN_TRACE_REL)
        os.makedirs(os.path.dirname(trace), exist_ok=True)
        with io.open(trace, "w", encoding="utf-8") as fh:
            fh.write("skipped -- no results with a jira_xray_id to import\n")
        info = xt.trace(self.root)
        self.assertIn("no results with a jira_xray_id", info["xray_client"][-1])

    def test_save_maven_trace_writes_xray_client_lines(self):
        xt.save_maven_trace(self.root, "noise\n[XrayClient] imported 2 result(s)\n")
        path = os.path.join(self.root, xt.MAVEN_TRACE_REL)
        with io.open(path, encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("imported 2 result(s)", body)

    def test_append_diagnostics_to_run_log(self):
        log = os.path.join(self.root, xt.RUN_LOG_REL)
        os.makedirs(os.path.dirname(log), exist_ok=True)
        with io.open(log, "w", encoding="utf-8") as fh:
            fh.write("surefire line\n")
        maven = "noise\n[XrayClient] skipped -- xray.enabled=false\n"
        xt.append_diagnostics_to_run_log(self.root, xt.JIRA_SUITE_REL, maven)
        with io.open(log, encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("surefire line", body)
        self.assertIn(xt._RUN_LOG_MARKER, body)
        self.assertIn("[XrayClient] skipped -- xray.enabled=false", body)
        self.assertIn("XRAY TRACE", body)
        self.assertTrue(xt.run_log_has_workbench_diagnostics(self.root))


if __name__ == "__main__":
    unittest.main(verbosity=1)
