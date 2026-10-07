package com.hi.api.tests.framework;

import org.testng.Assert;
import org.testng.annotations.Test;

import com.hi.api.reporting.XrayReportListener;

/**
 * A result is published only against something shaped like an issue key.
 *
 * <p>The column that drives the sync is not guaranteed to hold one.
 * Measured on this tree: 1,318 of 1,322 generated rows carry a
 * {@code jira_xray_id} of {@code B2B-8920_LTA_get_accountBookingMetrics_
 * monthToDateBookingMetrics_200} -- the ReadyAPI case name. The ticket is
 * in the sibling {@code jira_issue} column.</p>
 *
 * <p>So the interesting direction here is the REJECTIONS. Publishing a
 * case name as a test key puts results against keys Xray cannot resolve,
 * and because Xray reports on the import rather than the rows, the run
 * still looks like it synced.</p>
 */
public class XrayKeyGuardTest {

    @Test(groups = {"guards"})
    public void realIssueKeysAreAccepted() {
        for (String k : new String[]{"B2B-8920", "PROJ-101", "AB-1",
                                     "A1-99999", "MY_PROJ-7", " B2B-1 "}) {
            Assert.assertTrue(XrayReportListener.looksLikeIssueKey(k), k);
        }
    }

    @Test(groups = {"guards"})
    public void aReadyApiCaseNameIsRejected() {
        // The exact value in the tree, and the whole reason for the guard.
        Assert.assertFalse(XrayReportListener.looksLikeIssueKey(
                "B2B-8920_LTA_get_accountBookingMetrics_monthToDateBookingMetrics_200"));
    }

    @Test(groups = {"guards"})
    public void otherNonKeysAreRejected() {
        for (String k : new String[]{null, "", "   ", "B2B", "8920",
                                     "b2b-8920", "B2B-", "-1",
                                     "B2B-8920 extra", "B2B-8920_200"}) {
            Assert.assertFalse(XrayReportListener.looksLikeIssueKey(k),
                    String.valueOf(k));
        }
    }

    @Test(groups = {"guards"})
    public void lowerCaseIsRejectedBecauseJiraKeysAreUpperCase() {
        // Not normalised on purpose: silently upper-casing someone's typo
        // would publish against a key they did not name.
        Assert.assertFalse(XrayReportListener.looksLikeIssueKey("proj-101"));
    }
}
