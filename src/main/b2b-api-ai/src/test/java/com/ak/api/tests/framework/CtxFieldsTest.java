package com.ak.api.tests.framework;


import com.ak.api.tests.ImportedTest;
import java.util.HashMap;
import java.util.Map;

import org.testng.Assert;
import org.testng.annotations.Test;

import com.ak.api.config.Config;
import com.ak.api.support.CtxFields;
import com.ak.api.support.ImportedScenario;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;

@Epic("API Automation")
@Feature("Readable-test utilities")
public class CtxFieldsTest {

    @Test(groups = {"unit"})
    @Story("Dual-case ctx writes")
    @Description("putBothCases writes Properties.Email and Properties.email to the same value.")
    public void putBothCases_writesFlippedFirstLetter() {
        Map<String, String> ctx = new HashMap<>();
        CtxFields.putBothCases(ctx, "Properties", "Email", "a@example.com");
        Assert.assertEquals(ctx.get("Properties.Email"), "a@example.com");
        Assert.assertEquals(ctx.get("Properties.email"), "a@example.com");

        CtxFields.putBothCases(ctx, "Properties.guestID", "190012345");
        Assert.assertEquals(ctx.get("Properties.guestID"), "190012345");
        Assert.assertEquals(ctx.get("Properties.GuestID"), "190012345");
    }

    @Test(groups = {"unit"})
    @Story("Name-shape generators")
    @Description("valueFor matches groovy_translator shapes: phone/id digits, domain .com, email @ALLOWED_DOMAIN.")
    public void valueFor_matchesConverterShapes() {
        String phone = CtxFields.valueFor("Phone");
        Assert.assertEquals(phone.length(), 9, "phone is 9 digits");
        Assert.assertTrue(phone.chars().allMatch(Character::isDigit), phone);

        String hhonors = CtxFields.valueFor("hhonorsNumber");
        Assert.assertEquals(hhonors.length(), 9);
        Assert.assertTrue(hhonors.chars().allMatch(Character::isDigit), hhonors);

        String guestId = CtxFields.valueFor("guestID");
        Assert.assertEquals(guestId.length(), 9);
        Assert.assertTrue(guestId.chars().allMatch(Character::isDigit), guestId);
        Assert.assertFalse(guestId.startsWith("0"), "id must be a valid JSON number: " + guestId);

        String accountId = CtxFields.valueFor("accountId");
        Assert.assertEquals(accountId.length(), 9);
        Assert.assertTrue(accountId.chars().allMatch(Character::isDigit), accountId);

        String domain = CtxFields.valueFor("Domain");
        Assert.assertTrue(domain.endsWith(".com"), domain);
        Assert.assertEquals(domain.length(), 10, "6-char username + .com");

        String email = CtxFields.valueFor("Email");
        String domainPart = Config.get("ALLOWED_DOMAIN", "example.com");
        Assert.assertTrue(email.contains("@"), email);
        Assert.assertTrue(email.endsWith("@" + domainPart), email);

        String customerId = CtxFields.valueFor("customerId");
        Assert.assertTrue(customerId.matches("[a-z]{6}"),
                "customerId is a username, not 9 digits: " + customerId);

        String username = CtxFields.valueFor("Username");
        Assert.assertTrue(username.matches("[a-z]{6}"), username);
    }

    @Test(groups = {"unit"})
    @Story("generate writes both casings, unique per field")
    @Description("generate collapses Email/email so both casings share one value; Phone is a different value.")
    public void generate_uniquePerField_dualCase() {
        Map<String, String> ctx = new HashMap<>();
        CtxFields.generate(ctx, "Properties", "Email", "email", "Phone");
        Assert.assertEquals(ctx.get("Properties.Email"), ctx.get("Properties.email"));
        Assert.assertNotNull(ctx.get("Properties.Email"));
        Assert.assertNotEquals(ctx.get("Properties.Email"), ctx.get("Properties.Phone"));
        Assert.assertEquals(ctx.get("Properties.Phone"), ctx.get("Properties.phone"));
    }

    @Test(groups = {"unit"})
    @Story("generate shares one domain across emails and website")
    @Description("Owner/member emails use the same domain as websiteDomain so create-member matches the account allowlist.")
    public void generate_emailsShareWebsiteDomain() {
        Map<String, String> ctx = new HashMap<>();
        CtxFields.generateStandard(ctx, "Properties", "generatedemailAddress1");
        String domain = ctx.get("Properties.websiteDomain");
        Assert.assertNotNull(domain);
        Assert.assertTrue(domain.endsWith(".com"), domain);
        Assert.assertEquals(ctx.get("Properties.Domain"), domain);
        Assert.assertTrue(ctx.get("Properties.Email").endsWith("@" + domain),
                ctx.get("Properties.Email"));
        Assert.assertTrue(ctx.get("Properties.EmailMember").endsWith("@" + domain),
                ctx.get("Properties.EmailMember"));
        Assert.assertTrue(ctx.get("Properties.generatedemailAddress").endsWith("@" + domain),
                ctx.get("Properties.generatedemailAddress"));
        Assert.assertTrue(ctx.get("Properties.generatedemailAddress1").endsWith("@" + domain),
                ctx.get("Properties.generatedemailAddress1"));
        Assert.assertNotEquals(ctx.get("Properties.Email"), ctx.get("Properties.EmailMember"));
        Assert.assertFalse(ctx.get("Properties.guestID").startsWith("0"));
    }

    @Test(groups = {"unit"})
    @Story("regen pack keeps owner and member distinct")
    @Description("regenRandomProperties writes distinct usernames/emails on one domain; a second call is skipped by RestStep via _identityPackReady.")
    public void regenRandomProperties_distinctOwnerAndMember() {
        Map<String, String> ctx = new HashMap<>();
        ImportedScenario.regenRandomProperties(ctx);
        String domain = ctx.get("Properties.websiteDomain");
        Assert.assertNotNull(domain);
        Assert.assertNotEquals(ctx.get("Properties.Username"), ctx.get("Properties.usernamemember"));
        Assert.assertNotEquals(ctx.get("Properties.Email"), ctx.get("Properties.EmailMember"));
        Assert.assertEquals(ctx.get("Properties.generatedemailAddress"), ctx.get("Properties.Email"));
        Assert.assertEquals(ctx.get("Properties.generatedemailAddress1"), ctx.get("Properties.EmailMember"));
        Assert.assertTrue(ctx.get("Properties.Email").endsWith("@" + domain));
        Assert.assertTrue(ctx.get("Properties.generatedemailAddress1").endsWith("@" + domain));
    }

    @Test(groups = {"unit"})
    @Story("CSV freemail Domain survives identity regen")
    @Description("B2B-6238 Properties.Domain=yahoo.com after DataGen must not become Hardcodeddomain laafd.com.")
    public void regenRandomProperties_keepsCsvFreemailDomain() {
        Map<String, String> ctx = new HashMap<>();
        ctx.put("Properties.Hardcodeddomain", "laafd.com");
        Map<String, String> row = new HashMap<>();
        row.put("Properties.Domain", "yahoo.com");
        ImportedScenario.regenRandomProperties(ctx, row);
        Assert.assertEquals(ctx.get("Properties.Domain"), "yahoo.com");
        Assert.assertEquals(ctx.get("Properties.websiteDomain"), "yahoo.com");
        Assert.assertTrue(ctx.get("Properties.Email").endsWith("@yahoo.com"),
                ctx.get("Properties.Email"));
        Assert.assertTrue(ctx.get("Properties.generatedemailAddress1").endsWith("@yahoo.com"),
                ctx.get("Properties.generatedemailAddress1"));
        Assert.assertFalse(ctx.get("Properties.Email").contains("laafd.com"));
    }

    @Test(groups = {"unit"})
    @Story("CSV competitor Domain survives identity regen on create-400")
    @Description("B2B-6324 Properties.Domain=wyn.com must not become Hardcodeddomain when create expects 400.")
    public void regenRandomProperties_keepsCsvDomainWhenCreateExpects400() {
        Map<String, String> ctx = new HashMap<>();
        ctx.put("Properties.Hardcodeddomain", "laafd.com");
        Map<String, String> row = new HashMap<>();
        row.put("Properties.Domain", "wyn.com");
        row.put("expected_http_request_400_status_code", "400");
        row.put("Properties.RandomDomain2", "omnihotels.com");
        ImportedScenario.regenRandomProperties(ctx, row);
        Assert.assertEquals(ctx.get("Properties.Domain"), "wyn.com");
        Assert.assertTrue(ctx.get("Properties.Email").endsWith("@wyn.com"),
                ctx.get("Properties.Email"));
        Assert.assertEquals(ctx.get("Properties.RandomDomain2"), "omnihotels.com");
        Assert.assertFalse(ctx.get("Properties.Email").contains("laafd.com"));
    }

    @Test(groups = {"unit"})
    @Story("Happy-path CSV Domain still uses frozen allowlist")
    public void regenRandomProperties_frozenDomainWhenCreateExpects200() {
        Map<String, String> ctx = new HashMap<>();
        ctx.put("Properties.Hardcodeddomain", "laafd.com");
        Map<String, String> row = new HashMap<>();
        row.put("Properties.Domain", "explorer.de");
        row.put("expected_http_request_200_createAccount_status_code", "200");
        ImportedScenario.regenRandomProperties(ctx, row);
        Assert.assertEquals(ctx.get("Properties.Domain"), "laafd.com");
    }

    @Test(groups = {"unit"})
    @Story("member enroll overlays Email to member slot")
    @Description("B2B-7816 MemberHHonorsEnroll uses ${Properties#Email}; overlay must not mutate session ctx.")
    public void ctxForStep_memberEnrollOverlaysEmail_ownerEnrollDoesNot() {
        Map<String, String> ctx = new HashMap<>();
        ImportedScenario.regenRandomProperties(ctx);
        String owner = ctx.get("Properties.Email");
        String member = ctx.get("Properties.generatedemailAddress1");
        Assert.assertNotEquals(owner, member);

        Map<String, String> memberCtx = ImportedScenario.ctxForStep(
                ctx, "MemberHHonorsEnroll");
        Assert.assertEquals(memberCtx.get("Properties.Email"), member);
        Assert.assertEquals(memberCtx.get("Properties.email"), member);
        Assert.assertEquals(ctx.get("Properties.Email"), owner,
                "session ctx must keep owner email for create-account");

        Map<String, String> ownerCtx = ImportedScenario.ctxForStep(
                ctx, "HHonorsEnroll");
        Assert.assertSame(ownerCtx, ctx);
        Assert.assertEquals(ownerCtx.get("Properties.Email"), owner);
        Assert.assertTrue(ImportedScenario.isMemberEnrollStep("MemberHHonorsEnroll"));
        Assert.assertFalse(ImportedScenario.isMemberEnrollStep("HHonorsEnroll"));
    }

    @Test(groups = {"unit"})
    @Story("guestId and guestID are distinct keys")
    @Description("generateStandard writes both Properties.guestId and Properties.guestID; enroll uses #Properties_guestID#.")
    public void generateStandard_writesIdAndIDSeparately() {
        Map<String, String> ctx = new HashMap<>();
        CtxFields.generateStandard(ctx, "Properties");
        Assert.assertNotNull(ctx.get("Properties.guestId"), "guestId");
        Assert.assertNotNull(ctx.get("Properties.guestID"), "guestID");
        Assert.assertEquals(ctx.get("Properties.guestId").length(), 9);
        Assert.assertEquals(ctx.get("Properties.guestID").length(), 9);
        Assert.assertTrue(ctx.get("Properties.guestId").chars().allMatch(Character::isDigit));
        Assert.assertTrue(ctx.get("Properties.guestID").chars().allMatch(Character::isDigit));
        Assert.assertEquals(ctx.get("Properties.GuestId"), ctx.get("Properties.guestId"));
        Assert.assertEquals(ctx.get("Properties.GuestID"), ctx.get("Properties.guestID"));
        Assert.assertNotNull(ctx.get("Properties.accountId"));
        Assert.assertNotNull(ctx.get("Properties.accountID"));
        Assert.assertNotNull(ctx.get("Properties.memberId"));
        Assert.assertNotNull(ctx.get("Properties.memberID"));
        Assert.assertNotNull(ctx.get("Properties.partnerAccountId"));
        Assert.assertNotNull(ctx.get("Properties.partnerAccountID"));
        Assert.assertEquals(ctx.get("Properties.Email"), ctx.get("Properties.email"));
        Assert.assertFalse(ctx.containsKey("Properties.name"),
                "generateStandard alone does not write script-only name");
    }

    @Test(groups = {"unit"})
    @Story("B2B-9098 extras plus ALWAYS")
    @Description("generateStandard extras write name/Firstname; ALWAYS still writes guestID.")
    public void generateStandard_withB2b9098Extras() {
        Map<String, String> ctx = new HashMap<>();
        CtxFields.generateStandard(ctx, "Properties", CtxFields.B2B9098_EXTRA_FIELDS);
        Assert.assertNotNull(ctx.get("Properties.name"));
        Assert.assertNotNull(ctx.get("Properties.Firstname"));
        Assert.assertNotNull(ctx.get("Properties.customerId"));
        Assert.assertTrue(ctx.get("Properties.customerId").matches("[a-z]{6}"));
        Assert.assertNotNull(ctx.get("Properties.guestID"));
        Assert.assertNotNull(ctx.get("Properties.websitedomain"));
        Assert.assertNotNull(ctx.get("Properties.websiteDomain"));
    }

    @Test(groups = {"unit"})
    @Story("seedFromRow putIfAbsent")
    @Description("CSV seed does not overwrite a value already generated into ctx.")
    public void seedFromRow_doesNotOverwriteGenerated() {
        Map<String, String> ctx = new HashMap<>();
        CtxFields.generate(ctx, "Properties", "Email", "Domain");
        String generatedEmail = ctx.get("Properties.Email");
        String generatedDomain = ctx.get("Properties.Domain");

        Map<String, String> row = new HashMap<>();
        row.put("Properties.Email", "csv-override@example.com");
        row.put("Properties.Domain", "csv-domain.com");
        row.put("Properties.Phone", "555123456");
        row.put("other.column", "ignored");
        CtxFields.seedFromRow(ctx, row, "Properties.");

        Assert.assertEquals(ctx.get("Properties.Email"), generatedEmail);
        Assert.assertEquals(ctx.get("Properties.Domain"), generatedDomain);
        Assert.assertEquals(ctx.get("Properties.Phone"), "555123456");
        Assert.assertFalse(ctx.containsKey("other.column"));
    }

    @Test(groups = {"unit"})
    @Story("seedFromRow uses testData defaults")
    @Description("Ungenerated Hardcodeddomain is seeded from test_data_defaults JSON when the CSV omits the column.")
    public void seedFromRow_appliesJsonDefaultForHardcodeddomain() {
        Map<String, String> ctx = new HashMap<>();
        Map<String, String> row = new HashMap<>();
        CtxFields.seedFromRow(ctx, row, "Properties.");
        String expected = ImportedScenario.testData(row, "Properties.Hardcodeddomain");
        Assert.assertFalse(expected.isEmpty(), "suite default for Hardcodeddomain");
        Assert.assertEquals(ctx.get("Properties.Hardcodeddomain"), expected);
    }

    @Test(groups = {"unit"})
    @Story("ImportedTest.begin preamble")
    @Description("begin clears ctx but keeps accessToken, then returns the resolved row.")
    public void begin_keepsAccessTokenAndClearsOtherKeys() {
        Map<String, String> ctx = new HashMap<>();
        ctx.put("accessToken", "tok-1");
        ctx.put("Properties.Email", "stale@example.com");
        Map<String, String> row = new HashMap<>();
        row.put("testCaseId", "B2B-9098");
        Map<String, String> resolved = ImportedTest.begin(ctx, row, "B2B-9098");
        Assert.assertEquals(ctx.get("accessToken"), "tok-1");
        Assert.assertFalse(ctx.containsKey("Properties.Email"));
        Assert.assertEquals(resolved.get("testCaseId"), "B2B-9098");
    }

    @Test(groups = {"unit"})
    @Story("Member TOTP JDBC id overwrites owner id")
    @Description("putIfNonEmpty is putIfAbsent (owner TOTP pins hiltonmemberid). putExtracted overwrites so member JDBC uses the pending member id.")
    public void putExtracted_overwritesHiltonmemberid() {
        Map<String, String> ctx = new HashMap<>();
        ImportedScenario.putIfNonEmpty(ctx, "hiltonmemberid", "341850");
        ImportedScenario.putIfNonEmpty(ctx, "hiltonmemberid", "341851");
        Assert.assertEquals(ctx.get("hiltonmemberid"), "341850",
                "putIfNonEmpty must not clobber the first JDBC id");
        ImportedScenario.putExtracted(ctx, "hiltonmemberid", "341851");
        Assert.assertEquals(ctx.get("hiltonmemberid"), "341851");
        ImportedScenario.putExtracted(ctx, "hiltonmemberid", "");
        Assert.assertEquals(ctx.get("hiltonmemberid"), "341851",
                "empty extract must not plant blank and break alias-walk");
    }

    @Test(groups = {"unit"})
    @Story("ctxGet matches ReadyAPI's case-insensitive property names")
    @Description("B2B-3216 reads PropertiesGuestId#guestID; the step holds guestId. Must not fall to random Properties.guestID.")
    public void ctxGet_sameKeyDifferentCaseBeatsRandomAlias() {
        Map<String, String> ctx = new java.util.LinkedHashMap<>();
        ctx.put("Properties.guestID", "111111119");
        ctx.put("PropertiesGuestId.guestId", "1901026572");
        Assert.assertEquals(ImportedScenario.ctxGet(ctx, "PropertiesGuestId.guestID"), "1901026572");
        ctx.put("PropertiesGuestId.GUESTID", "999");
        Assert.assertNotEquals(ImportedScenario.ctxGet(ctx, "PropertiesGuestId.guestID"), "999",
                "two case variants with different values must not be guessed");
    }

    @Test(groups = {"unit"})
    @Story("frozen domain follows the row")
    @Description("B2B-5530/3233: saved identity on its own domain -> fresh domain, not the shared Hardcodeddomain.")
    public void regenRandomProperties_unfreezesWhenRowUsedItsOwnDomain() {
        Map<String, String> ctx = new HashMap<>();
        ctx.put("Properties.Hardcodeddomain", "laafd.com");
        Map<String, String> row = new HashMap<>();
        row.put("Properties.Domain", "xnfjqmub.net");
        row.put("Properties.generatedemailAddress", "soxbid@xnfjqmub.net");
        row.put("Properties.Email", "p8lsd@tnfgwpxv.com");
        ImportedScenario.regenRandomProperties(ctx, row);
        String domain = ctx.get("Properties.Domain");
        Assert.assertNotEquals(domain, "laafd.com");
        Assert.assertNotEquals(domain, "xnfjqmub.net", "must be fresh, not the stale saved domain");
        Assert.assertTrue(domain.endsWith(".net"), domain);
        Assert.assertTrue(ctx.get("Properties.Email").endsWith("@" + domain), ctx.get("Properties.Email"));
        // Hardcodeddomain follows the identity: 11 templates put it in
        // emailDomains next to an owner email built from Email /
        // generatedemailAddress, and the server rejects the mismatch (997/503).
        Assert.assertEquals(ctx.get("Properties.Hardcodeddomain"), domain);
        Assert.assertTrue(ctx.get("Properties.hardcodedemail").endsWith("@" + domain),
                ctx.get("Properties.hardcodedemail"));
        Assert.assertTrue(ctx.get("Properties.updatedemail").endsWith("@" + domain),
                ctx.get("Properties.updatedemail"));
    }

    @Test(groups = {"unit"})
    @Story("frozen domain follows the row")
    @Description("Saved emails on Hardcodeddomain, or no saved emails -> the freeze stays.")
    public void regenRandomProperties_keepsFreezeWhenRowUsedHardcodeddomain() {
        Map<String, String> ctx = new HashMap<>();
        ctx.put("Properties.Hardcodeddomain", "laafd.com");
        Map<String, String> row = new HashMap<>();
        row.put("Properties.Domain", "abcxyz.net");
        row.put("Properties.generatedemailAddress", "nzbiay@laafd.com");
        ImportedScenario.regenRandomProperties(ctx, row);
        Assert.assertEquals(ctx.get("Properties.Domain"), "laafd.com");
        // A frozen row is unchanged: the identity domain IS Hardcodeddomain.
        Assert.assertEquals(ctx.get("Properties.Hardcodeddomain"), "laafd.com");
        Assert.assertTrue(ctx.get("Properties.hardcodedemail").endsWith("@laafd.com"),
                ctx.get("Properties.hardcodedemail"));

        Map<String, String> ctx2 = new HashMap<>();
        ctx2.put("Properties.Hardcodeddomain", "laafd.com");
        ImportedScenario.regenRandomProperties(ctx2, null);
        Assert.assertEquals(ctx2.get("Properties.Domain"), "laafd.com");
    }

    @Test(groups = {"unit"})
    @Story("frozen domain follows the row")
    @Description("B2B-4913: a saved hardcodedemail on Hardcodeddomain means the case needs that managed domain -- keep the freeze.")
    public void regenRandomProperties_keepsFreezeWhenRowIsBuiltOnHardcodedemail() {
        Map<String, String> ctx = new HashMap<>();
        ctx.put("Properties.Hardcodeddomain", "explorer.de");
        Map<String, String> row = new HashMap<>();
        row.put("Properties.Domain", "6r0zxv4mrb3.net");
        row.put("Properties.Email", "l4w50@6r0zxv4mrb3.net");
        row.put("Properties.generatedemailAddress", "edetx@6r0zxv4mrb3.net");
        row.put("Properties.hardcodedemail", "btmjkfm4@explorer.de");
        ImportedScenario.regenRandomProperties(ctx, row);
        Assert.assertEquals(ctx.get("Properties.Domain"), "explorer.de");
        Assert.assertEquals(ctx.get("Properties.Hardcodeddomain"), "explorer.de");
        Assert.assertTrue(ctx.get("Properties.hardcodedemail").endsWith("@explorer.de"),
                ctx.get("Properties.hardcodedemail"));
        Assert.assertTrue(ctx.get("Properties.Email").endsWith("@explorer.de"),
                ctx.get("Properties.Email"));
    }

    @Test(groups = {"unit"})
    @Story("address fields generate API-valid shapes")
    @Description("postalCode/state/city/addressLine1/country reach contactInfo.address; a word-shaped value is a 400.")
    public void valueFor_addressFieldsMatchReadyApiShapes() {
        Assert.assertTrue(CtxFields.valueFor("postalCode").matches("\\d{5}"),
                CtxFields.valueFor("postalCode"));
        Assert.assertTrue(CtxFields.valueFor("postalCode2").matches("\\d{5}"),
                CtxFields.valueFor("postalCode2"));
        Assert.assertTrue(CtxFields.valueFor("state").matches("[A-Z]{2}"),
                CtxFields.valueFor("state"));
        Assert.assertTrue(CtxFields.valueFor("city").startsWith("City_"),
                CtxFields.valueFor("city"));
        Assert.assertTrue(CtxFields.valueFor("addressLine1").startsWith("Address_"),
                CtxFields.valueFor("addressLine1"));
        Assert.assertTrue(CtxFields.valueFor("addressLine1").endsWith("Blvd"),
                CtxFields.valueFor("addressLine1"));
        Assert.assertEquals(CtxFields.valueFor("country"), "US");
        // numbered variants the suite actually uses
        Assert.assertEquals(CtxFields.valueFor("country2"), "US");
        Assert.assertTrue(CtxFields.valueFor("state2").matches("[A-Z]{2}"),
                CtxFields.valueFor("state2"));
        Assert.assertTrue(CtxFields.valueFor("city2").startsWith("City_"),
                CtxFields.valueFor("city2"));
        Assert.assertTrue(CtxFields.valueFor("addressLine1_2").startsWith("Address_"),
                CtxFields.valueFor("addressLine1_2"));
        // unchanged shapes
        Assert.assertTrue(CtxFields.valueFor("Phone").matches("\\d{9}"),
                CtxFields.valueFor("Phone"));
        Assert.assertTrue(CtxFields.valueFor("guestId").matches("\\d{9}"),
                CtxFields.valueFor("guestId"));
        Assert.assertTrue(CtxFields.valueFor("Email").contains("@"),
                CtxFields.valueFor("Email"));
    }

    @Test(groups = {"unit"})
    @Story("numbered domains are generated per run")
    @Description("B2B-5530/3553: DomainN came from the CSV, a domain the author's run already registered.")
    public void regenRandomProperties_regeneratesNumberedDomainsKeepingTheirShape() {
        Map<String, String> ctx = new HashMap<>();
        ctx.put("Properties.Domain1", "8-8h6f6t.com");
        ctx.put("Properties.Domain2", "a1.y7cycf.org");
        ctx.put("Properties.Domain3", "88lhtzqo.in");
        ctx.put("Properties.Domain4", "sugywt.co.uk");
        ctx.put("Properties.Website", "www.a1.y7cycf.org");
        ImportedScenario.regenRandomProperties(ctx, new HashMap<>());

        Assert.assertNotEquals(ctx.get("Properties.Domain1"), "8-8h6f6t.com");
        Assert.assertTrue(ctx.get("Properties.Domain1").startsWith("8-"),
                ctx.get("Properties.Domain1"));
        Assert.assertTrue(ctx.get("Properties.Domain1").endsWith(".com"),
                ctx.get("Properties.Domain1"));
        Assert.assertTrue(ctx.get("Properties.Domain2").startsWith("a1."),
                ctx.get("Properties.Domain2"));
        Assert.assertTrue(ctx.get("Properties.Domain2").endsWith(".org"),
                ctx.get("Properties.Domain2"));
        Assert.assertTrue(ctx.get("Properties.Domain3").endsWith(".in"),
                ctx.get("Properties.Domain3"));
        Assert.assertTrue(ctx.get("Properties.Domain4").endsWith(".co.uk"),
                ctx.get("Properties.Domain4"));
        // Website tracks Domain2 -- the account's websiteDomain must match
        Assert.assertEquals(ctx.get("Properties.Website"),
                "www." + ctx.get("Properties.Domain2"));
    }

    @Test(groups = {"unit"})
    @Story("numbered domains are generated per run")
    @Description("A freemail DomainN, or a create-400 row, keeps the authored value.")
    public void regenRandomProperties_leavesNegativeNumberedDomainsAlone() {
        Map<String, String> ctx = new HashMap<>();
        ctx.put("Properties.Domain2", "yahoo.com");
        // positive control in the same call: a normal DomainN MUST be
        // regenerated, so this test fails if regeneration stops happening
        // rather than passing by doing nothing.
        ctx.put("Properties.Domain3", "88lhtzqo.in");
        ImportedScenario.regenRandomProperties(ctx, new HashMap<>());
        Assert.assertEquals(ctx.get("Properties.Domain2"), "yahoo.com");
        Assert.assertNotEquals(ctx.get("Properties.Domain3"), "88lhtzqo.in");
        Assert.assertTrue(ctx.get("Properties.Domain3").endsWith(".in"),
                ctx.get("Properties.Domain3"));

        Map<String, String> ctx2 = new HashMap<>();
        ctx2.put("Properties.Domain2", "dpxhlczh.com");
        Map<String, String> row = new HashMap<>();
        row.put("expected_http_request_400_status_code", "400");
        ImportedScenario.regenRandomProperties(ctx2, row);
        Assert.assertEquals(ctx2.get("Properties.Domain2"), "dpxhlczh.com");
    }
}
