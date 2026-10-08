"""Two gaps from the amex_backbook run (0 of 18 passed).

1. A script reads a response the emitting code has no variable for.

   SetupHelper.flow_A enrolls the guest; `Groovy Script for guestId` is
   emitted in the case's bootstrap hook and reads `guestId` from that
   response. With no variable in scope the translation passed `null`: the
   extract returned "", PropertiesGuestId.guestId was never written, and
   the case used DataGenInput's random number as its guest -- 404 on every
   /guests/{guestId}/... call, nine tests. The runtime keeps each response
   of the test by step name, so the fallback now asks it.

2. The last path parameter has NO entry in the step.

   `GET .../partneraccounts/{partneraccount}` is saved without the
   parameter in 10 of 16 steps (and as "" in the other 6). ReadyAPI
   records both as `.../partneraccounts`. The converter defaulted the
   absent one to Properties.partneraccount, which nothing writes; it
   resolved empty and the broken-path guard threw.

    python tools/ra_converter/test_response_lookup_and_absent_param.py
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import groovy_translator as gt  # noqa: E402
import ra_converter as rc  # noqa: E402

GUEST_ID = '''def PropertiesPropertyVal = testRunner.testCase.getTestStepByName("PropertiesGuestId")
PropertiesPropertyVal.setPropertyValue("guestId", "")
def resp = testRunner.testCase.getTestStepByName("http_request_200_enroll_guest").getPropertyValue('response');
def jsonSlurper = new groovy.json.JsonSlurper();
def resp_obj = jsonSlurper.parseText(resp)
PropertiesPropertyVal.setPropertyValue("guestId", resp_obj.guestId.toString().trim())
'''


def test_a_known_response_variable_is_used_as_before():
    assert gt._resp_var_for("enroll", {"response_var_by_step": {"enroll": "enrollRes"}}) == "enrollRes"


def test_an_unknown_one_is_looked_up_by_step_name_at_run_time():
    got = gt._resp_var_for("http_request_200_enroll_guest", {"response_var_by_step": {}})
    assert got == 'com.hi.api.rest.utilities.LastExchange.of("http_request_200_enroll_guest")', got
    assert "null" not in got


def test_the_lookup_uses_the_name_the_runtime_records():
    """RestStep records under the sanitised step name."""
    got = gt._resp_var_for("http_request_200_3-CreatePendingAccountmember", {})
    assert 'LastExchange.of("http_request_200_3_CreatePendingAccountmember")' in got, got


def test_the_guest_id_script_reads_the_setup_flows_enroll():
    lines, _ = gt.translate(GUEST_ID, {}, "Groovy Script for guestId")
    java = "\n".join(lines)
    assert "unknown_response_for" not in java, java
    assert ('"PropertiesGuestId.guestId", com.hi.api.rest.utilities.RestUtilities.safeJsonExtract('
            'com.hi.api.rest.utilities.LastExchange.of("http_request_200_enroll_guest"), "guestId")') in java, java


def test_the_runtime_has_the_lookup():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    src = open(os.path.join(root, "src", "main", "java", "com", "hi", "api", "rest",
                            "utilities", "LastExchange.java"), encoding="utf-8").read()
    assert "public static Response of(String stepName)" in src


def test_an_absent_last_path_parameter_is_treated_as_author_empty():
    """Source check: the rule lives inside the 600-line REST renderer, which
    cannot be driven without a whole parsed case. The behaviour is checked
    by converting amexbackbook: every get_partner_account spec must carry
    Ref.row("path_get_partner_account_partneraccount", "")."""
    src = inspect.getsource(rc)
    assert "p not in step.path_params and path_param_names" in src
    assert 'step.resource_path.rstrip().endswith("{" + p + "}")' in src


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok  " + name)
            n += 1
    print("%d passed" % n)
