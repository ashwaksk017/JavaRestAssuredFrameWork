package com.hi.api.tests.framework;

import java.util.Arrays;
import java.util.Map;

import org.testng.Assert;
import org.testng.annotations.Test;

import com.hi.api.support.CtxFields;
import com.hi.api.support.IdentityVocabulary;
import com.hi.api.support.ImportedScenario;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;

/**
 * The runtime reads its project vocabulary from converter_identity.json
 * (emitted by the converter from converter.config.json). With the committed
 * config that resource says exactly what the built-in defaults say, so the
 * behaviour the framework tests pin elsewhere must hold either way.
 */
@Epic("API Automation")
@Feature("Project configuration")
public class IdentityVocabularyTest {

    @Test(groups = {"unit"})
    @Story("resource and built-in defaults agree for the committed config")
    @Description("The emitted converter_identity.json is on the test classpath; every accessor must return the same values the code defaults carry.")
    public void resourceMatchesBuiltInDefaults() {
        Assert.assertEquals(IdentityVocabulary.namespace(), "Properties");
        Assert.assertEquals(IdentityVocabulary.frozenDomainKey(), "Hardcodeddomain");
        Assert.assertEquals(IdentityVocabulary.allowedDomainsKey(), "ALLOWED_DOMAINS");
        Assert.assertTrue(Arrays.asList(IdentityVocabulary.standardFields()).contains("generatedemailAddress"));
        Assert.assertEquals(IdentityVocabulary.standardFields().length, 22);
        Assert.assertTrue(IdentityVocabulary.freemailDomains().contains("gmail.com"));
        Assert.assertTrue(Arrays.asList(IdentityVocabulary.bindableDomains()).contains("Domain_3"));
        Assert.assertTrue(IdentityVocabulary.namedIdentityKeys().contains("hhonorsnumber"));
        Assert.assertEquals(IdentityVocabulary.fixtureLiteralShape("memberGuestID"), "\\d{6,}");
        Assert.assertNull(IdentityVocabulary.fixtureLiteralShape("guestId"));
        Assert.assertTrue(IdentityVocabulary.salesforceEnabled());
        Assert.assertTrue(IdentityVocabulary.salesforceIdField().matcher("sfdccontactid").matches());
        Assert.assertTrue(IdentityVocabulary.salesforceIdShape().matcher("AskjBT4C2dF0DmbNh").matches());
        Assert.assertFalse(IdentityVocabulary.salesforceIdShape().matcher("12345").matches());
    }

    @Test(groups = {"unit"})
    @Story("member-enroll step patterns, including the a+b form")
    public void memberEnrollPatterns() {
        Assert.assertTrue(IdentityVocabulary.isMemberEnrollStep("MemberHHonorsEnroll"));
        Assert.assertTrue(IdentityVocabulary.isMemberEnrollStep("MemberHHonorsEnroll 2"));
        Assert.assertTrue(IdentityVocabulary.isMemberEnrollStep("HHonorsEnroll_member"));
        Assert.assertTrue(IdentityVocabulary.isMemberEnrollStep("member-x-hhonorsenroll"));
        Assert.assertFalse(IdentityVocabulary.isMemberEnrollStep("HHonorsEnroll"));
        Assert.assertFalse(IdentityVocabulary.isMemberEnrollStep(""));
        Assert.assertEquals(ImportedScenario.isMemberEnrollStep("MemberHHonorsEnroll"), true,
                "ImportedScenario delegates to the vocabulary");
    }

    @Test(groups = {"unit"})
    @Story("the consumers read through the vocabulary")
    @Description("CtxFields.STANDARD_FIELDS, the fixture rule and the Salesforce rule are the vocabulary's, not literals.")
    public void consumersUseTheVocabulary() {
        Assert.assertEquals(CtxFields.STANDARD_FIELDS, IdentityVocabulary.standardFields());
        Map<String, String> ctx = new java.util.HashMap<>();
        CtxFields.generate(ctx, "Properties", "memberGuestID", "sfdcContactID");
        Map<String, String> row = new java.util.HashMap<>();
        row.put("Properties.memberGuestID", "1900747836");
        row.put("Properties.sfdcContactID", "12345");
        CtxFields.seedFromRow(ctx, row, "Properties.");
        Assert.assertEquals(ctx.get("Properties.memberGuestID"), "1900747836", "fixture literal wins");
        Assert.assertEquals(ctx.get("Properties.sfdcContactID"), "12345", "invalid Salesforce id literal wins");
        Assert.assertTrue(CtxFields.isCapturedSalesforceSessionKey("sftokenId.GeneratedTokenID"));
        Assert.assertFalse(CtxFields.isCapturedSalesforceSessionKey("tokenId.GeneratedTokenID"));
    }
}
