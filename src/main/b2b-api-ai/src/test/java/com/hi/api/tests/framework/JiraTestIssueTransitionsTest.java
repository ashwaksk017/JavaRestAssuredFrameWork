package com.hi.api.tests.framework;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.testng.Assert;
import org.testng.annotations.AfterMethod;
import org.testng.annotations.Test;

import com.hi.api.xray.JiraTestIssueTransitions;
import com.hi.api.xray.XrayClient;
import com.hi.api.xray.XrayResult;

public class JiraTestIssueTransitionsTest {

    private static final class Recorder implements XrayClient.Transport {
        final List<Object> bodies = new ArrayList<>();

        @Override
        public XrayClient.HttpReply post(String url, Map<String, String> h, Object body) {
            bodies.add(body);
            return new XrayClient.HttpReply(204, "");
        }
    }

    private final List<String> touched = new ArrayList<>();

    private void set(String k, String v) {
        touched.add(k);
        System.setProperty(k, v);
    }

    @AfterMethod(alwaysRun = true)
    public void clearProps() {
        for (String k : touched) {
            System.clearProperty(k);
        }
        touched.clear();
    }

    @Test(groups = {"guards"})
    public void onlyB2bWorkflowTransitionNamesAreAllowed() {
        Assert.assertTrue(JiraTestIssueTransitions.isAllowedTransitionName("Ready"));
        Assert.assertTrue(JiraTestIssueTransitions.isAllowedTransitionName("failed testing"));
        Assert.assertEquals(JiraTestIssueTransitions.canonicalTransitionName("done"), "Done");
        Assert.assertNull(JiraTestIssueTransitions.canonicalTransitionName("Automated"));
    }

    @Test(groups = {"guards"})
    public void afterImportUsesConfiguredPassTransitionOnlyWhenAllowed() {
        set("xray.issueTransition.pass", "Ready");
        set("xray.issueTransition.fail", "Not A Real Status");

        Recorder rec = new Recorder();
        List<XrayResult> results = List.of(
                new XrayResult("B2B-99771", XrayResult.Status.PASSED, null, 1L));

        JiraTestIssueTransitions.applyAfterImport(
                results, XrayClient.Flavour.SERVER, "https://jira.example.com", "pat", rec);

        Assert.assertEquals(rec.bodies.size(), 1);
        @SuppressWarnings("unchecked")
        Map<String, Object> payload = (Map<String, Object>) rec.bodies.get(0);
        @SuppressWarnings("unchecked")
        Map<String, Object> transition = (Map<String, Object>) payload.get("transition");
        Assert.assertEquals(transition.get("name"), "Ready");
    }

    @Test(groups = {"guards"})
    public void failTransitionUsesFailedTestingWhenConfigured() {
        set("xray.issueTransition.fail", "Failed Testing");

        Recorder rec = new Recorder();
        JiraTestIssueTransitions.applyAfterImport(
                List.of(new XrayResult("B2B-99771", XrayResult.Status.FAILED, "nope", 1L)),
                XrayClient.Flavour.SERVER, "https://jira.example.com", "pat", rec);

        @SuppressWarnings("unchecked")
        Map<String, Object> transition = (Map<String, Object>)
                ((Map<String, Object>) rec.bodies.get(0)).get("transition");
        Assert.assertEquals(transition.get("name"), "Failed Testing");
    }
}
