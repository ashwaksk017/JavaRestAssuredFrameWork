"""What an endpoint needs, who makes it, how it is tested today -- worked
out from the specification, the project's helpers and the converted suites.

    python tools/agent/test_depmap.py
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
spec_ = importlib.util.spec_from_file_location("depmap_under_test", os.path.join(HERE, "depmap.py"))
dm = importlib.util.module_from_spec(spec_)
spec_.loader.exec_module(dm)

ROUTES = '''class ApiRoutes {
    public static final String GUEST_ENROLL = "/realms/guests/enroll";
    public static final String GUEST_BUSINESSES = "/guests/{guestId}/businesses";
    public static final String BUSINESS_ACTIVATE =
            "/businesses/{accountId}/activate";
    public static final String GUEST_BUSINESS_MEMBERS = "/guests/{guestId}/businesses/{accountId}/members";
}
'''
BASE = '''abstract class StoryTestBase {
    protected String enrollGuest(Map<String, String> row, String body, String tag) throws Exception {
        Response res = step("enrollGuest: POST " + ApiRoutes.GUEST_ENROLL, () -> null);
        return "";
    }
    protected String createProgramAccount(Map<String, String> row, String body, String guestId) {
        Response res = step("createProgramAccount: POST " + ApiRoutes.GUEST_BUSINESSES, () -> null);
        return "";
    }
    protected boolean activateAccount(String accountId, String token, String tag) {
        Response res = step("activateProgramAccount: POST " + ApiRoutes.BUSINESS_ACTIVATE, () -> null);
        return true;
    }
    protected String addMember(Actor member, String accountId, String role, String key, String token) {
        Response res = step("createAccountMember: POST " + ApiRoutes.GUEST_BUSINESS_MEMBERS, () -> null);
        return "";
    }
}
'''
FLOWS = '''| Step | Converted step | Helper | Id it gives, and from where |
|---|---|---|---|
| Enrol | `enrollGuest` | `Actor a = enrollActor(row, ENROLL, CASE)` | `a.guestId` (enrol body `guestId`) |
| Account | `createProgramAccount` | `String[] acc = createAccountFor(owner, CREATE, svc, CASE)` | `acc[0]` accountId, `acc[1]` owner memberId |
| Activate | `activateProgramAccount` | `activateAccount(accountId, svc, CASE)` | -- (204) |
| Member | `createAccountMember` | `addMember(member, accountId, "employee", key, svc, CASE)` | `member.memberId` |
'''
CATALOG = {"kind": "flow-catalog", "project": "b2b", "chains": [
    {"suite": "accounts", "case": "#1", "steps": ["POST /businesses/{accountId}/activate"]},
    {"suite": "accounts", "case": "#2", "steps": ["POST /realms/guests/enroll", "POST /guests/{guestId}/businesses",
                                                  "POST /businesses/{accountId}/activate"]}],
    "by_endpoint": {"POST /businesses/{accountId}/activate": [0, 1],
                    "POST /guests/{guestId}/businesses": [1], "POST /realms/guests/enroll": [1]}}
SWAGGER2 = {"swagger": "2.0", "paths": {
    "/guests/{guestId}/businesses": {"post": {"parameters": [
        {"in": "body", "name": "b", "schema": {"$ref": "#/definitions/NewAccount"}}],
        "responses": {"200": {"schema": {"$ref": "#/definitions/Summary"}}}}},
    "/businesses/{accountId}/activate": {"post": {"responses": {"204": {}}}},
    "/businesses/{accountId}/suspend": {"post": {"parameters": [
        {"in": "body", "name": "b", "schema": {"required": ["reasonCode"], "properties": {"reasonCode": {}}}}],
        "responses": {"204": {}}}},
    "/reasons": {"get": {"responses": {"200": {"schema": {"type": "array", "items": {
        "properties": {"reasonCode": {}}}}}}}}},
    "definitions": {"NewAccount": {"properties": {"name": {}}},
                    "Summary": {"properties": {"accountId": {}, "status": {}}}}}


def write(root, rel, text):
    p = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with io.open(p, "w", encoding="utf-8") as fh:
        fh.write(text)


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="depmap-")
        self.addCleanup(shutil.rmtree, self.root, True)
        write(self.root, dm.ROUTES_REL, ROUTES)
        write(self.root, dm.BASES["b2b"], BASE)
        write(self.root, dm.FLOWS["b2b"], FLOWS)
        write(self.root, dm.CATALOG.format(project="b2b"), json.dumps(CATALOG))


class TheProjectsHelpers(Base):
    def test_each_helper_s_call_is_read_and_named_as_the_flow_guide_names_it(self):
        got = {(h["helper"], h["method"], h["path"]) for h in dm.helpers(self.root, "b2b")}
        self.assertIn(("enrollActor", "POST", "/realms/guests/enroll"), got, "not enrollGuest: the guide's name")
        self.assertIn(("createAccountFor", "POST", "/guests/{guestId}/businesses"), got)
        self.assertIn(("activateAccount", "POST", "/businesses/{accountId}/activate"), got,
                      "a route split over two lines")
        self.assertEqual(dm.guide_gives(self.root, "b2b")["memberid"], ["createAccountFor", "addMember"])


class AnEndpoint(Base):
    def test_a_tested_endpoint_gets_its_setup_chain_and_a_converted_chain_with_setup(self):
        text = dm.block(self.root, "b2b", SWAGGER2, ["POST /businesses/{accountId}/activate"])
        self.assertIn("made by the helper `activateAccount()`", text)
        self.assertIn("0. `serviceToken()` -- the token, before any call", text)
        self.assertIn("1. POST /realms/guests/enroll -> `guestId` -- the helper `enrollActor()`", text)
        self.assertIn("2. POST /guests/{guestId}/businesses -> `accountId` -- the helper `createAccountFor()`", text)
        first = text.index("tested today")
        self.assertIn("accounts #2: POST /realms/guests/enroll ->", text[first:].splitlines()[1],
                      "a chain that shows the setup comes first, not the lone call")

    def test_a_new_endpoint_is_built_from_the_project_and_the_spec_with_its_nearest_tested(self):
        text = dm.block(self.root, "b2b", SWAGGER2, ["POST /businesses/{accountId}/suspend -- Suspend"])
        self.assertIn("2. POST /guests/{guestId}/businesses -> `accountId`", text)
        self.assertIn("GET /reasons -> `reasonCode` -- no helper makes this call yet", text,
                      "a required body id from the operation whose response has it (Swagger 2.0)")
        self.assertIn("no converted suite calls it yet", text)
        self.assertIn("POST /businesses/{accountId}/activate (in 2 converted chain(s))", text)

    def test_what_nobody_makes_is_said_not_invented(self):
        spec = {"openapi": "3.0.1", "paths": {"/widgets/{widgetId}/spin": {"post": {"responses": {"204": {}}}}}}
        text = dm.block(self.root, "b2b", spec, ["POST /widgets/{widgetId}/spin"])
        self.assertIn("NOT RESOLVED: `widgetId`", text)
        self.assertIn("ask in open_questions", text)

    def test_openapi_3_responses_give_ids_too(self):
        spec = {"openapi": "3.0.1", "paths": {
            "/things": {"post": {"responses": {"201": {"content": {"application/json": {"schema": {
                "properties": {"thingId": {}}}}}}}}},
            "/things/{thingId}/close": {"post": {"responses": {"204": {}}}}}}
        self.assertIn("POST /things -> `thingId`", dm.block(self.root, "b2b", spec, ["POST /things/{thingId}/close"]))

    def test_what_a_tester_answered_before_shows_under_its_endpoint(self):
        write(self.root, ".cursor/skills/jira-to-restassured/KNOWN_B2B.md",
              "# Known answers -- B2B\n\n<!-- known:0123456789ab -->\n### How is an account put in "
              "status L?\n\nEndpoints: POST /businesses/{accountId}/activate\n\nA status update in the "
              "database, before activate.\n\n_Kept 2026-10-23._\n")
        text = dm.block(self.root, "b2b", SWAGGER2, ["POST /businesses/{accountId}/activate",
                                                     "POST /businesses/{accountId}/suspend"])
        self.assertIn("known (answered before): How is an account put in status L? -> A status update "
                      "in the database, before activate.", text)
        self.assertEqual(text.count("known (answered before)"), 1, "only under its own endpoint")

    def test_nothing_asked_nothing_said(self):
        self.assertEqual(dm.block(self.root, "b2b", SWAGGER2, []), "")


if __name__ == "__main__":
    unittest.main(verbosity=1)
