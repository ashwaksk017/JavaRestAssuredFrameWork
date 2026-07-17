// =============================================================================
// @XrayTest -- annotation-based Jira Xray test-key association
// -----------------------------------------------------------------------------
// Companion to the datasheet-driven `jira_xray_id` column. Data-driven tests
// carry the ID per-row (so one test class can service many tickets); tests
// that don't take a row parameter -- smoke tests, single-shot integration
// tests, unit-style checks -- declare it at the method with this annotation.
//
//   @XrayTest("PROJ-101")
//   @Test(groups = {"smoke"})
//   public void my_single_shot_test() { ... }
//
// Precedence when both are present on the same run:
//   1. Row column `jira_xray_id` -- most specific, wins if non-blank
//   2. @XrayTest(...) on the method -- fallback for tests without rows
//   3. Neither -- test isn't synced to Xray (silent)
//
// XrayReportListener reads this annotation via reflection at
// IInvokedMethodListener.beforeInvocation time, so no per-test wiring needed.
// =============================================================================

package com.ak.api.xray;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

@Retention(RetentionPolicy.RUNTIME)
@Target(ElementType.METHOD)
public @interface XrayTest {
    /** Jira Xray test key, e.g. "PROJ-101". */
    String value();
}
