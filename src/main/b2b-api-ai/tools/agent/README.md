# The agent UI

One local page, three tabs, over the tools that already exist. No
framework, no build step, no database — Python standard library only.
The job directory on disk **is** the state, so a browser refresh loses
nothing and anything the page does can be re-run from the command line.

## Start it

**The path is relative to where you are.** This file sits next to the
code, so you are probably already in `tools/agent/` — in which case:

```
python server.py
```

From the repository root (`src/main/b2b-api-ai`) instead:

```
python tools/agent/server.py
```

Either is fine. The working directory does not matter: the server finds
the repository from its own location, and the banner prints the root it
resolved, so check that line rather than guessing. Giving the wrong
relative path produces the doubled-up one:

```
tools/agent> python tools/agent/server.py
python: can't open file '...\tools\agent\tools\agent\server.py'
```

Then open **http://127.0.0.1:8787**.

```
python server.py --port 9000     # a different port
python server.py --verbose       # log every request
```

Ctrl-C stops it. Nothing is installed and nothing is left running.

> Every other command below is written **from the repository root**
> (`src/main/b2b-api-ai`). From `tools/agent/` drop the `tools/agent/`
> prefix, as above.

Requires Python 3.9 or newer (3.11 is what it is run on). The UI itself
needs no third-party packages; the converter it drives in tab 2 needs
whatever it normally needs, `openpyxl` included.

## It is bound to loopback on purpose

This process **starts commands that write to the repository**. It has no
login. Serving it on anything but loopback puts that on the network
unauthenticated, so `--host` has to be given explicitly and the banner
says what it means:

```
python tools/agent/server.py --host 0.0.0.0
  WARNING: not bound to loopback. This process starts commands that
  write to the repository and has NO authentication. Put it behind
  something that does.
```

If a team needs to reach it, put it behind something that authenticates.
Do not just change the flag.

Three things the server refuses, because a page on another site could
otherwise have made the request on your behalf:

| refused | why |
|---|---|
| a `Host` header that is not loopback | a domain that resolves to 127.0.0.1 (DNS rebinding) looks same-origin to the browser |
| an `Origin` that is not loopback | this API starts commands; it answers only its own page |
| a POST without `Content-Type: application/json` | `text/plain` is a *simple* request — no CORS preflight — so a form on any site could have started a convert, `--clean` included |

That closes the drive-by path. It does not make the server safe to
expose.

## The three tabs

### Tab 1 — New test case

Paste a story, request payloads, and Jira or Confluence links. It reads
them, then decides **create / update / upstream**: whether this is a new
test, a change to one that exists, or something that belongs further up
the chain.

Writes `brief.md` and `plan.md` into the job directory.

Two things it needs before links will work:

- **Jira and Confluence hosts must be allowlisted** in
  `src/main/resources/program_configuration.json` (gitignored — the only
  place the tokens live), under `jira_config.base_urls` and
  `confluence_config.base_urls`. A host that is not on the list is
  **refused**, deliberately, rather than fetched from somewhere else.
- **The shape index** at `target/shape-index.json` is what the
  create/update decision is made against. It is built from what is
  currently converted, so **rebuild it after a convert** or every verdict
  is about the old tree. Tick the rebuild option, or:

  ```
  python tools/agent/locate.py --job <job> --rebuild-index
  ```

### Tab 2 — Convert ReadyAPI

A form over the converter's CLI. Every flag the command line takes is
here, and the command it builds is shown before it runs, so the page is
never doing something you cannot reproduce in a terminal.

`--data-dir` points at the folder of `.xlsx` workbooks the ReadyAPI XML
names, and is how DataSource columns get real values instead of empty
cells. It is **optional** — a suite whose project has no DataSource
workbooks needs none — so the form marks it *advised*, not required,
and will run without it. When the suite does read workbooks, leaving it
out empties every DataSource column, and the run then looks like an API
problem rather than a missing path, which is why the form says so.

### Tab 3 — Agent loop

The only tab that writes code. It takes the plan tab 1 produced and has
**Cursor** act on it. All Java (and converter) changes go through Cursor;
this tool decides where it may write, checks what it did, and holds the
result until a person approves it.

```
Run Cursor  ->  guardrails  ->  verify  ->  PENDING REVIEW
                                                 |
                          you read proposed.diff, then click
                                                 v
                                             Approve
```

#### Choose what Cursor may change

| scope | Cursor may write | verify | Approve does |
|---|---|---|---|
| **A new hand-written test** | `tests/jira/`, `csv/manual/` | compile + duplicate-method check | commits to branch `agent/<job>` and pushes it |
| **Converted Java of one suite** | that suite's `support/<suite>/`, `tests/imported/<suite>/`, `templates/<suite>/`, `csv/<suite>/`, `<Suite>Client.java` | compile + duplicate-method check | keeps the files on this machine; stores the change so it survives a convert |
| **The converter itself** | `tools/ra_converter/`, the hand-maintained runtime (`rest/utilities/`, `retry/`, `config/`, `data/`, `db/`), framework tests | compile + the whole gate, before and after | commits to branch `agent/<job>` and pushes it |

**Converted Java stays on the machine.** Those files are gitignored and
regenerated by every convert, so they cannot be committed or pushed.
This is the scope for "the converter has done its job, now the Java
needs adjusting here".

**One suite means one suite.** Generated classes are per suite
(`<Suite>Hooks1`, `<Suite>Specs1`, `<Suite>Steps`, ...). A converted job
names its suite and is held to that suite's folders. Two things keep it
there:

- *Cursor is told.* The prompt lists the suite's own generated files,
  the generated classes **every** suite shares (`ImportedScenario`,
  `ImportedRestClient`, ...) marked read-only, the names of the other
  suites, and any change already approved for this suite.
- *It is checked.* The whole generated tree is copied before the run. If
  Cursor touches another suite's class or a shared one, the job fails
  and **everything** it touched is put back from that copy -- git cannot
  do this, because it does not track those files.

**An approved change survives a convert.** Approving stores the change
under `.agent-patches/<suite>/` (gitignored). After every later convert
of that suite the converter puts it back, as a three-way merge of what
the converter wrote then, what was approved, and what it writes now:

```
[agent-patches] amexbackbook: 1 re-applied, 0 already in place, 0 CONFLICT, 0 refused
[agent-patches]   re-applied: 001-job1: src/main/java/.../AmexbackbookHooks1.java
```

A merge, not a diff, because generated classes renumber between
converts and a diff would fail on lines that have nothing to do with the
change. If the converter now changes the **same lines**, the file is
left exactly as converted and the conflict is reported with the path of
the approved version -- it is never half-applied. A stored change can
only name files of its own suite.

```
python tools/agent/patches.py list                              # what is stored
python tools/agent/patches.py reapply --suite amexbackbook      # by hand
python tools/agent/patches.py forget  --suite amexbackbook --id 001-job1
python tools/ra_converter/ra_converter.py ... --no-reapply-patches   # skip it for one convert
```

**The converter scope does not edit generated output.** Fix the emitter,
not the copy it produced -- a converter job that touches a suite's
generated files fails. Its verify step runs the gate once before Cursor
and once after, and fails only when a check that passed before fails
after, so a tree that already has known failures can still be worked on.

Each run is one Cursor call, plus one repair call if the result does not
verify (the compiler or gate output is handed back).

#### The Cursor log

Everything about the conversation with Cursor is in its own log,
`target/agent/<job>/cursor.log`, shown in its own pane and separate from
the command log:

```
generate  job=job1  scope=converted  suite=amexbackbook  base=main@27402ea1
may write: src/main/java/com/hi/api/support/amexbackbook/, ...
attempt 1 of 2: prompt 4312 characters (agent-prompt-1.txt)
----- prompt -----            (first 6,000 characters)
answered in 84.2s  status=finished  run id=...  model=composer-2.5
----- answer -----
changed: 0 created, 2 modified, 0 deleted, 0 outside
  modified  src/main/java/com/hi/api/support/amexbackbook/cases/...
verify ok   compile
PENDING REVIEW
```

It is appended to, never truncated, so a job's whole history is there:
every attempt, every rejection and what was undone, the approval or the
discard. The API key is never written to it.

#### What Cursor needs

- **The SDK.** `cursor-sdk` is installed automatically the first time it
  is needed (`pip install -r requirements-cursor.txt` into the Python
  that runs the server). If that fails -- no network, a locked-down
  index -- the log says so and gives the command to run by hand.
- **The key.** `CURSOR_API_KEY` in the environment of the process that
  starts the server, or `apiKey` in
  `tools/ra_converter/cursor_agent.json` (gitignored; copy the
  `.example` beside it). The same file and the same lookup the converter
  uses, so there is one key in one place.

**Check Cursor setup** runs both checks without calling the agent.

#### What is enforced, and where

These are checked against the files on disk **after** Cursor returns.
The prompt says the same things, but a prompt is advice.

| rule | if broken |
|---|---|
| writes only inside the chosen scope | job fails, every touched file is put back -- tracked files from git or from the copy of your own uncommitted version, generated files from the copy of the generated tree taken before the run |
| no deletes | job fails, the file is restored |
| another suite's generated files, or the ones every suite shares | job fails and they are put back; the message names whose they were |
| the agent does not commit or switch branch | job fails |
| no uncommitted work of yours inside the write paths before a run | run refused, agent not called |
| approve only from `pending-review`, with `--confirm <job>` | refused |
| no credential, and no host or secret value from `program_configuration.json`, in a diff that is pushed | push refused before any commit |

The scopes are a list in `tools/agent/policy.json`; widen or narrow the
write paths there.

#### From the command line

```
python tools/agent/loop.py setup    --job <job>
python tools/agent/loop.py generate --job <job> --scope new-test
python tools/agent/loop.py generate --job <job> --scope converted --suite amexbackbook
python tools/agent/loop.py generate --job <job> --scope converter
python tools/agent/loop.py approve  --job <job> --confirm <job>
python tools/agent/loop.py discard  --job <job>
```

## Where the state lives

Everything is under `target/agent/<job>/`:

```
target/agent/<job>/
  intake.json            what was pasted, with credentials redacted
  brief.md               the story as the tools read it
  plan.md                create / update / upstream, and why
  locate.json            the matching evidence behind that verdict
  <runnable>.log         full output of each command
  <runnable>.status.json state, exit code, and the argv it ran
  cursor.log             tab 3: the conversation with Cursor, appended across runs
  review.json            tab 3: state, scope, the files changed, verify result
  proposed.diff          tab 3: what is waiting for review
  converted.patch        tab 3: an approved change to converted Java, as a diff to read
  pre-generated/         tab 3: the changed generated files as they were before the run
  gate-before.json       tab 3 (converter scope): what failed before / after
  agent-prompt-N.txt     tab 3: exactly what Cursor was asked
  agent-answer-N.md      tab 3: what it said it did
  pre/                   tab 3: your uncommitted files as they were before the run
```

The page polls the log by byte offset, so a long convert streams and a
refresh picks up where it left off. The UI will only read back
`brief.md`, `plan.md`, `intake.json`, `locate.json`, `review.json` and
`proposed.diff` — a job directory
cannot be used to read arbitrary files through the API.

## What it is allowed to run

The API never accepts a command *string*. Every runnable is named in
`jobs.py` and the caller may only pass arguments that runnable declares:

| name | does |
|---|---|
| `convert` | convert ReadyAPI suites |
| `intake` | read the pasted story and links |
| `locate` | decide create / update / upstream |
| `audit-service-keys` | audit service keys |
| `audit-token-chain` | audit the token chain |
| `agent-setup` | check the Cursor SDK, the key and git |
| `agent-generate` | Cursor makes the change in the chosen scope; guardrails; verify |
| `agent-approve` | approve: push branch `agent/<job>`, or keep converted Java locally |
| `agent-discard` | undo what the agent wrote |
| `agent-patches` | list the approved changes stored for re-applying |
| `agent-reapply` | put a suite's stored changes back by hand |

Arguments declared as paths are resolved against the repository root and
**refused if they escape it**, so `--output ../../somewhere` cannot be
used to write outside the tree.

## If something looks wrong

- **Page loads but every action fails** — check the terminal. A
  `PermissionError` naming `Host`, `Origin` or `Content-Type` is the
  guard above, not a bug.
- **`locate` says the index is stale** — it is telling you the truth.
  Rebuild it after a convert.
- **Confluence or Jira link refused** — the host is not in
  `*_config.base_urls`. Add it to `program_configuration.json`; do not
  work around it.
- **A convert is "already running"** — two converts into one output
  directory would interleave their writes. Wait, or use a different job.

## Running the tests

```
python tools/agent/test_intake.py
python tools/agent/test_jobs.py
python tools/agent/test_locate.py
python tools/agent/test_loop.py
```

`test_loop.py` is in the gate (`verify_all`, check `agent-loop`): it is
the code that decides what may be pushed. The other three are **not** in
the usual sweep -- the repository-wide run globs `tools/test_*.py` and
`tools/ra_converter/test_*.py`, which does not reach this directory. Run
them yourself after touching `intake.py`, `jobs.py` or `locate.py`:

```
for t in tools/agent/test_*.py; do python -B "$t" || break; done
```
