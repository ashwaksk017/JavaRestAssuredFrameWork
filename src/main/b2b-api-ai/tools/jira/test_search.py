"""Lists from Jira say when they are short, and the token goes one place.

    python tools/jira/test_search.py

No test here reaches a network: the transport is a function.
"""
from __future__ import annotations

import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import search  # noqa: E402

CONN = {"base": "https://jira.example.com", "bases": ["https://jira.example.com"],
        "token": "tok_1234567890", "timeout": 5, "api": "/rest/api/2", "cleartext": False}


def issue(n, **fields):
    return {"key": f"ABC-{n}", "fields": dict({"summary": f"story {n}",
                                               "status": {"name": "Open"}}, **fields)}


class Jira:
    """A search endpoint over a list, with the faults a real one has."""

    def __init__(self, issues=(), total=None, pages=None, fail=None):
        self.issues = list(issues)
        self.total = total
        self.pages = pages          # explicit page contents, by call order
        self.fail = fail
        self.calls = []

    def __call__(self, method, url, token, timeout, body=None):
        self.calls.append((method, url, token, body))
        if self.fail:
            raise self.fail
        if self.pages is not None:
            batch = self.pages[min(len(self.calls) - 1, len(self.pages) - 1)]
        else:
            at, size = body["startAt"], body["maxResults"]
            batch = self.issues[at:at + size]
        total = len(self.issues) if self.total is None else self.total
        return {"issues": batch, "total": total}


class TheConnection(unittest.TestCase):
    def test_everything_comes_from_the_config_and_the_first_base_url(self):
        conn = search.connect({"base_urls": ["https://jira.example.com/", "https://other.example"],
                               "pat": " tok ", "timeout_seconds": "12"}, "env qa")
        self.assertEqual(conn, {"base": "https://jira.example.com", "token": "tok",
                                "bases": ["https://jira.example.com/", "https://other.example"],
                                "timeout": 12, "api": "/rest/api/2", "cleartext": False})
        self.assertTrue(search.connect({"base_urls": ["http://jira.example.com"], "pat": "t"},
                                       "")["cleartext"], "http works, and is flagged")

    def test_a_missing_piece_is_refused_with_what_to_do(self):
        for cfg, word in (({}, "jira_config"),
                          ({"base_urls": [], "pat": "t"}, "base_urls is empty"),
                          ({"base_urls": ["jira.example.com"], "pat": "t"}, "not a plain"),
                          ({"base_urls": ["https://user:pw@jira.example.com"], "pat": "t"}, "not a plain"),
                          ({"base_urls": ["https://jira.example.com/jira?x=1"], "pat": "t"}, "not a plain"),
                          ({"base_urls": ["https://j.example"], "pat": "tok\nX-Evil: 1"}, "cannot be sent"),
                          ({"base_urls": ["https://j.example"], "pat": "two words"}, "cannot be sent"),
                          ({"base_urls": ["https://j.example"], "pat": "t",
                            "api_path": "/rest/api/2/../../x"}, "api_path"),
                          ({"base_urls": ["https://j.example"], "pat": ""}, "pat is empty"),
                          ({"base_urls": ["https://j.example"], "pat": "t",
                            "api_path": "/rest/../../x?y"}, "api_path")):
            with self.assertRaises(search.Refused) as got:
                search.connect(cfg, "note")
            self.assertIn(word, str(got.exception))

    def test_a_bad_timeout_falls_back_instead_of_crashing(self):
        conn = search.connect({"base_urls": ["https://j.example"], "pat": "t",
                               "timeout_seconds": "soon"}, "")
        self.assertEqual(conn["timeout"], 30)

    def test_the_command_line_has_no_way_to_name_a_host(self):
        for cmd in (["verify", "--base", "https://evil.example"],
                    ["search", "--jql", "x", "--url", "https://evil.example"]):
            with self.assertRaises(SystemExit), redirect_stdout(io.StringIO()), \
                    open(os.devnull, "w") as null:
                err, sys.stderr = sys.stderr, null
                try:
                    search.main(cmd)
                finally:
                    sys.stderr = err


class TheToken(unittest.TestCase):
    def test_an_accepted_token_names_its_owner(self):
        jira = lambda *a, **k: {"name": "jdoe", "displayName": "J. Doe"}
        self.assertEqual(search.verify(CONN, jira), (True, "the token is accepted, as J. Doe"))

    def test_a_rejected_token_says_why(self):
        def jira(*a, **k):
            raise search.JiraError(401, search._error_text(401, "Unauthorized", ""))
        ok, msg = search.verify(CONN, jira)
        self.assertFalse(ok)
        self.assertIn("expired, revoked or mistyped", msg)

    def test_an_answer_that_is_not_a_user_is_not_a_pass(self):
        for answer in ({}, [], {"errorMessages": ["x"]}, None):
            ok, msg = search.verify(CONN, lambda *a, **k: answer)
            self.assertFalse(ok, answer)
            self.assertIn("anonymous", msg)

    def test_jiras_own_words_are_passed_on_and_the_token_is_not(self):
        text = search._error_text(400, "Bad Request", json.dumps(
            {"errorMessages": ["The value 'Nope' does not exist for the field 'project'."],
             "errors": {"jql": "bad"}}))
        self.assertIn("does not exist for the field", text)
        self.assertIn("bad", text)
        self.assertEqual(search._error_text(503, "Unavailable", "<html>gateway</html>"),
                         "Jira returned 503 Unavailable")

    def test_every_shape_jira_puts_its_message_in_is_read(self):
        text = lambda body: search._error_text(400, "Bad Request", json.dumps(body))
        self.assertIn("Jira said: oops", text({"errorMessages": "oops"}))
        self.assertIn("a; b", text({"errorMessages": ["a"], "errors": ["b"]}))
        self.assertIn("plain", text({"message": "plain"}))
        self.assertEqual(text({"errorMessages": 5}), "Jira returned 400 Bad Request")
        self.assertEqual(text([1, 2]), "Jira returned 400 Bad Request")

    def test_the_token_is_sent_as_a_header_to_the_configured_host_only(self):
        jira = Jira([issue(1)])
        search.search(CONN, "project = ABC", transport=jira)
        method, url, token, body = jira.calls[0]
        self.assertEqual((method, url, token),
                         ("POST", "https://jira.example.com/rest/api/2/search", "tok_1234567890"))
        self.assertNotIn("tok_", url)
        self.assertNotIn("tok_", json.dumps(body))


class Versions(unittest.TestCase):
    def test_they_are_ordered_by_number_not_by_text_or_date(self):
        names = ["Release 4.9", "Release 4.10", "Hotfix 6.02.02.03", "Release 6.02",
                 "Sprint June30", "Release 6.19", "Backlog"]
        jira = lambda *a, **k: [{"name": n, "id": i} for i, n in enumerate(names)]
        got = [v["name"] for v in search.list_versions(CONN, "abc", transport=jira)]
        self.assertEqual(got, ["Release 6.19", "Hotfix 6.02.02.03", "Release 6.02",
                               "Release 4.10", "Release 4.9", "Sprint June30", "Backlog"])

    def test_a_date_in_the_name_is_not_the_version_when_there_is_one(self):
        key = search.version_sort_key
        self.assertEqual(key("2024.06.30 Release 6.1"), (1, (6, 1)))
        self.assertEqual(key("Release 6.1 (2024.06.30)"), (1, (6, 1)))
        self.assertEqual(key("2024.06"), (1, (2024, 6)), "the only number there is")
        self.assertLess(key("Release 7"), key("Release 6.19"),
                        "a known limit: no dotted number sorts below any that has one")

    def test_archived_ones_are_left_out_unless_asked_for(self):
        jira = lambda *a, **k: [{"name": "1.0", "archived": True}, {"name": "2.0"},
                                {"name": "  "}, "junk"]
        self.assertEqual([v["name"] for v in search.list_versions(CONN, "ABC", transport=jira)],
                         ["2.0"])
        self.assertEqual(len(search.list_versions(CONN, "ABC", True, transport=jira)), 2)

    def test_a_project_key_that_could_change_the_url_or_the_query_is_refused(self):
        for bad in ("", "A", "abc/../x", "ABC OR 1=1", "A-B", "ABC\"", "1ABC"):
            with self.assertRaises(search.Refused, msg=bad):
                search.project_key(bad)
        self.assertEqual(search.project_key(" abc_2 "), "ABC_2")

    def test_an_answer_that_is_not_a_list_is_an_error_not_no_versions(self):
        with self.assertRaises(search.JiraError):
            search.list_versions(CONN, "ABC", transport=lambda *a, **k: {"errorMessages": ["x"]})


class Search(unittest.TestCase):
    def test_every_page_is_read_and_the_result_is_complete(self):
        jira = Jira([issue(n) for n in range(250)])
        r = search.search(CONN, "project = ABC", transport=jira)
        self.assertEqual((len(r["issues"]), r["total"], r["complete"], r["limited"]),
                         (250, 250, True, False))
        self.assertEqual([c[3]["startAt"] for c in jira.calls], [0, 100, 200])

    def test_a_query_without_an_order_gets_one_that_does_not_move(self):
        self.assertEqual(search.stable_order("project = ABC"),
                         ("project = ABC ORDER BY key ASC", True))
        self.assertEqual(search.stable_order("project = ABC order  BY updated"),
                         ("project = ABC order  BY updated", False))
        self.assertEqual(search.stable_order('summary ~ "sort order by date"')[1], True,
                         "the words inside a quoted value are not an ordering")

    def test_the_limit_is_reported_not_silent(self):
        jira = Jira([issue(n) for n in range(250)])
        r = search.search(CONN, "project = ABC", max_results=120, transport=jira)
        self.assertEqual((len(r["issues"]), r["limited"], r["complete"]), (120, True, True))
        self.assertEqual([c[3]["maxResults"] for c in jira.calls], [100, 20])

    def test_an_issue_that_came_back_twice_makes_the_result_incomplete(self):
        first = [issue(n) for n in range(100)]
        second = [issue(99)] + [issue(n) for n in range(101, 200)]     # 100 was never seen
        r = search.search(CONN, "project = ABC", transport=Jira(pages=[first, second], total=200))
        self.assertEqual((len(r["issues"]), r["duplicates"], r["complete"]), (199, 1, False))
        self.assertIn("came back twice", r["why_incomplete"])
        self.assertIn("never seen", r["why_incomplete"])

    def test_a_server_that_stops_early_is_incomplete_and_does_not_loop(self):
        jira = Jira(pages=[[issue(n) for n in range(100)], []], total=5000)
        r = search.search(CONN, "project = ABC", transport=jira)
        self.assertFalse(r["complete"])
        self.assertIn("stopped returning pages early", r["why_incomplete"])
        self.assertEqual(len(jira.calls), 2)

    def test_a_server_that_repeats_one_page_forever_is_bounded(self):
        jira = Jira(pages=[[issue(1)]], total=10**6)
        r = search.search(CONN, "project = ABC", max_results=300, transport=jira)
        self.assertEqual(len(jira.calls), 2, "a page with nothing new ends the walk")
        self.assertEqual((len(r["issues"]), r["complete"]), (1, False))

    def test_a_total_that_changes_between_pages_is_not_complete(self):
        """Page 1: 100 of 200. Fifty early issues leave the set. Page 2 at
        offset 100 is then the LAST 50 of 150 -- no duplicate, the count
        adds up, and 50 issues were never seen."""
        class Shrinking(Jira):
            def __call__(self, method, url, token, timeout, body=None):
                self.calls.append((method, url, token, body))
                if body["startAt"] == 0:
                    return {"issues": [issue(n) for n in range(100)], "total": 200}
                return {"issues": [issue(n) for n in range(150, 200)], "total": 150}
        r = search.search(CONN, "project = ABC", transport=Shrinking())
        self.assertEqual((len(r["issues"]), r["duplicates"], r["complete"]), (150, 0, False))
        self.assertIn("changed while the pages were read", r["why_incomplete"])

    def test_a_server_with_a_smaller_page_reads_everything_at_scale(self):
        class Capped(Jira):
            def __call__(self, method, url, token, timeout, body=None):
                body = dict(body, maxResults=min(body["maxResults"], 50))
                return super().__call__(method, url, token, timeout, body)
        jira = Capped([issue(n) for n in range(900)])
        r = search.search(CONN, "project = ABC", max_results=5000, transport=jira)
        self.assertEqual((len(r["issues"]), r["complete"], r["page_size"]), (900, True, 50))
        self.assertEqual(len(jira.calls), 18)

    def test_an_issue_without_a_key_uses_its_id_and_one_with_neither_is_said(self):
        by_id = [{"id": str(n), "fields": {}} for n in range(150)]
        r = search.search(CONN, "project = ABC", transport=Jira(by_id))
        self.assertEqual((len(r["issues"]), r["complete"]), (150, True))
        r = search.search(CONN, "project = ABC", transport=Jira([issue(1), {"fields": {}}]))
        self.assertEqual((len(r["issues"]), r["complete"]), (1, False))
        self.assertIn("neither a key nor an id", r["why_incomplete"])

    def test_a_count_that_contradicts_what_was_read_is_not_complete(self):
        r = search.search(CONN, "project = ABC",
                          transport=lambda *a, **k: {"issues": [issue(1)], "total": -1})
        self.assertFalse(r["complete"])
        r = search.search(CONN, "project = ABC", transport=lambda *a, **k: {
            "issues": [issue(1), issue(2), issue(3)], "total": 2})
        self.assertFalse(r["complete"])
        self.assertIn("cannot be relied on", r["why_incomplete"])
        self.assertEqual(search.slim({"key": "A-1", "fields": {"fixVersions": 5, "labels": "x"}})
                         ["fix_versions"], [])
        self.assertEqual(search.connect({"base_urls": ["https://j.example"], "pat": "t",
                                         "timeout_seconds": "-3"}, "")["timeout"], 30)

    def test_the_notes_about_a_search_say_what_happened(self):
        class Wobble(Jira):
            def __call__(self, method, url, token, timeout, body=None):
                self.calls.append((method, url, token, body))
                at = body["startAt"]
                return {"issues": [issue(n) for n in range(at, min(at + 100, 250))],
                        "total": [250, 251, 250][min(len(self.calls) - 1, 2)]}
        r = search.search(CONN, "project = ABC", transport=Wobble())
        self.assertFalse(r["complete"])
        self.assertIn("250 on the first page, 251 on a later one", r["why_incomplete"])
        r = search.search(CONN, "project = ABC",
                          transport=lambda *a, **k: {"issues": [issue(1), issue(2)]})
        self.assertEqual(r["page_size"], search.PAGE_SIZE,
                         "two issues and no total is not a page size of two")

    def test_a_backslash_outside_a_quoted_value_is_refused_in_an_extra_condition(self):
        with self.assertRaises(search.Refused):
            search.contained('a = \\" ) OR (project = OTHER) OR (b = "z\\""')
        self.assertEqual(search.contained('summary ~ "a \\" b"'), 'summary ~ "a \\" b"')

    def test_a_limit_below_one_is_refused(self):
        for bad in (0, -5):
            with self.assertRaises(search.Refused):
                search.search(CONN, "project = ABC", max_results=bad, transport=Jira([issue(1)]))

    def test_short_pages_are_followed_by_what_was_actually_read(self):
        class Short(Jira):
            def __call__(self, method, url, token, timeout, body=None):
                body = dict(body, maxResults=min(body["maxResults"], 30))
                return super().__call__(method, url, token, timeout, body)
        jira = Short([issue(n) for n in range(70)])
        r = search.search(CONN, "project = ABC", transport=jira)
        self.assertEqual((len(r["issues"]), r["complete"]), (70, True))
        self.assertEqual([c[3]["startAt"] for c in jira.calls], [0, 30, 60])

    def test_no_total_is_never_called_complete(self):
        jira = lambda *a, **k: {"issues": []}
        r = search.search(CONN, "project = ABC", transport=jira)
        self.assertFalse(r["complete"])
        self.assertIn("did not say how many", r["why_incomplete"])

    def test_an_empty_query_and_an_absurd_limit_are_held(self):
        with self.assertRaises(search.Refused):
            search.search(CONN, "   ", transport=Jira())
        with self.assertRaises(search.Refused):
            search.count(CONN, "", transport=Jira())
        r = search.search(CONN, "project = ABC", max_results=10**9, transport=Jira([issue(1)]))
        self.assertEqual(r["limit"], search.HARD_MAX)

    def test_count_reads_no_issue(self):
        jira = Jira([issue(n) for n in range(42)])
        self.assertEqual(search.count(CONN, "project = ABC", transport=jira), 42)
        self.assertEqual(jira.calls[0][3]["maxResults"], 0)
        with self.assertRaises(search.JiraError):
            search.count(CONN, "x", transport=lambda *a, **k: {"issues": []})

    def test_a_row_survives_fields_of_the_wrong_shape(self):
        row = search.slim({"key": "ABC-1", "fields": {"summary": None, "status": "Open",
                                                      "labels": [1, "api"], "fixVersions": [None, {"name": "6.02"}]}})
        self.assertEqual(row, {"key": "ABC-1", "summary": "", "type": "", "status": "",
                               "priority": "", "labels": ["api"], "fix_versions": ["6.02"],
                               "updated": ""})
        self.assertEqual(search.slim({})["key"], "")


class ReleasesAndTests(unittest.TestCase):
    def test_a_version_name_cannot_change_the_query(self):
        jql = search.fix_version_jql("abc", '6.02" OR project = SECRET OR fixVersion = "x', "Story")
        self.assertEqual(jql, 'project = ABC AND fixVersion = "6.02\\" OR project = SECRET OR '
                              'fixVersion = \\"x" AND issuetype = "Story"')
        self.assertEqual(search.jql_quote("a\\b"), '"a\\\\b"')

    def test_what_changed_between_two_releases(self):
        res = lambda keys, **kw: dict({"issues": [issue(k) for k in keys], "complete": True,
                                       "limited": False}, **kw)
        d = search.compare(res([1, 2, 3]), res([2, 3, 4]))
        keys = lambda rows: [r["key"] for r in rows]
        self.assertEqual((keys(d["added"]), keys(d["removed"]), keys(d["continuing"])),
                         (["ABC-1"], ["ABC-4"], ["ABC-2", "ABC-3"]))
        self.assertTrue(d["reliable"])

    def test_a_comparison_of_a_short_list_says_it_cannot_be_relied_on(self):
        res = lambda **kw: dict({"issues": [issue(1)], "complete": True, "limited": False}, **kw)
        self.assertFalse(search.compare(res(complete=False), res())["reliable"])
        self.assertFalse(search.compare(res(), res(limited=True))["reliable"])

    def test_an_extra_condition_cannot_leave_its_brackets(self):
        for bad in ("1=1) OR (project = OTHER", "labels = a) OR (1=1", "(labels = a",
                    "labels = a ORDER BY key", 'summary ~ "unclosed'):
            with self.assertRaises(search.Refused, msg=bad):
                search.tests_jql("ABC", "Test", bad)
        self.assertEqual(search.contained('summary ~ "a ) order by ( b" AND (labels = x)'),
                         'summary ~ "a ) order by ( b" AND (labels = x)',
                         "brackets and words inside a quoted value are text")

    def test_the_inventory_query(self):
        self.assertEqual(search.tests_jql("abc"), 'project = ABC AND issuetype = "Test"')
        self.assertEqual(search.tests_jql("abc", "Xray Test", "labels = api OR labels = b2b"),
                         'project = ABC AND issuetype = "Xray Test" AND (labels = api OR labels = b2b)')


class TheCommand(unittest.TestCase):
    def run_main(self, argv, transport, cfg=None):
        orig = search.connect
        search.connect = lambda: dict(CONN) if cfg is None else orig(cfg, "test")
        writes = []
        orig_write = search.write
        search.write = lambda name, payload: writes.append((name, payload)) or f"target/{name}.json"
        out = io.StringIO()
        try:
            with redirect_stdout(out):
                rc = search.main(argv, transport=transport)
        finally:
            search.connect, search.write = orig, orig_write
        return rc, out.getvalue(), writes

    def test_verify_exits_by_the_answer_and_never_prints_the_token(self):
        rc, out, _ = self.run_main(["verify"], lambda *a, **k: {"name": "jdoe"})
        self.assertEqual(rc, 0)
        self.assertNotIn("tok_1234567890", out)
        def no(*a, **k):
            raise search.JiraError(401, "Jira returned 401")
        self.assertEqual(self.run_main(["verify"], no)[0], 1)

    def test_no_configuration_is_exit_2_before_any_request(self):
        calls = []
        rc, out, _ = self.run_main(["verify"], lambda *a, **k: calls.append(a), cfg={})
        self.assertEqual((rc, calls), (2, []))
        self.assertIn("refused", out)

    def test_an_incomplete_search_is_a_failing_exit_code(self):
        jira = Jira(pages=[[issue(n) for n in range(100)], []], total=5000)
        rc, out, writes = self.run_main(["search", "--jql", "project = ABC"], jira)
        self.assertEqual(rc, 1)
        self.assertIn("INCOMPLETE", out)
        self.assertEqual(len(writes[0][1]["issues"]), 100, "what was read is still written")

    def test_fix_version_compare_reads_both_and_writes_the_difference(self):
        class Two(Jira):
            def __call__(self, method, url, token, timeout, body=None):
                self.issues = [issue(1), issue(2)] if '"6.02"' in body["jql"] else [issue(2), issue(3)]
                return super().__call__(method, url, token, timeout, body)
        rc, out, writes = self.run_main(
            ["fix-version", "--project", "abc", "--version", "6.02", "--compare", "6.01"], Two())
        self.assertEqual(rc, 0)
        delta = writes[0][1]["delta"]
        self.assertEqual([r["key"] for r in delta["added"]], ["ABC-1"])
        self.assertEqual([r["key"] for r in delta["removed"]], ["ABC-3"])
        self.assertIn("only in 6.02: 1", out)

    def test_a_list_cut_at_the_limit_is_a_failing_exit_code(self):
        jira = Jira([issue(n) for n in range(30)])
        rc, out, writes = self.run_main(["tests", "--project", "ABC", "--max", "10"], jira)
        self.assertEqual(rc, 1)
        self.assertIn("LIMIT", out)
        self.assertEqual(len(writes[0][1]["issues"]), 10)

    def test_a_comparison_that_cannot_be_relied_on_is_a_failing_exit_code(self):
        jira = Jira([issue(n) for n in range(30)])
        rc, out, _ = self.run_main(["fix-version", "--project", "ABC", "--version", "2",
                                    "--compare", "1", "--max", "10"], jira)
        self.assertEqual(rc, 1)
        self.assertIn("NOT RELIABLE", out)

    def test_an_unexpected_error_names_its_type_and_nothing_else(self):
        def boom(*a, **k):
            raise ValueError("Invalid header value b'Bearer tok_1234567890'")
        rc, out, _ = self.run_main(["verify"], boom)
        self.assertEqual(rc, 1)
        self.assertIn("unexpected ValueError", out)
        self.assertNotIn("tok_1234567890", out)

    def test_an_http_base_url_is_warned_about_on_every_run(self):
        orig = search.connect
        search.connect = lambda: dict(CONN, cleartext=True)
        out = io.StringIO()
        try:
            with redirect_stdout(out):
                search.main(["verify"], transport=lambda *a, **k: {"name": "x"})
        finally:
            search.connect = orig
        self.assertIn("sent unencrypted", out.getvalue())

    def test_a_paste_of_keys_or_a_query_is_searched_on_the_configured_host(self):
        jira = Jira([issue(1), issue(2)])
        rc, out, writes = self.run_main(["paste", "abc-1, https://jira.example.com/browse/ABC-2"], jira)
        self.assertEqual(rc, 0)
        self.assertEqual(jira.calls[0][3]["jql"], "issuekey in (ABC-1, ABC-2) ORDER BY key ASC")
        self.assertTrue(jira.calls[0][1].startswith("https://jira.example.com/"))
        jira = Jira([issue(1)])
        self.run_main(["paste", "https://jira.example.com/issues/?jql=project%20%3D%20ABC"], jira)
        self.assertEqual(jira.calls[0][3]["jql"], "project = ABC ORDER BY key ASC")

    def test_a_paste_from_a_second_approved_jira_is_not_answered_from_the_first(self):
        orig = search.connect
        search.connect = lambda: dict(CONN, bases=[CONN["base"], "https://jira2.example.com"])
        jira = Jira([issue(1)])
        out = io.StringIO()
        try:
            with redirect_stdout(out):
                rc = search.main(["paste", "https://jira2.example.com/browse/ABC-1"], transport=jira)
        finally:
            search.connect = orig
        self.assertEqual((rc, jira.calls), (2, []))
        self.assertIn("searches only go to https://jira.example.com", out.getvalue())

    def test_the_same_host_written_another_way_is_the_same_host(self):
        for text in ("https://jira.example.com:443/browse/ABC-1",
                     "https://JIRA.example.com./browse/ABC-1"):
            jira = Jira([issue(1)])
            rc, out, _ = self.run_main(["paste", text], jira)
            self.assertEqual((rc, len(jira.calls)), (0, 1), text)

    def test_a_refused_paste_does_not_print_a_user_name_or_password_it_carried(self):
        jira = Jira([issue(1)])
        rc, out, _ = self.run_main(["paste", "https://user:s3cret@evil.example/browse/ABC-1"], jira)
        self.assertEqual((rc, jira.calls), (2, []))
        self.assertNotIn("s3cret", out)
        self.assertIn("https://evil.example", out)
        rc, out, _ = self.run_main(["paste", "https://user:s3cret@jira.example.com/browse/ABC-1"], jira)
        self.assertEqual((rc, jira.calls), (2, []))
        self.assertNotIn("s3cret", out)

    def test_an_ordering_alone_is_not_searched(self):
        jira = Jira([issue(1)])
        rc, out, _ = self.run_main(["paste", "order by created"], jira)
        self.assertEqual((rc, jira.calls), (2, []))

    def test_a_summary_the_console_cannot_print_does_not_cost_the_result(self):
        class Narrow(io.TextIOWrapper):
            pass
        raw = io.BytesIO()
        narrow = Narrow(raw, encoding="cp1252", errors="strict")
        jira = Jira([issue(1, summary="before \u2192 after \u4e2d")])
        orig, orig_write, orig_out = search.connect, search.write, sys.stdout
        search.connect = lambda: dict(CONN)
        writes = []
        search.write = lambda name, payload: writes.append(payload) or "target/x.json"
        sys.stdout = narrow
        try:
            rc = search.main(["search", "--jql", "project = ABC"], transport=jira)
            narrow.flush()
        finally:
            sys.stdout = orig_out
            search.connect, search.write = orig, orig_write
        self.assertEqual(rc, 0)
        self.assertEqual(writes[0]["issues"][0]["summary"], "before \u2192 after \u4e2d")
        self.assertIn(b"ABC-1", raw.getvalue())

    def test_a_paste_from_another_host_or_of_nothing_is_refused_before_any_request(self):
        for text in ("https://evil.example/browse/ABC-1",
                     "https://evil.example/x project = ABC",
                     "https://jira.example.com/browse/ABC-1 https://evil.example/secure/Dashboard.jspa",
                     "https://evil.example/issues/?jql=project%3DABC",
                     "http://jira.example.com/browse/ABC-1",
                     "the status in the response is wrong"):
            jira = Jira([issue(1)])
            rc, out, _ = self.run_main(["paste", text], jira)
            self.assertEqual((rc, jira.calls), (2, []), text)
            self.assertIn("refused", out)

    def test_a_jira_error_is_one_line_not_a_traceback(self):
        rc, out, _ = self.run_main(["tests", "--project", "ABC"],
                                   Jira(fail=search.JiraError(410, "Jira returned 410 Gone")))
        self.assertEqual(rc, 1)
        self.assertIn("FAIL Jira returned 410 Gone", out)

    def test_result_files_stay_in_the_output_directory(self):
        self.assertEqual(search._safe("../../etc/passwd"), "etc_passwd")
        self.assertEqual(search._safe("ABC-fixversion-6.02 (hot/fix)"), "ABC-fixversion-6.02_hot_fix")
        self.assertEqual(search._safe("///"), "result")


class TheRealTransport(unittest.TestCase):
    """urllib against two servers on loopback: the one configured, and one
    a redirect points at. Nothing leaves the machine."""

    @classmethod
    def setUpClass(cls):
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer
        cls.seen = []

        def handler(name, routes):
            class H(BaseHTTPRequestHandler):
                def log_message(self, *a):
                    pass

                def _serve(self):
                    cls.seen.append((name, self.command, self.path,
                                     self.headers.get("Authorization")))
                    n = int(self.headers.get("Content-Length") or 0)
                    if n:
                        self.rfile.read(n)
                    status, headers, body = routes(self.path)
                    self.send_response(status)
                    for k, v in headers.items():
                        self.send_header(k, v)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                do_GET = do_POST = _serve
            return H

        cls.other = HTTPServer(("127.0.0.1", 0), handler("other", lambda p: (200, {}, b'{"name": "stolen"}')))
        away = f"http://127.0.0.1:{cls.other.server_port}"

        def routes(path):
            if path.endswith("/myself"):
                return 200, {"Content-Type": "application/json"}, b'{"name": "jdoe"}'
            if path.endswith("/away"):
                return 302, {"Location": away + "/rest/api/2/myself"}, b""
            if path.endswith("/moved"):
                return 302, {"Location": "/rest/api/2/myself"}, b""
            if path.endswith("/login"):
                return 200, {"Content-Type": "text/html"}, b"<html>Sign in</html>"
            if path.endswith("/search"):
                return 302, {"Location": "/rest/api/2/myself"}, b""
            return 400, {}, json.dumps({"errorMessages": ["bad thing"]}).encode()

        cls.jira = HTTPServer(("127.0.0.1", 0), handler("jira", routes))
        cls.base = f"http://127.0.0.1:{cls.jira.server_port}"
        for srv in (cls.jira, cls.other):
            threading.Thread(target=srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        for srv in (cls.jira, cls.other):
            srv.shutdown()
            srv.server_close()

    def setUp(self):
        type(self).seen.clear()

    def call(self, method, path, body=None):
        return search._urllib_transport(method, self.base + path, "tok_1234567890", 5, body)

    def test_a_good_answer_and_the_header(self):
        self.assertEqual(self.call("GET", "/rest/api/2/myself"), {"name": "jdoe"})
        self.assertEqual(self.seen[0][3], "Bearer tok_1234567890")

    def test_a_redirect_to_another_host_is_refused_and_the_token_stays(self):
        with self.assertRaises(search.JiraError) as got:
            self.call("GET", "/away")
        self.assertIn("redirected to http://127.0.0.1:", str(got.exception))
        self.assertIn("base_urls", str(got.exception))
        self.assertEqual([s[0] for s in self.seen], ["jira"],
                         "the other server was never contacted")

    def test_a_redirect_on_the_same_host_is_followed_for_a_get(self):
        self.assertEqual(self.call("GET", "/moved"), {"name": "jdoe"})

    def test_a_redirected_post_is_refused_not_resent_without_its_query(self):
        with self.assertRaises(search.JiraError) as got:
            self.call("POST", "/rest/api/2/search", {"jql": "project = ABC"})
        self.assertIn("POST", str(got.exception))
        self.assertEqual(len(self.seen), 1)

    def test_a_sign_in_page_is_a_refused_token_not_an_empty_result(self):
        with self.assertRaises(search.JiraError) as got:
            self.call("GET", "/login")
        self.assertIn("not JSON", str(got.exception))

    def test_jiras_error_body_is_read(self):
        with self.assertRaises(search.JiraError) as got:
            self.call("GET", "/nope")
        self.assertEqual(got.exception.status, 400)
        self.assertIn("bad thing", str(got.exception))

    def test_a_host_that_is_not_there_is_one_line(self):
        with self.assertRaises(search.JiraError) as got:
            search._urllib_transport("GET", "http://127.0.0.1:1/x", "tok_1234567890", 2)
        self.assertEqual(got.exception.status, 0)
        self.assertNotIn("tok_", str(got.exception))


if __name__ == "__main__":
    unittest.main(verbosity=1)
