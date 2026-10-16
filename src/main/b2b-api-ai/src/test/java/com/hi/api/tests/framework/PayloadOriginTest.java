package com.hi.api.tests.framework;

import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.Map;

import org.testng.Assert;
import org.testng.annotations.Test;

import com.hi.api.data.PayloadProvenance;
import com.hi.api.data.PlaceholderResolver;
import com.hi.api.rest.utilities.RestUtilities;

/**
 * The [subst] log line says where a value came from. Seen live: a row a
 * test built in code, holding generated values, was logged origin=csv, and
 * the generated password was printed in full.
 */
public class PayloadOriginTest {

    @Test
    public void aRowATestBuiltSaysSoAndACsvRowSaysCsv() {
        Map<String, String> built = new LinkedHashMap<>();
        built.put("account_name", "Acme");
        Map<String, String> out = PlaceholderResolver.resolveRow(built, new HashMap<>());
        Assert.assertEquals(PayloadProvenance.originOf(out, "account_name"),
                "set by the test (not from a data sheet)");

        Map<String, String> csv = new LinkedHashMap<>();
        csv.put(PayloadProvenance.SOURCE_KEY, "csv/manual/x.csv#row1");
        csv.put("account_name", "Acme");
        Map<String, String> out2 = PlaceholderResolver.resolveRow(csv, new HashMap<>());
        Assert.assertEquals(PayloadProvenance.originOf(out2, "account_name"), "csv");
    }

    @Test
    public void aTokenIsGeneratedAndARefIsAContextValue() {
        Map<String, String> row = new LinkedHashMap<>();
        row.put("request_id", "<<uuid>>");
        row.put("owner_email", "${owner_email}");
        Map<String, String> ctx = new HashMap<>();
        ctx.put("owner_email", "someone@example.com");
        Map<String, String> out = PlaceholderResolver.resolveRow(row, ctx);
        Assert.assertEquals(PayloadProvenance.originOf(out, "request_id"), "generated <<uuid>>");
        Assert.assertTrue(PayloadProvenance.originOf(out, "owner_email").startsWith("from ${owner_email}"),
                PayloadProvenance.originOf(out, "owner_email"));
        Assert.assertFalse(out.keySet().stream().anyMatch(k -> k.startsWith("__origin")),
                "the origins are kept beside the row, not in it");
    }

    @Test
    public void aCredentialColumnIsMaskedInTheLogLine() {
        Assert.assertEquals(RestUtilities.shownValue("enroll_password", "not-a-real-password-1"), "****");
        Assert.assertEquals(RestUtilities.shownValue("client_secret", "abc"), "****");
        Assert.assertEquals(RestUtilities.shownValue("ACCESS_TOKEN", "abc"), "****");
        Assert.assertEquals(RestUtilities.shownValue("enroll_username", "someone"), "someone");
        Assert.assertEquals(RestUtilities.shownValue("enroll_password", ""), "");
    }
}
