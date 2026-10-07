"""A call must reach the service it was recorded against.

A generated client holds ONE baseUrl and every method does
`baseUrl + path`, where path is only the ReadyAPI resourcePath. That is
correct while a project talks to one service and silently misroutes the
moment it talks to two. Both converted projects talk to several:

    goal  5 services over 7 hosts
    b2b   7 services -- a second partner service (8 calls) and
          internal-ro (25) had been riding the default base,
          and one vendor's service only worked because it was special-cased BY NAME

Three groupings were measured against the real XMLs before this one:

  1. ReadyAPI's own `service` attribute. No: `Goal Services API` spans
     7 bases, and five different b2b services share one base.
  2. "originalUri path minus the resourcePath substring". No: recorded
     URIs carry substituted path params, so the substring is not there.
  3. "path minus the last N segments, N = resourcePath segments". No:
     over-fragments to 9 keys (goal) and 16 (b2b).

What holds is that a VERSIONED path prefix identifies the service and
the host identifies the environment. The converter therefore names the
service and never picks the host -- a test host versus a stage host
is an environment
choice, and guessing it would bake one reviewer's environment into
several thousand generated calls.

The compatibility rule is pinned below. It is NOT "an unset key falls
back to baseUrl" -- this file used to say that, and it is false:
`Config.serviceBase` prefers the RECORDED base over the caller's, so an
unset key still routes to whatever host the recording held. The real
rule is that a call with no RECORDED base falls back to baseUrl, which
is why a loopback host must never be emitted as one.

    python tools/ra_converter/test_service_routing.py
"""
from __future__ import annotations

import os
import re
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ra_converter as R  # noqa: E402


def _Step(original_uri: str, endpoint: str):
    """A stand-in for the two host fields `_service_key_for_step` reads.

    `test_the_step_fields_these_fixtures_stand_in_for_exist` keeps this
    honest. A previous round of fixtures in this repo supplied a key
    that real rows did not carry, and the tests passed against a shape
    production never produces -- so a hand-made fixture is only worth
    as much as the check that it still matches the real dataclass.
    """
    return types.SimpleNamespace(original_uri=original_uri, endpoint=endpoint)


def test_the_step_fields_these_fixtures_stand_in_for_exist():
    fields = set(getattr(R.RestStep, "__dataclass_fields__", {}))
    assert {"original_uri", "endpoint"} <= fields, (
        "RestStep no longer carries both recorded hosts; _service_key_for_step "
        "and every _Step fixture below are reading dead attribute names")


# --------------------------------------------------------- the key rule

def test_a_versioned_prefix_is_the_service():
    cases = {
        "https://partner-host.example.com/partner/v2/realms/applications/token":
            ("partner_v2",
             "https://partner-host.example.com/partner/v2"),
        "https://alt-test.example.com/internal-all/v2/props/APROP/groups":
            ("internal_all_v2",
             "https://alt-test.example.com/internal-all/v2"),
        "https://corp-host.example.com/corporate/v2/realms/CORP/login":
            ("corporate_v2",
             "https://corp-host.example.com/corporate/v2"),
    }
    for uri, want in cases.items():
        assert R._service_key_for_uri(uri) == want, uri


def test_the_same_service_on_different_hosts_is_ONE_key():
    # The whole reason the converter must not pick a host. These three
    # are one service in three environments.
    keys = {R._service_key_for_uri(u)[0] for u in (
        "https://api-stage.example.com/internal-all/v2/groups",
        "https://api-test.example.com/internal-all/v2/groups",
        "https://alt-stage.example.com/internal-all/v2/groups",
    )}
    assert keys == {"internal_all_v2"}, keys


def test_an_unversioned_service_is_identified_by_its_host():
    key, base = R._service_key_for_uri(
        "https://dataservice.example.com/groupevents")
    assert key == "dataservice"
    assert base == "https://dataservice.example.com"


def test_a_port_is_part_of_the_base_but_not_the_key():
    key, base = R._service_key_for_uri("http://mocksvc.stg.example:9006/props/EPROP")
    assert key == "mocksvc"
    assert base == "http://mocksvc.stg.example:9006"


# ------------------------------------- loopback is never a service host
#
# This block exists because the test it replaced asserted the bug.
# `_service_key_for_uri("http://localhost:9006/...")` used to return
# the key "localhost", and the converter emitted that as BOTH a config
# key and a recorded base. Config.serviceBase prefers a recorded base
# over the caller's baseUrl, so 23 generated call sites across 18
# suites were pinned to this machine; the 101 that ran all died on
# ConnectException -- 49% of the failures AND 49% of the skips in one
# full run. The old test passed throughout.

def test_a_loopback_host_yields_no_key_and_no_base():
    for uri in ("http://localhost", "http://localhost:9006",
                "http://127.0.0.1:8080", "http://127.1.2.3",
                "http://0.0.0.0:9006", "http://host.docker.internal:9006"):
        assert R._service_key_for_uri(uri) == ("", ""), uri


def test_services_localhost_is_never_minted():
    """One knob named after this machine cannot route four services.

    `localhost` as a key collapsed the token, rateplan, groups and shop
    services of 18 suites onto a single `services.localhost` value --
    which cannot be correct for more than one of them at a time.
    """
    for uri in ("http://localhost/realms/applications/token",
                "http://localhost/groups/rateplan",
                "http://localhost:9006/props/EPROP/groups"):
        key, _ = R._service_key_for_uri(uri)
        assert key != "localhost", uri


def test_a_versioned_prefix_on_loopback_keeps_the_key_but_drops_the_host():
    """The prefix names the service; the host names the recorder."""
    key, base = R._service_key_for_uri(
        "http://localhost:9006/hospitality-partner/v2/realms/token")
    assert key == "hospitality_partner_v2"
    assert base == "", "a loopback host must never become a recorded base"


def test_a_laptop_hostname_keeps_its_key_so_it_stays_overridable():
    """Narrower than _is_local_host on purpose.

    A dotless machine name resolves on somebody's network, so its key
    is the knob that redirects it. Dropping the key would take that
    knob away. Loopback is different: no environment can make it right.
    """
    key, base = R._service_key_for_uri("http://5978-b3x4n32:8080/kafka")
    assert key == "5978_b3x4n32"
    assert base == "http://5978-b3x4n32:8080"


def test_the_endpoint_element_rescues_a_loopback_original_uri():
    """ReadyAPI records two hosts and they disagree; take the real one.

    Every loopback `tokenRequest` in this project carries a real
    <con:endpoint>, so the right auth host comes from the XML rather
    than from a guess -- the converter used to discard it by always
    preferring <con:originalUri>.
    """
    step = _Step("http://localhost", "https://authgw.example.test")
    key, base = R._service_key_for_step(step)
    assert key == "authgw"
    assert base == "https://authgw.example.test"
    assert not R._loopback_only_step(step)


def test_both_hosts_loopback_means_no_host_is_recoverable():
    step = _Step("http://localhost", "http://localhost:9006")
    assert R._service_key_for_step(step) == ("", "")
    assert R._loopback_only_step(step), "must be reported, not silently routed"


def test_a_real_original_uri_still_wins_over_the_endpoint():
    """The fix must not reorder the normal case."""
    step = _Step("https://partner-s.example.test/hospitality-partner/v2",
                 "https://somewhere-else.example")
    key, base = R._service_key_for_step(step)
    assert key == "hospitality_partner_v2"
    assert base == "https://partner-s.example.test/hospitality-partner/v2"


def test_a_step_with_no_recorded_host_is_not_called_loopback():
    assert not R._loopback_only_step(_Step("", ""))


def test_no_uri_means_no_key_so_the_caller_keeps_baseUrl():
    for bad in ("", None, "not a url", "/just/a/path"):
        assert R._service_key_for_uri(bad) == ("", ""), repr(bad)


def test_different_versions_of_one_service_are_different_keys():
    a, _ = R._service_key_for_uri("https://h/internal-all/v2/x")
    b, _ = R._service_key_for_uri("https://h/internal-all/v3/x")
    assert a != b, "v2 and v3 must not share an endpoint"


# ------------------------------------------------- the safety guarantee

def test_every_emitted_service_base_carries_its_recorded_base():
    """Read off the generated clients: no service loses its recorded base.

    This test used to assert the opposite -- that every emitted base
    ended in `, baseUrl`. That WAS the bug. For a service recorded under
    a path prefix, falling back to a bare baseUrl silently drops the
    prefix: the partner API lives at `<host>/hospitality-partner/v2`, so
    with `services.hospitality_partner_v2` unset the token POST went to
    `<baseUrl>/realms/applications/token` and every suite died on a 404
    at its first call. The prefix sat in the audit the whole time; it was
    never put into the emitted code.

    The contract now is Config.serviceBase(key, recordedBase, baseUrl),
    which keeps an explicit -DbaseUrl ABOVE the recorded value so a run
    can still be pointed at a stand-in.
    """
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    clients = os.path.join(root, "src/main/java/com/hi/api/rest/clients")
    if not os.path.isdir(clients):
        return                      # nothing converted in this tree yet
    checked = 0
    for name in os.listdir(clients):
        if not name.endswith(".java"):
            continue
        with open(os.path.join(clients, name), encoding="utf-8") as fh:
            src = fh.read()
        # the old shape must be gone: it is what caused the 404
        assert 'Config.get("services.' not in src, (
            f"{name}: still uses Config.get(\"services.…\", baseUrl), which "
            f"drops any recorded path prefix")
        for m in re.finditer(
                r'Config\.serviceBase\("([A-Za-z0-9_]+)",\s*"([^"]*)",\s*baseUrl\)',
                src):
            checked += 1
            key, recorded = m.group(1), m.group(2)
            # A recorded base that carries a path prefix is exactly the
            # case the old code broke, so it must be present and absolute.
            if recorded:
                assert recorded.startswith("http"), (
                    f"{name}: services.{key} recorded base is not absolute: "
                    f"{recorded!r}")
    assert checked > 0, "expected at least one service-routed call"


def test_a_recorded_path_prefix_survives_into_the_emitted_call():
    """The specific regression: /vN prefixes reach the generated Java.

    Guards the 404. A versioned service key is derived FROM a path
    prefix, so if the emitted call cannot show that prefix the prefix has
    been lost between the audit and the code.
    """
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    clients = os.path.join(root, "src/main/java/com/hi/api/rest/clients")
    if not os.path.isdir(clients):
        return
    versioned = []
    for name in os.listdir(clients):
        if not name.endswith(".java"):
            continue
        with open(os.path.join(clients, name), encoding="utf-8") as fh:
            src = fh.read()
        for m in re.finditer(
                r'Config\.serviceBase\("([A-Za-z0-9_]+_v\d+)",\s*"([^"]*)"',
                src):
            versioned.append((name, m.group(1), m.group(2)))
    for name, key, recorded in versioned:
        assert recorded, f"{name}: versioned services.{key} lost its recorded base"
        tail = recorded.split("://", 1)[-1]
        assert "/" in tail, (
            f"{name}: versioned services.{key} recorded base has no path "
            f"prefix, which is what the key was derived from: {recorded!r}")


def test_collect_service_bases_is_per_suite_not_global():
    """A global here handed b2b's report goal's services.

    The multi-XML driver preps EVERY suite before it emits any of them,
    so anything accumulated in module state during prep is merged by
    the time the reports are written.
    """
    class _Step(R.RestStep):
        pass

    def mk(uri):
        return R.RestStep(
            step_name="s", service="svc", method_name="m",
            resource_path="/x", http_method="GET", endpoint="",
            original_uri=uri, media_type="application/json",
            request_body="", headers={}, path_params={}, query_params={})

    class _Case:
        def __init__(self, steps):
            self.steps = steps

    a = _Case([mk("https://h1/alpha/v1/x")])
    b = _Case([mk("https://h2/beta/v1/x")])
    assert set(R._collect_service_bases([a])) == {"alpha_v1"}
    assert set(R._collect_service_bases([b])) == {"beta_v1"}
    assert set(R._collect_service_bases([a, b])) == {"alpha_v1", "beta_v1"}


# ----------------------------------------------- reporting, not routing

def test_a_local_only_host_is_recognised():
    """An UNSET key falls back to baseUrl.

    For most services that is merely imprecise. For one recorded against
    a host that exists only on the capturing machine it is a silent
    REDIRECT: calls that never left that laptop now reach a real
    environment. The report has to say so, which means recognising the
    shape.
    """
    for base in ("http://localhost:9006", "http://127.0.0.1",
                 "http://LOCALHOST", "http://a-dev-laptop"):
        assert R._is_local_host(base) is True, base
    for base in ("https://api.example.com", "https://a.b.c/x/v2",
                 "https://host.internal:8443", ""):
        assert R._is_local_host(base) is False, base


def _write_cfg(tmpdir, text):
    d = os.path.join(tmpdir, "src", "main", "resources")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "program_configuration.json"), "w",
              encoding="utf-8") as fh:
        fh.write(text)
    return tmpdir


def test_configured_keys_are_read_per_environment():
    import json
    import tempfile
    cfg = {
        "envA": {"services": {"alpha_v1": "https://a.example.com",
                              "beta_v1": ""}},
        "envB": {"services": {"alpha_v1": "https://a2.example.com"}},
        "envC": {"api_config": {}},
    }
    with tempfile.TemporaryDirectory() as tmp:
        _write_cfg(tmp, json.dumps(cfg))
        got = R._configured_service_keys(tmp)
    # beta_v1 is present but EMPTY -- that is unset, not configured
    assert sorted(got) == ["alpha_v1"], got
    assert sorted(got["alpha_v1"]) == ["envA", "envB"], got


def test_a_missing_config_is_not_an_error():
    """The file is gitignored, so a clean clone has none.

    It only decides how a line is worded; treating absence as a failure
    would make the converter refuse to run on a fresh checkout.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        assert R._configured_service_keys(tmp) == {}


def test_an_unparseable_config_is_not_an_error():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        _write_cfg(tmp, "{ this is not json")
        assert R._configured_service_keys(tmp) == {}
    with tempfile.TemporaryDirectory() as tmp:
        _write_cfg(tmp, "[1, 2, 3]")
        assert R._configured_service_keys(tmp) == {}


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"ok  {fn.__name__}")
        except Exception as ex:
            failed += 1
            print(f"FAIL {fn.__name__}: {ex}")
    if failed:
        sys.exit(1)
    print(f"{len(tests)} passed")
