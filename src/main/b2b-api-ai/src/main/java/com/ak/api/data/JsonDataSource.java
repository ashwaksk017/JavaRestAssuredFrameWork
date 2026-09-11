// =============================================================================
// JsonDataSource
// -----------------------------------------------------------------------------
// Sibling of CsvDataSource -- reads a JSON test-data file into rows.
//
// Supported layouts:
//   1. Top-level JSON array of objects:
//        [ {"title":"a","userId":10}, {"title":"b","userId":20} ]
//   2. Top-level object with a named array:
//        { "rows": [ {...}, {...} ] }
//      Reader treats "rows" as the default key; override with rows(path, arrayKey).
//
// Rows come back as ordered Map<String,String> so downstream code (template
// substitution via RestUtilities.mapJsonValues) can plug in unchanged.
// Typed POJO rows also available: rows(path, Case.class).
// =============================================================================

package com.ak.api.data;

import java.io.IOException;
import java.io.InputStream;
import java.io.UncheckedIOException;
import java.util.ArrayList;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

public final class JsonDataSource {

    private static final ObjectMapper JSON = new ObjectMapper();

    private JsonDataSource() { }

    /** Rows from a top-level array, or from a "rows" key if the top level is an object. */
    public static List<Map<String, String>> rows(String classpathResource) {
        return rows(classpathResource, "rows", 1, Integer.MAX_VALUE);
    }

    /** Rows from a specific named array under a top-level object. */
    public static List<Map<String, String>> rows(String classpathResource, String arrayKey) {
        return rows(classpathResource, arrayKey, 1, Integer.MAX_VALUE);
    }

    public static List<Map<String, String>> rows(String classpathResource, String arrayKey,
                                                 int startRow, int endRow) {
        validateRange(startRow, endRow);
        JsonNode array = arrayNode(classpathResource, arrayKey);
        List<Map<String, String>> out = new ArrayList<>();
        int counter = 1;
        for (Iterator<JsonNode> it = array.elements(); it.hasNext(); ) {
            JsonNode elem = it.next();
            if (counter >= startRow && counter <= endRow) {
                out.add(nodeToStringMap(elem));
            }
            if (counter >= endRow) break;
            counter++;
        }
        return out;
    }

    /** Rows as typed POJOs. */
    public static <T> List<T> rows(String classpathResource, Class<T> type) {
        return rows(classpathResource, "rows", type, 1, Integer.MAX_VALUE);
    }

    public static <T> List<T> rows(String classpathResource, String arrayKey, Class<T> type,
                                   int startRow, int endRow) {
        validateRange(startRow, endRow);
        try (InputStream in = open(classpathResource)) {
            JsonNode root = JSON.readTree(in);
            JsonNode array = root.isArray() ? root : root.path(arrayKey);
            requireArray(array, classpathResource, arrayKey);
            List<T> all = JSON.convertValue(array, new TypeReference<>() { });
            if (startRow == 1 && endRow >= all.size()) return all;
            int from = Math.max(0, startRow - 1);
            int to = Math.min(all.size(), endRow);
            return new ArrayList<>(all.subList(from, to));
        } catch (IOException e) {
            throw new UncheckedIOException("Failed to read JSON: " + classpathResource, e);
        }
    }

    /** TestNG-shaped: Object[][] with one Map<String,String> per row. */
    public static Object[][] asTestNg(String classpathResource) {
        return asTestNg(classpathResource, "rows", 1, Integer.MAX_VALUE);
    }

    public static Object[][] asTestNg(String classpathResource, String arrayKey,
                                      int startRow, int endRow) {
        List<Map<String, String>> rows = rows(classpathResource, arrayKey, startRow, endRow);
        Object[][] out = new Object[rows.size()][1];
        for (int i = 0; i < rows.size(); i++) {
            out[i][0] = rows.get(i);
        }
        return out;
    }

    // ---- internals ----

    private static JsonNode arrayNode(String classpathResource, String arrayKey) {
        try (InputStream in = open(classpathResource)) {
            JsonNode root = JSON.readTree(in);
            JsonNode array = root.isArray() ? root : root.path(arrayKey);
            requireArray(array, classpathResource, arrayKey);
            return array;
        } catch (IOException e) {
            throw new UncheckedIOException("Failed to read JSON: " + classpathResource, e);
        }
    }

    private static void requireArray(JsonNode array, String classpathResource, String arrayKey) {
        if (array == null || array.isMissingNode() || !array.isArray()) {
            throw new IllegalArgumentException(
                    "JSON test-data must be a top-level array or an object with a '" + arrayKey
                            + "' array. File: " + classpathResource);
        }
    }

    /**
     * Flat map projection -- treats every leaf value as a string so
     * mapJsonValues() placeholder substitution works verbatim. Nested
     * objects/arrays get stringified via toString().
     */
    private static Map<String, String> nodeToStringMap(JsonNode elem) {
        Map<String, String> row = new LinkedHashMap<>();
        elem.fields().forEachRemaining(f -> {
            JsonNode v = f.getValue();
            row.put(f.getKey(), v.isValueNode() ? v.asText() : v.toString());
        });
        return row;
    }

    private static InputStream open(String classpathResource) {
        InputStream in = JsonDataSource.class.getClassLoader().getResourceAsStream(classpathResource);
        if (in == null) {
            throw new IllegalArgumentException(
                    "JSON test-data not found on classpath: " + classpathResource
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
