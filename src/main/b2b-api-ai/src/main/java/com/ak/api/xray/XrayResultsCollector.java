// =============================================================================
// XrayResultsCollector -- per-thread current-test key + suite-level batch
// -----------------------------------------------------------------------------
// Two roles in one class because the state is intrinsically linked:
//
//   1. CURRENT_KEY (ThreadLocal) -- the jira_xray_id extracted from the
//      current test's row parameter. XrayReportListener.beforeInvocation
//      pulls it out of the row Map and stashes it here; afterInvocation
//      reads it back to build an XrayResult.
//
//   2. RESULTS (synchronized List) -- every XrayResult recorded during the
//      suite. Drained ONCE at suite end and POSTed to Xray in one batch.
//
// Thread-safety: parallel="classes" means each test method runs on one
// thread, so ThreadLocal cleanly isolates the current key. The results list
// is synchronized because listeners on different threads append to it.
// =============================================================================

package com.ak.api.xray;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

public final class XrayResultsCollector {

    private static final ThreadLocal<String> CURRENT_KEY = new ThreadLocal<>();
    private static final List<XrayResult> RESULTS = Collections.synchronizedList(new ArrayList<>());

    private XrayResultsCollector() { }

    // ---- per-test current key (ThreadLocal) ----

    public static void setCurrentKey(String key) {
        if (key == null || key.isBlank()) return;
        CURRENT_KEY.set(key.trim());
    }

    public static String getCurrentKey() {
        return CURRENT_KEY.get();
    }

    public static void clearCurrentKey() {
        CURRENT_KEY.remove();
    }

    // ---- suite-level batch ----

    public static void record(XrayResult r) {
        if (r == null || r.testKey() == null || r.testKey().isBlank()) return;
        RESULTS.add(r);
    }

    /** Return + clear all queued results (called once at suite end). */
    public static List<XrayResult> drain() {
        synchronized (RESULTS) {
            List<XrayResult> out = new ArrayList<>(RESULTS);
            RESULTS.clear();
            return out;
        }
    }
}
