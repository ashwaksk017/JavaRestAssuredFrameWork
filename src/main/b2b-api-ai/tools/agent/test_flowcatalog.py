"""flowcatalog.py: the converted cases' ordered calls, by project, for
reuse -- and what is never written into the committed catalog."""
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import flowcatalog as fc  # noqa: E402

HEAD = "prefix,case,step_name,step_type,method_or_kind,endpoint_or_query,coverage,gap_detail\n"


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="flowcat-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def suite(self, name, rows):
        d = os.path.join(self.root, fc.AUDIT, name)
        os.makedirs(d, exist_ok=True)
        with io.open(os.path.join(d, "steps.csv"), "w", encoding="utf-8") as fh:
            fh.write(HEAD + "".join(rows))


class Building(Base):
    def test_projects_order_and_what_is_left_out(self):
        self.suite("programaccountregression", [
            "__setup__,B2B-8920_create_account_200,tokenRequest,REST,POST,"
            "https://api.example.test/realms/applications/token,FULL,\n",
            "p,B2B-8920_create_account_200,enroll,REST,POST,/realms/guests/enroll,FULL,\n",
            "p,B2B-8920_create_account_200,dbstatus,JDBC,-,UPDATE account SET status='A' WHERE web_site='x.com',FULL,\n",
            "p,B2B-8920_create_account_200,create,REST,POST,/guests/{guestId}/businesses?x=1,FULL,\n",
            "p,B2B-8920_create_account_200,groovy,GROOVY,-,-,FULL,\n"])
        self.suite("roomingliststage", [
            "p,goal55_rooming_PROPX,shop,REST,GET,/props/{propCode}/groups,FULL,\n",
            "p,goal55_rooming_PROPX,list,REST,POST,/props/{propCode}/roomingList,FULL,\n"])
        self.assertEqual(fc.write(self.root), {"b2b": 1, "goal": 1})
        b2b = fc.load(self.root, "b2b")
        first = b2b["chains"][0]
        self.assertEqual(first["steps"], ["POST /realms/applications/token", "POST /realms/guests/enroll",
                                          "DB", "POST /guests/{guestId}/businesses"])
        self.assertEqual(first["case"], "#1", "a number, never the case's name")
        for project in ("b2b", "goal"):
            text = io.open(os.path.join(self.root, fc.OUT_DIR, project + ".json"), encoding="utf-8").read()
            for leak in ("B2B-8920", "create_account", "goal55", "PROPX", "api.example.test",
                         "UPDATE", "x.com", "?x=1"):
                self.assertNotIn(leak, text, project)
        self.assertNotIn("POST /realms/applications/token", b2b["by_endpoint"], "the token is no reference")

    def test_two_cases_named_alike_stay_two_chains(self):
        self.suite("programaccountguest", [
            "p,B2B-1_x,a,REST,GET,/a,FULL,\n", "p,B2B-2_x,b,REST,GET,/b,FULL,\n"])
        self.assertEqual(fc.write(self.root), {"b2b": 2})

    def test_a_project_with_no_chain_left_loses_its_file(self):
        self.suite("roomingliststage", ["p,c,a,REST,GET,/a,FULL,\n"])
        fc.write(self.root)
        shutil.rmtree(os.path.join(self.root, fc.AUDIT, "roomingliststage"))
        self.suite("programaccountguest", ["p,c,a,REST,GET,/a,FULL,\n"])
        fc.write(self.root)
        self.assertFalse(os.path.exists(os.path.join(self.root, fc.OUT_DIR, "goal.json")))


class Nearest(Base):
    def setUp(self):
        super().setUp()
        self.suite("group360createstage", [
            "p,c1,shop,REST,GET,/props/{propCode}/groups,FULL,\n",
            "p,c1,create,REST,POST,/props/{propCode}/groups,FULL,\n",
            "p,c1,pay,REST,POST,/props/{propCode}/groups/{groupId}/payment,FULL,\n",
            "p,c2,shop,REST,GET,/props/{p}/groups,FULL,\n"])
        fc.write(self.root)
        self.cat = fc.load(self.root, "goal")

    def test_the_same_call_whatever_its_placeholder_is_named(self):
        how, found = fc.nearest(self.cat, "GET /props/{prop}/groups")
        self.assertEqual(how, "same call")
        self.assertEqual([c["case"] for c in found], ["#2", "#1"], "shortest first (c2, then c1)")

    def test_a_new_endpoint_gets_the_nearest_path(self):
        how, found = fc.nearest(self.cat, "POST /props/{propCode}/groups/{groupId}/cancel")
        self.assertIn("nearest path", how)
        self.assertEqual(found[0]["case"], "#1")

    def test_the_block_says_when_nothing_is_near(self):
        text = fc.block(self.root, "goal", ["GET /totally/else"])
        self.assertIn("a new flow", text)
        self.assertEqual(fc.block(self.root, "b2b", ["GET /x"]), "", "no catalog: no block")


class TheCommittedCatalog(unittest.TestCase):
    def test_it_holds_only_suites_numbers_and_calls(self):
        # Seen in review: case names carried lower-case ticket keys and a
        # property code; a scrub missed shapes. So names are not written.
        folder = os.path.join(fc.ROOT, fc.OUT_DIR)
        if not os.path.isdir(folder):
            self.skipTest("no committed catalog here")
        call = re.compile(r"^(DB|(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) /[^\s?#@]*)$")
        for name in os.listdir(folder):
            with io.open(os.path.join(folder, name), encoding="utf-8") as fh:
                data = json.load(fh)
            for ch in data["chains"]:
                self.assertRegex(ch["case"], r"^#\d+$", name)
                self.assertRegex(ch["suite"], r"^[a-z0-9_]+$", name)
                for step in ch["steps"]:
                    self.assertRegex(step, call, name)
            self.assertEqual(set(data), {"kind", "project", "made_from", "chains", "by_endpoint"}, name)


if __name__ == "__main__":
    unittest.main(verbosity=1)
