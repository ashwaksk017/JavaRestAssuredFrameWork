package com.ak.api.support;

/**
 * Resolves {@code Templates.CONSTANT} for the suite bound on this thread
 * so framework-level {@code ScenarioSteps} can share choreography across
 * every imported suite without compiling against one suite's Templates class.
 */
public final class ImportedTemplates {

    private ImportedTemplates() {}

    public static String get(String name) {
        String suite = ImportedScenario.current().suiteName;
        if (suite == null || suite.isEmpty()) {
            throw new IllegalStateException(
                    "ImportedScenario.bind(..., suiteName) was not called");
        }
        try {
            return (String) Class.forName("com.ak.api.templates." + suite + ".Templates")
                    .getField(name)
                    .get(null);
        } catch (ReflectiveOperationException e) {
            throw new IllegalStateException(
                    "Templates." + name + " for suite " + suite, e);
        }
    }
}
