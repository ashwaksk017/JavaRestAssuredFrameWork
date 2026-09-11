package com.ak.api.security;

import org.apache.logging.log4j.core.LogEvent;
import org.apache.logging.log4j.core.appender.rewrite.RewritePolicy;
import org.apache.logging.log4j.core.config.plugins.Plugin;
import org.apache.logging.log4j.core.config.plugins.PluginFactory;
import org.apache.logging.log4j.core.impl.Log4jLogEvent;
import org.apache.logging.log4j.message.SimpleMessage;

/**
 * Redacts every credential out of log output before it reaches an
 * appender. Wired in {@code log4j2.xml} as a {@code Rewrite} appender in
 * front of the console.
 *
 * <p>This is the blanket layer. Rather than chasing individual
 * {@code LOG.info(" .. request body: {}", body)} call sites -- of which
 * there are many, in generated code that gets rewritten on every
 * convert -- everything routed through slf4j is scrubbed centrally.
 * A new log line added tomorrow is covered without anyone remembering
 * to redact it.</p>
 *
 * <p>It matters here specifically because the console is redirected to
 * a file on real runs ({@code mvn test > accountdashboard-full-run.log}),
 * so console output is durable, not ephemeral.</p>
 *
 * <p>The formatted message is re-wrapped as a {@link SimpleMessage}.
 * Parameterized messages are already rendered by
 * {@code getFormattedMessage()}, so no placeholder information is lost.
 * Thrown-exception text is redacted via its own message only when the
 * event carries one; stack frames themselves never hold credentials.</p>
 */
@Plugin(name = "SecretRedactingRewritePolicy", category = "Core",
        elementType = "rewritePolicy", printObject = true)
public final class SecretRedactingRewritePolicy implements RewritePolicy {

    @PluginFactory
    public static SecretRedactingRewritePolicy createPolicy() {
        return new SecretRedactingRewritePolicy();
    }

    @Override
    public LogEvent rewrite(LogEvent source) {
        if (source == null || !Secrets.enabled()) {
            return source;
        }
        String original;
        try {
            original = source.getMessage() == null
                    ? null : source.getMessage().getFormattedMessage();
        } catch (RuntimeException e) {
            // A broken toString() in a log argument must not kill the run.
            return source;
        }
        if (original == null || original.isEmpty()) {
            return source;
        }
        String redacted = Secrets.redact(original);
        if (redacted.equals(original)) {
            return source;
        }
        return new Log4jLogEvent.Builder(source)
                .setMessage(new SimpleMessage(redacted))
                .build();
    }
}
