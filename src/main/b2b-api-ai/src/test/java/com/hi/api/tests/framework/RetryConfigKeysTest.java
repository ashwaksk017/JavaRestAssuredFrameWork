package com.hi.api.tests.framework;

import org.testng.Assert;
import org.testng.annotations.AfterMethod;
import org.testng.annotations.Test;

import com.hi.api.config.Config;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;

/**
 * The two retry budgets are separate knobs and must stay separate.
 *
 * <p>{@code wholeTestRetry} re-runs a whole {@code @Test} from its first
 * phase; {@code tokenRetry} re-sends ONE request after regenerating a dead
 * token. Conflating them is easy to do by accident and expensive to notice:
 * a chain that re-runs when it meant to replay one call creates a second set
 * of accounts.</p>
 */
@Epic("Framework")
@Feature("Retry configuration")
public class RetryConfigKeysTest {

    @AfterMethod(alwaysRun = true)
    public void clearProps() {
        System.clearProperty("wholeTestRetry");
        System.clearProperty("retry.maxCount");
        System.clearProperty("tokenRetry");
    }

    @Test(groups = {"unit"})
    @Story("defaults are unchanged when neither key is set")
    @Description("""
            An unset key must reproduce the behaviour these replaced: 2
            whole-test retries, and exactly one token refresh + replay. If
            these drift, every existing program_configuration.json silently
            changes meaning.
            """)
    public void defaultsMatchThePreviousHardcodedBehaviour() {
        Assert.assertEquals(Config.retryMaxCount(), 2,
                "whole-test retry default must stay 2");
        Assert.assertEquals(Config.tokenRetryCount(), 1,
                "token retry default must stay 1 -- the old hardcoded 'retry once'");
    }

    @Test(groups = {"unit"})
    @Story("wholeTestRetry drives the whole-test budget")
    public void wholeTestRetryIsRead() {
        System.setProperty("wholeTestRetry", "5");
        Assert.assertEquals(Config.retryMaxCount(), 5);
    }

    @Test(groups = {"unit"})
    @Story("the legacy retry.maxCount keeps working")
    @Description("""
            Existing configs and -D flags must not break. The new key wins
            when both are set, so a migration can land the new key first and
            remove the old one later.
            """)
    public void legacyKeyStillHonouredAndNewKeyWins() {
        System.setProperty("retry.maxCount", "7");
        Assert.assertEquals(Config.retryMaxCount(), 7,
                "retry.maxCount alone must still be honoured");
        System.setProperty("wholeTestRetry", "3");
        Assert.assertEquals(Config.retryMaxCount(), 3,
                "wholeTestRetry must win when both are set");
    }

    @Test(groups = {"unit"})
    @Story("tokenRetry drives the token replay budget")
    public void tokenRetryIsRead() {
        System.setProperty("tokenRetry", "3");
        Assert.assertEquals(Config.tokenRetryCount(), 3);
    }

    @Test(groups = {"unit"})
    @Story("tokenRetry=0 disables the replay without disabling refresh")
    @Description("""
            0 must mean "do not replay", not "fall back to the default".
            auth.tokenRefresh.enabled remains the master switch.
            """)
    public void tokenRetryZeroDisablesTheReplay() {
        System.setProperty("tokenRetry", "0");
        Assert.assertEquals(Config.tokenRetryCount(), 0);
    }

    @Test(groups = {"unit"})
    @Story("NEGATIVE CONTROL: a negative or unparseable value cannot loop forever")
    public void badValuesClampInsteadOfMisbehaving() {
        System.setProperty("tokenRetry", "-4");
        Assert.assertEquals(Config.tokenRetryCount(), 0,
                "a negative budget must clamp to 0, never loop");
        System.setProperty("tokenRetry", "not-a-number");
        Assert.assertEquals(Config.tokenRetryCount(), 1,
                "an unparseable value must fall back to the default");
    }

    @Test(groups = {"unit"})
    @Story("the two budgets are independent")
    public void oneKeyDoesNotAffectTheOther() {
        System.setProperty("wholeTestRetry", "9");
        Assert.assertEquals(Config.tokenRetryCount(), 1,
                "wholeTestRetry must not change the token budget");
        System.clearProperty("wholeTestRetry");
        System.setProperty("tokenRetry", "9");
        Assert.assertEquals(Config.retryMaxCount(), 2,
                "tokenRetry must not change the whole-test budget");
    }
}
