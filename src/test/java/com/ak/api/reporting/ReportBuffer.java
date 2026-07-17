// =============================================================================
// ReportBuffer -- thread-local capture of every HTTP exchange in a test
// -----------------------------------------------------------------------------
// The RestAssuredRecordingFilter appends to this buffer on every request/response.
// The ExtentReportListener reads (and clears) the buffer at test end, whether
// the test passed or failed, and attaches the request + response body to the
// ExtentTest node.
//
// Allure receives the same exchanges via the AllureRestAssured filter, which is
// wired in BaseApiTest at suite start. Allure attaches automatically -- no
// listener code needed.
//
// Thread-safety: one buffer per thread. TestNG's parallel="classes" means each
// test method runs on one thread from start to finish, so no cross-thread
// contamination.
// =============================================================================

package com.ak.api.reporting;

import java.util.ArrayList;
import java.util.List;

public final class ReportBuffer {

    private static final ThreadLocal<List<Exchange>> BUFFER = ThreadLocal.withInitial(ArrayList::new);

    private ReportBuffer() {
    }

    public static void add(Exchange e) {
        BUFFER.get().add(e);
    }

    /** Return current buffered exchanges and reset the thread-local list. */
    public static List<Exchange> drain() {
        List<Exchange> out = new ArrayList<>(BUFFER.get());
        BUFFER.get().clear();
        return out;
    }

    /** Read-only copy of the current buffer -- does NOT clear. Safe to call
     *  from multiple listeners that want to see the same exchanges. */
    public static List<Exchange> snapshot() {
        return new ArrayList<>(BUFFER.get());
    }

    /** Replace the thread-local buffer with a fresh empty list. Called from
     *  BaseApiTest.@BeforeMethod so every test starts with a clean slate,
     *  which lets multiple listeners share the buffer via snapshot(). */
    public static void reset() {
        BUFFER.get().clear();
    }

    /** Immutable record of one request/response round-trip. */
    public static final class Exchange {
        public final String method;
        public final String url;
        public final String requestBody;
        public final String responseBody;
        public final int statusCode;
        public final long responseTimeMs;

        public Exchange(String method, String url, String requestBody,
                        String responseBody, int statusCode, long responseTimeMs) {
            this.method = method;
            this.url = url;
            this.requestBody = requestBody;
            this.responseBody = responseBody;
            this.statusCode = statusCode;
            this.responseTimeMs = responseTimeMs;
        }
    }
}
