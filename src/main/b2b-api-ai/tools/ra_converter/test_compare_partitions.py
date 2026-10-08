"""The ComparePartitions script is translated, and the waits around the
partition listings really wait.

A Kafka case lists a topic's partitions, makes the call that should publish
an event, lists them again, and reads the event from the partition whose end
offset moved. The comparison is a Groovy loop no recogniser knew: 169 steps
in four suites emitted two bindings and a log line, and the partition and
offset came from the datasheet -- the author's last ReadyAPI run. The event
read back was somebody else's, or `/partitions/null`.

The Delay steps beside the listings were deferred, which for a listing means
never slept (it answers 200 at once, so nothing later has a failure to spend
the wait on).

    python tools/ra_converter/test_compare_partitions.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import groovy_translator as gt  # noqa: E402
import ra_converter as rc  # noqa: E402

SCRIPT = '''import groovy.json.JsonSlurper

def partBeforeResponse = context.expand( '${Partition_before_http_request_200#Response}' )
def partAfterResponse = context.expand( '${Partition_after_http_request_200#Response}' )

Map beforeMap = new JsonSlurper().parseText(partBeforeResponse) as Map
Map afterMap = new JsonSlurper().parseText(partAfterResponse) as Map

def diffNum = 0
def partitionId;
def endOffset;

assert beforeMap.size() == afterMap.size()

for(int i = 0; i < beforeMap.size(); i++){
	assert beforeMap[i].get("partitionId") == afterMap[i].get("partitionId")
}

for(int i = 0; i < beforeMap.size(); i++){
	if(beforeMap[i].get("endOffset") != afterMap[i].get("endOffset")){
		log.info(beforeMap[i])
		endOffset = beforeMap[i].get("endOffset")
		partitionId = beforeMap[i].get("partitionId")
		diffNum++
	}
}

log.info(diffNum + " difference(s) found:------------------------------------- ")
%s
def diffPropStep = testRunner.testCase.getTestStepByName("%s")

diffPropStep.setPropertyValue("partitionId", partitionId.toString())
diffPropStep.setPropertyValue("endOffset", endOffset.toString())
'''


def script(extra="", target="difference"):
    return SCRIPT % (extra, target)


def test_the_plain_script_is_recognised():
    assert gt.compare_partitions_spec(script()) == {
        "before": "Partition_before_http_request_200",
        "after": "Partition_after_http_request_200",
        "target": "difference", "offset_add": 0, "expect_none": False}


def test_the_plus_one_variant_carries_the_increment():
    assert gt.compare_partitions_spec(script("endOffset = endOffset +1"))["offset_add"] == 1


def test_a_commented_out_increment_is_not_an_increment():
    assert gt.compare_partitions_spec(script("//endOffset = endOffset +1"))["offset_add"] == 0


def test_the_target_step_and_listing_names_follow_the_script():
    s = script(target="difference_reject").replace(
        "http_request_200#", "http_request_200_reject#")
    sp = gt.compare_partitions_spec(s)
    assert sp["target"] == "difference_reject"
    assert sp["before"] == "Partition_before_http_request_200_reject"
    assert sp["after"] == "Partition_after_http_request_200_reject"


def test_the_expect_no_event_variant():
    s = script("assert diffNum == 0").replace(
        'diffPropStep.setPropertyValue("partitionId", partitionId.toString())',
        'diffPropStep.setPropertyValue("partitionId", diffNum.toString())').replace(
        'diffPropStep.setPropertyValue("endOffset", endOffset.toString())\n', "")
    assert gt.compare_partitions_spec(s)["expect_none"] is True
    lines, _ = gt.translate(s, {}, "ComparePartitions-Expecting-0-differences")
    assert any("KafkaPartitions.expectNoDifference(" in l for l in lines)


def test_the_emitted_call_reads_both_listings_and_names_the_target():
    lines, meta = gt.translate(script("endOffset = endOffset +1"), {}, "ComparePartitions")
    call = [l for l in lines if "KafkaPartitions.publishDifference(" in l]
    assert len(call) == 1, lines
    assert ('publishDifference(ctx, softAssert, "difference", '
            'TestSupport.ctxGet(ctx, "Partition_before_http_request_200_Response"), '
            'TestSupport.ctxGet(ctx, "Partition_after_http_request_200_Response"), 1L);') in call[0]
    assert "compare_partitions" in meta["patterns_matched"]


# --- what it must NOT claim ----------------------------------------------

def test_a_script_that_clears_the_cursor_is_not_a_comparison():
    s = ("def diffStep = testRunner.testCase.getTestStepByName('difference')\n"
         "diffStep.setPropertyValue('partitionId', null)\n"
         "diffStep.setPropertyValue('endOffset',  null)\n")
    assert gt.compare_partitions_spec(s) is None
    lines, _ = gt.translate(s, {}, "cleanUp_difference")
    assert not any("KafkaPartitions" in l for l in lines)


def test_an_offset_taken_from_the_after_listing_is_not_guessed():
    s = script().replace('endOffset = beforeMap[i].get("endOffset")',
                         'endOffset = afterMap[i].get("endOffset")')
    assert gt.compare_partitions_spec(s) is None


def test_an_expected_count_other_than_zero_is_left_alone():
    assert gt.compare_partitions_spec(script("assert diffNum == 2")) is None


def test_both_listings_being_the_same_step_is_not_a_comparison():
    s = script().replace("Partition_after_http_request_200#", "Partition_before_http_request_200#")
    assert gt.compare_partitions_spec(s) is None


# --- the waits ------------------------------------------------------------

def _rest(name, verb, path):
    s = rc.RestStep.__new__(rc.RestStep)
    s.step_name, s.http_method, s.resource_path, s.assertions = name, verb, path, []
    return s


def _delay(ms):
    d = rc.DelayStep.__new__(rc.DelayStep)
    d.step_name, d.delay_ms = "Delay", ms
    return d


class Case:
    def __init__(self, steps):
        self.steps = steps


def test_a_delay_before_a_partition_listing_really_sleeps():
    d1, d2 = _delay(5000), _delay(2000)
    case = Case([_rest("create", "POST", "/guests/{guestId}/businesses"), d1,
                 _rest("Partition_before_http_request_200", "GET", "/topics/{topicName}/partitions"),
                 _rest("activate", "POST", "/businesses/{accountId}/activate"), d2,
                 _rest("Partition_after_http_request_200", "GET", "/topics/{topicName}/partitions")])
    assert rc._delay_guards_offset_snapshot(d1, case)
    assert rc._delay_guards_offset_snapshot(d2, case)


def test_any_other_delay_is_still_deferred():
    """NEGATIVE CONTROL: deferring is what keeps a run from sleeping through
    every wait; only the snapshot waits are exempt."""
    d = _delay(5000)
    case = Case([d, _rest("read", "GET", "/businesses/{accountId}"),
                 _rest("Partition_after_http_request_200", "GET", "/topics/{topicName}/partitions")])
    assert not rc._delay_guards_offset_snapshot(d, case)
    # reading ONE partition is not a listing
    d2 = _delay(5000)
    case2 = Case([d2, _rest("Get_Partition_details_get_200", "GET",
                            "/topics/{topicName}/partitions/{partitionId}")])
    assert not rc._delay_guards_offset_snapshot(d2, case2)
    assert not rc._delay_guards_offset_snapshot(_delay(1), case)      # not in the case


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok  " + name)
            n += 1
    print("%d passed" % n)
