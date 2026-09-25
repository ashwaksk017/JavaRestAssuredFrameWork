# API Automation Framework — Rest Assured 5 + TestNG 7 (Java 17)

Standalone, API-only automation framework built on **Rest Assured 5.x + TestNG 7 + Java 17**.

The utility layer (`RestUtilities`, `RestLoggerUtilityDataHolder`, `RestLogAppender`) is ported from a larger enterprise batch-payment framework (`BatchPaymentAutomation`, stack). Method signatures and placeholder semantics are preserved so calling code migrates 1:1; every P0 correctness gap in the reference is fixed here.

## Where do I find…

| I want to… | Go to |
|---|---|
| put a ReadyAPI XML in so the converter picks it up | [Quick start §1](#1-generate-everything-from-the-readyapi-xmls) — `tools/ra_converter/input/` |
| put the Swagger / OpenAPI spec in | [Where the OpenAPI (Swagger) spec goes](#where-the-openapi-swagger-spec-goes) — `src/main/resources/openapi/` |
| convert a suite driven by Excel DataSources | [Excel DataSources](#excel-datasources) — `--data-dir` |
| make a tree compile without any XML | `--bootstrap`, [Quick start §1](#1-generate-everything-from-the-readyapi-xmls) |
| read a value from the datasheet into a request body | [Authoring template values](#authoring-template-values--types-the-datasheet-and-random-data) |
| send a number or boolean rather than a string | [Authoring template values](#authoring-template-values--types-the-datasheet-and-random-data) |
| generate random / unique data per run | [Authoring template values](#authoring-template-values--types-the-datasheet-and-random-data) |
| know where a hand-written test's CSV goes | [CreateTestCase.md](CreateTestCase.md) — `src/test/resources/csv/<ClassSimpleName>/<method>.csv` |
| know where a hand-written body template goes | [CreateTestCase.md](CreateTestCase.md) §7 — `src/test/resources/templates/manual/` |
| write a test by hand | [CreateTestCase.md](CreateTestCase.md) (the authoring manual) |
| see which phases the DSL offers | [PHASES.md](PHASES.md) |
| understand convert-time vs run-time | [ARCHITECTURE.md](ARCHITECTURE.md) |
| tune retries | [Configuration hierarchy](#configuration-hierarchy) — `wholeTestRetry`, `tokenRetry` |
| see the response at a breakpoint, mid-chain | [Reading the response](#reading-the-response--lastresponse) — `lastResponse()` |
| run only the framework guards (no HTTP, no DB) | [Quick start §3](#3-run-the-tests) — `testng-guards.xml` |

The companion docs: **[CreateTestCase.md](CreateTestCase.md)** (writing a test
by hand), **[ARCHITECTURE.md](ARCHITECTURE.md)** (convert-time vs run-time,
generated vs hand-written), **[PHASES.md](PHASES.md)** (every phase the
onboarding DSL offers). Start here; the table above says which one answers
what.

## Quick start — commands

> **Two ways in on a fresh checkout.** The committed Java imports
> `com.hi.api.support.*`, which is generated and gitignored — a clone has none
> of it, so `mvn compile` fails until it exists. Either convert an XML
> (step 1 below), or — to write a hand-written test **before** you have any
> XML — emit just the framework types:
>
> ```bash
> python tools/ra_converter/ra_converter.py --bootstrap --output . --package-root com.hi.api
> ```
>
> `--bootstrap` needs no `--input`. It writes the bundled support types, an
> `ImportedRestClient` covering every method the committed tree calls, and a
> `rest/manual/client/ManualClient.java` scaffold for you to fill in — the
> generated clients live in gitignored `rest/clients/` and only exist after a
> convert. The scaffold is skip-if-exists, so re-running never touches your
> edits. What it does NOT give you is
> anything needing a converted suite: `templates/<suite>/_index.csv` does not
> exist, so 11 of the 175 guards fail on a bare clone and `Template.of(case,
> step)` has nothing to resolve — use `Template.ofPath(...)` until you convert.
> A later convert replaces `ImportedRestClient` with the real union and leaves
> the other framework files alone. See **CreateTestCase.md §0**, "Starting from
> a clone with no conversions".

### 1. Generate everything from the ReadyAPI XMLs

**First, put the XMLs where the converter looks.** Export the ReadyAPI /
SoapUI project and drop the `.xml` files into:

```
tools/ra_converter/input/
```

That directory's contents are gitignored (`tools/ra_converter/.gitignore`) —
the exports carry endpoint paths, request/response schemas and hard-coded
identifiers, so they stay local and only the directory's own README is
tracked. A fresh clone therefore has **no** XML, and the converter has
nothing to do until you add one. See
[`tools/ra_converter/input/README.md`](tools/ra_converter/input/README.md).

If you only want a tree that compiles, you do not need an XML at all —
`--bootstrap` emits the framework types without parsing anything:

```powershell
python tools/ra_converter/ra_converter.py --bootstrap --output . --package-root com.hi.api
```

Then convert:

```powershell
python tools/ra_converter/ra_converter.py `
    --input tools/ra_converter/input `
    --output . `
    --package-root com.hi.api `
    --clean `
    --max-name-len 40 `
    --no-cursor-assist
```

Converts **all** XMLs in `tools/ra_converter/input/` in ONE process. That is
deliberate: fluent-phase votes are computed across every suite before any test
is emitted, so a phase shared by two suites lands on one shared
`ScenarioSteps` method instead of two suite-local copies. Converting suites
separately (or in parallel) loses that, and several processes would race on
the same `ScenarioSteps.java` / `fluent_catalog.json`.

Expect: **18/18 suites, 1276 cases, 0 tracebacks**, ~20 minutes.

Single suite (rarely what you want — see above):

```powershell
python tools/ra_converter/ra_converter.py --input tools/ra_converter/input/membervalidationregression.xml `
    --output . --package-root com.hi.api --service-name MemberValidation --clean --max-name-len 40
```

`--clean` deletes that suite's generated tests / CSVs / templates /
`_audit` / `_flows` / SetupHelper / TestSupport / SuiteCleanup. It must NOT
delete `support/scenario/` or `fluent_catalog.json`.

### Excel DataSources

A ReadyAPI case driven by a **DataSource + DataSource Loop** runs its steps
once per row of a spreadsheet. The XML names that spreadsheet but does not
contain it:

```xml
<con:dataSource type="Excel">
  <con:configuration><file>${projectDir}/data/shopSearch.xlsx</file>
  <worksheet>200_success</worksheet><cell>A2</cell></con:configuration>
```

`${projectDir}` is a property of the ReadyAPI installation that exported the
project, so it does not resolve here. Point the converter at the workbooks:

```powershell
python tools/ra_converter/ra_converter.py --input tools/ra_converter/input/Project.xml `
    --output . --package-root com.hi.api --data-dir C:\path	o\workbooks
```

Only the file NAME is used. The converter looks in `--data-dir` first, then
beside the input XML, then a `data/` folder next to it, and reports which
workbook it read so "which copy of that workbook was it" is never a guess.

Each workbook row becomes **one CSV row**, so the per-method data provider
replays the test once per row — the same iteration ReadyAPI's loop performed.
Needs `openpyxl` (`pip install openpyxl`); without it the convert still
succeeds and says why no rows arrived.

**Without the workbooks the convert still works**, and says exactly what is
missing per case:

```
[ra_converter] DataSource rows: 4 case(s) imported 32 row(s); 24 case(s) could not
    get_shop_200                                 8 row(s)
    [no data] workbook `ratePlans.xlsx` not found (looked in: ...)
```

A case with no data converts to a single row of **empty** values — it will
run and send blanks. That is reported, not silent: the step shows `STUB` in
`_audit/<suite>/steps.csv` with the workbook and worksheet named, and the
loop shows `PARTIAL`. Both read `FULL` once the rows are imported.

### 2. Verify

```powershell
python tools/verify_all.py            # 7 checks, ~12s
python tools/verify_all.py --full     # + Java compile + TestNG guards, ~5min
```

One command instead of nine. Run `--full` **before any reconvert or commit**.
Exit code is 0 only when every check passes; a failing check prints the last
25 lines of its output.

| Check | Guards against |
|---|---|
| `contracts` | Converter behaviours regressed before (last-write-wins, `/remove` is POST, existence polarity) |
| `cross-case` | Identity pack, member-enroll email overlay, token-step skip-regen |
| `emit-shape` | A dangling block in the JDBC emit — this class of bug once made 13 of 18 suites fail silently and lose ~390k lines |
| `assertions` | An assertion type converting to *nothing* while coverage still reports FULL |
| `groovy-dates` | `java.time` computation vanishing instead of reaching ctx |
| `vocabulary` | Phase names deriving from the full path, so a sub-resource is never swallowed by its parent |
| `generated` | Filename/class mismatch, unbalanced braces, leftover TODOs, implausibly few files, new conversion holes vs baseline |
| `java-compile` *(--full)* | All generated Java still compiles |
| `java-tests` *(--full)* | Credential redaction, SQL guards, typed context, deferred delays |

Individually, if you want just one:

```powershell
python tools/ra_converter/test_converter_fixes.py
python tools/ra_converter/test_cross_case_contracts.py
python tools/ra_converter/test_jdbc_emit_shape.py
python tools/ra_converter/test_assertion_coverage.py
python tools/ra_converter/test_groovy_datetime.py
python tools/ra_converter/phase_vocabulary.py
python tools/check_generated_output.py
python tools/check_generated_output.py --update-baseline   # after a deliberate change
```

### 3. Run the tests

```powershell
# One converted suite
mvn test "-DsuiteXmlFile=Suites/Programaccountregression_Regression.xml"
mvn test "-DsuiteXmlFile=Suites/Programaccountregression_Smoke.xml"

# Framework guard tests only -- no HTTP, no DB, no credentials needed
mvn test "-DsuiteXmlFile=src/test/resources/testng-guards.xml"

# One class
mvn test "-Dtest=com.hi.api.tests.imported.membervalidationregression.members.B2B2047ConfirmValidation200BugTest" -DfailIfNoTests=false
```

**Credentials are required for anything that talks to stg.** Fill
`src/main/resources/program_configuration.json` first — it ships stubbed with
`__SET_ME__`, and `Config.preflightIssues()` prints one clear banner naming
the file rather than letting the run fail as a wall of 401s. The file is
gitignored; never commit real values.

### Runtime switches worth knowing

```powershell
# ReadyAPI Delay steps defer into a shared budget instead of sleeping (default ON).
# A later step that fails its expected status spends the budget; nothing racy costs nothing.
mvn test "-Drest.deferDelays=false"        # restore a real sleep at every delay

# Bounded re-read when a step's asserted JsonPath value has not propagated yet (default OFF)
mvn test "-Drest.pollExpectedJsonMs=10000"

# Show raw request/response payloads while debugging (default is redacted)
mvn test "-Dsecurity.redaction.enabled=false"
```

Credential redaction is **on by default** and covers every sink — Extent HTML,
per-test logs, console (so a redirected `> full-run.log` is safe too), and
Allure attachments.

**Non-Latin test data is logged as-is, not as `??????`.** ReadyAPI datasheets
carry CJK / accented values (`tpl_contactInfo_name`, addresses, ...). The wire
was always right (Rest Assured sends JSON as UTF-8); the `?` came from the
Windows JDK 17 default charset (Cp1252) at three points, all pinned to UTF-8
now: `RestUtilities.getRequestTemplate` (template read), the Log4j console
layout (`charset="UTF-8"` in `log4j2.xml`) plus the surefire fork
(`-Dfile.encoding=UTF-8 ...` in the pom `argLine`), and the Maven JVM itself
via `.mvn/jvm.config` — Surefire re-prints the fork's output and the failure
summary through Maven's own stdout, so that JVM has to be UTF-8 too. Nothing
to set on the Maven command line. One thing is outside Maven's reach: a
**Windows PowerShell 5.1** `>` redirect re-decodes the JVM's bytes with the
console code page (cp1252 / cp437 by default), so `mvn test > full-run.log`
from a plain PowerShell window turns 鴕火 into `é´•ç«`. `cmd.exe` passes
the bytes through untouched. From PowerShell use the wrapper, which sets the
console to UTF-8 for that one invocation and forwards every argument:

```powershell
tools\mvn-utf8.ps1 test "-DsuiteXmlFile=Suites/Programaccountregression_Regression.xml" > full-run.log
```

If a `?` still shows up in a payload after that, it was a `?` in the datasheet.

---

### Making the converter fit another ReadyAPI project (`converter.config.json`)

The parser, translators, CSV/template emission, dedup, audit and digest are
project-agnostic. What is not is the *data realism* layer: which
`Properties.*` fields the ReadyAPI DataGen script regenerates per run,
which key holds the frozen domain, what the member-enroll steps are
called, which saved ids are pre-existing fixtures, and a few validation
quirks (Salesforce ids, OTP zero-padding). All of that is now read from
`tools/ra_converter/converter.config.json`:

| Section | What it drives |
|---|---|
| `project` | ticket-prefix regex (dropped from test names), product-line tokens (stripped so siblings share a class), partner tokens (CSV `partner` column) |
| `identity` | standard pack fields, regen trigger keys, identity/id hints, frozen-domain key, allowed-domains config key, freemail list, bindable email/domain slots, named identity keys, member-enroll step patterns, fixture-literal fields |
| `heuristics` | Salesforce id field/shape/session key; OTP pad width, code-like names and exclusions |

The Python emitter reads it directly; the Java runtime reads the same
values from `src/main/resources/converter_identity.json`, which every
convert regenerates from the config (never edit that file). The committed
values are this suite's, so a convert with no overrides produces the same
tree as before; a tree without the resource falls back to the same
built-in defaults. For a new project: copy the config, change the
`identity` names to what that project's DataGen script writes, adjust
`project.ticket_regex` and the tokens, convert, and read the first
digest. Still hand-written per API and next in line: the domain receiver
tables and phase vocabulary in `fluent_scenario.py` /
`phase_vocabulary.py`.

### Test-case diagrams as images (`converter.config.json`)

Every convert writes one Mermaid flowchart per ReadyAPI case under
`_flows/<suite>/cases/<case>.md` (open in VS Code or GitHub with Mermaid
preview). To also get an image per case, switch it on in the committed
`tools/ra_converter/converter.config.json`, or per machine in a gitignored
`converter.config.local.json` next to it, or for one run:

```powershell
python tools/ra_converter/ra_converter.py --input tools/ra_converter/input --output . --package-root com.hi.api --clean --max-name-len 40 --no-cursor-assist `
    --diagram-png --diagram-cases "B2B-5264*"
```

Images land in `_flows/<suite>/png/<case>.png` (or `.svg`), the case file
gets an **Image** link, and the suite index gains an Image column. Keys
under `diagrams.png`: `enabled`, `format` (png/svg), `scale`, `background`,
`cases` (`*`, a glob, `re:<regex>`, or a list of names), `renderer`
(`auto`, `mmdc`, `playwright`). Rendering needs one of:

- `npm i -g @mermaid-js/mermaid-cli` (the `mmdc` command), or
- `pip install playwright && python -m playwright install chromium`.

With Playwright, only the Mermaid library is fetched from jsDelivr; the
diagram text is rendered inside the local headless browser and never
leaves the machine. Without a renderer the convert still succeeds and says
what to install. A full suite of ~700 diagrams takes a few minutes; the
`cases` filter keeps a one-off render short.

### Phases as data (`--phase-specs`)

```
python tools/ra_converter/ra_converter.py --input tools/ra_converter/input --output . --package-root com.hi.api --clean --max-name-len 40 --phase-specs
```

Opt-in for now (the default output is unchanged until a regression run
matches it). With the flag, a ReadyAPI step is no longer copied into a
method of its own -- 1,304 of them on programaccountregression, 30,000
lines in one class -- but described as data:

- `support/<suite>/cases/<TestClass>Phases.java` registers each case's
  phases in `CaseRegistry`: step name, verb + path, where each path
  parameter comes from (`Ref.ctx` / `Ref.resp` / `Ref.row` / literal),
  token, template, expected status, polling, query placeholders,
  extracts and the CSV-driven checks. A compound phase is a sequence of
  parts.
- `support/<suite>/cases/Hooks.java` holds each distinct translated
  Groovy / transfer / script-assertion block once; a spec names its hook.
- `support/<suite>/Calls.java` is the engine: one `case` per typed
  client operation, the only per-call code left.
- The chain reads `.enrollGuest().createProgramAccount().readProgramAccount()`.
  When a case runs the same phase twice, every occurrence names its
  ReadyAPI step: `.readProgramAccount("get_account_after_activate")` --
  the same name the CSV columns and the `step=` log lines use.
- `Insights.verifyProgramAccount(scenario, expected)` runs that case's
  verify spec through the same engine.

The run ends with `[ra_converter] reuse: ...` -- the duplication report
(`_audit/<suite>/dedup_report.txt`, also in `summary.md`): methods that
are the same call with different data, entry classes that differ only
in generated fields, and templates that are the same shape with the
same placeholders. Under the flag the first two read `0 groups`.

Run-time rules worth knowing: `readProgramAccount()` throws if the case
runs it more than once ("say which one: readProgramAccount(\"a\", \"b\")");
a prefix-merged cluster's shorter members stop after the REST count in
their `_stop_after` CSV cell -- and, unlike the text path, make no further
calls at all once stopped. Hand-written flows (`MasterClass`) are
unaffected: they drive `RestStep` themselves.

## What's inside

| Concern | Where | Notes |
|---|---|---|
| **HTTP verbs** | `com.hi.api.rest.utilities.RestUtilities` | POST / GET / PUT / PATCH / DELETE, all thread-safe |
| **Placeholder templates** | `RestUtilities.mapJsonValues(schema, dataMap, strict)` | Regex-based, strict mode throws `UnresolvedPlaceholderException` |
| **Environment config** | `com.hi.api.config.Config` + `application-{env}.properties` | Env-var > `-Dkey` > `application-{env}.properties` > `application.properties` > defaults |
| **Auth** | `com.hi.api.auth.AuthUtilities` | Bearer / Basic / OAuth2 client-credentials with token cache |
| **JSON schema validation** | `com.hi.api.schema.SchemaValidator` + `src/test/resources/schemas/*.json` | Wraps Rest Assured's `matchesJsonSchemaInClasspath`; `validateOpenApi` uses Swagger definitions |
| **POJO models** | `com.hi.api.models.*` | Hand-written demo types; `RestUtilities.as(response, Post.class)` |
| **OpenAPI models** | `com.hi.api.openapi.OpenApiModels` + generated `com.hi.api.openapi.programaccounts.model` | Maven codegen from `src/main/resources/openapi/ProgramAccounts-1.0.71.yaml`; imported tests stay JsonPath + ctx |
| **Data-driven (CSV)** | `com.hi.api.data.CsvDataSource` + `DataProviders.csvData` | jackson-dataformat-csv; headers-as-keys map rows or typed POJOs; startRow/endRow range |
| **Data-driven (JSON)** | `com.hi.api.data.JsonDataSource` + `DataProviders.jsonData` | Top-level array or `{rows: [...]}`; typed POJOs; same row-range semantics |
| **Response-time SLOs** | `RestUtilities.assertResponseTimeBelow(response, millis)` | AssertionError with actual vs ceiling |
| **Retry** | `com.hi.api.retry.RetryAnalyzer` | Attach via `@Test(retryAnalyzer = RetryAnalyzer.class)`; max count from Config |
| **Reporting (Allure)** | `io.qameta.allure:allure-testng` + `AllureRestAssured` filter | Rich hierarchical HTML via `mvn allure:serve` or `allure:report` |
| **Reporting (ExtentReports)** | `com.hi.api.reporting.ExtentReportListener` | Self-contained HTML in `extent-reports/`, no CLI to open |
| **Request/response capture** | `com.hi.api.reporting.RestAssuredRecordingFilter` + `ReportBuffer` | Every exchange auto-attached to BOTH reports for passed AND failed tests |
| **Counters + suite lifecycle** | `com.hi.api.reporting.TestSuiteListener` | Pass/fail totals via ITestListener; reset once per suite |

## Two reporting options — both run every time

Both reports are generated on every `mvn test` — you pick which one to hand to which audience.

### Allure — polished, hierarchical, tester-facing

- Rich UI: features → stories → tests, severity, retries, history-across-runs.
- Every HTTP exchange auto-attached as request + response artifacts via `AllureRestAssured` filter (no per-test code).
- Requires the Allure CLI (or `mvn allure:serve` / `allure:report`) to view.

```powershell
# Generate + open in browser (requires Java + Allure CLI):
mvn allure:serve

# Just generate to target/allure-report/:
mvn allure:report

# Keep Trends / Retries / Duration history across local runs (gitignored cache):
python tools/allure_history.py restore
mvn allure:report
python tools/allure_history.py save
```

GitHub Actions and GitLab Pages restore `allure-history/` before `allure:report` and save the new `target/allure-report/history/` afterward, so Allure trend widgets accumulate across pipelines. Reports themselves stay gitignored; only the small history JSON is cached (and uploaded as a CI artifact).

Toggle: `report.allure.enabled=false` in `application.properties` (or `-Dreport.allure.enabled=false`) to skip.

### ExtentReports — self-contained HTML, stakeholder-facing

- Single dark-themed HTML file at `extent-reports/<timestamp>-Extent.html`.
- No CLI needed — double-click the file, opens in a browser.
- **Every HTTP exchange rendered as method/URL summary + REQUEST BODY (blue label) + RESPONSE BODY (green label) as JSON code blocks — for passed AND failed tests.**
- System info panel shows env, base URL, auth type.

Toggle: `report.extent.enabled=false` in `application.properties` (or `-Dreport.extent.enabled=false`) to skip.

**Which one to demo?** Allure looks more polished in a QA / SDET interview context because of the hierarchy and the history-across-runs feature. Extent is stronger when handing a static file to a non-technical stakeholder — no tooling required to open.

## Project layout

```
ApiAutomationRestAssured/
├── pom.xml
├── README.md
├── .gitignore
├── .github/
│   └── workflows/api-tests.yml
├── .gitlab-ci.yml
├── tools/allure_history.py                       -- restore/save Allure history for trend charts
├── logs/                                         -- one .log per test class (banner-separator style, reference-compat)
├── extent-reports/                               -- generated on each run
├── allure-history/                               -- gitignored cache of Allure history JSON (CI + local)
├── target/allure-results/                        -- generated by AllureRestAssured filter
├── target/allure-report/                         -- generated by `mvn allure:report`
├── src/
│   ├── main/
│   │   ├── java/
│   │   │   └── com/hi/api/
│   │   │       ├── config/Config.java
│   │   │       ├── auth/AuthUtilities.java
│   │   │       ├── schema/SchemaValidator.java
│   │   │       ├── openapi/OpenApiModels.java    -- bind + Swagger definition checks
│   │   │       ├── models/Post.java
│   │   │       ├── retry/RetryAnalyzer.java
│   │   │       ├── data/                         -- CSV + JSON data providers
│   │   │       │   ├── CsvDataSource.java
│   │   │       │   ├── JsonDataSource.java
│   │   │       │   └── DataProviders.java        -- TestNG @DataProvider facade
│   │   │       └── rest/utilities/
│   │   │           ├── RestUtilities.java        -- HTTP verbs + placeholders + logging
│   │   │           ├── RestLoggerUtilityDataHolder.java
│   │   │           └── RestLogAppender.java
│   │   └── resources/
│   │       ├── application.properties
│   │       ├── application-qa.properties
│   │       └── templates/createPost.json         -- template with #/%/@ placeholders
│   └── test/
│       ├── java/
│       │   └── com/hi/api/
│       │       ├── reporting/
│       │       │   ├── ReportBuffer.java         -- thread-local exchange buffer
│       │       │   ├── RestAssuredRecordingFilter.java -- feeds ReportBuffer + ExtentListener
│       │       │   ├── ExtentReportListener.java -- ExtentReports HTML
│       │       │   └── TestSuiteListener.java    -- counters
│       │       └── tests/
│       │           ├── BaseApiTest.java          -- @BeforeSuite bootstraps RestAssured + filters + auth
│       │           ├── PostsSmokeTests.java      -- GET/POST/PUT/PATCH/DELETE, JsonPath asserts, groups
│       │           ├── PostsDataDrivenTest.java  -- inline @DataProvider (kept as smallest example)
│       │           ├── PostsCsvDrivenTest.java   -- Jackson-CSV DataProvider (posts.csv)
│       │           ├── PostsJsonDrivenTest.java  -- Jackson-JSON DataProvider (posts.json)
│       │           └── AuthTests.java            -- Bearer + Basic against httpbin.org (5 tests)
│       └── resources/
│           ├── testng.xml                        -- listeners + groups + per-<test> dataFile parameters
│           ├── allure.properties
│           ├── log4j2.xml
│           ├── schemas/post-schema.json          -- JSON schema for validation
│           └── testdata/
│               ├── posts.csv                     -- sample CSV rows for PostsCsvDrivenTest
│               └── posts.json                    -- sample JSON rows for PostsJsonDrivenTest
```

## Configuration hierarchy

Every value read via `Config.get(...)` follows this precedence, highest priority first:

1. `-Dkey=value` on the Maven command line
2. Environment variable `KEY` (dots become underscores, uppercased)
3. `application-{env}.properties` on the classpath
4. `application.properties` on the classpath
5. Built-in default in the accessor

Env selection: `-Denv` → `TEST_ENV` env var → `env` property → `qa`.

### Switching environments

```powershell
mvn test "-Denv=qa"
mvn test "-Denv=stg" "-DbaseUrl=https://stg.example.com"
```

### Switching auth

```powershell
mvn test "-Dauth.type=bearer" "-Dauth.bearer.token=eyJhbGciOi..."
mvn test "-Dauth.type=basic" "-Dauth.basic.username=svc" "-Dauth.basic.password=secret"
mvn test "-Dauth.type=oauth2" `
    "-Dauth.oauth2.tokenUrl=https://auth.example/oauth2/token" `
    "-Dauth.oauth2.clientId=abc" "-Dauth.oauth2.clientSecret=def" "-Dauth.oauth2.scope=api"
```

## Utility API reference

### `RestUtilities` — HTTP verbs (all return `io.restassured.response.Response`)

```java
Response getResponsePost   (String body, String url, Map<String,String> headers);
Response getResponseGet    (             String url, Map<String,String> headers);  // 2-arg overload
Response getResponseGet    (String body, String url, Map<String,String> headers);  // reference-compat
Response getResponsePut    (String body, String url, Map<String,String> headers);
Response getResponsePatch  (String body, String url, Map<String,String> headers);
Response getResponseDelete (             String url, Map<String,String> headers);
```

### Authoring template values — types, the datasheet, and random data

A request-body template is JSON with placeholders. **The delimiter you choose
decides the JSON type**, because the scalar forms match the surrounding
quotes and replace them:

| In the template | Produces | JSON type |
|---|---|---|
| `"addressLine1": "#Properties_addressLine1#"` | `"221B Baker St"` | **string** (JSON-escaped) |
| `"employeeCount": "@Properties_employeeCount@"` | `33` | **number** — quotes consumed |
| `"annualSpend": "@Properties_annualSpend@"` | `10923427.5` | **number** |
| `"selfManaged": "%Properties_selfManaged%"` | `true` | **boolean** — quotes consumed |

One CSV cell holding `33` becomes the string `"33"` or the number `33`
purely by which delimiter names it. Values in the `#...#` form are
JSON-escaped on the way in, so a value containing `"` or `\` cannot corrupt
the payload.

#### Reading a value from the datasheet

Replace the literal with a placeholder and add a column of the same name:

```json
"addressLine1": "#Properties_addressLine1#"
```
```csv
Properties.addressLine1
221B Baker St
```

**Dots in the CSV column, underscores in the template.** `mergedRow`
aliases `.` to `_`, which is what makes those the same key. Get it wrong
and the field resolves to the type's fallback below rather than failing, so
a typo looks like a server problem.

#### Random data

Two forms, both usable directly in a template or in a CSV cell:

| Form | Behaviour | Use when |
|---|---|---|
| `<<email(example.com)>>`, `<<digits(9)>>` | **fresh on every occurrence** | each field should differ |
| `${username}`, `${email}` | **generated once, stable for the rest of the row** | two fields must agree |

```json
"stableUser":    "${username}",
"sameUserAgain": "${username}"     →  both "jdzdqn"
```

`<<>>` vocabulary: `name firstName lastName username(N) email email(domain)
phone address city state zip country company uuid unique int(min,max)
alphanum(N) alpha(N) digits(N)`.
`${}` auto-generated keys: `email email_domain domain phone username
firstName lastName uuid`.

Order matters: `#/@/%` resolve first against the merged row, then `<<>>` and
`${}` against ctx — so a CSV cell may itself contain `<<email>>` and it is
still generated.

A hand-written test gets no `regenIdentity()`, so a fixed literal is sent on
every run and a second run can collide on a duplicate. Use `${}` / `<<>>`
for anything that must be unique per run.

### `RestUtilities` — Placeholder substitution

| Placeholder in template | dataMap resolved | dataMap missing (non-strict) | dataMap missing (strict) |
|---|---|---|---|
| `#key#` | value | `null` | `UnresolvedPlaceholderException` |
| `"%key%"` | value | `false` | `UnresolvedPlaceholderException` |
| `"@key@"` | value | `0` | `UnresolvedPlaceholderException` |

```java
String mapJsonValues(String schema, Map<String,String> dataMap);                // non-strict, reference-compat
String mapJsonValues(String schema, Map<String,String> dataMap, boolean strict);
String mapJsonValues(Reader reader, Map<String,String> dataMap);
String mapJsonValues(Reader reader, Map<String,String> dataMap, boolean strict);
```

### `RestUtilities` — Response + POJO helpers

```java
String  checkActualResponse    (Response r);
String  getResponseAsString    (Response r);
boolean containsStringPattern  (String pattern, Response r);   // discouraged; use JsonPath instead
void    assertResponseTimeBelow(Response r, long ceilingMs);

<T> T   as(Response r, Class<T> type);                         // Jackson bind
String  toJson(Object payload);                                // Jackson serialize
```

### Reading the response — `lastResponse()`

Every call path here returns something other than the `Response`: a chained
phase returns the flow so the chain can continue, and a converted chain keeps
each response in a `protected <step>Res` field the test class cannot read. So
a breakpoint on `.enrollOwner()` stops on the **builder**, not on the
exchange, and the body used to be three frames down inside `RestStep`.

Three accessors fix that, and they are on every path:

```java
lastResponse()            // the response of the call that just happened
lastStep()                // which step that was  (lastPhase() on the manual chain)
responseOf("createProgramAccount")   // an EARLIER step, by name
```

Where they live:

| Path | Call it on |
|---|---|
| converted test | the chain — `Onboarding.start(row).enrollGuest().lastResponse()` |
| manual chain | the flow — `CustomerOnboarding` / `OnboardingFlow` stages |
| legacy-style test | `LastExchange.response()` — the test already holds its own `Response` too |
| anywhere, including a breakpoint in generated code | `LastExchange.response()` |

All four read the **same** per-thread record
([`LastExchange`](src/main/java/com/hi/api/rest/utilities/LastExchange.java)),
so they cannot disagree about the same call. It is written by `RestStep` and
by the global Rest Assured recording filter, which means an auth fetch and a
plain `RestUtilities.post(...)` are recorded too — anything that reached the
wire.

#### Worked example: the response of one step in a converted test

A converted chain returns the flow, so `.enrollGuest()` hands back the chain,
not the response. Three ways to reach it, in the order they are usually
wanted.

**1. Break the chain and ask.** Every phase returns `S`, so the accessors are
available mid-chain:

```java
var flow = Onboarding.start(row, "<case id>")
        .enrollGuest();
Response enrolled = flow.lastResponse();          // what enrollGuest got
String   id       = enrolled.jsonPath().getString("guestId");
flow.createProgramAccount().complete();           // carry on
```

**2. By ReadyAPI step name, from anywhere later in the test.** The step map
at the top of every generated method gives you the name to pass — that is
what it is for:

```java
// ReadyAPI steps -> where each one runs here:
//   HHonorsEnroll        .enrollGuest()          <- this name
//   http_request_200_1   .createProgramAccount()

Onboarding.start(row, "<case id>")
        .enrollGuest()
        .createProgramAccount()
        .complete();

Response enrolled = LastExchange.of("HHonorsEnroll");   // still available
```

**3. At a breakpoint, with no code change at all.** Put the breakpoint on the
line *after* the phase you care about — stopping *on* `.enrollGuest()` means
it has not run yet — and evaluate:

```java
LastExchange.response()                  // the call that just happened
LastExchange.of("HHonorsEnroll")         // that step specifically
LastExchange.body()                      // its body as a String
LastExchange.steps()                     // every step so far, in order
```

This is the one that needs no edit and no re-run, so it is usually the right
one while debugging.

At a breakpoint, type any of these in the evaluate window:

```java
LastExchange.response().getStatusCode()
LastExchange.body()                    // the body as a String
LastExchange.steps()                   // every step so far, in call order
LastExchange.all()                     // each one as "POST /accounts -> 201  (createAccount)"
LastExchange.of("enrollGuest").jsonPath().getString("guestId")
```

#### Inspecting the REQUEST

The same record holds what went out, so you can compare the two without
leaving the debugger:

```java
LastExchange.requestBody()                      // what the last call SENT
LastExchange.requestBodyOf("HHonorsEnroll")     // what that step sent
LastExchange.last().method()                    // POST
LastExchange.last().uri()                       // the resolved URL
```

**It is redacted.** The token call's body IS the client secret, so the
recording filter redacts before the record ever holds it — a debugging aid
must not become the second place a credential is readable.

Three other views of the request, when you want more than the body:

| Want | Where |
|---|---|
| the resolved body of the most recent `RestStep` | `RestStep.lastResolvedBody()` |
| verb, path, query keys, body, per step | the run log — `-> POST /path`, `.. query/form keys=[...]`, `.. request body (N chars)` |
| request + response side by side, per step | the Allure attachments on that step |

Two rules worth knowing:

* **it is the settled response.** A step that transiently retried, or that
  refreshed a dead token and replayed, records the attempt that counted — not
  the 503 that was thrown away.
* **it is per test.** `BaseApiTest`'s `@BeforeMethod` clears it, because
  TestNG pools threads and a stale `200` read at a breakpoint looks like an
  answer.

The step name is the one already in the Allure step title and in the
`expected_<step>_status_code` CSV column, so there is nothing new to look up
before calling `responseOf(...)`.

### `SchemaValidator`

```java
SchemaValidator.validate(response, "post-schema.json");       // throws AssertionError on mismatch
boolean ok = SchemaValidator.matches(response, "post-schema.json");
SchemaValidator.validateOpenApi(response, "ProgramAccount");  // Swagger definitions in the YAML spec
```

### Where the OpenAPI (Swagger) spec goes

Drop the `.yaml` / `.json` spec here:

```
src/main/resources/openapi/
```

`--bootstrap` creates that directory and drops a README in it, so a fresh
tree shows you where the file goes instead of leaving you to find it in the
pom. The README is the only file in there git tracks -- without it the
directory is invisible on a clone, because git stores no empty directories.

That directory is gitignored (`.gitignore`, `src/main/resources/openapi/`)
for the same reason `tools/ra_converter/input/` is: a spec is a vendor API
contract — endpoint paths, schemas, examples — and this repo is public. So a
fresh clone has **no** spec, and you supply your own.

Generation is **off by default** (`<openapi.codegen.skip>true</openapi.codegen.skip>`
in `pom.xml`), which is what lets a clone with no spec still compile. Turn it
on for a run:

```powershell
mvn clean test -Dopenapi.codegen.skip=false
```

That flag does three things at once, via the `openapi-codegen` profile:

| | |
|---|---|
| runs `openapi-generator` at `generate-sources` | reading `src/main/resources/openapi/${openapi.spec}` — default `ProgramAccounts-1.0.71.yaml` |
| emits models into `target/generated-sources/openapi` | package `com.hi.api.openapi.programaccounts.model` |
| restores the tests that reference them | `${openapi.test.excludes}` — today `OpenApiModelsTest.java` |

**When the version changes.** The file name carries the API version, so it
changes on every vendor release. Two readers need it — the generator's
`<inputSpec>` at build time, and `OpenApiModels`' classpath lookup at run
time — and both take it from one key, `openapi.spec`:

```powershell
mvn clean test -Dopenapi.codegen.skip=false -Dopenapi.spec=abc.yaml
```

Change the pom's `<openapi.spec>` default instead when the new version is
permanent, or set `openapi.spec` in `program_configuration.json` /
`OPENAPI_SPEC`, by the usual [Configuration hierarchy](#configuration-hierarchy).

They share a key on purpose: moving only one of them generates models from
one spec and validates bodies against another, and that mismatch surfaces
nowhere near its cause. A spec in the folder that `openapi.spec` does not
name is simply ignored — the build will not find it and will not say so.

Two things the spec is used for, and only one of them needs generation:

* **typed models** — `OpenApiModels.as(res, ProgramAccount.class)`. Needs
  generation on, so it needs the flag.
* **schema assertions** — `SchemaValidator.validateOpenApi(res, "ProgramAccount")`
  reads the YAML's `definitions` at runtime off the classpath. It needs the
  spec present, not generated, so it works without the flag.

Neither is required by a converted test: those assert with JsonPath and a
`Map<String,String>` ctx, so a tree with no spec at all runs the full imported
suite. Typed binding is additive.

### OpenAPI models (Program Accounts)

Maven generates Jackson models from `src/main/resources/openapi/ProgramAccounts-1.0.71.yaml` into `target/generated-sources/openapi` (`com.hi.api.openapi.programaccounts.model`). Imported ReadyAPI tests keep JsonPath + `Map<String,String>` ctx; typed bind is additive:

```java
ProgramAccount account = OpenApiModels.as(res, ProgramAccount.class);
ProgramAccount typed = accounts.readAs(token, accountId, ProgramAccount.class);
```

### Auth wiring — exercised by AuthTests

`AuthUtilities` (`bearer` / `basic` / `oauth2ClientCredentialsToken`) is verified end-to-end by [AuthTests](src/test/java/com/hi/api/tests/AuthTests.java) against [httpbin.org](https://httpbin.org):

| Test | httpbin endpoint | Asserts |
|---|---|---|
| `bearer_positive_validToken` | `GET /bearer` | 200 + `authenticated=true` + echoed token |
| `bearer_negative_missingHeader` | `GET /bearer` (no header) | 401 |
| `basic_positive_matching` | `GET /basic-auth/{u}/{p}` | 200 + `authenticated=true` + echoed user |
| `basic_negative_wrongPassword` | `GET /basic-auth/{u}/{p}` | 401 |
| `header_roundTripsToServer` | `GET /headers` | Bearer header echoed back — proves it left the JVM |

Run just the auth suite:

```powershell
mvn test "-Dgroups=auth"
```

The auth suite requires outbound HTTPS to httpbin.org. On air-gapped CI, exclude the `auth` group or point the tests at an internal echo service. OAuth2 client-credentials is wired but not exercised here (needs a real token endpoint); the code path is tested during suite bootstrap when `-Dauth.type=oauth2` + credentials are supplied.

### Dynamic headers — env / suite / per-request

`Headers.builder()` composes `Map<String,String>` header maps that plug directly into any `RestUtilities.getResponseXxx(url, headers)` call. Values can come from four sources; later calls win on conflict:

```java
Map<String,String> h = Headers.builder()
        .fromFile("headers/qa.json")           // env-scoped stack from a file
        .contentTypeJson()                     // presets
        .bearer(token)                         // per-request Authorization
        .correlationId()                       // auto UUID
        .header("X-Tenant-Id", "acme")         // arbitrary
        .build();
```

**File loaders** — accept classpath OR filesystem paths; auto-detect by extension:

| Method | Format | Example |
|---|---|---|
| `Headers.fromJson(path)` | `{"X-Api-Key":"..."}` or `{"headers":{...}}` | `headers/qa.json` |
| `Headers.fromProperties(path)` | Java `.properties` (# comments OK) | `headers/qa.properties` |
| `Headers.fromText(path)` | HTTP-style `Name: value` per line | `headers/qa.txt` |
| `Headers.fromFile(path)` | Auto-detect by extension | any of the above |

Sample fixtures ship under [src/test/resources/headers/](src/test/resources/headers/). Loader correctness + one httpbin round-trip are covered by [HeadersFromFileTests](src/test/java/com/hi/api/tests/HeadersFromFileTests.java).

### Live login sample — Tricentis DemoWebshop

[DemoWebshopLoginTest](src/test/java/com/hi/api/tests/DemoWebshopLoginTest.java) drives the login contract at [demowebshop.tricentis.com](https://demowebshop.tricentis.com) end-to-end — a classic form-encoded ASP.NET login with cookie-based session (no JSON tokens). Three tests:

| Test | Signal |
|---|---|
| `login_validCredentials_returns302AndAuthCookie` | 302 → `/`, `NOPCOMMERCE.AUTH` cookie set, under 3s SLO |
| `login_wrongPassword_returns200AndErrorMessage` | 200 (no redirect), body contains "Login was unsuccessful", no auth cookie |
| `authCookie_unlocksProtectedPage` | Cookie carried to `GET /customer/info` returns 200 with the user's email in the body |

**Credentials — never commit them.** The test reads `demoshop.username` / `demoshop.password` from Config; if either is blank, the test SKIPs (does not fail). Supply at runtime:

```powershell
# System properties
mvn test "-Dgroups=demoshop" "-Ddemoshop.username=you@example.com" "-Ddemoshop.password=your-pass"

# Or env vars (Config maps DEMOSHOP_USERNAME → demoshop.username)
$env:DEMOSHOP_USERNAME="you@example.com"; $env:DEMOSHOP_PASSWORD="your-pass"; mvn test "-Dgroups=demoshop"
```

For persistent local override without committing anything: create `src/main/resources/application-local.properties` and add the two keys there — the file is gitignored.

### Jira Xray Cloud — result sync driven by a datasheet column

The [XrayReportListener](src/test/java/com/hi/api/reporting/XrayReportListener.java)
captures a reserved column called **`jira_xray_id`** from every data-driven row
and POSTs a single batched result import to
[Xray Cloud's `/api/v2/import/execution`](https://docs.getxray.app/display/XRAYCLOUD/Import+Execution+Results+-+REST)
at the end of the suite.

**Two ways to associate a test with a Jira ticket:**

1. **Datasheet column `jira_xray_id`** — one row = one Jira ticket. Best for
   data-driven tests where each row is a different case.

   ```
   jira_xray_id,accountId,...
   PROJ-101,acct-42,...
   PROJ-102,acct-99,...
   ```

2. **`@XrayTest("PROJ-101")` annotation** — one method = one Jira ticket.
   For tests that don't take a row parameter (smoke, single-shot integration,
   unit-style).

   ```java
   @Test(groups = {"smoke"})
   @XrayTest("PROJ-101")
   public void my_single_shot_test() { ... }
   ```

Live example in
[PostsSmokeTests.get_singlePost_matchesPojoAndSchema()](src/test/java/com/hi/api/tests/PostsSmokeTests.java)
— replace `PROJ-DEMO-1` with your real Jira Xray test key.

**Precedence when both are present:**
1. Row column `jira_xray_id` (non-blank) — always wins
2. `@XrayTest(value)` on the method — fallback
3. Neither — no sync (silent)

When each test runs, TestNG's `IInvokedMethodListener.beforeInvocation` applies
that rule to pick a Jira ticket, stashes it on a ThreadLocal, and
`afterInvocation` records `{testKey, status: PASSED|FAILED|SKIPPED, comment:
<failure message or null>, durationMs}`. At `ISuiteListener.onFinish` all
results POST in ONE call.

**Configuration** ([application.properties](src/main/resources/application.properties)):

```properties
# Master kill switch -- default OFF so a public-repo run never phones home.
xray.enabled=false
xray.baseUrl=https://xray.cloud.getxray.app
xray.clientId=
xray.clientSecret=
# Optional -- omit and Xray creates a fresh execution per run.
xray.testExecutionKey=
```

**Enable at runtime** (never commit secrets):

```powershell
mvn test `
    "-Dxray.enabled=true" `
    "-Dxray.clientId=<uuid>" `
    "-Dxray.clientSecret=<secret>" `
    "-Dxray.testExecutionKey=PROJ-42"
```

Or via env vars: `XRAY_ENABLED=true`, `XRAY_CLIENTID=...`, `XRAY_CLIENTSECRET=...`.

**Test status mapping** ([XrayResult.Status](src/main/java/com/hi/api/xray/XrayResult.java)):

| TestNG outcome | Xray status |
|---|---|
| SUCCESS | `PASSED` |
| FAILURE | `FAILED` |
| SKIP | `SKIPPED` |
| other | `ABORTED` |

Failure `throwable.getMessage()` (truncated at 2000 chars) is attached as the
`comment` on the Xray test result.

**Failure semantics** — [XrayClient](src/main/java/com/hi/api/xray/XrayClient.java)
NEVER throws. Any auth / HTTP / network failure is logged to stdout with an
`[XrayClient]` prefix and swallowed. Xray outages must not fail the local
automation suite.

**When it doesn't sync:**
- Test has neither a row `jira_xray_id` nor an `@XrayTest(...)` annotation
- The row's `jira_xray_id` cell is blank AND no annotation on the method
- `xray.enabled=false` (default)
- `xray.clientId` / `xray.clientSecret` is missing

All four cases print a clear `[XrayClient] skipped -- <reason>` line so it's
obvious why a run didn't publish.

### Database queries — plain JDBC via `Db`

[Db](src/main/java/com/hi/api/db/Db.java) is a thin, zero-abstraction plain-JDBC
wrapper for the four operations tests actually reach for. Every call opens a
fresh `DriverManager.getConnection(...)` and closes it via try-with-resources
— no connection pool, no ORM.

**Static shortcuts** — use when the whole suite talks to one DB (reads
`db.url` / `db.user` / `db.password` / `db.driver` from Config):

```java
Map<String,Object> row  = Db.queryOne("SELECT * FROM users WHERE id = ?", 42);
List<Map<String,Object>> rows = Db.queryAll("SELECT * FROM posts WHERE user_id = ?", 1);
int updated = Db.execute("UPDATE users SET active = ? WHERE id = ?", true, 42);
boolean has = Db.exists("SELECT 1 FROM users WHERE email = ?", "a@x.com");
```

**Explicit-connection escape hatch** — bypass Config entirely (handy for a
secondary DB or self-contained tests):

```java
Db legacy = Db.using("jdbc:postgresql://legacy:5432/warehouse", "ro", "");
List<Map<String,Object>> refs = legacy.queryAllRows("SELECT ...");
```

**Config keys** ([application.properties](src/main/resources/application.properties)) —
leave blank in the committed file; override at runtime:

```properties
db.url=jdbc:postgresql://localhost:5432/mydb
db.user=myuser
db.password=mypass
db.driver=org.postgresql.Driver
```

```powershell
mvn test "-Dgroups=db" `
    "-Ddb.url=jdbc:postgresql://localhost:5432/mydb" `
    "-Ddb.user=myuser" `
    "-Ddb.password=mypass"
```

Or via env vars: `DB_URL` / `DB_USER` / `DB_PASSWORD` (Config's dots-become-underscores rule).

**Two shipping tests:**

| Test | What it proves | When it runs |
|---|---|---|
| [H2QueryTest](src/test/java/com/hi/api/tests/H2QueryTest.java) | Every `Db` operation end-to-end against in-memory H2 in Postgres-compat mode | Always — H2 is a test-scoped dep, no external setup |
| [PostgresQueryTest](src/test/java/com/hi/api/tests/PostgresQueryTest.java) | Live Postgres round-trip (`SELECT 1`, `pg_catalog` parameterized query) | Only when `db.url` is configured — else `@BeforeClass` throws `SkipException` |

**Result-set mapping** — every row comes back as a `LinkedHashMap<String,Object>`
keyed by the ResultSetMetaData column *label* (so `SELECT foo AS bar` yields
a `bar` key). Values are JDBC-native types (Integer, Long, String, Timestamp,
etc.) — cast at the call site.

### Expected-column convention

Every data-driven row can carry an `expected` column that packs multiple
per-row expectations into ONE string:

```
key1:value1;key2:value2;key3:value3
```

Rules:
- Semicolon separates pairs; the FIRST colon in each pair splits key from value
- Values may contain colons (e.g. URLs `https://x`), but MUST NOT contain semicolons
- Leading / trailing whitespace on keys and values is trimmed

CSV example ([login_credentials.csv](src/test/resources/testdata/login_credentials.csv)):
```
username,password,expected
testuser1@example.com,fake-password-1,domain:example.com;pwLength:15;isEmail:true
testuser2@example.com,fake-password-2,domain:example.com;pwLength:15;isEmail:true
```

JSON example ([posts.json](src/test/resources/testdata/posts.json)):
```json
{ "rows": [
    { "title": "...", "expected": "statusCode:201;titleLenMin:5" }
]}
```

**Read it from a test** — the base class ships an `expected(row)` helper that
returns an [Expected](src/main/java/com/hi/api/data/Expected.java) wrapper
with typed getters:

```java
Expected exp = expected(row);           // parses row.get("expected")

int statusCode = exp.getInt("statusCode", 201);   // default when key missing
softAssert.assertEquals(res.statusCode(), statusCode, "statusCode");

if (exp.has("titleLenMin")) {
    softAssert.assertTrue(title.length() >= exp.getInt("titleLenMin"),
            "title length >= " + exp.get("titleLenMin"));
}
```

Getter surface: `get(k)`, `get(k, fallback)`, `getInt(k)`, `getInt(k, fallback)`,
`getLong(k)`, `getBool(k)`, `has(k)`, `keys()`, `size()`, `asMap()`.
Typed getters without a fallback fail fast on missing key or unparseable value.

Wired into [PostsCsvDrivenTest](src/test/java/com/hi/api/tests/PostsCsvDrivenTest.java),
[PostsJsonDrivenTest](src/test/java/com/hi/api/tests/PostsJsonDrivenTest.java),
and [ParameterizedLoginTest](src/test/java/com/hi/api/tests/ParameterizedLoginTest.java) —
each demonstrates the `expected` column driving real assertions.

### Data-driven tests — CSV + JSON

Modernized replacement for the reference project's OpenCSV `@DataProvider(name="csvData")` in `BaseClass`.

**Data sources** (`com.hi.api.data.CsvDataSource` / `JsonDataSource`) — direct programmatic use:

```java
// Every row as headers-as-keys map:
List<Map<String,String>> rows = CsvDataSource.rows("testdata/posts.csv");

// Rows 1..3, header excluded, 1-based inclusive (reference-compat range):
List<Map<String,String>> subset = CsvDataSource.rows("testdata/posts.csv", 1, 3);

// Rows as typed POJOs (Jackson field names must match headers/JSON keys):
List<PostCase> cases = CsvDataSource.rows("testdata/posts.csv", PostCase.class);

// JSON: accepts a top-level array or {"rows": [...]}:
List<Map<String,String>> jrows = JsonDataSource.rows("testdata/posts.json");
List<Map<String,String>> jsub  = JsonDataSource.rows("testdata/posts.json", "rows", 1, 3);
List<PostCase> jcases = JsonDataSource.rows("testdata/posts.json", "rows", PostCase.class, 1, Integer.MAX_VALUE);
```

**TestNG hookup** (`com.hi.api.data.DataProviders`) — reference by class + name:

```java
@Test(dataProvider = "csvData",  dataProviderClass = DataProviders.class)
public void t(Map<String,String> row) { ... }

@Test(dataProvider = "jsonData", dataProviderClass = DataProviders.class)
public void t(Map<String,String> row) { ... }
```

Configure the data file per `<test>` block in `testng.xml`:

```xml
<test name="CsvDriven">
    <parameter name="dataFile"     value="testdata/posts.csv"/>
    <parameter name="dataStartRow" value="1"/>
    <parameter name="dataEndRow"   value="3"/>
    <groups><run><include name="csv"/></run></groups>
    <classes><class name="com.hi.api.tests.data.PostsCsvDrivenTest"/></classes>
</test>

<test name="JsonDriven">
    <parameter name="dataFile"     value="testdata/posts.json"/>
    <parameter name="dataArrayKey" value="rows"/>
    <groups><run><include name="json"/></run></groups>
    <classes><class name="com.hi.api.tests.data.PostsJsonDrivenTest"/></classes>
</test>
```

Or override on the CLI: `mvn test "-DdataFile=testdata/big.csv" "-DdataStartRow=50" "-DdataEndRow=100"`.

### `AuthUtilities`

```java
String bearer  = AuthUtilities.bearer("token123");
String basic   = AuthUtilities.basic("user", "pass");
String oauth   = AuthUtilities.oauth2ClientCredentialsToken();     // reads Config, caches in-process
Map<String,String> headers = AuthUtilities.authHeaderMap();        // wired into BaseApiTest globally
```

## Running

> For the converter workflow (generate → verify → run the imported suites),
> see [Quick start — commands](#quick-start--commands) at the top. This
> section covers the generic framework knobs, which apply to both the
> imported suites and hand-written tests.

```powershell
# Default: env=qa, both reports generated
mvn test

# Filter by group
mvn test "-Dgroups=smoke"

# Skip a report
mvn test "-Dreport.extent.enabled=false"

# Higher retry ceiling for flaky endpoints
mvn test "-Dretry.maxCount=4"

# Serve Allure report interactively
mvn allure:serve
```

Expected artifacts after a successful run:

```
target/allure-results/               <- Allure raw JSON
target/allure-report/                <- Allure HTML (after `allure:report`)
allure-history/                      <- Allure history JSON (after `allure_history.py save`; CI cache)
target/surefire-reports/             <- native TestNG XML
extent-reports/<timestamp>-Extent.html  <- ExtentReports HTML
logs/<TestClass>__<method>.log       <- one .log per test method, overwritten
                                         each run (contains req/resp bodies)
logs/<TestClass>.log                 <- class-level banner log (reference-compat)
```

## Per-test-case logs (overwritten each run)

Every test method produces a self-contained log at `logs/<TestClass>__<method>.log`
via the [TestCaseLogListener](src/test/java/com/hi/api/reporting/TestCaseLogListener.java).
Each file contains:

- **Header**: class + method, status (`PASSED` / `FAILED` / `SKIPPED`),
  start time, duration, groups
- **Every HTTP exchange** captured by `RestAssuredRecordingFilter`: method + URL,
  status + response time, request body (pretty-printed if JSON), response body
- **Footer**: failure stack / skip reason if applicable

Files are opened with truncate semantics — the second run of the same test
**overwrites** the first, so `logs/` always reflects the LATEST run. Sample
excerpt from `logs/AuthTests__bearer_positive_validToken.log`:

```
================================================================================
TEST      : AuthTests.bearer_positive_validToken
STATUS    : PASSED
STARTED   : 2026-07-16T17:01:05.444
DURATION  : 356 ms
GROUPS    : auth, bearer
================================================================================

[1] GET https://httpbin.org/bearer
     status=200  responseTime=295ms

--------------------------------------------------------------------------------
REQUEST BODY
--------------------------------------------------------------------------------
(no body)

--------------------------------------------------------------------------------
RESPONSE BODY
--------------------------------------------------------------------------------
{
    "authenticated": true,
    "token": "test-bearer-token-123"
}
```

## Fake data — net.datafaker

[FakeData](src/main/java/com/hi/api/data/FakeData.java) wraps `net.datafaker`
for the common patterns tests reach for. Every call yields a fresh value:

```java
String email    = FakeData.email();                 // jane_doe@example.com
String username = FakeData.username();
String title    = FakeData.sentence(6);
String body     = FakeData.paragraphs(2);
int    userId   = FakeData.intBetween(1, 10);
String uuid     = FakeData.uuid();

Map<String,String> row = FakeData.postDataMap();    // ready for mapJsonValues
Map<String,String> row = FakeData.signupDataMap();  // username + email + password

Faker escape = FakeData.faker();                    // full library access
```

**Reproducible runs** — set `-Dfake.seed=<long>` to make the RNG deterministic
(useful when a failure needs to be reproduced exactly):

```powershell
mvn test "-Dgroups=faker" "-Dfake.seed=42"
```

[FakerPostsTest](src/test/java/com/hi/api/tests/FakerPostsTest.java) shows the
end-to-end pipeline: `FakeData.postDataMap()` → `RestUtilities.mapJsonValues()`
(auto JSON-escapes string values) → POST → assert the server echoed the
generated values back.

## Tag-based test execution

Every `@Test` method has one or more `groups = {...}` tags. Combine them from
the CLI to slice the suite any way you want.

**Available tags** (as of 2026-07-16):

| Tag | Description |
|---|---|
| `smoke` | Fastest happy-path assertions |
| `regression` | Wider coverage tests |
| `posts` | `/posts` endpoint coverage |
| `auth`, `bearer`, `basic` | httpbin.org auth flows |
| `headers`, `loaders` | Header file-loader tests |
| `parameterized` | CSV-driven parameterization demo |
| `csv`, `json` | Data-driven format subgroups |
| `data-driven` | Any data-driven test |
| `demoshop`, `login` | Tricentis DemoWebshop live login |
| `faker` | Faker-generated data |
| `jsonpath` | JsonPath depth-coverage demos |
| `unit` | Pure unit / non-network tests |
| `external` (n/a yet, reserved) | Tests hitting external services |

**Usage:**

```powershell
# Include a single tag
mvn test "-Dgroups=smoke"

# Union -- runs anything matching ANY listed tag
mvn test "-Dgroups=smoke,faker"

# Exclude specific tags from the default suite
mvn test "-DexcludedGroups=demoshop,auth"

# Combine include + exclude
mvn test "-Dgroups=regression" "-DexcludedGroups=auth"
```

**Alternate suite file** — [testng-tags.xml](src/test/resources/testng-tags.xml)
is a flat one-block suite that lists every test class with no per-`<test>`
group filter. Use it when the default `testng.xml`'s per-block filtering
gets in your way:

```powershell
mvn test "-DsuiteXmlFile=src/test/resources/testng-tags.xml" "-Dgroups=jsonpath"
```

## Multiple TestNG suites

The framework supports three progressively richer ways to organise suites.
Pick the one that matches how your team wants to slice test runs.

### 1. Multiple `<test>` blocks in one suite file (default)

Every `<test name="...">` inside a single `testng.xml` is an independent
"sub-suite" -- its own `<groups>` filter, its own `<parameter>` values, its own
class list. The default [testng.xml](src/test/resources/testng.xml) already
uses this pattern: Smoke, Regression, CsvDriven, ParameterizedLogin, Auth,
DemoWebshopLogin, HeadersLoaders, FakerPosts, JsonPathDemo, JsonDriven — all
run back-to-back in one `mvn test`.

Trade-off: everything lives in one file — great for a small project, gets
crowded past ~15 blocks.

### 2. Multiple suite files, one per Maven invocation

Point Maven at a specific alternate suite via the `suiteXmlFile` property.
Three suites ship:

| File | Use when |
|---|---|
| [testng.xml](src/test/resources/testng.xml) | Default -- full run |
| [testng-smoke.xml](src/test/resources/testng-smoke.xml) | Fastest happy path only |
| [testng-tags.xml](src/test/resources/testng-tags.xml) | Flat suite for `-Dgroups=` filtering across all classes |

```powershell
# Focused smoke run -- 5 tests, ~8s
mvn test "-DsuiteXmlFile=src/test/resources/testng-smoke.xml"

# Group filter against the flat tags suite
mvn test "-DsuiteXmlFile=src/test/resources/testng-tags.xml" "-Dgroups=jsonpath"
```

Add your own: drop a new `testng-<name>.xml` under `src/test/resources/`, no
pom edit needed.

### 3. Parent suite pulls in multiple child suites in ONE invocation

TestNG's native `<suite-files>` element lets one parent suite include others.
Each child keeps its own listeners / parameters / groups; results are combined.

[testng-all.xml](src/test/resources/testng-all.xml) demonstrates:

```xml
<suite name="ApiAutomationSuite-All">
    <suite-files>
        <suite-file path="./testng-smoke.xml"/>
        <suite-file path="./testng.xml"/>
    </suite-files>
</suite>
```

Runs both children sequentially:

```powershell
mvn test "-DsuiteXmlFile=src/test/resources/testng-all.xml"
# === Suite 'ApiAutomationSuite-Smoke' -- passed=5 ===
# === Suite 'ApiAutomationSuite'       -- passed=42, skipped=3 ===
# 50 tests total, 3 skipped
```

Use this when a nightly / CI run needs to layer multiple suite files without
editing the pom or writing a shell script.

## Migrating from the reference `BatchPaymentAutomation`

Point calling code at `com.hi.api.rest.utilities.RestUtilities` instead of `com.visa.b2b.connect.rest.utilities.RestUtilities`. Method names, argument order, placeholder rules, log file format are identical. `mapJsonValues` picks up strict mode via the new 3-arg overload — pass `strict=true` when you migrate to catch dataMap bugs the reference silently hid.

For domain-specific test data, drop your JSON templates in `src/main/resources/templates/` and feed the dataMap from Excel / CSV / DB / Vault. The reference used Apache POI for Excel; nothing in the utility layer is Excel-specific.

python C:/Users/asuseelakamalas/Downloads/JavaRestAssuredFrameWork-main/tools/ra_converter/ra_converter.py --input tools/ra_converter/input/programaccountregression.xml --clean --output . --package-root com.hi.api --service-name ProgramAccounts --max-name-len 40
mvn test -Dtest="com.hi.api.tests.imported.accountmemberregression.members.ActivateTest" -DfailIfNoTests=false
