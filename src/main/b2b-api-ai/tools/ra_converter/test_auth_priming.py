"""@BeforeClass primes a token only for cases that cannot get one.

The previous rule asked "does this case contain a step whose path ends
in something token-shaped". It got both answers wrong on the goal suite:

  - 4 cases hitting an unversioned internal service send NO Authorization header at all
    (the project defines no OAuth2 profile; every authProfile in it is
    `Inherit From Parent` or `No Authorization`). They were marked as
    needing priming, so the framework would POST client_id and
    client_secret on behalf of calls that need no credential.
  - 1 case authenticates through `/realms/CORP/login`, which is a token
    fetch that does not say `token`. It was marked the same way.

Adding `/login` to the suffix list would have been the third guess in a
row. Whether a case can obtain an authorization value is a dataflow
question, and the converter already holds both halves of it: what the
Authorization header READS, and what the case's Groovy and Transfer
steps PUBLISH.

One deliberate asymmetry is pinned below: a SoapUI Properties step does
NOT count as a producer. SoapUI persists the last run's value there --
the goal project's `tokenId` step still carries a stale
`Bearer <stale literal>` literal -- so counting it would let a case with no
token source look satisfied and then send a dead token.

    python tools/ra_converter/test_auth_priming.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ra_converter as R  # noqa: E402


def _rest(name, headers=None, path="/x", method="GET", body=""):
    return R.RestStep(
        step_name=name, service="svc", method_name=name,
        resource_path=path, http_method=method, endpoint="",
        original_uri="https://h/svc/v1" + path,
        media_type="application/json", request_body=body,
        headers=headers or {}, path_params={}, query_params={})


def _groovy(name, script):
    return R.GroovyStep(step_name=name, script=script)


class _Case:
    def __init__(self, steps):
        self.steps = steps


AUTH = {"Authorization": "${tokenId#GeneratedTokenID}"}
PUBLISHES = 'tokenIDproperty.setPropertyValue("GeneratedTokenID", newTokenId)'


def test_a_case_that_sends_no_authorization_never_needs_priming():
    # The an unversioned internal service shape. Priming these transmits a credential for
    # calls that need none.
    case = _Case([_rest("GET_groupEvents"), _rest("POST_groupEvents",
                                                  method="POST")])
    assert R._case_needs_auth_priming(case) is False


def test_a_case_that_publishes_what_it_reads_never_needs_priming():
    # The /realms/CORP/login shape: the route is not called `token`, but
    # a Groovy step in the case sets the value the header reads.
    case = _Case([
        _rest("500TokenRequest", path="/realms/CORP/login", method="POST"),
        _groovy("Token", PUBLISHES),
        _rest("GET_Shop", headers=AUTH),
    ])
    assert R._case_needs_auth_priming(case) is False


def test_a_case_that_reads_a_token_nothing_produces_DOES_need_priming():
    case = _Case([_rest("GET_Shop", headers=AUTH)])
    assert R._case_needs_auth_priming(case) is True


def test_a_properties_step_default_is_not_a_producer():
    # SoapUI persists the last run's value. `tokenId` in the goal project
    # holds a stale `Bearer <stale literal>`; treating that as a producer
    # would skip priming and then send a dead token.
    case = _Case([
        R.PropertiesStep(step_name="tokenId",
                         properties={"GeneratedTokenID": "Bearer STALE"}),
        _rest("GET_Shop", headers=AUTH),
    ])
    assert R._case_needs_auth_priming(case) is True


def test_a_classic_oauth_token_step_still_counts():
    # The path the old rule got right must keep working.
    case = _Case([
        _rest("tokenRequest", path="/realms/applications/token",
              method="POST",
              body='{"client_id":"#client_id#","client_secret":"#c#"}'),
        _rest("GET_Shop", headers=AUTH),
    ])
    assert R._case_needs_auth_priming(case) is False


def test_an_empty_authorization_header_does_not_count_as_reading_one():
    case = _Case([_rest("GET_Shop", headers={"Authorization": "   "})])
    assert R._case_needs_auth_priming(case) is False


def test_authorization_refs_are_read_off_the_header_only():
    case = _Case([_rest("GET_Shop", headers=AUTH)])
    assert R._case_authorization_refs(case) == {"GeneratedTokenID"}
    # a body mentioning the same name must not count as reading it
    case2 = _Case([_rest("GET_Shop", body="${tokenId#GeneratedTokenID}")])
    assert R._case_authorization_refs(case2) == set()


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
