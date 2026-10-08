"""The API test design step reports what it checked, and guesses nothing.

    python tools/agent/test_design.py

No test here calls Cursor. The agent is a function that returns a reply;
what is tested is everything around it: what is sent, what is kept out
of the prompt, how a damaged reply is read, which cases are dropped or
marked, and what is written.
"""
from __future__ import annotations

import csv
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


design = _load("design_under_test", os.path.join(HERE, "design.py"))
jsonfix = design.jsonfix
loop = design.loop

SPEC = {
    "openapi": "3.0.1",
    "servers": [{"url": "https://internal-gateway.corp.example/v1"}],
    "paths": {
        "/groups/{groupId}/rates": {
            "get": {"summary": "List rates", "responses": {"200": {}}},
            "post": {"operationId": "createRate", "responses": {"201": {}, "400": {}}},
            "parameters": [{"name": "groupId", "in": "path"}],
        },
        "/health": {"get": {"responses": {"200": {}}}},
    },
}


def case(**over):
    base = {"id": "TC-001", "title": "Create a rate returns 201",
            "endpoint": "POST /groups/{groupId}/rates", "type": "positive",
            "priority": "high", "preconditions": "",
            "steps": [{"action": "POST a valid rate", "data": "a valid group id",
                       "expected": "201 and the body carries rateId"}],
            "requirement_refs": ["AC-1"]}
    base.update(over)
    return base


def reply(*cases, **extra):
    return json.dumps(dict({"service": "rates", "summary": "s",
                            "test_cases": list(cases), "open_questions": []}, **extra))


class ReadingTheReply(unittest.TestCase):
    def test_a_clean_object_is_read_as_written(self):
        data, how = jsonfix.loads('{"a": 1}')
        self.assertEqual((data, how), ({"a": 1}, "as written"))

    def test_prose_and_a_code_fence_around_it_are_removed(self):
        data, how = jsonfix.loads('Here you go:\n```json\n{"a": [1, 2]}\n```\nDone.')
        self.assertEqual(data, {"a": [1, 2]})

    def test_a_trailing_comma_is_repaired_and_said(self):
        data, how = jsonfix.loads('{"a": [1, 2,], "b": {"c": 1,},}')
        self.assertEqual(data, {"a": [1, 2], "b": {"c": 1}})
        self.assertIn("trailing commas", how)

    def test_typographic_quotes_are_repaired(self):
        data, how = jsonfix.loads("{\u201ca\u201d: \u201cb\u201d}")
        self.assertEqual(data, {"a": "b"})
        self.assertIn("quotes", how)

    def test_a_reply_cut_off_gives_only_the_complete_items(self):
        whole = reply(case(), case(id="TC-002", title="Second"), case(id="TC-003"))
        cut = whole[:whole.index('"TC-003"') + 30]
        with self.assertRaises(ValueError):
            jsonfix.loads(cut)
        data, how = jsonfix.loads(cut, partial_key="test_cases")
        self.assertEqual([c["id"] for c in data["test_cases"]], ["TC-001", "TC-002"])
        self.assertTrue(how.startswith("partial"))

    def test_a_brace_inside_a_string_does_not_end_an_item(self):
        text = '{"test_cases": [{"t": "a } b ] \\" c"}, {"t": "x"}, {"t": "cu'
        data, _how = jsonfix.loads(text, partial_key="test_cases")
        self.assertEqual(data["test_cases"], [{"t": 'a } b ] " c'}, {"t": "x"}])

    def test_nothing_recognisable_is_an_error_not_a_guess(self):
        for text in ("", "I could not do that.", "[1, 2, 3]"):
            with self.assertRaises(ValueError):
                jsonfix.loads(text, partial_key="test_cases")


class TheSpecification(unittest.TestCase):
    def test_endpoints_are_read_by_the_program(self):
        spec, how = design.read_spec(json.dumps(SPEC))
        self.assertEqual(how, "JSON")
        self.assertEqual(design.endpoints_of(spec),
                         ["GET /groups/{groupId}/rates -- List rates",
                          "POST /groups/{groupId}/rates -- createRate",
                          "GET /health"],
                         "`parameters` under a path is not a verb")

    def test_yaml_is_read_when_pyyaml_is_there(self):
        try:
            import yaml                                   # noqa: F401
        except ImportError:
            self.skipTest("PyYAML is not installed")
        spec, how = design.read_spec("openapi: 3.0.0\npaths:\n  /a:\n    get: {}\n")
        self.assertEqual((how, design.endpoints_of(spec)), ("YAML", ["GET /a"]))

    def test_text_that_is_not_a_specification_is_refused_with_a_reason(self):
        self.assertIsNone(design.read_spec('{"info": {}}')[0])
        self.assertIn("paths", design.read_spec('{"info": {}}')[1])
        self.assertIsNone(design.read_spec("just a sentence")[0])

    def test_the_same_endpoint_written_two_ways_is_one(self):
        k = design.endpoint_key
        self.assertEqual(k("post /groups/{id}/rates/ -- x"), k("POST /groups/{groupId}/rates"))
        self.assertEqual(k("GET /a/:id?x=1"), k("GET /a/{id}"))
        self.assertNotEqual(k("GET /a/{id}"), k("PUT /a/{id}"))
        self.assertEqual(k("no verb here at all, sorry"), "")

    def test_server_entries_never_reach_the_prompt(self):
        text, notes = design.spec_text(SPEC, 80_000)
        self.assertNotIn("internal-gateway", text)
        self.assertTrue(any("server" in n for n in notes))
        self.assertIn("/groups/{groupId}/rates", text)

    def test_a_large_specification_is_shrunk_in_steps_and_each_is_reported(self):
        big = json.loads(json.dumps(SPEC))
        big["paths"]["/health"]["get"]["x-internal"] = "y" * 5000
        big["paths"]["/health"]["get"]["example"] = "e" * 5000
        big["paths"]["/health"]["get"]["description"] = "d" * 5000
        text, notes = design.spec_text(big, 2000)
        self.assertLessEqual(len(text), 2000)
        self.assertNotIn("x-internal", text)
        self.assertTrue(any("examples" in n for n in notes))
        self.assertTrue(any("descriptions" in n for n in notes))
        self.assertIn("/groups/{groupId}/rates", text, "the contract itself is kept")

    def test_a_specification_on_a_fetched_page_is_found(self):
        d = tempfile.mkdtemp(prefix="designtest_")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        os.makedirs(os.path.join(d, "sources", "01-confluence"))
        with io.open(os.path.join(d, "sources", "01-confluence", "page.md"), "w",
                     encoding="utf-8") as fh:
            fh.write("# Page\n\n```json\n{\"not\": \"a spec\"}\n```\n\n"
                     "```json\n" + json.dumps(SPEC) + "\n```\n")
        text, where = design.find_spec(d)
        self.assertEqual(json.loads(text), SPEC)
        self.assertIn("sources/01-confluence/page.md", where)
        self.assertEqual(design.find_spec(tempfile.gettempdir() + "/nowhere-at-all"), ("", ""))


class ThePrompt(unittest.TestCase):
    def test_a_section_over_its_limit_is_cut_and_reported(self):
        out, notes = design.fit({"story": "s" * 500, "notes": "n" * 50},
                                {"story": 100, "notes": 100}, total=10_000)
        self.assertTrue(out["story"].startswith("s" * 100))
        self.assertIn("400 more characters were left out", out["story"])
        self.assertEqual(out["notes"], "n" * 50)
        self.assertEqual(len(notes), 1)

    def test_the_total_is_held_by_cutting_the_largest(self):
        out, notes = design.fit({"swagger": "a" * 9000, "story": "b" * 3000},
                                {}, total=8000)
        self.assertLessEqual(sum(len(v) for v in out.values()), 8000)
        self.assertEqual(out["story"], "b" * 3000)
        self.assertTrue(any(n.startswith("swagger") for n in notes))

    def test_a_placeholder_inside_pasted_material_stays_text(self):
        got = design.render("A {{story}} B {{notes}}", {"story": "{{notes}}", "notes": "N"})
        self.assertEqual(got, "A {{notes}} B N")

    def test_private_hosts_and_credentials_are_removed(self):
        text, hits = design.redact("call Stage-API.corp.example with pw S3cretValue!",
                                   {"stage-api.corp.example": "host", "S3cretValue!": "value of x",
                                    "abc": "too short to be meaningful"})
        self.assertEqual(text, "call <redacted> with pw <redacted>")
        self.assertEqual(hits, 2)


class WhatComesBack(unittest.TestCase):
    def test_a_case_without_an_expected_result_is_dropped_and_said(self):
        cases, warnings = design.normalise(
            [case(), case(id="TC-002", steps=[{"action": "send it"}]), case(id="TC-003", title="")])
        self.assertEqual([c["id"] for c in cases], ["TC-001"])
        self.assertTrue(any("TC-002" in w and "expected" in w for w in warnings))
        self.assertTrue(any("TC-003" in w and "title" in w for w in warnings))

    def test_a_case_on_an_endpoint_not_in_the_specification_is_marked(self):
        known = design.endpoints_of(SPEC)
        cases, warnings = design.normalise(
            [case(endpoint="post /groups/{id}/rates"),
             case(id="TC-002", endpoint="DELETE /groups/{id}/rates")], known)
        self.assertEqual([c["endpoint_in_spec"] for c in cases], [True, False])
        self.assertTrue(any("TC-002" in w and "not in the specification" in w for w in warnings))

    def test_without_an_endpoint_list_nothing_is_marked(self):
        cases, warnings = design.normalise([case()], [])
        self.assertNotIn("endpoint_in_spec", cases[0])
        self.assertEqual(warnings, [])

    def test_the_other_tools_column_names_are_accepted(self):
        cases, _w = design.normalise([{
            "TestID": "API-7", "Summary": "Reject a missing date", "Test Type": "Negative",
            "Step": "POST without arrivalDate", "Data": "no date",
            "Expected Result": "400", "br_refs": "BR-2"}])
        c = cases[0]
        self.assertEqual((c["id"], c["title"], c["type"]), ("API-7", "Reject a missing date", "negative"))
        self.assertEqual(c["steps"], [{"action": "POST without arrivalDate", "data": "no date",
                                       "expected": "400"}])
        self.assertEqual(c["requirement_refs"], ["BR-2"])

    def test_missing_and_repeated_ids_are_made_unique(self):
        cases, warnings = design.normalise([case(), case(), case(id="")])
        ids = [c["id"] for c in cases]
        self.assertEqual(len(set(ids)), 3)
        self.assertEqual(ids[0], "TC-001")
        self.assertTrue(any("used twice" in w for w in warnings))

    def test_more_than_the_limit_is_cut_and_said(self):
        cases, warnings = design.normalise([case(id=f"T{i}") for i in range(6)], max_cases=4)
        self.assertEqual(len(cases), 4)
        self.assertTrue(any("first 4" in w for w in warnings))

    def test_a_reply_that_is_not_a_list_gives_nothing(self):
        self.assertEqual(design.normalise("none"), ([], []))
        self.assertEqual(design.normalise([1, "x"])[0], [])


class Run(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="designtest_root_")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.out = os.path.join(self.root, "target", "agent", "j1")
        self.prompts = []

    def agent(self, *replies, ask=True):
        """Replies in order. The second is only used when the step asks
        again, which it does through `followup`."""
        def call(prompt, followup):
            self.prompts.append(prompt)
            text = replies[0]
            again = followup(text) if ask else None
            if again and len(replies) > 1:
                self.prompts.append(again)
                text = replies[1]
            return {"result": text, "model": "fake"}
        return call

    def run_design(self, agent, **kw):
        kw.setdefault("swagger", json.dumps(SPEC))
        return design.design("j1", root=self.root, agent=agent, **kw)

    def read(self, name):
        with io.open(os.path.join(self.out, name), encoding="utf-8-sig") as fh:
            return fh.read()

    def test_a_good_reply_writes_the_four_files(self):
        rc = self.run_design(self.agent(reply(case(), case(id="TC-002", endpoint="GET /nope"))),
                             service="rates", requirements="AC-1 a rate can be created")
        self.assertEqual(rc, 0)
        d = json.loads(self.read("design.json"))
        self.assertEqual((d["service"], d["parsed"], d["partial"]), ("rates", "as written", False))
        self.assertEqual(len(d["test_cases"]), 2)
        self.assertTrue(d["endpoints_checked"])
        md = self.read("design.md")
        self.assertIn("[NOT IN THE SPECIFICATION]", md)
        self.assertIn("expect: 201 and the body carries rateId", md)
        rows = list(csv.reader(io.StringIO(self.read("test-cases.csv")), delimiter=";"))
        self.assertEqual(rows[0][0], "TestID")
        self.assertEqual(rows[1][0], "TC-001")
        self.assertEqual(len(rows), 3)
        self.assertTrue(os.path.isfile(os.path.join(self.out, "xray.csv")))

    def test_the_prompt_carries_the_material_and_the_programs_endpoint_list(self):
        self.run_design(self.agent(reply(case())), requirements="AC-1 text", notes="focus on dates",
                        speed="fast")
        p = self.prompts[0]
        self.assertIn("POST /groups/{groupId}/rates -- createRate", p)
        self.assertIn("AC-1 text", p)
        self.assertIn("focus on dates", p)
        self.assertIn("At most 25 test cases", p)
        self.assertNotIn("internal-gateway", p)
        self.assertNotIn("{{", p.split("===== ENDPOINTS")[0], "every placeholder was filled")

    def test_a_value_from_the_private_configuration_is_not_sent_or_logged(self):
        cfg = os.path.join(self.root, "src", "main", "resources")
        os.makedirs(cfg)
        with io.open(os.path.join(cfg, "program_configuration.json"), "w", encoding="utf-8") as fh:
            json.dump({"auth": {"client_secret": "Zx9-very-private-value"}}, fh)
        self.run_design(self.agent(reply(case())),
                        requirements="use Zx9-very-private-value to log in")
        self.assertNotIn("Zx9-very-private-value", self.prompts[0])
        self.assertNotIn("Zx9-very-private-value", self.read("cursor.log"))

    def test_an_unreadable_reply_is_asked_for_again_once(self):
        rc = self.run_design(self.agent("Sure! I designed 12 tests for you.", reply(case())))
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.prompts), 2)
        self.assertIn("ONLY the JSON object", self.prompts[1])

    def test_a_good_reply_is_not_asked_for_again(self):
        self.run_design(self.agent(reply(case()), "never used"))
        self.assertEqual(len(self.prompts), 1)

    def test_a_cut_off_reply_keeps_its_complete_cases_and_says_partial(self):
        whole = reply(case(), case(id="TC-002"), case(id="TC-003"))
        cut = whole[:whole.index('"TC-003"') + 20]
        rc = self.run_design(self.agent(cut, "still not json"))
        self.assertEqual(rc, 0)
        d = json.loads(self.read("design.json"))
        self.assertTrue(d["partial"])
        self.assertEqual([c["id"] for c in d["test_cases"]], ["TC-001", "TC-002"])
        self.assertIn("PARTIAL", self.read("design.md"))
        self.assertTrue(any("cut off" in w for w in d["warnings"]))

    def test_no_usable_reply_fails_and_leaves_no_old_design_behind(self):
        self.assertEqual(self.run_design(self.agent(reply(case()))), 0)
        self.assertTrue(os.path.isfile(os.path.join(self.out, "design.md")))
        self.assertEqual(self.run_design(self.agent("no", "still no")), 1)
        for name in design.ARTIFACTS:
            self.assertFalse(os.path.isfile(os.path.join(self.out, name)), name)

    def test_cases_that_are_all_unusable_fail(self):
        self.assertEqual(self.run_design(self.agent(reply(case(steps=[])))), 1)

    def test_nothing_to_design_from_fails_before_the_agent_is_called(self):
        rc = design.design("j1", root=self.root, agent=self.agent(reply(case())))
        self.assertEqual(rc, 1)
        self.assertEqual(self.prompts, [])

    def test_an_unknown_speed_is_refused(self):
        self.assertEqual(self.run_design(self.agent(reply(case())), speed="ludicrous"), 1)
        self.assertEqual(self.prompts, [])

    def test_an_agent_failure_is_a_fail_line_not_a_traceback(self):
        def boom(prompt, followup):
            raise RuntimeError("the agent did not start: 401")
        self.assertEqual(self.run_design(boom), 1)
        self.assertIn("did not start", self.read("cursor.log"))

    def test_without_a_specification_the_requests_intake_read_are_the_endpoints(self):
        os.makedirs(self.out)
        with io.open(os.path.join(self.out, "intake.json"), "w", encoding="utf-8") as fh:
            json.dump({"requests": [{"verb": "post", "raw_path": "/groups/{id}/rates"}]}, fh)
        with io.open(os.path.join(self.out, "brief.md"), "w", encoding="utf-8") as fh:
            fh.write("# Intake: 1 request(s)\nthe story text\n")
        rc = design.design("j1", root=self.root,
                           agent=self.agent(reply(case(), case(id="TC-002", endpoint="GET /x"))))
        self.assertEqual(rc, 0)
        self.assertIn("the story text", self.prompts[0])
        self.assertIn("POST /groups/{id}/rates", self.prompts[0])
        d = json.loads(self.read("design.json"))
        self.assertEqual([c["endpoint_in_spec"] for c in d["test_cases"]], [True, False])

    def test_text_that_is_not_a_specification_is_sent_and_the_check_is_skipped_aloud(self):
        rc = self.run_design(self.agent(reply(case())), swagger="POST /a creates a thing")
        self.assertEqual(rc, 0)
        d = json.loads(self.read("design.json"))
        self.assertFalse(d["endpoints_checked"])
        self.assertTrue(any("NOT checked" in w for w in d["warnings"]))

    def test_a_cell_that_would_run_as_a_formula_is_defused(self):
        self.run_design(self.agent(reply(case(title="=HYPERLINK(\"http://x\")",
                                              steps=[{"action": "+1", "data": "-2",
                                                      "expected": "@x"}]))))
        rows = list(csv.reader(io.StringIO(self.read("test-cases.csv")), delimiter=";"))
        self.assertTrue(all(not c or c[0] not in "=+-@" for c in rows[1]), rows[1])

    def test_an_unusable_job_id_is_refused(self):
        with self.assertRaises(ValueError):
            design.design("../escape", swagger=json.dumps(SPEC), root=self.root,
                          agent=self.agent(reply(case())))


class HandedToTheAgentLoop(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="designtest_loop_")
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)

    def write(self, cases):
        with io.open(os.path.join(self.d, "design.json"), "w", encoding="utf-8") as fh:
            json.dump({"test_cases": cases}, fh)

    def test_the_designed_cases_are_in_the_prompt_for_a_new_test(self):
        self.write([dict(case(), endpoint_in_spec=True),
                    dict(case(id="TC-009", title="Off contract"), endpoint_in_spec=False)])
        policy = loop.load_policy()
        scope = loop.resolve_scope(policy, "new-test", "", loop.ROOT)
        prompt = loop.build_prompt(self.d, policy, scope, root=loop.ROOT)
        self.assertIn("TEST CASES DESIGNED FOR THIS JOB", prompt)
        self.assertIn("TC-001 [positive, high] POST /groups/{groupId}/rates", prompt)
        self.assertIn("expect: 201 and the body carries rateId", prompt)
        self.assertNotIn("TC-009", prompt, "a case off the contract is not for automation")
        conv = loop.resolve_scope(policy, "converter", "", loop.ROOT)
        self.assertNotIn("TEST CASES DESIGNED",
                         loop.build_prompt(self.d, policy, conv, root=loop.ROOT))

    def test_no_design_adds_nothing(self):
        self.assertEqual(loop.designed_cases(self.d), "")
        with io.open(os.path.join(self.d, "design.json"), "w", encoding="utf-8") as fh:
            fh.write("{ not json")
        self.assertEqual(loop.designed_cases(self.d), "")


class AskingAgainOnTheSameAgent(unittest.TestCase):
    """cursor_call.run(..., followup=): the second turn goes to the SAME
    session, once, and only replaces the first answer when it is usable."""

    def setUp(self):
        self.sent = []
        self.sessions = 0
        outer = self
        self.results = []

        class Run:
            def __init__(self, result):
                self.result = result

            def messages(self):
                return iter(())

            def wait(self):
                status, text = self.result
                return types.SimpleNamespace(status=status, result=text, id="r")

        class Session:
            def __enter__(self):
                outer.sessions += 1
                return self

            def __exit__(self, *exc):
                return False

            def send(self, prompt):
                outer.sent.append(prompt)
                return Run(outer.results[len(outer.sent) - 1])

        class Agent:
            create = staticmethod(lambda options: Session())

        mod = types.ModuleType("cursor_sdk")
        mod.Agent = Agent
        mod.AgentOptions = lambda **kw: types.SimpleNamespace(**kw)
        mod.LocalAgentOptions = lambda **kw: types.SimpleNamespace(**kw)
        self.saved = sys.modules.get("cursor_sdk")
        sys.modules["cursor_sdk"] = mod
        self.patch = loop.cursor_call.patch_bridge
        loop.cursor_call.patch_bridge = lambda ca: None
        self.cfg = types.SimpleNamespace(api_key="k_123456", model="m", cwd=".")

    def tearDown(self):
        loop.cursor_call.patch_bridge = self.patch
        if self.saved is None:
            sys.modules.pop("cursor_sdk", None)
        else:
            sys.modules["cursor_sdk"] = self.saved

    def call(self, followup):
        return loop.cursor_call.run("first", None, self.cfg, followup=followup)

    def test_the_second_answer_replaces_the_first_on_one_session(self):
        self.results = [("finished", "prose"), ("finished", '{"ok": 1}')]
        out = self.call(lambda text: "json only" if text == "prose" else None)
        self.assertEqual(out["result"], '{"ok": 1}')
        self.assertEqual((self.sent, self.sessions), (["first", "json only"], 1))

    def test_nothing_more_is_sent_when_the_first_answer_is_fine(self):
        self.results = [("finished", '{"ok": 1}')]
        out = self.call(lambda text: None)
        self.assertEqual((out["result"], self.sent), ('{"ok": 1}', ["first"]))

    def test_a_failed_or_empty_second_turn_keeps_the_first_answer(self):
        self.results = [("finished", "prose"), ("error", "broke")]
        self.assertEqual(self.call(lambda text: "again")["result"], "prose")
        self.sent.clear()
        self.results = [("finished", "prose"), ("finished", "")]
        self.assertEqual(self.call(lambda text: "again")["result"], "prose")


if __name__ == "__main__":
    unittest.main(verbosity=1)
