# B2B flows: how a story test gets its people, ids and tokens

Read this before writing a B2B story test (program accounts, members,
travel coordinators, dashboards, spend, stays, bookings). It is what the
**converted B2B suites** do -- the tests that run green against `stg` --
written down so a hand-written test does the same. Everything a test needs
is in sections 1-3 and in `StoryTestBase`; the converted files named in
section 4 are extra reading and may be absent (they are generated and
gitignored, present only where a convert ran).

**The one rule:** a B2B test **creates the people it needs**. It never logs
in as a person someone set up by hand, and it never reads a person's
username, password, guest id, account id or member id from configuration.
Every id comes from a **response** of an earlier step in the same `@Test`.

Why: the converted suites never do either -- one project login for the
whole run, every person enrolled by the test -- and a per-story persona key
(`jira.<story>.<role>.username`) is a key nobody has set: the test skips
with "no value for 'username'".

---

## 1. Tokens

The converted suites send **every** call -- enrol, create, add member, set
role, and the "as the coordinator" call -- with **one service token**: the
project credential of the environment (`tokenRequest` with client id,
client secret, project username and password). The acting person is named
by the **guest id in the path**, not by the token. Do the same:

```java
String svc = serviceToken();          // once per test; the helpers take it as `token`
```

`serviceToken()` needs, **once per environment** (never per story):
`api_config.client_id`, `api_config.client_secret`, and the project
`username` / `password` of that environment block (or `sf_config.ui_username`
/ `ui_password`). Unset, the test skips naming them. `enrollActor` uses the
application token for the enrolment itself, as `enrollGuest` always has.

Each actor also has its own token (`actor.accessToken()`, a login made the
first time it is asked for). Use it only when
the story is about what a guest may do **with their own login** (e.g. "a
coordinator's token is refused on another account"); say so in the
DATA_INVENTORY. Never build a token request in the test.

---

## 2. The building blocks (StoryTestBase)

| Step | Converted step | Helper | Id it gives, and from where |
|---|---|---|---|
| Enrol a guest and log in as it | `enrollGuest` | `Actor a = enrollActor(row, ENROLL, CASE)` | `a.guestId` (enrol body `guestId`); `a.email`, `a.username` generated |
| Program account + owner's member id | `createProgramAccount` | `String[] acc = createAccountFor(owner, CREATE_ACCOUNT, svc, CASE)` | `acc[0]` accountId (body), `acc[1]` owner memberId (body `memberId` or `hilton-member-location` header) |
| Validate the owner (one-time code) | `dbReadEmailOtp...` + `confirmMemberTotp` | `confirmOwnerValidation(owner, accountId, acc[1], svc, CASE)` | needs **database.\*** -- call `requireDb(...)` first |
| Activate the account | `activateProgramAccount` | `activateAccount(accountId, svc, CASE)` | -- (204) |
| The account's invite key | `readProgramAccount` | `inviteKey(accountId, svc, CASE)` | body `inviteKey` |
| A guest joins the account | `createAccountMember` | `addMember(member, accountId, "employee", key, svc, CASE)` | `member.memberId` (body / header, else members list polled for `[0].memberId`) |
| Activate the member | `activateAccountMember` | `activateMember(accountId, member.memberId, svc, CASE)` | -- (204) |
| Give a member a role (by the owner) | `updateAccountMember` | `setMemberRole(owner, accountId, member, "travelCoordinator", svc, CASE)` | -- (200); roles: `travelCoordinator`, `admin`, `employee` |

Every helper asserts its own step as `CASE/SETUP` and returns null/false
when it failed: **stop** (`return;`) -- the SETUP assertion says why, so
the test fails; it never passes on nothing.

**Request bodies.** Start from the committed ones:
`templates/manual/b2b_enroll_guest.json` and
`templates/manual/b2b_create_program_account.json`. An enrol body MUST send
`"username": "#enroll_username#"`, `"password": "#enroll_password#"` and
`"emailAddress": "#owner_company_email#"` -- `enrollActor` generates those
three per actor and logs in with them; a fixed password in the body (the
converted bodies have one) makes that login fail. The enrolment's source and
property codes are `#enroll_source_code#` / `#enroll_prop_code#`, filled from
`enroll.source_code` / `enroll.prop_code` of the environment (set once; never
a literal in a committed body). These columns, and `#account_email_domain#`,
are filled by the helpers: list them in the csv/manual sheet header with the
cells **blank**.

A second guest: call `enrollActor(row, ...)` again -- it generates its own
username and email, in the same email domain as the first (the account only
accepts members in its domain).

---

## 3. The chains (copy the order)

Call `requireDb(...)` and `serviceToken()` FIRST when the chain needs them:
a skip after the enrolment leaves a half-made account behind.

**Owner (program account tests).**
`enrollActor` -> `createAccountFor` -> [`confirmOwnerValidation` ->
`activateAccount`] when the AC needs an ACTIVE account.

**Member / employee.**
Owner chain with activation -> `enrollActor` (member) -> `inviteKey` ->
`addMember(member, ..., "employee", ...)` -> `activateMember`.

**Travel coordinator / admin.**
Member chain -> `setMemberRole(owner, accountId, member, "travelCoordinator", svc, CASE)`
-> act **as the member**: the member's `guestId` in the path (and `memberId`
where the path wants one), the service token. This is the shape of
`StoryTestExample`.

**Spend, stays, bookings** (spendDetail, spendSummary, stays, dashboard).
The data has to exist first, created in the test with the service token:
- a reservation: `GET /shop/props/{propCode}?programAccountId=..&arrivalDate=..`
  -> pick a rate plan and room type from the response -> `POST /guests/{guestId}/reservations`
  with `clientAccounts[{programAccountId}]` -> `confNumber`;
- spend / booker spend / honors summaries: `PUT /businesses/{accountId}/dashboardSummary/spend`,
  `/bookerspend`, `/honors`.
Say in `open_questions` when the AC needs data you cannot create this way.

---

## 4. Where to look for more (when present on this machine)

- `_flows/<suite>/README.md` -- each converted case as an ordered diagram.
- `_audit/<suite>/steps.csv` -- every step of every case: method, endpoint, order.
- `src/main/java/com/hi/api/support/<suite>/cases/*Phases.java` -- a case as data;
  `SpecsN.java` -- method, path, token, template, extracts; `HooksN.java` -- ids cut
  from headers, dates, database statements. The travel-coordinator chain:
  `support/h4bbookonbehalf/cases/StaysTCStayTestPhases.java`; spend after a
  reservation: `support/accountdashboardregression/cases/SpenddetailReservationSpenddetailTestPhases.java`;
  booker spend: `.../BookerspendBookerSpendTestPhases.java`.
- `src/main/resources/templates/<suite>/...` -- converted request bodies. Copy one
  into `src/test/resources/templates/manual/<job>_<step>.json`; replace every
  `#Properties_...#` with a column the row or a helper fills, and every fixed
  credential with its placeholder. Never edit the converted file.

These are generated and gitignored: when absent, sections 1-3 are enough.

---

## 5. The database

The converted suites use it for the owner's one-time code (validation),
some role and status changes, and seeding spend / honors summaries. Prefer
the API step when one exists (roles: `setMemberRole`). When only the
database can do it, call `requireDb("<what for>")` first (it skips naming
the `database.*` keys) and `com.hi.api.db.Db.queryOne` / `execute` with `?`
parameters -- never a value pasted into SQL.

---

## 6. Configuration a story test may read

Only what cannot be created and is not a person: a fixed property code, a
partner id, a feature switch -- under `jira.<story>.<object>.<field>` with
`rowOrConfig`. **Never** a person's `.username` / `.password` or a person's
guest, member or account id, and never another story's keys: the
workbench's checks fail a test that reads one.
