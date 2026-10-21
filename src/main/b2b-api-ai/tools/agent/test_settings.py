"""The Settings tab: tokens written where the readers look, never read back.

    python tools/agent/test_settings.py
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("settings_under_test", os.path.join(HERE, "settings.py"))
st = importlib.util.module_from_spec(spec)
spec.loader.exec_module(st)
PC = st._projectconfig()


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="settings-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.addCleanup(setattr, PC, "ROOT", PC.ROOT)
        self.addCleanup(os.environ.__setitem__, "TEST_ENV", os.environ.get("TEST_ENV", "")) \
            if "TEST_ENV" in os.environ else self.addCleanup(os.environ.pop, "TEST_ENV", None)
        os.environ["TEST_ENV"] = "stg"
        self.cfg = os.path.join(self.root, *st.CONFIG_REL.split("/"))
        os.makedirs(os.path.dirname(self.cfg))
        with io.open(self.cfg, "w", encoding="utf-8-sig") as fh:
            json.dump({"stg": {"baseUrl": "https://api.example.test", "jira_config": {"pat": "old-token-123"}},
                       "qa": {"keep": "me"}}, fh)

    def doc(self):
        with io.open(self.cfg, encoding="utf-8-sig") as fh:
            return json.load(fh)


class Saving(Base):
    def test_tokens_go_where_the_readers_look_and_are_never_read_back(self):
        r = st.save({"jira_base_urls": "https://jira.example.test/, https://jira2.example.test",
                     "jira_pat": "new-jira-token-1", "confluence_base_urls": "https://wiki.example.test",
                     "confluence_pat": "conf-token-22", "xray_base_url": "https://jira.example.test",
                     "xray_same_as_jira": True, "xray_project_key": "abc",
                     "xray_regression_key": "abc-7", "xray_enabled": True}, self.root)
        self.assertTrue(r["saved"])
        d = self.doc()
        self.assertEqual(d["stg"]["jira_config"], {"pat": "new-jira-token-1",
                                                    "base_urls": ["https://jira.example.test", "https://jira2.example.test"]})
        self.assertEqual(d["stg"]["confluence_config"]["pat"], "conf-token-22")
        self.assertEqual(d["stg"]["xray"], {"baseUrl": "https://jira.example.test", "token": "new-jira-token-1",
                                            "projectKey": "ABC", "regressionKey": "ABC-7", "enabled": "true"})
        self.assertEqual(d["qa"], {"keep": "me"}, "the other environments are left alone")
        self.assertEqual(d["stg"]["baseUrl"], "https://api.example.test", "and the rest of this one")
        shown = json.dumps(st.status(self.root))
        for secret in ("new-jira-token-1", "conf-token-22", "old-token-123"):
            self.assertNotIn(secret, shown)
        self.assertIn("set, 16 characters", shown)
        self.assertTrue(st.status(self.root)["xray"]["same_as_jira"])
        self.assertEqual(len(os.listdir(os.path.join(self.root, st.BACKUPS))), 1, "a copy of the file as it was")
        with io.open(self.cfg, "rb") as fh:
            self.assertNotEqual(fh.read(3), bytes([0xEF, 0xBB, 0xBF]),
                                "no byte-order mark: tools/jira reads it as plain utf-8")

    def test_an_empty_token_box_keeps_the_token_and_clear_removes_it(self):
        st.save({"jira_pat": "", "jira_base_urls": "https://jira.example.test"}, self.root)
        self.assertEqual(self.doc()["stg"]["jira_config"]["pat"], "old-token-123")
        st.save({"jira_pat_clear": True}, self.root)
        self.assertNotIn("pat", self.doc()["stg"]["jira_config"])

    def test_what_is_refused(self):
        for changes, why in (({"jira_base_urls": "http://jira.example.test"}, "https://"),
                             ({"jira_pat": "has space in it"}, "does not look like one"),
                             ({"xray_regression_key": "nope"}, "issue key"),
                             ({"xray_same_as_jira": True, "jira_pat_clear": True}, "no Jira token")):
            with self.assertRaises(ValueError) as got:
                st.save(changes, self.root)
            self.assertIn(why, str(got.exception))
        with self.assertRaises(ValueError) as got:
            st.save({"jira_pat": "fine-token-1"}, self.root, busy="convert (job j)")
        self.assertIn("nothing is running", str(got.exception))
        self.assertEqual(self.doc()["stg"]["jira_config"]["pat"], "old-token-123", "nothing written")


class CursorSettings(Base):
    def cursor_file(self):
        with io.open(os.path.join(self.root, *st.CURSOR_REL.split("/")), encoding="utf-8") as fh:
            return json.load(fh)

    def test_key_and_models_go_to_the_converters_file_and_the_key_is_never_shown(self):
        os.makedirs(os.path.join(self.root, "tools", "ra_converter"))
        with io.open(os.path.join(self.root, *st.CURSOR_REL.split("/")), "w", encoding="utf-8") as fh:
            json.dump({"enabled": True, "runtime": "local", "maxSamplesPerCategory": 3}, fh)
        r = st.save({"cursor_key": "key_abcdef0123456789", "cursor_model": "composer-2.5",
                     "cursor_design_model": "grok-4.6"}, self.root)
        self.assertTrue(r["saved"], r)
        data = self.cursor_file()
        self.assertEqual((data["apiKey"], data["model"], data["designModel"]),
                         ("key_abcdef0123456789", "composer-2.5", "grok-4.6"))
        self.assertEqual((data["enabled"], data["maxSamplesPerCategory"]), (True, 3), "the converter's own fields stay")
        shown = json.dumps(st.status(self.root))
        self.assertNotIn("key_abcdef0123456789", shown)
        self.assertEqual(st.status(self.root)["cursor"]["design_model"], "grok-4.6")
        with io.open(os.path.join(self.root, *st.CURSOR_REL.split("/")), "rb") as fh:
            self.assertFalse(fh.read().startswith(bytes([0xEF, 0xBB, 0xBF])), "no BOM: the converter reads plain UTF-8")

    def test_an_empty_key_keeps_it_clear_removes_it_and_a_bad_model_is_refused(self):
        st.save({"cursor_key": "key_abcdef0123456789"}, self.root)
        st.save({"cursor_key": "", "cursor_model": "grok-4.6"}, self.root)
        self.assertEqual(self.cursor_file()["apiKey"], "key_abcdef0123456789")
        st.save({"cursor_key_clear": True}, self.root)
        self.assertNotIn("apiKey", self.cursor_file())
        with self.assertRaises(ValueError):
            st.save({"cursor_model": "grok 4.6; rm"}, self.root)
        with self.assertRaises(ValueError):
            st.save({"cursor_key": "has space in it"}, self.root)
        st.save({"cursor_design_model": ""}, self.root)
        self.assertNotIn("designModel", self.cursor_file(), "empty: the main model is used")


class WhereTheCursorSettingsLive(Base):
    def test_the_shared_file_is_used_and_the_converter_s_old_one_still_is_alone(self):
        self.assertEqual(st.cursor_rel(self.root), "tools/cursor_agent.json")
        old = os.path.join(self.root, "tools", "ra_converter", "cursor_agent.json")
        os.makedirs(os.path.dirname(old), exist_ok=True)
        with io.open(old, "w", encoding="utf-8") as fh:
            fh.write('{"model": "old-place"}')
        self.assertEqual(st.cursor_rel(self.root), "tools/ra_converter/cursor_agent.json")
        with io.open(os.path.join(self.root, "tools", "cursor_agent.json"), "w", encoding="utf-8") as fh:
            fh.write('{"model": "new-place"}')
        self.assertEqual(st.cursor_rel(self.root), "tools/cursor_agent.json")


class TheCopies(Base):
    def test_a_copy_keeps_no_token_and_an_unchanged_save_writes_nothing(self):
        st.save({"jira_pat": "brand-new-token-9"}, self.root)
        keep = os.path.join(self.root, st.BACKUPS)
        (only,) = os.listdir(keep)
        with io.open(os.path.join(keep, only), encoding="utf-8") as fh:
            text = fh.read()
        self.assertNotIn("old-token-123", text)
        self.assertIn("<removed from the copy>", text)
        r = st.save({"jira_base_urls": ""}, self.root)
        r = st.save({"jira_base_urls": ""}, self.root)
        self.assertFalse(r["saved"], "the same settings again: nothing written")
        self.assertEqual(len(os.listdir(keep)), 2)


class TheStages(Base):
    def test_create_in_xray_run_and_mapping_are_saved_where_they_are_read(self):
        r = st.save({"xray_create_on": True, "xray_create_projects": "abc, def",
                     "xray_create_issue_type": "Test", "xray_create_link_type": "Verifies",
                     "run_env": "qa", "run_timeout_min": "30",
                     "suite_map": {"always": ["regression"], "rules": [
                         {"group": "smoke", "type": "positive", "priority": "high, p1"}]}}, self.root)
        self.assertTrue(r["saved"])
        pub = self.doc()["stg"]["jira_config"]["publish"]
        self.assertEqual(pub, {"create_tests": True, "projects": ["ABC", "DEF"], "test_link_type": "Verifies"},
                         "the default issue type is not written")
        self.assertEqual(st.workbench_settings(self.root), {"run_env": "qa", "run_timeout_min": 30})
        with io.open(os.path.join(self.root, ".workbench", "suite-map.json"), encoding="utf-8") as fh:
            m = json.load(fh)
        self.assertEqual(m["always"], ["jira", "regression"], "jira is always kept")
        self.assertEqual(m["rules"], [{"group": "smoke", "type": ["positive"], "priority": ["high", "p1"]}])
        s = st.status(self.root)
        self.assertEqual((s["xray_create"]["on"], s["run"]["env"], s["run"]["envs"]), (True, "qa", ["qa", "stg"]))
        st.save({"suite_map_reset": True}, self.root)
        self.assertFalse(os.path.isfile(os.path.join(self.root, ".workbench", "suite-map.json")))

    def test_what_is_refused_here(self):
        for changes, why in (({"run_env": "prod"}, "not an environment"),
                             ({"run_timeout_min": "1"}, "between 5 and 240"),
                             ({"xray_create_projects": "a b-1"}, "project key"),
                             ({"suite_map": {"rules": [{"group": "Bad Group"}]}}, "group name")):
            with self.assertRaises(ValueError) as got:
                st.save(changes, self.root)
            self.assertIn(why, str(got.exception))


if __name__ == "__main__":
    unittest.main(verbosity=1)
