// =============================================================================
// DbRouting -- per-database connection resolution for imported JDBC steps
// -----------------------------------------------------------------------------
// A ReadyAPI JDBC step carries its own connection string. The converter keeps
// only the database NAME from it (the path segment of the JDBC URL, e.g.
// `groupmaintenance`) and emits `Db.forDatabase("groupmaintenance")`. This
// class turns that name into connection settings from the active env block:
//
//     database.<name>.host | port | schema | username | password
//     (optional: database.<name>.scheme, database.<name>.driver)
//
// When no `database.<name>.*` key is set at all, the plain `database.*` keys
// (already derived into db.url / db.user / db.password / db.driver by Config)
// are used -- so a suite whose steps name the same database the env block
// already points at behaves exactly as before, and so does every un-named
// call. One line is logged per name per JVM saying which block was chosen.
// The password is never part of that line.
// =============================================================================

package com.hi.api.db;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import com.hi.api.config.Config;

public final class DbRouting {

    private static final Logger LOG = LoggerFactory.getLogger(DbRouting.class);

    /** Names already announced, so each database is explained once per JVM. */
    private static final Set<String> ANNOUNCED = ConcurrentHashMap.newKeySet();

    private DbRouting() {
    }

    /** Resolved connection settings for one database name. */
    public static final class Target {
        private final String name;
        private final String url;
        private final String user;
        private final String pass;
        private final String driver;
        private final boolean named;
        private final List<String> missing;

        Target(String name, String url, String user, String pass, String driver,
               boolean named, List<String> missing) {
            this.name = name;
            this.url = url;
            this.user = user;
            this.pass = pass;
            this.driver = driver;
            this.named = named;
            this.missing = Collections.unmodifiableList(new ArrayList<>(missing));
        }

        public String name()    { return name; }
        public String url()     { return url; }
        public String user()    { return user; }
        public String pass()    { return pass; }
        public String driver()  { return driver; }

        /** True when `database.<name>.*` supplied the connection. */
        public boolean named()  { return named; }

        /** `database.<name>.<key>` entries that were blank (named block only),
         *  or the named keys that were present when the block was too partial
         *  to use and the plain `database.*` block was taken instead. */
        public List<String> missing() { return missing; }

        /** True when a JDBC URL is available (named or fallback). */
        public boolean isConfigured() {
            return url != null && !url.isBlank();
        }

        /** The block this name resolved to -- the one line that is logged. */
        public String source() {
            return named ? "database." + name + ".*" : "database.* (fallback)";
        }

        /**
         * One human line, safe for a log file: name, block, redacted URL,
         * user, and any missing keys. Never the password.
         */
        public String describe() {
            StringBuilder sb = new StringBuilder("[db] ").append(name)
                    .append(" -> ").append(source());
            if (isConfigured()) {
                sb.append("  url=").append(Db.redactUrlCredentials(url));
                sb.append("  user=").append(
                        (user == null || user.isBlank()) ? "(none)" : user);
            } else {
                sb.append("  (no db.url -- JDBC steps will be skipped)");
            }
            if (!missing.isEmpty()) {
                sb.append("  missing: ").append(String.join(", ", missing));
            }
            return sb.toString();
        }
    }

    /**
     * Resolve the connection for {@code name}.
     *
     * <ul>
     *   <li>blank name: the plain block, nothing logged (identical to
     *       {@link Db#configured()}).</li>
     *   <li>{@code database.<name>.host} and {@code .schema} present: the
     *       named block. Blank {@code username}/{@code password} are listed
     *       as missing but the named block is still used, so a wrong
     *       password fails as an auth error on the RIGHT server instead of
     *       as a missing relation on the wrong one.</li>
     *   <li>some named keys present but not host+schema: WARN naming what is
     *       missing, then the plain block.</li>
     *   <li>no named key at all: the plain block.</li>
     * </ul>
     */
    public static Target resolve(String name) {
        String n = name == null ? "" : name.trim();
        if (n.isEmpty()) {
            return plain("", Collections.emptyList());
        }
        String prefix = "database." + n + ".";
        String host   = Config.get(prefix + "host", null);
        String port   = Config.get(prefix + "port", null);
        String schema = Config.get(prefix + "schema", null);
        String user   = Config.get(prefix + "username", null);
        String pass   = Config.get(prefix + "password", null);
        String scheme = Config.get(prefix + "scheme", null);
        String driver = Config.get(prefix + "driver", null);

        boolean anyNamed = present(host) || present(port) || present(schema)
                || present(user) || present(pass) || present(scheme) || present(driver);
        Target t;
        if (present(host) && present(schema)) {
            List<String> missing = new ArrayList<>();
            if (!present(user)) missing.add(prefix + "username");
            if (!present(pass)) missing.add(prefix + "password");
            String url = String.format("jdbc:%s://%s:%s/%s",
                    present(scheme) ? scheme : "postgresql",
                    host,
                    present(port) ? port : "5432",
                    schema);
            t = new Target(n, url, user, pass,
                    present(driver) ? driver : Config.get("db.driver", null),
                    true, missing);
        } else if (anyNamed) {
            List<String> missing = new ArrayList<>();
            if (!present(host))   missing.add(prefix + "host");
            if (!present(schema)) missing.add(prefix + "schema");
            t = plain(n, missing);
        } else {
            t = plain(n, Collections.emptyList());
        }
        announceOnce(t);
        return t;
    }

    private static Target plain(String name, List<String> missing) {
        return new Target(name,
                Config.get("db.url", null),
                Config.get("db.user", null),
                Config.get("db.password", null),
                Config.get("db.driver", null),
                false, missing);
    }

    private static void announceOnce(Target t) {
        if (!ANNOUNCED.add(t.name())) {
            return;
        }
        if (t.missing().isEmpty()) {
            LOG.info(t.describe());
        } else {
            LOG.warn(t.describe());
        }
    }

    /** Forget which names were announced (tests). */
    static void resetAnnouncements() {
        ANNOUNCED.clear();
    }

    private static boolean present(String s) {
        return s != null && !s.isBlank();
    }
}
