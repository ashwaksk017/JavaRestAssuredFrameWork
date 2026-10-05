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

import io
import os
import sys
import tempfile
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


class ProgressReporting(unittest.TestCase):
    """Chatty on the CLI, silent as a library.

    The progress lines exist so a run that is waiting on Jira looks
    different from a run that is stuck. They must not leak into anything
    that imports this module: these 191 tests inject their own transport
    and would otherwise print a line per call, and `run.py` composes the
    stages rather than shelling out.
    """

    def test_it_is_off_until_the_cli_turns_it_on(self):
        self.assertFalse(fetch.VERBOSE,
                         "importing fetch.py must not make it chatty")

    def test_say_prints_nothing_when_off(self):
        buf = io.StringIO()
        real, sys.stdout = sys.stdout, buf
        try:
            fetch.VERBOSE = False
            fetch.say("should not appear")
        finally:
            sys.stdout = real
        self.assertEqual(buf.getvalue(), "")

    def test_say_prints_a_prefixed_line_when_on(self):
        buf = io.StringIO()
        real, sys.stdout = sys.stdout, buf
        try:
            fetch.VERBOSE = True
            fetch.say("talking to Jira")
        finally:
            sys.stdout = real
            fetch.VERBOSE = False
        self.assertEqual(buf.getvalue(), "[jira] talking to Jira\n")

    def test_sizes_are_rendered_for_a_human(self):
        self.assertEqual(fetch._human(512), "512 B")
        self.assertEqual(fetch._human(2048), "2.0 KB")
        self.assertIn("MB", fetch._human(5 * 1024 * 1024))


class ParentChain(unittest.TestCase):
    """AC often live on the parent, and a sub-task often carries none."""

    def test_the_chain_walks_up_to_the_epic(self):
        issues = {
            "S-1": issue(key="S-1", fields={"parent": {"key": "P-1"}}),
            "P-1": issue(key="P-1", fields={"parent": {"key": "E-1"}}),
            "E-1": issue(key="E-1"),
        }
        chain = fetch_chain_t(self, issues, "S-1")
        self.assertEqual([i["key"] for i in chain], ["S-1", "P-1", "E-1"])

    def test_depth_is_bounded(self):
        issues = {f"K-{i}": issue(key=f"K-{i}",
                                  fields={"parent": {"key": f"K-{i+1}"}})
                  for i in range(10)}
        chain = fetch_chain_t(self, issues, "K-0", depth=2)
        self.assertEqual(len(chain), 2)

    def test_a_parent_loop_cannot_spin(self):
        issues = {"A-1": issue(key="A-1", fields={"parent": {"key": "B-1"}}),
                  "B-1": issue(key="B-1", fields={"parent": {"key": "A-1"}})}
        chain = fetch_chain_t(self, issues, "A-1", depth=9)
        self.assertEqual([i["key"] for i in chain], ["A-1", "B-1"])

    def test_an_unreadable_parent_is_recorded_not_fatal(self):
        issues = {"S-1": issue(key="S-1", fields={"parent": {"key": "GONE-9"}})}
        chain = fetch_chain_t(self, issues, "S-1")
        self.assertEqual(chain[0]["key"], "S-1")
        self.assertIn("_error", chain[1])

    def test_ac_is_inherited_from_a_parent_and_the_owner_is_named(self):
        chain = [issue(key="S-1", description="no criteria here, just prose"),
                 issue(key="P-1",
                       description="Acceptance Criteria\n- a rule\n- another\n")]
        ac = fetch.acceptance_criteria_chain(chain)
        self.assertTrue(ac["present"])
        self.assertEqual(ac["found_on"], "P-1")
        self.assertIn("inherited from P-1", " ".join(ac["reasons"]))

    def test_the_storys_own_ac_wins_over_the_parents(self):
        chain = [issue(key="S-1", description="Acceptance Criteria\n- own\n- two\n"),
                 issue(key="P-1", description="Acceptance Criteria\n- parent\n- x\n")]
        ac = fetch.acceptance_criteria_chain(chain)
        self.assertEqual(ac["found_on"], "S-1")
        self.assertEqual(ac["depth"], 0)

    def test_no_ac_anywhere_still_reports_the_storys_own_verdict(self):
        chain = [issue(key="S-1", description="prose"),
                 issue(key="P-1", description="more prose")]
        ac = fetch.acceptance_criteria_chain(chain)
        self.assertFalse(ac["present"])
        self.assertEqual(ac["found_on"], "S-1")


def fetch_chain_t(tc, issues, key, depth=fetch.MAX_PARENT_DEPTH):
    def transport(url, token, timeout):
        k = url.split("/issue/")[1].split("?")[0]
        if k not in issues:
            raise RuntimeError(f"404 for {k}")
        return issues[k]
    return fetch.fetch_chain(key, "https://jira.example.com", "t",
                             transport=transport, max_depth=depth)


class Comments(unittest.TestCase):
    """A sample payload is as likely to be in comment 40 as comment 2."""

    def test_all_fields_are_requested(self):
        """Jira Cloud omits `comment` from the default field set, and this
        tool reads comments for payloads."""
        seen = []
        fetch.fetch_issue("A-1", "https://j", "t",
                          transport=lambda u, t, to: seen.append(u) or {})
        self.assertIn("fields=*all", seen[0])

    def _pager(self, total, per_page=2):
        def transport(url, token, timeout):
            if "/comment?" not in url:
                return issue(fields={"comment": {"total": total, "comments": [
                    {"body": f"c{i}"} for i in range(min(per_page, total))]}})
            at = int(url.split("startAt=")[1].split("&")[0])
            return {"total": total, "startAt": at,
                    "comments": [{"body": f"c{i}"}
                                 for i in range(at, min(at + per_page, total))]}
        return transport

    def test_comments_beyond_the_inlined_slice_are_paged_in(self):
        chain = fetch.fetch_chain("A-1", "https://j", "t",
                                  transport=self._pager(7))
        got = chain[0]["fields"]["comment"]["comments"]
        self.assertEqual(len(got), 7)

    def test_an_issue_whose_comments_all_fit_is_not_paged_again(self):
        calls = []
        def transport(url, token, timeout):
            calls.append(url)
            return issue(fields={"comment": {"total": 1,
                                             "comments": [{"body": "only"}]}})
        fetch.fetch_chain("A-1", "https://j", "t", transport=transport,
                          max_depth=1)
        self.assertFalse([u for u in calls if "/comment?" in u])

    def test_a_server_that_never_advances_cannot_loop(self):
        def transport(url, token, timeout):
            if "/comment?" not in url:
                return issue(fields={"comment": {"total": 999,
                                                 "comments": [{"body": "a"}]}})
            return {"total": 999, "comments": []}
        chain = fetch.fetch_chain("A-1", "https://j", "t", transport=transport,
                                  max_depth=1)
        self.assertIn("_error", chain[0]["fields"]["comment"])

    def test_a_comment_read_failure_is_recorded_not_raised(self):
        def transport(url, token, timeout):
            if "/comment?" in url:
                raise RuntimeError("Jira returned 503")
            return issue(fields={"comment": {"total": 9,
                                             "comments": [{"body": "a"}]}})
        chain = fetch.fetch_chain("A-1", "https://j", "t", transport=transport,
                                  max_depth=1)
        c = chain[0]["fields"]["comment"]
        self.assertIn("503", c["_error"])
        self.assertEqual(len(c["comments"]), 1)   # what was read is kept


class Attachments(unittest.TestCase):
    def _issue(self, *atts):
        return issue(fields={"attachment": list(atts)})

    def _att(self, name, size=10, host="https://jira.example.com"):
        return {"filename": name, "size": size, "mimeType": "application/json",
                "content": f"{host}/secure/attachment/1/{name}"}

    def test_an_attachment_on_an_unapproved_host_is_refused(self):
        """The content URL comes from the Jira RESPONSE -- as untrusted as
        the story URL was, and the token would be sent to it."""
        with tempfile.TemporaryDirectory() as td:
            got = fetch.download_attachments(
                self._issue(self._att("a.json", host="https://evil.example.net")),
                td, "tok", ALLOW, fetcher=lambda *a: b"{}")
        self.assertIn("refused", got[0]["status"])
        self.assertEqual(got[0]["saved_to"], "")

    def test_an_oversized_attachment_is_skipped_and_said_so(self):
        with tempfile.TemporaryDirectory() as td:
            got = fetch.download_attachments(
                self._issue(self._att("big.json", size=99)), td, "t", ALLOW,
                fetcher=lambda *a: b"{}", max_bytes=10)
        self.assertIn("over the", got[0]["status"])

    def test_a_good_attachment_is_saved(self):
        with tempfile.TemporaryDirectory() as td:
            got = fetch.download_attachments(
                self._issue(self._att("ok.json")), td, "t", ALLOW,
                fetcher=lambda *a: b'{"a":1}')
            self.assertEqual(got[0]["status"], "saved")
            self.assertTrue(os.path.isfile(got[0]["saved_to"]))

    def test_a_hostile_filename_cannot_escape_the_directory(self):
        with tempfile.TemporaryDirectory() as td:
            got = fetch.download_attachments(
                self._issue(self._att("../../etc/passwd")), td, "t", ALLOW,
                fetcher=lambda *a: b"x")
            self.assertEqual(got[0]["status"], "saved")
            self.assertEqual(os.path.dirname(os.path.abspath(got[0]["saved_to"])),
                             os.path.abspath(td))

    def test_two_attachments_sharing_a_name_do_not_overwrite_each_other(self):
        """A revised contract re-uploaded under the same name is the usual
        case; one path would lose the first and still report it saved."""
        a1 = dict(self._att("contract.json"), id="101")
        a2 = dict(self._att("contract.json"), id="202")
        with tempfile.TemporaryDirectory() as td:
            got = fetch.download_attachments(self._issue(a1, a2), td, "t", ALLOW,
                                             fetcher=lambda *a: b"{}")
        self.assertEqual([r["status"] for r in got], ["saved", "saved"])
        self.assertNotEqual(got[0]["saved_to"], got[1]["saved_to"])

    def test_each_attachment_names_the_issue_it_came_from(self):
        with tempfile.TemporaryDirectory() as td:
            got = fetch.download_attachments(
                dict(self._issue(self._att("a.json")), key="EPIC-9"),
                td, "t", ALLOW, fetcher=lambda *a: b'{"a":1}')
            self.assertEqual(got[0]["issue"], "EPIC-9")
            payloads = fetch.payload_candidates([], got)
            self.assertIn("on EPIC-9", payloads[0]["source"])

    def test_a_download_failure_is_reported_not_raised(self):
        def boom(*a):
            raise OSError("connection reset")
        with tempfile.TemporaryDirectory() as td:
            got = fetch.download_attachments(self._issue(self._att("x.json")),
                                             td, "t", ALLOW, fetcher=boom)
        self.assertIn("failed", got[0]["status"])


class SamplePayloads(unittest.TestCase):
    def test_a_fenced_json_block_is_found_and_parsed(self):
        got = fetch.payload_candidates([issue(
            'Send this:\n```json\n{"accountId": "1", "status": "ACTIVE"}\n```\n')])
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0]["parses"])
        self.assertIn("accountId", got[0]["preview"])

    def test_a_jira_code_macro_block_is_found(self):
        got = fetch.payload_candidates([issue(
            '{code:json}\n{"a": 1}\n{code}\n')])
        self.assertTrue(any(p["parses"] for p in got))

    def test_a_curl_command_is_captured_even_though_it_is_not_json(self):
        got = fetch.payload_candidates([issue(
            "curl -X POST https://api/x -d '{\"a\":1}'")])
        self.assertTrue(any(p["kind"] == "curl" for p in got))
        self.assertFalse(got[0]["parses"])

    def test_comments_are_searched_too(self):
        got = fetch.payload_candidates([issue("", fields={"comment": {"comments": [
            {"author": {"displayName": "QA"}, "body": '```\n{"b":2}\n```'}]}})])
        self.assertTrue(any("comment by QA" in p["source"] for p in got))

    def test_the_parent_chain_is_searched(self):
        got = fetch.payload_candidates([
            issue(key="S-1", description="nothing here"),
            issue(key="P-1", description='```\n{"fromParent":true}\n```')])
        self.assertTrue(any(p["source"].startswith("P-1") for p in got))

    def test_a_textual_attachment_becomes_a_candidate(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "payload.json")
            with io.open(p, "w", encoding="utf-8") as fh:
                fh.write('{"x":1}')
            got = fetch.payload_candidates(
                [], [{"name": "payload.json", "saved_to": p, "size": 7}])
        self.assertTrue(got[0]["parses"])

    def test_a_binary_attachment_is_labelled_not_parsed(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "shot.png")
            with io.open(p, "wb") as fh:
                fh.write(b"\x89PNG")
            got = fetch.payload_candidates(
                [], [{"name": "shot.png", "saved_to": p, "size": 4}])
        self.assertEqual(got[0]["kind"], "binary")
        self.assertFalse(got[0]["parses"])

    def test_a_story_with_no_payload_yields_nothing_rather_than_a_guess(self):
        self.assertEqual(fetch.payload_candidates([issue("just prose")]), [])


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
