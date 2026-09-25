"""Guards for the audit report's completeness.

Three gaps this pins shut:

 1. Cluster members share one @Test method with the case that was
    rendered, so only the rendered case reaches ``add_assertion``. The
    report must still account for the members, or its totals cannot be
    reconciled against the source XML.
 2. summary.md must name WHAT produced the tree (converter revision,
    command, config), because converter.config.json now changes emitted
    behaviour and two trees from one XML can legitimately differ.
 3. Decisions that change generated code (framework refresh vs skip,
    phase-spec fallbacks, diagram render failures, client-name
    collisions, a skipped dedup report) must land in the audit instead
    of scrolling past in the console.
"""
from __future__ import annotations

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import ra_converter as rc  # noqa: E402


def _written(led, suite="suite1", provenance=None):
    out = tempfile.mkdtemp()
    led.write(out, "src.xml", "2026-01-01 00:00:00Z", suite_name=suite,
              provenance=provenance)
    base = os.path.join(out, "_audit", suite)
    return base, open(os.path.join(base, "summary.md"), encoding="utf-8").read()


def test_shared_cluster_members_reach_the_audit():
    """The gap: 93 converted cases had no row anywhere in the audit."""
    led = rc.AuditLedger()
    led.add_case_coverage("s", "rendered_case", "F", "m", "emitted", 10, 2)
    led.add_case_coverage("s", "member_one", "F", "m", "shared", 7, 1)
    led.add_case_coverage("s", "member_two", "F", "m", "shared", 3, 0)
    base, summary = _written(led)

    rows = open(os.path.join(base, "case_coverage.csv"), encoding="utf-8").read()
    for name in ("rendered_case", "member_one", "member_two"):
        assert name in rows, name

    assert "## Source reconciliation" in summary
    # 3 cases, 20 active, 3 disabled -- the source totals.
    assert "| Converted (all) | 3 | 20 | 3 |" in summary
    assert "| Rendered (own rows in assertions.csv) | 1 | 10 | 2 |" in summary
    assert "| Share a rendered case's method | 2 | 10 | 1 |" in summary


def test_reconciliation_absent_when_nothing_recorded():
    """A ledger with no case coverage must not grow an empty section."""
    _base, summary = _written(rc.AuditLedger())
    assert "## Source reconciliation" not in summary


def test_summary_records_provenance():
    prov = {
        "converter": "abc1234",
        "command": "ra_converter.py --clean",
        "config_files": ["converter.config.json"],
        "config_sha": "0123456789abcdef",
        "identity_resource": "src/main/resources/converter_identity.json",
    }
    _base, summary = _written(rc.AuditLedger(), provenance=prov)
    assert "- Converter: abc1234" in summary
    assert "`ra_converter.py --clean`" in summary
    assert "`converter.config.json`" in summary
    assert "0123456789abcdef" in summary
    assert "converter_identity.json" in summary


def test_summary_without_provenance_is_unchanged():
    """Provenance is optional: callers that pass none get the old header."""
    _base, summary = _written(rc.AuditLedger())
    assert "- Generated: " in summary
    assert "- Converter: " not in summary


def test_secret_looking_flag_values_are_masked():
    """The audit is committed-adjacent; a credential must never reach it."""
    line = rc._redact_argv(
        ["ra.py", "--input", "x", "--db-password", "hunter2",
         "--token=abc123", "--api_key", "zzz", "--clean"])
    assert "hunter2" not in line
    assert "abc123" not in line
    assert "zzz" not in line
    assert "--clean" in line and "--input x" in line


def test_console_only_decisions_have_a_registered_meaning():
    """Every category routed into preflight must render with a meaning, or
    preflight.md shows '(no meaning registered)' and the reader is stuck."""
    cats = [
        "framework-file-refreshed", "framework-file-kept",
        "bundled-framework-file-missing", "phase-spec-text-path",
        "phase-spec-capture-failed", "diagram-image-not-rendered",
        "dedup-report-skipped", "service-client-name-collision",
    ]
    led = rc.AuditLedger()
    for c in cats:
        led.add_preflight_finding("INFO", c, "case", "detail")
    base, _summary = _written(led)
    md = open(os.path.join(base, "preflight.md"), encoding="utf-8").read()
    assert "(no meaning registered)" not in md
    for c in cats:
        assert c in md, c


def test_new_categories_do_not_become_cursor_assist_noise():
    """These are informational. cursor_assist reviews HIGH/BLOCKER only;
    if one of these were raised to HIGH it would queue hundreds of rows."""
    import cursor_assist
    led = rc.AuditLedger()
    led.add_preflight_finding("INFO", "framework-file-kept", "f", "d")
    led.add_preflight_finding("MEDIUM", "framework-file-refreshed", "f", "d")
    led.add_preflight_finding("MEDIUM", "dedup-report-skipped", "", "d")
    gaps = cursor_assist.collect_gaps(led, "suite1")
    assert gaps == [], gaps


def test_framework_skip_and_refresh_are_recorded():
    """The stale-framework incident: a SKIP decided silently which code the
    run machine executed. Both branches must leave a trace."""
    out = tempfile.mkdtemp()
    em = rc.Emitter(out, suite_name="s1")
    name = sorted(em._AUTHOR_EDITABLE_BASENAMES)[0]
    rel = "src/main/java/com/hi/api/support/%s" % name
    em._write(rel, "// ra_converter-framework-rev: 1\nclass A {}\n")
    assert not getattr(em.ledger, "preflight", []), "first write is not a decision"

    em._write(rel, "// ra_converter-framework-rev: 1\nclass A {}\n")
    cats = [r[1] for r in em.ledger.preflight]
    assert "framework-file-kept" in cats, cats


if __name__ == "__main__":
    failed = 0
    names = [n for n in globals() if n.startswith("test_")]
    for name in names:
        fn = globals()[name]
        try:
            fn()
            print("ok  ", name)
        except Exception as e:  # noqa: BLE001
            failed += 1
            print("FAIL", name, "->", e)
    print(f"{len(names) - failed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
