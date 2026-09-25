package com.hi.api.tests.framework;

import org.testng.Assert;
import org.testng.annotations.AfterMethod;
import org.testng.annotations.Test;

import com.hi.api.rest.utilities.ResponseMasking;
import com.hi.api.rest.utilities.StepOutcomes;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;

/**
 * The digest must not say something different from what it measured.
 *
 * <h2>Why this exists</h2>
 *
 * <p>Triaging one regression run cost roughly fifteen rounds of log-grepping,
 * and two of those rounds were spent reasoning from digest columns that did
 * not mean what they said:</p>
 *
 * <ul>
 *   <li>{@code StepOutcomes} overwrote on every failure, so it held the LAST
 *       non-2xx -- while the digest printed it as "first bad call". Anyone
 *       reasoning about ordering from that column read the opposite of the
 *       truth.</li>
 *   <li>The masker covered {@code www.host} but not bare {@code host.com}, so
 *       digests pasted into issues carried real customer domains.</li>
 * </ul>
 *
 * <p>Both are cheap to assert and expensive to discover, which is exactly
 * what a guard test is for.</p>
 */
@Epic("Guards")
@Feature("Failure digest accuracy")
public class DigestDiagnosticsTest {

    @AfterMethod(alwaysRun = true)
    public void reset() {
        StepOutcomes.reset();
    }

    // -----------------------------------------------------------------
    // first-failure semantics
    // -----------------------------------------------------------------

    @Test(groups = {"unit", "guards"})
    @Story("the recorded call is the FIRST failure, not the last")
    @Description("""
            The digest labels this column "first bad call". It must actually
            be the first: the earliest non-2xx is the call that failed to mint
            the id, and everything after it is a symptom.
            """)
    public void keepsTheEarliestFailureNotTheLatest() {
        StepOutcomes.record("createAccount", 400, 200);
        StepOutcomes.record("getAccount", 404, 200);
        StepOutcomes.record("deleteAccount", 404, 204);

        Assert.assertTrue(StepOutcomes.firstFailure().startsWith("createAccount"),
                "expected the EARLIEST failure, got: " + StepOutcomes.firstFailure());
    }

    @Test(groups = {"unit", "guards"})
    @Story("a 2xx never registers as a failure")
    public void successesAreIgnored() {
        StepOutcomes.record("enrollGuest", 200, 200);
        StepOutcomes.record("createAccount", 201, 201);
        Assert.assertNull(StepOutcomes.firstFailure());
    }

    @Test(groups = {"unit", "guards"})
    @Story("NEGATIVE CONTROL: a negative test getting the status it asked for is not a failure")
    @Description("""
            A case asserting 400 that receives 400 is PASSING. Counting it
            blamed tests on calls that worked and inflated the digest totals.
            """)
    public void assertedStatusIsNotAFailure() {
        StepOutcomes.record("http_request_400_3", 400, 400);
        Assert.assertNull(StepOutcomes.firstFailure(),
                "an expected 400 must not count as an upstream failure");
    }

    @Test(groups = {"unit", "guards"})
    @Story("the body is kept for the first failure only")
    @Description("A 44-row data-driven test must not accumulate 44 bodies.")
    public void bodyBelongsToTheFirstFailure() {
        StepOutcomes.record("createAccount", 400, 200, "first body");
        StepOutcomes.record("getAccount", 404, 200, "second body");
        Assert.assertEquals(StepOutcomes.firstFailureBody(), "first body");
    }

    @Test(groups = {"unit", "guards"})
    @Story("reset clears both the call and its body")
    public void resetClearsEverything() {
        StepOutcomes.record("createAccount", 400, 200, "body");
        StepOutcomes.reset();
        Assert.assertNull(StepOutcomes.firstFailure());
        Assert.assertNull(StepOutcomes.firstFailureBody());
    }

    // -----------------------------------------------------------------
    // masking -- this repo is public
    // -----------------------------------------------------------------

    @Test(groups = {"unit", "guards"})
    @Story("BARE hostnames are masked, not just www-prefixed ones")
    @Description("""
            The gap that leaked domains: the old masker matched `www.host`
            only, so `dpptd.com` and `sa.hilton.com` passed through into
            digests that get pasted into issues.
            """)
    public void masksBareHostnames() {
        String masked = ResponseMasking.mask(
                "websiteDomain dpptd.com and sa.hilton.com and www.foliera.com");
        Assert.assertFalse(masked.contains("dpptd.com"), masked);
        Assert.assertFalse(masked.contains("sa.hilton.com"), masked);
        Assert.assertFalse(masked.contains("foliera.com"), masked);
    }

    @Test(groups = {"unit", "guards"})
    @Story("emails, ids and timestamps are masked")
    public void masksTheObviousIdentifiers() {
        String masked = ResponseMasking.mask(
                "lthfcz@dpptd.com accountId 2000488665 at 2026-09-17T14:02:19.584383Z");
        Assert.assertFalse(masked.contains("lthfcz@dpptd.com"), masked);
        Assert.assertFalse(masked.contains("2000488665"), masked);
        Assert.assertFalse(masked.contains("2026-09-17T14"), masked);
    }

    @Test(groups = {"unit", "guards"})
    @Story("the DIAGNOSTIC shape survives masking")
    @Description("""
            Masking is aggressive on purpose, but it is worthless if it also
            destroys the reason. The real rejection that took fifteen rounds
            to find must still be readable after masking.
            """)
    public void keepsTheReasonIntact() {
        String real = "{\"context\":\"PROGRAMACCOUNTS\",\"code\":997,"
                + "\"message\":\"Bad Request\",\"notifications\":[{\"code\":\"503\","
                + "\"fields\":[\"emailAddress\"],\"message\":\"Email address domain "
                + "must match an allowed domain within program account\"}]}";
        String masked = ResponseMasking.mask(real);

        Assert.assertTrue(masked.contains("emailAddress"), masked);
        Assert.assertTrue(masked.contains("must match an allowed domain"), masked);
        Assert.assertTrue(masked.contains("997"), masked);
        Assert.assertTrue(masked.contains("PROGRAMACCOUNTS"), masked);
    }

    @Test(groups = {"unit", "guards"})
    @Story("mask is null-safe")
    public void maskToleratesNull() {
        Assert.assertNull(ResponseMasking.mask(null));
    }
}
