package com.hi.api.support;

// ra_converter-framework-rev: 3
// Bumped whenever this bundled file changes. The converter
// SKIPS author-editable files that already exist, so without a
// revision it cannot tell an author's edit from a copy left by
// an older converter -- and an in-method change (no new symbol)
// would silently never reach existing trees.

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
            return (String) Class.forName("com.hi.api.templates." + suite + ".Templates")
                    .getField(name)
                    .get(null);
        } catch (ReflectiveOperationException e) {
            throw new IllegalStateException(
                    "Templates." + name + " for suite " + suite, e);
        }
    }
}
