"""A query wrapped after `+` is still one query, and a LocalDate a GString
reads is still a date.

Both were found in one EADkafkaevents run, and both failed the same way:
nothing went wrong at convert time, and at run time the request carried a
value nobody had computed.

1. GroovyScript_TOTPcode in `DeleteProgramAccount 2` wraps its query:

       def sql_query = "select email_otp from ... where account_member_id = " +
       hiltonmemberid
       sql.eachRow(sql_query) { row -> emailOtp = row.email_otp.toString().trim() }

   The same script on one line converts to a Db read. Wrapped, the `def`
   lookup saw a concat with nothing on its right and emitted a hand-wire
   comment. The OTP was never read and the confirm call was sent the code
   saved in the datasheet: 400, "TOTP code is invalid".

2. DBupdate in `CreateInviteLink` ages an invitation key:

       def currentDate = LocalDate.now()
       def olderDate = currentDate.minusDays(390)
       sql.executeUpdate("update account set d='${olderDate}' WHERE account_id='${account_id}'")

   The date recogniser needs `.format(...)`, so `olderDate` was never
   produced and the SQL kept the placeholder `#olderDate#`.

    python tools/ra_converter/test_wrapped_sql_and_local_dates.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import groovy_translator as gt  # noqa: E402

RESP = {"http_request_200_1": "http_request_200_1Res"}

_TOTP_HEAD = '''
import groovy.sql.Sql
def dbUrl = context.expand('${#Project#DB_URL}')
def PropertiesPropertyVal = testRunner.testCase.getTestStepByName("Properties")
def hiltonmemberid = context.expand( '${http_request_200_1#Response#$[\\'memberId\\']}' )
'''
_TOTP_TAIL = '''
def sql = Sql.newInstance(dbUrl, dbUser, dbPassword, driver)
def emailOtp = ''
sql.eachRow(sql_query) { row ->
emailOtp = row.email_otp.toString().trim()
}
emailOtp = emailOtp.padLeft(6, '0')
def PropertiesPropertyVal2 = testRunner.testCase.getTestStepByName("Properties")
PropertiesPropertyVal2.setPropertyValue("totpCodeDB", emailOtp)
'''
_SQL = '"select email_otp from segment.account_member_internal_security where account_member_id = "'

TOTP_ONE_LINE = _TOTP_HEAD + "def sql_query = " + _SQL + " + hiltonmemberid\n" + _TOTP_TAIL
TOTP_WRAPPED = _TOTP_HEAD + "def sql_query = " + _SQL + " +\n\t\t\thiltonmemberid\n" + _TOTP_TAIL

DBUPDATE = '''
import groovy.sql.Sql
import java.time.LocalDate
def sql = Sql.newInstance(dbUrl, dbUser, dbPassword, driver)
def currentDate = LocalDate.now()
def olderDate = currentDate.minusDays(390)
try {
    def account_id = context.expand( '${PropertiesaccountID#accountID}' )
    def accountUpdate = sql.executeUpdate("update account set invitation_key_activation_date='${olderDate}' WHERE account_id='${account_id}'")
} finally {
    sql.close()
}
'''


def _java(script, hint):
    lines, _meta = gt.translate(script, RESP, hint)
    return "\n".join(lines)


# --- a query wrapped after `+` -------------------------------------------

def test_the_one_line_query_reads_the_otp():
    """The control: this shape worked before, and must be what the wrapped
    one is compared against."""
    java = _java(TOTP_ONE_LINE, "GroovyScript_TOTPcode")
    assert "account_member_id = '#hiltonmemberid#'" in java, java
    assert 'putExtracted(ctx, "Properties.totpCodeDB", emailOtp)' in java, java
    assert "cannot safely inline" not in java


def test_the_wrapped_query_converts_to_the_same_java():
    wrapped = _java(TOTP_WRAPPED, "GroovyScript_TOTPcode")
    assert "cannot safely inline" not in wrapped, (
        "a query wrapped after `+` was left as a hand-wire comment:\n" + wrapped)
    assert wrapped == _java(TOTP_ONE_LINE, "GroovyScript_TOTPcode"), (
        "wrapping a statement must not change what it converts to")


def test_join_follows_a_trailing_plus_only():
    s = 'def q = "a = " +\n   x\nfoo()\n'
    end = s.index("+") + 1
    assert gt._join_plus_continuation('"a = " +', s, end) == '"a = " + x'
    # complete on its line: untouched, and the next line is not swallowed
    s2 = 'def q = "a = " + x\nfoo()\n'
    assert gt._join_plus_continuation('"a = " + x', s2, s2.index("\n")) == '"a = " + x'


def test_join_continues_across_several_lines_and_a_blank_one():
    s = 'def q = "a = " +\n\n  x +\n  " and b = " +\n  y\nfoo()\n'
    got = gt._join_plus_continuation('"a = " +', s, s.index("+") + 1)
    assert got == '"a = " + x + " and b = " + y', got


def test_an_increment_is_not_a_continuation():
    """269 of the 273 line-ending `+` in the 29 suites are `diffNum++`."""
    s = "diffNum++\nlog.info('next')\n"
    assert gt._join_plus_continuation("diffNum++", s, s.index("\n")) == "diffNum++"


def test_a_comment_or_the_end_of_the_script_stops_the_join():
    s = 'def q = "a = " +\n// x\n'
    assert gt._join_plus_continuation('"a = " +', s, s.index("+") + 1) == '"a = " +'
    s2 = 'def q = "a = " +'
    assert gt._join_plus_continuation('"a = " +', s2, len(s2)) == '"a = " +'


# --- a LocalDate local a GString reads -----------------------------------

def test_the_two_step_local_date_is_recognised():
    got = gt._unformatted_local_date_vars(DBUPDATE)
    assert got == [("olderDate",
                    "java.time.LocalDate.now().minusDays(390).toString()",
                    "currentDate.minusDays(390)")], got


def test_the_one_step_form_and_other_units_are_recognised():
    script = ('def cutoff = LocalDate.now().plusMonths(2).minusDays(1)\n'
              'log.info("until ${cutoff}")\n')
    got = gt._unformatted_local_date_vars(script)
    assert got and got[0][1] == \
        "java.time.LocalDate.now().plusMonths(2).minusDays(1).toString()", got


def test_the_date_is_in_ctx_before_the_update_reads_it():
    java = _java(DBUPDATE, "DBupdate")
    put = 'TestSupport.putExtracted(ctx, "olderDate", olderDate);'
    assert "String olderDate = java.time.LocalDate.now().minusDays(390).toString();" in java, java
    assert put in java, java
    assert "'#olderDate#'" in java, "the SQL still reads the placeholder:\n" + java
    assert java.index(put) < java.index("'#olderDate#'"), (
        "the date must be published BEFORE the JDBC block that reads it")


def test_a_formatted_chain_is_left_to_the_date_recogniser():
    """`.format(...)` after the chain belongs to the existing recogniser;
    claiming it here would declare the same Java local twice."""
    script = ('def formatter = DateTimeFormatter.ofPattern("yyyy-MM-dd")\n'
              'def arrival = LocalDate.now().plusDays(10).format(formatter)\n'
              'log.info("arrive ${arrival}")\n')
    assert gt._unformatted_local_date_vars(script) == []


def test_a_root_that_is_not_local_date_now_is_not_guessed():
    # `base` is not a date we can see
    assert gt._unformatted_local_date_vars(
        'def older = base.minusDays(3)\nlog.info("${older}")\n') == []
    # declared twice: which `now` it was is not knowable from here
    assert gt._unformatted_local_date_vars(
        'def d = LocalDate.now()\ndef d = LocalDate.now()\n'
        'def older = d.minusDays(3)\nlog.info("${older}")\n') == []
    # a parsed date is not today
    assert gt._unformatted_local_date_vars(
        'def d = LocalDate.parse(x)\ndef older = d.minusDays(3)\n'
        'log.info("${older}")\n') == []


def test_a_date_nothing_interpolates_gets_no_ctx_key():
    script = ('def currentDate = LocalDate.now()\n'
              'def olderDate = currentDate.minusDays(390)\n')
    assert gt._unformatted_local_date_vars(script) == []


def test_a_commented_out_definition_is_ignored():
    script = ('def currentDate = LocalDate.now()\n'
              '// def olderDate = currentDate.minusDays(390)\n'
              'log.info("${olderDate}")\n')
    assert gt._unformatted_local_date_vars(script) == []


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok  " + name)
            passed += 1
    print("%d passed" % passed)
