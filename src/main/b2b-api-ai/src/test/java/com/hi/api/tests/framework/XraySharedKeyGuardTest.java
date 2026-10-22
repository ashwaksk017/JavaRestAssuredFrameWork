package com.hi.api.tests.framework;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;

import org.testng.Assert;
import org.testng.annotations.Test;

import com.hi.api.reporting.XrayReportListener;
import com.hi.api.xray.XrayResult;

/**
 * One Xray test is one test method: a key carried by two different methods
 * is reported for neither.
 *
 * <p>Seen live: story B2B-99454's TC-001 key B2B-99802 had been written into
 * the TC-001 rows of two other tests' sheets. The real test skipped, an
 * unrelated test passed, and Xray recorded B2B-99802 as passed.</p>
 */
public class XraySharedKeyGuardTest {

    private static XrayResult result(String key, XrayResult.Status status) {
        return new XrayResult(key, status, null, 10L);
    }

    @Test(groups = {"guards"})
    public void aKeyOfTwoMethodsIsNotReportedAndIsSaid() {
        List<String> said = new ArrayList<>();
        List<XrayResult> kept = XrayReportListener.withoutSharedKeys(
                List.of(result("B2B-99802", XrayResult.Status.PASSED),
                        result("B2B-99900", XrayResult.Status.FAILED)),
                Map.of("B2B-99802", Set.of("a.GuestProgramAccountTest#create",
                                           "a.B2b99454SpendDetailTest#filter"),
                       "B2B-99900", Set.of("a.Other#one")),
                said::add);
        Assert.assertEquals(kept.size(), 1);
        Assert.assertEquals(kept.get(0).testKey(), "B2B-99900");
        Assert.assertEquals(said.size(), 1);
        Assert.assertTrue(said.get(0).contains("B2B-99802")
                && said.get(0).contains("GuestProgramAccountTest#create")
                && said.get(0).contains("B2b99454SpendDetailTest#filter"), said.get(0));
    }

    @Test(groups = {"guards"})
    public void manyRowsOfOneMethodShareTheirKeyFreely() {
        // A data-driven method runs once a row: still one test.
        List<String> said = new ArrayList<>();
        List<XrayResult> in = List.of(result("B2B-1", XrayResult.Status.PASSED),
                                      result("B2B-1", XrayResult.Status.FAILED));
        Assert.assertEquals(XrayReportListener.withoutSharedKeys(
                in, Map.of("B2B-1", Set.of("a.T#m")), said::add), in);
        Assert.assertTrue(said.isEmpty());
    }

    @Test(groups = {"guards"})
    public void aKeyIsComparedWhateverItsCase() {
        List<String> said = new ArrayList<>();
        List<XrayResult> kept = XrayReportListener.withoutSharedKeys(
                List.of(result("b2b-7", XrayResult.Status.PASSED)),
                Map.of("B2B-7", Set.of("a.T#m"), "b2b-7 ", Set.of("a.U#n")),
                said::add);
        Assert.assertTrue(kept.isEmpty());
        Assert.assertEquals(said.size(), 1);
    }
}
