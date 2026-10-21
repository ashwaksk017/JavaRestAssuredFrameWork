package com.hi.api.tests.framework;

import java.util.LinkedHashMap;
import java.util.Map;

import org.testng.Assert;
import org.testng.annotations.Test;

import com.hi.api.xray.XrayRowKeys;

public class XrayRowKeysTest {

    @Test(groups = {"guards"})
    public void fillFromConfigUsesJiraIssueAndTestCaseIdColumns() {
        Map<String, String> row = new LinkedHashMap<>();
        row.put("test_case_id", "TC-001");
        row.put("jira_issue", "B2B-99454");
        row.put("jira_xray_id", "");

        String prior = System.getProperty("jira.b2b-99454.tc-001.jira_xray_id");
        try {
            System.setProperty("jira.b2b-99454.tc-001.jira_xray_id", "B2B-99771");
            XrayRowKeys.fillFromConfig(row);
            Assert.assertEquals(row.get("jira_xray_id"), "B2B-99771");
        } finally {
            if (prior == null) {
                System.clearProperty("jira.b2b-99454.tc-001.jira_xray_id");
            } else {
                System.setProperty("jira.b2b-99454.tc-001.jira_xray_id", prior);
            }
        }
    }

    @Test(groups = {"guards"})
    public void fillFromConfigDoesNotOverwriteExistingRowKey() {
        Map<String, String> row = new LinkedHashMap<>();
        row.put("test_case_id", "TC-001");
        row.put("jira_issue", "B2B-99454");
        row.put("jira_xray_id", "B2B-9999");

        String prior = System.getProperty("jira.b2b-99454.tc-001.jira_xray_id");
        try {
            System.setProperty("jira.b2b-99454.tc-001.jira_xray_id", "B2B-99771");
            XrayRowKeys.fillFromConfig(row);
            Assert.assertEquals(row.get("jira_xray_id"), "B2B-9999");
        } finally {
            if (prior == null) {
                System.clearProperty("jira.b2b-99454.tc-001.jira_xray_id");
            } else {
                System.setProperty("jira.b2b-99454.tc-001.jira_xray_id", prior);
            }
        }
    }
}
