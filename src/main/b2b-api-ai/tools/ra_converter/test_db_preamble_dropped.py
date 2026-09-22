"""DB connection properties must not become Java locals.

A ReadyAPI Groovy script that touches the database opens its own JDBC
connection, so it begins by expanding DB_URL / DB_USER / DB_PASSWORD /
DB_DRIVER. The translated Java has no use for them: Db owns the
connection and reads its own config under db.url / db.user / db.password
/ db.driver, which are different keys entirely.

Binding them cost eight dead lines in each of 281 blocks, and made
otherwise-identical DB blocks differ, which blocked phase reuse.

The control matters as much as the subject here: other project refs such
as ALLOWED_DOMAINS MUST still bind, because SQL reads them back through
`#name#` placeholders.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import groovy_translator as gt  # noqa: E402


def _emit(script: str) -> str:
    return "\n".join(gt.translate(script, {}, "dbStep")[0])


def _expand(var: str, ref: str) -> str:
    return "def %s = context.expand('${%s}')\n" % (var, ref)


def test_db_connection_props_are_not_bound():
    out = _emit(
        _expand("dbUrl", "#Project#DB_URL")
        + _expand("dbUser", "#Project#DB_USER")
        + _expand("dbPassword", "#Project#DB_PASSWORD"))
    assert "String dbUrl" not in out, out
    assert "String dbUser" not in out, out
    assert "String dbPassword" not in out, out
    assert 'putExtracted(ctx, "dbUrl"' not in out, out


def test_the_driver_is_matched_by_property_not_variable_name():
    """This suite binds DB_DRIVER to BOTH `driver` and `dbDriver`. A
    variable-name list would leave one behind, and the two blocks would
    still differ."""
    for var in ("driver", "dbDriver"):
        out = _emit(_expand(var, "#Project#DB_DRIVER"))
        assert "String %s =" % var not in out, (var, out)


def test_a_provenance_line_replaces_them():
    """Eight dead lines become one that says where the connection went,
    so a reader looking for the setup is not left guessing."""
    out = _emit(_expand("dbUrl", "#Project#DB_URL"))
    assert "DB connection properties" in out, out
    assert "DB_URL" in out, out
    assert "Db opens the connection" in out, out


def test_other_project_refs_still_bind():
    """THE control. SQL reads these back via #name# placeholders, so
    suppressing them would break the statements that use them."""
    out = _emit(_expand("allowed", "#Project#ALLOWED_DOMAINS"))
    assert "String allowed" in out, out
    assert 'putExtracted(ctx, "allowed", allowed);' in out, out
    assert "DB connection properties" not in out, out


def test_non_project_refs_still_bind():
    """Properties-scoped refs are how #domain# / #guestID# reach SQL."""
    out = _emit(_expand("domain", "Properties#Domain"))
    assert "String domain" in out, out


def test_a_mixed_script_keeps_only_what_is_used():
    out = _emit(
        _expand("dbUrl", "#Project#DB_URL")
        + _expand("domain", "Properties#Domain"))
    assert "String dbUrl" not in out, out
    assert "String domain" in out, out


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
