"""A specification kept as a file is one, is under its own name, and came from where it says.

    python tools/agent/test_specsource.py

No test here reaches Confluence or Cursor: the page's attachments are a
list, reading one is a function, and the job directory is a temporary one.
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
import urllib.error
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


src = _load("specsource_under_test", os.path.join(HERE, "specsource.py"))
design = src.design
att = _load("attachments_under_test",
            os.path.join(os.path.dirname(HERE), "confluence", "attachments.py"))

SPEC = json.dumps({"openapi": "3.0.1", "paths": {
    "/orders": {"get": {"summary": "List"}, "post": {"summary": "Create"}},
    "/orders/{id}": {"get": {"summary": "One"}}}}).encode()
OTHER = json.dumps({"openapi": "3.0.1", "paths": {"/health": {"get": {}}}}).encode()
CFG = {"base_urls": ["https://wiki.example.com/confluence"], "pat": "tok_conf_123456",
       "timeout_seconds": "5"}
LINK = "https://wiki.example.com/confluence/pages/viewpage.action?pageId=4242"


def row(title, data=b"", download=None):
    return {"id": "1", "title": title, "bytes": len(data), "media": "",
            "download": download or f"/download/attachments/4242/{title}?version=1", "_data": data}


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="b2b-spec-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.job = os.path.join(self.root, "target", "agent", "j1")
        os.makedirs(self.job)

    def fetch(self, rows, name=""):
        return src.from_confluence(self.job, LINK, name, conf=att, conn={"base": "x"},
                                   lister=lambda conn: rows,
                                   reader=lambda a: a["_data"], root=self.root)


class AFileChosen(Base):
    def test_it_is_kept_under_its_own_name_and_says_what_is_in_it(self):
        got = src.store(self.job, "C:\\Users\\me\\Orders API.json", SPEC, "chosen on the page",
                        self.root)
        self.assertEqual({k: got[k] for k in ("name", "path", "endpoints", "format", "origin")},
                         {"name": "Orders API.json", "endpoints": 3, "format": "JSON",
                          "path": "target/agent/j1/spec/Orders API.json",
                          "origin": "chosen on the page"})
        self.assertEqual(sorted(os.listdir(os.path.join(self.job, "spec"))),
                         ["Orders API.json", "_kept.json"])
        self.assertEqual(src.current(self.job, self.root), got)

    def test_what_is_not_a_specification_is_not_kept_and_the_old_one_stays(self):
        src.store(self.job, "good.json", SPEC, "chosen on the page", self.root)
        for name, data in (("notes.json", b'{"hello": 1}'), ("empty.json", b""),
                           ("bin.json", b"\xff\xfe\x00\x01"), ("list.json", b"[1, 2]"),
                           ("nopaths.json", b'{"openapi": "3.0.1", "paths": {}}'),
                           ("big.json", b" " * (src.MAX_SPEC_BYTES + 1))):
            with self.assertRaises(src.Refused, msg=name):
                src.store(self.job, name, data, "x", self.root)
        for bad in ("spec.txt", "..json", "a/../../x.json.exe", "nul.json", "", ".hidden.yaml",
                    "source.json.bak", "_kept.json", "_KEPT.json", "_x.json",
                    "x" * 120 + ".json"):
            with self.assertRaises(src.Refused, msg=bad):
                src.store(self.job, bad, SPEC, "x", self.root)
        self.assertEqual(src.current(self.job, self.root)["name"], "good.json")
        self.assertEqual(sorted(os.listdir(self.job)), ["spec"], "no half-written directory")

    def test_the_record_cannot_name_a_file_somewhere_else(self):
        src.store(self.job, "good.json", SPEC, "x", self.root)
        with io.open(os.path.join(self.root, "secret.json"), "wb") as fh:
            fh.write(SPEC)
        for name in ("../../../../secret.json", os.path.join(self.root, "secret.json"),
                     "_kept.json", "_KEPT.JSON", "", "missing.json"):
            with io.open(os.path.join(self.job, "spec", "_kept.json"), "w") as fh:
                json.dump({"name": name}, fh)
            self.assertIsNone(design.stored_spec(self.job), name)
        self.assertEqual(src.current(self.job, self.root), {})

    def test_a_new_one_replaces_the_old_and_remove_forgets_it(self):
        src.store(self.job, "a.json", SPEC, "x", self.root)
        got = src.store(self.job, "b.json", OTHER, "y", self.root)
        self.assertEqual((got["name"], got["endpoints"]), ("b.json", 1))
        self.assertEqual(sorted(os.listdir(os.path.join(self.job, "spec"))),
                         ["_kept.json", "b.json"])
        self.assertTrue(src.clear(self.job))
        self.assertEqual(src.current(self.job, self.root), {})
        self.assertFalse(src.clear(self.job))

    def test_the_design_step_reads_the_file_when_nothing_is_pasted(self):
        src.store(self.job, "orders.json", SPEC, "chosen on the page", self.root)
        seen = []

        def agent(prompt, followup):
            seen.append(prompt)
            return {"result": json.dumps({"test_cases": [{
                "title": "lists orders", "endpoint": "GET /orders",
                "steps": [{"action": "call it", "expected": "200"}]}]})}
        out = io.StringIO()
        with redirect_stdout(out):
            design.design("j1", root=self.root, agent=agent, fresh=True)
        self.assertIn("the file spec/orders.json, from chosen on the page", out.getvalue())
        self.assertIn("/orders/{id}", seen[0])
        with redirect_stdout(io.StringIO()):
            design.design("j1", swagger=OTHER.decode(), root=self.root, agent=agent, fresh=True)
        self.assertIn("/health", seen[1])
        self.assertNotIn("/orders", seen[1], "what is pasted wins over the file")


class FromAConfluencePage(Base):
    def test_the_one_attachment_that_is_a_specification_is_taken(self):
        out = self.fetch([row("design.png", b"\x89PNG"), row("openapi.json", SPEC),
                          row("notes.json", b'{"a": 1}')])
        self.assertTrue(out["ok"])
        self.assertEqual((out["spec"]["name"], out["spec"]["endpoints"]), ("openapi.json", 3))
        self.assertEqual(out["spec"]["origin"], f"Confluence: {LINK}")
        self.assertEqual([(c["title"], c["endpoints"]) for c in out["candidates"]],
                         [("openapi.json", 3), ("notes.json", None)])

    def test_several_that_fit_are_listed_and_none_is_chosen(self):
        src.store(self.job, "before.json", OTHER, "x", self.root)
        out = self.fetch([row("orders.json", SPEC), row("health.json", OTHER)])
        self.assertFalse(out["ok"])
        self.assertIn("Nothing was taken", out["message"])
        self.assertEqual(out["spec"]["name"], "before.json", "what was kept before still is")
        named = self.fetch([row("orders.json", SPEC), row("health.json", OTHER)], "health.json")
        self.assertEqual((named["ok"], named["spec"]["name"]), (True, "health.json"))

    def test_a_name_that_is_not_there_or_not_a_specification_is_refused(self):
        rows = [row("orders.json", SPEC), row("diagram.png", b"x")]
        for name in ("missing.json", "diagram.png", "ORDERS.JSON"):
            with self.assertRaises(src.Refused, msg=name):
                self.fetch(rows, name)
        with self.assertRaises(src.Refused) as got:
            self.fetch([row("a.json", b'{"x": 1}')], "a.json")
        self.assertIn("not an API specification", str(got.exception))

    def test_a_page_with_nothing_that_could_be_one_says_what_it_has(self):
        with self.assertRaises(src.Refused) as got:
            self.fetch([row("design.png"), row("notes.docx")])
        self.assertIn("design.png, notes.docx", str(got.exception))
        self.assertIn("code block", str(got.exception))
        with self.assertRaises(src.Refused) as got:
            self.fetch([])
        self.assertIn("no attachments at all", str(got.exception))

    def test_more_attachments_than_are_looked_at_is_not_a_choice_made_for_you(self):
        rows = [row("orders.json", SPEC)] + [row(f"n{i}.json", b'{"a": 1}')
                                             for i in range(src.MAX_CANDIDATES)]
        out = self.fetch(rows)
        self.assertFalse(out["ok"])
        self.assertIn("1 more were not looked at", out["message"])
        self.assertIn("1 of this page's attachments is a specification", out["message"])

    def test_the_command_writes_what_the_page_shows(self):
        saved = src.loop.intake.ROOT
        src.loop.intake.ROOT = self.root
        self.addCleanup(setattr, src.loop.intake, "ROOT", saved)
        with redirect_stdout(io.StringIO()):
            rc = src.main(["confluence", "--job", "j1", "--link", LINK], conf=att,
                          conn={"base": "x"}, lister=lambda c: [row("openapi.json", SPEC)],
                          reader=lambda a: a["_data"], root=self.root)
        self.assertEqual(rc, 0)
        with io.open(os.path.join(self.job, src.RESULT), encoding="utf-8") as fh:
            r = json.load(fh)
        self.assertEqual((r["kind"], r["ok"], r["spec"]["name"]), ("spec", True, "openapi.json"))
        with redirect_stdout(io.StringIO()):
            rc = src.main(["confluence", "--job", "j1", "--link", LINK], conf=att,
                          conn={"base": "x"}, lister=lambda c: [], reader=None, root=self.root)
        self.assertEqual(rc, 2)
        with io.open(os.path.join(self.job, src.RESULT), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["kind"], "error")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(src.main(["confluence", "--job", "../x", "--link", LINK]), 2)


class FoundByTheReview(Base):
    """Each of these was wrong when the step was first written."""

    def test_a_file_named_like_the_record_does_not_replace_it(self):
        """`source.json` was the record's name: a file called that wiped
        the kept specification and was reported as kept."""
        src.store(self.job, "good.json", SPEC, "x", self.root)
        for name in ("source.json", "Source.JSON"):
            got = src.store(self.job, name, OTHER, "y", self.root)
            self.assertEqual((got["name"], got["endpoints"]), (name, 1))
            self.assertEqual(design.stored_spec(self.job)[0], name)
        out = self.fetch([row("source.json", SPEC)])
        self.assertEqual((out["ok"], out["spec"]["name"]), (True, "source.json"))

    def test_a_failure_while_keeping_leaves_the_one_kept_before(self):
        src.store(self.job, "good.json", SPEC, "x", self.root)
        real = os.replace
        calls = []

        def failing(a, b):
            calls.append((os.path.basename(a), os.path.basename(b)))
            if os.path.basename(a).startswith("spec.new."):
                raise PermissionError(13, "in use", "C:\\somewhere\\private\\path")
            return real(a, b)
        os.replace = failing
        try:
            with self.assertRaises(src.Refused) as got:
                src.store(self.job, "new.json", OTHER, "y", self.root)
        finally:
            os.replace = real
        self.assertNotIn("private", str(got.exception), "no path of this machine in the message")
        self.assertEqual(src.current(self.job, self.root)["name"], "good.json")
        self.assertEqual(design.stored_spec(self.job)[0], "good.json")
        self.assertEqual(sorted(os.listdir(self.job)), ["spec"], "nothing left lying about")

    def test_two_at_once_leave_one_of_them_whole(self):
        import threading
        errors = []

        def put(name, data):
            try:
                src.store(self.job, name, data, "x", self.root)
            except Exception as e:                       # noqa: BLE001
                errors.append(e)
        for _ in range(15):
            a = threading.Thread(target=put, args=("a.json", SPEC))
            b = threading.Thread(target=put, args=("b.json", OTHER))
            a.start(); b.start(); a.join(); b.join()
            self.assertIn(src.current(self.job, self.root).get("name"), ("a.json", "b.json"))
        self.assertEqual(errors, [])
        self.assertEqual(sorted(os.listdir(self.job)), ["spec"])

    def test_what_the_page_is_told_comes_from_the_record_not_from_reading_it_again(self):
        src.store(self.job, "orders.json", SPEC, "x", self.root)
        saved = design.read_spec
        design.read_spec = lambda text: (_ for _ in ()).throw(AssertionError("parsed again"))
        self.addCleanup(setattr, design, "read_spec", saved)
        got = src.current(self.job, self.root)
        self.assertEqual((got["endpoints"], got["format"]), (3, "JSON"))

    def test_one_that_could_not_be_read_is_not_the_same_as_not_being_one(self):
        def reader(a):
            if a["title"] == "flaky.json":
                raise att.Refused("HTTP 500 from Confluence for 'flaky.json'")
            return a["_data"]
        out = src.from_confluence(self.job, LINK, "", conf=att, conn={"base": "x"},
                                  lister=lambda c: [row("flaky.json", SPEC), row("ok.json", OTHER)],
                                  reader=reader, root=self.root)
        self.assertFalse(out["ok"], "the other one may be the specification")
        self.assertIn("1 could not be told", out["message"])
        self.assertEqual([(c["title"], c["note"]) for c in out["candidates"]],
                         [("flaky.json", "could not be told"), ("ok.json", "")])
        with self.assertRaises(src.Refused):                 # none good, none unknown
            self.fetch([row("a.json", b'{"x": 1}')])

    def test_yaml_that_cannot_be_read_here_is_not_called_not_a_specification(self):
        saved = design.read_spec

        def no_yaml(text):
            if text.lstrip().startswith("{"):
                return saved(text)
            return None, "not JSON, and PyYAML is not installed so YAML cannot be read"
        design.read_spec = no_yaml
        self.addCleanup(setattr, design, "read_spec", saved)
        out = self.fetch([row("openapi.yaml", b"openapi: 3.0.1\npaths: {}\n"),
                          row("other.json", OTHER)])
        self.assertFalse(out["ok"], "the YAML may be the one; the JSON is not taken instead")
        self.assertIn("PyYAML is not installed", out["message"])

    def test_a_name_that_cannot_be_kept_is_said_not_passed_over(self):
        with self.assertRaises(src.Refused) as got:
            self.fetch([row("openapi[1].json", SPEC)])
        self.assertIn("cannot be used for a file here", str(got.exception))
        out = self.fetch([row("openapi[1].json", SPEC), row("health.json", OTHER)])
        self.assertFalse(out["ok"])
        self.assertIn("have a name that cannot be used here (openapi[1].json)", out["message"])

    def test_a_list_that_was_not_all_seen_is_not_called_complete(self):
        out = src.from_confluence(self.job, LINK, "", conf=att, conn={"base": "x", "more": True},
                                  lister=lambda c: [row("ok.json", OTHER)],
                                  reader=lambda a: a["_data"], root=self.root)
        self.assertFalse(out["ok"])
        self.assertIn("more attachments than were listed", out["message"])

    def test_remove_stops_it_being_used_even_when_a_file_cannot_be_deleted(self):
        src.store(self.job, "good.json", SPEC, "x", self.root)
        real = shutil.rmtree
        src.shutil.rmtree = lambda *a, **k: None             # as if a file were held open
        try:
            self.assertTrue(src.clear(self.job))
        finally:
            src.shutil.rmtree = real
        self.assertEqual(src.current(self.job, self.root), {})
        self.assertIsNone(design.stored_spec(self.job))

    def test_the_real_wiring_asks_confluence_for_the_page_the_link_names(self):
        asked = []

        class Conf:
            Refused = att.Refused

            @staticmethod
            def connect(link):
                asked.append(("connect", link))
                return {"base": "b", "more": False}

            @staticmethod
            def page_id(conn):
                asked.append("page_id")
                return "4242"

            @staticmethod
            def list_attachments(conn, pid):
                asked.append(("list", pid))
                return [row("openapi.json", SPEC)]

            @staticmethod
            def read(conn, a, limit):
                asked.append(("read", a["title"], limit))
                return a["_data"]
        out = src.from_confluence(self.job, LINK, "", conf=Conf, root=self.root)
        self.assertTrue(out["ok"])
        self.assertEqual(asked, [("connect", LINK), "page_id", ("list", "4242"),
                                 ("read", "openapi.json", src.MAX_SPEC_BYTES)])

    def test_the_kept_file_beats_a_page_and_is_left_alone_by_a_design(self):
        src.store(self.job, "orders.json", SPEC, "x", self.root)
        os.makedirs(os.path.join(self.job, "sources", "01-confluence"))
        with io.open(os.path.join(self.job, "sources", "01-confluence", "p.md"), "w") as fh:
            fh.write("```\n" + OTHER.decode() + "\n```\n")
        with io.open(os.path.join(self.job, "intake.json"), "w") as fh:
            json.dump({"links": [{"ok": True, "dir": "01-confluence", "files": ["p.md"]}]}, fh)
        other_job = os.path.join(self.root, "target", "agent", "j2")
        os.makedirs(other_job)
        seen = []

        def agent(prompt, followup):
            seen.append(prompt)
            return {"result": json.dumps({"test_cases": [{
                "title": "t", "endpoint": "GET /orders",
                "steps": [{"action": "a", "expected": "200"}]}]})}
        with redirect_stdout(io.StringIO()):
            design.design("j1", root=self.root, agent=agent, fresh=True)
            design.design("j2", swagger=OTHER.decode(), root=self.root, agent=agent, fresh=True)
        self.assertIn("/orders/{id}", seen[0])
        self.assertNotIn("/health", seen[0], "the kept file, not the page's code block")
        self.assertIsNone(design.stored_spec(other_job), "another job's file is not this job's")
        self.assertNotIn("/orders", seen[1])
        self.assertEqual(src.current(self.job, self.root)["name"], "orders.json")


class TheAttachmentItself(unittest.TestCase):
    """tools/confluence/attachments.py: where the token may go."""

    def conn(self):
        return att.connect(LINK, CFG)

    def test_the_same_refusals_as_reading_a_page(self):
        self.assertEqual(self.conn()["base"], "https://wiki.example.com/confluence")
        for link, cfg in (("not a link at all", CFG),
                          (LINK.replace("wiki.example.com", "evil.example.net"), CFG),
                          (LINK.replace("https://", "http://"), CFG),
                          (LINK, dict(CFG, pat="")), (LINK, dict(CFG, base_urls=[])),
                          (LINK, dict(CFG, base_urls="https://wiki.example.com/confluence")),
                          (LINK, {})):
            with self.assertRaises(att.Refused, msg=link):
                att.connect(link, cfg)

    def test_the_list_is_what_confluence_said_and_asks_only_the_approved_host(self):
        asked = []

        def get(url, token, timeout):
            asked.append((url, token))
            return {"results": [
                {"id": "att1", "title": "openapi.yaml",
                 "extensions": {"fileSize": 1234, "mediaType": "text/yaml"},
                 "_links": {"download": "/download/attachments/4242/openapi.yaml?version=3"}},
                "junk", {"title": "odd", "extensions": {"fileSize": "big"}}]}
        rows = att.list_attachments(self.conn(), "4242", get)
        self.assertEqual(rows[0], {"id": "att1", "title": "openapi.yaml", "bytes": 1234,
                                   "media": "text/yaml",
                                   "download": "/download/attachments/4242/openapi.yaml?version=3"})
        self.assertEqual((rows[1]["title"], rows[1]["bytes"], rows[1]["download"]), ("odd", 0, ""))
        self.assertTrue(asked[0][0].startswith(
            "https://wiki.example.com/confluence/rest/api/content/4242/child/attachment?"))
        self.assertEqual(asked[0][1], "tok_conf_123456")

    def test_an_attachment_is_read_only_from_a_path_on_the_approved_host(self):
        conn = self.conn()
        self.assertEqual(att.download_url(conn, {"title": "a", "download": "/download/attachments/1/a.json"}),
                         "https://wiki.example.com/confluence/download/attachments/1/a.json")
        for link in ("https://evil.example.net/download/attachments/1/a.json",
                     "//evil.example.net/download/a.json", "", "download/attachments/1/a.json",
                     "/download/../../admin/a.json", "/rest/api/user/current",
                     "/download\\..\\x", "javascript:alert(1)",
                     "/download/%2e%2e/%2e%2e/rest/api/user/current",
                     "/download/..%2f..%2fadmin/x.json", "/download/a/..;/..;/rest/api/x",
                     "/plugins/servlet/anything/download/x", "/download/a\r\nX-Evil: 1",
                     "/download/a b.json", "/download/caf\u00e9.json", "/download//x.json"):
            with self.assertRaises(att.Refused, msg=link):
                att.download_url(conn, {"title": "a", "download": link})

    def test_a_redirect_to_http_on_the_same_host_does_not_take_the_token(self):
        guard = att.fetch._SameHostRedirect("wiki.example.com", "https")
        import urllib.request
        req = urllib.request.Request("https://wiki.example.com/download/a")
        req.add_header("Authorization", "Bearer tok_conf_123456")
        for to in ("http://wiki.example.com/x", "https://evil.example.net/x",
                   "https://wiki.example.com:8443/x", "//wiki.example.com/x"):
            with self.assertRaises(urllib.error.URLError, msg=to):
                guard.redirect_request(req, None, 302, "Found", {}, to)
        self.assertIsNotNone(guard.redirect_request(
            req, None, 302, "Found", {}, "https://wiki.example.com/elsewhere"))
        opener = att.fetch._opener("https://wiki.example.com/confluence")
        mine = [h for h in opener.handlers if isinstance(h, att.fetch._SameHostRedirect)][0]
        self.assertEqual((mine.allowed, mine.scheme), ("wiki.example.com", "https"))

    def test_the_api_path_of_the_configuration_cannot_change_the_host(self):
        for api in ("rest/api/content", "@evil.example.net/rest", "//evil.example.net/rest",
                    "/rest/api?x=1", "/rest api", "\\\\evil\\rest"):
            with self.assertRaises(att.Refused, msg=api):
                att.connect(LINK, dict(CFG, api_path=api))
        conn = att.connect(LINK, dict(CFG, api_path="/confluence/rest/api/content"))
        self.assertEqual((conn["base"], conn["site"]),
                         ("https://wiki.example.com", "https://wiki.example.com/confluence"))
        self.assertEqual(att.download_url(conn, {"title": "a", "download": "/download/attachments/1/a.json"}),
                         "https://wiki.example.com/confluence/download/attachments/1/a.json")

    def test_a_reply_cut_short_or_timed_out_is_a_refusal_not_a_crash(self):
        import http.client

        class Breaks:
            def __init__(self, exc): self.exc = exc
            def open(self, req, timeout=0): raise self.exc
        a = {"title": "a.json", "bytes": 3, "download": "/download/attachments/1/a.json"}
        for exc in (TimeoutError("tok_conf_123456"), ConnectionResetError(),
                    http.client.IncompleteRead(b"x"), ValueError("bad")):
            with self.assertRaises(att.Refused) as got:
                att.read(self.conn(), a, opener=Breaks(exc))
            self.assertNotIn("tok_conf", str(got.exception))

    def test_a_list_with_a_next_page_says_so(self):
        conn = self.conn()
        att.list_attachments(conn, "1", lambda u, t, s: {"results": [], "_links": {"next": "/x"}})
        self.assertTrue(conn["more"])
        att.list_attachments(conn, "1", lambda u, t, s: {"results": []})
        self.assertFalse(conn["more"])

    def test_reading_sends_the_token_in_a_header_and_stops_at_the_limit(self):
        conn = self.conn()
        seen = []

        class Reply:
            def __init__(self, data): self.data = data
            def read(self, n): return self.data[:n]
            def __enter__(self): return self
            def __exit__(self, *a): return False

        class Opener:
            def __init__(self, data): self.data = data
            def open(self, req, timeout=0):
                seen.append((req.full_url, req.get_header("Authorization"), req.get_method()))
                return Reply(self.data)
        a = {"title": "a.json", "bytes": 3, "download": "/download/attachments/1/a.json"}
        self.assertEqual(att.read(conn, a, opener=Opener(b"abc")), b"abc")
        self.assertEqual(seen[0], ("https://wiki.example.com/confluence/download/attachments/1/a.json",
                                   "Bearer tok_conf_123456", "GET"))
        self.assertNotIn("tok_conf", seen[0][0])
        with self.assertRaises(att.Refused):
            att.read(conn, dict(a, bytes=0), limit=2, opener=Opener(b"abc"))   # more arrived than said
        with self.assertRaises(att.Refused):
            att.read(conn, dict(a, bytes=10), limit=5, opener=Opener(b"abc"))
        self.assertEqual(len(seen), 2, "one known to be too large is not asked for")

        class Fails:
            def open(self, req, timeout=0):
                raise urllib.error.URLError("refusing a redirect to evil.example.net")
        with self.assertRaises(att.Refused) as got:
            att.read(conn, a, opener=Fails())
        self.assertNotIn("tok_conf", str(got.exception))


class Declared(unittest.TestCase):
    def test_the_page_sends_a_link_and_a_name_and_no_path(self):
        jobs = _load("jobs_for_specsource", os.path.join(HERE, "jobs.py"))
        r = jobs.RUNNABLES["spec-confluence"]
        self.assertEqual(r["options"], {"--job": "text", "--link": "text", "--name": "text"})
        self.assertEqual(jobs.RESULT_FILES["spec-"], src.RESULT)
        self.assertEqual(jobs.read_artifact("no-such-job-here", "spec-result.json"), "")

    def test_a_chosen_file_goes_through_the_same_checks(self):
        jobs = _load("jobs_for_spec_upload", os.path.join(HERE, "jobs.py"))
        root = tempfile.mkdtemp(prefix="b2b-specjobs-")
        self.addCleanup(shutil.rmtree, root, True)
        saved = (jobs.ROOT, jobs.intake.ROOT)
        jobs.ROOT = jobs.intake.ROOT = root
        self.addCleanup(lambda: (setattr(jobs, "ROOT", saved[0]),
                                 setattr(jobs.intake, "ROOT", saved[1])))
        got = jobs.save_spec("j1", "orders.json", io.BytesIO(SPEC), len(SPEC))
        self.assertEqual((got["path"], got["endpoints"]), ("target/agent/j1/spec/orders.json", 3))
        self.assertEqual(jobs.spec_state("j1")["name"], "orders.json")
        for job, name, data, length in (("j1", "x.json", b'{"a": 1}', None),
                                        ("j1", "x.exe", SPEC, None),
                                        ("../x", "x.json", SPEC, None),
                                        ("j1", "x.json", SPEC, len(SPEC) + 50),
                                        ("j1", "x.json", SPEC, jobs.MAX_SPEC_UPLOAD + 1),
                                        ("j1", "x.json", b"", None)):
            with self.assertRaises(ValueError, msg=(job, name)):
                jobs.save_spec(job, name, io.BytesIO(data), len(data) if length is None else length)
        self.assertEqual(jobs.spec_state("j1")["name"], "orders.json")
        self.assertEqual(jobs.spec_state("never-made"), {})
        self.assertTrue(jobs.spec_clear("j1"))
        self.assertEqual(jobs.spec_state("j1"), {})


if __name__ == "__main__":
    unittest.main(verbosity=1)
