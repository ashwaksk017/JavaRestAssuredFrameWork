"""One configuration file a project (B2B, GOAL): which file each step reads.

Each project's file has its own services, database AND Jira / Xray /
Confluence settings. A job records its project; every command started for
it reads that project's file (WORKBENCH_PROJECT); a test run passes
-Dconfig.project to Java; secrets of EVERY file are kept out of prompts.
"""
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


import jobs  # noqa: E402

loop = jobs._loop_module()
intake = jobs.intake
PC = intake.projectconfig
mvnrun = _load("projects_mvnrun", os.path.join(HERE, "mvnrun.py"))
settings = jobs.settings


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="projects-")
        self.addCleanup(shutil.rmtree, self.root, True)
        before = os.environ.pop("WORKBENCH_PROJECT", None)
        self.addCleanup(lambda: os.environ.pop("WORKBENCH_PROJECT", None) if before is None
                        else os.environ.__setitem__("WORKBENCH_PROJECT", before))
        self.b2b = PC.config_path(self.root, "b2b")
        self.goal = PC.config_path(self.root, "goal")


class WhichFile(Base):
    def test_no_project_is_b2b_and_its_file_is_the_one_every_run_used(self):
        self.assertEqual(PC.active_project(), "b2b")
        self.assertTrue(PC.config_rel().endswith("/program_configuration.json"))

    def test_a_goal_command_reads_goals_file(self):
        os.environ["WORKBENCH_PROJECT"] = "GOAL"
        self.assertEqual(PC.active_project(), "goal")
        self.assertTrue(PC.config_rel().endswith("/program_configuration.goal.json"))

    def test_an_unknown_project_is_an_error_not_b2b(self):
        os.environ["WORKBENCH_PROJECT"] = "gaol"
        with self.assertRaises(ValueError):
            PC.active_project()

    def test_using_sets_it_for_a_block_and_puts_it_back(self):
        with PC.using("goal"):
            self.assertEqual(PC.active_project(), "goal")
        self.assertNotIn("WORKBENCH_PROJECT", os.environ)
        self.assertEqual(PC.active_project(), "b2b")

    def test_every_copy_of_the_module_sees_a_using_block(self):
        # Seen in review: intake's copy and the Jira client's copy each had
        # their own thread-local; the GOAL Check checked B2B's token.
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "projects_second_copy", os.path.join(os.path.dirname(HERE), "jira", "projectconfig.py"))
        other = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(other)
        with PC.using("goal"):
            self.assertEqual(other.active_project(), "goal")
            self.assertEqual(jobs.intake.jira_search.projectconfig.active_project(), "goal")
        self.assertEqual(other.active_project(), "b2b")

    def test_using_is_this_threads_only(self):
        # The server answers on several threads: one request's project must
        # not become another's.
        import threading
        seen = {}
        inside, done = threading.Event(), threading.Event()

        def other():
            inside.wait(5)
            seen["other"] = PC.active_project()
            done.set()
        t = threading.Thread(target=other)
        t.start()
        with PC.using("goal"):
            inside.set()
            done.wait(5)
        t.join(5)
        self.assertEqual(seen["other"], "b2b")

    def test_settings_read_inside_a_goal_job_is_goals_file(self):
        # Seen in review: Xray publish in a GOAL job read B2B's Xray settings.
        os.environ["WORKBENCH_PROJECT"] = "goal"
        self.assertTrue(settings._rel().endswith("program_configuration.goal.json"))

    def test_goals_jira_settings_come_from_goals_file(self):
        write_json(self.b2b, {"stg": {"jira_config": {"base_urls": ["https://b2b-jira.example.test"]}}})
        write_json(self.goal, {"stg": {"jira_config": {"base_urls": ["https://goal-jira.example.test"]}}})
        with mock.patch.object(PC, "ROOT", self.root), mock.patch.dict(os.environ, {"TEST_ENV": "stg"}):
            self.assertIn("b2b-jira", PC.section("jira_config")[0]["base_urls"][0])
            with PC.using("goal"):
                self.assertIn("goal-jira", PC.section("jira_config")[0]["base_urls"][0])


class AJobsProject(Base):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(intake, "ROOT", self.root)
        p.start()
        self.addCleanup(p.stop)
        self.dir = intake.job_dir("job-1")

    def test_a_job_made_before_projects_is_b2b(self):
        self.assertEqual(intake.job_project(self.dir), "b2b")

    def test_only_a_new_job_takes_the_pages_project(self):
        # Seen in review: opening an old B2B job (no project.json) while GOAL
        # was selected turned it into a GOAL job.
        os.makedirs(self.dir, exist_ok=True)
        self.assertTrue(jobs.project_info("job-1")["new"])
        write_json(os.path.join(self.dir, "intake.json"), {"story": {"key": "ABC-1"}})
        info = jobs.project_info("job-1")
        self.assertEqual((info["chosen"], info["new"]), (False, False), "an old job: stays B2B")

    def test_a_job_keeps_its_project(self):
        intake.set_job_project(self.dir, "GOAL")
        self.assertEqual(intake.job_project(self.dir), "goal")
        with self.assertRaises(ValueError):
            intake.set_job_project(self.dir, "nope")

    def test_the_project_cannot_change_under_a_change_made_for_the_other(self):
        write_json(os.path.join(self.dir, "review.json"), {"state": "pending-review"})
        with self.assertRaises(ValueError) as e:
            jobs.set_project("job-1", "goal")
        self.assertIn("held by this job", str(e.exception))
        self.assertEqual(intake.job_project(self.dir), "b2b")
        self.assertEqual(jobs.set_project("job-1", "b2b")["project"], "b2b", "the same project is fine")
        write_json(os.path.join(self.dir, "review.json"), {"state": "approved-local"})
        with self.assertRaises(ValueError):
            jobs.set_project("job-1", "goal")

    def test_switching_after_the_story_was_read_says_to_read_it_again(self):
        write_json(os.path.join(self.dir, "intake.json"), {"story": {"key": "ABC-1"}})
        info = jobs.set_project("job-1", "goal")
        self.assertIn("read the story again", info.get("note", ""))
        self.assertTrue(info["chosen"])

    def test_the_run_tab_lists_the_jobs_projects_environments(self):
        intake.set_job_project(self.dir, "goal")
        write_json(PC.config_path(self.root, "b2b"), {"stg": {}, "STG_Partner_500": {}})
        write_json(PC.config_path(self.root, "goal"), {"stg": {}, "goalonly": {}})
        with mock.patch.object(jobs, "ROOT", self.root):
            got = jobs.run_targets("job-1")
        self.assertEqual(got["project"], "goal")
        self.assertIn("goalonly", got["envs"])
        self.assertNotIn("STG_Partner_500", got["envs"])

    def test_the_gate_and_the_converter_get_no_project(self):
        intake.set_job_project(self.dir, "goal")
        seen = {}

        class Proc:
            pid = 4243
            returncode = 0

            def poll(self):
                return 0

            def wait(self, *a, **k):
                return 0

        def popen(argv, **kw):
            seen.update(kw.get("env") or {})
            return Proc()
        with mock.patch.object(jobs.subprocess, "Popen", popen), \
                mock.patch.dict(os.environ, {"WORKBENCH_PROJECT": "goal"}):
            try:
                jobs.start("job-1", "verify-all", {})
            except Exception:                            # noqa: BLE001
                pass
        self.assertNotIn("WORKBENCH_PROJECT", seen)

    def test_every_command_of_the_job_carries_its_project(self):
        intake.set_job_project(self.dir, "goal")
        seen = {}

        class Proc:
            pid = 4242
            returncode = 0

            def poll(self):
                return 0

            def wait(self, *a, **k):
                return 0

        def popen(argv, **kw):
            seen.update(kw.get("env") or {})
            return Proc()
        with mock.patch.object(jobs.subprocess, "Popen", popen), \
                mock.patch.object(jobs, "_watch", lambda *a, **k: None, create=True), \
                mock.patch.dict(os.environ, {"WORKBENCH_PROJECT": "b2b"}):
            try:
                jobs.start("job-1", "agent-question", {"--job": "job-1"})
            except Exception:                            # noqa: BLE001
                pass                                     # what follows the launch is not under test
        self.assertEqual(seen.get("WORKBENCH_PROJECT"), "goal",
                         "the job's project, not the server's own environment")


    # ---- the Run tests tab's Project choice ------------------------------

    def start_run(self, options):
        """What jobs.start hands the subprocess for an mvn-test run: (env, argv)."""
        seen = {}

        class Proc:
            pid = 4244
            returncode = 0

            def poll(self):
                return 0

            def wait(self, *a, **k):
                return 0

        def popen(argv, **kw):
            seen["env"] = dict(kw.get("env") or {})
            seen["argv"] = list(argv)
            return Proc()
        with mock.patch.object(jobs.subprocess, "Popen", popen), \
                mock.patch.object(jobs, "_watch", lambda *a, **k: None, create=True), \
                mock.patch.dict(os.environ, {"WORKBENCH_PROJECT": "b2b"}):
            try:
                jobs.start("job-1", "mvn-test", dict(options, **{"--job": "job-1"}))
            except (ValueError, RuntimeError):
                raise
            except Exception:                            # noqa: BLE001
                pass                                     # what follows the launch is not under test
        return seen.get("env") or {}, seen.get("argv") or []

    def test_the_run_tab_lists_the_chosen_projects_environments_not_the_jobs(self):
        intake.set_job_project(self.dir, "goal")
        write_json(PC.config_path(self.root, "b2b"), {"stg": {}, "STG_Partner_500": {}})
        write_json(PC.config_path(self.root, "goal"), {"stg": {}, "goalonly": {}})
        with mock.patch.object(jobs, "ROOT", self.root):
            got = jobs.run_targets("job-1", "B2B")
        self.assertEqual(got["project"], "b2b")
        self.assertEqual(got["job_project"], "goal", "the job's own project is still said")
        self.assertIn("STG_Partner_500", got["envs"])
        self.assertNotIn("goalonly", got["envs"])
        self.assertEqual({p["name"] for p in got["projects"]}, set(PC.PROJECT_FILES))
        with mock.patch.object(jobs, "ROOT", self.root):
            self.assertEqual(jobs.run_targets("job-1", "")["project"], "goal", "none chosen: the job's")
            with self.assertRaises(ValueError):
                jobs.run_targets("job-1", "gaol")

    def test_each_converted_suite_says_its_own_project(self):
        for rel in ("Suites/Group360CreateSTAGE/Group360CreateSTAGE_Regression.xml",
                    "Suites/Eadkafkaevents/Eadkafkaevents_Regression.xml", "Suites/jira/job-1.xml"):
            write_json(os.path.join(self.root, *rel.split("/")), {})
        with mock.patch.object(jobs, "ROOT", self.root):
            suites = {s["path"]: s for s in jobs.run_targets("job-1")["suites"]}
        self.assertEqual(suites["Suites/Group360CreateSTAGE/Group360CreateSTAGE_Regression.xml"]["project"], "goal")
        self.assertEqual(suites["Suites/Eadkafkaevents/Eadkafkaevents_Regression.xml"]["project"], "b2b")
        self.assertNotIn("project", suites["Suites/jira/job-1.xml"], "a story suite is the job's")

    def test_the_chosen_project_wins_over_the_suites_and_the_jobs(self):
        intake.set_job_project(self.dir, "goal")
        env, argv = self.start_run({"--suite": "Suites/Group360CreateSTAGE_Regression.xml", "--project": "b2b"})
        self.assertEqual(env.get("WORKBENCH_PROJECT"), "b2b")
        self.assertIn("--project", argv)
        self.assertEqual(argv[argv.index("--project") + 1], "b2b", "mvnrun gets it for -Dconfig.project")

    def test_with_no_project_chosen_a_converted_suite_runs_with_its_own_and_a_class_with_the_jobs(self):
        env, _ = self.start_run({"--suite": "Suites/Group360CreateSTAGE_Regression.xml"})
        self.assertEqual(env.get("WORKBENCH_PROJECT"), "goal")
        intake.set_job_project(self.dir, "goal")
        env, _ = self.start_run({"--test": "FooTest"})
        self.assertEqual(env.get("WORKBENCH_PROJECT"), "goal")

    def test_a_project_that_is_not_one_starts_nothing(self):
        with self.assertRaises(ValueError):
            self.start_run({"--test": "FooTest", "--project": "gaol"})
        self.assertNotEqual(jobs.read_status("job-1", "mvn-test").get("state"), "running")


class Running(Base):
    def test_goal_refuses_a_block_without_goals_service(self):
        write_json(self.goal, {"stg": {"services": {"hospitality_internal_all_v2": ""}}})
        why = mvnrun.project_preflight(self.root, "goal", "stg")
        self.assertIn("services.hospitality_internal_all_v2", why)
        write_json(self.goal, {"stg": {"services": {"hospitality_internal_all_v2": "https://goal.example.test"}}})
        self.assertIn("services.hospitality_partner_v2", mvnrun.project_preflight(self.root, "goal", "stg"),
                      "where the token is minted must be set too")
        write_json(self.goal, {"stg": {"services": {"hospitality_internal_all_v2": "https://goal.example.test",
                                                    "hospitality_partner_v2": "https://partner.example.test"}}})
        self.assertEqual(mvnrun.project_preflight(self.root, "goal", "stg"), "")
        write_json(self.goal, {"stg": {"goal": {"token_service": "own_token"},
                                       "services": {"hospitality_internal_all_v2": "https://goal.example.test"}}})
        self.assertIn("services.own_token", mvnrun.project_preflight(self.root, "goal", "stg"))

    def test_goal_without_its_file_or_block_is_refused_and_b2b_is_never_checked(self):
        self.assertIn("absent", mvnrun.project_preflight(self.root, "goal", "stg"))
        write_json(self.goal, {"qa": {}})
        self.assertIn("no `stg` block", mvnrun.project_preflight(self.root, "goal", "stg"))
        self.assertEqual(mvnrun.project_preflight(self.root, "b2b", "stg"), "")

    def test_each_project_has_its_run_environment(self):
        wb_file = os.path.join(self.root, settings.WORKBENCH_FILE)
        write_json(wb_file, {"run_env": "STG_Partner_500", "projects": {"goal": {"run_env": "stg"}}})
        self.assertEqual(loop.run_env_for(self.root, {}, "b2b"), "STG_Partner_500")
        self.assertEqual(loop.run_env_for(self.root, {}, "goal"), "stg")
        write_json(wb_file, {})
        self.assertEqual(loop.run_env_for(self.root, {"run_env": "x1"}, "goal"), "stg",
                         "B2B's policy run_env is not GOAL's")
        self.assertEqual(loop.run_env_for(self.root, {"run_env": "x1"}, "b2b"), "x1")
        self.assertEqual(loop.run_env_for(self.root, {"projects": {"goal": {"run_env": "g1"}}}, "goal"), "g1")


class Secrets(Base):
    def test_every_projects_secrets_are_kept_out(self):
        write_json(self.b2b, {"stg": {"jira_config": {"pat": "b2b-token-123456"}}})
        write_json(self.goal, {"stg": {"jira_config": {"pat": "goal-token-654321"}}})
        found = loop.private_config(self.root)
        self.assertIn("b2b-token-123456", found)
        self.assertIn("goal-token-654321", found)

    def test_a_broken_goal_file_is_refused_when_strict(self):
        write_json(self.b2b, {"stg": {}})
        os.makedirs(os.path.dirname(self.goal), exist_ok=True)
        with io.open(self.goal, "w", encoding="utf-8") as fh:
            fh.write("{ not json")
        with self.assertRaises(ValueError) as e:
            loop.private_config(self.root, strict=True)
        self.assertIn("program_configuration.goal.json", str(e.exception))


class SettingsTab(Base):
    def test_a_goal_save_writes_goals_file_and_its_own_run_environment(self):
        write_json(self.b2b, {"stg": {"jira_config": {"pat": "b2b-token-123456"}}})
        write_json(self.goal, {"stg": {"jira_config": {}}, "goalenv": {}})
        wb_file = os.path.join(self.root, settings.WORKBENCH_FILE)
        with mock.patch.object(PC, "ROOT", self.root), mock.patch.dict(os.environ, {"TEST_ENV": "stg"}):
            settings.save({"jira_pat": "goal-token-777777", "run_env": "goalenv"}, self.root, project="goal")
            with io.open(self.goal, encoding="utf-8-sig") as fh:
                self.assertEqual(json.load(fh)["stg"]["jira_config"]["pat"], "goal-token-777777")
            with io.open(self.b2b, encoding="utf-8-sig") as fh:
                self.assertEqual(json.load(fh)["stg"]["jira_config"]["pat"], "b2b-token-123456")
            with io.open(wb_file, encoding="utf-8") as fh:
                wb = json.load(fh)
            self.assertEqual(wb["projects"]["goal"]["run_env"], "goalenv")
            self.assertNotIn("run_env", wb, "B2B's run environment is left as it was")
            st = settings.status(self.root, project="goal")
            self.assertEqual((st["project"], st["run"]["env"]), ("goal", "goalenv"))
            self.assertTrue(st["file"].endswith("program_configuration.goal.json"))


class WhatCursorIsShown(Base):
    SCOPE = {"name": "new-test", "skill": ".cursor/skills/jira-to-restassured/SKILL.md"}

    def block(self, project):
        os.environ["WORKBENCH_PROJECT"] = project
        return loop.skill_and_example_block(loop.ROOT, loop.load_policy(), dict(self.SCOPE))

    def test_a_goal_job_gets_goals_guide_and_golden_test_not_b2bs(self):
        text, shown = self.block("goal")
        self.assertIn("THIS JOB IS A GOAL JOB", text)
        self.assertIn("GOAL_FLOWS.md -----", text)
        self.assertNotIn("B2B_FLOWS.md -----", text)
        self.assertEqual(shown, ["src/test/java/com/hi/api/tests/examples/GoalStoryTestExample.java"])

    def test_a_b2b_job_gets_b2bs(self):
        text, shown = self.block("b2b")
        self.assertIn("THIS JOB IS A B2B JOB", text)
        self.assertIn("B2B_FLOWS.md -----", text)
        self.assertNotIn("GOAL_FLOWS.md -----", text)
        self.assertEqual(shown, ["src/test/java/com/hi/api/tests/examples/StoryTestExample.java"])


class TemplateDrift(unittest.TestCase):
    """A hand-written body that leaves out what the converted bodies of the
    same call always send. Seen live: the story enrolment had no
    address.company and every enrolment was answered 400."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        st = loop.storytests
        st._CONVERTED_SHAPES.pop(self.root, None)
        self.st = st
        conv = os.path.join(self.root, "src", "main", "resources", "templates", "s")
        os.makedirs(conv)
        for i in range(4):
            write_json(os.path.join(conv, f"enroll{i}.json"),
                       {"username": "#u#", "propCode": "X", "address": {"city": "M", "company": "C", "state": "T"},
                        "name": {"firstName": "a", "title": "Mr."}})
        manual = os.path.join(self.root, "src", "test", "resources", "templates", "manual")
        os.makedirs(manual)
        write_json(os.path.join(manual, "old.json"),
                   {"username": "#u#", "propCode": "#p#", "address": {"city": "M", "state": "T"},
                    "name": {"firstName": "a", "title": "Mr."}})
        write_json(os.path.join(manual, "new.json"),
                   {"username": "#u#", "propCode": "#p#", "address": {"city": "M", "state": "T", "company": "#c#"},
                    "name": {"firstName": "a", "title": "Mr."}})

    def issues(self, tpl):
        return self.st._template_drift_issues(self.root, "X.java", f'ManualBody.render("templates/manual/{tpl}.json"')

    def test_a_field_every_converted_body_sends_is_said(self):
        got = self.issues("old")
        self.assertEqual(len(got), 1, got)
        self.assertIn("leaves out address.company", got[0])
        self.assertIn("4 converted bodies of the same call", got[0])

    def test_a_template_that_sends_it_is_fine(self):
        self.assertEqual(self.issues("new"), [])

    def test_no_converted_suites_nothing_to_compare(self):
        shutil.rmtree(os.path.join(self.root, "src", "main"))
        self.st._CONVERTED_SHAPES.pop(self.root, None)
        self.assertEqual(self.issues("old"), [])


class TemplateValueDrift(unittest.TestCase):
    """A hand-written body that sends a value no converted body of the same
    call sends. Seen live: businessProfile.employeeCountRange 0 (copied
    from a reference example that wrote every number as 0) -- the converted
    bodies send 1 -- and every test was answered 400, code 31."""

    BODY = {"name": "#n#", "address": {"city": "Memphis", "postalCode": "38125"},
            "phone": {"phoneType": "mobile"},
            "businessProfile": {"employeeCountRange": 1, "annualHotelSpend": 0, "travelAgencyInUse": True}}

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.st = loop.storytests
        self.st._CONVERTED_SHAPES.pop(self.root, None)
        self.addCleanup(self.st._CONVERTED_SHAPES.pop, self.root, None)
        conv = os.path.join(self.root, "src", "main", "resources", "templates", "s")
        os.makedirs(conv)
        for i in range(4):
            body = json.loads(json.dumps(self.BODY))
            body["address"]["postalCode"] = f"3812{i}"                 # free text: differs per body
            body["businessProfile"]["employeeCountRange"] = 1 if i < 3 else "501-1000"
            body["businessProfile"]["travelAgencyInUse"] = i % 2 == 0   # a flag set both ways
            write_json(os.path.join(conv, f"create{i}.json"), body)
        self.manual = os.path.join(self.root, "src", "test", "resources", "templates", "manual")
        os.makedirs(self.manual)

    def issues(self, **changes):
        body = json.loads(json.dumps(self.BODY))
        for path, value in changes.items():
            node = body
            *parents, leaf = path.split("__")
            for p in parents:
                node = node[p]
            node[leaf] = value
        write_json(os.path.join(self.manual, "mine.json"), body)
        return [i for i in self.st._template_drift_issues(
            self.root, "X.java", 'ManualBody.render("templates/manual/mine.json"') if ": sends " in i]

    def test_the_value_seen_live_is_said(self):
        got = self.issues(businessProfile__employeeCountRange=0)
        self.assertEqual(len(got), 1, got)
        self.assertIn("sends businessProfile.employeeCountRange = 0", got[0])
        self.assertIn("4 of them send 1 (3), \"501-1000\" (1)", got[0])
        self.assertIn("#name#", got[0], "a test of a refused value on purpose has a way out")

    def test_what_the_converted_bodies_send_is_fine(self):
        self.assertEqual(self.issues(), [])
        self.assertEqual(self.issues(businessProfile__employeeCountRange="501-1000"), [])

    def test_a_flag_the_converted_bodies_set_both_ways_is_the_scenarios_choice(self):
        self.assertEqual(self.issues(businessProfile__travelAgencyInUse=False), [])

    def test_free_text_is_not_compared(self):
        self.assertEqual(self.issues(address__postalCode="99999", address__city="Nashville"), [])

    def test_an_enum_word_none_of_them_sends_is_said(self):
        got = self.issues(phone__phoneType="pager")
        self.assertEqual(len(got), 1, got)
        self.assertIn("phone.phoneType = \"pager\"", got[0])

    def test_a_placeholder_is_not_a_value(self):
        self.assertEqual(self.issues(businessProfile__employeeCountRange="#employee_count_range#"), [])


class ProjectsDoNotMix(Base):
    def test_a_test_whose_base_is_the_other_projects_fails_the_check(self):
        st = loop.storytests
        goal_test = "/** DATA_INVENTORY */ class G extends GoalStoryTestBase {}"
        b2b_test = "/** DATA_INVENTORY */ class B extends StoryTestBase {}"
        self.assertIn("GoalStoryTestBase", " ".join(st._project_base_issues("B.java", b2b_test, "goal")))
        self.assertIn("StoryTestBase", " ".join(st._project_base_issues("G.java", goal_test, "b2b")))
        self.assertEqual(st._project_base_issues("G.java", goal_test, "goal"), [])
        self.assertEqual(st._project_base_issues("B.java", b2b_test, "b2b"), [])

    def test_a_converted_suite_runs_with_its_own_projects_configuration(self):
        self.assertEqual(jobs.suite_project("Suites/Group360CreateSTAGE_Regression.xml"), "goal")
        self.assertEqual(jobs.suite_project("Suites/smokePROD_Smoke.xml"), "goal")
        self.assertEqual(jobs.suite_project("Suites/Eadkafkaevents_Regression.xml"), "b2b")

    def test_earlier_job_keys_come_only_from_the_same_project(self):
        storykeys = loop.storykeys
        base = os.path.join(self.root, ".workbench")
        for name, project in (("j-b2b", "b2b"), ("j-goal", "goal"), ("j-here", "goal")):
            write_json(os.path.join(base, name, "intake.json"), {"story": {"key": "ABC-1"}})
            write_json(os.path.join(base, name, "project.json"), {"project": project})
        for name in ("j-b2b", "j-goal"):
            write_json(os.path.join(base, name, "xray-tests.json"),
                       {"tests": {"design:TC-001": {"key": "ABC-9" if name == "j-b2b" else "ABC-8",
                                                   "title": "t", "mock": False}}})
        jobs_seen = [j["job"] for j in storykeys.earlier(os.path.join(base, "j-here"))]
        self.assertEqual(jobs_seen, ["j-goal"], "B2B's job is another Jira")


class GoalChecks(unittest.TestCase):
    def test_the_goal_starter_bodies_have_no_fixed_credential(self):
        st = loop.storytests
        for name in ("goal_create_group.json", "goal_payment.json", "goal_confirm.json"):
            self.assertEqual(st._template_fixed_value_issues(
                loop.ROOT, "G.java", 'String t = "templates/manual/%s";' % name), [], name)

    def test_the_goal_example_passes_the_story_test_checks(self):
        st = loop.storytests
        path = os.path.join(loop.ROOT, "src", "test", "java", "com", "hi", "api", "tests", "examples",
                            "GoalStoryTestExample.java")
        with io.open(path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertEqual(st._shared_helper_issues("G.java", text), [])
        self.assertEqual(st._goal_issues("G.java", st._strip_comments(text)), [])

    def test_a_frozen_date_in_a_goal_test_fails(self):
        st = loop.storytests
        said = st._goal_issues("G.java", 'class G extends GoalStoryTestBase { String d = "2026-12-03"; }')
        self.assertIn("2026-12-03", " ".join(said))
        self.assertEqual(st._goal_issues("B.java", 'class B extends StoryTestBase { String d = "2026-12-03"; }'),
                         [], "only a GOAL test")

    def test_its_own_copy_of_a_goal_helper_fails(self):
        st = loop.storytests
        said = st._shared_helper_issues("G.java", "\n".join([
            "class G extends GoalStoryTestBase {",
            '  @Test(groups = {"jira"}) public void a() {}',
            "  private String findAvailability(String p) { return p; }",
            "}", ""]))
        self.assertIn("findAvailability", " ".join(said))


if __name__ == "__main__":
    unittest.main(verbosity=1)
