// =============================================================================
// Db -- SQL query utility (plain JDBC or jOOQ)
// -----------------------------------------------------------------------------
// Default backend is plain JDBC (db.api=sql): every call opens a fresh
// DriverManager connection and closes it on the way out. Set
// {@code db.api=jooq} (or {@code -Ddb.api=jooq}) to run the same SQL
// strings through org.jooq. Imported ReadyAPI JDBC steps keep calling
// {@code Db.queryAll} / {@code pollUntilStable} unchanged.
//
// Fluent jOOQ (optional, hand-written tests):
//
//     int n = Db.configured().withDsl(dsl -> dsl.fetchCount(Db.table("users")));
//     Integer otp = db.withDsl(dsl -> dsl.select(Tables.ACCOUNT_MEMBER_INTERNAL_SECURITY.EMAIL_OTP)
//         .from(Tables.ACCOUNT_MEMBER_INTERNAL_SECURITY)
//         .where(Tables.ACCOUNT_MEMBER_INTERNAL_SECURITY.ACCOUNT_MEMBER_ID.eq(id))
//         .fetchOne(Tables.ACCOUNT_MEMBER_INTERNAL_SECURITY.EMAIL_OTP));
//
// Schema catalog: com.hi.api.db.schema (hand-maintained OTP table).
// Full codegen from live Postgres (skipped by default):
//     mvn generate-sources -Djooq.codegen.skip=false -Ddb.url=... -Ddb.user=... -Ddb.password=...
// Output package: com.hi.api.db.generated
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
//   * Db.using(url, user, pass) -- explicit connection override. Always
//     JDBC ({@link Api#SQL}) so H2 self-tests stay isolated from
//     {@code -Ddb.api=jooq}. Use {@link #withApi} or {@link #configured}
//     when the jOOQ backend should apply.
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

package com.hi.api.db;

import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.ResultSetMetaData;
import java.sql.SQLException;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

import org.jooq.DSLContext;
import org.jooq.Field;
import org.jooq.Name;
import org.jooq.Record;
import org.jooq.SQLDialect;
import org.jooq.Table;
import org.jooq.exception.DataAccessException;
import org.jooq.impl.DSL;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import com.hi.api.config.Config;
import com.hi.api.db.schema.AccountMemberInternalSecurity;

public final class Db {

    private static final Logger LOG = LoggerFactory.getLogger(Db.class);

    private static final String EMAIL_OTP_SQL =
            "select email_otp from segment.account_member_internal_security "
                    + "where account_member_id = ?";
    private static final int OTP_POLL_ATTEMPTS = 4;
    private static final long OTP_POLL_SLEEP_MS = 2000L;

    // ---------------------------------------------------------------
    // Thread-local fallback signal
    // ---------------------------------------------------------------
    // Set by RestUtilities.mapSqlValues after its underlying
    // mapJsonValues reports one or more unresolved #placeholder#
    // substitutions -- those get replaced with the literal string
    // `null`, which combined with the raw SQL's surrounding quotes
    // (`WHERE email='#email#'` -> `WHERE email='null'`) is the exact
    // bug pattern unsafeSqlReason catches.
    //
    // Without this flag, unsafeSqlReason would refuse ANY SQL with
    // `='null'` -- including the perfectly legitimate case where a
    // varchar audit column literally stores the string "null" and the
    // author wrote `WHERE status='null'` directly. That produced silent
    // 0-row results and made tests pass on wrong data.
    //
    // ThreadLocal so parallel="classes" runs don't cross-contaminate:
    // one class's mapSqlValues fallback should not affect another
    // class's Db call. Also cleared in BaseApiTest.newTestHolder as
    // defense-in-depth against thread reuse across tests.
    private static final ThreadLocal<Boolean> NULL_FALLBACK_TRIPPED =
            ThreadLocal.withInitial(() -> Boolean.FALSE);

    /** Framework-internal -- called by RestUtilities.mapSqlValues. */
    public static void markNullFallbackTripped() {
        NULL_FALLBACK_TRIPPED.set(Boolean.TRUE);
    }

    /** Framework-internal -- called by RestUtilities.mapSqlValues + BaseApiTest.newTestHolder. */
    public static void clearNullFallbackFlag() {
        NULL_FALLBACK_TRIPPED.set(Boolean.FALSE);
    }

    /** Non-consuming peek -- used by unsafeSqlReason variants. */
    public static boolean isNullFallbackTripped() {
        return Boolean.TRUE.equals(NULL_FALLBACK_TRIPPED.get());
    }

    private final String url;
    private final String user;
    private final String pass;
    private final String driverClass;
    /** Null = resolve {@link Api#fromConfig()} on each call. */
    private final Api apiOverride;

    /**
     * How {@link Db} runs SQL. {@code SQL} is plain JDBC;
     * {@code JOOQ} uses {@code org.jooq} for the same strings.
     */
    public enum Api {
        SQL,
        JOOQ;

        public static Api fromConfig() {
            String v = Config.get("db.api", "sql");
            if (v == null || v.isBlank()) {
                return SQL;
            }
            return switch (v.trim().toLowerCase(Locale.ROOT)) {
                case "sql", "jdbc" -> SQL;
                case "jooq", "orm" -> JOOQ;
                default -> throw new IllegalArgumentException(
                        "db.api must be sql or jooq, got: " + v);
            };
        }
    }

    @FunctionalInterface
    public interface DslWork<T> {
        T run(DSLContext dsl) throws Exception;
    }

    private Db(String url, String user, String pass, String driverClass) {
        this(url, user, pass, driverClass, null);
    }

    private Db(String url, String user, String pass, String driverClass, Api apiOverride) {
        this.url = url;
        this.user = user;
        this.pass = pass;
        this.driverClass = driverClass;
        this.apiOverride = apiOverride;
    }

    // =========================================================================
    // Factory
    // =========================================================================

    /** Explicit connection override -- bypasses Config (URL/user/password
     *  <b>and</b> {@code db.api}). Backend is JDBC until {@link #withApi}. */
    public static Db using(String url, String user, String pass) {
        return new Db(url, user, pass, null, Api.SQL);
    }

    public static Db using(String url, String user, String pass, String driverClass) {
        return new Db(url, user, pass, driverClass, Api.SQL);
    }

    /**
     * Pin JDBC vs jOOQ for this instance (ignores {@code db.api} for
     * subsequent calls). Handy in H2 self-tests that must exercise both.
     */
    public Db withApi(Api api) {
        if (api == null) {
            throw new IllegalArgumentException("Db.withApi requires sql or jooq");
        }
        return new Db(url, user, pass, driverClass, api);
    }

    /** Backend this instance will use on the next query. */
    public Api api() {
        return resolveApi();
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

    /**
     * ReadyAPI CleanupDB helper: turn a comma-separated Project property
     * ({@code WEBSITE_DOMAIN}) into a SQL {@code IN (...)} list of
     * single-quoted literals. Empty/blank input yields {@code ""}.
     */
    public static String quotedCsvInList(String csv) {
        if (csv == null || csv.isBlank()) {
            return "";
        }
        StringBuilder sb = new StringBuilder();
        for (String part : csv.split(",")) {
            String trimmed = part.trim();
            if (trimmed.isEmpty()) {
                continue;
            }
            if (sb.length() > 0) {
                sb.append(',');
            }
            sb.append('\'').append(trimmed.replace("'", "''")).append('\'');
        }
        return sb.toString();
    }

    /**
     * Comma-separated numeric literals for a SQL {@code IN (...)} list.
     * Non-digit tokens are dropped so caller-supplied ctx values cannot
     * inject SQL.
     */
    public static String numericCsvInList(Iterable<String> ids) {
        if (ids == null) {
            return "";
        }
        StringBuilder sb = new StringBuilder();
        for (String part : ids) {
            if (part == null) {
                continue;
            }
            String trimmed = part.trim();
            if (trimmed.isEmpty() || !trimmed.matches("\\d{1,20}")) {
                continue;
            }
            if (sb.length() > 0) {
                sb.append(',');
            }
            sb.append(trimmed);
        }
        return sb.toString();
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
            LOG.warn("Db.execute: refusing to run malformed SQL -- {}. SQL: {}",
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
    /**
     * Run one translated ReadyAPI JDBC step: resolve placeholders, refuse
     * malformed SQL, execute, and never let a DB problem fail the test.
     *
     * <p>Replaces a 16-line block the converter emitted at <b>1,291 sites
     * across 529 files</b> -- roughly 20,000 lines of identical
     * guard/try/catch/log ceremony. Every one of those sites had to be
     * regenerated to change any part of the behaviour; now there is one.</p>
     *
     * <p>Semantics are preserved exactly, because the emitted block is what
     * this was extracted from:</p>
     * <ul>
     *   <li>Db not configured -> log and skip (a suite without DB access
     *       still runs its HTTP assertions).</li>
     *   <li>{@link #unsafeSqlReason} non-null -> log the reason and skip,
     *       rather than sending a malformed statement.</li>
     *   <li>Any exception -> warn and continue. A cleanup/setup DB step is
     *       not the thing under test; failing the case here would hide the
     *       actual assertion result.</li>
     * </ul>
     *
     * @param rawSql    SQL with {@code #placeholder#} refs, as translated
     * @param mergedRow row + ctx merge used for placeholder resolution
     * @param ctx       scenario context
     * @return rows affected, or {@link #DID_NOT_RUN} when the statement was
     *         skipped or threw. A caller that needs the difference between
     *         "ran and changed nothing" and "never ran" can now see it;
     *         callers that do not care keep ignoring the value, which is why
     *         widening this from void is source-compatible.
     */
    public static int executeTranslated(String rawSql,
                                        Map<String, String> mergedRow,
                                        Map<String, String> ctx) {
        if (!isConfigured()) {
            LOG.warn("Skipping JDBC step (Db not configured): {}", preview(rawSql));
            return DID_NOT_RUN;
        }
        try {
            String sql = com.hi.api.rest.utilities.RestUtilities.mapSqlValues(
                    rawSql, mergedRow, ctx);
            String reason = unsafeSqlReason(sql);
            if (reason != null) {
                LOG.warn(" .. jdbc SKIPPED ({}): {}", reason, sql);
                return DID_NOT_RUN;
            }
            LOG.info(" .. jdbc SQL: {}", sql);
            return execute(sql);
        } catch (Exception e) {
            LOG.warn("JDBC execute failed: {}", e.getMessage());
            return DID_NOT_RUN;
        }
    }

    /**
     * Returned by {@link #executeTranslated} when the statement never
     * reached the database -- no config, refused by the SQL guard, or threw.
     * Distinct from {@code 0}, which means it RAN and matched no rows.
     */
    public static final int DID_NOT_RUN = -1;

    /**
     * Guard + log + execute + swallow, for SQL the caller has ALREADY
     * composed and placeholder-resolved.
     *
     * <p>This is the invariant tail of the 16-line block the converter
     * emitted at 1,291 sites. It is separate from
     * {@link #executeTranslated} because those sites compose their SQL six
     * different ways (literal, string-concat, mapSqlValues, ...). Extracting
     * only the tail lets every site keep its own composition verbatim, so
     * the change is mechanical rather than a rewrite of six code paths.</p>
     */
    public static void executeComposed(String sql) {
        if (!isConfigured()) {
            LOG.warn("Skipping JDBC step (Db not configured): {}", preview(sql));
            return;
        }
        try {
            String reason = unsafeSqlReason(sql);
            if (reason != null) {
                LOG.warn(" .. jdbc SKIPPED ({}): {}", reason, sql);
                return;
            }
            LOG.info(" .. jdbc SQL: {}", sql);
            execute(sql);
        } catch (Exception e) {
            LOG.warn("JDBC execute failed: {}", e.getMessage());
        }
    }

    /**
     * Query counterpart of {@link #executeComposed}, for SQL the caller has
     * already composed.
     *
     * <p>Uses {@link #unsafeSqlReasonForQuery} rather than the full check:
     * the SELECT-vs-execute rule exists to catch a SELECT misrouted into
     * {@code execute}, and would spuriously refuse this correctly-routed
     * path.</p>
     *
     * <p>Returns an empty list, never null, so callers can iterate
     * unconditionally after a skip.</p>
     */
    public static List<Map<String, Object>> queryComposed(String sql, Object... params) {
        if (!isConfigured()) {
            LOG.warn("Skipping JDBC query (Db not configured): {}", preview(sql));
            return java.util.Collections.emptyList();
        }
        try {
            String reason = unsafeSqlReasonForQuery(sql);
            if (reason != null) {
                LOG.warn(" .. jdbc SKIPPED ({}): {}", reason, sql);
                return java.util.Collections.emptyList();
            }
            LOG.info(" .. jdbc SQL: {}", sql);
            List<Map<String, Object>> rows = queryAll(sql, params);
            LOG.info(" .. jdbc rows returned: {}", rows == null ? 0 : rows.size());
            return rows == null ? java.util.Collections.emptyList() : rows;
        } catch (Exception e) {
            LOG.warn("JDBC queryAll failed: {}", e.getMessage());
            return java.util.Collections.emptyList();
        }
    }

    /**
     * Single-row counterpart of {@link #queryComposed}, for translated
     * {@code sql.firstRow(...)}.
     *
     * <p>Returns {@code null} when the DB is unavailable, the SQL is refused,
     * or no row matched -- matching {@link #queryOne}, so callers that already
     * null-check keep working unchanged.</p>
     */
    public static Map<String, Object> queryOneComposed(String sql, Object... params) {
        if (!isConfigured()) {
            LOG.warn("Skipping JDBC query (Db not configured): {}", preview(sql));
            return null;
        }
        try {
            String reason = unsafeSqlReasonForQuery(sql);
            if (reason != null) {
                LOG.warn(" .. jdbc SKIPPED ({}): {}", reason, sql);
                return null;
            }
            LOG.info(" .. jdbc SQL: {}", sql);
            Map<String, Object> row = queryOne(sql, params);
            LOG.info(" .. jdbc returned {} row(s)", row == null ? 0 : 1);
            return row;
        } catch (Exception e) {
            LOG.warn("JDBC queryOne failed: {}", e.getMessage());
            return null;
        }
    }

    /**
     * Query form of {@link #executeTranslated}. Returns an empty list rather
     * than null when the DB is unavailable or the SQL is refused, so callers
     * can iterate unconditionally.
     */
    public static List<Map<String, Object>> queryTranslated(String rawSql,
                                                            Map<String, String> mergedRow,
                                                            Map<String, String> ctx) {
        if (!isConfigured()) {
            LOG.warn("Skipping JDBC query (Db not configured): {}", preview(rawSql));
            return java.util.Collections.emptyList();
        }
        try {
            String sql = com.hi.api.rest.utilities.RestUtilities.mapSqlValues(
                    rawSql, mergedRow, ctx);
            String reason = unsafeSqlReasonForQuery(sql);
            if (reason != null) {
                LOG.warn(" .. jdbc query SKIPPED ({}): {}", reason, sql);
                return java.util.Collections.emptyList();
            }
            LOG.info(" .. jdbc query: {}", sql);
            return queryAll(sql);
        } catch (Exception e) {
            LOG.warn("JDBC query failed: {}", e.getMessage());
            return java.util.Collections.emptyList();
        }
    }

    /**
     * A statement must contain at least one word character outside quotes /
     * punctuation to be worth sending to the driver.
     */
    private static final java.util.regex.Pattern STATEMENT_TEXT =
            java.util.regex.Pattern.compile("[A-Za-z0-9_]");

    private static int countChar(String s, char c) {
        int n = 0;
        for (int i = 0; i < s.length(); i++) {
            if (s.charAt(i) == c) {
                n++;
            }
        }
        return n;
    }

    /** Short, log-safe echo of a rejected statement. */
    private static String preview(String sql) {
        String one = sql.replaceAll("\\s+", " ").trim();
        return one.length() <= 120 ? one : one.substring(0, 120) + "...";
    }

    public static String unsafeSqlReason(String sql) {
        if (sql == null || sql.isEmpty()) return "empty SQL";
        String stripped = sql.trim();
        // Whitespace-only. `isEmpty()` above misses "   " / "\n\t", which a
        // Groovy translation produces when every concatenated fragment
        // resolved to "". Executing it throws a driver syntax error whose
        // message names no cause; this names it.
        if (stripped.isEmpty()) return "SQL is whitespace only";
        // No statement text at all -- quote-only ('' / '''') or punctuation
        // left behind when the placeholder that carried the whole statement
        // resolved empty.
        if (!STATEMENT_TEXT.matcher(stripped).find()) {
            return "SQL has no statement text (quote/punctuation only): " + preview(stripped);
        }
        // Unbalanced single quotes. SQL escapes a quote by DOUBLING it
        // (`'it''s'` = 4 quotes), so a well-formed statement always has an
        // EVEN count -- an odd count means a literal was truncated. Real
        // case in this corpus:
        //   DELETE FROM account WHERE web_site IN ('a.com', 'www.laafd.com)
        // The final literal never closes, so the driver swallows the rest
        // of the statement (including the paren) into the string and the
        // DELETE either errors or, worse, matches nothing silently.
        if (countChar(stripped, '\'') % 2 != 0) {
            return "SQL has unbalanced single quotes (odd count) -- a literal is "
                    + "unterminated: " + preview(stripped);
        }
        if (countChar(stripped, '(') != countChar(stripped, ')')) {
            return "SQL has unbalanced parentheses: " + preview(stripped);
        }
        if (stripped.contains("${")) return "SQL contains untranslated SoapUI ref `${...}`";
        // Detect the mapJsonValues null-substitution fallback: any
        // `= 'null'` or `IN ('null'` etc. that came from an unresolved
        // #placeholder#. Real NULL comparisons use `IS NULL` / `IS NOT
        // NULL`, so a literal 'null' string in a WHERE clause is
        // ~always the fallback marker, not intended data.
        //
        // GATED on isNullFallbackTripped(): only trigger when we KNOW
        // RestUtilities.mapSqlValues just fired the fallback path on
        // this thread. Otherwise a legitimate `WHERE status='null'`
        // against a varchar audit column that literally stores the
        // string "null" would be wrongly blocked (returning 0 rows +
        // silently passing on wrong data).
        String lowered = stripped.toLowerCase();
        if (isNullFallbackTripped()
                && (lowered.contains("='null'") || lowered.contains("= 'null'")
                        || lowered.contains("in ('null'"))) {
            return "SQL has 'null' literal from unresolved #placeholder# (mapJsonValues fallback fired on this thread)";
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
        // Empty IN-list from an unset WEBSITE_DOMAIN / ALLOWED_DOMAINS CSV.
        if (lowered.contains("in ()") || lowered.contains("in()")) {
            return "SQL IN-list is empty";
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
        // Same fallback-flag gate as unsafeSqlReason -- see the comment
        // there for why the raw pattern-match had unacceptable false
        // positives on `WHERE varchar_col='null'` audit-column SQL.
        String lowered = stripped.toLowerCase();
        if (isNullFallbackTripped()
                && (lowered.contains("='null'") || lowered.contains("= 'null'")
                        || lowered.contains("in ('null'"))) {
            return "SQL has 'null' literal from unresolved #placeholder# (mapJsonValues fallback fired on this thread)";
        }
        if (lowered.contains("in ()") || lowered.contains("in()")) {
            return "SQL IN-list is empty";
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
        if (resolveApi() == Api.JOOQ) {
            return queryAllRowsJooq(sql, params);
        }
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
        if (resolveApi() == Api.JOOQ) {
            return executeStatementJooq(sql, params);
        }
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
    // Poll helpers (OTP / until-stable)
    // =========================================================================

    /**
     * Poll {@code sql} until the named column's string value is stable
     * across two consecutive non-empty reads, or {@code maxAttempts} is
     * exhausted. Matches the 4-attempt / 2s loop the converter inlines
     * around JDBC OTP extracts.
     *
     * <p>Skips (returns {@code ""}) when {@link #isConfigured()} is false
     * or {@link #unsafeSqlReasonForQuery} flags the SQL.</p>
     */
    public static String pollUntilStable(String sql, String column) {
        return pollUntilStable(sql, column, OTP_POLL_ATTEMPTS, OTP_POLL_SLEEP_MS);
    }

    public static String pollUntilStable(String sql, String column,
                                         int maxAttempts, long sleepMs) {
        if (!isConfigured()) {
            LOG.warn("Skipping JDBC poll (Db not configured -- set db.url + db.user "
                    + "+ db.password in program_configuration.json OR DB_URL/DB_USER/"
                    + "DB_PASSWORD env vars)");
            return "";
        }
        String reason = unsafeSqlReasonForQuery(sql);
        if (reason != null) {
            LOG.warn(" .. jdbc SKIPPED ({}): {}", reason, sql);
            return "";
        }
        try {
            return configured().pollColumnUntilStable(sql, column, maxAttempts, sleepMs);
        } catch (RuntimeException e) {
            LOG.warn("JDBC poll failed: {}", e.getMessage());
            return "";
        }
    }

    /**
     * Poll {@code segment.account_member_internal_security.email_otp} for
     * {@code accountMemberId} (parameterized {@code ?}, 4 attempts / 2s,
     * 6-digit pad).
     *
     * <p><b>Before</b> (B2B-9098, ~80 inlined JDBC lines):</p>
     * <pre>
     * String sql = RestUtilities.mapSqlValues(
     *     "select email_otp from ... where account_member_id = '#guestID#'", ...);
     * // 4-attempt / 2s loop, pad, putExtracted
     * </pre>
     *
     * <p><b>After:</b></p>
     * <pre>
     * String otp = Db.pollEmailOtp(TestSupport.ctxGet(ctx, "PropertiesaccountID.hilton-member-id"));
     * TestSupport.putExtracted(ctx, "Properties.totpCode", otp);
     * </pre>
     *
     * <p>Returns {@code ""} when the DB is not configured, the id is blank,
     * or the query throws (same skip-and-continue as the generated tests).</p>
     */
    public static String pollEmailOtp(String accountMemberId) {
        if (!isConfigured()) {
            LOG.warn("Skipping JDBC eachRow step (Db not configured -- set db.url + db.user "
                    + "+ db.password in program_configuration.json OR DB_URL/DB_USER/"
                    + "DB_PASSWORD env vars)");
            return "";
        }
        if (accountMemberId == null || accountMemberId.isBlank()) {
            LOG.warn(" .. jdbc SKIPPED (empty account_member_id)");
            return "";
        }
        try {
            return configured().pollMemberEmailOtp(accountMemberId);
        } catch (RuntimeException e) {
            LOG.warn("JDBC eachRow failed: {}", e.getMessage());
            return "";
        }
    }

    /**
     * Instance OTP poll. jOOQ backend uses
     * {@link AccountMemberInternalSecurity}; SQL backend uses the same
     * parameterized string as imported tests.
     */
    public String pollMemberEmailOtp(String accountMemberId) {
        return pollMemberEmailOtp(accountMemberId, OTP_POLL_ATTEMPTS, OTP_POLL_SLEEP_MS);
    }

    public String pollMemberEmailOtp(String accountMemberId,
                                     int maxAttempts, long sleepMs) {
        if (accountMemberId == null || accountMemberId.isBlank()) {
            LOG.warn(" .. jdbc SKIPPED (empty account_member_id)");
            return "";
        }
        if (resolveApi() == Api.JOOQ) {
            return pollEmailOtpJooq(accountMemberId, maxAttempts, sleepMs);
        }
        return pollColumnUntilStable(
                EMAIL_OTP_SQL, "email_otp",
                maxAttempts, sleepMs, accountMemberId);
    }

    private String pollEmailOtpJooq(String accountMemberId,
                                    int maxAttempts, long sleepMs) {
        if (maxAttempts < 1) maxAttempts = 1;
        if (sleepMs < 0) sleepMs = 0;
        AccountMemberInternalSecurity t =
                AccountMemberInternalSecurity.ACCOUNT_MEMBER_INTERNAL_SECURITY;
        String last = "";
        String prev = null;
        for (int attempt = 1; attempt <= maxAttempts; attempt++) {
            Integer otp = withDsl(dsl -> dsl.select(t.EMAIL_OTP)
                    .from(t)
                    .where(t.ACCOUNT_MEMBER_ID.eq(accountMemberId))
                    .fetchOne(t.EMAIL_OTP));
            last = formatColumn("email_otp", otp);
            LOG.info(" .. jooq extract email_otp={}", last.isEmpty() ? "<empty>" : last);
            if (attempt > 1 && last != null && !last.isEmpty() && last.equals(prev)) {
                LOG.info(" .. [OTP poll] STABLE after {} polls: email_otp={}", attempt, last);
                break;
            }
            if (attempt > 1) {
                LOG.info(" .. [OTP poll] attempt {} email_otp={} (previous={}) -- NOT stable, will re-poll",
                        attempt, last, prev);
            }
            prev = last;
            if (attempt < maxAttempts) {
                try {
                    Thread.sleep(sleepMs);
                } catch (InterruptedException ie) {
                    Thread.currentThread().interrupt();
                    break;
                }
            } else {
                LOG.warn(" .. [OTP poll] EXHAUSTED {} attempts without stability -- using last value: email_otp={}",
                        maxAttempts, last);
            }
        }
        return last == null ? "" : last;
    }

    /**
     * Instance poll so {@link #using} (H2 self-tests) can exercise the
     * same stability loop without Config. Column lookup is
     * case-insensitive (H2 labels come back uppercase). Values from an
     * {@code email_otp} column are 6-digit padded.
     */
    public String pollColumnUntilStable(String sql, String column,
                                        int maxAttempts, long sleepMs,
                                        Object... params) {
        if (sql == null) sql = "";
        if (maxAttempts < 1) maxAttempts = 1;
        if (sleepMs < 0) sleepMs = 0;
        String last = "";
        String prev = null;
        for (int attempt = 1; attempt <= maxAttempts; attempt++) {
            List<Map<String, Object>> rows = queryAllRows(sql, params);
            LOG.info(" .. jdbc eachRow attempt {} returned {} row(s)",
                    attempt, rows == null ? 0 : rows.size());
            last = "";
            if (rows != null) {
                for (Map<String, Object> row : rows) {
                    last = formatColumn(column, columnValue(row, column));
                    LOG.info(" .. jdbc extract {}={}",
                            column, last.isEmpty() ? "<empty>" : last);
                }
            }
            if (attempt > 1 && last != null && !last.isEmpty() && last.equals(prev)) {
                LOG.info(" .. [OTP poll] STABLE after {} polls: {}={}",
                        attempt, column, last);
                break;
            }
            if (attempt > 1) {
                LOG.info(" .. [OTP poll] attempt {} {}={} (previous={}) -- NOT stable, will re-poll",
                        attempt, column, last, prev);
            }
            prev = last;
            if (attempt < maxAttempts) {
                try {
                    Thread.sleep(sleepMs);
                } catch (InterruptedException ie) {
                    Thread.currentThread().interrupt();
                    break;
                }
            } else {
                LOG.warn(" .. [OTP poll] EXHAUSTED {} attempts without stability -- using last value: {}={}",
                        maxAttempts, column, last);
            }
        }
        return last == null ? "" : last;
    }

    // =========================================================================
    // jOOQ fluent (always DSL, regardless of db.api)
    // =========================================================================

    /**
     * Open one connection, run {@code work} against a {@link DSLContext},
     * then close. Use for type-safe / fluent verification in hand-written
     * tests. Imported JDBC steps should keep using {@link #queryAll}.
     *
     * <pre>
     * String email = Db.using(url, user, pass).withDsl(dsl -&gt;
     *     dsl.fetchOne("SELECT email FROM users WHERE id = ?", 1)
     *        .get("EMAIL", String.class));
     * </pre>
     */
    public <T> T withDsl(DslWork<T> work) {
        if (work == null) {
            throw new IllegalArgumentException("Db.withDsl requires a callback");
        }
        try (Connection c = openConnection()) {
            DSLContext dsl = DSL.using(c, dialectFor(url));
            try {
                return work.run(dsl);
            } catch (RuntimeException e) {
                throw e;
            } catch (Exception e) {
                throw new RuntimeException("Db.withDsl failed: " + e.getMessage(), e);
            }
        } catch (SQLException e) {
            throw new RuntimeException(
                    "Db.withDsl failed [" + e.getSQLState() + "]: " + e.getMessage(), e);
        }
    }

    /**
     * Config-driven {@link #withDsl(DslWork)} ({@code db.url} / user / password).
     */
    public static <T> T dsl(DslWork<T> work) {
        return configured().withDsl(work);
    }

    /**
     * Dialect-safe identifier. Prefer {@code Db.table("segment", "account_member_internal_security")}
     * or {@link com.hi.api.db.schema.Tables} over {@code DSL.table("users")}.
     */
    public static Name name(String... parts) {
        if (parts == null || parts.length == 0) {
            throw new IllegalArgumentException("Db.name requires at least one part");
        }
        return DSL.name(parts);
    }

    public static Table<Record> table(String... parts) {
        return DSL.table(name(parts));
    }

    public static Field<Object> field(String... parts) {
        return DSL.field(name(parts));
    }

    // =========================================================================
    // Internals
    // =========================================================================

    private Api resolveApi() {
        return apiOverride != null ? apiOverride : Api.fromConfig();
    }

    public static SQLDialect dialectFor(String jdbcUrl) {
        if (jdbcUrl == null || jdbcUrl.isBlank()) {
            return SQLDialect.DEFAULT;
        }
        String u = jdbcUrl.toLowerCase(Locale.ROOT);
        if (u.startsWith("jdbc:postgresql:")) {
            return SQLDialect.POSTGRES;
        }
        if (u.startsWith("jdbc:h2:")) {
            return SQLDialect.H2;
        }
        if (u.startsWith("jdbc:mysql:") || u.startsWith("jdbc:mariadb:")) {
            return SQLDialect.MYSQL;
        }
        // Oracle / SQL Server dialects are commercial jOOQ; OSS uses DEFAULT.
        return SQLDialect.DEFAULT;
    }

    private List<Map<String, Object>> queryAllRowsJooq(String sql, Object... params) {
        LOG.debug(" .. db[jooq] query {}", sql);
        try (Connection c = openConnection()) {
            DSLContext dsl = DSL.using(c, dialectFor(url));
            try (ResultSet rs = dsl.resultQuery(sql, params).fetchResultSet()) {
                return readAll(rs);
            }
        } catch (SQLException | DataAccessException e) {
            String state = (e instanceof SQLException se) ? se.getSQLState() : "JOOQ";
            throw new RuntimeException(
                    "Db.queryAll (jooq) failed [" + state + "]: " + e.getMessage()
                            + "  SQL: " + sql, e);
        }
    }

    private int executeStatementJooq(String sql, Object... params) {
        LOG.debug(" .. db[jooq] execute {}", sql);
        try (Connection c = openConnection()) {
            return DSL.using(c, dialectFor(url)).execute(sql, params);
        } catch (SQLException | DataAccessException e) {
            String state = (e instanceof SQLException se) ? se.getSQLState() : "JOOQ";
            throw new RuntimeException(
                    "Db.execute (jooq) failed [" + state + "]: " + e.getMessage()
                            + "  SQL: " + sql, e);
        }
    }

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

    /** Case-insensitive column lookup -- H2 returns uppercase labels. */
    static Object columnValue(Map<String, Object> row, String column) {
        if (row == null || column == null) return null;
        if (row.containsKey(column)) return row.get(column);
        for (Map.Entry<String, Object> e : row.entrySet()) {
            if (e.getKey() != null && e.getKey().equalsIgnoreCase(column)) {
                return e.getValue();
            }
        }
        return null;
    }

    /**
     * Stringify a JDBC cell. {@code email_otp} numbers and short digit
     * strings are left-padded to 6 digits (Hilton TOTP).
     */
    static String formatColumn(String column, Object value) {
        if (value == null) return "";
        boolean otp = column != null && "email_otp".equalsIgnoreCase(column);
        if (otp && value instanceof Number) {
            return String.format("%06d", ((Number) value).longValue());
        }
        String s = String.valueOf(value).trim();
        if (otp && !s.isEmpty() && s.length() < 6 && s.chars().allMatch(Character::isDigit)) {
            String padded = String.format("%6s", s).replace(' ', '0');
            LOG.info(" .. [OTP pad] expanding {}-char DB value '{}' -> '{}'",
                    s.length(), s, padded);
            return padded;
        }
        return s;
    }
}
