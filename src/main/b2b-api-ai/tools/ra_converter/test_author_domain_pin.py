"""A Domain the script TYPES is marked so the identity regen keeps it.

amexBackbookDuplicateManagedAccount sets

    PropertiesPropertyVal.setPropertyValue("Domain", "explorer.de")

and expects the registration to come back `duplicateManagedAccount`. The
hook published the literal, then the regeneration that runs before every
enroll replaced it with a fresh random domain: the run showed
`websiteDomain=hwrelo.com` for that case.

The hook now calls `ImportedScenario.pinAuthorDomain(ctx)` after it
publishes a literal Domain. A list pick (`blockFreeEmailList[rand]`) is
deliberately NOT pinned: which entry is the domain is decided per run.

    python tools/ra_converter/test_author_domain_pin.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import groovy_translator as gt  # noqa: E402

HEAD = '''
def generator = { String alphabet, int n -> "x" }
def generatedUser = generator( ('a'..'z').join(), 5 )
def generatedEmail = generatedUser + "@" + "explorer.de"
def PropertiesPropertyVal = testRunner.testCase.getTestStepByName("Properties")
PropertiesPropertyVal.setPropertyValue("Email", generatedEmail)
'''

LITERAL = HEAD + 'PropertiesPropertyVal.setPropertyValue("Domain", "explorer.de")\n'

VAR_LITERAL = HEAD + '''
def fixedDomain = "jbhunt.com"
PropertiesPropertyVal.setPropertyValue("Domain", fixedDomain)
'''

LIST_PICK = HEAD + '''
Random randomizer = new Random();
def blockFreeEmailList = ["gmail.com","yahoo.com","hotmail.com"]
def randomEmail = blockFreeEmailList[randomizer.nextInt(blockFreeEmailList.size())];
PropertiesPropertyVal.setPropertyValue("Domain", randomEmail)
'''

RANDOM = HEAD + '''
def generatedDomain = generator( ('a'..'z').join(), 8 ) + ".com"
PropertiesPropertyVal.setPropertyValue("Domain", generatedDomain)
'''

OTHER_FIELD = HEAD + 'PropertiesPropertyVal.setPropertyValue("country", "US")\n'

PIN = "ImportedScenario.pinAuthorDomain(ctx);"


def _java(script):
    lines, _meta = gt.translate(script, {}, "DataGenInput")
    return "\n".join(lines)


def test_an_inline_literal_domain_is_pinned_after_it_is_published():
    java = _java(LITERAL)
    assert '"Properties.Domain", "explorer.de"' in java, java
    assert PIN in java
    assert java.index('"Properties.Domain", "explorer.de"') < java.index(PIN), (
        "the pin reads ctx: it must come after the publication")


def test_a_literal_held_in_a_variable_is_pinned():
    java = _java(VAR_LITERAL)
    assert '"Properties.Domain", "jbhunt.com"' in java, java
    assert PIN in java


def test_a_list_pick_is_not_pinned():
    java = _java(LIST_PICK)
    assert "FakeData.oneOf(" in java, "fixture no longer exercises a list pick"
    assert PIN not in java


def test_a_generated_domain_is_not_pinned():
    assert PIN not in _java(RANDOM)


def test_a_literal_on_another_field_does_not_pin_the_domain():
    java = _java(OTHER_FIELD)
    assert '"Properties.country", "US"' in java, java
    assert PIN not in java


def test_the_runtime_has_the_method_the_hook_calls():
    src = open(os.path.join(HERE, "framework", "ImportedScenario.java"),
               encoding="utf-8").read()
    assert "public static void pinAuthorDomain(Map<String, String> ctx)" in src
    assert "if (authorDomain != null) {\n            domain = authorDomain;" \
        in src.replace("\r\n", "\n")


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok  " + name)
            passed += 1
    print("%d passed" % passed)
