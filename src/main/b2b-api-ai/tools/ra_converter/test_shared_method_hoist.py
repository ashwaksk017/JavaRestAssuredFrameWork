"""Shared fluent methods must actually be inherited, not re-inlined per case.

Three defects, found by asking why `createProgramAccount380` carried a number
when only 94 distinct `createProgramAccount` bodies existed.

1. COLLECT/EMIT SHAPE MISMATCH (the big one).
   `collect_shared_fluent_phases` voted with `emit_stop_checks=True`, while
   `emit_test_class_per_suite` emits with `bool(stop_markers)` -- empty for
   every case outside a prefix-merged cluster. The flag is not cosmetic: it
   gates the `__restStepIdx` guards AND, in `_prepare_method_steps`, whether
   token steps are hoisted. So the voted body was one no case ever emitted:

     * `_shared_phase_matches` returned False for EVERY phase, so no per-case
       Support class inherited anything -- 4,059 method definitions covering
       1,454 distinct bodies, 2,605 of them byte-identical copies.
     * the name allocator met an unrecognised body on each pass and suffixed
       it again, so catalog `enrollGuest13` re-emerged as `enrollGuest128`.

   After the fix: 945 definitions, 4 copies, and 502 of 509 ScenarioSteps
   methods referenced (was 52).

2. UNBOUNDED CATALOG.
   `_pick_shared_fluent_variant` deliberately keeps a phase whose only vote is
   `case="catalog"`, so a suite outside this conversion can still inherit it.
   Nothing ever removed one, so residue from older emitters accumulated: 457
   of 509 ScenarioSteps methods had no caller anywhere.

3. ImportedRestClient MISSED CALL SITES OUTSIDE ITS THREE SOURCES.
   The interface was built from the catalog + support/scenario + rest/clients.
   But `domain/*Api` wrappers and the hand-written `dsl/` classes are tracked
   files that accumulate across every converted suite, and they call the
   client too. Converting a single XML -- or losing fluent_catalog.json --
   dropped the declarations they still call: 134 "cannot find symbol" errors
   on code the converter itself had emitted.

    python tools/ra_converter/test_shared_method_hoist.py
"""
from __future__ import annotations

import io
import os
import re
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

SRC = io.open(os.path.join(HERE, "ra_converter.py"), encoding="utf8").read()

# Matches the receiver-discovery regexes inside _collect_imported_client_methods.
WRAPPER_RX = re.compile(
    r"public Response (\w+)\(([^)]*)\)\s*\{\s*return\s+client\.(\w+)\(")
FIELD_RX = re.compile(r"\bImportedRestClient\s+(\w+)\s*[;=,)]")
ACCESSOR_RX = re.compile(r"\bImportedRestClient\s+(\w+)\s*\(")


# --------------------------------------------------------------- defect 1
def test_collect_and_emit_agree_on_stop_checks():
    """The vote must be cast on the shape the emitter will actually produce."""
    m = re.search(
        r"def collect_shared_fluent_phases.*?_fluent_render_groups\(\s*"
        r"case, service_class_name, emit_stop_checks=(\w+)\)",
        SRC, re.S)
    assert m, "collect_shared_fluent_phases no longer renders a plan"
    assert m.group(1) == "False", (
        "collect votes with emit_stop_checks=" + m.group(1) + "; the emit "
        "pass uses bool(stop_markers), which is empty for the vast majority "
        "of cases, so no shared body would ever match an emitted one")


def test_emit_still_derives_stop_checks_from_markers():
    """Cases that DO carry stop markers must keep their guards."""
    assert "emit_stop_checks = bool(stop_markers)" in SRC


def test_stop_guards_change_the_body_fingerprint():
    """Pins WHY the flag matters: guarded and unguarded are genuinely two
    different methods, so they can never be normalised together -- the only
    fix is for the two passes to agree."""
    from fluent_scenario import phase_body_key
    plain = ['this.fooRes = RestStep.exec(ctx).get("/x");']
    guarded = plain + [
        "__restStepIdx++;",
        "if (!__stopAfter.isEmpty() && __restStepIdx >= "
        "Integer.parseInt(__stopAfter)) { return self(); }",
    ]
    assert phase_body_key(plain) != phase_body_key(guarded)


# --------------------------------------------------------------- defect 1b
def test_every_shared_bootstrap_is_kept_not_just_the_best():
    """A case whose every phase was shared STILL emitted a full per-case
    Support class when its setup differed, because only the highest-voted
    bootstrap was retained. One suite has 115 distinct bootstraps, so the
    gate failed for almost everyone: 95 classes were pure boilerplate and
    only 12 of 579 tests entered through the shared class."""
    fin = SRC.split("def finalize_shared_fluent_phases", 1)[1][:4000]
    assert "self._shared_bootstraps.append(" in fin, (
        "finalize must keep every qualifying bootstrap variant")
    assert "self._shared_bootstrap = (" in fin, (
        "the primary must still be exposed for the ScenarioSteps base body")


def test_gate_returns_which_entry_class_not_a_bool():
    """With several bootstraps the answer is 'which one', not 'yes'."""
    gate = SRC.split("def _case_uses_only_shared_fluent", 1)[1][:1400]
    assert "return self.shared_entry_class(idx)" in gate
    assert "return None" in gate and "return False" not in gate


def _name_stub(bootstraps):
    """Emitter with just enough state to derive entry-class names."""
    from ra_converter import Emitter
    import types
    stub = types.SimpleNamespace(
        _shared_bootstraps=[{"body": b} for b in bootstraps],
        _entry_class_names=[],
        _ENTRY_FLOW_RX=Emitter._ENTRY_FLOW_RX,
        _ENTRY_GEN_RX=Emitter._ENTRY_GEN_RX,
    )
    stub._derive_entry_class_names = types.MethodType(
        Emitter._derive_entry_class_names, stub)
    stub.shared_entry_class = types.MethodType(
        Emitter.shared_entry_class, stub)
    return stub


FLOW_A_UPDATEEMAIL = [
    'ImportedScenario.runSetup("flow_A", client, ctx, row, softAssert, holder, testCaseId);',
    'CtxFields.generateStandard(ctx, "Properties", "updateemail", "name");',
]
FLOW_B_NAME = [
    'ImportedScenario.runSetup("flow_B", client, ctx, row, softAssert, holder, testCaseId);',
    'CtxFields.generateStandard(ctx, "Properties", "name");',
]


def test_entry_class_is_named_after_what_its_bootstrap_does():
    """`CustomerOnboarding20` told you nothing. The name has to carry the
    setup flow and the generated fields, or every reader has to open the
    class to find out which of 113 setups they are looking at."""
    stub = _name_stub([FLOW_A_UPDATEEMAIL, FLOW_B_NAME])
    assert stub.shared_entry_class(0) == "OnboardingFlowAUpdateemailName"
    assert stub.shared_entry_class(1) == "OnboardingFlowBName"


def test_entry_numbering_is_scoped_to_its_own_name_group():
    """Two bootstraps a name cannot separate share a base and differ by a
    counter -- but only against EACH OTHER, never a global ranking."""
    other = list(FLOW_A_UPDATEEMAIL)
    other.append('CtxFields.seedFromRow(ctx, row, "Other.");')
    stub = _name_stub([FLOW_A_UPDATEEMAIL, FLOW_B_NAME, other])
    names = [stub.shared_entry_class(i) for i in range(3)]
    assert sorted(names) == ["OnboardingFlowAUpdateemailName",
                             "OnboardingFlowAUpdateemailName2",
                             "OnboardingFlowBName"], names


def test_entry_names_do_not_depend_on_popularity_order():
    """The regression: names were indexes into a list ordered by how many
    cases voted, so adding ONE case could renumber unrelated entry classes
    and churn every test that named them. Reordering the list must not
    change which name a given bootstrap gets."""
    other = list(FLOW_A_UPDATEEMAIL) + ['CtxFields.seedFromRow(ctx, row, "X.");']
    forward = _name_stub([FLOW_A_UPDATEEMAIL, FLOW_B_NAME, other])
    reverse = _name_stub([other, FLOW_B_NAME, FLOW_A_UPDATEEMAIL])
    got = {}
    for i, body in enumerate([FLOW_A_UPDATEEMAIL, FLOW_B_NAME, other]):
        got[tuple(body)] = forward.shared_entry_class(i)
    for i, body in enumerate([other, FLOW_B_NAME, FLOW_A_UPDATEEMAIL]):
        assert reverse.shared_entry_class(i) == got[tuple(body)], (
            "name moved when the list was reordered")


def test_entry_name_survives_a_bootstrap_with_no_setup_flow():
    stub = _name_stub([['CtxFields.seedFromRow(ctx, row, "Properties.");']])
    assert stub.shared_entry_class(0) == "Onboarding"


def test_entry_name_trims_whole_parts_not_half_a_word():
    """A 20-field generator produced `...Generatedemailaddres` -- unreadable,
    and two different fields can collide on one truncated prefix."""
    long_keys = ", ".join(f'"generatedemailaddress{i}"' for i in range(8))
    stub = _name_stub([[
        'ImportedScenario.runSetup("flow_A", client, ctx, row, softAssert, holder, testCaseId);',
        f'CtxFields.generateStandard(ctx, "Properties", {long_keys});',
    ]])
    name = stub.shared_entry_class(0)
    assert len(name) <= 48, name
    assert not name.endswith("addres"), name
    assert name.startswith("Onboarding"), name


def test_shared_entry_start_takes_the_case_id():
    """A shared entry serves many cases, so it cannot hard-code the ReadyAPI
    case id the way a per-case Support class does. Without passing it, every
    shared-entry test would report itself as "imported"."""
    tmpl = SRC.split("def _emit_suite_customer_onboarding", 1)[1][:4000]
    assert "String defaultTestCaseId" in tmpl
    assert 'row.getOrDefault("test_case_id", defaultTestCaseId)' in tmpl
    emit = SRC.split("start_extra = (", 1)[1][:200]
    assert "_jlit(case.name)" in emit


def test_entry_classes_import_what_a_bootstrap_body_needs():
    """A bootstrap is arbitrary translated Groovy -- Config lookups,
    CtxFields generators, Db calls. The two imports the bootstrap-less entry
    class needed produced 409 "cannot find symbol" errors across 63 files."""
    tmpl = SRC.split("def _emit_suite_customer_onboarding", 1)[1][:4000]
    for need in ("com.ak.api.support.CtxFields",
                 "com.ak.api.config.Config",
                 "com.ak.api.db.Db",
                 "com.ak.api.rest.utilities.RestStep"):
        assert need in tmpl, need


def test_scenario_steps_response_fields_cover_every_bootstrap():
    """A subclass overriding bootstrap() still assigns response fields
    declared on the base, so the base must declare all of them."""
    fn = SRC.split("def _emit_scenario_steps", 1)[1][:1200]
    assert "for boot in self._shared_bootstraps:" in fn


# --------------------------------------------------------------- defect 1c
def test_singleton_bodies_go_on_a_PER_SUITE_base():
    """A body only one case renders cannot clear the >=2 bar for the
    framework base, and that single method used to force an entire per-case
    Support class into existence -- 567 of them.

    They cannot simply move to `ScenarioSteps`: at 509 methods that class
    already uses 8,656 constant-pool entries, 13% of the 65,535 ceiling, so
    about 17 per method and room for ~3,850. One suite needs ~1,450. Shared
    across eighteen suites it would overflow. A per-suite base keeps each
    one independent.
    """
    fn = SRC.split("def _emit_suite_steps_base", 1)[1][:6000]
    assert "_suite_scenario_pkg()" in fn, "the base must live in the SUITE package"
    assert "extends ScenarioSteps<S>" in fn, "it must sit under the framework base"


def test_suite_base_excludes_fields_the_framework_base_declares():
    """Re-declaring a parent field hides it, splitting one response in two."""
    fn = SRC.split("def _emit_suite_steps_base", 1)[1][:3000]
    assert "self._framework_resp" in fn


def test_fallback_support_extends_the_suite_base():
    """Its methods are filtered against BOTH bases, so inheriting only the
    framework one leaves the suite-local ones unresolvable -- which is
    exactly what `cannot find symbol: deleteProgramAccount15()` was."""
    assert "_parent = (_suite_base" in SRC
    assert 'f"extends {_parent}<CustomerOnboarding> {{"' in SRC


def test_fallback_filters_against_both_bases():
    """Checking only the framework base let a fallback class re-declare 86
    methods the suite base already had -- covariant overrides, so they
    compiled and nothing flagged them."""
    blk = SRC.split("unique_flow = unique_method_defs", 1)[1][:700]
    assert "_shared_phase_matches" in blk and "_suite_phase_matches" in blk
    assert "_shared_verify_matches" in blk and "_suite_verify_matches" in blk


def test_bootstraps_have_no_two_vote_bar():
    """A phase shared by one case costs a method on a base every suite
    carries; a bootstrap costs one small entry class in this suite's own
    package. Requiring two left 52 cases owning a Support class for setup."""
    fin = SRC.split("def finalize_shared_fluent_phases", 1)[1][:4000]
    boots = fin.split("self._shared_bootstraps = []", 1)[1][:900]
    assert "len(infos) < 2" not in boots, (
        "the >=2 bar must not apply to bootstraps")


# --------------------------------------------------------------- defect 2
def _emitter_stub(votes, shared, converted):
    """Bind the real methods onto a bare namespace: constructing an Emitter
    needs a parsed XML, and the prune touches none of that state."""
    from ra_converter import Emitter
    stub = types.SimpleNamespace(
        _fluent_phase_votes=votes,
        _fluent_verify_votes={},
        _shared_phases=dict(shared),
        _shared_verifies={},
        _converted_suites=set(converted),
        _pruned_shared_phases=set(),
        _pruned_shared_verifies=set(),
    )
    stub._owning_suites = types.MethodType(Emitter._owning_suites, stub)
    stub._prune_dead_catalog_entries = types.MethodType(
        Emitter._prune_dead_catalog_entries, stub)
    return stub


def test_prune_drops_residue_from_a_reconverted_suite():
    votes = {"deadPhase": {"k": [
        {"case": "catalog", "cases": ["suiteA/case1", "suiteA/case2"]}]}}
    stub = _emitter_stub(votes, {"deadPhase": {}}, {"suiteA"})
    stub._prune_dead_catalog_entries()
    assert "deadPhase" not in stub._shared_phases
    assert "deadPhase" in stub._pruned_shared_phases


def test_prune_keeps_a_phase_another_suite_still_inherits():
    """suiteB was not converted in this run, so its emitted files are still
    on disk and still call this method."""
    votes = {"livePhase": {"k": [
        {"case": "catalog", "cases": ["suiteB/case1"]}]}}
    stub = _emitter_stub(votes, {"livePhase": {}}, {"suiteA"})
    stub._prune_dead_catalog_entries()
    assert "livePhase" in stub._shared_phases


def test_prune_keeps_anything_a_real_case_voted_for():
    votes = {"votedPhase": {"k": [
        {"case": "catalog", "cases": ["suiteA/case1"]},
        {"case": "suiteA/case9"}]}}
    stub = _emitter_stub(votes, {"votedPhase": {}}, {"suiteA"})
    stub._prune_dead_catalog_entries()
    assert "votedPhase" in stub._shared_phases


def test_prune_keeps_unattributable_entries():
    """An older catalog recorded no cases. We cannot prove such an entry is
    dead, so it stays -- deleting it could break a suite still on disk."""
    votes = {"oldPhase": {"k": [{"case": "catalog", "cases": []}]}}
    stub = _emitter_stub(votes, {"oldPhase": {}}, {"suiteA"})
    stub._prune_dead_catalog_entries()
    assert "oldPhase" in stub._shared_phases


def test_prune_is_a_noop_when_nothing_was_converted():
    votes = {"p": {"k": [{"case": "catalog", "cases": ["suiteA/c"]}]}}
    stub = _emitter_stub(votes, {"p": {}}, set())
    stub._prune_dead_catalog_entries()
    assert "p" in stub._shared_phases


def test_pruned_names_are_dropped_from_the_saved_catalog():
    """Otherwise the next run re-seeds exactly what this run pruned."""
    save = SRC.split("def _save_fluent_catalog", 1)[1][:2000]
    assert "_pruned_shared_phases" in save
    assert "_pruned_shared_verifies" in save


# --------------------------------------------------------------- defect 3
def test_converter_source_has_no_stray_control_characters():
    """A `\\b` that lost its raw-string escape becomes a real backspace byte.

    That is invisible to grep and to a code review -- the pattern still LOOKS
    like `\\bImportedRestClient...` -- but it compiles to
    `\\x08ImportedRestClient...` and matches nothing, silently. It cost a full
    convert-and-compile cycle to find. Any control character other than tab
    or newline in the emitter source is a bug of this shape.
    """
    bad = {i: repr(ch) for i, ch in enumerate(SRC)
           if ord(ch) < 32 and ch not in "\t\n\r"}
    assert not bad, "control characters in ra_converter.py at " + str(
        list(bad.items())[:5])


def test_receiver_regexes_compile_to_what_they_look_like():
    """Guards the same failure from the other side: the pattern text in the
    source must match what actually gets compiled."""
    m = re.search(r'field_rx = re\.compile\(r"([^"]+)"\)', SRC)
    assert m, "field_rx moved or changed shape"
    assert m.group(1) == r"\bImportedRestClient\s+(\w+)\s*[;=,)]", m.group(1)
    assert re.compile(m.group(1)).findall(
        "  private final ImportedRestClient client;\n") == ["client"]


def test_interface_scans_the_whole_package_tree():
    body = SRC.split("def _collect_imported_client_methods", 1)[1][:6000]
    assert "ImportedRestClient" in body and "os.walk(pkg_dir)" in body, (
        "domain/ and dsl/ call sites must feed ImportedRestClient; scanning "
        "only the catalog, support/scenario and rest/clients dropped them")


def test_receivers_are_discovered_not_hardcoded():
    """A file declares how it reaches the client; we read that, so a new
    accessor name needs no converter change."""
    body = SRC.split("def _collect_imported_client_methods", 1)[1][:6000]
    assert "field_rx" in body and "accessor_rx" in body


def test_receivers_are_pooled_across_files_not_per_file():
    """`client()` is declared in domain/DomainApis.java and called from
    dsl/CustomerOnboarding.java. A per-file pass sees the declaration and
    the call site in different files and matches neither, which left
    `salesforceID` undeclared and the build broken."""
    body = SRC.split("def _collect_imported_client_methods", 1)[1][:6000]
    collect = body.index("needles |=")
    use = body.index("_find_client_calls(text, tuple(sorted(needles)))")
    assert collect < use, "receivers must be gathered before they are used"
    assert "client_texts" in body, (
        "the scan must keep every file and re-walk them after pooling "
        "receivers, not match within a single pass over each file")


def test_object_methods_never_reach_the_interface():
    """`DomainApis` legitimately calls `raw.getClass()` on the proxy. Reading
    that as a client operation declared `Response getClass()` on the
    interface -- and `getClass` is final on Object, so it does not compile."""
    from ra_converter import _OBJECT_METHODS
    for name in ("getClass", "hashCode", "equals", "toString", "wait",
                 "notify", "notifyAll", "clone", "finalize"):
        assert name in _OBJECT_METHODS, name
    guard = SRC.split("def _collect_imported_client_methods", 1)[1][:900]
    assert "_OBJECT_METHODS" in guard, (
        "the guard belongs in add(), so every source is covered, not just "
        "the call-site scan")


def test_field_receiver_is_found():
    sample = ("    private final ImportedRestClient client;\n"
              "    public Response x() { return client.deleteGuest(t, g); }\n")
    assert FIELD_RX.findall(sample) == ["client"]


def test_accessor_receiver_is_found():
    """`apis.client().salesforceID(...)` in dsl/ -- the receiver is a call,
    not a field, which a `client.` needle cannot see."""
    sample = "    public ImportedRestClient client() { return raw; }\n"
    assert ACCESSOR_RX.findall(sample) == ["client"]


def test_accessor_call_site_is_matched_with_the_derived_needle():
    from ra_converter import _find_client_calls
    text = ('exec("readSalesforceLead", 200, (body, q, h) ->\n'
            '        apis.client().salesforceID(sfToken(), required("leadId")));')
    found = _find_client_calls(text, ("client().",))
    assert [n for n, _a in found] == ["salesforceID"], found


def test_default_receivers_still_work_when_none_are_passed():
    """The original callers pass no needles and must be unaffected."""
    from ra_converter import _find_client_calls
    text = "guests.hHonorsEnroll(token, body);"
    assert [n for n, _a in _find_client_calls(text)] == ["hHonorsEnroll"]


def test_wrapper_signature_is_taken_verbatim():
    """A wrapper that forwards under the same name states the client
    signature exactly -- better than inferring it from the arguments."""
    sample = (
        "    public Response httpRequest200EnrollGuest(String token, "
        "Map<String, String> queryParams, String requestBody) {\n"
        "        return client.httpRequest200EnrollGuest(token, queryParams, "
        "requestBody);\n    }\n")
    m = WRAPPER_RX.search(sample)
    assert m and m.group(1) == m.group(3) == "httpRequest200EnrollGuest"
    assert m.group(2) == ("String token, Map<String, String> queryParams, "
                          "String requestBody")


def test_renaming_wrapper_is_not_taken_verbatim():
    """Forwarding under a DIFFERENT name says nothing about the client's own
    signature; taking it would declare a bogus overload."""
    sample = ("    public Response createGuest(String token) {\n"
              "        return client.enrollGuestV2(token);\n    }\n")
    m = WRAPPER_RX.search(sample)
    assert m and m.group(1) != m.group(3)


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print("ok  " + fn.__name__)
        except Exception as ex:
            failed += 1
            print("FAIL " + fn.__name__ + ": " + repr(ex))
    if failed:
        print("")
        print(str(failed) + " of " + str(len(tests)) + " FAILED")
        sys.exit(1)
    print(str(len(tests)) + " passed")
