"""A value the script COMPUTES is not random text, and a JDBC bind
parameter is either supplied or the query is not sent.

Both were found in one Amexbackbook run.

1. DataGenInput writes a date and a UUID:

       def currentDay = dateFormat.format(new Date()-1)
       def uuid = UUID.randomUUID().toString()
       Properties.setPropertyValue("todayDate", currentDay)
       Properties.setPropertyValue("partnerAccountID", uuid)

   The generator pack picks a value by FIELD NAME, so the request went
   out with `"startDate": "tscbgv"` and a nine-digit account number.

2. retrieve_unique_invite_key binds a step property:

       def account_id = context.expand('${PropertiesaccountID#accountID}').toLong()
       sql.firstRow("... WHERE account_id = ?", [account_id])

   The bind list was dropped ("contains Groovy identifiers") and the
   query was sent with `?` and no value: SQL error 22023 on a shared
   database, three times per test.

    python tools/ra_converter/test_computed_values_and_jdbc_binds.py
"""
import glob
import io
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import groovy_translator as gt  # noqa: E402

AMEX = os.path.join(HERE, "input", "amexbackbook.xml")
DATA_DIR = os.environ.get("RA_TEST_DATA_DIR", "")

DATAGEN = '''
import java.text.SimpleDateFormat
def generator = { String alphabet, int n -> "x" }
def dateFormat = new SimpleDateFormat("yyyy-MM-dd")
def currentDay = dateFormat.format(new Date()-1)
def yesterdayDate = dateFormat.format(new Date()-2)
def today = dateFormat.format(new Date())
def uuid = UUID.randomUUID().toString()
def firstname = generator( (('a'..'z')).join(), 8 )
def PropertiesPropertyVal = testRunner.testCase.getTestStepByName("Properties")
PropertiesPropertyVal.setPropertyValue("todayDate", currentDay)
PropertiesPropertyVal.setPropertyValue("PreviousDate", yesterdayDate)
PropertiesPropertyVal.setPropertyValue("now", today)
PropertiesPropertyVal.setPropertyValue("partnerAccountID", uuid)
PropertiesPropertyVal.setPropertyValue("Firstname", firstname)
'''

INVITE = '''
def sql = Sql.newInstance(dbUrl, dbUser, dbPassword, driver)
def account_id = context.expand('${PropertiesaccountID#accountID}').toLong()  // Convert
def result = sql.firstRow("SELECT invitation_key FROM account_member_invite WHERE account_id = ?", [account_id])
'''


# --- computed values ------------------------------------------------------

def test_a_formatted_date_keeps_its_pattern_and_offset():
    comp = gt._computed_var_expressions(DATAGEN)
    assert 'SimpleDateFormat("yyyy-MM-dd")' in comp["currentDay"]
    assert "- 1L * 86400000L" in comp["currentDay"]
    assert "- 2L * 86400000L" in comp["yesterdayDate"]
    assert "86400000L" not in comp["today"], "no offset means today"


def test_a_uuid_stays_a_uuid():
    assert gt._computed_var_expressions(DATAGEN)["uuid"] == \
        "java.util.UUID.randomUUID().toString()"


def test_a_random_word_is_not_treated_as_computed():
    assert "firstname" not in gt._computed_var_expressions(DATAGEN)


def test_a_format_call_on_an_unknown_formatter_is_left_alone():
    script = 'def d = mystery.format(new Date()-1)\n'
    assert gt._computed_var_expressions(script) == {}


def test_the_fields_are_published_with_the_computed_value():
    pubs = gt._var_backed_publications(DATAGEN)
    assert "SimpleDateFormat" in pubs[("Properties", "todayDate")]
    assert "SimpleDateFormat" in pubs[("Properties", "PreviousDate")]
    assert "randomUUID" in pubs[("Properties", "partnerAccountID")]
    assert ("Properties", "Firstname") not in pubs, (
        "a random name must stay with the generator pack")


def test_a_later_write_of_the_field_still_wins():
    script = DATAGEN + 'PropertiesPropertyVal.setPropertyValue("todayDate", firstname)\n'
    assert ("Properties", "todayDate") not in gt._var_backed_publications(script)


# --- bind sourcing --------------------------------------------------------

def test_a_step_property_bind_is_sourced_from_ctx_as_a_number():
    assert gt._ctx_backed_bind(INVITE, "account_id") == (
        "PropertiesaccountID.accountID", "Long.valueOf(%s.trim())")


def test_a_plain_expand_stays_a_string():
    script = "def k = context.expand('${Properties#inviteKey}')\n"
    assert gt._ctx_backed_bind(script, "k") == ("Properties.inviteKey", "%s")


def test_a_commented_out_definition_is_not_the_source():
    script = ("//def a = context.expand('${P#x}').toLong()\n"
              "def a = other.toLong()\n")
    assert gt._ctx_backed_bind(script, "a") is None


def test_a_project_property_or_a_computed_value_is_not_guessed():
    assert gt._ctx_backed_bind(
        "def a = context.expand('${#Project#DB_URL}')\n", "a") is None
    assert gt._ctx_backed_bind("def a = b + 1\n", "a") is None
    assert gt._ctx_backed_bind(INVITE, "account_id + 1") is None


# --- the real suite -------------------------------------------------------

_CONVERTED = {}


def _amex_hooks():
    if "src" in _CONVERTED:
        return _CONVERTED["src"]
    if not os.path.isfile(AMEX):
        _CONVERTED["src"] = None
        return None
    out = tempfile.mkdtemp(prefix="amexcv")
    argv = [sys.executable, os.path.join(HERE, "ra_converter.py"),
            "--input", AMEX, "--output", out,
            "--skip-self-test", "--no-cursor-assist"]
    if os.path.isdir(DATA_DIR):
        argv += ["--data-dir", DATA_DIR]
    proc = subprocess.run(argv, capture_output=True, text=True, cwd=out)
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]
    files = glob.glob(os.path.join(out, "src", "main", "java", "**",
                                   "amexbackbook", "**", "*.java"),
                      recursive=True)
    assert files, "no amexbackbook sources generated"
    _CONVERTED["src"] = "\n".join(
        io.open(f, encoding="utf-8").read() for f in files)
    return _CONVERTED["src"]


def test_amex_dates_and_uuids_are_emitted_as_such():
    src = _amex_hooks()
    if src is None:
        print("    (skipped: amexbackbook.xml not in input/)")
        return
    for field in ("todayDate", "PreviousDate"):
        hits = re.findall(
            r'putExtracted\(ctx, "Properties\.%s", ([^;]+);' % field, src)
        assert hits, field + " is never published"
        assert all("SimpleDateFormat" in h for h in hits), (field, hits[:2])
    hits = re.findall(
        r'putExtracted\(ctx, "Properties\.partnerAccountID\d?", ([^;]+);', src)
    assert hits and all("randomUUID" in h for h in hits), hits[:2]


def test_amex_invite_key_query_is_bound_and_guarded():
    src = _amex_hooks()
    if src is None:
        print("    (skipped: amexbackbook.xml not in input/)")
        return
    assert "omitting bind values" not in src
    i = src.index("WHERE account_id = ?")
    block = src[max(0, i - 1200):i + 600]
    assert 'ctxGet(ctx, "PropertiesaccountID.accountID")' in block
    assert "jdbc SKIPPED (no value for bind parameter" in block
    assert re.search(r"queryOneComposed\(__jdbcSql, Long\.valueOf\(", block), block


def test_no_amex_query_is_sent_with_an_unfilled_parameter():
    """Every `?` query either passes a bind argument or is not executed."""
    src = _amex_hooks()
    if src is None:
        print("    (skipped: amexbackbook.xml not in input/)")
        return
    for m in re.finditer(r'mapSqlValues\("([^"\n]*\?[^"\n]*)"', src):
        tail = src[m.end():m.end() + 500]
        call = re.search(r"Db\.\w+\(__jdbcSql([^)]*)\)", tail)
        assert call and call.group(1).strip().startswith(","), (
            "query with `?` executed without a bind value: " + m.group(1))


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok  " + name)
            passed += 1
    print("%d passed" % passed)
