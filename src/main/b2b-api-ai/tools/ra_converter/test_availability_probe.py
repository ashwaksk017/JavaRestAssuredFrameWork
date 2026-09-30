"""The availability search must not vanish silently.

`groovyScript_availPropertyAndDates` walks ten property pairs x four date
offsets, re-running the shop call for each, and keeps the first with
inventory above a threshold. The translator recognised a ctx read and some
log lines, and the search itself disappeared -- so downstream steps used
whatever `generatedDatesAndProps.*` the CSV carried, a snapshot from when
the project was captured. That is how a shop query goes out against a
property and dates that had inventory months ago.

This emits the candidate ReadyAPI would try FIRST, computed at run time,
and says at WARN that the other 39 are not tried. It does not run the
search -- that needs a hook to invoke an engine, which is an addition to
the phase model rather than a translation.
"""

import ra_converter


PROBE = '''import groovy.json.JsonSlurper
def currentDate = new Date()
def arrivalDates = [
        currentDate.plus(84).format('yyyy-MM-dd'),
        currentDate.plus(54).format('yyyy-MM-dd'),
        currentDate.plus(64).format('yyyy-MM-dd'),
        currentDate.plus(74).format('yyyy-MM-dd')
]
def departureDates = [
        currentDate.plus(85).format('yyyy-MM-dd'),
        currentDate.plus(55).format('yyyy-MM-dd'),
        currentDate.plus(65).format('yyyy-MM-dd'),
        currentDate.plus(75).format('yyyy-MM-dd')
]
def hcrs = ["NYCNH","LONME","ROMHI"]
def pcrs = ["HNLES","MEMSG","WASDL"]
Outer:
for (int j = 0; j < hcrs.size(); j++) {
    for (int i = 0; i < arrivalDates.size(); i++) {
        generatedDatesPropertyVal.setPropertyValue("hcrs", hcrs[j])
        def result = testStep.run(testRunner, context)
        for(def roomRate : json.roomRates) {
            int inventory = roomRate.inventory ?: 0
            if(inventory > 9) { break Outer }
        }
    }
}'''


def _render(script, name="groovyScript_availPropertyAndDates"):
    step = ra_converter.GroovyStep(step_name=name, script=script)
    return ra_converter.Emitter._render_availability_probe(
        ra_converter.Emitter.__new__(ra_converter.Emitter), step)


def test_the_probe_is_recognised():
    out = _render(PROBE)
    assert out, "the availability search was not recognised"


def test_only_the_arrival_offsets_are_counted():
    """departureDates are arrival+1, not separate candidates. Counting
    both gave 8 offsets and a claim of 80 combinations where ReadyAPI
    tries 12 here (3 properties x 4 dates)."""
    body = "\n".join(_render(PROBE))
    assert "List.of(84, 54, 64, 74)" in body, body
    assert "12 combination(s)" in body, body


def test_the_first_candidate_is_emitted_and_computed_at_runtime():
    body = "\n".join(_render(PROBE))
    assert "LocalDate.now().plusDays(84)" in body, body
    assert '"generatedDatesAndProps.hcrs", "NYCNH"' in body, body
    assert '"generatedDatesAndProps.pcrs", "HNLES"' in body, body
    # departure is the day after the arrival it pairs with
    assert "plusDays(1)" in body, body


def test_not_running_the_search_is_stated_loudly():
    """A test that quietly checks one combination where ReadyAPI checked
    twelve is the silence this converter keeps having to undo."""
    body = "\n".join(_render(PROBE))
    assert "LOG.warn" in body, body
    assert "NOT run" in body, body
    assert "inventory > 9" in body, body


def test_the_candidates_are_published_for_later():
    body = "\n".join(_render(PROBE))
    assert "candidateProps" in body, body
    assert "candidateOffsets" in body, body


def test_a_script_that_never_reruns_a_step_is_not_claimed():
    """Conservative: no `.run(testRunner`, not a search."""
    assert _render('def hcrs = ["A"]\ndef x = currentDate.plus(84)') is None


def test_a_search_without_property_lists_is_not_claimed():
    assert _render(
        'def result = testStep.run(testRunner, context)\n'
        'generatedDatesPropertyVal.setPropertyValue("x", 1)') is None


def test_a_search_without_offsets_is_not_claimed():
    assert _render(
        'def hcrs = ["A"]\nsetPropertyValue("h", hcrs[0])\n'
        'def r = testStep.run(testRunner, context)') is None
