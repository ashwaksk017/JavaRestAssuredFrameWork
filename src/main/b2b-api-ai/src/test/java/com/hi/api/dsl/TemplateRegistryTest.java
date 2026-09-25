package com.hi.api.dsl;

import java.util.List;

import org.testng.Assert;
import org.testng.annotations.AfterMethod;
import org.testng.annotations.Test;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;

/**
 * Contract tests for the hand-authoring template registry.
 *
 * <p>Pure unit tests: they read the converter's exported index off the
 * classpath and make no HTTP call, so they belong in the guards suite.</p>
 *
 * <p>Lives in {@code com.hi.api.dsl} so the package-private CSV splitter
 * can be exercised directly, rather than widening that method to public
 * purely for a test.</p>
 */
@Epic("Manual")
@Feature("Template registry")
public class TemplateRegistryTest {

    private static final String SUITE = "programaccountregression";

    @AfterMethod(alwaysRun = true)
    public void clearSuiteOverride() {
        System.clearProperty("manual.suite");
    }

    @Test(groups = {"unit"})
    @Story("a named template resolves to a real body on the classpath")
    @Description("""
            Binding is by (ReadyAPI case, step), so the assertion is that a
            body EXISTS -- not that it has a particular hash. Asserting the
            hash would re-create the brittleness this registry removes.
            """)
    public void namedTemplateResolvesToARealClasspathBody() {
        System.setProperty("manual.suite", SUITE);
        String path = Template.singleMemberOnboarding.resolve();
        Assert.assertTrue(path.startsWith("templates/" + SUITE + "/"),
                "unexpected path: " + path);
        Assert.assertNotNull(
                Thread.currentThread().getContextClassLoader().getResource(path),
                "index points at a body that is not on the classpath: " + path);
    }

    @Test(groups = {"unit"})
    @Story("every curated name still resolves after a reconvert")
    @Description("Catches a ReadyAPI rename before a test sends a wrong body.")
    public void everyCuratedNameResolves() {
        System.setProperty("manual.suite", SUITE);
        Template[] all = {
            Template.singleMemberOnboarding,
            Template.amexInviteLinkAccount,
            Template.rejectedEmailDomainAccount,
            Template.registerBusinessActivation,
        };
        for (Template t : all) {
            String path = t.resolve();
            Assert.assertNotNull(
                    Thread.currentThread().getContextClassLoader().getResource(path),
                    t + " resolved to a missing body: " + path);
        }
    }

    @Test(groups = {"unit"})
    @Story("an unknown pair fails loudly instead of guessing")
    @Description("""
            A silent fallback would send a plausible-looking wrong body --
            the exact failure this layer exists to prevent. The message must
            also name the near misses so the fix is obvious.
            """)
    public void unknownPairThrowsWithNearMisses() {
        System.setProperty("manual.suite", SUITE);
        Template bogus = Template.of(
                "renamed in ReadyAPI", "NoSuchCase_999", "Reject_Account");
        try {
            bogus.resolve();
            Assert.fail("expected an unresolved template to throw");
        } catch (IllegalStateException expected) {
            String msg = String.valueOf(expected.getMessage());
            Assert.assertTrue(msg.contains("_index.csv"), msg);
            // near misses: other cases DO have a Reject_Account step
            Assert.assertTrue(msg.contains("reject_account"), msg);
        }
    }

    @Test(groups = {"unit"})
    @Story("resolveOrNull is the non-throwing variant")
    public void resolveOrNullReturnsNullForAnUnknownPair() {
        System.setProperty("manual.suite", SUITE);
        Assert.assertNull(
                Template.of("nope", "NoSuchCase_999", "NoSuchStep").resolveOrNull());
        // positive control -- the same call shape DOES find a real one
        Assert.assertNotNull(Template.singleMemberOnboarding.resolveOrNull());
    }

    @Test(groups = {"unit"})
    @Story("a comma inside a ReadyAPI case name does not shift the columns")
    @Description("""
            ReadyAPI case names contain commas. A naive split would map the
            case to the wrong template path, which is silent and wrong.
            """)
    public void csvSplitHonoursQuotedCommas() {
        List<String> cells = Template.splitCsv(
                "\"B2B-2065_create, activate\",createAccount,templates/x/y.json,CONST");
        Assert.assertEquals(cells.size(), 4, String.valueOf(cells));
        Assert.assertEquals(cells.get(0), "B2B-2065_create, activate");
        Assert.assertEquals(cells.get(2), "templates/x/y.json");
        // control: an unquoted line still splits normally
        List<String> plain = Template.splitCsv("caseA,stepB,templates/x/z.json,C2");
        Assert.assertEquals(plain.get(0), "caseA");
        Assert.assertEquals(plain.get(2), "templates/x/z.json");
    }

    @Test(groups = {"unit"})
    @Story("a doubled quote inside a quoted cell is unescaped")
    public void csvSplitUnescapesDoubledQuotes() {
        List<String> cells = Template.splitCsv("\"say \"\"hi\"\"\",step,path,C");
        Assert.assertEquals(cells.get(0), "say \"hi\"");
        Assert.assertEquals(cells.get(1), "step");
    }

    @Test(groups = {"unit"})
    @Story("the label travels with the template for readable failures")
    public void toStringCarriesLabelCaseAndStep() {
        String s = Template.singleMemberOnboarding.toString();
        Assert.assertTrue(s.contains("single member onboarding"), s);
        Assert.assertTrue(s.contains("B2B-4955"), s);
        Assert.assertTrue(s.contains("CreatePendingAccountmember"), s);
    }
}
