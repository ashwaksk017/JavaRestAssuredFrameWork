# Converter input

Drop your ReadyAPI / SoapUI project XMLs here, then run the converter.

```powershell
python tools/ra_converter/ra_converter.py `
    --input tools/ra_converter/input `
    --output . `
    --package-root com.ak.api `
    --clean `
    --max-name-len 40 `
    --no-cursor-assist
```

Convert **every** XML in one process. Fluent-phase votes are computed
across all suites before any test is emitted, so a body two suites render
identically lands on one shared `ScenarioSteps` method instead of two
copies. Converting suites separately loses that, and — because the run
allocates fluent names from a single index — a suite converted on its own
after a converter change rebuilds the shared layer from that suite alone
and strands the others.

The XMLs themselves are **not** committed. They are customer project
exports: endpoint paths, request/response schemas, test-case IDs and
hard-coded identifiers. `tools/ra_converter/.gitignore` keeps this
directory's contents local; only this README is tracked.

Everything the converter emits is likewise gitignored — generated tests,
request-body templates, CSVs, `_audit/`, `_flows/`, `fluent_catalog.json`.
See the repository `.gitignore` for the full list.
