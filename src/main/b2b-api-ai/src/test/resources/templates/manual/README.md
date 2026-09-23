# Hand-written request bodies

Bodies for manual tests live here. Reference one by its CLASSPATH path,
which drops the `src/test/resources/` prefix:

    .using(Template.ofPath("b2b722Enroll", "templates/manual/b2b722_enroll.json"))
    .enrollOwner()

`using(...)` applies to the NEXT phase only.

## Why not src/main/resources/templates/

That tree is converter output. It is gitignored, it is renumbered on every
convert, and `--clean` deletes it. `Template.of(case, step)` resolves through
the generated index and so moves with it; `Template.ofPath` points at a file
you own, which a reconvert cannot touch.

## This directory IS committed -- keep secrets out

Unlike `src/test/resources/csv/`, this path is not gitignored, so anything
here is published with the repo. Put no password, token, or real customer
value in a body. Reference a CSV column instead:

    { "password": "#enroll_password#" }

and hold the value in the per-method CSV, which is gitignored. `#key#`
resolves against the CSV row and ctx, so an id an earlier phase captured can
be referenced by name too.
