// =============================================================================
// GitLabClient -- GitLab REST (MR notes + test-case/issue notes)
// -----------------------------------------------------------------------------
// Never throws: GitLab outages must not fail the automation suite.
// JUnit XML for the MR "Test summary" widget is uploaded by .gitlab-ci.yml;
// this client adds optional API sync when gitlab.enabled=true + a token.
// =============================================================================

package com.hi.api.gitlab;

import static io.restassured.RestAssured.given;

import java.io.IOException;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import com.hi.api.config.Config;

import io.restassured.http.ContentType;
import io.restassured.response.Response;

public final class GitLabClient {

    private final GitLabCi ci;
    private final boolean enabled;
    private final boolean mergeRequestNote;
    private final boolean issueNotes;

    public GitLabClient() {
        this(GitLabCi.fromEnvironment());
    }

    public GitLabClient(GitLabCi ci) {
        this.ci = ci;
        this.enabled = "true".equalsIgnoreCase(Config.get("gitlab.enabled", "false"));
        this.mergeRequestNote = !"false".equalsIgnoreCase(
                Config.get("gitlab.mergeRequestNote", "true"));
        this.issueNotes = !"false".equalsIgnoreCase(
                Config.get("gitlab.issueNotes", "true"));
    }

    public boolean isEnabled() {
        return enabled
                && ci.token() != null && !ci.token().isBlank()
                && ci.projectId() != null && !ci.projectId().isBlank();
    }

    /**
     * Write local summary files, then optionally POST to GitLab.
     * Returns true when every attempted API call returned 2xx (or none ran).
     */
    public boolean publish(List<GitLabResult> results) {
        Path target = Path.of(Config.get("gitlab.summaryDir", "target"));
        try {
            Files.createDirectories(target);
            Files.writeString(target.resolve("gitlab-summary.md"),
                    buildSummaryMarkdown(results, ci));
            Files.writeString(target.resolve("gitlab.env"),
                    buildDotenv(results));
        } catch (IOException e) {
            log("could not write summary files: " + e.getMessage());
        }

        if (!isEnabled()) {
            log("API skipped -- gitlab.enabled=false or missing token/projectId");
            return true;
        }
        if (results == null) results = List.of();

        boolean ok = true;
        if (mergeRequestNote && ci.mergeRequestIid() != null) {
            ok &= postNote("merge_requests/" + ci.mergeRequestIid() + "/notes",
                    buildSummaryMarkdown(results, ci));
        }
        if (issueNotes) {
            Map<String, List<GitLabResult>> byIid = new LinkedHashMap<>();
            for (GitLabResult r : results) {
                if (!r.hasTestCase()) continue;
                byIid.computeIfAbsent(r.testCaseIid(), k -> new ArrayList<>()).add(r);
            }
            for (Map.Entry<String, List<GitLabResult>> e : byIid.entrySet()) {
                ok &= postNote("issues/" + e.getKey() + "/notes",
                        buildIssueNote(e.getKey(), e.getValue(), ci));
            }
        }
        return ok;
    }

    public static String buildSummaryMarkdown(List<GitLabResult> results, GitLabCi ci) {
        int passed = 0, failed = 0, skipped = 0, aborted = 0;
        List<GitLabResult> failures = new ArrayList<>();
        if (results != null) {
            for (GitLabResult r : results) {
                switch (r.status()) {
                    case PASSED -> passed++;
                    case FAILED -> { failed++; failures.add(r); }
                    case SKIPPED -> skipped++;
                    default -> aborted++;
                }
            }
        }
        int total = passed + failed + skipped + aborted;
        StringBuilder sb = new StringBuilder();
        sb.append("## API automation (Rest Assured / TestNG)\n\n");
        sb.append("| Result | Count |\n|---|---:|\n");
        sb.append("| Passed | ").append(passed).append(" |\n");
        sb.append("| Failed | ").append(failed).append(" |\n");
        sb.append("| Skipped | ").append(skipped).append(" |\n");
        sb.append("| Other | ").append(aborted).append(" |\n");
        sb.append("| **Total** | ").append(total).append(" |\n\n");
        if (ci != null) {
            if (ci.pipelineUrl() != null) {
                sb.append("- Pipeline: ").append(ci.pipelineUrl()).append('\n');
            }
            if (ci.jobUrl() != null) {
                sb.append("- Job: ").append(ci.jobUrl()).append('\n');
            }
            if (ci.commitSha() != null) {
                sb.append("- Commit: `").append(ci.commitSha()).append("`\n");
            }
            if (ci.refName() != null) {
                sb.append("- Ref: `").append(ci.refName()).append("`\n");
            }
            sb.append('\n');
        }
        if (!failures.isEmpty()) {
            sb.append("### Failures\n\n");
            int n = 0;
            for (GitLabResult r : failures) {
                if (n++ >= 15) {
                    sb.append("- … ").append(failures.size() - 15).append(" more\n");
                    break;
                }
                sb.append("- `").append(r.className()).append("#")
                        .append(r.methodName()).append("`");
                if (r.comment() != null && !r.comment().isBlank()) {
                    sb.append(" — ").append(r.comment().replace('\n', ' '));
                }
                sb.append('\n');
            }
        }
        sb.append("\nJUnit XML is attached on the pipeline **Tests** tab ")
                .append("(see `.gitlab-ci.yml` `artifacts:reports:junit`).\n");
        return sb.toString();
    }

    public static String buildIssueNote(String iid, List<GitLabResult> rows, GitLabCi ci) {
        StringBuilder sb = new StringBuilder();
        sb.append("Automated run for GitLab test case / issue !").append(iid);
        if (ci != null && ci.pipelineUrl() != null) {
            sb.append(" — ").append(ci.pipelineUrl());
        }
        sb.append('\n');
        for (GitLabResult r : rows) {
            sb.append("- ").append(r.status()).append(" `")
                    .append(r.className()).append('#').append(r.methodName())
                    .append("`\n");
        }
        return sb.toString();
    }

    public static String buildDotenv(List<GitLabResult> results) {
        int passed = 0, failed = 0, skipped = 0, aborted = 0;
        if (results != null) {
            for (GitLabResult r : results) {
                switch (r.status()) {
                    case PASSED -> passed++;
                    case FAILED -> failed++;
                    case SKIPPED -> skipped++;
                    default -> aborted++;
                }
            }
        }
        return "GITLAB_TESTS_PASSED=" + passed + "\n"
                + "GITLAB_TESTS_FAILED=" + failed + "\n"
                + "GITLAB_TESTS_SKIPPED=" + skipped + "\n"
                + "GITLAB_TESTS_OTHER=" + aborted + "\n"
                + "GITLAB_TESTS_TOTAL=" + (passed + failed + skipped + aborted) + "\n";
    }

    private boolean postNote(String resourcePath, String bodyMarkdown) {
        String url = ci.apiV4() + "/projects/" + encode(ci.projectId()) + "/" + resourcePath;
        Map<String, String> payload = new LinkedHashMap<>();
        payload.put("body", bodyMarkdown);
        try {
            var spec = given()
                    .relaxedHTTPSValidation()
                    .contentType(ContentType.JSON)
                    .body(payload);
            if (ci.isJobToken()) {
                spec = spec.header("JOB-TOKEN", ci.token());
            } else {
                spec = spec.header("PRIVATE-TOKEN", ci.token());
            }
            Response res = spec.when().post(url);
            int code = res.statusCode();
            log("POST " + resourcePath + " -> HTTP " + code
                    + (code >= 200 && code < 300 ? " OK" : " body=" + res.body().asString()));
            return code >= 200 && code < 300;
        } catch (RuntimeException e) {
            log("POST " + resourcePath + " failed: " + e.getMessage());
            return false;
        }
    }

    public static String encode(String projectId) {
        if (projectId == null) return "";
        return URLEncoder.encode(projectId, StandardCharsets.UTF_8).replace("+", "%20");
    }

    private static void log(String msg) {
        System.out.println("[GitLabClient] " + msg);
    }
}
