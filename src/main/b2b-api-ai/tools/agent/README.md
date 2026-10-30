# The agent UI

One local page, three tabs, over the tools that already exist. No
framework, no build step, no database — Python standard library only.
The job directory on disk **is** the state, so a browser refresh loses
nothing and anything the page does can be re-run from the command line.

## Start it

**The easy way.** In the project folder (`src/main/b2b-api-ai`) there are
four files:

| | Windows | macOS (and Linux) |
|---|---|---|
| start | `start-workbench.bat` | `start-workbench.command` |
| stop | `stop-workbench.bat` | `stop-workbench.command` |

Double-click one, or run it from a console. **Start** starts the server
in the background -- no window has to stay open -- waits until it
answers, and opens the page. Run it again while it is running and it
just opens the page; it never starts a second server. **Stop** stops it.

- Stop ends only a server that says it is THIS folder's workbench.
  Another copy of the project on the same port, or any other program, is
  left alone and named.
- If the port is taken by something else, Start says by what and starts
  nothing. Use another port with `WORKBENCH_PORT` (e.g. `set
  WORKBENCH_PORT=9000` on Windows, `export WORKBENCH_PORT=9000` on a Mac)
  for both Start and Stop.
- What the server prints is kept in `.workbench/server-console.log`;
  if it does not come up, Start shows the end of that.
- A convert or a Cursor run that the workbench had started is not ended
  by Stop: it runs to its own end.
- On a Mac, the first time: if double-clicking says the file cannot be
  run, run `chmod +x start-workbench.command stop-workbench.command`
  once in the project folder. If macOS says it is from an unidentified
  developer, right-click it and choose Open.
- A workbench server that was started the old way BEFORE these files
  existed cannot say who it is, so Stop will not end it: end that python
  process by hand once.

The same from a console, on either system:

```
python tools/agent/launch.py start
python tools/agent/launch.py status
python tools/agent/launch.py stop
```

**By hand**, with the server in the terminal you start it from:

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
  "jira": false,
  "cursor": false
}
```

Both are **off as committed**: a fresh clone reaches the real Jira and
Cursor configured in `program_configuration.json` and
`tools/ra_converter/cursor_agent.json`. Set a switch to `true` to try
the workbench without them.

With a switch `true`, the workbench answers from sample data on this
machine. The buttons, the commands, the checks and the results are the
same; nothing leaves the machine. Set it back to `false` and the same button
reaches the real service. Nothing else has to change, and no restart is
needed: each command reads the file when it starts.

| switch | `true` | `false` |
|---|---|---|
| `jira` | The **Jira** and **Defects** tabs use a sample project (three releases, stories, bugs, tests) under whatever project key you type. No `jira_config`, host or token is needed. **Write to Jira** changes `.workbench/mock-jira.json` and nothing else. | The Jira in `program_configuration.json`, with its token. |
| `cursor` | **Design the tests** returns two cases per endpoint; **Suggest reasons** and **Review the change** are keyword matches; **Run Cursor** writes one placeholder test so the checks, the review and Discard can be tried. No SDK, no key, no cost. | Cursor, with the key in `cursor_agent.json`. |

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
Automate Test Case tab are fetched for real; `tools/jira/fetch.py` and
`run.py` (a single story, from a console) use the real Jira; the
converter and its own Cursor assist; the tests and the Failures tab;
and git. As shipped both switches are `true`, so a fresh copy is safe to
click through. **Switch a service to `false` when you are ready to use
the real one**, and load again: what was loaded from the sample data is
not carried over to the real service.

For one process, the environment variable `WORKBENCH_MOCK` overrides
the file: `0` turns every switch off (the tests run that way), `1` turns
every switch on. Any other value is an error.

## What the page tells you while it works

Nothing the page waits for is silent.

- **Beside the button** that started a command: what it is doing now and
  for how long -- `Asking Jira and waiting for its answer… 0:07`. Before
  the command has said anything, it is what the command is for. When it
  ends: `finished in 0:42`, or `exit 1 after 0:12 — stopped at: Asking
  Jira and waiting for its answer`.
- **Above the log**: every step so far, ticked as it is passed, the
  current one marked, the one a failure stopped at crossed.
- **The strip at the top**: a request to the workbench itself that takes
  more than a moment -- storing an uploaded file, loading a result. The
  button that sent it cannot be pressed again until it has answered.
- **The log pane** is unchanged: the command's full output, steps
  included.

A step is an ordinary line of a command's output that starts with
`==> `. A command says one with `diag.step("...")` (`tools/diag.py`);
the same step repeated in a loop is said once. To make a new command
say what it is doing, call that where its slow parts begin -- there is
nothing to register. The steps are in the command's log and in
`diagnostics.log`, so "where did it stop" can be answered afterwards.

Adding `&follow=<command>` to the address (`/?job=release-6&follow=intake`)
shows a command that is running, or has run, the way its button would --
its steps, and the time counted from when the command started. The page
does not add this to the address itself: it is for a link, or for
looking again at a long run after the page was closed.

## The tabs

### Automate Test Case

Give a Jira story (a link to it, or its key), paste request payloads,
and add Confluence links. It reads them, then decides **create / update
/ upstream / confirm**: whether
this is a new test, a change to one that exists (and whether a new row
of data is all it takes), something that belongs further up the chain,
or something a person must look at. How it decides is set out
[below](#how-it-decides-whether-a-test-already-exists).

Writes `brief.md` and `plan.md` into the job directory.

#### Giving it a Jira story

The story is given **by link or key** (the default) or **pasted**, and
the Confluence pages **by link** (the default) or **pasted** -- a pair
of choices, each showing one box. Only the box that shows is read; the
other keeps its text for when it is chosen again. When the story cannot
be read, Read it asks Jira about the token, and an expired or wrong
token is said first -- instead of coming back as a story that does not
exist. The API
specification of the design step is likewise **uploaded** (the
default), **pasted** or **fetched from a Confluence page**, and the
requirements **uploaded** (the default; a .txt or .md file, read in the
browser) or **pasted**.

Put the story's address or key in **Jira story**. *Read it* then reads,
from the Jira in your configuration:

- **the story** -- summary, description, every comment, attachments;
- **its parents**, up to three levels (acceptance criteria and the
  request contract are often on the epic);
- **what is around it** -- the stories Jira links to it (*blocks*, *is
  blocked by*, *relates to*, *clones*...) and its sub-tasks, up to 8,
  each read once and not followed further;
- **the Confluence pages the story's text points at**, up to 6, read
  as if their links had been given: a story that says "table updated,
  see [page]" names the page its tests depend on. The Confluence
  allowlist still decides whether a host is read at all, and a refused
  page is named with the reason.

The text of each **Confluence page** given with the job is read too --
its rules and its tables (a table arrives as rows of `a | b | c`) --
and goes into the brief under **Confluence pages**, up to 40,000
characters a page and 90,000 in all (a longer page is cut, and the
brief says so; the whole page is in the job's `sources/` folder). A code block longer than 1,500
characters is named and left out: a specification on a page is handed
to the design step on its own.

All of it goes into `brief.md`: the story's own text under **The
story**, and each parent and linked story under **Around the story**
with how it is related. The brief is what *Design the API tests* hands
to Cursor, and what the Agentic Test Creation hands it until a person
has reviewed the cases (see *What Cursor needs*), so Cursor sees the
story in its setting rather than alone. Pasting the story is then optional; paste only what
Jira does not hold.

What each part is used for is deliberately different:

| Read | Searched for requests | Searched for acceptance criteria | Shown to Cursor |
|---|---|---|---|
| the story | yes | yes | yes |
| its parents | named, **not taken** | yes, and said to be inherited | for the design; not once the cases are reviewed |
| linked stories, sub-tasks | **no** | **no** | for the design, marked as other stories; not once the cases are reviewed |

Only the story's own requests become requests of the job. An epic lists
the calls of every story under it, so a parent's requests are named
under *Not used* for you to paste the one that applies; a request in a
linked story is that story's to test.

Things it says rather than hides:

- **No acceptance criteria found** on the story or its parents: a
  warning in the brief and under *Not used*, not a stop. A design made
  from such a story is the designer's guess at what matters.
- **The story holds no request**: the brief says so and points to
  *Design the API tests*, which can now design from the story alone
  (its result says the endpoints were checked against nothing).
- **A linked story or a parent could not be read**, or there were more
  than 8 linked: named.
- **A Confluence page could not be read**: the brief gives the
  fetcher's reason. Open the page yourself and paste what it says into
  **Page text**; it is then read exactly like a fetched page -- for
  requests, and into the brief under *Confluence pages* for the design
  (the Agentic step keeps only its table rows once the cases are
  reviewed).
- **A link to another Jira** than the configured one is refused, and so
  is an address that cannot be read as a story's. The token is only
  ever sent to the first host in `jira_config.base_urls`, and a story of
  the same key on your Jira is not the one that was meant.
- **Jira did not answer, or answered something that is not a story**
  (a sign-in page, a cut connection): said under *Not used*. Whatever
  was pasted, and the Confluence links, are still read.
- **One story a job.** Give several keys (one a line, or separated by
  commas) and *Read it* makes one job per story, named by its key, reads
  them all at once and lists them: state, summary, requests found,
  whether acceptance criteria were found. **Open** puts a job in the box
  to go on with it. The Confluence links go with every story; pasted text
  and page text go with none (they belong to one story). Up to ten at a
  time.
- **Stories of one release** (*…or pick stories from a release*, under the
  story box). A Jira project key -- remembered per workbench project, as
  B2B's and GOAL's live under different keys -- then *Load releases*
  (newest first; *include archived* for old ones), a release and a type
  (`Story`; empty for every type), and *List its stories*: only
  `project = KEY AND fixVersion = "release" AND issuetype = Story` is read,
  never the project's every story; the query is shown over the list, and a
  list cut short says so. A story that already has a job here (one named
  by its key) says *has a job*. Tick some and *Use the ticked stories*:
  their keys go into the story box, and *Read it* makes one job a story as
  above. Read with the Jira of the project chosen on the tab (B2B / GOAL);
  changing the project or the key clears what was listed.

Text from Jira is quoted line by line in the brief and is described
there as material, not instructions. Credentials written in it --
passwords, tokens, keys, `Authorization` and `Cookie` headers, `curl -u`
and `user:password@host` addresses -- are masked before anything is
written. That is a pattern match: it catches the usual shapes, not
every way a secret can be written, so read a brief made from a story
that carries credentials before it goes to Cursor. In mock mode a sample story with a
parent and two linked stories is returned, marked MOCK.

Jira Cloud sign-in (email + API token) is still not supported: this
uses the personal access token the Jira tab uses.

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

#### Using the story's Xray tests

When the tests already exist in Jira -- written by a person, linked to
the story -- choose **Use the story's Xray tests** as the Jira story
(beside *A link or a key* and *Paste the story*): the story is read by its
link or key as usual, and **Design the tests** (now *Design from the Xray
tests*) takes them as the cases instead of asking Cursor to propose new
ones. It reads the story's issue links of the test link type
(`jira_config.publish.test_link_type`, "Tests" by default: the story
"is tested by" each test) and each test's steps from Xray (Server / Data
Center), and writes them as the job's design, one case a test, with the
test's key recorded. A test with no steps is listed as not used; an
issue of another link type, or of another type, is left out. The
direction counts: a test linked the other way round (the story "tests"
it) is listed as not used, with that reason, and a story whose every
test is linked that way is refused with their keys. The first 40 linked
tests are read; any after them are listed as not used ("not read").

Then the usual flow: review the cases, and the Agentic Test Creation
step writes REST Assured tests with the real keys in the `jira_xray_id`
column, so results report to the tests that exist. *Create in Xray*
finds each one already there and creates nothing. Read it first (the
job needs its story); a job that already has a design is refused --
Clear the design first. In mock mode the sample story has two linked
tests.

**Automated already? Updated, not written twice.** Before Automate asks
Cursor, the workbench looks for each case's Xray key in the tests that
exist: `@XrayTest("KEY")` on a method, or KEY in the `jira_xray_id` column
of a data sheet under `src/test` (`loop.automated_by_key`). A case found
in the paths the job may write is marked `[AUTOMATED IN <file>]`, and
Cursor UPDATES that test -- its row and assertions -- to the case's steps
as they are in Xray now, keeping its key. One found outside them (a
converted test) is marked so, and Cursor writes nothing for it and names
it. Every other case is CREATED, with its key in `jira_xray_id`. Automate's
log says how many were found automated already.

**Results reach Xray when the tests run.** The job's suite and the group
suites (`Suites/jira/`) list `com.hi.api.reporting.XrayReportListener`:
with `xray.enabled` on (Settings tab), each run posts its results to Xray
by their keys at the end of the run, and Publish to Xray then makes no
second Test Execution for it (it still adds the regression tests after the
push, or puts the results into an existing execution named beside it).
With `xray.enabled` off the listener does nothing, and Publish to Xray is
how results get there.

#### Update in Xray

Cases change after their Xray test exists -- a review edit, a refinement.
**Update in Xray: preview** (under Create in Xray) compares each case that
has an Xray key in three versions: **ours** (the case now), **Xray now**
(read live), and **last synced** (what the workbench last sent to the test
or took from it -- kept with the key since this version). It sends nothing.

| ours vs last synced | Xray vs last synced | proposed |
|---|---|---|
| same | same | nothing (in step) |
| changed | same | **send** ours |
| same | changed | **newer in Xray**: skip, or take Xray's steps into the case |
| changed | changed | **conflict**: skip (the default), take Xray's, or send ours |

Each row also says when Jira last changed the test, and by whom (its
changelog), and whether that was after our last sync. The times compared
are both Jira's: right after each create, import, send or take, the
workbench records the `updated` Jira gives the test. Jira counts any edit
(a comment, a link, a status), so the time is shown and used for keys kept
before snapshots; the steps themselves decide.

**Send the chosen** reads each test again first: one changed in Xray since
the preview is skipped and said, never overwritten. Before a test is
overwritten, what Xray had is added to `xray-update-backup.json` in the
job. Steps are edited in place (POST `.../test/<key>/step/<id>`), extra
ones added, surplus ones removed; the summary follows the case's title, and
a test the workbench created gets its description rewritten too (one taken
from the story keeps its own). "Take" changes the case -- as a review
correction, so Finalize must be pressed again -- and nothing in Jira.
Sending is refused only when `jira_config.publish.create_tests` is `false` (Settings: Create in Xray).
Xray Server / Data Center only; the step calls are Xray Server's REST API
and have not been run against a live Xray.

#### How long a design takes

One Cursor call: a few seconds to start, the model thinking (60-100 s with
a reasoning model such as grok-4.6), then writing the JSON (35-45 s for
15,000-18,000 tokens, about 28 cases). The design, Manual tests, Defects
and the code review can use a model of their own -- a faster one -- set as
`designModel` in `tools/ra_converter/cursor_agent.json`:

```
{"apiKey": "...", "model": "grok-4.6", "designModel": "composer-2.5"}
```

The Cursor log names the model of each call. Fewer cases (speed "fast")
also means less to write.

A specification too large for one call is designed in parts. Part 1 is
asked alone -- a bad key or a dead network then fails once -- and the
others three at a time (`designParallel` in `cursor_agent.json`, 1 to 6).
A part that fails while asked alongside the others is asked once more,
alone. The cases are put together in part order, whatever order the
answers came in. With images attached the parts go one at a time.

cursor-sdk's bridge re-runs a request when the connection sits idle after
the answer ("the same request ran again" in the Cursor log). These calls
therefore end as soon as one whole JSON answer has come ("the answer is
complete: the run is ended here").

#### Clearing a design

A design stays with the job -- reload the page and it is there -- and
so do the Cursor replies it was made from: a design asked again with
nothing changed reuses the kept reply instead of paying for it twice
(tick *Ask again* to ask anyway). **Clear the design** takes the cases,
`design.md`, the CSVs, the design's log and the kept replies out of the
job, into `design-cleared-<time>/` beside them, so the next design asks
Cursor afresh. Review decisions and Xray keys stay -- they name issues
that exist in Jira -- and the next design is matched to the cleared
one, so a case that comes back keeps its id, its decision and its test.
What was pasted or uploaded for the design stays too.

#### When the story changes

A job reads its story once, and everything after is made from that
reading. **Has the story changed?** (under the story box) reads the
story again and compares: Jira's revision of it, and the story's own
text, shown as a line-by-line diff. When it has changed, the answer
also lists what in the job was made from the earlier reading -- the
design, the review, tests created in Xray, a change waiting for review
-- so you know what to redo. It changes nothing itself.

Taking the change in: *Read it again* -- the button under the answer
reads the story with the links this job was given the first time and
the text and page still pasted in its folder, so nothing has to be
typed again -- then *Design the tests* again.
A re-design is matched to the design before it: a case that says the
same thing (title, preconditions and steps) keeps its id, and with it
its review decision and its Xray test. A case on the **same endpoint
whose words are clearly alike** is the same case re-worded -- a model
rarely repeats itself to the letter -- so it keeps its id and its Xray
test, but its review goes back to *proposed* with the note "re-worded
by the design of ...; was approved -- decide again": one tick to
confirm, and no decision is carried over to words nobody read. A case
that matches nothing is new: it gets an id nothing in the job has used
-- not the earlier design, not a review decision, not an Xray test --
and comes back as proposed. A decision is about a case, not a number: once the cases are
renumbered, the old decisions count for nothing, the flow strip and the
Agentic run say the cases must be reviewed again, and a decision sent
for a case that is gone is refused. Cases that are gone are named in
the design's warnings; an Xray test made for one of them stays in Jira
until you close it (nothing here edits or closes an issue), and the
Xray block lists it under *Tests for cases no longer in the result*.
Until you design again, *Create in Xray* refuses the old design the
same way the Agentic run does, and says so. Then review the new cases, *Create in
Xray* for them, and on the Agentic tab ask Cursor to change the
automated test with what changed. The design before the last one is
kept as `design.previous.json`, so a design that fails does not lose
the match.

#### How it decides whether a test already exists

*Then: is it already automated?* answers one question per request of
the story, and writes the answer and its evidence to `plan.md` (and, for
programs, `locate.json`). No model is asked: it is a comparison against
what is in this folder.

| Decision | Means | Stops the job? |
|---|---|---|
| **CREATE** | nothing automated makes this call this way: write a new test | no |
| **UPDATE** | a hand-written test covers it: add to that test | no |
| **UPSTREAM** | a *generated* test covers it. A change there is overwritten by the next convert, so it belongs in the ReadyAPI XML | yes |
| **CONFIRM** | it cannot be told from here -- a likeness, a likely duplicate, or data that is already there. A person decides | yes |

A stop is the step working. The Agentic Test Creation tab is handed `plan.md`, so
what is decided here is what gets written.

**What a request is compared with, in order:**

1. **The converted suites** (`tools/jira/shape_match.py`). Each request
   is reduced to a *shape* -- verb, path with its parameters as
   `{name}`, the names and types of the body's fields, the names of the
   query parameters -- and looked up in the shape index, which holds the
   same for every call in the ReadyAPI sources. Same shape, one test:
   UPSTREAM. Same shape and the same expected status in several: a
   likely duplicate, CONFIRM. Alike but not the same (a field more or
   fewer): CONFIRM. A path pasted with real values in it
   (`/props/ABCDE/groups`) is first read against the recorded templates;
   only a path none of them fits is then read against the templates
   hand-written tests use.

2. **The hand-written tests** (`tools/agent/coverage.py`). They are not
   in the shape index, so they are read from the committed code on every
   run -- there is nothing to rebuild and nothing to go stale. A call is
   recognised in these ways, and the plan says which:

   | Found as | The code says | How the endpoint is known |
   |---|---|---|
   | a written path | `.post("/guests/{guestId}/businesses", body)` | it is there |
   | a client call | `client.shopGroups(token, code)` | the generated client, or `_audit/<suite>/facade_endpoints.md` |
   | a step of the DSL | `.enrollOwner()` | followed through the DSL to the client call behind it |
   | a helper | `addBusiness("x")`, a method of the same class | whichever of the above the helper is |

   A request nothing generated covers, that a hand-written **`@Test`
   method** makes, is UPDATE. The match is by verb and path -- the code
   does not show the body, and the plan says so. A step of the DSL, a
   helper or a set-up method that no test uses is not coverage; it is
   named as a note, so that the new test is written with it rather than
   with a second copy of the call.

   A helper is followed one level: a helper that calls another helper
   is not. When hand-written tests of *different* paths all fit what was
   pasted, or the path fits several templates and none can be chosen,
   the answer is CONFIRM.

   Two things are deliberately not credited, because the name is all
   there is to go on. A client method whose name means **more than one**
   endpoint across the converted suites (`create`, `read`) credits none
   of them. A DSL step with a name any object might have (`get`, `build`,
   `with`) is never followed: `map.get("k")` is not a step.

   Which folders count as hand-written tests is `handwritten_roots` in
   `tools/agent/policy.json`. It is deliberately not all of `tests/`:
   the framework's own unit tests are there too, and cover no story.
   Generated folders are never read, whatever the list says, and
   neither is a folder that links out of the project.

3. **The data.** The CSV of each test a request matches is opened (each
   file once; the first 12, and the plan says when there were more) and
   one of these is said under *the data* in the plan:

   | Finding | Means |
   |---|---|
   | **The data is already there** | every field of the request has a column, and a row holds the same values and the same expected status. On an UPDATE this becomes CONFIRM: adding it again is a duplicate |
   | **The same inputs are there with another expected status** | a row holds the same values and expects something else. On an UPDATE this becomes CONFIRM: one set of inputs with two expectations is for a person to settle |
   | **A new row is enough** | every field has a column and no row has these values. The test does not change; its CSV gets a row |
   | **A row alone is not enough** | the request carries a field no column holds -- or two fields of one name that would need one column, or an expected status the file has no column for. What the test sends for those is fixed in its template or its code, so the test changes too |
   | **Not compared** | no data file, the file is not on this machine (generated files are not committed), or the request carries no values |

   A field is paired with a column by **name**: `peakRooms` with
   `peakRooms`, `tpl_peakRooms`, `Properties.peakRooms`. Values filling
   a pasted path (`ABCDE` for `{propCode}`) are compared too. `5` and
   `5.0` are one value; `01234` and `1234` are not. A `null` in the body
   is a field like any other. Rows with `execute` set to `N` do not
   count.

4. **A story with no request in it.** When the story's text gave no
   request and *Design the API tests* has been run for the job, the
   endpoints of the designed cases are located instead -- run *Then: is
   it already automated?* again after designing. A designed endpoint has
   no body, so it can only be matched as a call: one a generated test
   already makes is CONFIRM, never permission to write. A design made in
   mock mode is not used. Requests found in the story always win.
   Designed cases whose endpoint cannot be read as `VERB /path` are
   counted at the top of the plan, not dropped in silence.

**What it cannot tell you -- read these before trusting a CREATE:**

- A client call or a DSL step can only be turned into an endpoint when a
  **converted suite is on the machine**. The plan lists every `@Test`
  method it could read no call from -- for that reason, or because the
  path is held in a constant or built by `String.format`. A CREATE does
  not rule those tests out.
- Names are all the data check has. A column named differently from the
  field is not found, and the finding then reads *a row alone is not
  enough* when a row might do.
- A hand-written match compares no body. Two tests of one endpoint with
  different payloads look the same to it.
- A path assembled in code (`"/accounts/" + id + "/members"`) is known
  only by how it starts. It is noted as a likeness and decides nothing.
- The shape index is a snapshot. After a convert, rebuild it.

#### Design the API tests (optional)

The lower half of the Automate Test Case tab. *Read it* and *locate* answer "is this
automated already?". This answers "what should be tested?": Cursor is
given the API specification, the requirements and `brief.md`, and
proposes test cases, each with an endpoint, steps and a checkable
expected result. Read the result before the Agentic Test Creation tab writes any Java. The Agentic Test Creation tab's
prompt carries the cases (new-test and converted scopes) for as long as
the job's brief is the one they were designed from; after *Read it* is
run with a different story, they are not used and the Agentic Test Creation tab's log says so.
They are handed over as data, each field on one line, with a statement
that nothing in them is an instruction.

- **A story gets a design made for it.** When the job has a story (or
  pasted text, or the requirements box) that names endpoints of the
  specification, only those endpoints are designed, every case tied to
  the story -- in one call, unless even that part of the specification
  is too large for one. Without this a large
  specification is cut into parts by size and a story about three
  endpoints comes back as forty cases of which a handful concern it.

  An endpoint is recognised when the story writes its path
  (`/guests/{guestId}/accounts/{accountId}/members/{memberId}/stays`,
  with or without real values), the end of it (`GET .../spendDetail`),
  or calls its last word an endpoint ("the bookingpolicy endpoint").
  Case and plurals are ignored. The result lists which paths were
  chosen and why, so a wrong guess is visible; add `GET /path` lines to
  the requirements box to name others. When nothing is named, the whole
  specification is designed and the result says so.

  Tick **Design the whole specification, not only what the story
  touches** for every endpoint.
- **The service name**, left empty, is the specification's own title.
- **Specification** — OpenAPI / Swagger, JSON or YAML, from one of
  three places:
  - **pasted** into the box. What is pasted always wins.
  - **From a file.** Choose a `.json`, `.yaml` or `.yml`. The page sends
    the file; the workbench keeps it as
    `.workbench/<job>/spec/<its own name>` and the design step reads it
    from that path. The page shows the path, the format and how many
    endpoints it found. It is kept only if it IS a specification (it has
    a `paths` section) -- anything else is refused with the reason.
  - **From a Confluence page.** Paste the page's link and press *Fetch*.
    The specification is looked for among the page's **attachments**,
    read from the approved Confluence only, and kept in the same place.
    If exactly one attachment is a specification, and every other
    candidate was read and found not to be one, it is taken. Otherwise
    none is chosen for you -- several fit, or one could not be read, or
    is YAML with no `PyYAML` here, or has a name that cannot be used for
    a file, or the page has more attachments than the first 200, or more
    than 8 candidates. They are listed with what was found, and you put
    the name of the one you mean in the attachment box and fetch again.

  One specification is kept per job; a new one replaces it, a failed
  attempt leaves the earlier one in use, and *Remove* forgets it. A file
  name may hold letters, digits, spaces and `. _ - ( ) +`, up to 100
  characters; the limit is 20 MB. With the box empty and no file kept, a specification found
  in a **code block** of a Confluence page that the last *Read it*
  fetched is used, as before. YAML needs `PyYAML` installed.
- **Requirements**, **notes** — free text.
- **How many** — fast 25, balanced 40, thorough 80 cases at most.

What the step checks instead of trusting:

- The endpoint list is read from the specification by the program
  (`$ref` path items are followed; a Swagger 2 `basePath` or a server
  URL's path prefix is accepted in front of a path). A designed case on
  an endpoint that is not in it is kept and marked
  `[NOT IN THE SPECIFICATION]`, and is not handed to the Agentic Test Creation tab. With no
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
- A run that has said or done something and then sends nothing for
  `cursor_quiet_seconds` (600 by default; 0 switches it off) is taken
  as finished: it is cancelled, what it said so far is its answer, and
  what it wrote is verified like any other run. Seen live: Cursor wrote
  the test, summed up, then stayed silent for 18 minutes until the hard
  limit -- which then undid everything it had written.
- A run whose last status from the SDK is "error" but which had
  answered (seen live: the whole summary of the files it wrote) keeps
  its answer; what it wrote goes to the verify step, which decides. The
  error is on the record in the Cursor log. Only a run that ends in
  error having said nothing is a failure.
- The same holds for the time limit: when it comes after Cursor has
  answered and the run is confirmed cancelled, the answer is kept and
  what was written is verified. A run the limit ends before any answer,
  or that cannot be cancelled, is a failure and its files are put back.
- Each call to Cursor has a time limit (`cursor_deadline_seconds` in
  `policy.json`: 900 seconds for a design call; 2700 for an Agentic Test Creation tab call,
  per attempt, so twice that with the repair attempt; 0 is no limit, and
  a value that is not a number is the default). A call that does not
  come back is asked to cancel and reported as stuck, instead of the job
  showing "running" until someone kills it.
- An Agentic Test Creation tab call that fails -- times out, or errors half way -- is not
  treated as a call that did nothing. Everything checked after a
  finished run is checked after a failed one: the credential file,
  `.git` (hooks, config), files hidden from git, folders that ignore
  themselves. What is found is put back and named in the reason. A
  moved HEAD -- the agent committed or switched branch -- is NOT put
  back, after a failed run or a finished one: nothing is reverted, the
  reason says to reset the branch by hand, and Discard then puts the
  files back.
- When an Agentic Test Creation tab call times out, the tree is put back, and after a few
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
`test-cases.csv` (one row per step, `,` separated, so it opens in columns in Excel) and `xray.csv` (one
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

#### What the cases were made from

Above the review, **Sources** lists every story the job read -- the story
itself and each linked one, with how it is linked -- and the Confluence
pages, specifications and pasted text that went into the design, each with
its size and when it was read. It is read from the job's `intake.json`,
`brief.md` and `design.json`, so it is the same after a restart.

#### The developer's change: read, designed from, checked against

The **Developer's change** tab (beside the story, at the top) takes the
repository (GitLab or GitHub, `https://` or `git@`), the developer's
branch, the ticket (empty: the story key) and, if it is not the default
branch, the branch it merges into. They are kept with the job
(`devcode-input.json`).

**Reading it** (`devcode.py --read`, or **Read the change** on the tab):
the workbench fetches the branch into `.workbench/devrepos/` -- a bare,
blobless copy: nothing is checked out and nothing runs, and a file's
content is fetched the first time it is read and kept -- one fetch at a
time per repository. It takes the commits whose message names the story
**or one of its sub-tasks** (whole keys: ABC-12 is not ABC-123); when the
branch has already been merged, those commits where they now are; when
none names a key, all of the branch's own commits. A branch deleted after
its merge is read from its merge request (GitLab) or pull request (GitHub),
which the host keeps. From the change it reads: endpoints added, changed
or **removed** (Spring and JAX-RS annotations, OpenAPI YAML or JSON);
**who may call them** (`@PreAuthorize`, `@Secured`, `@RolesAllowed`,
`hasRole`/`hasAuthority`, role enums); status codes; validation; new and
removed fields; enum values; error codes and **messages**; parameters; the
repository's own specification before and after; the developer's own
tests. Around it, within a budget of files read whole at the branch's end:
the endpoints a changed request or response class is used by, and the
whole rules of the classes the changed endpoints take and give. The
merge or pull request's title and description come too. The reading is
`devchange.json`; a branch still at the same commit is not read again.

**Design the tests** reads the branch again first (only what is new is
fetched) and puts the reading in the request as a section of names and
numbers -- never the diff -- with the rule that the code shows what was
built and the story what is wanted: where they disagree the difference is
an open question, never a case that expects what the code does. Its
endpoints join the story's focus. `design.json` keeps the commit it was
designed from. A reading that fails is said, and the design goes on
without it. Only Java, Kotlin and OpenAPI files are read for behaviour; a
change in another language is said so.

**Check the cases against the change**, under the review, is the
make-sure step after the design: the same reading is compared with the
cases; what no case covers is a **gap**, with a severity (an access rule
no case calls as its roles, or with no case expecting 403, is one). It
says when the branch has moved since the design. Tick **also ask Cursor**
to send the change (cut to size, host names and private values removed)
and the cases to Cursor in plan mode as well, for gaps the program's
reading cannot see.

When `devcode_config.base_urls` is set in `program_configuration.json`,
only those hosts are read. git's own credential helper answers for the
host; `devcode_config.token` there, when set, is sent instead -- as a
header, never in a URL, and never shown -- and also to look up the merge
or pull request. github.com and a host with "gitlab" in its name are
known; any other host is named in `devcode_config.hosts`:

```json
"devcode_config": {
  "hosts": {"git.example.com": {"kind": "gitlab", "api": "https://git.example.com/api/v4"}}
}
```

(GitHub Enterprise: `"kind": "github", "api": "https://host/api/v3"`.)
**Add cases for the chosen gaps** is a refinement of the design that keeps
every existing case and adds cases for the gaps ticked. The result is
`devcode.json` / `devcode.md` in the job. On Windows a project in a very
deep folder can be too deep for git: move it to a shorter path.

```
python tools/agent/devcode.py --job <job> --repo <url> --branch <branch> [--ticket ABC-123] [--base develop] [--read | --ask-cursor]
```

#### Refining one case, some, or all

Every row of the review has a tick in its first column (the header's tick
selects them all). With cases ticked, **Approve selected**, **Reject
selected** and **Refine selected** act on those only; a refinement then
names the ticked cases to Cursor as the ones to change and keeps the rest
exactly as they are. With none ticked a refinement is of the whole list,
as before. A single case is refined by ticking only it.

### Run tests

Compile and run from the page. **Compile** is `mvn test-compile`. **Run
the tests** runs one of: **this job's new tests** (the suite the
workbench wrote for the job open on the page, `Suites/jira/<job>.xml`,
chosen for you when it exists), a suite file (every file under `Suites/` and
`src/test/resources/testng*.xml`), a class or method (`-Dtest=`, e.g.
`GuestProgramAccountTest#create*`), or a TestNG group -- through its
`Suites/jira/group-<group>.xml`, since surefire ignores `-Dgroups` when a
suite file is set -- against an environment of the chosen project's
configuration file. Maven runs offline first and goes online
only when an artifact is missing. A `JAVA_HOME` that points at no JDK is
replaced by a JDK 17 or later found on the machine, and the log says
which. **Clean build first** (on by default; always for the change's
Run the tests on the Agentic tab) deletes what Maven built and the last
run's reports -- `target/classes`, `test-classes`, `maven-status`,
`surefire-reports`, `allure-results`, `allure-report` -- and compiles
from nothing; not `mvn clean`, which would also delete the uploads in
`target/convert-input`, the failure digest and the Jira fetches. After
every run, passed or not, `mvn allure:report` builds `target/allure-report`
(the first time online, to fetch the Allure command line), and the result
shows **Open the Allure report**: the workbench serves it on its own port
(the workbench's + 1, e.g. 8788), another origin, because the report runs
scripts and shows the APIs' responses; the workbench's API answers only
its own port. A report that cannot be built is said; the run's verdict
stands. The result is passed / failed / skipped counts and a table of the
failures, read from the surefire reports; it is `mvnrun-result.json` in
the job (`runs` when no job is chosen). One run at a time, and not while
Cursor is writing a change.

**Project** (B2B / GOAL) says whose configuration file the run reads:
`program_configuration.json` for B2B, `program_configuration.goal.json`
for GOAL. It decides the **Environment** list (that file's blocks), the
environment chosen first (the Settings tab's run environment of that
project), and the run's `-Dconfig.project` and `WORKBENCH_PROJECT`. It
starts as the project of what is run: a converted suite's own (by its
name, as the flow catalog splits them), else the open job's (a story
suite, a class or method, a group). Change it and the environments follow;
choose something else to run and it starts from that one's project again.
The converted suites are listed by project, the chosen one's first.
Running a converted suite with the other project is warned of, on the
page, in the log and on the result -- not refused -- since its calls were
converted for that project's services. A project that is not one is
refused before anything starts.

```
python tools/agent/mvnrun.py compile --job runs
python tools/agent/mvnrun.py test --job runs --suite Suites/jira/group-smoke.xml --env stg --compile-first
python tools/agent/mvnrun.py test --job runs --test FooTest --env stg --project goal
```

#### The story helpers' own check

Story tests call shared helpers (`StoryTestBase`, `GoalStoryTestBase`, `Db`,
the token code) that only story tests run. The converted suites make the same
calls their own way, so a helper broken against the real systems shows up
first in a story, after AI Agent wrote it. That happened twice: on b2b-stg the
one-time-code lookup bound a bigint id as text, and in GOAL the search left
out `peakRooms`. Run each project's helpers once before a story meets them:

| project | suite (Run tests tab, choose the suite) | what it makes |
|---|---|---|
| B2B | `src/test/resources/testng-helpers-b2b.xml` | two guests and a program account: enrol, create, owner code and confirm, activate, invite key, add, activate and promote a member |
| GOAL | `src/test/resources/testng-helpers-goal.xml` | a group: search, create, pay, confirm (200) |

Run it after a change to those files, a starter body (`templates/manual/`) or
the configuration, and on a new environment. The first step that fails is
named (`SMOKE/SETUP: ...`), and nothing after it runs.

#### Fix Failures

Under a run with failed tests the tab offers **Fix Failures**. It says which of the two existing ways applies, and why
(`runask.py`):

| the failing tests are | the request goes |
|---|---|
| the job's own: its `Suites/jira/<job>.xml` was run, or every failing class is in that suite or in the change waiting for its review | to that job's **Ask Me** (`agent-followup.txt`, `loop.py followup`): Cursor changes the waiting change, or starts one |
| one converted suite's (`com.hi.api.tests.imported.<suite>.`) | to a **fix brief** -- what Fix with Cursor on the Failures tab writes (`fixbrief.py`), from THIS run's failures -- in a new job `fix-<suite>-<time>`, run as the Migration tab runs one (scope `converted`) |
| anything else (another job's tests, several suites, a mix) | nowhere: the tab says what to do instead |

The box is prefilled with each failed test, its case id, why it failed and
the run's own lines about it (the failure digest when this run wrote it,
surefire's `[ERROR]` lines). Edit it before sending; a screenshot can be
pasted in. Values from the configuration files are hidden before it is
shown and again before it is sent, and a configuration file that cannot be
read refuses the send. Nothing is sent while another run, convert or
Cursor run is going, or when the last run had no failed test. **Open the
job to review it** takes you to the Commit and push tab of the job the
request went to.

**The run is made again by itself.** When the fix Fix Failures started ends
and its change passed its check (waiting for review), the workbench runs
the failed run again, in the server (`jobs.rerun_after_fix`), so the page
may be closed. The re-run uses the same suite, test or group, environment,
project and threads, with a clean build first. For a suite or a group, only
the tests that failed run again: a copy of the suite file under
`target/run-suites/<name>.failed.xml` names them (`mvnrun.py --failed-only`,
`mvnrun-rerun.json`). The copy keeps the suite's parameters, listeners and
groups. A failed `@Before`/`@After` runs its whole class again. A class the
copy cannot place runs the whole suite instead, and the log says so. A failed
compile is compiled again. The run is NOT made again when the change failed
its check, when the fix was stopped, when someone ran the tests by hand
while AI Agent worked, or when another run is going. The tab says why
(`run-ask-rerun.json`). The re-run never carries `--auto-fix`. Whatever
still fails waits for you and never goes back to AI Agent by itself.

A **compile that failed** (Compile, a run's compile first, or a run that
did not compile) offers the box too: no test ran, but each error names its
file, and the files say whose they are.

| the errors are in | the request goes |
|---|---|
| the job's own files: its suite's classes, or any Java file of the change waiting for its review (a helper under `src/main` too) | to that job's **Ask Me**, prefilled with the errors (`file:line: message`) |
| files the converter writes (`com.hi.api.support`, a converted suite's tests), and the open job's waiting change broke them -- its own check failed on them (`verify-failed`), or the job's own files fail too | to that job's **Ask Me**: Cursor changes what the job changed (a shared helper) so they compile again, and leaves the generated files alone |
| a converted suite's own files (`imported/<suite>/`, `support/<suite>/`, its client, its templates), and no waiting change broke them | to **convert-fix**, what runs after a convert (`convertfix.py`): Cursor makes each suite compile in a fix job of its own (`<job>-fix-<suite>`, scope `converted`). Each fix waits for review only once its suite compiles. A suite whose fix job is already waiting is not asked again. The fix is lost on the suite's next convert: when the converter is the cause, fix the converter |
| shared generated files (`support/*.java`, `support/scenario/`) | nowhere: the tab says to convert again (a stale generated wrapper is removed on the next Compile by itself) or to fix the converter |
| anything else (another job's files, a mix, no file named) | nowhere: the tab says what to do instead |

**Cursor fixes compile errors** is ticked by default. It sits next to
*clean build first* on this tab, and next to *Compile* on the Commit and
push tab. With it ticked, the compile or run carries `--auto-fix`. When it
ends with errors that go to a job or to convert-fix, the workbench itself
sends them (`jobs.auto_fix`), without a click or a confirm. That happens
in the server, so the page may be closed. What was sent, or why it could
not be, is kept beside the run (`run-ask-auto.json`). Both tabs show it and
follow its log. The fix still waits for review on the Commit and push tab.
Failed tests are never sent by themselves, and neither are errors that go
nowhere. Untick it to look at the errors first.

A compile fix is checked against the files that failed. They are listed in
`agent-mustfix.json`, which the follow-up reads once. The follow-up's
before-compile is of the tree with the change already in it, so without
that list those files counted as "already broken" and a fix that fixed
nothing passed its check.

### Manual tests

Manual UI test cases from requirements, written as a **delta** against
the tests the team already keeps in Jira / Xray: what is new, and which
existing tests a changed rule makes wrong. Cursor reads and proposes
only. Nothing on this tab writes to Jira.

**What it reads**

- **Requirements files** -- choose up to 8. The page sends each file; the
  workbench keeps it as `.workbench/<job>/docs/<its own name>` and
  reads it from there, and shows the path. Text (`.txt .md .csv .json
  .log`), HTML and Word (`.docx`) are read with what Python ships. PDF
  needs `pypdf` and Excel needs `openpyxl`; a file that cannot be read on
  this machine is listed as unread with the reason, not passed over.
- **Notes** -- free text.
- **The story** read on the Automate Test Case tab for the same job, if any.
- **Existing tests** -- *Read existing tests* takes a project key, an
  issue type (default `Test`), optionally a release (`fixVersion` -- only
  that release's tests, not the project's whole history; past the 200 below
  a project's list is cut short) and optionally one further condition, and
  reads up to 200 tests with their Xray steps (Server / Data Center; when
  Xray gives no steps the test's description is used, and the page says
  so; steps that could not be read because Jira refused or did not answer
  are counted and said). The step format has not been checked against a
  live Xray: two shapes are read. Optional: without existing tests there
  are no updates and no check for duplicates. A failed read keeps the
  list read before; the result says which project's list it compared
  with and when that was read.

**What comes back** -- on the page, and as `manual-tests.md`,
`manual-new-cases.csv` (one row per step) and `manual-xray-updates.csv`:

- **New test cases** -- title, module, category, priority, preconditions,
  steps with data and expected result, the document it comes from, and
  why no existing test covers it.
- **Existing tests to update** -- the test's key, why, and what to change.

**What is checked instead of trusted**

- An update must name a test that was **sent**. A key Cursor makes up, or
  one of a test that did not fit, is dropped and counted. With no
  existing tests read, there are no updates whatever the reply says.
- A case needs a title and at least one step. It is marked when no step
  says what should be seen, when its source is not one of the documents
  sent, or when its title is the same as an existing test's.
- The number of cases is capped by the speed chosen (15 / 30 / 60).
- What did not fit is said above the results: a document cut short
  (30,000 characters each, 120,000 in all), notes or a story over 8,000
  characters, lines that were not sent because they begin like this
  tool's own markers (`-----`, `=====`), existing tests that were not
  sent, a list of tests that is not the whole of the project's, a reply
  that was cut off (no update can be read from one).
- Of a Word file the body is read, as it reads NOW: text deleted with
  changes tracked is left out, and headers, footers, footnotes and
  comments are not read (said when the file has any). HTML is read
  against a clock; a page that takes too long is read in part and said.
- Documents and tickets go to Cursor fenced as material, with the private
  configuration's values and the hosts of URLs removed, in plan mode in an
  empty directory. The same is taken out of what is WRITTEN -- Cursor's
  reply and the titles of existing tests alike. The report and the CSVs
  are plain text: nothing in them becomes a link, a mention or a formula.

```
python tools/agent/manualtests.py inventory --job <job> --project ABC [--version 6.02]
python tools/agent/manualtests.py generate  --job <job> --app "Group booking"
```

### Migration from ReadyAPI

*(Was "Convert ReadyAPI".)* Converts ReadyAPI project files into this
project, and below the converter, **Ask Me**: say what should be
different, and choose what Cursor may change -- this suite's converted
Java (kept on this machine, re-applied after every reconvert of the
suite) or the converter itself (committed and pushed). A job's first
request becomes its brief and plan; a request while a change is waiting
adds to it. A job that holds a story or a design is refused: a migration
request is its own job. Fix with Cursor on the Failures tab writes the
request for you -- leave the box empty and press Ask Me. The change
waits on the Commit and push tab.

The converter itself: The page shows the
handful of options a person sets; everything else keeps the converter's
own default. The command that ran is in the job's status file, so a run
can always be repeated in a terminal.

**Choosing the files.** Press *Choose Files* and pick one suite XML, or
several. A browser does not tell a page where a chosen file lives, so the
page cannot pass the converter a path -- it sends the file. The workbench
writes it to `target/convert-input/<its own name>` (gitignored) and gives
the converter that path. The name is kept because the suite is named
after it. The path is written with forward slashes and is relative to
this project, so it reads the same on Windows and on a Mac. Choosing
again replaces what was chosen before; *Remove* forgets it. Only a
`.xml` that starts like XML is taken, up to 300 MB a file.

| On the page | What it sends |
|---|---|
| ReadyAPI project XML | `--input` -- the stored file, or several, comma separated |
| Workbooks folder | `--data-dir` (see below) |
| Suite name | `--suite-name`; empty takes it from the file name |
| Diagrams: As configured / Also render images / Diagrams only | nothing / `--diagram-png` / `--diagrams-only` |
| Phase specs: On / Off | `--phase-specs` / `--no-phase-specs` |
| Cursor assist: Off / On | `--no-cursor-assist` / `--cursor-assist`. **On calls the real Cursor**, whatever `mock.json` says |
| Clean first, Classic mode, Skip self-test | `--clean`, `--classic`, `--skip-self-test` |
| Verify afterwards | when the convert finishes cleanly, runs the checks chosen in **What to verify** and shows their log under the convert's. Not run after a failed convert, and the page says so |
| What to verify | **Converter checks** (`verify_all.py --area converter`, the default: all a convert can break), **Workbench checks**, or **Everything** (slow). **Verify** runs them now, without a convert |
| Does it compile? | After every convert (and **Check it compiles**): `mvn test-compile`. The server starts it when the convert ends (`jobs.check_after_convert`), so a page closed or reloaded meanwhile does not skip it; the end of the convert's log says it started, or why not (`convert-check.json`). The convert ends with a **NEXT** block naming each suite it wrote and the XML to run it with (`Suites/<Suite>_Regression.xml`), and lists them in `convert-suites.json`; the check names them again, and when they compile, says the XML to run. Only those suites go to Cursor: a suite with errors this convert did not write is named and left alone (nothing runs until the whole project compiles). With **Cursor assist** on, each of them whose own files do not compile is handed to Cursor in a job of its own, `<job>-fix-<suite>` (the "converted suite" scope): Cursor fixes, the tree is compiled, Cursor is asked again while the errors change, and the change waits for review on the Commit and push tab ONLY once that suite's files compile; otherwise it ends `verify-failed`. Errors in shared framework files are said and left to a person. Cursor assist off: checked and said, nothing fixed (`tools/agent/convertfix.py`) |
| Cursor log | The tab's Cursor log shows the convert's own Cursor call as it happens -- what it was asked, each tool it used, what it answered, or why it was NOT asked (nothing the convert was unsure of; no key) -- and the conversation of every fix after it |

Not on the page, and left at their defaults: `--package-root`, `--envs`,
`--config`, `--cursor-config`, `--service-name`, `--diagram-cases`,
`--max-name-len`, `--bootstrap`, `--skip-dataflow-check`,
`--keep-dead-props`, `--no-reapply-patches`. `--output` is not shown
either and is always this project (`.`): the converter's own default is a
folder called `output`. All of them still work from the command line.

A fresh start of the workbench opens a new, empty job (`job-<date>-<time>`):
nothing of an earlier job shows. The job in use is kept in the address
(`?job=`), so a refresh stays on it; earlier jobs stay on disk and are
offered in the job box's list.

With the job box empty, the Convert tab runs as job `convert`.

`--data-dir` points at the folder of `.xlsx` workbooks the ReadyAPI XML
names, and is how DataSource columns get real values instead of empty
cells. It is **optional** — a suite whose project has no DataSource
workbooks needs none — so the form marks it *advised*, not required,
and will run without it. When the suite does read workbooks, leaving it
out empties every DataSource column, and the run then looks like an API
problem rather than a missing path, which is why the form says so.

### Commit and push

*(Was "Agentic Test Creation". Cursor is now run where the work starts:
**Automate the cases** at the bottom of the Automate Test Case tab --
a case's own Automate button, or every approved case at once -- and
**Ask Me** on the Migration from ReadyAPI tab. All of a job's runs
make one change; this tab is where it is reviewed and pushed. The
Cursor setup is checked as every run starts; there is no button for it.
What this section says about running Cursor, its log and the follow-up
box now happens on those two tabs.)*

The change Cursor made for the job waits here: its files, its diff, a
code review. **Approve & push** commits it to its own branch and pushes
it (a converted suite's change is kept on this machine instead);
**Discard** puts everything back; **Dismiss** puts an outcome away.
**Stop** stops what is running from this tab: the code review at once;
Discard or Approve only after you confirm, since stopped half-way
Discard has put back some files (press it again), and Approve may have
made its commit or branch without pushing it.

#### Automate the cases

The prompt of every run carries **the part of the job's specification
the cases need**: the paths of the cases' endpoints, and the schemas,
parameters and responses they refer to (through `$ref`s), so that a
request body -- its required fields, its allowed values -- is built from
the contract rather than guessed. The specification is the one the
design used (pasted, uploaded, fetched from Confluence, or found in the
story's pages); hosts and servers are never sent; an endpoint not in it
is named so Cursor says so. With no specification nothing is sent.
Cases taken from Xray get their endpoint from their steps (`GET
/path`), checked against the specification the same way.

Each case of the review table has an **Automate** button (enabled once
it is approved and marked Automate); **Automate the approved cases**
does every such case in one run. Cursor is handed only those cases. When
a change is already waiting, the run adds to it -- the cases written
before stay -- so a job has one change however many runs it took. Each
row then says what its last run came to (written, does not compile,
rejected). **Ask Me to change it** sits beside, for anything the
cases do not say.

**The repository, and where the branch goes.** The strip at the top of
the tab reads git each time the tab is opened: the branch checked out,
whether the tree is clean, the remote Approve & push sends to (by name
only: its address is never shown or sent to the page), and how far the branch is from its upstream. **Switch to
it** checks out another branch -- only on a clean tree, with no agent job
running, and with the name typed again; a branch known only on the
remote is checked out tracking it. Under the review controls, **Build
on** is the remote branch the job's branch starts from (the one checked
out, by default) and **Push as** its name (`agent/<job>` by default; it
must keep the `agent/` prefix, so that no one's main line can be named).
Both are passed to Approve and said in its confirmation.

The switch needs no *changed tracked file* other than
`tools/agent/mock.json` (edited on every machine where the real Jira
and Cursor are; git carries it across when both branches hold the same
version of it, and says so when not). Untracked files never stand in
the way. When another tracked file is changed the strip names it and
the button says so: commit, stash or discard that file first.

**Asking Cursor to change what it wrote.** Under the review controls,
*Ask Me to change it* takes your words -- "use the member's email
from the CSV instead of a fixed one" -- and runs Cursor again on the
same job: same scope, same paths, with your ask and the diff so far in
front of it. The result is verified and waits for your review again as
one change: the diff below shows the first run and every follow-up
together, Approve pushes it all, and Discard puts the tree back to how
it was before the first run. Each ask is kept in the job's record
(`followups`). It is not offered for a change the stand-in made. A
follow-up that fails -- Cursor did not answer, or it wrote outside the
allowed paths -- undoes itself and nothing more: the change you had
already read is put back as it was and waits for review again, with the
reason under `followup_failed` in the job's record. A file you made by
hand inside the agent's paths stops a follow-up before it starts, the
way it stops a run, so that it is not swallowed into Cursor's change.

**Reading the Cursor log while it runs.** Each line is one thing the
agent did: `tool shell started: command=mvn -q compile` says what it
asked for, `tool shell completed in 38s -> BUILD SUCCESS…` how long it
took and the start of what came back (shell calls only; what a file
read returns is not logged), `tool edit_file started: target_file=…`
which file it is changing (never the content), `cursor says: …` what
the agent said in words, and `status FINISHED` when it is done. When
nothing has been heard for a minute, a line says so -- `still working:
nothing from Cursor for 2 min (last: tool shell started: command=mvn
test), 9 min in all` -- which is most often a long compile, not a hang.
The agent's private reasoning is not logged.

Cursor is told **where things are** before it is told where it may
write: where the hand-written and the converted test classes are, which
TestNG suite files there are, where the data sheets are and how one is
laid out, where the steps, the clients and the request bodies are, and
which of all that is generated. It is read from the folders as they are
when the run starts, with counts and the names of the suites converted
on the machine. The same text is written to the Cursor log as its own
block -- *what Cursor was told about where things are* -- because the
prompt itself is cut short there. All Java (and converter) changes go through Cursor;
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

**Uncommitted work never blocks a run.** It is listed as a warning, each file with whose it is (another job's change waiting for review, or not from a workbench job). Cursor's change is told from yours by content: a file it does not edit is not part of its change. Every one of your files is copied before the run (in a follow-up too), so Discard puts YOUR version back -- not the last commit. If Cursor does edit one of your files, the change -- and a push -- carries your edit too.

| scope | Cursor may write | verify | Approve does |
|---|---|---|---|
| **A new hand-written test** | anywhere in this project folder (`**`): nothing is off limits. What still holds is not a path: the credential files (`program_configuration.json`, `cursor_agent.json`) and `.git` are put back after every run, and a file git ignores (the converter's output) can be changed but is never pushed. A scope may still be given limits: a root starting `!` takes a path out, and the most specific root decides | compile + duplicate-method check | commits to branch `agent/<job>` and pushes it |
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

Each run is one Cursor call, and then, while the result does not verify
(the compile, a check, the gate), another call with the output handed
back and the instruction to fix only what it reports. It keeps going
**until it verifies**, with two stops: `repair_max_attempts` in
`tools/agent/policy.json` (8 by default), and an attempt that leaves the
failing output exactly as it was -- Cursor going round in circles -- after
which no further attempt is made. The log says which, and the Agentic
tab's state says `verify-failed` with the number of attempts. Set
`repair_attempts` to a number for that many repairs instead.

A change of tests (new tests, a converted suite: `clean_build` in
policy.json) reaches review only after a **clean build**: what Maven built
is deleted first and the project is compiled from nothing, and any file
the change touched with an error fails it -- even a file that was already
broken before. Errors in files it never touched are still "already
broken" when the tree did not compile before. A repair attempt first
compiles only the changed files, so a failing attempt is quick.

#### The suite of a job's tests

When Cursor finishes a new-test job, the workbench writes
`Suites/jira/<job>.xml`: a TestNG suite of the test
classes the job wrote under `tests/jira/`, so the story's tests run on
their own --

```
mvn -o test -DsuiteXmlFile=Suites/jira/<job>.xml
```

It is part of the change: in the diff you review, pushed with Approve,
put back with Discard. `testng-jira.xml` still runs every story's tests
together.

#### Which suites a case's test runs in

Every designed case is mapped to TestNG groups, and each group is a
suite. The rules are in `tools/agent/suite-map.json`: every case gets
`jira` and `regression`; a positive or flow case of high priority also
gets `smoke`; a negative or boundary case `negative`; a security case
`security`. Edit the file to change them. The review table shows each
case's groups in its **Suites** column; type others there to set them by
hand (empty the box to go back to the rules).

Cursor is told each case's groups and puts exactly those on its test
method. When it finishes, the workbench writes the suite file of any
group that has none yet -- `Suites/jira/group-<group>.xml`,
every test of the `tests.jira` package in that group, so no class list
to keep up to date -- and warns in the log when a group a case was
mapped to is on none of the change's tests.

```
mvn -o test -DsuiteXmlFile=Suites/jira/group-smoke.xml
python tools/agent/suitemap.py --job <job>      # each case's groups
```

The existing `testng-smoke.xml` and `testng.xml` list their classes by
name, so they do not pick up the story tests; the group suites do.

**Suggest suites with Cursor** (under the review) sends the cases --
id, type, priority, endpoint and title, nothing else -- and the groups the
project already has to Cursor, which proposes the groups of each case with
a reason and a confidence. Each suggestion appears under its case's
Suites box with **Use**; **Use every suggestion** takes them all. Nothing
changes until you choose, and the box stays yours to edit. A group no
suite has yet is marked new. The result is the job's `suite-suggest.json`.

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
`.workbench/<job>/cursor.log`, shown in its own pane and separate from
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

Four things keep a run short, all in the prompt. Once a person has
reviewed the cases and the brief is long (20,000 characters or more),
the brief handed to Cursor is **the story, the requests, and the table
rows of the Confluence pages**: parents, linked stories, the pages'
prose and the sources were material for the design, and the approved
cases carry what came of them; the tables (field names, types, allowed
values, formats) stay because a case's `data: valid values` is short
for them. The prompt names what was left out, how much, and where the
whole brief is (`.workbench/<job>/brief.md`), to be opened only when
a case names something not explained. With nothing reviewed the whole
brief goes, since then the cases are not vetted; `agent_brief: "full"`
in `policy.json` keeps the whole brief always. Cursor is told **not
to run a build of its own**: the workbench compiles the moment it
finishes and hands back the errors, so a `mvn` it starts is only a wait.
The scope's **skill file goes into the prompt whole** (`skill` in
`policy.json`, e.g. `.cursor/skills/jira-to-restassured/SKILL.md`, up
to 16,000 characters), with the rule that where anything else differs
from it on layout, naming, data and assertions, the skill wins. Its
commands to run (`mvn ...`, `python tools/...`) are marked `NOT YOURS`:
the workbench runs the checks when Cursor finishes. Seen live without
that: told to follow the skill, Cursor ran the repository's whole gate,
a workbench test failed on its machine, and it set about fixing the
workbench's tests and README -- outside its paths, for twenty minutes.
The prompt also names `tools/`, `.cursor/`, `AGENTS.md`, `CURSOR.md`,
`README.md` and `pom.xml` as never to touch, and says that the
repository's agent instructions do not apply to the job. It used to be only pointed at, with an example
test from another folder inlined instead; Cursor built from the example,
read the skill later, found they disagreed and did the work twice. Only
when the scope has no skill file is **one existing hand-written test**
shown instead (the smallest that has a `@Test`, or the files
`scopes.new-test.pattern_example` in `policy.json`), with the first lines of a
data sheet under `csv/manual/` -- the committed location; the other
`csv/` folders are gitignored because their rows carry customer data,
and are never put in a prompt. The log names what was shown. And a failed compile
comes back as **a list of `file:line: message`** (each once, at most
40) with a short tail, not sixty lines of build output.

**Reading a slow run.** The log carries one `backend request #N` line
per request the bridge makes to Cursor's backend. One turn is one
request; a second request in the same run is a retry after a failure,
and the bridge waits before it -- seconds at first, then minutes (its
backoff triples each time and caps at five minutes) -- and runs the turn
again from the start, so Cursor is seen re-reading the same files. A
run that takes thirty minutes for two cases has usually spent twenty of
them in these retries, not thinking. The `sdk:` lines are the SDK's own
log and the `bridge:` lines are the bridge's; a 429 or a 5xx from
Cursor's backend shows there. Those are Cursor's side (the API key's
limits, or capacity): check the key's usage on Cursor's dashboard, try
another model id, or pause before running again.

While Cursor works, the Cursor log shows it: one `cursor:` line per
status change and per finished tool call, and what Cursor says in words
as `cursor says:` -- gathered whole, since it arrives a few letters at a
time. When nothing has arrived for a minute the log says so, with the
last thing seen; `cursor is writing: ...` there means Cursor is in the
middle of a reply and the words so far are shown. A failure says which kind it
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

#### Review the change

**Review the change** asks Cursor to read what it just proposed and list
what looks wrong, before you approve. It is on whenever a change is
waiting (PENDING REVIEW or VERIFY FAILED). You give it nothing: it reads
the job's `proposed.diff`, the same text shown under it.

What comes back is a table -- severity, file and line, what is wrong and
why, the line itself, a suggested change -- and `code-review.md`, the
same as a report you can attach to a merge request.

- **It is advice.** It does not change the job and Approve does not look
  at it. No findings is not a sign-off: the reviewer is a model of the
  same family as the author, and it sees the diff, not the service.
- **A finding has to quote a line of the change.** The quote is looked
  up in the diff; the line number shown is the diff's, not the model's.
  A finding whose quote is not in the change is listed apart as
  *Unconfirmed*; one about a file that is not in the change is dropped
  and counted.
- **What was not read is said first.** A file counts as read only when
  the reply gives it a verdict of *ok* or *findings*. Listed above the
  findings as NOT REVIEWED, with the command exiting 1: a file the reply
  does not mention, or says it *cannot judge* from the change alone; a
  binary file or a change of mode (nothing to read, or not text); a part
  of the diff that does not add up; a file whose diff is too long for
  one question; whatever is beyond what one review reads (six questions
  of about 40,000 characters); a reply that was cut off. So exit 1 means
  "part of the change went unread", not "problems were found" -- a
  review that read everything exits 0 whatever it found.
- A reply that arrived whole but in JSON that had to be mended is used,
  with a note saying so.
- A review that was stopped, or that failed before it could write a
  result, leaves a line saying nothing was read rather than an empty
  panel.
- **A review belongs to one run and one diff.** After another Run
  Cursor, or when Approve has written the diff again because files
  changed, the page says the review is not of the change on screen.
- **It does not report style, naming or formatting.** The prompt asks
  for bugs, tests that assert nothing or the wrong thing, test data that
  will collide, swallowed failures, and credentials in the change.
- The diff goes to Cursor with the private configuration's values and
  the hosts of its URLs removed, fenced as material, in plan mode in an
  empty directory. Each review is one or more paid Cursor calls; it is a
  button, never automatic. Its time limit is `review` under
  `cursor_deadline_seconds` in `policy.json`.

#### From the command line

```
python tools/agent/codereview.py    --job <job>
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
  given) the project has, so a new test can be checked against them --
  only that release's when *Version* is filled in.

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

#### Fix with Cursor

A signature card has two buttons. **Fix with Cursor (this suite's
Java)** writes the job's `brief.md` and `plan.md` from what the run left
behind -- the failing (test, row, signature) lines of the latest
recorded run (or the digest in `target/`), surefire's message and stack,
the tail of the case's request/response log under `logs/`, the
converter's notes on the case from `_audit/<suite>/` (which ReadyAPI
case it is, its data row, what was skipped, placeholders, preflight),
and the note a person attached to the signature -- then opens the
Agentic tab on scope *Converted Java of one suite* with the suite set.
From there it is the usual run: Cursor, compile and checks, review,
Approve (the change is stored and re-applied after every reconvert) or
Discard. **Fix the converter with Cursor** writes the same brief with the
plan for the emitter, scope *The converter itself*.

One suite a job: a signature that several suites share is refused until
the suite is named in the Agentic tab's Suite box (the box is read only
after such a refusal); the brief then takes that suite's cases and names
the others as left out. The digest names a test class by its simple name
and the converted tree holds the same simple name in several suites, so
the class is told apart by what surefire ran and by the suite's own
audit mapping, and refused -- naming the suites -- when neither says.
Everything quoted from the run is redacted, hosts included. A brief
longer than one prompt can carry drops whole cases from the end and
names them. A job that already holds a story, a design, a review, Xray
keys or a plan of another kind is refused -- a fix is its own job. The command is `python tools/agent/fixbrief.py --job J
--signature ID [--suite S] [--scope converted|converter]`, or `--test
<part of the name>` instead of a signature.

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

### Review the cases

Between a proposal and anything that acts on it, a person says which
cases stand. Under the design on the Automate Test Case tab, and under
the cases on the Manual tests tab, each case has **Approve** (ticked:
approved; unticked after a tick: rejected), **Automate** (design only:
Cursor should write this one), a **title** that can be corrected in
place, and a **note**. *Approve all* and *Reject all* do the lot. Each
decision is saved as it is made, in the job's `case-review.json`.

What the decision does:

- **Create in Xray** creates approved cases only, with the corrected
  title, and refuses while none is approved.
- **Agentic Test Creation** hands Cursor every proposed case until
  anyone has decided anything; once a case has been approved or rejected,
  only the approved ones marked Automate go, the prompt says that these
  and no others are to be written (a request of the plan with no
  approved case gets nothing), and the log says how many were left out.
  A job whose design cannot be used -- made from an earlier brief, or by
  the stand-in -- or whose review left nothing to automate does not run:
  Cursor would otherwise be handed the plan alone and write a test for
  every request in it, the rejected ones included.
- Nothing is deleted from a result; a rejected case stays in
  `design.md`, marked rejected on the page.

The strip at the top of the Automate Test Case tab shows where the job
is: Read it, Is it automated?, Design, Review, Create in Xray, Agentic
Test Creation -- each ticked when the job holds what that step
produces, and each a link to where that step is done (the last one
opens the Agentic Test Creation tab). It is read from the job's files,
not remembered by the page.

### Create in Xray

Under the design on the Automate Test Case tab, and under the cases on
the Manual tests tab: **Create in Xray as Jira test cases**. It creates
one Jira issue of the Test type per case, in the story's project, with
the case's steps as Xray steps, linked to the story (*tests* / *is
tested by*). The keys Jira gives back are kept in the job
(`xray-tests.json`) and shown beside the cases.

**The keys then reach the automated tests.** The Agentic Test Creation
step hands each designed case to Cursor with its key, as `[Xray
ABC-901]`, and tells it to put the key in the `jira_xray_id` column of
the data row it writes (or `@XrayTest("ABC-901")` on a test that takes
no row). After the run it checks that every key handed over is in a
file that was written, and says which are not. The framework's result
listener reports by that column, so the run's results reach the right
Xray test.

The same rules as Publish:

- **On** unless `jira_config.publish.create_tests` is `false` (Settings: Create in Xray);
  `projects`, when set, is where it may create.
- **The story key is typed twice**, and tests go in the story's project
  as Jira has it. The page says beforehand how many would be created.
- **Approved cases only** (the Review step), with the title as corrected
  there. It refuses while no case is approved.
- **Nothing a stand-in made** goes to a real Jira; a case holding a
  private value stops the whole run before anything is sent.
- **Every key is written down the moment it comes back**, and the
  attempt is on record before the first write: a run that dies half way
  keeps what it got, and the next run creates only the cases that have
  no key. A test deleted in Jira is created again, and said.
- **Nothing is edited.** A case whose text changed since its test was
  created is reported as changed, not updated.
- **A key follows its case, not its number.** A regeneration that
  numbers the cases differently does not create a second test for a case
  that already has one: the case is found by its text and the key moves
  with it (the result says so). A result that gives one id to two cases
  is refused before anything is sent.
- **A test the story already has is not created again.** Before
  creating, the tests linked to the story are compared with the cases:
  the same title (case, spacing and punctuation aside) or the same
  steps means the same test -- its key is kept for the case, so the
  data sheet and the results go to it ("already there, not created").
  A case whose title only looks like a linked test's (most of its words
  in common) is **held**: neither created nor given the key. The result
  lists each one with *it is ABC-12* / *a new test* / *decide later*,
  and **Go on with these choices** runs Create in Xray again with them
  (`--adopt TC-003=ABC-12`, `--create-anyway TC-004`). A test this job
  already holds is never given to a second case.
- **Said beforehand**: the block tells you when the switch is off, when
  Jira here is a stand-in, and when the result was made by a stand-in
  and will not be sent to the real Jira.
- **Steps or the link refused** by Jira do not lose the issue: the
  result says what is missing for each.

**Xray Server / Data Center only, for now.** Steps are added through
Xray's own REST API (`PUT /rest/raven/1.0/api/test/<key>/step`), which
Xray Cloud does not have; Cloud is refused with the way round (import
`xray.csv` or `manual-new-cases.csv`). The step call has not been run
against a live Xray. If yours answers differently, set
`jira_config.publish.xray_step_call` (e.g. `"POST
/rest/raven/2.0/api/test/{key}/steps"`); `test_issue_type` (default
`Test`) and `test_link_type` (default `Tests`) are there for a Jira
that names them differently.

In mock mode the stand-in creates `XYZ-9001`-style keys in a file on
this machine, and those keys are never handed to a real agent run.

### Publish

Puts a job's results where the team works: a file attached to a Jira
issue, or a new Confluence page. **This is the tab that writes to Jira
and Confluence.** What it writes is seen by other people and cannot be
taken back from here, so it is built to do one small thing at a time and
to say exactly what happened.

**Off until switched on.** In the gitignored `program_configuration.json`:

```json
"jira_config":       { "publish": { "attach": true, "projects": ["ABC"] } },
"confluence_config": { "publish": { "create_page": true, "spaces": ["QA"] } }
```

`attach` and `create_page` must be exactly `true`. `projects` may be
left out (any project the token may write to); when it is there it must
be a list of project keys -- a restriction that is mistyped is an error,
not "no restriction". `spaces` is required: a page is only ever created
in a space that is listed. Both addresses must be `https`.

Opening the tab asks, each time, what is switched on **now** and which of
this job's results exist; until the answer is in, what was found last
time is shown marked as such and the buttons are off. The confirmation
you are shown ("MOCK" or "this writes to Jira now") comes from that
answer, and the command is told what you were shown: if `mock.json` was
changed in between, it writes nothing.

**Attach a file to a Jira issue**

- Choose one of the job's own result files (`design.md`,
  `test-cases.csv`, `xray.csv`, `manual-tests.md`,
  `manual-new-cases.csv`, `manual-xray-updates.csv`, `code-review.md`)
  and give the issue key. You are asked to type the key again.
- It adds one file, named `<job>-<file>`. It never removes or replaces an
  attachment, and refuses when the issue already has one of that name.

**Create a Confluence page**

- Choose what the page is made from (manual test cases, the API test
  design, or the code review), the space, the **numeric id** of the
  parent page (the `pageId` in its address), and a title. You are asked
  to type the title again.
- The parent must exist and be in that space. It creates one new page
  and never edits one: if the space already has a page with that title,
  it refuses.
- The page is built by the workbench from the result file, with every
  word escaped: nothing a model, a document or a ticket said becomes
  markup on the page.

**Both**

- Only a result is published. What a failed step leaves behind is not
  one, and neither is a result with nothing in it.
- A result made by a stand-in (mock), or from sample data, or that does
  not say how it was made, is not sent to a real Jira or Confluence.
- What is about to be sent is searched for the private configuration's
  values and hosts, and refused if any is in it -- searched as the words
  were before the report escaped them (`tok_abc` is written `tok\_abc`
  in Markdown, `a&b` is `a&amp;b` on a page).
- The token goes only to the configured address, in a header. A write is
  not followed through a redirect.
- `publish.log` in the job directory gets a line before each write and
  one after, and is shown on the tab as the *Record of writes*. From just
  before a write until its answer is in, the result says "started,
  outcome not known" -- so a command that was stopped or died is on
  record as possibly done. When the service did not answer, or answered
  unclearly, the result says the write **may** have happened and to look
  before trying again -- not that it failed. Checking what is switched on
  never removes the record of the last write.
- In mock mode both change a file under `.workbench/` and contact
  nobody. Confluence follows the `jira` switch in `mock.json`; add
  `"confluence": true|false` there to set it separately.

```
python tools/agent/publish.py status --job <job>
python tools/agent/publish.py attach --job <job> --key ABC-12 --file manual-tests.md --confirm ABC-12
python tools/agent/publish.py page   --job <job> --what manual --space QA --parent 12345                                      --title "Booking: manual tests" --confirm "Booking: manual tests"
```

### Logs

For finding out what went wrong. Everywhere else the page shows an
unexpected error only by its kind -- an error's text can quote what was
being read, and that output is served to the page. The rest is kept here.

- **This job** -- `.workbench/<job>/diagnostics.log`. Every command run
  for the job: when it started and with what options, how long it took,
  its exit code, **what it said when it failed** (its own log is written
  over by the next run; this is not), and the **full traceback of every
  error**, including the ones a command handled and carried on from.
  The same error at the same place is written out five times in a run
  and then counted.
- **The web server** -- `.workbench/server.log`. Requests that failed,
  every request that starts or changes something, refusals with their
  reason, tracebacks of faults in the server, and errors in the page's
  own script (a button that "does nothing" usually left one here).
  What the page asks for over and over while it waits is left out.
- **Maven test run log** -- `target/b2b-full-run.log`, with a short
  diagnosis when its timestamp is older than the last Surefire results
  (or Surefire never ran). Log4j writes it only when TestNG starts inside
  Surefire; `Run the tests` passes `-Dlog.level=debug`. For a manual run:
  `mvn -o test "-DsuiteXmlFile=src/test/resources/testng-jira.xml"
  "-Denv=stg" "-DfailIfNoTests=false" "-Dlog.level=debug"`.
  What it holds besides the framework's own lines:
  - `RUN - TEST START` / `TEST END` for every test outside the converted
    suites (which log their own), with its case id, Xray key, story,
    environment and time;
  - a `==== TEST FAILED` block for EVERY failed test: why, each failed
    assertion, the line, the calls it made, the last request (body
    redacted) and the answer (`RunLogListener`, registered in
    META-INF/services -- a convert keeps it there);
  - a story test's setup: `step <name> -> HTTP 200`, `SETUP ... enrol a
    guest -> guestId ...`, `ACT ...`;
  - `WORKBENCH STEP [design|xraytests|loop|...]`: the START and END of each
    command that makes or runs tests, with what it said it did (cases
    designed, Xray keys created / found / held, files written, verified,
    PENDING REVIEW). A test run recreates this file, so the same lines are
    kept in `.workbench/activity.log`;
  - `WORKBENCH FAIL`: a failed command, with the steps it took and the
    errors it handled before it failed. The page checks' throwaway jobs
    (`check-ui-*`) are kept out.

*Only errors* keeps each line that reports a problem and the lines of its
traceback.

**Credentials are taken out before a line is written**: every credential
value in `program_configuration.json`, the Cursor key, and anything
shaped like `Bearer <token>` or an `Authorization:` header. If the
configuration cannot be read, the log says so on its first line. Host
names, issue keys and file paths ARE written -- that is what makes the
log useful -- so treat these files as you would the job directory: they
are under the gitignored `target/` and are not for sharing as they are.

Each file rolls over at 2 MB and keeps one earlier copy (`.1`).

| `WORKBENCH_LOG` | |
|---|---|
| not set | on |
| `off` | nothing is written |
| `debug` | also every request the page makes while polling |

Commands run from a console write to the same job file. The record never
causes a failure: a log that cannot be written is skipped.

Covered: every command behind the tabs (`intake`, `locate`, `design`,
`loop`, `codereview`, `defects`, `history`, `patches`, `specsource`,
`manualtests`, `publish`, `tools/jira/search.py`,
`tools/failure_history.py`), the command runner and the server. Not
covered: the converter itself (`tools/ra_converter/`), which keeps its own
logs under `target/`.

### Linking to a tab

The tab is in the address: `http://127.0.0.1:8787/#jira`, `#failures`,
`#agent`, `#convert`, `#new`. `?job=<name>` opens the page on a job:
`http://127.0.0.1:8787/?job=release-6#jira`. The Jira, Defects and Failures tabs show
the last result of that job when they are opened.

#### Change the workbench

At the bottom of the Logs tab, Cursor can change the workbench itself: this
page, its server and their tests (`tools/agent/`, the "workbench" scope of
`policy.json`). It never changes `policy.json` or `mock.json` -- the rule
book it is held to; an edit there is put back and the run rejected.

- Its own job, `workbench-<date>-<time>`, one change at a time; a request
  while a change waits is added to it.
- It runs only while nothing else runs, and nothing else starts until it has
  finished: every other command is started by, or reads, the files it
  changes. A workbench job runs only Ask Me, Review, Approve and Discard.
- It never changes `policy.json`, `mock.json` or `check_workbench.py` (the
  checker it is verified by). A change to the files that decide what later
  runs may do -- jobs.py, loop.py, workflow.py, the tests -- is named at
  review: read those parts closely.
- Before review the change must pass `python tools/agent/check_workbench.py`:
  every Python file compiles, `ui/app.js` parses, the workbench's quick tests
  and the page checks pass. (`test_loop.py`, about two minutes, is not run: run
  it yourself before approving a change to `loop.py` or `cursor_call.py`.)
- **Review it** opens the change on the Commit and push tab. After Approve &
  push, Stop and Start the workbench to run the changed server; refresh the
  page for the changed page.
- The files on disk change as soon as Cursor writes them, so a broken page
  can stop you from pressing Discard. From a terminal:

```
python tools/agent/loop.py discard --job workbench-<date>-<time>
```

### Screenshots for Cursor

Every box whose words go to Cursor takes a pasted image as well: Ctrl+V a
screenshot into Refine with Cursor, the design notes, the manual tests'
notes, Ask Me, the Migration tab's request, the Run tests tab's Ask Me
to fix the failures, or Change the workbench. Up
to 4 images of 5 MB each (PNG, JPEG, GIF, WebP -- read from the bytes, not
the name). They go with that request's first message to Cursor, as images
it looks at, and nothing keeps them: the run reads them into memory when it
starts and deletes their files at once (`paste.py`, under
`.workbench/<job>/paste/` for those few seconds). A request that does not
start removes them. A design asked with images is never answered from the
kept replies, which never saw them.

### The workflow, stage by stage

The strip at the top of the Automate Test Case tab is the job's workflow:

    Read it -> Generate -> Review -> Finalize -> Xray tests -> Automate
    -> Regression -> Run on stg -> Push -> Publish

Each stage is a link to where it is done, and the server holds the gates
(`workflow.py`, `jobs.workflow_gate`), whatever the page does:

| Stage | Done when | It unlocks |
|---|---|---|
| Finalize | **Finalize the cases** (under the review): the approved cases -- titles, steps, suites, Automate -- frozen. An edit afterwards reopens it. | For a job with designed cases: Create in Xray, Automate, Ask Me, a follow-up, Run the tests and Approve |
| Regression | **Confirm the regression cases** (Commit and push tab): the automated cases in the `regression` group, for the change as it is now -- a follow-up or another Automate needs it again. Publish adds exactly these to the regression Test Set / Plan. | Run the tests |
| Run on stg | **Run the tests**, on the environment chosen beside it (the Settings tab's by default): every test passed (a row switched off in its sheet aside; an attempt retried to a pass is not a skip). It can be run again after the change is approved or pushed; only a run while it waits for review opens the push. **Compile** beside it compiles the project and lists the errors in the change's own files first. | Approve & push -- for a run on the Settings tab's environment, of exactly these files |
| Push | **Approve & push** to `feature_<story key>` (`feature_<job>` without one; `feature_<key>_<job>` when the story's branch exists already). | Publish to Xray |
| Publish | **Publish to Xray**: any run, passed or not -- a failure is a result Xray should hold too (the push still needs a green run; publishing does not). A new Test Execution with the run's results (publishing the same run again as a new one is refused), or, with a key in the box beside the button, the results put into that **existing** Test Execution (its summary, description and plan left as they are; it must be a Test Execution). Once the change is pushed, the confirmed regression cases are added to the regression Test Set / Plan (Settings tab); before that they are not -- publish again after the push. If adding them fails, the execution is still on record. | -- |

### Settings

**Theme**: Dark, Light or Blue, from the switch at the top right of every
tab; remembered by the browser.

**Cursor**: the API key, the model that writes the tests (Automate, Ask
Cursor; `composer-2.5` by default) and the design model (designs,
refinements and reviews; empty: the model above). Saved into
`tools/ra_converter/cursor_agent.json` (gitignored) as `apiKey`, `model`
and `designModel`, where the converter and the workbench read them; the
file's other settings stay as they are. The key is never shown again, and
when the `CURSOR_API_KEY` environment variable is set the page says it is
used instead. A change applies to the next Cursor call; nothing needs a
restart.

The Jira, Confluence and Xray connection of this machine, saved into the
active environment's block of `src/main/resources/program_configuration.json`
(gitignored) -- `jira_config.base_urls/pat`, `confluence_config.base_urls/pat`,
`xray.baseUrl/token/projectKey/regressionKey/enabled` -- where the readers
already look. A token is never shown again: the page says "set, N
characters" or "not set". An empty token box keeps the token; "remove the
saved token" removes it. On Xray Server / Data Center the Jira token is the
Xray token: tick "use the Jira token for Xray". Each save keeps the file as
it was in `.workbench/settings/` (the last five). Nothing is saved while a
command runs: a Cursor run guards that file. **Check the Jira token** asks
Jira who the saved token belongs to.

## Where the state lives

Everything is under **`.workbench/`** at the module root (gitignored),
one folder a job. It used to be `target/agent/`, until a `mvn clean`
could not delete the running server's console log -- and would have
taken every job with it had it succeeded. On start the server and the
launcher move whatever is still under `target/agent/` across, so
nothing is lost; a file an old server still holds open stays behind
until that server stops. `mvn clean` is safe again.

Everything is under `.workbench/<job>/`:

```
.workbench/<job>/
  intake.json            what was pasted, with credentials redacted
  brief.md               the story as the tools read it
  plan.md                create / update / upstream / confirm, and why
  locate.json            the matching evidence behind that verdict
  <runnable>.log         full output of each command
  <runnable>.status.json state, exit code, and the argv it ran
  design-*.txt           design: the specification, requirements and notes as pasted
  spec/                  design: the specification kept as a file (chosen, or from a Confluence attachment) and where it came from
  diagnostics.log        Logs: every command run for the job, and the full text of every error
  publish-result.json    Publish: the outcome of the last write (or the refusal of one)
  publish-status.json    Publish: what was switched on and publishable, when last asked
  publish.log            Publish: every write to Jira or Confluence attempted from this job
  docs/                  Manual tests: the requirements files chosen for the job
  manual-*.json/.md/.csv Manual tests: the existing tests read, the result, the report and the CSVs
  design-cache/          design: replies kept so an unchanged request is not asked again
  design.md / .json      design: the proposed test cases, and what was left out
  test-cases.csv         design: one row per step
  xray.csv               design: the same, grouped for Xray's importer
  jira-result.json       the Jira tab: the last Jira command's result, as the page shows it
  code-review.json / .md the Agentic Test Creation tab: Cursor's review of the proposed change, and the report
  defects.json           the Defects tab: the loaded bugs, their text, the suggestions
  defects-result.json    the Defects tab: the same without the ticket text, as the page shows it
  defects-applied.log    the Defects tab: every write to Jira made from this job
  failures-result.json   the Failures tab: the last failure-history command's result
  cursor.log             the conversation with Cursor (design and the Agentic Test Creation tab), appended across runs
  review.json            the Agentic Test Creation tab: state, scope, the files changed, verify result
  proposed.diff          the Agentic Test Creation tab: what is waiting for review
  converted.patch        the Agentic Test Creation tab: an approved change to converted Java, as a diff to read
  pre-generated/         the Agentic Test Creation tab: the changed generated files as they were before the run
  gate-before.json       the Agentic Test Creation tab (converter scope): what failed before / after
  agent-prompt-N.txt     the Agentic Test Creation tab: exactly what Cursor was asked
  agent-answer-N.md      the Agentic Test Creation tab: what it said it did
  pre/                   the Agentic Test Creation tab: your uncommitted files as they were before the run
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
| `locate` | decide create / update / upstream / confirm |
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
| `manual-inventory` | read a project's existing tests with their steps (Jira, read-only) |
| `manual-generate` | Cursor proposes new manual test cases and updates to existing ones (plan mode) |
| `publish-status` | what is switched on for publishing, and which results this job has |
| `publish-attach` | attach one result file (by name) to one Jira issue; the key twice; **writes to Jira** |
| `publish-page` | create one Confluence page from a result; the title twice; **writes to Confluence** |
| `spec-confluence` | take the API specification attached to a Confluence page into the job directory; a link and a name, no path |
| `verify-all` | run the checks of the gate (`tools/verify_all.py`); takes only `--area` from the page (verify_all refuses an area it does not know). A converter job's own gate runs `--area converter` (policy `gate_area`) |
| `agent-design` | Cursor proposes API test cases (plan mode); takes no path, writes the job directory |
| `agent-setup` | check the Cursor SDK, the key and git |
| `agent-generate` | Cursor makes the change in the chosen scope; guardrails; verify |
| `agent-review` | Cursor reads the proposed change and lists what looks wrong; takes no path, changes nothing |
| `agent-approve` | approve: push branch `agent/<job>`, or keep converted Java locally |
| `agent-discard` | undo what the agent wrote |
| `agent-patches` | list the approved changes stored for re-applying |
| `agent-reapply` | put a suite's stored changes back by hand |

Arguments declared as paths are resolved against the repository root and
**refused if they escape it**, so `--output ../../somewhere` cannot be
used to write outside the tree.

## If something looks wrong

The page is never cached: every file and every answer is sent with
`Cache-Control: no-store`, so after a `git pull` a plain reload runs
the new page. The server itself (`server.py`, `jobs.py`) is a running
process: after a pull, Stop and Start it; the commands it runs (intake,
design, the agent loop) are started fresh each time and need no restart.
What the Agentic tab shows is the job's LAST state, with its time
(`as of ...`); it changes when a new run starts, not before. A
rejection says what happened -- a rule the agent broke, or a Cursor call
that did not finish in time (no rule broken; the files were put back).
**Dismiss** puts an outcome away once nothing of it is left in the
working tree (rejected and undone, discarded, pushed, approved). The
run's Cursor log, command logs, prompts and answers go with it into
`.workbench/<job>/dismissed-<time>/`, so the tab starts blank and
nothing is lost; the story, brief, plan, design and review stay, they
are the job's. A command that fails says its `FAIL` line in the label
beside its button, not only the step it stopped at. A change that is waiting, or
that did not verify, cannot be dismissed: Discard or Approve it.

- **Anything unexplained** — open the **Logs** tab first. It has the full
  text of the error the page only named, and what each command said as it
  failed.
- **Page loads but every action fails** — check the terminal. A
  `PermissionError` naming `Host`, `Origin` or `Content-Type` is the
  guard above, not a bug.
- **`locate` says the index is stale** — it is telling you the truth.
  Rebuild it after a convert.
- **`locate` says the intake found no request** — the story is prose.
  Run *Design the API tests*, then locate again: the designed endpoints
  are checked instead.
- **The plan says tests "could not be seen into"** — they use client
  calls or DSL steps and no converted suite is on this machine to say
  which endpoints those are (convert a suite and locate again), or their
  path is not written out where it is used.
- **A hand-written test exists but the plan says CREATE** — its folder
  is not in `handwritten_roots` (`tools/agent/policy.json`), or see the
  line above.
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
python tools/agent/test_coverage.py
python tools/agent/test_loop.py
python tools/agent/test_design.py
python tools/agent/test_history.py
python tools/agent/test_defects.py
python tools/agent/test_codereview.py
python tools/agent/test_specsource.py
python tools/agent/test_manualtests.py
python tools/agent/test_publish.py
python tools/agent/test_launch.py
python tools/test_diag.py
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
the code that decides what may be pushed. So are `test_locate.py` and
`test_coverage.py` (`agent-locate`, `agent-coverage`): they decide
whether anything is written at all. Some of the others are **not** in
the usual sweep -- the repository-wide run globs `tools/test_*.py` and
`tools/ra_converter/test_*.py`, which does not reach this directory. Run
them yourself after touching `intake.py`, `jobs.py` or `locate.py`:

```
for t in tools/agent/test_*.py; do python -B "$t" || break; done
```
