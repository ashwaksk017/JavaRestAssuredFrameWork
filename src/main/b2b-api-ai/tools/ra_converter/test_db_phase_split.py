"""A DB-only Groovy step gets its own phase instead of riding inside the
REST method that happens to precede it.

The grouping rule breaks a phase whenever the fluent name changes, and
Groovy steps are exempt from the inherit-the-previous-name clause. So
naming the step is the whole mechanism: `step_fluent_override` returns a
name derived from the SQL, and the database work lands in its own method.

Two properties matter as much as the naming itself:

  - Steps that also read a REST response must STAY fused. The response
    variable is a local of the REST step's method, so the SQL cannot move
    away from it.
  - The name comes from the SQL, never from the ReadyAPI step title. This
    suite calls the same UPDATE "DBupdate", "DB update" and
    "Update account status_Limited".
"""
from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import fluent_scenario as fs  # noqa: E402

JAVA_IDENT = re.compile(r"[a-z][A-Za-z0-9]*")


class GroovyStep:
    """Only the type NAME and `.script` are read by the override."""

    def __init__(self, script, step_name="DBupdate"):
        self.script = script
        self.step_name = step_name


class RestStep:
    def __init__(self):
        self.script = None
        self.resource_path = "/guests"
        self.http_method = "POST"


def test_update_is_named_for_its_table_and_column():
    assert fs.db_phase_name(
        "UPDATE account SET status='L' WHERE web_site='#domain#'"
    ) == "dbAccountSetStatus"
    assert fs.db_phase_name(
        "UPDATE account_member SET status = 'A' WHERE guest_id='#guestID#'"
    ) == "dbAccountMemberSetStatus"


def test_delete_and_insert_are_named_for_their_table():
    assert fs.db_phase_name("DELETE FROM account_member WHERE id=1") == "dbDeleteAccountMember"
    assert fs.db_phase_name("INSERT INTO account_rules (a) VALUES (1)") == "dbInsertAccountRules"


def test_select_is_named_for_its_column_and_table():
    n = fs.db_phase_name("select email_otp from account_member where id=1")
    assert n == "dbReadEmailOtpFromAccountMember", n
    assert fs.db_phase_name("SELECT * FROM account_rules") == "dbReadAllFromAccountRules"


def test_an_aliased_column_cannot_carry_a_dot_into_the_name():
    """`SELECT a.account_id FROM account a` produced `dbReadA.AccountId...`
    before the identifier sanitiser, which is not compilable Java."""
    n = fs.db_phase_name("SELECT a.account_id FROM account a WHERE x=1")
    assert "." not in n, n
    assert JAVA_IDENT.fullmatch(n), n


def test_every_generated_name_is_a_valid_bounded_identifier():
    """The longest real one is 47 chars, over the @Test name cap, so it
    must arrive already trimmed rather than be truncated downstream."""
    for sql in (
        "select email_otp from segment.account_member_internal_security where id=1",
        "UPDATE account SET invitation_key_activation_date = now()",
        "SELECT invitation_key FROM account_member_invite WHERE account_id = ?",
    ):
        n = fs.db_phase_name(sql)
        assert JAVA_IDENT.fullmatch(n), (sql, n)
        assert len(n) <= 42, (n, len(n))


def test_a_script_reading_a_response_stays_fused():
    """THE safety property. Splitting these would move SQL away from the
    response local it reads, which does not compile."""
    assert fs.db_phase_name(
        "def id = context.expand('${step#Response#accountId}')\n"
        "sql.execute(\"UPDATE account SET status='L'\")") is None


def test_a_script_without_sql_is_not_ours():
    assert fs.db_phase_name("def x = 1") is None
    assert fs.db_phase_name("") is None
    assert fs.db_phase_name(None) is None


def test_an_unrecognised_statement_leaves_behaviour_unchanged():
    """None means 'not mine' and keeps today's fused emission."""
    assert fs.db_phase_name("TRUNCATE TABLE account") is None


def test_the_override_only_fires_for_groovy_steps():
    assert fs.step_fluent_override(
        GroovyStep("UPDATE account SET status=1")) == "dbAccountSetStatus"
    assert fs.step_fluent_override(RestStep()) is None


def test_the_name_ignores_the_readyapi_step_title():
    """Same SQL under three different author titles must land on one name,
    which is what lets the bodies hoist together."""
    sql = "UPDATE account SET status='L' WHERE web_site='#domain#'"
    names = {fs.step_fluent_override(GroovyStep(sql, t))
             for t in ("DBupdate", "DB update", "Update account status_Limited")}
    assert names == {"dbAccountSetStatus"}, names


if __name__ == "__main__":
    failed = 0
    names = [n for n in globals() if n.startswith("test_")]
    for name in names:
        try:
            globals()[name]()
            print("ok  ", name)
        except Exception as e:  # noqa: BLE001
            failed += 1
            print("FAIL", name, "->", e)
    print(f"{len(names) - failed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
