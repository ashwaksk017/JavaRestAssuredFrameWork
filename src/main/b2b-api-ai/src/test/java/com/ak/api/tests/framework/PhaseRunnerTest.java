package com.ak.api.tests.framework;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.atomic.AtomicReference;

import org.testng.Assert;
import org.testng.annotations.Test;
import org.testng.asserts.SoftAssert;

import com.ak.api.rest.utilities.RestLoggerUtilityDataHolder;
import com.ak.api.rest.utilities.RestStep;
import com.ak.api.rest.utilities.phase.PhaseContext;
import com.ak.api.rest.utilities.phase.PhaseRunner;
import com.ak.api.rest.utilities.phase.PhaseSpec;
import com.ak.api.rest.utilities.phase.Ref;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;
import io.restassured.builder.ResponseBuilder;
import io.restassured.response.Response;

/**
 * A phase written as DATA must do what the generated 30-line body did.
 *
 * <p>No HTTP: the exchange lambda returns fabricated responses, exactly the
 * seam the generated engine methods will fill with the typed client call.
 * Every assertion here corresponds to one line of the old body -- record
 * the response, keep the resolved request, copy extracts into ctx, run the
 * CSV-driven checks, then the case's hook.</p>
 */
@Epic("Guards")
@Feature("Parameterised phases")
public class PhaseRunnerTest {

    private static Response json(int status, String body) {
        return new ResponseBuilder()
                .setStatusCode(status)
                .setContentType("application/json")
                .setBody(body)
                .build();
    }

    private static PhaseContext context(Map<String, String> ctx, Map<String, String> row,
                                        SoftAssert soft) {
        return new PhaseContext(null, ctx, row, soft, new RestLoggerUtilityDataHolder(), "unit");
    }

    @Test(groups = {"unit", "guards"})
    @Story("the call is sent, the response recorded, the extract lands in ctx")
    public void runsTheChainAndRecordsTheResponse() throws Exception {
        Map<String, String> ctx = new LinkedHashMap<>();
        ctx.put("Properties.accountId", "2000488665");
        SoftAssert soft = new SoftAssert();
        PhaseContext c = context(ctx, new LinkedHashMap<>(), soft);

        PhaseSpec spec = PhaseSpec.phase("get_account")
                .get("/businesses/{accountId}")
                .args(Ref.ctx("Properties.accountId"))
                .expect(200)
                .exists("accountId")
                .extract("PropertiesDetails.accountID", "accountId")
                .build();

        AtomicReference<Map<String, String>> seenQuery = new AtomicReference<>();
        Response res = PhaseRunner.run(spec, c, (body, q, h) -> {
            seenQuery.set(q);
            return json(200, "{\"accountId\": 2000488665, \"status\": \"active\"}");
        });

        Assert.assertNotNull(res);
        Assert.assertSame(c.response("get_account"), res, "response must be recorded under the step name");
        Assert.assertEquals(ctx.get("PropertiesDetails.accountID"), "2000488665", "extract must land in ctx");
        // putExtracted skips empty values, so a bodiless GET records no
        // RawRequest -- exactly what the generated body did.
        Assert.assertFalse(ctx.containsKey("get_account_RawRequest"),
                "a GET has no body; an empty RawRequest must not be stored");
        Assert.assertNotNull(seenQuery.get(), "the exchange receives the resolved query map");
        soft.assertAll();
    }

    @Test(groups = {"unit", "guards"})
    @Story("path parameters resolve from ctx, a prior response, a row column or a literal -- in order")
    public void resolvedPathSubstitutesEveryArgInOrder() {
        Map<String, String> ctx = new LinkedHashMap<>();
        ctx.put("Properties.guestID", "g-1");
        Map<String, String> row = new LinkedHashMap<>();
        row.put("path_read_member_memberId", "m-from-csv");
        PhaseContext c = context(ctx, row, new SoftAssert());
        c.record("create_account", json(200, "{\"accountId\": \"a-2\"}"));

        PhaseSpec spec = PhaseSpec.phase("read_member")
                .get("/guests/{guestId}/businesses/{accountId}/members/{memberId}/{extra}")
                .args(Ref.ctx("Properties.guestID"),
                      Ref.resp("create_account", "accountId"),
                      Ref.row("path_read_member_memberId", "9999999999"),
                      Ref.literal("x"))
                .build();

        Assert.assertEquals(PhaseRunner.resolvedPathForTest(spec, c),
                "/guests/g-1/businesses/a-2/members/m-from-csv/x");
    }

    @Test(groups = {"unit", "guards"})
    @Story("a row column falls back to the SoapUI literal; a missing prior response resolves empty")
    public void refFallbacks() {
        PhaseContext c = context(new LinkedHashMap<>(), new LinkedHashMap<>(), new SoftAssert());
        Assert.assertEquals(Ref.row("absent_col", "8888888888").resolve(c), "8888888888");
        Assert.assertEquals(Ref.resp("never_ran", "id").resolve(c), "");
        Assert.assertEquals(Ref.expr("upper", x -> "abc".toUpperCase()).resolve(c), "ABC");
        Assert.assertEquals(Ref.ctx("no.such.key").resolve(c), "");
    }

    @Test(groups = {"unit", "guards"})
    @Story("NEGATIVE CONTROL: the builder refuses a spec whose Refs do not match the path")
    public void builderRefusesArgCountMismatch() {
        try {
            PhaseSpec.phase("x").get("/businesses/{accountId}/activate").build();
            Assert.fail("one parameter, zero Refs must not build");
        } catch (IllegalStateException expected) {
            Assert.assertTrue(expected.getMessage().contains("1 parameter(s) but 0 Ref(s)"),
                    expected.getMessage());
        }
    }

    @Test(groups = {"unit", "guards"})
    @Story("a check that fails is reported through the SoftAssert, as before")
    @Description("""
            The old body called ResponseAsserts.jsonEquals(...) with the SoapUI
            literal as the default. The spec carries that literal; a wrong
            response must still surface at assertAll().
            """)
    public void checksReportThroughSoftAssert() throws Exception {
        SoftAssert soft = new SoftAssert();
        PhaseContext c = context(new LinkedHashMap<>(), new LinkedHashMap<>(), soft);
        PhaseSpec spec = PhaseSpec.phase("verify")
                .get("/guests/{guestId}/businesses/verify")
                .args(Ref.literal("g"))
                .expect(200)
                .equals("[0].status", "limited")
                .absent("[0].inviteKey")
                .build();

        PhaseRunner.run(spec, c, (b, q, h) ->
                json(200, "[{\"status\": \"pending\", \"inviteKey\": \"k\"}]"));
        try {
            soft.assertAll();
            Assert.fail("status was pending, not limited, and inviteKey was present");
        } catch (AssertionError expected) {
            Assert.assertTrue(expected.getMessage().contains("status"), expected.getMessage());
        }
    }

    @Test(groups = {"unit", "guards"})
    @Story("the case's hook runs after the call, with the response and the flow state")
    public void hookRunsAfterTheCall() throws Exception {
        Map<String, String> ctx = new LinkedHashMap<>();
        PhaseContext c = context(ctx, new LinkedHashMap<>(), new SoftAssert());
        PhaseSpec spec = PhaseSpec.phase("enroll")
                .post("/realms/guests/enroll")
                .expect(200)
                .after((res, flow) -> flow.ctx.put("Properties.role",
                        res.jsonPath().getString("role") + "-seen"))
                .build();

        PhaseRunner.run(spec, c, (b, q, h) -> json(200, "{\"role\": \"owner\"}"));
        Assert.assertEquals(ctx.get("Properties.role"), "owner-seen");
    }

    @Test(groups = {"unit", "guards"})
    @Story("the query map the exchange receives is the spec's, resolved against the row")
    public void queryPlaceholdersResolveAgainstTheRow() throws Exception {
        Map<String, String> row = new LinkedHashMap<>();
        row.put("Properties_country", "US");
        row.put("qry_verify_emailDomain", "example.test");
        PhaseContext c = context(new LinkedHashMap<>(), row, new SoftAssert());
        PhaseSpec spec = PhaseSpec.phase("verify")
                .get("/guests/{guestId}/businesses/verify")
                .args(Ref.literal("g"))
                .query("country", "#Properties_country#")
                .query("emailDomain", "#qry_verify_emailDomain#")
                .expect(200)
                .build();

        AtomicReference<Map<String, String>> seen = new AtomicReference<>();
        PhaseRunner.run(spec, c, (b, q, h) -> { seen.set(q); return json(200, "[]"); });
        Assert.assertEquals(seen.get().get("country"), "US");
        Assert.assertEquals(seen.get().get("emailDomain"), "example.test");
    }

    @Test(groups = {"unit", "guards"})
    @Story("a hook-only part runs its hook and makes no call")
    public void hookOnlyPartRunsWithoutACall() throws Exception {
        Map<String, String> ctx = new LinkedHashMap<>();
        PhaseContext c = context(ctx, new LinkedHashMap<>(), new SoftAssert());
        PhaseSpec part = PhaseSpec.hookOnly("groovy_between", (res, flow) -> flow.ctx.put("ran", "yes"));
        Assert.assertTrue(part.isHookOnly());
        Assert.assertNull(PhaseRunner.run(part, c, null));
        Assert.assertEquals(ctx.get("ran"), "yes");
    }

    @Test(groups = {"unit", "guards"})
    @Story("the registry resolves by vocabulary, names repeats, and keeps compound parts in order")
    public void registryResolvesByVocabularyAndStep() {
        com.ak.api.rest.utilities.phase.CaseRegistry.Case cs =
                com.ak.api.rest.utilities.phase.CaseRegistry.register("unit-case")
                .phase("enrollGuest", "HHonorsEnroll",
                        () -> PhaseSpec.phase("HHonorsEnroll").post("/realms/guests/enroll").build())
                .phase("readProgramAccount", "get_1",
                        () -> PhaseSpec.phase("get_1").get("/businesses/{id}").args(Ref.literal("a")).build())
                .phase("readProgramAccount", "get_2",
                        () -> PhaseSpec.phase("get_2").get("/businesses/{id}").args(Ref.literal("b")).build(),
                        () -> PhaseSpec.hookOnly("get_2", (r, f) -> { }));
        Assert.assertEquals(cs.only("enrollGuest", false).size(), 1);
        try {
            cs.only("readProgramAccount", false);
            Assert.fail("two readProgramAccount phases must not resolve silently");
        } catch (IllegalStateException e) {
            Assert.assertTrue(e.getMessage().contains("say which one: readProgramAccount(\"get_1\", \"get_2\")"),
                    e.getMessage());
        }
        java.util.List<PhaseSpec> parts = cs.named("readProgramAccount", "get_2", false);
        Assert.assertEquals(parts.size(), 2, "compound: call + hook-only part, in order");
        Assert.assertFalse(parts.get(0).isHookOnly());
        Assert.assertTrue(parts.get(1).isHookOnly());
        try {
            cs.named("readProgramAccount", "nope", false);
            Assert.fail();
        } catch (IllegalStateException e) {
            Assert.assertTrue(e.getMessage().contains("it has: enrollGuest(\"HHonorsEnroll\")"), e.getMessage());
        }
    }

    @Test(groups = {"unit", "guards"})
    @Story("toString names the call, the step and what varies -- readable in a failure")
    public void specDescribesItself() {
        PhaseSpec spec = PhaseSpec.phase("get_account").get("/businesses/{accountId}")
                .args(Ref.ctx("Properties.accountId")).expect(404).build();
        String s = spec.toString();
        Assert.assertTrue(s.contains("GET /businesses/{accountId}") && s.contains("get_account")
                && s.contains("404") && s.contains("ctx:Properties.accountId"), s);
        Assert.assertNotNull(RestStep.class); // the runner drives the same RestStep as the generated code
    }
}
