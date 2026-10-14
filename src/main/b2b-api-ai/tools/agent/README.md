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
- What the server prints is kept in `target/agent/server-console.log`;
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
    `target/agent/<job>/spec/<its own name>` and the design step reads it
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

### Manual tests

Manual UI test cases from requirements, written as a **delta** against
the tests the team already keeps in Jira / Xray: what is new, and which
existing tests a changed rule makes wrong. Cursor reads and proposes
only. Nothing on this tab writes to Jira.

**What it reads**

- **Requirements files** -- choose up to 8. The page sends each file; the
  workbench keeps it as `target/agent/<job>/docs/<its own name>` and
  reads it from there, and shows the path. Text (`.txt .md .csv .json
  .log`), HTML and Word (`.docx`) are read with what Python ships. PDF
  needs `pypdf` and Excel needs `openpyxl`; a file that cannot be read on
  this machine is listed as unread with the reason, not passed over.
- **Notes** -- free text.
- **The story** read on the Automate Test Case tab for the same job, if any.
- **Existing tests** -- *Read existing tests* takes a project key, an
  issue type (default `Test`) and optionally one further condition, and
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
python tools/agent/manualtests.py inventory --job <job> --project ABC
python tools/agent/manualtests.py generate  --job <job> --app "Group booking"
```

### Convert ReadyAPI

Converts ReadyAPI project files into this project. The page shows the
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
| Verify all afterwards | when the convert finishes cleanly, runs `python tools/verify_all.py` and shows its log under the convert's. Not run after a failed convert, and the page says so |

Not on the page, and left at their defaults: `--package-root`, `--envs`,
`--config`, `--cursor-config`, `--service-name`, `--diagram-cases`,
`--max-name-len`, `--bootstrap`, `--skip-dataflow-check`,
`--keep-dead-props`, `--no-reapply-patches`. `--output` is not shown
either and is always this project (`.`): the converter's own default is a
folder called `output`. All of them still work from the command line.

With the job box empty, the Convert tab runs as job `convert`.

`--data-dir` points at the folder of `.xlsx` workbooks the ReadyAPI XML
names, and is how DataSource columns get real values instead of empty
cells. It is **optional** — a suite whose project has no DataSource
workbooks needs none — so the form marks it *advised*, not required,
and will run without it. When the suite does read workbooks, leaving it
out empties every DataSource column, and the run then looks like an API
problem rather than a missing path, which is why the form says so.

### Agentic Test Creation

The only tab that writes code. It takes the plan the Automate Test Case tab produced and has
**Cursor** act on it.

**The repository, and where the branch goes.** The strip at the top of
the tab reads git each time the tab is opened: the branch checked out,
whether the tree is clean, the remote (its address shown without any
credentials), and how far the branch is from its upstream. **Switch to
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
*Ask Cursor to change it* takes your words -- "use the member's email
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

Each run is one Cursor call, and then, while the result does not verify
(the compile, a check, the gate), another call with the output handed
back and the instruction to fix only what it reports. It keeps going
**until it verifies**, with two stops: `repair_max_attempts` in
`tools/agent/policy.json` (8 by default), and an attempt that leaves the
failing output exactly as it was -- Cursor going round in circles -- after
which no further attempt is made. The log says which, and the Agentic
tab's state says `verify-failed` with the number of attempts. Set
`repair_attempts` to a number for that many repairs instead.

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

Four things keep a run short, all in the prompt. Once a person has
reviewed the cases and the brief is long (20,000 characters or more),
the brief handed to Cursor is **the story, the requests, and the table
rows of the Confluence pages**: parents, linked stories, the pages'
prose and the sources were material for the design, and the approved
cases carry what came of them; the tables (field names, types, allowed
values, formats) stay because a case's `data: valid values` is short
for them. The prompt names what was left out, how much, and where the
whole brief is (`target/agent/<job>/brief.md`), to be opened only when
a case names something not explained. With nothing reviewed the whole
brief goes, since then the cases are not vetted; `agent_brief: "full"`
in `policy.json` keeps the whole brief always. Cursor is told **not
to run a build of its own**: the workbench compiles the moment it
finishes and hands back the errors, so a `mvn` it starts is only a wait.
The scope's **skill file goes into the prompt whole** (`skill` in
`policy.json`, e.g. `.cursor/skills/jira-to-restassured/SKILL.md`, up
to 16,000 characters), with the rule that where anything else differs
from it, the skill wins. It used to be only pointed at, with an example
test from another folder inlined instead; Cursor built from the example,
read the skill later, found they disagreed and did the work twice. Only
when the scope has no skill file is **one existing hand-written test**
shown instead (the smallest that has a `@Test`, or the files
`pattern_example` names in `policy.json`), with the first lines of a
data sheet under `csv/manual/` -- the committed location; the other
`csv/` folders are gitignored because their rows carry customer data,
and are never put in a prompt. The log names what was shown. And a failed compile
comes back as **a list of `file:line: message`** (each once, at most
40) with a short tail, not sixty lines of build output.

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

- **Off** unless `jira_config.publish.create_tests` is `true`;
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
- In mock mode both change a file under `target/agent/` and contact
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

- **This job** -- `target/agent/<job>/diagnostics.log`. Every command run
  for the job: when it started and with what options, how long it took,
  its exit code, **what it said when it failed** (its own log is written
  over by the next run; this is not), and the **full traceback of every
  error**, including the ones a command handled and carried on from.
  The same error at the same place is written out five times in a run
  and then counted.
- **The web server** -- `target/agent/server.log`. Requests that failed,
  every request that starts or changes something, refusals with their
  reason, tracebacks of faults in the server, and errors in the page's
  own script (a button that "does nothing" usually left one here).
  What the page asks for over and over while it waits is left out.

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

## Where the state lives

Everything is under `target/agent/<job>/`:

```
target/agent/<job>/
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
| `verify-all` | run every check of the gate (`tools/verify_all.py`); takes nothing from the page |
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
`target/agent/<job>/dismissed-<time>/`, so the tab starts blank and
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
