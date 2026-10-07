package com.hi.api.rest.utilities.phase;

import java.util.LinkedHashMap;
import java.util.Map;

import org.testng.asserts.SoftAssert;

import com.hi.api.rest.utilities.RestLoggerUtilityDataHolder;
import com.hi.api.support.ImportedRestClient;

import io.restassured.response.Response;

/**
 * The state one flow carries between phases.
 *
 * <p>This is what the generated {@code ScenarioSteps} fields were --
 * {@code client, ctx, row, softAssert, holder, testCaseId} -- plus the
 * responses, which the generated code kept as one field per step name
 * ({@code https_Get_verify_200Res}) declared on the base class for every
 * step any suite ever used. A map keyed by step name is the same thing
 * without the 300 field declarations.</p>
 */
public final class PhaseContext {

    public final ImportedRestClient client;
    public final Map<String, String> ctx;
    public final Map<String, String> row;
    public final SoftAssert softAssert;
    public final RestLoggerUtilityDataHolder holder;
    public final String testCaseId;
    private final Map<String, Response> responses = new LinkedHashMap<>();
    /** The same responses under {@link #normalise}d names; see {@link #response}. */
    private final Map<String, Response> byNormalisedName = new LinkedHashMap<>();

    public PhaseContext(ImportedRestClient client, Map<String, String> ctx,
                        Map<String, String> row, SoftAssert softAssert,
                        RestLoggerUtilityDataHolder holder, String testCaseId) {
        this.client = client;
        this.ctx = ctx;
        this.row = row;
        this.softAssert = softAssert;
        this.holder = holder;
        this.testCaseId = testCaseId;
    }

    /**
     * The response of an earlier phase in this flow, or null.
     *
     * <p>A phase records under its SANITISED step name
     * ({@code http_request_200_3_CreatePendingAccountmember}), while a
     * reference the converter lifted out of the ReadyAPI project names the
     * step as its author spelled it
     * ({@code http_request_200_3-CreatePendingAccountmember}). The two never
     * matched, so the read came back null, the path parameter resolved empty
     * and the next call died on a trailing empty segment -- with the value
     * sitting in a 200 response one line above. 191 reads in 9 suites.</p>
     *
     * <p>The exact name is tried first, so a read that works today resolves
     * to the same response it always did; the normalised name is consulted
     * only where the exact one finds nothing.</p>
     */
    public Response response(String step) {
        Response exact = responses.get(step);
        if (exact != null || step == null) {
            return exact;
        }
        return byNormalisedName.get(normalise(step));
    }

    /** Record a phase's response; a repeated step name keeps the latest, as the old field did. */
    public void record(String step, Response res) {
        if (step != null && res != null) {
            responses.put(step, res);
            byNormalisedName.put(normalise(step), res);
        }
    }

    /**
     * A step name as the converter's {@code sanitize_identifier} spells it:
     * every run of non-alphanumerics becomes one underscore, none at the ends.
     */
    static String normalise(String step) {
        String s = step.replaceAll("[^A-Za-z0-9]+", "_");
        int from = s.startsWith("_") ? 1 : 0;
        int to = s.endsWith("_") && s.length() > from ? s.length() - 1 : s.length();
        return s.substring(from, to);
    }

    /** Step names seen so far, in order -- for diagnostics. */
    public Iterable<String> recordedSteps() {
        return responses.keySet();
    }
}
