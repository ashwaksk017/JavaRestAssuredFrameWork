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
        // Hardcodeddomain stays frozen: B2B-3233 posts Properties.Domain as a
        // NEW email domain, so the two must not collapse into one value.
        Assert.assertEquals(ctx.get("Properties.Hardcodeddomain"), "laafd.com");
        Assert.assertTrue(ctx.get("Properties.hardcodedemail").endsWith("@laafd.com"),
                ctx.get("Properties.hardcodedemail"));
        Assert.assertNotEquals(ctx.get("Properties.Hardcodeddomain"), domain);
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

    @Test(groups = {"unit"})
    @Story("each email sits on the domain its saved value used")
    @Description("B2B-3216 owner is generatedemailAddress on Hardcodeddomain; B2B-5530 owner is Email on Domain2.")
    public void regenRandomProperties_bindsEachEmailToItsOwnSavedDomain() {
        // B2B-3216 shape: generatedemailAddress belongs on the frozen domain
        Map<String, String> ctx = new HashMap<>();
        ctx.put("Properties.Hardcodeddomain", "rteet.com");
        Map<String, String> row = new HashMap<>();
        row.put("Properties.Domain", "moubfwrs.com");
        row.put("Properties.Hardcodeddomain", "rteet.com");
        row.put("Properties.Email", "io98l@moubfwrs.com");
        row.put("Properties.generatedemailAddress", "qcpalw@rteet.com");
        ImportedScenario.regenRandomProperties(ctx, row);
        Assert.assertTrue(ctx.get("Properties.generatedemailAddress").endsWith("@rteet.com"),
                ctx.get("Properties.generatedemailAddress"));
        Assert.assertTrue(ctx.get("Properties.Email").endsWith("@" + ctx.get("Properties.Domain")),
                ctx.get("Properties.Email") + " vs " + ctx.get("Properties.Domain"));
        Assert.assertNotEquals(ctx.get("Properties.Domain"), "rteet.com",
                "the posted Domain must stay distinct from Hardcodeddomain");

        // B2B-5530 shape: Email belongs on Domain2, which is regenerated
        Map<String, String> ctx2 = new HashMap<>();
        ctx2.put("Properties.Hardcodeddomain", "laafd.com");
        ctx2.put("Properties.Domain2", "tnfgwpxv.com");
        Map<String, String> row2 = new HashMap<>();
        row2.put("Properties.Domain", "xnfjqmub.net");
        row2.put("Properties.Domain2", "tnfgwpxv.com");
        row2.put("Properties.Email", "p8lsd@tnfgwpxv.com");
        row2.put("Properties.generatedemailAddress", "soxbid@xnfjqmub.net");
        ImportedScenario.regenRandomProperties(ctx2, row2);
        String fresh2 = ctx2.get("Properties.Domain2");
        Assert.assertNotEquals(fresh2, "tnfgwpxv.com", "Domain2 is regenerated per run");
        Assert.assertTrue(ctx2.get("Properties.Email").endsWith("@" + fresh2),
                ctx2.get("Properties.Email") + " vs " + fresh2);
        Assert.assertTrue(
                ctx2.get("Properties.generatedemailAddress").endsWith("@" + ctx2.get("Properties.Domain")),
                ctx2.get("Properties.generatedemailAddress"));
    }

    @Test(groups = {"unit"})
    @Story("placeholders resolve the way ReadyAPI resolves them")
    @Description("#Properties_firstName# vs ctx Firstname, #Properties_2_websiteDomain2# vs websitedomain2 -- mid-name case.")
    public void mapJsonValues_fallsBackToACaseInsensitiveKey() throws Exception {
        Map<String, String> data = new HashMap<>();
        data.put("Properties_Firstname", "Ada");
        data.put("Properties_2_websitedomain2", "www.fresh.com");
        String body = com.ak.api.rest.utilities.RestUtilities.mapJsonValues(
                "{\"firstName\":\"#Properties_firstName#\","
                + "\"websiteDomain\":\"#Properties_2_websiteDomain2#\"}", data);
        Assert.assertTrue(body.contains("\"firstName\":\"Ada\""), body);
        Assert.assertTrue(body.contains("\"websiteDomain\":\"www.fresh.com\""), body);
        Assert.assertFalse(body.contains("null"), body);
    }

    @Test(groups = {"unit"})
    @Story("placeholders resolve the way ReadyAPI resolves them")
    @Description("An exact key still wins, and an ambiguous fold is left unresolved rather than guessed.")
    public void mapJsonValues_exactKeyWinsAndAmbiguityIsNotGuessed() throws Exception {
        Map<String, String> exact = new HashMap<>();
        exact.put("Properties_Domain", "exact.com");
        exact.put("Properties_domain", "other.com");
        String body = com.ak.api.rest.utilities.RestUtilities.mapJsonValues(
                "{\"d\":\"#Properties_Domain#\"}", exact);
        Assert.assertTrue(body.contains("exact.com"), body);
        // positive control: a differently-cased key DOES resolve, so this
        // test cannot pass merely because no fallback exists
        Map<String, String> folded = new HashMap<>();
        folded.put("Properties_Firstname", "Ada");
        String bodyCi = com.ak.api.rest.utilities.RestUtilities.mapJsonValues(
                "{\"f\":\"#Properties_firstName#\"}", folded);
        Assert.assertTrue(bodyCi.contains("Ada"), bodyCi);

        // only differently-cased keys, disagreeing -> no guess
        Map<String, String> ambiguous = new HashMap<>();
        ambiguous.put("Properties_Thing", "one");
        ambiguous.put("Properties_THING", "two");
        String body2 = com.ak.api.rest.utilities.RestUtilities.mapJsonValues(
                "{\"t\":\"#Properties_thing#\"}", ambiguous);
        Assert.assertFalse(body2.contains("one"), body2);
        Assert.assertFalse(body2.contains("two"), body2);
    }
    @Test(groups = {"unit"})
    @Story("emailDomain / Website / Domain2 generate domain shapes, not addresses or words")
    @Description("Digest 17: emailDomains: [\"user@x.com\"] (999) and websiteDomain: \"pkheba\" (553) came from name-shape generation.")
    public void emailDomain_isDomainShaped() {
        String ed = CtxFields.valueFor("emailDomain");
        Assert.assertFalse(ed.contains("@"), "emailDomain must be a domain: " + ed);
        Assert.assertTrue(ed.contains("."), ed);
        String site = CtxFields.valueFor("Website");
        Assert.assertTrue(site.startsWith("www.") && site.indexOf('.', 4) > 0, site);
        String d2 = CtxFields.valueFor("Domain2");
        Assert.assertFalse(d2.contains("@"), d2);
        Assert.assertTrue(d2.contains("."), "Domain2 must carry a TLD: " + d2);
        Assert.assertTrue(CtxFields.valueFor("RandomDomain").contains("."));

        Map<String, String> ctx = new HashMap<>();
        CtxFields.generate(ctx, "Properties", "Email", "emailDomain", "Website", "Domain2");
        String email = ctx.get("Properties.Email");
        String emailDomain = email.substring(email.indexOf('@') + 1);
        Assert.assertEquals(ctx.get("Properties.emailDomain"), emailDomain, "one domain per pack");
        Assert.assertEquals(ctx.get("Properties.Website"), "www." + emailDomain);
    }

    @Test(groups = {"unit"})
    @Story("bare-label Domain (kkzgg + x@kkzgg.com) is the row's own domain")
    @Description("B2B-2931: everything fell to Hardcodeddomain and emailDomain kept a foreign value.")
    public void regen_bareLabelDomain_keepsRowOnItsOwnFreshDomain() {
        Map<String, String> ctx = new HashMap<>();
        ctx.put("Properties.Hardcodeddomain", "laafd.com");
        ctx.put("Properties.emailDomain", "someone@elsewhere.com");
        Map<String, String> row = new HashMap<>();
        row.put("Properties.Domain", "kkzgg");
        row.put("Properties.Email", "vmn3c@kkzgg.com");
        row.put("Properties.emailDomain", "kkzgg.com");
        row.put("Properties.websiteDomain", "www.kkzgg.com");
        row.put("expected_http_request_200_1_status_code", "200");
        ImportedScenario.regenRandomProperties(ctx, row);
        String domain = ctx.get("Properties.Domain");
        Assert.assertNotEquals(domain, "laafd.com", "row used its own domain, not the frozen one");
        Assert.assertTrue(domain.contains(".") && domain.length() > 4, "full domain, not a chopped label: " + domain);
        Assert.assertTrue(ctx.get("Properties.Email").endsWith("@" + domain), ctx.get("Properties.Email"));
        Assert.assertEquals(ctx.get("Properties.emailDomain"), domain);
        Assert.assertEquals(ctx.get("Properties.websiteDomain"), domain);
    }

    @Test(groups = {"unit"})
    @Story("RandomDomain that was the identity domain follows the regenerated identity")
    @Description("B2B-8398: emailDomains: [\"${Properties#RandomDomain}\"] stayed on the CSV domain while the owner email moved -> 400/503.")
    public void regen_randomDomainEqualToIdentity_followsIt() {
        Map<String, String> ctx = new HashMap<>();
        ctx.put("Properties.Hardcodeddomain", "laafd.com");
        Map<String, String> row = new HashMap<>();
        row.put("Properties.Domain", "westinghouse.com");
        row.put("Properties.Email", "j1h0c@westinghouse.com");
        row.put("Properties.RandomDomain", "westinghouse.com");
        row.put("Properties.RandomDomain2", "competitor.com");
        row.put("expected_http_request_200_createAccount_status_code", "200");
        ImportedScenario.regenRandomProperties(ctx, row);
        String domain = ctx.get("Properties.Domain");
        Assert.assertNotEquals(domain, "westinghouse.com");
        Assert.assertEquals(ctx.get("Properties.RandomDomain"), domain, "RandomDomain was the identity domain");
        Assert.assertEquals(ctx.get("Properties.RandomDomain2"), "competitor.com", "unrelated CSV domain kept");
        Assert.assertTrue(ctx.get("Properties.Email").endsWith("@" + domain));
    }

    @Test(groups = {"unit"})
    @Story("Website follows Domain2 by the ROW's saved pairing, even if a pack pre-generated it")
    @Description("B2B-5530: createAccount2 sent websiteDomain \"pkheba\" (a generated word) with emailDomains on the fresh Domain2 -> 553.")
    public void regen_websiteFollowsSavedDomain2_notPregeneratedWord() {
        Map<String, String> ctx = new HashMap<>();
        ctx.put("Properties.Hardcodeddomain", "laafd.com");
        ctx.put("Properties.Website", "pkheba");     // what the translated pack put there
        ctx.put("Properties.Domain2", "zzyxq");      // ditto
        Map<String, String> row = new HashMap<>();
        row.put("Properties.Domain", "xnfjqmub.net");
        row.put("Properties.Domain2", "tnfgwpxv.com");
        row.put("Properties.Website", "www.tnfgwpxv.com");
        row.put("Properties.Email", "p8lsd@tnfgwpxv.com");
        row.put("Properties.generatedemailAddress", "soxbid@xnfjqmub.net");
        row.put("expected_http_request_200_createAccount2_status_code", "200");
        ImportedScenario.regenRandomProperties(ctx, row);
        String d2 = ctx.get("Properties.Domain2");
        Assert.assertTrue(d2.endsWith(".com"), "shape preserved from the row: " + d2);
        Assert.assertEquals(ctx.get("Properties.Website"), "www." + d2);
        Assert.assertTrue(ctx.get("Properties.Email").endsWith("@" + d2), ctx.get("Properties.Email"));
    }
    @Test(groups = {"unit"})
    @Story("author-literal email domain survives regen; underscore slots; Salesforce id shape")
    @Description("B2B_4473 blocklist / B2B_3934 emailDomain, B2B-7504 username_1, B2B_3778 sfdcID.")
    public void authorLiteralEmailDomain_survivesRegen() {
        Map<String, String> ctx = new HashMap<>();
        ctx.put("Properties.Hardcodeddomain", "laafd.com");
        Map<String, String> row = new HashMap<>();
        row.put("Properties.Domain", "mluifzha.org");
        row.put("Properties.Email", "blackstone.com");
        row.put("Properties.generatedemailAddress", "umzgxl@blackstone.com");
        row.put("Properties.websiteDomain", "www.blackstone.com");
        row.put("Properties.username_1", "saved1");
        row.put("Properties.generatedemailAddress_1", "saved1@x.com");
        ImportedScenario.regenRandomProperties(ctx, row);
        Assert.assertEquals(ctx.get("Properties.Email"), "blackstone.com");
        String gen = ctx.get("Properties.generatedemailAddress");
        Assert.assertTrue(gen.endsWith("@blackstone.com"), "author's domain: " + gen);
        Assert.assertNotEquals(gen, "umzgxl@blackstone.com", "fresh local part, or the owner enroll is a 409 every run");
        Assert.assertEquals(ctx.get("Properties.websiteDomain"), "www.blackstone.com");
        Assert.assertNotEquals(ctx.get("Properties.Username"), "", "the rest of the pack is still fresh");
        Assert.assertEquals(ctx.get("Properties.username_1"), ctx.get("Properties.username1"));

        String sf = CtxFields.valueFor("sfdcContactID");
        Assert.assertEquals(sf.length(), 18, sf);
        Assert.assertTrue(sf.chars().allMatch(Character::isLetterOrDigit), sf);
        Assert.assertEquals(CtxFields.valueFor("salesforceLeadId").length(), 18);
    }
    @Test(groups = {"unit"})
    @Story("Email1..3 / Email_1..3 / username_3 saved literals follow the regenerated identity")
    @Description("B2B-3503: no Properties.Email, Email1..3 on the saved Domain -> Domain fell to the frozen one while Email1 stayed (503). B2B-7505: username_3 literal -> 504 every run.")
    public void numberedEmailSlots_followIdentity() {
        Map<String, String> ctx = new HashMap<>();
        ctx.put("Properties.Hardcodeddomain", "laafd.com");
        Map<String, String> row = new HashMap<>();
        row.put("Properties.Domain", "oenpnooe.org");
        row.put("Properties.Email1", "zfcdpbyg@oenpnooe.org");
        row.put("Properties.Email2", "eosdgdws@oenpnooe.org");
        row.put("Properties.username_3", "lkfjar");
        row.put("Properties.Email_1", "1i2v2@oenpnooe.org");
        ImportedScenario.regenRandomProperties(ctx, row);
        String domain = ctx.get("Properties.Domain");
        Assert.assertNotEquals(domain, "laafd.com", "Email1 on the saved Domain means the row used its own domain");
        Assert.assertTrue(ctx.get("Properties.Email1").endsWith("@" + domain), ctx.get("Properties.Email1"));
        Assert.assertTrue(ctx.get("Properties.Email2").endsWith("@" + domain), ctx.get("Properties.Email2"));
        Assert.assertTrue(ctx.get("Properties.Email_1").endsWith("@" + domain), ctx.get("Properties.Email_1"));
        Assert.assertNotEquals(ctx.get("Properties.Email1"), ctx.get("Properties.Email2"));
        String u3 = ctx.get("Properties.username_3");
        Assert.assertTrue(u3 != null && !u3.isEmpty() && !u3.equals("lkfjar"), "username_3 regenerated: " + u3);
        Assert.assertNotEquals(u3, ctx.get("Properties.username_1"));
        Assert.assertNotEquals(u3, ctx.get("Properties.Username2"));
    }

    @Test(groups = {"unit"})
    @Story("an invalid Salesforce-id literal in the row is the author's and survives generation")
    @Description("B2B_3778 value_number / numberandspecialcharacters / morethan_20chars expect Fault 999.")
    public void invalidSalesforceIdLiteral_wins() {
        Map<String, String> ctx = new HashMap<>();
        CtxFields.generate(ctx, "Properties", "sfdcContactID", "sfdcID");
        Assert.assertEquals(ctx.get("Properties.sfdcContactID").length(), 18);
        Map<String, String> row = new HashMap<>();
        row.put("Properties.sfdcContactID", "12345");
        row.put("Properties.sfdcID", "AskjBT4C2dF0DmbNh");        // 17 alnum: plausible, generated wins
        CtxFields.seedFromRow(ctx, row, "Properties.");
        Assert.assertEquals(ctx.get("Properties.sfdcContactID"), "12345");
        Assert.assertEquals(ctx.get("Properties.sfdcID").length(), 18);
    }
    @Test(groups = {"unit"})
    @Story("every other email-/phone-shaped saved value follows what the row says")
    @Description("updateemail (51 rows) / generateEmail (37) on the identity domain move with it; amex EmailDomain/updateemail on sa.hilton.com keep the author's domain; Phone2 (95 rows) is fresh; existPhoneNo is kept.")
    public void rowShapedIdentity_followsWhatTheRowSays() {
        Map<String, String> ctx = new HashMap<>();
        ctx.put("Properties.Hardcodeddomain", "laafd.com");
        Map<String, String> row = new HashMap<>();
        row.put("Properties.Domain", "ernestpackaging.com");
        row.put("Properties.Email", "abc@ernestpackaging.com");
        row.put("Properties.updateemail", "jdqcd@ernestpackaging.com");
        row.put("Properties.generateEmail", "omdveq@ernestpackaging.com");
        row.put("Properties.Phone2", "126715693");
        row.put("Properties.newPhone", "1234567890");
        row.put("Properties.existPhoneNo", "555000111");
        row.put("expected_http_request_200_createAccount_status_code", "200");
        ImportedScenario.regenRandomProperties(ctx, row);
        String domain = ctx.get("Properties.Domain");
        Assert.assertTrue(ctx.get("Properties.updateemail").endsWith("@" + domain), ctx.get("Properties.updateemail"));
        Assert.assertTrue(ctx.get("Properties.generateEmail").endsWith("@" + domain), ctx.get("Properties.generateEmail"));
        Assert.assertNotEquals(ctx.get("Properties.updateemail"), ctx.get("Properties.generateEmail"));
        Assert.assertNotEquals(ctx.get("Properties.updateemail"), ctx.get("Properties.Email"));
        String p2 = ctx.get("Properties.Phone2");
        Assert.assertEquals(p2.length(), 9);
        Assert.assertNotEquals(p2, "126715693");
        Assert.assertEquals(ctx.get("Properties.newPhone").length(), 10);
        Assert.assertNull(ctx.get("Properties.existPhoneNo"), "author literal: regen leaves it to the row seed");

        Map<String, String> amex = new HashMap<>();
        amex.put("Properties.Domain", "www.amexoneclickuser234.com");
        amex.put("Properties.Email", "4i586@www.amexoneclickuser234.com");
        amex.put("Properties.EmailDomain", "sa.hilton.com");
        amex.put("Properties.updateemail", "rfful@sa.hilton.com/");
        Map<String, String> ctx2 = new HashMap<>();
        ImportedScenario.regenRandomProperties(ctx2, amex);
        Assert.assertEquals(ctx2.get("Properties.EmailDomain"), "sa.hilton.com", "a literal on another domain is the author's");
        Assert.assertTrue(ctx2.get("Properties.updateemail").endsWith("@sa.hilton.com/"), ctx2.get("Properties.updateemail"));
        Assert.assertNotEquals(ctx2.get("Properties.updateemail"), "rfful@sa.hilton.com/", "fresh local part");
    }
    @Test(groups = {"unit"})
    @Story("two-user pack: Email is the member's, no overlay; one-user pack still overlays")
    @Description("B2B-6851 family: member enroll on Email, 2nd on generatedemailAddress1, add-member on Email -- the overlay sent three different addresses for two people (409/509).")
    public void twoUserPack_skipsMemberOverlay() {
        Map<String, String> row = new HashMap<>();
        row.put("Properties.Email", "member@x.com");
        row.put("Properties.generatedemailAddress", "owner@x.com");   // distinct -> two users
        Map<String, String> ctx = new HashMap<>();
        ImportedScenario.regenRandomProperties(ctx, row);
        Assert.assertNotEquals(ctx.get("Properties.Email"), ctx.get("Properties.generatedemailAddress"));
        Map<String, String> memberCtx = ImportedScenario.ctxForStep(ctx, "MemberHHonorsEnroll");
        Assert.assertEquals(memberCtx.get("Properties.Email"), ctx.get("Properties.Email"), "no overlay: Email is the member");
        Assert.assertNotEquals(ctx.get("Properties.usernamemember2"), ctx.get("Properties.usernamemember"));
        Assert.assertNotEquals(ctx.get("Properties.usernamemember2"), ctx.get("Properties.Username"));

        Map<String, String> one = new HashMap<>();
        ImportedScenario.regenRandomProperties(one);                     // one user: Email == generatedemailAddress
        Map<String, String> oneMember = ImportedScenario.ctxForStep(one, "MemberHHonorsEnroll");
        Assert.assertNotEquals(oneMember.get("Properties.Email"), one.get("Properties.Email"), "one-user pack keeps the overlay");
    }

    @Test(groups = {"unit"})
    @Story("memberGuestID literal wins the seed; amex literal www. domain is kept verbatim")
    public void memberGuestId_andLiteralWwwDomain() {
        Map<String, String> ctx = new HashMap<>();
        CtxFields.generate(ctx, "Properties", "memberGuestID");
        Map<String, String> row = new HashMap<>();
        row.put("Properties.memberGuestID", "1900747836");
        CtxFields.seedFromRow(ctx, row, "Properties.");
        Assert.assertEquals(ctx.get("Properties.memberGuestID"), "1900747836");

        Map<String, String> amex = new HashMap<>();
        amex.put("Properties.Domain", "www.amexoneclickuser234.com");
        amex.put("Properties.Email", "4i586@www.amexoneclickuser234.com");
        Map<String, String> ctx2 = new HashMap<>();
        ctx2.put("Properties.Hardcodeddomain", "laafd.com");
        ImportedScenario.regenRandomProperties(ctx2, amex);
        Assert.assertEquals(ctx2.get("Properties.Domain"), "www.amexoneclickuser234.com");
        Assert.assertTrue(ctx2.get("Properties.Email").endsWith("@www.amexoneclickuser234.com"), ctx2.get("Properties.Email"));
        Assert.assertNotEquals(ctx2.get("Properties.Email"), "4i586@www.amexoneclickuser234.com", "fresh local part");
    }
    @Test(groups = {"unit"})
    @Story("Domain_1 / websitedomain_1 / generatedemailAddress_1 move together; literal-Email rows keep the owner's own domain")
    @Description("B2B-7505: createAccount sent ownerEmailAddress on the identity domain with emailDomains [Domain_1] (503). B2B-5264: owner on the saved website domain, not the frozen one.")
    public void underscoreNumberedSlots_moveTogether() {
        Map<String, String> ctx = new HashMap<>();
        ctx.put("Properties.Hardcodeddomain", "laafd.com");
        Map<String, String> row = new HashMap<>();
        row.put("Properties.Domain", "plgawaps.org");
        row.put("Properties.Email", "abc@plgawaps.org");
        row.put("Properties.Domain_1", "oobveifd.org");
        row.put("Properties.websitedomain_1", "www.oobveifd.org");
        row.put("Properties.generatedemailAddress_1", "raboum@oobveifd.org");
        row.put("Properties.Email_1", "1i2v2@oobveifd.org");
        row.put("Properties.username_1", "fvzfuc");
        ImportedScenario.regenRandomProperties(ctx, row);
        String d1 = ctx.get("Properties.Domain_1");
        Assert.assertNotNull(d1);
        Assert.assertNotEquals(d1, "oobveifd.org", "Domain_1 is a numbered slot: regenerated");
        Assert.assertNotEquals(d1, ctx.get("Properties.Domain"));
        Assert.assertEquals(ctx.get("Properties.websitedomain_1"), "www." + d1);
        Assert.assertTrue(ctx.get("Properties.generatedemailAddress_1").endsWith("@" + d1), ctx.get("Properties.generatedemailAddress_1"));
        Assert.assertTrue(ctx.get("Properties.Email_1").endsWith("@" + d1), ctx.get("Properties.Email_1"));
        Assert.assertNotEquals(ctx.get("Properties.username_1"), "fvzfuc");

        Map<String, String> social = new HashMap<>();
        social.put("Properties.Domain", "jqnbtnbc.org");
        social.put("Properties.Email", "linkedin.com");
        social.put("Properties.generatedemailAddress", "yvwlsf@test.highbook.com");
        social.put("Properties.websiteDomain", "test.highbook.com");
        Map<String, String> ctx2 = new HashMap<>();
        ctx2.put("Properties.Hardcodeddomain", "laafd.com");
        ImportedScenario.regenRandomProperties(ctx2, social);
        Assert.assertTrue(ctx2.get("Properties.generatedemailAddress").endsWith("@test.highbook.com"), ctx2.get("Properties.generatedemailAddress"));
        Assert.assertNotEquals(ctx2.get("Properties.generatedemailAddress"), "yvwlsf@test.highbook.com");
        Assert.assertEquals(ctx2.get("Properties.websiteDomain"), "test.highbook.com");
    }
}
