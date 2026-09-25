package com.hi.api.dsl;

import java.util.Locale;

import com.hi.api.config.Config;
import com.hi.api.support.ImportedScenario;

/**
 * Which converted suite a HAND-WRITTEN test is running against.
 *
 * <h2>Why this exists</h2>
 *
 * <p>A generated test binds its real suite id ({@code bind(..., "programaccountregression")}),
 * so everything that keys off {@code ImportedScenario.current().suiteName} just
 * works. A hand-written test binds the literal {@code "manual"}, which is not a
 * generated package and has no {@code templates/manual/} tree -- so every
 * consumer of the bound name degraded, each routing around it differently.</p>
 *
 * <p>{@link Template} and {@link ManualCleanup} both worked around it by
 * deriving the suite from {@code -Dmanual.client}. That made a command-line
 * flag load-bearing: forget it and templates fail to resolve, and cleanup
 * silently deletes nothing while the test still passes -- leaving real rows
 * behind with only a WARN in the log.</p>
 *
 * <h2>Why the bound client, not a classpath scan</h2>
 *
 * <p>The session already holds the client the test constructed. Its type names
 * the suite exactly ({@code FooClient -> foo}), so no flag and no guessing are
 * needed. Scanning {@code rest/clients} for "the" client was rejected: with
 * several suites converted, a wrong pick resolves another suite's templates AND
 * invokes its {@code SuiteCleanup}, which runs DELETEs. Reading the instance
 * cannot pick the wrong suite, because it reports the client actually sending
 * the requests.</p>
 *
 * <p>Derivation lives here once, not in each caller, because templates and
 * cleanup disagreeing about the suite is precisely the failure that matters:
 * one would write against a suite the other then cleaned up.</p>
 */
final class SuiteName {

    private static final String CLIENT_SUFFIX = "Client";

    private SuiteName() {
    }

    /**
     * {@code FooClient -> foo}, or {@code null} if that is not a client name.
     *
     * <p>A bare {@code "Client"} yields {@code null} rather than the empty
     * string, which would otherwise be offered as a candidate suite.</p>
     *
     * <p>Accepts a FULLY QUALIFIED name too. One caller passes
     * {@code getSimpleName()}, but the other passes {@code -Dmanual.client}
     * verbatim, and CreateTestCase.md tells authors to set that to
     * {@code com.hi.api.rest.manual.client.ManualClient} -- a bare name
     * resolves under {@code rest.clients}, so the scaffold needs the package.
     * That string still ends in {@code Client}, so the old code stripped only
     * the suffix and produced the suite
     * {@code com.hi.api.rest.manual.client.manual}: no SuiteCleanup matched,
     * and a real run reported "Rows created by this test are NOT being
     * deleted" while every other part of the test passed.</p>
     */
    static String fromClientSimpleName(String clientName) {
        if (clientName == null) {
            return null;
        }
        int lastDot = clientName.lastIndexOf('.');
        String simpleName = lastDot >= 0
                ? clientName.substring(lastDot + 1)
                : clientName;
        if (!simpleName.endsWith(CLIENT_SUFFIX)
                || simpleName.length() == CLIENT_SUFFIX.length()) {
            return null;
        }
        return simpleName
                .substring(0, simpleName.length() - CLIENT_SUFFIX.length())
                .toLowerCase(Locale.ROOT);
    }

    /**
     * The suite of the client bound to this thread, or {@code null}.
     *
     * <p>Null whenever there is nothing to read -- no session, or a session
     * bound without a client. Callers treat that as "no opinion" and fall
     * through; resolution is never guessed from an absent binding.</p>
     */
    static String ofBoundClient() {
        Object client;
        try {
            client = ImportedScenario.current().client;
        } catch (RuntimeException notBound) {
            return null;
        }
        return client == null
                ? null
                : fromClientSimpleName(client.getClass().getSimpleName());
    }

    /**
     * The suite named by {@code -Dmanual.client}, or {@code null}.
     *
     * <p>Kept as an explicit override for running a manual test against a
     * suite other than its default client's.</p>
     */
    static String ofConfiguredClient() {
        return fromClientSimpleName(Config.get("manual.client", ""));
    }
}
