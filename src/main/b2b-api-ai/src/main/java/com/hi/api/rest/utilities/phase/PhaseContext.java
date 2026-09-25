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

    /** The response of an earlier phase in this flow, or null. */
    public Response response(String step) {
        return responses.get(step);
    }

    /** Record a phase's response; a repeated step name keeps the latest, as the old field did. */
    public void record(String step, Response res) {
        if (step != null && res != null) {
            responses.put(step, res);
        }
    }

    /** Step names seen so far, in order -- for diagnostics. */
    public Iterable<String> recordedSteps() {
        return responses.keySet();
    }
}
