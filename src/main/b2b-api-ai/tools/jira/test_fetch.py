"""The allowlist, the AC gate, and the places this must refuse.

No test here touches the network: `fetch_issue` takes a transport, so the
URL it would have called and the header it would have sent are assertable
without one.

Two behaviours carry most of the weight:

  * a story URL is UNTRUSTED INPUT. Following it to an unapproved host
    would send the Jira token there, so an unknown host is refused and the
    key is not quietly fetched from somewhere else instead.
  * `weak` is not `present`. A story with unclear acceptance criteria
    stops the run, because every later step would invent the rule it needs
    and an invented rule reads exactly like a requirement once it is in a
    test.
"""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import fetch  # noqa: E402
import projectconfig  # noqa: E402

ALLOW = ["https://jira.example.com", "https://tickets.example.org"]


def issue(description="", fields=None, key="ABC-1"):
    f = {"summary": "s", "description": description,
         "status": {"name": "Open"}, "issuetype": {"name": "Story"}}
    f.update(fields or {})
    return {"key": key, "fields": f}


class IssueKeys(unittest.TestCase):
    def test_a_browse_url_yields_key_and_host(self):
        k, base = fetch.normalize_issue_key(
            "https://jira.example.com/browse/B2B-1234")
        self.assertEqual((k, base), ("B2B-1234", "https://jira.example.com"))

    def test_query_string_and_case_do_not_matter(self):
        k, _ = fetch.normalize_issue_key(
            "https://jira.example.com/browse/b2b-99?filter=10")
        self.assertEqual(k, "B2B-99")

    def test_a_bare_key_carries_no_host(self):
        self.assertEqual(fetch.normalize_issue_key("B2B-7"), ("B2B-7", ""))

    def test_junk_is_refused(self):
        for bad in ("", "   ", "not-a-key", "https://jira.example.com/dashboard",
                    "1234"):
            with self.assertRaises(ValueError, msg=bad):
                fetch.normalize_issue_key(bad)


class HostAllowlist(unittest.TestCase):
    def test_an_approved_host_is_allowed(self):
        ok, _ = fetch.host_allowed("https://jira.example.com", ALLOW)
        self.assertTrue(ok)

    def test_an_unapproved_host_is_refused(self):
        ok, why = fetch.host_allowed("https://evil.example.net", ALLOW)
        self.assertFalse(ok)
        self.assertIn("not in jira_config.base_urls", why)

    def test_a_scheme_downgrade_is_refused(self):
        """http://jira.example.com is a different origin from https://."""
        ok, _ = fetch.host_allowed("http://jira.example.com", ALLOW)
        self.assertFalse(ok)

    def test_a_lookalike_host_is_refused(self):
        for bad in ("https://jira.example.com.evil.net",
                    "https://notjira.example.com"):
            ok, _ = fetch.host_allowed(bad, ALLOW)
            self.assertFalse(ok, bad)

    def test_an_empty_allowlist_refuses_everything(self):
        """Empty means every host is unapproved, not that every host is fine."""
        ok, why = fetch.host_allowed("https://jira.example.com", [])
        self.assertFalse(ok)
        self.assertIn("empty", why)

    def test_a_bare_key_uses_the_configured_base_url(self):
        ok, _ = fetch.host_allowed("", ALLOW)
        self.assertTrue(ok)


class TokenHandling(unittest.TestCase):
    def test_the_token_goes_in_a_header_and_never_in_the_url(self):
        seen = {}

        def transport(url, token, timeout):
            seen["url"], seen["token"] = url, token
            return {"key": "ABC-1", "fields": {}}

        fetch.fetch_issue("ABC-1", "https://jira.example.com", "s3cr3t-pat",
                          transport=transport)
        self.assertNotIn("s3cr3t-pat", seen["url"])
        self.assertEqual(seen["token"], "s3cr3t-pat")
        self.assertIn("/rest/api/2/issue/ABC-1", seen["url"])

    def test_the_api_path_is_overridable_for_cloud(self):
        seen = {}

        def transport(url, token, timeout):
            seen["url"] = url
            return {}

        fetch.fetch_issue("ABC-1", "https://jira.example.com", "t",
                          api_path="/rest/api/3", transport=transport)
        self.assertIn("/rest/api/3/issue/ABC-1", seen["url"])

    def test_redaction_shows_which_keys_are_set_not_what_they_are(self):
        red = projectconfig.redacted({"pat": "abcdef123456", "base_urls": ALLOW})
        self.assertNotIn("abcdef123456", str(red))
        self.assertIn("<set,", red["pat"])
        self.assertEqual(red["base_urls"], ALLOW)


class AcceptanceCriteriaGate(unittest.TestCase):
    def test_an_explicit_heading_with_a_list_is_present(self):
        ac = fetch.acceptance_criteria(issue(
            "Context here.\n\nAcceptance Criteria\n"
            "- the token expires after 30 minutes\n"
            "- an expired token returns 401\n"))
        self.assertTrue(ac["present"])
        self.assertEqual(ac["confidence"], "explicit")
        self.assertIn("expires", ac["evidence"])

    def test_given_when_then_is_present(self):
        ac = fetch.acceptance_criteria(issue(
            "Given an active account\nWhen the owner is removed\n"
            "Then the account returns 409\n"))
        self.assertTrue(ac["present"])
        self.assertIn("Given/When/Then", " ".join(ac["reasons"]))

    def test_a_bare_list_with_no_heading_is_WEAK_not_present(self):
        """Stops the run: a list of sentences is not acceptance criteria."""
        ac = fetch.acceptance_criteria(issue(
            "Please look at:\n- the login page\n- the error banner\n"))
        self.assertFalse(ac["present"])
        self.assertEqual(ac["confidence"], "weak")

    def test_prose_with_no_structure_is_absent(self):
        ac = fetch.acceptance_criteria(issue(
            "The reset flow feels slow and we should make it better."))
        self.assertFalse(ac["present"])
        self.assertEqual(ac["confidence"], "absent")

    def test_an_empty_story_is_absent(self):
        ac = fetch.acceptance_criteria(issue(""))
        self.assertFalse(ac["present"])
        self.assertIn("empty", " ".join(ac["reasons"]))

    def test_a_configured_custom_field_is_preferred_over_the_description(self):
        ac = fetch.acceptance_criteria(
            issue("just some prose", {"customfield_101":
                                      "Acceptance Criteria\n- a\n- b\n"}),
            ac_fields=["customfield_101"])
        self.assertTrue(ac["present"])
        self.assertEqual(ac["source"], "customfield_101")

    def test_atlassian_document_format_is_read(self):
        adf = {"type": "doc", "content": [
            {"type": "paragraph", "content": [{"text": "Acceptance Criteria"}]},
            {"type": "paragraph", "content": [{"text": "- a thing\n- another"}]}]}
        ac = fetch.acceptance_criteria(issue(adf))
        self.assertTrue(ac["present"])

    def test_every_verdict_carries_its_reason(self):
        for desc in ("", "prose only", "Acceptance Criteria\n- a\n- b\n",
                     "- x\n- y\n"):
            ac = fetch.acceptance_criteria(issue(desc))
            self.assertTrue(ac["reasons"], desc)


class Snapshot(unittest.TestCase):
    def test_a_revision_changes_when_the_story_changes(self):
        a = fetch.story_revision(issue("one"))
        b = fetch.story_revision(issue("two"))
        self.assertNotEqual(a, b)

    def test_the_same_story_gives_the_same_revision(self):
        self.assertEqual(fetch.story_revision(issue("x")),
                         fetch.story_revision(issue("x")))

    def test_jiras_own_updated_is_used_when_present(self):
        rev = fetch.story_revision(issue("x", {"updated": "2026-10-04T12:00:00"}))
        self.assertTrue(rev.startswith("2026-10-04T12:00:00|"))

    def test_the_summary_reports_attachments_and_ac(self):
        iss = issue("Acceptance Criteria\n- a\n- b\n",
                    {"attachment": [{"filename": "contract.yaml", "size": 10,
                                     "mimeType": "text/yaml"}]})
        s = fetch.summarise(iss, fetch.acceptance_criteria(iss))
        self.assertEqual(len(s["attachments"]), 1)
        self.assertEqual(s["attachments"][0]["name"], "contract.yaml")
        self.assertTrue(s["acceptance_criteria"]["present"])
        self.assertTrue(s["fetched_at"].endswith("Z"))


class Config(unittest.TestCase):
    def test_env_resolution_reports_its_source(self):
        env, src = projectconfig.active_env()
        self.assertTrue(env)
        self.assertTrue(src)

    def test_a_missing_section_explains_itself(self):
        sec, note = projectconfig.section("definitely_not_a_section")
        self.assertEqual(sec, {})
        self.assertTrue(note)


if __name__ == "__main__":
    unittest.main(verbosity=2)
