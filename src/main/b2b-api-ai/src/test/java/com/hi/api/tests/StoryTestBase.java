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
    @Deprecated
    protected JsonTokens.Session ownerSession(Map<String, String> row, String configPrefix) {
        return ownerSession(row, configPrefix, null);
    }

    /**
     * As above, falling back to another story's persona keys ({@code fallbackPrefix}).
     * @deprecated a story test creates its people (enrollActor, addMember,
     * setMemberRole -- B2B_FLOWS.md); persona keys are ones nobody sets. Kept
     * for the tests written before.
     */
    @Deprecated
    protected JsonTokens.Session ownerSession(Map<String, String> row, String configPrefix,
                                              String fallbackPrefix) {
        LOG.info("SETUP persona {}: username and password from the row, else {}.username/.password{}",
                configPrefix, configPrefix, fallbackPrefix == null ? "" : ", else " + fallbackPrefix);
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

    /**
     * The API headers, as the converted suites' clients send them --
     * Content-Type and Accept JSON, a fresh X-Correlation-Id, the bearer --
     * plus headers/&lt;env&gt;.json when this environment has one. There is no
     * fallback to another environment's file: headers/qa.json holds demo
     * values (X-Api-Key, X-Tenant-Id) that must not reach stg.
     */
    protected static Map<String, String> apiHeaders(String bearerToken) {
        Map<String, String> headers = new java.util.LinkedHashMap<>();
        headers.put("Content-Type", "application/json");
        headers.put("Accept", "application/json");
        headers.put("X-Correlation-Id", java.util.UUID.randomUUID().toString());
        String own = headersFile();
        if (own != null) {
            headers.putAll(Headers.fromJson(own));
        }
        String bearer = bearerToken == null ? "" : bearerToken.trim();
        headers.put("Authorization", bearer.regionMatches(true, 0, "Bearer ", 0, 7) ? bearer : "Bearer " + bearer);
        return headers;
    }

    /** headers/&lt;env&gt;.json when this environment has one, else null. */
    protected static String headersFile() {
        String own = "headers/" + Config.env() + ".json";
        return StoryTestBase.class.getClassLoader().getResource(own) != null ? own : null;
    }

    /**
     * Run one exchange inside a named Allure step, so its request and
     * response attachments sit under the step. Name it after the verb and
     * path: {@code "createProgramAccount: POST /guests/{guestId}/businesses"}.
     */
    protected static <T> T step(String name, Supplier<T> exchange) {
        long started = System.currentTimeMillis();
        try {
            T out = Allure.step(name, exchange::get);
            LOG.info("step {} -> {} ({} ms)", name, outcome(out), System.currentTimeMillis() - started);
            return out;
        } catch (RuntimeException | Error e) {
            LOG.warn("step {} FAILED after {} ms: {}", name, System.currentTimeMillis() - started, e.toString());
            throw e;
        }
    }

    /** What a step came to, for the run log: never a token or a body. */
    private static String outcome(Object out) {
        if (out instanceof Response) {
            return "HTTP " + ((Response) out).getStatusCode();
        }
        if (out instanceof JsonTokens.Session) {
            String g = ((JsonTokens.Session) out).guestId;
            return "token obtained, guest id " + (present(g) ? g : "not in the token");
        }
        if (out instanceof String) {
            return "token obtained";
        }
        return out == null ? "nothing" : out.getClass().getSimpleName();
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
        // The enrolment's source and property codes: per environment, from the
        // configuration (the converted bodies carry them as literals; the
        // committed starter body does not).
        if (!present(row.get("enroll_source_code"))) {
            row.put("enroll_source_code", Config.get("enroll.source_code", ""));
        }
        if (!present(row.get("enroll_prop_code"))) {
            row.put("enroll_prop_code", Config.get("enroll.prop_code", ""));
        }
        if ((!present(row.get("enroll_source_code")) || !present(row.get("enroll_prop_code")))
                && WARNED_ENROLL_CODES.compareAndSet(false, true)) {
            LOG.warn("enroll.source_code / enroll.prop_code are not set for env '{}': an enrolment body using "
                    + "#enroll_source_code# / #enroll_prop_code# sends them empty. Set them in that block of "
                    + "program_configuration.json (the converted suites' enrol bodies show the values).",
                    Config.env());
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

    private static final java.util.concurrent.atomic.AtomicBoolean WARNED_ENROLL_CODES =
            new java.util.concurrent.atomic.AtomicBoolean(false);
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
        LOG.info("SETUP {} enrol a guest -> HTTP {}, guestId {}", tag, status, present(guestId) ? guestId.trim()
                : "MISSING (the response did not carry one; what depends on it will fail)");
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
        LOG.info("SETUP {} create a program account for guest {} -> HTTP {}, accountId {}", tag, guestId, status,
                present(accountId) ? accountId.trim() : "MISSING (what depends on it will fail)");
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
        LOG.info("ACT {} create a program account for guest {} -> HTTP {}", tag, guestId, res.getStatusCode());
        return res;
    }

    // ---- actors: the people a B2B test needs, created by the test ------------------
    //
    // The converted B2B suites never log in as a person someone set up by hand:
    // they ENROL every guest they need (generated username/email), create the
    // program account, add members, and give a member a role -- taking every
    // id from a response (enrol body guestId, create body accountId/memberId,
    // the hilton-member-location header, the members list) -- and send every
    // call with ONE service token, the acting guest named by the path. A story
    // test does the same with the helpers below; each takes the token to send,
    // and the service token is what the converted suites send.
    // See .cursor/skills/jira-to-restassured/B2B_FLOWS.md for the chains.

    /** Columns {@link #fillEnrollFields} generates for one guest: never shared between two actors. */
    private static final java.util.Set<String> ACTOR_OWN =
            java.util.Set.of("enroll_username", "enroll_password", "owner_company_email");

    /** A guest this test enrolled: its ids, its credentials and, on request, its own token. */
    protected static final class Actor {
        public final String guestId;
        public final String username;
        public final String password;
        /** The email it was enrolled with ({@code #owner_company_email#} in the enrol template). */
        public final String email;
        /** The enrolment row (enroll_* columns), for rendering another body as this actor. */
        public final Map<String, String> row;
        /** Set by {@link #addMember}: this actor's member id in the account. */
        public String memberId;
        private final Supplier<String> login;
        private String token;

        Actor(String guestId, Map<String, String> row, Supplier<String> login) {
            this.guestId = guestId;
            this.row = row;
            this.username = row.get("enroll_username");
            this.password = row.get("enroll_password");
            this.email = row.get("owner_company_email");
            this.login = login;
        }

        /**
         * Its own token -- a resource-owner login with its generated
         * credentials, asked the first time it is needed. Only for an AC
         * about what a guest may do with their own login: the converted
         * suites send every call with the service token.
         */
        public String accessToken() {
            if (token == null) {
                token = login.get();
            }
            return token;
        }
    }

    private String serviceTokenValue;
    private long serviceTokenAt;
    /** Under a typical 300 s access token: a long sheet asks again rather than sending an expired one. */
    private static final long SERVICE_TOKEN_REUSE_MS = 4 * 60 * 1000L;

    /**
     * The converted suites' own token: {@code POST /realms/applications/token}
     * with {@code api_config.client_id}/{@code client_secret} and the
     * PROJECT credential {@code username}/{@code password} of this
     * environment (aliases {@code sf_config.ui_username}/{@code ui_password}).
     * Set once per environment, never per story. The default token of every
     * setup and act call (see the helpers below). Asked again after four
     * minutes. Skipped, naming the keys, when unset.
     */
    protected String serviceToken() {
        if (serviceTokenValue != null && System.currentTimeMillis() - serviceTokenAt < SERVICE_TOKEN_REUSE_MS) {
            return serviceTokenValue;
        }
        requireTokenConfig();
        String user = Config.get("api_config.username", Config.get("username", ""));
        String pass = Config.get("api_config.password", Config.get("password", ""));
        if (!present(user) || !present(pass)) {
            throw new SkipException("no project credential for the service token in env '" + Config.env()
                    + "': set username and password (or sf_config.ui_username / ui_password) in that block "
                    + "of program_configuration.json -- the same pair the converted suites' tokenRequest uses");
        }
        serviceTokenValue = step("tokenRequest: service credential (as the converted suites)",
                () -> JsonTokens.resourceOwner(user, pass)).accessToken;
        serviceTokenAt = System.currentTimeMillis();
        return serviceTokenValue;
    }

    /**
     * Setup: enrol a NEW guest -- its own generated username, password and
     * email, nothing from configuration -- and log in as it. Every actor of
     * one test shares {@code base}'s {@code account_email_domain} (set on
     * {@code base} by the first call when blank), so a member's email is in
     * the domain the account allows. The enrol template must use
     * {@code #enroll_username#}, {@code #enroll_password#} and
     * {@code #owner_company_email#}. Returns null when the enrolment failed
     * (asserted as SETUP). The actor logs in only when {@link Actor#accessToken()}
     * is first called.
     */
    protected Actor enrollActor(Map<String, String> base, String bodyTemplate, String tag) throws Exception {
        if (base != null && !present(base.get("account_email_domain"))) {
            base.put("account_email_domain", allowedDomainOrRandom());
        }
        Map<String, String> own = new java.util.LinkedHashMap<>();
        if (base != null) {
            for (Map.Entry<String, String> e : base.entrySet()) {
                if (!ACTOR_OWN.contains(e.getKey())) {
                    own.put(e.getKey(), e.getValue());
                }
            }
        }
        String guestId = enrollGuest(own, bodyTemplate, tag);
        if (guestId == null) {
            return null;
        }
        // No login now: the converted chain never logs in as the new guest,
        // and a login it does not need must not fail the test.
        return new Actor(guestId, own, () -> {
            try {
                return guestSession(own.get("enroll_username"), own.get("enroll_password")).accessToken;
            } catch (SkipException skip) {
                throw skip;
            } catch (RuntimeException e) {
                throw new IllegalStateException(tag + "/SETUP: log in as the enrolled guest " + guestId
                        + " failed (" + e.getMessage() + ") -- does the enrol template send "
                        + "#enroll_password#, not a fixed one?", e);
            }
        });
    }

    /**
     * The member id the create-account response gives its owner: body
     * {@code memberId}, else the {@code hilton-member-location} header
     * ({@code /guests/{g}/businesses/{a}/members/{memberId}}), else null.
     */
    protected static String ownerMemberId(Response createRes) {
        String fromBody = RestUtilities.safeJsonExtract(createRes, "memberId");
        if (present(fromBody)) {
            return fromBody.trim();
        }
        String loc = createRes == null ? null : createRes.header("hilton-member-location");
        if (!present(loc)) {
            return null;
        }
        String path = loc.trim().replaceFirst("[?#].*$", "").replaceAll("/+$", "");
        int at = path.indexOf("/members/");
        String id = at < 0 ? "" : path.substring(at + "/members/".length()).split("/")[0];
        return present(id) ? id : null;
    }

    /**
     * Setup: a program account for {@code owner}, and both ids it gives --
     * {@code [accountId, ownerMemberId]} -- or null when the create failed
     * or did not give them (asserted as SETUP, so the test fails, never
     * passes on nothing).
     */
    protected String[] createAccountFor(Actor owner, String bodyTemplate, String token, String tag)
            throws Exception {
        Response res = createProgramAccountResponse(owner.row, bodyTemplate, owner.guestId, token, tag);
        String status = RestUtilities.checkActualResponse(res);
        String accountId = RestUtilities.safeJsonExtract(res, "accountId");
        String memberId = ownerMemberId(res);
        softAssert.assertEquals(status, "200", tag + "/SETUP: POST create program account status");
        softAssert.assertTrue(present(accountId), tag + "/SETUP: the create response carries an accountId");
        softAssert.assertTrue(present(memberId),
                tag + "/SETUP: the create response gives the owner's memberId (body or hilton-member-location)");
        return "200".equals(status) && present(accountId) && present(memberId)
                ? new String[] {accountId.trim(), memberId} : null;
    }

    /** The account's invite key ({@code GET /businesses/{accountId}} -> inviteKey), or null (asserted). */
    protected String inviteKey(String accountId, String token, String tag) {
        String url = baseUrl() + ApiRoutes.fill(ApiRoutes.BUSINESS, "accountId", accountId);
        Response res = step("readProgramAccount: GET " + ApiRoutes.BUSINESS,
                () -> RestUtilities.getResponseGet(url, apiHeaders(token)));
        RestUtilities.logResponseBody(tag, holder, RestUtilities.getResponseAsString(res));
        String key = RestUtilities.safeJsonExtract(res, "inviteKey");
        softAssert.assertTrue(present(key), tag + "/SETUP: GET the account gives an inviteKey");
        return present(key) ? key.trim() : null;
    }

    /**
     * Setup: {@code member} joins the account with {@code role} (as the
     * converted add-member: POST /guests/{member}/businesses/{account}/members,
     * body emailAddress + role + inviteKey), then its member id is read from
     * the response, else from the members list (polled, as the converted
     * listAccountMembers: 206 until {@code [0].memberId}). Sets
     * {@code member.memberId}; returns it, or null (asserted as SETUP).
     */
    protected String addMember(Actor member, String accountId, String role, String inviteKey, String token,
                               String tag) {
        String url = baseUrl() + ApiRoutes.fill(ApiRoutes.GUEST_BUSINESS_MEMBERS,
                "guestId", member.guestId, "accountId", accountId);
        String body = "{\"emailAddress\":" + jsonString(member.email) + ",\"role\":" + jsonString(role)
                + (present(inviteKey) ? ",\"inviteKey\":" + jsonString(inviteKey) : "") + "}";
        RestUtilities.logRequestBody(tag, holder, body);
        Response res = step("createAccountMember: POST " + ApiRoutes.GUEST_BUSINESS_MEMBERS,
                () -> RestUtilities.getResponsePost(body, url, apiHeaders(token)));
        RestUtilities.logResponseBody(tag, holder, RestUtilities.getResponseAsString(res));
        String status = RestUtilities.checkActualResponse(res);
        softAssert.assertTrue(status.startsWith("2"), tag + "/SETUP: POST add member status (" + status + ")");
        String memberId = ownerMemberId(res);
        if (!present(memberId) && status.startsWith("2")) {
            Response list = step("listAccountMembers: GET " + ApiRoutes.GUEST_BUSINESS_MEMBERS,
                    () -> com.hi.api.retry.Poller.untilJsonNonEmpty(
                            () -> RestUtilities.getResponseGet(url, apiHeaders(token)), "[0].memberId", 8000L, 1000L));
            RestUtilities.logResponseBody(tag, holder, RestUtilities.getResponseAsString(list));
            memberId = RestUtilities.safeJsonExtract(list, "[0].memberId");
        }
        softAssert.assertTrue(present(memberId), tag + "/SETUP: the new member has a memberId");
        LOG.info("SETUP {} add guest {} to account {} as {} -> HTTP {}, memberId {}", tag, member.guestId,
                accountId, role, status, present(memberId) ? memberId : "MISSING");
        member.memberId = present(memberId) ? memberId.trim() : null;
        return member.memberId;
    }

    /** Setup: {@code POST /businesses/{accountId}/members/{memberId}/activate} (204). */
    protected boolean activateMember(String accountId, String memberId, String token, String tag) {
        String url = baseUrl() + ApiRoutes.fill(ApiRoutes.BUSINESS_MEMBER_ACTIVATE,
                "accountId", accountId, "memberId", memberId);
        Response res = step("activateAccountMember: POST " + ApiRoutes.BUSINESS_MEMBER_ACTIVATE,
                () -> RestUtilities.getResponsePost("{}", url, apiHeaders(token)));
        String status = RestUtilities.checkActualResponse(res);
        softAssert.assertEquals(status, "204", tag + "/SETUP: activate member status");
        return "204".equals(status);
    }

    /** Setup: {@code POST /businesses/{accountId}/activate} with activationSource manual (204). */
    protected boolean activateAccount(String accountId, String token, String tag) {
        String url = baseUrl() + ApiRoutes.fill(ApiRoutes.BUSINESS_ACTIVATE, "accountId", accountId);
        Response res = step("activateProgramAccount: POST " + ApiRoutes.BUSINESS_ACTIVATE,
                () -> RestUtilities.getResponsePost("{\"activationSource\":\"manual\"}", url, apiHeaders(token)));
        String status = RestUtilities.checkActualResponse(res);
        softAssert.assertEquals(status, "204", tag + "/SETUP: activate account status");
        return "204".equals(status);
    }

    /**
     * Setup: the OWNER gives a member a role -- {@code travelCoordinator},
     * {@code admin}, {@code employee} -- by
     * {@code PUT /guests/{owner}/businesses/{account}/members/{memberId}}
     * with status active (as h4bbookonbehalf's updateAccountMember).
     */
    protected boolean setMemberRole(Actor owner, String accountId, Actor member, String role, String token,
                                    String tag) {
        String url = baseUrl() + ApiRoutes.fill(ApiRoutes.GUEST_BUSINESS_MEMBER,
                "guestId", owner.guestId, "accountId", accountId, "memberId", member.memberId);
        String body = "{\"status\":\"active\",\"emailAddress\":" + jsonString(member.email)
                + ",\"role\":" + jsonString(role) + "}";
        RestUtilities.logRequestBody(tag, holder, body);
        Response res = step("updateAccountMember: PUT " + ApiRoutes.GUEST_BUSINESS_MEMBER,
                () -> RestUtilities.getResponsePut(body, url, apiHeaders(token)));
        RestUtilities.logResponseBody(tag, holder, RestUtilities.getResponseAsString(res));
        String status = RestUtilities.checkActualResponse(res);
        softAssert.assertEquals(status, "200", tag + "/SETUP: PUT member role " + role + " status");
        if (!"200".equals(status)) {
            return false;
        }
        // The converted suite waits after this PUT (Delay_5Sec); here the role
        // is read back until it shows, with a deadline -- not a sleep.
        String read = baseUrl() + ApiRoutes.fill(ApiRoutes.GUEST_BUSINESS_MEMBER,
                "guestId", member.guestId, "accountId", accountId, "memberId", member.memberId);
        Response back = step("readAccountMember: GET " + ApiRoutes.GUEST_BUSINESS_MEMBER + " (role settled)",
                () -> com.hi.api.retry.Poller.untilJsonEquals(
                        () -> RestUtilities.getResponseGet(read, apiHeaders(token)), "role", role, 10_000L));
        String now = RestUtilities.safeJsonExtract(back, "role");
        softAssert.assertEquals(now, role, tag + "/SETUP: the member reads back as " + role);
        return role.equals(now);
    }

    /**
     * Setup that needs the database (the converted suites read the owner's
     * one-time code there): confirm the owner's validation of a new account
     * -- the code polled and zero-padded ({@link com.hi.api.db.Db#pollEmailOtp}),
     * then POST .../confirmValidation. Call {@link #requireDb} BEFORE the
     * first enrolment, so a skip leaves nothing half made.
     */
    protected boolean confirmOwnerValidation(Actor owner, String accountId, String ownerMemberId, String token,
                                             String tag) {
        requireDb("read the owner's one-time code (segment.account_member_internal_security)");
        String otp = com.hi.api.db.Db.pollEmailOtp(ownerMemberId);
        softAssert.assertTrue(present(otp), tag + "/SETUP: the owner's one-time code is in the database "
                + "(account_member_id " + ownerMemberId + ")");
        if (!present(otp)) {
            return false;
        }
        String url = baseUrl() + ApiRoutes.fill(ApiRoutes.MEMBER_CONFIRM_VALIDATION,
                "guestId", owner.guestId, "accountId", accountId, "memberId", ownerMemberId);
        Response res = step("confirmMemberTotp: POST " + ApiRoutes.MEMBER_CONFIRM_VALIDATION,
                () -> RestUtilities.getResponsePost("{\"totpCode\":" + jsonString(otp.trim()) + "}",
                        url, apiHeaders(token)));
        RestUtilities.logResponseBody(tag, holder, RestUtilities.getResponseAsString(res));
        String status = RestUtilities.checkActualResponse(res);
        softAssert.assertEquals(status, "200", tag + "/SETUP: confirm owner validation status");
        return "200".equals(status);
    }

    /** Skip, naming the keys, when this environment has no database configuration. */
    protected static void requireDb(String why) {
        if (!com.hi.api.db.Db.isConfigured()) {
            throw new SkipException("needs the database to " + why + ", and env '" + Config.env()
                    + "' has none: set database.host / port / schema / username / password in that block "
                    + "of program_configuration.json (or -Ddb.url / db.user / db.password)");
        }
    }

    private static String jsonString(String s) {
        if (s == null) {
            return "null";
        }
        StringBuilder out = new StringBuilder("\"");
        for (char c : s.toCharArray()) {
            if (c == '"' || c == '\\') {
                out.append('\\').append(c);
            } else if (c < 0x20) {
                out.append(String.format("\\u%04x", (int) c));
            } else {
                out.append(c);
            }
        }
        return out.append('"').toString();
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
