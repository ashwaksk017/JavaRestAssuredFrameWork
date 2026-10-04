# autofix — running the loop

An agent fixes the converter; this directory refuses to believe it until
the evidence holds. Read `invariants.py`'s docstring for why that is the
whole design.

## The one-minute version

```bash
# 1. what is failing, in a form a machine can read
python tools/verify_all.py --full --baseline --json target/verify.json

# 2. what the agent would be told (no agent called, nothing changed)
python tools/autofix/propose.py --records target/verify.json --agent dry-run
cat target/autofix/packet.txt

# 3. ask Cursor for a fix, keep it as a proposal
python tools/autofix/propose.py --records target/verify.json --agent cursor
cat target/autofix-report.md

# 4. after a few dozen runs, see what the record says
python tools/autofix/ledger.py --stats
python tools/autofix/ledger.py --eligibility
```

Nothing above commits to your branch and nothing pushes, ever.

`--attempts N` gives the agent a retry budget: each retry starts from the
restored tree and carries the exact rejection, so "you edited generated
output" is something it can act on rather than a dead end. Default 1.

## Setup on the Cursor machine

```bash
pip install cursor-sdk
cp tools/ra_converter/cursor_agent.json.example tools/ra_converter/cursor_agent.json
# set apiKey, or export CURSOR_API_KEY
```

`cursor_agent.json` is gitignored. The adapter reuses the converter's own
`cursor_assist` wrapper — same config, same key resolution, same Windows
bridge patch — so there is one path to the SDK, not two.

If the key or the SDK is missing, `propose.py` says so and exits; it does
not fall back to anything.

## What each piece does

| file | job |
|---|---|
| `failure_record.py` | turns `verify_all` output into records with a stable fingerprint, the suites and cases involved, and a per-check autofix policy |
| `baseline.json` | known failures, accepted with a reason and an **expiry** |
| `accept.py` | adds a baseline entry from a real run, so no fingerprint is ever hand-copied |
| `invariants.py` | validates a candidate **diff** before anything runs it |
| `mutations.py` | the two-sided gate: proof a check still fails on a tree it was built to catch |
| `ladder.py` | runs the gates cheapest-first and stops at the first failure |
| `manifest.py` | hashes the generated tree; exact blast radius of a fix |
| `taskpacket.py` | the brief handed to the agent |
| `propose.py` | the orchestrator |
| `ledger.py` | every run, and what autonomy the record earns |

## Things that will bite you

**The generated tree is gitignored.** A fresh clone has no `support/`,
no CSVs, no templates — so every tree check fails until you convert. Run
the authoritative convert once before trusting any result:

```bash
python tools/ra_converter/ra_converter.py --input tools/ra_converter/input \
    --output . --package-root com.hi.api --clean --max-name-len 40 \
    --no-cursor-assist --phase-specs
```

That is **one command of about 40 minutes** and it exceeds a 30-minute
command budget, so give it its own job. It is also the only convert that
is authoritative: `--input <dir>` computes shared phases and clustering
across all 15 suites at once.

**A single-suite convert is not side-effect-free.** Measured here:
converting `accountdashboardregression` alone changed 122 files across
nine suites. Every one was a CSV and not one was Java — CSV data rows
come from a shared identity allocation whose sequence depends on which
suites are in the run. So after `repro-convert`, `compile` and the
Java-reading checks are sound, while anything reading CSV data is looking
at a hybrid tree that matches no full convert. Pass `--manifest` to any
run that converts and the hybrid state is visible instead of misleading.

**`verify_all --baseline` is not the default.** A plain run still reports
the known `tokenRequest 2` step-parity failure and exits 1, exactly as it
always has. The loop passes `--baseline`; your own runs need not.

**Accepting a known failure needs `accept.py`.** Editing `baseline.json`
by hand means hand-copying a 40-character fingerprint, which fails
silently in both directions. The tool refuses a vague reason, an expiry
in the past or more than 400 days out, and a check that was passing.

## After a rejected proposal

Source is restored from a hash-verified snapshot. **Generated output is
not.** If the ladder got as far as `repro-convert`, the tree still holds
output built from the rejected patch, and the next `verify_all` measures
that — so a failure caused by the discarded fix gets attributed to
whatever you look at next. `propose.py` says so, names the suites, and
repeats it in the report. Converts are deterministic, so reconverting
those suites from the restored source is enough.

## Turning autonomy on

Don't, yet. `autonomy.json` ships with `"enabled": false` and that is the
right setting until the ledger says otherwise:

```bash
python tools/autofix/ledger.py --eligibility
```

The gate is per check **kind**, and two kinds can never earn it:

- `tree` — only a whole-directory convert is authoritative for a static
  check, so a green single-suite rung is a signal. Auto-applying on a
  signal is how a wrong fix lands.
- `runtime` — the TestNG guard suite *is* the guard.

`prompt`-policy checks (`step-parity`, `dataflow`, `request-schemas`,
`java-tests`) are never sent to an agent by default at all. They need the
ReadyAPI XML cross-checked first, which is a judgement call and yours.

When you do enable it, `--mode auto` applies the patch, climbs the ladder,
measures the blast radius, and on success commits **on a new branch**. It
never touches your working branch and never pushes.

## Patch paths

Emit the diff relative to **`b2b-api-ai/`**, not to the git root. Both are
accepted -- `propose.py` normalises the header paths before anything looks
at them -- but only because validating the raw git-root form used to miss
everything: a `support/` path behind the extra `src/main/b2b-api-ai/`
prefix was not recognised as generated, so the diff passed every rule and
then failed to apply, and the report blamed the wrong thing.

Absolute paths, drive letters and `..` segments are rejected outright.

## Reading a rejection

```
[REJECT] generated-output: src/main/java/com/hi/api/support/amexbackbook/cases/Specs3.java
         generated output -- the next convert overwrites it, so a fix here
         looks like it worked and then vanishes. Fix the emitter instead
```

Rejections are the system working. They are recorded as `guarded` in the
ledger and deliberately **not** counted against the accept rate — scoring
the guards as unreliability creates pressure to loosen them.

Two rejections can never be waived: `secret-added` and `never-touch`.
Everything else a human can override, with a reason, which is recorded:

```bash
python tools/autofix/invariants.py --worktree \
    --waive checker-only-change --waive-reason "why this is correct"
```

Never pass `--waive` on an agent's behalf. A loop that can waive its own
holds has no holds.

## When a fixture says MISSED

`mutations.py` reporting MISSED means a check did not catch a mutation of
the thing it guards. That is a **hypothesis, not a verdict** — three of
the first four fixtures written here were aimed at the wrong thing and
each looked exactly like a vacuous guard:

- `PhaseSpec.phase("...")` in a Specs class is the spec's **label**, not
  a chain link; `phase-order` resolves `start(row, "<case id>")` against
  registrations instead.
- `${Properties#x}` in a template is **not** a bug: `PlaceholderResolver`
  handles templates and maps `#` to `.`, so a well-shaped ref is
  deferred. Only a shape its key pattern cannot match — brackets and
  quotes, as in the real `${step#Respons['accountID']}` typo — is stuck
  as literal text on the wire.

Check the fixture before believing the check is broken.
