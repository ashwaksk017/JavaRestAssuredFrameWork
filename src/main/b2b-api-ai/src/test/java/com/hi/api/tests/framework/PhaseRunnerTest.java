package com.hi.api.tests.framework;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.atomic.AtomicReference;

import org.testng.Assert;
import org.testng.annotations.Test;
import org.testng.asserts.SoftAssert;

import com.hi.api.rest.utilities.RestLoggerUtilityDataHolder;
import com.hi.api.rest.utilities.RestStep;
import com.hi.api.rest.utilities.phase.PhaseContext;
import com.hi.api.rest.utilities.phase.PhaseRunner;
import com.hi.api.rest.utilities.phase.PhaseSpec;
import com.hi.api.rest.utilities.phase.Ref;

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
    @Story("a response recorded under the sanitised step name is found by the name ReadyAPI spells")
    @Description("""
            A phase records as http_request_200_3_CreatePendingAccountmember;
            the reference lifted from the project says
            http_request_200_3-CreatePendingAccountmember. The read returned
            null, {memberId} resolved empty and the next GET died on a
            trailing empty segment while the id sat in a 200 response.
            """)
    public void responseIsFoundUnderTheReadyApiSpelling() {
        PhaseContext c = context(new LinkedHashMap<>(), new LinkedHashMap<>(), new SoftAssert());
        c.record("http_request_200_3_CreatePendingAccountmember", json(200, "{\"memberId\": 405830}"));
        c.record("http_POST_request_Create_Account_200", json(200, "{\"accountId\": 7}"));

        Assert.assertEquals(
                Ref.resp("http_request_200_3-CreatePendingAccountmember", "memberId").resolve(c),
                "405830", "hyphen in the ReadyAPI name");
        Assert.assertEquals(
                Ref.resp("http_POST_request_Create _Account_200_", "accountId").resolve(c),
                "7", "space, doubled separator and trailing underscore");
    }

    @Test(groups = {"unit", "guards"})
    @Story("NEGATIVE CONTROL: the exact name wins, and a different step is not found by accident")
    public void normalisedLookupNeverOverridesAnExactMatch() {
        PhaseContext c = context(new LinkedHashMap<>(), new LinkedHashMap<>(), new SoftAssert());
        c.record("get-account", json(200, "{\"id\": \"exact\"}"));
        c.record("get_account", json(200, "{\"id\": \"other\"}"));

        Assert.assertEquals(Ref.resp("get-account", "id").resolve(c), "exact",
                "a name recorded verbatim must resolve to its own response");
        Assert.assertEquals(Ref.resp("get_account", "id").resolve(c), "other");
        Assert.assertNull(c.response("get_accounts"), "a different step name must stay unfound");
        Assert.assertNull(c.response(null));
    }

    @Test(groups = {"unit", "guards"})
    @Story("a path ending in a parameter the author saved empty is the collection URL, not a broken path")
    @Description("""
            POST .../partneraccounts/{partneraccount} is recorded with
            partneraccount="". The guard read the trailing slash as a missing
            id and threw before the request was sent.
            """)
    public void authorEmptyTrailingParameterIsSent() throws Exception {
        Map<String, String> ctx = new LinkedHashMap<>();
        ctx.put("PropertiesaccountID.accountID", "2000510649");
        PhaseContext c = context(ctx, new LinkedHashMap<>(), new SoftAssert());
        PhaseSpec spec = PhaseSpec.phase("post_create_partneraccount")
                .post("/businesses/{accountId}/partneraccounts/{partneraccount}")
                .args(Ref.ctx("PropertiesaccountID.accountID"),
                      Ref.row("path_post_create_partneraccount_partneraccount", ""))
                .expect(201)
                .build();

        Assert.assertEquals(PhaseRunner.resolvedPathForTest(spec, c),
                "/businesses/2000510649/partneraccounts/", "the wire URL is not rewritten");
        Response res = PhaseRunner.run(spec, c, (b, q, h) -> json(201, "{}"));
        Assert.assertEquals(res.statusCode(), 201, "the call must be sent, not refused by the guard");
    }

    @Test(groups = {"unit", "guards"})
    @Story("An empty last path parameter goes together with its slash")
    @Description("""
            ReadyAPI records .../partneraccounts/{partneraccount} with the
            parameter empty as .../partneraccounts. Filling in "" kept the
            slash and the API answered 404 for .../partneraccounts/.
            """)
    public void emptyLastParameterTakesItsSlashWithIt() throws Exception {
        final String t = "/businesses/{accountId}/partneraccounts/{partneraccount}";
        // What the generated client does with its arguments, as the phase runs it.
        Map<String, String> ctx = new LinkedHashMap<>();
        ctx.put("PropertiesaccountID.accountID", "2");
        PhaseContext c = context(ctx, new LinkedHashMap<>(), new SoftAssert());
        final String[] wire = new String[1];
        PhaseSpec spec = PhaseSpec.phase("post_create_partneraccount")
                .post(t)
                .args(Ref.ctx("PropertiesaccountID.accountID"),
                      Ref.row("path_post_create_partneraccount_partneraccount", ""))
                .expect(201)
                .build();
        PhaseRunner.run(spec, c, (b, q, h) -> {
            wire[0] = com.hi.api.rest.ApiRoutes.fill(t, "accountId", "2", "partneraccount", "");
            return json(201, "{}");
        });
        Assert.assertEquals(wire[0], "/businesses/2/partneraccounts");

        // NEGATIVE CONTROLS. Outside such a phase nothing changes: an id an
        // extract failed to supply keeps its slash and still looks broken.
        Assert.assertEquals(com.hi.api.rest.ApiRoutes.fill(t, "accountId", "2", "partneraccount", ""),
                "/businesses/2/partneraccounts/");
        // Inside one: a value is kept, an empty id in the MIDDLE is not
        // papered over, a template written with a trailing slash keeps it.
        Assert.assertEquals(com.hi.api.rest.ApiRoutes.withAuthorEmptyTail(() ->
                com.hi.api.rest.ApiRoutes.fill(t, "accountId", "2", "partneraccount", "77")),
                "/businesses/2/partneraccounts/77");
        Assert.assertEquals(com.hi.api.rest.ApiRoutes.withAuthorEmptyTail(() ->
                com.hi.api.rest.ApiRoutes.fill(t, "accountId", "", "partneraccount", "77")),
                "/businesses//partneraccounts/77");
        Assert.assertEquals(com.hi.api.rest.ApiRoutes.withAuthorEmptyTail(() ->
                com.hi.api.rest.ApiRoutes.fill("/businesses/{accountId}/", "accountId", "2")),
                "/businesses/2/");
        Assert.assertEquals(PhaseRunner.withoutEmptyTrailingSegment("/a/b/?x=1"), "/a/b?x=1");
        Assert.assertEquals(PhaseRunner.withoutEmptyTrailingSegment("/"), "/");
    }

    @Test(groups = {"unit", "guards"})
    @Story("NEGATIVE CONTROL: an id that an extract failed to supply still stops the step")
    public void emptyTrailingIdFromAnExtractStillFailsFast() throws Exception {
        PhaseContext c = context(new LinkedHashMap<>(), new LinkedHashMap<>(), new SoftAssert());

        // trailing, but from ctx: nothing says the author meant it
        PhaseSpec fromCtx = PhaseSpec.phase("read_member")
                .get("/businesses/{accountId}/members/{memberId}")
                .args(Ref.literal("a"), Ref.ctx("no.such.key"))
                .expect(200).build();
        assertBrokenPath(fromCtx, c, "TRAILING empty path segment");

        // trailing, from a prior response that never ran
        PhaseSpec fromResp = PhaseSpec.phase("read_member")
                .get("/businesses/{accountId}/members/{memberId}")
                .args(Ref.literal("a"), Ref.resp("never_ran", "memberId"))
                .expect(200).build();
        assertBrokenPath(fromResp, c, "TRAILING empty path segment");

        // author-empty, but in the MIDDLE of the path
        PhaseSpec middle = PhaseSpec.phase("read_member")
                .get("/businesses/{accountId}/members")
                .args(Ref.row("path_read_member_accountId", ""))
                .expect(200).build();
        assertBrokenPath(middle, c, "EMPTY path segment");

        // author-empty last parameter does not excuse an empty one before it
        PhaseSpec both = PhaseSpec.phase("post_create_partneraccount")
                .post("/businesses/{accountId}/partneraccounts/{partneraccount}")
                .args(Ref.ctx("no.such.key"), Ref.row("path_x_partneraccount", ""))
                .expect(201).build();
        assertBrokenPath(both, c, "EMPTY path segment");
    }

    private static void assertBrokenPath(PhaseSpec spec, PhaseContext c, String expectedDetail)
            throws Exception {
        try {
            PhaseRunner.run(spec, c, (b, q, h) -> json(200, "{}"));
            Assert.fail("must not be sent: " + PhaseRunner.resolvedPathForTest(spec, c));
        } catch (IllegalStateException expected) {
            Assert.assertTrue(expected.getMessage().contains(expectedDetail), expected.getMessage());
        }
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
        com.hi.api.rest.utilities.phase.CaseRegistry.Case cs =
                com.hi.api.rest.utilities.phase.CaseRegistry.register("unit-case")
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
    @Story("a case's bootstrap is registered parts plus the SetupHelper REST offset")
    public void registryHoldsTheBootstrapAndItsRestOffset() {
        Map<String, String> ctx = new LinkedHashMap<>();
        com.hi.api.rest.utilities.phase.CaseRegistry.Case cs =
                com.hi.api.rest.utilities.phase.CaseRegistry.register("unit-boot")
                .bootstrap(() -> PhaseSpec.hookOnly("bootstrap", (r, f) -> f.ctx.put("boot", "ran")))
                .restOffset(2);
        Assert.assertTrue(cs.hasBootstrap());
        Assert.assertEquals(cs.restOffset(), 2);
        Assert.assertEquals(cs.bootstrapParts().size(), 1);
        com.hi.api.rest.utilities.phase.CaseRegistry.Case none =
                com.hi.api.rest.utilities.phase.CaseRegistry.register("unit-noboot");
        Assert.assertFalse(none.hasBootstrap(), "a case with no setup steps registers no bootstrap");
        Assert.assertEquals(none.restOffset(), 0);
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
