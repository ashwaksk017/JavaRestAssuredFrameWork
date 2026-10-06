---
name: app-goal
description: Environment wiring for the GOAL application's converted suites — which service key each call and token uses, which config block to run with, and what is settled. Use when a GOAL suite (Group360, AdHoc, AmadeusOneStep, MFR, Inventory, RoomBlocks, RoomRates, RoomingList, PEPMeetingDetails, TopicAPI, GroupsData, PartialGoalRegression, smokePROD) 401s, cannot mint a token, or is being run for the first time.
---

# GOAL — environment wiring

Method and the reasoning behind it:
[converted-app-environment](../converted-app-environment/SKILL.md).
This file is GOAL's answers. **No host values here** — they live only in
the gitignored `program_configuration.json`.

## Run it with

`-Denv=goal`. Running GOAL under the B2B block reaches the B2B database
and the B2B gateway, which shows up as `relation goal.<table> does not
exist` next to `401 Invalid Credentials`.

Per-ReadyAPI-environment blocks also exist (`STG_Partner_500`,
`STG_Partner_600`, `STG_Partner_700`, `STG_Corporate_500`). They use a
**password** grant and ship with empty `username`/`password`; the `goal`
block uses client_credentials and does not need them.

## Suites

AdHocSTAGE, AmadeusOneStep, Group360Create, Group360Shop, GroupsData,
Inventory, MFR, PEPMeetingDetails, PartialGoalRegression, RoomBlocks,
RoomRates, RoomingList, TopicAPI, smokePROD.

## Token routing — three different answers

This is the part that misleads. GOAL does **not** mint its token in one
place:

| token key | suites |
|---|---|
| `hospitality_partner_v2` | Group360Create, Group360Shop, PartialGoalRegression |
| `hospitality_internal_all_v2` | MFR |
| `localhost` | AdHoc, AmadeusOneStep, Inventory, PEPMeetingDetails, RoomBlocks, RoomingList, RoomRates, smokePROD, TopicAPI |
| *(none)* | GroupsData |

Minting on the **partner** gateway while calling **internal-all** is
deliberate for the first three — the source XML has 43 token URIs on the
partner host, and ReadyAPI's own environment config names it. Do not
"fix" that mismatch.

The `localhost` row is **not** deliberate. It is an `originalUri`
artifact with the port stripped: nine suites would POST their token at
`http://localhost/realms/applications/token` and get nothing, then 401
on every following step. `services.localhost` must be set for GOAL, and
it is. All 23 calls on that key are ordinary GOAL paths
(`/realms/applications/token`, `/props/{propCode}/groups`,
`/groups/rateplan`) — the same paths that elsewhere route through
`hospitality_internal_all_v2`.

## Service keys

| key | status for GOAL |
|---|---|
| `hospitality_internal_all_v2` | set. Five recorded hosts, only one of them a real `<con:endpoint>` for GOAL; the others are `originalUri` artifacts, including a test-tier gateway that 401s a GOAL-minted token |
| `hospitality_partner_v2` | set — the token gateway for three suites |
| `hospitality_corporate_v2` | set |
| `mc_groupsdata` | set — GroupsData and PartialGoalRegression |
| `localhost` | set — see above; unset means nine suites cannot authenticate |
| `extended_partner_v2` | set |
| `hospitality_internal_ro_v2` | **unset** — B2B-only in practice, no GOAL caller |
| `5978_b3x4n32` | **unset** — TopicAPI's Kafka calls ride this; see [app-b2b](../app-b2b/SKILL.md) |

## Database

The GOAL schema, not the B2B one. Verified working: the JDBC steps
connect and return rows under `-Denv=goal`.

## Token chain state (all 14 XMLs converted in one pass)

`audit_token_chain.py`: 13 suites have a token step and every one of
them extracts `access_token`, publishes it to ctx **with** the `Bearer `
prefix, and reads it back. GroupsData has no token step, which is
correct -- it talks to an unauthenticated service.

The one failing link is the request **body**: all 13 share one token
template whose four credential placeholders have no CSV column. See the
common skill for why that is not fixable from config today. It is not
GOAL-specific; B2B has it too.

## Known non-auth gaps

- `ALLOWED_DOMAINS` is **B2B-only** — referenced by 12 B2B projects and zero GOAL ones. Its absence is noise in a GOAL run, not a fault.
- Some suites stub a DataSource from an external workbook that is not in the export (`STUB DataSource external file`).
- `groovyScript_availPropertyAndDates` tries up to 20 property/date combinations in ReadyAPI; the converter sends only the first.

## State

`tools/audit_service_keys.py` exits 1 on this tree, for the two keys
listed as unset above. Both are B2B concerns. Every GOAL token route
resolves to a configured host.
