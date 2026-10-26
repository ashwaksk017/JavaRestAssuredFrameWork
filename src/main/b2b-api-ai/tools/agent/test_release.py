"""The Automate tab's release picker (release.py): a release's stories, not
every story of the Jira project. Against the stand-in Jira (mock mode).

    python tools/agent/test_release.py
"""
from __future__ import annotations

import os as _os
_os.environ["WORKBENCH_MOCK"] = "1"

import importlib.util
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("release_under_test", os.path.join(HERE, "release.py"))
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class TheReleasePicker(unittest.TestCase):

    def setUp(self):
        if not release.search.mock_jira():
            self.skipTest("the stand-in Jira is switched off in tools/agent/mock.json")

    def test_the_versions_newest_first(self):
        got = release.versions("abc")
        self.assertEqual(got["project_key"], "ABC")
        names = [v["name"] for v in got["versions"]]
        self.assertEqual(names[0], "Release 2.0")
        self.assertLess(names.index("Release 2.0"), names.index("Release 1.0"))

    def test_only_the_releases_stories_are_read(self):
        got = release.stories("ABC", "Release 2.0")
        self.assertIn('fixVersion = "Release 2.0"', got["jql"])
        self.assertIn('issuetype = "Story"', got["jql"])
        self.assertTrue(got["issues"], "the sample release holds stories")
        for i in got["issues"]:
            self.assertEqual(i["type"], "Story", i)
            self.assertIn("Release 2.0", i["fix_versions"], i)
        self.assertTrue(got["complete"])

    def test_the_type_box_can_widen_it(self):
        got = release.stories("ABC", "Release 2.0", issue_type="")
        self.assertNotIn("issuetype", got["jql"])

    def test_without_a_story_in_question_nothing_is_ranked(self):
        got = release.stories("ABC", "Release 2.0")
        self.assertEqual(got["related_to"], "")
        self.assertTrue(all("related" not in i for i in got["issues"]))

    def test_each_story_says_why_it_is_related_to_the_one_in_question(self):
        got = release.stories("ABC", "Release 2.0", related_to="abc-6")
        self.assertEqual(got["related_to"], "ABC-6")
        self.assertEqual(got["issues"][0]["key"], "ABC-6", "the story itself first")
        self.assertEqual(got["issues"][0]["related"], ["this is the story"])
        # The sample stories share a component and labels: said, weakest last.
        five = [i for i in got["issues"] if i["key"] == "ABC-5"][0]
        self.assertEqual(five["related"][0], "component Booking")
        self.assertTrue(five["related"][-1].startswith("label"))
        self.assertEqual(got["related"], len(got["issues"]))

    def test_related_first_strongest_first(self):
        self.assertLess(release._strength(["linked: ABC-1 blocks ABC-2"]),
                        release._strength(["same epic ABC-9"]))
        self.assertLess(release._strength(["same epic ABC-9"]), release._strength(["component X"]))
        self.assertLess(release._strength(["label x"]), release._strength([]))

    def test_a_story_in_question_jira_does_not_have_is_said(self):
        # The stand-in Jira answers every key; a real one may not.
        got = release.stories("ABC", "Release 2.0", related_to="ABC-6",
                              transport=lambda m, url, *a, **k: (
                                  {"issues": [], "total": 0, "startAt": 0} if "/search" in url
                                  else []))
        self.assertIn("not found", got["related_note"])
        self.assertEqual(got["related_to"], "")
        with self.assertRaises(release.Refused):
            release.stories("ABC", "Release 2.0", related_to="not a key")

    def test_what_cannot_be_asked_is_refused_plainly(self):
        with self.assertRaises(release.Refused):
            release.stories("ABC", "")
        with self.assertRaises(release.Refused):
            release.stories("not a key!", "Release 2.0")
        with self.assertRaises(release.Refused):
            release.versions("ABC", project="nope")
        self.assertTrue(issubclass(release.Refused, ValueError), "the server says it in one line")


if __name__ == "__main__":
    unittest.main(verbosity=1)
