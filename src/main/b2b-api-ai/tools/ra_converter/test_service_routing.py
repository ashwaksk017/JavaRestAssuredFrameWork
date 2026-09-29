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

The compatibility rule is pinned below and is what makes this safe to
ship against a working suite: an unset `services.<key>` falls back to
baseUrl, so a project whose calls all belong to one service emits
exactly what it emitted before.

    python tools/ra_converter/test_service_routing.py
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ra_converter as R  # noqa: E402


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
    key, base = R._service_key_for_uri("http://localhost:9006/props/EPROP")
    assert key == "localhost"
    assert base == "http://localhost:9006"


def test_no_uri_means_no_key_so_the_caller_keeps_baseUrl():
    for bad in ("", None, "not a url", "/just/a/path"):
        assert R._service_key_for_uri(bad) == ("", ""), repr(bad)


def test_different_versions_of_one_service_are_different_keys():
    a, _ = R._service_key_for_uri("https://h/internal-all/v2/x")
    b, _ = R._service_key_for_uri("https://h/internal-all/v3/x")
    assert a != b, "v2 and v3 must not share an endpoint"


# ------------------------------------------------- the safety guarantee

def test_every_emitted_service_base_falls_back_to_baseUrl():
    """The compatibility rule, read off the generated clients.

    If any emitted base lacked the `, baseUrl` fallback, a suite with an
    unfilled config would send calls to the empty string instead of
    behaving exactly as it did before this change.
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
        for m in re.finditer(r'Config\.get\("services\.([A-Za-z0-9_]+)"([^)]*)\)',
                             src):
            checked += 1
            assert m.group(2).strip() == ", baseUrl", (
                f"{name}: services.{m.group(1)} has no baseUrl fallback: "
                f"{m.group(0)}")
    assert checked > 0, "expected at least one service-routed call"


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
