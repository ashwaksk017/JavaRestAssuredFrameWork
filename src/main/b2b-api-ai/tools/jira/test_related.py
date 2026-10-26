"""related.py: which linked issues are read with a story, and why a
release's story is related to the one in question.

    python tools/jira/test_related.py
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import related  # noqa: E402

RULE = related.rules({})
BLOCKS = {"name": "Blocks", "inward": "is blocked by", "outward": "blocks"}
CLONES = {"name": "Cloners", "inward": "is cloned by", "outward": "clones"}
TESTS = {"name": "Tests", "inward": "is tested by", "outward": "tests"}


def issue(key, kind="Story", **fields):
    f = {"issuetype": {"name": kind}}
    f.update(fields)
    return {"key": key, "fields": f}


class WhichLinksAreRead(unittest.TestCase):

    def link(self, key, kind, kind_of_issue=""):
        theirs = {"key": key}
        if kind_of_issue:
            theirs["fields"] = {"issuetype": {"name": kind_of_issue}}
        return related.links({"issuelinks": [{"type": kind, "outwardIssue": theirs}]})[0]

    def test_blocks_depends_and_relates_are_read(self):
        self.assertEqual(related.skip_link(RULE, "ABC-1", self.link("ABC-2", BLOCKS)), "")
        self.assertEqual(related.skip_link(RULE, "ABC-1", self.link(
            "ABC-2", {"name": "Relates", "outward": "relates to"})), "")
        self.assertEqual(related.skip_link(RULE, "ABC-1", self.link(
            "ABC-2", {"name": "Dependency", "outward": "depends on"})), "")

    def test_clones_tests_and_other_projects_are_not(self):
        self.assertIn("clones", related.skip_link(RULE, "ABC-1", self.link("ABC-2", CLONES)))
        self.assertIn("tests", related.skip_link(RULE, "ABC-1", self.link("ABC-2", TESTS)))
        self.assertEqual(related.skip_link(RULE, "ABC-1", self.link("XYZ-2", BLOCKS)),
                         "another Jira project")
        self.assertIn("Bug", related.skip_link(RULE, "ABC-1", self.link("ABC-2", BLOCKS, "Bug")))

    def test_what_the_link_did_not_say_is_checked_once_read(self):
        mine = {"fixVersions": [{"name": "R2"}]}
        self.assertIn("another release",
                      related.skip_issue(RULE, mine, {"issuetype": {"name": "Story"},
                                                      "fixVersions": [{"name": "R1"}]}))
        self.assertEqual(related.skip_issue(RULE, mine, {"issuetype": {"name": "Story"},
                                                         "fixVersions": [{"name": "R2"}]}), "")
        # No release on either side says nothing: kept.
        self.assertEqual(related.skip_issue(RULE, mine, {"issuetype": {"name": "Story"}}), "")
        self.assertIn("Epic", related.skip_issue(RULE, mine, {"issuetype": {"name": "Epic"}}))

    def test_the_rules_come_from_jira_config(self):
        rule = related.rules({"linked_stories": {"link_types": ["clone"], "issue_types": ["Bug"],
                                                 "same_project": False, "same_release": False}})
        self.assertEqual(related.skip_link(rule, "ABC-1", self.link("XYZ-2", CLONES, "Bug")), "")
        self.assertTrue(related.skip_link(rule, "ABC-1", self.link("ABC-2", BLOCKS)))
        # Junk in the config is the defaults, not "read nothing".
        self.assertEqual(related.rules({"linked_stories": {"link_types": []}})["link_words"],
                         related.DEFAULT_LINK_WORDS)


class TheEpic(unittest.TestCase):

    def test_parent_first(self):
        self.assertEqual(related.epic_of(issue("ABC-1", parent={"key": "abc-9"})), "ABC-9")

    def test_the_epic_link_field_of_jira_server(self):
        got = issue("ABC-1", customfield_10008="ABC-9")
        self.assertEqual(related.epic_of(got), "", "an unnamed custom field is not guessed at")
        self.assertEqual(related.epic_of(got, "customfield_10008"), "ABC-9")
        got["names"] = {"customfield_10008": "Epic Link"}
        self.assertEqual(related.epic_of(got), "ABC-9")

    def test_a_value_that_is_not_a_key_is_not_an_epic(self):
        self.assertEqual(related.epic_of(issue("ABC-1", cf="../x"), "cf"), "")


class WhyARelease_sStoryIsRelated(unittest.TestCase):

    def test_linked_either_way(self):
        story = issue("ABC-1", issuelinks=[{"type": BLOCKS, "outwardIssue": {"key": "ABC-2"}}])
        self.assertEqual(related.reasons(RULE, story, issue("ABC-2")),
                         ["linked: ABC-1 blocks ABC-2"])
        other = issue("ABC-3", issuelinks=[{"type": BLOCKS, "inwardIssue": {"key": "ABC-1"}}])
        self.assertEqual(related.reasons(RULE, issue("ABC-1"), other),
                         ["linked: ABC-3 is blocked by ABC-1"])

    def test_a_clone_is_not_a_reason(self):
        story = issue("ABC-1", issuelinks=[{"type": CLONES, "outwardIssue": {"key": "ABC-2"}}])
        self.assertEqual(related.reasons(RULE, story, issue("ABC-2")), [])

    def test_same_epic_component_and_label(self):
        story = issue("ABC-1", parent={"key": "ABC-9"}, components=[{"name": "Booking"}],
                      labels=["api"])
        other = issue("ABC-2", parent={"key": "ABC-9"}, components=[{"name": "Booking"}],
                      labels=["api", "x"])
        self.assertEqual(related.reasons(RULE, story, other),
                         ["same epic ABC-9", "component Booking", "label api"])

    def test_the_epic_link_field_counts_too(self):
        story = issue("ABC-1", cf_e="ABC-9")
        self.assertEqual(related.reasons(RULE, story, issue("ABC-2", cf_e="ABC-9"), "cf_e"),
                         ["same epic ABC-9"])

    def test_nothing_in_common_is_nothing(self):
        self.assertEqual(related.reasons(RULE, issue("ABC-1", labels=["a"]),
                                         issue("ABC-2", labels=["b"], components="junk")), [])


if __name__ == "__main__":
    unittest.main(verbosity=1)
