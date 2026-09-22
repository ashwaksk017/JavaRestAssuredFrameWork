"""The emitted tree is REST Assured / Java. Labels the CONVERTER invents
must not call a translated step 'groovy' -- that describes the SOURCE,
not what runs. A node reading 'groovy: Token' in a flow diagram sends a
reader looking for Groovy that is not there.

This pins BOTH directions: the invented labels drop the word, and the
three places that legitimately keep it do not drift.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import ra_converter as rc  # noqa: E402

SRC = open(os.path.join(HERE, "ra_converter.py"), encoding="utf-8").read()


def _lbl(text, n=50):
    return (text or "")[:n]


def _emitter():
    return rc.Emitter("out", suite_name="s1")


def test_diagram_node_does_not_say_groovy():
    """The label in the rendered png."""
    node = rc.GroovyStep(step_name="Token", script="def x = 1")
    _shape, label = _emitter()._flow_step_node(node, _lbl)
    assert "groovy" not in label.lower(), label
    assert label == "script: Token", label


def test_step_table_row_does_not_say_groovy():
    """The per-case markdown step table."""
    node = rc.GroovyStep(step_name="DataGenInput", script="def x = 1")
    kind, detail = _emitter()._flow_step_row(node, _lbl)
    assert "groovy" not in kind.lower(), kind
    assert "groovy" not in detail.lower(), detail
    assert kind == "SCRIPT", kind


def test_emitted_java_labels_do_not_say_groovy():
    """The console marker and the Allure step name a tester reads at
    runtime. Asserted on the emitter template: these are f-strings, so a
    literal check is what pins the emitted text."""
    assert 'LOG.info(" .. script step: ' in SRC
    assert '"script: {_jlit(step.step_name)}");' in SRC
    assert 'LOG.info(" .. groovy step: ' not in SRC
    assert '"groovy: {_jlit(step.step_name)}");' not in SRC


def test_structural_signature_still_says_GROOVY():
    """NOT a label: _step_sig groups equivalent step sequences into shared
    helpers. It never reaches output, and changing the literal would
    repartition clusters and rename generated methods."""
    node = rc.GroovyStep(step_name="Token", script="def x = 1")
    assert rc._step_sig(node) == ("GROOVY", "Token")


def test_audit_still_records_the_source_step_type():
    """The audit's job is to say what the ReadyAPI XML contained, so
    steps.csv keeps GROOVY even though the emitted step is Java."""
    assert '"GROOVY", "-", "-", cov, ""' in SRC


def test_assertion_provenance_comments_are_left_alone():
    """'// [GroovyScriptAssertion]' names the SoapUI assertion TYPE, one of
    a family with MessageContentAssertion / DataAndMetadataAssertion.
    Dropping only this one would break the pattern and lose traceability."""
    assert "[GroovyScriptAssertion]" in SRC
    assert "[MessageContentAssertion]" in SRC


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
