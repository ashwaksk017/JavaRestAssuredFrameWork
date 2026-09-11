package com.ak.api.data;

import java.io.IOException;
import java.io.InputStream;
import java.io.UncheckedIOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.apache.poi.ss.usermodel.Cell;
import org.apache.poi.ss.usermodel.DataFormatter;
import org.apache.poi.ss.usermodel.FormulaEvaluator;
import org.apache.poi.ss.usermodel.Row;
import org.apache.poi.ss.usermodel.Sheet;
import org.apache.poi.ss.usermodel.Workbook;
import org.apache.poi.ss.usermodel.WorkbookFactory;

/**
 * Excel sibling of {@link CsvDataSource}. First row is headers; later rows
 * become {@code Map&lt;header, stringValue&gt;} so {@code RestStep} / templates
 * consume Excel the same way they consume CSV.
 *
 * <p>Default sheet is the workbook's first sheet. Pass a sheet name to
 * target another. {@code startRow}/{@code endRow} are 1-based inclusive
 * data-row indexes (header excluded), matching CSV/JSON.</p>
 *
 * <p>{@code path} is tried as a classpath resource first, then as a
 * filesystem path so authors can point {@code -DdataFile=} at a local
 * {@code .xlsx} without copying it under {@code src/test/resources}.</p>
 */
public final class ExcelDataSource {

    private ExcelDataSource() {}

    public static List<Map<String, String>> rows(String path) {
        return rows(path, null, 1, Integer.MAX_VALUE);
    }

    public static List<Map<String, String>> rows(String path, int startRow, int endRow) {
        return rows(path, null, startRow, endRow);
    }

    public static List<Map<String, String>> rows(String path, String sheetName,
                                                 int startRow, int endRow) {
        validateRange(startRow, endRow);
        try (InputStream in = open(path)) {
            return rows(in, sheetName, startRow, endRow);
        } catch (IOException e) {
            throw new UncheckedIOException("Failed to read Excel: " + path, e);
        }
    }

    /**
     * Read rows from an already-open stream (unit tests, in-memory workbooks).
     * Does not close {@code in}.
     */
    public static List<Map<String, String>> rows(InputStream in, String sheetName,
                                                 int startRow, int endRow) {
        validateRange(startRow, endRow);
        if (in == null) {
            throw new IllegalArgumentException("Excel InputStream is null");
        }
        try (Workbook wb = WorkbookFactory.create(in)) {
            Sheet sheet = resolveSheet(wb, sheetName);
            DataFormatter formatter = new DataFormatter();
            FormulaEvaluator eval = wb.getCreationHelper().createFormulaEvaluator();
            Row headerRow = sheet.getRow(sheet.getFirstRowNum());
            if (headerRow == null) {
                throw new IllegalArgumentException(
                        "Excel sheet '" + sheet.getSheetName() + "' has no header row");
            }
            List<String> headers = readHeaders(headerRow, formatter, eval);
            List<Map<String, String>> out = new ArrayList<>();
            int dataIndex = 0;
            int last = sheet.getLastRowNum();
            for (int r = sheet.getFirstRowNum() + 1; r <= last; r++) {
                Row row = sheet.getRow(r);
                if (isEmptyRow(row, headers.size(), formatter, eval)) {
                    continue;
                }
                dataIndex++;
                if (dataIndex < startRow) {
                    continue;
                }
                if (dataIndex > endRow) {
                    break;
                }
                out.add(readDataRow(row, headers, formatter, eval));
            }
            return out;
        } catch (IOException e) {
            throw new UncheckedIOException("Failed to parse Excel workbook", e);
        }
    }

    public static Object[][] asTestNg(String path) {
        return asTestNg(path, null, 1, Integer.MAX_VALUE);
    }

    public static Object[][] asTestNg(String path, String sheetName,
                                      int startRow, int endRow) {
        return DataFiles.toTestNg(rows(path, sheetName, startRow, endRow));
    }

    public static Object[][] asTestNg(InputStream in, String sheetName,
                                      int startRow, int endRow) {
        return DataFiles.toTestNg(rows(in, sheetName, startRow, endRow));
    }

    static InputStream open(String path) throws IOException {
        if (path == null || path.isBlank()) {
            throw new IllegalArgumentException("Excel path is empty");
        }
        String resource = path.startsWith("/") ? path.substring(1) : path;
        InputStream in = ExcelDataSource.class.getClassLoader().getResourceAsStream(resource);
        if (in != null) {
            return in;
        }
        Path file = Path.of(path);
        if (Files.isRegularFile(file)) {
            return Files.newInputStream(file);
        }
        throw new IllegalArgumentException(
                "Excel file not found on classpath or filesystem: " + path);
    }

    private static Sheet resolveSheet(Workbook wb, String sheetName) {
        if (sheetName == null || sheetName.isBlank()) {
            if (wb.getNumberOfSheets() < 1) {
                throw new IllegalArgumentException("Excel workbook has no sheets");
            }
            return wb.getSheetAt(0);
        }
        Sheet sheet = wb.getSheet(sheetName);
        if (sheet == null) {
            throw new IllegalArgumentException(
                    "Excel sheet not found: '" + sheetName + "'");
        }
        return sheet;
    }

    private static List<String> readHeaders(Row headerRow, DataFormatter formatter,
                                            FormulaEvaluator eval) {
        List<String> headers = new ArrayList<>();
        short last = headerRow.getLastCellNum();
        if (last < 0) {
            throw new IllegalArgumentException("Excel header row is empty");
        }
        for (int c = 0; c < last; c++) {
            String name = cellText(headerRow.getCell(c), formatter, eval);
            headers.add(name == null ? "" : name);
        }
        boolean any = false;
        for (String h : headers) {
            if (h != null && !h.isBlank()) {
                any = true;
                break;
            }
        }
        if (!any) {
            throw new IllegalArgumentException("Excel header row has no column names");
        }
        return headers;
    }

    private static Map<String, String> readDataRow(Row row, List<String> headers,
                                                   DataFormatter formatter,
                                                   FormulaEvaluator eval) {
        Map<String, String> map = new LinkedHashMap<>();
        for (int c = 0; c < headers.size(); c++) {
            String header = headers.get(c);
            if (header == null || header.isBlank()) {
                continue;
            }
            Cell cell = row == null ? null : row.getCell(c);
            String value = cellText(cell, formatter, eval);
            map.put(header, value == null ? "" : value);
        }
        return map;
    }

    private static boolean isEmptyRow(Row row, int columnCount, DataFormatter formatter,
                                      FormulaEvaluator eval) {
        if (row == null) {
            return true;
        }
        for (int c = 0; c < columnCount; c++) {
            String v = cellText(row.getCell(c), formatter, eval);
            if (v != null && !v.isBlank()) {
                return false;
            }
        }
        return true;
    }

    private static String cellText(Cell cell, DataFormatter formatter, FormulaEvaluator eval) {
        if (cell == null) {
            return "";
        }
        return formatter.formatCellValue(cell, eval);
    }

    private static void validateRange(int startRow, int endRow) {
        if (startRow < 1) {
            throw new IllegalArgumentException("startRow must be >= 1, got " + startRow);
        }
        if (endRow < startRow) {
            throw new IllegalArgumentException(
                    "endRow (" + endRow + ") must be >= startRow (" + startRow + ")");
        }
    }
}
