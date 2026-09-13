package com.ak.api.rest.utilities;

import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.testng.asserts.SoftAssert;

import com.ak.api.auth.TokenCache;
import com.ak.api.config.Config;
import com.ak.api.data.PlaceholderResolver;
import com.ak.api.retry.AsyncBudget;
import com.ak.api.retry.Poller;
import com.ak.api.rest.ApiRoutes;
import com.ak.api.support.ImportedScenario;

import io.qameta.allure.Allure;
import io.qameta.allure.model.Status;
import io.qameta.allure.model.StepResult;
import io.restassured.RestAssured;
import io.restassured.response.Response;

/**
 * One REST exchange: optional identity regen, template body, query
 * placeholders, path check, transient retry, truncated-body log, status
 * soft-assert.
 *
 * <p>Replaces the ~40-line ceremony the converter emits around every
 * {@code client.*} call. The actual HTTP verb still lives in the lambda
 * so the generated {@code com.ak.api.rest.clients.*Client} stays the typed
 * surface. Referenced as text, not {@code @link}: which client classes
 * exist depends on which ReadyAPI XMLs were converted, and this file must
 * compile for any of them.</p>
 *
 * <p><b>Before</b> (B2B-9098 {@code http_request_200_enroll_guest}):</p>
 * <pre>
 * TestSupport.regenRandomProperties(ctx);
 * String payload = RestUtilities.mapJsonValues(
 *     RestUtilities.getRequestTemplate(Templates.REALMS_HHONORSENROLL_MERGED),
 *     TestSupport.mergedRow(row, ctx), false);
 * String resolved = PlaceholderResolver.resolveAll(payload, ctx);
 * Map&lt;String,String&gt; q = new LinkedHashMap&lt;&gt;();
 * q.put("ownerEmailAddress", PlaceholderResolver.resolveAll(
 *     RestUtilities.mapJsonValues("#Properties_Email#", ...), ctx));
 * // ... assertPathResolved, Allure.step, retry, 800-char body log, status ...
 * Response res = RestUtilities.callWithTransientRetry("http_request_200_enroll_guest",
 *     15000L, 200, () -&gt; client.httpRequest200EnrollGuest(token, q, resolved));
 * </pre>
 *
 * <p><b>After:</b></p>
 * <pre>
 * Response enroll = RestStep.exec(ctx, row, softAssert, holder, testCaseId)
 *     .name("http_request_200_enroll_guest")
 *     .template(Templates.REALMS_HHONORSENROLL_MERGED)
 *     .regenIdentity()
 *     .query("ownerEmailAddress", "#Properties_Email#")
 *     .query("phoneNum", "#Properties_Phone#")
 *     .expectedStatus(200)
 *     .post("/realms/guests/enroll",
 *         (body, q, h) -&gt; client.httpRequest200EnrollGuest(
 *             TestSupport.ctxGet(ctx, "tokenId.GeneratedTokenID"), q, body));
 * </pre>
 */
public final class RestStep {

    private static final Logger LOG = LoggerFactory.getLogger(RestStep.class);
    private static final long DEFAULT_RETRY_DEADLINE_MS = 15_000L;
    private static final int BODY_LOG_LIMIT = 800;

    private static final ThreadLocal<String> LAST_RESOLVED_BODY =
            ThreadLocal.withInitial(() -> "");

    private final Map<String, String> ctx;
    private final Map<String, String> row;
    private final SoftAssert softAssert;
    private final RestLoggerUtilityDataHolder holder;
    private final String testCaseId;

    private String stepName = "unnamed";
    private String templateResource;
    private boolean regenIdentity;
    private int expectedStatus = 200;
    private int pollUntilStatus = -1;
    private String pollUntilJsonPath;
    private String pollUntilJsonExpected;
    private boolean pollUntilJsonPresent;
    private long pollTimeoutMs;
    private long pollIntervalMs;
    private final Map<String, String> rawQuery = new LinkedHashMap<>();
    private final Map<String, String> rawHeaders = new LinkedHashMap<>();

    private RestStep(Map<String, String> ctx, Map<String, String> row,
                     SoftAssert softAssert, RestLoggerUtilityDataHolder holder,
                     String testCaseId) {
        this.ctx = ctx;
        this.row = row;
        this.softAssert = softAssert;
        this.holder = holder;
        this.testCaseId = testCaseId;
    }

    /**
     * Cap a payload for logging.
     *
     * <p>The RESPONSE body was capped at {@link #BODY_LOG_LIMIT} but the two
     * REQUEST-body logs were not, so an oversized request flooded the log
     * while a response of the same size was truncated. Both request logs
     * also print the SAME payload -- once after placeholder resolution and
     * once as it goes out -- so an uncapped body landed in the log twice
     * per step.</p>
     */
    static String capForLog(String s) {
        if (s == null) {
            return "<null>";
        }
        return s.length() > BODY_LOG_LIMIT
                ? s.substring(0, BODY_LOG_LIMIT) + "... (truncated)"
                : s;
    }
    public static RestStep exec(Map<String, String> ctx, Map<String, String> row,
                                SoftAssert softAssert,
                                RestLoggerUtilityDataHolder holder,
                                String testCaseId) {
        return new RestStep(ctx, row, softAssert, holder, testCaseId);
    }

    public RestStep name(String stepName) {
        if (stepName != null && !stepName.isEmpty()) this.stepName = stepName;
        return this;
    }

    /** Classpath template constant from {@code Templates.*}. */
    public RestStep template(String classpathResource) {
        this.templateResource = classpathResource;
        return this;
    }

    /**
     * Call {@link com.ak.api.support.ImportedScenario#regenRandomProperties} immediately before
     * building the body -- same as the converter's {@code [regen]} block.
     */
    public RestStep regenIdentity() {
        this.regenIdentity = true;
        return this;
    }

    public RestStep expectedStatus(int status) {
        this.expectedStatus = status;
        return this;
    }

    /** Poll the exchange until HTTP {@code status} or {@code timeoutMs}. */
    public RestStep pollUntilStatus(int status, long timeoutMs) {
        this.pollUntilStatus = status;
        this.pollTimeoutMs = timeoutMs;
        return this;
    }

    /** Poll the exchange until JsonPath equals {@code expected}. */
    /**
     * Re-issue this request until {@code jsonPath} comes back non-empty.
     *
     * <p>For a value a LATER step needs as a required path segment. The
     * existing salesforce-id poller armed only when the ReadyAPI case
     * happened to carry a MessageContentAssertion on the field -- an
     * assertion, not a dependency -- so a case that consumes the id without
     * asserting it (B2B-4913, B2B-5942) read it the instant the account was
     * created, got "", and died on "TRAILING empty path segment". The
     * converter knows the real relationship at emit time and now says so
     * here.</p>
     */
    public RestStep pollUntilJsonPresent(String jsonPath, long timeoutMs) {
        this.pollUntilJsonPath = jsonPath;
        this.pollUntilJsonPresent = true;
        this.pollTimeoutMs = timeoutMs;
        return this;
    }

    public RestStep pollUntilJson(String jsonPath, String expected, long timeoutMs) {
        this.pollUntilJsonPath = jsonPath;
        this.pollUntilJsonExpected = expected;
        this.pollTimeoutMs = timeoutMs;
        return this;
    }

    /**
     * Query parameter whose value may contain {@code #placeholder#} refs.
     * Empty values are kept (matches converter emit of blank SoapUI params).
     */
    public RestStep query(String name, String placeholderOrValue) {
        if (name != null) rawQuery.put(name, placeholderOrValue == null ? "" : placeholderOrValue);
        return this;
    }

    /**
     * Extra header (not Authorization -- that stays a client token arg).
     * Values may contain {@code #placeholder#} refs and are resolved
     * after optional identity regen, same as query params.
     */
    public RestStep header(String name, String placeholderOrValue) {
        if (name == null || "authorization".equalsIgnoreCase(name)) return this;
        rawHeaders.put(name, placeholderOrValue == null ? "" : placeholderOrValue);
        return this;
    }

    @FunctionalInterface
    public interface Exchange {
        Response send(String body, Map<String, String> queryParams,
                      Map<String, String> extraHeaders) throws Exception;
    }

    public Response get(String path, Exchange call) throws Exception {
        return execute("GET", path, call);
    }

    public Response post(String path, Exchange call) throws Exception {
        return execute("POST", path, call);
    }

    public Response put(String path, Exchange call) throws Exception {
        return execute("PUT", path, call);
    }

    public Response patch(String path, Exchange call) throws Exception {
        return execute("PATCH", path, call);
    }

    public Response delete(String path, Exchange call) throws Exception {
        return execute("DELETE", path, call);
    }

    /**
     * Resolve a map of {@code #placeholder#} query values the same way
     * generated tests do (mergedRow + mapJsonValues + resolveAll). Public
     * so unit tests can cover the substitution without firing HTTP.
     */
    public static Map<String, String> resolveQuery(Map<String, String> row,
                                                   Map<String, String> ctx,
                                                   Map<String, String> raw)
            throws Exception {
        Map<String, String> out = new LinkedHashMap<>();
        if (raw == null || raw.isEmpty()) return out;
        Map<String, String> merged = ImportedScenario.mergedRow(row, ctx);
        for (Map.Entry<String, String> e : raw.entrySet()) {
            String expr = e.getValue() == null ? "" : e.getValue();
            // Resolve @Key@ / #Key# against ctx aliases BEFORE mapJsonValues
            // so `@Properties_hilton-member-id@` is not replaced with the
            // numeric fallback `0`.
            String resolved = PlaceholderResolver.resolveAll(expr, ctx);
            String mapped = RestUtilities.mapJsonValues(resolved, merged, false, false);
            String value = PlaceholderResolver.resolveAll(mapped, ctx);
            // mapJsonValues treats an empty CSV cell as unresolved and
            // substitutes the word "null". ReadyAPI stores those params
            // as empty, not the literal null (B2B-3056 attest sent
            // travelAgentId=null and H4B 400'd). Keep the key; send empty.
            if (value != null && "null".equalsIgnoreCase(value.trim())
                    && (expr.isBlank() || resolved.isBlank()
                    || "null".equalsIgnoreCase(mapped.trim()))) {
                value = "";
            }
            out.put(e.getKey(), value == null ? "" : value);
        }
        return out;
    }

    /**
     * Resolved request body from the most recent {@code exec} on this
     * thread. Used by converter auto-extract of
     * {@code ${step#RawRequest#path}} refs.
     */
    public static String lastResolvedBody() {
        String b = LAST_RESOLVED_BODY.get();
        return b == null ? "" : b;
    }

    private Response execute(String verb, String path, Exchange call) throws Exception {
        if (call == null) {
            throw new IllegalArgumentException("RestStep." + verb + " requires an Exchange lambda");
        }
        if (regenIdentity) {
            ImportedScenario.traceCtx(ctx, "before-regen:" + stepName);
            if (ImportedScenario.identityPackReady(ctx)) {
                LOG.info(" .. [regen skipped] step={} identity pack already applied this attempt",
                        stepName);
            } else {
                ImportedScenario.regenRandomProperties(ctx, row);
                ImportedScenario.markIdentityPackReady(ctx);
                LOG.info(" .. [regen] step={} Properties.Username={} Properties.Email={} Properties.usernamemember={} Properties.generatedemailAddress1={}",
                        stepName,
                        ctx == null ? null : ctx.get("Properties.Username"),
                        ctx == null ? null : ctx.get("Properties.Email"),
                        ctx == null ? null : ctx.get("Properties.usernamemember"),
                        ctx == null ? null : ctx.get("Properties.generatedemailAddress1"));
            }
            ImportedScenario.traceCtx(ctx, "after-regen:" + stepName);
        } else {
            ImportedScenario.traceCtx(ctx, "before:" + stepName);
        }

        Map<String, String> resolveCtx = ImportedScenario.ctxForStep(ctx, stepName);
        String body = "";
        if (templateResource != null && !templateResource.isEmpty()) {
            String mapped = RestUtilities.mapJsonValues(
                    RestUtilities.getRequestTemplate(templateResource),
                    ImportedScenario.mergedRow(row, resolveCtx), false);
            LOG.info(" .. [after-mapJsonValues] step={} ({} chars): {}",
                    stepName, mapped.length(), capForLog(mapped));
            body = PlaceholderResolver.resolveAll(mapped, resolveCtx);
        }
        LAST_RESOLVED_BODY.set(body);

        Map<String, String> query = resolveQuery(row, resolveCtx, rawQuery);
        Map<String, String> headers = resolveQuery(row, resolveCtx, rawHeaders);
        String resolvedUrl = fillTrailingSalesforceId(
                PlaceholderResolver.resolveAll(path == null ? "" : path, resolveCtx));
        RestUtilities.assertPathResolved(verb, stepName, resolvedUrl, expectedStatus);
        LOG.info(" -> {} {}  (step={})", verb, resolvedUrl, stepName);
        if (!query.isEmpty()) {
            String assertion = query.get("assertion");
            if (assertion != null) {
                LOG.info(" .. form/query assertion len={} jwtLike={} grant_type={} (step={})",
                        assertion.length(), assertion.startsWith("eyJ"),
                        query.get("grant_type"), stepName);
            } else {
                LOG.info(" .. query/form keys={} (step={})", query.keySet(), stepName);
            }
        }
        if (!body.isEmpty()) {
            LOG.info(" .. request body ({} chars): {}", body.length(),
                    capForLog(body));
        }

        final String bodyForCall = body;
        final Map<String, String> queryForCall = query;
        final Map<String, String> headersForCall = headers;
        // Keep this Allure step OPEN for the HTTP exchange so
        // AllureRestAssured Request/Response attachments nest under it.
        // Allure.step(String) starts and immediately stops a step, which
        // dumped every attachment at the end of the test body.
        return inAllureStep(stepName + ": " + verb + " " + resolvedUrl, () -> {
            long t0 = System.currentTimeMillis();
            if (TokenCache.canReuseForSetup(stepName, expectedStatus, resolvedUrl)) {
                Response cached = TokenCache.cachedAsResponse();
                if (cached != null) {
                    TokenCache.applyToCtx(ctx);
                    LOG.info(" <- HTTP {} in {}ms  (step={}, token cache hit)",
                            cached.getStatusCode(), System.currentTimeMillis() - t0, stepName);
                    ResponseAsserts.statusFromStepColumn(
                            softAssert, cached, row, stepName, expectedStatus);
                    return cached;
                }
            }
            java.util.function.Supplier<Response> exchange = () -> {
                try {
                    return call.send(bodyForCall, queryForCall, headersForCall);
                } catch (RuntimeException re) {
                    throw re;
                } catch (Exception e) {
                    throw new RuntimeException(
                            "RestStep " + stepName + " exchange failed", e);
                }
            };
            if ("POST".equals(verb)) {
                maybeWaitUntilActivatable(resolvedUrl);
            }
            if ("GET".equals(verb)) {
                maybePollUntilSalesforceId();
            }
            // Additive only -- never removes a wait. See the method javadoc
            // for why converting Delay steps themselves was reverted.
            maybePollUntilExpectedJson();
            if (pollUntilStatus > 0) {
                exchange = wrapPollStatus(exchange);
            } else if (pollUntilJsonPath != null && !pollUntilJsonPath.isEmpty()) {
                exchange = wrapPollJson(exchange);
            }
            Response res = RestUtilities.callWithTransientRetry(
                    stepName, DEFAULT_RETRY_DEADLINE_MS, expectedStatus, exchange);
            res = spendAsyncBudgetIfNeeded(res, exchange);
            if (res != null && (res.getStatusCode() == 401
                    || res.getStatusCode() == 403)) {
                // Guaranteed producer for the auth counter: every request goes
                // through here, unlike the reporting filter which recorded
                // nothing on a real run. See AuthDiagnostics.
                String verdict = AuthDiagnostics.verdictFromCtx(ctx);
                AuthDiagnostics.record(verdict);
                LOG.warn(" .. [auth-diag] step={} HTTP {} -- {}",
                        stepName, res.getStatusCode(), verdict);
                // Not for a step that ASKED for 401/403 -- that is the
                // assertion passing, not a dead token.
                if (expectedStatus != 401 && expectedStatus != 403
                        && AuthDiagnostics.invalidateCachedTokens(
                                Config.getInt("auth.invalidateDebounceMs", 5_000))) {
                    LOG.warn(" .. [auth-diag] cleared cached tokens -- next"
                            + " auth-requiring step will fetch a fresh one");
                }
            }
            if (com.ak.api.rest.ApiRoutes.isTokenPath(resolvedUrl)
                    || (stepName != null
                    && stepName.toLowerCase().contains("tokenrequest"))) {
                TokenCache.storeFrom(res);
            }
            if (TokenRefresh.shouldAttempt(stepName, expectedStatus, res)) {
                LOG.info(" .. [token-refresh] step={} HTTP {} -- regenerating {} then retrying once",
                        stepName, res.getStatusCode(), TokenRefresh.CTX_TOKEN);
                if (TokenRefresh.refreshHiltonToken(ctx)) {
                    res = RestUtilities.callWithTransientRetry(
                            stepName, DEFAULT_RETRY_DEADLINE_MS, expectedStatus, exchange);
                } else {
                    LOG.warn(" .. [token-refresh] step={} refresh failed -- keeping original HTTP {}",
                            stepName, res.getStatusCode());
                }
            }
            LOG.info(" <- HTTP {} in {}ms  (step={})",
                    res.getStatusCode(), System.currentTimeMillis() - t0, stepName);
            logTruncatedBody(res);
            if (LOG.isDebugEnabled()) {
                LOG.debug(" .. response headers: {}", res.getHeaders());
            }
            if (holder != null) {
                RestUtilities.logResponseBody(
                        testCaseId, holder, RestUtilities.getResponseAsString(res));
            }
            ResponseAsserts.statusFromStepColumn(
                    softAssert, res, row, stepName, expectedStatus);
            captureRuntimeExtracts(verb, resolvedUrl, res);
            publishCsvRawRequestRefs();
            maybeRefreshSalesforceIdAfterActivate(verb, resolvedUrl, res);
            return res;
        });
    }

    private java.util.function.Supplier<Response> wrapPollStatus(
            java.util.function.Supplier<Response> exchange) {
        final java.util.function.Supplier<Response> inner = exchange;
        int status = pollUntilStatus;
        long timeout = pollTimeoutMs > 0 ? pollTimeoutMs : DEFAULT_RETRY_DEADLINE_MS;
        long interval = pollIntervalMs > 0 ? pollIntervalMs : Poller.DEFAULT_INTERVAL_MS;
        return () -> Poller.untilStatus(inner, status, timeout, interval);
    }

    private java.util.function.Supplier<Response> wrapPollJson(
            java.util.function.Supplier<Response> exchange) {
        final java.util.function.Supplier<Response> inner = exchange;
        String path = pollUntilJsonPath;
        String expected = pollUntilJsonExpected;
        long timeout = pollTimeoutMs > 0 ? pollTimeoutMs : DEFAULT_RETRY_DEADLINE_MS;
        if (pollUntilJsonPresent) {
            long interval = Config.getInt("rest.pollSalesforceIdIntervalMs", 2_000);
            return () -> Poller.untilJsonNonEmpty(inner, path, timeout, interval);
        }
        return () -> Poller.untilJsonEquals(inner, path, expected, timeout);
    }

    /**
     * ReadyAPI MessageContent "salesforceID exists" after activate. H4B
     * sometimes omits {@code alternateAccounts} for a few seconds; the
     * Salesforce GET then builds {@code /sobjects/Account/} and fail-fast
     * fires. Poll this GET until the id is present.
     */
    /**
     * Poll a step until its OWN asserted JsonPath value appears, when the row
     * declares one.
     *
     * <p>This is the safe half of "replace fixed sleeps with polling". The
     * unsafe half -- converting a ReadyAPI Delay into a poll on the next REST
     * step -- was tried and reverted for a documented reason: SoapUI authors
     * do not reliably put the delay immediately before the racy step (the
     * accountmemberregression 5s wait sits before
     * {@code Partition_before_http_request_200} for Kafka offset reasons,
     * while the step that actually races is two steps later). Converting the
     * delay would DELETE the wall-clock wait that protects the later step and
     * introduce fresh flakiness.</p>
     *
     * <p>So nothing here removes a wait. This only ADDS a bounded re-read when
     * the value the step asserts has not propagated yet, turning a flaky
     * failure into a pass. Where the value is already correct it costs one
     * comparison and returns immediately.</p>
     *
     * <p>Default OFF: {@code rest.pollExpectedJsonMs=0}. Enabling it makes a
     * genuinely-wrong value take that long to report, so it is opt-in per run
     * rather than a silent timing change across the suite.</p>
     */
    /**
     * Spend deferred Delay time here, but only if this step actually needs it.
     *
     * <p>This is what makes deferring a ReadyAPI Delay safe. The delay no
     * longer sleeps up-front; instead whichever later step fails its
     * expectation retries against the pooled budget. A step that passes first
     * time spends nothing, so a scenario with no real race costs zero extra
     * wall-clock -- while a step several positions after the delay can still
     * spend the whole wait, which is the case that made polling only the
     * next step unsafe.</p>
     *
     * <p>Only a STATUS mismatch triggers a spend. A 2xx whose body is stale is
     * deliberately NOT retried here -- {@code maybePollUntilExpectedJson}
     * owns that, gated behind its own opt-in budget, because retrying on a
     * value mismatch can mask a genuinely wrong value.</p>
     */
    private Response spendAsyncBudgetIfNeeded(Response res,
                                              java.util.function.Supplier<Response> exchange) {
        if (expectedStatus < 0 || res == null) {
            return res;
        }
        if (res.getStatusCode() == expectedStatus) {
            return res; // nothing to wait for
        }
        if (!AsyncBudget.hasBudget()) {
            return res;
        }
        long budget = AsyncBudget.claim(Config.getInt("rest.asyncBudgetCapMs", 60_000));
        if (budget <= 0) {
            return res;
        }
        long interval = Config.getInt("rest.asyncBudgetIntervalMs", 2_000);
        long deadline = System.currentTimeMillis() + budget;
        long t0 = System.currentTimeMillis();
        LOG.info(" .. [async-budget] step={} got HTTP {} (expected {}) -- spending up "
                + "to {}ms of deferred delay before accepting it",
                stepName, res.getStatusCode(), expectedStatus, budget);
        Response last = res;
        while (System.currentTimeMillis() < deadline) {
            Poller.sleepQuietly(Math.min(interval,
                    Math.max(1, deadline - System.currentTimeMillis())));
            try {
                last = exchange.get();
            } catch (RuntimeException e) {
                LOG.warn(" .. [async-budget] retry threw: {}", e.toString());
                break;
            }
            if (last != null && last.getStatusCode() == expectedStatus) {
                long spent = System.currentTimeMillis() - t0;
                AsyncBudget.spent(spent);
                LOG.info(" .. [async-budget] step={} reached HTTP {} after {}ms",
                        stepName, expectedStatus, spent);
                return last;
            }
        }
        long spent = System.currentTimeMillis() - t0;
        AsyncBudget.spent(spent);
        LOG.warn(" .. [async-budget] step={} still HTTP {} after spending {}ms "
                + "-- accepting; the assertion below will report it",
                stepName, last == null ? "?" : last.getStatusCode(), spent);
        return last == null ? res : last;
    }

    private void maybePollUntilExpectedJson() {
        if (pollUntilJsonPath != null && !pollUntilJsonPath.isEmpty()) {
            return; // an explicit poll already wins
        }
        if (row == null || stepName == null || expectedStatus < 0) {
            return;
        }
        long budget = Config.getInt("rest.pollExpectedJsonMs", 0);
        if (budget <= 0) {
            return;
        }
        String prefix = "expected_" + stepName + "_jsonpath_";
        for (Map.Entry<String, String> e : row.entrySet()) {
            String col = e.getKey();
            if (col == null || !col.startsWith(prefix)) {
                continue;
            }
            String expected = e.getValue();
            if (expected == null || expected.isEmpty()) {
                continue;
            }
            // Unresolved placeholders are not a condition worth waiting on.
            if (expected.contains("#") || expected.contains("@")
                    || expected.contains("${")) {
                continue;
            }
            String path = col.substring(prefix.length());
            if (path.isEmpty()) {
                continue;
            }
            this.pollUntilJsonPath = path;
            this.pollUntilJsonExpected = expected;
            this.pollTimeoutMs = budget;
            LOG.info(" .. polling {} until {}={} (budget {}ms, rest.pollExpectedJsonMs)",
                    stepName, path, expected, budget);
            return;
        }
    }

    private void maybePollUntilSalesforceId() {
        if (pollUntilJsonPath != null && !pollUntilJsonPath.isEmpty()) {
            return;
        }
        if (row == null || stepName == null) {
            return;
        }
        // The converter numbers msgcontent columns by element ordinal
        // (expected_<step>_msgcontent_<N>_salesforceId). Building the name
        // without the ordinal matched none of the 22 such columns in the
        // reference suite, so this poller NEVER armed -- the Salesforce id
        // was read the instant the account was created, came back empty, and
        // the step died on "TRAILING empty path segment" before the 30s
        // budget could help.
        String col = ResponseAsserts.msgContentColumn(row, stepName, "salesforceId");
        if (col == null) {
            return;
        }
        this.pollUntilJsonPath = "alternateAccounts.salesforceId";
        this.pollUntilJsonPresent = true;
        if (this.pollTimeoutMs <= 0) {
            this.pollTimeoutMs = Config.getInt("rest.pollSalesforceIdMs", 30_000);
        }
        LOG.info(" .. polling {} until {} is non-empty (timeout {}ms)",
                stepName, pollUntilJsonPath, pollTimeoutMs);
    }

    /**
     * ReadyAPI JDBC (or confirm-validation) puts the account in Limited
     * before a single activate. Retrying activate after 400 honors moves
     * H4B to "must be Limited" and never recovers. Wait on GET status,
     * then POST activate once.
     */
    private void maybeWaitUntilActivatable(String resolvedUrl) {
        if (expectedStatus != 204 || ctx == null) {
            return;
        }
        if (resolvedUrl == null || !resolvedUrl.contains("/activate")) {
            return;
        }
        String token = ImportedScenario.ctxGet(ctx, "tokenId.GeneratedTokenID");
        if (token == null || token.isEmpty()) {
            return;
        }
        String accountId = firstAccountIdFromCtx();
        if (accountId.isEmpty()) {
            return;
        }
        String base = Config.baseUrl();
        if (base == null || base.isBlank()) {
            return;
        }
        String getPath = ApiRoutes.fill("/businesses/{accountId}", "accountId", accountId);
        long timeout = Config.getInt("rest.pollActivateReadyMs", 30_000);
        long interval = Config.getInt("rest.pollActivateReadyIntervalMs", 2_000);
        String auth = token.regionMatches(true, 0, "Bearer ", 0, 7)
                ? token : "Bearer " + token;
        LOG.info(" .. waiting until GET {} is limited/rejected before activate (timeout {}ms)",
                getPath, timeout);
        Poller.until(() -> RestAssured.given()
                        .header("Authorization", auth)
                        .header("Accept", "application/json")
                        .get(base.replaceAll("/+$", "") + getPath),
                res -> {
                    if (res == null || res.getStatusCode() != 200) {
                        return false;
                    }
                    String st = RestUtilities.safeJsonExtract(res, "status");
                    return "limited".equalsIgnoreCase(st)
                            || "rejected".equalsIgnoreCase(st);
                },
                timeout, interval, "account limited/rejected");
    }

    /**
     * ReadyAPI {@code ${http_request_200_2#Response#$['alternateAccounts']['salesforceId']}}
     * even when this case's GET is named {@code get_program_account}. Fill
     * trailing {@code /Account/} from ctx before fail-fast.
     */
    String fillTrailingSalesforceId(String url) {
        if (url == null || ctx == null) {
            return url;
        }
        if (!url.endsWith("/sobjects/Account/") && !url.endsWith("/Account/")) {
            return url;
        }
        String id = SalesforceAuth.resolveAccountId(ctx);
        if (id == null || id.isEmpty()) {
            return url;
        }
        LOG.info(" .. filled Salesforce Account id from ctx for step={} id={}",
                stepName, id);
        return url + id;
    }

    private void captureRuntimeExtracts(String verb, String path, Response res) {
        if (ctx == null || res == null || res.getStatusCode() >= 300) {
            return;
        }
        if ("POST".equals(verb) && path != null && path.contains("/enroll")) {
            String honors = RestUtilities.safeJsonExtract(res, "hhonorsNumber");
            if (honors == null || honors.isEmpty()) {
                honors = RestUtilities.safeJsonExtract(res, "hHonorsNumber");
            }
            ImportedScenario.putExtracted(ctx, "Properties.hhonorsNumber", honors);
        }
        if ("GET".equals(verb) && isProgramAccountGet(path)) {
            storeSalesforceId(RestUtilities.safeJsonExtract(
                    res, "alternateAccounts.salesforceId"));
        }
        publishCsvResponseRefs(res);
    }

    /**
     * ReadyAPI {@code ${REST Request#Response#$['sourceId']}} lands in CSV
     * as {@code #REST_Request_Response_sourceId#}. Converter auto-extract
     * used to miss JsonPath Match content (only scanned later request
     * bodies). Publish any {@code #<thisStep>_Response_<field>#} keys
     * already present on the row so assertions resolve without waiting
     * on reconvert.
     */
    private void publishCsvResponseRefs(Response res) {
        if (ctx == null || row == null || stepName == null) {
            return;
        }
        String sanitized = stepName.replaceAll("[^A-Za-z0-9_]", "_");
        String prefix = sanitized + "_Response_";
        Pattern p = Pattern.compile("#" + Pattern.quote(prefix) + "([A-Za-z0-9_.-]+)#");
        Set<String> fields = new LinkedHashSet<>();
        collectHashFields(row.values(), p, fields);
        collectHashFields(ctx.values(), p, fields);
        for (String field : fields) {
            String path = field.replace('_', '.');
            String val = RestUtilities.safeJsonExtract(res, path);
            if (val == null || val.isEmpty()) {
                continue;
            }
            ImportedScenario.putExtracted(ctx, prefix + field, val);
        }
    }

    /**
     * ReadyAPI {@code ${step#RawRequest#$['contactInfo']['name']}} lands in
     * CSV as {@code #http_request_200_createAccount_RawRequest_contactInfo_name#}.
     * Converter auto-extract used to miss DataAndMetadata assertion
     * content (only scanned later request bodies). Publish any
     * {@code #<thisStep>_RawRequest_<field>#} keys already on the row
     * from the resolved request payload.
     */
    private void publishCsvRawRequestRefs() {
        if (ctx == null || row == null || stepName == null) {
            return;
        }
        String body = lastResolvedBody();
        if (body == null || body.isEmpty()) {
            return;
        }
        String sanitized = stepName.replaceAll("[^A-Za-z0-9_]", "_");
        String prefix = sanitized + "_RawRequest_";
        Pattern p = Pattern.compile("#" + Pattern.quote(prefix) + "([A-Za-z0-9_.-]+)#");
        Set<String> fields = new LinkedHashSet<>();
        collectHashFields(row.values(), p, fields);
        collectHashFields(ctx.values(), p, fields);
        for (String field : fields) {
            String path = field.replace('_', '.');
            String val = RestUtilities.safeJsonExtractFromString(body, path);
            if (val == null || val.isEmpty()) {
                continue;
            }
            ImportedScenario.putExtracted(ctx, prefix + field, val);
        }
    }

    private static void collectHashFields(Iterable<String> values, Pattern p,
                                          Set<String> fields) {
        if (values == null) {
            return;
        }
        for (String v : values) {
            if (v == null || v.indexOf('#') < 0) {
                continue;
            }
            Matcher m = p.matcher(v);
            while (m.find()) {
                fields.add(m.group(1));
            }
        }
    }

    /**
     * B2B-5484 activates then Salesforce-GETs
     * {@code http_request_200_2.alternateAccounts_salesforceId} without a
     * GET after activate. ReadyAPI still expects 200; poll H4B until the
     * id exists (Hilton token required -- skipped in unit tests).
     */
    private void maybeRefreshSalesforceIdAfterActivate(
            String verb, String path, Response res) {
        if (ctx == null || res == null) {
            return;
        }
        if (!"POST".equals(verb) || path == null || !path.contains("/activate")) {
            return;
        }
        int status = res.getStatusCode();
        if (status != 204 && status != 200) {
            return;
        }
        String existing = SalesforceAuth.resolveAccountId(ctx);
        if (existing != null && !existing.isEmpty()) {
            return;
        }
        String token = ImportedScenario.ctxGet(ctx, "tokenId.GeneratedTokenID");
        if (token == null || token.isEmpty()) {
            return;
        }
        String accountId = firstAccountIdFromCtx();
        if (accountId.isEmpty()) {
            return;
        }
        String base = Config.baseUrl();
        if (base == null || base.isBlank()) {
            return;
        }
        String getPath = ApiRoutes.fill("/businesses/{accountId}", "accountId", accountId);
        long timeout = Config.getInt("rest.pollSalesforceIdMs", 30_000);
        long interval = Config.getInt("rest.pollSalesforceIdIntervalMs", 2_000);
        LOG.info(" .. polling GET {} until salesforceId after activate (timeout {}ms)",
                getPath, timeout);
        String auth = token.regionMatches(true, 0, "Bearer ", 0, 7)
                ? token : "Bearer " + token;
        Response got = Poller.untilJsonNonEmpty(() -> RestAssured.given()
                .header("Authorization", auth)
                .header("Accept", "application/json")
                .get(base.replaceAll("/+$", "") + getPath),
                "alternateAccounts.salesforceId", timeout, interval);
        storeSalesforceId(RestUtilities.safeJsonExtract(
                got, "alternateAccounts.salesforceId"));
    }

    private String firstAccountIdFromCtx() {
        for (String key : new String[] {
                "PropertiesDetails.accountID",
                "PropertiesaccountID.accountID",
                "Properties.accountID",
                "Properties.accountId"}) {
            String v = ctx.get(key);
            if (v == null || v.isEmpty() || v.indexOf('@') >= 0) {
                continue;
            }
            return v;
        }
        return "";
    }

    private void storeSalesforceId(String id) {
        if (id == null || id.isEmpty()) {
            return;
        }
        ImportedScenario.putExtracted(ctx,
                stepName + ".alternateAccounts_salesforceId", id);
        ImportedScenario.putExtracted(ctx,
                "http_request_200_2.alternateAccounts_salesforceId", id);
        ImportedScenario.putExtracted(ctx,
                "http_request_200_2_Response_alternateAccounts_salesforceId", id);
        ImportedScenario.putExtracted(ctx, "Properties.salesforceId", id);
    }

    private static boolean isProgramAccountGet(String path) {
        if (path == null || !path.contains("/businesses")) {
            return false;
        }
        return !path.contains("/members") && !path.contains("/attest")
                && !path.contains("/activate") && !path.contains("/dashboard");
    }

    @FunctionalInterface
    private interface AllureStepBody<T> {
        T run() throws Exception;
    }

    /**
     * Run {@code body} while an Allure step is the current lifecycle
     * item so RestAssured attachments attach to that step, not the test.
     */
    private static <T> T inAllureStep(String name, AllureStepBody<T> body)
            throws Exception {
        String uuid = UUID.randomUUID().toString();
        boolean started = false;
        try {
            Allure.getLifecycle().startStep(uuid, new StepResult().setName(name));
            started = true;
        } catch (RuntimeException ignored) {
            return body.run();
        }
        try {
            T result = body.run();
            try {
                Allure.getLifecycle().updateStep(uuid, s -> s.setStatus(Status.PASSED));
            } catch (RuntimeException ignored) {
                // Allure no-op outside a running test.
            }
            return result;
        } catch (Exception e) {
            try {
                Allure.getLifecycle().updateStep(uuid, s -> s.setStatus(Status.FAILED));
            } catch (RuntimeException ignored) {
                // Allure no-op outside a running test.
            }
            throw e;
        } finally {
            if (started) {
                try {
                    Allure.getLifecycle().stopStep(uuid);
                } catch (RuntimeException ignored) {
                    // Allure no-op outside a running test.
                }
            }
        }
    }

    private void logTruncatedBody(Response res) {
        String body = RestUtilities.getResponseAsString(res);
        if (body == null) body = "<null>";
        body = capForLog(body);
        if (res.getStatusCode() >= 400) {
            LOG.warn(" .. response body (HTTP {}): {}", res.getStatusCode(), body);
        } else {
            LOG.info(" .. response body (HTTP {}): {}", res.getStatusCode(), body);
        }
    }
}
