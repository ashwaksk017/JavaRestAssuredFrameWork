"""Guards for the ctx-dataflow checker itself.

A checker that silently stops detecting is worse than no checker, so the
collision fix below is pinned by a test.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import check_ctx_dataflow as C


def test_setup_writes_are_keyed_by_suite():
    """SetupHelper flow names repeat across suites.

    `flow_A` exists in 3 of the 5 reference suites. Keyed by bare name the
    last file walked won, so eadkafkaevents' flow_A -- which seeds
    Properties.* -- was overwritten by programaccountregression's, which
    does not, and every Properties.* read in the Kafka chains was reported
    as an unsatisfied dataflow.
    """
    src = C.__file__.replace(".pyc", ".py")
    with open(src, encoding="utf8") as fh:
        text = fh.read()
    assert "setup_writes[(suite, name)] = w" in text, (
        "setup_writes must be keyed by (suite, name), not by name alone")
    assert "setup_writes.get((suite, payload)" in text, (
        "lookup must pass the suite through")


def test_put_env_scoped_counts_as_a_producer():
    """putEnvScoped always writes its key -- it must satisfy later reads."""
    body = 'ImportedScenario.putEnvScoped(ctx, "Properties.topicenv", x);'
    writes = [p for _o, k, p in C.method_events(body) if k == "w"]
    assert "Properties.topicenv" in writes, writes


def test_seed_from_row_is_a_wildcard_producer():
    body = 'CtxFields.seedFromRow(ctx, row, "Properties.");'
    writes = [p for _o, k, p in C.method_events(body) if k == "w"]
    assert "Properties.*" in writes, writes


def test_reads_inside_log_lines_are_not_counted():
    body = 'LOG.info("x {}", ImportedScenario.ctxGet(ctx, "Properties.foo"));'
    reads = [p for _o, k, p in C.method_events(body) if k == "r"]
    assert reads == [], reads
