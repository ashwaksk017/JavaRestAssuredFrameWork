package com.hi.api.tests.framework;

import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;

import org.testng.Assert;
import org.testng.annotations.AfterMethod;
import org.testng.annotations.Test;

import com.hi.api.auth.TokenCache;
import com.hi.api.data.PlaceholderResolver;
import com.hi.api.meta.TestMetadata;
import com.hi.api.rest.ApiRoutes;
import com.hi.api.retry.Poller;
import com.hi.api.rest.utilities.RestUtilities;
import com.hi.api.rest.utilities.SalesforceAuth;
import com.hi.api.support.CtxFields;
import com.hi.api.support.ImportedScenario;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;
import io.restassured.response.Response;

@Epic("API Automation")
@Feature("Partial-gap runtime")
public class FrameworkPartialGapsTest {

    @AfterMethod(alwaysRun = true)
    public void reset() {
        TokenCache.clear();
        System.clearProperty("auth.tokenCache.enabled");
        System.clearProperty("rest.failFastBrokenPath");
        ImportedScenario.unbind();
    }

    @Test(groups = {"unit"})
    @Story("ApiRoutes.fill substitutes path params")
    public void apiRoutes_fill_replacesSlots() {
        Assert.assertEquals(ApiRoutes.fill(ApiRoutes.TOKEN), "/realms/applications/token");
        Assert.assertEquals(
                ApiRoutes.fill(ApiRoutes.BUSINESS, "accountId", "99"),
                "/businesses/99");
        Assert.assertEquals(
                ApiRoutes.fill("/guests/{guestId}/businesses/{accountId}",
                        "guestId", "1", "accountId", null),
                "/guests/1/businesses/");
        Assert.assertTrue(ApiRoutes.isTokenPath("/realms/applications/token"));
    }

    @Test(groups = {"unit"})
    @Story("Poller.until stops when the condition matches")
    public void poller_until_stopsOnMatch() {
        AtomicInteger n = new AtomicInteger(0);
        Integer v = Poller.until(n::incrementAndGet, i -> i >= 3, 5_000L, 1L, "count");
        Assert.assertEquals(v.intValue(), 3);
    }

    @Test(groups = {"unit"})
    @Story("TokenCache reuse for setup tokenRequest")
    public void tokenCache_reusesStoredAccessToken() {
        TokenCache.putAccessToken("abc.jwt");
        Assert.assertTrue(TokenCache.canReuseForSetup(
                "tokenRequest", 200, "/realms/applications/token"));
        Response cached = TokenCache.cachedAsResponse();
        Assert.assertEquals(cached.getStatusCode(), 200);
        Assert.assertTrue(cached.asString().contains("abc.jwt"));
        Assert.assertEquals(
                RestUtilities.safeJsonExtract(cached, "access_token"),
                "abc.jwt");
        Assert.assertFalse(TokenCache.canReuseForSetup(
                "tokenRequest", 401, "/realms/applications/token"));
    }

    @Test(groups = {"unit"})
    @Story("TokenCache applyToCtx seeds tokenId.GeneratedTokenID")
    public void tokenCache_applyToCtx_seedsBearerToken() {
        TokenCache.putAccessToken("abc.jwt");
        Map<String, String> ctx = new HashMap<>();
        TokenCache.applyToCtx(ctx);
        Assert.assertEquals(ctx.get("accessToken"), "abc.jwt");
        Assert.assertEquals(ctx.get("tokenId.GeneratedTokenID"), "Bearer abc.jwt");
        ctx.put("tokenId.GeneratedTokenID", "Bearer keep-me");
        TokenCache.applyToCtx(ctx);
        Assert.assertEquals(ctx.get("tokenId.GeneratedTokenID"), "Bearer keep-me");
    }

    @Test(groups = {"unit"})
    @Story("ctxGet falls back to TokenCache for GeneratedTokenID")
    public void ctxGet_generatedTokenId_fallsBackToTokenCache() {
        TokenCache.putAccessToken("cached.jwt");
        Map<String, String> ctx = new HashMap<>();
        Assert.assertEquals(
                ImportedScenario.ctxGet(ctx, "tokenId.GeneratedTokenID"),
                "Bearer cached.jwt");
        ctx.put("accessToken", "from-ctx.jwt");
        Assert.assertEquals(
                ImportedScenario.ctxGet(ctx, "tokenId.GeneratedTokenID"),
                "Bearer from-ctx.jwt");
    }

    @Test(groups = {"unit"})
    @Story("TestMetadata infers partner and Jira from names")
    public void testMetadata_infersPartnerAndJira() {
        Map<String, String> row = new HashMap<>();
        row.put("test_case_id", "B2B-9300_H4L_reject_account_when_owner_guest_profile_is_deleted");
        TestMetadata meta = TestMetadata.infer(row,
                "b2B9300H4LRejectAccountWhenOwnerGuestProfileIsDeletedTest",
                "B2B9300H4Test");
        Assert.assertEquals(meta.partner, "h4l");
        Assert.assertEquals(meta.jiraIssue, "B2B-9300");
        Assert.assertEquals(meta.accountStatus, "rejected");
    }

    @Test(groups = {"unit"})
    @Story("Broken trailing-slash path fails fast")
    @Description("Empty account id must not hit GET /businesses/ (206 list).")
    public void assertPathResolved_failFastTrailingSlash() {
        try {
            RestUtilities.assertPathResolved("GET", "readProgramAccount", "/businesses/");
            Assert.fail("expected IllegalStateException");
        } catch (IllegalStateException e) {
            Assert.assertTrue(e.getMessage().contains("TRAILING"), e.getMessage());
        }
    }

    @Test(groups = {"unit"})
    @Story("Expected 404 empty guest path is sent")
    @Description("B2B-5268 CSV empty guestId expects 404; ReadyAPI still hits /guests//businesses/verify.")
    public void assertPathResolved_expected404AllowsEmptyGuestSegment() {
        RestUtilities.assertPathResolved(
                "GET", "get_guestid_bussinesses_verify",
                "/guests//businesses/verify", 404);
    }

    @Test(groups = {"unit"})
    @Story("Expected 200 empty guest path still fails fast")
    public void assertPathResolved_expected200FailFastEmptyGuestSegment() {
        try {
            RestUtilities.assertPathResolved(
                    "GET", "get_guestid_bussinesses_verify",
                    "/guests//businesses/verify", 200);
            Assert.fail("expected IllegalStateException");
        } catch (IllegalStateException e) {
            Assert.assertTrue(e.getMessage().contains("EMPTY path segment"), e.getMessage());
        }
    }

    @Test(groups = {"unit"})
    @Story("Salesforce JWT form fills unresolved grant_type and assertion")
    public void salesforceAuth_fillJwtBearerForm_replacesPlaceholders() {
        // Deliberately FAILS (does not skip) when sf_config.assertion is
        // unset: a missing credential should be loud, not silently green.
        Map<String, String> form = new HashMap<>();
        form.put("grant_type", "null");
        form.put("assertion", "#salesforce_assertion#");
        SalesforceAuth.fillJwtBearerForm(form);
        Assert.assertEquals(form.get("grant_type"), SalesforceAuth.JWT_BEARER);
        String assertion = form.get("assertion");
        Assert.assertNotNull(assertion);
        // Names the key + file rather than echoing the value: on a real
        // misconfiguration the value can be credential-shaped, and the
        // message lands in surefire XML / CI logs.
        Assert.assertTrue(assertion.startsWith("eyJ"),
                "sf_config.assertion is not a JWT -- set it in "
                + "src/main/resources/program_configuration.json "
                + "(currently resolves to a " + assertion.length() + "-char "
                + "non-JWT value)");
        Assert.assertFalse(SalesforceAuth.isUnresolvedFormValue(assertion));
        Assert.assertTrue(SalesforceAuth.tokenHosts(form).stream()
                .anyMatch(h -> h.contains("test.salesforce.com")));
    }

    @Test(groups = {"unit"})
    @Story("Salesforce form placeholders vs JWT assertions")
    @Description("#placeholders and mapJsonValues 0 are unresolved; a JWT with # is not overwritten.")
    public void salesforceAuth_isUnresolvedFormValue_keepsJwtWithHash() {
        Assert.assertTrue(SalesforceAuth.isUnresolvedFormValue(null));
        Assert.assertTrue(SalesforceAuth.isUnresolvedFormValue(""));
        Assert.assertTrue(SalesforceAuth.isUnresolvedFormValue("null"));
        Assert.assertTrue(SalesforceAuth.isUnresolvedFormValue("0"));
        Assert.assertTrue(SalesforceAuth.isUnresolvedFormValue("#salesforce_assertion#"));
        Assert.assertTrue(SalesforceAuth.isUnresolvedFormValue(
                "#qry_sf_token_Request_grant_type#"));
        Assert.assertTrue(SalesforceAuth.isUnresolvedFormValue(
                "${#Project#salesforce_assertion}"));
        Assert.assertFalse(SalesforceAuth.isUnresolvedFormValue(SalesforceAuth.JWT_BEARER));
        String jwtWithHash = "eyJhbGciOiJSUzI1NiJ9.eyJhdWQiOiJodHRwczovL3Rlc3Quc2FsZXNmb3JjZS5jb20iLCJzdWIiOiJ4In0.sig#not-a-placeholder";
        Assert.assertFalse(SalesforceAuth.isUnresolvedFormValue(jwtWithHash));
        Map<String, String> form = new HashMap<>();
        form.put("grant_type", SalesforceAuth.JWT_BEARER);
        form.put("assertion", jwtWithHash);
        SalesforceAuth.fillJwtBearerForm(form);
        Assert.assertEquals(form.get("assertion"), jwtWithHash);
    }

    @Test(groups = {"unit"})
    @Story("Properties. field wins before suffix walk")
    @Description("Owner Email stays Properties.Email even when another namespaced Email was inserted first.")
    public void placeholderResolver_prefersPropertiesKeyBeforeSuffixWalk() {
        Map<String, String> ctx = new LinkedHashMap<>();
        ctx.put("Zzz.Email", "other@example.com");
        ctx.put("Properties.EmailMember", "member@example.com");
        ctx.put("Properties.Email", "owner@example.com");
        ctx.put("OtherStep.hilton-member-id", "WRONG");
        ctx.put("PropertiesaccountID.hilton-member-id", "ALSO-NOT-PROPERTIES");
        ctx.put("Properties.hilton-member-id", "349237001");
        Assert.assertEquals(
                PlaceholderResolver.resolveAll("#Properties_Email#", ctx),
                "owner@example.com");
        Assert.assertEquals(
                PlaceholderResolver.resolveAll("#Properties_EmailMember#", ctx),
                "member@example.com");
        Assert.assertEquals(
                PlaceholderResolver.resolveAll("@Properties_hilton-member-id@", ctx),
                "349237001");
        Assert.assertEquals(
                ImportedScenario.ctxGet(ctx, "Missing.Email"),
                "owner@example.com");
    }

    @Test(groups = {"unit"})
    @Story("Suffix walk uses Properties extract bags over other steps")
    @Description("When Properties.hilton-member-id is absent, PropertiesaccountID wins over OtherStep despite insertion order.")
    public void placeholderResolver_suffixWalkPrefersPropertiesExtractBag() {
        Map<String, String> ctx = new LinkedHashMap<>();
        ctx.put("OtherStep.hilton-member-id", "WRONG");
        ctx.put("PropertiesaccountID.hilton-member-id", "349237001");
        Assert.assertEquals(
                PlaceholderResolver.resolveAll("@Properties_hilton-member-id@", ctx),
                "349237001");
        Assert.assertEquals(
                ImportedScenario.ctxGet(ctx, "Properties.hilton-member-id"),
                "349237001");
    }

    @Test(groups = {"unit"})
    @Story("sftokenId.GeneratedTokenID is not seeded from captured ReadyAPI sessions")
    public void ctxFields_skipsCapturedSalesforceSessionKey() {
        Assert.assertTrue(CtxFields.isCapturedSalesforceSessionKey(
                "sftokenId.GeneratedTokenID"));
        Assert.assertFalse(CtxFields.isCapturedSalesforceSessionKey(
                "tokenId.GeneratedTokenID"));
    }

    @Test(groups = {"unit"})
    @Story("Poller.untilJsonNonEmpty waits until the path is populated")
    public void poller_untilJsonNonEmpty_waitsForSalesforceId() {
        AtomicInteger n = new AtomicInteger(0);
        Response last = Poller.untilJsonNonEmpty(() -> {
            int i = n.incrementAndGet();
            String json = i < 2
                    ? "{\"status\":\"active\"}"
                    : "{\"alternateAccounts\":{\"salesforceId\":\"001x\"}}";
            return new io.restassured.builder.ResponseBuilder()
                    .setStatusCode(200)
                    .setBody(json)
                    .build();
        }, "alternateAccounts.salesforceId", 5_000L, 1L);
        Assert.assertEquals(
                RestUtilities.safeJsonExtract(last, "alternateAccounts.salesforceId"),
                "001x");
        Assert.assertTrue(n.get() >= 2);
    }

    @Test(groups = {"unit"})
    @Story("boundCtxOr prefers the ThreadLocal session map")
    public void importedScenario_boundCtxOr_usesSession() {
        Map<String, String> classCtx = new HashMap<>();
        classCtx.put("ignored", "1");
        ImportedScenario.bind(null, classCtx, null, null, "programaccountregression");
        ImportedScenario.current().ctx.put("PropertiesaccountID.accountID", "1234567890");
        Map<String, String> used = ImportedScenario.boundCtxOr(classCtx);
        Assert.assertEquals(used.get("PropertiesaccountID.accountID"), "1234567890");
        Assert.assertNull(classCtx.get("PropertiesaccountID.accountID"));
    }

    @Test(groups = {"unit"})
    @Story("DomainApis routes client.hHonorsEnroll through GuestApi")
    public void domainApis_routesGuestCallsThroughGuestApi() {
        RecordingClient raw = new RecordingClient();
        com.hi.api.domain.DomainApis domain = com.hi.api.domain.DomainApis.bind(raw);
        Assert.assertEquals(com.hi.api.domain.DomainApis.receiverFor("hHonorsEnroll"), "guests");
        Assert.assertEquals(com.hi.api.domain.DomainApis.receiverFor("createProgramAccount"), "accounts");
        Assert.assertEquals(com.hi.api.domain.DomainApis.receiverFor("validateMemberTOTP"), "members");
        Assert.assertEquals(com.hi.api.domain.DomainApis.receiverFor("tokenRequest"), "client");

        Assert.assertSame(raw, com.hi.api.domain.DomainApis.unwrapRaw(domain.client()));
        Assert.assertSame(raw, com.hi.api.domain.DomainApis.unwrapRaw(raw));

        domain.guests().enrollHhonors("tok", "{}");
        Assert.assertTrue(raw.sawGuestApi, "direct GuestApi call must reach the raw client via GuestApi");

        raw.sawGuestApi = false;
        domain.client().hHonorsEnroll("tok", "{}");
        Assert.assertTrue(raw.sawGuestApi, "Support-style client.hHonorsEnroll must go through GuestApi");

        raw.sawGuestApi = false;
        raw.sawAccountApi = false;
        domain.client().createProgramAccount("tok", "g1", Map.of(), "{}");
        Assert.assertTrue(raw.sawAccountApi, "client.createProgramAccount must go through ProgramAccountApi");

        raw.sawMemberApi = false;
        domain.client().validateMemberTOTP("tok", "g", "a", "m", "{}");
        Assert.assertTrue(raw.sawMemberApi, "client.validateMemberTOTP must go through MemberApi");

        raw.sawGuestApi = false;
        raw.tokenHits = 0;
        domain.client().tokenRequest("tok", "{}");
        Assert.assertEquals(raw.tokenHits, 1);
        Assert.assertFalse(raw.sawGuestApi, "tokenRequest must stay on the raw client");
    }

    @Test(groups = {"unit"})
    @Story("Salesforce Account id resolves from a differently named GET extract")
    public void salesforceAuth_resolveAccountId_fromAliasedGet() {
        Map<String, String> ctx = new HashMap<>();
        ctx.put("get_program_account.alternateAccounts_salesforceId", "001abc");
        Assert.assertEquals(SalesforceAuth.resolveAccountId(ctx), "001abc");
        ctx.put("http_request_200_2.alternateAccounts_salesforceId", "001preferred");
        Assert.assertEquals(SalesforceAuth.resolveAccountId(ctx), "001preferred");
        Assert.assertEquals(SalesforceAuth.resolveAccountId(null), "");
    }

    /** ImportedRestClient stub that records whether Guest/Account/MemberApi is on the stack. */
    private static final class RecordingClient implements com.hi.api.support.ImportedRestClient {
        boolean sawGuestApi;
        boolean sawAccountApi;
        boolean sawMemberApi;
        int tokenHits;

        private static boolean stackHas(String simpleName) {
            for (StackTraceElement e : Thread.currentThread().getStackTrace()) {
                if (e.getClassName().endsWith("." + simpleName)) {
                    return true;
                }
            }
            return false;
        }

        private static Response dummy() {
            return new io.restassured.builder.ResponseBuilder()
                    .setStatusCode(200)
                    .setBody("{}")
                    .build();
        }

        @Override
        public Response hHonorsEnroll(String token, String requestBody) {
            sawGuestApi = stackHas("GuestApi");
            return dummy();
        }

        @Override
        public Response createProgramAccount(String token, String guestId,
                Map<String, String> queryParams, String requestBody) {
            sawAccountApi = stackHas("ProgramAccountApi");
            return dummy();
        }

        @Override
        public Response validateMemberTOTP(String token, String guestId, String accountId,
                String memberId, String requestBody) {
            sawMemberApi = stackHas("MemberApi");
            return dummy();
        }

        @Override
        public Response tokenRequest(String token, String requestBody) {
            tokenHits++;
            sawGuestApi = stackHas("GuestApi");
            return dummy();
        }
    }
}
