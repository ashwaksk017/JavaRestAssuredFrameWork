package com.hi.api.tests.framework;

import static org.testng.Assert.assertEquals;
import static org.testng.Assert.assertNotNull;
import static org.testng.Assert.assertTrue;

import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.Map;

import javax.xml.parsers.DocumentBuilderFactory;

import org.testng.IConfigurationListener;
import org.testng.IHookable;
import org.testng.ISuiteListener;
import org.testng.annotations.Test;
import org.w3c.dom.Document;
import org.w3c.dom.Element;
import org.w3c.dom.NodeList;

import com.hi.api.tests.RunLogListener;
import com.hi.api.tests.StoryTestBase;

/**
 * Guard: the full run log (target/b2b-full-run.log) holds everything needed
 * to troubleshoot a run -- asked for, "no more gaps in our logging".
 *
 * <ul>
 *   <li>what is printed to the console, every HTTP call outside a converted
 *       step and every soft check of a story test reach the FILE (and only
 *       the file: written to the console as well, the console copy would be
 *       copied again);</li>
 *   <li>the file goes through the credential redactor;</li>
 *   <li>the run's start and end, and setup methods that failed, are heard;</li>
 *   <li>a story test's soft assertions fail the test itself (so its failure
 *       block is written), and its checks are logged one by one.</li>
 * </ul>
 */
public class RunLogCompletenessGuardTest {

    private static Document log4j() throws Exception {
        try (InputStream in = RunLogCompletenessGuardTest.class.getClassLoader().getResourceAsStream("log4j2.xml")) {
            assertNotNull(in, "log4j2.xml is on the test classpath");
            return DocumentBuilderFactory.newInstance().newDocumentBuilder().parse(in);
        }
    }

    @Test(groups = {"guard"})
    public void consoleCallsAndChecksGoToTheRunLogFileOnly() throws Exception {
        Document d = log4j();
        Map<String, Element> loggers = new HashMap<>();
        NodeList ls = d.getElementsByTagName("Logger");
        for (int i = 0; i < ls.getLength(); i++) {
            Element e = (Element) ls.item(i);
            loggers.put(e.getAttribute("name"), e);
        }
        for (String name : new String[] {"CONSOLE", "com.hi.api.http", "com.hi.api.check"}) {
            Element e = loggers.get(name);
            assertNotNull(e, "log4j2.xml declares the " + name + " logger");
            assertEquals(e.getAttribute("additivity"), "false", name + " is not repeated to the console");
            NodeList refs = e.getElementsByTagName("AppenderRef");
            assertEquals(refs.getLength(), 1, name + " has one appender");
            assertEquals(((Element) refs.item(0)).getAttribute("ref"), "File", name + " writes the run log file");
        }
    }

    @Test(groups = {"guard"})
    public void theRunLogFileIsRedacted() throws Exception {
        Document d = log4j();
        NodeList rw = d.getElementsByTagName("Rewrite");
        boolean fileRedacted = false;
        for (int i = 0; i < rw.getLength(); i++) {
            Element e = (Element) rw.item(i);
            if ("File".equals(e.getAttribute("name"))
                    && e.getElementsByTagName("SecretRedactingRewritePolicy").getLength() == 1) {
                fileRedacted = true;
            }
        }
        assertTrue(fileRedacted, "the File appender rewrites every line through SecretRedactingRewritePolicy");
    }

    @Test(groups = {"guard"})
    public void theRunsStartEndAndFailedSetupMethodsAreHeard() throws Exception {
        assertTrue(ISuiteListener.class.isAssignableFrom(RunLogListener.class), "RUN START / RUN END");
        assertTrue(IConfigurationListener.class.isAssignableFrom(RunLogListener.class),
                "SETUP METHOD FAILED / SKIPPED");
        try (InputStream in = getClass().getClassLoader()
                .getResourceAsStream("META-INF/services/org.testng.ITestNGListener")) {
            assertNotNull(in, "the TestNG listener registration is there");
            String listed = new String(in.readAllBytes(), StandardCharsets.UTF_8);
            assertTrue(listed.contains("com.hi.api.tests.RunLogListener"), "RunLogListener is registered");
        }
    }

    @Test(groups = {"guard"})
    public void aStoryTestFailsOnItsSoftChecksAndLogsEachOne() throws Exception {
        assertTrue(IHookable.class.isAssignableFrom(StoryTestBase.class),
                "StoryTestBase flushes its soft assertions as part of the test (IHookable)");
        assertNotNull(StoryTestBase.class.getDeclaredMethod("newSoftAssert"),
                "StoryTestBase gives a SoftAssert that logs each check");
    }
}
