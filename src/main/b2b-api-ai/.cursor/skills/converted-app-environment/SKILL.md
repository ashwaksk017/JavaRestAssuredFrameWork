---
name: converted-app-environment
description: Work out how a converted application reaches its APIs — service keys, token routing, credentials, database — and record the answers in a per-application skill. Use when a converted suite 401s, cannot mint a token, hits localhost or a stranger's machine, when setting up program_configuration.json for a new environment, or when starting the conversion of another application.
---

# Environment wiring for a converted application

The converter decides **which service** a call belongs to. It never
decides **which host** answers for that service — that is an environment
choice a human makes, per application, in
`src/main/resources/program_configuration.json`.

This skill is the method. The answers for each application live in its
own skill: see [app-goal](../app-goal/SKILL.md) and
[app-b2b](../app-b2b/SKILL.md). Starting a new application means writing
a third one, from the checklist at the bottom.

## The one trap that matters

`Config.serviceBase(key, recordedBase, callerBase)` resolves in this
order:

1. `services.<key>` from config
2. `-DbaseUrl` / `BASEURL`
3. **the host the converter recorded**
4. `Config.baseUrl()`

Step 3 is the trap. The source XML carries two kinds of host and they
are not interchangeable:

| element | meaning |
|---|---|
| `<con:endpoint>` | what ReadyAPI actually sends to |
| `<con:originalUri>` | where the request was first imported from — a historical note ReadyAPI never calls |

An unset key routes live traffic at whatever was recorded, and recorded
can be an `originalUri`. In this project that produced, all three
silently:

- a **test-tier gateway** when the token was minted on a different one — every call `401 Invalid Credentials`, and refresh-and-retry got the same 401 because a fresh token from the wrong gateway is still the wrong gateway;
- **`localhost` with its port stripped** as the token endpoint for nine suites — no token at all, so everything after it 401'd;
- a **developer's machine name** carrying ten suites' Kafka calls.

None of it is visible in the emitted Java, at compile time, or to any
other check.

## Run the audit before blaming the converter

```bash
python tools/audit_service_keys.py \
    --config src/main/resources/program_configuration.json \
    --input  tools/ra_converter/input
```

It lists every service key, which config blocks answer for it, which
recorded hosts are **not** declared as a `<con:endpoint>` anywhere, and
which key each suite's token exchange routes through. It exits 1 when a
key is unset *and* its fallback is unattested. It prints key and suite
names only, never a host value, so its output is safe to paste into a
ticket.

## Deciding a host

Take it from `<con:endpoint>`, never from `<con:originalUri>`:

```bash
grep -ohE '<con:endpoint>https?://[^<]+</con:endpoint>' tools/ra_converter/input/*.xml \
  | sort | uniq -c | sort -rn
```

If one service key shows several endpoint hosts, they are usually
**different environments of the same service**, which is an argument for
one config block per environment — not for picking the most common one.
If a key shows no endpoint host at all, every recorded value for it is
an artifact and the right host has to come from whoever owns the service.

Never invent a hostname to fill a gap. An unset key that fails loudly is
better than a guessed one that quietly reaches the wrong tier — and a
host with no `-s` / `-t` style marker in its name may be production.

## Token routing is a separate question

The token is just another step, so it resolves through its own service
key, which is often **not** the key the suite's API calls use. Mixing
them up is the easiest way to misread a 401:

- the same `401` means "wrong gateway for this token" and "wrong credentials" and "token expired";
- `token-refresh ... attempt 1 of 1` followed by another 401 means the *gateway* is wrong, not the token;
- a token minted successfully (HTTP 200) proves nothing about whether the API gateway accepts it.

The audit prints the token key per suite. Check it before changing
credentials.

## Credentials

One file, gitignored, never anywhere else:
`src/main/resources/program_configuration.json`. Everything in it is also
settable as `-Dkey=...` or an env var, which is how CI supplies it.

Most ReadyAPI projects here do **not** export their credentials. Their
token step loads them at runtime from

```
<ReadyAPI project folder>/local-config/<ActiveEnvironment>.properties
```

reading `client_id`, `client_secret`, `username`, `password`. The
ReadyAPI environment name is the file name, so a config block named
after that environment maps one-to-one. If a credential is missing, it
is in that file on the machine that runs ReadyAPI — ask for it, do not
reconstruct it.

## One block per application, not per project

Two applications in one tree need two config blocks, selected with
`-Denv=<block>`. Block names are free-form (`Config` does
`root.get(envName)`), so no code change is needed to add one. They
differ in more than the host: **database** and **token gateway** too.
Running one application under the other's block is what produces
`relation <x> does not exist` next to `401 Invalid Credentials`.

## Checklist for a new application

Work through this, then write `.cursor/skills/app-<name>/SKILL.md`
recording the answers. Keep host values out of it — the skill names
keys, suites and decisions; the values live only in the gitignored
config.

1. `tools/audit_service_keys.py` — list the keys and the token routes.
2. For each key, find its `<con:endpoint>` hosts. Note any key with none.
3. Decide one host per key **per environment block**. Leave genuinely
   unknown ones empty and say so loudly, with what the fallback would
   reach and who owns the answer.
4. Identify the token key per suite and confirm its gateway matches the
   credentials you have.
5. Find the database for this application; confirm the schema matches
   the tables the suites query.
6. Note which keys are shared with another application and which are
   not — a key named for a service may still need a different host per
   application.
7. Re-run the audit and record the exit code in the new skill.
