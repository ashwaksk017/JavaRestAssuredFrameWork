// =============================================================================
// B2B722LegacyStyleTest
// -----------------------------------------------------------------------------
// The SAME ReadyAPI case as the converted
// `B2B-722_post_activate_program_account_204_1`, written in the legacy
// Rest Assured style: plain RestUtilities calls, templates, a CSV row, and
// explicit assertions. No fluent chain, no CaseRegistry, no phase vocabulary.
//
// It exists to answer a specific question: what does this framework give you
// on a project that has NO ReadyAPI XML to convert? Everything below depends
// only on committed code -- RestUtilities, Headers, Config, Expected,
// BaseApiTest, Db -- so it compiles and runs on a bare clone after
// `--bootstrap`, with no generated client and no generated support classes.
//
// WHAT YOU GIVE UP versus the chained version
//   * ids are wired by hand (captureId below) instead of a phase publishing
//     them into ctx for the next phase
//   * the URL is built here rather than resolved from a generated client
//   * no staged types, so nothing stops you calling activate before create
//   * no ManualCleanup hook-up; teardown is yours
//
// WHAT YOU KEEP
//   * templates + #placeholder# substitution, including strict mode
//   * the CSV-per-method data provider
//   * Allure request/response attachments (BaseApiTest installs the filters)
//   * redaction, soft assertions, the failure digest
// =============================================================================

package com.ak.api.tests.legacy;

import java.io.InputStreamReader;
import java.util.HashMap;
import java.util.Map;

import org.testng.annotations.Test;

import com.ak.api.config.Config;
import com.ak.api.data.Expected;
import com.ak.api.data.PerMethodCsvDataProvider;
import com.ak.api.db.Db;
import com.ak.api.rest.utilities.Headers;
import com.ak.api.rest.utilities.RestUtilities;
import com.ak.api.tests.BaseApiTest;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;
import io.restassured.response.Response;

@Epic("API Automation")
@Feature("Program account activation (legacy style)")
public class B2B722LegacyStyleTest extends BaseApiTest {

    /** Bearer token for every call in this test, fetched once per method. */
    private String token;

    private String base() {
        return Config.baseUrl();
    }

    private Map<String, String> auth() {
        // bearer() adds the "Bearer " prefix itself, so `token` is stored raw.
        return Headers.builder()
                .contentTypeJson()
                .acceptJson()
                .bearer(token)
                .build();
    }

    /** Resolve a template against the CSV row, strict so a typo fails loudly. */
    private String body(String templateResource, Map<String, String> row) throws Exception {
        InputStreamReader tpl = RestUtilities.getRequestTemplate(templateResource);
        // strict = true: an unresolved #placeholder# throws instead of
        // silently becoming the literal "null". The chained version defaults
        // to non-strict for ReadyAPI compatibility; a hand-written test has
        // no such legacy to honour, so fail on a typo.
        return RestUtilities.mapJsonValues(tpl, new HashMap<>(row), true);
    }

    @Test(dataProvider = "rows",
          dataProviderClass = PerMethodCsvDataProvider.class,
          groups = {"legacy", "onboarding"})
    @Story("Activate sets attestation status and source on the account")
    @Description("""
            Legacy-style port of ReadyAPI B2B-722. Same five calls as the
            converted case: token, enrol, create account, activate, read back.
            Ids are carried in local variables rather than ctx.

            DELIBERATE DEVIATION, same as the chained port: the XML creates the
            account BEFORE the enrol, against a hardcoded guestID the enrol
            never feeds. This enrols first and uses a fresh guest.
            """)
    public void activateProgramAccount(Map<String, String> row) throws Exception {
        String testCaseId = row.getOrDefault("test_case_id", "B2B-722-legacy");
        Expected exp = Expected.from(row.get("expected"));

        // ---- 1. token -------------------------------------------------------
        // AuthHelper primes ctx for the chained path; here we ask for it
        // directly and keep it in a field.
        token = com.ak.api.auth.TokenCache.getAccessToken();
        if (token == null || token.isEmpty()) {
            Map<String, String> ctx = new HashMap<>();
            com.ak.api.rest.utilities.AuthHelper.primeClientCredentialsToken(ctx);
            token = ctx.getOrDefault("accessToken", "");
        }
        if (token == null) {
            token = "";
        }
        // Kept RAW: Headers.bearer() adds the prefix. Pre-prefixing here
        // would produce "Bearer Bearer ...".
        if (token.regionMatches(true, 0, "Bearer ", 0, 7)) {
            token = token.substring("Bearer ".length());
        }
        softAssert.assertFalse(token.isEmpty(),
                "no access token -- check api_config.* in program_configuration.json");

        // ---- 2. enrol the owner --------------------------------------------
        String enrolBody = body("templates/manual/b2b722_enroll.json", row);
        RestUtilities.logRequestBody(testCaseId, holder, enrolBody);
        Response enrol = RestUtilities.getResponsePost(
                enrolBody, base() + "/realms/guests/enroll", auth());
        RestUtilities.logResponseBody(testCaseId, holder,
                RestUtilities.getResponseAsString(enrol));
        softAssert.assertEquals(enrol.statusCode(),
                exp.getInt("enrollGuest_status_code", 200),
                "enrol status for " + testCaseId);
        String guestId = RestUtilities.safeJsonExtract(enrol, "guestId");
        softAssert.assertFalse(guestId.isEmpty(), "enrol returned no guestId");

        // ---- 3. create the program account ---------------------------------
        // The chained version publishes guestId into ctx; here it is a local
        // and the URL is assembled by hand.
        String acctBody = body("templates/manual/b2b722_create_account.json", row);
        RestUtilities.logRequestBody(testCaseId, holder, acctBody);
        Response created = RestUtilities.getResponsePost(
                acctBody, base() + "/guests/" + guestId + "/businesses", auth());
        RestUtilities.logResponseBody(testCaseId, holder,
                RestUtilities.getResponseAsString(created));
        softAssert.assertEquals(created.statusCode(),
                exp.getInt("createProgramAccount_status_code", 200),
                "create-account status for " + testCaseId);
        String accountId = RestUtilities.safeJsonExtract(created, "accountId");
        softAssert.assertFalse(accountId.isEmpty(), "create returned no accountId");

        // ---- 4. arrange attestation state (the XML's DBupdate step) --------
        // No db() phase here; call Db directly. Same guard the DSL applies:
        // a step the test named must not be skipped in silence.
        String websiteDomain = row.getOrDefault("website_domain", "");
        if (!Db.isConfigured()) {
            throw new IllegalStateException(
                    "this case arranges attestation state in the database, but "
                    + "db.url is not set in program_configuration.json");
        }
        int rows = Db.execute(
                "UPDATE account SET status=?, attestation_status=?, "
                + "attestation_source=?, attestation_failure_reason=? WHERE web_site=?",
                "L", "p", "leadspace", "lowConfidence", websiteDomain);
        if (rows == 0) {
            // Same trap as the chained version: a WHERE that matches nothing
            // arranges nothing, and the failure surfaces two calls later.
            softAssert.fail("DB update matched no rows for web_site='" + websiteDomain
                    + "' -- the attestation assertions below would fail for the "
                    + "wrong reason");
        }

        // ---- 5. activate ----------------------------------------------------
        String activateBody = body("templates/manual/b2b722_activate.json", row);
        RestUtilities.logRequestBody(testCaseId, holder, activateBody);
        Response activated = RestUtilities.getResponsePost(
                activateBody, base() + "/businesses/" + accountId + "/activate", auth());
        RestUtilities.logResponseBody(testCaseId, holder,
                RestUtilities.getResponseAsString(activated));
        softAssert.assertEquals(activated.statusCode(),
                exp.getInt("activateProgramAccount_status_code", 204),
                "activate status for " + testCaseId);

        // ---- 6. read back and assert attestation ---------------------------
        Response read = RestUtilities.getResponseGet(
                base() + "/businesses/" + accountId, auth());
        RestUtilities.logResponseBody(testCaseId, holder,
                RestUtilities.getResponseAsString(read));
        softAssert.assertEquals(read.statusCode(),
                exp.getInt("readProgramAccount_status_code", 200),
                "read-back status for " + testCaseId);

        softAssert.assertEquals(read.jsonPath().getString("attestationSummary.status"),
                row.getOrDefault("expected_attestation_status", "pending"),
                "attestationSummary.status for " + testCaseId);
        softAssert.assertEquals(read.jsonPath().getString("attestationSummary.source"),
                row.getOrDefault("expected_attestation_source", "leadspace"),
                "attestationSummary.source for " + testCaseId);
        softAssert.assertEquals(
                read.jsonPath().getString("attestationSummary.failureReason"),
                row.getOrDefault("expected_attestation_failureReason", "lowConfidence"),
                "attestationSummary.failureReason for " + testCaseId);

        softAssert.assertAll();
    }
}
