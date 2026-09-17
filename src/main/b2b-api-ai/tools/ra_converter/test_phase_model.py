"""Stage 1a guards: phase specs are captured, shapes are keyed on the call
and nothing else, the registry survives the catalog's invalidation rules.

Runs as a script (verify_all) or under pytest.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import phase_model as pm  # noqa: E402
import ra_converter  # noqa: E402


def _spec(**over) -> pm.PhaseSpec:
    base = dict(
        suite="s1", case="B2B-1", step_name="get_account", sid="get_account",
        verb="GET", path="/businesses/{accountId}", client_method="readProgramAccount",
        receiver="client", template_expr=None, regen=False, expected_status=200,
        query=(), headers=(), path_args=('TestSupport.ctxGet(ctx, "Properties.accountId")',),
        token_expr='ctx.getOrDefault("tokenId.GeneratedTokenID", "")',
        poll=None, extracts=(), assertion_types=(), setup=False,
    )
    base.update(over)
    return pm.PhaseSpec(**base)


# ---------------------------------------------------------------------------
# shape identity
# ---------------------------------------------------------------------------

def test_shape_ignores_everything_that_is_data():
    """Step name, ctx key, template, status, extracts, query VALUES: all data.

    These are exactly the things the text-based dedup treated as identity,
    which is how 211 `GET /businesses/{id}` calls became 211 methods.
    """
    a = _spec()
    b = _spec(step_name="http_request_200_2", sid="http_request_200_2",
              case="B2B-9", suite="s2", expected_status=404,
              path_args=('TestSupport.ctxGet(ctx, "PropertiesDetails.accountID")',),
              extracts=(("x_Response_accountId", "json", "accountId"),),
              assertion_types=("JsonPath Match",),
              query=(("q", "#other_col#"),) if False else ())
    assert a.shape().id == b.shape().id, (a.shape(), b.shape())


def test_shape_changes_with_verb_path_or_key_sets():
    base = _spec().shape().id
    assert _spec(verb="DELETE").shape().id != base
    assert _spec(path="/businesses/{accountId}/activate").shape().id != base
    assert _spec(query=(("include", "#col#"),)).shape().id != base
    assert _spec(headers=(("X-Op", "#col#"),)).shape().id != base
    assert _spec(template_expr="Templates.X").shape().id != base, "body vs no body is a different call"


def test_shape_query_values_do_not_matter_but_keys_do():
    a = _spec(query=(("email", "#Properties_Email#"),))
    b = _spec(query=(("email", "#Properties_generatedemailAddress#"),))
    c = _spec(query=(("emailAddress", "#Properties_Email#"),))
    assert a.shape().id == b.shape().id
    assert a.shape().id != c.shape().id


def test_path_parameter_names_do_not_matter():
    a = _spec(path="/guests/{guestId}/businesses")
    b = _spec(path="/guests/{id}/businesses")
    c = _spec(path="/guests/2000123456/businesses")   # author-baked literal id
    assert a.shape().id == b.shape().id == c.shape().id
    assert pm.normalize_path("/guests/{guestId}/businesses/") == "/guests/{}/businesses"


def test_engine_name_comes_from_the_vocabulary():
    import phase_vocabulary
    sh = _spec().shape()
    assert sh.engine_name == phase_vocabulary.canonical_name("GET", "/businesses/{}")
    assert sh.bucket == "businesses"
    assert pm.bucket_of("/v2/realms/guests/enroll") == "realms"
    assert pm.bucket_of("/services/data/v58.0/sobjects/Account") == "services"


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------

def test_registry_merge_replaces_a_suite_and_keeps_others():
    reg = pm.ShapeRegistry()
    reg.merge([_spec(), _spec(step_name="again", sid="again")], "s1")
    reg.merge([_spec(suite="s2")], "s2")
    (entry,) = reg.data.values()
    assert entry["suites"] == {"s1": 2, "s2": 1}
    # re-converting s1 with ONE call replaces its count, does not add to it
    reg.merge([_spec()], "s1")
    assert reg.data[_spec().shape().id]["suites"] == {"s1": 1, "s2": 1}
    st = reg.stats()
    assert st["shapes"] == 1 and st["shared"] == 1 and st["steps"] == 2


def test_registry_drops_a_shape_no_suite_uses_any_more():
    reg = pm.ShapeRegistry()
    reg.merge([_spec()], "s1")
    reg.merge([_spec(verb="DELETE")], "s1")     # s1 re-converted: GET is gone
    assert [e["verb"] for e in reg.data.values()] == ["DELETE"]


def test_registry_counts_setup_helper_renderings():
    """A case with an assigned flow skips the prefix steps and calls
    SetupHelper, so the helper body is the ONLY rendering of those calls."""
    reg = pm.ShapeRegistry()
    reg.merge([_spec(setup=True)], "s1")
    assert reg.stats()["shapes"] == 1


def test_registry_round_trips_through_json():
    reg = pm.ShapeRegistry()
    reg.merge([_spec(), _spec(verb="POST", template_expr="Templates.T")], "s1")
    again = pm.ShapeRegistry(json.loads(json.dumps(reg.to_dict())))
    assert again.to_dict() == reg.to_dict()


def test_report_renders_without_identifying_values():
    reg = pm.ShapeRegistry()
    reg.merge([_spec(), _spec(suite="s2", case="B2B-77")], "s1")
    text = pm.render_report(reg, phases_today=1304)
    assert "distinct call shapes: 1" in text
    assert "engine methods after Stage 1b: 1" in text and "cached today: 1304" in text
    assert "B2B-77" not in text and "s2" not in text.split("== most shared")[1]


# ---------------------------------------------------------------------------
# capture: the renderer records what it emitted
# ---------------------------------------------------------------------------

def _rest_step(**over):
    kw = dict(
        step_name="get_account_after_activate", service="", method_name="ReadProgramAccount",
        resource_path="/businesses/{accountId}", http_method="GET", endpoint="",
        original_uri="", media_type="application/json", request_body="",
        headers={"authorization": "${tokenId#GeneratedTokenID}"},
        path_params={"accountId": "${PropertiesDetails#accountID}"},
        query_params={},
    )
    kw.update(over)
    return ra_converter.RestStep(**kw)


def _emitter():
    em = ra_converter.Emitter(output_dir=".", package_root="com.ak.api",
                              suite_name="unit")
    em._reset_per_method_state()
    em._current_case = "B2B-1_unit"
    em._current_case_obj = None
    em._current_prefix = ""
    return em


def test_renderer_captures_a_spec_that_matches_the_java():
    pm.reset_run()
    em = _emitter()
    step = _rest_step()
    lines = em._render_rest_step_body(step, "Programaccountregression")
    java = "\n".join(lines)
    assert '.name("get_account_after_activate")' in java, java
    (spec,) = em._phase_specs.values()
    assert spec.sid == "get_account_after_activate"
    assert spec.verb == "GET" and spec.path == "/businesses/{accountId}"
    # no "Valid HTTP Status Codes" assertion on the step -> the renderer
    # emits -1 (no expectation) and the spec must say the same, not 200
    assert spec.expected_status == em._rest_expected_status(step) == -1
    assert spec.template_expr is None
    assert spec.path_args and "PropertiesDetails" in spec.path_args[0]
    assert "GeneratedTokenID" in spec.token_expr
    assert spec.setup is False
    # and it reached the run-wide accumulator under the suite name
    assert "unit" in pm.RUN_SPECS and len(pm.RUN_SPECS["unit"]) == 1
    # the Java the spec describes really is the Java that was emitted
    assert f'.expectedStatus({spec.expected_status})' in java
    assert spec.path_args[0] in java


def test_capture_never_breaks_emission():
    """Bookkeeping failure must not change what is emitted."""
    pm.reset_run()
    em = _emitter()
    import phase_model
    orig = phase_model.record
    try:
        def boom(*a, **k):
            raise RuntimeError("registry down")
        phase_model.record = boom
        lines = em._render_rest_step_body(_rest_step(), "Programaccountregression")
    finally:
        phase_model.record = orig
    assert any('.name("get_account_after_activate")' in ln for ln in lines)


def test_repeated_step_name_in_one_method_captures_both():
    pm.reset_run()
    em = _emitter()
    em._render_rest_step_body(_rest_step(), "Programaccountregression")
    em._render_rest_step_body(_rest_step(), "Programaccountregression")
    assert len(em._phase_specs) == 2, list(em._phase_specs)


def test_post_with_body_records_template_and_body_shape():
    pm.reset_run()
    em = _emitter()
    step = _rest_step(step_name="HHonorsEnroll", method_name="HHonorsEnroll",
                      resource_path="/realms/guests/enroll", http_method="POST",
                      request_body='{"email": "${#TestCase#Properties.Email}"}',
                      path_params={}, query_params={"email": "${#TestCase#Properties.Email}"})
    em._render_rest_step_body(step, "Programaccountregression")
    (spec,) = em._phase_specs.values()
    assert spec.verb == "POST" and spec.template_expr, spec
    assert spec.shape().takes_body is True
    assert spec.shape().query_keys == ("email",)
    assert spec.shape().engine_name == "enrollGuest"


# ---------------------------------------------------------------------------
# catalog: shapes survive the invalidation that discards cached phases
# ---------------------------------------------------------------------------

def test_every_catalog_save_carries_the_registry():
    """Regression: the first run registered 0 shapes because a later save
    path overwrote the key. The merge now lives in save_fluent_catalog."""
    import fluent_scenario as fs
    orig = fs.catalog_path
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "fluent_catalog.json")
        fs.catalog_path = lambda *a, **k: path
        try:
            pm.reset_run()
            pm.record(_spec(suite="probe"), ("k1",))
            fs.save_fluent_catalog(fs._empty_catalog())          # shapes: {} going in
            assert len(json.load(open(path))["shapes"]) == 1     # registry merged on the way out
            fs.save_fluent_catalog({"phases": {}, "clientMethods": []})  # a save that never had the key
            assert len(json.load(open(path))["shapes"]) == 1
        finally:
            fs.catalog_path = orig
            pm.reset_run()


def test_catalog_keeps_shapes_across_an_emitter_change_but_not_a_vocabulary_change():
    import fluent_scenario as fs
    orig = fs.catalog_path
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "fluent_catalog.json")
        fs.catalog_path = lambda *a, **k: path
        try:
            reg = pm.ShapeRegistry()
            reg.merge([_spec()], "s1")
            base = fs._empty_catalog()
            base["shapes"] = reg.to_dict()
            base["phases"] = {"readProgramAccount": {"key": "k", "body": [], "resp": []}}
            # 1. emitter changed: phases discarded, shapes kept
            stale = dict(base, emitterVersion="0000")
            json.dump(stale, open(path, "w"))
            got = fs.load_fluent_catalog()
            assert got["phases"] == {} and got["shapes"] == reg.to_dict()
            # 2. vocabulary changed: engine names came from it -- rebuild
            stale = dict(base, vocabularyVersion="old-vocab")
            json.dump(stale, open(path, "w"))
            got = fs.load_fluent_catalog()
            assert got["shapes"] == {}
            # 3. untouched: everything kept, and the key always exists
            json.dump(base, open(path, "w"))
            got = fs.load_fluent_catalog()
            assert got["shapes"] == reg.to_dict()
            assert "shapes" in fs._empty_catalog()
        finally:
            fs.catalog_path = orig


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"ok  {fn.__name__}")
        except Exception as ex:
            failed += 1
            print(f"FAIL {fn.__name__}: {type(ex).__name__}: {ex}")
    if failed:
        sys.exit(1)
    print(f"{len(tests)} passed")
