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
needs no third-party packages; the converter it drives in the Convert ReadyAPI tab needs
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

## Mock mode: try it before it touches anything

The workbench talks to two services nobody wants to meet for the first
time by pressing a button: a Jira that holds real tickets, and Cursor,
which costs money and writes code. `tools/agent/mock.json` has one
switch for each:

```
{
  "jira": true,
  "cursor": true
}
```

With a switch `true`, the workbench answers from sample data on this
machine. The buttons, the commands, the checks and the results are the
same; nothing leaves the machine. Set it to `false` and the same button
reaches the real service. Nothing else has to change, and no restart is
needed: each command reads the file when it starts.

| switch | `true` | `false` |
|---|---|---|
| `jira` | The **Jira** and **Defects** tabs use a sample project (three releases, stories, bugs, tests) under whatever project key you type. No `jira_config`, host or token is needed. **Write to Jira** changes `target/agent/mock-jira.json` and nothing else. | The Jira in `program_configuration.json`, with its token. |
| `cursor` | **Design the tests** returns two cases per endpoint; **Suggest reasons** is a keyword match; **Run Cursor** writes one placeholder test so the checks, the review and Discard can be tried. No SDK, no key, no cost. | Cursor, with the key in `cursor_agent.json`. |

While a switch is on:

- the page shows a **MOCK MODE** banner on every tab;
- every command says MOCK in its log, every result carries
  `"mock": true`, and sample summaries start with `[MOCK]`;
- a mock agent run **cannot be approved** -- only discarded -- whatever
  the switch says by then, so a placeholder never reaches a branch;
- what the sample Jira returns is saved under `target/jira/search/` with
  `MOCK-` in front of its name, so it never replaces a real result;
- a reason the mock Cursor suggested is **not written to a real bug**:
  with `"jira": false, "cursor": true` the page does not pre-select it
  and **Write to Jira** refuses it. A reason you choose yourself is yours
  to write;
- the sample Jira answers only the queries the workbench sends (project,
  keys, issue type, fix version, "has a reason"). Any other JQL is
  refused rather than answered with everything;
- the file must hold exactly the two switches, each exactly `true` or
  `false`. A misspelt or missing switch, an extra one, or any other
  value is an error: the page says the settings cannot be read, and
  commands refuse rather than guess. Only deleting the file altogether
  means "everything real".

**Not simulated**, whatever the switches say: Confluence links on the
New test case tab are fetched for real; `tools/jira/fetch.py` and
`run.py` (a single story, from a console) use the real Jira; the
converter and its own Cursor assist; the tests and the Failures tab;
and git. As shipped both switches are `true`, so a fresh copy is safe to
click through. **Switch a service to `false` when you are ready to use
the real one**, and load again: what was loaded from the sample data is
not carried over to the real service.

For one process, the environment variable `WORKBENCH_MOCK` overrides
the file: `0` turns every switch off (the tests run that way), `1` turns
every switch on. Any other value is an error.

## The tabs

### New test case

Paste a story, request payloads, and Jira or Confluence links. It reads
them, then decides **create / update / upstream**: whether this is a new
test, a change to one that exists, or something that belongs further up
the chain.

Writes `brief.md` and `plan.md` into the job directory.

When the job names a story -- a Jira link in the links box, or a key
written in the pasted text -- the plan ends with **Earlier work that
names this story**: commits on any branch whose message mentions the
key, with the files they touched, and test files that mention it now.
It is evidence, not a verdict: the create / update decision is made from
the shape of the request and is not changed by it. A `CREATE` next to
commits that name the story is one to read twice.

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

#### Design the API tests (optional)

The lower half of the New test case tab. *Read it* and *locate* answer "is this
automated already?". This answers "what should be tested?": Cursor is
given the API specification, the requirements and `brief.md`, and
proposes test cases, each with an endpoint, steps and a checkable
expected result. Read the result before the Agent loop tab writes any Java. The Agent loop tab's
prompt carries the cases (new-test and converted scopes) for as long as
the job's brief is the one they were designed from; after *Read it* is
run with a different story, they are not used and the Agent loop tab's log says so.
They are handed over as data, each field on one line, with a statement
that nothing in them is an instruction.

- **Specification** — paste OpenAPI / Swagger as JSON or YAML, or pick a
  file (it is read into the box in the browser). Left empty, a
  specification found in a code block of a Confluence page that the
  last *Read it* fetched is used. A Confluence *attachment* is not
  fetched.
- **Requirements**, **notes** — free text.
- **How many** — fast 25, balanced 40, thorough 80 cases at most.

What the step checks instead of trusting:

- The endpoint list is read from the specification by the program
  (`$ref` path items are followed; a Swagger 2 `basePath` or a server
  URL's path prefix is accepted in front of a path). A designed case on
  an endpoint that is not in it is kept and marked
  `[NOT IN THE SPECIFICATION]`, and is not handed to the Agent loop tab. With no
  specification, the requests in the story are shown to Cursor but
  nothing is checked against them, and `design.md` says so.
- A case with no expected result is dropped, and listed.
- A reply wrapped in prose, or with a trailing comma, is repaired and the
  log says how. A reply with no test cases in it is asked for once more
  on the same agent. A reply cut off half way gives its complete cases
  and the design is labelled **PARTIAL** — with or without `json-repair`
  installed.
- Every endpoint of the specification is accounted for: the ones no
  designed case is on are listed in `design.md` under *Endpoints with no
  test case*.
- A specification too large for one prompt is split before it is cut.
  Its paths go into parts -- each sent with only the schemas it refers
  to -- and are designed one call after another, at most six, each part
  with its share of the case limit. A part that fails is named, the
  design is labelled **PARTIAL**, and the rest is kept; a failure that
  would repeat (no key, no connection, a timeout) stops the remaining
  parts. There is a ceiling: a specification that would need more than
  six calls gets larger parts, which lose examples, then long
  descriptions, and are then cut at the size limit -- each reported for
  the part it happened to.
- The same question is not paid for twice. A reply that was read whole
  is kept in the job directory (`design-cache/`) under a hash of exactly
  what was sent, the mode and the model. Run the step again with nothing
  changed and that reply is used; `design.md` says so, with its date.
  Change anything that is sent -- the material, the notes, the speed --
  and Cursor is asked again; what is not sent (text past a size limit,
  the date the story was read) changes nothing. Each part of a large
  specification is kept as soon as it is read, so a run stopped half way
  does not pay for its finished parts again. A reply that was cut off,
  needed a second turn, or gave no usable case is never kept. Tick **Ask
  again** (`--fresh`) to forget every reply kept for the job and ask
  (nothing is forgotten when Cursor cannot be asked: no key, no SDK);
  it unticks itself after the run. A run answered entirely from kept
  replies needs neither the SDK nor a key.
- Each call to Cursor has a time limit (`cursor_deadline_seconds` in
  `policy.json`: 900 seconds for a design call; 2700 for an Agent loop tab call,
  per attempt, so twice that with the repair attempt; 0 is no limit, and
  a value that is not a number is the default). A call that does not
  come back is asked to cancel and reported as stuck, instead of the job
  showing "running" until someone kills it.
- An Agent loop tab call that fails -- times out, or errors half way -- is not
  treated as a call that did nothing. Everything checked after a
  finished run is checked after a failed one: the credential file,
  `.git` (hooks, config), files hidden from git, folders that ignore
  themselves. What is found is put back and named in the reason. A
  moved HEAD -- the agent committed or switched branch -- is NOT put
  back, after a failed run or a finished one: nothing is reverted, the
  reason says to reset the branch by hand, and Discard then puts the
  files back.
- When an Agent loop tab call times out, the tree is put back, and after a few
  seconds it is checked again. If the run wrote again, or could not be
  confirmed cancelled, the job is left **discardable**: end the Cursor
  bridge process (`cursor-sdk-bridge` / `node`) in the task manager if
  it is still there, **first**, and then **Discard**, which puts back
  anything written since. Discard works once: it restores the tree as it
  was before the run, so a second use later would take with it whatever
  you had done in between. **Stop** ends a job that is still running; it
  cannot reach a helper process the job left behind.
- Whether Discard may be used again is a setting: `"repeat_discard"` in
  `policy.json`, `false` by default. Set it to `true` (exactly that; any
  other value is off) on a machine where a timed-out run's helper
  process cannot be ended by hand. Then, for a run that was **not
  confirmed stopped** -- and only for such a run, not after an ordinary
  discard or a moved HEAD -- the button becomes **Discard again** and
  keeps working, so what the run writes afterwards can be put back as
  often as needed. Every use restores the tree as it was before the
  run: the page asks first and says so, because your own edits since
  then go with it. Commit or stash your work before using it.
- Each input has a size limit (specification 80,000 characters,
  requirements 100,000, brief 40,000, notes 20,000; 200,000 together).
  A large specification loses its examples and long descriptions before
  anything else. What was left out is in `design.md` under *Read this
  first*.

Cursor is asked in **plan mode** (it proposes; it does not edit or run)
and is started in an empty temporary directory. That is not a sandbox —
it is still a program on this machine — so three things are done as
well: the specification's server, host, contact and
external-documentation entries, the host of every URL in the material,
and every host or credential value in `program_configuration.json` are
removed from what is sent; the same values are removed from the reply
before anything is written; and the working tree is compared before and
after, with a warning if it changed. Plan mode has not been tried
against the live service: if it returns no usable reply, the page has an
*Agent* setting for this step (`--mode agent`).

The page gives this command no file path. What is pasted is written to
`design-swagger.txt`, `design-requirements.txt` and `design-notes.txt`
in the job directory, and that is the only place the command looks
unless a file is named on the command line.

Output, in the job directory: `design.md`, `design.json`,
`test-cases.csv` (one row per step, `;` separated) and `xray.csv` (one
row per step grouped by `TCID`, laid out for Xray's Test Case Importer
— map the columns in its wizard; this layout has not been tried
against a live Jira). The page links to all but the JSON.

Two packages make it better and neither is required:

```
python -m pip install -r requirements-design.txt
```

`PyYAML` reads a YAML specification (without it YAML is sent as text and
the endpoint check is skipped, and the log says so). `json-repair`
recovers more kinds of damaged reply.

```
python tools/agent/design.py --job <job> --service <name> --swagger-file spec.yaml --requirements-file brd.txt --speed fast
```

### Convert ReadyAPI

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

### Agent loop

The only tab that writes code. It takes the plan the New test case tab produced and has
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

#### What Approve & push publishes

- **Only the job's files, on top of what the remote already has.** The
  commit is built on `origin/<your branch>`, not on your local branch, so
  local commits you have not pushed are **not** published with it. If
  those unpushed commits changed one of the same files, approve is
  refused -- push them first.
- **Your checkout is not touched.** No branch switch, no change to the
  index or the working tree. The files stay where Cursor left them, as
  uncommitted changes; they come back through the pull request.
- **What is scanned is what is pushed**: the commit against the remote
  branch, plus the commit message. The pasted story is not quoted in the
  message.
- **No hooks run**, and the push goes to the branch `agent/<job>` only.
- **What you approve is what you saw.** If a file changed after the diff
  was produced, approve stops, rewrites `proposed.diff`, and asks again.

One run at a time: a convert, an agent run, an approve, a discard and a
re-apply all work on the same files, so the page (and a lock file, for
the command line) refuses to start a second while one is going.

A run that was stopped or died can still be undone: **Discard** puts
back everything changed since it started. A run that ended
"verify failed" has to be discarded before the job is run again.

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

**Check Cursor setup** runs both checks without calling the agent, and
also starts the SDK's local bridge once. The bridge is what fails on a
new machine (antivirus scanning the bundled Node binary, a blocked
executable), and it is cheaper to learn that here than in the middle of
a run.

While Cursor works, the Cursor log shows it: one `cursor:` line per
status change and per finished tool call. A failure says which kind it
was -- *did not start* (key, network, bridge) or *started but did not
finish* (prompt size, timeout) -- because the fixes are different. The
call is in `tools/agent/cursor_call.py`.

#### What is enforced, and where

These are checked against the files on disk **after** Cursor returns.
The prompt says the same things, but a prompt is advice.

| rule | if broken |
|---|---|
| writes only inside the chosen scope | job fails, every touched file is put back -- tracked files from git or from the copy of your own uncommitted version, generated files from the copy of the generated tree taken before the run |
| no deletes | job fails, the file is restored |
| another suite's generated files, or the ones every suite shares | job fails and they are put back; the message names whose they were |
| the agent does not commit or switch branch | job fails; Discard puts the files back (the branch is yours to restore) |
| nothing inside `.git` changes (hooks, config, exclude) | job fails, the files are put back |
| no file is hidden from git (assume-unchanged, skip-worktree, a folder that ignores itself) | job fails, the mark or the folder is removed |
| `program_configuration.json` / `cursor_agent.json` unchanged | job fails, put back from memory |
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

### Jira

Questions to Jira about more than one story. Read-only: nothing here
writes to Jira. The address and the token are the ones in
`program_configuration.json`; the page has no field for either.

- **Paste** a story key, several, the address of a story, the address of
  a Jira search or saved filter, or a query, and **List it**. The page
  works out which it is. An address on any host but the configured Jira
  is refused; text that is none of those is refused rather than searched
  for.
- **Check the token** asks Jira who the token belongs to. Do this first
  when a story "does not exist": an expired token looks the same.
- **Versions** lists a project's versions, newest first by the number in
  the name. Click a name to use it as the version.
- **What is in this version** lists a release; with *Compare with*
  filled in, it also shows what is only in one of the two, and what is
  in both.
- **Existing tests** lists the issues of type `Test` (or the type
  given) the project has, so a new test can be checked against them.

Every list says how much of it was read. **INCOMPLETE** (an issue moved
while the pages were read, or Jira stopped early), **LIMIT** (more than
*At most*) and, for a comparison, **NOT RELIABLE** are shown above the
list, and the run ends with a non-zero exit. Text from Jira is shown as
text; nothing from it is run as markup.

The same commands from a console are `python tools/jira/search.py ...`
(see `JIRA_XRAY.md`). None of this has been run against a live Jira; it
uses the search endpoint that Server and Data Centre have.

### Defects

For Jira bugs: **why each one happened**. A team keeps a field on its
bugs for that -- a failure reason, a root cause, whatever it is called
there -- and this tab helps fill it in and keep it consistent.

- **Load defects** — paste bug keys, a Jira search address or a query,
  or give a project (and a fix version) to take its bugs. Each bug is
  shown with the reason it has now, and with the most similar *earlier*
  bugs that already have a reason, and what that reason was. Similar is
  counted, not guessed: the words of the summary, exception names,
  status codes, paths, components and labels two bugs share, and the row
  says which.
- **Suggest reasons** — Cursor proposes a reason for each loaded bug from
  your team's list, with one or two sentences of why and a phrase quoted
  from the ticket. The page asks before sending. A suggestion is marked
  **DIFFERS from the current reason**, *agrees*, or *no reason yet*.
- **Write to Jira** — on a row, with the reason chosen in the box beside
  it. This button exists only when writing is switched on (below).

What is checked rather than trusted:

- A suggestion must be one of the allowed reasons exactly. Anything else
  is shown as "the answer was not one of the allowed reasons", with what
  it said, and is not offered.
- Every bug sent is accounted for: one the answer leaves out is marked
  **NOT REVIEWED**.
- A bug's text is somebody else's writing. It goes to Cursor as
  material, with URL hosts and every value of the private configuration
  removed; Cursor is asked in plan mode, from an empty directory; what
  comes back is redacted again. The page shows all of it as text.

**Configuration.** Nothing about your Jira is in the code. In
`src/main/resources/program_configuration.json` (gitignored), inside
`jira_config`:

```
"defects": {
  "field": "Failure Reason",
  "reasons": ["Code defect", "Test data", "Environment"],
  "issue_type": "Bug",
  "write_back": false
}
```

`field` is required: the field's name as Jira shows it, or its id
(`customfield_NNNNN`). It must be a custom field; one of Jira's own
(Summary, Assignee, Status) is refused. `reasons` is optional -- without
it, the values Jira allows for the field are used. `issue_type` defaults
to `Bug`.

**Writing to Jira.** Everything else in this workbench only reads Jira.
This is the one thing that changes it, so:

- it is **off** unless `write_back` is exactly `true` (not `"true"`,
  not `1`); with it off there is no button, and the command refuses;
- one field of one bug per click, after a question that shows the bug,
  the field, the old value and the new one. The box beside the button
  starts on the suggestion, or on *choose…* when there is none: never on
  a reason nobody picked;
- the field is the one the configuration names at the moment of writing,
  the bug must be one this job loaded and of the configured issue type,
  and the reason one of the allowed values;
- the bug is read first. If its reason has changed in Jira since it was
  loaded, nothing is written and you are asked to load again; a field
  holding several values is not replaced by one;
- it goes to the configured Jira only; a redirect is refused;
- after writing, the bug is read back and the row shows what Jira now
  holds -- including when Jira accepted the write and did not keep it;
- a request that left and could not be confirmed (a timeout, a failed
  read-back) is shown as **SENT, NOT CONFIRMED**, with the bug to check.
  It is never shown as not having happened;
- the attempt is written to `defects-applied.log` in the job directory
  before the request, and the outcome after it.

One defects command runs at a time for a job: a write is refused while
suggestions are still being fetched.

From a console: `python tools/agent/defects.py load|suggest|apply --job
<job> ...`. Loading again replaces the list, suggestions included. At
most 200 bugs are loaded (50 unless *At most* says otherwise); up to 300
earlier bugs are read for comparison. None of this has been run against
a live Jira or the real Cursor service.

### Failures

After a test run. **Record the last run and compare** reads
`target/failure-digest.txt`, keeps one small snapshot of the run in
`.failure-history/` (which git ignores), and shows:

- **New** — not failing in the run before;
- **Failing differently** — the same test with another signature: the
  fix worked and exposed the next problem;
- **Still failing the same way** (a count);
- **No longer failing** — never "fixed": the digest lists failures, not
  what ran, and the page says so when the two runs executed a different
  number of tests.

Under that, each signature of this run: whether exactly that signature
failed in an earlier run, and, for one never seen before, the closest
earlier one it **resembles** — with what the two share and that one's
note. A pointer, not a diagnosis.

**Save note** writes down what a signature turned out to be; the note
comes back whenever that signature does. Saving a note records nothing:
the comparison on screen is read again with the note in it. **Every run it was in** lists
the runs and tests a signature failed in. **Recorded runs** lists the
snapshots.

*Record* takes no file from the page: it records this project's last
run. A saved digest from somewhere else can be recorded from a console
(`python tools/failure_history.py record --digest ...`).

### Linking to a tab

The tab is in the address: `http://127.0.0.1:8787/#jira`, `#failures`,
`#agent`, `#convert`, `#new`. `?job=<name>` opens the page on a job:
`http://127.0.0.1:8787/?job=release-6#jira`. The Jira, Defects and Failures tabs show
the last result of that job when they are opened.

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
  design-*.txt           design: the specification, requirements and notes as pasted
  design-cache/          design: replies kept so an unchanged request is not asked again
  design.md / .json      design: the proposed test cases, and what was left out
  test-cases.csv         design: one row per step
  xray.csv               design: the same, grouped for Xray's importer
  jira-result.json       the Jira tab: the last Jira command's result, as the page shows it
  defects.json           the Defects tab: the loaded bugs, their text, the suggestions
  defects-result.json    the Defects tab: the same without the ticket text, as the page shows it
  defects-applied.log    the Defects tab: every write to Jira made from this job
  failures-result.json   the Failures tab: the last failure-history command's result
  cursor.log             the conversation with Cursor (design and the Agent loop tab), appended across runs
  review.json            the Agent loop tab: state, scope, the files changed, verify result
  proposed.diff          the Agent loop tab: what is waiting for review
  converted.patch        the Agent loop tab: an approved change to converted Java, as a diff to read
  pre-generated/         the Agent loop tab: the changed generated files as they were before the run
  gate-before.json       the Agent loop tab (converter scope): what failed before / after
  agent-prompt-N.txt     the Agent loop tab: exactly what Cursor was asked
  agent-answer-N.md      the Agent loop tab: what it said it did
  pre/                   the Agent loop tab: your uncommitted files as they were before the run
```

The page polls the log by byte offset, so a long convert streams and a
refresh picks up where it left off. The UI will only read back
`brief.md`, `plan.md`, `intake.json`, `locate.json`, `review.json`,
`proposed.diff`, the four design files and the two result files of
the Jira and Failures tabs — a job directory
cannot be used to read arbitrary files through the API.

## What it is allowed to run

The API never accepts a command *string*. Every runnable is named in
`jobs.py` and the caller may only pass arguments that runnable declares:

| name | does |
|---|---|
| `convert` | convert ReadyAPI suites |
| `intake` | read the pasted story and links |
| `locate` | decide create / update / upstream |
| `jira-verify` | ask Jira whether the configured token is accepted |
| `jira-versions` | a project's versions, newest first |
| `jira-release` | what a release holds, and what changed since another |
| `jira-tests` | the tests a project already has |
| `jira-paste` | list what was pasted: keys, a query, or a Jira address |
| `defects-load` | read defects from Jira, with the reason each has |
| `defects-suggest` | Cursor proposes a reason for each loaded defect |
| `defects-apply` | write one reason to one defect in Jira (off unless switched on) |
| `failures-record` | keep the last run's failures and compare with the run before |
| `failures-list` | the runs recorded so far |
| `failures-show` | every run a failure signature appeared in |
| `failures-note` | write down what a failure turned out to be |
| `audit-service-keys` | audit service keys |
| `audit-token-chain` | audit the token chain |
| `agent-design` | Cursor proposes API test cases (plan mode); takes no path, writes the job directory |
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
python tools/agent/test_design.py
python tools/agent/test_history.py
python tools/agent/test_defects.py
python tools/agent/test_mock.py
python tools/agent/check_ui.py
```

`check_ui.py` is the only one that runs the **page**: it starts the real
server on a spare port, puts real results in a throwaway job, loads
every tab in a headless Edge or Chrome and checks what the browser ends
up showing -- including that text from Jira and from test output is
shown as text. It needs a browser, so it is not in the gate; it reaches
no live Jira and leaves nothing behind. It loads pages and sends the
requests the buttons send; it does not click.

`test_loop.py` is in the gate (`verify_all`, check `agent-loop`): it is
the code that decides what may be pushed. The other three are **not** in
the usual sweep -- the repository-wide run globs `tools/test_*.py` and
`tools/ra_converter/test_*.py`, which does not reach this directory. Run
them yourself after touching `intake.py`, `jobs.py` or `locate.py`:

```
for t in tools/agent/test_*.py; do python -B "$t" || break; done
```
