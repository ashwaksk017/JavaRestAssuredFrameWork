"""A credential must reach the request from config, never from the XML.

Two holes, both found converting a second, unrelated ReadyAPI project,
and both invisible against the first one.

  1. The author kept secrets OUT of the project XML, the way this
     framework asks: every credential was a `${= ... }` expression that
     loaded `local-config/<active-env>.properties` at run time. The
     converter had no rule for it, so all four fell to the
     `#groovy_expr#` catch-all -- and that stub is not inert. It became
     the CSV cell VALUE, so 45 of 57 rows would have posted the literal
     text `#groovy_expr#/*import java.util.Properties;def pr=context...*/`
     as their client_id. A 401 whose body explains nothing.

     The expression's whole meaning is "read key K for the active
     environment", which is what Config.get(K) already does, so the
     honest translation is `#K#` routed to config.

  2. One token step in the same project hardcoded all four values
     instead. Those were copied verbatim into a generated template on
     disk -- and from there into the Allure attachment and the request
     log. The standing rule for this repo is that a credential is
     supplied in exactly one file and readable nowhere else, so a
     literal in a credential payload is rewritten to `#key#` too.

The second rule is the dangerous one to get wrong, because `username`
and `password` are ordinary test data in a create-user body. It is
therefore gated on the payload also carrying a client identifier or
client secret -- the pairing that makes it an OAuth-style grant -- and
matches leaf names exactly, so `passwordChangedDate` is left alone.
Both halves are pinned below; loosen either and real test data starts
being replaced by the runner's own credentials.

    python tools/ra_converter/test_external_secrets.py
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ra_converter as R  # noqa: E402


def _secrets_expr(key: str) -> str:
    """The idiom verbatim, as ReadyAPI stores it."""
    return (
        'import java.util.Properties;'
        'def pr=context.testCase.testSuite.project;'
        'def env=pr.getActiveEnvironment().getName();'
        'if(!env||env.trim().isEmpty()||env=="No Environment")'
        'throw new RuntimeException("No active ReadyAPI environment selected.");'
        'def file=new File(new File(pr.path).parent+"/local-config/"+env+".properties");'
        'if(!file.exists())throw new RuntimeException("Secrets file not found: "'
        '+file.absolutePath);'
        'def props=new Properties();def fis=new FileInputStream(file);'
        'props.load(fis);fis.close();'
        'def v=props.getProperty("%s");'
        'if(v==null)throw new RuntimeException("Missing key");v' % key
    )


# --------------------------------------------------------------- rule 1

def test_secrets_file_expression_becomes_its_config_key():
    for key in ("client_id", "client_secret", "username", "password"):
        got = R._external_config_key(_secrets_expr(key))
        assert got == key, f"{key!r} -> {got!r}"


def test_the_key_taken_is_the_one_returned_not_a_guard_clause():
    # Guard clauses read the key too. The LAST getProperty is the one
    # whose value the expression evaluates to.
    expr = ('def props=new Properties();props.load(new FileInputStream(f));'
            'if(props.getProperty("legacy_id")==null)'
            'throw new RuntimeException("x");'
            'props.getProperty("client_id")')
    assert R._external_config_key(expr) == "client_id"


def test_expressions_that_are_not_file_reads_stay_untranslated():
    # Each of these must keep falling through to #groovy_expr#: guessing
    # a config key for a computed value would silently send the wrong
    # data, which is worse than an obvious stub.
    for expr in (
        'System.getProperty("user.dir")',
        'new Date().format("yyyy-MM-dd")',
        'context.expand(\'${#Project#propCode}\')',
        'context.testCase.getPropertyValue("someKey")',
        '',
    ):
        assert R._external_config_key(expr) == "", expr


def test_a_file_read_with_no_getproperty_is_not_a_config_key():
    assert R._external_config_key(
        'def f=new File("x.properties");f.text') == ""


# --------------------------------------------------------------- rule 2

def test_hardcoded_credentials_in_a_grant_body_route_to_config():
    body = json.dumps({"client_id": "AAA", "client_secret": "BBB",
                       "username": "svc-acct", "password": "hunter2"})
    out, routed = R._route_credential_literals(body)
    assert routed == {"client_id", "client_secret", "username", "password"}
    tree = json.loads(out)
    assert tree == {"client_id": "#client_id#",
                    "client_secret": "#client_secret#",
                    "username": "#username#",
                    "password": "#password#"}
    # and nothing of the original values survives in the text
    for v in ("AAA", "BBB", "svc-acct", "hunter2"):
        assert v not in out


def test_a_create_user_body_is_left_alone():
    # THE regression to fear. No client_id/client_secret, so this is
    # test data -- rewriting it would post the runner's own credential
    # as the new user's name and the test would pass against the wrong
    # record.
    body = json.dumps({"username": "jdoe", "password": "hunter2",
                       "email": "j@example.com"})
    out, routed = R._route_credential_literals(body)
    assert routed == set()
    assert out == body


def test_leaf_names_match_exactly_not_by_substring():
    body = json.dumps({"client_id": "AAA", "passwordChangedDate": "2026-01-01",
                       "usernameHint": "initials"})
    out, routed = R._route_credential_literals(body)
    assert routed == {"client_id"}
    tree = json.loads(out)
    assert tree["passwordChangedDate"] == "2026-01-01"
    assert tree["usernameHint"] == "initials"


def test_values_that_are_already_placeholders_survive_untouched():
    # _fold_placeholder_aliases has already decided that #c_id# and
    # #client_id# are one key. Rewriting here would undo that and split
    # one template back into two.
    body = json.dumps({"client_id": "#c_id#", "client_secret": "#c_sec#"})
    out, routed = R._route_credential_literals(body)
    assert routed == set()
    assert out == body


def test_non_json_and_non_credential_bodies_are_returned_unchanged():
    for body in ('grant_type=client_credentials&client_id=AAA',
                 json.dumps({"propCode": "EPROP", "peakRooms": 12}),
                 '', 'not json at all'):
        out, routed = R._route_credential_literals(body)
        assert routed == set(), body
        assert out == body, body


def test_a_malformed_body_does_not_raise():
    # This path swallowed a NameError once (`_json` is function-local in
    # this module, not global) and the whole rule silently did nothing.
    # The except is narrow now; this pins that a real parse failure is
    # still handled and a coding error is not.
    out, routed = R._route_credential_literals('{"client_id": ')
    assert (out, routed) == ('{"client_id": ', set())


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
