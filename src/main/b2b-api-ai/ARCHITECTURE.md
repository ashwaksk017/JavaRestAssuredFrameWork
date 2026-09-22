# Framework architecture

How a ReadyAPI suite becomes runnable Java, and which parts of the tree are
written by hand versus emitted by the converter.

The single most important rule when reading this repo:

> **Everything under `src/main/java/com/ak/api/support/`, `tests/imported/`,
> `src/test/resources/csv/`, `templates/`, `Suites/`, `_audit/` and `_flows/`
> is GENERATED and gitignored.** It is rewritten on every convert and must
> never be hand-edited — `--clean` deletes it.

---

## 1. Convert-time flow

```mermaid
flowchart TD
    XML["ReadyAPI project XML<br/><code>tools/ra_converter/input/*.xml</code>"]
    CONV["<b>ra_converter.py</b><br/>parse · cluster · translate"]
    GROOVY["groovy_translator.py<br/>Groovy → Java"]
    FLUENT["fluent_scenario.py<br/>phase naming · reuse catalog"]
    PEMIT["phase_emit.py<br/>specs · hooks · registration"]

    XML --> CONV
    CONV --> GROOVY
    CONV --> FLUENT
    CONV --> PEMIT

    PEMIT --> SPECS["<b>Specs&lt;N&gt;.java</b><br/>one method per DISTINCT spec<br/>deduped suite-wide"]
    PEMIT --> PHASES["<b>&lt;TestClass&gt;Phases.java</b><br/>which phases each case runs<br/>references only"]
    PEMIT --> HOOKS["<b>Hooks&lt;N&gt;.java</b><br/>translated Groovy, one per distinct block"]
    PEMIT --> CALLS["<b>Calls.java</b><br/>one engine per client operation"]

    CONV --> TESTS["@Test classes<br/><code>tests/imported/&lt;suite&gt;/</code>"]
    CONV --> CSV["per-method CSVs<br/><code>src/test/resources/csv/</code>"]
    CONV --> TMPL["request-body templates<br/><code>resources/templates/&lt;suite&gt;/</code>"]
    CONV --> CLIENT["&lt;Suite&gt;Client.java<br/><code>rest/clients/</code>"]
    CONV --> AUDIT["audit + flow diagrams<br/><code>_audit/ _flows/</code>"]

    classDef gen fill:#eef7ff,stroke:#3a7bd5
    class SPECS,PHASES,HOOKS,CALLS,TESTS,CSV,TMPL,CLIENT,AUDIT gen
```

## 2. Run-time flow

```mermaid
flowchart TD
    TESTNG["TestNG suite<br/><code>Suites/*.xml</code>"] --> TEST["@Test method<br/>(generated)"]
    DP["PerMethodCsvDataProvider"] --> TEST
    CSVF["CSV row<br/>expected_* columns"] --> DP

    TEST --> ENTRY["<b>Onboarding.start(row, caseId)</b><br/>the one generated entry class"]
    ENTRY --> REG["CaseRegistry.forCase(caseId)"]
    REG --> PH["&lt;TestClass&gt;Phases.register()"]
    PH -.->|method refs| SP["Specs&lt;N&gt;::specN"]

    ENTRY --> CHAIN["fluent chain<br/><code>.enrollGuest() .createProgramAccount() …</code>"]
    CHAIN --> RUN["ScenarioSteps.runPhase(vocab, step)"]
    RUN --> SPEC["PhaseSpec<br/>verb · path · args · token · asserts"]
    SPEC --> ENG["Calls.call(ctx, spec)<br/>engine per operation"]
    ENG --> RS["<b>RestStep.exec(...)</b><br/>template · query · headers · poll · status"]
    RS --> CLIENT["&lt;Suite&gt;Client → REST Assured"]
    RS --> ASSERT["ResponseAsserts<br/>CSV override → expected value"]
    SPEC --> HOOK["Hooks&lt;N&gt;::hookN<br/>translated Groovy after the call"]
    HOOK --> DB[("Db<br/>JDBC")]
    CHAIN --> COMPLETE[".complete() → verify helpers"]

    classDef hand fill:#f3fff0,stroke:#3d9140
    class DP,RS,ASSERT,DB,ENG,SPEC hand
```

Green = hand-written framework. Blue = generated.

## 3. What is hand-written vs generated

| Package | Files | Kind |
|---|---|---|
| `rest/utilities/` | 12 | hand-written — `RestStep`, `RestUtilities`, `ResponseAsserts`, `AuthHelper`, `Headers` |
| `rest/utilities/phase/` | 5 | hand-written — `PhaseSpec`, `PhaseRunner`, `PhaseContext`, `CaseRegistry`, `Ref` |
| `data/` | 9 | hand-written — `PerMethodCsvDataProvider`, `PlaceholderResolver`, `Expected`, `FakeData` |
| `dsl/` | 4 | hand-written — **the manual-test surface** (see §5) |
| `db/`, `db/repo/`, `db/schema/` | 7 | hand-written |
| `domain/` (+ account/guest/member) | 4 | hand-written facades |
| `retry/`, `security/`, `schema/`, `auth/`, `config/`, `context/` | — | hand-written |
| `reporting/`, `gitlab/`, `xray/`, `meta/` | — | hand-written |
| `support/` | 7 | **generated** — incl. `ImportedScenario`, `CtxFields`, `ImportedTemplates` |
| `support/scenario/` | 1 | **generated** — `ScenarioSteps` base |
| `support/<suite>/scenario/` | 3 | **generated** — entry, suite steps base, verify helper |
| `support/<suite>/cases/` | 354 | **generated** — `Phases`, `Specs`, `Hooks`, `CaseIndex` |
| `rest/clients/` | 1 | **generated** — one client per suite |
| `templates/<suite>/` | 1 | **generated** — template constants |

`ImportedScenario` and `CtxFields` look like framework but live under
`support/` and **are emitted** — do not hand-edit them.

## 4. Two emission modes

`--phase-specs` is **on by default**. `--no-phase-specs` opts out.

| | default (phase specs) | `--no-phase-specs` |
|---|---|---|
| phases live as | data (`Specs`/`Phases`) | copied Java methods |
| entry classes | 1 (`Onboarding`) | 134, of which 101 numbered |
| `ScenarioSteps` methods | 192 (96 vocabulary names × 2) | 540 |
| per-case setup | `CaseRegistry.forCase(id)` | one class per bootstrap variant |

Compare **method counts, not line counts**. The emitter preserves
`protected Response` fields already declared in an existing
`ScenarioSteps.java` (see `_existing_scenario_steps_resp_fields`), so a
tree that has had several suites converted into it carries their fields
too. A fresh single-suite convert emits ~963 lines; this repo's tree is
1,188 because 225 fields survive from earlier suite converts. The method
count is unaffected by that and is the stable comparison.

Both modes produce the same tests, the same CSV columns and the same
TestNG suites. The default exists because numbered clones
(`Onboarding2`, `createProgramAccount104`) made the tree hard to read.

Specs are deduplicated **suite-wide**: one distinct builder becomes one
method in `Specs<N>`, referenced by every case that uses it. Doing this
per class instead cost ~18k duplicated lines.

## 5. Writing tests by hand

Hand-written tests use the **DSL**, not the generated classes:

```java
import com.ak.api.dsl.CustomerOnboarding;   // hand-written, committed
```

`com/ak/api/dsl/` is deliberately independent of converter output. As its
own javadoc puts it: `ScenarioSteps` is converter output, regenerated and
gitignored, so the DSL depends only on committed framework pieces
(`RestStep`, the domain facades, `ImportedScenario`).

**A converter run therefore cannot break hand-written tests.** Manual
tests live in `src/test/java/com/ak/api/tests/manual/` and `…/dsl/`, are
tracked in git, and are never touched by `--clean`.

## 6. Running the converter

```bash
# canonical: whole input directory, so fluent reuse is computed across
# every imported suite at once
python tools/ra_converter/ra_converter.py \
    --input tools/ra_converter/input \
    --output . --package-root com.ak.api --clean --max-name-len 40

# one suite only
python tools/ra_converter/ra_converter.py \
    --input tools/ra_converter/input/<suite>.xml \
    --output . --package-root com.ak.api --clean --max-name-len 40

# opt out of phase-spec emission
... --no-phase-specs
```

Guards run with `python tools/verify_all.py` (add `--full` for the Java
compile and TestNG). Note that the guard suite does **not** spawn a
converter process — it inspects source, unit-tests emitters, and builds
`Emitter` in process. Changes that only show up in a full convert need an
actual convert plus a compile as evidence.
