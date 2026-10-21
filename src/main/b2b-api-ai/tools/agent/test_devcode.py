"""The developer-change check reads a diff as it says, and refuses what it must.

    python tools/agent/test_devcode.py

No test here reaches a repository or Cursor: git is a function that
answers from the strings below, and Cursor is a function too.
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


dc = _load("devcode_under_test", os.path.join(HERE, "devcode.py"))

CONTROLLER = "src/main/java/com/acme/TravelerController.java"
DTO = "src/main/java/com/acme/dto/TravelerFilterRequest.java"
ENUM = "src/main/java/com/acme/TravelerRole.java"
UNIT = "src/test/java/com/acme/TravelerControllerTest.java"

CONTROLLER_TIP = """package com.acme;

@RestController
@RequestMapping("/v1/members")
public class TravelerController {

    @GetMapping("/{memberId}/spend")
    public ResponseEntity<Spend> spend(@PathVariable String memberId,
                                       @RequestParam(name = "travelerGuestId", required = false) String traveler) {
        if (traveler != null && !service.knows(traveler)) {
            return ResponseEntity.notFound().build();
        }
        return ResponseEntity.ok(service.spend(memberId, traveler));
    }

    @PostMapping("/{memberId}/travelers")
    public ResponseEntity<Traveler> add(@PathVariable String memberId, @Valid @RequestBody TravelerFilterRequest body) {
        if (service.exists(body)) {
            throw new TravelerAlreadyExistsException("TRV_DUPLICATE");
        }
        return ResponseEntity.status(201).body(service.add(memberId, body));
    }
}
"""

DIFF = f"""diff --git a/{CONTROLLER} b/{CONTROLLER}
--- a/{CONTROLLER}
+++ b/{CONTROLLER}
@@ -8,4 +8,8 @@ public class TravelerController {{
     public ResponseEntity<Spend> spend(@PathVariable String memberId,
+                                       @RequestParam(name = "travelerGuestId", required = false) String traveler) {{
+        if (traveler != null && !service.knows(traveler)) {{
+            return ResponseEntity.notFound().build();
+        }}
         return ResponseEntity.ok(service.spend(memberId, traveler));
@@ -15,0 +16,8 @@ public class TravelerController {{
+    @PostMapping("/{{memberId}}/travelers")
+    public ResponseEntity<Traveler> add(@PathVariable String memberId, @Valid @RequestBody TravelerFilterRequest body) {{
+        if (service.exists(body)) {{
+            throw new TravelerAlreadyExistsException("TRV_DUPLICATE");
+        }}
+        return ResponseEntity.status(201).body(service.add(memberId, body));
+    }}
+}}
diff --git a/{DTO} b/{DTO}
--- a/{DTO}
+++ b/{DTO}
@@ -3,2 +3,6 @@
 public class TravelerFilterRequest {{
-    private String legacyCode;
+    @NotNull
+    @Size(max = 50)
+    private String travelerName;
+    private String costCenter;
 }}
diff --git a/{ENUM} b/{ENUM}
--- a/{ENUM}
+++ b/{ENUM}
@@ -2,3 +2,4 @@
 public enum TravelerRole {{
     OWNER,
+    COORDINATOR,
     GUEST;
diff --git a/{UNIT} b/{UNIT}
--- a/{UNIT}
+++ b/{UNIT}
@@ -1,0 +1,3 @@
+class TravelerControllerTest {{
+    @Test void returns404ForUnknownTraveler() {{ }}
+}}
"""

DTO_TIP = """package com.acme.dto;
public class TravelerFilterRequest {
    @NotNull
    @Size(max = 50)
    private String travelerName;
    private String costCenter;
}
"""
ENUM_TIP = "package com.acme;\npublic enum TravelerRole {\n    OWNER,\n    COORDINATOR,\n    GUEST;\n}\n"
TIPS = {CONTROLLER: CONTROLLER_TIP, DTO: DTO_TIP, ENUM: ENUM_TIP}


def case(cid, endpoint, title, ctype="positive", expected="status 200", data=""):
    return {"id": cid, "title": title, "endpoint": endpoint, "type": ctype, "priority": "high",
            "steps": [{"action": f"Send {endpoint}", "data": data, "expected": expected}]}


class Reading(unittest.TestCase):
    def setUp(self):
        self.found = dc.analyse(DIFF, lambda p: TIPS.get(p, ""))

    def test_a_new_and_a_changed_endpoint_are_found_with_the_class_prefix(self):
        eps = {f"{e['verb']} {e['path']}": e["how"] for e in self.found["endpoints"]}
        self.assertEqual(eps.get("POST /v1/members/{memberId}/travelers"), "new")
        self.assertEqual(eps.get("GET /v1/members/{memberId}/spend"), "changed")

    def test_status_codes_are_read_from_responses_and_exceptions(self):
        codes = {s["code"] for s in self.found["statuses"]}
        self.assertTrue({404, 201, 409} <= codes, codes)

    def test_validation_is_tied_to_its_field(self):
        rules = {(v["rule"], v["field"]) for v in self.found["validations"]}
        self.assertIn(("@NotNull", "travelerName"), rules)
        self.assertIn(("@Size(max = 50)", "travelerName"), rules)

    def test_fields_enums_params_and_error_codes(self):
        self.assertEqual({f["name"] for f in self.found["fields"]}, {"travelerName", "costCenter"})
        self.assertEqual([f["name"] for f in self.found["removed_fields"]], ["legacyCode"])
        self.assertEqual([e["value"] for e in self.found["enums"]], ["COORDINATOR"])
        self.assertIn("travelerGuestId", {p["name"] for p in self.found["params"]})
        self.assertFalse(next(p for p in self.found["params"] if p["name"] == "travelerGuestId")["required"])
        self.assertIn("TRV_DUPLICATE", {e["code"] for e in self.found["error_codes"]})

    def test_the_developers_own_tests_are_listed_and_not_read_as_behaviour(self):
        self.assertEqual([t["name"] for t in self.found["dev_tests"]], ["returns404ForUnknownTraveler"])
        self.assertFalse(any(UNIT in e for ep in self.found["endpoints"] for e in ep["evidence"]))

    def test_openapi_operations_are_endpoints(self):
        spec = "openapi: 3.0.0\npaths:\n  /v1/things/{id}:\n    get:\n      responses:\n        '404':\n          description: no\n"
        diff = ("diff --git a/api/openapi.yaml b/api/openapi.yaml\n--- a/api/openapi.yaml\n+++ b/api/openapi.yaml\n"
                "@@ -0,0 +1,7 @@\n" + "".join("+" + l + "\n" for l in spec.splitlines()))
        found = dc.analyse(diff, lambda p: spec)
        self.assertEqual([(e["verb"], e["path"], e["how"]) for e in found["endpoints"]],
                         [("GET", "/v1/things/{id}", "new")])
        self.assertEqual([s["code"] for s in found["statuses"]], [404])


class Comparing(unittest.TestCase):
    def setUp(self):
        self.found = dc.analyse(DIFF, lambda p: TIPS.get(p, ""))

    def cases(self, *rows):
        return [{"id": c["id"], "title": c["title"], "type": c["type"], "verb": c["endpoint"].split()[0],
                 "path": c["endpoint"].split()[1], "endpoint": c["endpoint"], "state": "proposed",
                 "text": " ".join([c["title"], c["endpoint"], c["type"]] +
                                  [" ".join(s.values()) for s in c["steps"]]).lower()} for c in rows]

    def test_no_cases_means_everything_is_a_gap(self):
        gaps, covered = dc.compare(self.found, [])
        self.assertFalse(covered)
        kinds = {g["kind"] for g in gaps}
        self.assertTrue({"endpoint", "status", "validation", "field", "enum", "error-code", "param"} <= kinds, kinds)
        self.assertEqual([g["id"] for g in gaps], [f"G-{n:03d}" for n in range(1, len(gaps) + 1)])
        self.assertEqual(gaps[0]["severity"], "high")

    def test_a_case_on_the_endpoint_with_the_code_covers_it_and_the_rest_stay_gaps(self):
        cases = self.cases(
            case("TC-001", "GET /members/{id}/spend", "spend filtered by travelerGuestId", data="travelerGuestId=G1"),
            case("TC-002", "GET /members/{id}/spend", "unknown traveler gives 404", "negative", "status 404"),
            case("TC-003", "POST /members/{id}/travelers", "travelerName longer than 50 is refused",
                 "boundary", "status 400"))
        gaps, covered = dc.compare(self.found, cases)
        whats = " | ".join(c["what"] for c in covered)
        self.assertIn("GET /v1/members/{memberId}/spend", whats)
        self.assertIn("status 404", whats)
        self.assertIn("travelerName", whats)
        titles = " | ".join(g["title"] for g in gaps)
        self.assertNotIn("endpoint POST", titles)               # TC-003 is on it
        self.assertIn("Status 409", titles)                     # nobody expects the duplicate
        self.assertIn("costCenter", titles)
        self.assertIn("COORDINATOR", titles)

    def test_a_removed_field_still_used_by_a_case_is_a_high_gap(self):
        cases = self.cases(case("TC-009", "POST /members/{id}/travelers", "send legacyCode", data='{"legacyCode": "X"}'))
        gaps, _ = dc.compare(self.found, cases)
        hit = [g for g in gaps if g["kind"] == "removed-field"]
        self.assertEqual(len(hit), 1)
        self.assertEqual(hit[0]["severity"], "high")
        self.assertIn("TC-009", hit[0]["title"])

    def test_paths_match_across_a_prefix_and_parameter_names(self):
        self.assertTrue(dc.same_endpoint("GET", "/v1/members/{memberId}/spend", "GET", "/members/{id}/spend"))
        self.assertTrue(dc.same_endpoint("ANY", "/a/{x}", "POST", "/a/{y}"))
        self.assertFalse(dc.same_endpoint("GET", "/a/b", "POST", "/a/b"))
        self.assertFalse(dc.same_endpoint("GET", "/a/b", "GET", "/a/c"))


class Refusing(unittest.TestCase):
    def test_only_https_and_ssh_addresses_without_a_password(self):
        self.assertEqual(dc.parse_repo("https://gitlab.example.com/team/svc.git")["shown"],
                         "gitlab.example.com/team/svc.git")
        self.assertEqual(dc.parse_repo("git@github.com:team/svc.git")["kind"], "ssh")
        for bad in ("http://gitlab.example.com/x.git", "file:///c:/repo", "C:\\repo", "../repo",
                    "https://user:secret@gitlab.example.com/x.git", "https://h/x.git; rm -rf /",
                    "ext::sh -c touch% /tmp/x", ""):
            with self.assertRaises(dc.Refused, msg=bad):
                dc.parse_repo(bad)

    def test_the_allowlist_is_kept_when_there_is_one(self):
        allowed = ["https://gitlab.example.com"]
        dc.parse_repo("https://gitlab.example.com/a/b.git", allowed)
        with self.assertRaises(dc.Refused):
            dc.parse_repo("https://github.com/a/b.git", allowed)

    def test_branch_names_that_are_not_names(self):
        self.assertEqual(dc.check_ref("refs/heads/feature/ABC-1", "branch"), "feature/ABC-1")
        for bad in ("-upload-pack=x", "a..b", "a b", "x.lock", ""):
            with self.assertRaises(dc.Refused, msg=bad):
                dc.check_ref(bad, "branch")

    def test_a_token_goes_in_a_header_and_never_in_the_address(self):
        cfg = dc._git_config("tok-123456")
        joined = " ".join(cfg)
        self.assertIn("http.extraHeader=Authorization: Basic ", joined)
        self.assertNotIn("tok-123456", joined)
        self.assertIn("protocol.allow=never", joined)


def fake_git(log_out, by_sha):
    calls = []

    def runner(args):
        calls.append(args)
        if args[0] == "ls-remote":
            return 0, "ref: refs/heads/develop\tHEAD\n"
        if args[0] == "fetch":
            return 0, ""
        if args[0] == "log":
            grep = [a for a in args if a.startswith("--grep=")]
            return 0, log_out(grep[0][7:] if grep else "")
        if args[0] == "show":
            if args[1].startswith("refs/remotes/origin/feature/x:"):
                path = args[1].split(":", 1)[1]
                return (0, TIPS[path]) if path in TIPS else (128, "fatal: no such path")
            sha = args[-1]
            if "--numstat" in args:
                return 0, "".join(f"3\t1\t{p}\n" for p in TIPS)
            return 0, by_sha.get(sha, "")
        return 1, f"unexpected {args}"
    return runner, calls


SHA = "a" * 40


class Running(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="b2b-dc-")
        self.job = "job-dc"
        self.out = os.path.join(self.root, ".workbench", self.job)
        os.makedirs(self.out)
        with io.open(os.path.join(self.out, "design.json"), "w", encoding="utf-8") as fh:
            json.dump({"test_cases": [
                case("TC-001", "GET /members/{id}/spend", "spend for a traveler", data="travelerGuestId=G1"),
                case("TC-002", "POST /members/{id}/travelers", "rejected one", "negative", "status 409"),
            ]}, fh)
        with io.open(os.path.join(self.out, "case-review.json"), "w", encoding="utf-8") as fh:
            json.dump({"design": {"TC-002": {"state": "rejected"}}}, fh)
        with io.open(os.path.join(self.out, "intake.json"), "w", encoding="utf-8") as fh:
            json.dump({"story": {"key": "ABC-77"}}, fh)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def result(self):
        with io.open(os.path.join(self.out, "devcode.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def run_it(self, runner, **kw):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = dc.run(self.job, "https://gitlab.example.com/t/svc.git", "feature/x", root=self.root,
                        runner=runner, **kw)
        return rc, buf.getvalue()

    def test_commits_naming_the_ticket_are_read_and_a_report_is_written(self):
        seen = {}

        def log_out(grep):
            seen["grep"] = grep
            return f"{SHA}\x1fDev One\x1f2026-10-01T10:00:00+00:00\x1fABC-77 traveler filter\x1e"
        runner, calls = fake_git(log_out, {SHA: DIFF})
        rc, said = self.run_it(runner)
        self.assertEqual(rc, 0, said)
        self.assertEqual(seen["grep"], "ABC-77")                # the job's story, when none is given
        r = self.result()
        self.assertEqual(r["commit_mode"], "ticket")
        self.assertEqual(r["base"], "develop")
        self.assertEqual(r["cases_checked"], 1)                  # TC-002 is rejected
        self.assertTrue(any(g["kind"] == "endpoint" and "POST" in g["title"] for g in r["gaps"]))
        self.assertTrue(os.path.isfile(os.path.join(self.out, "devcode.md")))
        fetch = next(c for c in calls if c[0] == "fetch")
        self.assertIn("--filter=blob:none", fetch)

    def test_no_commit_naming_the_ticket_falls_back_to_the_branch_and_says_so(self):
        def log_out(grep):
            return "" if grep else f"{SHA}\x1fDev\x1f2026-10-01T10:00:00+00:00\x1fwip\x1e"
        runner, _ = fake_git(log_out, {SHA: DIFF})
        rc, said = self.run_it(runner, ticket="ABC-77")
        self.assertEqual(rc, 0, said)
        r = self.result()
        self.assertEqual(r["commit_mode"], "branch")
        self.assertTrue(any("no commit" in n for n in r["notes"]))

    def test_cursor_gaps_are_added_and_marked(self):
        runner, _ = fake_git(lambda g: f"{SHA}\x1fD\x1f2026-10-01T10:00:00+00:00\x1fABC-77\x1e", {SHA: DIFF})
        sent = {}

        def agent(prompt, followup):
            sent["prompt"] = prompt
            return {"result": json.dumps({"gaps": [{"title": "cache is not invalidated", "why": "w",
                                                    "severity": "high", "evidence": "Svc.java:9"}],
                                          "summary": "s"}), "model": "m"}
        rc, said = self.run_it(runner, ask=True, agent=agent)
        self.assertEqual(rc, 0, said)
        r = self.result()
        mine = [g for g in r["gaps"] if g["source"] == "cursor"]
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0]["id"], f"G-{len(r['gaps']):03d}")
        self.assertIn("===== DEVELOPER CHANGE =====", sent["prompt"])
        self.assertNotIn("gitlab.example.com", sent["prompt"].split("===== DEVELOPER CHANGE =====")[0])

    def test_a_bad_address_writes_nothing(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = dc.run(self.job, "file:///etc", "main", root=self.root, runner=lambda a: (0, ""))
        self.assertEqual(rc, 1)
        self.assertFalse(os.path.exists(os.path.join(self.out, "devcode.json")))

    def test_the_refinement_for_chosen_gaps_keeps_every_case(self):
        runner, _ = fake_git(lambda g: f"{SHA}\x1fD\x1f2026-10-01T10:00:00+00:00\x1fABC-77\x1e", {SHA: DIFF})
        self.run_it(runner)
        text = dc.refine_request(self.out, ["G-001", "G-002"])
        self.assertIn("Keep EVERY existing case", text)
        self.assertIn("G-001", text)
        self.assertIn("G-002", text)
        self.assertNotIn("G-003", text)
        with self.assertRaises(ValueError):
            dc.refine_request(self.out, ["G-999"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
