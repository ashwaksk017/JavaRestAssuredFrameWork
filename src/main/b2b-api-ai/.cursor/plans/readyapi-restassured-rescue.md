---
name: ReadyAPI RestAssured rescue
overview: Finish ReadyAPI cases the converter left TODO/PARTIAL or likely-wrong by fixing emit or helpers, never by hand-editing imported tests.
todos:
  - id: discover
    content: Map ReadyAPI case(s) via _audit case_to_method_mapping and list_gaps.py
  - id: classify
    content: Classify each gap (emit vs helper vs intentional skip) using confused-signals.md
  - id: implement
    content: Patch converter/helpers + add converter unit tests; do not edit tests/imported
  - id: reconvert
    content: Reconvert only if emit changed; restore narrowed smoke XMLs
  - id: verify
    content: Run converter tests and compile; optional single TestNG class
isProject: true
---

# ReadyAPI → Rest Assured rescue

Follow `.cursor/skills/readyapi-restassured-migration/SKILL.md`. Fill names below, then implement.

## Goal

Rescue ReadyAPI case(s): `[paste case names / B2B ids]`

Suite XML: `tools/ra_converter/input/[file].xml`

## Classification

| Case | Signal (TODO / HIGH / runtime) | Fix site (emit / helper / skip) |
|---|---|---|
| | | |

## Non-negotiables

- No hand-edits under `tests/imported/` or `support/<suite>/`
- Token setup + after-test cleanup only
- Unique `--service-name`; do not delete `support/scenario/` or `fluent_catalog.json`
- No SF credential invention / HWS Selenium
- Keep SMB smoke **7816-only**
- Do not regress identity pack, `putExtracted` hiltonmemberid, POST `/remove`, token skip-regen, `jsonAbsent`, member-enroll Email overlay

## Verify

```bash
python tools/ra_converter/test_converter_fixes.py
python tools/ra_converter/test_cross_case_contracts.py
mvn -q -DskipTests compile
```
