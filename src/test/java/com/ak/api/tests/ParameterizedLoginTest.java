// =============================================================================
// ParameterizedLoginTest -- pure XML-driven parameterization smoke test
// -----------------------------------------------------------------------------
// Demonstrates the framework's end-to-end parameterization pipeline:
//
//   testng.xml  <parameter name="dataFile" value="testdata/login_credentials.csv"/>
//        |                                                                    |
//        v                                                                    v
//   DataProviders.csvData (reads ITestContext for `dataFile`)                 |
//        |                                                                    |
//        v                                                                    v
//   CsvDataSource.asTestNg(path, start, end)  ->  Object[][] where each      |
//                                                 row is a Map<String,String>|
//        |                                                                    |
//        v                                                                    v
//   this @Test method receives each row as its sole argument.                 v
//                                              CSV columns become map keys.
//
// The credentials shipped in the CSV are deliberately fake -- this test's
// purpose is to prove the parameterization mechanism, not to log anyone in.
// To iterate over a different data file (e.g. a gitignored local copy with
// real creds), either edit the testng.xml <parameter> or override on the CLI:
//
//   mvn test "-Dgroups=parameterized" "-DdataFile=path/to/local-creds.csv"
// =============================================================================

package com.ak.api.tests;

import java.util.Map;

import org.testng.annotations.Test;

import com.ak.api.data.DataProviders;
import com.ak.api.data.Expected;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;

@Epic("API Automation")
@Feature("XML-Driven Parameterization")
public class ParameterizedLoginTest extends BaseApiTest {

    @Test(dataProvider = "csvData",
          dataProviderClass = DataProviders.class,
          groups = {"parameterized", "csv"})
    @Story("Row from testng.xml <parameter dataFile=...>")
    @Description("Each row of the CSV named by testng.xml's dataFile parameter is fed to this method as a Map<String,String>. Asserts the username + password columns arrived intact -- a pure mechanism check, not a login attempt.")
    public void login_paramsFromCsv(Map<String, String> row) {
        String username = row.get("username");
        String password = row.get("password");
        Expected exp    = expected(row);   // parses row.get("expected")

        softAssert.assertNotNull(username, "'username' column must be present in row");
        softAssert.assertNotNull(password, "'password' column must be present in row");
        softAssert.assertFalse(username == null || username.isBlank(),
                "'username' value must be non-blank");
        softAssert.assertFalse(password == null || password.isBlank(),
                "'password' value must be non-blank");

        // Every row's `expected` column packs multiple validations. Iterate
        // the ones the CSV declares -- keeps the test data-driven end to end.
        if (exp.has("domain")) {
            softAssert.assertTrue(username != null && username.endsWith("@" + exp.get("domain")),
                    "username domain matches expected: " + exp.get("domain"));
        }
        if (exp.has("pwLength")) {
            int expectedPwLen = exp.getInt("pwLength");
            softAssert.assertEquals(password == null ? 0 : password.length(), expectedPwLen,
                    "password length matches expected pwLength");
        }
        if (exp.has("isEmail")) {
            boolean shouldBeEmail = exp.getBool("isEmail");
            boolean actuallyEmail = username != null && username.contains("@") && username.contains(".");
            softAssert.assertEquals(actuallyEmail, shouldBeEmail,
                    "username email-shape matches expected isEmail");
        }

        System.out.printf("[ParameterizedLoginTest] row -> username=%s  password=%s  expected=%s%n",
                username, mask(password), exp.asMap());
    }

    private static String mask(String secret) {
        if (secret == null || secret.isEmpty()) return "<empty>";
        return "*".repeat(secret.length());
    }
}
