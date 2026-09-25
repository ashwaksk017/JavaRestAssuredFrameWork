package com.hi.api.data;

import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * Picks CSV / Excel / JSON from a file path so authors can data-drive
 * without changing the {@code @Test} method — only the file (or
 * {@code -DdataFile=}) changes.
 */
public final class DataFiles {

    public enum Format {
        CSV, EXCEL, JSON
    }

    private DataFiles() {}

    public static Format formatOf(String path) {
        if (path == null || path.isBlank()) {
            throw new IllegalArgumentException("data file path is empty");
        }
        String name = path;
        int slash = Math.max(path.lastIndexOf('/'), path.lastIndexOf('\\'));
        if (slash >= 0 && slash < path.length() - 1) {
            name = path.substring(slash + 1);
        }
        int dot = name.lastIndexOf('.');
        String ext = dot < 0 ? "" : name.substring(dot + 1).toLowerCase(Locale.ROOT);
        return switch (ext) {
            case "csv" -> Format.CSV;
            case "xlsx", "xls" -> Format.EXCEL;
            case "json" -> Format.JSON;
            default -> throw new IllegalArgumentException(
                    "Unsupported data file extension '." + ext + "' on " + path
                            + " — use .csv, .xlsx, .xls, or .json");
        };
    }

    public static Format formatFromName(String format) {
        if (format == null || format.isBlank()) {
            return null;
        }
        return switch (format.trim().toLowerCase(Locale.ROOT)) {
            case "csv" -> Format.CSV;
            case "excel", "xlsx", "xls" -> Format.EXCEL;
            case "json" -> Format.JSON;
            default -> throw new IllegalArgumentException(
                    "dataFormat must be csv, xlsx, xls, excel, or json; got: " + format);
        };
    }

    /**
     * Load rows. {@code sheetOrArrayKey} is the Excel sheet name, or the
     * JSON array key (default {@code rows}); ignored for CSV.
     */
    public static List<Map<String, String>> rows(String path, String sheetOrArrayKey,
                                                 int startRow, int endRow) {
        return switch (formatOf(path)) {
            case CSV -> CsvDataSource.rows(path, startRow, endRow);
            case EXCEL -> ExcelDataSource.rows(path, sheetOrArrayKey, startRow, endRow);
            case JSON -> JsonDataSource.rows(path,
                    (sheetOrArrayKey == null || sheetOrArrayKey.isBlank())
                            ? "rows" : sheetOrArrayKey,
                    startRow, endRow);
        };
    }

    public static Object[][] asTestNg(String path, String sheetOrArrayKey,
                                      int startRow, int endRow) {
        return toTestNg(rows(path, sheetOrArrayKey, startRow, endRow));
    }

    static Object[][] toTestNg(List<Map<String, String>> rows) {
        Object[][] out = new Object[rows.size()][1];
        for (int i = 0; i < rows.size(); i++) {
            out[i][0] = rows.get(i);
        }
        return out;
    }
}
