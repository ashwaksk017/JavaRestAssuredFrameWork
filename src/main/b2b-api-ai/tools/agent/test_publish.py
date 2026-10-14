"""The two commands that write to Jira and Confluence write only what was asked, once.

    python tools/agent/test_publish.py

No test here reaches Jira or Confluence: both are functions that record
what they were sent.
"""
from __future__ import annotations

import os as _os
_os.environ["WORKBENCH_MOCK"] = "0"

import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


pb = _load("publish_under_test", os.path.join(HERE, "publish.py"))
search, loop = pb.search, pb.loop
CONN = {"base": "https://jira.example.com", "bases": ["https://jira.example.com"],
        "token": "tok_publish_12345", "timeout": 5, "api": "/rest/api/2", "cleartext": False}
JCFG = {"publish": {"attach": True}}
CCFG = {"base_urls": ["https://wiki.example.com/confluence"], "pat": "tok_conf_publish_1",
        "publish": {"create_page": True, "spaces": ["QA"]}}
MANUAL = {"kind": "manual-tests", "at": "2026-10-10T10:00:00Z", "mock": False,
          "inventory": {"mock_jira": False},
          "new_cases": [{"id": "MT-001", "title": "Group <b>booking</b> & more",
                         "module": "Booking", "priority": "P1", "preconditions": "signed in",
                         "steps": [{"action": "Book 10 rooms", "data": "10",
                                    "expected": "Group flow <script>x</script>"}],
                         "source": "rules.md"}],
          "updates": [{"key": "ABC-2", "existing_title": "Cancel", "reason": "fee changed",
                       "changes": "expect a fee"}]}


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="b2b-publish-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.job = os.path.join(self.root, ".workbench", "j1")
        os.makedirs(self.job)
        saved = (loop.intake.ROOT, pb.ROOT)
        loop.intake.ROOT = pb.ROOT = self.root
        self.addCleanup(lambda: (setattr(loop.intake, "ROOT", saved[0]),
                                 setattr(pb, "ROOT", saved[1])))
        os.environ["WORKBENCH_MOCK"] = "0"
        self.put("manual-result.json", json.dumps(MANUAL))
        self.put("manual-tests.md", "# Manual test cases\n\nMT-001\n")

    def put(self, name, text):
        with io.open(os.path.join(self.job, name), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)

    def log(self):
        try:
            with io.open(os.path.join(self.job, pb.LOG), encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            return ""

    def config(self, **jira):
        os.makedirs(os.path.join(self.root, "src", "main", "resources"), exist_ok=True)
        with io.open(os.path.join(self.root, "src", "main", "resources",
                                  "program_configuration.json"), "w", encoding="utf-8") as fh:
            json.dump({"jira_config": jira}, fh)


class Jira:
    """An issue that exists, and a record of every request."""

    def __init__(self, attached=(), answer=(200, [{"filename": "x"}]), key="ABC-12"):
        self.asked, self.sent = [], []
        self.attached, self.answer, self.key = list(attached), answer, key

    def transport(self, method, url, token, timeout, body=None):
        self.asked.append((method, url))
        return {"key": self.key, "fields": {"summary": "A  story\nabout booking",
                                           "attachment": [{"filename": n} for n in self.attached]}}

    def uploader(self, url, token, timeout, filename, data, ctype):
        self.sent.append({"url": url, "token": token, "filename": filename, "data": data,
                          "ctype": ctype})
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


class AttachToAnIssue(Base):
    def go(self, jira=None, key="ABC-12", name="manual-tests.md", confirm=None, cfg=JCFG,
           conn=None):
        jira = jira or Jira()
        return pb.attach("j1", key, name, key if confirm is None else confirm, self.root,
                         conn or dict(CONN), cfg, jira.transport, jira.uploader), jira

    def test_one_file_goes_to_one_issue_and_is_recorded_before_and_after(self):
        r, jira = self.go()
        self.assertEqual((r["ok"], r["key"], r["sent_as"], r["mock"]),
                         (True, "ABC-12", "j1-manual-tests.md", False))
        self.assertEqual(r["summary"], "A story about booking")
        sent = jira.sent[0]
        self.assertEqual(sent["url"], "https://jira.example.com/rest/api/2/issue/ABC-12/attachments")
        self.assertEqual((sent["filename"], sent["ctype"], sent["token"]),
                         ("j1-manual-tests.md", "text/markdown", "tok_publish_12345"))
        self.assertEqual(sent["data"], b"# Manual test cases\n\nMT-001\n")
        self.assertEqual([m for m, _u in jira.asked], ["GET"], "one read, then the one write")
        lines = self.log().splitlines()
        self.assertIn("ATTEMPT attach j1-manual-tests.md", lines[0])
        self.assertIn("DONE attach", lines[1])
        self.assertNotIn("tok_publish", self.log())

    def test_it_is_off_unless_switched_on_with_a_plain_true(self):
        for cfg in ({}, {"publish": {}}, {"publish": {"attach": "true"}},
                    {"publish": {"attach": 1}}, {"publish": "yes"}, {"publish": {"attach": False}}):
            jira = Jira()
            with self.assertRaises(pb.Refused, msg=cfg):
                self.go(jira, cfg=cfg)
            self.assertEqual((jira.asked, jira.sent), ([], []), "not one request")
        self.assertEqual(self.log(), "")

    def test_the_key_twice_a_file_by_name_and_a_project_that_is_listed(self):
        for kw in ({"confirm": "ABC-13"}, {"confirm": ""}, {"key": "abc"}, {"key": "ABC-12; x"},
                   {"name": "review.json"}, {"name": "../../pom.xml"}, {"name": "cursor.log"},
                   {"name": "manual-inventory.json"},
                   {"name": os.path.join(self.root, "x.md")}, {"name": "design.md"},
                   {"cfg": {"publish": {"attach": True, "projects": ["XYZ"]}}}):
            jira = Jira()
            with self.assertRaises(pb.Refused, msg=kw):
                self.go(jira, **kw)
            self.assertEqual(jira.sent, [], kw)
        r, _j = self.go(key="abc-12", confirm=" ABC-12 ",
                        cfg={"publish": {"attach": True, "projects": ["abc"]}})
        self.assertTrue(r["ok"])

    def test_it_adds_and_never_replaces(self):
        jira = Jira(attached=["other.md", "j1-manual-tests.md"])
        with self.assertRaises(pb.Refused) as got:
            self.go(jira)
        self.assertIn("already has an attachment", str(got.exception))
        self.assertEqual(jira.sent, [])
        with self.assertRaises(pb.Refused):
            self.go(Jira(key="ABC-99"))                    # Jira answered about another issue

    def test_what_a_stand_in_made_does_not_go_to_a_real_issue(self):
        for change in ({"mock": True}, {"inventory": {"mock_jira": True}}):
            self.put("manual-result.json", json.dumps(dict(MANUAL, **change)))
            jira = Jira()
            with self.assertRaises(pb.Refused) as got:
                self.go(jira)
            self.assertIn("stand-in", str(got.exception))
            self.assertEqual((jira.asked, jira.sent), ([], []))
        os.remove(os.path.join(self.job, "manual-result.json"))
        with self.assertRaises(pb.Refused):
            self.go()                                      # nobody can say how it was made

    def test_a_file_with_a_private_value_in_it_is_not_sent(self):
        self.config(pat="tok_SECRET_value_1234567", base_urls=["https://jira.corp.example.net"])
        for text in ("see tok_SECRET_value_1234567", "at JIRA.corp.example.net/x"):
            self.put("manual-tests.md", text)
            jira = Jira()
            with self.assertRaises(pb.Refused) as got:
                self.go(jira)
            self.assertIn("private values", str(got.exception))
            self.assertNotIn("SECRET", str(got.exception))
            self.assertEqual((jira.asked, jira.sent), ([], []))
        with io.open(os.path.join(self.root, "src", "main", "resources",
                                  "program_configuration.json"), "w") as fh:
            fh.write("{ broken")
        self.put("manual-tests.md", "fine")
        with self.assertRaises(pb.Refused):
            self.go()                                      # cannot be checked, so not sent

    def test_what_jira_said_is_what_is_reported(self):
        r, _j = self.go(Jira(answer=(403, {"errorMessages": ["no"]})))
        self.assertEqual(r["ok"], False)
        self.assertIn("nothing was attached", r["message"])
        self.assertIn("REFUSED BY JIRA", self.log())
        r, _j = self.go(Jira(answer=(502, "bad gateway")))
        self.assertIsNone(r["ok"])
        self.assertIn("MAY have been attached", r["message"])
        r, _j = self.go(Jira(answer=TimeoutError("tok_publish_12345")))
        self.assertIsNone(r["ok"])
        self.assertIn("MAY have been attached", r["message"])
        self.assertIn("NO ANSWER", self.log())
        self.assertNotIn("tok_publish", self.log() + json.dumps(r))
        r, _j = self.go(Jira(answer=(200, "<html>sign in</html>")))
        self.assertIsNone(r["ok"], "a 200 that is not Jira's answer is not a success")

    def test_empty_missing_and_too_large(self):
        self.put("manual-tests.md", "  \n")
        with self.assertRaises(pb.Refused):
            self.go()
        os.remove(os.path.join(self.job, "manual-tests.md"))
        with self.assertRaises(pb.Refused):
            self.go()
        self.put("manual-tests.md", "x" * (pb.MAX_ATTACH_BYTES + 1))
        with self.assertRaises(pb.Refused):
            self.go()

    def test_the_request_that_is_built(self):
        seen = []
        real = pb._send
        pb._send = lambda req, timeout: (seen.append(req), (200, '[{"filename": "a.md"}]'))[1]
        self.addCleanup(setattr, pb, "_send", real)
        status, answer = pb.upload("https://jira.example.com/rest/api/2/issue/ABC-1/attachments",
                                   "tok_publish_12345", 5, "j1-a.md", b"hello", "text/markdown")
        req = seen[0]
        self.assertEqual((status, answer), (200, [{"filename": "a.md"}]))
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.get_header("Authorization"), "Bearer tok_publish_12345")
        self.assertEqual(req.get_header("X-atlassian-token"), "no-check")
        self.assertNotIn("tok_publish", req.full_url)
        self.assertIn(b'filename="j1-a.md"', req.data)
        self.assertIn(b"\r\n\r\nhello\r\n--", req.data)

    def test_a_write_is_not_followed_through_a_redirect(self):
        import urllib.error
        import urllib.request
        guard = pb._NoRedirect()
        with self.assertRaises(pb.RedirectedWrite):
            guard.redirect_request(urllib.request.Request("https://jira.example.com/x"),
                                   None, 302, "Found", {}, "https://jira.example.com/y")
        self.assertTrue([h for h in pb._opener().handlers if isinstance(h, pb._NoRedirect)],
                        "and it is the one the sender uses")


class Confluence:
    def __init__(self, parent_space="QA", existing=(), answer=None):
        self.calls, self.parent_space, self.existing = [], parent_space, list(existing)
        self.answer = answer or (200, {"id": "777", "_links": {"webui": "/display/QA/T"}})

    def send(self, method, url, token, timeout, body=None):
        self.calls.append({"method": method, "url": url, "token": token, "body": body})
        if method == "POST":
            if isinstance(self.answer, Exception):
                raise self.answer
            return self.answer
        if "?spaceKey=" in url:
            return 200, {"results": self.existing}
        return 200, {"id": url.rsplit("/", 1)[1].split("?")[0], "space": {"key": self.parent_space}}


class CreateAPage(Base):
    def go(self, conf=None, what="manual", space="QA", parent="4242",
           title="Group booking: manual tests", confirm=None, cfg=CCFG):
        conf = conf or Confluence()
        return pb.create_page("j1", what, space, parent, title,
                              title if confirm is None else confirm, self.root, cfg,
                              conf.send, False), conf

    def test_one_new_page_under_the_parent_named_and_recorded(self):
        r, conf = self.go()
        self.assertEqual((r["ok"], r["id"], r["space"], r["parent"]), (True, "777", "QA", "4242"))
        self.assertEqual(r["link"], "https://wiki.example.com/confluence/display/QA/T")
        self.assertEqual([c["method"] for c in conf.calls], ["GET", "GET", "POST"])
        self.assertTrue(all(c["url"].startswith("https://wiki.example.com/confluence/rest/api/content")
                            for c in conf.calls))
        self.assertTrue(all(c["token"] == "tok_conf_publish_1" for c in conf.calls))
        body = conf.calls[2]["body"]
        self.assertEqual((body["type"], body["title"], body["space"], body["ancestors"]),
                         ("page", "Group booking: manual tests", {"key": "QA"}, [{"id": "4242"}]))
        self.assertEqual(body["body"]["storage"]["representation"], "storage")
        self.assertIn("ATTEMPT create page", self.log().splitlines()[0])
        self.assertIn("DONE create page", self.log().splitlines()[1])
        self.assertNotIn("tok_conf", self.log())

    def test_everything_on_the_page_is_escaped(self):
        r, conf = self.go()
        value = conf.calls[2]["body"]["body"]["storage"]["value"]
        self.assertIn("Group &lt;b&gt;booking&lt;/b&gt; &amp; more", value)
        self.assertIn("Group flow &lt;script&gt;x&lt;/script&gt;", value)
        self.assertNotIn("<script>", value)
        self.assertNotIn("<b>", value)
        import xml.dom.minidom
        xml.dom.minidom.parseString(f"<root>{value}</root>")       # well-formed
        self.assertIn("<h2>New test cases (1)</h2>", value)
        self.assertIn("<h2>Existing tests to update (1)</h2>", value)

    def test_it_is_off_unless_switched_on_and_only_into_a_listed_space(self):
        for cfg in (dict(CCFG, publish={}), dict(CCFG, publish={"create_page": "true", "spaces": ["QA"]}),
                    dict(CCFG, publish={"create_page": True}),
                    dict(CCFG, publish={"create_page": True, "spaces": []}),
                    dict(CCFG, publish={"create_page": True, "spaces": "QA"}),
                    dict(CCFG, publish={"create_page": True, "spaces": ["DEV"]}),
                    {k: v for k, v in CCFG.items() if k != "publish"}):
            conf = Confluence()
            with self.assertRaises(pb.Refused, msg=cfg):
                self.go(conf, cfg=cfg)
            self.assertEqual(conf.calls, [], "not one request")
        self.assertEqual(self.log(), "")

    def test_the_title_twice_a_numeric_parent_a_known_content(self):
        for kw in ({"confirm": "another title"}, {"confirm": ""}, {"title": ""},
                   {"title": "x" * 201}, {"title": "a\x00b"}, {"parent": "abc"},
                   {"parent": "42/../1"}, {"parent": ""}, {"space": "Q A"}, {"space": "../x"},
                   {"what": "everything"}, {"what": "design"}):
            conf = Confluence()
            with self.assertRaises(pb.Refused, msg=kw):
                self.go(conf, **kw)
            self.assertEqual([c for c in conf.calls if c["method"] == "POST"], [], kw)
        r, _c = self.go(title="  Spaced   title ", confirm="Spaced title")
        self.assertEqual(r["title"], "Spaced title")

    def test_the_parent_must_be_in_the_space_and_it_never_edits(self):
        conf = Confluence(parent_space="DEV")
        with self.assertRaises(pb.Refused) as got:
            self.go(conf)
        self.assertIn("is in space DEV, not QA", str(got.exception))
        conf = Confluence(existing=[{"id": "5", "title": "Group booking: manual tests"}])
        with self.assertRaises(pb.Refused) as got:
            self.go(conf)
        self.assertIn("never edits", str(got.exception))
        self.assertEqual([c["method"] for c in conf.calls], ["GET", "GET"])

        class Down(Confluence):
            def send(self, method, url, token, timeout, body=None):
                self.calls.append({"method": method})
                return 500, "oops"
        conf = Down()
        with self.assertRaises(pb.Refused):
            self.go(conf)
        self.assertEqual([c["method"] for c in conf.calls], ["GET"],
                         "what cannot be checked is not written")

    def test_what_a_stand_in_made_or_holds_a_private_value_is_not_published(self):
        self.put("manual-result.json", json.dumps(dict(MANUAL, mock=True)))
        conf = Confluence()
        with self.assertRaises(pb.Refused):
            self.go(conf)
        self.assertEqual(conf.calls, [])
        self.config(pat="tok_SECRET_value_1234567")
        leaky = dict(MANUAL, new_cases=[dict(MANUAL["new_cases"][0],
                                             title="uses tok_SECRET_value_1234567")])
        self.put("manual-result.json", json.dumps(leaky))
        conf = Confluence()
        with self.assertRaises(pb.Refused) as got:
            self.go(conf)
        self.assertIn("private values", str(got.exception))
        self.assertEqual(conf.calls, [])

    def test_what_confluence_said_is_what_is_reported(self):
        r, _c = self.go(Confluence(answer=(403, {"message": "no"})))
        self.assertEqual(r["ok"], False)
        self.assertIn("no page was created", r["message"])
        r, _c = self.go(Confluence(answer=(504, "timeout")), title="Second")
        self.assertIsNone(r["ok"])
        self.assertIn("MAY have been created", r["message"])
        r, _c = self.go(Confluence(answer=ConnectionResetError()), title="Third")
        self.assertIsNone(r["ok"])
        r, _c = self.go(Confluence(answer=(200, {"id": "9", "_links": {"webui": "https://evil.example/x"}})),
                        title="Fourth")
        self.assertEqual((r["ok"], r["link"]), (True, ""), "a link that is not a path is not shown")

    def test_the_other_pages(self):
        self.put("design.json", json.dumps({"mock": False, "test_cases": [
            {"id": "TC-1", "title": "lists <i>orders</i>", "endpoint": "GET /orders",
             "steps": [{"action": "call", "expected": "200"}]}]}))
        r, conf = self.go(what="design", title="API design")
        self.assertIn("lists &lt;i&gt;orders&lt;/i&gt;", conf.calls[2]["body"]["body"]["storage"]["value"])
        self.put("code-review.json", json.dumps({"kind": "code-review", "mock": False,
                                                 "message": "1 finding(s).",
                                                 "findings": [{"severity": "high", "file": "A.java",
                                                               "line": 3, "title": "t", "why": "w",
                                                               "fix": "f"}]}))
        r, conf = self.go(what="review", title="Review")
        self.assertIn("A.java:3", conf.calls[2]["body"]["body"]["storage"]["value"])


class FoundByTheReview(Base):
    """Each of these was wrong when the commands were first written."""

    def attach(self, jira=None, cfg=JCFG, **kw):
        jira = jira or Jira()
        return pb.attach("j1", kw.pop("key", "ABC-12"), kw.pop("name", "manual-tests.md"),
                         kw.pop("confirm", "ABC-12"), self.root, kw.pop("conn", dict(CONN)),
                         cfg, jira.transport, jira.uploader, **kw), jira

    def page(self, conf=None, cfg=CCFG, **kw):
        conf = conf or Confluence()
        title = kw.pop("title", "T")
        return pb.create_page("j1", kw.pop("what", "manual"), kw.pop("space", "QA"),
                              kw.pop("parent", "4242"), title, kw.pop("confirm", title),
                              self.root, cfg, conf.send, kw.pop("mocked", False), **kw), conf

    def test_what_the_person_was_told_is_checked_against_what_is_used(self):
        """The page said MOCK; the switch was then turned to real."""
        jira = Jira()
        with self.assertRaises(pb.Refused) as got:
            self.attach(jira, expect="mock")
        self.assertIn("it is now the REAL one", str(got.exception))
        self.assertEqual((jira.asked, jira.sent), ([], []))
        r, _j = self.attach(expect="real")
        self.assertTrue(r["ok"])
        conf = Confluence()
        with self.assertRaises(pb.Refused):
            self.page(conf, expect="mock")
        self.assertEqual(conf.calls, [])
        with self.assertRaises(pb.Refused):
            self.page(expect="perhaps")
        os.environ["WORKBENCH_MOCK"] = "1"
        self.addCleanup(os.environ.__setitem__, "WORKBENCH_MOCK", "0")
        with self.assertRaises(pb.Refused) as got:
            pb.attach("j1", "ABC-12", "manual-tests.md", "ABC-12", self.root, expect="real")
        self.assertIn("it is now a stand-in", str(got.exception))

    def test_a_private_value_is_found_however_the_report_wrote_it(self):
        self.config(pat="tok_SECRET_value_1234567", password='p&ss"w0rd<1>',
                    base_urls=["https://www.corp-jira.example.net"])
        for text in (pb.mdsafe.md("uses tok_SECRET_value_1234567 here"),
                     pb.mdsafe.md("see www.corp-jira.example.net/x"),
                     'a;"says p&ss""w0rd<1>";b', "p&amp;ss&quot;w0rd&lt;1&gt;"):
            self.put("manual-tests.md", text)
            self.assertNotIn("tok_SECRET_value_1234567", text.replace("corp", ""))
            jira = Jira()
            with self.assertRaises(pb.Refused, msg=text) as got:
                self.attach(jira)
            self.assertIn("private values", str(got.exception))
            self.assertEqual(jira.sent, [])
        self.put("manual-tests.md", "clean")
        leaky = dict(MANUAL, new_cases=[dict(MANUAL["new_cases"][0], title='p&ss"w0rd<1>')])
        self.put("manual-result.json", json.dumps(leaky))
        conf = Confluence()
        with self.assertRaises(pb.Refused):
            self.page(conf)
        self.assertEqual(conf.calls, [], "the words are searched before they are escaped")

    def test_a_restriction_that_is_mistyped_is_not_no_restriction(self):
        for projects in ("ABC", [123], [""], {"ABC": 1}, ["ABC", 5], [], ["a b"]):
            jira = Jira(key="XYZ-1")
            with self.assertRaises(pb.Refused, msg=projects):
                self.attach(jira, cfg={"publish": {"attach": True, "projects": projects}},
                            key="XYZ-1", confirm="XYZ-1")
            self.assertEqual((jira.asked, jira.sent), ([], []), projects)
        for spaces in ("QA", [5], ["Q A"]):
            with self.assertRaises(pb.Refused, msg=spaces):
                self.page(cfg=dict(CCFG, publish={"create_page": True, "spaces": spaces}))
        r, _j = self.attach(cfg={"publish": {"attach": True}})          # left out: any
        self.assertTrue(r["ok"])

    def test_the_error_a_failed_step_leaves_is_not_a_result(self):
        for name, what, bad in (
                ("manual-result.json", "manual", {"kind": "error", "refused": True, "message": "no"}),
                ("manual-result.json", "manual", dict(MANUAL, new_cases=[], updates=[])),
                ("manual-result.json", "manual", dict(MANUAL, new_cases=7)),
                ("code-review.json", "review", {"kind": "error", "message": "unexpected KeyError"}),
                ("code-review.json", "review", {"mock": False, "findings": []}),
                ("design.json", "design", {"mock": False, "test_cases": []})):
            self.put(name, json.dumps(bad))
            conf = Confluence()
            with self.assertRaises(pb.Refused, msg=bad):
                self.page(conf, what=what)
            self.assertEqual(conf.calls, [])
            self.assertNotIn(what, pb.status("j1", self.root)["pages"])
        self.assertEqual(pb.status("j1", self.root)["files"], [],
                         "nor is the file that belongs to it offered")

    def test_only_a_result_that_says_it_is_real_goes_to_the_real_thing(self):
        for change in ({"mock": None}, {"mock": 0}, {"mock": "false"},
                       {"inventory": {}}, {"inventory": {"mock_jira": None}}):
            data = dict(MANUAL, **change)
            if "mock" in change and change["mock"] is None:
                data.pop("mock")
            self.put("manual-result.json", json.dumps(data))
            jira, conf = Jira(), Confluence()
            with self.assertRaises(pb.Refused, msg=change):
                self.attach(jira)
            with self.assertRaises(pb.Refused, msg=change):
                self.page(conf)
            self.assertEqual((jira.sent, conf.calls), ([], []))

    def test_a_write_is_not_sent_in_clear_or_to_an_address_with_a_user_in_it(self):
        for base in ("http://jira.example.com", "https://user:pw@jira.example.com"):
            jira = Jira()
            with self.assertRaises(pb.Refused, msg=base):
                self.attach(jira, conn=dict(CONN, base=base))
            self.assertEqual((jira.asked, jira.sent), ([], []))
        conf = Confluence()
        with self.assertRaises(pb.Refused):
            self.page(conf, cfg=dict(CCFG, base_urls=["http://wiki.example.com/confluence"]))
        self.assertEqual(conf.calls, [])

    def test_from_just_before_the_write_the_record_says_possibly_done(self):
        """Whatever then happens to the command, the result file is true."""
        told = []
        jira = Jira(answer=RuntimeError("boom"))
        r, _j = self.attach(jira, on_attempt=told.append)
        self.assertEqual((told[0]["ok"], told[0]["kind"]), (None, "publish"))
        self.assertIn("outcome is not known", told[0]["message"])
        self.assertIsNone(r["ok"])
        # A summary this cannot write down, and a log that cannot be opened.
        bad = Jira()
        real = bad.transport
        bad.transport = lambda *a, **k: {"key": "ABC-12", "fields": {
            "summary": "broken " + chr(0xD83D), "attachment": []}}
        real_log = pb.LOG
        pb.LOG = os.path.join("no", "such", "dir", "publish.log")
        self.addCleanup(setattr, pb, "LOG", real_log)
        r, _j = self.attach(bad)
        self.assertTrue(r["ok"], "the outcome does not depend on the log")
        json.dumps(r, ensure_ascii=True)
        self.assertNotIn(chr(0xD83D), r["summary"])

    def test_the_command_keeps_possibly_done_when_it_dies_after_the_write(self):
        search_connect = search.connect
        search.connect = lambda *a, **k: dict(CONN)
        self.addCleanup(setattr, search, "connect", search_connect)
        real = search.projectconfig.section
        search.projectconfig.section = lambda name: (JCFG if name == "jira_config" else {}, "")
        self.addCleanup(setattr, search.projectconfig, "section", real)
        jira = Jira()

        def dies(*a, **k):
            raise KeyboardInterrupt()                       # as if the process were stopped
        with redirect_stdout(io.StringIO()):
            try:
                pb.main(["attach", "--job", "j1", "--key", "ABC-12", "--file", "manual-tests.md",
                         "--confirm", "ABC-12"], transport=jira.transport, uploader=dies)
            except KeyboardInterrupt:
                pass
        with io.open(os.path.join(self.job, pb.RESULT), encoding="utf-8") as fh:
            left = json.load(fh)
        self.assertEqual((left["kind"], left["ok"]), ("publish", None))
        self.assertIn("outcome is not known", left["message"])
        # ...and asking what is switched on afterwards does not remove it.
        with redirect_stdout(io.StringIO()):
            self.assertEqual(pb.main(["status", "--job", "j1"]), 0)
        with io.open(os.path.join(self.job, pb.RESULT), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["ok"], None)
        self.assertTrue(os.path.isfile(os.path.join(self.job, pb.STATUS)))

    def test_a_read_that_fails_is_a_refusal_and_odd_values_do_not_crash(self):
        class Down(Confluence):
            def send(self, method, url, token, timeout, body=None):
                raise TimeoutError("tok_conf_publish_1")
        with self.assertRaises(pb.Refused) as got:
            self.page(Down())
        self.assertIn("nothing was created", str(got.exception))
        self.assertNotIn("tok_conf", str(got.exception))
        for parent in (chr(0x661) + chr(0x662), chr(0xFF11) + chr(0xFF12), "1" * 20):
            with self.assertRaises(pb.Refused, msg=ascii(parent)):
                self.page(parent=parent)
        self.put("code-review.json", json.dumps({"kind": "code-review", "mock": False,
                                                 "message": "m", "findings": [
                                                     {"file": None, "line": 3, "title": "t" + chr(0xD83D)},
                                                     "junk"]}))
        r, conf = self.page(what="review")
        value = conf.calls[2]["body"]["body"]["storage"]["value"]
        self.assertIn("?:3", value)
        self.assertNotIn(chr(0xD83D), value)
        with self.assertRaises(ValueError):
            pb.upload("https://jira.example.com/x", "t", 5, 'a"' + chr(13) + chr(10) + "X-Evil: 1",
                      b"x", "text/plain")

    def test_the_real_sender_follows_no_redirect_and_reports_what_came_back(self):
        """Against a server on this machine: the code that is otherwise
        replaced in every test."""
        import http.server
        import threading
        seen = []

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):                           # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                seen.append((self.path, self.headers.get("Authorization"),
                             self.headers.get("X-Atlassian-Token"), self.rfile.read(n)))
                if self.path.startswith("/redirect"):
                    self.send_response(302)
                    self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/ok")
                    self.end_headers()
                    return
                code = 403 if self.path.startswith("/no") else 200
                body = b'{"errorMessages": ["no"]}' if code == 403 else b'[{"filename": "a.md"}]'
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass
        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        base = f"http://127.0.0.1:{srv.server_port}"
        self.assertEqual(pb.upload(base + "/ok", "tok_x", 5, "j1-a.md", b"hello", "text/markdown"),
                         (200, [{"filename": "a.md"}]))
        self.assertEqual(seen[0][1:3], ("Bearer tok_x", "no-check"))
        self.assertEqual(pb.upload(base + "/no", "tok_x", 5, "j1-a.md", b"x", "text/markdown")[0], 403)
        with self.assertRaises(pb.RedirectedWrite):
            pb.upload(base + "/redirect", "tok_x", 5, "j1-a.md", b"x", "text/markdown")
        self.assertEqual([s[0] for s in seen], ["/ok", "/no", "/redirect"],
                         "the redirect was not followed: nothing reached /ok a second time")
        status, got = pb.conf_request("POST", base + "/ok", "tok_y", 5, {"a": 1})
        self.assertEqual((status, got), (200, [{"filename": "a.md"}]))
        self.assertEqual(json.loads(seen[-1][3]), {"a": 1})


class FoundByTheSecondReview(Base):
    def test_a_redirect_is_an_answer_and_nothing_was_written(self):
        """It was reported as "did not answer, MAY have been attached"."""
        jira = Jira(answer=pb.RedirectedWrite())
        r = pb.attach("j1", "ABC-12", "manual-tests.md", "ABC-12", self.root, dict(CONN), JCFG,
                      jira.transport, jira.uploader)
        self.assertIs(r["ok"], False)
        self.assertIn("nothing was written", r["message"])
        self.assertNotIn("MAY", r["message"])
        self.assertIn("REDIRECTED attach", self.log())
        conf = Confluence(answer=pb.RedirectedWrite())
        r = pb.create_page("j1", "manual", "QA", "4242", "T", "T", self.root, CCFG, conf.send, False)
        self.assertIs(r["ok"], False)
        self.assertIn("nothing was written", r["message"])

    def test_a_private_value_is_found_as_the_other_writers_leave_it(self):
        self.config(pat="tok_SECRET_value_1234567", password="p@ss|w<rd`1Z", secret_phrase="two  spaces  here")
        for text in (pb.mdsafe.code("p@ss|w<rd`1Z"), pb.mdsafe.md("it says two  spaces  here")):
            self.put("manual-tests.md", text)
            jira = Jira()
            with self.assertRaises(pb.Refused, msg=ascii(text)):
                pb.attach("j1", "ABC-12", "manual-tests.md", "ABC-12", self.root, dict(CONN),
                          JCFG, jira.transport, jira.uploader)
            self.assertEqual(jira.sent, [])
        self.put("manual-tests.md", "clean")
        conf = Confluence()
        with self.assertRaises(pb.Refused):                # in the TITLE only
            pb.create_page("j1", "manual", "QA", "4242", "about tok_SECRET_value_1234567",
                           "about tok_SECRET_value_1234567", self.root, CCFG, conf.send, False)
        self.assertEqual(conf.calls, [])

    def test_a_stand_ins_design_or_review_does_not_go_to_the_real_thing_either(self):
        self.put("design.json", json.dumps({"mock": True, "test_cases": [{"id": "TC-1", "title": "t"}]}))
        self.put("design.md", "# design")
        self.put("code-review.json", json.dumps({"kind": "code-review", "mock": True, "findings": []}))
        self.put("code-review.md", "# review")
        for name in ("design.md", "code-review.md"):
            jira = Jira()
            with self.assertRaises(pb.Refused, msg=name):
                pb.attach("j1", "ABC-12", name, "ABC-12", self.root, dict(CONN), JCFG,
                          jira.transport, jira.uploader)
            self.assertEqual((jira.asked, jira.sent), ([], []))
        for what in ("design", "review"):
            conf = Confluence()
            with self.assertRaises(pb.Refused, msg=what):
                pb.create_page("j1", what, "QA", "4242", "T", "T", self.root, CCFG, conf.send, False)
            self.assertEqual(conf.calls, [])

    def test_the_stand_ins_settings_never_reach_a_real_connection(self):
        """mock.json says mock; the connection in hand is real."""
        os.environ["WORKBENCH_MOCK"] = "1"
        self.addCleanup(os.environ.__setitem__, "WORKBENCH_MOCK", "0")
        jira = Jira()
        with self.assertRaises(pb.Refused) as got:
            pb.attach("j1", "ABC-12", "manual-tests.md", "ABC-12", self.root, dict(CONN), {},
                      jira.transport, jira.uploader)
        self.assertIn("switched off", str(got.exception))
        conf = Confluence()
        with self.assertRaises(pb.Refused):
            pb.create_page("j1", "manual", "QA", "4242", "T", "T", self.root,
                           {k: v for k, v in CCFG.items() if k != "publish"}, conf.send, False)
        self.assertEqual((jira.sent, conf.calls), ([], []))

    def test_an_attachment_of_the_same_name_in_other_capitals_is_the_same_name(self):
        jira = Jira(attached=["J1-MANUAL-TESTS.MD"])
        with self.assertRaises(pb.Refused):
            pb.attach("j1", "ABC-12", "manual-tests.md", "ABC-12", self.root, dict(CONN), JCFG,
                      jira.transport, jira.uploader)
        self.assertEqual(jira.sent, [])

    def test_a_page_too_large_is_not_sent(self):
        big = dict(MANUAL, new_cases=[dict(MANUAL["new_cases"][0], title="x" * 500,
                                           preconditions="y" * (pb.MAX_PAGE_CHARS + 10))])
        self.put("manual-result.json", json.dumps(big))
        conf = Confluence()
        with self.assertRaises(pb.Refused) as got:
            pb.create_page("j1", "manual", "QA", "4242", "T", "T", self.root, CCFG, conf.send, False)
        self.assertIn("the limit is", str(got.exception))
        self.assertEqual(conf.calls, [])

    def test_the_command_keeps_not_known_when_something_ordinary_goes_wrong_after_the_attempt(self):
        connect = search.connect
        search.connect = lambda *a, **k: dict(CONN)
        self.addCleanup(setattr, search, "connect", connect)
        section = search.projectconfig.section
        search.projectconfig.section = lambda name: (JCFG if name == "jira_config" else {}, "")
        self.addCleanup(setattr, search.projectconfig, "section", section)
        jira = Jira()
        real_log = pb._log

        def log_then_fail(job_dir, line):
            real_log(job_dir, line)
            if line.startswith("DONE"):
                raise ValueError("after the write")
        pb._log = log_then_fail
        self.addCleanup(setattr, pb, "_log", real_log)
        with redirect_stdout(io.StringIO()):
            rc = pb.main(["attach", "--job", "j1", "--key", "ABC-12", "--file", "manual-tests.md",
                          "--confirm", "ABC-12"], transport=jira.transport, uploader=jira.uploader)
        self.assertEqual(rc, 1)
        with io.open(os.path.join(self.job, pb.RESULT), encoding="utf-8") as fh:
            left = json.load(fh)
        self.assertEqual((left["kind"], left["ok"]), ("publish", None),
                         "not overwritten by 'nothing was written'")


class MockAndStatus(Base):
    def test_in_mock_mode_both_change_a_file_on_this_machine_and_say_so(self):
        os.environ["WORKBENCH_MOCK"] = "1"
        self.addCleanup(os.environ.__setitem__, "WORKBENCH_MOCK", "0")
        self.put("manual-result.json", json.dumps(dict(MANUAL, mock=True)))
        st = pb.status("j1", self.root)
        self.assertEqual((st["attach"], st["create_page"], st["spaces"], st["mock_jira"],
                          st["mock_confluence"]), (True, True, ["QA"], True, True))
        self.assertEqual((st["files"], st["pages"]), (["manual-tests.md"], ["manual"]))
        r = pb.attach("j1", "ABC-12", "manual-tests.md", "ABC-12", self.root)
        self.assertEqual((r["ok"], r["mock"]), (True, True))
        self.assertIn("no Jira was contacted", r["message"])
        with self.assertRaises(pb.Refused):
            pb.attach("j1", "ABC-12", "manual-tests.md", "ABC-12", self.root)   # already there
        p = pb.create_page("j1", "manual", "QA", "4242", "Mock page", "Mock page", self.root)
        self.assertEqual((p["ok"], p["mock"]), (True, True))
        with self.assertRaises(pb.Refused):
            pb.create_page("j1", "manual", "QA", "4242", "Mock page", "Mock page", self.root)
        with self.assertRaises(pb.Refused):
            pb.create_page("j1", "manual", "DEV", "4242", "Other", "Other", self.root)
        self.assertEqual(sorted(os.listdir(os.path.join(self.root, ".workbench"))),
                         ["j1", "mock-confluence.json", "mock-jira.json"])
        self.assertIn("(MOCK)", self.log())

    def test_status_says_what_is_off_without_a_configuration(self):
        real = search.projectconfig.section
        search.projectconfig.section = lambda name: ({}, "no such block")
        self.addCleanup(setattr, search.projectconfig, "section", real)
        st = pb.status("j1", self.root)
        self.assertEqual((st["attach"], st["create_page"], st["mock_jira"]), (False, False, False))

    def test_the_command_writes_what_the_page_shows_and_needs_an_existing_job(self):
        os.environ["WORKBENCH_MOCK"] = "1"
        self.addCleanup(os.environ.__setitem__, "WORKBENCH_MOCK", "0")
        self.put("manual-result.json", json.dumps(dict(MANUAL, mock=True)))
        with redirect_stdout(io.StringIO()):
            self.assertEqual(pb.main(["attach", "--job", "j1", "--key", "ABC-12", "--file",
                                      "manual-tests.md", "--confirm", "ABC-12"]), 0)
        with io.open(os.path.join(self.job, pb.RESULT), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["kind"], "publish")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(pb.main(["attach", "--job", "j1", "--key", "ABC-12", "--file",
                                      "manual-tests.md", "--confirm", "ABC-13"]), 2)
            self.assertEqual(pb.main(["status", "--job", "nosuchjob"]), 2)
            self.assertEqual(pb.main(["status", "--job", "../x"]), 2)
        with io.open(os.path.join(self.job, pb.RESULT), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["kind"], "error")
        self.assertFalse(os.path.exists(os.path.join(self.root, ".workbench", "nosuchjob")))


class Declared(unittest.TestCase):
    def test_the_page_sends_names_and_no_path(self):
        jobs = _load("jobs_for_publish", os.path.join(HERE, "jobs.py"))
        for name in ("publish-status", "publish-attach", "publish-page"):
            self.assertFalse([k for k, v in jobs.RUNNABLES[name]["options"].items()
                              if v in ("path", "paths")], name)
        self.assertEqual(jobs.RESULT_FILES["publish-attach"], pb.RESULT)
        self.assertEqual(jobs.RESULT_FILES["publish-page"], pb.RESULT)
        self.assertFalse([k for k in jobs.RESULT_FILES if "publish-status".startswith(k)],
                         "asking what is switched on never removes the record of a write")
        for name in (pb.RESULT, pb.STATUS, pb.LOG):
            self.assertEqual(jobs.read_artifact("no-such-job-here", name), "")
        self.assertEqual(jobs.RUNNABLES["publish-attach"]["options"]["--expect"], "text")
        self.assertEqual(jobs.build_argv("publish-attach", {
            "--job": "j", "--key": "ABC-1", "--file": "design.md", "--confirm-key": "ABC-1"})[-8:],
            ["--job", "j", "--key", "ABC-1", "--file", "design.md", "--confirm-key", "ABC-1"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
