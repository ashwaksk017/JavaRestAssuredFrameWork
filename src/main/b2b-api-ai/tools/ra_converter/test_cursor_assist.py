"""Unit tests for cursor_assist (no live Cursor API)."""
from __future__ import annotations

import io
import json
import os
import tempfile
import time
import unittest
from types import SimpleNamespace

import cursor_assist


class FakeLedger:
    def __init__(self):
        self.assertions = []
        self.groovy = []
        self.steps = []
        self.unmapped = []
        self.preflight = []
        self.runtime_skips = []


class CursorAssistTest(unittest.TestCase):
    def setUp(self):
        cursor_assist.clear_pending()
        self._old = os.environ.pop("CURSOR_API_KEY", None)
        self._old_mem = os.environ.pop("CURSOR_ASSIST_MEMORY", None)
        self._mem_td = tempfile.TemporaryDirectory()
        os.environ["CURSOR_ASSIST_MEMORY"] = os.path.join(
            self._mem_td.name, "memory.md")

    def tearDown(self):
        cursor_assist.clear_pending()
        if self._old is None:
            os.environ.pop("CURSOR_API_KEY", None)
        else:
            os.environ["CURSOR_API_KEY"] = self._old
        os.environ.pop("CURSOR_ASSIST_MEMORY", None)
        if self._old_mem is not None:
            os.environ["CURSOR_ASSIST_MEMORY"] = self._old_mem
        self._mem_td.cleanup()

    def test_collect_skips_full_and_auto_fixes(self):
        led = FakeLedger()
        led.assertions.append(("p", "c1", "s", "JsonPath", "{}", "java", "FULL"))
        led.assertions.append(("p", "c2", "s", "Unknown", "{}", "TODO", "TODO"))
        led.preflight.append(("INFO", "token-injected", "c3", "ok"))
        led.preflight.append(("HIGH", "jdbc-mutation-skip", "c4", "sql.execute"))
        gaps = cursor_assist.collect_gaps(led, "suiteA")
        cats = {(g.kind, g.category, g.case) for g in gaps}
        self.assertIn(("assertion", "TODO", "c2"), cats)
        self.assertIn(("preflight", "jdbc-mutation-skip", "c4"), cats)
        self.assertFalse(any(g.case == "c1" for g in gaps))
        self.assertFalse(any(g.case == "c3" for g in gaps))

    def test_conversion_report_lists_every_gap(self):
        gaps = [
            cursor_assist.Gap("s", "preflight", "jdbc-mutation-skip",
                              f"c{i}", "DBupdate", "sql.execute", "HIGH")
            for i in range(5)
        ]
        body = cursor_assist.build_conversion_report(gaps, ["s"])
        self.assertIn("gap rows: 5", body)
        self.assertIn("before assist fix", body)
        self.assertIn("c4", body)

    def test_cli_summary_prints_counts_and_sample_details(self):
        gaps = [
            cursor_assist.Gap("prog", "preflight", "jdbc-mutation-skip",
                              f"c{i}", "DBupdate", "sql.execute", "HIGH")
            for i in range(15)
        ]
        gaps.append(cursor_assist.Gap(
            "prog", "assertion", "TODO", "Reject", "Assert", "unknown", "TODO"))
        text = cursor_assist.format_cli_summary(
            gaps, ["prog"], report_path=r"C:\out\_audit\cursor_assist_report.md",
            max_rows_per_category=3)
        self.assertIn("gap rows: 16", text)
        self.assertIn("suites: prog", text)
        self.assertIn("cursor_assist_report.md", text)
        self.assertIn("preflight:jdbc-mutation-skip", text)
        self.assertIn("assertion:TODO", text)
        self.assertIn("case=c0", text)
        self.assertIn("... 12 more in this category", text)
        self.assertNotIn("case=c14", text)

    def test_prompt_includes_report_path_and_memory(self):
        gaps = [cursor_assist.Gap("s", "preflight", "jdbc-mutation-skip",
                                  "Reject", "DBupdate", "sql.execute", "HIGH")]
        prompt = cursor_assist.build_prompt(
            gaps, 3, ["s"],
            report_path="/tmp/cursor_assist_report.md",
            memory_text="- last-write-wins memberId")
        self.assertIn("/tmp/cursor_assist_report.md", prompt)
        self.assertIn("last-write-wins memberId", prompt)
        self.assertIn("After-fix summary", prompt)
        self.assertIn("Do NOT hand-edit", prompt)
        self.assertNotIn("apiKey", prompt)
        self.assertNotIn("CURSOR_API_KEY", prompt)

    def test_extract_lesson_prefers_after_fix_heading(self):
        text = "worked\n\n## After-fix summary\n- files changed: groovy_translator.py\n"
        self.assertIn("groovy_translator.py", cursor_assist.extract_lesson(text))

    def test_flush_writes_report_before_agent_and_after_file(self):
        led = FakeLedger()
        led.preflight.append(("HIGH", "untranslated-cross-tc-ref", "Reject",
                              "${#[other#token]}"))
        cursor_assist.enqueue("prog", led)
        order = []

        def fake(prompt, cfg):
            order.append("agent")
            self.assertIn("cursor_assist_report.md", prompt)
            return {"status": "finished",
                    "result": "## After-fix summary\n- files changed: none\n",
                    "id": "run-1"}

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "cursor_agent.json")
            mem = os.path.join(td, "memory.md")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"enabled": True, "apiKey": "secret-key"}, f)
            os.environ["CURSOR_ASSIST_MEMORY"] = mem
            args = SimpleNamespace(
                cursor_config=path, cursor_assist=False,
                no_cursor_assist=False, output=td)
            report = os.path.join(td, "_audit", "cursor_assist_report.md")

            def fake_after_report_exists(prompt, cfg):
                self.assertTrue(os.path.isfile(report), "report must exist before agent")
                return fake(prompt, cfg)

            index = cursor_assist.flush(args, call_agent=fake_after_report_exists)
            self.assertTrue(index and os.path.isfile(index))
            self.assertEqual(order, ["agent"])
            after = os.path.join(td, "_audit", "cursor_assist_after.md")
            self.assertTrue(os.path.isfile(after))
            with open(index, encoding="utf-8") as rf:
                idx = rf.read()
                self.assertIn("cursor_assist_report.md", idx)
                self.assertIn("cursor_assist_after.md", idx)
                self.assertNotIn("secret-key", idx)
            with open(after, encoding="utf-8") as af:
                self.assertNotIn("secret-key", af.read())
            with open(mem, encoding="utf-8") as mf:
                self.assertIn("After-fix summary", mf.read())

            cursor_assist.enqueue("prog", led)
            args_off = SimpleNamespace(
                cursor_config=path, cursor_assist=False,
                no_cursor_assist=True, output=td)
            self.assertIsNone(cursor_assist.flush(args_off, call_agent=fake))
            self.assertEqual(order, ["agent"])

    def test_flush_no_key_still_writes_conversion_report(self):
        led = FakeLedger()
        led.preflight.append(("HIGH", "jdbc-mutation-skip", "c4", "sql.execute"))
        cursor_assist.enqueue("prog", led)
        called = []

        def fake(prompt, cfg):
            called.append(1)
            return {"status": "finished", "result": "nope", "id": "x"}

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "cursor_agent.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"enabled": True, "apiKey": ""}, f)
            args = SimpleNamespace(
                cursor_config=path, cursor_assist=False,
                no_cursor_assist=False, output=td)
            index = cursor_assist.flush(args, call_agent=fake)
            self.assertTrue(index and os.path.isfile(index))
            self.assertEqual(called, [])
            with open(os.path.join(td, "_audit", "cursor_assist_report.md"),
                      encoding="utf-8") as rf:
                self.assertIn("jdbc-mutation-skip", rf.read())
            with open(os.path.join(td, "_audit", "cursor_assist_after.md"),
                      encoding="utf-8") as af:
                self.assertIn("no API key", af.read())

    def test_env_key_wins_over_file(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "cursor_agent.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"enabled": True, "apiKey": "file-key"}, f)
            args = SimpleNamespace(
                cursor_config=path, cursor_assist=False,
                no_cursor_assist=False, output=td)
            os.environ["CURSOR_API_KEY"] = "env-key"
            cfg = cursor_assist.load_config(args)
            self.assertTrue(cfg.enabled)
            self.assertEqual(cfg.api_key, "env-key")

    def test_flush_calls_agent_once_and_skips_when_disabled(self):
        led = FakeLedger()
        led.preflight.append(("HIGH", "untranslated-cross-tc-ref", "Reject",
                              "${#[other#token]}"))
        cursor_assist.enqueue("prog", led)
        calls = []

        def fake(prompt, cfg):
            calls.append((prompt, cfg.api_key))
            return {"status": "finished", "result": "ok", "id": "run-1"}

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "cursor_agent.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"enabled": True, "apiKey": "secret-key"}, f)
            args = SimpleNamespace(
                cursor_config=path, cursor_assist=False,
                no_cursor_assist=False, output=td)
            report = cursor_assist.flush(args, call_agent=fake)
            self.assertTrue(report and os.path.isfile(report))
            self.assertEqual(len(calls), 1)
            self.assertIn("untranslated-cross-tc-ref", calls[0][0])
            with open(report, encoding="utf-8") as rf:
                self.assertNotIn("secret-key", rf.read())

            cursor_assist.enqueue("prog", led)
            args_off = SimpleNamespace(
                cursor_config=path, cursor_assist=False,
                no_cursor_assist=True, output=td)
            self.assertIsNone(cursor_assist.flush(args_off, call_agent=fake))
            self.assertEqual(len(calls), 1)

    def test_redact(self):
        self.assertEqual(
            cursor_assist.redact_for_log("token=abc", "abc"),
            "token=<redacted>")

    def test_bridge_discovery_reads_ready_line_without_select(self):
        class Proc:
            def __init__(self):
                self.stderr = io.StringIO(
                    "noise\ncursor-sdk-bridge ready {\"ok\": true}\n")

            def poll(self):
                return None

        def parse(line):
            if "cursor-sdk-bridge ready" in line:
                return {"ok": True}
            return None

        self.assertEqual(
            cursor_assist._read_bridge_discovery(Proc(), 2.0, parse),
            {"ok": True})

    def test_bridge_discovery_times_out_when_stderr_hangs(self):
        class Hang:
            def __iter__(self):
                return self

            def __next__(self):
                time.sleep(5)
                raise StopIteration

        class Proc:
            stderr = Hang()

            def poll(self):
                return None

        with self.assertRaisesRegex(RuntimeError, "Timed out"):
            cursor_assist._read_bridge_discovery(Proc(), 0.25, lambda _l: None)

    def test_windows_bridge_patch_replaces_sdk_select(self):
        try:
            import cursor_sdk._bridge as bridge
        except ImportError:
            self.skipTest("cursor-sdk not installed")
        cursor_assist._patch_cursor_sdk_bridge_for_windows()
        if os.name != "nt":
            return
        self.assertTrue(getattr(bridge._read_discovery, "_ra_windows_safe", False))


if __name__ == "__main__":
    unittest.main()
