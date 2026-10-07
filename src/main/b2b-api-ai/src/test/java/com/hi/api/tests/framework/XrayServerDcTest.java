package com.hi.api.tests.framework;

import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.testng.Assert;
import org.testng.annotations.AfterMethod;
import org.testng.annotations.Test;

import com.hi.api.xray.XrayClient;
import com.hi.api.xray.XrayResult;

/**
 * Publishing to Xray Server / Data Center, and picking the right flavour.
 *
 * <p>This client only ever spoke Cloud: {@code POST /api/v2/authenticate}
 * with a client id and secret. This org's Jira is self-hosted and its
 * configured route is {@code /rest/raven/1.0/import/execution}, so result
 * publishing had never worked here -- the config was right and the client
 * was for the other product.</p>
 *
 * <p>Nothing here touches a network. {@code XrayClient} takes a transport,
 * so what WOULD have been sent -- url, bearer, body -- is assertable, which
 * is the only way to cover a Server/DC path without a Server/DC to hit.</p>
 */
public class XrayServerDcTest {

    /** Captures one POST instead of making it. */
    private static final class Recorder implements XrayClient.Transport {
        final List<String> urls = new ArrayList<>();
        final List<Map<String, String>> headers = new ArrayList<>();
        final List<Object> bodies = new ArrayList<>();
        int status = 200;
        String reply = "{}";

        @Override
        public XrayClient.HttpReply post(String url, Map<String, String> h,
                                         Object body) {
            urls.add(url);
            headers.add(h == null ? new LinkedHashMap<>() : new LinkedHashMap<>(h));
            bodies.add(body);
            return new XrayClient.HttpReply(status, reply);
        }
    }

    private final List<String> touched = new ArrayList<>();

    private void set(String k, String v) {
        touched.add(k);
        System.setProperty(k, v);
    }

    @AfterMethod(alwaysRun = true)
    public void clearProps() {
        // System properties are process-wide and Config reads them first.
        // Leaving one set would change every later test in the suite.
        for (String k : touched) {
            System.clearProperty(k);
        }
        touched.clear();
    }

    private static List<XrayResult> oneResult() {
        List<XrayResult> rs = new ArrayList<>();
        rs.add(new XrayResult("PROJ-101", XrayResult.Status.PASSED, null, 12L));
        return rs;
    }

    private static Map<String, Object> bodyOf(Recorder r, int i) {
        @SuppressWarnings("unchecked")
        Map<String, Object> b = (Map<String, Object>) r.bodies.get(i);
        return b;
    }

    // --- picking the flavour ------------------------------------------
    @Test(groups = {"guards"})
    public void aSelfHostedJiraWithAPatIsServerDc() {
        Assert.assertEquals(
                XrayClient.detectFlavour(null, "https://jira.yourorg.com",
                        "pat-123", null, null),
                XrayClient.Flavour.SERVER);
    }

    @Test(groups = {"guards"})
    public void theCloudHostIsCloudEvenWithAPatLyingAround() {
        // The host is checked before the credentials so a tree holding both
        // kinds of key resolves the same way on every run.
        Assert.assertEquals(
                XrayClient.detectFlavour(null, "https://xray.cloud.getxray.app",
                        "pat-123", null, null),
                XrayClient.Flavour.CLOUD);
    }

    @Test(groups = {"guards"})
    public void aKeyPairOnASelfHostedHostIsStillCloud() {
        Assert.assertEquals(
                XrayClient.detectFlavour(null, "https://jira.yourorg.com",
                        null, "id", "secret"),
                XrayClient.Flavour.CLOUD);
    }

    @Test(groups = {"guards"})
    public void anExplicitSettingBeatsEveryGuess() {
        Assert.assertEquals(
                XrayClient.detectFlavour("server", "https://xray.cloud.getxray.app",
                        null, "id", "secret"),
                XrayClient.Flavour.SERVER);
        Assert.assertEquals(
                XrayClient.detectFlavour("cloud", "https://jira.yourorg.com",
                        "pat", null, null),
                XrayClient.Flavour.CLOUD);
        for (String alias : new String[]{"dc", "DataCenter", "SERVER"}) {
            Assert.assertEquals(
                    XrayClient.detectFlavour(alias, "https://xray.cloud.getxray.app",
                            null, null, null),
                    XrayClient.Flavour.SERVER, alias);
        }
    }

    @Test(groups = {"guards"})
    public void nothingConfiguredStaysOnTheHistoricalDefault() {
        Assert.assertEquals(
                XrayClient.detectFlavour(null, null, null, null, null),
                XrayClient.Flavour.CLOUD);
    }

    // --- the Server / DC publish --------------------------------------
    @Test(groups = {"guards"})
    public void serverDcSendsThePatStraightThroughWithNoAuthCall() {
        set("xray.enabled", "true");
        set("xray.baseUrl", "https://jira.yourorg.com");
        set("xray.token", "pat-abc");
        Recorder rec = new Recorder();

        boolean ok = new XrayClient(rec).importResults(
                oneResult(), Instant.now(), Instant.now());

        Assert.assertTrue(ok);
        Assert.assertEquals(rec.urls.size(), 1,
                "Server/DC must make exactly ONE call -- there is no token "
                        + "exchange: " + rec.urls);
        Assert.assertEquals(rec.urls.get(0),
                "https://jira.yourorg.com/rest/raven/1.0/import/execution");
        Assert.assertEquals(rec.headers.get(0).get("Authorization"),
                "Bearer pat-abc");
    }

    @Test(groups = {"guards"})
    public void cloudStillExchangesKeysForAJwtFirst() {
        set("xray.enabled", "true");
        set("xray.baseUrl", "https://xray.cloud.getxray.app");
        set("xray.clientId", "id");
        set("xray.clientSecret", "secret");
        Recorder rec = new Recorder();
        rec.reply = "\"jwt-xyz\"";          // Xray returns a bare quoted string

        boolean ok = new XrayClient(rec).importResults(
                oneResult(), Instant.now(), Instant.now());

        Assert.assertTrue(ok);
        Assert.assertEquals(rec.urls.size(), 2, rec.urls.toString());
        Assert.assertTrue(rec.urls.get(0).endsWith("/api/v2/authenticate"));
        Assert.assertTrue(rec.urls.get(1).endsWith("/api/v2/import/execution"));
        // The quotes Xray wraps the JWT in must not reach the header.
        Assert.assertEquals(rec.headers.get(1).get("Authorization"),
                "Bearer jwt-xyz");
    }

    @Test(groups = {"guards"})
    public void theConfiguredRouteWins() {
        // xray_api_config.route is where this repo's Server/DC route already
        // lived, so it is honoured without anyone re-entering it.
        set("xray.enabled", "true");
        set("xray.baseUrl", "https://jira.yourorg.com");
        set("xray.token", "pat");
        set("xray.importPath", "/rest/raven/2.0/import/execution");
        Recorder rec = new Recorder();
        new XrayClient(rec).importResults(oneResult(), Instant.now(), Instant.now());
        Assert.assertEquals(rec.urls.get(0),
                "https://jira.yourorg.com/rest/raven/2.0/import/execution");
    }

    @Test(groups = {"guards"})
    public void serverDcCarriesAProjectOnlyWhenItHasNoExecutionKey() {
        set("xray.enabled", "true");
        set("xray.baseUrl", "https://jira.yourorg.com");
        set("xray.token", "pat");
        set("xray.projectKey", "PROJ");
        Recorder rec = new Recorder();
        new XrayClient(rec).importResults(oneResult(), Instant.now(), Instant.now());

        @SuppressWarnings("unchecked")
        Map<String, Object> info = (Map<String, Object>) bodyOf(rec, 0).get("info");
        Assert.assertEquals(info.get("project"), "PROJ",
                "Server/DC needs a project to create an execution in");
    }

    @Test(groups = {"guards"})
    public void anExecutionKeyMakesTheProjectRedundant() {
        set("xray.enabled", "true");
        set("xray.baseUrl", "https://jira.yourorg.com");
        set("xray.token", "pat");
        set("xray.projectKey", "PROJ");
        set("xray.testExecutionKey", "PROJ-42");
        Recorder rec = new Recorder();
        new XrayClient(rec).importResults(oneResult(), Instant.now(), Instant.now());

        Map<String, Object> body = bodyOf(rec, 0);
        Assert.assertEquals(body.get("testExecutionKey"), "PROJ-42");
        @SuppressWarnings("unchecked")
        Map<String, Object> info = (Map<String, Object>) body.get("info");
        Assert.assertNull(info.get("project"),
                "appending a project to a named execution is at best ignored");
    }

    @Test(groups = {"guards"})
    public void theBodyShapeIsTheSameForBothFlavours() {
        set("xray.enabled", "true");
        set("xray.baseUrl", "https://jira.yourorg.com");
        set("xray.token", "pat");
        Recorder rec = new Recorder();
        new XrayClient(rec).importResults(oneResult(), Instant.now(), Instant.now());

        Map<String, Object> body = bodyOf(rec, 0);
        Assert.assertTrue(body.containsKey("info"));
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> tests =
                (List<Map<String, Object>>) body.get("tests");
        Assert.assertEquals(tests.size(), 1);
        Assert.assertEquals(tests.get(0).get("testKey"), "PROJ-101");
        Assert.assertEquals(tests.get(0).get("status"), "PASSED");
    }

    // --- refusing, and saying why -------------------------------------
    @Test(groups = {"guards"})
    public void serverDcWithNoPatIsSkippedWithAReasonThatNamesTheProduct() {
        set("xray.enabled", "true");
        set("xray.baseUrl", "https://jira.yourorg.com");
        Recorder rec = new Recorder();
        XrayClient c = new XrayClient(rec);

        Assert.assertFalse(c.isEnabled());
        Assert.assertFalse(c.importResults(oneResult(), Instant.now(), Instant.now()));
        Assert.assertTrue(rec.urls.isEmpty(), "must not call out at all");
        Assert.assertEquals(c.flavour(), XrayClient.Flavour.SERVER);
    }

    @Test(groups = {"guards"})
    public void cloudWithOnlyAPatIsSkippedRatherThanSentAsABearer() {
        // The trap this whole change exists for: a PAT is not a Cloud
        // credential, and sending it as one would 401 on every run.
        set("xray.enabled", "true");
        set("xray.baseUrl", "https://xray.cloud.getxray.app");
        set("xray.token", "pat-abc");
        Recorder rec = new Recorder();
        XrayClient c = new XrayClient(rec);

        Assert.assertEquals(c.flavour(), XrayClient.Flavour.CLOUD);
        Assert.assertFalse(c.isEnabled());
        Assert.assertTrue(rec.urls.isEmpty());
    }

    @Test(groups = {"guards"})
    public void theKillSwitchStillWinsOverEverything() {
        set("xray.baseUrl", "https://jira.yourorg.com");
        set("xray.token", "pat");
        Recorder rec = new Recorder();
        Assert.assertFalse(new XrayClient(rec).isEnabled(),
                "xray.enabled defaults to false and must stay the master switch");
        Assert.assertTrue(rec.urls.isEmpty());
    }

    @Test(groups = {"guards"})
    public void anHttpErrorIsReportedNotThrown() {
        set("xray.enabled", "true");
        set("xray.baseUrl", "https://jira.yourorg.com");
        set("xray.token", "pat");
        Recorder rec = new Recorder();
        rec.status = 401;
        rec.reply = "Unauthorized";
        Assert.assertFalse(new XrayClient(rec).importResults(
                oneResult(), Instant.now(), Instant.now()));
    }

    @Test(groups = {"guards"})
    public void aTransportBlowUpIsSwallowedBecauseAnOutageMustNotFailTheSuite() {
        set("xray.enabled", "true");
        set("xray.baseUrl", "https://jira.yourorg.com");
        set("xray.token", "pat");
        XrayClient.Transport boom = (u, h, b) -> {
            throw new RuntimeException("connection reset");
        };
        Assert.assertFalse(new XrayClient(boom).importResults(
                oneResult(), Instant.now(), Instant.now()));
    }

    @Test(groups = {"guards"})
    public void noResultsMeansNoCall() {
        set("xray.enabled", "true");
        set("xray.baseUrl", "https://jira.yourorg.com");
        set("xray.token", "pat");
        Recorder rec = new Recorder();
        Assert.assertFalse(new XrayClient(rec).importResults(
                new ArrayList<>(), Instant.now(), Instant.now()));
        Assert.assertTrue(rec.urls.isEmpty());
    }
}
