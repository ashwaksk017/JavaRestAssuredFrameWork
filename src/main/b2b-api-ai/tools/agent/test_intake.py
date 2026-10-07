"""Intake turns a paste into a brief, or says plainly that it could not.

    python tools/agent/test_intake.py

Nothing here touches the network or reads the real
program_configuration.json. That matters more than it looks: on a
machine with no config the Confluence fetcher refuses, and a test that
leaned on that refusal would PASS for the wrong reason and then fail on
a developer's machine where the config exists. The fetcher is stubbed.
"""
from __future__ import annotations

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
