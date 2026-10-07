"""Storage XHTML becomes readable text WITHOUT losing the payload.

    python tools/confluence/test_storage.py

The first test is the reason this module exists. Strip tags before
lifting code macros and the page still reads fine -- it just no longer
contains the request anybody came for, and nothing downstream can tell
the difference between that and a page that never had one.
"""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import storage  # noqa: E402

CODE_PAGE = """
<p>The create call is below.</p>
<ac:structured-macro ac:name="code">
  <ac:parameter ac:name="language">json</ac:parameter>
  <ac:plain-text-body><![CDATA[{"propCode": "ABC", "peakRooms": 10}]]></ac:plain-text-body>
</ac:structured-macro>
<p>Expect 201.</p>
"""


class CodeSurvives(unittest.TestCase):
    def test_the_payload_is_still_there_after_rendering(self):
        text = storage.to_text(CODE_PAGE)
        self.assertIn('"propCode": "ABC"', text)
        self.assertIn("```json", text)
        self.assertIn("Expect 201.", text)

    def test_code_blocks_are_extractable_on_their_own(self):
        blocks = storage.extract_code_blocks(CODE_PAGE)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["language"], "json")
        self.assertIn("peakRooms", blocks[0]["text"])

    def test_a_macro_without_cdata_still_yields_its_body(self):
        body = ('<ac:structured-macro ac:name="noformat">'
                '<ac:plain-text-body>GET /props/ABC/groups</ac:plain-text-body>'
                '</ac:structured-macro>')
        self.assertIn("GET /props/ABC/groups", storage.to_text(body))
        self.assertEqual(len(storage.extract_code_blocks(body)), 1)

    def test_a_page_with_no_code_yields_no_blocks(self):
        self.assertEqual(storage.extract_code_blocks("<p>words</p>"), [])


class Readability(unittest.TestCase):
    def test_tables_keep_their_columns(self):
        body = ("<table><tbody>"
                "<tr><th>field</th><th>value</th></tr>"
                "<tr><td>propCode</td><td>ABC</td></tr>"
                "</tbody></table>")
        text = storage.to_text(body)
        self.assertIn("field | value", text)
        self.assertIn("propCode | ABC", text)

    def test_list_items_become_dashes(self):
        text = storage.to_text("<ul><li>one</li><li>two</li></ul>")
        self.assertIn("- one", text)
        self.assertIn("- two", text)

    def test_entities_are_unescaped(self):
        self.assertIn("Rates & Taxes", storage.to_text("<p>Rates &amp; Taxes</p>"))

    def test_navigation_macros_are_dropped(self):
        body = ('<ac:structured-macro ac:name="toc"/>'
                "<p>real content</p>")
        text = storage.to_text(body)
        self.assertIn("real content", text)
        self.assertNotIn("toc", text.lower())

    def test_a_macro_nested_in_rich_text_is_still_reached(self):
        body = ('<ac:structured-macro ac:name="info">'
                "<ac:rich-text-body>"
                '<ac:structured-macro ac:name="code">'
                "<ac:plain-text-body><![CDATA[POST /x]]></ac:plain-text-body>"
                "</ac:structured-macro>"
                "</ac:rich-text-body>"
                "</ac:structured-macro>")
        self.assertIn("POST /x", storage.to_text(body))

    def test_blank_runs_collapse(self):
        text = storage.to_text("<p>a</p><p></p><p></p><p></p><p>b</p>")
        self.assertNotIn("\n\n\n", text)

    def test_empty_body_is_empty_not_an_error(self):
        self.assertEqual(storage.to_text(""), "")
        self.assertEqual(storage.to_text(None), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
