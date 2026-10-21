// =============================================================================
// JiraTestIssueTransitions -- optional workflow moves on Xray Test issues
// -----------------------------------------------------------------------------
// After a Server/DC execution import, some teams move the Test issue itself
// (e.g. B2B-99771) via Jira workflow transitions. B2B Accounts Test issues
// only expose these transition names:
//   Failed Testing, New, New (2), EA High Level Arch, Ready, Done,
//   EA Detailed Design
//
// Configure which to fire (by transition name, not destination status):
//   xray.issueTransition.pass  e.g. Ready
//   xray.issueTransition.fail  e.g. Failed Testing
// Leave a key empty to skip that outcome. Names outside the allowlist are
// refused and logged -- never sent to Jira.
// =============================================================================

package com.hi.api.xray;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import com.hi.api.config.Config;

public final class JiraTestIssueTransitions {

    private static final Logger LOG = LoggerFactory.getLogger(JiraTestIssueTransitions.class);

    /** Transition names on B2B Accounts Test issues (Jira workflow menu). */
    public static final Set<String> ALLOWED = Set.of(
            "Failed Testing",
            "New",
            "New (2)",
            "EA High Level Arch",
            "Ready",
            "Done",
            "EA Detailed Design");

    private static final String PASS_KEY = "xray.issueTransition.pass";
    private static final String FAIL_KEY = "xray.issueTransition.fail";

    private JiraTestIssueTransitions() { }

    public static boolean isAllowedTransitionName(String name) {
        if (name == null || name.isBlank()) {
            return false;
        }
        String trimmed = name.trim();
        for (String allowed : ALLOWED) {
            if (allowed.equalsIgnoreCase(trimmed)) {
                return true;
            }
        }
        return false;
    }

    /** Canonical spelling from {@link #ALLOWED}, or {@code null} if not allowed. */
    public static String canonicalTransitionName(String name) {
        if (name == null || name.isBlank()) {
            return null;
        }
        String trimmed = name.trim();
        for (String allowed : ALLOWED) {
            if (allowed.equalsIgnoreCase(trimmed)) {
                return allowed;
            }
        }
        return null;
    }

    /**
     * After a successful import, optionally transition each distinct test key.
     * Failures are logged and swallowed (same contract as {@link XrayClient}).
     */
    public static void applyAfterImport(List<XrayResult> results, XrayClient.Flavour flavour,
            String jiraBaseUrl, String bearerToken, XrayClient.Transport transport) {
        if (flavour != XrayClient.Flavour.SERVER || results == null || results.isEmpty()) {
            return;
        }
        if (bearerToken == null || bearerToken.isBlank() || jiraBaseUrl == null || jiraBaseUrl.isBlank()) {
            return;
        }
        String passTransition = canonicalTransitionName(Config.get(PASS_KEY, ""));
        String failTransition = canonicalTransitionName(Config.get(FAIL_KEY, ""));
        if (passTransition == null && failTransition == null) {
            return;
        }
        if (!Config.get(PASS_KEY, "").isBlank() && passTransition == null) {
            log("skipped issue transition -- " + PASS_KEY + " is not one of the B2B Test "
                    + "workflow transitions: " + ALLOWED);
        }
        if (!Config.get(FAIL_KEY, "").isBlank() && failTransition == null) {
            log("skipped issue transition -- " + FAIL_KEY + " is not one of the B2B Test "
                    + "workflow transitions: " + ALLOWED);
        }
        Map<String, XrayResult.Status> worst = new LinkedHashMap<>();
        for (XrayResult r : results) {
            if (r == null || r.testKey() == null || r.testKey().isBlank()) {
                continue;
            }
            String key = r.testKey().trim();
            worst.merge(key, r.status(), JiraTestIssueTransitions::worstOf);
        }
        String base = jiraBaseUrl.replaceAll("/+$", "");
        for (Map.Entry<String, XrayResult.Status> e : worst.entrySet()) {
            String transition = transitionFor(e.getValue(), passTransition, failTransition);
            if (transition == null) {
                continue;
            }
            try {
                String url = base + "/rest/api/2/issue/" + e.getKey() + "/transitions";
                Map<String, Object> body = Map.of("transition", Map.of("name", transition));
                Map<String, String> headers = Map.of("Authorization", "Bearer " + bearerToken.trim());
                XrayClient.HttpReply reply = transport.post(url, headers, body);
                if (reply.status() < 200 || reply.status() >= 300) {
                    log("issue transition " + e.getKey() + " -> \"" + transition + "\" HTTP "
                            + reply.status() + " body=" + reply.body());
                } else {
                    log("issue transition " + e.getKey() + " -> \"" + transition + "\" OK");
                }
            } catch (RuntimeException ex) {
                log("issue transition " + e.getKey() + " failed: " + ex.getMessage());
            }
        }
    }

    private static XrayResult.Status worstOf(XrayResult.Status a, XrayResult.Status b) {
        return rank(b) > rank(a) ? b : a;
    }

    private static int rank(XrayResult.Status s) {
        return switch (s) {
            case FAILED -> 3;
            case ABORTED -> 2;
            case SKIPPED -> 1;
            case PASSED -> 0;
        };
    }

    private static String transitionFor(XrayResult.Status status, String pass, String fail) {
        return switch (status) {
            case FAILED, ABORTED -> fail;
            case PASSED -> pass;
            case SKIPPED -> null;
        };
    }

    private static void log(String msg) {
        LOG.info("[XrayClient] {}", msg);
    }
}
