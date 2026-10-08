"""A script that sets no random field does not regenerate the identity.

GenNewDomain runs in the MIDDLE of a case -- after the guest is enrolled and
the account created -- and does two things:

    Properties2.Domain   = one entry of ${#Project#ALLOWED_DOMAINS}
    Properties2.topicenv = "programaccounts-stg"

It also carries a `def generator = {...}` it never uses for a property.
`def generator` + `setPropertyValue` was the trigger for the identity pack,
so the translation emitted `CtxFields.generateStandard(ctx, "Properties")`
there: Properties.guestID -- the guest enrolled a few steps earlier --
became a random nine-digit number and every later call on
/guests/{guestId}/... answered 403. Properties2.Domain was never written.

    python tools/ra_converter/test_domain_only_script.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import groovy_translator as gt  # noqa: E402

GEN = '''def generator = { String alphabet, int n ->
  new Random().with {
    (1..n).collect { alphabet[ nextInt( alphabet.length() ) ] }.join()
  }
}
def generatedUser = generator( (('a'..'z')+('0'..'9')).join(), 5 )
'''
PICK = '''def allowedDomains =  context.expand( '${#Project#ALLOWED_DOMAINS}').split(',');
def allowedDomain = allowedDomains[new Random().nextInt(allowedDomains.length-1)]
def generatedDomain = allowedDomain
'''
GEN_NEW_DOMAIN = GEN + PICK + '''
def PropertiesPropertyVal = testRunner.testCase.getTestStepByName("Properties2")
PropertiesPropertyVal.setPropertyValue("Domain", generatedDomain)
def env = context.testCase.testSuite.project.getActiveEnvironment ().getName ()
if(env  ==  "EKS_TST")
{ PropertiesPropertyVal.setPropertyValue("topicenv", "programaccounts-test")
} else if (env  ==  "EKS_STG")
 { PropertiesPropertyVal.setPropertyValue("topicenv", "programaccounts-stg")
}
'''
DATA_GEN_INPUT = GEN + PICK + '''
def generatedEmail = generatedUser + "@" + generatedDomain
def PropertiesPropertyVal = testRunner.testCase.getTestStepByName("Properties")
PropertiesPropertyVal.setPropertyValue("Domain", generatedDomain)
PropertiesPropertyVal.setPropertyValue("Email", generatedEmail)
PropertiesPropertyVal.setPropertyValue("Username", generatedUser)
'''


def _java(script, name):
    lines, meta = gt.translate(script, {}, name)
    return "\n".join(lines), meta


def test_the_identity_is_not_regenerated():
    java, _ = _java(GEN_NEW_DOMAIN, "GenNewDomain")
    assert "generateStandard" not in java, java


def test_the_domain_is_picked_from_the_allowed_list_into_its_own_step():
    java, meta = _java(GEN_NEW_DOMAIN, "GenNewDomain")
    assert ('TestSupport.putExtracted(ctx, "Properties2.Domain", '
            'CtxFields.allowedDomainOrRandom());') in java, java
    assert "allowed_domain_pick" in meta["patterns_matched"]
    assert 'Properties2.topicenv' in java           # the literal still lands


def test_the_identity_script_is_untouched():
    """NEGATIVE CONTROL: DataGenInput writes random fields to Properties and
    must keep its pack -- 1,173 scripts."""
    java, _ = _java(DATA_GEN_INPUT, "DataGenInput")
    assert 'CtxFields.generateStandard(ctx, "Properties"' in java, java
    assert "no random field is set" not in java


def test_a_random_field_on_another_step_still_generates():
    """A second identity (Properties_2.Email2 = random) is not 'no random
    field'; that shape keeps today's output."""
    s = GEN + '''def generatedEmail2 = generatedUser + "@x.com"
def p = testRunner.testCase.getTestStepByName("Properties_2")
p.setPropertyValue("Email2", generatedEmail2)
'''
    java, _ = _java(s, "DataGenInput_2")
    assert "no random field is set" not in java
    assert "generateStandard" in java


def test_the_pick_is_recognised_only_from_the_allowed_list():
    assert gt._allowed_domain_pick_publications(GEN_NEW_DOMAIN) == {
        ("Properties2", "Domain"): "CtxFields.allowedDomainOrRandom()"}
    other = GEN_NEW_DOMAIN.replace("ALLOWED_DOMAINS", "BLOCKED_DOMAINS")
    assert gt._allowed_domain_pick_publications(other) == {}
    # on the identity step the generator pack already owns the domain
    assert gt._allowed_domain_pick_publications(DATA_GEN_INPUT) == {}


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok  " + name)
            n += 1
    print("%d passed" % n)
