# Do not regress

Keep these behaviors when touching converter or helpers.

| Contract | Where it lives |
|---|---|
| Identity pack: owner vs member emails/usernames stay distinct; regen **once** per attempt | `CtxFields` / converter identity overlay |
| Member-enroll CSV/template overlay `Properties.Email` (7816) | converter + `ImportedScenario` / `CtxFields` |
| Groovy `hiltonmemberid` extracts use `putExtracted` | `groovy_translator.py` |
| Same-field Groovy `setPropertyValue` is last-write-wins (replicate ReadyAPI; do not reorder JSON vs location-header vs JDBC) | `groovy_translator.py` `_should_emit_setproperty` |
| DataGenInput space-concat `name`/`name2` (shared generator vars + `def a = b` aliases) | `groovy_translator.py` `_space_concat_overlay_lines` |
| `jsonEquals`: path that extracts a different value does not pass via a sibling JSON scalar | `ResponseAsserts.valueInResponse` |
| ReadyAPI JsonPath typos: missing `contactInfo.Domain` reads `websiteDomain`; missing root `status` reads `memberStatus` (not `accountStatus`) | `RestUtilities.safeJsonExtract` |
| Path `.../remove` → POST with ReadyAPI body (not DELETE) | `ra_converter._infer_http_method` |
| Token request and `${#Project#...}` do **not** `.regenIdentity()` | converter REST emit + `SetupHelper.flow_A` |
| Existence Match `content=false` → `jsonAbsent`; `true` → `jsonExists` (per assertion) | `ra_converter.py` + `test_cross_case_contracts.py` |
| OTP numeric values padded to 6 digits | `Db.formatColumn` |
| `unsafeSqlReason` `'null'` gate only when `isNullFallbackTripped()` | `Db.java` |
| Cleanup after each test, not a `@Test` | `SuiteCleanup.afterEachTest` |
| Token fetch in setup, not a `@Test` | `AuthHelper` / `SetupHelper.flow_A` |
| PerMethod CSV keeps multiline JSON cells | `PerMethodCsvDataProvider` |
| `--clean` does not delete `support/scenario/` or `fluent_catalog.json` | `ra_converter.py` |
| Credentials render only from `program_configuration.json`; every sink (Extent, per-test logs, console, Allure) redacts via `Secrets.redact` | `com.ak.api.security.Secrets` + `SecretRedactingRewritePolicy` |
| `AllureRestAssured` must NOT be re-wired in `BaseApiTest` -- it attaches bodies verbatim; use `AllureRedactingFilter` | `BaseApiTest.bootstrapRestAssured` |
| log4j2 loggers reference the `Console` Rewrite appender, never `ConsoleRaw` | `src/test/resources/log4j2.xml` |
| Fluent phase names come from `phase_vocabulary.canonical_name(verb, FULL path)` -- never from the first two path segments, or a sub-resource gets swallowed by its parent | `tools/ra_converter/phase_vocabulary.py` |
| `fluent_catalog.json` carries `vocabularyVersion`; a mismatch discards stale `phases` (keeps `clientMethods`). Do not remove the stamp or old counter-suffixed names re-seed | `fluent_scenario.load_fluent_catalog` |
| Role/partner aliases (`enrollOwner`, `createH4LAccount`) exist ONLY on the hand-authoring DSL. The converter still emits the neutral phase + CSV-driven role | `com.ak.api.dsl.CustomerOnboarding` |
| Hand-written tests live in `tests/manual/` and must not depend on generated `support/` types | `OnboardingE2ETest` |
| Emitted class-name collisions are checked CASE-INSENSITIVELY. A case-only clash silently clobbers one class on Windows/macOS (file path is derived from the class name) | `Emitter.emit_test_class_per_suite` |
| `Db.unsafeSqlReason` refuses blank, quote-only, unbalanced-quote and unbalanced-paren SQL. Doubled quotes (`'it''s'`) are SQL escapes and must still PASS | `Db.java` + `DbSqlGuardTest` |
| `ScenarioContext` resolves ctx by DECLARED ordered aliases, not the heuristic walk. `guestId` must never resolve from `memberGuestID` (or vice versa) | `ScenarioContext` + `ScenarioContextTest` |
| `ScenarioContext` wraps the LIVE ctx map (never a copy) so generated Support classes and typed callers see each other's writes | `ScenarioContext.of` |
| `ctxGetRaw` consults `ScenarioContext.resolveDeclared` BEFORE the trailing-field heuristic, and only when the exact key is ABSENT. A present-but-empty key must stay empty -- a blank id is often the point of a negative test | `ImportedScenario.ctxGetRaw` |
| `resolveDeclared` must never call `ctxGet` (infinite recursion) and returns "" for undeclared keys | `ScenarioContext.resolveDeclared` |
| Numbered ctx variants (`guestId1`, `guestId2`, `accountID2`) are SECOND entities -- never fold them into the base field | `ScenarioContext` alias lists |
| ReadyAPI Delay steps keep their FULL wall-clock wait. Converting a delay into a poll on the next REST step was tried and REVERTED: the racy step is often 2+ steps later, so the poll deletes the wait that was protecting it | `_render_step` DelayStep branch |
| `rest.pollExpectedJsonMs` defaults to 0 (off). It only ADDS a bounded re-read; it never shortens a delay | `RestStep.maybePollUntilExpectedJson` |
| A run with failing suites marks `fluent_catalog.json` INCOMPLETE so the next convert rebuilds phases. Never save a catalog from a partial run | `_mark_catalog_incomplete` + `load_fluent_catalog` |
| An assertion type that cannot convert must emit a TODO, never a check that silently asserts nothing | `_render_assertion` + `test_assertion_coverage.py` |
| Emitted-tree invariants (name/class match, brace balance, no leaked placeholders, no leftover TODO) | `tools/check_generated_output.py` |
| `salesforceAuth_fillJwtBearerForm_replacesPlaceholders` FAILS (never skips) on an unset `sf_config.assertion` -- a missing credential must be loud | `FrameworkPartialGapsTest` |

**Verify everything with one command:** `python tools/verify_all.py` (fast, ~12s) or
`python tools/verify_all.py --full` (adds Java compile + TestNG guards, ~5m).
Run `--full` before any reconvert or commit.

Converter tests that lock these: `tools/ra_converter/test_converter_fixes.py`, `tools/ra_converter/test_cross_case_contracts.py`.
Vocabulary is locked by `python tools/ra_converter/phase_vocabulary.py` (self-test).
SQL guards are locked by `mvn test "-DsuiteXmlFile=src/test/resources/testng-guards.xml"`.
Redaction is locked by `com.ak.api.tests.security.SecretsRedactionTest` (`mvn test "-DsuiteXmlFile=src/test/resources/testng-security.xml"`).
