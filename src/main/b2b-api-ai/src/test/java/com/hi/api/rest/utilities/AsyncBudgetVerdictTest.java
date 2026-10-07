package com.hi.api.rest.utilities;

import org.testng.Assert;
import org.testng.annotations.Test;

import io.qameta.allure.Feature;
import io.qameta.allure.Story;

/**
 * The deferred-delay budget is a ONE-WAY instrument.
 *
 * <p>It exists for a record that is not visible yet: waiting lets a write
 * land. So waiting can only make a record MORE visible, never less, and
 * only the statuses that mean "not there yet" are worth spending on.</p>
 *
 * <p>Without this rule, one full run spent 222 of 242 budget claims on
 * verdicts that could not move -- ~3.3s each, roughly 800s of a 1,057s
 * run. 180 of those were a single step holding at HTTP 200 while the case
 * expected 400: no amount of waiting makes a server start rejecting a
 * resource it is happily returning.</p>
 *
 * <p>These assert the decision rather than the wall-clock, because the
 * decision is the part that was wrong.</p>
 */
@Feature("AsyncBudget")
public class AsyncBudgetVerdictTest {

    @Test(groups = {"unit", "budget"})
    @Story("A success is never worth waiting on, whatever was expected")
    public void aSuccessIsNeverWorthWaitingOn() {
        for (int code : new int[] {200, 201, 202, 204, 206, 299}) {
            Assert.assertFalse(RestStep.waitingCouldChangeTheVerdict(code),
                    "HTTP " + code + " must not spend the budget: waiting "
                    + "cannot turn a success into a rejection");
        }
    }

    @Test(groups = {"unit", "budget"})
    @Story("An auth verdict is not eventual consistency")
    public void authVerdictsDoNotWait() {
        Assert.assertFalse(RestStep.waitingCouldChangeTheVerdict(401));
        Assert.assertFalse(RestStep.waitingCouldChangeTheVerdict(403));
    }

    @Test(groups = {"unit", "budget"})
    @Story("A 400 rejects the request's own values, which a delay cannot change")
    public void badRequestDoesNotWait() {
        Assert.assertFalse(RestStep.waitingCouldChangeTheVerdict(400));
    }

    @Test(groups = {"unit", "budget"})
    @Story("404/409/412 ARE the shapes the budget was built for")
    public void notYetVisibleStillWaits() {
        for (int code : new int[] {404, 409, 412}) {
            Assert.assertTrue(RestStep.waitingCouldChangeTheVerdict(code),
                    "HTTP " + code + " is a write that may not have landed "
                    + "yet -- removing this wait would defeat the budget");
        }
    }

    @Test(groups = {"unit", "budget"})
    @Story("A server error is still worth one retry window")
    public void serverErrorsStillWait() {
        // Deliberately NOT lumped in with 400: a 502/503 is transient
        // infrastructure, not a verdict on this request's values.
        Assert.assertTrue(RestStep.waitingCouldChangeTheVerdict(500));
        Assert.assertTrue(RestStep.waitingCouldChangeTheVerdict(502));
        Assert.assertTrue(RestStep.waitingCouldChangeTheVerdict(503));
    }

    @Test(groups = {"unit", "budget"})
    @Story("Every refusal carries a reason for the log")
    public void everyRefusalExplainsItself() {
        for (int code : new int[] {200, 204, 400, 401, 403}) {
            String why = RestStep.whyWaitingCannotHelp(code);
            Assert.assertNotNull(why);
            Assert.assertNotEquals(why, "no reason recorded",
                    "HTTP " + code + " is refused, so the log line that "
                    + "explains the refusal must not be a placeholder");
        }
    }
}
