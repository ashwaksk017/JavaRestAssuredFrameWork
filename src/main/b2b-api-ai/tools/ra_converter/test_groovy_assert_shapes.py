"""Three Groovy assertion shapes translate instead of stubbing.

59 script assertions in the goal suite reached the runtime STUBBED: a
WARN, the script attached to Allure, and nothing validated. A green test
that checks less than the ReadyAPI case did is the one outcome worse than
a red one.

Each script below is a real one from the imported suite, reduced.
"""

import ra_converter


class _A:
    def __init__(self, name, script):
        self.name = name
        self.config = {"scriptText": script}


def _render(name, script):
    e = ra_converter.Emitter.__new__(ra_converter.Emitter)
    lines, cov = ra_converter.Emitter._render_groovy_script_assertion(
        e, _A(name, script), "res", "STEP", "v1")
    return lines, cov, any("STUBBED" in l for l in lines)


CACHE = '''import java.time.*
def parsedResponse = new groovy.json.JsonSlurper().parseText( response )
def expiryTimeStr =  parsedResponse.cacheExpiryTime
Instant expiryInstant = Instant.parse(expiryTimeStr)
long diffInMinutes = TimeUnit.MILLISECONDS.toMinutes(diffInMillis)
assert(diffInMinutes <= 45)'''

ENV_PREFIX = '''import groovy.json.JsonSlurper
def response = messageExchange.responseContent
assert response?.trim() : "POST_Confirm response is empty"
def json = new JsonSlurper().parseText(response)
def ratePlanCode = json.ratePlanCode?.toString()?.trim()
assert ratePlanCode : "ratePlanCode is required"
def envName = context.testCase.testSuite.project.activeEnvironment.name
if (envName.contains("Corporate_500")) { assert ratePlanCode.startsWith("5") : "b" }
else if (envName.contains("Partner_600")) { assert ratePlanCode.startsWith("6") : "b" }
else if (envName.contains("Partner_700")) { assert ratePlanCode.startsWith("7") : "b" }'''

TRUTHY = '''import groovy.json.JsonSlurper
def response = messageExchange.responseContent
assert response?.trim() : "Response is empty"
def json = new JsonSlurper().parseText(response)
assert json.groupId : "groupId is required"'''


def test_the_timestamp_window_shape_translates():
    """22 of the goal suite's stubs were this one script."""
    lines, _cov, stubbed = _render("CacheExpiryTimeLimit45min", CACHE)
    assert not stubbed, lines
    body = "\n".join(lines)
    assert "instantWithinMinutes" in body, body
    assert '"cacheExpiryTime"' in body, body
    assert "45L" in body, body


def test_the_environment_prefix_shape_translates_every_branch():
    """The whole point is the branches. An earlier version emitted the two
    guards, dropped the startsWith entirely, and LOOKED translated -- worse
    than the stub, which at least says it is not checking."""
    lines, _cov, stubbed = _render("Environment Rate Plan Code", ENV_PREFIX)
    assert not stubbed, lines
    body = "\n".join(lines)
    assert "prefixForEnvironment" in body, body
    for env, want in (("Corporate_500", "5"), ("Partner_600", "6"),
                      ("Partner_700", "7")):
        assert '"%s", "%s"' % (env, want) in body, (env, body)


def test_the_environment_shape_names_the_field_not_the_method_chain():
    """`json.ratePlanCode?.toString()?.trim()` names the field
    ratePlanCode. Keeping the chain produced a JsonPath of
    `ratePlanCode.toString().trim()`, which resolves to nothing."""
    lines, _cov, _s = _render("Environment Rate Plan Code", ENV_PREFIX)
    body = "\n".join(lines)
    assert '"ratePlanCode"' in body, body
    assert "toString" not in body, body
    assert "trim()" not in body, body


def test_groovy_truthiness_translates_and_is_not_just_not_null():
    """Groovy counts "" and [] as false, and an empty string is exactly
    what `assert json.groupId` is written to catch -- so it must not
    become jsonExists, which asserts non-null only."""
    lines, _cov, stubbed = _render("G-SHOP-07", TRUTHY)
    assert not stubbed, lines
    body = "\n".join(lines)
    assert "jsonTruthy" in body, body
    assert '"groupId"' in body, body
    assert "jsonExists" not in body, body


def test_the_body_guard_no_longer_sinks_the_whole_script():
    """`assert response?.trim()` opens almost every one of these scripts.
    The JsonSlurper pass is ALL-OR-NOTHING, so while that guard was
    unrecognised it sank the asserts that WERE understood."""
    lines, _cov, stubbed = _render("G-SHOP-07", TRUTHY)
    body = "\n".join(lines)
    assert not stubbed
    assert "bodyNotEmpty" in body, body


def test_an_unrecognised_script_still_stubs_loudly():
    """The fallback has to stay. Silently emitting nothing for a script we
    do not understand is the failure this whole area exists to avoid."""
    lines, cov, stubbed = _render(
        "something else", "def x = 1\nassert someThing.weird(x) > 3")
    assert stubbed, lines
    assert cov == "TODO"


def test_an_empty_script_is_a_noop_not_a_stub():
    lines, cov, stubbed = _render("empty", "")
    assert not stubbed, lines
    assert cov == "FULL"
