package com.hi.api.tests.manual;

import java.util.Map;

import org.testng.SkipException;
import org.testng.annotations.AfterMethod;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.BeforeMethod;
import org.testng.annotations.Test;

import com.hi.api.config.Config;
import com.hi.api.context.ScenarioContext;
import com.hi.api.data.PerMethodCsvDataProvider;
import com.hi.api.dsl.MasterClass;
import com.hi.api.dsl.Template;
import com.hi.api.dsl.ManualCleanup;
import com.hi.api.support.ImportedRestClient;
import com.hi.api.support.ImportedScenario;
import com.hi.api.tests.BaseApiTest;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;

/**
 * Worked example of the hand-authoring surface: template selection,
 * value capture, and response assertions on one chain.
 *
 * <p>{@code OnboardingE2ETest} shows the bare chain. This one adds the three
 * things a real test usually needs on top of it, so the two together cover
 * the whole vocabulary.</p>
 *
 * <h2>The one rule that is easy to get backwards</h2>
 *
 * <p>{@code using(...)}, {@code capture(...)} and {@code expect*(...)} all
 * bind to the <b>NEXT</b> phase, not the one before them. They queue onto
 * {@code pendingTemplate} / {@code pendingCaptures} / {@code pendingExpectations},
 * and {@code exec()} drains the queues for the phase it is about to run.
 * So it reads "set up, then act":</p>
 *
 * <pre>
 * .capture("guestId", ScenarioContext.GUEST_ID)   // applies to ...
 * .enrollOwner()                                  // ... this response
 * </pre>
 *
 * <p>The queues are cleared in a {@code finally}, so a queued expectation
 * cannot leak into the next phase and assert against an unrelated response.</p>
 *
 * <h2>Phase names are exec() names, not method names</h2>
 *
 * <p>The per-phase CSV columns key off the string passed to {@code exec()},
 * which is not always the Java method name:</p>
 *
 * <pre>
 * enrollOwner()        -> enrollGuest
 * createH4BAccount()   -> createProgramAccount
 * readProgramAccount() -> readProgramAccount
 * addEmployee()        -> createAccountMember   &lt;-- not "addEmployee"
 * readAccountMember()  -> readAccountMember
 * </pre>
 *
 * <p>Use the right-hand name in {@code expected_<phase>_status_code},
 * {@code expected_<phase>_jsonpath_<field>} and {@code template_<phase>}, or
 * the override silently does nothing.</p>
 *
 * <h2>Caveat on the JSON paths below</h2>
 *
 * <p>The paths are illustrative. They have NOT been verified against live
 * responses -- that needs a run against the real environment. Check each one
 * before trusting it; {@code expectExists} on a wrong path fails loudly,
 * which is the point.</p>
 */
@Epic("Manual")
@Feature("Customer onboarding")
public class H4bMemberOnboardingTest extends BaseApiTest {

    /**
     * Resolved by NAME so this file compiles for anyone, whatever XML they
     * converted. Same approach as {@code OnboardingE2ETest}, but defaulting
     * to the client this tree actually generates -- that test's default of
     * {@code ProgramAccountClient} does not exist here, so it skips unless
     * you pass {@code -Dmanual.client=...}.
     */
    private ImportedRestClient client;

    /** Carries ids between phases for one test. */
    /**
     * Per-class SEED for the scenario context -- NOT the map the chain writes to.
     *
     * <p>{@code ImportedScenario.bind} calls {@code isolateCtx}, which (unless
     * {@code test.isolateCtxPerMethod=false}) gives each {@code @Test} its own
     * map seeded with just the accessToken. Extracted ids land in that isolated
     * map, and cleanup reads them back through {@code boundCtxOr} -- which is
     * why {@code ManualCleanup} must run BEFORE {@code unbind()}.</p>
     *
     * <p>The {@code synchronizedMap} wrapper is not what makes
     * {@code parallel="methods"} safe; {@code isolateCtx} is.</p>
     */
    private final Map<String, String> ctx =
            java.util.Collections.synchronizedMap(new java.util.LinkedHashMap<>());

    @BeforeClass(alwaysRun = true)
    public void initClient() {
        String baseUrl = Config.get("base_url", Config.baseUrl());
        String name = Config.get("manual.client", "ProgramaccountregressionClient");
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
                    "Client " + fqn + " is not on the classpath -- convert the "
                    + "ReadyAPI XML that generates it, or point this test at "
                    + "another client with -Dmanual.client=<SimpleName>.");
        }
    }

    @BeforeMethod(alwaysRun = true)
    public void bindScenario() {
        // MasterClass reads client / ctx / softAssert / holder off this session
        // and throws IllegalStateException if nothing is bound.
        ImportedScenario.bind(client, ctx, softAssert, holder, "manual");
    }

    @AfterMethod(alwaysRun = true)
    public void unbindScenario() {
        // Delete rows this test created. Manual tests previously skipped this
        // entirely -- the generated SuiteCleanup is gitignored and
        // suite-specific, so nothing committed could reach it.
        ManualCleanup.afterEachTest(ctx);
        ImportedScenario.unbind();
    }

    @Test(dataProvider = "rows",
          dataProviderClass = PerMethodCsvDataProvider.class,
          groups = {"manual", "onboarding"})
    @Story("H4B account with an employee, read back")
    @Description("""
            Owner enrolled -> H4B account created from a named template ->
            account read back and asserted -> employee added -> member read
            back. Captures the ids each later phase depends on.
            """)
    public void h4bAccount_addsEmployeeAndReadsItBack(Map<String, String> row) {
        MasterClass.onboarding(row)

                // ---- phase 1: enrol the owner -------------------------
                // guestId is what createProgramAccount requires() next.
                .capture("guestId", ScenarioContext.GUEST_ID)
                .enrollOwner()

                // ---- phase 2: create the account ----------------------
                // using() picks the body by business meaning and applies to
                // this ONE phase. No accountId capture here: the phase
                // already publishes it internally via sc.put(ACCOUNT_ID, ...).
                .using(Template.singleMemberOnboarding)
                .expectExists("accountId")
                .createH4BAccount()

                // ---- phase 3: read it back ----------------------------
                // expectJson is overridable per row by the CSV column
                // expected_readProgramAccount_jsonpath_accountStatus;
                // an EMPTY cell skips the check rather than failing it.
                .expectJson("accountStatus", "pending")
                .capture("contactInfo.websiteDomain", ScenarioContext.WEBSITE_DOMAIN)
                .readProgramAccount()

                // ---- phase 4: add an employee -------------------------
                // exec name is createAccountMember, not addEmployee.
                .capture("memberId", ScenarioContext.MEMBER_ID)
                .addEmployee()

                // ---- phase 5: read the member back --------------------
                .expectExists("guestId")
                .readAccountMember()

                .complete();

        // BaseApiTest also asserts in @AfterMethod; doing it here reports
        // the failure against this method rather than the teardown.
        softAssert.assertAll();
    }
}
