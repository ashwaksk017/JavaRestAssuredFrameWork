// =============================================================================
// CsvDataSource
// -----------------------------------------------------------------------------
// Modernized replacement for the reference project's OpenCSV-based
// BaseClass.provideCSVData() (com.visa.b2b.connect.csv.reader.prop.manager).
//
// Uses jackson-dataformat-csv so:
//   * we reuse the ObjectMapper family already on the classpath
//   * typed POJO rows come for free -- rows(path, MyDto.class)
//   * headers-as-keys map rows preserve insertion order (LinkedHashMap)
//
// Row range semantics match the reference: startRow / endRow are 1-based
// and inclusive on both ends, counted from the FIRST data row (header
// excluded), so startRow=1,endRow=3 returns the first three data rows.
// =============================================================================

package com.ak.api.data;

import java.io.IOException;
import java.io.InputStream;
import java.io.UncheckedIOException;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import com.fasterxml.jackson.databind.MappingIterator;
import com.fasterxml.jackson.dataformat.csv.CsvMapper;
import com.fasterxml.jackson.dataformat.csv.CsvSchema;

public final class CsvDataSource {

    private static final CsvMapper CSV = new CsvMapper();

    private CsvDataSource() { }

    /** Every data row as an ordered Map<header,value>. */
    public static List<Map<String, String>> rows(String classpathResource) {
        return rows(classpathResource, 1, Integer.MAX_VALUE);
    }

    /**
     * Rows startRow..endRow (both 1-based, both inclusive, header excluded).
     * Matches the reference project's row-range semantics.
     */
    public static List<Map<String, String>> rows(String classpathResource, int startRow, int endRow) {
        validateRange(startRow, endRow);
        CsvSchema schema = CsvSchema.emptySchema().withHeader();
        List<Map<String, String>> out = new ArrayList<>();
        try (InputStream in = open(classpathResource)) {
            MappingIterator<Map<String, String>> it = CSV.readerFor(Map.class).with(schema).readValues(in);
            int counter = 1;
            while (it.hasNext()) {
                Map<String, String> row = it.next();
                if (counter >= startRow && counter <= endRow) {
                    out.add(new LinkedHashMap<>(row));
                }
                if (counter >= endRow) break;
                counter++;
            }
        } catch (IOException e) {
            throw new UncheckedIOException("Failed to read CSV: " + classpathResource, e);
        }
        return out;
    }

    /** Rows as typed POJOs -- Jackson field names must match CSV headers. */
    public static <T> List<T> rows(String classpathResource, Class<T> type) {
        return rows(classpathResource, type, 1, Integer.MAX_VALUE);
    }

    public static <T> List<T> rows(String classpathResource, Class<T> type, int startRow, int endRow) {
        validateRange(startRow, endRow);
        CsvSchema schema = CsvSchema.emptySchema().withHeader();
        List<T> out = new ArrayList<>();
        try (InputStream in = open(classpathResource)) {
            MappingIterator<T> it = CSV.readerFor(type).with(schema).readValues(in);
            int counter = 1;
            while (it.hasNext()) {
                T row = it.next();
                if (counter >= startRow && counter <= endRow) {
                    out.add(row);
                }
                if (counter >= endRow) break;
                counter++;
            }
        } catch (IOException e) {
            throw new UncheckedIOException("Failed to read CSV: " + classpathResource, e);
        }
        return out;
    }

    /** TestNG-shaped: Object[][] with one Map<String,String> per row. */
    public static Object[][] asTestNg(String classpathResource) {
        return asTestNg(classpathResource, 1, Integer.MAX_VALUE);
    }

    public static Object[][] asTestNg(String classpathResource, int startRow, int endRow) {
        List<Map<String, String>> rows = rows(classpathResource, startRow, endRow);
        Object[][] out = new Object[rows.size()][1];
        for (int i = 0; i < rows.size(); i++) {
            out[i][0] = rows.get(i);
        }
        return out;
    }

    // ---- internals ----

    private static InputStream open(String classpathResource) {
        InputStream in = CsvDataSource.class.getClassLoader().getResourceAsStream(classpathResource);
        if (in == null) {
            throw new IllegalArgumentException(
                    "CSV not found on classpath: " + classpathResource
                            + "  (looked under src/test/resources/" + classpathResource + ")");
        }
        return in;
    }

    private static void validateRange(int startRow, int endRow) {
        if (startRow < 1) throw new IllegalArgumentException("startRow must be >= 1, got " + startRow);
        if (endRow < startRow) throw new IllegalArgumentException(
                "endRow (" + endRow + ") must be >= startRow (" + startRow + ")");
    }
}
