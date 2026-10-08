"""A paste is stories, a query, or neither -- by its shape, never by a guess.

    python tools/jira/test_query.py
"""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import query  # noqa: E402

J = "https://jira.example.com"


class Keys(unittest.TestCase):
    def test_one_key_and_several_in_any_separator(self):
        self.assertEqual(query.parse("abc-12")["keys"], ["ABC-12"])
        r = query.parse("ABC-12, ABC-13;abc-12\n  XY_Z-7")
        self.assertEqual((r["kind"], r["keys"], r["host"]),
                         (query.KEYS, ["ABC-12", "ABC-13", "XY_Z-7"], ""))

    def test_a_story_address_gives_the_key_and_the_host_to_check(self):
        for url in (f"{J}/browse/ABC-12", f"{J}/browse/abc-12?focusedCommentId=5#c",
                    f"{J}/projects/ABC/issues/ABC-12?filter=allopenissues",
                    f"{J}/secure/RapidBoard.jspa?rapidView=3&selectedIssue=ABC-12"):
            r = query.parse(url)
            self.assertEqual((r["kind"], r["keys"], r["host"]), (query.KEYS, ["ABC-12"], J), url)

    def test_a_story_opened_from_a_search_is_the_story(self):
        r = query.parse(f"{J}/browse/ABC-12?jql=project%20%3D%20ABC")
        self.assertEqual((r["kind"], r["keys"]), (query.KEYS, ["ABC-12"]))

    def test_keys_and_addresses_mixed_share_one_host_or_are_refused(self):
        r = query.parse(f"ABC-1 {J}/browse/ABC-2")
        self.assertEqual((r["keys"], r["host"]), (["ABC-1", "ABC-2"], J))
        r = query.parse(f"{J}/browse/ABC-1 https://other.example/browse/ABC-2")
        self.assertEqual(r["kind"], query.NONE)
        self.assertIn("more than one host", r["why"])

    def test_too_many_keys_is_a_refusal_not_a_slow_run(self):
        r = query.parse(" ".join(f"ABC-{n}" for n in range(query.MAX_KEYS + 1)))
        self.assertEqual(r["kind"], query.NONE)

    def test_a_wiki_page_whose_title_looks_like_a_key_is_not_a_story(self):
        for url in ("https://wiki.example.com/display/SP/Release-12",
                    "https://wiki.example.com/spaces/SP/pages/123/Sprint-4",
                    f"{J}/secure/attachment/55/ABC-12"):
            self.assertEqual(query.parse(url)["kind"], query.NONE, url)

    def test_a_key_is_not_found_inside_another_word(self):
        for text in ("XABC-12Y is broken", "see ticket ABC-12 please", "ABC-", "-12", "12-ABC"):
            self.assertNotEqual(query.parse(text)["kind"], query.KEYS, text)


class Queries(unittest.TestCase):
    def test_a_query_is_returned_exactly_as_written(self):
        jql = 'project in (ABC, XYZ) AND labels = "api, v2"  ORDER BY updated DESC'
        r = query.parse("  " + jql + "\n")
        self.assertEqual((r["kind"], r["jql"], r["keys"]), (query.JQL, jql, []))

    def test_the_shapes_that_are_queries(self):
        for jql in ("project = ABC", "fixVersion=6.02", 'summary ~ "rate plan"',
                    "status not in (Done, Closed)", "assignee is EMPTY", "resolution IS NOT EMPTY",
                    "cf[10010] = 5", '"Epic Link" = ABC-1', "status changed to Done",
                    "status was in (Open)", "(project = ABC OR project = XYZ)",
                    "order by created", "created >= -7d", "issuekey in (ABC-1, ABC-2)"):
            self.assertEqual(query.parse(jql)["kind"], query.JQL, jql)

    def test_functions_history_and_negation_are_queries(self):
        for jql in ("sprint in openSprints()", "fixVersion in releasedVersions()",
                    "issue in linkedIssues(ABC-1)", "Sprint not in closedSprints()",
                    "status was Done", 'status was not "In Progress"',
                    "assignee was currentUser()", "status changed", "status changed after -1w",
                    'status WAS "Open" BEFORE "2020/01/01"', "NOT status = Done",
                    "assignee = currentUser()", 'text ~ "x"',
                    "status changed from Open to Done by jdoe order by key",
                    "project = ABC AND (labels = a OR labels in (b, c)) ORDER BY created DESC, key",
                    "\n  project = ABC\n  AND labels = api\n"):
            self.assertTrue(query.is_jql(jql), jql)

    def test_one_clause_inside_a_sentence_is_not_a_query(self):
        for text in ("the total = 5 in the response", "expected status = 200",
                     "Content-Type: text/html; charset=utf-8", "the flag is null",
                     "it was to be expected", "he was not in (the room)",
                     "the color changed from red to blue", "ABC-1 ORDER BY x",
                     "project = ", "= 5", "project = ABC AND", "(project = ABC",
                     'summary ~ "unclosed', "project = ABC ) OR ( 1 = 1"):
            self.assertFalse(query.is_jql(text), text)
            self.assertEqual(query.parse(text)["kind"], query.NONE, text)

    def test_a_remark_that_is_a_whole_query_is_a_query_and_that_is_documented(self):
        for text in ("status = 200", "flag is null", "order by phone"):
            self.assertTrue(query.is_jql(text), text)

    def test_hostile_nesting_is_refused_quickly_not_recursed_into(self):
        import time
        t0 = time.time()
        for text in ("(" * 4000, "not " * 4000 + "a = b", "(" * 500 + "a = b" + ")" * 500,
                     "a = b AND " * 700 + "a = b"):
            query.is_jql(text)
            query.parse(text)
        self.assertLess(time.time() - t0, 3)

    def test_sentences_that_use_jqls_words_are_not_queries(self):
        for text in ("the status in the response is wrong",
                     "check the type and status of the account",
                     "order was not created", "please test the login",
                     "key not found for project", "it is empty sometimes",
                     "NOT working AND broken OR slow"):
            r = query.parse(text)
            self.assertEqual(r["kind"], query.NONE, text)
            self.assertTrue(r["why"])

    def test_a_search_address_gives_the_decoded_query_and_the_host(self):
        r = query.parse(f"{J}/issues/?jql=project%20%3D%20ABC%20AND%20labels%20in%20(a%2C%20b)")
        self.assertEqual((r["kind"], r["jql"], r["host"]),
                         (query.JQL, "project = ABC AND labels in (a, b)", J))
        r = query.parse(f"{J}/issues/?jql=project+%3D+ABC&startIndex=50")
        self.assertEqual(r["jql"], "project = ABC")

    def test_a_saved_filter_address_is_a_query_on_that_filter(self):
        self.assertEqual(query.parse(f"{J}/issues/?filter=10400")["jql"], "filter = 10400")
        self.assertEqual(query.parse(f"{J}/issues/?filter=-1 OR 1=1")["kind"], query.NONE,
                         "a filter id is digits, or it is not used")
        self.assertEqual(query.parse(f"{J}/issues/?filter=allopenissues")["kind"], query.NONE)

    def test_what_an_address_says_is_a_query_must_be_one(self):
        r = query.parse(f"{J}/issues/?jql=hello%20world")
        self.assertEqual((r["kind"], r["host"]), (query.NONE, J))
        r = query.parse(f"{J}/secure/RapidBoard.jspa?selectedIssue=ABC-2&jql=project%20%3D%20ABC")
        self.assertEqual(r["keys"], ["ABC-2"], "the story that is open, not the board's filter")

    def test_only_ascii_digits_are_digits(self):
        self.assertEqual(query.parse("ABC-\u0661\u0662")["kind"], query.NONE)
        self.assertEqual(query.parse(f"{J}/issues/?filter=\u00b2")["kind"], query.NONE)
        self.assertEqual(query.parse(f"{J}/issues/?filter=\uff11\uff12")["kind"], query.NONE)

    def test_an_absurdly_long_query_is_refused(self):
        self.assertEqual(query.parse("project = " + "A" * query.MAX_JQL)["kind"], query.NONE)
        self.assertEqual(query.parse(f"{J}/issues/?jql=" + "a" * (query.MAX_JQL + 1))["kind"], query.NONE)


class Neither(unittest.TestCase):
    def test_nothing_and_other_addresses(self):
        self.assertEqual(query.parse("")["why"], "nothing was pasted")
        self.assertEqual(query.parse(None)["kind"], query.NONE)
        r = query.parse(f"{J}/secure/Dashboard.jspa")
        self.assertEqual((r["kind"], r["host"]), (query.NONE, J))
        for other in ("ftp://jira.example.com/browse/ABC-1", "javascript:alert(1)",
                      "file:///etc/passwd", "//jira.example.com/browse/ABC-1"):
            self.assertEqual(query.parse(other)["kind"], query.NONE, other)

    def test_an_address_that_cannot_be_read_is_none_not_an_exception(self):
        for bad in ("https://[bad/browse/ABC-1", "https://jira.example.com:abc/browse/ABC-1x",
                    "https://", "http://?jql=a%3Db"):
            self.assertEqual(query.parse(bad)["kind"], query.NONE, bad)

    def test_an_address_pasted_out_of_a_sentence_is_still_that_address(self):
        for text in (f"<{J}/browse/ABC-1>", f"{J}/browse/ABC-1.", f"{J}/browse/ABC-1,",
                     f"({J}/browse/ABC-1)"):
            r = query.parse(text)
            self.assertEqual((r["kind"], r["keys"], r["host"]), (query.KEYS, ["ABC-1"], J), text)

    def test_every_host_in_a_paste_is_reported_even_when_nothing_else_is(self):
        r = query.parse("https://evil.example/x project = ABC")
        self.assertEqual((r["kind"], r["hosts"]), (query.NONE, ["https://evil.example"]))
        self.assertIn("on its own", r["why"])
        r = query.parse(f"{J}/browse/ABC-1 https://evil.example/secure/Dashboard.jspa")
        self.assertEqual(r["kind"], query.NONE)
        self.assertEqual(r["hosts"], ["https://evil.example", J])
        r = query.parse(f"see {J}/browse/ABC-1 (the login story)")
        self.assertEqual((r["kind"], r["hosts"]), (query.NONE, [J]))

    def test_a_flood_of_tokens_is_answered_at_once(self):
        import time
        t0 = time.time()
        r = query.parse(" ".join(f"ABC-{n}" for n in range(40000)))
        self.assertEqual(r["kind"], query.NONE)
        self.assertLess(time.time() - t0, 2)

    def test_the_host_is_reported_never_acted_on(self):
        r = query.parse("https://evil.example/browse/ABC-1")
        self.assertEqual((r["kind"], r["host"]), (query.KEYS, "https://evil.example"),
                         "what it is, and where it claims to be from: the caller refuses the host")

    def test_userinfo_in_an_address_stays_in_the_host_so_the_allowlist_rejects_it(self):
        r = query.parse("https://jira.example.com@evil.example/browse/ABC-1")
        self.assertEqual(r["host"], "https://jira.example.com@evil.example")


if __name__ == "__main__":
    unittest.main(verbosity=1)
