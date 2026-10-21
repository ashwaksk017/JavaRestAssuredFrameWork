package com.hi.api.tests;

import java.util.Map;
import java.util.function.Supplier;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.testng.SkipException;

import com.hi.api.auth.JsonTokens;
import com.hi.api.config.Config;
import com.hi.api.data.FakeData;
import com.hi.api.data.ManualBody;
import com.hi.api.rest.ApiRoutes;
import com.hi.api.rest.utilities.Headers;
import com.hi.api.rest.utilities.RestUtilities;

import io.qameta.allure.Allure;
import io.restassured.response.Response;

/**
 * The base class of every story test the workbench writes (tests/jira/).
 * Everything a story test needs besides its own calls and assertions is
 * here, ONCE: tokens and the caller's guest id, the API headers, values
 * from the row or the configuration, guest enrolment and a program account
 * as setup, and an Allure step around a call.
 *
 * <p>Why: the story tests generated before this each wrote these again --
 * five copies of the resource-owner token request, three of reading the
 * guest id out of the token, four of the headers, the enrol-then-create
 * block pasted from class to class. A story test now extends this class and
 * writes none of them; the workbench's checks fail a test that does.</p>
 *
 * <p>Assertion messages begin with the case and what they check:
 * {@code "TC-003/AC-2: ..."} for an acceptance criterion, and
 * {@code "TC-003/SETUP: ..."} for setup and plumbing (which is what the
 * setup helpers here write). {@code tag} below is the case id.</p>
 */
public abstract class StoryTestBase extends BaseApiTest {

    private static final Logger LOG = LoggerFactory.getLogger(StoryTestBase.class);

    // ---- values from the row, else the configuration ---------------------------

    /**
     * {@link #rowOrConfig(Map, String, String)}, falling back to a second
     * configuration key (a persona another story already set up); skipped
     * with both keys named when none has a value.
     */
    protected static String rowOrConfig(Map<String, String> row, String column, String configKey,
                                        String fallbackKey) {
        String v = optionalRowOrConfig(row, column, configKey, fallbackKey);
        if (v != null) {
            return v;
        }
        throw new SkipException("no value for '" + column + "': set " + configKey
                + (fallbackKey == null ? "" : " or " + fallbackKey)
                + " in program_configuration.json on this machine (or fill the '" + column
                + "' column of the row)");
    }

    /** The row's value, else the configuration's; null (no skip) when neither has one. */
    protected static String optionalRowOrConfig(Map<String, String> row, String column, String configKey) {
        return optionalRowOrConfig(row, column, configKey, null);
    }

    /** As above, with a second configuration key to fall back to. */
    protected static String optionalRowOrConfig(Map<String, String> row, String column, String configKey,
                                                String fallbackKey) {
        String fromRow = row == null ? null : row.get(column);
        if (present(fromRow)) {
            return fromRow.trim();
        }
        for (String key : new String[] {configKey, fallbackKey}) {
            if (key == null) {
                continue;
            }
            String fromConfig = Config.get(key, "");
            if (present(fromConfig)) {
                return fromConfig.trim();
            }
        }
        return null;
    }

    /** True when the row's cell, else the configuration's value, is true / yes. */
    protected static boolean truthyRowOrConfig(Map<String, String> row, String column, String configKey) {
        String v = optionalRowOrConfig(row, column, configKey);
        return v != null && ("true".equalsIgnoreCase(v) || "yes".equalsIgnoreCase(v) || "y".equalsIgnoreCase(v));
    }

    /** A real value: not blank, not "null", not a placeholder (__SET_ME__, TODO ... -- Config.isUnset). */
    private static boolean present(String v) {
        return v != null && !v.trim().isEmpty() && !"null".equalsIgnoreCase(v.trim()) && !Config.isUnset(v);
    }

    // ---- tokens -------------------------------------------------------------------

    /** The application's own token (client credentials); the test is skipped when it is not configured. */
    protected String clientToken() {
        requireTokenConfig();
        return step("tokenRequest: client-credentials grant", JsonTokens::clientCredentials);
    }

    /**
     * A persona's token, and its guest id, from the row's {@code username} /
     * {@code password} columns or {@code <configPrefix>.username} /
     * {@code .password} (e.g. {@code jira.b2b-1234.travel_coordinator}).
     */
    protected JsonTokens.Session ownerSession(Map<String, String> row, String configPrefix) {
        return ownerSession(row, configPrefix, null);
    }

    /** As above, falling back to another story's persona keys ({@code fallbackPrefix}). */
    protected JsonTokens.Session ownerSession(Map<String, String> row, String configPrefix,
                                              String fallbackPrefix) {
        String username = rowOrConfig(row, "username", configPrefix + ".username",
                fallbackPrefix == null ? null : fallbackPrefix + ".username");
        String password = rowOrConfig(row, "password", configPrefix + ".password",
                fallbackPrefix == null ? null : fallbackPrefix + ".password");
        return guestSession(username, password);
    }

    /** A token, and its guest id, for a username and password the test has (e.g. a guest it enrolled). */
    protected JsonTokens.Session guestSession(String username, String password) {
        requireTokenConfig();
        return step("tokenRequest: resource-owner grant", () -> JsonTokens.resourceOwner(username, password));
    }

    private static void requireTokenConfig() {
        String why = JsonTokens.missingConfig();
        if (!why.isEmpty()) {
            throw new SkipException(why);
        }
    }

    // ---- headers and calls ----------------------------------------------------

    /** The API headers of this environment, with the bearer token. */
    protected static Map<String, String> apiHeaders(String bearerToken) {
        Map<String, String> headers = Headers.fromJson(headersFile());
        headers.put("Authorization", "Bearer " + bearerToken);
        return headers;
    }

    /** headers/&lt;env&gt;.json when there is one, else headers/qa.json. */
    protected static String headersFile() {
        String own = "headers/" + Config.env() + ".json";
        return StoryTestBase.class.getClassLoader().getResource(own) != null ? own : "headers/qa.json";
    }

    /**
     * Run one exchange inside a named Allure step, so its request and
     * response attachments sit under the step. Name it after the verb and
     * path: {@code "createProgramAccount: POST /guests/{guestId}/businesses"}.
     */
    protected static <T> T step(String name, Supplier<T> exchange) {
        return Allure.step(name, exchange::get);
    }

    // ---- setup: a guest, a program account ----------------------------------------

    /**
     * Fill the enrolment columns a row leaves blank: username, password, the
     * account email domain (one of ALLOWED_DOMAINS when configured -- a
     * random domain is rejected by the target's allowlist), owner email.
     */
    protected static void fillEnrollFields(Map<String, String> row) {
        Map<String, String> signup = FakeData.signupDataMap();
        if (!present(row.get("enroll_username"))) {
            row.put("enroll_username", signup.get("username"));
        }
        if (!present(row.get("enroll_password"))) {
            row.put("enroll_password", signup.get("password"));
        }
        if (!present(row.get("account_email_domain"))) {
            row.put("account_email_domain", allowedDomainOrRandom());
        }
        if (!present(row.get("owner_company_email"))) {
            row.put("owner_company_email", row.get("enroll_username") + "@" + row.get("account_email_domain"));
        }
    }

    /** One of the configured ALLOWED_DOMAINS, else a random {@code word.com} (and a warning, once). */
    protected static String allowedDomainOrRandom() {
        String configured = Config.get(Config.get("identity.allowed_domains_key", "ALLOWED_DOMAINS"), "");
        if (present(configured)) {
            java.util.List<String> domains = new java.util.ArrayList<>();
            for (String d : configured.trim().split("[,;\\s]+")) {
                String clean = d.trim().toLowerCase(java.util.Locale.ROOT).replaceFirst("^www\\.", "");
                if (!clean.isEmpty()) {
                    domains.add(clean);
                }
            }
            if (!domains.isEmpty()) {
                return domains.get(java.util.concurrent.ThreadLocalRandom.current().nextInt(domains.size()));
            }
        }
        if (WARNED_DOMAINS.compareAndSet(false, true)) {
            LOG.warn("ALLOWED_DOMAINS is not configured -- a generated email uses a random word.com domain, "
                    + "which the target's domain allowlist rejects. Set ALLOWED_DOMAINS in "
                    + "program_configuration.json, or pass -DALLOWED_DOMAINS=a.com,b.com.");
        }
        return FakeData.username() + ".com";
    }

    private static final java.util.concurrent.atomic.AtomicBoolean WARNED_DOMAINS =
            new java.util.concurrent.atomic.AtomicBoolean(false);

    /**
     * Setup: enrol a new guest and return its guest id, or null when the
     * enrolment failed (then this test's soft assertions say why, tagged
     * {@code <tag>/SETUP}, and the caller stops). The body is the job's
     * template (e.g. {@code templates/manual/<job>_enroll_guest.json}); its
     * {@code #enroll_username#}-style columns are filled by
     * {@link #fillEnrollFields(Map)}.
     */
    protected String enrollGuest(Map<String, String> row, String bodyTemplate, String tag) throws Exception {
        fillEnrollFields(row);
        String token = clientToken();
        Map<String, String> ctx = new java.util.LinkedHashMap<>();
        ctx.put("accessToken", token);
        String body = ManualBody.render(bodyTemplate, row, ctx);
        RestUtilities.logRequestBody(tag, holder, body);
        String url = baseUrl() + ApiRoutes.fill(ApiRoutes.GUEST_ENROLL);
        Response res = step("enrollGuest: POST " + ApiRoutes.GUEST_ENROLL,
                () -> RestUtilities.getResponsePost(body, url, apiHeaders(token)));
        RestUtilities.logResponseBody(tag, holder, RestUtilities.getResponseAsString(res));
        String status = RestUtilities.checkActualResponse(res);
        softAssert.assertEquals(status, "200", tag + "/SETUP: POST enroll guest status");
        String guestId = RestUtilities.safeJsonExtract(res, "guestId");
        softAssert.assertTrue(present(guestId), tag + "/SETUP: the enrol response carries a guestId");
        return "200".equals(status) && present(guestId) ? guestId.trim() : null;
    }

    /**
     * Setup: a program account for an enrolled guest, as that guest; returns
     * the account id, or null when the create failed (asserted as SETUP).
     */
    protected String createProgramAccount(Map<String, String> row, String bodyTemplate, String guestId,
                                          String guestToken, String tag) throws Exception {
        Map<String, String> ctx = new java.util.LinkedHashMap<>();
        String body = ManualBody.render(bodyTemplate, row, ctx);
        RestUtilities.logRequestBody(tag, holder, body);
        String url = baseUrl() + ApiRoutes.fill(ApiRoutes.GUEST_BUSINESSES, "guestId", guestId);
        Response res = step("createProgramAccount: POST " + ApiRoutes.GUEST_BUSINESSES,
                () -> RestUtilities.getResponsePost(body, url, apiHeaders(guestToken)));
        RestUtilities.logResponseBody(tag, holder, RestUtilities.getResponseAsString(res));
        String status = RestUtilities.checkActualResponse(res);
        softAssert.assertEquals(status, "200", tag + "/SETUP: POST create program account status");
        String accountId = RestUtilities.safeJsonExtract(res, "accountId");
        softAssert.assertTrue(present(accountId), tag + "/SETUP: the create response carries an accountId");
        return "200".equals(status) && present(accountId) ? accountId.trim() : null;
    }

    /**
     * The create call itself, for a case whose acceptance criteria ARE the
     * create (its status, its headers): the response, unasserted -- the test
     * asserts it with its AC tags. {@link #createProgramAccount} is the
     * same call as SETUP, asserted and reduced to the account id.
     */
    protected Response createProgramAccountResponse(Map<String, String> row, String bodyTemplate, String guestId,
                                                    String guestToken, String tag) throws Exception {
        String body = ManualBody.render(bodyTemplate, row, new java.util.LinkedHashMap<>());
        RestUtilities.logRequestBody(tag, holder, body);
        String url = baseUrl() + ApiRoutes.fill(ApiRoutes.GUEST_BUSINESSES, "guestId", guestId);
        Response res = step("createProgramAccount: POST " + ApiRoutes.GUEST_BUSINESSES,
                () -> RestUtilities.getResponsePost(body, url, apiHeaders(guestToken)));
        RestUtilities.logResponseBody(tag, holder, RestUtilities.getResponseAsString(res));
        return res;
    }

    // ---- reading a response ------------------------------------------------------

    /** The list at a JSON path, or an empty list (never null, never an exception). */
    protected static java.util.List<Object> listAt(Response res, String jsonPath) {
        try {
            java.util.List<Object> got = res.jsonPath().getList(jsonPath);
            return got == null ? java.util.Collections.emptyList() : got;
        } catch (RuntimeException notAList) {
            return java.util.Collections.emptyList();
        }
    }
}
