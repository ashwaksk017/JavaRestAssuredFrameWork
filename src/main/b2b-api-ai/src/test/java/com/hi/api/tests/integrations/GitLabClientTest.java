package com.hi.api.tests.integrations;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;

import org.testng.Assert;
import org.testng.annotations.AfterMethod;
import org.testng.annotations.Test;

import com.hi.api.gitlab.GitLabCi;
import com.hi.api.gitlab.GitLabClient;
import com.hi.api.gitlab.GitLabResult;
import com.hi.api.gitlab.GitLabResultsCollector;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;

@Epic("API Automation")
@Feature("GitLab CI / test management")
public class GitLabClientTest {

    @AfterMethod(alwaysRun = true)
    public void drainCollector() {
        GitLabResultsCollector.clearCurrentTestCaseIid();
    }

    @Test(groups = {"unit", "gitlab"})
    @Story("CI env overlay")
    @Description("GitLabCi prefers CI_* then config; job token vs PAT.")
    public void from_prefersCiVariables() {
        GitLabCi ci = GitLabCi.from(
                Map.of(
                        "GITLAB_CI", "true",
                        "CI_SERVER_URL", "https://gitlab.example.com/",
                        "CI_PROJECT_ID", "42",
                        "CI_PIPELINE_URL", "https://gitlab.example.com/p/-/pipelines/9",
                        "CI_JOB_TOKEN", "job-tok",
                        "CI_MERGE_REQUEST_IID", "7"),
                k -> null);
        Assert.assertTrue(ci.isPipeline());
        Assert.assertEquals(ci.serverUrl(), "https://gitlab.example.com");
        Assert.assertEquals(ci.apiV4(), "https://gitlab.example.com/api/v4");
        Assert.assertEquals(ci.projectId(), "42");
        Assert.assertEquals(ci.mergeRequestIid(), "7");
        Assert.assertEquals(ci.token(), "job-tok");
        Assert.assertTrue(ci.isJobToken());
    }

    @Test(groups = {"unit", "gitlab"})
    @Story("PAT beats job token")
    public void from_patWinsOverJobToken() {
        GitLabCi ci = GitLabCi.from(
                Map.of("CI_JOB_TOKEN", "job-tok"),
                k -> "gitlab.token".equals(k) ? "glpat-xxx" : null);
        Assert.assertEquals(ci.token(), "glpat-xxx");
        Assert.assertFalse(ci.isJobToken());
    }

    @Test(groups = {"unit", "gitlab"})
    @Story("summary markdown")
    public void summaryMarkdown_countsAndTruncatesFailures() {
        GitLabCi ci = GitLabCi.from(
                Map.of("CI_PIPELINE_URL", "https://gitlab.example.com/p/-/pipelines/1"),
                k -> null);
        List<GitLabResult> rows = List.of(
                new GitLabResult("C", "ok", null, GitLabResult.Status.PASSED, null, 1),
                new GitLabResult("C", "bad", "12", GitLabResult.Status.FAILED, "boom", 2),
                new GitLabResult("C", "skip", null, GitLabResult.Status.SKIPPED, null, 0));
        String md = GitLabClient.buildSummaryMarkdown(rows, ci);
        Assert.assertTrue(md.contains("| Passed | 1 |"));
        Assert.assertTrue(md.contains("| Failed | 1 |"));
        Assert.assertTrue(md.contains("C#bad"));
        Assert.assertTrue(md.contains("pipelines/1"));
        Assert.assertTrue(md.contains("artifacts:reports:junit"));
    }

    @Test(groups = {"unit", "gitlab"})
    @Story("dotenv")
    public void dotenv_isGitLabSafe() {
        String env = GitLabClient.buildDotenv(List.of(
                new GitLabResult("C", "a", null, GitLabResult.Status.PASSED, null, 1),
                new GitLabResult("C", "b", null, GitLabResult.Status.FAILED, "x\ny", 1)));
        Assert.assertTrue(env.contains("GITLAB_TESTS_PASSED=1"));
        Assert.assertTrue(env.contains("GITLAB_TESTS_FAILED=1"));
        Assert.assertTrue(env.contains("GITLAB_TESTS_TOTAL=2"));
        Assert.assertFalse(env.contains("\n\n"));
        for (String line : env.trim().split("\n")) {
            Assert.assertTrue(line.matches("[A-Z0-9_]+=[0-9]+"), line);
        }
    }

    @Test(groups = {"unit", "gitlab"})
    @Story("project path encoding")
    public void encode_projectPath() {
        Assert.assertEquals(GitLabClient.encode("group/proj"), "group%2Fproj");
        Assert.assertEquals(GitLabClient.encode("42"), "42");
    }

    @Test(groups = {"unit", "gitlab"})
    @Story("issue note")
    public void issueNote_listsStatuses() {
        String note = GitLabClient.buildIssueNote("12", List.of(
                new GitLabResult("C", "m", "12", GitLabResult.Status.PASSED, null, 5)),
                GitLabCi.from(Map.of(), k -> null));
        Assert.assertTrue(note.contains("issue !12") || note.contains("!12"));
        Assert.assertTrue(note.contains("PASSED"));
        Assert.assertTrue(note.contains("C#m"));
    }

    @Test(groups = {"unit", "gitlab"})
    @Story("publish writes files without API")
    @Description("gitlab.enabled defaults false so publish only writes target files.")
    public void publish_writesSummaryWithoutCallingApi() throws Exception {
        Path dir = Path.of("target");
        Files.createDirectories(dir);
        GitLabCi ci = GitLabCi.from(Map.of(), k -> null);
        GitLabClient client = new GitLabClient(ci);
        Assert.assertFalse(client.isEnabled());
        boolean ok = client.publish(List.of(
                new GitLabResult("C", "m", null, GitLabResult.Status.PASSED, null, 1)));
        Assert.assertTrue(ok);
        Assert.assertTrue(Files.exists(dir.resolve("gitlab-summary.md")));
        Assert.assertTrue(Files.exists(dir.resolve("gitlab.env")));
        String env = Files.readString(dir.resolve("gitlab.env"));
        Assert.assertTrue(env.contains("GITLAB_TESTS_PASSED=1"));
    }
}
