package com.ak.api.meta;

import java.util.Locale;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Jira / Xray / partner / account-type fields derived from the CSV row
 * and method/class name so Allure and reports can filter without
 * renaming B2B* classes.
 */
public final class TestMetadata {

    private static final Pattern JIRA = Pattern.compile("(B2B[-_]?\\d+)", Pattern.CASE_INSENSITIVE);

    public final String testCaseId;
    public final String xrayId;
    public final String jiraIssue;
    public final String partner;
    public final String accountType;
    public final String accountStatus;
    public final String summary;

    public TestMetadata(String testCaseId, String xrayId, String jiraIssue,
                        String partner, String accountType, String accountStatus,
                        String summary) {
        this.testCaseId = nz(testCaseId);
        this.xrayId = nz(xrayId);
        this.jiraIssue = nz(jiraIssue);
        this.partner = nz(partner);
        this.accountType = nz(accountType);
        this.accountStatus = nz(accountStatus);
        this.summary = nz(summary);
    }

    public static TestMetadata infer(Map<String, String> row, String methodName,
                                     String className) {
        String blob = join(row, methodName, className);
        String testCaseId = first(row, "test_case_id");
        String xrayId = first(row, "jira_xray_id", "xray_key", "xray_id");
        String jiraIssue = first(row, "jira_issue", "jira_key", "jira");
        if (jiraIssue.isEmpty()) {
            jiraIssue = firstJira(first(row, "test_case_id"), methodName, className);
        }
        String partner = first(row, "partner", "partner_name", "client_name");
        if (partner.isEmpty()) {
            partner = inferPartner(blob);
        }
        String accountType = first(row, "account_type", "accountType");
        if (accountType.isEmpty()) {
            accountType = partner;
        }
        String accountStatus = first(row, "account_status", "accountStatus");
        if (accountStatus.isEmpty()) {
            accountStatus = inferStatus(blob);
        }
        String summary = first(row, "description", "summary");
        return new TestMetadata(testCaseId, xrayId, jiraIssue, partner,
                accountType, accountStatus, summary);
    }

    static String inferPartner(String blob) {
        String n = blob.toLowerCase(Locale.ROOT);
        if (n.contains("amex")) {
            return "amex";
        }
        if (n.contains("silhouette")) {
            return "silhouette";
        }
        if (n.contains("h4l")) {
            return "h4l";
        }
        if (n.contains("h4b")) {
            return "h4b";
        }
        if (n.contains("lta") || n.contains("travel") || n.contains("_ta_")) {
            return "lta";
        }
        if (n.contains("smb")) {
            return "smb";
        }
        return "";
    }

    static String inferStatus(String blob) {
        String n = blob.toLowerCase(Locale.ROOT);
        if (n.contains("rejected") || n.contains("reject")) {
            return "rejected";
        }
        if (n.contains("pending")) {
            return "pending";
        }
        if (n.contains("limited")) {
            return "limited";
        }
        if (n.contains("active")) {
            return "active";
        }
        return "";
    }

    private static String firstJira(String... parts) {
        for (String p : parts) {
            if (p == null) {
                continue;
            }
            Matcher m = JIRA.matcher(p);
            if (m.find()) {
                return m.group(1).toUpperCase(Locale.ROOT).replace('_', '-');
            }
        }
        return "";
    }

    private static String first(Map<String, String> row, String... keys) {
        if (row == null) {
            return "";
        }
        for (String k : keys) {
            String v = row.get(k);
            if (v != null && !v.isBlank()) {
                return v.trim();
            }
        }
        return "";
    }

    private static String join(Map<String, String> row, String method, String cls) {
        StringBuilder sb = new StringBuilder();
        if (cls != null) {
            sb.append(cls).append(' ');
        }
        if (method != null) {
            sb.append(method).append(' ');
        }
        if (row != null) {
            sb.append(first(row, "test_case_id")).append(' ');
            sb.append(first(row, "description")).append(' ');
            sb.append(first(row, "jira_xray_id"));
        }
        return sb.toString();
    }

    private static String nz(String s) {
        return s == null ? "" : s;
    }
}
