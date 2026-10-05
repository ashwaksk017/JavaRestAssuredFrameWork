"""Reading a request out of a story, and refusing to invent one.

The load-bearing behaviours:

  * a CONCRETE path is matched back to the recorded TEMPLATE. Without
    this every story matched nothing, because the signature holds the
    resource path literally and a recording holds `{accountId}` where a
    story holds `12345`. A false `NEW` is the worst answer the pipeline
    can give -- it reads as permission to write a duplicate.
  * a path parameter is emitted as a REF, because 8,748 of 9,073
    recorded path params are refs and a literal would key as `row`.
  * when nothing can be READ, nothing is written. No verb, no path and no
    status is ever inferred from prose.
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import extract  # noqa: E402

TEMPLATES = [
    "/businesses/{accountId}/activate",
    "/businesses/{accountId}",
    "/guests/{guestId}/businesses",
    "/guests/{guestId}/businesses/{accountId}/members",
    "/realms/guests/enroll",
    "/topics/{topicName}/partitions/{partitionId}",
]


def index_of(paths):
    """A shape index holding exactly these paths, as shapes.py writes it."""
    exact = {}
    for p in paths:
        sig = json.dumps([["GET", p, "application/json", "-", [], []]])
        exact[sig] = [{"suite": "s", "case_name": "c"}]
    return {"exact": exact}


def story(description, key="ABC-1", parents=None, comments=None):
    fields = {"description": description}
    if comments:
        fields["comment"] = {"comments": [{"author": {"displayName": "QA"},
                                           "body": c} for c in comments]}
    return {"summary": {"issue_key": key, "payload_candidates": [],
                        "attachments": []},
            "issue": {"key": key, "fields": fields},
            "parents": parents or []}


class PathTemplates(unittest.TestCase):
    def test_templates_come_out_of_the_index(self):
        got = extract.path_templates(index_of(TEMPLATES))
        self.assertEqual(sorted(got), sorted(TEMPLATES))

    def test_a_concrete_path_resolves_to_its_template(self):
        got = extract.templatize("/businesses/12345/activate", TEMPLATES)
        self.assertEqual(got["matched"], "/businesses/{accountId}/activate")
        self.assertEqual(got["params"], {"accountId": "12345"})

    def test_a_leading_version_or_gateway_prefix_is_dropped_and_reported(self):
        got = extract.templatize("/v1/businesses/12345/activate", TEMPLATES)
        self.assertEqual(got["matched"], "/businesses/{accountId}/activate")
        self.assertEqual(got["trimmed"], "v1")
        self.assertTrue(any("dropped the leading" in n for n in got["notes"]))

    def test_an_already_templated_path_matches_as_is(self):
        got = extract.templatize("/businesses/{accountId}/activate", TEMPLATES)
        self.assertEqual(got["matched"], "/businesses/{accountId}/activate")

    def test_a_path_with_no_parameters_matches(self):
        got = extract.templatize("/realms/guests/enroll", TEMPLATES)
        self.assertEqual(got["matched"], "/realms/guests/enroll")
        self.assertEqual(got["params"], {})

    def test_several_fitting_templates_are_listed_and_none_chosen(self):
        """Two one-parameter templates of the same arity both fit `/a/x`."""
        got = extract.templatize("/a/x", ["/a/{id}", "/{kind}/x"])
        self.assertEqual(got["matched"], "")
        self.assertEqual(sorted(got["alternatives"]), ["/a/{id}", "/{kind}/x"])
        self.assertTrue(any("Not choosing" in n for n in got["notes"]))

    def test_an_unknown_path_is_left_alone(self):
        got = extract.templatize("/nothing/like/this/at/all", TEMPLATES)
        self.assertEqual(got["matched"], "")
        self.assertEqual(got["alternatives"], [])

    def test_segment_count_must_agree(self):
        self.assertIsNone(extract._fits("/a/{b}", "/a/b/c"))

    def test_a_template_segment_never_matches_an_empty_one(self):
        self.assertIsNone(extract._fits("/a/{b}", "/a/"))


class Curl(unittest.TestCase):
    def test_verb_url_body_and_content_type(self):
        got = extract.parse_curl(
            """curl -X PATCH 'https://api.example.com/businesses/9' """
            """-H 'Content-Type: application/merge-patch+json' """
            """-d '{"status":"ACTIVE"}'""")
        self.assertEqual(got["verb"], "PATCH")
        self.assertEqual(got["body"], '{"status":"ACTIVE"}')
        self.assertEqual(got["headers"]["Content-Type"],
                         "application/merge-patch+json")

    def test_a_body_with_no_X_flag_is_a_post_which_is_curls_own_rule(self):
        got = extract.parse_curl(
            """curl https://api.example.com/x -d '{"a":1}'""")
        self.assertEqual(got["verb"], "POST")

    def test_no_body_and_no_flag_is_a_get(self):
        got = extract.parse_curl("curl https://api.example.com/x")
        self.assertEqual(got["verb"], "GET")

    def test_line_continuations_are_joined(self):
        got = extract.parse_curl(
            "curl -X POST \\\n  https://api.example.com/x \\\n  -d '{}'")
        self.assertEqual(got["verb"], "POST")
        self.assertTrue(got["url"].endswith("/x"))

    def test_a_flag_that_takes_a_value_never_becomes_the_url(self):
        """`-u user:pass` once ate the URL and the step came out pathless."""
        got = extract.parse_curl(
            "curl -s -u someone:secret --compressed https://api.example.com/x")
        self.assertEqual(got["url"], "https://api.example.com/x")

    def test_a_curl_with_no_url_yields_nothing(self):
        self.assertEqual(extract.parse_curl("curl -X POST -d '{}'"), {})

    def test_query_parameter_names_are_read_from_the_url(self):
        steps = extract.from_text(
            "curl 'https://api.example.com/x?limit=10&offset=0'", "here")
        self.assertEqual(sorted(steps[0]["query_param_names"]),
                         ["limit", "offset"])


class HttpLine(unittest.TestCase):
    def test_a_verb_and_path_line_is_read(self):
        steps = extract.from_text("Call `POST /businesses/{accountId}/activate` "
                                  "to activate.", "desc")
        self.assertEqual(steps[0]["verb"], "POST")
        self.assertEqual(steps[0]["raw_path"],
                         "/businesses/{accountId}/activate")

    def test_jira_bold_and_heading_noise_does_not_block_it(self):
        steps = extract.from_text("h3. *POST* /guests/7/businesses", "desc")
        self.assertEqual(steps[0]["verb"], "POST")
        self.assertEqual(steps[0]["raw_path"], "/guests/7/businesses")

    def test_the_body_taken_is_the_block_AFTER_the_call(self):
        """A story writes the call, then its body. Searching backwards
        attached the previous request's payload to this one."""
        text = ('```json\n{"first":1}\n```\n'
                'Then POST /realms/guests/enroll\n'
                '```json\n{"second":2}\n```\n')
        steps = extract.from_text(text, "desc")
        self.assertEqual(json.loads(steps[0]["body"]), {"second": 2})

    def test_a_get_takes_no_body(self):
        text = "GET /businesses/1\n```json\n{\"a\":1}\n```"
        steps = extract.from_text(text, "desc")
        self.assertEqual(steps[0]["body"], "")

    def test_curl_wins_over_a_prose_line(self):
        """A curl carries headers and a body; a prose line carries neither."""
        text = ("POST /wrong/path\n"
                "curl -X POST https://api.example.com/right/path -d '{}'")
        steps = extract.from_text(text, "desc")
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["raw_path"], "/right/path")

    def test_a_non_verb_word_is_not_a_verb(self):
        self.assertEqual(extract.from_text("POSTAL /x is not a call", "d"), [])


class ExpectedStatus(unittest.TestCase):
    def test_returns_a_code(self):
        self.assertEqual(extract.expected_status("It returns 204.")[0], "204")

    def test_responds_with_a_code(self):
        self.assertEqual(
            extract.expected_status("The API responds with a 409 here.")[0],
            "409")

    def test_an_arrow_form(self):
        self.assertEqual(extract.expected_status("activate -> 201")[0], "201")

    def test_the_sentence_it_came_from_is_kept_as_evidence(self):
        code, line = extract.expected_status("Blah.\nIt returns 400 on retry.")
        self.assertEqual(code, "400")
        self.assertIn("400", line)

    def test_an_unstated_status_stays_empty_rather_than_defaulting_to_200(self):
        self.assertEqual(extract.expected_status("Activate the account.")[0], "")

    def test_a_bare_number_is_not_a_status(self):
        self.assertEqual(extract.expected_status("about 204 accounts")[0], "")


class WholeStory(unittest.TestCase):
    def test_a_curl_story_extracts_and_templatizes(self):
        doc = extract.extract(
            story("curl -X POST https://api.example.com/v1/businesses/55/activate"
                  " -d '{\"activationSource\":\"x\"}'\nIt returns 204.\n"),
            index_of(TEMPLATES))
        self.assertEqual(doc["extraction"], "OK")
        step = doc["steps"][0]
        self.assertEqual(step["path"], "/businesses/{accountId}/activate")
        self.assertEqual(step["expected_status"], "204")

    def test_a_path_parameter_is_emitted_as_a_ref_not_a_literal(self):
        """A literal keys as `row`; 8,748 of 9,073 recorded params are refs."""
        doc = extract.extract(
            story("GET /businesses/12345"), index_of(TEMPLATES))
        step = doc["steps"][0]
        self.assertEqual(step["path_params"], {"accountId": "${accountId}"})
        self.assertEqual(step["_path_param_examples"], {"accountId": "12345"})
        self.assertTrue(any("emitted as refs" in n for n in doc["notes"]))

    def test_comments_and_parents_are_searched(self):
        doc = extract.extract(
            story("nothing here", comments=["POST /realms/guests/enroll"]),
            index_of(TEMPLATES))
        self.assertEqual(doc["extraction"], "OK")
        self.assertIn("comment by QA", doc["steps"][0]["_provenance"])

    def test_a_story_with_no_request_writes_nothing_and_says_why(self):
        doc = extract.extract(story("Please make activation faster."),
                              index_of(TEMPLATES))
        self.assertEqual(doc["extraction"], "FAILED")
        self.assertIn("no request could be READ", doc["why"])
        self.assertEqual(doc["steps"], [])

    def test_a_payload_alone_is_not_a_request(self):
        """A fenced body with no verb and no path must not become a step."""
        doc = extract.extract(story('```json\n{"activationSource":"x"}\n```'),
                              index_of(TEMPLATES))
        self.assertEqual(doc["extraction"], "FAILED")

    def test_without_an_index_it_says_so_rather_than_matching_blindly(self):
        doc = extract.extract(story("GET /businesses/12345"), {})
        self.assertTrue(any("no shape index" in n for n in doc["notes"]))
        self.assertEqual(doc["steps"][0]["path"], "/businesses/12345")

    def test_an_ambiguous_path_is_carried_into_the_candidate(self):
        doc = extract.extract(story("GET /a/x"),
                              index_of(["/a/{id}", "/{kind}/x"]))
        self.assertEqual(sorted(doc["steps"][0]["_path_alternatives"]),
                         ["/a/{id}", "/{kind}/x"])


class Har(unittest.TestCase):
    def test_a_har_entry_becomes_a_step(self):
        har = {"log": {"entries": [{"request": {
            "method": "POST", "url": "https://api.example.com/guests/3/businesses",
            "headers": [{"name": "Content-Type", "value": "application/json"}],
            "queryString": [], "postData": {"text": '{"selfManaged":true}'}}}]}}
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "capture.har")
            with io.open(p, "w", encoding="utf-8") as fh:
                json.dump(har, fh)
            steps = extract.from_har(p)
        self.assertEqual(steps[0]["verb"], "POST")
        self.assertEqual(steps[0]["raw_path"], "/guests/3/businesses")

    def test_a_har_is_preferred_over_prose_because_it_is_a_recording(self):
        har = {"log": {"entries": [{"request": {
            "method": "GET", "url": "https://api.example.com/businesses/77",
            "headers": [], "queryString": []}}]}}
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "c.har")
            with io.open(p, "w", encoding="utf-8") as fh:
                json.dump(har, fh)
            doc = extract.extract(story("POST /realms/guests/enroll"),
                                  index_of(TEMPLATES),
                                  [{"name": "c.har", "saved_to": p}])
        self.assertEqual(doc["steps"][0]["verb"], "GET")
        self.assertEqual(doc["steps"][0]["path"], "/businesses/{accountId}")

    def test_a_broken_har_is_ignored_rather_than_fatal(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "bad.har")
            with io.open(p, "w", encoding="utf-8") as fh:
                fh.write("{not json")
            self.assertEqual(extract.from_har(p), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
