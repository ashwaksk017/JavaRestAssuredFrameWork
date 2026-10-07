---
name: Story-to-test agent with a UI layer
overview: A paste-in UI (story, payloads, Confluence links) that drives a step-by-step agent to create, update or delete automation tests and open a branch for review. Built on the existing Jira chain, Cursor SDK call and gate, not beside them.
todos:
  - id: decide
    content: Settle the five open decisions below; three of them change the build order
  - id: confluence
    content: tools/confluence/fetch.py mirroring tools/jira/fetch.py -- allowlist, PAT from config, snapshot to target/
  - id: intake
    content: Normalise story text + payloads + fetched pages into one brief.md plus sources/
  - id: locate
    content: Reuse shape_match to decide create vs update vs duplicate before any edit
  - id: sdk
    content: Ensure cursor-sdk at runtime and probe once for a resumable session; adapt behind one small interface
  - id: loop
    content: Orchestrate the agent as gated steps, each writing a file a human can argue with
  - id: guard
    content: Write-path allowlist, delete manifest, secret scan -- enforced in code, not in the prompt
  - id: ui
    content: One local page + JSON API over the job directory
  - id: publish
    content: Gate, branch, PR. Never main, never an unverified push
isProject: true
---

# Story-to-test agent with a UI layer

## The shape of it

```
  UI (paste story / payloads / Confluence links)
        |
        v
  target/agent/<job-id>/          <- gitignored, every step writes here
        brief.md                  intake, normalised
        sources/                  Jira + Confluence snapshots
        plan.md                   create / update / delete, per test
        proposed.diff             what the agent WOULD change
        verify.txt                gate output
        manifest.json             what this pipeline owns
        |
        v
  branch `agent/<job-id>-<slug>` + PR        <- never main
```

The job directory is the product. The UI renders it; it does not hold
state of its own. That is the same bet `tools/jira/run.py` already
makes, and for the same reason: every decision stays inspectable, and a
step that goes wrong can be rerun on its own.

## What already exists — reuse, do not rebuild

| piece | where | what it gives |
|---|---|---|
| gated story chain | `tools/jira/run.py` | fetch → extract → match → packet, each stage stopping rather than inventing |
| host allowlist + PAT auth | `tools/jira/fetch.py` (`host_allowed`, `normalize_issue_key`) | exact scheme+host match, token in a header, refuses unapproved hosts |
| duplicate detection | `tools/jira/shape_match.py` | clustering-signature match against existing tests; already knows generated-vs-hand-written |
| agent call | `tools/ra_converter/cursor_assist.py` (`_sdk_prompt`) | `cursor_sdk.Agent.prompt` with `LocalAgentOptions(cwd)`, output redaction, `git_watch_diff`, run memory |
| agent instructions | `.cursor/skills/*` | jira-to-restassured, converted-app-environment, app-goal, app-b2b |
| the gate | `tools/verify_all.py` + `audit_service_keys.py` + `audit_token_chain.py` | what "done" has to mean before anything is pushed |
| write destinations | `tests/jira/`, `csv/manual/` | the only two places a hand-written test may live |

Roughly 60% of "story in, test out" is already built. The new work is
Confluence, the loop, the guardrails, the UI and the push-back.

## What is missing

1. **Confluence** — nothing exists. No fetch, no allowlist entry, no config block.
2. **A loop** — `cursor_assist` calls the agent **once**. Step-by-step means orchestrating several calls with state between them.
3. **Update and delete** — the Jira path only creates. Nothing knows how to revise or retire a test.
4. **Guardrails as code** — today "do not edit `tests/imported/`" is a sentence in a skill. An agent that can delete and push needs that enforced by the runner.
5. **A UI** — nothing. No server, no page, no job model.

## Constraints that must not bend

- **The repo is public.** Snapshots of Jira and Confluence carry customer data. They go to `target/` (gitignored), never into a tracked file, and the existing secret scan runs before every push.
- **One config file.** Confluence PAT goes in `program_configuration.json` under a `confluence_config` block shaped like `jira_config` — allowlist as a **list**, token never in a URL or a log.
- **Generated trees are off limits.** `tests/imported/`, `tests/classic/`, `support/`, `rest/clients/`, `templates/`, `csv/<suite>/` are converter output. The agent may only write `src/test/java/com/hi/api/tests/jira/**` and `src/test/resources/csv/manual/**`.
- **Never push to main.** Branch + PR, gate green first.
- **Delete is the dangerous verb.** See the rule below.

## The staged plan

Each stage is independently useful and independently verifiable. Stop
after any of them and the repo is still in a better state.

### Stage 1 — Confluence fetch *(no open decisions block this)*

**Gap review changed this stage.** The plan called it "a near-copy of
`tools/jira/fetch.py`". It is not. That file is 754 lines and most of
what it does, Confluence needs *differently* rather than identically:

| `fetch.py` does | Confluence equivalent |
|---|---|
| one key shape (`ABC-123`) | at least four URL forms, two needing an extra lookup |
| text fields | `body.storage` XHTML full of `<ac:structured-macro>` |
| walks **parents**, depth 3 | specs span **children** — opposite direction |
| pages comments | same idea, different endpoint |
| attachments: textual-only, 10 MB cap, allowlisted | identical rules, different endpoint |
| refuses without acceptance criteria | a page has no AC field — see below |

What actually has to be built:

1. **URL forms.** `/display/SPACE/Page+Title`, `/pages/viewpage.action?pageId=N`, `/spaces/SPACE/pages/N/Title`, and short `/x/AbCdEf`. Title-based needs a space+title lookup; short links need redirect resolution. Both stay on the allowlisted host, so the existing `host_allowed` still governs.
2. **Code macros before tag-stripping.** `<ac:structured-macro ac:name="code">` carries the payloads Stage 2 exists to read. Naive tag-stripping destroys exactly the part that matters. Extract macros to fenced blocks FIRST, then strip — the mirror of `payload_candidates`, against different markup.
3. **Children, with a cap**, defaulting to depth 1 and off unless asked. Unbounded child-walking turns one link into a crawl of the space.
4. **Comments**, which on a Confluence page often carry the final contract.
5. **Server/DC first.** Jira here is Server/DC (`/rest/api/2`, PAT), so Confluence is `/rest/api/content/<id>?expand=body.storage,version`. Cloud's `/wiki/api/v2` differs; make the path configurable the way `--api-path` already is, and default to Server/DC.
6. **`confluence_config`** shaped exactly like `jira_config`: `base_urls` as a **LIST** (the existing comment warns that a bare string iterates as characters and refuses every fetch), `pat`, `timeout_seconds`. `projectconfig.section()` already reads it with no change.

**The refusal gate moves.** `fetch.py` refuses a story with no acceptance
criteria because AC is what makes a story testable. A page has no AC
field, so copying that rule would invent one. Confluence fetch refuses
only what it genuinely cannot do — unreachable, unapproved host, empty
body — and "no request could be read" stays Stage 2's gate, which
already owns it. Two stages refusing the same thing is how a pipeline
starts arguing with itself.

**Secrets.** Confluence pages routinely carry credentials inline. The
snapshot is gitignored like every other `target/` artifact, but the UI
renders it and the agent prompt will embed it, so redaction happens on
the way into both. `cursor_assist.redact_for_log` only masks a known
key; this needs a pattern pass.

Verification: tests mirroring `test_fetch.py` — an unapproved host, each
URL form, a code macro surviving the strip, and an empty body.

**Built, then reviewed.** The review found four defects in my own code,
three fixed and one deferred:

| found | status |
|---|---|
| the PAT followed a cross-host redirect | fixed — `urllib` copies every header but content-length/type onto a redirected request, and tiny links follow a redirect *by design*. Redirects off the approved host are now refused outright |
| an ambiguous title lookup took `results[0]` | fixed — two pages can share a title across versions or archived copies, and the wrong one reads as plausible. It refuses and names the ids |
| no response size cap | fixed — 20 MB, refused rather than held in memory |
| the child walk had no dedup or global cap | fixed — a space is a diamond, not a tree; 100 pages total |
| attachments are not implemented | **out of scope** — decided, not deferred |

**Attachments are out of scope** (decided 2026-10-06). Pages whose spec
lives in an attachment will read to Stage 2 as pages with no request in
them, and that is the accepted behaviour: the gate says what was
missing rather than guessing. The Jira side already has the shape to
copy if this is ever revisited.

### Stage 2 — Intake and brief

One normaliser that takes whatever the user pasted — free-text story,
Jira URL, curl commands, JSON payloads, Confluence links — and produces
`brief.md` plus `sources/`. Payload parsing reuses `tools/jira/extract.py`,
which already reads a request out of prose.

Gate: if no request can be read and no Confluence page carries one, the
job stops here and says what was missing. It does not guess.

### Stage 3 — Locate before you write

Run `shape_match` against the brief's request signature:

| verdict | action |
|---|---|
| matches a **generated** test | stop — the change belongs upstream in the ReadyAPI XML, not here |
| matches a **hand-written** test | propose an **update** to that file |
| no match | propose a **create** |

This is the stage that makes "update" possible at all, and it is also
the stage that stops the agent quietly forking a second copy of a test
that already exists.

### Stage 4 — The agent loop

Steps, each writing its artifact and each stoppable:

1. `plan` — agent reads brief + located files + the skills, writes `plan.md`: per test, create/update/delete and why. **No edits.**
2. *human approves `plan.md` in the UI*
3. `apply` — agent edits only allowed paths; runner captures `proposed.diff`
4. `verify` — compile + `verify_all` subset + `check_no_duplicate_methods` + both audits
5. on failure, one bounded repair attempt with the verify output fed back; then stop and show the human

The one-shot `Agent.prompt` is called once per step with the prior
artifacts in the prompt.

**Bootstrap.** The runner ensures `cursor-sdk` itself rather than
assuming it: `requirements-cursor.txt` already declares it, so the step
is a guarded `pip install -r` into the active environment, run once per
job and skipped when the import already succeeds. It fails the job with
a readable message rather than a traceback when there is no network or
no permission to install.

**Session mode is detected, not assumed.** I could not check whether the
SDK exposes a resumable session — it is not installed in my environment,
and a dynamic install does not answer the capability question, only the
presence one. So the runner probes once at startup and picks:

| if the SDK offers | the loop uses |
|---|---|
| a resumable session / conversation handle | one session carried across steps — cheaper, keeps context |
| only `Agent.prompt` | a fresh prompt per step, with the job directory as the memory |

Both are correct; the second costs more tokens. Writing the loop against
a small internal interface (`start()`, `step(prompt)`, `finish()`) keeps
that choice in one adapter instead of spread through the orchestrator,
and means the fallback is not a rewrite.

### Stage 5 — Guardrails, in code

- **Write-path allowlist** enforced by the runner: after `apply`, any changed path outside the two allowed roots fails the job and reverts. Not a prompt instruction — prompts are advisory, `git checkout` is not.
- **Delete** requires all three: the path is in the allowlist, the UI showed a confirmation naming the file, and `manifest.json` says this pipeline created it. A test the pipeline did not create is never deleted automatically.
- **Secret scan** on the staged diff before any push, the same grep already used by hand this session.

### Stage 6 — UI

Minimal and local: one Python process (FastAPI or stdlib `http.server`)
serving one page plus a small JSON API over the job directory. Paste
box, link list, a step timeline, the diff, the verify output, and two
buttons — approve and reject. No framework, no build step, no database;
the filesystem is the database.

### Stage 7 — Push-back

Branch `agent/<job-id>-<slug>`, commit with the brief as the body, PR
via `gh`. Gate must be green. **Never main.**

## Open decisions — these change the build

1. **Who pushes?** Your standing rule on this repo is that you verify locally and push. Does the agent open a PR, or stop at a local commit on a branch?
2. **Where does the UI run?** Your machine only, or the remote test machine others can reach? The second needs auth in front of it and changes the credential story.
3. **Scope of "test case".** Hand-written `tests/jira/` only, or should a story that belongs to a converted suite be allowed to drive a ReadyAPI XML change + reconvert? The second is much larger and I would not put it in v1.
4. **Confluence auth** — PAT like Jira, or SSO/OAuth? PAT is a day; SSO is not.
5. **Delete policy** — is "only what this pipeline created" acceptable, or does it need to retire older hand-written tests too?

## Risks

- **Blast radius.** An agent that writes code and pushes is the largest-blast-radius thing in this repo. Branch-only, gate-green and the write-path allowlist are what keep it bounded. None of those should be relaxed for speed.
- **Confluence pages are messy.** Expect tables, macros and screenshots carrying the actual contract. The fetch must degrade to "I could not read a request from this page" rather than half-read one.
- **Agent edits that compile but are wrong.** This is the `--classic` lesson: three faults compiled and only a shape check caught them. `verify` must include the duplicate-method and shape checks, not just `javac`.
- **Cost.** Each step is a model call over a large repo. Budget per job, and make step 1 cheap by sending the brief and the located files rather than the tree.
