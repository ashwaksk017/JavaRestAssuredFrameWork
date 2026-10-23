package com.hi.api.tests;

import java.io.ByteArrayOutputStream;
import java.io.OutputStream;
import java.io.PrintStream;
import java.nio.charset.Charset;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Everything printed to the console during a run -- System.out and
 * System.err, by the framework (the [Config] banner, [Retry] attempts, data
 * rows switched off, the failure digest) or by any library -- is also
 * written to the full run log (target/b2b-full-run.log).
 *
 * <p>Asked for: no gaps in the run log. Those lines were printed, not
 * logged, so they reached the Maven console and never the file a run is
 * read from afterwards. Copying the console here, once, covers every such
 * line -- including ones added later -- instead of each call site.</p>
 *
 * <p>The console still gets every byte, as before. The copy goes to the
 * {@code CONSOLE} logger, which writes to the file only (log4j2.xml,
 * additivity off): written to the console it would be copied again. A
 * per-thread guard stops that loop even if the configuration does not.
 * Lines are kept apart per thread, so parallel tests do not splice each
 * other's lines. Never throws: a log line must not be why a test fails.</p>
 */
final class ConsoleToRunLog {

    private static final Logger COPY = LoggerFactory.getLogger("CONSOLE");
    private static final ThreadLocal<Boolean> COPYING = ThreadLocal.withInitial(() -> Boolean.FALSE);
    private static volatile boolean installed;

    private ConsoleToRunLog() {
    }

    /** Copy System.out and System.err into the run log from now on (once a JVM). */
    static synchronized void install() {
        if (installed) {
            return;
        }
        installed = true;
        try {
            System.setOut(copying(System.out, false));
            System.setErr(copying(System.err, true));
        } catch (RuntimeException ignored) {
            // the console stays as it was
        }
    }

    private static PrintStream copying(PrintStream console, boolean err) {
        Charset cs = Charset.defaultCharset();       // what the console stream was writing
        ThreadLocal<ByteArrayOutputStream> line = ThreadLocal.withInitial(ByteArrayOutputStream::new);
        OutputStream both = new OutputStream() {
            @Override
            public void write(int b) {
                console.write(b);
                take((byte) b);
            }

            @Override
            public void write(byte[] b, int off, int len) {
                console.write(b, off, len);
                for (int i = off; i < off + len; i++) {
                    take(b[i]);
                }
            }

            @Override
            public void flush() {
                console.flush();
            }

            private void take(byte c) {
                if (c == '\n') {
                    ByteArrayOutputStream buf = line.get();
                    String text = new String(buf.toByteArray(), cs);
                    buf.reset();
                    copy(text, err);
                } else if (c != '\r') {
                    line.get().write(c);
                }
            }
        };
        return new PrintStream(both, true, cs);
    }

    private static void copy(String text, boolean err) {
        if (text.isEmpty() || COPYING.get()) {
            return;
        }
        COPYING.set(Boolean.TRUE);
        try {
            if (err) {
                COPY.warn("{}", text);
            } else {
                COPY.info("{}", text);
            }
        } catch (RuntimeException ignored) {
            // never the reason a test fails
        } finally {
            COPYING.set(Boolean.FALSE);
        }
    }
}
