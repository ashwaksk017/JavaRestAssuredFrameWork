package com.hi.api.rest.utilities;

import java.util.List;
import java.util.Map;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.testng.asserts.SoftAssert;

import com.hi.api.support.ImportedScenario;

import io.restassured.path.json.JsonPath;

/**
 * Which partition of a topic moved between two listings.
 *
 * <p>The event-verification cases all work the same way: list the topic's
 * partitions, do the thing that should publish an event, list them again,
 * and read the event from the partition whose end offset changed:</p>
 *
 * <pre>
 * Partition_before  GET /topics/{topic}/partitions
 * (the call under test)
 * Partition_after   GET /topics/{topic}/partitions
 * ComparePartitions for each i: if before[i].endOffset != after[i].endOffset
 *                       -&gt; difference.partitionId, difference.endOffset
 * Get_Partition_details  GET /topics/{topic}/partitions/{partitionId}?offset={endOffset}
 * </pre>
 *
 * <p>ComparePartitions is a Groovy loop, and it was not translated: 169 steps
 * in four suites emitted two variable bindings and a log line. The partition
 * and offset then came from the datasheet -- the values the script wrote the
 * last time somebody ran the case in ReadyAPI. Every run read the same old
 * offset on the same old partition, found somebody else's event there (or
 * none, or a 404 for {@code /partitions/null}), and failed the assertions on
 * the event's operation and ids.</p>
 */
public final class KafkaPartitions {

    private static final Logger LOG = LoggerFactory.getLogger(KafkaPartitions.class);

    private KafkaPartitions() {
    }

    /** What changed between two partition listings. */
    public static final class Diff {
        /** Partitions whose end offset differs. */
        public final int count;
        /** The LAST differing partition, as the script's loop leaves it; null when none. */
        public final String partitionId;
        /** That partition's end offset BEFORE the call: where the new event starts. */
        public final String endOffset;
        /** Why the listings could not be compared, or null. */
        public final String problem;

        Diff(int count, String partitionId, String endOffset, String problem) {
            this.count = count;
            this.partitionId = partitionId;
            this.endOffset = endOffset;
            this.problem = problem;
        }
    }

    /** Compare two {@code GET /topics/{topic}/partitions} bodies, index by index. */
    public static Diff diff(String beforeJson, String afterJson) {
        List<Map<String, Object>> before = rows(beforeJson);
        List<Map<String, Object>> after = rows(afterJson);
        if (before == null || after == null) {
            return new Diff(0, null, null, "a partition listing is missing or is not a JSON array ("
                    + (before == null ? "before" : "after") + ")");
        }
        if (before.size() != after.size()) {
            return new Diff(0, null, null, "the listings have different sizes: "
                    + before.size() + " before, " + after.size() + " after");
        }
        int count = 0;
        String partitionId = null;
        String endOffset = null;
        for (int i = 0; i < before.size(); i++) {
            String idBefore = text(before.get(i), "partitionId");
            if (!idBefore.equals(text(after.get(i), "partitionId"))) {
                return new Diff(0, null, null, "partition order differs at index " + i);
            }
            String offBefore = text(before.get(i), "endOffset");
            if (!offBefore.equals(text(after.get(i), "endOffset"))) {
                count++;
                partitionId = idBefore;
                endOffset = offBefore;
            }
        }
        return new Diff(count, partitionId, endOffset, null);
    }

    /**
     * The translated ComparePartitions step: publish
     * {@code <target>.partitionId} and {@code <target>.endOffset}.
     *
     * @param target the Properties step the script writes ({@code difference})
     * @param offsetAdd what the script adds to the offset before storing it
     *     ({@code endOffset = endOffset + 1} in some cases; otherwise 0)
     */
    public static void publishDifference(Map<String, String> ctx, SoftAssert softAssert,
                                         String target, String beforeJson, String afterJson,
                                         long offsetAdd) {
        Diff d = diff(beforeJson, afterJson);
        if (d.problem != null || d.count == 0) {
            // The Groovy script fails here too (an assert, or `null.toString()`).
            // Nothing is published: the saved datasheet value would send the
            // next step to an offset from somebody's earlier run.
            String why = d.problem != null ? d.problem
                    : "no partition's end offset changed -- the event was not published, "
                            + "or the second listing was taken before it landed";
            LOG.warn(" .. [kafka] {}: {}", target, why);
            // put(), not putExtracted(): that one keeps the old value when
            // handed an empty one, and the old value is the stale one.
            if (ctx != null) {
                ctx.put(target + ".partitionId", "");
                ctx.put(target + ".endOffset", "");
            }
            if (softAssert != null) {
                softAssert.fail("ComparePartitions (" + target + "): " + why);
            }
            return;
        }
        String offset = d.endOffset;
        if (offsetAdd != 0) {
            try {
                offset = String.valueOf(Long.parseLong(d.endOffset.trim()) + offsetAdd);
            } catch (NumberFormatException e) {
                LOG.warn(" .. [kafka] {}: end offset `{}` is not a number; stored as read",
                        target, d.endOffset);
            }
        }
        LOG.info(" .. [kafka] {}: {} partition(s) moved -- partitionId={} endOffset={}",
                target, d.count, d.partitionId, offset);
        ImportedScenario.putExtracted(ctx, target + ".partitionId", d.partitionId);
        ImportedScenario.putExtracted(ctx, target + ".endOffset", offset);
    }

    /**
     * The "expect no event" variant: the script asserts that nothing moved
     * and stores the count as {@code partitionId}.
     */
    public static void expectNoDifference(Map<String, String> ctx, SoftAssert softAssert,
                                          String target, String beforeJson, String afterJson) {
        Diff d = diff(beforeJson, afterJson);
        if (d.problem != null) {
            LOG.warn(" .. [kafka] {}: {}", target, d.problem);
            if (softAssert != null) {
                softAssert.fail("ComparePartitions (" + target + "): " + d.problem);
            }
            return;
        }
        LOG.info(" .. [kafka] {}: {} partition(s) moved (none expected)", target, d.count);
        if (softAssert != null) {
            softAssert.assertEquals(d.count, 0,
                    "ComparePartitions (" + target + "): partitions that moved");
        }
        ImportedScenario.putExtracted(ctx, target + ".partitionId", String.valueOf(d.count));
    }

    private static List<Map<String, Object>> rows(String json) {
        if (json == null || json.trim().isEmpty() || !json.trim().startsWith("[")) {
            return null;
        }
        try {
            return JsonPath.from(json).getList("$");
        } catch (RuntimeException e) {
            return null;
        }
    }

    private static String text(Map<String, Object> row, String key) {
        Object v = row == null ? null : row.get(key);
        return v == null ? "" : String.valueOf(v);
    }
}
