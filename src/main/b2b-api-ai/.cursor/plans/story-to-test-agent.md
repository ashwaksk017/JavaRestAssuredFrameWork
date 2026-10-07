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

### Stage 1 — Confluence fetch *(no open decisions block this; can start now)*

`tools/confluence/fetch.py`, deliberately a near-copy of
`tools/jira/fetch.py`:

- `confluence_config.base_urls` (list) + `pat`, read from `program_configuration.json`
- exact scheme+host allowlist; an unapproved host is **refused**, not fetched from elsewhere
- page id or URL in, `target/agent/<job>/sources/confluence-<id>.md` out
- strips macros/attachments to text; records the page version so a later run can say "this changed"
- refuses on empty body, the same way `fetch.py` refuses on missing acceptance criteria

Verification: unit tests mirroring `test_fetch.py`, including an
unapproved host and a bare page id.

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
