// =============================================================================
// Db -- plain-JDBC query utility
// -----------------------------------------------------------------------------
// Zero abstraction, zero connection pool. Every call opens a fresh connection
// via DriverManager and closes it on the way out. Perfectly fast for test-scale
// workloads (dozens of queries per suite), and stays out of the way of
// long-lived pool tuning that real apps do.
//
// Two entry points:
//
//   * Static shortcuts -- read connection info from Config (db.url / db.user /
//     db.password / db.driver). Use these when the whole suite talks to one DB.
//
//         List<Map<String,Object>> rows = Db.queryAll("SELECT * FROM posts WHERE user_id = ?", 1);
//         Map<String,Object> row       = Db.queryOne("SELECT * FROM users WHERE id = ?", 42);
//         int updated                  = Db.execute("UPDATE ...", ...);
//         boolean present              = Db.exists("SELECT 1 FROM ... WHERE ...", ...);
//
//   * Db.using(url, user, pass) -- explicit connection override. Handy for
//     the H2 self-contained demo test and for tests that need to hit a
//     secondary DB (a read replica, a legacy warehouse).
//
//         Db h2 = Db.using("jdbc:h2:mem:test;MODE=PostgreSQL;DB_CLOSE_DELAY=-1", "sa", "");
//         h2.executeStatement("CREATE TABLE ...");
//         Map<String,Object> row = h2.queryOneRow("SELECT ...");
//
// Every row is returned as a LinkedHashMap<String,Object> keyed by the
// ResultSetMetaData column LABEL (so 'AS foo' aliases work). Values are the
// JDBC-native objects -- Integer, Long, String, Timestamp, java.sql.Date,
// etc. Cast at the call site or via typed getters on the Map's Objects.
//
// Configuration -- put connection info in application.properties OR override
// via -Ddb.url=... / DB_URL env var (Config's usual precedence rules apply):
//     db.url=jdbc:postgresql://localhost:5432/mydb
//     db.user=myuser
//     db.password=mypass
//     db.driver=org.postgresql.Driver   (loaded once; usually auto-registers)
//
// If db.url is blank, static shortcuts throw IllegalStateException with a
// clear message. Tests that should skip rather than fail can call
// Db.isConfigured() up-front and throw SkipException.
// =============================================================================

package com.ak.api.db;

import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.ResultSetMetaData;
import java.sql.SQLException;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import com.ak.api.config.Config;

public final class Db {

    private final String url;
    private final String user;
    private final String pass;
    private final String driverClass;

    private Db(String url, String user, String pass, String driverClass) {
        this.url = url;
        this.user = user;
        this.pass = pass;
        this.driverClass = driverClass;
    }

    // =========================================================================
    // Factory
    // =========================================================================

    /** Explicit connection override -- bypasses Config entirely. */
    public static Db using(String url, String user, String pass) {
        return new Db(url, user, pass, null);
    }

    public static Db using(String url, String user, String pass, String driverClass) {
        return new Db(url, user, pass, driverClass);
    }

    /** Config-driven instance -- reads db.url / db.user / db.password / db.driver. */
    public static Db configured() {
        return new Db(
                Config.get("db.url", null),
                Config.get("db.user", null),
                Config.get("db.password", null),
                Config.get("db.driver", null)
        );
    }

    /** True when db.url is present (used by tests that skip rather than fail). */
    public static boolean isConfigured() {
        String url = Config.get("db.url", null);
        return url != null && !url.isBlank();
    }

    // =========================================================================
    // Static shortcuts (use Config)
    // =========================================================================

    public static Map<String, Object> queryOne(String sql, Object... params) {
        return configured().queryOneRow(sql, params);
    }

    public static List<Map<String, Object>> queryAll(String sql, Object... params) {
        return configured().queryAllRows(sql, params);
    }

    public static int execute(String sql, Object... params) {
        String reason = unsafeSqlReason(sql);
        if (reason != null) {
            // Refuse to run malformed SQL against the DB -- the driver
            // would reject it with a cryptic 22P02 / 42601 anyway, and
            // spamming a real DB with garbage each run is wasteful.
            // Logs a clear WARN and returns 0 (no rows affected) so the
            // Groovy-translated caller's try/catch behaves like an empty
            // result set.
            org.slf4j.LoggerFactory.getLogger(Db.class)
                    .warn("Db.execute: refusing to run malformed SQL -- {}. SQL: {}",
                          reason, sql);
            return 0;
        }
        return configured().executeStatement(sql, params);
    }

    /**
     * Detect SQL strings the framework knows will fail at the driver:
     * <ul>
     *   <li>Unresolved SoapUI refs still in the SQL ({@code ${...}})</li>
     *   <li>Placeholder-fallback values -- {@code mapJsonValues} writes
     *       the literal string {@code null} when a {@code #placeholder#}
     *       can't be resolved. A SQL like
     *       {@code select * from account where account_id='null'} then
     *       hits {@code invalid input syntax for type bigint: "null"}.</li>
     *   <li>Zero-arg SELECT into {@link #execute} (should be
     *       {@link #queryAll})</li>
     * </ul>
     * Returns a short human reason when unsafe, {@code null} when clean.
     */
    public static String unsafeSqlReason(String sql) {
        if (sql == null || sql.isEmpty()) return "empty SQL";
        String stripped = sql.trim();
        if (stripped.contains("${")) return "SQL contains untranslated SoapUI ref `${...}`";
        // Detect the mapJsonValues null-substitution fallback: any
        // `= 'null'` or `IN ('null'` etc. that came from an unresolved
        // #placeholder#. Real NULL comparisons use `IS NULL` / `IS NOT
        // NULL`, so a literal 'null' string in a WHERE clause is
        // ~always the fallback marker, not intended data.
        String lowered = stripped.toLowerCase();
        if (lowered.contains("='null'") || lowered.contains("= 'null'")
                || lowered.contains("in ('null'")) {
            return "SQL has 'null' literal from unresolved #placeholder# (mapJsonValues fallback)";
        }
        // Db.execute is for INSERT/UPDATE/DELETE/DDL. A SELECT here means
        // the caller translated `sql.execute` from Groovy but should have
        // used queryAll -- the driver returns a ResultSet and Statement
        // .execute() reports [0100E] "A result was returned when none
        // was expected". Emit a clear WARN so the SoapUI translation is
        // fixable.
        if (lowered.startsWith("select ")) {
            return "SQL is a SELECT -- use Db.queryAll(...) instead of Db.execute(...)";
        }
        return null;
    }

    /**
     * Same as {@link #unsafeSqlReason} but WITHOUT the SELECT-vs-execute
     * check. Callers dispatching to {@link #queryAll} / {@link #queryOne}
     * already handle SELECT correctly; the SELECT reason exists only to
     * catch misroutes into {@link #execute} (which uses executeUpdate and
     * would then throw "A result was returned when none was expected").
     * Otherwise-identical checks: empty SQL, untranslated `${...}` refs,
     * and `= 'null'` fallback from unresolved placeholders.
     */
    public static String unsafeSqlReasonForQuery(String sql) {
        if (sql == null || sql.isEmpty()) return "empty SQL";
        String stripped = sql.trim();
        if (stripped.contains("${")) return "SQL contains untranslated SoapUI ref `${...}`";
        String lowered = stripped.toLowerCase();
        if (lowered.contains("='null'") || lowered.contains("= 'null'")
                || lowered.contains("in ('null'")) {
            return "SQL has 'null' literal from unresolved #placeholder# (mapJsonValues fallback)";
        }
        return null;
    }

    public static boolean exists(String sql, Object... params) {
        return configured().rowExists(sql, params);
    }

    // =========================================================================
    // Instance methods
    // =========================================================================

    public Map<String, Object> queryOneRow(String sql, Object... params) {
        List<Map<String, Object>> rows = queryAllRows(sql, params);
        if (rows.isEmpty()) return null;
        if (rows.size() > 1) {
            throw new IllegalStateException(
                    "queryOne returned " + rows.size() + " rows for: " + sql);
        }
        return rows.get(0);
    }

    public List<Map<String, Object>> queryAllRows(String sql, Object... params) {
        try (Connection c = openConnection();
             PreparedStatement ps = c.prepareStatement(sql)) {
            bind(ps, params);
            try (ResultSet rs = ps.executeQuery()) {
                return readAll(rs);
            }
        } catch (SQLException e) {
            throw new RuntimeException(
                    "Db.queryAll failed [" + e.getSQLState() + "]: " + e.getMessage()
                            + "  SQL: " + sql, e);
        }
    }

    public int executeStatement(String sql, Object... params) {
        try (Connection c = openConnection();
             PreparedStatement ps = c.prepareStatement(sql)) {
            bind(ps, params);
            return ps.executeUpdate();
        } catch (SQLException e) {
            throw new RuntimeException(
                    "Db.execute failed [" + e.getSQLState() + "]: " + e.getMessage()
                            + "  SQL: " + sql, e);
        }
    }

    public boolean rowExists(String sql, Object... params) {
        return queryOneRow(sql, params) != null;
    }

    // =========================================================================
    // Internals
    // =========================================================================

    private Connection openConnection() throws SQLException {
        if (url == null || url.isBlank()) {
            throw new IllegalStateException(
                    "db.url not configured -- set via -Ddb.url=... / DB_URL env var, "
                            + "or construct with Db.using(url, user, pass)");
        }
        if (driverClass != null && !driverClass.isBlank()) {
            try {
                Class.forName(driverClass);
            } catch (ClassNotFoundException e) {
                throw new IllegalStateException(
                        "db.driver class not found on classpath: " + driverClass, e);
            }
        }
        if (user == null || user.isBlank()) {
            return DriverManager.getConnection(url);
        }
        return DriverManager.getConnection(url, user, pass == null ? "" : pass);
    }

    private static void bind(PreparedStatement ps, Object[] params) throws SQLException {
        for (int i = 0; i < params.length; i++) {
            ps.setObject(i + 1, params[i]);
        }
    }

    private static List<Map<String, Object>> readAll(ResultSet rs) throws SQLException {
        List<Map<String, Object>> out = new ArrayList<>();
        ResultSetMetaData md = rs.getMetaData();
        int cols = md.getColumnCount();
        while (rs.next()) {
            Map<String, Object> row = new LinkedHashMap<>();
            for (int i = 1; i <= cols; i++) {
                // getColumnLabel honors 'SELECT foo AS bar' aliases; getColumnName does NOT.
                row.put(md.getColumnLabel(i), rs.getObject(i));
            }
            out.add(row);
        }
        return out;
    }
}
