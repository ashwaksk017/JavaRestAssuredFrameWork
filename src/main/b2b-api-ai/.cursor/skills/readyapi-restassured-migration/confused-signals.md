# Converter confused / likely-wrong signals

Read these **before** editing Java. Sources: `_audit/<suite>/` + emitted `// TODO`.

## Audit files

| File | Treat as confused when |
|---|---|
| `summary.md` | PARTIAL / STUB / TODO > 0 on assertions, Groovy, or step types |
| `preflight.md` / `preflight.csv` | BLOCKER or HIGH (`jdbc-mutation-skip`, `untranslated-cross-tc-ref`, `jdbc-untranslated-soapui-ref`) |
| `unmapped.csv` | Any row: assertion/Groovy not in the recognizer |
| `assertions.csv` / `groovy.csv` / `steps.csv` | `coverage` in TODO, STUB, PARTIAL |
| `runtime_skips.csv` | JDBC mutation / Manual / CallTestCase left as skip |
| emitted Java | `// TODO`, `TODO manual`, leftover `${#`, `SkipException` for a step ReadyAPI actually runs |

`SKIPPED` in the audit means the SoapUI step was disabled. That is author intent, not a converter gap.

## Likely-wrong even when coverage is FULL

These already bit this repo (do not reintroduce):

- **Existence Match** `content=false` emitted as `jsonExists` instead of `jsonAbsent`
- **HTTP verb**: path `.../remove` inferred DELETE (405) — must be POST with body
- **Identity pack**: owner + member enroll share one email (409 / 400) — keep distinct `#Properties_username#` vs member slots; overlay member-enroll `Properties.Email`
- **`putIfAbsent` on `hiltonmemberid`**: owner TOTP pins the key; member JDBC never overwrites — use `putExtracted`
- **Location-header slice vs later `resp_obj.memberId`**: ReadyAPI last `setPropertyValue` wins; emitting `split('/')[0]` after JSON plants the guest id on confirm-validation (403)
- **DataGenInput `name`/`name2` concat**: ReadyAPI joins shared generator words (`generatedUsername2 + " " + generatedUser3`); `generateStandard` alone emits one username each so TA `companyName` never matches create-account name (account stays `limited`, no `hiltonAgencyProfile`)
- **`jsonEquals` wrap fallback**: body-wide scalar search when the historic path is empty is wrap-tolerance; a path that extracted a *different* value (`accountStatus=limited` while `memberStatus=active`) is a fail
- **ReadyAPI JsonPath typos**: assertion name `websiteDomain` with path `$['contactInfo']['Domain']`; name `memberStatus` with path `$['status']`. Runtime `safeJsonExtract` aliases only when the historic path is empty.
- **Still flattened DataGen (not in B2B-6604 two-words smoke)**: concat with extra string literals (`+" "+ "@"`); `name2` as a bare ident that is one word of `name`'s concat (uppercase case); `StopWords` list-pick emitted as `[a-z]{8}`; `businessname = "bn"+generator(...)` left as `generateStandard` username
- **`jsonTreeEquals`**: if the historic path extracts a different tree, leaf search still walks the whole body (same sibling-scalar class as old `jsonEquals`)
- **Token / Project# refs** calling `.regenIdentity()` and consuming the pack before enroll
- **Groovy JDBC** left with `${...}` after mapSqlValues — `unsafeSqlReasonForQuery` should skip, not hit the driver
- **CallTestCase / Manual / JMS / HWS UI** emitted as if they were REST

## Decision: translate vs stub

Translate when the ReadyAPI step is REST, Properties, Delay, PropertyTransfer, or Groovy that already has a translator pattern (JsonSlurper, `setPropertyValue`, `context.expand`, JDBC **select** → `Db.pollUntilStable` / `queryAll`).

Stub (TODO + skip, cite step name) when:

- Salesforce login needs secrets not in `program_configuration.json`
- HWS / Selenium / Manual
- JMS
- JDBC **mutation** Groovy the translator cannot parameterize safely
- Cross-testcase `${#[OtherSuite#OtherCase#tokenId]#GeneratedTokenID}` until token hoist exists for that suite (prefer `AuthHelper` / `flow_A`, not a new `@Test`)

## Compare XML vs emit

1. Open the `<con:testCase>` in `tools/ra_converter/input/<suite>.xml`.
2. Diff step order vs `_flows/<suite>/cases/<case>.md` and the `*Support.java` method.
3. If a REST step is missing or the verb/path/body diverges, fix `_infer_http_method` / template merge / fluent catalog — not the Support class.
