package com.ak.api.tests.framework;

import org.testng.Assert;
import org.testng.annotations.AfterMethod;
import org.testng.annotations.BeforeMethod;
import org.testng.annotations.Test;

import com.ak.api.retry.AsyncBudget;
import com.ak.api.retry.Poller;

import io.qameta.allure.Description;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;

/**
 * Locks the deferred-Delay contract.
 *
 * <p>The whole point is that a ReadyAPI Delay stops costing wall-clock time
 * unless a later step actually needs it. These tests assert both halves: the
 * time is genuinely not spent up front, and it IS still available to a step
 * several positions later.</p>
 */
@Feature("AsyncBudget")
public class AsyncBudgetTest {

    @BeforeMethod(alwaysRun = true)
    public void clear() {
        AsyncBudget.reset();
        System.clearProperty("rest.deferDelays");
    }

    @AfterMethod(alwaysRun = true)
    public void cleanup() {
        AsyncBudget.reset();
        System.clearProperty("rest.deferDelays");
    }

    @Test(groups = {"unit", "budget"})
    @Story("A delay does not sleep -- it becomes budget")
    @Description("The saving being claimed. 5s of ReadyAPI delay must cost "
            + "approximately zero wall-clock when nothing is racy.")
    public void delayDefersInsteadOfSleeping() {
        long t0 = System.currentTimeMillis();
        Poller.delay(5_000L, "Delay_1");
        long elapsed = System.currentTimeMillis() - t0;

        Assert.assertTrue(elapsed < 500,
                "delay must not sleep when deferral is on; took " + elapsed + "ms");
        Assert.assertEquals(AsyncBudget.remainingMs(), 5_000L,
                "the full duration must be available to later steps");
    }

    @Test(groups = {"unit", "budget"})
    @Story("Budgets accumulate across stacked delays")
    public void budgetsAccumulate() {
        Poller.delay(2_000L, "Delay_1");
        Poller.delay(3_000L, "Delay_2");
        Assert.assertEquals(AsyncBudget.remainingMs(), 5_000L);
    }

    @Test(groups = {"unit", "budget"})
    @Story("A later step can claim the budget")
    @Description("This is the case that made polling only the NEXT step "
            + "unsafe -- the racy step is often several positions later.")
    public void aLaterStepCanClaimIt() {
        Poller.delay(10_000L, "Delay_1");

        long first = AsyncBudget.claim(4_000L);
        Assert.assertEquals(first, 4_000L, "capped claim");
        Assert.assertEquals(AsyncBudget.remainingMs(), 6_000L, "remainder survives");

        long second = AsyncBudget.claim(60_000L);
        Assert.assertEquals(second, 6_000L, "claim cannot exceed what is left");
        Assert.assertEquals(AsyncBudget.remainingMs(), 0L);
        Assert.assertEquals(AsyncBudget.claim(1_000L), 0L, "exhausted budget yields nothing");
    }

    @Test(groups = {"unit", "budget"})
    @Story("delayStrict always sleeps")
    @Description("""
            Used where the wait guards a NEGATIVE assertion. Deferring those
            would let the test pass by checking too early, which weakens the
            assertion rather than breaking it.
            """)
    public void strictDelayActuallySleeps() {
        long t0 = System.currentTimeMillis();
        Poller.delayStrict(300L, "negative-guard");
        long elapsed = System.currentTimeMillis() - t0;

        Assert.assertTrue(elapsed >= 250,
                "delayStrict must really sleep; took " + elapsed + "ms");
        Assert.assertEquals(AsyncBudget.remainingMs(), 0L,
                "a strict delay must not also register budget");
    }

    @Test(groups = {"unit", "budget"})
    @Story("Kill switch restores the original sleep")
    public void killSwitchRestoresSleeping() {
        System.setProperty("rest.deferDelays", "false");
        long t0 = System.currentTimeMillis();
        Poller.delay(300L, "Delay_1");
        long elapsed = System.currentTimeMillis() - t0;

        Assert.assertTrue(elapsed >= 250,
                "with deferral off, delay must sleep; took " + elapsed + "ms");
        Assert.assertEquals(AsyncBudget.remainingMs(), 0L);
    }

    @Test(groups = {"unit", "budget"})
    @Story("Reset prevents budget leaking between tests")
    @Description("A leftover budget would let one scenario spend another's wait.")
    public void resetClearsBudget() {
        Poller.delay(5_000L, "Delay_1");
        Assert.assertTrue(AsyncBudget.remainingMs() > 0);
        AsyncBudget.reset();
        Assert.assertEquals(AsyncBudget.remainingMs(), 0L);
        Assert.assertFalse(AsyncBudget.hasBudget());
    }

    @Test(groups = {"unit", "budget"})
    @Story("Summary reports what was saved")
    public void summaryReportsSaving() {
        Poller.delay(5_000L, "Delay_1");
        AsyncBudget.claim(1_000L);
        AsyncBudget.spent(1_000L);
        String s = AsyncBudget.summary();
        Assert.assertTrue(s.contains("5000ms deferred"), s);
        Assert.assertTrue(s.contains("4000ms saved"), s);
    }

    @Test(groups = {"unit", "budget"})
    @Story("Zero and negative durations are no-ops")
    public void zeroDurationIsNoOp() {
        Poller.delay(0L, "noop");
        Poller.delay(-5L, "noop");
        Assert.assertEquals(AsyncBudget.remainingMs(), 0L);
    }
}
