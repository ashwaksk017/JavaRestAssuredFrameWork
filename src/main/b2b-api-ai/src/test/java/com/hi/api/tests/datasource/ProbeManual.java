package com.hi.api.tests.datasource;

import java.util.Map;

/**
 * Probe class for {@link com.hi.api.data.PerMethodCsvDataProvider}: a hand-written
 * (not converted) test reads its row from csv/manual/&lt;Class&gt;/&lt;method&gt;.csv,
 * the one tracked data location. Stands in for a story test, whose sheet holds
 * real keys and does not belong in this repository.
 */
public final class ProbeManual {
    public void fromManual(Map<String, String> row) { }
}
