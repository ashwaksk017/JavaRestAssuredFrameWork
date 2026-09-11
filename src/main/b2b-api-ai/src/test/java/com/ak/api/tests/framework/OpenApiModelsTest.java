package com.ak.api.tests.framework;

import java.util.HashMap;
import java.util.Map;

import org.testng.Assert;
import org.testng.annotations.Test;
import org.testng.asserts.SoftAssert;

import com.ak.api.openapi.OpenApiModels;
import com.ak.api.openapi.programaccounts.model.ProgramAccount;
import com.ak.api.openapi.programaccounts.model.ProgramAccountContactInfo;
import com.ak.api.openapi.programaccounts.model.ProgramAccountStatus;
import com.ak.api.openapi.programaccounts.model.ProgramAccountSummary;
import com.ak.api.rest.utilities.ResponseAsserts;
import com.ak.api.rest.utilities.RestUtilities;
import com.ak.api.schema.SchemaValidator;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;
import io.restassured.builder.ResponseBuilder;
import io.restassured.http.ContentType;
import io.restassured.response.Response;

@Epic("API Automation")
@Feature("OpenAPI models")
public class OpenApiModelsTest {

    private static final String ACCOUNT_JSON = """
            {
              "accountId": 42,
              "status": "limited",
              "selfManaged": true,
              "contactInfo": {
                "name": "Acme Travel",
                "websiteDomain": "www.example.com"
              }
            }
            """;

    private static Response json(String body) {
        return new ResponseBuilder()
                .setStatusCode(200)
                .setContentType(ContentType.JSON)
                .setBody(body)
                .build();
    }

    @Test(groups = {"unit"})
    @Story("Generated ProgramAccount binds Jackson")
    @Description("Swagger ProgramAccount deserializes accountId, status, contactInfo.websiteDomain.")
    public void programAccount_roundTrip_bindsWebsiteDomain() {
        Response res = json(ACCOUNT_JSON);
        ProgramAccount account = OpenApiModels.as(res, ProgramAccount.class);
        Assert.assertEquals(account.getAccountId(), Long.valueOf(42L));
        Assert.assertEquals(account.getStatus(), ProgramAccountStatus.LIMITED);
        ProgramAccountContactInfo contact = account.getContactInfo();
        Assert.assertNotNull(contact);
        Assert.assertEquals(contact.getName(), "Acme Travel");
        Assert.assertEquals(contact.getWebsiteDomain(), "www.example.com");

        ProgramAccount again = OpenApiModels.asJson(OpenApiModels.toJson(account), ProgramAccount.class);
        Assert.assertEquals(again.getAccountId(), account.getAccountId());
        Assert.assertEquals(again.getContactInfo().getWebsiteDomain(), "www.example.com");
    }

    @Test(groups = {"unit"})
    @Story("ProgramAccountSummary named lookup")
    public void programAccountSummary_asNamed_usesGeneratedClass() {
        String body = "{\"accountId\":7,\"status\":\"active\",\"name\":\"Beta Co\"}";
        Object named = OpenApiModels.asNamed(json(body), "ProgramAccountSummary");
        Assert.assertTrue(named instanceof ProgramAccountSummary);
        ProgramAccountSummary summary = (ProgramAccountSummary) named;
        Assert.assertEquals(summary.getAccountId(), Long.valueOf(7L));
        Assert.assertEquals(summary.getStatus(), ProgramAccountStatus.ACTIVE);
        Assert.assertEquals(summary.getName(), "Beta Co");
        Assert.assertEquals(OpenApiModels.modelClass("ProgramAccountSummary"), ProgramAccountSummary.class);
    }

    @Test(groups = {"unit"})
    @Story("json-schema-validator against Swagger definitions")
    public void schemaValidator_matchesOpenApi_programAccount() {
        Response res = json(ACCOUNT_JSON);
        SchemaValidator.validateOpenApi(res, "ProgramAccount");
        Assert.assertTrue(SchemaValidator.matchesOpenApi(res, "ProgramAccount"));
        SoftAssert sa = new SoftAssert();
        ResponseAsserts.matchesOpenApi(sa, res, "ProgramAccount");
        sa.assertAll();
    }

    @Test(groups = {"unit"})
    @Story("Imported JsonPath polarity unchanged")
    @Description("Typed bind does not make jsonEquals pass a sibling scalar (accountStatus vs memberStatus).")
    public void jsonEquals_siblingScalar_stillFailsAfterTypedBind() {
        Response res = json("{\"accountStatus\":\"limited\",\"memberStatus\":\"active\"}");
        OpenApiModels.as(res, ProgramAccount.class);

        Assert.assertEquals(RestUtilities.safeJsonExtract(res, "accountStatus"), "limited");
        SoftAssert sa = new SoftAssert();
        Map<String, String> ctx = new HashMap<>();
        ResponseAsserts.jsonEquals(sa, res, ctx, new HashMap<>(),
                "post_confirm_validation_limited", "accountStatus", "active");
        try {
            sa.assertAll();
            Assert.fail("jsonEquals must not pass when accountStatus is limited and expected is active");
        } catch (AssertionError expected) {
            Assert.assertTrue(expected.getMessage().contains("accountStatus")
                    || expected.getMessage().contains("limited")
                    || expected.getMessage().contains("active"),
                    expected.getMessage());
        }
    }

    @Test(groups = {"unit"})
    @Story("ReadyAPI Domain path still aliases websiteDomain")
    public void jsonEquals_contactInfoDomain_stillReadsWebsiteDomain() {
        Response res = json(ACCOUNT_JSON);
        ProgramAccount account = ResponseAsserts.asModel(res, ProgramAccount.class);
        Assert.assertEquals(account.getContactInfo().getWebsiteDomain(), "www.example.com");
        Assert.assertEquals(RestUtilities.safeJsonExtract(res, "contactInfo.Domain"),
                "www.example.com");
        SoftAssert sa = new SoftAssert();
        ResponseAsserts.jsonEquals(sa, res, new HashMap<>(), new HashMap<>(),
                "http_get_account_details_200",
                "contactInfo.Domain", "www.example.com");
        sa.assertAll();
    }
}
