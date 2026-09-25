package com.hi.api.dsl;

import java.lang.reflect.Method;

import org.testng.Assert;
import org.testng.annotations.Test;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;

/**
 * Two ways a hand-written chain used to fail for a reason it never reported.
 *
 * <p>Both were found by auditing the manual path against the converted one:
 * the converted flow always supplies a body template and always publishes the
 * Salesforce token in a generated hook, so neither gap can occur there. The
 * DSL had no equivalent of either.</p>
 */
@Epic("Framework")
@Feature("Manual authoring gaps")
public class ManualPayloadGapsTest {

    private static String sourceOf(String relPath) throws Exception {
        java.nio.file.Path p = java.nio.file.Paths.get("src/main/java", relPath);
        Assert.assertTrue(java.nio.file.Files.exists(p), "missing source: " + p);
        return java.nio.file.Files.readString(p);
    }

    @Test(groups = {"unit", "dsl"})
    @Story("an empty-handed POST says so")
    @Description("""
            RestStep leaves body = "" when no template was set, and the body log
            line is gated behind !body.isEmpty() -- so a POST with no template
            logged a request with no payload and no explanation. The committed
            OnboardingE2ETest has neither a using(...) call nor a template_*
            CSV column, so every POST in it goes out empty.
            """)
    public void restStepWarnsWhenABodyVerbCarriesNothing() throws Exception {
        String src = sourceOf("com/hi/api/rest/utilities/RestStep.java");
        Assert.assertTrue(src.contains("warnIfEmptyHanded(verb, body, query)"),
                "execute() must call the check for every request");
        int decl = src.indexOf("private static void warnIfEmptyHanded");
        Assert.assertTrue(decl > 0, "warnIfEmptyHanded must exist");
        String body = src.substring(decl, decl + 1400);
        // body-bearing verbs only -- a GET with no body is normal
        Assert.assertTrue(body.contains("\"POST\".equals(verb)"), body);
        Assert.assertTrue(body.contains("\"PUT\".equals(verb)"), body);
        Assert.assertTrue(body.contains("\"PATCH\".equals(verb)"), body);
        // query/form params count as a payload, so a converted run does not
        // get a wall of warnings: 22 of 710 body-verb specs carry no template
        // and several of those legitimately post form parameters instead.
        Assert.assertTrue(body.contains("query != null && !query.isEmpty()"),
                "parameters must count as a payload, or converted runs get noise: " + body);
        Assert.assertTrue(body.contains("LOG.warn"), body);
    }

    @Test(groups = {"unit", "dsl"})
    @Story("prepareSalesforceAccount publishes the token it fetched")
    @Description("""
            SalesforceAuth.requestToken RETURNS the response and never touches
            ctx -- the only mention of sftokenId.GeneratedTokenID in that class
            is javadoc describing what ReadyAPI's Groovy did. So the token was
            fetched and dropped, the next two calls 401'd, and
            activateThroughHws() then threw a 'leadId' error naming guest
            enrolment, nowhere near the cause.
            """)
    public void salesforceTokenIsPublishedNotDiscarded() throws Exception {
        String src = sourceOf("com/hi/api/dsl/CustomerOnboarding.java");
        int at = src.indexOf("public CustomerOnboarding prepareSalesforceAccount()");
        Assert.assertTrue(at > 0, "prepareSalesforceAccount must exist");
        String body = src.substring(at, src.indexOf("return this;", at));
        Assert.assertTrue(body.contains("Response tokenRes = exec(\"fetchSalesforceToken\""),
                "the token response must be captured, not discarded: " + body);
        Assert.assertTrue(body.contains("safeJsonExtract(tokenRes, \"access_token\")"), body);
        Assert.assertTrue(
                body.contains("sc.put(ScenarioContext.SALESFORCE_TOKEN, \"Bearer \" + sf)"),
                "must be stored Bearer-prefixed, as the converted sf-Token hook "
                + "does -- the value is used verbatim as the Authorization header: "
                + body);
        Assert.assertTrue(body.contains("LOG.warn"),
                "an empty access_token must say so rather than 401 three lines later");
        // ordering: the publish has to happen BEFORE the calls that read it
        int publish = body.indexOf("SALESFORCE_TOKEN");
        int firstUse = body.indexOf("createSalesforceAccount");
        Assert.assertTrue(publish < firstUse,
                "the token must be published before createSalesforceAccount reads it");
    }

    @Test(groups = {"unit", "dsl"})
    @Story("sfToken() reads the field prepareSalesforceAccount writes")
    public void theTokenGetterAndSetterAgreeOnTheField() throws Exception {
        String src = sourceOf("com/hi/api/dsl/CustomerOnboarding.java");
        int at = src.indexOf("private String sfToken()");
        Assert.assertTrue(at > 0);
        String getter = src.substring(at, at + 200);
        Assert.assertTrue(getter.contains("sc.salesforceToken()"), getter);
        // and that accessor must resolve the same declared field
        String ctxSrc = sourceOf("com/hi/api/context/ScenarioContext.java");
        Assert.assertTrue(
                ctxSrc.contains("salesforceToken() { return rawOf(SALESFORCE_TOKEN); }")
                || ctxSrc.contains("rawOf(SALESFORCE_TOKEN)"),
                "salesforceToken() must read SALESFORCE_TOKEN");
    }

    @Test(groups = {"unit", "dsl"})
    @Story("NEGATIVE CONTROL: the warning is not wired for GET")
    public void aGetWithNoBodyIsNotWarnedAbout() throws Exception {
        String src = sourceOf("com/hi/api/rest/utilities/RestStep.java");
        int decl = src.indexOf("private static void warnIfEmptyHanded");
        String body = src.substring(decl, decl + 1400);
        Assert.assertFalse(body.contains("\"GET\".equals(verb)"),
                "a GET carrying no body is normal and must not warn");
        Assert.assertFalse(body.contains("\"DELETE\".equals(verb)"),
                "a DELETE carrying no body is normal and must not warn");
    }

    @Test(groups = {"unit", "dsl"})
    @Story("the DSL still exposes prepareSalesforceAccount at every stage it should")
    public void prepareSalesforceAccountStaysOnTheStages() throws Exception {
        for (Class<?> stage : new Class<?>[] {
                OnboardingFlow.Start.class,
                OnboardingFlow.Enrolled.class,
                OnboardingFlow.AccountReady.class }) {
            Method m = stage.getMethod("prepareSalesforceAccount");
            Assert.assertEquals(m.getReturnType(), stage,
                    stage.getSimpleName() + ".prepareSalesforceAccount must return "
                    + stage.getSimpleName());
        }
    }
}
