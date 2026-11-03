# API story template

Fill in what you know; leave a line empty rather than guess. The sections
marked ★ are the ones automated tests fail on most often when they are
missing: where each value comes from, and what to capture from each response.

## Story
As a <who calls the API>, I want <what>, so that <why>.

## Endpoints
| Method | Path | Called by (role / client) |
|---|---|---|
| POST | /customers/{customerId}/orders | account owner |

## Before the call (preconditions)
- The state the data must be in, and how it is reached (e.g. "an account owner
  created with createCustomer(); an order placed for them").

## ★ Where each value comes from
Every path parameter, header and request field that is not a fixed value.

| Value | Used in | Comes from |
|---|---|---|
| customerId | path of step 2 | step 1 response `$.customerId` |
| memberId | body `${memberId}` | CSV column `member_id` |
| client id | header | configuration (the client id key) |

## ★ Request template
The body (or the name of the existing template), with each placeholder written
as `${name}` and listed in the table above.

```json
{ "memberId": "${memberId}", "role": "ADMIN" }
```

## ★ What to capture from each response
| Step | Field (JSON path) | Saved as | Used by |
|---|---|---|---|
| 1 | `$.customerId` | customerId | step 2 path |

## Acceptance criteria
One line each; every one names the status code and the field it is checked by.
- AC1: Given <state>, when <call>, then **201** and `$.orderId` is present.
- AC2: Given <state>, when <call with a missing field>, then **400** and `$.errors[0].field` is `name`.

## Negative and edge cases
- Not allowed (role, wrong owner) -> 403
- Not found -> 404
- Duplicate -> 409
- Field limits (length, format, required)

## Test data (CSV)
Columns and one example row, described rather than real values:

| member_id | role | expected_status |
|---|---|---|
| a valid member id | ADMIN | 201 |

## Out of scope
- What this story does not change.
