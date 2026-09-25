---
name: readyapi-restassured-migration
description: Rescues ReadyAPI (SoapUI) to Rest Assured migrations when ra_converter emits TODO/STUB/PARTIAL, preflight HIGH findings, untranslated Groovy/JDBC, or likely-wrong HTTP/assert polarity. Use when the user mentions ReadyAPI, SoapUI, converter confused, reconvert, unmapped.csv, jsonAbsent, identity pack, or a generated imported test that should not be hand-edited.
---

# ReadyAPI → Rest Assured rescue

The converter is the source of truth for imported suites. This skill is the **fallback** when emit is incomplete or likely wrong. Do not patch one generated test and stop.

## When to use

- `_audit/<suite>/` shows TODO / PARTIAL / STUB, `unmapped.csv` rows, or preflight HIGH
- Emitted Java has `// TODO` / `SkipException` / leftover `${...}`
- Runtime disagrees with ReadyAPI (wrong verb, existence polarity, shared identity, 405 on `/remove`)
- User asks to migrate a case the converter “cannot handle”

If the change spans Groovy **and** assertions **and** HTTP inference, switch to **Plan mode** and fill [plan-template.md](plan-template.md).

## Converter auto-call

When `tools/ra_converter/cursor_agent.json` has `"enabled": true` and an `apiKey` (or `CURSOR_API_KEY`), a convert that records TODO/STUB/PARTIAL or HIGH preflight writes a **conversion report**, then calls **one** local Cursor agent to fix emit/helpers, then writes an **after-fix report**. Memory in `tools/ra_converter/cursor_assist_memory.md` is prepended on the next convert.

- Before: `_audit/cursor_assist_report.md`
- Index: `_audit/cursor_assist.md`
- After: `_audit/cursor_assist_after.md`

Copy `cursor_agent.json.example`. Do not commit the live json. Force with `--cursor-assist`; disable with `--no-cursor-assist`. Coverage `FULL` is not reviewed by this pass.

## Hard rules

1. Do **not** hand-edit `tests/imported/`, `support/<suite>/`, or that suite’s CSVs/templates to finish a case.
2. Fix **emit** (`ra_converter.py`, `groovy_translator.py`, `fluent_scenario.py`) or **runtime helpers**, then reconvert if emit changed.
3. Token = setup, not `@Test`. Cleanup = `SuiteCleanup.afterEachTest`, not `@Test`.
4. Do not invent Salesforce credentials or drive HWS with Selenium. Stub + skip with a ReadyAPI citation.
5. Do not expand author-narrowed smoke XMLs. Snapshot `Suites/*_Smoke.xml` before `--clean`.
6. Do not regress items in [do-not-regress.md](do-not-regress.md).
7. Replicate ReadyAPI. Same-field Groovy `setPropertyValue` is last-write-wins. DataGenInput space-concat `name`/`name2` must share generator vars. Do not invent a “better” member/guest id or assertion polarity than the XML.

## Workflow

Copy and track:

```
Rescue:
- [ ] 1. Locate ReadyAPI case + landing Java/CSV
- [ ] 2. Classify gap (see confused-signals.md)
- [ ] 3. Choose emit vs helper vs skip
- [ ] 4. Unit-test the converter/helper change
- [ ] 5. Reconvert only that suite (or all input XMLs if fluent votes need it)
- [ ] 6. Restore smoke XML if rewritten
- [ ] 7. Compile + run converter tests + the rescued case
```

**Step 1 — Locate**

```bash
python .cursor/skills/readyapi-restassured-migration/scripts/list_gaps.py
python .cursor/skills/readyapi-restassured-migration/scripts/list_gaps.py --suite programaccountregression
```

Landing: `_audit/<suite>/case_to_method_mapping.csv`. Source XML: `tools/ra_converter/input/<suite>.xml`. Flow: `_flows/<suite>/cases/`.

**Step 2 — Classify** — [confused-signals.md](confused-signals.md)

**Step 3 — Fix site**

| Gap | Fix in |
|---|---|
| Wrong Java for every similar step | Emitter (`ra_converter.py` / `groovy_translator.py`) |
| Wrong at runtime for all suites (OTP pad, SQL gate, token refresh) | Helper (`Db`, `CtxFields`, `RestStep`, `ResponseAsserts`) |
| ReadyAPI Manual / JMS / HWS UI / unknown Groovy | TODO + `SkipException` + Allure note citing the SoapUI step |
| Hand-written new API (not in XML) | `CreateTestCase.md` path under `tests/manual/` |

**Step 4 — Tests**

Add a focused test next to `tools/ra_converter/test_converter_fixes.py` or `test_cross_case_contracts.py`. Run those files plus any new Java test.

**Step 5 — Reconvert** (emit changes only)

Single XML (unique client name):

```bash
python tools/ra_converter/ra_converter.py --input tools/ra_converter/input/<file>.xml --output . --package-root com.hi.api --service-name <UniqueClient> --clean --max-name-len 40
```

All XMLs (fluent votes across suites; `--service-name` ignored):

```bash
python tools/ra_converter/ra_converter.py --input tools/ra_converter/input --output . --package-root com.hi.api --clean --max-name-len 40
```

`--clean` must not delete `src/main/java/com/hi/api/support/scenario/` or `tools/ra_converter/fluent_catalog.json`.

Known clients: `programaccountregression` → `ProgramAccount`; `accountmemberregression` → `ProgramAccounts`; `smbtohwsconsumerregressione2e` → `SmbToHwsConsumer`.

**Step 6 — Smoke** — restore `Suites/Smbtohwsconsumerregressione2e_Smoke.xml` to **B2B7816 only** if the emitter dumped every class. Keep any author 6801-only programaccount smoke.

**Step 7 — Verify** — `mvn -q -DskipTests compile` and converter unit tests. Run the rescued TestNG class only if the user wants a live API run.

## Additional resources

- [confused-signals.md](confused-signals.md) — how to tell the converter is guessing
- [do-not-regress.md](do-not-regress.md) — landed contracts
- [plan-template.md](plan-template.md) — Cursor Plan mode outline
