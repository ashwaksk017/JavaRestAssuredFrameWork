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
src/test/resources/testng-manual.xml   (committed; do NOT use Suites/, it is gitignored)
```

Do not hand-edit files under `tests/imported/<converted-suite>/`. A later convert will wipe them.

### Naming `<optional-area>`

It is optional -- a class directly under `tests/manual/` is fine. When you do
use one, it is an ordinary Java package segment, so:

- any valid package name works (`account`, `member`, `booking`, `lta`);
- lower-case, no hyphens or spaces, not a Java reserved word;
- the folder MUST match the `package` declaration in the file;
- nest as deep as you like (`manual/lta/delegators/`).

Two things it does **not** change:

- **The CSV path.** Outside `tests.imported` the provider resolves by SIMPLE
  CLASS NAME -- `csv/<SimpleClassName>/<methodName>.csv` -- so the area
  folder never appears in it. Two classes with the same simple name in
  different areas silently share one row folder. Keep simple names unique.
- **Whether the suite finds it.** `testng-manual.xml` uses
  `<package name="com.ak.api.tests.manual.*"/>`. The trailing `.*` is what
  makes sub-packages run: measured on TestNG 7.10.2, the plain form without
  it skipped a sub-package class with no error at all. If you copy that
  suite, keep the `.*`.

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
| `expected_<phase>_status_code` | status for ONE phase of a hand-written chain |
| `expected_<phase>_jsonpath_<field>` | overrides `expectJson` for that phase; empty cell skips the check (see 14) |
| `expected_<phase>_exists_<field>` | flag for `expectExists`; `false` asserts ABSENT |
| `expected_<phase>_count_<field>` | overrides `expectCount` |
| `expected_<phase>_header_<name>` | flag for `expectHeader`; `false` asserts ABSENT |
| `template_<phase>` | request body for that phase; `using(Template)` overrides it (see 13) |

`<phase>` is the string the chain passes to `exec(...)`, which is **not
always the Java method name**. Get this wrong and the column is silently
ignored -- nothing fails, the override just never applies.

| You call | `<phase>` to use in the column |
|---|---|
| `createTravelAgency()` | `createTravelAgency` |
| `enrollGuest(Role)` / `enrollOwner()` / `enrollEmployee()` / `enrollTravelAdvisor()` | `enrollGuest` |
| `createProgramAccount(Partner)` / `createH4LAccount()` / `createH4BAccount()` / `createLTAAccount()` / `createSmbAccount()` | `createProgramAccount` |
| `readProgramAccount()` | `readProgramAccount` |
| `activateProgramAccount()` | `activateProgramAccount` |
| `confirmMemberTotp(Role)` / `confirmOwner()` / `confirmTravelAdvisor()` | `confirmMemberTotp` |
| `addMember(Role)` / `addEmployee()` / `addTravelAdvisor()` | **`createAccountMember`** |
| `readAccountMember()` | `readAccountMember` |
| `prepareSalesforceAccount()` | three phases: `fetchSalesforceToken`, `createSalesforceAccount`, `createSalesforceDistribution` |
| `activateThroughHws()` | `readSalesforceLead` |
| `verifySynchronization()` | `readSalesforceAccount` |

Regenerate this mapping any time with:

```
grep -n 'exec("' src/main/java/com/ak/api/dsl/CustomerOnboarding.java
```

### One caveat for manual tests

Outside `tests.imported`, the provider resolves by **simple class name**
(`getSimpleName()`), not by package. So two hand-written test classes with
the same simple name in different packages resolve to the SAME
`csv/<SimpleClassName>/` folder and would silently share row files. Keep
manual test class names unique across packages.

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
import com.ak.api.data.PerMethodCsvDataProvider;
import com.ak.api.dsl.CustomerOnboarding;
import com.ak.api.dsl.CustomerOnboarding.Partner;
import com.ak.api.dsl.CustomerOnboarding.Role;
import com.ak.api.dsl.ManualCleanup;
import com.ak.api.retry.RetryAnalyzer;
import com.ak.api.support.ImportedRestClient;
import com.ak.api.support.ImportedScenario;
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
        CustomerOnboarding.start(row)
                .enrollGuest(Role.OWNER)
                .createProgramAccount(Partner.H4L)
                .readProgramAccount()
                .complete();
        softAssert.assertAll();
    }
}
```

Rules for the `@Test` body:

- Keep it a **story chain**. Do not paste REST, Groovy, or JDBC here.
- `start(row)` must run before any phase. It also primes a client-credentials
  token when ctx has none -- a hand-written chain runs no `tokenRequest` step,
  so without that every phase sent an EMPTY bearer.
- `complete()` returns **void**. It ends the chain; it is not a handle.
- Assert with the chain's own `expect*` verbs (section 14). The `Insights.*`
  helpers take an `Object scenario` that `complete()` does not give you.
- Call `ManualCleanup.afterEachTest(ctx)` in `@AfterMethod`, before
  `ImportedScenario.unbind()`, or the rows this test creates are never
  deleted.
- `softAssert.assertAll()` belongs at the end of the method (and `BaseApiTest` also asserts in `@AfterMethod`).
- `@Test` signature must be `(Map<String, String> row)`.

You no longer need `AuthHelper.primeClientCredentialsToken(ctx)` in
`@BeforeClass`: `start(row)` primes when ctx has no token. It is skipped when
a token is already present, so an imported flow that fetches its own inline is
untouched, and it is gated on `Config.isUnset` for both credentials so a tree
carrying `__SET_ME__` placeholders never POSTs them at the token endpoint.

---

## 5. Which fluent methods exist

Authoritative list for HAND-WRITTEN tests:
`src/main/java/com/ak/api/dsl/CustomerOnboarding.java`. It is committed, and
the converter never touches it.

Do **not** author against
`src/main/java/com/ak/api/support/scenario/ScenarioSteps.java`. It is
generated, gitignored and rewritten by every `--clean`, it is ~18,000 lines,
and its numeric suffixes are cluster-derived and move between runs
(`enrollGuest2` became `enrollGuest52` within one day of reconverts). A test
bound to those names breaks silently on the next convert -- which is exactly
why `MasterClass` exposes only hand-written and stable client methods.

The complete set, with the `exec()` phase name each one reports under (that
is the name the per-phase CSV columns in section 3 key off):

| Verb | Phase name |
|---|---|
| `createTravelAgency()` | `createTravelAgency` |
| `enrollGuest(Role)` / `enrollOwner()` / `enrollEmployee()` / `enrollTravelAdvisor()` | `enrollGuest` |
| `createProgramAccount(Partner)` / `createH4LAccount()` / `createH4BAccount()` / `createLTAAccount()` / `createSmbAccount()` | `createProgramAccount` |
| `readProgramAccount()` | `readProgramAccount` |
| `activateProgramAccount()` | `activateProgramAccount` |
| `confirmMemberTotp(Role)` / `confirmOwner()` / `confirmTravelAdvisor()` | `confirmMemberTotp` |
| `addMember(Role)` / `addEmployee()` / `addTravelAdvisor()` | **`createAccountMember`** |
| `readAccountMember()` | `readAccountMember` |
| `prepareSalesforceAccount()` | `fetchSalesforceToken`, `createSalesforceAccount`, `createSalesforceDistribution` |
| `activateThroughHws()` (loud stub until the HWS UI exists -- do not treat as success) | `readSalesforceLead` |
| `verifySynchronization()` | `readSalesforceAccount` |

Plus the modifiers, which all bind to the **next** phase: `using(Template)`,
`capture(...)`, `expect*(...)` (sections 13-15). `complete()` ends the chain.

There are no numeric suffixes here. That is the point: the suffixed names in
`ScenarioSteps` move between reconverts, these do not.

Regenerate this mapping any time with:

```
grep -n 'exec("' src/main/java/com/ak/api/dsl/CustomerOnboarding.java
```

**Do not use `Insights.*` from a hand-written test.** Every one of its
methods takes an `Object scenario`, and `CustomerOnboarding.complete()`
returns **void** -- there is no handle to pass. It is also generated and
gitignored, so a manual test importing it breaks for anyone who converted a
different XML.

Assert with the chain's own verbs instead -- `expectJson`, `expectExists`,
`expectAbsent`, `expectCount`, `expectHeader`, `expectBodyContains`,
`expectSubstring`, `expectJsonTree`, `expectCaptured` (section 14). They are
per-phase, row-overridable, and committed.

---

## 6. Path B — unique REST step (Support, not `@Test`)

When a call is not already a `ScenarioSteps` method:

1. Add `src/main/java/com/ak/api/dsl/<Name>Flow.java`.

   > **Do not use `src/main/java/com/ak/api/support/manual/`.** `--clean`
   > spares it (it only wipes `support/<suite_name>`), but the WHOLE
   > `support/` tree is gitignored -- `git check-ignore` confirms it. Code
   > written there is never committed and disappears on a fresh clone.
   > `dsl/` is committed and the converter never writes to it.
   >
   > Extending the generated `ScenarioSteps` from there is still fine:
   > that class is suite-AGNOSTIC and re-emitted on every convert, so
   > committed code may name it. What committed code must never name is
   > a PER-SUITE type -- `<Suite>Client`, `support/<suite>/*`, or
   > `templates/<suite>/Templates`. `tools/check_generic.py` enforces
   > exactly that line.
2. Nested builder `extends ScenarioSteps<YourType>`.
3. Copy the shape of `start(row)` from `com.ak.api.dsl.CustomerOnboarding`
   (resolve the bound session, then `ImportedScenario.begin`). Note that
   `bootstrap()` is a `ScenarioSteps` method -- the `dsl` class has none,
   so inherit it from the builder you extend, not from there.
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

Do **not** add the class to `Suites/Programaccountregression_*.xml` (those
files are regenerated).

Use the committed **`src/test/resources/testng-manual.xml`**. It picks up the
whole `com.ak.api.tests.manual` package, so a new class needs no registration.

Do NOT put a suite under `Suites/` -- that directory is gitignored wholesale,
so anything there cannot be shared and every author would have to recreate it.
`testng-manual.xml` lives beside `testng-guards.xml` for that reason. It is
deliberately NOT part of `verify_all`: these make real HTTP calls and create
real accounts.

For reference, its shape:

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
            <!-- no per-class entry needed; the package is scanned -->
        </classes>
    </test>
</suite>
```

`configfailurepolicy="continue"` keeps later `@Test` methods in the same class running after a soft-assert failure.

### Plain `mvn test` does NOT run your manual tests

This is the single most common way to think a test ran when it did not.

`pom.xml` defaults `<suiteXmlFile>` to `src/test/resources/testng.xml`. That
suite lists only the sample classes (`com.ak.api.tests.samples`,
`com.ak.api.tests.data`, ...) behind group filters. It never mentions
`com.ak.api.tests.manual`. So a bare:

```powershell
mvn test
```

gives you a **green build that ran none of your hand-written tests**. No
error, no "skipped" count, nothing to notice. The only clue is a test total
that does not include yours.

To actually run them you MUST pass both of these:

```powershell
mvn test "-DsuiteXmlFile=src/test/resources/testng-manual.xml" -Dmanual.client=ProgramaccountregressionClient
```

- `-DsuiteXmlFile=...` selects `testng-manual.xml` instead of the default
  sample suite. Without it, your package is never scanned.
- `-Dmanual.client=...` names the generated client to bind. Without it the
  test skips, `using(...)` throws, and `ManualCleanup` silently does nothing
  (see **Config** below).

Sanity check: the run total should go up by the number of `@Test` methods you
added. If it did not change, your suite file argument did not take.

### Group filters can silently exclude your test

Every other suite in this repo filters by group:

| Suite | Group filter |
| ----- | ------------ |
| `Suites/Programaccountregression_Regression.xml` | `<include name="imported"/>` |
| `Suites/Programaccountregression_Smoke.xml` | `<include name="imported"/>` |
| `src/test/resources/testng.xml` | `smoke`, `regression`, `csv`, `auth`, ... (per `<test>` block) |
| `src/test/resources/testng-smoke.xml` | `smoke` |
| `src/test/resources/testng-manual.xml` | **none** |

`testng-manual.xml` has no `<groups>` block, which is why
`groups = {"manual", "onboarding"}` on the manual tests is currently
**decorative** -- nothing reads it.

It stops being decorative the moment anyone adds a filter. A TestNG `<groups>`
`<include>` is allow-list semantics: any method without a matching group is
dropped from the run with **no error and no skipped count**, exactly like the
default-suite problem above.

So: **if you add a `<groups>` block to `testng-manual.xml`, every manual test
must carry a matching group or it vanishes silently.** Keep putting
`groups = {"manual", "onboarding"}` on new `@Test` methods even though nothing
reads it today -- that annotation is the thing that keeps the filter safe to
add later.


### Config

Active env: `-Denv=qa` (or `TEST_ENV`), else `qa`. Values come from `program_configuration.json` (then properties files). Typical keys:

- `api_config.api_end_point` / `version` → `base_url`
- `api_config.client_id` / `client_secret` / `token_end_point` / `token_route`
- `database.*` if a phase runs JDBC
- `manual.client` -- **required for hand-written tests.** It names the
  generated client to bind, AND its derived suite name (`FooClient` -> `foo`)
  is what resolves `templates/<suite>/_index.csv` for `using(...)` and locates
  the generated `SuiteCleanup` for `ManualCleanup`. Get it wrong and the test
  skips, `using(...)` throws, and cleanup silently does nothing.
- `salesforce_assertion` / `sf_config.*` if you chain `prepareSalesforceAccount`

Do not invent Salesforce credentials or a full HWS Selenium flow.

#### Env var names are not what you would guess

`Config.get` (`src/main/java/com/ak/api/config/Config.java`) resolves an env
var as:

```java
String envKey = key.replace('.', '_').toUpperCase();
```

Dots to underscores, uppercase, and **nothing else**. There is no camelCase
split. So `domains.fromDb.enabled` reads `DOMAINS_FROMDB_ENABLED`, and the
natural-looking `DOMAINS_FROM_DB_ENABLED` is silently ignored -- an ignored
env var looks exactly like an unset one, so the default just quietly wins.

Two escape hatches, in order of preference:

1. Pass `-Dkey=value` on the Maven command line. System properties are checked
   first and use the key **verbatim**, dots and camelCase included
   (`-Ddomains.fromDb.enabled=true`). No transform, nothing to get wrong.
2. Use the REAL env var name from the table below.

Since the fix for this gap, `Config.get` also prints a `[Config] WARNING: ...`
on stderr (once per key) when it finds the naive spelling set and the real one
unset. Resolution is unchanged -- it only names both forms so the miss is
loud instead of silent. If you see that warning, rename your variable.

Every key in the codebase whose env-var name differs from the naive guess:

| Config key                       | REAL env var (this one works)    | Naive guess (silently ignored)       |
| -------------------------------- | -------------------------------- | ------------------------------------ |
| `auth.invalidateDebounceMs`      | `AUTH_INVALIDATEDEBOUNCEMS`      | `AUTH_INVALIDATE_DEBOUNCE_MS`        |
| `auth.tokenCache.enabled`        | `AUTH_TOKENCACHE_ENABLED`        | `AUTH_TOKEN_CACHE_ENABLED`           |
| `auth.tokenCache.skipRepeatFetch` | `AUTH_TOKENCACHE_SKIPREPEATFETCH` | `AUTH_TOKEN_CACHE_SKIP_REPEAT_FETCH` |
| `auth.tokenCache.ttlMs`          | `AUTH_TOKENCACHE_TTLMS`          | `AUTH_TOKEN_CACHE_TTL_MS`            |
| `auth.tokenRefresh.enabled`      | `AUTH_TOKENREFRESH_ENABLED`      | `AUTH_TOKEN_REFRESH_ENABLED`         |
| `demoshop.baseUrl`               | `DEMOSHOP_BASEURL`               | `DEMOSHOP_BASE_URL`                  |
| `demoshop.loginPath`             | `DEMOSHOP_LOGINPATH`             | `DEMOSHOP_LOGIN_PATH`                |
| `domains.fromDb.enabled`         | `DOMAINS_FROMDB_ENABLED`         | `DOMAINS_FROM_DB_ENABLED`            |
| `domains.fromDb.reason`          | `DOMAINS_FROMDB_REASON`          | `DOMAINS_FROM_DB_REASON`             |
| `domains.fromDb.table`           | `DOMAINS_FROMDB_TABLE`           | `DOMAINS_FROM_DB_TABLE`              |
| `gitlab.issueNotes`              | `GITLAB_ISSUENOTES`              | `GITLAB_ISSUE_NOTES`                 |
| `gitlab.mergeRequestNote`        | `GITLAB_MERGEREQUESTNOTE`        | `GITLAB_MERGE_REQUEST_NOTE`          |
| `gitlab.summaryDir`              | `GITLAB_SUMMARYDIR`              | `GITLAB_SUMMARY_DIR`                 |
| `rest.asyncBudgetCapMs`          | `REST_ASYNCBUDGETCAPMS`          | `REST_ASYNC_BUDGET_CAP_MS`           |
| `rest.asyncBudgetIntervalMs`     | `REST_ASYNCBUDGETINTERVALMS`     | `REST_ASYNC_BUDGET_INTERVAL_MS`      |
| `rest.deferDelays`               | `REST_DEFERDELAYS`               | `REST_DEFER_DELAYS`                  |
| `rest.failFastBrokenPath`        | `REST_FAILFASTBROKENPATH`        | `REST_FAIL_FAST_BROKEN_PATH`         |
| `rest.pollActivateReadyIntervalMs` | `REST_POLLACTIVATEREADYINTERVALMS` | `REST_POLL_ACTIVATE_READY_INTERVAL_MS` |
| `rest.pollActivateReadyMs`       | `REST_POLLACTIVATEREADYMS`       | `REST_POLL_ACTIVATE_READY_MS`        |
| `rest.pollExpectedJsonMs`        | `REST_POLLEXPECTEDJSONMS`        | `REST_POLL_EXPECTED_JSON_MS`         |
| `rest.pollPathParamMs`           | `REST_POLLPATHPARAMMS`           | `REST_POLL_PATH_PARAM_MS`            |
| `rest.pollSalesforceIdIntervalMs` | `REST_POLLSALESFORCEIDINTERVALMS` | `REST_POLL_SALESFORCE_ID_INTERVAL_MS` |
| `rest.pollSalesforceIdMs`        | `REST_POLLSALESFORCEIDMS`        | `REST_POLL_SALESFORCE_ID_MS`         |
| `test.interMethodCoolDownMs`     | `TEST_INTERMETHODCOOLDOWNMS`     | `TEST_INTER_METHOD_COOL_DOWN_MS`     |
| `test.isolateCtxPerMethod`       | `TEST_ISOLATECTXPERMETHOD`       | `TEST_ISOLATE_CTX_PER_METHOD`        |
| `xray.baseUrl`                   | `XRAY_BASEURL`                   | `XRAY_BASE_URL`                      |
| `xray.clientId`                  | `XRAY_CLIENTID`                  | `XRAY_CLIENT_ID`                     |
| `xray.clientSecret`              | `XRAY_CLIENTSECRET`              | `XRAY_CLIENT_SECRET`                 |
| `xray.testExecutionKey`          | `XRAY_TESTEXECUTIONKEY`          | `XRAY_TEST_EXECUTION_KEY`            |

Keys with no camelCase hump (`api_config.client_id` -> `API_CONFIG_CLIENT_ID`,
`database.host` -> `DATABASE_HOST`) are unaffected: naive and real agree.

Separately, `Config` **skips** the env-var step entirely for a short list of
OS-reserved names (`USERNAME`, `PASSWORD`, `HOME`, `PATH`, ...) so a Windows
machine's OS-set `USERNAME` cannot masquerade as test data. Use the nested
form (`API_CONFIG_USERNAME`, `SF_CONFIG_UI_USERNAME`) for those.


### Commands

```powershell
mvn -o test-compile

mvn -o test "-Dtest=com.ak.api.tests.manual.CreateLimitedAccountTest"

mvn -o test "-DsuiteXmlFile=src/test/resources/testng-manual.xml" -Dmanual.client=ProgramaccountregressionClient
```

`pom.xml` default suite is `src/test/resources/testng.xml`. Passing `-DsuiteXmlFile` selects yours.

---

## 9. Isolation, cleanup, and tokens

- TestNG imported suites use `parallel="classes"`. Keep `ctx` on the instance; bind/unbind per method.
- Cleanup is `@AfterMethod` only -- never wipe the DB in `@BeforeMethod` or
  `bootstrap()`. Generated tests call `SuiteCleanup.afterEachTest(ctx)`.
  Hand-written tests **cannot** (it is generated, gitignored and
  suite-specific) and must call `ManualCleanup.afterEachTest(ctx)`, which
  delegates to it reflectively.
- Token fetch belongs in setup, never as its own `@Test`. Generated flows use
  `flow_A`; hand-written chains get one from `start(row)` automatically.
- `ImportedScenario.begin` clears ctx except `accessToken`, then expands the CSV row.

---

## 10. What a later converter run will and will not do

| Artifact | Converter |
|---|---|
| `tests/manual/**`, `testng-manual.xml`, `csv/YourTest/**` | Untouched |
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
- [ ] `@AfterMethod` calls `ManualCleanup.afterEachTest(ctx)` then `unbind()`
- [ ] `@Test` takes `Map<String, String> row` and uses `dataProvider = "rows"`
- [ ] CSV path matches section 3 (file exists on classpath)
- [ ] Chain uses `CustomerOnboarding` verbs in HTTP order (NOT `ScenarioSteps` -- see section 5)
- [ ] Unique HTTP lives in Support + `RestStep`, not in the `@Test`
- [ ] No token `@Test`; no before-test domain wipe
- [ ] New client methods / templates documented if path C
- [ ] Runs via `-DsuiteXmlFile=src/test/resources/testng-manual.xml` with `-Dmanual.client=<TheClient>`
- [ ] `mvn -o test-compile` succeeds
- [ ] Ran the method once (`-Dtest=...`) and checked logs for STARTED/FINISHED + REST steps

---

## 12. Quick file map

```
# committed -- the converter never touches these
src/main/java/com/ak/api/dsl/CustomerOnboarding.java     # the fluent verbs
src/main/java/com/ak/api/dsl/MasterClass.java            # one entry point
src/main/java/com/ak/api/dsl/Template.java               # bodies by name
src/main/java/com/ak/api/dsl/ManualCleanup.java          # per-test cleanup
src/main/java/com/ak/api/context/ScenarioContext.java    # declared ctx fields
src/main/java/com/ak/api/data/PerMethodCsvDataProvider.java
src/test/java/com/ak/api/tests/manual/OnboardingE2ETest.java        # bare chain
src/test/java/com/ak/api/tests/manual/H4bMemberOnboardingTest.java  # + template/capture/assert
src/test/resources/testng-manual.xml

# generated + gitignored -- read, never edit or import from a manual test
src/main/java/com/ak/api/support/ImportedScenario.java
src/main/java/com/ak/api/support/scenario/ScenarioSteps.java
src/main/java/com/ak/api/support/<suite>/SuiteCleanup.java

# local only, gitignored (endpoints / emails / ids -- public repo)
src/test/resources/csv/<TestClass>/<methodName>.csv
```

---

## 13. Naming a template by business meaning

`Templates.<CONSTANT>` is fine for generated call sites but is **not** a
stable handle to choose a body by hand. The ordinal is assigned while
walking template paths in ascending content-hash order, so editing one
template renumbers its siblings. For this suite, 392 of 472 constants
(83%) sit in such groups -- `CreateAccount_200` alone has 22 rows behind
**14 different bodies** named `BUSINESSES_CREATEACCOUNT_200`, `_3`, `_9`,
`_10`, `_14`, ...

The ReadyAPI case name and step name are written by a human in the XML,
so they survive a reconvert. Every convert now exports that mapping:

| File | Purpose |
|---|---|
| `src/main/resources/templates/<suite>/_index.csv` | on the classpath; what the registry reads |
| `_audit/<suite>/templates.csv` | identical copy, for reading by eye |

Columns: `case,step,template,constant`.

### Using it

```java
MasterClass.onboarding(row)
    .using(Template.singleMemberOnboarding)
    .createH4LAccount()
    .complete();
```

Precedence for a phase body: explicit `.using(...)` > the
`template_<phase>` CSV column > the converter default. `using(...)`
applies to the **next phase only**, so a template cannot silently leak
into the rest of the chain.

### Adding a name

Look the pair up in `_index.csv`, then add a constant to
`com.ak.api.dsl.Template`:

```java
public static final Template myScenario = of(
        "what this body means",
        "<ReadyAPI case name>",
        "<ReadyAPI step name>");
```

`TemplateRegistryTest` resolves every curated name on each run, so a
ReadyAPI rename fails in the guards suite rather than by sending a wrong
body at runtime.

### Where the pieces live

| Path | Committed | Touched by convert |
|---|---|---|
| `com/ak/api/dsl/` (`MasterClass`, `Template`, `CustomerOnboarding`) | yes | never |
| `com/ak/api/domain/` (facades over all 75 client methods) | yes | never |
| `src/test/java/com/ak/api/tests/manual/` | yes | never |
| `src/test/resources/csv/<TestClass>/` | **no** (gitignored on purpose) | only `csv/<suite>` |

Test data stays local by design: `csv/` is gitignored because those files
carry endpoint paths, schemas, test-case IDs and hardcoded emails, and
this repo is public. Create your row file locally; do not commit it.

---

## 14. Validating responses

`RestStep` asserts the **status only**, from
`expected_<phase>_status_code`. It does not apply JsonPath, exists, count
or header expectations: generated tests get those from explicit
`ResponseAsserts` calls the converter emits per step, and a hand-written
phase has no emitter behind it. So a manual phase that only sets an
expected status will pass a 200 carrying entirely wrong content.

State body expectations explicitly:

```java
MasterClass.onboarding(row)
    .expectJson("accountStatus", "active")
    .expectExists("accountId")
    .createH4LAccount()
    .complete();
```

Verbs:

| Verb | Checks |
|---|---|
| `expectJson(path, value)` | value at that path |
| `expectExists(path)` / `expectAbsent(path)` | presence |
| `expectCount(path, n)` | list size at that path |
| `expectHeader(name)` | response header present |
| `expectBodyContains(value)` | value as an exact JSON scalar ANYWHERE in the body |
| `expectBodyContains(path, value)` | at that path, falling back to anywhere |
| `expectSubstring(path, value)` | the path's value CONTAINS value |
| `expectJsonTree(path, json)` | the subtree at that path equals a JSON document |
| `expectCaptured(key, value)` | a value in ctx -- memory, not the response |

`expectBodyContains` matches exact scalars only, so `verified` is not
satisfied by `unverified`. Prefer `expectJson` when you know the path: a
path-specific check cannot pass by coincidence.

`expectJsonTree` ignores key order, and is lenient in one way worth
knowing: when the trees are not equal it does not fail outright, but
instead checks that every scalar leaf of the expected document appears
somewhere in the body. So it is stricter than `expectBodyContains` and
looser than true equality. An expected document that is not parseable
JSON fails.

`expectCaptured` is the only expectation that reads MEMORY rather than
the response, so it pairs with `capture` (section 15): capture on one
phase, assert on a later one. It reads through `ImportedScenario.ctxGet`,
so aliases resolve. It is deliberately NOT CSV-overridable -- the natural
column shape (`expected_<phase>_ctx_<key>`) is not one
`check_csv_contracts` recognises, so inventing it would make any row
using it fail that gate.

### Not wired: OpenAPI schema assertions

`ResponseAsserts.matchesOpenApi(res, definitionName)` exists and would be
the strongest check available -- validate a whole response against the
API contract. It is deliberately NOT exposed as a verb, because it cannot
work in this repo as it stands:

- it resolves `openapi/ProgramAccounts-1.0.71.yaml` off the classpath,
  and that spec is not in the tree;
- `OpenApiModels.modelClass` needs generated models, which the pom
  produces at `generate-sources` FROM that same spec.

The spec is a vendor API contract and this repo is public, so it is not
committed -- and `src/main/resources/openapi/` is now gitignored so that
dropping it in locally cannot leak it. With the spec present, `mvn
generate-sources` produces the models and `matchesOpenApi` starts working;
wiring a verb for it is then a small change.

Each applies to the **next phase only** and is drained afterwards, even
when that phase throws, so an expectation can never assert against an
unrelated response.

### The value in code is a default, not a hard-code

Expectations route through the same `ResponseAsserts` entry points the
converter uses, against the same columns. The three families behave
differently, so they are worth stating exactly rather than as one rule:

| Stated | Column | Absent cell | Empty cell | Value in the cell |
|---|---|---|---|---|
| `expectJson(p, v)` | `expected_<phase>_jsonpath_<field>` | uses `v` | **skips the check** | that value is expected |
| `expectExists(p)` | `expected_<phase>_exists_<field>` | asserts present | asserts present | `false` asserts ABSENT |
| `expectCount(p, n)` | `expected_<phase>_count_<field>` | uses `n` | uses `n` | that count is expected |
| `expectHeader(h)` | `expected_<phase>_header_<name>` | asserts present | asserts present | `false` asserts ABSENT |

Only `expectJson` treats a present-but-empty cell as "skip this check".
That is deliberate fail-open behaviour in `rowSaysSkip`: it can lose a
check, but it can never invent a failure. The other three read an empty
cell as "nothing configured" and fall back to the value in code, so the
check still runs.

`expectExists` and `expectHeader` are FLAGS, not value overrides -- only
the literal `false` is special, and it inverts the assertion into an
absence check. For `expectHeader` the column name replaces every run of
non-alphanumeric characters in the header name with `_`, so
`Content-Type` reads `expected_<phase>_header_Content_Type`.

A malformed count cell falls back to the code value and logs a WARN
naming the column, rather than failing the row.

So one hand-written chain covers many rows, and a row can adjust or (for
`expectJson`) disable a single check without a code change.

### Soft assertions

All of these are soft: the chain runs to the end and every failure is
reported together. TWO things flush them, and you get the failure either
way:

- `complete()` calls `softAssert.assertAll()` at the end of a chain.
- `BaseApiTest.assertAll(ITestResult)` is an `@AfterMethod(alwaysRun =
  true)`. It flushes the thread's SoftAssert and stamps the result
  FAILURE. It deliberately does not rethrow: TestNG would report that as
  an `@AfterMethod` configuration failure and Allure would list
  `assertAll` as a fake test of its own.

So a chain that throws before reaching `complete()` still reports its
soft failures -- they are not silently swallowed. Finish the chain
anyway, because `complete()` is how a chain says it ended deliberately
rather than by accident.

### Polling, not asserting

`rest.pollExpectedJsonMs` (default `0`) makes `RestStep` treat
`expected_<phase>_jsonpath_*` as a WAIT condition before the assertion,
for endpoints that settle asynchronously. It is a poll, not a check: with
the property unset those columns cause no assertion on their own.

---

## 15. Capturing values into ctx

ctx is the memory that carries ids between phases. Outside a fluent
chain -- a raw `MasterClass` call, or a Path B class -- capture the way
generated code does:

```java
ImportedScenario.putExtracted(ctx, "Properties.guestID",
        RestUtilities.safeJsonExtract(res, "guestId"));
```

Inside a chain, phases return the builder rather than the `Response`, so
use the capture verbs:

```java
MasterClass.onboarding(row)
    .capture("guestId", ScenarioContext.GUEST_ID)
    .enrollOwner()
    .capture("accountId", ScenarioContext.ACCOUNT_ID)
    .expectJson("accountStatus", "active")
    .createH4LAccount()
    .complete();
```

Like the expectations, a capture applies to the **next phase only** and
is drained afterwards even if that phase throws.

### Order within a phase

Captures run **before** expectations. An expectation's expected value is
resolved against ctx, so it may legitimately reference something the same
response just published.

### Raw key or declared field

| Form | Writes through | Use when |
|---|---|---|
| `capture(path, "Some.key")` | `ImportedScenario.putExtracted` | any value; see the alias note below |
| `capture(path, ScenarioContext.ACCOUNT_ID)` | `ScenarioContext.put(Field, …)` | the framework already declares the id |

Prefer the declared field for `accountId`, `guestId`, `memberId`,
`travelAgentId` and friends: the value is written through the canonical
alias and mirrored onto any other declared alias already in ctx, so a
later phase reading an older spelling still finds it.

**The raw-key alias is narrow.** `putExtracted` publishes exactly one
extra spelling, and only when the key ends in `d` or `D`: it flips that
last character and writes the result too, so `Properties.guestID` also
lands as `Properties.guestId`. A key ending in anything else
(`Properties.Email`, `Properties.status`) gets **no** alias, and a phase
reading a different spelling will not find it. That is the real argument
for the declared field -- its aliases are a declared list, not a
single-character flip.

### Overwrite, not putIfAbsent

Capture uses `putExtracted`, which **overwrites**. That is deliberate:
`putIfNonEmpty` is `putIfAbsent`, so a stale generated default would beat
the value the server just returned. Use `putIfNonEmpty` only for seeding
defaults, never for an extracted id.

An absent JsonPath extracts empty, and an empty value is skipped and
logged, so a failed extract leaves whatever ctx already held rather than
blanking it mid-chain.

### Reading it back

```java
String id = chain.captured("Properties.guestID");   // alias-aware read
Map<String, String> raw = chain.ctx();              // escape hatch
```

`captured(key)` resolves declared aliases and the case-insensitive
fallbacks that `ImportedScenario.ctxGet` applies. `ctx()` hands back the
live map for anything the verbs do not cover -- prefer `capture` for
writes, since a bare `put` skips the alias publishing that lets later
phases and generated templates find the value.

### What ctx is not

`ImportedScenario.begin` clears ctx at the start of every row except
`accessToken`, and `bind` isolates it per test method. So ctx is memory
for ONE row of ONE test, not a place to pass state between tests.
