# Cursor assist memory

Lessons from converter-assist runs. The next convert prepends this file to the fix-agent prompt so the same ReadyAPI-vs-emit mistakes are less likely to repeat. Keep bullets short. No secrets.

## Seeded (B2B-6604 / converter contracts)

- ReadyAPI same-field `setPropertyValue` is last-write-wins. Do not emit location-header `split('/')[0]` after JSON `resp_obj.memberId`.
- DataGenInput `name`/`name2` space-concat must share generator vars (`def generatedUsername2 = generatedUser2`). `CtxFields.generateStandard` alone emits one username each and TA `companyName` will not match create-account name.
- `jsonEquals`: if the historic path extracts a different non-empty value, fail. Do not pass `accountStatus=active` because `memberStatus=active` is in the body.
- Existence Match `content=false` is `jsonAbsent`, not `jsonExists`. Path `.../remove` is POST.
- Do not hand-edit `tests/imported/` or `support/<suite>/` to finish a case.

- CleanupDB `def websiteDomains = "'www.example.com'"` (semicolon-terminated) + DELETE concat: strip trailing `;` on def RHS and map literal/`Properties.getPropertyValue` quoted SQL fragments via `_local_java`.
- `Update_second_Account_info` `sql.execute(sql_addressline1)`: `context.expand('${step#Response#...}').replace("'", "''")` must populate `_local_java` (backref-quoted expand regex) so GString SQL vars compose to `Db.execute`.
- Standalone JDBC `${step#Response#$['field']}`: route through `soapui_body_to_placeholders`; auto-extract scan must include `JdbcStep.query` or OTP lookups stay unresolved.
- Preflight `untranslated-cross-tc-ref` / `jdbc-mutation-skip`: flag only when translation still emits `${...}` or `SkipException`, not on every source XML cross-ref or commented `sql.execute`.

## 2026-09-04 14:20 UTC
- suites: programaccountregression
- gaps: 243
- agent status: finished  run: run-cabcf474-a9a4-4e75-98d3-031b389167a0
- changed (watch paths):
### git status (watch paths)

```
fatal: not a git repository (or any of the parent directories): .git
```

### git diff --stat

```
warning: Limiting comparison with pathspecs is only supported if both paths are directories.
usage: git diff --no-index [<options>] <path> <path> [<pathspec>...]

Diff output format options
    -p, --patch           generate patch
    -s, --no-patch        suppress diff output
    -u                    generate patch
    -U, --unified[=<n>]   generate diffs with <n> lines context
    -W, --[no-]function-context
                          generate diffs with <n> lines context
    --raw                 generate the diff in raw format
    --patch-with-raw      synonym for '-p --raw'
    --patch-with-stat     synonym for '-p --stat'
    --numstat             machine friendly --stat
    --shortstat           output only the last line of --stat
    -X, --dirstat[=<param1>,<param2>...]
                          output the distribution of relative amount of changes for each sub-directory
    --cumulative          synonym for --dirstat=cumulative
    --dirstat-by-file[=<param1>,<param2>...]
                          synonym for --dirstat=files,<param1>,<param2>...
    --check               warn if changes introduce conflict markers or whitespace errors
    --summary             condensed summary such as creations, renames and mode changes
    --name-only           show only names of changed files
    --name-status         show only names and status of cha
- lesson:
## After-fix summary
- **files changed:** `tools/ra_converter/groovy_translator.py`, `tools/ra_converter/ra_converter.py`, `tools/ra_converter/test_converter_fixes.py`, `tools/ra_converter/cursor_assist_memory.md`
- **reconvert needed:** yes (emit changed; run `programaccountregression` convert to regenerate support classes and clear runtime skips)
- **tests added:** `test_cross_tc_token_header_translates`, `test_groovy_cleanupdb_hardcoded_domain_delete`, `test_groovy_cleanupdb_properties_quoted_sql_literal`, `test_groovy_update_second_account_info_sql_var`, `test_jdbc_step_query_response_ref_to_placeholder`, `test_dbupdate_executeupdate_not_preflight_skip`
- **residual risk:** Reconvert not run here — existing `support/programaccountregression/` still has old `SkipException` emit until you reconvert. Preflight counts should drop sharply (cross-tc ~28→0, jdbc-mutation ~207→only truly untranslatable scripts). `#Project#` keys still need `program_configuration.json` entries for runtime (MEDIUM preflight, unchanged).

## 2026-09-04 15:35 UTC
- suites: programaccountregression
- gaps: 8 (jdbc-mutation-skip / runtime SkipException on Update_second_Account_info)
- `_balanced_arg_call` must scan `_strip_groovy_comments` script — ReadyAPI keeps `//def sql_query` + `//sql.execute(sql_query)` beside live `sql.execute(sql_addressline1)` GString updates; bare matcher treated dead call as untranslated mutation.
- file fixed: `groovy_translator.py` (`_strip_groovy_comments`, `_balanced_arg_call`); test `test_groovy_commented_sql_execute_not_mutation_skip`.
- do-not-regress: commented `//sql.execute(...)` must not emit SkipException when sibling composed locals translate to `Db.execute`.

## 2026-09-04 14:34 UTC
- `Groovy Script for guestId` with `parseText(resp)` but no `setPropertyValue("guestId", resp_obj.guestId...)`: ReadyAPI XML truncated the final line (5 cases: B2B-4878×2, B2B-3503×3). Emit `PropertiesGuestId.guestId` via `safeJsonExtract(enrollRes, "guestId")`; do not STUB.
- Complete guestId scripts (263 others) still use `setproperty_extract` last-write-wins — incomplete fallback must bail when nonempty guestId write exists.
- Do-not-regress: do not emit incomplete fallback when `resp_obj.guestId` setPropertyValue is present (would double-put).

## 2026-09-04 14:38 UTC
- suites: programaccountregression
- gaps: 20
- agent status: finished  run: run-47f434d2-fc27-4be5-ad46-cb4a15254860
- changed (watch paths):
### git status (watch paths)

```
fatal: not a git repository (or any of the parent directories): .git
```

### git diff --stat

```
warning: Limiting comparison with pathspecs is only supported if both paths are directories.
usage: git diff --no-index [<options>] <path> <path> [<pathspec>...]

Diff output format options
    -p, --patch           generate patch
    -s, --no-patch        suppress diff output
    -u                    generate patch
    -U, --unified[=<n>]   generate diffs with <n> lines context
    -W, --[no-]function-context
                          generate diffs with <n> lines context
    --raw                 generate the diff in raw format
    --patch-with-raw      synonym for '-p --raw'
    --patch-with-stat     synonym for '-p --stat'
    --numstat             machine friendly --stat
    --shortstat           output only the last line of --stat
    -X, --dirstat[=<param1>,<param2>...]
                          output the distribution of relative amount of changes for each sub-directory
    --cumulative          synonym for --dirstat=cumulative
    --dirstat-by-file[=<param1>,<param2>...]
                          synonym for --dirstat=files,<param1>,<param2>...
    --check               warn if changes introduce conflict markers or whitespace errors
    --summary             condensed summary such as creations, renames and mode changes
    --name-only           show only names of changed files
    --name-status         show only names and status of cha
- lesson:
## After-fix summary
- **files changed:** `tools/ra_converter/groovy_translator.py`, `tools/ra_converter/test_converter_fixes.py`, `tools/ra_converter/cursor_assist_memory.md`, regenerated `support/programaccountregression/` and audit CSVs via reconvert
- **reconvert needed:** no (already run)
- **tests added:** `test_groovy_incomplete_guestid_script_infers_extract`, `test_groovy_complete_guestid_script_still_uses_setproperty_extract`
- **residual risk:** LOW — inference assumes the missing line was always `resp_obj.guestId` from the enroll step response (matches all 5 truncated XML scripts). `#Project#` DB/config keys still need `program_configuration.json` for runtime JDBC steps (unchanged MEDIUM preflight).

## 2026-09-04 14:39 UTC
- suites: programaccountregression
- gaps: 8
- agent status: finished  run: run-04b50e93-abed-4d1d-aa23-f1b88cd7996d
- changed (watch paths):
### git status (watch paths)

```
fatal: not a git repository (or any of the parent directories): .git
```

### git diff --stat

```
warning: Limiting comparison with pathspecs is only supported if both paths are directories.
usage: git diff --no-index [<options>] <path> <path> [<pathspec>...]

Diff output format options
    -p, --patch           generate patch
    -s, --no-patch        suppress diff output
    -u                    generate patch
    -U, --unified[=<n>]   generate diffs with <n> lines context
    -W, --[no-]function-context
                          generate diffs with <n> lines context
    --raw                 generate the diff in raw format
    --patch-with-raw      synonym for '-p --raw'
    --patch-with-stat     synonym for '-p --stat'
    --numstat             machine friendly --stat
    --shortstat           output only the last line of --stat
    -X, --dirstat[=<param1>,<param2>...]
                          output the distribution of relative amount of changes for each sub-directory
    --cumulative          synonym for --dirstat=cumulative
    --dirstat-by-file[=<param1>,<param2>...]
                          synonym for --dirstat=files,<param1>,<param2>...
    --check               warn if changes introduce conflict markers or whitespace errors
    --summary             condensed summary such as creations, renames and mode changes
    --name-only           show only names of changed files
    --name-status         show only names and status of cha
- lesson:
## After-fix summary
- **files changed:** `tools/ra_converter/groovy_translator.py`, `tools/ra_converter/test_converter_fixes.py`, `tools/ra_converter/cursor_assist_memory.md`, regenerated `support/programaccountregression/` (reconvert)
- **reconvert needed:** no (already run)
- **tests added:** `test_groovy_commented_sql_execute_not_mutation_skip`
- **residual risk:** LOW for these four cases. Tests still need `Db.isConfigured()` and `#Project#` DB keys in config at runtime (MEDIUM preflight for unresolved project refs — unchanged). If DB is not configured, steps log a warn and continue rather than skip the whole test.

## 2026-09-09
- B2B-5269 diff_address `Update_second_Account_info`: `//def sql_addressline1` (and city/state/postal) commented but `sql.execute(sql_addressline1)` live — ReadyAPI try/catch swallows MissingPropertyException; emit LOG.warn per undefined local, not SkipException.
- file fixed: `groovy_translator.py` (`_has_active_groovy_def`, undefined-local branch); test `test_groovy_commented_def_live_sql_execute_not_mutation_skip`.
- do-not-regress: active `def sql_* = ...` that fails composition must still emit SkipException; only skip when no non-comment `def` exists for the bare identifier.

## 2026-09-09 15:51 UTC
- suites: programaccountregression
- gaps: 2
- agent status: finished  run: run-c48c6865-5b9b-4047-95bf-f57456e0b50d
- changed (watch paths):
### git status (watch paths)

```
?? .cursor/skills/readyapi-restassured-migration/
?? src/main/java/com/hi/api/db/Db.java
?? src/main/java/com/hi/api/rest/utilities/AuthHelper.java
?? src/main/java/com/hi/api/rest/utilities/ResponseAsserts.java
?? tools/ra_converter/cursor_assist.py
?? tools/ra_converter/cursor_assist_memory.md
?? tools/ra_converter/fluent_scenario.py
?? tools/ra_converter/groovy_translator.py
?? tools/ra_converter/ra_converter.py
```
- lesson:
## After-fix summary
- **files changed:** `tools/ra_converter/groovy_translator.py`, `tools/ra_converter/test_converter_fixes.py`, `tools/ra_converter/cursor_assist_memory.md`, regenerated `support/programaccountregression/` and audit CSVs via reconvert
- **reconvert needed:** no (already run)
- **tests added:** `test_groovy_commented_def_live_sql_execute_not_mutation_skip`
- **residual risk:** LOW for the two reported gaps. `#Project#` DB keys still need `program_configuration.json` at runtime (MEDIUM preflight, unchanged). Minor JDBC fidelity gap: emitted code may run phone/domain SQL updates that ReadyAPI’s try/catch never reaches. Unrelated pre-existing Java compile errors exist in other support classes (not introduced by this change).
