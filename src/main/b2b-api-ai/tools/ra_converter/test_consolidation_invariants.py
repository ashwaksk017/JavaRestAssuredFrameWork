"""Folding a family into one implementation must not change what MATCHES.

Consolidation rewrites a phase body to a one-line wrapper. Three separate
things read those bodies, and each one broke in turn when it read the
folded form instead of the original:

  - the response-field collector, which declares `protected Response
    fooRes;`. Reading folded bodies declared nothing, and the suite class
    failed to compile with 66 "cannot find symbol" errors.
  - `_shared_phase_matches` / `_suite_phase_matches`, which decide whether
    a per-case class may inherit a method instead of re-declaring it.
    Comparing a case's unfolded body against the folded stored one missed
    every time, so all 560 cases inlined their own copy and the tree went
    from 146 files to 706 -- consolidation as a net LOSS.
  - the fingerprint indexes, built from whichever map was handed to them.

So the invariant is not "folding works" but "folding is invisible to
everything except the emitted text". The matching tests below are the
ones that would have caught the 146 -> 706 regression.

Two further failure modes are pinned because they were silent rather than
loud: a dead slot regex produces zero groups, which reads as "nothing to
fold" rather than as a bug; and verify runners share a class with phases,
so a verify family and a same-named phase family would both claim
`<stem>Impl`.
"""
from __future__ import annotations

import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import ra_converter as rc  # noqa: E402


def body(step, tmpl, src="aRes", path="memberId"):
    """A REST phase body: one response field, one template, one extract."""
    return [
        "// ==== REST step: %s ====" % step,
        "this.%sRes = RestStep.exec(ctx, row, softAssert, holder, testCaseId)" % step,
        '        .name("%s")' % step,
        "        .template(Templates.%s)" % tmpl,
        '        .put("/x", (b,q,h) -> client.upd('
        'RestUtilities.safeJsonExtract(%s, "%s"), b));' % (src, path),
    ]


def emitter():
    return rc.Emitter("out", suite_name="s1")


def test_slot_patterns_are_alive():
    """A dead pattern yields zero groups, which looks like 'nothing to
    fold'. These once landed double-escaped and matched nothing at all."""
    E = rc.Emitter
    assert E._STR_SLOT.search('.name("x")'), "_STR_SLOT is dead"
    assert E._RES_SLOT.search('safeJsonExtract(fooRes, "a")'), "_RES_SLOT is dead"
    assert E._TMPL_SLOT.search(".template(Templates.X_Y)"), "_TMPL_SLOT is dead"


def test_stem_strips_trailing_digits():
    """updateAccountMember11 and 12 must reduce to one stem. The stem
    stripper was double-escaped for a while, so it never matched and no
    numbered family could group at all."""
    out, impls = emitter()._consolidate_families({
        "updateAccountMember1": body("same", "T1", "aRes"),
        "updateAccountMember11": body("same", "T2", "bRes"),
        "updateAccountMember12": body("same", "T3", "cRes"),
    })
    assert len(impls) == 1, impls
    assert all(len(b) == 1 for b in out.values()), out


def test_a_different_step_name_is_never_folded_in():
    """The step name sits inside the response field IDENTIFIER, which
    cannot be built by concatenation, so it must split the group."""
    out, _ = emitter()._consolidate_families({
        "fam1": body("stepA", "T1", "aRes"),
        "fam2": body("stepA", "T2", "bRes"),
        "fam3": body("stepB", "T3", "cRes"),
    })
    assert len(out["fam3"]) > 1, "a different step name was folded in"


def test_self_read_field_is_not_parameterised():
    """A body that assigns fooRes and reads it back must keep the read.
    Parameterising it feeds the caller's stale value instead of the one
    just assigned -- a silent behaviour change, not a compile error."""
    out, impls = emitter()._consolidate_families({
        "sr1": body("same", "T1", "sameRes"),
        "sr2": body("same", "T2", "sameRes"),
    })
    assert len(impls) == 1, impls
    assert "sameRes" in impls[0], "self-read field was parameterised away"
    first = impls[0].split("\n")[0]
    assert "Response " not in first, first


def test_impl_numbering_is_per_stem():
    """Off a global counter, adding one family renumbers impls in every
    later family, so unrelated converts diff everywhere."""
    _, impls = emitter()._consolidate_families({
        "fam1": body("sA", "T1", "aRes"), "fam2": body("sA", "T2", "bRes"),
        "fam3": body("sB", "T3", "cRes"), "fam4": body("sB", "T4", "dRes"),
        "other1": body("sC", "T5", "eRes"), "other2": body("sC", "T6", "fRes"),
    })
    names = [i.split("(")[0].split()[-1] for i in impls]
    assert names == ["famImpl", "famImpl2", "otherImpl"], names


def test_matching_survives_folding():
    """THE 146 -> 706 REGRESSION. A case still renders the ORIGINAL body;
    if that stops matching the shared phase, the case re-declares it."""
    em = emitter()
    orig, folded = body("foo", "T1"), ["fooImpl(Templates.T1);"]
    other = body("bar", "T9", "bRes")
    em._shared_phases = {"foo1": {"body": folded, "match_body": orig,
                                  "resp": [], "cases": []}}
    assert em._shared_phase_matches("foo1", orig), "folded body broke matching"
    assert not em._shared_phase_matches("foo1", other), "matched a different body"

    em._shared_verifies = {("C", "v1"): {"body": folded, "match_body": orig,
                                         "resp": [], "cases": []}}
    assert em._shared_verify_matches("C", "v1", orig), "verify matching broke"
    assert not em._shared_verify_matches("C", "v1", other), "verify over-matched"


def test_verify_impls_cannot_collide_with_a_phase_family():
    """Verify runners land in the SAME class as phases, and this suite has
    both a phase family and a verify family named verifyProgramAccount."""
    em = emitter()
    ph = {"verifyProgramAccount1": body("sA", "T1", "aRes"),
          "verifyProgramAccount2": body("sA", "T2", "bRes")}
    vf = {("C", "verifyProgramAccount1"): body("sA", "T1", "aRes"),
          ("C", "verifyProgramAccount2"): body("sA", "T2", "bRes")}
    _, pi = em._consolidate_families(ph)
    vo, vi = em._consolidate_families(vf, name_of=lambda k: k[1],
                                      suffix="VerifyImpl")
    assert pi and vi, (pi, vi)
    pn = {i.split("(")[0].split()[-1] for i in pi}
    vn = {i.split("(")[0].split()[-1] for i in vi}
    assert not (pn & vn), "verify impl collides with phase impl: %s" % (pn & vn)
    assert all(isinstance(k, tuple) for k in vo), "tuple keys were rewritten"


def test_indexes_and_resp_are_built_from_prefold_bodies():
    """Source-level: the suite base must feed its fingerprint indexes and
    its response-field collector the RAW snapshots. Functional coverage
    would need a whole convert, and the failure is silent, so pin the
    wiring itself."""
    src = io.open(os.path.join(HERE, "ra_converter.py"), encoding="utf8").read()
    for needle in ("_raw_phases = dict(phases)",
                   "_raw_verifies = dict(verifies)",
                   "for n, b in _raw_phases.items()",
                   "for k, b in _raw_verifies.items()",
                   "for body in _raw_bodies:",
                   'info["match_body"] = list(info["body"])'):
        assert needle in src, "lost pre-fold wiring: %s" % needle


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
