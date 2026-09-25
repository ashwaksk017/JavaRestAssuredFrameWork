package com.hi.api.tests;

import java.util.Map;

/**
 * Preamble helper for ReadyAPI-imported {@code @Test} methods.
 *
 * <p>Collapses the ~25-line start-of-method ritual (preserve class-level
 * {@code accessToken}, clear ctx, expand CSV faker tokens, attach Allure
 * parameters) into one call. Does <em>not</em> run {@code SetupHelper.flow_A}
 * -- that stays explicit so a reader still sees the token bootstrap.</p>
 *
 * <p><b>Before</b> (B2B-9098):</p>
 * <pre>
 * String __auth = ctx.get("accessToken");
 * ctx.clear();
 * if (__auth != null) ctx.put("accessToken", __auth);
 * row = PlaceholderResolver.resolveRow(row, ctx);
 * io.qameta.allure.Allure.getLifecycle().updateTestCase(tc -&gt; { ... });
 * </pre>
 *
 * <p><b>After:</b></p>
 * <pre>
 * row = ImportedTest.begin(ctx, row, testCaseId);
 * SetupHelper.flow_A(client, ctx, row, softAssert, holder, testCaseId);
 * </pre>
 */
public final class ImportedTest {

    private ImportedTest() {}

    /**
     * Reset per-row ctx (keeping a primed {@code accessToken}), expand
     * {@code <<faker>>} / {@code ${property}} tokens in the CSV row, and
     * publish the row as Allure parameters. Returns the resolved row.
     */
    public static Map<String, String> begin(Map<String, String> ctx,
                                            Map<String, String> row,
                                            String testCaseId) {
        return com.hi.api.support.ImportedScenario.begin(ctx, row, testCaseId);
    }
}
