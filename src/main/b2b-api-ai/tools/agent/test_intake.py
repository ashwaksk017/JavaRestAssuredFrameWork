"""Intake turns a paste into a brief, or says plainly that it could not.

    python tools/agent/test_intake.py

Nothing here touches the network or reads the real
program_configuration.json. That matters more than it looks: on a
machine with no config the Confluence fetcher refuses, and a test that
leaned on that refusal would PASS for the wrong reason and then fail on
a developer's machine where the config exists. The fetcher is stubbed.
"""
from __future__ import annotations

import os as _os
_os.environ["WORKBENCH_MOCK"] = "0"      # these tests are about the real paths

import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))

_spec = importlib.util.spec_from_file_location(
    "agent_intake_under_test", os.path.join(HERE, "intake.py"))
intake = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(intake)

CURL = ('curl -X POST "https://host/props/ABC/groups/roomrates?peakRooms=10" '
        '-H "Content-Type: application/json" -d \'{"arrivalDate":"2026-11-29"}\'')

# A fetched page whose body carries one readable request.
PAGE_WITH_A_GET = "```\nGET /props/ABC/groups\n```"


class Classify(unittest.TestCase):
    def test_a_bare_jira_key(self):
        self.assertEqual(intake.classify("B2B-1234"), intake.JIRA)

    def test_a_jira_browse_url(self):
        self.assertEqual(
            intake.classify("https://jira.example.com/browse/B2B-1234"),
            intake.JIRA)

    def test_confluence_links(self):
        for link in (
                "https://wiki.example.com/x/AbCdEf",
                "https://wiki.example.com/pages/viewpage.action?pageId=1",
                "https://wiki.example.com/display/ABC/Some+Page",
                "https://wiki.example.com/spaces/ABC/pages/9/T"):
            self.assertEqual(intake.classify(link), intake.CONFLUENCE, link)

    def test_a_bare_number_is_not_a_confluence_link(self):
        """A page id with no host could be anything; the UI should not
        silently resolve it against whichever wiki is configured."""
        self.assertEqual(intake.classify("12345"), intake.UNKNOWN)

    def test_nonsense(self):
        self.assertEqual(intake.classify("notalink"), intake.UNKNOWN)
        self.assertEqual(intake.classify("the status in the response is wrong"),
                         intake.UNKNOWN)

    def test_several_stories_and_other_story_addresses_are_jira(self):
        for link in ("B2B-1, B2B-2",
                     "https://jira.example.com/projects/B2B/issues/B2B-9",
                     "https://jira.example.com/secure/RapidBoard.jspa?selectedIssue=B2B-9"):
            self.assertEqual(intake.classify(link), intake.JIRA, link)

    def test_a_query_or_a_search_address_is_a_list_not_a_story(self):
        for link in ("project = B2B AND labels = api",
                     "https://jira.example.com/issues/?jql=project%20%3D%20B2B",
                     "https://jira.example.com/issues/?filter=10400"):
            self.assertEqual(intake.classify(link), intake.JIRA_QUERY, link)

    def test_what_was_jira_before_is_still_jira(self):
        for link in ("jira.example.com/browse/B2B-1",
                     "https://jira.example.com/browse/B2B-1.",
                     "<https://jira.example.com/browse/B2B-1>",
                     "see https://jira.example.com/browse/B2B-1",
                     "https://jira.example.com/browse/B2B-1 (the login story)"):
            self.assertEqual(intake.classify(link), intake.JIRA, link)

    def test_two_addresses_on_one_line_are_not_guessed_between(self):
        self.assertEqual(intake.classify(
            "https://jira.example.com/browse/B2B-1 https://wiki.example.com/x/AbCd"),
            intake.UNKNOWN)
        self.assertEqual(intake.classify("it was good"), intake.UNKNOWN)
        self.assertEqual(
            intake.classify("https://jira.example.com/browse/B2B-1?returnUrl=https://jira.example.com/x"),
            intake.JIRA, "an address inside the query string is not a second link")
        self.assertEqual(intake.classify("order by created"), intake.UNKNOWN)

    def test_a_line_that_cannot_be_read_is_unknown_not_a_lost_job(self):
        self.assertEqual(intake.classify("https://[bad/x"), intake.UNKNOWN)

    def test_a_wiki_address_with_a_story_key_in_it_is_the_wikis(self):
        for link in ("https://wiki.example.com/pages/viewpage.action?pageId=123&selectedIssue=B2B-1",):
            self.assertEqual(intake.classify(link), intake.CONFLUENCE, link)

    def test_a_wiki_page_titled_like_a_key_is_still_confluence_or_unknown(self):
        self.assertNotEqual(
            intake.classify("https://wiki.example.com/display/SP/Release-12"), intake.JIRA)
        self.assertEqual(intake.classify(""), intake.UNKNOWN)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def brief(self, job):
        with io.open(os.path.join(intake.job_dir(job, self.tmp), "brief.md"),
                     encoding="utf-8") as fh:
            return fh.read()

    def meta(self, job):
        with io.open(os.path.join(intake.job_dir(job, self.tmp),
                                  "intake.json"), encoding="utf-8") as fh:
            return json.load(fh)


class ReadsRequests(Base):
    def test_a_pasted_curl_becomes_a_request(self):
        r = intake.build("j", text=CURL, root=self.tmp)
        self.assertTrue(r["ok"])
        self.assertEqual(len(r["requests"]), 1)
        self.assertEqual(r["requests"][0]["verb"], "POST")
        self.assertIn("/props/ABC/groups/roomrates", r["requests"][0]["raw_path"])

    def test_a_verb_path_line_becomes_a_request(self):
        r = intake.build("j", text="GET /props/ABC/groups", root=self.tmp)
        self.assertTrue(r["ok"])
        self.assertEqual(r["requests"][0]["verb"], "GET")

    def test_it_writes_a_brief_and_a_manifest(self):
        intake.build("j", text=CURL, root=self.tmp)
        self.assertIn("POST", self.brief("j"))
        self.assertEqual(self.meta("j")["pasted_chars"], len(CURL))

    def test_provenance_survives_into_the_brief(self):
        intake.build("j", text=CURL, root=self.tmp)
        self.assertIn("curl command", self.brief("j"))


class RefusesRatherThanGuesses(Base):
    def test_prose_describing_an_endpoint_is_not_a_request(self):
        """The failure this gate exists for: a sentence that mentions an
        endpoint is a description. Guessing its shape makes a test that
        asserts something nobody specified."""
        r = intake.build(
            "j", root=self.tmp,
            text="The endpoint creates a group rate plan and returns rates.")
        self.assertFalse(r["ok"])
        self.assertIn("NO REQUEST COULD BE READ", self.brief("j"))

    def test_the_brief_is_written_even_when_nothing_was_read(self):
        """'I read these and found nothing' is a useful answer; an empty
        directory is not."""
        intake.build("j", root=self.tmp, text="nothing useful here")
        self.assertTrue(os.path.isfile(
            os.path.join(intake.job_dir("j", self.tmp), "brief.md")))

    def test_an_empty_intake_is_not_ok(self):
        self.assertFalse(intake.build("j", root=self.tmp)["ok"])


class JobIdIsUserInput(Base):
    """It names a directory and comes from a UI text box."""

    def test_an_absolute_job_id_cannot_escape_the_job_root(self):
        """os.path.join lets an absolute second argument win outright,
        so this did not land under target/agent at all."""
        for bad in ("C:/Windows/Temp", "/etc", r"\server\share"):
            with self.assertRaises(ValueError, msg=bad):
                intake.job_dir(bad, self.tmp)

    def test_traversal_is_refused(self):
        for bad in ("../../escaped", "..", "a/../../b", "x/y"):
            with self.assertRaises(ValueError, msg=bad):
                intake.job_dir(bad, self.tmp)

    def test_ordinary_ids_are_fine(self):
        for good in ("j1", "B2B-1234", "job_2026.10.07", "a" * 64):
            self.assertTrue(intake.job_dir(good, self.tmp))

    def test_it_validates_rather_than_sanitises(self):
        """A silently-rewritten id makes two different jobs share a
        directory, which is worse than refusing."""
        with self.assertRaises(ValueError):
            intake.job_dir("../j1", self.tmp)


class Secrets(Base):
    def test_a_credential_in_a_pasted_body_is_redacted(self):
        """The Confluence fetcher redacts what IT reads, but pasted text
        reaches intake unredacted -- and intake.json feeds the UI and the
        agent prompt. A token-request payload IS a client id and secret."""
        r = intake.build("j", root=self.tmp, text=(
            'curl -X POST "https://h/realms/applications/token" '
            '-d \'{"client_secret":"SUPERSECRET98765"}\''))
        self.assertTrue(r["ok"])
        blob = json.dumps(r) + self.brief("j")
        self.assertNotIn("SUPERSECRET98765", blob)


class Reruns(Base):
    """Re-running a job is the normal thing to do from a UI."""

    def setUp(self):
        super().setUp()
        self._main = intake.conf_fetch.main
        self.addCleanup(setattr, intake.conf_fetch, "main", self._main)

        def fake(argv):
            out = argv[argv.index("--out") + 1]
            os.makedirs(out, exist_ok=True)
            with io.open(os.path.join(out, "confluence-1-x.md"), "w",
                         encoding="utf-8") as fh:
                fh.write(PAGE_WITH_A_GET)
            return 0
        intake.conf_fetch.main = fake
        self.link = "https://wiki.example.com/x/AbCdEf"

    def test_the_second_run_still_reads_the_page(self):
        """The old set-diff found no NEW file on a re-run and reported
        'fetched fine, nothing in it'."""
        first = intake.build("j", root=self.tmp, links=[self.link])
        second = intake.build("j", root=self.tmp, links=[self.link])
        self.assertEqual(len(first["requests"]), 1)
        self.assertEqual(len(second["requests"]), 1)

    def test_two_links_to_one_page_both_resolve(self):
        r = intake.build("j", root=self.tmp, links=[self.link, self.link])
        self.assertTrue(all(g["ok"] for g in r["links"]))

    def test_each_link_gets_its_own_directory(self):
        r = intake.build("j", root=self.tmp, links=[self.link, self.link])
        self.assertNotEqual(r["links"][0]["dir"], r["links"][1]["dir"])


class Deduplication(Base):
    def test_the_same_request_pasted_and_fetched_counts_once(self):
        """Otherwise Stage 3 proposes two tests for one request."""
        self._m = intake.conf_fetch.main
        self.addCleanup(setattr, intake.conf_fetch, "main", self._m)

        def fake(argv):
            out = argv[argv.index("--out") + 1]
            os.makedirs(out, exist_ok=True)
            with io.open(os.path.join(out, "c.md"), "w", encoding="utf-8") as fh:
                fh.write(PAGE_WITH_A_GET)
            return 0
        intake.conf_fetch.main = fake
        r = intake.build("j", root=self.tmp, text="GET /props/ABC/groups",
                         links=["https://wiki.example.com/x/A"])
        self.assertEqual(len(r["requests"]), 1)
        self.assertTrue(any("duplicate" in n for n in r["notes"]))

    def test_different_requests_are_kept(self):
        r = intake.build(
            "j", root=self.tmp,
            text="GET /props/ABC/groups\nPOST /props/ABC/groups")
        self.assertEqual(len(r["requests"]), 2)


class Links(Base):
    def setUp(self):
        super().setUp()
        self._gather = intake.conf_fetch.main
        self.addCleanup(setattr, intake.conf_fetch, "main", self._gather)

    def _confluence_returns(self, body):
        def fake(argv):
            out = argv[argv.index("--out") + 1]
            os.makedirs(out, exist_ok=True)
            with io.open(os.path.join(out, "confluence-1-x.md"), "w",
                         encoding="utf-8") as fh:
                fh.write(body)
            return 0
        intake.conf_fetch.main = fake

    def test_a_request_on_a_confluence_page_is_read(self):
        self._confluence_returns("# Spec\n\n```\nGET /props/ABC/groups\n```\n")
        r = intake.build("j", root=self.tmp,
                         links=["https://wiki.example.com/x/AbCdEf"])
        self.assertTrue(r["ok"])
        self.assertIn("confluence", r["requests"][0]["provenance"])

    def test_a_refusing_fetch_becomes_a_note_not_a_crash(self):
        intake.conf_fetch.main = lambda argv: 1
        r = intake.build("j", root=self.tmp, text="GET /x",
                         links=["https://wiki.example.com/x/AbCdEf"])
        self.assertTrue(r["ok"])            # the pasted request still counts
        self.assertTrue(any("refused" in n for n in r["notes"]))

    def test_a_raising_fetch_becomes_a_note_not_a_crash(self):
        def boom(argv):
            raise RuntimeError("network gone")
        intake.conf_fetch.main = boom
        r = intake.build("j", root=self.tmp,
                         links=["https://wiki.example.com/x/AbCdEf"])
        self.assertFalse(r["ok"])
        self.assertTrue(any("network gone" in n for n in r["notes"]))

    def test_a_jira_link_is_routed_to_its_own_chain_not_fetched_here(self):
        """tools/jira/run.py is a gated chain that writes its own packet.
        Re-running half of it here would give two answers to one
        question."""
        r = intake.build("j", root=self.tmp, text="GET /x", links=["ABC-123"])
        note = " ".join(r["notes"])
        self.assertIn("run.py", note)
        self.assertEqual(r["links"][0]["kind"], intake.JIRA)

    def test_an_unknown_link_is_reported_by_name(self):
        r = intake.build("j", root=self.tmp, text="GET /x", links=["notalink"])
        self.assertTrue(any("not recognised" in n for n in r["notes"]))

    def test_one_bad_link_does_not_lose_the_others(self):
        self._confluence_returns("```\nPOST /props/ABC/groups\n```")
        r = intake.build("j", root=self.tmp,
                         links=["notalink",
                                "https://wiki.example.com/x/AbCdEf"])
        self.assertTrue(r["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
