// =============================================================================
// XrayRowKeys -- resolve jira_xray_id on a data row before reporting
// -----------------------------------------------------------------------------
// The CSV column is the committed source of truth. When Create in Xray has
// run but the sheet is not updated yet, the key can live in
// program_configuration.json under:
//   jira.<jira_issue column, lower case>.<test_case_id, lower case>.jira_xray_id
// e.g. jira.b2b-99454.tc-001.jira_xray_id
//
// XrayReportListener runs before the @Test body, so config fill belongs here
// (and in TestMetadataListener for Allure labels), not only inside the test.
// =============================================================================

package com.hi.api.xray;

import java.util.Map;
import java.util.regex.Pattern;

import com.hi.api.config.Config;

public final class XrayRowKeys {

    private static final Pattern ISSUE_KEY =
            Pattern.compile("[A-Z][A-Z0-9_]*-\\d+");

    private XrayRowKeys() { }

    /** True when {@code value} is shaped like {@code B2B-8920} or {@code PROJ-101}. */
    public static boolean looksLikeIssueKey(String value) {
        return value != null && ISSUE_KEY.matcher(value.trim()).matches();
    }

    /**
     * When the row's {@code jira_xray_id} is blank, copy from config using
     * {@code jira_issue} and {@code test_case_id} on the row. Mutates {@code row}.
     */
    public static void fillFromConfig(Map<String, String> row) {
        if (row == null || !isBlank(row.get("jira_xray_id"))) {
            return;
        }
        String testCaseId = row.get("test_case_id");
        if (isBlank(testCaseId)) {
            return;
        }
        String scope = row.get("jira_issue");
        if (isBlank(scope)) {
            return;
        }
        String configKey = "jira." + scope.trim().toLowerCase() + "."
                + testCaseId.trim().toLowerCase() + ".jira_xray_id";
        String fromConfig = Config.get(configKey, "");
        if (!isBlank(fromConfig)) {
            row.put("jira_xray_id", fromConfig.trim());
        }
    }

    /** Stash a row's resolved key for {@link XrayReportListener} afterInvocation. */
    public static void registerCurrentKey(Map<String, String> row) {
        if (row == null) {
            return;
        }
        String id = row.get("jira_xray_id");
        if (looksLikeIssueKey(id)) {
            XrayResultsCollector.setCurrentKey(id.trim());
        }
    }

    private static boolean isBlank(String value) {
        return value == null || value.trim().isEmpty()
                || "null".equalsIgnoreCase(value.trim());
    }
}
