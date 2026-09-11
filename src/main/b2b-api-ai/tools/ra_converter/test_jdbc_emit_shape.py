"""Structural checks on the Groovy -> JDBC emit.

Added after a regex extraction of the guard/log/execute tail silently broke
the ``sql.eachRow`` path. That path's ``else`` branch is continued by later
conditional appends (OTP extraction), so collapsing it left a dangling block
and an ``UnboundLocalError`` at emit time: 13 of 18 suites failed to convert
and ~390k lines of output went missing. A full reconvert only surfaced it
after 20 minutes.

These checks run the translator in-process, so the same class of breakage is
caught in seconds instead. They assert SHAPE, not exact text, so they do not
have to be rewritten every time the emitted wording changes.

    python tools/ra_converter/test_jdbc_emit_shape.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import groovy_translator  # noqa: E402

NEW_INSTANCE = "def sql = Sql.newInstance(dbUrl, dbUser, dbPassword, driver)"

SCRIPTS = {
    "execute": NEW_INSTANCE + "\nsql.execute(\"DELETE FROM account WHERE id=1\")",
    "eachRow": NEW_INSTANCE + "\nsql.eachRow(\"SELECT email_otp FROM account_member\")"
               " { row -> def otp = row.email_otp }",
    "rows": NEW_INSTANCE + "\ndef r = sql.rows(\"SELECT id FROM account\")",
    # firstRow is site B's OTHER branch -- it returns a Map, not a
    # List, so it dispatches to a different composed helper.
    "firstRow": NEW_INSTANCE
                + "\ndef r = sql.firstRow(\"SELECT id FROM account WHERE id=1\")",
    # --- eachRow / OTP variants ------------------------------------------
    # These three take DIFFERENT emit paths through the most intricate block
    # in the translator (brace depth is managed by hardcoded indentation
    # across ~150 lines). They handle TOTP extraction, which is
    # business-critical, and were previously uncovered.
    "otp_single": NEW_INSTANCE
                  + "\nsql.eachRow(\"SELECT email_otp FROM account_member WHERE id=1\")"
                    " { row -> emailOtp = row.email_otp }",
    "otp_multi": NEW_INSTANCE
                 + "\nsql.eachRow(\"SELECT email_otp, totp_code FROM account_member\")"
                   " { row -> emailOtp = row.email_otp; totpCode = row.totp_code }",
    "plain_each": NEW_INSTANCE
                  + "\nsql.eachRow(\"SELECT status FROM account\") { row -> st = row.status }",
    "execute_concat": NEW_INSTANCE
                      + "\ndef q = \"DELETE FROM account WHERE d='\" + domain + \"'\""
                      + "\nsql.execute(q)",
}


def _emit(script):
    lines, _meta = groovy_translator.translate(script, {}, "jdbcStep")
    return "\n".join(lines)


def test_braces_balance():
    """Unbalanced braces are exactly what the failed extraction produced."""
    for name, script in SCRIPTS.items():
        java = _emit(script)
        opens, closes = java.count("{"), java.count("}")
        assert opens == closes, (
            f"[{name}] unbalanced braces: {opens} open vs {closes} close\n{java}")


def test_no_half_collapsed_reason_variable():
    """__jdbcReason must be both declared and used, or neither.

    One without the other means a tail was partially collapsed -- the
    signature of the regression this file exists to prevent.
    """
    for name, script in SCRIPTS.items():
        java = _emit(script)
        declared = "String __jdbcReason" in java
        used = "__jdbcReason !=" in java or "__jdbcReason," in java
        assert declared == used, (
            f"[{name}] __jdbcReason declared={declared} used={used}\n{java}")


def test_every_jdbc_emit_still_runs_sql():
    """A collapsed emit must not lose its execute/query call entirely."""
    for name, script in SCRIPTS.items():
        java = _emit(script)
        if "Db." not in java:
            continue  # translator declined this shape; nothing to check
        # Keep this list in step with Db's runner surface. A collapsed emit
        # that dispatches to a NEW helper would otherwise look like a lost
        # execute path -- which is how this list first went stale.
        runs = any(tok in java for tok in (
            "Db.execute(", "Db.executeComposed(",
            "Db.queryAll(", "Db.queryComposed(",
            "Db.queryOne(", "Db.queryOneComposed(",
            "Db.pollUntilStable("))
        assert runs, f"[{name}] JDBC emit has no execute/query call\n{java}"


def test_no_unresolved_python_placeholders():
    """A mis-built f-string can leak `{result_var}` / `{params_java}` into Java."""
    for name, script in SCRIPTS.items():
        java = _emit(script)
        for leaked in ("{result_var}", "{params_java}", "{java_query}",
                       "{composed_java}", "{composed_concat}"):
            assert leaked not in java, (
                f"[{name}] unresolved emit placeholder {leaked}\n{java}")


def test_guard_precedes_execution():
    """SQL must never reach the driver without passing a safety gate.

    Either the emit keeps the inline unsafeSqlReason check, or it delegates
    to a Db.*Composed helper that performs the same check internally.
    """
    for name, script in SCRIPTS.items():
        java = _emit(script)
        if "Db.execute(" not in java:
            continue
        guarded = ("unsafeSqlReason" in java) or ("Composed(" in java)
        assert guarded, f"[{name}] Db.execute reached without a guard\n{java}"


def test_composed_helper_matches_result_type():
    """A List helper must not be assigned to a Map variable, or vice versa.

    Site B dispatches rows -> queryComposed (List) and firstRow ->
    queryOneComposed (Map). The emit is built from a table, so a table edit
    could silently pair them wrongly; this pins the pairing.
    """
    java_rows = _emit(SCRIPTS["rows"])
    if "queryComposed(" in java_rows or "queryAll(" in java_rows:
        assert "java.util.List" in java_rows, java_rows
        assert "queryOneComposed(" not in java_rows, java_rows

    java_first = _emit(SCRIPTS["firstRow"])
    if "queryOneComposed(" in java_first or "queryOne(" in java_first:
        assert "java.util.Map<String, Object>" in java_first, java_first
        assert "queryComposed(" not in java_first, java_first


def test_otp_paths_stay_distinct_and_balanced():
    """The three eachRow paths must each emit, and each stay balanced.

    A single captured column polls for stability; multiple columns keep the
    inlined retry loop so siblings still populate; a non-OTP query does
    neither. Collapsing any of them into another would change what TOTP
    extraction actually does.
    """
    single = _emit(SCRIPTS["otp_single"])
    assert "Db.pollUntilStable" in single, single
    assert "__otp_attempt" not in single, "single column must not use the retry loop"

    multi = _emit(SCRIPTS["otp_multi"])
    assert "__otp_attempt" in multi, "multi-column must keep the retry loop"
    assert "Db.pollUntilStable" not in multi, multi

    plain = _emit(SCRIPTS["plain_each"])
    assert "Db.pollUntilStable" not in plain and "__otp_attempt" not in plain, plain


def test_otp_value_is_padded_to_six_digits():
    """do-not-regress: OTP numeric values pad to 6 digits.

    Hilton stg rejects a 5-char OTP as invalid even when the missing char is
    a leading zero the DB dropped. The contract was recorded but nothing
    asserted the EMIT still produces it.
    """
    multi = _emit(SCRIPTS["otp_multi"])
    assert "OTP pad" in multi, multi
    assert "length() < 6" in multi, multi


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"ok  {fn.__name__}")
        except Exception as ex:
            failed += 1
            print(f"FAIL {fn.__name__}: {ex}")
    if failed:
        sys.exit(1)
    print(f"{len(tests)} passed")
