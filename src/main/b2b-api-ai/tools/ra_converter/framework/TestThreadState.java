package com.ak.api.support;

// ra_converter-framework-rev: 2
// Bumped whenever this bundled file changes. The converter
// SKIPS author-editable files that already exist, so without a
// revision it cannot tell an author's edit from a copy left by
// an older converter -- and an in-method change (no new symbol)
// would silently never reach existing trees.

import org.testng.asserts.SoftAssert;

import com.ak.api.rest.utilities.RestLoggerUtilityDataHolder;

/**
 * The current test method's {@link SoftAssert} and log holder, per thread.
 *
 * <p>A test class instance is shared across its {@code @Test} methods. Under
 * {@code parallel="methods"} that means sibling methods run concurrently on
 * one instance, so a field read gives whichever method wrote last -- soft
 * assertions and log lines land on the wrong test. Binding them per THREAD
 * keeps each method's state to itself.</p>
 *
 * <p>{@link ImportedScenario} falls back to the passed-in value when nothing
 * is bound, so this is additive: a suite running {@code parallel="classes"}
 * (one method per thread at a time) behaves exactly as before.</p>
 *
 * <p>Bound in {@code @BeforeMethod} and cleared in {@code @AfterMethod}.
 * Clearing matters: TestNG reuses threads from a pool, so a value left
 * behind would leak into the next test to run on that thread.</p>
 */
public final class TestThreadState {

    private static final ThreadLocal<SoftAssert> SOFT = new ThreadLocal<>();
    private static final ThreadLocal<RestLoggerUtilityDataHolder> HOLDER =
            new ThreadLocal<>();

    private TestThreadState() {}

    /** Bind this thread's per-method state. Either argument may be null. */
    public static void bind(SoftAssert softAssert,
                            RestLoggerUtilityDataHolder holder) {
        SOFT.set(softAssert);
        HOLDER.set(holder);
    }

    /** This thread's SoftAssert, or null when nothing is bound. */
    public static SoftAssert softAssert() {
        return SOFT.get();
    }

    /** This thread's log holder, or null when nothing is bound. */
    public static RestLoggerUtilityDataHolder holder() {
        return HOLDER.get();
    }

    /** Release both, so a pooled thread cannot carry state into the next test. */
    public static void clear() {
        SOFT.remove();
        HOLDER.remove();
    }
}
