# Create a test case by hand (no converter)

This guide is for **authoring a new TestNG scenario in Java** without running `ra_converter`. The converter is how ReadyAPI XML becomes tests. The fluent reuse (`CustomerOnboarding`, `ScenarioSteps`, `Insights`) already lives in the framework, so a hand-written test can chain the same phases.

Reuse is **not** applied by editing imported Java. It is applied at convert time for XML suites, and at **runtime** when your `@Test` calls the shared types below.

---

## 0. Decide which path you are on

| Path | When to use | What you write |
|---|---|---|
| **A. Reuse existing phases** | Story is enroll / create account / read / TOTP / SF / invite LTA / etc. already on `ScenarioSteps` | Test class + CSV + TestNG XML |
| **B. New HTTP step** | One or more calls are not a shared phase | Path A, plus a Support class with `RestStep.exec` |
| **C. New API / new suite** | New client, templates, or cleanup | Path B, plus client method, template file, bind suite |

If the case already exists in a ReadyAPI XML, prefer converting that XML instead of cloning it by hand.

---

## 1. Do not put author tests next to imported suites

`--clean` on a converter run **deletes**:

- `src/test/java/com/ak/api/tests/imported/<suite>/`
- `src/main/java/com/ak/api/support/<suite>/` (including generated `*Support`)
- `src/test/resources/csv/<suite>/`
- that suite’s templates, `_audit/<suite>/`, `_flows/<suite>/`

Imported `Suites/<Suite>_Regression.xml` / `_Smoke.xml` are also rewritten.

**Put hand-written tests here:**

```
src/test/java/com/ak/api/tests/manual/<optional-area>/YourTest.java
src/test/resources/csv/YourTest/<methodName>.csv
src/main/java/com/ak/api/support/manual/scenario/YourSupport.java   (only for path B)
Suites/Manual.xml
```

Do not hand-edit files under `tests/imported/<converted-suite>/`. A later convert will wipe them.

---

## 2. Pick the suite you bind to

`ImportedScenario.bind(client, ctx, softAssert, holder, suiteName)` selects, for that thread:

| Bound by `suiteName` | Class / resource |
|---|---|
| REST client you construct | e.g. `ProgramAccountClient` |
| Token + DataGen setup | `com.ak.api.support.<suite>.SetupHelper.flow_A` |
| JSON templates | `com.ak.api.templates.<suite>.Templates` |
| Per-test DB cleanup | `com.ak.api.support.<suite>.SuiteCleanup` |
| Config-key list for `#c_id#` etc. | that suite’s `TestSupport.CONFIG_KEYS` |

Known `suiteName` values today:

- `programaccountregression` → `ProgramAccountClient`
- `accountmemberregression` → `ProgramAccountsClient`
- `smbtohwsconsumerregressione2e` → `SmbToHwsConsumerClient`

The client **must** implement `ImportedRestClient` if you call `CustomerOnboarding.start` (the three clients above already do).

`CustomerOnboarding.start(row)` always runs shared `bootstrap()`:

1. `ImportedScenario.runSetup("flow_A", ...)` (token fetch — **not** a `@Test`)
2. DataGen (`CtxFields.generateStandard`)
3. `CtxFields.seedFromRow(ctx, row, "Properties.")`

If you must skip `flow_A`, write your own Support and override `bootstrap()` (path B). Do not add a token `@Test`.

---

## 3. Add the data file (CSV, Excel, or JSON)

`PerMethodCsvDataProvider` (`dataProvider = "rows"`) loads **one file per `@Test` method**. A missing file fails the test with `IllegalStateException` (it will not run with zero rows).

Search order under the method path: **`.csv`**, then **`.xlsx`**, **`.xls`**, **`.json`**. Imported ReadyAPI cases still emit CSV; drop an Excel or JSON file next to (or instead of) the CSV to data-drive that method without changing Java.

Force a format: `-DdataFormat=xlsx` (or `csv` / `json` / `xls`). Excel sheet: `-DdataSheet=SheetName`. JSON array key: `-DdataArrayKey=rows`.

Hand-written tests can also use TestNG XML / `-DdataFile=`:

```
@Test(dataProvider = "fileData", dataProviderClass = DataProviders.class)
```

`fileData` picks CSV / Excel / JSON from the file extension. `csvData`, `excelData`, and `jsonData` stay available if you want to pin the format.

### Path rule

The provider looks at the test class FQN:

- If the FQN contains `.tests.imported.`, the file is  
  `csv/<everything after tests.imported.>/<methodName>.csv` (or `.xlsx` / `.json`)  
  (package folders match Java packages.)
- **Otherwise** (recommended for manual tests) the file is  
  `csv/<SimpleClassName>/<methodName>.csv` (or `.xlsx` / `.json`)

Example: class `com.ak.api.tests.manual.CreateLimitedAccountTest`, method `createLimitedAccount`:

```
src/test/resources/csv/CreateLimitedAccountTest/createLimitedAccount.csv
src/test/resources/csv/CreateLimitedAccountTest/createLimitedAccount.xlsx
src/test/resources/csv/CreateLimitedAccountTest/createLimitedAccount.json
```

CSV: save as UTF-8. Excel’s UTF-8 BOM is stripped. Excel workbooks: first row is headers; first sheet is the default.

### Columns

Minimum:

```csv
test_case_id,expected_status_code,expected
B2B-9999_create_limited_account,200,statusCode:200
```

Useful columns:

| Column | Used by |
|---|---|
| `test_case_id` | `CustomerOnboarding.start` / Allure name |
| `expected` | `expected(row)` → `key:value;key2:value2` (no semicolons inside values) |
| `expected_status_code` | Convention for the primary REST status |
| `Properties.Email`, `Properties.Username`, … | Seeded in bootstrap; override generated identity |
| `tpl_*` | Values substituted into JSON templates (`#tpl_...#`) |
| `_stop_after` | Optional; stop the chain after N REST steps (imported prefix-merge). Leave blank for a full run. |

Blank cells become `""`.

CSV cells may use faker tokens (expanded in `ImportedScenario.begin` / `PlaceholderResolver.resolveRow`):

`<<firstname>>` `<<lastname>>` `<<username>>` `<<email>>` `<<email(domain.com)>>` `<<phone>>` `<<company>>` `<<uuid>>` `<<unique>>` `<<alphanum(8)>>` `<<digits(9)>>`

Also `#Key#` and `@Key@` against ctx (after extracts).

Add a **row** to add a data variant. Do not copy the `@Test` method for the same HTTP shape.

---

## 4. Path A — test class that reuses shared phases

Create `src/test/java/com/ak/api/tests/manual/CreateLimitedAccountTest.java`.

```java
package com.ak.api.tests.manual;

import java.util.Map;

import org.testng.annotations.AfterMethod;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.BeforeMethod;
import org.testng.annotations.Test;

import com.ak.api.config.Config;
import com.ak.api.data.Expected;
import com.ak.api.data.PerMethodCsvDataProvider;
import com.ak.api.rest.clients.ProgramAccountClient;
import com.ak.api.retry.RetryAnalyzer;
import com.ak.api.support.ImportedScenario;
import com.ak.api.support.programaccountregression.SuiteCleanup;
import com.ak.api.support.scenario.CustomerOnboarding;
import com.ak.api.support.scenario.Insights;
import com.ak.api.tests.BaseApiTest;
import com.ak.api.xray.XrayTest;

import io.qameta.allure.Description;
import io.qameta.allure.Story;

public class CreateLimitedAccountTest extends BaseApiTest {

    private ProgramAccountClient client;

    // LinkedHashMap + synchronized: insertion-order aliases + parallel="methods" safety.
    // Suites use parallel="classes" today (one instance = one thread).
    private final Map<String, String> ctx =
            java.util.Collections.synchronizedMap(new java.util.LinkedHashMap<>());

    @BeforeClass(alwaysRun = true)
    public void initClient() {
        client = new ProgramAccountClient(Config.get("base_url", Config.baseUrl()));
        // Optional if this class has no flow_A / inline token step:
        // AuthHelper.primeClientCredentialsToken(ctx);
    }

    @BeforeMethod(alwaysRun = true)
    public void bindImportedScenario() {
        ImportedScenario.bind(client, ctx, softAssert, holder, "programaccountregression");
    }

    @AfterMethod(alwaysRun = true)
    public void afterEach() {
        SuiteCleanup.afterEachTest(ctx);   // after-test only; not a @Test
        ImportedScenario.unbind();
    }

    @Test(
            dataProvider = "rows",
            dataProviderClass = PerMethodCsvDataProvider.class,
            groups = { "manual" },
            retryAnalyzer = RetryAnalyzer.class)
    @XrayTest("B2B-9999")
    @Story("Create limited account")
    @Description("Hand-authored scenario that reuses shared fluent phases.")
    public void createLimitedAccount(Map<String, String> row) throws Exception {
        Expected expected = expected(row);
        var scenario = CustomerOnboarding.start(row)
                .enrollGuest()
                .createProgramAccount()
                .readProgramAccount()
                .complete();
        Insights.verifyProgramAccount(scenario, expected);
        softAssert.assertAll();
    }
}
```

Rules for the `@Test` body:

- Keep it a **story chain**. Do not paste REST, Groovy, or JDBC here.
- `start(row)` must run before any phase (it binds `s.flow` for Insights).
- `complete()` returns a scenario handle for verify helpers.
- `softAssert.assertAll()` belongs at the end of the method (and `BaseApiTest` also asserts in `@AfterMethod`).
- `@Test` signature must be `(Map<String, String> row)`.

Optional: `AuthHelper.primeClientCredentialsToken(ctx)` in `@BeforeClass` only when you are **not** using `flow_A` and need `ctx.accessToken` up front. Imported suites usually skip this because `flow_A` fetches the Hilton token.

---

## 5. Which fluent methods exist

Authoritative list: `src/main/java/com/ak/api/support/scenario/ScenarioSteps.java`.

A later convert **rewrites** that file from the fluent catalog. Methods with a trailing `2`, `3`, … are the **same kind of call later in the same flow** (ReadyAPI order), not extra helpers. Chain them in HTTP order.

Common story verbs (drop the numeric suffix unless you need a later repeat):

- `enrollGuest` (POST `/realms/guests/enroll`; a later enroll in the same flow is `enrollGuest2`)
- `createProgramAccount` / `createTravelAgency`
- `confirmMemberTotp`
- `readProgramAccount` / `readProgramAccountBeforeAttest` / `readOwnerAfterSync`
- `prepareSalesforceAccount` / `readSalesforceContact` / `readSalesforceProgramMember`
- `addAndActivateTravelAdvisor`
- `activateThroughHws` (loud stub until HWS UI exists — do not treat as success)
- `postBusinessesForcedenroll` / `getBusinesses` / `postBusinessesCompare`
- reservation / stay / partition / honors GET-PUT variants (`postGuestsGuestidReservations`, …)

**Insights** (`com.ak.api.support.scenario.Insights`) — trailing GET + payload asserts:

- `verifyProgramAccount` `verifyAccountMember` `verifyBusinesses`
- `verifyHhonors` `verifyPartitionId` `verifyVerify`
- `verifyLeadid` `verifyParticipationid`

Call Insights **after** `complete()`, and only if that verify body matches what you need. If asserts differ, keep them on a Support method (path B) instead of forcing Insights.

---

## 6. Path B — unique REST step (Support, not `@Test`)

When a call is not already a `ScenarioSteps` method:

1. Add `src/main/java/com/ak/api/support/manual/scenario/<Name>Support.java`.
2. Nested builder `extends ScenarioSteps<YourType>`.
3. Copy `start(row)` from `com.ak.api.support.scenario.CustomerOnboarding` (bind session, `ImportedScenario.begin`, `bootstrap()`).
4. Inherit shared methods (`enrollGuest()`, …). Override `bootstrap()` only if setup must differ.
5. Add only the **delta** methods.

Example delta:

```java
public YourType patchAccountStatus() throws Exception {
    this.patchStatusRes = RestStep.exec(ctx, row, softAssert, holder, testCaseId)
            .name("patch_account_status")
            .template(ImportedTemplates.get("BUSINESSES_HTTP_REQUEST_204_MERGED"))
            .expectedStatus(204)
            .patch("/guests/{guestId}/businesses/{accountId}",
                (body, q, h) -> client.activateProgramAccount(
                        ImportedScenario.ctxGet(ctx, "tokenId.GeneratedTokenID"),
                        ImportedScenario.ctxGet(ctx, "PropertiesaccountID.accountID"),
                        body));
    ImportedScenario.putExtracted(ctx, "patch_account_status_RawRequest",
            RestStep.lastResolvedBody());
    return this;
}
```

`RestStep` API:

| Method | Purpose |
|---|---|
| `name("...")` | ReadyAPI-style step name in logs / Allure |
| `template(classpath or Templates.X)` | Request JSON |
| `regenIdentity()` | Fresh Username/Email/etc. before the body is built |
| `query(name, "#Properties_Email#")` | Query/form placeholders |
| `header(name, value)` | Extra headers (not `Authorization`) |
| `expectedStatus(n)` | Soft-assert HTTP status |
| `.get/.post/.put/.patch/.delete(path, (body, q, h) -> client.foo(...))` | Wire call |

After a live response:

```java
ImportedScenario.putExtracted(ctx, "Properties.guestID",
        RestUtilities.safeJsonExtract(res, "guestId"));
```

Use `putExtracted` for response IDs (overwrite). Use `putIfNonEmpty` only for seeding defaults. Then `@Test` becomes:

```java
YourSupport.start(row)
        .enrollGuest()
        .createProgramAccount()
        .patchAccountStatus()
        .complete();
```

---

## 7. Path C — new client method or new template

### Client

Generated clients live in `src/main/java/com/ak/api/rest/clients/`. They use `// @generated` markers so a **reconvert can overwrite** unmarked regions.

If `ScenarioSteps` or `CustomerOnboarding` will call the new method:

1. Add `public Response yourOp(...)` on the suite `*Client`.
2. Add the same signature as a `default` method on `ImportedRestClient` (converter unions this on the next convert; re-add if it disappears).
3. Match arity already on the interface (`queryParams` / `extraHeaders` / body overloads). Wrong arity will not compile against `ImportedRestClient`.

If only your Support calls a **typed** `ProgramAccountClient`, you can skip `ImportedRestClient` until something shared needs it.

### Template

1. Add JSON under `src/main/resources/templates/<suite>/<area>/your_step.json`.
2. Use `#Properties_Email#`, `#c_id#`, `#tpl_...#` — not frozen env IDs.
3. Either add a constant on `com.ak.api.templates.<suite>.Templates` or pass the classpath string into `RestStep.template(...)`.
4. `Templates` for converted suites is **regenerated** on convert; prefer `ImportedTemplates.get("CONSTANT")` only if that field exists on the bound suite’s `Templates` class.

---

## 8. Register and run

Do **not** add the class to `Suites/Programaccountregression_*.xml` (those files are regenerated). Create `Suites/Manual.xml`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE suite SYSTEM "https://testng.org/testng-1.0.dtd">
<suite name="Manual" parallel="classes" thread-count="3" configfailurepolicy="continue">
    <parameter name="testSuite" value="manual"/>
    <listeners>
        <listener class-name="com.ak.api.reporting.ProgressLogListener"/>
        <listener class-name="com.ak.api.reporting.TestSuiteListener"/>
        <listener class-name="com.ak.api.reporting.TestCaseLogListener"/>
        <listener class-name="com.ak.api.reporting.ExtentReportListener"/>
        <listener class-name="com.ak.api.reporting.XrayReportListener"/>
    </listeners>
    <test name="manual">
        <classes>
            <class name="com.ak.api.tests.manual.CreateLimitedAccountTest"/>
        </classes>
    </test>
</suite>
```

`configfailurepolicy="continue"` keeps later `@Test` methods in the same class running after a soft-assert failure.

### Config

Active env: `-Denv=qa` (or `TEST_ENV`), else `qa`. Values come from `program_configuration.json` (then properties files). Typical keys:

- `api_config.api_end_point` / `version` → `base_url`
- `api_config.client_id` / `client_secret` / `token_end_point` / `token_route`
- `database.*` if a phase runs JDBC
- `salesforce_assertion` / `sf_config.*` if you chain `prepareSalesforceAccount`

Do not invent Salesforce credentials or a full HWS Selenium flow.

### Commands

```powershell
mvn -o test-compile

mvn -o test "-Dtest=com.ak.api.tests.manual.CreateLimitedAccountTest"

mvn -o test "-DsuiteXmlFile=Suites/Manual.xml" -Denv=qa
```

`pom.xml` default suite is `src/test/resources/testng.xml`. Passing `-DsuiteXmlFile` selects yours.

---

## 9. Isolation, cleanup, and tokens

- TestNG imported suites use `parallel="classes"`. Keep `ctx` on the instance; bind/unbind per method.
- Cleanup is **`@AfterMethod` → `SuiteCleanup.afterEachTest(ctx)` only**. Do not wipe the DB in `@BeforeMethod` or in `bootstrap()`.
- Token fetch belongs in setup (`flow_A` or `AuthHelper`), never as its own `@Test`.
- `ImportedScenario.begin` clears ctx except `accessToken`, then expands the CSV row.

---

## 10. What a later converter run will and will not do

| Artifact | Converter |
|---|---|
| `tests/manual/**`, `Suites/Manual.xml`, `csv/YourTest/**` | Untouched |
| `tests/imported/<suite>/**` | Deleted on `--clean`, rewritten |
| `support/scenario/ScenarioSteps.java` | Rewritten from catalog when any suite converts |
| `ImportedRestClient.java` | Rewritten as the union of `*Client` methods |
| Hand-added client methods without `@generated` protection | Can be lost on reconvert |

A manual `@Test` is **not** voted into the fluent catalog. Catalog reuse still comes from ReadyAPI XML. Your test only **calls** the shared Java that convert already emitted.

To have the same story generated for everyone, add it to the ReadyAPI project and reconvert.

---

## 11. Checklist

- [ ] Class is **not** under `tests/imported/<converted-suite>/`
- [ ] Extends `BaseApiTest`
- [ ] Client constructed from `Config` base URL
- [ ] `@BeforeMethod` calls `ImportedScenario.bind(..., "<suite>")`
- [ ] `@AfterMethod` calls `SuiteCleanup.afterEachTest(ctx)` then `unbind()`
- [ ] `@Test` takes `Map<String, String> row` and uses `dataProvider = "rows"`
- [ ] CSV path matches section 3 (file exists on classpath)
- [ ] Chain uses existing `ScenarioSteps` methods in HTTP order
- [ ] Unique HTTP lives in Support + `RestStep`, not in the `@Test`
- [ ] No token `@Test`; no before-test domain wipe
- [ ] New client methods / templates documented if path C
- [ ] Class listed in `Suites/Manual.xml` (or run with `-Dtest=`)
- [ ] `mvn -o test-compile` succeeds
- [ ] Ran the method once (`-Dtest=...`) and checked logs for STARTED/FINISHED + REST steps

---

## 12. Quick file map

```
src/test/java/com/ak/api/tests/manual/CreateLimitedAccountTest.java
src/test/resources/csv/CreateLimitedAccountTest/createLimitedAccount.csv
src/main/java/com/ak/api/support/manual/scenario/…Support.java   # path B only
src/main/java/com/ak/api/support/scenario/CustomerOnboarding.java
src/main/java/com/ak/api/support/scenario/ScenarioSteps.java
src/main/java/com/ak/api/support/scenario/Insights.java
src/main/java/com/ak/api/support/ImportedScenario.java
src/main/java/com/ak/api/data/PerMethodCsvDataProvider.java
Suites/Manual.xml
```
