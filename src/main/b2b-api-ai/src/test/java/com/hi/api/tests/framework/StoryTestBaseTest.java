package com.hi.api.tests.framework;

import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.HashMap;
import java.util.Map;

import org.testng.Assert;
import org.testng.SkipException;
import org.testng.annotations.AfterMethod;
import org.testng.annotations.Test;

import com.hi.api.auth.JsonTokens;
import com.hi.api.tests.StoryTestBase;

/**
 * The helpers every story test shares (StoryTestBase, JsonTokens). Seen in
 * the generated story tests: each wrote these again, in two flavours.
 */
public class StoryTestBaseTest {

    /** Not a test class: a way to reach StoryTestBase's protected helpers. */
    static final class Probe extends StoryTestBase {
        static String opt(Map<String, String> row, String col, String key, String fallback) {
            return optionalRowOrConfig(row, col, key, fallback);
        }

        static String req(Map<String, String> row, String col, String key, String fallback) {
            return rowOrConfig(row, col, key, fallback);
        }

        static boolean truthy(Map<String, String> row, String col, String key) {
            return truthyRowOrConfig(row, col, key);
        }

        static String path(String template, String... nameValues) {
            return route(template, nameValues);
        }

        static void minted(Map<String, String> row, String col) {
            generated(row, col);
        }

        static void retryFresh(Map<String, String> row) {
            forgetGenerated(row);
        }

        static String letters(String s) {
            return lettersOnly(s);
        }
    }

    @Test(groups = {"unit"})
    public void aMadeUpCompanyIsLettersOnly() {
        Assert.assertEquals(Probe.letters("Smith, Jones & Wolf-Ltd."), "Smith Jones WolfLtd");
        Assert.assertEquals(Probe.letters("&&&"), "Acme Corp");
        Assert.assertEquals(Probe.letters(null), "Acme Corp");
        Assert.assertTrue(Probe.letters("A".repeat(60)).length() <= 40);
    }

    @Test(groups = {"unit"})
    public void aPathWithAnEmptyIdStopsBeforeTheCall() {
        Assert.assertEquals(Probe.path("/accounts/{accountId}/members", "accountId", "42"), "/accounts/42/members");
        for (String empty : new String[] {null, "", "  "}) {
            try {
                Probe.path("/accounts/{accountId}/members", "accountId", empty);
                Assert.fail("an empty {accountId} must not become /accounts//members");
            } catch (IllegalStateException e) {
                Assert.assertTrue(e.getMessage().contains("{accountId} is empty"), e.getMessage());
            }
        }
    }

    @Test(groups = {"unit"})
    public void aRetryForgetsTheValuesTheFirstAttemptGenerated() {
        Map<String, String> row = new HashMap<>();
        row.put("email", "from-the-sheet@example.test");
        row.put("firstName", "made-up");
        row.put("lastName", "made-up-too");
        Probe.minted(row, "firstName");
        Probe.minted(row, "lastName");
        Probe.retryFresh(row);
        Assert.assertEquals(row.get("email"), "from-the-sheet@example.test", "the sheet's value stays");
        Assert.assertFalse(row.containsKey("firstName"), row.toString());
        Assert.assertFalse(row.containsKey("lastName"), row.toString());
        Assert.assertFalse(row.containsKey("__generated"), row.toString());

        Map<String, String> fixed = java.util.Collections.unmodifiableMap(new HashMap<>(row));
        Probe.minted(fixed, "x");   // no throw on a row that cannot change
        Probe.retryFresh(fixed);
    }

    @AfterMethod(alwaysRun = true)
    public void clear() {
        for (String k : new String[] {"api_config.token_end_point", "api_config.token_route",
                "jira.abc-1.owner.username", "jira.abc-2.owner.username"}) {
            System.clearProperty(k);
        }
    }

    private static String jwt(String claimsJson) {
        Base64.Encoder enc = Base64.getUrlEncoder().withoutPadding();
        return enc.encodeToString("{\"alg\":\"none\"}".getBytes(StandardCharsets.UTF_8)) + "."
                + enc.encodeToString(claimsJson.getBytes(StandardCharsets.UTF_8)) + ".sig";
    }

    @Test(groups = {"unit"})
    public void theGuestIdIsReadFromTheTokensClaims() {
        Assert.assertEquals(JsonTokens.guestIdFromClaims(jwt("{\"sub\":\"x\",\"guestId\":\"123456\"}")), "123456");
        Assert.assertEquals(JsonTokens.guestIdFromClaims(jwt("{\"guestID\":987}")), "987");
        Assert.assertNull(JsonTokens.guestIdFromClaims(jwt("{\"sub\":\"no guest\"}")));
        Assert.assertNull(JsonTokens.guestIdFromClaims("not-a-jwt"));
        Assert.assertNull(JsonTokens.guestIdFromClaims(null));
        Assert.assertNull(JsonTokens.guestIdFrom(null, "a.%%%.c"), "a payload that is not base64: null, no throw");
    }

    @Test(groups = {"unit"})
    public void theTokenUrlIsTheConfiguredOneElseTheRealmsRoute() {
        System.setProperty("api_config.token_end_point", "https://auth.example.test/base/");
        System.setProperty("api_config.token_route", "oauth/token");
        Assert.assertEquals(JsonTokens.tokenUrl(), "https://auth.example.test/base/oauth/token");
        System.setProperty("api_config.token_route", "/oauth/token");
        System.setProperty("api_config.token_end_point", "https://auth.example.test/base");
        Assert.assertEquals(JsonTokens.tokenUrl(), "https://auth.example.test/base/oauth/token");
        System.setProperty("api_config.token_route", "");
        Assert.assertTrue(JsonTokens.tokenUrl().endsWith("/realms/applications/token"),
                "no route configured: the realms token route on the base URL -- " + JsonTokens.tokenUrl());
    }

    @Test(groups = {"unit"})
    public void bothTokenUrlsAreAskedTheConfiguredOneFirst() {
        System.setProperty("api_config.token_end_point", "https://auth.example.test/base");
        System.setProperty("api_config.token_route", "/oauth/token");
        java.util.List<String> urls = JsonTokens.tokenUrls();
        Assert.assertEquals(urls.size(), 2, urls.toString());
        Assert.assertEquals(urls.get(0), "https://auth.example.test/base/oauth/token");
        Assert.assertTrue(urls.get(1).endsWith("/realms/applications/token"), urls.toString());
        System.setProperty("api_config.token_route", "");
        Assert.assertEquals(JsonTokens.tokenUrls().size(), 1, "nothing configured: the realms route only");
    }

    @Test(groups = {"unit"})
    public void aPlaceholderIsNotAValue() {
        Map<String, String> row = new HashMap<>();
        row.put("username", "__SET_ME__");
        Assert.assertNull(Probe.opt(row, "username", "jira.abc-1.owner.username", null),
                "a placeholder in the row is not a persona: unset, so the test is skipped, not run with it");
    }

    @Test(groups = {"unit"})
    public void aSessionWithoutAGuestIdSaysSoWhenOneIsNeeded() {
        Assert.assertEquals(new JsonTokens.Session("t", "42").requireGuestId(), "42");
        Assert.assertThrows(IllegalStateException.class, () -> new JsonTokens.Session("t", null).requireGuestId());
    }

    @Test(groups = {"unit"})
    public void theRowWinsThenTheConfigThenTheFallbackKey() {
        Map<String, String> row = new HashMap<>();
        row.put("username", "from-row");
        Assert.assertEquals(Probe.opt(row, "username", "jira.abc-1.owner.username", null), "from-row");
        row.put("username", " null ");
        System.setProperty("jira.abc-2.owner.username", "from-fallback");
        Assert.assertEquals(Probe.opt(row, "username", "jira.abc-1.owner.username", "jira.abc-2.owner.username"),
                "from-fallback");
        Assert.assertNull(Probe.opt(new HashMap<>(), "username", "jira.abc-1.owner.username", null));
        Assert.assertThrows(SkipException.class,
                () -> Probe.req(new HashMap<>(), "username", "jira.abc-1.owner.username", null));
        Map<String, String> yes = new HashMap<>();
        yes.put("flag", "Yes");
        Assert.assertTrue(Probe.truthy(yes, "flag", "jira.abc-1.flag"));
        Assert.assertFalse(Probe.truthy(new HashMap<>(), "flag", "jira.abc-1.flag"));
    }
}
