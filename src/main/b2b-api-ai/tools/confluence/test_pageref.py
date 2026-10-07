"""A pasted Confluence link resolves to the right intent.

    python tools/confluence/test_pageref.py

The cases that matter are the two that do NOT contain a page id. Reading
them as "no id, give up" loses the most common link people paste out of
a browser, and guessing an id for them fetches a different page whose
content looks perfectly plausible.
"""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import pageref  # noqa: E402


class DirectIds(unittest.TestCase):
    def test_viewpage_action(self):
        ref, why = pageref.parse(
            "https://wiki.example.com/pages/viewpage.action?pageId=12345")
        self.assertEqual(why, "")
        self.assertEqual(ref.kind, "id")
        self.assertEqual(ref.page_id, "12345")
        self.assertEqual(ref.base_url, "https://wiki.example.com")

    def test_viewpage_action_with_other_params(self):
        ref, _ = pageref.parse(
            "https://wiki.example.com/pages/viewpage.action"
            "?spaceKey=ABC&pageId=777&preview=x")
        self.assertEqual(ref.page_id, "777")

    def test_spaces_pages_path(self):
        ref, why = pageref.parse(
            "https://wiki.example.com/spaces/ABC/pages/4242/Some+Title")
        self.assertEqual(why, "")
        self.assertEqual(ref.kind, "id")
        self.assertEqual(ref.page_id, "4242")
        self.assertEqual(ref.space, "ABC")

    def test_cloud_wiki_prefix(self):
        ref, _ = pageref.parse(
            "https://x.atlassian.net/wiki/spaces/ABC/pages/99/T")
        self.assertEqual(ref.page_id, "99")

    def test_a_bare_id_is_an_id(self):
        """What someone pastes after copying it out of a URL."""
        ref, why = pageref.parse(" 55512 ")
        self.assertEqual(why, "")
        self.assertEqual(ref.kind, "id")
        self.assertEqual(ref.page_id, "55512")
        self.assertEqual(ref.base_url, "")


class NeedsAnotherRequest(unittest.TestCase):
    def test_display_link_is_space_plus_title(self):
        ref, why = pageref.parse(
            "https://wiki.example.com/display/ABC/Group+Rate+Plan+API")
        self.assertEqual(why, "")
        self.assertEqual(ref.kind, "title")
        self.assertEqual(ref.space, "ABC")
        self.assertEqual(ref.title, "Group Rate Plan API")

    def test_display_link_percent_encoded(self):
        ref, _ = pageref.parse(
            "https://wiki.example.com/display/ABC/Rates%20%26%20Taxes")
        self.assertEqual(ref.title, "Rates & Taxes")

    def test_tiny_link(self):
        ref, why = pageref.parse("https://wiki.example.com/x/AbCdEf")
        self.assertEqual(why, "")
        self.assertEqual(ref.kind, "tiny")
        self.assertEqual(ref.tiny, "AbCdEf")


class Refusals(unittest.TestCase):
    """Refuse by name. A guess here fetches the wrong page."""

    def test_empty(self):
        ref, why = pageref.parse("   ")
        self.assertIsNone(ref)
        self.assertEqual(why, "empty")

    def test_not_a_url_and_not_an_id(self):
        ref, why = pageref.parse("Group Rate Plan API")
        self.assertIsNone(ref)
        self.assertIn("page id", why)

    def test_viewpage_without_a_page_id(self):
        ref, why = pageref.parse(
            "https://wiki.example.com/pages/viewpage.action?spaceKey=ABC")
        self.assertIsNone(ref)
        self.assertIn("numeric pageId", why)

    def test_unrecognised_confluence_path(self):
        ref, why = pageref.parse("https://wiki.example.com/dashboard.action")
        self.assertIsNone(ref)
        self.assertIn("not a recognised", why)

    def test_an_unapproved_host_still_PARSES(self):
        """Parsing is not fetching.

        The allowlist is applied by the fetcher. Refusing here as well
        would put the same rule in two places, and the one that matters
        is the one next to the request."""
        ref, why = pageref.parse(
            "https://not-approved.example.com/pages/viewpage.action?pageId=1")
        self.assertEqual(why, "")
        self.assertEqual(ref.base_url, "https://not-approved.example.com")


if __name__ == "__main__":
    unittest.main(verbosity=2)
