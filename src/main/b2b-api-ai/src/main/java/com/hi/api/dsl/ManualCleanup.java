package com.hi.api.dsl;

import java.lang.reflect.Method;
import java.util.LinkedHashSet;
import java.util.Map;
import java.util.Set;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import com.hi.api.support.ImportedScenario;

/**
 * Per-test cleanup for HAND-WRITTEN tests.
 *
 * <h2>Why this is reflective rather than real SQL</h2>
 *
 * <p>Generated tests call {@code SuiteCleanup.afterEachTest(ctx)}, which
 * deletes only the rows that test created. Hand-written tests could not: that
 * class lives in {@code com.hi.api.support.<suite>}, which is generated and
 * gitignored, and naming it directly would couple a committed file to one
 * converted XML -- it would stop compiling for anyone who converted a
 * different suite.</p>
 *
 * <p>So manual tests cleaned up nothing, and every run left {@code account}
 * and {@code account_member} rows behind.</p>
 *
 * <p>The fix delegates by reflection instead of re-implementing the DELETEs.
 * Duplicating destructive SQL into committed code would mean two definitions
 * of "what this test created" that could drift apart -- and the copy that
 * drifts deletes the wrong rows. There is exactly one definition, and it
 * stays in the generated file that the converter keeps in step with the
 * schema.</p>
 *
 * <p>FAIL-SOFT: a missing class, a missing method, or a cleanup that throws
 * is logged and swallowed. Cleanup runs in {@code @AfterMethod}; letting it
 * throw there would mask the actual test result.</p>
 */
public final class ManualCleanup {

    private static final Logger LOG = LoggerFactory.getLogger(ManualCleanup.class);

    private ManualCleanup() {
    }

    /**
     * Delete the rows this test created, if a generated cleanup is present.
     *
     * <p>Call from {@code @AfterMethod}, before {@code ImportedScenario.unbind()}
     * -- the ctx it reads is the one the chain wrote ids into.</p>
     */
    public static void afterEachTest(Map<String, String> ctx) {
        for (String suite : candidateSuites()) {
            String fqn = "com.hi.api.support." + suite + ".SuiteCleanup";
            try {
                Class<?> type = Class.forName(fqn);
                Method m = type.getMethod("afterEachTest", Map.class);
                m.invoke(null, ctx);
                LOG.info("ManualCleanup: ran {}#afterEachTest", fqn);
                return;
            } catch (ClassNotFoundException | NoSuchMethodException notThisOne) {
                // Try the next candidate -- this suite was not converted here.
                LOG.debug("ManualCleanup: no cleanup at {}", fqn);
            } catch (ReflectiveOperationException | RuntimeException e) {
                // Present but failed. Report and stop: a second attempt against
                // a different suite would delete rows this test never created.
                LOG.warn("ManualCleanup: {}#afterEachTest failed -- test data may "
                        + "be left behind: {}", fqn, e.getMessage());
                return;
            }
        }
        LOG.warn("ManualCleanup: no generated SuiteCleanup found (tried {}). "
                + "Rows created by this test are NOT being deleted. Convert that "
                + "suite, or name it explicitly with -Dmanual.client=<TheClient>.",
                candidateSuites());
    }

    /**
     * Suite package names to try, best first.
     *
     * <p>The bound name comes first, but a manual test binds the literal
     * {@code "manual"}, which is not a generated package. What actually
     * resolves is the suite of the CLIENT bound to this thread
     * ({@code FooClient -> foo}) -- the client the test constructed, so it
     * names the suite those rows were created against and cannot select a
     * different suite's DELETEs. {@code -Dmanual.client} stays last as an
     * explicit override.</p>
     *
     * <p>This is the same derivation {@link Template} uses, shared through
     * {@link SuiteName} on purpose: templates and cleanup disagreeing about
     * the suite would mean writing against one and cleaning up another.</p>
     */
    private static Set<String> candidateSuites() {
        Set<String> out = new LinkedHashSet<>();
        try {
            String bound = ImportedScenario.current().suiteName;
            if (bound != null && !bound.isEmpty()) {
                out.add(bound);
            }
        } catch (RuntimeException notBound) {
            // Cleanup can be called with nothing bound; fall through.
            LOG.debug("ManualCleanup: no bound session");
        }
        String fromBoundClient = SuiteName.ofBoundClient();
        if (fromBoundClient != null) {
            out.add(fromBoundClient);
        }
        String configured = SuiteName.ofConfiguredClient();
        if (configured != null) {
            out.add(configured);
        }
        return out;
    }
}
