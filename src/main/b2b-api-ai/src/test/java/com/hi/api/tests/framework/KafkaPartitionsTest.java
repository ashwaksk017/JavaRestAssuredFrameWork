package com.hi.api.tests.framework;

import java.util.HashMap;
import java.util.Map;

import org.testng.Assert;
import org.testng.annotations.Test;
import org.testng.asserts.SoftAssert;

import com.hi.api.rest.utilities.KafkaPartitions;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;

/**
 * The partition whose end offset moved is found from the two listings of
 * THIS run, not read from the datasheet.
 */
@Epic("Framework")
@Feature("Kafka partition comparison")
public class KafkaPartitionsTest {

    private static String listing(long... endOffsets) {
        StringBuilder sb = new StringBuilder("[");
        for (int i = 0; i < endOffsets.length; i++) {
            if (i > 0) sb.append(',');
            sb.append("{\"partitionId\":").append(i)
              .append(",\"beginningOffset\":0,\"endOffset\":").append(endOffsets[i]).append('}');
        }
        return sb.append(']').toString();
    }

    @Test(groups = {"unit"})
    @Story("The moved partition is published")
    @Description("Partition 2 moved from 884 to 885: partitionId=2 and the offset BEFORE the call, where the new event starts.")
    public void publishesTheMovedPartitionAndItsOffsetBeforeTheCall() {
        Map<String, String> ctx = new HashMap<>();
        ctx.put("difference.partitionId", "0");      // what the datasheet had saved
        ctx.put("difference.endOffset", "17");
        SoftAssert sa = new SoftAssert();
        KafkaPartitions.publishDifference(ctx, sa, "difference",
                listing(100, 250, 884), listing(100, 250, 885), 0L);
        sa.assertAll();
        Assert.assertEquals(ctx.get("difference.partitionId"), "2");
        Assert.assertEquals(ctx.get("difference.endOffset"), "884");
    }

    @Test(groups = {"unit"})
    @Story("The moved partition is published")
    @Description("`endOffset = endOffset + 1` in the script is applied; the target step name is the script's.")
    public void appliesTheScriptsIncrementAndTarget() {
        Map<String, String> ctx = new HashMap<>();
        KafkaPartitions.publishDifference(ctx, new SoftAssert(), "difference_reject",
                listing(100, 250), listing(100, 252), 1L);
        Assert.assertEquals(ctx.get("difference_reject.partitionId"), "1");
        Assert.assertEquals(ctx.get("difference_reject.endOffset"), "251");
    }

    @Test(groups = {"unit"})
    @Story("The moved partition is published")
    @Description("Two partitions moved: the script's loop keeps the LAST one, and so does this.")
    public void keepsTheLastMovedPartitionLikeTheLoop() {
        KafkaPartitions.Diff d = KafkaPartitions.diff(listing(1, 2, 3), listing(9, 2, 9));
        Assert.assertEquals(d.count, 2);
        Assert.assertEquals(d.partitionId, "2");
        Assert.assertEquals(d.endOffset, "3");
    }

    @Test(groups = {"unit"})
    @Story("Nothing moved")
    @Description("No partition moved: the saved value is cleared rather than reused, and the test is failed with the reason -- the ReadyAPI script fails here too.")
    public void nothingMoved_failsAndDoesNotReuseTheSavedValue() {
        Map<String, String> ctx = new HashMap<>();
        ctx.put("difference.partitionId", "0");
        ctx.put("difference.endOffset", "17");
        SoftAssert sa = new SoftAssert();
        KafkaPartitions.publishDifference(ctx, sa, "difference",
                listing(100, 250), listing(100, 250), 0L);
        Assert.assertEquals(ctx.get("difference.partitionId"), "");
        Assert.assertEquals(ctx.get("difference.endOffset"), "");
        AssertionError e = Assert.expectThrows(AssertionError.class, sa::assertAll);
        Assert.assertTrue(e.getMessage().contains("no partition's end offset changed"), e.getMessage());
    }

    @Test(groups = {"unit"})
    @Story("Nothing moved")
    @Description("A missing, non-array or re-ordered listing is reported as what it is, never compared.")
    public void listingsThatCannotBeCompared() {
        Assert.assertNotNull(KafkaPartitions.diff(null, listing(1)).problem);
        Assert.assertNotNull(KafkaPartitions.diff("", listing(1)).problem);
        Assert.assertNotNull(KafkaPartitions.diff("{\"message\":\"Not Found\"}", listing(1)).problem);
        Assert.assertNotNull(KafkaPartitions.diff(listing(1, 2), listing(1)).problem);
        String swapped = "[{\"partitionId\":1,\"endOffset\":2},{\"partitionId\":0,\"endOffset\":1}]";
        Assert.assertNotNull(KafkaPartitions.diff(listing(1, 2), swapped).problem);
        Assert.assertNull(KafkaPartitions.diff(listing(1, 2), listing(1, 2)).problem);
    }

    @Test(groups = {"unit"})
    @Story("Expect no event")
    @Description("The variant that asserts nothing moved: passes on identical listings, fails when one moved, and stores the count.")
    public void expectNoDifference() {
        Map<String, String> ctx = new HashMap<>();
        SoftAssert quiet = new SoftAssert();
        KafkaPartitions.expectNoDifference(ctx, quiet, "difference", listing(5, 6), listing(5, 6));
        quiet.assertAll();
        Assert.assertEquals(ctx.get("difference.partitionId"), "0");

        SoftAssert moved = new SoftAssert();
        KafkaPartitions.expectNoDifference(ctx, moved, "difference", listing(5, 6), listing(5, 7));
        Assert.expectThrows(AssertionError.class, moved::assertAll);
        Assert.assertEquals(ctx.get("difference.partitionId"), "1");
    }
}
