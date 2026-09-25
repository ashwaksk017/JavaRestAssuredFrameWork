// =============================================================================
// GitLabCi -- GitLab CI/CD environment + config overlay
// -----------------------------------------------------------------------------
// Prefers GitLab-injected CI_* variables, then Config (gitlab.* / GITLAB_*).
// Safe to call outside GitLab: every field may be blank.
// =============================================================================

package com.hi.api.gitlab;

import java.util.Map;
import java.util.function.Function;

import com.hi.api.config.Config;

public final class GitLabCi {

    private final boolean pipeline;
    private final String serverUrl;
    private final String projectId;
    private final String pipelineUrl;
    private final String jobUrl;
    private final String commitSha;
    private final String refName;
    private final String mergeRequestIid;
    private final String token;
    private final boolean jobToken;

    private GitLabCi(boolean pipeline, String serverUrl, String projectId,
                     String pipelineUrl, String jobUrl, String commitSha,
                     String refName, String mergeRequestIid, String token,
                     boolean jobToken) {
        this.pipeline = pipeline;
        this.serverUrl = serverUrl;
        this.projectId = projectId;
        this.pipelineUrl = pipelineUrl;
        this.jobUrl = jobUrl;
        this.commitSha = commitSha;
        this.refName = refName;
        this.mergeRequestIid = mergeRequestIid;
        this.token = token;
        this.jobToken = jobToken;
    }

    public static GitLabCi fromEnvironment() {
        return from(System.getenv(), key -> Config.get(key, null));
    }

    public static GitLabCi from(Map<String, String> env, Function<String, String> config) {
        Map<String, String> e = env == null ? Map.of() : env;
        Function<String, String> cfg = config == null ? k -> null : config;
        boolean pipeline = "true".equalsIgnoreCase(first(e.get("GITLAB_CI")));
        String server = first(
                cfg.apply("gitlab.baseUrl"),
                e.get("CI_SERVER_URL"),
                "https://gitlab.com");
        String project = first(
                cfg.apply("gitlab.projectId"),
                e.get("CI_PROJECT_ID"));
        String pat = first(cfg.apply("gitlab.token"), e.get("GITLAB_TOKEN"));
        String jobTok = first(e.get("CI_JOB_TOKEN"));
        String token = first(pat, jobTok);
        boolean jobToken = token != null && token.equals(jobTok) && (pat == null || pat.isBlank());
        return new GitLabCi(
                pipeline,
                trimSlash(server),
                project,
                first(e.get("CI_PIPELINE_URL")),
                first(e.get("CI_JOB_URL")),
                first(e.get("CI_COMMIT_SHA"), e.get("CI_COMMIT_SHORT_SHA")),
                first(e.get("CI_COMMIT_REF_NAME")),
                first(cfg.apply("gitlab.mergeRequestIid"), e.get("CI_MERGE_REQUEST_IID")),
                token,
                jobToken);
    }

    public boolean isPipeline() { return pipeline; }
    public String serverUrl() { return serverUrl; }
    public String projectId() { return projectId; }
    public String pipelineUrl() { return pipelineUrl; }
    public String jobUrl() { return jobUrl; }
    public String commitSha() { return commitSha; }
    public String refName() { return refName; }
    public String mergeRequestIid() { return mergeRequestIid; }
    public String token() { return token; }
    public boolean isJobToken() { return jobToken; }

    public String apiV4() {
        String base = serverUrl == null || serverUrl.isBlank() ? "https://gitlab.com" : serverUrl;
        return base + "/api/v4";
    }

    static String first(String... values) {
        if (values == null) return null;
        for (String v : values) {
            if (v != null && !v.isBlank()) return v.trim();
        }
        return null;
    }

    private static String trimSlash(String url) {
        if (url == null) return null;
        return url.endsWith("/") ? url.substring(0, url.length() - 1) : url;
    }
}
