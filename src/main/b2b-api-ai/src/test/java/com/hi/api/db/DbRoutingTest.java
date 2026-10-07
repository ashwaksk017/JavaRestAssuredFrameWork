package com.hi.api.db;

import static org.testng.Assert.assertEquals;
import static org.testng.Assert.assertFalse;
import static org.testng.Assert.assertNotNull;
import static org.testng.Assert.assertTrue;

import java.util.ArrayList;
import java.util.List;

import org.testng.annotations.AfterMethod;
import org.testng.annotations.BeforeMethod;
import org.testng.annotations.Test;

/**
 * {@link DbRouting} resolves a JDBC step's database NAME to connection
 * settings. Pure config lookups -- no database is contacted.
 *
 * <p>Every key is supplied as a System property, which is the highest
 * precedence in {@code Config.get}, so the test is independent of whatever
 * program_configuration.json (if any) is on the classpath. Each property set
 * here is removed again after the method so the JVM is left as found.</p>
 */
public class DbRoutingTest {

    private static final String PLAIN_URL  = "jdbc:postgresql://plain-host.example:5432/plaindb";
    private static final String PLAIN_USER = "plain_user";
    private static final String PLAIN_PASS = "plain-pw-7f3a";

    private static final String NAMED_HOST = "named-host.example";
    private static final String NAMED_PASS = "named-pw-9c1e";

    private final List<String> setKeys = new ArrayList<>();

    private void set(String key, String value) {
        System.setProperty(key, value);
        setKeys.add(key);
    }

    @BeforeMethod
    public void plainBlock() {
        DbRouting.resetAnnouncements();
        set("db.url", PLAIN_URL);
        set("db.user", PLAIN_USER);
        set("db.password", PLAIN_PASS);
    }

    @AfterMethod
    public void clearProperties() {
        for (String k : setKeys) {
            System.clearProperty(k);
        }
        setKeys.clear();
        DbRouting.resetAnnouncements();
    }

    @Test
    public void namedKeysWinOverThePlainBlock() {
        set("database.unitdb.host", NAMED_HOST);
        set("database.unitdb.port", "6543");
        set("database.unitdb.schema", "unitdb");
        set("database.unitdb.username", "unit_user");
        set("database.unitdb.password", NAMED_PASS);

        DbRouting.Target t = DbRouting.resolve("unitdb");

        assertTrue(t.named(), "named block should be chosen");
        assertEquals(t.url(), "jdbc:postgresql://" + NAMED_HOST + ":6543/unitdb");
        assertEquals(t.user(), "unit_user");
        assertEquals(t.pass(), NAMED_PASS);
        assertTrue(t.missing().isEmpty(), "nothing missing: " + t.missing());
        assertEquals(t.source(), "database.unitdb.*");
        assertTrue(t.isConfigured());
        assertTrue(Db.isConfigured("unitdb"));
        assertNotNull(Db.forDatabase("unitdb"));
    }

    @Test
    public void portDefaultsAndSchemeOverrides() {
        set("database.mydb.host", NAMED_HOST);
        set("database.mydb.schema", "mydb");
        set("database.mydb.username", "u");
        set("database.mydb.password", "p");
        set("database.mydb.scheme", "mysql");

        DbRouting.Target t = DbRouting.resolve("mydb");

        assertEquals(t.url(), "jdbc:mysql://" + NAMED_HOST + ":5432/mydb");
    }

    @Test
    public void unknownNameFallsBackToThePlainBlock() {
        DbRouting.Target t = DbRouting.resolve("b2b-stg");

        assertFalse(t.named());
        assertEquals(t.url(), PLAIN_URL);
        assertEquals(t.user(), PLAIN_USER);
        assertEquals(t.pass(), PLAIN_PASS);
        assertEquals(t.source(), "database.* (fallback)");
        assertTrue(t.missing().isEmpty());
        assertTrue(t.describe().contains("fallback"), t.describe());
        assertEquals(Db.isConfigured("b2b-stg"), Db.isConfigured());
    }

    @Test
    public void blankNameIsThePlainBlock() {
        assertEquals(DbRouting.resolve("").url(), PLAIN_URL);
        assertEquals(DbRouting.resolve(null).url(), PLAIN_URL);
        assertFalse(DbRouting.resolve("  ").named());
    }

    @Test
    public void partialBlockWithoutHostOrSchemaReportsWhatIsMissingAndFallsBack() {
        set("database.halfdb.username", "half_user");
        set("database.halfdb.password", "half-pw");

        DbRouting.Target t = DbRouting.resolve("halfdb");

        assertFalse(t.named(), "host+schema absent -> plain block");
        assertEquals(t.url(), PLAIN_URL);
        assertEquals(t.missing(), List.of("database.halfdb.host", "database.halfdb.schema"));
        String line = t.describe();
        assertTrue(line.contains("missing: database.halfdb.host, database.halfdb.schema"), line);
        assertTrue(line.contains("fallback"), line);
    }

    @Test
    public void namedBlockWithEmptyPasswordIsUsedAndSaysSo() {
        set("database.nopw.host", NAMED_HOST);
        set("database.nopw.schema", "nopw");
        set("database.nopw.username", "nopw_user");
        // password deliberately not set -- the shape the user fills in later

        DbRouting.Target t = DbRouting.resolve("nopw");

        assertTrue(t.named(), "host+schema present -> named block even without password");
        assertEquals(t.url(), "jdbc:postgresql://" + NAMED_HOST + ":5432/nopw");
        assertEquals(t.missing(), List.of("database.nopw.password"));
        assertTrue(t.describe().contains("missing: database.nopw.password"), t.describe());
    }

    @Test
    public void passwordNeverAppearsInTheLogLine() {
        set("database.unitdb.host", NAMED_HOST);
        set("database.unitdb.schema", "unitdb");
        set("database.unitdb.username", "unit_user");
        set("database.unitdb.password", NAMED_PASS);

        String named = DbRouting.resolve("unitdb").describe();
        String plain = DbRouting.resolve("other").describe();

        assertFalse(named.contains(NAMED_PASS), named);
        assertFalse(named.contains(PLAIN_PASS), named);
        assertFalse(plain.contains(PLAIN_PASS), plain);
        assertFalse(plain.contains(NAMED_PASS), plain);
        // the useful parts ARE there
        assertTrue(named.contains("[db] unitdb -> database.unitdb.*"), named);
        assertTrue(named.contains("user=unit_user"), named);
        assertTrue(named.contains(NAMED_HOST), named);
        assertTrue(plain.contains("[db] other -> database.* (fallback)"), plain);
    }

    @Test
    public void urlEmbeddedCredentialsAreRedactedInTheLogLine() {
        set("db.url", "jdbc:postgresql://h.example:5432/x?user=embedded_u&password=embedded-pw-55");

        String line = DbRouting.resolve("whatever").describe();

        assertFalse(line.contains("embedded-pw-55"), line);
        assertFalse(line.contains("embedded_u"), line);
        assertTrue(line.contains("password=***"), line);
    }
}
