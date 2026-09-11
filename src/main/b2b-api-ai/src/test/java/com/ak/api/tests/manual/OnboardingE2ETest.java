package com.ak.api.tests.manual;

import java.util.Map;

import org.testng.annotations.AfterMethod;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.BeforeMethod;
import org.testng.annotations.Test;

import com.ak.api.config.Config;
import com.ak.api.data.PerMethodCsvDataProvider;
import com.ak.api.dsl.CustomerOnboarding;
import com.ak.api.rest.clients.ProgramAccountClient;
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

    private ProgramAccountClient client;

    /** ctx carries IDs between phases for one test. */
    private final Map<String, String> ctx =
            java.util.Collections.synchronizedMap(new java.util.LinkedHashMap<>());

    @BeforeClass(alwaysRun = true)
    public void initClient() {
        String baseUrl = Config.get("base_url", Config.baseUrl());
        client = com.ak.api.rest.SharedClients.get(
                "ProgramAccountClient", baseUrl, ProgramAccountClient::new);
    }

    @BeforeMethod(alwaysRun = true)
    public void bindScenario() {
        // Same binding the generated tests use -- CustomerOnboarding.start()
        // reads the client / ctx / softAssert / holder off this session.
        ImportedScenario.bind(client, ctx, softAssert, holder, "manual");
    }

    @AfterMethod(alwaysRun = true)
    public void unbindScenario() {
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
