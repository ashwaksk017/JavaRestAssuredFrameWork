package com.ak.api.dsl;

import org.testng.Assert;
import org.testng.annotations.AfterMethod;
import org.testng.annotations.Test;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;

/**
 * Which suite's template index a HAND-WRITTEN test resolves against.
 *
 * <h2>Why this gap existed</h2>
 *
 * <p>{@code TemplateRegistryTest} sets {@code -Dmanual.suite} explicitly in
 * every one of its tests, so it only ever exercises the FIRST branch of
 * {@code Template.suite()}. A real manual test sets no such property: it binds
 * {@code ImportedScenario.bind(..., "manual")}, and there is no
 * {@code templates/manual/} tree -- so resolution silently depends on the
 * THIRD branch, the name derived from {@code -Dmanual.client}
 * ({@code FooClient -> foo}).</p>
 *
 * <p>That derivation was the load-bearing path for every {@code using(...)}
 * in a hand-written test and nothing covered it. A client whose derived suite
 * has no index makes every {@code using(...)} throw at runtime, which is
 * exactly the kind of failure a guard should catch first.</p>
 *
 * <p>Offline by construction: {@code suite()} catches the not-bound exception
 * and falls through, so no session, client or HTTP is needed.</p>
 */
@Epic("Manual")
@Feature("Template selection")
public class TemplateManualSuiteTest {

    private static final String CONVERTED_SUITE = "programaccountregression";
    private static final String CONVERTED_CLIENT = "ProgramaccountregressionClient";

    @AfterMethod(alwaysRun = true)
    public void clearOverrides() {
        // Both, always: these are process-wide and the guards suite shares a
        // JVM with TemplateRegistryTest / TemplateChoiceTest.
        System.clearProperty("manual.suite");
        System.clearProperty("manual.client");
    }

    @Test(groups = {"unit"})
    @Story("a manual test resolves templates via the manual.client derivation")
    @Description("""
            The path a real hand-written test actually takes: no manual.suite,
            no bound session with a template tree -- just the client name.
            Strips the `Client` suffix, lower-cases, and finds that suite's
            index.
            """)
    public void resolvesViaTheClientDerivationWhenNoSuiteIsSet() {
        System.clearProperty("manual.suite");
        System.setProperty("manual.client", CONVERTED_CLIENT);

        String body = Template.singleMemberOnboarding.resolveOrNull();

        Assert.assertNotNull(body,
                "a manual test with -Dmanual.client=" + CONVERTED_CLIENT
                + " must resolve templates from templates/" + CONVERTED_SUITE
                + "/_index.csv");
        Assert.assertFalse(body.isEmpty(), "resolved template path was empty");
    }

    @Test(groups = {"unit"})
    @Story("an explicit manual.suite still wins")
    public void explicitSuitePropertyWins() {
        System.setProperty("manual.suite", CONVERTED_SUITE);
        System.setProperty("manual.client", "NoSuchThingClient");

        Assert.assertNotNull(Template.singleMemberOnboarding.resolveOrNull(),
                "an explicit manual.suite must beat the client derivation");
    }

    @Test(groups = {"unit"})
    @Story("NEGATIVE CONTROL: neither property means a loud, actionable failure")
    @Description("""
            With no suite property, no bound session and no client to derive
            from, there is no index to read at all.

            Note the contract: resolveOrNull() returns null only when the index
            LOADS but lacks the key. A missing index throws from load() before
            any lookup happens -- which is the better behaviour, because the
            message names the missing resource and the property to set. This
            test pins that message down, since a silent null here would let a
            manual test send the wrong body instead of failing.
            """)
    public void withoutSuiteOrClientNothingResolves() {
        System.clearProperty("manual.suite");
        System.clearProperty("manual.client");

        try {
            Template.singleMemberOnboarding.resolveOrNull();
            Assert.fail("expected IllegalStateException: there is no index to read");
        } catch (IllegalStateException expected) {
            Assert.assertTrue(expected.getMessage().contains("_index.csv"),
                    "message must name the missing resource: " + expected.getMessage());
            Assert.assertTrue(expected.getMessage().contains("manual.suite"),
                    "message must name the property that fixes it: " + expected.getMessage());
        }
    }

    @Test(groups = {"unit"})
    @Story("a client whose suite was never converted does not resolve")
    @Description("This is the runtime failure the guard exists to catch early.")
    public void unconvertedClientDoesNotResolve() {
        System.clearProperty("manual.suite");
        System.setProperty("manual.client", "TotallyUnconvertedClient");

        try {
            Template.singleMemberOnboarding.resolveOrNull();
            Assert.fail("expected IllegalStateException: that suite has no index");
        } catch (IllegalStateException expected) {
            Assert.assertTrue(expected.getMessage().contains("_index.csv"),
                    "message must name the missing resource: " + expected.getMessage());
            // The critical property: it must NOT have silently resolved
            // against the one suite that does happen to be converted here.
            Assert.assertFalse(expected.getMessage().contains(CONVERTED_SUITE),
                    "must not have fallen back to the converted suite: "
                    + expected.getMessage());
        }
    }
}
