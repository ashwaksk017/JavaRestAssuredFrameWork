"""Creating a job's cases as Xray Test issues: off unless switched on, one
story named twice, nothing sent that a stand-in made, every key written
down as it comes back, nothing created twice.

    python tools/agent/test_xraytests.py

Jira is a dictionary here; nothing leaves the machine.
"""
from __future__ import annotations

import os as _os
_os.environ["WORKBENCH_MOCK"] = "0"

import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


xt = _load("xraytests_under_test", os.path.join(HERE, "xraytests.py"))
pb, loop, search = xt.publish, xt.loop, xt.search

DESIGN = {"kind": "design", "mock": False, "brief": "abc", "test_cases": [
    {"id": "TC-001", "title": "Create a rate returns 201", "endpoint": "POST /groups/{groupId}/rates",
     "type": "positive", "priority": "high", "preconditions": "a group exists",
     "steps": [{"action": "POST a valid rate", "data": "a valid group id",
                "expected": "201 and the body carries rateId"}],
     "requirement_refs": ["AC-1"], "endpoint_in_spec": True},
    {"id": "TC-002", "title": "A rate for an unknown group is refused *now* {really}",
     "endpoint": "POST /groups/{groupId}/rates", "type": "negative", "priority": "medium",
     "preconditions": "", "steps": [{"action": "POST with an unknown group id", "data": "",
                                     "expected": "404"}],
     "requirement_refs": [], "endpoint_in_spec": True},
    {"id": "TC-003", "title": "no steps here", "endpoint": "GET /x", "steps": []},
]}
MANUAL = {"kind": "manual-tests", "mock": False, "inventory": {"mock_jira": False},
          "new_cases": [{"id": "MT-001", "title": "Group booking", "module": "Booking",
                         "priority": "P1", "preconditions": "signed in",
                         "steps": [{"action": "Book 10 rooms", "data": "10",
                                    "expected": "the group flow opens"}]}],
          "updates": []}
CONN = {"base": "https://jira.example.test", "bases": ["https://jira.example.test"],
        "token": "tok-SECRET-0123456789", "timeout": 5, "api": "/rest/api/2", "cleartext": False}
ON = {"publish": {"create_tests": True}}


class Jira:
    """A Jira that holds one story and creates whatever it is asked to."""

    def __init__(self, story="ABC-12", project="ABC", refuse_steps=False, refuse_link=False,
                 refuse_create=None, gone=()):
        self.story, self.project = story, project
        self.refuse_steps, self.refuse_link, self.refuse_create = refuse_steps, refuse_link, refuse_create
        self.gone = set(gone)
        self.calls, self.created, self.steps, self.links = [], [], {}, []
        self.n = 900
        self.linked = {}                 # key -> (summary, [(action, data, expected)])

    def transport(self, method, url, token, timeout, body=None):
        assert token == CONN["token"]
        assert url.startswith(CONN["base"]), url
        self.calls.append((method, url, body))
        path = url.split(CONN["base"], 1)[1]
        if method == "GET" and "/rest/raven/2.0/api/test/" in path:
            key = path.split("/test/")[1].split("/")[0]
            return [{"step": a, "data": d, "result": e} for a, d, e in self.linked[key][1]]
        if "/rest/raven/" in path:
            if self.refuse_steps:
                raise search.JiraError(404, "no such api")
            key = path.split("/test/")[1].split("/")[0]
            self.steps.setdefault(key, []).append(body)
            return {"id": len(self.steps[key])}
        if method == "POST" and path.endswith("/issue"):
            if self.refuse_create is not None and len(self.created) >= self.refuse_create:
                raise search.JiraError(400, "field 'issuetype' is invalid")
            self.n += 1
            key = f"{self.project}-{self.n}"
            self.created.append((key, body))
            return {"id": str(self.n), "key": key}
        if method == "POST" and path.endswith("/issueLink"):
            if self.refuse_link:
                raise search.JiraError(404, "no such link type")
            self.links.append(body)
            return None
        if method == "GET" and "/issue/" in path:
            key = path.split("/issue/")[1].split("?")[0]
            if key in self.gone:
                raise search.JiraError(404, "Issue Does Not Exist")
            if key == self.story:
                tested = {"name": "Tests", "inward": "is tested by", "outward": "tests"}
                return {"key": key, "fields": {"summary": "The story", "project": {"key": self.project},
                                               "issuetype": {"name": "Story"},
                                               "issuelinks": [
                                                   {"type": tested, "inwardIssue": {"key": k, "fields": {
                                                       "summary": s, "issuetype": {"name": "Test"}}}}
                                                   for k, (s, _st) in self.linked.items()]}}
            return {"key": key, "fields": {"summary": "a test"}}
        raise AssertionError(f"unexpected {method} {path}")


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="b2b-xray-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.job = os.path.join(self.root, ".workbench", "j1")
        os.makedirs(self.job)
        saved = (loop.intake.ROOT, pb.ROOT, xt.ROOT)
        loop.intake.ROOT = pb.ROOT = xt.ROOT = self.root
        self.addCleanup(lambda: (setattr(loop.intake, "ROOT", saved[0]),
                                 setattr(pb, "ROOT", saved[1]), setattr(xt, "ROOT", saved[2])))
        self.put("design.json", json.dumps(DESIGN))
        self.put("manual-result.json", json.dumps(MANUAL))
        # The design fixture says it was made from brief "abc"; so says the job.
        self.addCleanup(setattr, loop, "brief_fingerprint", loop.brief_fingerprint)
        loop.brief_fingerprint = lambda job_dir: "abc"
        self.jira = Jira()
        self.approve_all()

    def approve_all(self):
        xt.casereview.update(self.job, "design", ["TC-001", "TC-002"], state="approved")
        xt.casereview.update(self.job, "manual", ["MT-001"], state="approved")

    def put(self, name, text):
        with io.open(os.path.join(self.job, name), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)

    def read(self, name):
        with io.open(os.path.join(self.job, name), encoding="utf-8") as fh:
            return fh.read()

    def create(self, what="design", story="ABC-12", confirm=None, cfg=ON, xray=None, **kw):
        return xt.create("j1", what, story, story if confirm is None else confirm, self.root,
                         conn=dict(CONN), cfg=cfg, xray_cfg=xray or {"baseUrl": ""},
                         transport=self.jira.transport, **kw)


class Refusals(Base):
    def test_off_unless_switched_on(self):
        for cfg in ({}, {"publish": {}}, {"publish": {"create_tests": "true"}},
                    {"publish": {"create_tests": 1}}):
            with self.assertRaises(xt.Refused) as got:
                self.create(cfg=cfg)
            self.assertIn("switched off", str(got.exception))
        self.assertEqual(self.jira.calls, [])

    def test_the_story_key_twice_the_same(self):
        with self.assertRaises(xt.Refused):
            self.create(confirm="ABC-13")
        with self.assertRaises(xt.Refused):
            self.create(story="not a key")
        with self.assertRaises(xt.Refused):
            self.create(what="review")
        self.assertEqual(self.jira.calls, [])

    def test_a_project_restriction_is_the_storys_project_as_jira_has_it(self):
        with self.assertRaises(xt.Refused) as got:
            self.create(cfg={"publish": {"create_tests": True, "projects": ["XYZ"]}})
        self.assertIn("not in jira_config.publish.projects", str(got.exception))
        self.assertEqual(self.jira.created, [])
        with self.assertRaises(xt.Refused):
            self.create(cfg={"publish": {"create_tests": True, "projects": "ABC"}})

    def test_xray_cloud_is_refused_with_the_way_round(self):
        for xray in ({"flavour": "cloud"}, {"baseUrl": "https://xray.cloud.getxray.app"},
                     {"clientId": "a", "clientSecret": "b"}):
            with self.assertRaises(xt.Refused) as got:
                self.create(xray=xray)
            self.assertIn("CLOUD", str(got.exception))
        self.assertEqual(self.jira.calls, [])

    def test_a_result_a_stand_in_made_is_not_sent(self):
        self.put("design.json", json.dumps(dict(DESIGN, mock=True)))
        with self.assertRaises(xt.Refused) as got:
            self.create()
        self.assertIn("stand-in", str(got.exception))
        self.put("manual-result.json", json.dumps(dict(MANUAL, inventory={"mock_jira": True})))
        with self.assertRaises(xt.Refused):
            self.create(what="manual")

    def test_a_private_value_in_a_case_stops_everything(self):
        os.makedirs(os.path.join(self.root, "src", "main", "resources"))
        with io.open(os.path.join(self.root, "src", "main", "resources",
                                  "program_configuration.json"), "w", encoding="utf-8") as fh:
            json.dump({"jira_config": {"pat": "tok-SECRET-0123456789", "base_urls": [CONN["base"]]}}, fh)
        d = json.loads(json.dumps(DESIGN))
        d["test_cases"][1]["steps"][0]["data"] = "Authorization: Bearer tok-SECRET-0123456789"
        self.put("design.json", json.dumps(d))
        with self.assertRaises(xt.Refused) as got:
            self.create()
        self.assertIn("private values", str(got.exception))
        self.assertEqual(self.jira.created, [])

    def test_http_is_refused(self):
        conn = dict(CONN, base="http://jira.example.test", cleartext=True)
        with self.assertRaises(xt.Refused):
            xt.create("j1", "design", "ABC-12", "ABC-12", self.root, conn=conn, cfg=ON,
                      xray_cfg={}, transport=self.jira.transport)

    def test_a_story_jira_does_not_have_creates_nothing(self):
        self.jira.gone.add("ABC-12")
        with self.assertRaises(search.JiraError):
            self.create()
        self.assertEqual(self.jira.created, [])

    def test_a_result_with_no_usable_case_is_said(self):
        self.put("design.json", json.dumps(dict(DESIGN, test_cases=[DESIGN["test_cases"][2]])))
        with self.assertRaises(xt.Refused) as got:
            self.create()
        self.assertIn("no case with a title and steps", str(got.exception))

    def test_the_expectation_of_the_page_is_checked(self):
        with self.assertRaises(xt.Refused):
            self.create(expect="mock")


class Creating(Base):
    def test_one_issue_a_case_with_steps_linked_to_the_story_in_the_storys_project(self):
        r = self.create()
        self.assertTrue(r["ok"])
        self.assertEqual([c["id"] for c in r["created"]], ["TC-001", "TC-002"])
        self.assertEqual([c["key"] for c in r["created"]], ["ABC-901", "ABC-902"])
        key, body = self.jira.created[0]
        self.assertEqual(body["fields"]["project"], {"key": "ABC"})
        self.assertEqual(body["fields"]["issuetype"], {"name": "Test"})
        self.assertEqual(body["fields"]["summary"], "Create a rate returns 201")
        self.assertIn("ABC-12", body["fields"]["description"])
        self.assertIn("Preconditions:\na group exists", body["fields"]["description"])
        self.assertEqual(self.jira.steps["ABC-901"],
                         [{"step": "POST a valid rate", "data": "a valid group id",
                           "result": "201 and the body carries rateId"}])
        self.assertEqual(self.jira.links[0], {"type": {"name": "Tests"},
                                              "inwardIssue": {"key": "ABC-12"},
                                              "outwardIssue": {"key": "ABC-901"}})
        self.assertEqual((r["created"][0]["steps"], r["created"][0]["linked"]), (1, True))
        self.assertIn("2 test(s) created in ABC for ABC-12", r["message"])

    def test_the_step_call_goes_to_xrays_own_api_and_can_be_set(self):
        self.create()
        step_calls = [(m, u) for m, u, _b in self.jira.calls if "/rest/raven/" in u]
        self.assertEqual(step_calls[0], ("PUT", f"{CONN['base']}/rest/raven/1.0/api/test/ABC-901/step"))
        self.jira = Jira()
        for name in ("xray-tests.json",):
            os.remove(os.path.join(self.job, name))
        self.create(cfg={"publish": {"create_tests": True,
                                     "xray_step_call": "POST /rest/raven/2.0/api/test/{key}/steps"}})
        step_calls = [(m, u) for m, u, _b in self.jira.calls if "/rest/raven/" in u]
        self.assertEqual(step_calls[0], ("POST", f"{CONN['base']}/rest/raven/2.0/api/test/ABC-901/steps"))
        with self.assertRaises(xt.Refused):
            self.create(cfg={"publish": {"create_tests": True, "xray_step_call": "DELETE /x/{key}"}})

    def test_markup_in_a_title_or_a_step_is_text_in_jira(self):
        self.create()
        _key, body = self.jira.created[1]
        self.assertEqual(body["fields"]["summary"], "A rate for an unknown group is refused *now* {really}")
        self.assertNotIn("{color", body["fields"]["description"])
        self.assertIn("\\{", xt.description_of(
            {"id": "TC-1", "title": "t", "preconditions": "{code}x{code}", "endpoint": "", "priority": "",
             "type": "", "steps": [], "refs": []}, "ABC-12", "design"))

    def test_the_keys_are_written_down_as_they_come_back(self):
        self.jira.refuse_create = 1                      # the second create is refused
        r = self.create()
        self.assertFalse(r["ok"])
        self.assertEqual([c["key"] for c in r["created"]], ["ABC-901"])
        self.assertEqual(r["failed"][0]["id"], "TC-002")
        self.assertIn("stopped at TC-002", r["message"])
        kept = json.loads(self.read("xray-tests.json"))
        self.assertEqual(kept["tests"]["design:TC-001"]["key"], "ABC-901")
        self.assertNotIn("design:TC-002", kept["tests"])
        log = self.read("xray-tests.log")
        self.assertIn("ATTEMPT issue design:TC-001", log)
        self.assertIn("DONE issue design:TC-001 -> ABC-901", log)
        self.assertIn("REFUSED BY JIRA issue design:TC-002", log)
        self.assertLess(log.index("ATTEMPT issue design:TC-001"), log.index("DONE issue design:TC-001"))

    def test_a_second_run_creates_only_what_has_no_key(self):
        self.jira.refuse_create = 1
        self.create()
        self.jira.refuse_create = None
        r = self.create()
        self.assertTrue(r["ok"])
        self.assertEqual([c["id"] for c in r["created"]], ["TC-002"])
        self.assertEqual([c["id"] for c in r["kept"]], ["TC-001"])
        self.assertEqual(len(self.jira.created), 2)
        self.assertEqual(xt.keys_for(self.job, "design"), {"TC-001": "ABC-901", "TC-002": "ABC-902"})

    def test_a_test_deleted_in_jira_is_created_again_and_said(self):
        self.create()
        self.jira.gone.add("ABC-901")
        r = self.create()
        self.assertEqual([c["id"] for c in r["created"]], ["TC-001"])
        self.assertEqual(r["created"][0]["key"], "ABC-903")
        self.assertIn("GONE ABC-901", self.read("xray-tests.log"))

    def test_a_case_that_changed_since_is_said_not_updated(self):
        self.create()
        d = json.loads(json.dumps(DESIGN))
        d["test_cases"][0]["steps"][0]["expected"] = "200 now"
        self.put("design.json", json.dumps(d))
        r = self.create()
        self.assertEqual([c["id"] for c in r["changed"]], ["TC-001"])
        self.assertIn("NOT updated", r["changed"][0]["note"])
        self.assertEqual(len(self.jira.created), 2)

    def test_steps_or_the_link_refused_do_not_lose_the_issue(self):
        self.jira.refuse_steps = True
        self.jira.refuse_link = True
        r = self.create()
        self.assertTrue(r["ok"])
        row = r["created"][0]
        self.assertEqual((row["steps"], row["linked"]), (0, False))
        self.assertIn("0 of 1 steps were added", row["note"])
        self.assertIn("Not linked to ABC-12", row["note"])
        self.assertIn("2 created with steps or the link missing", r["message"])
        self.assertEqual(xt.keys_for(self.job, "design")["TC-001"], "ABC-901")

    def test_manual_cases_are_created_the_same_way_and_kept_apart(self):
        r = self.create(what="manual")
        self.assertEqual([c["key"] for c in r["created"]], ["ABC-901"])
        self.assertEqual(xt.keys_for(self.job, "manual"), {"MT-001": "ABC-901"})
        self.assertEqual(xt.keys_for(self.job, "design"), {})
        self.assertIn("manual test cases for ABC-12", self.jira.created[0][1]["fields"]["description"])

    def test_the_project_is_the_storys_not_the_keys_prefix(self):
        self.jira.project = "QA"
        r = self.create()
        self.assertEqual(r["project"], "QA")
        self.assertEqual(self.jira.created[0][1]["fields"]["project"], {"key": "QA"})

    def test_the_token_is_in_no_file_of_the_job(self):
        self.create()
        for name in os.listdir(self.job):
            self.assertNotIn(CONN["token"], self.read(name), name)

    def test_the_attempt_is_on_record_before_the_first_write(self):
        seen = []
        self.create(on_attempt=lambda payload: seen.append(dict(payload)))
        self.assertEqual(len(seen), 1)
        self.assertIsNone(seen[0]["ok"])
        self.assertIn("outcome is not known", seen[0]["message"])


class FoundByTheReview(Base):
    def test_a_repeated_case_id_is_refused_before_anything_is_sent(self):
        d = json.loads(json.dumps(DESIGN))
        d["test_cases"][1]["id"] = "TC-001"
        self.put("design.json", json.dumps(d))
        with self.assertRaises(xt.Refused) as got:
            self.create()
        self.assertIn("one id to more than one case", str(got.exception))
        self.assertEqual(self.jira.created, [])

    def test_a_case_renumbered_by_a_regeneration_keeps_its_test(self):
        self.create()
        d = json.loads(json.dumps(DESIGN))
        d["test_cases"][0]["id"], d["test_cases"][1]["id"] = "TC-002", "TC-001"
        self.put("design.json", json.dumps(d))
        r = self.create()
        self.assertEqual(r["created"], [])
        self.assertEqual({c["id"]: c["key"] for c in r["kept"]}, {"TC-002": "ABC-901", "TC-001": "ABC-902"})
        self.assertIn("was TC-001 when ABC-901 was created", r["kept"][0]["note"])
        self.assertEqual(xt.keys_for(self.job, "design"), {"TC-001": "ABC-902", "TC-002": "ABC-901"})
        self.assertIn("MOVED ABC-901", self.read("xray-tests.log"))

    def test_a_project_name_that_is_not_a_key_stops_everything(self):
        real = self.jira.transport

        def odd(method, url, token, timeout, body=None):
            got = real(method, url, token, timeout, body)
            if method == "GET" and "ABC-12" in url:
                got["fields"]["project"] = {"key": "ABC\nFAKE LINE"}
            return got
        self.jira.transport = odd
        with self.assertRaises(xt.Refused) as got:
            self.create()
        self.assertIn("not a project key", str(got.exception))
        self.assertEqual(self.jira.created, [])
        self.assertNotIn("FAKE LINE", self.read("xray-tests.log") if os.path.isfile(
            os.path.join(self.job, "xray-tests.log")) else "")

    def test_a_log_line_is_one_line_and_a_locked_log_loses_nothing(self):
        xt._log(self.job, "a\r\nb\x00c")
        self.assertEqual(self.read("xray-tests.log").strip().split("  ", 1)[1], "a b c")
        xt._log(os.path.join(self.job, "no-such-dir", "x"), "y")   # must not raise

    def test_the_status_says_a_stand_ins_result_will_not_be_sent(self):
        real = (search.mock_jira, search.projectconfig.section)
        self.addCleanup(lambda: (setattr(search, "mock_jira", real[0]),
                                 setattr(search.projectconfig, "section", real[1])))
        search.mock_jira = lambda: False
        search.projectconfig.section = lambda name: ({"publish": {"create_tests": True}}
                                                     if name == "jira_config" else {}, "t")
        self.put("design.json", json.dumps(dict(DESIGN, mock=True)))
        st = xt.status("j1", self.root)
        self.assertEqual(st["real"], {"design": False, "manual": True})

    def test_a_broken_mock_switch_is_a_problem_and_not_mock(self):
        real = search.mock_jira
        self.addCleanup(setattr, search, "mock_jira", real)

        def broken():
            raise search.Refused("mock.json is broken")
        search.mock_jira = broken
        st = xt.status("j1", self.root)
        self.assertIn("broken", st["problem"])
        self.assertFalse(st["mock_jira"])
        self.assertFalse(st["create_tests"])


class TheReviewStep(Base):
    def test_nothing_is_created_until_a_case_is_approved(self):
        os.remove(os.path.join(self.job, xt.casereview.FILE))
        with self.assertRaises(xt.Refused) as got:
            self.create()
        self.assertIn("no case is approved yet", str(got.exception))
        self.assertIn("2 proposed", str(got.exception))
        self.assertEqual(self.jira.created, [])

    def test_only_the_approved_cases_are_created_with_the_corrected_title(self):
        xt.casereview.update(self.job, "design", "TC-002", state="rejected", note="covered by TC-001")
        xt.casereview.update(self.job, "design", "TC-001", title="Create a rate: 201 and the id")
        r = self.create()
        self.assertEqual([c["id"] for c in r["created"]], ["TC-001"])
        self.assertEqual(self.jira.created[0][1]["fields"]["summary"], "Create a rate: 201 and the id")

    def test_the_status_counts_the_review(self):
        xt.casereview.update(self.job, "design", "TC-002", state="rejected")
        real = (search.mock_jira, search.projectconfig.section)
        self.addCleanup(lambda: (setattr(search, "mock_jira", real[0]),
                                 setattr(search.projectconfig, "section", real[1])))
        search.mock_jira = lambda: False
        search.projectconfig.section = lambda name: ({"publish": {"create_tests": True}}
                                                     if name == "jira_config" else {}, "t")
        st = xt.status("j1", self.root)
        self.assertEqual(st["review"]["design"]["approved"], 1)
        self.assertEqual(st["review"]["design"]["rejected"], 1)
        self.assertEqual(st["review"]["manual"]["approved"], 1)


class Status(Base):
    def test_it_says_what_could_be_created_and_what_already_was(self):
        self.put("intake.json", json.dumps({"story": {"key": "abc-12"}}))
        real = (search.mock_jira, search.projectconfig.section)
        self.addCleanup(lambda: (setattr(search, "mock_jira", real[0]),
                                 setattr(search.projectconfig, "section", real[1])))
        search.mock_jira = lambda: False
        search.projectconfig.section = lambda name: ({"publish": {"create_tests": True}}
                                                     if name == "jira_config" else {}, "test")
        st = xt.status("j1", self.root)
        self.assertEqual((st["create_tests"], st["flavour"], st["story"]), (True, "server", "ABC-12"))
        self.assertEqual(st["cases"], {"design": 2, "manual": 1})
        self.assertEqual(st["keys"], {"design": {}, "manual": {}})
        self.create()
        st = xt.status("j1", self.root)
        self.assertEqual(st["keys"]["design"], {"TC-001": "ABC-901", "TC-002": "ABC-902"})
        search.projectconfig.section = lambda name: ({"publish": {"create_tests": True}}
                                                     if name == "jira_config" else {"flavour": "cloud"}, "t")
        self.assertFalse(xt.status("j1", self.root)["create_tests"])

    def test_a_broken_setting_is_a_problem_not_a_crash(self):
        real = (search.mock_jira, search.projectconfig.section)
        self.addCleanup(lambda: (setattr(search, "mock_jira", real[0]),
                                 setattr(search.projectconfig, "section", real[1])))
        search.mock_jira = lambda: False
        search.projectconfig.section = lambda name: ({"publish": {"create_tests": True,
                                                                  "projects": "nope"}}, "t")
        st = xt.status("j1", self.root)
        self.assertIn("projects", st["problem"])
        self.assertFalse(st["create_tests"])


class AfterTheStoryChanged(Base):
    """A design made from an earlier reading is not created in Jira, and a
    test whose case is gone is named."""

    def test_a_design_from_an_earlier_brief_is_refused_before_anything_is_created(self):
        loop.brief_fingerprint = lambda job_dir: "the story was read again"
        with self.assertRaises(xt.Refused) as got:
            self.create()
        self.assertIn("earlier reading of the story", str(got.exception))
        self.assertEqual(self.jira.created, [], "nothing reached Jira")
        st = xt.status("j1", self.root)
        self.assertTrue(st["stale"]["design"])
        self.assertFalse(st["stale"]["manual"], "a manual result has no brief to be stale against")

    def test_a_key_whose_case_is_gone_is_an_orphan_in_the_status(self):
        self.put("xray-tests.json", json.dumps({"kind": "xray-tests", "tests": {
            "design:TC-001": {"key": "ABC-901", "mock": False, "fingerprint": "x"},
            "design:TC-077": {"key": "ABC-977", "mock": False, "fingerprint": "y"}}}))
        st = xt.status("j1", self.root)
        self.assertEqual(st["orphans"]["design"], {"TC-077": "ABC-977"})
        self.assertEqual(st["keys"]["design"], {"TC-001": "ABC-901", "TC-077": "ABC-977"})
        self.assertFalse(st["stale"]["design"])


class Mock(Base):
    def setUp(self):
        super().setUp()
        os.environ["WORKBENCH_MOCK"] = "1"
        self.addCleanup(os.environ.__setitem__, "WORKBENCH_MOCK", "0")

    def test_the_stand_in_creates_on_this_machine_and_its_keys_stay_apart_from_real_ones(self):
        self.put("design.json", json.dumps(dict(DESIGN, mock=True)))
        r = xt.create("j1", "design", "XYZ-7", "XYZ-7", self.root)
        with io.open(os.path.join(self.job, loop.CSV_XRAY_LOG), encoding="utf-8") as fh:
            self.assertIn("patch skipped", fh.read())
        with io.open(os.path.join(self.job, xt.LOG), encoding="utf-8") as fh:
            self.assertIn("csv-xray: patch skipped", fh.read())
        self.assertTrue(r["ok"] and r["mock"])
        self.assertEqual([c["key"] for c in r["created"]], ["XYZ-9001", "XYZ-9002"])
        self.assertTrue(all(c["linked"] and c["steps"] == 1 for c in r["created"]))
        self.assertIn("no Jira was contacted", r["message"])
        self.assertEqual(xt.keys_for(self.job, "design", mocked=True)["TC-001"], "XYZ-9001")
        self.assertEqual(xt.keys_for(self.job, "design", mocked=False), {})
        again = xt.create("j1", "design", "XYZ-7", "XYZ-7", self.root)
        self.assertEqual(again["created"], [])
        self.assertEqual(len(again["kept"]), 2)
        self.assertEqual(sorted(os.listdir(os.path.join(self.root, ".workbench"))),
                         ["j1", "mock-jira.json"])
        self.assertTrue(xt.status("j1", self.root)["mock_jira"])


class TheAgentLoop(Base):
    """What the Agentic Test Creation step does with the keys."""

    def test_the_prompt_carries_the_key_of_a_designed_case(self):
        self.create()
        real = loop.brief_fingerprint
        self.addCleanup(setattr, loop, "brief_fingerprint", real)
        loop.brief_fingerprint = lambda job_dir: "abc"
        text, note = loop.designed_cases(self.job, keys=loop.xray_keys(self.job, mocked=False))
        self.assertIn("TC-001 [positive, high] [Xray ABC-901] [groups: jira, regression, smoke] POST", text)
        self.assertIn("2 with an Xray key", note)
        self.assertEqual(loop.xray_keys(self.job, mocked=True), {})

    def test_once_reviewed_only_approved_cases_marked_to_automate_reach_the_prompt(self):
        self.create()
        real = loop.brief_fingerprint
        self.addCleanup(setattr, loop, "brief_fingerprint", real)
        loop.brief_fingerprint = lambda job_dir: "abc"
        xt.casereview.update(self.job, "design", "TC-002", state="rejected")
        xt.casereview.update(self.job, "design", "TC-001", title="Create a rate, corrected")
        text, note = loop.designed_cases(self.job, keys=loop.xray_keys(self.job, mocked=False))
        self.assertIn("TC-001", text)
        self.assertIn("Create a rate, corrected", text)
        self.assertNotIn("TC-002", text)
        self.assertIn("2 left out by the review", note)      # TC-002 rejected, TC-003 never approved
        xt.casereview.update(self.job, "design", "TC-001", automate=False)
        text, _note = loop.designed_cases(self.job)
        self.assertEqual(text, "")

    def test_with_nothing_decided_every_proposed_case_reaches_the_prompt(self):
        os.remove(os.path.join(self.job, xt.casereview.FILE))
        real = loop.brief_fingerprint
        self.addCleanup(setattr, loop, "brief_fingerprint", real)
        loop.brief_fingerprint = lambda job_dir: "abc"
        text, note = loop.designed_cases(self.job)
        self.assertIn("TC-001", text)
        self.assertIn("TC-002", text)
        self.assertNotIn("left out", note)

    def test_a_rejected_cases_key_is_not_looked_for_in_the_files(self):
        """The case was never handed to Cursor, so its key missing from the
        files is not a gap."""
        self.create()
        real = loop.brief_fingerprint
        self.addCleanup(setattr, loop, "brief_fingerprint", real)
        loop.brief_fingerprint = lambda job_dir: "abc"
        xt.casereview.update(self.job, "design", "TC-002", state="rejected")
        self.assertEqual(loop.designed_case_ids(self.job), {"TC-001"})
        handed = {c: k for c, k in loop.xray_keys(self.job, mocked=False).items()
                  if c in loop.designed_case_ids(self.job)}
        self.assertEqual(handed, {"TC-001": "ABC-901"})

    def test_the_keys_that_landed_in_the_written_files_are_told_from_those_that_did_not(self):
        os.makedirs(os.path.join(self.root, "csv"))
        with io.open(os.path.join(self.root, "csv", "a.csv"), "w", encoding="utf-8") as fh:
            # A long sheet: the key in its last row counts as much as one in the first.
            fh.write("test_case_id,jira_xray_id,note\n" + "".join(
                f"r{n},,{'x' * 100}\n" for n in range(1200)) + "x,ABC-901,\ny,ABC-9010,\n")
        got = loop.xray_landed(self.root, ["csv/a.csv", "notes.txt"],
                               {"TC-001": "ABC-901", "TC-002": "ABC-902", "TC-003": "ABC-9010"})
        self.assertEqual(got["found"], {"TC-001": "ABC-901", "TC-003": "ABC-9010"})
        self.assertEqual(got["missing"], {"TC-002": "ABC-902"})


class WhatTheStoryHas(Base):
    """Seen: Design the tests wrote cases for a story whose tests a person
    had written, and Create in Xray made a second set beside them."""

    def made(self):
        return [b["fields"]["summary"] for _k, b in self.jira.created]

    def test_a_case_with_the_title_of_a_linked_test_takes_its_key_and_is_not_created(self):
        self.jira.linked["ABC-50"] = ("create a RATE returns 201.", [("POST it", "", "201")])
        r = self.create()
        self.assertEqual([(f["id"], f["key"], f["how"]) for f in r["found"]], [("TC-001", "ABC-50", "title")])
        self.assertEqual(len(self.jira.created), 1, "only TC-002 is new")
        self.assertNotIn("Create a rate returns 201", self.made())
        entry = xt.read_keys(self.job)["tests"]["design:TC-001"]
        self.assertEqual((entry["key"], entry["found"]), ("ABC-50", "title"))
        self.assertEqual(entry["base"]["steps"], [{"action": "POST it", "data": "", "expected": "201"}],
                         "the base is Xray's: Update in Xray offers our steps as the change")
        self.assertEqual(xt.keys_for(self.job, "design", False)["TC-001"], "ABC-50",
                         "the key the Agentic step and the data sheet get")
        self.assertIn("already linked to ABC-12", r["message"])

    def test_the_same_steps_under_another_title_is_the_same_test(self):
        self.jira.linked["ABC-51"] = ("Rates: happy path", [("POST a valid rate", "x",
                                                             "201 and the body carries rateId")])
        r = self.create()
        self.assertEqual([(f["id"], f["how"]) for f in r["found"]], [("TC-001", "steps")])

    def test_a_look_alike_is_held_neither_created_nor_taken(self):
        self.jira.linked["ABC-52"] = ("Create a rate returns 201 Created", [("other", "", "other")])
        r = self.create()
        self.assertEqual([(h["id"], h["key"]) for h in r["held"]], [("TC-001", "ABC-52")])
        self.assertNotIn("TC-001", xt.keys_for(self.job, "design", False))
        self.assertNotIn("Create a rate returns 201", self.made())
        self.assertTrue(r["ok"])
        self.assertIn("NOT created", r["message"])

    def test_a_person_says_which_and_the_next_run_does_it(self):
        self.jira.linked["ABC-52"] = ("Create a rate returns 201 Created", [("other", "", "other")])
        self.create()
        r = self.create(adopt={"TC-001": "ABC-52"})
        self.assertEqual([(f["id"], f["key"], f["how"]) for f in r["found"]], [("TC-001", "ABC-52", "chosen")])
        self.assertEqual(r["held"], [])

    def test_create_anyway_makes_a_new_test(self):
        self.jira.linked["ABC-52"] = ("Create a rate returns 201 Created", [("other", "", "other")])
        r = self.create(create_anyway=["TC-001"])
        self.assertIn("Create a rate returns 201", self.made())
        self.assertEqual(r["held"], [])

    def test_a_key_the_story_does_not_have_is_refused_before_anything_is_created(self):
        self.jira.linked["ABC-52"] = ("something else entirely", [("x", "", "y")])
        with self.assertRaises(xt.Refused):
            self.create(adopt={"TC-001": "ABC-99"})
        self.assertEqual(self.jira.created, [])

    def test_a_linked_test_this_job_already_holds_is_not_given_to_another_case(self):
        self.jira.linked["ABC-50"] = ("Create a rate returns 201", [("POST it", "", "201")])
        self.create()
        self.jira.linked["ABC-50"] = ("A rate for an unknown group is refused now really", [])
        r = self.create()
        self.assertEqual(r["found"], [], "ABC-50 is TC-001's: not TC-002's too")

    def test_nothing_linked_creates_as_before(self):
        r = self.create()
        self.assertEqual((len(self.jira.created), r["found"], r["held"]), (2, [], []))

    def test_the_choices_are_read_and_a_bad_one_is_refused(self):
        self.assertEqual(xt.choices("TC-003=abc-12, TC-005=ABC-13", "TC-004"),
                         ({"TC-003": "ABC-12", "TC-005": "ABC-13"}, ["TC-004"]))
        for bad in (("TC-003", ""), ("TC-003=nokey", ""), ("", "-x"), ("TC-1=ABC-1", "TC-1")):
            with self.assertRaises(xt.Refused):
                xt.choices(*bad)


class Declared(unittest.TestCase):
    def test_the_two_commands_are_runnables_with_fixed_options(self):
        jobs = _load("xray_jobs_under_test", os.path.join(HERE, "jobs.py"))
        self.assertEqual(set(jobs.RUNNABLES["xray-create"]["options"]),
                         {"--job", "--what", "--story", "--confirm-story", "--expect",
                          "--adopt", "--create-anyway"})
        self.assertEqual(jobs.RESULT_FILES["xray-create"], "xray-result.json")
        self.assertNotIn("xray-status", jobs.RESULT_FILES)

    def test_the_command_writes_its_result_and_says_it(self):
        root = tempfile.mkdtemp(prefix="b2b-xray-cmd-")
        self.addCleanup(shutil.rmtree, root, True)
        job = os.path.join(root, ".workbench", "j1")
        os.makedirs(job)
        with io.open(os.path.join(job, "design.json"), "w", encoding="utf-8") as fh:
            # made from the brief this job has (none): the real fingerprint
            json.dump(dict(DESIGN, brief=loop.brief_fingerprint(job)), fh)
        xt.casereview.update(job, "design", ["TC-001", "TC-002"], state="approved")
        saved = (loop.intake.ROOT, pb.ROOT, xt.ROOT)
        loop.intake.ROOT = pb.ROOT = xt.ROOT = root
        self.addCleanup(lambda: (setattr(loop.intake, "ROOT", saved[0]),
                                 setattr(pb, "ROOT", saved[1]), setattr(xt, "ROOT", saved[2])))
        jira = Jira()
        out = io.StringIO()
        with redirect_stdout(out):
            rc = xt.main(["create", "--job", "j1", "--what", "design", "--story", "ABC-12",
                          "--confirm-story", "ABC-12"],
                         conn=dict(CONN), cfg=ON, xray_cfg={}, transport=jira.transport)
        self.assertEqual(rc, 0, out.getvalue())
        self.assertIn(" ok   2 test(s) created", out.getvalue())
        with io.open(os.path.join(job, "xray-result.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["kind"], "xray-create")
        with redirect_stdout(out):
            rc = xt.main(["create", "--job", "j1", "--what", "design", "--story", "ABC-12",
                          "--confirm-story", "ABC-13"], conn=dict(CONN), cfg=ON, xray_cfg={},
                         transport=jira.transport)
        self.assertEqual(rc, 2)
        with io.open(os.path.join(job, "xray-result.json"), encoding="utf-8") as fh:
            self.assertTrue(json.load(fh)["refused"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
