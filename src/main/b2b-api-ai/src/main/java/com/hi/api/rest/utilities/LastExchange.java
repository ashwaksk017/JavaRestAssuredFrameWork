// =============================================================================
// LastExchange
// -----------------------------------------------------------------------------
// Per-thread record of the HTTP responses this test has received, so a
// breakpoint anywhere can see what the server actually said.
//
// The problem it solves: every call path in this framework returns something
// OTHER than the Response to the test author.
//
//   * a chained phase          -- `.enrollOwner()` returns the flow, so the
//                                 Response is a local inside RestStep.execute
//   * a converted test         -- the generated phase stores it in a protected
//                                 `<step>Res` field the test cannot read
//   * a manual chained test    -- same as the chain
//
// A breakpoint on `.enrollOwner()` therefore shows the builder, not the
// response, and the only way to the body was to step down three frames into
// generated code. Recording every exchange here makes the response an
// expression away from ANY frame: `LastExchange.response()`.
//
// Why a ThreadLocal and not a field: parallel="methods" runs many tests on one
// class instance, so an instance field would be shared across concurrent
// tests. The thread is the test here, which is the same boundary
// ImportedScenario.isolateCtx already uses.
//
// Cleared per test by BaseApiTest.newTestHolder(). Without that, TestNG's
// pooled threads hand the NEXT test the previous test's responses -- and a
// stale 200 read at a breakpoint is worse than no value at all.
// =============================================================================

package com.hi.api.rest.utilities;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import io.restassured.response.Response;

public final class LastExchange {

    /**
     * How many exchanges one test keeps. The longest converted chain is
     * around 15 steps; 50 covers that with room for retries and setup flows
     * while keeping a stuck thread from holding response buffers forever.
     */
    private static final int MAX_KEPT = 50;

    /**
     * Insertion-ordered so {@link #steps()} reads back in call order, which is
     * what makes it usable as a debugger summary of the run so far.
     */
    private static final ThreadLocal<Map<String, Recorded>> LOG_BY_STEP =
            ThreadLocal.withInitial(LinkedHashMap::new);

    private static final ThreadLocal<Recorded> LAST = new ThreadLocal<>();

    /**
     * The step name the in-flight call belongs to, published by RestStep for
     * the benefit of the recording filter.
     *
     * <p>Both see the same exchange -- the filter as it leaves, RestStep once
     * it has settled -- and without a shared name they would file it twice
     * under two different keys. With it, the filter's provisional entry is the
     * one RestStep later overwrites.</p>
     */
    private static final ThreadLocal<String> PENDING_STEP = new ThreadLocal<>();

    private LastExchange() {
    }

    /** One exchange: the response plus enough context to identify it. */
    public static final class Recorded {
        private final String step;
        private final String method;
        private final String uri;
        private final Response response;
        private final String requestBody;

        Recorded(String step, String method, String uri, Response response,
                 String requestBody) {
            this.step = step;
            this.method = method;
            this.uri = uri;
            this.response = response;
            this.requestBody = requestBody;
        }

        public String step() {
            return step;
        }

        public String method() {
            return method;
        }

        public String uri() {
            return uri;
        }

        public Response response() {
            return response;
        }

        /**
         * The request body as it went on the wire, REDACTED, or "" when the
         * call had none.
         *
         * <p>Redacted because the token request's body is client_id and
         * client_secret: a debugging aid must not become the second place a
         * credential is readable. The recording filter redacts before this
         * ever holds it.</p>
         */
        public String requestBody() {
            return requestBody == null ? "" : requestBody;
        }

        public int status() {
            return response == null ? -1 : response.getStatusCode();
        }

        /**
         * Rendered as {@code POST /accounts -> 201 (createAccount)} so the
         * debugger's variables pane is readable without expanding anything.
         */
        @Override
        public String toString() {
            return method + " " + uri + " -> " + status() + "  (" + step + ")";
        }
    }

    /**
     * Called by every producer of an HTTP response. Safe to call twice for the
     * same exchange: the filter records it under the borrowed
     * {@link #pendingStep()} name as the call leaves, then RestStep re-records
     * the FINAL response -- the one that survived transient retry and token
     * refresh -- under the same name, replacing it in place.
     *
     * <p>Never throws. A recording fault must not fail a test that the server
     * answered correctly.</p>
     */
    public static void record(String step, String method, String uri, Response res) {
        record(step, method, uri, res, null);
    }

    /**
     * As {@link #record(String, String, String, Response)}, with the request
     * body that produced the response.
     *
     * <p>A null body means "I do not know it" and KEEPS whatever this step
     * already had -- RestStep re-records a call after it settles and has no
     * body to hand, and losing the filter's copy there would make the
     * request unreadable for exactly the steps that retried.</p>
     */
    public static void record(String step, String method, String uri, Response res,
                              String requestBody) {
        try {
            String key = (step == null || step.isBlank()) ? "(unnamed)" : step;
            Map<String, Recorded> map = LOG_BY_STEP.get();
            String body = requestBody;
            if (body == null) {
                Recorded prev = map.get(key);
                body = prev == null ? null : prev.requestBody;
            }
            Recorded rec = new Recorded(key, method == null ? "?" : method,
                    uri == null ? "?" : uri, res, body);
            // Re-put so a repeated step name keeps its LATEST response and
            // moves to the end -- a chain that calls readAccount twice should
            // report the second read, in the position it ran.
            map.remove(key);
            map.put(key, rec);
            while (map.size() > MAX_KEPT) {
                map.remove(map.keySet().iterator().next());
            }
            LAST.set(rec);
        } catch (Throwable ignored) {
            // reporting must never fail a test
        }
    }

    /** Publish the step name the next outgoing call belongs to. */
    public static void naming(String step) {
        PENDING_STEP.set(step);
    }

    /** Stop attributing outgoing calls to a step. */
    public static void doneNaming() {
        PENDING_STEP.remove();
    }

    /**
     * The step the in-flight call belongs to, or null when the call was made
     * outside a RestStep -- a legacy-style test calling RestUtilities
     * directly, or an auth fetch.
     */
    public static String pendingStep() {
        return PENDING_STEP.get();
    }

    /** The response of the most recent call on this thread, or null. */
    public static Response response() {
        Recorded r = LAST.get();
        return r == null ? null : r.response();
    }

    /** The step name {@link #response()} came from, or null before the first call. */
    public static String step() {
        Recorded r = LAST.get();
        return r == null ? null : r.step();
    }

    /** Status of the most recent call, or -1 before the first call. */
    public static int status() {
        Recorded r = LAST.get();
        return r == null ? -1 : r.status();
    }

    /**
     * REDACTED request body of the most recent call, or "" when it had none.
     *
     * <p>The counterpart to {@link #body()}: that is what came back, this is
     * what went out. Both are on the record, so a breakpoint can compare the
     * two without stepping into the filter.</p>
     */
    public static String requestBody() {
        Recorded r = LAST.get();
        return r == null ? "" : r.requestBody();
    }

    /** REDACTED request body of an earlier step by name, or "". */
    public static String requestBodyOf(String stepName) {
        Recorded r = LOG_BY_STEP.get().get(stepName);
        return r == null ? "" : r.requestBody();
    }

    /** Body of the most recent call as a String, or "" before the first call. */
    public static String body() {
        Response res = response();
        return res == null ? "" : RestUtilities.getResponseAsString(res);
    }

    /** The most recent exchange with its method and URI, or null. */
    public static Recorded last() {
        return LAST.get();
    }

    /**
     * The response of an EARLIER named step -- {@code responseOf("createProgramAccount")}
     * from a breakpoint three phases later. Null when no step of that name ran
     * on this thread.
     *
     * <p>Step names are the ones in the Allure step titles and the
     * {@code expected_&lt;step&gt;_status_code} CSV columns, so the name is
     * already visible in the report and the datasheet.</p>
     */
    public static Response of(String stepName) {
        Recorded r = LOG_BY_STEP.get().get(stepName);
        return r == null ? null : r.response();
    }

    /** Every exchange this test has made, in call order. */
    public static List<Recorded> all() {
        return new ArrayList<>(LOG_BY_STEP.get().values());
    }

    /** Just the step names, in call order -- the cheapest thing to eyeball. */
    public static List<String> steps() {
        return new ArrayList<>(LOG_BY_STEP.get().keySet());
    }

    /**
     * Drop everything recorded on this thread. Called from BaseApiTest's
     * {@code @BeforeMethod} so one test never reads another's responses.
     */
    public static void clear() {
        LOG_BY_STEP.remove();
        LAST.remove();
        PENDING_STEP.remove();
    }
}
