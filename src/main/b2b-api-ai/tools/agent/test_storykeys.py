"""storykeys.py: a job with no Xray keys learns of, and can take, the keys
an earlier job of the same story holds.

Seen live: a second job of a story was automated with no key while an
earlier job of the story had created the tests; nothing said so."""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import storykeys as sk  # noqa: E402

xt = sk._xt()

STORY = "ABC-1"


def case(cid, title, steps=(("GET /x", "200"),)):
    return {"id": cid, "title": title, "endpoint": "GET /x",
            "steps": [{"action": a, "data": "", "expected": e} for a, e in steps]}


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="storykeys-")
        self.addCleanup(shutil.rmtree, self.root, True)
        p = mock.patch.object(xt.search, "mock_jira", return_value=False)
        p.start()
        self.addCleanup(p.stop)

    def job(self, name, story=STORY, cases=(), keys=None, mocked=False):
        d = os.path.join(self.root, ".workbench", name)
        os.makedirs(d, exist_ok=True)
        with io.open(os.path.join(d, "intake.json"), "w", encoding="utf-8") as fh:
            json.dump({"story": {"key": story}}, fh)
        if cases:
            with io.open(os.path.join(d, "design.json"), "w", encoding="utf-8") as fh:
                json.dump({"test_cases": list(cases)}, fh)
        if keys:
            with io.open(os.path.join(d, "xray-tests.json"), "w", encoding="utf-8") as fh:
                json.dump({"kind": "xray-tests", "at": "t", "tests": {
                    f"design:{cid}": {"key": key, "title": title, "mock": mocked}
                    for cid, (key, title) in keys.items()}}, fh)
        return d

    def kept(self, d):
        return xt.keys_for(d, "design", False)


class Earlier(Base):
    def test_only_other_jobs_of_the_same_story_with_keys_of_the_same_kind(self):
        here = self.job("job-2", cases=[case("TC-001", "traveler filter")])
        self.job("job-1", cases=[case("TC-001", "traveler filter")],
                 keys={"TC-001": ("ABC-10", "traveler filter")})
        self.job("job-0", story="XYZ-9", keys={"TC-001": ("XYZ-10", "x")})
        self.job("job-m", keys={"TC-001": ("ABC-99", "m")}, mocked=True)
        jobs = sk.earlier(here)
        self.assertEqual([j["job"] for j in jobs], ["job-1"])
        self.assertEqual(jobs[0]["tests"][0]["key"], "ABC-10")
        self.assertEqual(jobs[0]["tests"][0]["steps"][0]["action"], "GET /x")

    def test_the_automate_warning_names_the_earlier_job(self):
        here = self.job("job-2", cases=[case("TC-001", "traveler filter")])
        self.assertEqual(sk.warning(here, ["TC-001"], {}), "")          # no earlier job yet
        self.job("job-1", keys={"TC-001": ("ABC-10", "traveler filter")})
        note = sk.warning(here, ["TC-001"], {})
        self.assertIn("job-1", note)
        self.assertIn("Take keys from the story's earlier jobs", note)
        self.assertEqual(sk.warning(here, ["TC-001"], {"TC-001": "ABC-10"}), "")   # it has its key

    def test_a_pasted_story_has_no_earlier_jobs(self):
        here = self.job("job-2", story="")
        self.job("job-1", story="", keys={"TC-001": ("ABC-10", "t")})
        self.assertEqual(sk.earlier(here), [])


class CheckAndTake(Base):
    def setUp(self):
        super().setUp()
        self.job("job-1", cases=[case("TC-001", "Traveler filter returns 200"),
                                 case("TC-002", "Bad path returns 404", (("GET /bad", "404"),)),
                                 case("TC-003", "Coordinator sees cancel link",
                                      (("GET /cancel", "200"),))],
                 keys={"TC-001": ("ABC-10", "Traveler filter returns 200"),
                       "TC-002": ("ABC-11", "Bad path returns 404"),
                       "TC-003": ("ABC-12", "Coordinator sees cancel link")})
        self.here = self.job("job-2", cases=[
            case("TC-001", "traveler filter returns 200"),                          # same title
            case("TC-002", "404 for an unknown path", (("GET /bad", "404"),)),      # same steps
            case("TC-003", "Coordinator sees the cancel link on a booking",         # looks alike
                 (("GET /booking", "200 with a cancel link"),)),
            case("TC-004", "Something new entirely", (("POST /new", "201"),))])

    def run_cli(self, *argv):
        out = io.StringIO()
        with mock.patch.object(xt, "ROOT", self.root), \
                mock.patch.object(xt.loop.intake, "ROOT", self.root), redirect_stdout(out):
            rc = sk.main(list(argv))
        return rc, out.getvalue()

    def test_check_finds_same_title_same_steps_and_holds_a_lookalike(self):
        r = sk.check("job-2", self.root)
        self.assertEqual({(s["id"], s["key"], s["how"]) for s in r["same"]},
                         {("TC-001", "ABC-10", "title"), ("TC-002", "ABC-11", "steps")})
        self.assertEqual([(h["id"], h["key"]) for h in r["held"]], [("TC-003", "ABC-12")])
        self.assertEqual(self.kept(self.here), {})              # a check writes nothing

    def test_take_writes_the_same_ones_and_not_the_lookalike(self):
        r = sk.take("job-2", self.root)
        self.assertEqual(self.kept(self.here), {"TC-001": "ABC-10", "TC-002": "ABC-11"})
        self.assertEqual([h["id"] for h in r["held"]], ["TC-003"])
        entry = xt.read_keys(self.here)["tests"]["design:TC-001"]
        self.assertEqual(entry["from_job"], "job-1")

    def test_a_person_can_take_the_lookalike(self):
        sk.take("job-2", self.root, adopt={"TC-003": "ABC-12"})
        self.assertEqual(self.kept(self.here)["TC-003"], "ABC-12")

    def test_adopting_a_key_no_earlier_job_holds_is_refused(self):
        with self.assertRaises(sk.Refused):
            sk.take("job-2", self.root, adopt={"TC-004": "ABC-77"})
        self.assertEqual(self.kept(self.here), {})

    def test_a_case_that_has_a_key_keeps_it(self):
        with io.open(os.path.join(self.here, "xray-tests.json"), "w", encoding="utf-8") as fh:
            json.dump({"tests": {"design:TC-001": {"key": "ABC-50", "title": "x", "mock": False}}}, fh)
        sk.take("job-2", self.root)
        self.assertEqual(self.kept(self.here)["TC-001"], "ABC-50")

    def test_the_command_says_what_it_took(self):
        rc, out = self.run_cli("take", "--job", "job-2")
        self.assertEqual(rc, 0, out)
        self.assertIn("TC-001 -> ABC-10", out)
        self.assertIn("HELD  TC-003", out)
        self.assertIn("nothing was sent to Jira", out)
        self.assertTrue(os.path.isfile(os.path.join(self.here, sk.RESULT)))


class CreateInXrayMatchesEarlierJobs(Base):
    def test_an_earlier_jobs_test_not_linked_in_jira_is_found_not_created_again(self):
        self.job("job-1", cases=[case("TC-001", "Traveler filter returns 200")],
                 keys={"TC-001": ("ABC-10", "Traveler filter returns 200")})
        here = self.job("job-2", cases=[case("TC-001", "Traveler filter returns 200"),
                                        case("TC-002", "Something new")])
        kept = xt.read_keys(here)
        done = {"found": [], "held": []}
        todo = xt.cases_of(xt.publish.result_for(here, "design"), "design")
        opts = {"link_type": "Tests", "issue_type": "Test"}
        with mock.patch.object(xt.search, "_call", return_value={"fields": {"updated": "u"}}):
            left = xt._not_what_the_story_has(here, "design", STORY, todo, {"issuelinks": []}, opts,
                                              {}, None, False, kept, done)
        self.assertEqual([c["id"] for c in left], ["TC-002"])
        self.assertEqual(done["found"][0]["key"], "ABC-10")
        self.assertEqual(xt.read_keys(here)["tests"]["design:TC-001"]["from_job"], "job-1")


if __name__ == "__main__":
    unittest.main(verbosity=1)
