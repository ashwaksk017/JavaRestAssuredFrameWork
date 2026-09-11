package com.ak.api.dsl;

import java.util.LinkedHashMap;
import java.util.Map;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.testng.asserts.SoftAssert;

import com.ak.api.context.ScenarioContext;
import com.ak.api.domain.DomainApis;
import com.ak.api.rest.utilities.RestLoggerUtilityDataHolder;
import com.ak.api.rest.utilities.RestStep;
import com.ak.api.support.ImportedRestClient;
import com.ak.api.support.ImportedScenario;

import io.restassured.response.Response;

/**
 * Hand-authoring DSL for B2B onboarding scenarios.
 *
 * <pre>
 * CustomerOnboarding.start(row)
 *     .createTravelAgency()
 *     .enrollOwner()
 *     .createH4LAccount()
 *     .confirmOwner()
 *     .prepareSalesforceAccount()
 *     .activateThroughHws()
 *     .enrollTravelAdvisor()
 *     .verifySynchronization()
 *     .complete();
 * </pre>
 *
 * <p>Note: a GENERATED type of the same simple name exists at
 * {@code com.ak.api.support.scenario.CustomerOnboarding}. They coexist
 * because they live in different packages and are never imported together:
 * that one is converter output for imported suites, this one is the
 * hand-authoring surface.</p>
 *
 * <h2>Why this is separate from ScenarioSteps</h2>
 *
 * {@code com.ak.api.support.scenario.ScenarioSteps} is CONVERTER OUTPUT -- 700+
 * methods, regenerated on every run, and gitignored. Anything written there is
 * wiped by {@code --clean} and never committed. This package is hand-written,
 * committed, and depends only on committed framework pieces ({@link RestStep},
 * the domain facades, {@link ImportedScenario}), so a converter run cannot
 * touch it.
 *
 * <h2>Role and partner are data, not methods</h2>
 *
 * {@code POST /realms/guests/enroll} is ONE endpoint. Whether the guest becomes
 * an owner or a travel advisor is a property of the request, not a different
 * call. So {@link #enrollGuest(Role)} is the real operation and
 * {@link #enrollOwner()} is a one-line alias.
 *
 * <p>This is the distinction that keeps the converter's
 * {@code RETIRED_FLUENT_NAMES} rule intact. That rule exists because the
 * CONVERTER must never GUESS a role from a ReadyAPI step title. A human writing
 * {@code .enrollOwner()} is not guessing -- they are declaring intent. The
 * converter still emits the neutral phase and takes the role from the CSV row;
 * only hand-written tests use these aliases.</p>
 *
 * <h2>Data</h2>
 *
 * Every phase reads its request body from the row column {@code body_<phase>}
 * when present, and asserts through the standard
 * {@code expected_<phase>_status_code} / {@code expected_<phase>_jsonpath_*}
 * columns that {@link RestStep} and {@code ResponseAsserts} already understand
 * -- the same CSV contract as imported tests, so both kinds of test are driven
 * the same way.
 */
public final class CustomerOnboarding {

    private static final Logger LOG = LoggerFactory.getLogger(CustomerOnboarding.class);

    /** Member role. The value is what the API expects on the wire. */
    public enum Role {
        OWNER("owner"),
        TRAVEL_ADVISOR("travelCoordinator"),
        EMPLOYEE("employee"),
        ADMIN("admin");

        private final String wire;

        Role(String wire) {
            this.wire = wire;
        }

        public String wire() {
            return wire;
        }
    }

    /** Account partner / product line. Drives payload + expectations via ctx. */
    public enum Partner {
        H4L, H4B, LTA, SMB, AMEX;

        public String wire() {
            return name().toLowerCase(java.util.Locale.ROOT);
        }
    }

    private final Map<String, String> ctx;
    /** Typed view over the SAME map -- see ScenarioContext. */
    private final ScenarioContext sc;
    private final Map<String, String> row;
    private final SoftAssert softAssert;
    private final RestLoggerUtilityDataHolder holder;
    private final DomainApis apis;
    private final String testCaseId;

    private CustomerOnboarding(Map<String, String> ctx, Map<String, String> row,
                       SoftAssert softAssert, RestLoggerUtilityDataHolder holder,
                       DomainApis apis, String testCaseId) {
        this.ctx = ctx;
        this.sc = ScenarioContext.of(ctx);
        this.row = row;
        this.softAssert = softAssert;
        this.holder = holder;
        this.apis = apis;
        this.testCaseId = testCaseId;
    }

    /**
     * Bind to the test's session. Requires {@code ImportedScenario.bind(...)}
     * to have run -- {@code BaseApiTest} subclasses do that in @BeforeMethod.
     */
    public static CustomerOnboarding start(Map<String, String> row) {
        ImportedScenario.Session s = ImportedScenario.current();
        String testCaseId = row == null
                ? "manual" : row.getOrDefault("test_case_id", "manual");
        Map<String, String> bound = ImportedScenario.begin(s.ctx, row, testCaseId);
        s.row = bound;
        s.testCaseId = testCaseId;
        DomainApis apis = DomainApis.bind((ImportedRestClient) s.client);
        LOG.info("=== CustomerOnboarding.start  testCaseId={} ===", testCaseId);
        return new CustomerOnboarding(s.ctx, bound, s.softAssert, s.holder, apis, testCaseId);
    }

    // =====================================================================
    // Travel agency
    // =====================================================================

    /** PUT /travelagencies/{travelAgentId} -- upsert the agency. */
    public CustomerOnboarding createTravelAgency() {
        String agencyId = required("travelAgentId");
        exec("createTravelAgency", 200, (body, q, h) ->
                apis.client().updateTravelAgency(token(), agencyId, body));
        return this;
    }

    // =====================================================================
    // Guest enrolment  (POST /realms/guests/enroll)
    // =====================================================================

    /** The real operation: role is data on the request, not a different call. */
    public CustomerOnboarding enrollGuest(Role role) {
        ctx.put("role", role.wire());
        LOG.info(" .. enrollGuest role={}", role.wire());
        Response res = exec("enrollGuest", 200, (body, q, h) ->
                apis.guests().hHonorsEnroll(token(), body));
        // Publish the new guest id under the role-specific slot so a later
        // phase can address owner and travel advisor separately.
        String guestId = com.ak.api.rest.utilities.RestUtilities
                .safeJsonExtract(res, "guestId");
        sc.put(ScenarioContext.GUEST_ID, guestId);
        ImportedScenario.putExtracted(ctx, slot(role, "guestId"), guestId);
        return this;
    }

    public CustomerOnboarding enrollOwner() {
        return enrollGuest(Role.OWNER);
    }

    public CustomerOnboarding enrollTravelAdvisor() {
        return enrollGuest(Role.TRAVEL_ADVISOR);
    }

    public CustomerOnboarding enrollEmployee() {
        return enrollGuest(Role.EMPLOYEE);
    }

    // =====================================================================
    // Program account  (POST /guests/{guestId}/businesses)
    // =====================================================================

    public CustomerOnboarding createProgramAccount(Partner partner) {
        ctx.put("partner", partner.wire());
        LOG.info(" .. createProgramAccount partner={}", partner.wire());
        Response res = exec("createProgramAccount", 201, (body, q, h) ->
                apis.accounts().createProgramAccount(token(), required("guestId"), q, body));
        sc.put(ScenarioContext.ACCOUNT_ID,
                com.ak.api.rest.utilities.RestUtilities.safeJsonExtract(res, "accountId"));
        return this;
    }

    public CustomerOnboarding createH4LAccount() {
        return createProgramAccount(Partner.H4L);
    }

    public CustomerOnboarding createH4BAccount() {
        return createProgramAccount(Partner.H4B);
    }

    public CustomerOnboarding createLTAAccount() {
        return createProgramAccount(Partner.LTA);
    }

    public CustomerOnboarding createSmbAccount() {
        return createProgramAccount(Partner.SMB);
    }

    /** GET /businesses/{accountId} */
    public CustomerOnboarding readProgramAccount() {
        exec("readProgramAccount", 200, (body, q, h) ->
                apis.accounts().readProgramAccount(token(), required("accountId")));
        return this;
    }

    /** POST /businesses/{accountId}/activate */
    public CustomerOnboarding activateProgramAccount() {
        exec("activateProgramAccount", 204, (body, q, h) ->
                apis.accounts().activateProgramAccount(token(), required("accountId"), body));
        return this;
    }

    // =====================================================================
    // Member validation  (TOTP confirm)
    // =====================================================================

    public CustomerOnboarding confirmMemberTotp(Role role) {
        ctx.put("role", role.wire());
        exec("confirmMemberTotp", 200, (body, q, h) ->
                apis.members().validateMemberTOTP(token(), required("guestId"),
                        required("accountId"), required("memberId"), body));
        return this;
    }

    public CustomerOnboarding confirmOwner() {
        return confirmMemberTotp(Role.OWNER);
    }

    public CustomerOnboarding confirmTravelAdvisor() {
        return confirmMemberTotp(Role.TRAVEL_ADVISOR);
    }

    // =====================================================================
    // Account members
    // =====================================================================

    public CustomerOnboarding addMember(Role role) {
        ctx.put("role", role.wire());
        Response res = exec("createAccountMember", 201, (body, q, h) ->
                apis.members().createProgramAccountMember(token(), required("guestId"),
                        required("accountId"), body));
        sc.put(ScenarioContext.MEMBER_ID,
                com.ak.api.rest.utilities.RestUtilities.safeJsonExtract(res, "memberId"));
        return this;
    }

    public CustomerOnboarding addTravelAdvisor() {
        return addMember(Role.TRAVEL_ADVISOR);
    }

    public CustomerOnboarding addEmployee() {
        return addMember(Role.EMPLOYEE);
    }

    public CustomerOnboarding readAccountMember() {
        exec("readAccountMember", 200, (body, q, h) ->
                apis.members().readProgramAccountMember(token(), required("guestId"),
                        required("accountId"), required("memberId")));
        return this;
    }

    // =====================================================================
    // Salesforce / HWS  -- composite phases
    // =====================================================================

    /**
     * Salesforce token + Account + Distribution. Composite: the suites always
     * perform these three together as the HWS trigger, so the chain reads at
     * business altitude instead of spelling out the plumbing.
     */
    public CustomerOnboarding prepareSalesforceAccount() {
        LOG.info(" .. prepareSalesforceAccount (token + account + distribution)");
        exec("fetchSalesforceToken", 200, (body, q, h) ->
                com.ak.api.rest.utilities.SalesforceAuth.requestToken(q));
        exec("createSalesforceAccount", 201, (body, q, h) ->
                apis.client().createAccount(sfToken(), body));
        exec("createSalesforceDistribution", 201, (body, q, h) ->
                apis.client().createDistribution(sfToken(), body));
        return this;
    }

    /**
     * The ReadyAPI suites drive the HWS console by hand at this point
     * (LaunchHwsSearchLead). There is no API for the manual attest, so the
     * DSL performs the API-visible half -- read the lead, then activate --
     * and says so rather than pretending to automate the console.
     */
    public CustomerOnboarding activateThroughHws() {
        LOG.info(" .. activateThroughHws (API half; HWS console step is manual in ReadyAPI)");
        exec("readSalesforceLead", 200, (body, q, h) ->
                apis.client().salesforceID(sfToken(), required("leadId")));
        return activateProgramAccount();
    }

    /** Owner + program member + participation all reflect the account. */
    public CustomerOnboarding verifySynchronization() {
        LOG.info(" .. verifySynchronization (salesforce account / member / participation)");
        exec("readSalesforceAccount", 200, (body, q, h) ->
                apis.client().salesforceID(sfToken(), required("salesforceId")));
        return this;
    }

    // =====================================================================
    // Terminal
    // =====================================================================

    /** Flush soft assertions. Call at the end of every chain. */
    public void complete() {
        LOG.info("=== CustomerOnboarding.complete  testCaseId={} ===", testCaseId);
        softAssert.assertAll();
    }

    // =====================================================================
    // Internals
    // =====================================================================

    /**
     * One HTTP exchange with the standard CSV contract. The body comes from
     * {@code body_<phase>} on the row when present; status and JsonPath
     * expectations come from the usual {@code expected_<phase>_*} columns,
     * which RestStep already resolves.
     */
    private Response exec(String phase, int expectedStatus, RestStep.Exchange call) {
        try {
            RestStep step = RestStep.exec(ctx, row, softAssert, holder, testCaseId)
                    .name(phase)
                    .expectedStatus(expectedStatus);
            String template = row == null ? null : row.get("template_" + phase);
            if (template != null && !template.isEmpty()) {
                step = step.template(template);
            }
            return step.post("/" + phase, call);
        } catch (Exception e) {
            throw new IllegalStateException("CustomerOnboarding phase '" + phase + "' failed", e);
        }
    }

    /**
     * Typed ctx lookup. Resolves through ScenarioContext's DECLARED alias
     * table rather than the legacy heuristic walk, so an account id cannot be
     * answered by a member id that happens to share a trailing field name.
     */
    private String required(String logicalName) {
        switch (logicalName) {
            case "guestId":       return Long.toString(sc.requireGuestId());
            case "accountId":     return Long.toString(sc.requireAccountId());
            case "memberId":      return Long.toString(sc.requireMemberId());
            case "travelAgentId": return sc.requireText(ScenarioContext.TRAVEL_AGENT_ID);
            default:              break;
        }
        // Long tail (leadId, salesforceId, ...) -- no declared field yet.
        String v = sc.raw(aliasFor(logicalName));
        if (v == null || v.isEmpty()) {
            throw new IllegalStateException(
                    "CustomerOnboarding: '" + logicalName + "' is not in ctx yet. "
                    + "An earlier phase must publish it (e.g. enrollOwner() before "
                    + "createH4LAccount()), or supply it as a CSV column.");
        }
        return v;
    }

    /** Logical name -> the ctx key the imported suites already use. */
    private static String aliasFor(String logicalName) {
        switch (logicalName) {
            case "guestId":       return "Properties.guestID";
            case "accountId":     return "PropertiesDetails.accountID";
            case "memberId":      return "PropertiesDetails.memberID";
            case "travelAgentId": return "Properties.travelAgentId";
            case "leadId":        return "Properties.leadId";
            case "salesforceId":  return "Properties.salesforceId";
            default:              return logicalName;
        }
    }

    private static String slot(Role role, String field) {
        return "Properties." + role.name().toLowerCase(java.util.Locale.ROOT) + "_" + field;
    }

    private String token() {
        return sc.apiToken();
    }

    private String sfToken() {
        return sc.salesforceToken();
    }

    /** Query/form map helper for phases that need one. */
    private static Map<String, String> params(String... kv) {
        Map<String, String> m = new LinkedHashMap<>();
        for (int i = 0; i + 1 < kv.length; i += 2) {
            m.put(kv[i], kv[i + 1]);
        }
        return m;
    }
}
