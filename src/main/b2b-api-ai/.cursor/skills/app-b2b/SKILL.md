---
name: app-b2b
description: Environment wiring for the B2B application's converted suites, including the two service keys that are still unanswered and must be settled before a B2B run. Use when starting or debugging a B2B suite (accountdashboardregression, programaccountregression, amexbackbook, memberregistration, membervalidation, H4BBookOnBehalf, LTAReservations, EADkafkaevents, leadspaceattestation, packagesendtoend, transliterateddata, topicsprogramaccounts, candianbackbookaccounts, programaccountguest, programaccounthonors).
---

# B2B — environment wiring

Method and the reasoning behind it:
[converted-app-environment](../converted-app-environment/SKILL.md).
This file is B2B's answers. **No host values here** — they live only in
the gitignored `program_configuration.json`.

## Run it with

`-Denv=stg`. That block carries the **B2B** database, which is a
different schema from GOAL's. Running B2B under the GOAL block reaches
the GOAL database.

## ▲ Two keys are unanswered — settle these before the first run

`tools/audit_service_keys.py` exits 1 on exactly these. Both are
B2B-only callers, which is why GOAL could ship without them.

### 1. `hospitality_internal_ro_v2` — may be production

Three calls, in `accountdashboardregression` and
`programaccountregression`:

- `GET /businesses/{accountId}`
- `GET /guests/{guestId}/businesses/verify`

Unset, it falls back to a recorded host that **has no `-s` or `-t` tier
marker in its name**, and that host is never declared as a
`<con:endpoint>` anywhere in the source XMLs — it appears only as an
`originalUri`. Treat it as possibly production until someone who owns
the service says otherwise.

**To settle:** get the stage host for the internal-ro service from the
service owner and set `services.hospitality_internal_ro_v2` in the
`stg` block. Do not pattern-match a tier suffix onto the recorded name
and hope.

### 2. `5978_b3x4n32` — a developer's machine

Ten suites, all on `/topics/{topicName}/partitions...`:
accountdashboardregression, amexbackbook, EADkafkaevents,
memberregistrationregression, packagesendtoenddirectvalidations,
programaccountguest, programaccountregression, topicsprogramaccounts,
transliterateddataevents — and TopicAPI on the GOAL side.

The key is named after the machine the requests were recorded from. Its
recorded host is that machine name, which will not resolve anywhere
else.

**To settle:** point `services.5978_b3x4n32` at the shared Kafka REST
proxy for the environment, in both the `stg` and `goal` blocks. Until
then those steps fail to connect. Consider renaming the key at the
converter level if the project keeps it long-term — a key named for a
laptop will not age well.

## Token routing — uniform, unlike GOAL

All 16 B2B suites mint through `hospitality_internal_all_v2`, the same
key their API calls use, so the token and the API share a gateway. A
401 here is therefore about credentials or scope, **not** about a
gateway mismatch — which is the opposite of the GOAL diagnosis. The
token has been observed returning HTTP 200 on this block.

## Service keys

| key | status for B2B |
|---|---|
| `hospitality_internal_all_v2` | set — both the token and the API calls |
| `hospitality_partner_v2` | set |
| `hospitality_corporate_v2` | set |
| `extended_partner_v2` | set — `/partners/amex/guestbusinesses` in 4 suites |
| `localhost` | set — 4 suites use it only for `/shop/props/{propCode}`; it is an `originalUri` artifact, same as GOAL's |
| `mc_groupsdata` | set |
| `hospitality_internal_ro_v2` | **UNSET — see above** |
| `5978_b3x4n32` | **UNSET — see above** |

## `ALLOWED_DOMAINS` is a B2B key

Referenced by 12 B2B projects and zero GOAL ones. `CtxFields` builds
generated identity from it — account `emailDomains`, `websiteDomain`,
owner and member emails. Unset, identity falls back to random
`word.com` domains the backend allowlist rejects, and
`CreatePendingAccountmember` 400s.

ReadyAPI keeps it in project settings and never exports it, so it is not
recoverable from the XML. The live list is in the B2B database:

```sql
SELECT DISTINCT value FROM account_rules WHERE CAST(reason AS VARCHAR) = 'managed_domain'
```

Set `ALLOWED_DOMAINS` in the `stg` block, comma-separated, or pass
`-DALLOWED_DOMAINS=a.com,b.com`.

Note that `AccountRulesRepository` already runs that same query at
runtime for a different code path. `CtxFields.allowedDomainOrNull()`
reads config only and does not consult it, which is why the warning
fires even with a healthy database.

## Credentials

The B2B token uses a client_credentials grant and the pair is present in
the `stg` block. As with GOAL, most ReadyAPI projects here load
credentials at runtime from
`<ReadyAPI project folder>/local-config/<ActiveEnvironment>.properties`
(`client_id`, `client_secret`, `username`, `password`) rather than
exporting them.
