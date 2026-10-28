# GOAL flows: how a story test gets its property, dates, group and token

Read this before writing a GOAL story test (groups, room blocks, payment,
confirm, rooming lists, room rates, rate plans, meetings, inventory, group
events). It is what the **converted GOAL suites** do (Group360Create,
Group360Shop, RoomBlocks, RoomingList, RoomRates, AmadeusOneStep, MFR,
PEPMeetingDetails, TopicAPI, Inventory, AdHoc, smokePROD, GroupsData,
PartialGoalRegression), written down so a hand-written test does the same.
Sections 1-4 are enough; the converted files in section 7 are extra reading
and may be absent (generated, gitignored).

**GOAL is not B2B.** There are no people to create. A GOAL test **finds** a
property and dates with availability (a probe, never a fixed property or a
frozen date), takes the server-made `groupId` from the shop answer, and
carries it through the group chain. It runs under an environment block that
has GOAL's service addresses (section 1).

**What is proven green** (PartialGoalRegression, 289 of 318 passed): the
shop read (179/179) and create group (36/36), rate plans (36/39), inventory,
group events. **Payment (4/8), confirm (5/20) and rooming list (1/5) are
not**: a story about them may fail for reasons outside the test -- say so
in `open_questions` rather than bending an assertion.

---

## 1. Environment and token

- Environment block: one whose `services.*` hold GOAL's addresses. The
  green run used `-Denv=stg` on a machine whose `stg` block carries them;
  a `goal` block exists too. A block WITHOUT `services.hospitality_internal_all_v2`
  sends GOAL calls to the B2B gateway: `401`, or `relation goal.<table> does
  not exist` from the database.
- Service bases (names only; values per machine): `services.hospitality_internal_all_v2`
  (nearly every GOAL call), `services.hospitality_partner_v2` (the token of
  Group360Create / Group360Shop / PartialGoal), `services.mc_groupsdata`
  (GroupsData). `POST /props/{propCode}/groups` and `POST /groups/rateplan`
  go to the block's `base_url`.
- Token: `POST <service>/realms/applications/token`, JSON body
  `client_id`, `client_secret` (`api_config.*`), and the project `username`
  / `password` of the block -- the same body as B2B's, minted where the
  proven suites mint it (`services.hospitality_partner_v2`; `goal.token_service`
  names another). Every call carries it; the token is not per person.

```java
String token = goalToken();                    // once per test (GoalStoryTestBase)
```

GroupsData calls send **no** token.

---

## 2. The building blocks (GoalStoryTestBase)

| Step | Converted step | Helper | What it gives |
|---|---|---|---|
| Token | `tokenRequest` + Token | `goalToken()` | bearer for every call |
| Property + dates with availability | `groovyScript_availPropertyAndDates` (AvailabilitySearch) | `Availability av = findAvailability(row, token, CASE)` | `av.propCode`, `av.arrivalDate`, `av.departureDate` (yyyy-MM-dd), `av.groupId`, `av.roomTypeCode`, `av.cacheExpiryTime`, `av.inventory` |
| Shop quote again (as the act) | `GET_Groups_SingleProp` | `shop(av, row.get("peak_rooms"), token, CASE)` | the `Response` (unasserted) |
| Create the group (room block) | `POST_Groups_SingleProp` | `createGroup(av, row, GROUP_BODY, token, CASE)` | true / false (asserted 200) |
| Payment | `POST_Payment` | `payGroup(av, row, PAYMENT_BODY, token, CASE)` | true / false (asserted 200) |
| Confirm | `POST_Confirm` | `Response r = confirmGroup(av, row, CONFIRM_BODY, token, CASE)` | UNASSERTED: the test asserts the status its AC names (200, or 400 without payment); `ratePlanCode`, `roomRates[0].roomTypeCode` for a rooming list |

`findAvailability` walks the candidate properties x day offsets, calls
`GET /props/{p}/groups?arrivalDate&departureDate&peakRooms` for each, and
takes the first where one of the first 6 `roomRates[i].inventory` is above
the threshold. Candidates come from the environment, never the source:
`goal.availability.props` (comma list), `goal.availability.offsets`
(default `32,52,62,66` days from today), `goal.availability.min_inventory`
(default `9`). Property first, then the offsets, as the converted search.
The answer is reused for 30 minutes within a run (as the converted search
caches its winner). Unset props: the test skips naming the key. No property
answers: a SETUP failure naming what was tried.

Every helper asserts its step as `CASE/SETUP` and returns null / false on
failure: **stop** (`return;`).

**Request bodies.** Start from the committed ones:
`templates/manual/goal_create_group.json`, `goal_payment.json`,
`goal_confirm.json`. They take `#propCode#`, `#arrivalDate#`,
`#departureDate#`, `#groupId#`, `#roomTypeCode#` (filled from `av`),
`#peakRooms#` (the row's `peak_rooms`, else `goal.peak_rooms`, else 10 -- the
same value the search shopped with: a shop without peakRooms is answered 400)
and `#inventoryCount#` (the row's `inventory_count`, else the inventory the
search found for that room, as the converted confirm sends it).
`goal_confirm.json` is the room-block confirm the converted 200 cases send
(the whole group again); a meeting-only confirm is a different body. Never a
frozen date, property code or `groupId` in a body or a committed CSV cell.

---

## 3. The chains (copy the order)

**Shop / availability read** (Group360Shop, Inventory, AdHoc, smoke).
`goalToken` -> `findAvailability` -> the act: `shop(av, ...)` (or
`GET /groups?propCode=..` multi-property, `GET /props/{p}/groups/inventory?startDate&endDate`).
Assert what the AC names: `groupId` present, `roomRates[0].roomTypeCode`,
`cacheExpiryTime` within 45 minutes, a rate plan code, counts.

**Create group** -- the proven write path, and the shape of the golden test:
`goalToken` -> `findAvailability` -> `createGroup` -> assert what the AC names.

**Group confirm family** (Group360Create, RoomBlocks, PEP, MFR, TopicAPI;
less proven, see above).
`goalToken` -> `findAvailability` -> `createGroup` -> `payGroup` ->
`confirmGroup`. A confirm without payment answers 400 -- that is a case of
its own. TopicAPI: read `GET /topics/{topicName}/partitions` before and
after the chain and compare.

**Rooming list.** Confirm family -> `POST /props/{propCode}/roomingList`
with the confirm's `ratePlanCode` and `roomRates[0].roomTypeCode`.

**Room rates / rate plan** (RoomRates, AmadeusOneStep).
`findAvailability` -> `POST /props/{propCode}/groups/roomrates` (av's
`groupId`, `roomTypeCode`) or `POST /groups/rateplan?propCode=..` with
`cutOffDate` = `startDate` = `effectiveDate` = `av.arrivalDate`.

**GroupsData** (no token). Group events: `POST /groupevents` (dates from
today, e.g. +22/+23, a unique id per run) -> `GET /groupevents`. Rate plans
need an existing one to act on: `GET /props/{p}/groupsdata/rateplans`, else
the database (`requireDb(...)`, then `goal.goalrateplans` ordered by
`actual_end_date`) -> POST / PUT / PATCH -> DELETE what the test made.

---

## 4. Dates

Always computed from today, `yyyy-MM-dd`: the search's arrival is
`today + offset`, departure `arrival + 1`. A longer stay or a second window
is computed the same way (`LocalDate.now().plusDays(n)`). A body's dates are
`av`'s -- the dates the search proved have inventory. The GET's query dates
and the body's dates are the same dates.

---

## 5. Headers

Every call: JSON content type, `Accept`, a correlation id, the bearer
(`apiHeaders(token)`). Confirm adds `Hilton-Operator-Location` (the
confirm helper sends it from `goal.operator_location`, when set). Topic
calls add `X-JWT-Assertion`, `Hilton-Operator-Location`,
`Hilton-Operator-DutyCode` -- from configuration, never a literal.

---

## 6. Data that stays behind

GOAL suites tear nothing down: groups, payments and confirms stay. A test
creates one group per run and never reuses another run's. GroupsData rate
plans a test creates, it deletes.

---

## 7. Where to look for more (when present on this machine)

- `_flows/<suite>/README.md`, `_audit/<suite>/steps.csv` -- every case's steps in order.
- `src/main/java/com/hi/api/support/<suite>/cases/*Phases.java` -- e.g.
  `group360createstage/cases/GroupsConfirmGroupsTestPhases.java` (confirm chain),
  `roomingliststage/cases/RoominglistCreateRoomingListTestPhases.java`,
  `roomratesstage/cases/RateplanCreateRateplanTestPhases.java`,
  `groupsdatastage/cases/RateplansDeleteRateplansTestPhases.java`.
- `src/main/java/com/hi/api/support/AvailabilitySearch.java` -- the search `findAvailability` mirrors.
- `src/main/resources/templates/<suite>/props/...` -- converted bodies; copy into
  `templates/manual/<job>_<step>.json`, replacing `#DataSource_*#`, `#groupid_*#`
  and every literal property, date or id with the placeholders above.
