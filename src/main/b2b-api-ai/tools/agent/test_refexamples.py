"""The scrubbed reference examples (refexamples.py): what is scrubbed, what
the scan refuses, that the committed ones hold no company data, and that
the prompt names them.

    python tools/agent/test_refexamples.py

The repository is public. These examples are the one converted material in
it; an example that still held a ticket key, a host, an e-mail address or a
property code is what this guards against.
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
ROOT = os.path.dirname(os.path.dirname(HERE))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rx = _load("refexamples_under_test", os.path.join(HERE, "refexamples.py"))
fc = _load("flowcatalog_for_refs", os.path.join(HERE, "flowcatalog.py"))


class Scrubbing(unittest.TestCase):

    # Invented values only: this file is public too. The company is "Corpco",
    # learned -- as from a real configuration -- from the host it runs on.
    PRIVATE = ["https://jira.corpco.test", "s3cret-Value9"]

    def setUp(self):
        self.s = rx.Scrubber(self.PRIVATE)

    def test_the_company_is_learned_from_its_hosts_not_written_here(self):
        self.assertEqual(rx.company_words(self.PRIVATE), ["corpco"])
        self.assertEqual(rx.company_words(["https://api.example.test", "https://x.amazonaws.com"]), [])

    def test_keys_hosts_emails_codes_and_private_values_go(self):
        text = ('Onboarding.start(row, "XYZ-905_post_x"); // XYZ-905, see https://jira.corpco.test/browse/XYZ-905\n'
                '"PAC-77_other_project" '
                '.header("Corpco-Operator-Location", "QWERTY") owner a.person@corpco.test pw s3cret-Value9\n'
                'void goal9901PostSnapTest() / B2b99002SpendTest / "PROGRAMACCOUNTS" / "GET"')
        out = self.s.text(text)
        for gone in ("XYZ-905", "jira.corpco.test", "Corpco", "QWERTY", "a.person@corpco.test", "s3cret-Value9",
                     "goal9901", "B2b99002"):
            self.assertNotIn(gone, out)
        self.assertEqual(out.count("ABC-1_post_x") + out.count("ABC-1,"), 2, "one key, one stand-in, throughout")
        self.assertNotIn("PAC-77", out, "a key of any project, followed by an underscore")
        self.assertIn("Acme-Operator-Location", out)
        self.assertIn('"PROGRAMACCOUNTS"', out, "a long API enum value stays")
        self.assertIn('"GET"', out)
        self.assertEqual(rx.leaks(out, self.PRIVATE), [])

    def test_a_body_keeps_its_keys_and_placeholders_only(self):
        body = ('{"username": "#u#", "propCode": "ZZQXW", "count": @n@, "flag": true,'
                ' "email": {"emailAddress": "real@corpco.test"}, "list": ["a", "b"]}')
        got = json.loads(self.s.template(body))
        self.assertEqual(got["username"], "#u#")
        self.assertEqual(got["propCode"], "<text>")
        self.assertEqual(got["count"], "@n@")
        self.assertIs(got["flag"], True)
        self.assertEqual(got["email"]["emailAddress"], "<text>")
        self.assertEqual(self.s.template("not json"), "(not JSON: its shape cannot be shown safely)")

    def test_a_small_number_is_shown_as_the_converted_body_sends_it(self):
        # Seen live: every number written as 0 was copied, and the server
        # refused employeeCountRange 0 (the converted bodies send 1).
        got = json.loads(self.s.template('{"employeeCountRange": 1, "spend": 0, "rate": 2.5, '
                                         '"guestId": 1901102667, "phone": 9015551212}'))
        self.assertEqual(got["employeeCountRange"], 1)
        self.assertEqual(got["spend"], 0)
        self.assertEqual(got["rate"], 2.5)
        self.assertEqual(got["guestId"], "<number>", "an id is not shown")
        self.assertEqual(got["phone"], "<number>")

    def test_an_enum_word_is_shown_a_persons_field_is_not(self):
        # Seen live: hidden as <text>, "none" was invented as "mandatory" and refused.
        got = json.loads(self.s.template(
            '{"travelPolicyEnforcement": "none", "businessModel": "retailStoreFront", "addressType": "business",'
            ' "username": "jsmith", "firstName": "george", "city": "memphis", "propCode": "ZZQXW",'
            ' "businessModelComments": "automated test", "groupName": "corpco"}'))
        self.assertEqual(got["travelPolicyEnforcement"], "none")
        self.assertEqual(got["businessModel"], "retailStoreFront")
        self.assertEqual(got["addressType"], "business", "a ...Type field is an enum")
        for hidden in ("username", "firstName", "city", "propCode", "businessModelComments", "groupName"):
            self.assertEqual(got[hidden], "<text>", hidden)

    def test_the_scan_names_what_is_left_without_quoting_it(self):
        found = rx.leaks('see https://jira.corpco.test x@corpco.test XYZ-1 "ZZQXW" corpco s3cret-Value9',
                         self.PRIVATE)
        self.assertEqual(sorted(found), sorted(["the company's name", "an e-mail address", "a ticket key",
                                                "a host", "a short upper-case code",
                                                "a value of the private configuration"]))

    def test_a_plain_word_in_the_configuration_is_not_a_secret(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        cfg = os.path.join(d, "src", "main", "resources")
        os.makedirs(cfg)
        with io.open(os.path.join(cfg, "program_configuration.json"), "w", encoding="utf-8") as fh:
            json.dump({"grant_type": "password", "base_url": "https://api.corpco.test", "pat": "Tok3n-Value"}, fh)
        got = rx.private_values(d)
        self.assertNotIn("password", got, "the field name 'password' would be hidden in every body")
        self.assertIn("https://api.corpco.test", got)
        self.assertIn("Tok3n-Value", got)


class TheCommittedExamples(unittest.TestCase):

    def test_they_hold_no_company_data(self):
        out = io.StringIO()
        from contextlib import redirect_stdout
        with redirect_stdout(out):
            rc = rx.check(ROOT)
        self.assertEqual(rc, 0, out.getvalue())

    def test_the_index_names_files_that_exist(self):
        with io.open(os.path.join(ROOT, rx.INDEX), encoding="utf-8") as fh:
            index = json.load(fh)
        self.assertTrue(index)
        for ex in index:
            self.assertTrue(os.path.isfile(os.path.join(ROOT, *ex["file"].split("/"))), ex["file"])
            self.assertIn(ex["project"], ("b2b", "goal"))
            self.assertTrue(ex["calls"], ex)

    def test_the_picks_name_no_method_and_no_key(self):
        with io.open(os.path.join(ROOT, rx.PICKS), encoding="utf-8") as fh:
            text = fh.read()
        self.assertNotIn('"method"', text, "a method name can carry a ticket number")
        self.assertEqual(rx.leaks(text, []), [])


class ThePromptNamesThem(unittest.TestCase):

    def test_a_call_gets_the_examples_that_make_it(self):
        fc._REFS.clear()
        refs = fc.references_for(ROOT, "b2b", "POST /businesses/{accountId}/activate")
        self.assertTrue(refs)
        self.assertTrue(all(f.startswith(".cursor/skills/jira-to-restassured/reference/b2b/") for f, _t in refs))
        self.assertEqual(fc.references_for(ROOT, "goal", "POST /businesses/{accountId}/activate"), [],
                         "another project's examples are not offered")


if __name__ == "__main__":
    unittest.main(verbosity=1)
