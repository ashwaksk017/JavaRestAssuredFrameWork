"""ask.py: a question to Cursor is answered and changes nothing.

Seen live: "what is the name of the suite I just created" was sent through
the code-writing run and Cursor wrote the tests again."""
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ask  # noqa: E402

loop = ask._loop()


def read(path):
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


class KindOf(unittest.TestCase):
    QUESTIONS = [
        "what is the name of the suite I just created",
        "What is the name of the suite I just created?",
        "which file has the test for TC-003",
        "can you tell me where the CSV rows went?",
        "Could you explain why the 403 case is skipped",
        "is TC-002 automated",
        "list the classes in this job's suite",
        "the suite name?",
        "hey cursor, how many tests did you write",
        "Do the tests pass on stg?",
    ]
    CHANGES = [
        "Use the member's email from the CSV instead of a fixed one.",
        "can you add a test for the 404 case",
        "please fix the assertion in H4bMemberStaysTest",
        "why does TC-003 fail? fix it",
        "rename the suite to member-stays",
        "add a negative case, and assert the error code",
        "don't hard-code the guest id",
        "the email should come from config",          # no question word, no '?': the old meaning
        "",
    ]

    def test_questions(self):
        for text in self.QUESTIONS:
            self.assertEqual(ask.kind_of(text), "question", text)

    def test_changes(self):
        for text in self.CHANGES:
            self.assertEqual(ask.kind_of(text), "change", text)

    def test_the_page_uses_the_same_words(self):
        # kindOf in ui/app.js preselects the radio; the server falls back to
        # kind_of. A word added to one list only would make them disagree.
        js = read(os.path.join(HERE, "ui", "app.js"))

        def words(const):
            m = re.search(r"const %s = \[(.*?)\];" % const, js, re.S)
            self.assertIsNotNone(m, const)
            return tuple(re.findall(r'"([^"]*)"', m.group(1)))
        self.assertEqual(words("ASK_QUESTION"), ask.QUESTION_STARTS)
        self.assertEqual(words("ASK_CHANGE"), ask.CHANGE_STARTS)
        m = re.search(r"const ASK_POLITE = /(.*)/;", js)
        self.assertEqual(m.group(1), ask._POLITE.pattern)


class Ask(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ask-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        p = mock.patch.object(loop.intake, "ROOT", self.tmp)
        p.start()
        self.addCleanup(p.stop)
        self.job = "job-20261022-101010"
        self.dir = loop.intake.job_dir(self.job)
        os.makedirs(self.dir)
        os.makedirs(os.path.join(self.tmp, "Suites", "jira"))
        with io.open(os.path.join(self.tmp, "Suites", "jira", self.job + ".xml"), "w") as fh:
            fh.write('<suite><test><classes><class name="com.hi.api.tests.jira.H4bMemberStaysTest"/>'
                     '</classes></test></suite>')
        io.open(os.path.join(self.tmp, "Suites", "jira", "group-smoke.xml"), "w").close()

    def write(self, name, data):
        with io.open(os.path.join(self.dir, name), "w", encoding="utf-8") as fh:
            fh.write(data if isinstance(data, str) else json.dumps(data))

    def run_ask(self, agent):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = ask.ask(self.job, root=self.tmp, agent=agent)
        return rc, out.getvalue()

    def test_the_facts_name_the_jobs_suite_file(self):
        self.write("review.json", {"state": "pending-review", "scope": "new-test",
                                   "files": {"created": ["src/test/java/x/H4bMemberStaysTest.java"]}})
        f = ask.facts(self.dir, self.tmp)
        self.assertIn(f"Suites/jira/{self.job}.xml", f)
        self.assertIn("H4bMemberStaysTest", f)
        self.assertIn("Suites/jira/group-smoke.xml", f)
        self.assertIn("pending-review", f)

    def test_a_question_is_answered_and_nothing_of_the_job_changes(self):
        review = {"state": "pending-review", "scope": "new-test", "started": "x"}
        self.write("review.json", review)
        self.write(ask.QUESTION_FILE, "what is the name of the suite I just created")
        before = read(os.path.join(self.dir, "review.json"))
        seen = []
        rc, out = self.run_ask(lambda prompt: seen.append(prompt) or f"Suites/jira/{self.job}.xml")
        self.assertEqual(rc, 0, out)
        self.assertIn(f"Suites/jira/{self.job}.xml", out)
        self.assertEqual(read(os.path.join(self.dir, "review.json")), before)
        answers = os.listdir(os.path.join(self.dir, ask.ANSWERS_DIR))
        self.assertEqual(len(answers), 1)
        # The prompt is a question, not the code-writing request.
        prompt = seen[0]
        self.assertIn("===== THE QUESTION =====", prompt)
        self.assertNotIn("CREATE its test", prompt)
        self.assertNotIn("THE PERSON ASKS", prompt)
        self.assertIn("Do not create, edit, delete", prompt)

    def test_a_pushed_job_can_be_asked(self):
        self.write("review.json", {"state": "pushed", "branch": "feature_B2B-1"})
        self.write(ask.QUESTION_FILE, "which branch was this pushed to?")
        rc, out = self.run_ask(lambda prompt: "feature_B2B-1")
        self.assertEqual(rc, 0, out)
        self.assertIn("feature_B2B-1", out)

    def test_nothing_asked_is_refused(self):
        rc, out = self.run_ask(lambda prompt: "x")
        self.assertEqual(rc, 1)
        self.assertIn("nothing was asked", out)

    def test_no_answer_is_a_failure(self):
        self.write(ask.QUESTION_FILE, "what?")
        rc, out = self.run_ask(lambda prompt: "  ")
        self.assertEqual(rc, 1)
        self.assertIn("no answer", out)

    def test_cursors_side_goes_to_the_jobs_cursor_log(self):
        self.write(ask.QUESTION_FILE, "what is in this job?")
        self.run_ask(lambda prompt: "two tests")
        log = read(os.path.join(self.dir, "cursor.log"))
        self.assertIn("QUESTION", log)
        self.assertIn("two tests", log)


class Wiring(unittest.TestCase):
    def test_the_question_command_is_registered_and_runs_beside_a_change(self):
        import jobs
        spec = jobs.RUNNABLES["agent-question"]
        self.assertTrue(spec["argv"][-1].endswith("ask.py"))
        self.assertNotIn("agent-question", jobs.EXCLUSIVE)
        self.assertIn("agent-question", jobs.WORKBENCH_RUNNABLES)
        self.assertEqual(jobs.workflow_gate("job-20261022-101010", "agent-question", {}), "")


if __name__ == "__main__":
    unittest.main(verbosity=1)
