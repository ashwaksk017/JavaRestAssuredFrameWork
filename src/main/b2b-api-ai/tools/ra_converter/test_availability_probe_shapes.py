"""The availability search has to RECOGNISE the scripts the project has.

Recognising one shape and silently falling back on the rest is the
worst outcome: the generated code still runs, still publishes a
property, and nothing says the search never happened. Measured across
the 29 input XMLs, the first version recognised 11 of 92 sites. The
other 81 published the FIRST candidate unverified, which is a
different test -- if that property has no rooms, every downstream call
is built on it and the failures read like API problems.

Two misses, both small and both invisible:

  * the dominant script computes its dates into NAMED VARIABLES first
    and puts the names in the list, so scoping the offset search to the
    list text finds no `plus(N)` at all;
  * `getTestStepByName('GET_Shop')` uses SINGLE quotes in 12 of 17
    scripts, and the matcher only accepted double.

    python tools/ra_converter/test_availability_probe_shapes.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ra_converter  # noqa: E402

_EMITTER = next(v for v in vars(ra_converter).values()
                if isinstance(v, type)
                and hasattr(v, "_render_availability_probe"))


def _offsets(script):
    return _EMITTER._probe_arrival_offsets(_EMITTER.__new__(_EMITTER), script)


INLINE = """
def currentDate = new Date()
def arrivalDates = [
    currentDate.plus(32).format('yyyy-MM-dd'),
    currentDate.plus(52).format('yyyy-MM-dd')
]
def departureDates = [
    currentDate.plus(33).format('yyyy-MM-dd'),
    currentDate.plus(53).format('yyyy-MM-dd')
]
"""

VIA_VARS = """
def currentDate = new Date()
def formattedArrDate = currentDate.plus(32).format('yyyy-MM-dd')
def formattedDepDate = currentDate.plus(33).format('yyyy-MM-dd')
def formattedArrDate2 = currentDate.plus(52).format('yyyy-MM-dd')
def formattedDepDate2 = currentDate.plus(53).format('yyyy-MM-dd')
def formattedArrDate3 = currentDate.plus(62).format('yyyy-MM-dd')
def formattedDepDate3 = currentDate.plus(63).format('yyyy-MM-dd')
def arrivalDates = [formattedArrDate.toString(),formattedArrDate2.toString(),formattedArrDate3.toString()]
def departureDates = [formattedDepDate.toString(),formattedDepDate2.toString(),formattedDepDate3.toString()]
"""


def test_an_inline_arrival_list_is_read():
    assert _offsets(INLINE) == [32, 52], _offsets(INLINE)


def test_dates_reached_through_named_variables_are_resolved():
    """The dominant shape -- 79 of 92 sites were lost to this."""
    assert _offsets(VIA_VARS) == [32, 52, 62], _offsets(VIA_VARS)


def test_departure_offsets_are_never_counted():
    """Counting both gave 8 offsets and a claim of 80 combinations
    where ReadyAPI tries 40."""
    for script in (INLINE, VIA_VARS):
        got = _offsets(script)
        for dep in (33, 53, 63):
            assert dep not in got, (script, got)


def test_order_is_the_order_the_script_tries():
    """ReadyAPI keeps the FIRST hit, so a different order picks a
    different winner."""
    shuffled = VIA_VARS.replace(
        "[formattedArrDate.toString(),formattedArrDate2.toString(),formattedArrDate3.toString()]",
        "[formattedArrDate3.toString(),formattedArrDate.toString(),formattedArrDate2.toString()]")
    assert _offsets(shuffled) == [62, 32, 52], _offsets(shuffled)


def test_a_script_with_no_arrival_list_still_yields_offsets():
    assert _offsets("def x = currentDate.plus(7).format('yyyy-MM-dd')") == [7]


def test_no_dates_at_all_means_no_offsets():
    assert _offsets("def hcrs = [\"AAA\"]") == []


# ---------------------------------------------- the real scripts

def _real_scripts():
    import glob
    import html
    import io
    import re
    here = os.path.dirname(os.path.abspath(__file__))
    out = {}
    for f in glob.glob(os.path.join(here, "input", "*.xml")):
        t = html.unescape(io.open(f, encoding="utf-8", errors="ignore").read())
        for m in re.finditer(
                r'<con:testStep[^>]*type="groovy"[^>]*>(.*?)</con:testStep>',
                t, re.S):
            s = re.search(r'<script>(.*?)</script>', m.group(1), re.S)
            if not s:
                continue
            sc = s.group(1).strip()
            if ".run(testRunner" not in sc or "roomRates" not in sc:
                continue
            out[hash(sc)] = sc
    return list(out.values())


def test_most_real_scripts_are_recognised():
    """A guard against a future narrowing that quietly loses coverage.

    Not 100%: the EMPTY-roomRates search is the opposite question and is
    deliberately excluded. If this drops, the search stopped running for
    scripts it used to handle and NOTHING ELSE would say so.
    """
    import re
    scripts = _real_scripts()
    if not scripts:
        return                      # input XMLs not present in this checkout
    ok = 0
    for sc in scripts:
        if re.search(r'emptyResponseFound|EMPTY RESPONSE FOUND', sc):
            ok += 1                 # correctly skipped
            continue
        if _offsets(sc):
            ok += 1
    assert ok / len(scripts) >= 0.95, (
        "only %d of %d availability scripts yield offsets" % (ok, len(scripts)))


def test_the_step_name_matcher_accepts_both_quote_styles():
    """12 of 17 scripts write getTestStepByName('X') with SINGLE quotes.

    Matching only double quotes made the probe target invisible, so the
    lambda could not be built and the site fell back -- with a message
    that blamed the script shape rather than the matcher.
    """
    import inspect
    import re
    src = inspect.getsource(_EMITTER._availability_probe_lambda)
    m = re.search(r'getTestStepByName\\\(.*?\)["\']', src)
    assert m, "could not find the step-name pattern in the source"
    frag = m.group(0)
    assert "'" in frag and '"' in frag, (
        "the step-name pattern must accept both quote styles: " + frag)


# ------------------------------------------- the probe must send real data

def test_every_probe_parameter_resolves_to_something_that_exists():
    """A probe parameter read from a key nothing publishes is DROPPED.

    This is the check that was missing. The first version emitted
    `row.get("qry_<step>_<param>")` for every non-literal parameter --
    but that column exists only for a parameter the XML gave no
    reference for. A parameter bound to `${DataSource#peakRoom}` is
    emitted by the normal path as `#DataSource_peakRoom#` and has NO
    qry_ column, so the lookup returned null, `__av.accept` skipped the
    empty, and the probe went out WITHOUT peakRooms.

    The server answered 400 to 460 of 500 probes and no search ever
    succeeded -- which reads as an environment with no availability
    rather than a malformed request. Nothing in the run said the
    parameter had been dropped.

    One grep would have caught it, so here is the grep.
    """
    import csv as _csv
    import glob as _glob
    import io as _io
    import re as _re

    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(here))
    hooks = _glob.glob(os.path.join(
        root, "src", "main", "java", "com", "hi", "api", "support",
        "*", "cases", "Hooks1.java"))
    if not hooks:
        return                       # nothing generated in this checkout

    bad = []
    for h in hooks:
        suite = h.replace("\\", "/").split("/support/")[1].split("/")[0]
        text = _io.open(h, encoding="utf-8", errors="ignore").read()
        # Both shapes. `resolve("#key#")` is how the probe reads a value
        # now; `row.get("col")` inside a probe lambda is how it used to,
        # and that is the shape that shipped broken -- so the guard has to
        # catch it too or it only protects against the fix.
        refs = set(_re.findall(
            r'AvailabilitySearch\.resolve\("#([^"#]+)#"', text))
        for block in _re.findall(r'__av\.accept\([^;]*;', text):
            refs |= set(_re.findall(r'row\.get\("([^"]+)"\)', block))
        if not refs:
            continue
        cols = set()
        for f in _glob.glob(os.path.join(
                root, "src", "test", "resources", "csv", suite, "**", "*.csv"),
                recursive=True):
            try:
                with _io.open(f, encoding="utf-8-sig") as fh:
                    cols |= set(next(_csv.reader(fh)))
            except Exception:
                pass
        if not cols:
            continue                 # no CSVs for this suite on disk
        for r in sorted(refs):
            # Only refs that MUST come from a column are enforced. A
            # `DataSource_*` or `qry_*` ref has no other producer, so a
            # missing column means the parameter is dropped -- that is
            # the bug this guards. A ref to another STEP's property
            # (`GET_Groups_MeetingDates_arrivalDate`) is published into
            # ctx at runtime and legitimately has no column, so failing
            # on it would be a false alarm that gets the guard disabled.
            if not _re.match(r'(?i)^(datasource|qry_)', r):
                continue
            if r not in cols and r.replace("_", ".") not in cols:
                bad.append("%s: probe resolves #%s# but no CSV column has it"
                           % (suite, r))
    assert not bad, (
        "A probe parameter that does not resolve is silently DROPPED from "
        "the request, and the server's 400 then looks like missing "
        "availability:" + "".join(chr(10) + "  " + b for b in bad))


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok  " + name)
            passed += 1
    print("%d passed" % passed)
