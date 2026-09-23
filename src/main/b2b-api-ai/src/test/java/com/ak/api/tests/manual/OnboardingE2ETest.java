package com.ak.api.tests.manual;

import java.util.Map;

import org.testng.SkipException;
import org.testng.annotations.AfterMethod;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.BeforeMethod;
import org.testng.annotations.Test;

import com.ak.api.config.Config;
import com.ak.api.data.PerMethodCsvDataProvider;
import com.ak.api.dsl.CustomerOnboarding;
import com.ak.api.dsl.ManualCleanup;
import com.ak.api.support.ImportedRestClient;
import com.ak.api.support.ImportedScenario;
import com.ak.api.tests.BaseApiTest;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;

/**
 * Hand-written onboarding scenario -- the authoring surface, not converter
 * output.
 *
 * <p>Lives under {@code tests/manual/} deliberately. A converter run with
 * {@code --clean} deletes {@code tests/imported/<suite>/} and that suite's
 * Support/CSV/template tree; anything written there is lost. This package is
 * never touched by the converter (see {@code CreateTestCase.md}).</p>
 *
 * <p>Data comes from
 * {@code csv/OnboardingE2ETest/<methodName>.csv} via the same
 * convention-based provider the imported tests use, so both kinds of test are
 * driven identically. (Manual tests resolve to {@code csv/<ClassName>/} --
 * the {@code .tests.imported.} anchor in PerMethodCsvDataProvider does not
 * apply here.)</p>
 */
@Epic("Manual")
@Feature("Customer onboarding")
public class OnboardingE2ETest extends BaseApiTest {

    /**
     * Resolved by NAME, not by type.
     *
     * <p>A hand-written test must not import a generated client class: the
     * class only exists after converting the XML that produced it, so naming
     * it here makes this file fail to compile for anyone converting a
     * different suite. The client is looked up reflectively instead, and the
     * test skips cleanly when that suite has not been converted.</p>
     *
     * <p>Override with {@code -Dmanual.client=YourClient}.</p>
     */
    private ImportedRestClient client;

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
        String fqn = name.contains(".") ? name : "com.ak.api.rest.clients." + name;
        try {
            Class<?> type = Class.forName(fqn);
            client = (ImportedRestClient) com.ak.api.rest.SharedClients.get(
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
        // Same binding the generated tests use -- CustomerOnboarding.start()
        // reads the client / ctx / softAssert / holder off this session.
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
    @Story("H4L account onboarded and synchronised to Salesforce")
    @Description("""
            Travel agency -> owner enrolled -> H4L account created -> owner
            confirmed -> Salesforce prepared -> activated through HWS ->
            travel advisor enrolled -> synchronisation verified.
            """)
    public void h4lAccount_onboardsAndSynchronises(Map<String, String> row) {
        CustomerOnboarding.start(row)
                .createTravelAgency()
                .enrollOwner()
                .createH4LAccount()
                .confirmOwner()
                .prepareSalesforceAccount()
                .activateThroughHws()
                .enrollTravelAdvisor()
                .verifySynchronization()
                .complete();
    }

    @Test(dataProvider = "rows",
          dataProviderClass = PerMethodCsvDataProvider.class,
          groups = {"manual", "onboarding"})
    @Story("LTA account with a travel advisor and an employee")
    @Description("Shows the same vocabulary composing a different business flow.")
    public void ltaAccount_addsAdvisorAndEmployee(Map<String, String> row) {
        CustomerOnboarding.start(row)
                .enrollOwner()
                .createLTAAccount()
                .confirmOwner()
                .activateProgramAccount()
                .addTravelAdvisor()
                .addEmployee()
                .readAccountMember()
                .complete();
    }
}
