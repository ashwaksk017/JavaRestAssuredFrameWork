# Plan: ReadyAPI → Rest Assured rescue

Use this in **Cursor Plan mode** when the converter is confused or likely wrong (TODO/STUB/PARTIAL, preflight HIGH, untranslated Groovy/JDBC, verb/polarity/identity mismatches). Do not implement until the plan is agreed.

## Goal

Finish ReadyAPI case(s) `[names / Jira ids]` as Rest Assured TestNG **without hand-editing imported Java**.

## Non-negotiables

- Emit via `tools/ra_converter/` + helpers only
- Token = setup; cleanup = `SuiteCleanup.afterEachTest`
- Unique `--service-name` per XML; `--clean` does not wipe `support/scenario/` or `fluent_catalog.json`
- No invented Salesforce credentials / HWS Selenium
- Do not expand smoke XMLs (SMB 7816-only)
- Do not regress identity pack, `putExtracted` hiltonmemberid, ReadyAPI last-write-wins, POST `/remove`, token skip-regen, `jsonAbsent` polarity, member-enroll Email overlay

## Discover

- Source XML: `tools/ra_converter/input/`
- Audit: `_audit/<suite>/summary.md`, `preflight.md`, `unmapped.csv`, `case_to_method_mapping.csv`
- Run: `python .cursor/skills/readyapi-restassured-migration/scripts/list_gaps.py --suite <suite>`
- Skill: `.cursor/skills/readyapi-restassured-migration/SKILL.md`

## Approach

For each gap: **emit pattern** vs **runtime helper** vs **intentional skip** (Manual/JMS/HWS/unsafe JDBC mutation).

Files likely in play (trim to what this rescue needs):

- `tools/ra_converter/ra_converter.py`
- `tools/ra_converter/groovy_translator.py`
- `tools/ra_converter/test_converter_fixes.py` / `test_cross_case_contracts.py`
- Helpers: `ImportedScenario`, `CtxFields`, `ResponseAsserts`, `AuthHelper`, `Db`

## Reconvert (only after emit change)

```bash
python tools/ra_converter/ra_converter.py --input tools/ra_converter/input --output . --package-root com.ak.api --clean --max-name-len 40
```

Or one XML with a unique `--service-name`. Snapshot `Suites/*_Smoke.xml` first; restore if rewritten.

## Verify

- Converter unit tests
- `mvn -q -DskipTests compile`
- Optional: the rescued TestNG class only (do not unwrap smoke)

## Out of scope

- New hand-written suites belong under `tests/manual/` (`CreateTestCase.md`)
- Do not reconvert “to see what happens” without an emit change
