"""A JDBC step runs against the database its connection string names.

ReadyAPI JDBC steps carry their own connection. The framework runs every
suite under ONE env block, so a GOAL step querying `goal.group_events`
through the block's B2B database failed with `relation does not exist`
(13 per run) and `#Postgres_Response_GOALRATEPLANS_SRP_CODE#` never
resolved. The converter now keeps the database NAME from the connection
string and emits `Db.forDatabase("<name>")`; the runtime resolves
`database.<name>.*` from the env block and falls back to `database.*`.

Only the name may leave the XML: never the host, user or password.

    python tools/ra_converter/test_jdbc_database_routing.py
"""
from __future__ import annotations

import glob
import os
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import ra_converter as rc  # noqa: E402

PGR = os.path.join(HERE, "input", "PartialGoalRegression.xml")
DATA_DIR = os.environ.get("RA_TEST_DATA_DIR", "")

# Synthetic. This repo is public: a real host or account name must never
# be written into a tracked file, least of all into the test that checks
# they do not leak.
FAKE_HOST = "db-host.example.internal"
FAKE_USER = "app-user"


def _real_secrets():
    """Host and user of every JDBC step in the input XML, read at run time.

    The input XML is gitignored, so the values this guards stay out of
    the repository while the guard still checks the real ones.
    """
    import re
    out = set()
    if not os.path.isfile(PGR):
        return out
    root = ET.parse(PGR).getroot()
    for step in root.iter("{%s}testStep" % rc.NS["con"]):
        if step.get("type") != "jdbc":
            continue
        conn = rc._parse_jdbc_step(step).connection_string or ""
        m = re.search(r"//([^:/?;]+)", conn)
        if m:
            out.add(m.group(1))
        m = re.search(r"[?&;]user=([^&;]+)", conn)
        if m:
            out.add(m.group(1))
    return {x for x in out if len(x) > 3}


# --- name derivation ------------------------------------------------------

@pytest.mark.parametrize("conn, expected", [
    ("jdbc:postgresql://h.example:5432/groupmaintenance?user=u&password=p",
     "groupmaintenance"),
    ("jdbc:postgresql://h.example:5432/b2b-stg?user=u&password=p", "b2b-stg"),
    ("jdbc:postgresql://h.example/plaindb", "plaindb"),
    ("jdbc:postgresql://h.example:5432/withslash/", "withslash"),
    ("  jdbc:postgresql://h.example:5432/padded?user=u  ", "padded"),
    ("jdbc:oracle:thin:@//h.example:1521/service_name", "service_name"),
    ("jdbc:oracle:thin:@h.example:1521:ORCLSID", "ORCLSID"),
    ("jdbc:sqlserver://h.example:1433;databaseName=mssqldb;encrypt=true",
     "mssqldb"),
])
def test_database_name_is_the_path_segment(conn, expected):
    assert rc._jdbc_database_name(conn) == expected


@pytest.mark.parametrize("conn", [
    "", None, "jdbc:postgresql://h.example:5432", "jdbc:postgresql://h.example",
])
def test_no_database_in_the_string_yields_empty(conn):
    assert rc._jdbc_database_name(conn) == ""


def test_name_never_carries_credentials_or_host():
    name = rc._jdbc_database_name(
        f"jdbc:postgresql://{FAKE_HOST}:5432/groupmaintenance"
        f"?user={FAKE_USER}&password=hunter2")
    assert name == "groupmaintenance"
    for secret in (FAKE_HOST, FAKE_USER, "hunter2", "password"):
        assert secret not in name


# --- the real XML ---------------------------------------------------------

def _goal3485_jdbc_step():
    if not os.path.isfile(PGR):
        pytest.skip("PartialGoalRegression.xml not in input/")
    root = ET.parse(PGR).getroot()
    for case in root.iter("{%s}testCase" % rc.NS["con"]):
        if "goal3485" not in (case.get("name") or ""):
            continue
        for step in case.iter("{%s}testStep" % rc.NS["con"]):
            if step.get("type") == "jdbc":
                return rc._parse_jdbc_step(step)
    pytest.skip("goal3485 has no jdbc step in this copy of the XML")


def test_goal3485_postgres_step_names_groupmaintenance():
    step = _goal3485_jdbc_step()
    assert step.step_name == "Postgres"
    assert rc._jdbc_database_name(step.connection_string) == "groupmaintenance"


@pytest.fixture(scope="module")
def converted_pgr(tmp_path_factory):
    """Convert PartialGoalRegression once for the emitted-code checks."""
    if not os.path.isfile(PGR):
        pytest.skip("PartialGoalRegression.xml not in input/")
    out = str(tmp_path_factory.mktemp("pgr"))
    argv = [sys.executable, os.path.join(HERE, "ra_converter.py"),
            "--input", PGR, "--output", out,
            "--skip-self-test", "--no-cursor-assist"]
    if os.path.isdir(DATA_DIR):
        argv += ["--data-dir", DATA_DIR]
    proc = subprocess.run(argv, capture_output=True, text=True, cwd=out)
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]
    return out


def _hooks_sources(out):
    files = glob.glob(os.path.join(
        out, "src", "main", "java", "**", "support", "partialgoalregression",
        "cases", "*Hooks*.java"), recursive=True)
    assert files, "no Hooks*.java generated for partialgoalregression"
    return {f: open(f, encoding="utf-8").read() for f in files}


def test_emitted_goal3485_jdbc_call_names_groupmaintenance(converted_pgr):
    joined = "\n".join(_hooks_sources(converted_pgr).values())
    assert 'Db.isConfigured("groupmaintenance")' in joined
    assert ('Db.forDatabase("groupmaintenance").queryAllRows(__jdbcSql_Postgres);'
            in joined)
    # the routed call sits inside the step it belongs to
    start = joined.index("// [jdbc step] Postgres")
    block = joined[start:start + 3000]
    assert 'Db.forDatabase("groupmaintenance")' in block


def test_emitted_code_never_carries_host_or_credentials(converted_pgr):
    for path, src in _hooks_sources(converted_pgr).items():
        for secret in sorted(_real_secrets()) + ["password=", "jdbc:postgresql://"]:
            assert secret not in src, f"{secret!r} leaked into {path}"


def test_ledger_records_the_routing(converted_pgr):
    hits = []
    for path in glob.glob(os.path.join(converted_pgr, "**", "*"), recursive=True):
        if not os.path.isfile(path) or not path.endswith((".md", ".csv", ".txt")):
            continue
        with open(path, encoding="utf-8", errors="replace") as fh:
            if "jdbc-database-routed" in fh.read():
                hits.append(path)
    assert hits, "no ledger/preflight file mentions jdbc-database-routed"
    for path in hits:
        src = open(path, encoding="utf-8", errors="replace").read()
        for secret in _real_secrets():
            assert secret not in src, f"{secret!r} leaked into {path}"


def test_the_leak_guard_has_real_values_to_guard():
    """Without this the two leak checks above pass on an empty set."""
    if not os.path.isfile(PGR):
        pytest.skip("PartialGoalRegression.xml not in input/")
    assert _real_secrets(), "no host/user parsed from the XML's JDBC steps"


def test_this_file_holds_no_real_host_or_user():
    src = open(os.path.abspath(__file__), encoding="utf-8").read()
    for secret in _real_secrets():
        assert secret not in src, "a real host/user is hardcoded in this test"


if __name__ == "__main__":
    # The other converter tests run as plain scripts; a pytest-only file
    # run that way executes nothing and exits 0, which reads as a pass.
    sys.exit(pytest.main([os.path.abspath(__file__), "-q"]))
