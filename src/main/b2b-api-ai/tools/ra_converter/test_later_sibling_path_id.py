"""A baked path id is replaced by the id the case itself is using, also
when the step that names it comes LATER.

    http_request_200_1      POST /guests/{guestId}/businesses
    accountDetails          PropertiesDetails.accountID = response.accountId
    http_request_200_2      GET  /businesses/2000173362          <- baked
    http_request_200_update PUT  /businesses/${PropertiesDetails#accountID}

The rewrite looked at EARLIER steps only; with none, it used
Properties.accountId, which DataGenInput fills with a random number. The
read went to an account that never existed -- 404 -- while the account the
case had just created was in ctx.

    python tools/ra_converter/test_later_sibling_path_id.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ra_converter as rc  # noqa: E402

ACCOUNT_DETAILS = '''def p = testRunner.testCase.getTestStepByName("PropertiesDetails")
p.setPropertyValue("accountID", resp_obj.accountId.toString().trim())
'''


def _rest(name, params):
    s = rc.RestStep.__new__(rc.RestStep)
    s.step_name, s.path_params, s.assertions = name, params, []
    return s


def _groovy(name, script):
    g = rc.GroovyStep.__new__(rc.GroovyStep)
    g.step_name, g.script = name, script
    return g


class Case:
    def __init__(self, steps):
        self.steps = steps


def test_the_later_steps_reference_is_used_when_already_written():
    read = _rest("http_request_200_2", {"accountId": "2000173362"})
    case = Case([_rest("http_request_200_1", {"guestId": "${Properties#guestID}"}),
                 _groovy("accountDetails", ACCOUNT_DETAILS), read,
                 _rest("update", {"accountId": "${PropertiesDetails#accountID}"})])
    assert rc._later_sibling_path_param_expr(case, read, "accountId") == \
        "${PropertiesDetails#accountID}"


def test_a_property_nobody_has_written_yet_is_not_used():
    """NEGATIVE CONTROL: the script that fills it runs AFTER the read, so
    the reference would resolve empty and send `/businesses/`."""
    read = _rest("http_request_200_2", {"accountId": "2000173362"})
    case = Case([read, _groovy("accountDetails", ACCOUNT_DETAILS),
                 _rest("update", {"accountId": "${PropertiesDetails#accountID}"})])
    assert rc._later_sibling_path_param_expr(case, read, "accountId") is None


def test_a_response_reference_or_another_param_is_not_used():
    read = _rest("r", {"accountId": "2000173362"})
    case = Case([_groovy("accountDetails", ACCOUNT_DETAILS), read,
                 _rest("a", {"accountId": "${create#Response#$.accountId}"}),
                 _rest("b", {"guestId": "${PropertiesDetails#accountID}"})])
    assert rc._later_sibling_path_param_expr(case, read, "accountId") is None


def test_a_step_outside_the_case_gets_no_guess():
    assert rc._later_sibling_path_param_expr(Case([]), _rest("r", {}), "accountId") is None


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok  " + name)
            n += 1
    print("%d passed" % n)
