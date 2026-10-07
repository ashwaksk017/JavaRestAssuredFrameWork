"""The Confluence fetcher refuses what it should, and leaks nothing.

    python tools/confluence/test_fetch.py

No network: the transport is replaced. What is worth testing here is not
"does urllib work" but the three things that go wrong quietly --
fetching an unapproved host, a `base_urls` that is a string instead of a
list, and a page's inline credentials reaching the snapshot, the UI and
the agent prompt.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# NOT `sys.path.insert` for tools/jira: it also has a module called
# `fetch`, and putting it on the path made `import fetch` resolve to the
# Jira one. Load this package's module by path instead.
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "confluence_fetch", os.path.join(HERE, "fetch.py"))
cf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cf)

PAGE = {
    "id": "12345",
    "title": "Group Rate Plan API",
    "space": {"key": "ABC"},
    "version": {"number": 7, "when": "2026-10-01T10:00:00.000Z"},
    "body": {"storage": {"value":
        '<p>Create a rate plan.</p>'
        '<ac:structured-macro ac:name="code">'
        '<ac:parameter ac:name="language">json</ac:parameter>'
        '<ac:plain-text-body><![CDATA[{"propCode": "ABC"}]]></ac:plain-text-body>'
        '</ac:structured-macro>'}},
}


class Redaction(unittest.TestCase):
    """Pages carry credentials inline far more often than anyone likes."""

    def test_an_authorization_header_is_masked(self):
        out = cf.redact("Authorization: Bearer abc123def456ghi")
        self.assertIn("<redacted>", out)
        self.assertNotIn("abc123def456ghi", out)

    def test_named_credentials_are_masked(self):
        for line in ('client_secret: s3cr3tvalue123',
                     'password = hunter2hunter2',
                     'api_key: "AKIAIOSFODNN7EXAMPLE"',
                     "pat=glpat-abcdefghijklmno"):
            out = cf.redact(line)
            self.assertIn("<redacted>", out, line)
            for leak in ("s3cr3tvalue123", "hunter2hunter2",
                         "AKIAIOSFODNN7EXAMPLE", "glpat-abcdefghijklmno"):
                self.assertNotIn(leak, out)

    def test_ordinary_prose_is_untouched(self):
        text = "The propCode is ABC and the token is issued by the gateway."
        self.assertEqual(cf.redact(text), text)

    def test_redaction_applies_to_the_rendered_page(self):
        page = json.loads(json.dumps(PAGE))
        page["body"]["storage"]["value"] += "<p>client_secret: topsecret12345</p>"
        out = cf.render(page)
        self.assertNotIn("topsecret12345", out)

    def test_redaction_applies_to_extracted_code_blocks(self):
        page = json.loads(json.dumps(PAGE))
        page["body"]["storage"]["value"] = (
            '<ac:structured-macro ac:name="code"><ac:plain-text-body>'
            '<![CDATA[{"client_secret": "leakme9876543"}]]>'
            '</ac:plain-text-body></ac:structured-macro>')
        blocks = cf.code_blocks(page)
        self.assertEqual(len(blocks), 1)
        self.assertNotIn("leakme9876543", blocks[0]["text"])


class Rendering(unittest.TestCase):
    def test_the_snapshot_carries_title_revision_and_payload(self):
        out = cf.render(PAGE)
        self.assertIn("# Group Rate Plan API", out)
        self.assertIn("v7", out)
        self.assertIn('"propCode": "ABC"', out)

    def test_comments_are_appended_when_asked_for(self):
        comment = {"body": {"storage": {"value": "<p>Use 202 not 201.</p>"}}}
        out = cf.render(PAGE, [comment])
        self.assertIn("Use 202 not 201.", out)

    def test_code_blocks_come_from_the_page_and_its_comments(self):
        comment = {"body": {"storage": {"value":
            '<ac:structured-macro ac:name="code"><ac:plain-text-body>'
            '<![CDATA[GET /x]]></ac:plain-text-body></ac:structured-macro>'}}}
        blocks = cf.code_blocks(PAGE, [comment])
        self.assertEqual(len(blocks), 2)

    def test_revision_is_readable(self):
        self.assertIn("v7", cf.page_revision(PAGE))


class Refusals(unittest.TestCase):
    """Each of these is a run that would otherwise look like it worked."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._section = cf.projectconfig.section
        self.addCleanup(setattr, cf.projectconfig, "section", self._section)

    def _config(self, cfg):
        cf.projectconfig.section = lambda name: (cfg, "test")

    def test_no_config_block(self):
        self._config({})
        rc = cf.main(["--id", "1", "--out", self.tmp])
        self.assertEqual(rc, 1)

    def test_empty_pat(self):
        self._config({"base_urls": ["https://wiki.example.com"], "pat": ""})
        self.assertEqual(cf.main(["--id", "1", "--out", self.tmp]), 1)

    def test_base_urls_as_a_string_is_refused_by_name(self):
        """A bare string iterates as characters, so every host fails the
        allowlist -- and the message would otherwise blame the host."""
        self._config({"base_urls": "https://wiki.example.com", "pat": "t"})
        self.assertEqual(cf.main(["--id", "1", "--out", self.tmp]), 1)

    def test_an_unapproved_host_is_refused(self):
        self._config({"base_urls": ["https://wiki.example.com"], "pat": "t"})
        rc = cf.main(["--url",
                      "https://evil.example.com/pages/viewpage.action?pageId=1",
                      "--out", self.tmp])
        self.assertEqual(rc, 1)

    def test_an_empty_body_is_refused(self):
        self._config({"base_urls": ["https://wiki.example.com"], "pat": "t"})
        empty = json.loads(json.dumps(PAGE))
        empty["body"]["storage"]["value"] = "   "
        cf.fetch_page = lambda *a, **k: empty
        self.addCleanup(setattr, cf, "fetch_page", cf.fetch_page)
        rc = cf.main(["--url",
                      "https://wiki.example.com/pages/viewpage.action?pageId=1",
                      "--out", self.tmp])
        self.assertEqual(rc, 1)


class AllowlistParity(unittest.TestCase):
    """The rule is stated twice; it must not drift.

    `host_allowed` is implemented here rather than imported from
    tools/jira/fetch.py, because that module's refusal names
    `jira_config` and would send a Confluence user to the wrong setting.
    Duplicating a security rule is only acceptable while something keeps
    the copies honest -- this is that something."""

    CASES = [
        ("https://wiki.example.com", ["https://wiki.example.com"]),
        ("https://WIKI.example.com", ["https://wiki.example.com"]),
        ("http://wiki.example.com", ["https://wiki.example.com"]),
        ("https://evil.example.com", ["https://wiki.example.com"]),
        ("https://wiki.example.com", []),
        ("https://wiki.example.com:8443", ["https://wiki.example.com"]),
        ("https://a.example.com", ["https://b.example.com",
                                   "https://a.example.com"]),
    ]

    def test_same_verdict_as_the_jira_allowlist(self):
        jira_dir = os.path.join(os.path.dirname(HERE), "jira")
        if not os.path.isfile(os.path.join(jira_dir, "fetch.py")):
            self.skipTest("tools/jira/fetch.py not present")
        sys.path.insert(0, jira_dir)
        spec = importlib.util.spec_from_file_location(
            "jira_fetch_for_parity", os.path.join(jira_dir, "fetch.py"))
        jf = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(jf)
        for url, allow in self.CASES:
            self.assertEqual(
                cf.host_allowed(url, allow)[0],
                jf.host_allowed(url, allow)[0],
                f"allowlist verdicts differ for {url} against {allow}")


class HappyPath(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._section = cf.projectconfig.section
        self._page = cf.fetch_page
        self.addCleanup(setattr, cf.projectconfig, "section", self._section)
        self.addCleanup(setattr, cf, "fetch_page", self._page)
        cf.projectconfig.section = lambda name: (
            {"base_urls": ["https://wiki.example.com"], "pat": "t"}, "test")
        cf.fetch_page = lambda *a, **k: PAGE

    def test_it_writes_a_snapshot_and_a_manifest(self):
        rc = cf.main(["--url",
                      "https://wiki.example.com/pages/viewpage.action?pageId=12345",
                      "--out", self.tmp])
        self.assertEqual(rc, 0)
        names = sorted(os.listdir(self.tmp))
        self.assertTrue(any(n.endswith(".md") for n in names), names)
        self.assertTrue(any(n.endswith(".json") for n in names), names)
        meta = json.load(io.open(
            os.path.join(self.tmp, [n for n in names if n.endswith(".json")][0]),
            encoding="utf-8"))
        self.assertEqual(meta["page_id"], "12345")
        self.assertEqual(len(meta["code_blocks"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
