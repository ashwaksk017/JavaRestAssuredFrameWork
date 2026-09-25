package com.hi.api.retry;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import com.hi.api.config.Config;

/**
 * A ReadyAPI Delay converted from "sleep now" into "time any later step may
 * spend waiting for the thing this delay was waiting for".
 *
 * <h2>Why not just poll the next step</h2>
 *
 * That was tried and reverted. SoapUI authors do not reliably place the delay
 * immediately before the racy step -- accountmemberregression puts a 5s wait
 * before {@code Partition_before_http_request_200} for Kafka offset reasons,
 * while the step that actually races is {@code http_request_pending_200}, two
 * steps later. Polling only the next step leaves the racy step unprotected AND
 * deletes the wall-clock wait that was covering it, so it trades one flake for
 * a worse one.
 *
 * <h2>What this does instead</h2>
 *
 * The delay registers a BUDGET on the thread rather than sleeping. Any later
 * step that fails its expectation may spend that budget retrying. So:
 *
 * <ul>
 *   <li>Nothing is racy -> no time is spent at all.</li>
 *   <li>The racy step is three steps later -> it spends the budget, which is
 *       precisely the case the reverted design could not reach.</li>
 *   <li>Worst case, the same wall-clock time is spent as before -- but only
 *       where it was actually needed.</li>
 * </ul>
 *
 * <h2>The case this must NOT be used for</h2>
 *
 * A delay followed by a NEGATIVE assertion ("wait 5s, verify no email
 * arrived") must sleep for real. Deferring it would check immediately, see
 * nothing, and pass -- while the email arrives at 4s. That does not break the
 * test, it silently WEAKENS it, which is worse: a green test that stopped
 * testing. The converter marks those delays and emits
 * {@link Poller#delayStrict} instead, which always sleeps.
 *
 * <p>Kill switch: {@code -Drest.deferDelays=false} restores a real sleep at
 * every delay.</p>
 *
 * <p>Thread-scoped: TestNG runs one test method per thread start-to-finish
 * under {@code parallel="classes"}, so a budget belongs to exactly one
 * scenario. {@link #reset()} is called per test method regardless.</p>
 */
public final class AsyncBudget {

    private static final Logger LOG = LoggerFactory.getLogger(AsyncBudget.class);

    private static final ThreadLocal<Long> REMAINING_MS = ThreadLocal.withInitial(() -> 0L);
    private static final ThreadLocal<String> REASON = ThreadLocal.withInitial(() -> "");
    private static final ThreadLocal<Long> GRANTED_MS = ThreadLocal.withInitial(() -> 0L);
    private static final ThreadLocal<Long> SPENT_MS = ThreadLocal.withInitial(() -> 0L);

    private AsyncBudget() {}

    /** Master switch. Default ON -- a delay becomes budget, not a sleep. */
    /**
     * Deferral is OPT-IN. ReadyAPI sleeps at a delay step, and the whole
     * point of those steps is that a downstream system (Salesforce sync,
     * attestation) needs the wall-clock time before the next read.
     *
     * <p>Defaulting this ON meant none of the 142 delay-bearing phases in
     * the reference suite ever slept, and the three mechanisms meant to
     * compensate all missed: the budget is spent only on a STATUS mismatch
     * and only AFTER the request (so a broken path throws first), the
     * Salesforce-id poller never armed (its column name lacked the element
     * ordinal), and {@code rest.pollExpectedJsonMs} defaults to 0. Net
     * effect: a 20-second sync wait silently became zero, and 26% of the
     * suite ran against data that was not ready yet.</p>
     *
     * <p>Turn it back on with {@code -Drest.deferDelays=true} when trading
     * fidelity for wall-clock (the reference suite spends ~7.6 minutes in
     * delay steps).</p>
     */
    public static boolean enabled() {
        return "true".equalsIgnoreCase(Config.get("rest.deferDelays", "false"));
    }

    /** Register a deferred wait. Budgets accumulate if several delays stack up. */
    public static void add(long millis, String reason) {
        if (millis <= 0) {
            return;
        }
        long now = REMAINING_MS.get() + millis;
        REMAINING_MS.set(now);
        GRANTED_MS.set(GRANTED_MS.get() + millis);
        if (reason != null && !reason.isEmpty()) {
            REASON.set(reason);
        }
        LOG.info(" .. [async-budget] deferred {}ms from '{}' (available: {}ms) "
                + "-- not sleeping; a later step may spend it", millis, reason, now);
    }

    public static long remainingMs() {
        return REMAINING_MS.get();
    }

    public static boolean hasBudget() {
        return enabled() && REMAINING_MS.get() > 0;
    }

    /**
     * Claim up to {@code cap} ms of the remaining budget for one step.
     * Returns 0 when nothing is available.
     */
    public static long claim(long cap) {
        if (!enabled()) {
            return 0L;
        }
        long avail = REMAINING_MS.get();
        if (avail <= 0) {
            return 0L;
        }
        long take = cap > 0 ? Math.min(avail, cap) : avail;
        REMAINING_MS.set(avail - take);
        LOG.info(" .. [async-budget] claiming {}ms of {}ms (granted by '{}')",
                take, avail, REASON.get());
        return take;
    }

    /** Record what a claim actually consumed, for the end-of-test summary. */
    public static void spent(long millis) {
        if (millis > 0) {
            SPENT_MS.set(SPENT_MS.get() + millis);
        }
    }

    /**
     * Per-test summary: how much blind sleep was avoided. Cheap observability
     * so the saving is measurable rather than asserted.
     */
    public static String summary() {
        long granted = GRANTED_MS.get();
        long spent = SPENT_MS.get();
        if (granted <= 0) {
            return "";
        }
        return String.format(
                "async-budget: %dms deferred, %dms actually spent, %dms saved",
                granted, spent, Math.max(0, granted - spent));
    }

    /** Clear for the next test method. */
    public static void reset() {
        REMAINING_MS.set(0L);
        REASON.set("");
        GRANTED_MS.set(0L);
        SPENT_MS.set(0L);
    }
}
