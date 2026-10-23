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


class EnvironmentValues(Base):
    """Every value of an environment block, editable on the page."""

    def setUp(self):
        super().setUp()
        example = os.path.join(self.root, *st.example_rel("b2b").split("/"))
        with io.open(example, "w", encoding="utf-8") as fh:
            json.dump({"stg": {"database": {"host": "", "port": "", "password": ""},
                               "api_config": {"api_end_point": "", "token_end_point": "", "client_id": ""},
                               "services": {"hospitality_partner_v2": ""},
                               "jira_config": {"pat": "", "base_urls": [], "acceptance_criteria_fields": []},
                               "poll_interval": "5", "retries": 3, "_jira_comment": "Jira's own note"}}, fh)
        with io.open(self.cfg, "w", encoding="utf-8") as fh:
            json.dump({"stg": {"database": {"host": "db.example.test", "password": "db-secret-77"},
                               "api_config": {"client_id": "client-id-secret"},
                               "jira_config": {"pat": "old-token-123"}, "flag": True},
                       "qa": {"keep": "me"}}, fh)

    def fields(self, env=""):
        return {tuple(f["path"]): f for f in st.config_fields(self.root, "b2b", env)["fields"]}

    def test_every_value_and_every_example_setting_is_listed_and_no_secret_is_sent(self):
        out = st.config_fields(self.root, "b2b")
        f = {tuple(x["path"]): x for x in out["fields"]}
        self.assertEqual(f[("database", "host")]["value"], "db.example.test")
        self.assertFalse(f[("database", "port")]["in_file"], "from the example: fill it in")
        self.assertEqual(f[("database", "password")]["kind"], "secret")
        self.assertEqual(f[("api_config", "client_id")]["kind"], "secret")
        self.assertEqual(f[("api_config", "token_end_point")]["kind"], "text", "a token's address is not a secret")
        self.assertEqual(f[("retries",)]["kind"], "number")
        self.assertEqual(f[("flag",)]["kind"], "bool")
        self.assertEqual(f[("jira_config", "acceptance_criteria_fields")]["kind"], "list")
        self.assertNotIn(("jira_config", "pat"), f, "edited in the Jira fields above")
        self.assertEqual(out["notes"].get("jira_config"), "Jira's own note")
        shown = json.dumps(out)
        for secret in ("db-secret-77", "client-id-secret", "old-token-123"):
            self.assertNotIn(secret, shown)

    def test_values_are_saved_as_the_file_types_them_and_an_empty_secret_is_kept(self):
        r = st.save({"config": {"env": "stg", "values": [
            {"path": ["database", "port"], "value": "5432"},
            {"path": ["database", "password"], "value": ""},
            {"path": ["retries"], "value": "4"},
            {"path": ["flag"], "value": False},
            {"path": ["jira_config", "acceptance_criteria_fields"], "value": "customfield_1, customfield_2"},
            {"path": ["services", "hospitality_partner_v2"], "value": "https://partner.example.test"},
            {"path": ["services", "mc_groupsdata"], "value": "https://groups.example.test", "add": True}]}},
            self.root)
        self.assertTrue(r["saved"])
        self.assertNotIn("5432", r["said"], "what changed is named, not its value")
        d = self.doc()["stg"]
        self.assertEqual(d["database"], {"host": "db.example.test", "password": "db-secret-77", "port": "5432"})
        self.assertEqual(d["retries"], 4)
        self.assertIs(d["flag"], False)
        self.assertEqual(d["jira_config"]["acceptance_criteria_fields"], ["customfield_1", "customfield_2"])
        self.assertEqual(d["services"], {"hospitality_partner_v2": "https://partner.example.test",
                                         "mc_groupsdata": "https://groups.example.test"})
        self.assertEqual(self.doc()["qa"], {"keep": "me"})
        st.save({"config": {"env": "stg", "clear": [["database", "password"]],
                            "remove": [["services", "mc_groupsdata"]]}}, self.root)
        d = self.doc()["stg"]
        self.assertNotIn("password", d["database"])
        self.assertNotIn("mc_groupsdata", d["services"])

    def test_another_block_is_edited_and_a_new_one_is_made_empty_or_as_a_copy(self):
        st.save({"config": {"env": "qa", "values": [{"path": ["database", "host"], "value": "qa-db"}]}},
                self.root)
        self.assertEqual(self.doc()["qa"], {"keep": "me", "database": {"host": "qa-db"}})
        self.assertEqual(self.fields("qa")[("keep",)]["value"], "me")
        st.save({"config": {"env": "uat", "copy_from": "stg"}}, self.root)
        self.assertEqual(self.doc()["uat"]["database"]["password"], "db-secret-77")
        st.save({"config": {"env": "dev"}}, self.root)
        self.assertEqual(self.doc()["dev"], {})
        self.assertTrue(st.config_fields(self.root, "b2b", "nope")["new"])

    def test_saving_another_block_leaves_the_active_one_exactly_as_it_was(self):
        with io.open(self.cfg, "w", encoding="utf-8") as fh:
            json.dump({"qa": {"keep": "me"}}, fh)                      # no stg block at all
        st.save({"config": {"env": "qa", "values": [{"path": ["keep"], "value": "you"}]}}, self.root)
        self.assertEqual(self.doc(), {"qa": {"keep": "you"}}, "no empty stg, no empty sections")
        with io.open(self.cfg, "w", encoding="utf-8") as fh:
            json.dump({"stg": {"a": "1"}, "qa": {"keep": "me"}}, fh)
        st.save({"config": {"env": "qa", "values": [{"path": ["keep"], "value": "you"}]}}, self.root)
        self.assertEqual(self.doc()["stg"], {"a": "1"})

    def test_the_block_shown_first_is_the_one_the_tests_run_against(self):
        wb = os.path.join(self.root, st.WORKBENCH_FILE)
        os.makedirs(os.path.dirname(wb))
        with io.open(wb, "w", encoding="utf-8") as fh:
            json.dump({"run_env": "qa"}, fh)
        out = st.config_fields(self.root, "b2b")
        self.assertEqual((out["env"], out["run_env"], out["active_env"]), ("qa", "qa", "stg"))

    def test_true_false_text_stays_text_and_a_backup_keeps_no_secret_of_the_section(self):
        with io.open(self.cfg, "w", encoding="utf-8") as fh:
            json.dump({"stg": {"domains": {"fromDb": {"enabled": "true"}}, "username": "svc-user-1",
                               "sf_config": {"assertion": "jwt-assertion-1", "token_end_point": "https://t"},
                               "api_config": {"client_id": "client-id-secret"}}}, fh)
        self.assertEqual(self.fields()[("domains", "fromDb", "enabled")]["kind"], "boolstr")
        st.save({"config": {"values": [{"path": ["domains", "fromDb", "enabled"], "value": False}]}}, self.root)
        self.assertEqual(self.doc()["stg"]["domains"]["fromDb"]["enabled"], "false")
        keep = os.path.join(self.root, st.BACKUPS)
        with io.open(os.path.join(keep, sorted(os.listdir(keep))[-1]), encoding="utf-8") as fh:
            copy = fh.read()
        for secret in ("svc-user-1", "jwt-assertion-1", "client-id-secret"):
            self.assertNotIn(secret, copy)
        self.assertIn("https://t", copy, "an address is not a secret")

    def goal_doc(self):
        with io.open(os.path.join(self.root, *st._rel("goal").split("/")), encoding="utf-8-sig") as fh:
            return json.load(fh)

    def test_another_project_s_block_moves_to_its_file_and_the_run_environment_follows(self):
        wb = os.path.join(self.root, st.WORKBENCH_FILE)
        os.makedirs(os.path.dirname(wb))
        with io.open(wb, "w", encoding="utf-8") as fh:
            json.dump({"run_env": "qa"}, fh)
        self.assertEqual(st.config_fields(self.root, "b2b")["other_projects"], ["goal"])
        r = st.save({"move_env": {"env": "qa", "to": "goal"}}, self.root, project="b2b")
        self.assertTrue(r["saved"])
        self.assertNotIn("qa", self.doc())
        self.assertEqual(self.doc()["stg"]["database"]["host"], "db.example.test", "the rest stays")
        self.assertEqual(self.goal_doc(), {"qa": {"keep": "me"}})
        self.assertEqual(st.run_env_of(st.workbench_settings(self.root), "b2b"), "stg")
        self.assertIn("run against stg again", r["said"])
        self.assertEqual(st.run_env_of(st.workbench_settings(self.root), "goal"), "qa",
                         "GOAL's file had no block for its run environment: it runs against the one moved in")
        self.assertIn("GOAL's tests run against qa", r["said"])
        names = os.listdir(os.path.join(self.root, st.BACKUPS))
        self.assertTrue(any(n.startswith("program_configuration-") for n in names), "the B2B file as it was")

    def test_a_block_the_other_file_already_has_is_removed_here_only_when_it_is_the_same(self):
        goal = os.path.join(self.root, *st._rel("goal").split("/"))
        with io.open(goal, "w", encoding="utf-8") as fh:
            json.dump({"qa": {"keep": "other"}}, fh)
        with self.assertRaises(ValueError) as got:
            st.save({"move_env": {"env": "qa", "to": "goal"}}, self.root, project="b2b")
        self.assertIn("not the same", str(got.exception))
        self.assertIn("qa", self.doc(), "nothing moved")
        with io.open(goal, "w", encoding="utf-8") as fh:
            json.dump({"qa": {"keep": "me"}}, fh)
        r = st.save({"move_env": {"env": "qa", "to": "goal"}}, self.root, project="b2b")
        self.assertIn("already has the same block", r["said"])
        self.assertNotIn("qa", self.doc())

    def test_what_is_refused_in_a_move(self):
        for move, why in (({"env": "qa", "to": "b2b"}, "not another project"),
                          ({"env": "qa", "to": "nope"}, "not another project"),
                          ({"env": "nope", "to": "goal"}, "no environment 'nope'")):
            with self.assertRaises(ValueError) as got:
                st.save({"move_env": move}, self.root, project="b2b")
            self.assertIn(why, str(got.exception))
        with self.assertRaises(ValueError) as got:
            st.save({"move_env": {"env": "qa", "to": "goal"}}, self.root, busy="mvn-test (job 1)")
        self.assertIn("is running", str(got.exception))

    def test_what_is_refused_in_the_environment_values(self):
        for config, why in (({"env": "bad env"}, "not an environment name"),
                            ({"env": "x", "copy_from": "nope"}, "no environment 'nope'"),
                            ({"values": [{"path": ["services", "x"], "value": "ftp://h"}]}, "not a setting"),
                            ({"values": [{"path": ["services", "x"], "value": "not a url", "add": True}]},
                             "is an address"),
                            ({"values": [{"path": ["database", "port"], "value": "54x"}]}, "port number"),
                            ({"values": [{"path": ["retries"], "value": "many"}]}, "is a number"),
                            ({"values": [{"path": ["jira_config", "pat"], "value": "x" * 10}]}, "fields above"),
                            ({"values": [{"path": ["unknown"], "value": "1"}]}, "not a setting"),
                            ({"values": [{"path": ["database", "host"], "value": "a\nb"}]}, "line break"),
                            ({"values": [{"path": ["services", "a.b"], "value": "https://h", "add": True}]},
                             "no dot"),
                            ({"remove": [["database", "host"]]}, "cannot be removed"),
                            ({"clear": [["database", "host"]]}, "not a secret")):
            with self.assertRaises(ValueError, msg=str(config)) as got:
                st.save({"config": config}, self.root)
            self.assertIn(why, str(got.exception))
        with self.assertRaises(ValueError) as got:
            st.save({"config": {"values": []}}, self.root, busy="mvn-test (job 1)")
        self.assertIn("is running", str(got.exception))


if __name__ == "__main__":
    unittest.main(verbosity=1)
