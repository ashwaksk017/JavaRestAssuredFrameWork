package com.ak.api.gitlab;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

public final class GitLabResultsCollector {

    private static final ThreadLocal<String> CURRENT_IID = new ThreadLocal<>();
    private static final List<GitLabResult> RESULTS =
            Collections.synchronizedList(new ArrayList<>());

    private GitLabResultsCollector() {}

    public static void setCurrentTestCaseIid(String iid) {
        if (iid == null || iid.isBlank()) return;
        CURRENT_IID.set(iid.trim());
    }

    public static String getCurrentTestCaseIid() {
        return CURRENT_IID.get();
    }

    public static void clearCurrentTestCaseIid() {
        CURRENT_IID.remove();
    }

    public static void record(GitLabResult r) {
        if (r == null) return;
        RESULTS.add(r);
    }

    public static List<GitLabResult> drain() {
        synchronized (RESULTS) {
            List<GitLabResult> out = new ArrayList<>(RESULTS);
            RESULTS.clear();
            return out;
        }
    }
}
