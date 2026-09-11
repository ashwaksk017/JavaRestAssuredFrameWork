package com.ak.api.retry;

import java.util.Objects;
import java.util.function.Supplier;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import com.ak.api.config.Config;
import com.ak.api.db.Db;
import com.ak.api.rest.utilities.RestUtilities;

import io.restassured.response.Response;

/**
 * Condition-based waits for HTTP status, JsonPath, and DB column values.
 * ReadyAPI {@code Delay} steps should call {@link #delay} (one sleep
 * site) instead of inlining {@code Thread.sleep}.
 */
public final class Poller {

    private static final Logger LOG = LoggerFactory.getLogger(Poller.class);

    public static final long DEFAULT_TIMEOUT_MS = 15_000L;
    public static final long DEFAULT_INTERVAL_MS = 500L;

    private Poller() {}

    /**
     * Replacement for ReadyAPI delay / {@code Thread.sleep}. Logs when
     * the wait is 5s or longer so long Kafka/OTP pauses are not mistaken
     * for a hang.
     */
    /**
     * A ReadyAPI Delay.
     *
     * <p>By default this does NOT sleep: it registers the duration as an
     * {@link AsyncBudget} that any later step may spend retrying a failed
     * expectation. Nothing racy costs nothing; a racy step several positions
     * later can still spend the full wait. See {@link AsyncBudget} for why
     * polling only the immediately-following step was reverted.</p>
     *
     * <p>Use {@link #delayStrict} when the wait guards a NEGATIVE assertion --
     * deferring those weakens the assertion instead of breaking it.</p>
     *
     * <p>{@code -Drest.deferDelays=false} restores the original sleep.</p>
     */
    public static void delay(long millis, String reason) {
        if (millis <= 0) {
            return;
        }
        if (AsyncBudget.enabled()) {
            AsyncBudget.add(millis, reason);
            return;
        }
        delayStrict(millis, reason);
    }

    /**
     * An unconditional sleep. For waits that guard a negative assertion
     * ("wait, then verify nothing arrived"), where checking early would let
     * the test pass for the wrong reason.
     */
    public static void delayStrict(long millis, String reason) {
        if (millis <= 0) {
            return;
        }
        String why = (reason == null || reason.isEmpty()) ? "wait" : reason;
        if (millis >= 5_000L) {
            LOG.info(" .. [poller] {} -- sleeping {}ms (intentional wait)", why, millis);
        }
        sleepQuietly(millis);
        if (millis >= 5_000L) {
            LOG.info(" .. [poller] {} -- awake", why);
        }
    }

    public static Response untilStatus(Supplier<Response> call, int status,
                                       long timeoutMs) {
        return untilStatus(call, status, timeoutMs, DEFAULT_INTERVAL_MS);
    }

    public static Response untilStatus(Supplier<Response> call, int status,
                                       long timeoutMs, long intervalMs) {
        return until(call,
                res -> res != null && res.getStatusCode() == status,
                timeoutMs,
                intervalMs,
                "HTTP " + status);
    }

    public static Response untilJsonEquals(Supplier<Response> call,
                                           String jsonPath, String expected,
                                           long timeoutMs) {
        String want = expected == null ? "" : expected;
        return until(call, res -> {
            if (res == null) {
                return false;
            }
            String actual = RestUtilities.safeJsonExtract(res, jsonPath);
            return want.equals(actual == null ? "" : actual);
        }, timeoutMs, DEFAULT_INTERVAL_MS, jsonPath + "=" + want);
    }

    /**
     * Poll until JsonPath extracts a non-empty value (H4B
     * {@code alternateAccounts.salesforceId} after activate).
     */
    public static Response untilJsonNonEmpty(Supplier<Response> call,
                                             String jsonPath, long timeoutMs,
                                             long intervalMs) {
        return until(call, res -> {
            if (res == null) {
                return false;
            }
            String actual = RestUtilities.safeJsonExtract(res, jsonPath);
            return actual != null && !actual.isEmpty();
        }, timeoutMs, intervalMs, jsonPath + " non-empty");
    }

    /**
     * Poll {@code sql} until {@code column} is non-empty and stable.
     * Delegates to {@link Db#pollUntilStable(String, String)}.
     */
    public static String untilDbColumn(String sql, String column) {
        return Db.pollUntilStable(sql, column);
    }

    public static <T> T until(Supplier<T> call, java.util.function.Predicate<T> ok,
                              long timeoutMs, long intervalMs, String label) {
        Objects.requireNonNull(call, "call");
        Objects.requireNonNull(ok, "ok");
        long timeout = timeoutMs > 0 ? timeoutMs : Config.getInt(
                "poll.timeoutMs", (int) DEFAULT_TIMEOUT_MS);
        long interval = intervalMs > 0 ? intervalMs : Config.getInt(
                "poll.intervalMs", (int) DEFAULT_INTERVAL_MS);
        long deadline = System.currentTimeMillis() + timeout;
        T last = call.get();
        int attempts = 1;
        while (!ok.test(last) && System.currentTimeMillis() < deadline) {
            LOG.info(" .. [poller] {} attempt={} not yet matched -- wait {}ms",
                    label, attempts, interval);
            sleepQuietly(interval);
            attempts++;
            last = call.get();
        }
        if (attempts > 1) {
            LOG.info(" .. [poller] {} finished after {} attempt(s)", label, attempts);
        }
        return last;
    }

    public static void sleepQuietly(long millis) {
        if (millis <= 0) {
            return;
        }
        try {
            Thread.sleep(millis);
        } catch (InterruptedException ie) {
            Thread.currentThread().interrupt();
        }
    }
}
