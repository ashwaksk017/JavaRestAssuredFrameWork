package com.hi.api.dsl;

import org.testng.SkipException;

/**
 * Precondition for guards that pin CURATED template handles.
 *
 * <h2>Why this exists</h2>
 *
 * <p>{@code Template.singleMemberOnboarding} and its three siblings are
 * business names for (case, step) pairs in ONE suite's {@code _index.csv}.
 * The guards around them are worth having -- they catch a ReadyAPI rename
 * before a test sends the wrong body -- but they can only run where that
 * suite has been converted.</p>
 *
 * <p>{@code src/main/resources/templates/} is generated and gitignored, so
 * what is on the classpath depends entirely on which XML the person in front
 * of the repo happened to convert. Converting a DIFFERENT suite turned five
 * of these guards into hard failures naming a suite that machine had never
 * heard of, on a tree where nothing was wrong:</p>
 *
 * <pre>
 * No template index on the classpath at
 * templates/&lt;someOtherSuite&gt;/_index.csv
 * </pre>
 *
 * <p>A guard that cannot run is not a failure. Skipping says so, names the
 * resource, and leaves the negative controls -- which assert that resolution
 * FAILS, and need no index at all -- running everywhere.</p>
 *
 * <p>This is deliberately not a fallback to "whatever suite happens to be
 * converted". The curated handles name b2b business concepts; resolving them
 * against another project's index would either miss or, worse, hit a
 * same-named step and assert nothing.</p>
 */
final class ConvertedSuite {

    private ConvertedSuite() {}

    /**
     * Skip the calling test unless {@code suite} has a template index.
     *
     * @param suite converted suite name, e.g. the one a curated handle
     *              was authored against
     */
    static void require(String suite) {
        String resource = "templates/" + suite + "/_index.csv";
        if (Thread.currentThread().getContextClassLoader()
                .getResource(resource) == null) {
            throw new SkipException(
                    "SKIPPED, not failed: " + resource + " is not on the "
                    + "classpath. This guard pins curated template handles "
                    + "against the '" + suite + "' index, so it can only run "
                    + "where that XML has been converted. Convert it to run "
                    + "this guard; on a tree that converts a different suite "
                    + "the skip is the correct outcome.");
        }
    }
}
