# What the V2 tree has that this tree does not

Written for whoever picks this up next (including Cursor on the remote
machine). Measured 2026-10-02 by regenerating both trees from the same
XML and diffing, not by reading changelogs.

V2 lives outside git at `Downloads/Telegram Desktop/b2b-api-ai_V2/b2b-api-ai_V2`.
This file exists so V2 can be left alone without losing track of what is
only there.

---

## 1. Already ported here

| Fix | Why it matters |
|---|---|
| Removed the nested `def _jlit` inside `_render_assertion` | It made the name local for the whole function, so a use ~25 lines earlier raised `UnboundLocalError`. Crashed the whole convert on `GroupsDataSTAGE.xml`. An AST scan of all 63 converter `.py` files found this was the **only** instance of the shape. |
| `emit_setup_helper` now imports `com.hi.api.support.ImportedScenario` | Five other emitters that import `CtxFields` already imported it; this one did not. Currently an unused import here (no probe emitter yet) and therefore harmless — it becomes load-bearing the moment item 2's probe is ported. |

Verified **cold-vs-cold** (convert with the committed converter, snapshot,
convert with the fix, snapshot): exactly 2 files differ, both
`SetupHelper.java`, and the only change is that import line. `verify_all
--full` passes, including `java-compile` and `java-tests`.

> Do not compare a convert against whatever is already on disk. Doing that
> showed 36 changed files here, all of it stale state. Only cold-vs-cold
> attributes correctly.

---

## 2. In V2, NOT here — converter

Eleven functions in `ra_converter.py` (V2 is +588/-72 lines against this
tree). Two of them close long-standing gaps:

- `_render_meeting_shop_availability_probe`, `_probe_inventory_threshold`,
  `_probe_arrival_day_offsets` — **the availability retry loop**, which
  was previously a stub needing a hook-to-engine invocation.
- `_service_base_config_expr`, `_is_versioned_service_key` — **multi-service
  host routing**. Unversioned keys such as `mc_groupsdata` get their own
  `Config.get("services.<key>", "<recorded host>")` plus relaxed TLS rather
  than falling through to `baseUrl`. This is what the four 401s were:
  those cases talk to the Groups Data API, not the Goal token pattern.

Also: `_is_synthetic_empty_json_confirm`, `_merge_goal_shop_get_extracts`,
`_normalize_restassured_json_path` (RestAssured wants `roomRates[0].x`, not
`roomRates.0.x`), `_collapse_redundant_datasource_expansion`,
`_emit_forced_classpath_templates`, `_validate_method_csv_if_datasheet`;
plus `csv_emit_validation.py`, which fails the convert on a ragged CSV.

`groovy_translator.py` is nearly unchanged in V2 (+16/-5, one helper for a
`testcase_uniqueId` alias). **No new assertion shapes** — see item 5.

## 3. In V2, NOT here — framework

Seven classes, 2,845 lines, in committed `src/main/java/com/hi/api/rest/utilities/`:
`ShopBootstrap`, `ShopContextSync`, `GoalGroupCodeAsserts`,
`GoalNegativeResponseAsserts`, `ExternalDataSourceDefaults`,
`ShopRecoveryProbe`, `GoalShopAvailabilityProbe`.

They survive `--clean`, but none are in `tools/ra_converter/framework/`, so
the converter cannot bootstrap them into a fresh tree — while V2's emitter
emits calls to two of them. **Port these before porting item 2's emitter,
or the generated tree will not compile here.**

---

## 4. Hand-edited GENERATED files in V2 — do not copy, reproduce

Proved by regenerating V2 from its own XML and diffing:

| File | diff lines | Lost on `--clean` |
|---|---|---|
| `Hooks1.java` | 444 | 6 stubs' worth of real logic |
| `Specs1.java` | 202 | one hand-added spec (empty confirm) |
| `GroupsConfirmGroupsTestPhases.java` | 14 | the phase-to-spec rewiring for it |
| `PartialgoalregressionClient.java` | 237 | **the build** |

Only the first three are in V2's own `HAND_MAINTAINED_AFTER_CLEAN.md`. The
client is undocumented there.

Real stub counts, excluding the informational `STUB DataSource external
file` log lines: **this tree 52, V2's converter alone 8, V2 as shipped 2.**
So the improvement is overwhelmingly genuine converter work; hand-editing
accounts for the last 6.

## 5. Open decisions

- **`Config.getServiceBaseUrl(key)`** is the other reason V2's client was
  hand-edited. It cross-falls-back `hospitality_internal_all_v2` to
  `hospitality_internal_ro_v2`; the emitter emits
  `Config.get("services.<key>", baseUrl)`, which does not. Switching the
  emitter changes fallback semantics for every versioned service in every
  suite (`Config.baseUrl()` versus the client's constructor argument), and
  **this tree's `Config` has no `getServiceBaseUrl` at all.**
- **Two Groovy shapes still stubbed**: GOAL-2133 `groupCode` N-digit, and
  roomingList `correlationId`/`confNumber`. Teaching
  `groovy_translator.py` these converts 6 hand-filled stubs into real
  output. This is the highest-value remaining converter work.
- **`_JAVA_DECL_RX` ignores package-private declarations.** It requires
  `public|protected|private`, so a package-private method added to a
  bundled framework file is invisible to the consistency check and the fix
  silently never lands. Left alone deliberately: relaxing a detector risks
  spurious gate failures, and it needs its own verification pass.

## 6. Source-side findings, not converter bugs

- **`${datasource_200#...}` and `${datasource_201#...}` are referenced by 8
  of the 14 `ready/` suites and defined in none of them** — no test step, no
  project property. TopicAPI alone references `datasource_200` 14 times for
  `topicName`, `partitionId`, `offset`, `limit`, `timestamp`, `confNumber`
  and `X-JWT-Assertion`. That is where most empty DataSource columns come
  from, and requests built from them carry empty values. Check against
  ReadyAPI before calling it an authoring bug; if ReadyAPI also cannot
  resolve them it sends empty too, and empty columns are faithful.
  `ExternalDataSourceDefaults` (item 3) is the mechanism for injecting real
  values once they are known.
- `TopicAPISTAGE.xml` records some requests against a bare developer
  machine hostname (recorded in the XML, which is gitignored). Two
  PartialGoalRegression methods point at `services.localhost`. Neither
  resolves in any environment, so anything routed to them will fail
  wherever it runs.
