# OpenAPI / Swagger specs

Put the spec for the API under test in THIS directory.

```
src/main/resources/openapi/<YourApi>-<version>.yaml
```

Every file here is gitignored EXCEPT this README -- a spec is a vendor API
contract (endpoint paths, schemas, examples) and this repo is public. So a
clone arrives with this file and no spec. That is expected; supply your own.

## Naming: the version is a flag, not an edit

The file name carries the API version, so it changes on every vendor
release. Both readers take it from ONE key, `openapi.spec`:

| Reader | Where |
|---|---|
| the generator's `<inputSpec>` | `pom.xml`, property `openapi.spec` |
| the runtime classpath lookup | `OpenApiModels.PROGRAM_ACCOUNTS_SPEC` via `Config.get` |

So a new version is a flag, not two edits:

```
mvn clean test -Dopenapi.codegen.skip=false -Dopenapi.spec=abc.yaml
```

Change the pom default instead if the new version is permanent. Moving only
one of the two readers gives you models generated from one spec and bodies
validated against another -- a mismatch that surfaces far from its cause,
which is why they share a key.

## Generation is OFF by default

`<openapi.codegen.skip>true</openapi.codegen.skip>` is what lets a clone with
no spec here still compile. Turn it on per run with
`-Dopenapi.codegen.skip=false`, which also restores the tests that reference
the generated models.

Two uses, and only one of them needs generation:

* `OpenApiModels.as(res, ProgramAccount.class)` -- typed binding. Needs the
  models, so it needs the flag.
* `SchemaValidator.validateOpenApi(res, "ProgramAccount")` -- reads this
  YAML's `definitions` off the classpath at run time. Needs the file present,
  not generated, so it works without the flag.

Neither is required by a converted or a hand-written test: those assert with
JsonPath and a `Map<String,String>` ctx. A tree with no spec at all runs the
full suite.
