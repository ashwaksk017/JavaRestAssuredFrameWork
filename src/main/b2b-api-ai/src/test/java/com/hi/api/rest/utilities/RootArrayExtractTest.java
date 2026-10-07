package com.hi.api.rest.utilities;

import org.testng.Assert;
import org.testng.annotations.Test;

import io.qameta.allure.Feature;
import io.qameta.allure.Story;
import io.restassured.path.json.JsonPath;

/**
 * A cross-step ref into a ROOT ARRAY must resolve.
 *
 * <p>ReadyAPI writes {@code ${GET_ratePlan#Response#$[0]['propCode']}}.
 * The converter folds the index into a dotted segment, so the PhaseSpec
 * records {@code .extract("GET_ratePlan_Response_0_propCode", "0.propCode")}.
 * GPath cannot apply a bare {@code 0} to a list: it threw, the catch
 * swallowed it, and the extract returned "" with nothing in the log.
 * Run 7: every {@code #GET_ratePlan_Response_0_*#} fell back to null,
 * the PUT path ended in an empty id, the framework refused to send it,
 * and the JDBC step was skipped over the literal it inherited -- three
 * failures whose log lines all pointed downstream of the cause.</p>
 */
@Feature("Framework")
@Story("Root-array response extraction")
public class RootArrayExtractTest {

    private static final String ROOT_ARRAY =
            "[{\"propCode\":\"BWITI\",\"srpUniqueId\":497,"
            + "\"nested\":{\"code\":\"X1\"}},"
            + "{\"propCode\":\"LONME\",\"srpUniqueId\":498}]";

    private static final String ROOT_OBJECT =
            "{\"propCode\":\"BWITI\",\"items\":[{\"name\":\"a\"},{\"name\":\"b\"}]}";

    @Test
    public void aLeadingIndexSegmentResolvesAgainstARootArray() {
        JsonPath jp = JsonPath.from(ROOT_ARRAY);
        Assert.assertEquals(RestUtilities.extractFromJsonPath(jp, "0.propCode"), "BWITI");
        Assert.assertEquals(RestUtilities.extractFromJsonPath(jp, "1.propCode"), "LONME");
        Assert.assertEquals(RestUtilities.extractFromJsonPath(jp, "0.srpUniqueId"), "497");
        Assert.assertEquals(RestUtilities.extractFromJsonPath(jp, "0.nested.code"), "X1");
    }

    @Test
    public void anIndexInTheMiddleOfAPathResolvesToo() {
        JsonPath jp = JsonPath.from(ROOT_OBJECT);
        Assert.assertEquals(RestUtilities.extractFromJsonPath(jp, "items.1.name"), "b");
    }

    @Test
    public void aPlainPathIsUntouched() {
        JsonPath jp = JsonPath.from(ROOT_OBJECT);
        Assert.assertEquals(RestUtilities.extractFromJsonPath(jp, "propCode"), "BWITI");
        Assert.assertEquals(RestUtilities.bracketNumericSegments("propCode"), "propCode");
        Assert.assertEquals(RestUtilities.bracketNumericSegments("a.b.c"), "a.b.c");
    }

    @Test
    public void theRespellingIsExact() {
        Assert.assertEquals(RestUtilities.bracketNumericSegments("0.propCode"), "[0].propCode");
        Assert.assertEquals(RestUtilities.bracketNumericSegments("items.1.name"), "items[1].name");
        Assert.assertEquals(RestUtilities.bracketNumericSegments("0"), "[0]");
        Assert.assertEquals(RestUtilities.bracketNumericSegments("0.1.x"), "[0][1].x");
        Assert.assertEquals(RestUtilities.bracketNumericSegments(""), "");
        Assert.assertNull(RestUtilities.bracketNumericSegments(null));
    }

    @Test
    public void aMissingIndexStillReportsAMissNotAnException() {
        JsonPath jp = JsonPath.from(ROOT_ARRAY);
        Assert.assertEquals(RestUtilities.extractFromJsonPath(jp, "7.propCode"), "");
    }
}
