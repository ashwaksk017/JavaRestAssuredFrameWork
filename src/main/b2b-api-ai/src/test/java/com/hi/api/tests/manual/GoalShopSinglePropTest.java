package com.hi.api.tests.manual;

import java.util.LinkedHashMap;
import java.util.Map;

import org.testng.SkipException;
import org.testng.annotations.AfterMethod;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.BeforeMethod;
import org.testng.annotations.Test;

import com.hi.api.config.Config;
import com.hi.api.data.PerMethodCsvDataProvider;
import com.hi.api.dsl.ManualCleanup;
import com.hi.api.support.ImportedRestClient;
import com.hi.api.support.ImportedScenario;
import com.hi.api.tests.BaseApiTest;

import io.qameta.allure.Allure;
import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;
import io.restassured.response.Response;

/**
 * Hand-written group-shopping scenario for the goal suite -- the authoring
 * surface, not converter output.
 *
 * <p>Lives under {@code tests/manual/} deliberately: a converter run with
 * {@code --clean} deletes {@code tests/imported/<suite>/} and that suite's
 * support, CSV and template tree. This package is never touched. Its data
 * lives at {@code csv/GoalShopSinglePropTest/<methodName>.csv}, the
 * simple-class-name path the provider uses outside {@code tests.imported}.</p>
 *
 * <h2>Why this does not chain CustomerOnboarding</h2>
 *
 * <p>{@code CustomerOnboarding.start(row)} is the fluent entry the b2b manual
 * tests use, and its staged verbs -- {@code enrollOwner},
 * {@code createH4LAccount}, {@code confirmOwner} -- are the vocabulary of
 * customer onboarding. This project shops group availability: there is no
 * owner to enrol and no account to create, so chaining that vocabulary would
 * mean inventing verbs the flow does not have.</p>
 *
 * <p>So this test binds the scenario (which is what gives it the bound client,
 * the isolated ctx, faker expansion, Allure attachments, redaction and
 * cleanup) and then calls the endpoint through {@code ImportedRestClient} --
 * the committed interface, not a generated class. That is the honest middle
 * path: everything the framework offers except a story chain that does not
 * exist for this domain yet.</p>
 *
 *
 * <h2>The datasheet</h2>
 *
 * <p>{@code src/test/resources/csv/GoalShopSinglePropTest/shopSingleProp_returnsAvailability.csv}
 * -- NOT committed. {@code src/test/resources/csv/} is gitignored wholesale
 * because rows carry property codes, dates and ids and this repo is public,
 * so a clone has to write one. Columns this test reads:</p>
 *
 * <pre>
 * description                     free text, shown in the run output
 * test_case_id                    id stamped on the scenario
 * propCode                        property to shop, e.g. a 5-letter code
 * qry_GET_Shop_arrivalDate        yyyy-MM-dd  -> query string
 * qry_GET_Shop_departureDate      yyyy-MM-dd  -> query string
 * qry_GET_Shop_peakRooms          integer     -> query string
 * expected_GET_Shop_status_code   expected HTTP status (default 200)
 * </pre>
 *
 * <p>Every {@code qry_GET_Shop_*} column becomes a query parameter named
 * after its suffix, so adding {@code qry_GET_Shop_numAttendees} sends
 * {@code numAttendees} with no Java change. Add a ROW for a new data
 * variant; do not copy the {@code @Test} method.</p>
 * <p>When the goal suite grows a real story -- shop, hold, confirm, pay -- the
 * right move is a committed DSL entry beside {@code CustomerOnboarding}, and
 * this test becomes a chain. It is written so that change is additive.</p>
 */
@Epic("Manual")
@Feature("Group shopping")
public class GoalShopSinglePropTest extends BaseApiTest {

    /** Suite whose client, templates and cleanup this test binds to. */
    private static final String SUITE = "partialgoalregression";

    /**
     * Resolved by NAME, not by type.
     *
     * <p>A hand-written test must not import a generated client class: it
     * only exists after converting the XML that produced it, so naming it
     * here would make this file fail to compile for anyone converting a
     * different suite. Override with {@code -Dmanual.goal.client=YourClient}.</p>
     */
    private ImportedRestClient client;

    /**
     * Per-class SEED for the scenario context -- NOT the map the test writes
     * to. {@code ImportedScenario.bind} calls isolateCtx, so each {@code @Test}
     * gets its own map seeded with the accessToken; cleanup reads it back via
     * boundCtxOr, which is why cleanup runs BEFORE unbind().
     */
    private final Map<String, String> ctx =
            java.util.Collections.synchronizedMap(new LinkedHashMap<>());

    @BeforeClass(alwaysRun = true)
    public void initClient() {
        String baseUrl = Config.get("base_url", Config.baseUrl());
        String name = Config.get("manual.goal.client", "PartialgoalregressionClient");
        String fqn = name.contains(".") ? name : "com.hi.api.rest.clients." + name;
        try {
            Class<?> type = Class.forName(fqn);
            client = (ImportedRestClient) com.hi.api.rest.SharedClients.get(
                    name, baseUrl, url -> {
                        try {
                            return type.getConstructor(String.class).newInstance(url);
                        } catch (ReflectiveOperationException e) {
                            throw new IllegalStateException(
                                    "cannot construct " + fqn + "(String baseUrl)", e);
                        }
                    });
        } catch (ClassNotFoundException e) {
            throw new SkipException(
                    "Client " + fqn + " is not on the classpath -- convert "
                    + "PartialGoalRegression.xml, or point this test at another "
                    + "client with -Dmanual.goal.client=<SimpleName>.");
        }
    }

    @BeforeMethod(alwaysRun = true)
    public void bindScenario() {
        ImportedScenario.bind(client, ctx, softAssert, holder, SUITE);
    }

    @AfterMethod(alwaysRun = true)
    public void unbindScenario() {
        // Before unbind: cleanup reads the isolated ctx back through the
        // bound session. This case creates nothing, but wiring it now means
        // a later phase that does create something is already covered.
        ManualCleanup.afterEachTest(ctx);
        ImportedScenario.unbind();
    }

    /**
     * Bearer for the call.
     *
     * <p>Taken from ctx when something upstream primed it, otherwise fetched
     * here with this project's own grant -- a JSON body of client_id,
     * client_secret, username and password. {@code AuthHelper} cannot do it:
     * it posts form-encoded grant_type/client_id/client_secret, which is a
     * different grant. See the sibling legacy test for the same shape spelled
     * out without the scenario binding.</p>
     */
    private String bearer() {
        String primed = ctx.get("accessToken");
        if (primed != null && !primed.isBlank()) {
            return primed;
        }
        String clientId = Config.get("api_config.client_id", "");
        String clientSecret = Config.get("api_config.client_secret", "");
        String username = Config.get("username", "");
        String password = Config.get("password", "");
        String tokenBase = Config.get("api_config.token_end_point", "");
        String tokenRoute = Config.get("api_config.token_route", "");

        if (Config.isUnset(clientId) || Config.isUnset(clientSecret)
                || Config.isUnset(username) || Config.isUnset(password)
                || Config.isUnset(tokenBase) || Config.isUnset(tokenRoute)) {
            throw new SkipException(
                    "Token credentials are not configured for env '"
                    + Config.env() + "'. Set api_config.client_id, "
                    + "api_config.client_secret, username and password in "
                    + "src/main/resources/program_configuration.json "
                    + "(gitignored) -- this project keeps them in "
                    + "local-config/<environment>.properties in ReadyAPI.");
        }

        Map<String, String> payload = new LinkedHashMap<>();
        payload.put("client_id", clientId);
        payload.put("client_secret", clientSecret);
        payload.put("username", username);
        payload.put("password", password);
        String body;
        try {
            body = new com.fasterxml.jackson.databind.ObjectMapper()
                    .writeValueAsString(payload);
        } catch (com.fasterxml.jackson.core.JsonProcessingException e) {
            throw new IllegalStateException("cannot serialise the token body", e);
        }

        String url = tokenBase.endsWith("/") || tokenRoute.startsWith("/")
                ? tokenBase + tokenRoute
                : tokenBase + "/" + tokenRoute;

        Response res = Allure.step("tokenRequest", () ->
                com.hi.api.rest.utilities.RestUtilities.getResponsePost(body, url,
                        com.hi.api.rest.utilities.Headers.builder()
                                .contentTypeJson().acceptJson().build()));
        if (res.getStatusCode() != 200) {
            throw new IllegalStateException(
                    "token endpoint returned HTTP " + res.getStatusCode());
        }
        String token = res.jsonPath().getString("access_token");
        if (token == null || token.isBlank()) {
            throw new IllegalStateException(
                    "token endpoint returned 200 but no access_token field");
        }
        ctx.put("accessToken", token);
        return token;
    }

    /** Query string from the same `qry_GET_Shop_*` columns the converted test reads. */
    private Map<String, String> shopQuery(Map<String, String> row) {
        Map<String, String> q = new LinkedHashMap<>();
        for (Map.Entry<String, String> e : row.entrySet()) {
            String k = e.getKey();
            if (k == null || !k.startsWith("qry_GET_Shop_")) {
                continue;
            }
            String v = e.getValue();
            if (v != null && !v.isBlank()) {
                q.put(k.substring("qry_GET_Shop_".length()), v.trim());
            }
        }
        return q;
    }

    @Test(dataProvider = "rows",
          dataProviderClass = PerMethodCsvDataProvider.class,
          groups = {"manual", "goal"})
    @Story("Shop a single property for group availability")
    @Description("""
            Hand-authored counterpart to the converted goal09 case. Binds the
            goal suite, then shops one property for the dates and peak rooms
            in the datasheet and asserts the availability fields.
            """)
    public void shopSingleProp_returnsAvailability(Map<String, String> row) {
        // Faker tokens and #ctx# references in the row resolve here, the same
        // way they do for an imported test.
        Map<String, String> resolved = ImportedScenario.begin(
                ctx, row, row.getOrDefault("test_case_id", "goal_shop_single_prop"));

        String propCode = resolved.get("propCode");
        if (propCode == null || propCode.isBlank()) {
            throw new IllegalArgumentException(
                    "the datasheet must supply a propCode column");
        }

        String token = bearer();
        Map<String, String> query = shopQuery(resolved);

        Response res = Allure.step("GET_Shop", () ->
                client.goalShopSingleProp(token, propCode, query));

        softAssert.assertEquals(res.getStatusCode(), 200, "GET_Shop status");
        if (res.getStatusCode() == 200) {
            for (String path : new String[] {
                    "groupId", "taxPeriods", "numRoomsAvail", "ratePlanCode",
                    "roomRates[0].roomTypeCode" }) {
                softAssert.assertNotNull(res.jsonPath().get(path),
                        "GET_Shop response is missing " + path);
            }
            softAssert.assertEquals(res.jsonPath().getString("propCode"),
                    propCode, "GET_Shop echoed a different propCode");
        }
        softAssert.assertAll();
    }
}
