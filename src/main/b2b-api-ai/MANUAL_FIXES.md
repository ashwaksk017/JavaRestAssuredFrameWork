# Manual fixes — everything that needs a person

This is the list of things the converter and the gate **cannot** do for
you, what each one looks like when it happens, and the exact steps to
resolve it.

Two rules that explain most of what follows.

**1. Know what survives a convert.** The converter overwrites its own
output every run. Edit a generated file and your change is gone, with
nothing telling you it went.

| | survives a convert? |
|---|---|
| `src/main/java/com/hi/api/support/<suite>/` | **no** — rewritten |
| `src/main/java/com/hi/api/rest/clients/` | **no** — rewritten |
| `src/test/java/com/hi/api/tests/imported/` | **no** — rewritten |
| `src/test/resources/csv/<suite>/` | **no** — rewritten (but see [§1](#1-stop-one-data-row-from-running)) |
| `src/main/resources/templates/<suite>/` | **no** — rewritten |
| `src/test/resources/testng-b2b-*.xml`, `Suites/` | **no** — rewritten |
| `src/test/resources/csv/manual/` | **yes** — yours, and tracked |
| `src/test/java/com/hi/api/tests/{manual,jira,framework}/` | **yes** |
| `src/main/java/com/hi/api/{data,reporting,retry,db,domain,auth}/` | **yes** |
| `src/main/java/com/hi/api/rest/utilities/` | **yes** |
| the 11 author-editable files below | **yes** — skip-if-exists |
| `tools/**`, `*.md`, `pom.xml` | **yes** |

The 11 author-editable files the converter writes **only if absent**:
`AuthHelper.java`, `ManualClient.java`, `PerMethodCsvDataProvider.java`,
`PlaceholderResolver.java`, `ProgressLogListener.java`, `CtxFields.java`,
`ImportedScenario.java`, `ImportedTemplates.java`,
`ImportedTestdataCleanup.java`, `TestThreadState.java`,
`IdentityVocabulary.java`.

> If the converter finds one of these on disk but the version it carries
> declares a method yours does not, it **refreshes** it and leaves your
> copy as `<name>.stale.orig`. That file is not junk — diff it to see
> what you lose, then delete it.
> ```bash
> git status --short | grep stale.orig
> diff <(cat src/main/java/com/hi/api/support/CtxFields.java.stale.orig) \
>      src/main/java/com/hi/api/support/CtxFields.java
> ```

**2. `--clean` is per-suite.** It removes only the suite being converted
("Other suites and framework files are untouched"). Converting one suite
never wipes another — but it *does* rewrite CSV data rows in other
suites, so do not treat a single-suite convert as side-effect-free.

---

## 1. Stop one data row from running

**Use when** a row is known-broken, environment-dependent, or waiting on
a fix, and you want the rest of the file to keep running.

Put `N` in the reserved **`execute`** column:

```
description,test_case_id,execute,jira_xray_id,...
"happy path",B2B-1234_activate_204,,h4b,...        <- blank: runs
"known broken",B2B-1235_activate_409,N,h4b,...     <- skipped
```

| cell | effect |
|---|---|
| blank / whitespace / no column | **runs** |
| `N` `n` `No` `false` `0` `off` `skip` | **skipped**, reported with the row id |
| `Y` `y` `Yes` `true` `1` `on` `run` | runs |
| anything else | **runs**, and warns on the console |

**Verify:**
```bash
# whichever suite holds the test -- Suites/ for an imported suite,
# testng-manual.xml / testng-jira.xml for a hand-written one
mvn test "-DsuiteXmlFile=Suites/Inventorystage_Smoke.xml"

# look for:
#   [execute] SKIPPED  <Class>#<method>  execute=N [test_case_id=...]
#             -- switched off in the data sheet, not a failure
```

- It is reported as **SKIPPED, not filtered out** — so the run output
  still answers "what are we not running?".
- It **survives a reconvert**: the converter carries the column forward
  keyed by `test_case_id`. A row whose ReadyAPI case has gone keeps no
  flag, because there is nothing left to switch off.
- It fails **open**: anything unrecognised runs. A typo must never cost
  coverage silently.
- It skips the **row**, not the method. Four rows with one `N` still run
  three times.

Full detail: [CreateTestCase.md](CreateTestCase.md) §3.

---

## 2. A placeholder goes to the server verbatim

**Symptom** — `verify_all` fails on `substitution`:

```
NEW placeholders with NO producer (sent to the server verbatim):
      1x  #http_get_inventory_200_Response_roomTypeCode#   first in Specs1.java
```

A request would go out with the literal `#...#` text as a parameter or
body value. **Nothing downstream fixes this**; the check exists because
the server's complaint will name a field, not the missing producer.

### Step 1 — find out who was supposed to produce it

The name is `<step>_Response_<field>`. Search the ReadyAPI XML for the
reference, not the generated code:

```bash
grep -o '\${http_get_inventory_200#[^}]*}' tools/ra_converter/input/InventorySTAGE.xml
```

### Step 2 — check the named step exists **in that test case**

This is the part worth being careful about. A case copied between
ReadyAPI projects keeps the reference and loses the step.

```bash
python - <<'EOF'
import io, re
XML, REF = "tools/ra_converter/input/InventorySTAGE.xml", "http_get_inventory_200"
s = io.open(XML, encoding="utf-8", errors="replace").read()
pos = s.index(REF)
start = s.rfind("<con:testCase ", 0, pos)
end = s.index("</con:testCase>", pos)
seg = s[start:end]
print("case:", re.search(r'name="([^"]*)"', seg).group(1))
print("steps:")
for m in re.finditer(r'<con:testStep type="([^"]+)"[^>]*name="([^"]*)"', seg):
    print(f"   {m.group(1):<16} {m.group(2)}")
print(f"\n{REF} is a step in this case:",
      bool(re.search(r'name="%s"' % re.escape(REF), seg)))
print(f"{REF} exists anywhere in the file:", s.count('name="%s"' % REF) > 0)
EOF
```

### Step 3 — pick the fix that matches what you found

| what step 2 showed | fix |
|---|---|
| the step exists in the case, **before** the consumer | nothing to do in the XML — reconvert and it resolves |
| the step exists in the case but **after** the consumer | move it earlier in ReadyAPI; a producer must run first |
| the step exists in the file, **other case** | repoint the reference at a step in *this* case |
| the step exists **nowhere** | the case was copied in. Add a step that returns the field, then repoint |

Repointing is one edit in the XML — keep the XPath, change only the step
name before the first `#`:

```
-  ${http_get_inventory_200#ResponseAsXml#declare namespace ns1='...'; //ns1:Response[1]/...}
+  ${GET_Groups_Inventory#ResponseAsXml#declare namespace ns1='...'; //ns1:Response[1]/...}
```

> **The producer must run BEFORE the consumer.** Pointing a reference at
> the step that uses it is self-referential: the value is empty the
> first time and the request goes out without it. If no earlier step
> returns the field, you have to add one.

### Step 4 — reconvert that one suite and confirm a producer appears

```bash
python tools/ra_converter/ra_converter.py \
    --input tools/ra_converter/input/InventorySTAGE.xml \
    --output . --clean

grep -rn 'extract("GET_Groups_Inventory_Response_roomTypeCode"' \
    src/main/java/com/hi/api/support/inventorystage/
```

You want a line pairing the consumer with a producer, and the producer's
path must be the **full** path, not the leaf:

```java
.query("roomType",  "#GET_Groups_Inventory_Response_roomTypeCode#")
.extract("GET_Groups_Inventory_Response_roomTypeCode",
         "roomTypeInventory[0].roomTypeCode")   // nested, not "roomTypeCode"
```

A leaf-only path there (`"roomTypeCode"` for a field two levels down)
returns null, and the request goes out empty with nothing reporting it.
If you see that, the XPath had a shape the converter could not translate
— [raise it](#6-when-none-of-this-applies) rather than editing the
generated file.

Then:
```bash
python tools/check_substitutions.py      # expect no NEW placeholders
python tools/verify_all.py --baseline
```

### If the test is simply wrong at source and you are not fixing it now

Switch the row off (§1) so it stops running, **and** record why the
placeholder is accepted, or the gate stays red:

```json
// tools/substitutions_baseline.json
"accepted_placeholders": {
  "#http_get_inventory_200_Response_roomTypeCode#":
    "Dangling ReadyAPI ref: the producer step is in smokePROD.xml, not
     InventorySTAGE.xml, and the case has no earlier inventory call. Row
     switched off with execute=N pending a source fix. Owner: <you>,
     review by <date>."
}
```

Be deliberate about this one. The other entries in that file are all
Javadoc false positives; this list is not meant to hold real gaps, and
an entry with no owner and no date becomes permanent by neglect.

---

## 3. A step ReadyAPI sends that the converted suite does not

**Symptom** — `verify_all` fails on `step-parity`:

```
B2B-422_get_spendSummary_usermembernotactive_403_suspended
    1/17 step(s) unreachable: tokenRequest 2
```

**A parity gap is ours until the ReadyAPI XML says otherwise.** Do not
start from "the SoapUI author made a mistake" — check the source first.

```bash
python tools/check_step_parity.py            # the full list
```

1. Open the case in the XML and read the step in question.
2. If ReadyAPI really sends it and we do not, it is a converter gap —
   that is a code fix, not a manual one.
3. `tokenRequest` specifically means the case registers no bootstrap, so
   every later call in it goes out unauthenticated.

To accept one knowingly, add it to `tools/autofix/baseline.json`. The
rules are enforced:

```bash
python tools/verify_all.py --json target/verify.json
# read .checks[] | select(.name=="step-parity") | .salient_fingerprint
```

```json
{
  "check": "step-parity",
  "salient_fingerprint": "<from the command above>",
  "reason": "What the failure IS and why it is tolerable. Not 'known issue'.",
  "recorded": "YYYY-MM-DD",
  "expires": "YYYY-MM-DD",
  "owner": "<you>",
  "scope": "which suites / how many cases when recorded"
}
```

- `expires` is **required** — an expired entry fails the gate.
- A plain `verify_all` still reports it as a failure; only
  `--baseline` consults the file.
- **The fingerprint covers the inventory line** (`N enabled REST steps
  across M cases`), so adding or removing any suite invalidates every
  accepted entry even when the failure itself is unchanged. If an entry
  reads "stale" straight after a convert, compare the failure text
  before assuming something broke — then re-fingerprint.

---

## 4. Configuration that only you can fill in

### Credentials — one file, nothing else

`src/main/resources/program_configuration.json` is gitignored and is the
**only** place a secret belongs.

```bash
cp src/main/resources/program_configuration.example.json \
   src/main/resources/program_configuration.json
```

Fill in the block for your environment. The active environment comes
from `TEST_ENV`, then `env=` in `application.properties`, then `qa`.

To see which environment resolved, and which keys are filled in,
**without printing any value** -- host names and client ids are as
unwelcome in a pasted terminal log as a password is:

```bash
python -c "import sys; sys.path.insert(0,'tools/jira'); import projectconfig as c; env, src = c.active_env(); print('env', env, 'from', src); print({k: ('set' if str(v).strip() else 'EMPTY')        for k, v in c.section('api_config')[0].items()})"
```

Never paste a credential into a test, a CSV, a template, or a commit
message. If a value has to vary per environment, add a sibling block —
do not branch in code.

### The OpenAPI / Swagger spec

Drop it in `src/main/resources/openapi/`. It is gitignored (it is a
vendor contract and this repo is public). Without it,
`request-schemas` reports **SKIP — checked nothing**, which is not a
pass. See `src/main/resources/openapi/README.md`.

### ReadyAPI input XMLs

Put them in `tools/ra_converter/input/`. Gitignored by
`tools/ra_converter/.gitignore`, so the protection travels with the
directory.

```bash
ls tools/ra_converter/input/*.xml
git status --short tools/ra_converter/input/    # must print NOTHING
```

That last command is the check that matters: these files carry endpoint
paths, hostnames, and in at least one case a plaintext DB password.

### Per-project converter settings

`tools/ra_converter/converter.config.json`. The one most often wrong:

```json
"scenario": { "entry_class": "Onboarding" }
```

`Onboarding` suits the B2B projects; a GOAL project wants `GoalJourney`
so a goal test does not read as onboarding. It is **project-wide**, so
to convert a second project into the same tree, pass a separate file
instead of editing the shared one:

```bash
python tools/ra_converter/ra_converter.py \
    --input <dir-of-goal-xmls> --output . --clean \
    --config /path/to/converter.config.goal.json
```

---

## 5. Writing a test by hand

Full manual: [CreateTestCase.md](CreateTestCase.md). The short version:

| | |
|---|---|
| Java | `src/test/java/com/hi/api/tests/jira/<Area>Test.java` |
| package | `com.hi.api.tests.jira` (must **not** contain `.tests.imported.`) |
| suite | `src/test/resources/testng-jira.xml` |
| rows | `src/test/resources/csv/manual/<Class>/<method>.csv` |
| body templates | `src/test/resources/templates/manual/` |

From a Jira story, let the tooling build the brief first — see
[README §From a Jira story](README.md#from-a-jira-story-no-readyapi-xml):

```bash
python tools/jira/run.py --url https://jira.yourorg.com/browse/B2B-1234
```

A new guard test must be registered or it never runs:

```bash
# add <class name="..."/> to:
src/test/resources/testng-guards.xml
mvn test -DsuiteXmlFile=src/test/resources/testng-guards.xml
```

---

## 6. When none of this applies

Before hand-editing anything generated, check which list it is on at the
top of this file. If it is rewritten by a convert, the fix belongs
upstream — in the ReadyAPI XML, in `tools/ra_converter/`, or in a
hand-written test — and a local edit will disappear without a trace.

Leave the gate red rather than silencing a check you have not
understood. A red gate is a question; a triaged entry with no reason is
a wrong answer that outlives whoever wrote it.
