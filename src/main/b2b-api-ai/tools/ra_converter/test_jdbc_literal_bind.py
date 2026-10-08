"""A bind parameter that is a literal in the same script reaches the SQL.

    def emailDomain = "zaxbys.com"
    sql.execute("DELETE FROM account WHERE web_site = ?", [emailDomain])

The `?` became `'#emailDomain#'`, to be resolved from ctx at run time. But
nothing publishes a script local, so it resolved to the text `null`, the
statement was skipped as unsafe, and the case's start-of-test cleanup never
cleaned -- in three suites, every run. The account the previous run left
behind was still there when the case created it again.

Only a local bound once to a plain literal is inlined. Anything else keeps
the `#name#` reference.

    python tools/ra_converter/test_jdbc_literal_bind.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import groovy_translator as gt  # noqa: E402

HEAD = "def sql = Sql.newInstance(dbUrl, dbUser, dbPassword, driver)\n"
DELETE = ('sql.execute("DELETE FROM account WHERE web_site = ?", [emailDomain])\n')


def _sql(script):
    lines, _meta = gt.translate(script, {}, "DB")
    return [l.strip() for l in lines if "__jdbcSql =" in l]


def test_a_literal_local_is_inlined():
    out = _sql(HEAD + 'def emailDomain = "zaxbys.com"\n' + DELETE)
    assert len(out) == 1, out
    assert "WHERE web_site = 'zaxbys.com'" in out[0], out[0]
    assert "#emailDomain#" not in out[0]


def test_every_statement_of_the_script_gets_it():
    s = (HEAD + 'def emailDomain = "zaxbys.com"\n'
         + 'sql.execute("DELETE FROM account_member WHERE account_id IN '
           '(SELECT a.account_id FROM account a WHERE a.web_site = ?)", [emailDomain])\n'
         + DELETE
         + 'sql.execute("DELETE FROM account_email_domain WHERE email_domain = ?", [emailDomain])\n')
    out = _sql(s)
    assert len(out) == 3 and all("'zaxbys.com'" in l for l in out), out


def test_a_quote_in_the_value_cannot_end_the_literal():
    out = _sql(HEAD + 'def emailDomain = "o\'brien.com"\n' + DELETE)
    assert "web_site = 'o''brien.com'" in out[0], out[0]


def test_a_local_that_is_not_a_literal_keeps_its_reference():
    """NEGATIVE CONTROL: a value read from another step is still looked up."""
    s = (HEAD + "def emailDomain = context.expand('${Properties#Domain}')\n" + DELETE)
    out = _sql(s)
    assert "'#emailDomain#'" in out[0], out[0]


def test_a_local_assigned_twice_keeps_its_reference():
    """Which value it holds at the statement is not knowable from the def."""
    s = (HEAD + 'def emailDomain = "a.com"\nemailDomain = "b.com"\n' + DELETE)
    out = _sql(s)
    assert "'#emailDomain#'" in out[0], out[0]


def test_a_value_that_is_itself_a_reference_is_not_inlined():
    s = (HEAD + 'def emailDomain = "#Properties_Domain#"\n' + DELETE)
    out = _sql(s)
    assert "'#emailDomain#'" in out[0], out[0]


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok  " + name)
            n += 1
    print("%d passed" % n)
