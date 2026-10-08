# CURSOR.md — working on this converter

Read this before changing anything. It is written for an AI assistant and
whoever is driving it, and most of it is about the traps, because the code
itself is readable and the traps are not.

---

## 1. What this is

A converter that turns a **ReadyAPI / SoapUI project XML** into a **Java +
REST Assured + TestNG** test suite, plus the framework those tests run on.

```
tools/ra_converter/input/*.xml        ReadyAPI export (GITIGNORED - vendor data)
            |
            v
tools/ra_converter/ra_converter.py    ~19,800 lines, one pass
            |
            +--> src/main/java/com/hi/api/support/**   GENERATED  (gitignored)
            +--> src/test/java/com/hi/api/tests/**     GENERATED  (gitignored)
            +--> src/main/resources/templates/**       GENERATED  (gitignored)
            +--> src/test/resources/csv/**             GENERATED  (gitignored)
            +--> _audit/**                             GENERATED  (gitignored)
```

Two suites are imported today: `partialgoalregression` ("goal") and
`programaccountregression` ("b2b").

### Run it

```bash
python tools/ra_converter/ra_converter.py \
    --input tools/ra_converter/input \
    --output . \
    --package-root com.hi.api \
    --clean --max-name-len 40 --no-cursor-assist \
    --data-dir <folder containing the .xlsx workbooks>
```

`--data-dir` matters. Without it every `DataSource_*` CSV column is empty
and dozens of tests fail on missing data that is sitting in a spreadsheet.
Lookup is **basename-only and non-recursive**.

### Verify it

```bash
python tools/verify_all.py --full     # 41 checks, all should pass
mvn -o test-compile
```

---

## 1b. Skills — common vs per-application

`.cursor/skills/` holds two kinds. Cursor picks them up from the
`description` in each `SKILL.md`; this list is for the human.

**Common** — the method, for any application we convert:

| skill | for |
|---|---|
| `readyapi-restassured-migration` | emit is incomplete or likely wrong (TODO/STUB/PARTIAL, preflight HIGH) |
| `jira-to-restassured` | a Jira story with no ReadyAPI XML to convert |
| `converted-app-environment` | service keys, token routing, credentials, databases — and the `<con:endpoint>` vs `<con:originalUri>` trap |

**Per application** — the answers for one converted app, written from
the checklist at the end of `converted-app-environment`:

| skill | for |
|---|---|
| `app-goal` | the GOAL suites |
| `app-b2b` | the B2B suites, including the service keys still unanswered |

Converting a new application means adding `app-<name>/SKILL.md`. Keep
host values and credentials OUT of these files — this repository is
public. They name keys, suites and decisions; the values belong only in
the gitignored `program_configuration.json`.

---

## 2. THE most important rule: generated vs committed

**Editing a generated file appears to work and is silently undone by the
next convert.** This has cost real time more than once.

| Path | Status |
|---|---|
| `src/main/java/com/hi/api/support/**` | GENERATED — never edit |
| `src/test/java/com/hi/api/tests/**` (imported) | GENERATED — never edit |
| `src/main/resources/templates/**` | GENERATED — never edit |
| `src/test/resources/csv/**` | GENERATED — never edit |
| `src/main/java/com/hi/api/{dsl,rest,config,data,db,reporting,domain}/**` | **COMMITTED — edit here** |
| `tools/ra_converter/framework/*.java` | **COMMITTED — bundled, copied into the tree** |
| `tools/ra_converter/ra_converter.py` | **COMMITTED — the emitter templates live here** |

So: to change generated Java, change **the emitter** in `ra_converter.py`
(f-string templates — literal Java braces are doubled `{{ }}`), or the
**bundled** file under `tools/ra_converter/framework/`.

### How to find which one emits a class

```bash
grep -n "class YourClassName" tools/ra_converter/ra_converter.py   # f-string emitter
ls tools/ra_converter/framework/                                    # bundled files
```

**A class can exist in more than one place.** `putWithAliases` lives in
BOTH `framework/ImportedScenario.java` and the `TestSupport` emitter. Patch
one and the other silently diverges. This has happened twice; keep them in
step and say so in the comment.

---

## 3. Two traps in the file-writing layer

### 3a. SKIP-IF-EXISTS files

`Emitter._AUTHOR_EDITABLE_BASENAMES` lists files that are **not overwritten
when they already exist**, so authors can hand-edit them:

```
AuthHelper.java   ManualClient.java   PerMethodCsvDataProvider.java
PlaceholderResolver.java   ProgressLogListener.java
CtxFields.java  ImportedScenario.java  ImportedTemplates.java
ImportedTestdataCleanup.java  TestThreadState.java  IdentityVocabulary.java
```

A change to their emitter reaches an existing tree **only if
`_staleness_reason` says the on-disk copy is stale**, by either:

* **rev marker** — the six bundled `framework/*.java` carry
  `// ra_converter-framework-rev: N`. Bump it on ANY edit, and update
  `_FRAMEWORK_REVS` in `tools/ra_converter/test_converter_fixes.py` with
  the new sha. A test enforces this. Forgetting means every existing tree
  keeps the old code while the commit claims to fix it.
* **missing declarations** — for the rest, a method the emitter declares
  and the on-disk file lacks.

### 3b. KNOWN BUG in that check — package-private methods are invisible

`_JAVA_DECL_RX` requires `public|protected|private`. A **package-private**
method added to a skip-if-exists file is not seen, so the file is judged
current, and the fix never lands. It compiles, the converter's self-tests
pass, and nothing happens.

**Workaround: always give new methods in those files an explicit
modifier.** The proper fix (rev markers on the five string-emitted files
too) is not done yet.

This bug hid a real one: the `PlaceholderResolver` emitter was missing five
methods (`lookupByFieldSuffix`, `lookupExactPropertiesField`, `suffixRank`,
`fieldOf`, `normalizeField`) that the committed file has. Harmless until a
refresh fired, then the build died on `cannot find symbol: suffixRank`.

---

## 4. The conversion pipeline

1. **Parse** — `parse_test_suites()` reads the XML into `TestCase` objects
   holding ordered steps: `RestStep`, `GroovyStep`, `PropertiesStep`,
   `DataSourceStep`, `TransferStep`, `JdbcStep`.
2. **Import workbook data** — `_import_datasource_rows()` attaches
   spreadsheet rows to each case (see §6).
3. **Cluster** — cases with the same call shape share one `@Test` method,
   differing only by CSV row.
4. **Emit** — `class Emitter` writes phases, hooks, templates, CSVs,
   clients and the audit.
5. **Self-check** — converter self-tests, then a post-emit dataflow check.

### Phases-as-data

A converted case is a chain of declarative phases, not inline code:

```java
CaseRegistry.register(id)
    .bootstrap(spec)
    .phase(vocab, step, Specs1::spec42)
    .verify(...)
```

* `<Suite>Specs*.java` — WHAT to call (method, path, query, token, template)
* `<Suite>Hooks*.java` — what to do with the RESPONSE (extracts, assertions)
* Both are prefixed with the suite's name (`EadkafkaeventsHooks1`); a tree
  converted before that has plain `Specs1` / `Hooks1`, and the tools read both.
* A suite's chain methods (`enrollGuest()` …) are on its own `<Suite>Steps`,
  never on the shared `ScenarioSteps` — see ARCHITECTURE.md §4.
* `PhaseContext` carries `client`, `ctx`, `row`, `softAssert`

Arguments resolve through `Ref`:

| | reads from |
|---|---|
| `Ref.ctx("k")` | runtime ctx, with alias walking |
| `Ref.row("col", fb)` | the CSV row |
| `Ref.resp("step", "path")` | an earlier response |
| `Ref.literal(v)` | a constant |

---

## 5. Placeholders — the single biggest source of bugs

ReadyAPI writes `${Step#prop}`. The converter translates that to its own
`#Step_prop#`, and the runtime resolves it against ctx + CSV row + config.

**Every bug in this area is the same shape: a value exists, a reference
asks for it, and the two spell it differently.**

Translation entry point — use it anywhere ReadyAPI syntax can reach the
runtime as DATA:

```python
_translate_readyapi_refs(text)        # ra_converter.py
```

Callers today: workbook cells (`_translate_workbook_cell`) and assertion
tokens (`_assert_default_value`). If you find a third path, route it here
rather than writing a new translator.

### Runtime resolvers — there are TWO, keep them in step

| | used by | knows |
|---|---|---|
| `RestUtilities.substitute` | request body templates | dot/underscore, case-folding, recursion (8 passes) |
| `PlaceholderResolver.resolveAll` | assertion cells, URL substitution | faker `<<x>>`, `${x}`, `#x#`/`@x@`, case-folding, field-suffix |

`ImportedScenario.mergedRow` builds the map they read: **config, then row,
then ctx** — later wins, EXCEPT a derived alias never overwrites a config
value (an exact key still does).

### The `null` fallback — read this before diagnosing any 400

When a `#key#` in a body template resolves to nothing, `RestUtilities`
substitutes the literal string **`null`**. So:

```json
"startDate": "null"
```

means *nothing supplied this field*, NOT *the data is wrong*. The failure
digest now annotates it. **ReadyAPI would send `""` here** — that parity
difference is a known open decision.

---

## 6. Workbook (Excel) data

* `--data-dir` must point at the workbooks; basename-only, non-recursive.
* A case's DataSource that the **Loop drives** supplies the ROWS (one CSV
  row per workbook row).
* Any **other** DataSource is a LOOKUP: ReadyAPI runs it once, so only row
  1 is read and its columns become constants (`case.ds_lookup`). Reading a
  lookup as rows would multiply the suite by the lookup sheet's length.
* **A cell can contain `${Step#prop}` rather than a value.** These sheets
  are parameterised like request bodies. Cells are translated on import.

---

## 7. Reading a failure digest

`target/failure-digest.txt`, written by `FailureDigestListener`. Share
that file, not the full log.

Per failure it prints the FIRST non-2xx (not the last — a broken path is
usually a symptom):

```
server said : <the API's own error body>
we sent     : <field>="<our value>"   <-- annotation if it is not really a value
url         : METHOD https://.../path
request     : <redacted body, capped>
```

`we sent:` pairs each field the server NAMED with our value, looking in the
JSON body then the query string. Annotations:

* `<-- unresolved placeholder` — nothing supplied it; `null` is the fallback
* `<-- unexpanded reference reached the wire` — a translation gap
* `<-- empty: resolved to nothing`

The auth section separates **OBSERVED** (what the Authorization header
actually carried) from **INFERRED** (what ctx held). They are not the same:
a token in ctx can still be missing from the request.

---

## 8. Safety rules — do not break these

1. **The repo is PUBLIC.** No vendor hostnames, no API specs, no
   credentials, no real property codes in committed files. Run a secret
   scan on the staged diff before every commit.
2. **`src/main/resources/program_configuration.json` is the ONLY place
   credentials live.** It is gitignored. `program_configuration.example.json`
   is the tracked, empty template that travels to a clone.
3. **Never commit the generated tree.**
4. **b2b must not regress.** Before and after any converter change,
   snapshot the `programaccountregression` output and diff it. Any delta
   must be explained and measured, not assumed benign.
5. **Never dismiss a parity failure as "the SoapUI author's bug"** without
   first running that case in ReadyAPI. It is our gap until proven
   otherwise.

---

## 9. Troubleshooting playbook

| Symptom | First thing to check |
|---|---|
| `cannot find symbol` in a support class | skip-if-exists staleness (§3) — is the emitter missing a method the committed file has? |
| A fix "doesn't take" | Did you edit a GENERATED file (§2)? Is the method package-private (§3b)? |
| `"field": "null"` on the wire | Unresolved placeholder. Run `check_near_miss_producers.py` |
| Empty query param / `/props//groups` | Upstream extract returned nothing, or a lookup DataSource was not imported |
| Assertion compares against `${...}` | Untranslated ReadyAPI syntax — route through `_translate_readyapi_refs` |
| Build fails right after clone | You have not converted yet. `support/` is generated |
| `MISSING-TREE` / `SUITE-NOT-EMITTED` | Same — convert first |

### The checks worth running, and what each answers

```bash
python tools/verify_all.py --full            # everything, 41 checks
python tools/check_substitutions.py          # does every placeholder resolve?
python tools/check_near_miss_producers.py    # does a value exist under ANOTHER name?
python tools/check_request_schemas.py        # does the body match the OpenAPI contract?
```

**What else did my change move?** Each suite has its own generated classes,
so a convert cannot break another suite -- but they still share one
converter, and a rule written for one suite's Groovy fires on every suite's.
Nothing fails when that happens. Two places answer it, and neither is a
separate tool to remember:

* **Every convert says so.** It fingerprints each suite it wrote and
  compares with that suite's previous convert (`_audit/fingerprints/`,
  hashes only). The last lines of the run list the suites that MOVED and
  whether the converter or the XML changed; `verify_all` repeats them under
  `[LAST CONVERT]`. Nothing is converted twice.
* **Before converting your tree**, ask the gate:

  ```bash
  # the suite the fix is for, plus a few it must not affect
  python tools/verify_all.py --impact eadkafkaevents,mfrstage,amexbackbook --impact-expect eadkafkaevents
  ```

  It converts those suites with the converter at HEAD and with your working
  tree, in scratch copies, and fails if a suite outside `--impact-expect`
  moved. Asked for, never run by default: it converts each named suite twice.

A suite in either list that you did not mean to touch is the finding.

`check_near_miss_producers` is the one to reach for first when something
resolves to `null`. It answers the question the others cannot: *there is a
producer right here, one rename away.* All three converter bugs fixed in
one recent session were found by asking it.

`check_request_schemas` needs a spec in `src/main/resources/openapi/`
(gitignored). It **reports, never rewrites** — where the spec and the
recorded request disagree, the recording is usually the better witness. It
has already caught a spec requiring a field it never defines.

---

## 10. Open items

* **4 x `401 Missing Credentials`** on `/groupevents` and
  `/props/{p}/groupsdata/rateplans`. The XML marks those steps
  `No Authorization`. Needs a ReadyAPI run to settle whether something
  outside the export supplies auth.
* **b2b token request** sends `username`/`password`, but neither is set in
  the `stg` block of `program_configuration.json`, and that block is
  configured `grant_type=client_credentials`. Decide which is right.
* **`null` vs `""`** for an unresolved placeholder (§5).
* **Package-private staleness blind spot** (§3b).
* Roughly 7 of the current goal failures are **environment**: the server
  answers "No availability or rate found" for the properties and dates in
  the data. No converter change fixes those.
