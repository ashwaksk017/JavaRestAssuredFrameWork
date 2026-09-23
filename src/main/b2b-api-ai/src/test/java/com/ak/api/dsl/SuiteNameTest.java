package com.ak.api.dsl;

import java.util.LinkedHashMap;

import org.testng.Assert;
import org.testng.annotations.AfterMethod;
import org.testng.annotations.Test;

import com.ak.api.support.ImportedScenario;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;

/**
 * A hand-written test finds its own suite with NO command-line flag.
 *
 * <h2>The bug this pins</h2>
 *
 * <p>A manual test binds the literal {@code "manual"}, which is not a generated
 * package. {@link Template} and {@link ManualCleanup} both worked around that by
 * deriving the suite from {@code -Dmanual.client}, so that flag was load-bearing:
 * without it templates failed to resolve, and cleanup resolved nothing at all --
 * silently, at WARN, while the test still passed. A green run therefore left real
 * rows behind.</p>
 *
 * <p>The session already holds the client the test constructed, and its type
 * names the suite exactly. These cases pin that path, which needs no flag.</p>
 *
 * <h2>Scope</h2>
 *
 * <p>{@code ManualCleanup.afterEachTest} is deliberately NOT invoked here with a
 * converted suite bound: it would reach the generated {@code SuiteCleanup} and
 * run DELETEs, and this suite is contractually offline. The shared derivation is
 * asserted directly, and {@link Template} exercises it end to end without a
 * database. {@code ManualAuthTest} covers the fail-soft behaviour of cleanup.</p>
 */
@Epic("Manual")
@Feature("Suite selection")
public class SuiteNameTest {

    /**
     * Stands in for the generated client. Only {@code getSimpleName()} is read,
     * so this needs no behaviour -- and naming the real generated type here
     * would couple a committed test to one converted XML.
     */
    static final class ProgramaccountregressionClient {
    }

    /** A client whose suite was never converted. */
    static final class TotallyUnconvertedClient {
    }

    @AfterMethod(alwaysRun = true)
    public void clearState() {
        // Process-wide, and this suite shares a JVM with the other dsl guards:
        // a leaked binding would change how they resolve.
        System.clearProperty("manual.suite");
        System.clearProperty("manual.client");
        ImportedScenario.unbind();
    }

    private static void bindClient(Object client) {
        ImportedScenario.bind(client, new LinkedHashMap<>(), null, null, "manual");
    }

    // -----------------------------------------------------------------
    // The derivation
    // -----------------------------------------------------------------

    @Test(groups = {"unit"})
    @Story("FooClient maps to foo")
    public void stripsTheClientSuffixAndLowerCases() {
        Assert.assertEquals(SuiteName.fromClientSimpleName("FooClient"), "foo");
        Assert.assertEquals(
                SuiteName.fromClientSimpleName("ProgramaccountregressionClient"),
                "programaccountregression");
    }

    @Test(groups = {"unit"})
    @Story("a fully-qualified -Dmanual.client still derives the right suite")
    @Description("""
            CreateTestCase.md tells authors to point manual.client at
            com.ak.api.rest.manual.client.ManualClient, because a bare name
            resolves under rest.clients and the scaffold is not there. That
            string ends in `Client`, so stripping only the suffix produced the
            suite `com.ak.api.rest.manual.client.manual` -- no SuiteCleanup
            matched it and a real run reported "Rows created by this test are
            NOT being deleted" while everything else passed.
            """)
    public void aFullyQualifiedClientNameDerivesItsSimpleSuite() {
        Assert.assertEquals(
                SuiteName.fromClientSimpleName(
                        "com.ak.api.rest.manual.client.ManualClient"),
                "manual",
                "the package must be stripped before the Client suffix");
        // A generated client's package, but a made-up suite: check_generic
        // forbids committed code from naming a client that only exists after
        // converting one particular XML, and a string literal trips its grep
        // just as a type reference would.
        Assert.assertEquals(
                SuiteName.fromClientSimpleName(
                        "com.ak.api.rest.clients." + "FooClient"),
                "foo");
        // a simple name keeps working -- the other caller passes getSimpleName()
        Assert.assertEquals(SuiteName.fromClientSimpleName("ManualClient"), "manual");
        // still not a client name, package or no package
        Assert.assertNull(SuiteName.fromClientSimpleName("com.ak.api.rest.Foo"));
        Assert.assertNull(SuiteName.fromClientSimpleName("com.ak.api.rest.Client"));
    }

    @Test(groups = {"unit"})
    @Story("NEGATIVE CONTROL: names that are not client names yield null")
    @Description("A bare Client must not derive the empty string: that would be "
            + "offered as a candidate suite and looked up as a SuiteCleanup in "
            + "the empty package.")
    public void nonClientNamesDeriveNothing() {
        Assert.assertNull(SuiteName.fromClientSimpleName("Client"),
                "a bare Client must not derive an empty suite name");
        Assert.assertNull(SuiteName.fromClientSimpleName("Foo"));
        Assert.assertNull(SuiteName.fromClientSimpleName(null));
    }

    // -----------------------------------------------------------------
    // Reading the bound session
    // -----------------------------------------------------------------

    @Test(groups = {"unit"})
    @Story("the suite comes from the client the test actually bound")
    public void derivesTheSuiteFromTheBoundClient() {
        bindClient(new ProgramaccountregressionClient());

        Assert.assertEquals(SuiteName.ofBoundClient(), "programaccountregression",
                "the bound client names its own suite; no property was set");
    }

    @Test(groups = {"unit"})
    @Story("nothing bound means no opinion, not a guess")
    @Description("Resolution must never be invented from an absent binding -- "
            + "the callers fall through to their own defaults instead.")
    public void unboundDerivesNothing() {
        ImportedScenario.unbind();

        Assert.assertNull(SuiteName.ofBoundClient());
    }

    @Test(groups = {"unit"})
    @Story("a session bound without a client derives nothing")
    public void boundWithoutAClientDerivesNothing() {
        bindClient(null);

        Assert.assertNull(SuiteName.ofBoundClient());
    }

    // -----------------------------------------------------------------
    // End to end, through Template
    // -----------------------------------------------------------------

    @Test(groups = {"unit"})
    @Story("THE FIX: a manual test resolves templates with no properties set")
    @Description("The exact scenario that used to fail: no manual.suite, no "
            + "manual.client, just a bound client. Before this, suite() fell "
            + "through to the bound name manual, there is no templates/manual/ "
            + "tree, and load() threw.")
    public void resolvesTemplatesFromTheBoundClientWithNoFlags() {
        System.clearProperty("manual.suite");
        System.clearProperty("manual.client");
        bindClient(new ProgramaccountregressionClient());

        String body = Template.singleMemberOnboarding.resolveOrNull();

        Assert.assertNotNull(body,
                "a bound client must resolve its own templates without manual.client");
        Assert.assertFalse(body.isEmpty(), "resolved template path was empty");
    }

    @Test(groups = {"unit"})
    @Story("NEGATIVE CONTROL: an unconverted bound client does not fall back")
    @Description("The safety property. With several suites converted, silently "
            + "resolving against whichever one happens to be present would write "
            + "one suite's body and let another suite's SuiteCleanup delete rows "
            + "this test never created.")
    public void anUnconvertedBoundClientDoesNotFallBack() {
        System.clearProperty("manual.suite");
        System.clearProperty("manual.client");
        bindClient(new TotallyUnconvertedClient());

        try {
            Template.singleMemberOnboarding.resolveOrNull();
            Assert.fail("expected IllegalStateException: that suite has no index");
        } catch (IllegalStateException expected) {
            Assert.assertFalse(
                    expected.getMessage().contains("programaccountregression"),
                    "must not have fallen back to the converted suite: "
                    + expected.getMessage());
        }
    }

    @Test(groups = {"unit"})
    @Story("an explicit manual.suite still beats the bound client")
    public void explicitSuitePropertyStillWins() {
        bindClient(new TotallyUnconvertedClient());
        System.setProperty("manual.suite", "programaccountregression");

        Assert.assertNotNull(Template.singleMemberOnboarding.resolveOrNull(),
                "an explicit manual.suite must beat the bound-client derivation");
    }
}
