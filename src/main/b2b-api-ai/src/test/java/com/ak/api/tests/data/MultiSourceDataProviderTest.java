package com.ak.api.tests.data;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.lang.reflect.Method;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;

import org.apache.poi.ss.usermodel.Row;
import org.apache.poi.ss.usermodel.Sheet;
import org.apache.poi.xssf.usermodel.XSSFWorkbook;
import org.testng.Assert;
import org.testng.annotations.AfterMethod;
import org.testng.annotations.Test;

import com.ak.api.data.DataFiles;
import com.ak.api.data.ExcelDataSource;
import com.ak.api.data.PerMethodCsvDataProvider;
import com.ak.api.tests.datasource.ProbeCsv;
import com.ak.api.tests.datasource.ProbeExcel;
import com.ak.api.tests.datasource.ProbeJson;
import com.ak.api.tests.datasource.ProbePref;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;

@Epic("API Automation")
@Feature("Data sources")
public class MultiSourceDataProviderTest {

    @AfterMethod(alwaysRun = true)
    public void clearFormatOverride() {
        System.clearProperty("dataFormat");
        System.clearProperty("dataSheet");
        System.clearProperty("dataArrayKey");
    }

    @Test(groups = {"unit"})
    @Story("Excel rows as Map")
    @Description("Header row + DataFormatter so numeric cells stay '11' not '11.0'; blank rows skipped.")
    public void excelDataSource_readsHeaderAndStringCells() throws Exception {
        byte[] xlsx = workbook(
                new String[] { "title", "userId", "expected" },
                new String[] { "Excel row 1", "11", "statusCode:201" },
                new String[] { "", "", "" },
                new String[] { "Excel row 2", "22", "statusCode:201" });
        List<Map<String, String>> rows = ExcelDataSource.rows(
                new ByteArrayInputStream(xlsx), null, 1, Integer.MAX_VALUE);
        Assert.assertEquals(rows.size(), 2);
        Assert.assertEquals(rows.get(0).get("title"), "Excel row 1");
        Assert.assertEquals(rows.get(0).get("userId"), "11");
        Assert.assertEquals(rows.get(1).get("title"), "Excel row 2");
    }

    @Test(groups = {"unit"})
    @Story("Excel named sheet and row range")
    @Description("dataSheet picks a non-default sheet; startRow/endRow are 1-based data rows.")
    public void excelDataSource_namedSheetAndRange() throws Exception {
        byte[] xlsx;
        try (XSSFWorkbook wb = new XSSFWorkbook()) {
            Sheet other = wb.createSheet("cases");
            header(other, "title", "userId");
            data(other, 1, "a", "1");
            data(other, 2, "b", "2");
            data(other, 3, "c", "3");
            wb.createSheet("ignored").createRow(0).createCell(0).setCellValue("nope");
            xlsx = toBytes(wb);
        }
        List<Map<String, String>> rows = ExcelDataSource.rows(
                new ByteArrayInputStream(xlsx), "cases", 2, 3);
        Assert.assertEquals(rows.size(), 2);
        Assert.assertEquals(rows.get(0).get("title"), "b");
        Assert.assertEquals(rows.get(1).get("title"), "c");
    }

    @Test(groups = {"unit"})
    @Story("fileData extension dispatch")
    @Description("DataFiles.formatOf maps csv/xlsx/json; unknown extension fails fast.")
    public void dataFiles_formatOf() {
        Assert.assertEquals(DataFiles.formatOf("testdata/posts.csv"), DataFiles.Format.CSV);
        Assert.assertEquals(DataFiles.formatOf("C:\\data\\rows.XLSX"), DataFiles.Format.EXCEL);
        Assert.assertEquals(DataFiles.formatOf("rows.json"), DataFiles.Format.JSON);
        try {
            DataFiles.formatOf("rows.txt");
            Assert.fail("expected unsupported extension");
        } catch (IllegalArgumentException expected) {
            Assert.assertTrue(expected.getMessage().contains("Unsupported"));
        }
    }

    @Test(groups = {"unit"})
    @Story("Per-method CSV still loads")
    @Description("Imported-style PerMethodCsvDataProvider.rows keeps reading .csv first.")
    public void perMethod_csv() throws Exception {
        Method m = ProbeCsv.class.getMethod("fromCsv", Map.class);
        Assert.assertEquals(PerMethodCsvDataProvider.resolveResourcePath(m),
                "csv/ProbeCsv/fromCsv.csv");
        Object[][] data = PerMethodCsvDataProvider.rows(m);
        Assert.assertEquals(data.length, 1);
        @SuppressWarnings("unchecked")
        Map<String, String> row = (Map<String, String>) data[0][0];
        Assert.assertEquals(row.get("title"), "CSV from per-method");
    }

    @Test(groups = {"unit"})
    @Story("Per-method JSON drop-in")
    @Description("A .json next to the method name is used when no CSV exists.")
    public void perMethod_json() throws Exception {
        Method m = ProbeJson.class.getMethod("fromJson", Map.class);
        Assert.assertEquals(PerMethodCsvDataProvider.resolveResourcePath(m),
                "csv/ProbeJson/fromJson.json");
        Object[][] data = PerMethodCsvDataProvider.rows(m);
        @SuppressWarnings("unchecked")
        Map<String, String> row = (Map<String, String>) data[0][0];
        Assert.assertEquals(row.get("title"), "JSON from per-method");
        Assert.assertEquals(row.get("userId"), "22");
    }

    @Test(groups = {"unit"})
    @Story("Per-method Excel drop-in")
    @Description("A .xlsx next to the method name is used when no CSV exists.")
    public void perMethod_excel() throws Exception {
        Path dir = Path.of("target", "test-classes", "csv", "ProbeExcel");
        Files.createDirectories(dir);
        byte[] xlsx = workbook(
                new String[] { "title", "userId" },
                new String[] { "Excel from per-method", "33" });
        Files.write(dir.resolve("fromExcel.xlsx"), xlsx);

        Method m = ProbeExcel.class.getMethod("fromExcel", Map.class);
        Assert.assertEquals(PerMethodCsvDataProvider.resolveResourcePath(m),
                "csv/ProbeExcel/fromExcel.xlsx");
        Object[][] data = PerMethodCsvDataProvider.rows(m);
        @SuppressWarnings("unchecked")
        Map<String, String> row = (Map<String, String>) data[0][0];
        Assert.assertEquals(row.get("title"), "Excel from per-method");
        Assert.assertEquals(row.get("userId"), "33");
    }

    @Test(groups = {"unit"})
    @Story("CSV wins when both CSV and JSON exist")
    @Description("Default search order is csv, xlsx, xls, json so imported CSVs are not shadowed.")
    public void perMethod_csvPreferredOverJson() throws Exception {
        Method m = ProbePref.class.getMethod("fromPref", Map.class);
        Assert.assertEquals(PerMethodCsvDataProvider.resolveResourcePath(m),
                "csv/ProbePref/fromPref.csv");
        Object[][] data = PerMethodCsvDataProvider.rows(m);
        @SuppressWarnings("unchecked")
        Map<String, String> row = (Map<String, String>) data[0][0];
        Assert.assertEquals(row.get("title"), "csv-wins");
    }

    @Test(groups = {"unit"})
    @Story("dataFormat forces JSON over CSV")
    @Description("-DdataFormat=json uses the JSON sibling even when a CSV exists.")
    public void perMethod_dataFormatOverride() throws Exception {
        System.setProperty("dataFormat", "json");
        Method m = ProbePref.class.getMethod("fromPref", Map.class);
        Assert.assertEquals(PerMethodCsvDataProvider.resolveResourcePath(m),
                "csv/ProbePref/fromPref.json");
        Object[][] data = PerMethodCsvDataProvider.rows(m);
        @SuppressWarnings("unchecked")
        Map<String, String> row = (Map<String, String>) data[0][0];
        Assert.assertEquals(row.get("title"), "json-should-not-win");
    }

    @Test(groups = {"unit"})
    @Story("Excel classpath or filesystem")
    @Description("ExcelDataSource opens a filesystem .xlsx when the path is not on the classpath.")
    public void excelDataSource_filesystemPath() throws Exception {
        Path file = Files.createTempFile("posts", ".xlsx");
        Files.write(file, workbook(
                new String[] { "title", "userId" },
                new String[] { "from-disk", "44" }));
        List<Map<String, String>> rows = ExcelDataSource.rows(file.toString());
        Assert.assertEquals(rows.get(0).get("title"), "from-disk");
        Assert.assertEquals(rows.get(0).get("userId"), "44");
        Files.deleteIfExists(file);
    }

    private static byte[] workbook(String[] header, String[]... dataRows) throws Exception {
        try (XSSFWorkbook wb = new XSSFWorkbook()) {
            Sheet sheet = wb.createSheet("Sheet1");
            header(sheet, header);
            for (int i = 0; i < dataRows.length; i++) {
                data(sheet, i + 1, dataRows[i]);
            }
            return toBytes(wb);
        }
    }

    private static void header(Sheet sheet, String... names) {
        Row row = sheet.createRow(0);
        for (int c = 0; c < names.length; c++) {
            row.createCell(c).setCellValue(names[c]);
        }
    }

    private static void data(Sheet sheet, int rowIndex, String... values) {
        Row row = sheet.createRow(rowIndex);
        for (int c = 0; c < values.length; c++) {
            String v = values[c];
            if (v != null && v.matches("\\d+")) {
                row.createCell(c).setCellValue(Long.parseLong(v));
            } else {
                row.createCell(c).setCellValue(v == null ? "" : v);
            }
        }
    }

    private static byte[] toBytes(XSSFWorkbook wb) throws Exception {
        try (ByteArrayOutputStream out = new ByteArrayOutputStream()) {
            wb.write(out);
            return out.toByteArray();
        }
    }
}
