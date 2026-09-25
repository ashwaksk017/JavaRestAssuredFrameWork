package com.hi.api.tests.security;

import org.testng.Assert;
import org.testng.annotations.Test;

import com.hi.api.security.Secrets;

import io.qameta.allure.Description;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;

/**
 * Locks the credential-redaction contract: a credential lives in
 * program_configuration.json and nowhere else. These are pure unit tests
 * -- no HTTP, no config file required.
 *
 * <p>The value-based layer can only be asserted against a real config, so
 * these cover the key-based layer plus the invariants (idempotency,
 * placeholder safety, kill switch) that hold regardless of config state.</p>
 */
@Feature("Security")
public class SecretsRedactionTest {

    @Test(groups = {"unit", "security"})
    @Story("Token request body is masked")
    @Description("The exact leak found in the Extent reports: a token-request "
            + "body carrying client_id / client_secret / password.")
    public void tokenRequestBody_isFullyMasked() {
        String body = "{\n"
                + "    \"client_id\": \"P_C_someClientIdValue\",\n"
                + "    \"client_secret\": \"someSuperSecretValue\",\n"
                + "    \"password\": \"Sup3rSecret@\",\n"
                + "    \"username\": \"svc@example.com\"\n"
                + "}";
        String out = Secrets.redact(body);

        Assert.assertFalse(out.contains("someSuperSecretValue"), "client_secret leaked");
        Assert.assertFalse(out.contains("P_C_someClientIdValue"), "client_id leaked");
        Assert.assertFalse(out.contains("Sup3rSecret@"), "password leaked");
        // Non-secret fields survive, or the report becomes useless.
        Assert.assertTrue(out.contains("svc@example.com"), "username should NOT be masked");
        Assert.assertTrue(out.contains("client_secret"), "key names should survive");
    }

    @Test(groups = {"unit", "security"})
    @Story("Form-encoded and bearer credentials are masked")
    public void formAndBearer_areMasked() {
        String form = "grant_type=client_credentials&client_secret=abc123XYZsecret&scope=api";
        String outForm = Secrets.redact(form);
        Assert.assertFalse(outForm.contains("abc123XYZsecret"));
        Assert.assertTrue(outForm.contains("grant_type=client_credentials"),
                "non-secret form fields survive");

        String hdr = "Authorization: Bearer eyJhbGciOiJSUzI1NiJ9.payloadpayload.signature";
        String outHdr = Secrets.redact(hdr);
        Assert.assertFalse(outHdr.contains("eyJhbGciOiJSUzI1NiJ9.payloadpayload.signature"));
    }

    @Test(groups = {"unit", "security"})
    @Story("Salesforce JWT assertion is masked")
    public void salesforceAssertion_isMasked() {
        String jwt = "eyJhbGciOiJSUzI1NiJ9.eyJpc3MiOiJ4In0.aVeryLongSignatureValueHere123456";
        String body = "{\"grant_type\":\"urn:ietf:params:oauth:grant-type:jwt-bearer\","
                + "\"assertion\":\"" + jwt + "\"}";
        Assert.assertFalse(Secrets.redact(body).contains(jwt));
    }

    @Test(groups = {"unit", "security"})
    @Story("Redaction is idempotent")
    @Description("Redaction is applied at more than one layer by design "
            + "(filter + log policy), so a double pass must be a no-op.")
    public void redaction_isIdempotent() {
        String body = "{\"client_secret\": \"abc123XYZsecret\"}";
        String once = Secrets.redact(body);
        String twice = Secrets.redact(once);
        Assert.assertEquals(twice, once, "second pass must not re-mask");
    }

    @Test(groups = {"unit", "security"})
    @Story("Auth headers are masked wholesale by name")
    public void authHeaders_maskedByName() {
        Assert.assertEquals(Secrets.redactHeader("Authorization", "Bearer xyz"), Secrets.MASK);
        Assert.assertEquals(Secrets.redactHeader("Cookie", "SESSION=abc"), Secrets.MASK);
        Assert.assertEquals(Secrets.redactHeader("Accept", "application/json"),
                "application/json", "ordinary headers pass through");
    }

    @Test(groups = {"unit", "security"})
    @Story("Placeholder config values are never treated as secrets")
    @Description("Masking __SET_ME__ would hide the preflight banner that "
            + "tells the user to fill program_configuration.json in.")
    public void placeholders_areNotRedactable() {
        Assert.assertFalse(Secrets.isRedactable("__SET_ME__"));
        Assert.assertFalse(Secrets.isRedactable("changeme"));
        Assert.assertFalse(Secrets.isRedactable(""));
        Assert.assertFalse(Secrets.isRedactable("abc"), "too short to mask safely");
        Assert.assertTrue(Secrets.isRedactable("aRealLookingSecretValue"));
    }

    @Test(groups = {"unit", "security"})
    @Story("Null and empty input are safe")
    public void nullAndEmpty_areSafe() {
        Assert.assertNull(Secrets.redact(null));
        Assert.assertEquals(Secrets.redact(""), "");
    }
}
