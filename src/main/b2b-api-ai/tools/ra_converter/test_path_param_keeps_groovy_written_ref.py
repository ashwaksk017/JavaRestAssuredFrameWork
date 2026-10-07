"""A path parameter fed by a datasheet reference must use the live value.

partialgoalregression / goal10_Post_rateplancreate_400, run 7:

    [availability] groovyScript_availPropertyAndDates -> LONME on 2026-11-09 (inventory 163 > 9 ...)
    [subst] ... column=generatedDatesAndProps_arrivalDate | value=2026-11-09
    -> GET  /props/MILHI/groups   (step=GET_Groups_SingleProp)
    <- {"propCode":"MILHI","roomRates":[]}

The URL's `{propCode}` is `${DataSource#propCode}`; the workbook cell
behind it is `${generatedDatesAndProps#hcrs}`, and generatedDatesAndProps
is the Properties step the availability Groovy writes. The CSV keeps the
cell as a reference (`DataSource_propCode = #generatedDatesAndProps_hcrs#`)
-- nothing was baked there -- but the spec bound the path argument to
`Ref.ctx("DataSource.propCode")`, and that resolves datasheet -> row, where
the row ALSO carries `generatedDatesAndProps.hcrs = MILHI`: the Properties
step's saved XML snapshot, emitted as the seed column for the no-search
case. The row answered with the snapshot. (The query and body routes go
through the merged row, where ctx wins -- which is why the arrivalDate on
the same request was live while the propCode beside it was stale.)

Fix: `datasheet_ref_live_key` follows the reference. When every cell of
the datasheet column is the one reference `${P#f}`, P is a Properties
step of this case, and a Groovy that runs before the REST step writes
P.f, the path argument is bound to `${P#f}` -- the translator renders it
`ctxGet(ctx, "P.f")`, the key the search publishes. With no search in the
run, the if-absent seed has already put the saved value under that key,
so the no-search case reads what it always read.

    python tools/ra_converter/test_path_param_keeps_groovy_written_ref.py
"""

import csv
import glob
import inspect
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))

sys.path.insert(0, HERE)

import ra_converter as rc  # noqa: E402

_EMITTER = next(v for v in vars(rc).values()
                if isinstance(v, type) and hasattr(v, "_render_rest_step_body"))

SEARCH = '''
def generatedDatesPropertyVal =
    testRunner.testCase.getTestStepByName("generatedDatesAndProps")
for (int i = 0; i < 4; i++) {
    generatedDatesPropertyVal.setPropertyValue("hcrs", hcrs[j])
    generatedDatesPropertyVal.setPropertyValue("arrivalDate", arrivalDates[i])
}
'''


# ------------------------------------------------------- builders

def _groovy(name, script=SEARCH):
    s = rc.GroovyStep.__new__(rc.GroovyStep)
    s.step_name, s.script = name, script
    return s


def _props(name, **values):
    s = rc.PropertiesStep.__new__(rc.PropertiesStep)
    s.step_name, s.properties = name, dict(values)
    return s


def _ds(name):
    s = rc.DataSourceStep.__new__(rc.DataSourceStep)
    s.step_name, s.ds_type, s.columns = name, "Excel", ["propCode", "peakRoom"]
    return s


def _rest(name, path="/props/{propCode}/groups", **path_params):
    s = rc.RestStep.__new__(rc.RestStep)
    s.step_name, s.resource_path = name, path
    s.path_params = dict(path_params)
    return s


def _case(steps, rows, loop_source="DataSource", lookup=None):
    c = rc.TestCase.__new__(rc.TestCase)
    c.name, c.steps = "synthetic", list(steps)
    c.ds_loops = [{"loop": "Loop", "source": loop_source, "target": "x"}]
    c.ds_rows = list(rows)
    c.ds_lookup = dict(lookup or {})
    return c


REF_ROWS = [{"propCode": "#generatedDatesAndProps_hcrs#", "peakRoom": "12"},
            {"propCode": "#generatedDatesAndProps_hcrs#", "peakRoom": "19"},
            {"propCode": "#generatedDatesAndProps_hcrs#", "peakRoom": "24"}]

GET = _rest("GET_Groups_SingleProp", propCode="${DataSource#propCode}",
            peakRooms="${DataSource#peakRoom}")
GOAL10 = [_ds("DataSource"), _groovy("groovyScript_availPropertyAndDates"),
          _props("generatedDatesAndProps", hcrs="MILHI", arrivalDate="2026-11-01"),
          GET, _props("groupid"), _rest("POST_Groups_SingleProp")]


def _bind(case, step=GET, expr="${DataSource#propCode}"):
    return rc.datasheet_ref_live_key(case, step, expr)


# ------------------------------------------------------- the rule

def test_the_goal10_shape_binds_to_the_groovy_written_key():
    assert _bind(_case(GOAL10, REF_ROWS)) == "${generatedDatesAndProps#hcrs}"


def test_the_bound_key_renders_as_the_dotted_ctx_read():
    java = rc.soapui_expr_to_java("${generatedDatesAndProps#hcrs}", {})
    assert 'ctxGet(ctx, "generatedDatesAndProps.hcrs")' in java, java


def test_a_literal_column_is_left_alone():
    rows = [{"propCode": "MILHI", "peakRoom": "12"},
            {"propCode": "LONME", "peakRoom": "19"}]
    assert _bind(_case(GOAL10, rows)) is None


def test_a_column_that_is_not_uniformly_the_reference_is_left_alone():
    rows = [{"propCode": "#generatedDatesAndProps_hcrs#", "peakRoom": "12"},
            {"propCode": "MILHI", "peakRoom": "19"}]
    assert _bind(_case(GOAL10, rows)) is None, (
        "a row with its own literal must keep reading the datasheet")


def test_a_reference_into_a_field_no_groovy_writes_is_left_alone():
    rows = [{"propCode": "#generatedDatesAndProps_pcrs#", "peakRoom": "12"}]
    assert _bind(_case(GOAL10, rows)) is None


def test_a_groovy_that_runs_after_the_request_does_not_count():
    steps = [_ds("DataSource"),
             _props("generatedDatesAndProps", hcrs="MILHI"),
             GET, _groovy("late")]
    assert _bind(_case(steps, REF_ROWS)) is None


def test_a_reference_into_a_step_this_case_does_not_have_is_left_alone():
    steps = [_ds("DataSource"), _groovy("search"), GET]
    assert _bind(_case(steps, REF_ROWS)) is None


def test_a_stale_case_that_does_not_contain_the_step_is_refused():
    other = _rest("GET_Groups_SingleProp", propCode="${DataSource#propCode}")
    assert _bind(_case(GOAL10, REF_ROWS), step=other) is None


def test_a_plain_properties_ref_is_not_a_datasheet_ref():
    assert _bind(_case(GOAL10, REF_ROWS), expr="${Properties#propCode}") is None
    assert _bind(_case(GOAL10, REF_ROWS), expr="LONME") is None
    assert _bind(_case(GOAL10, REF_ROWS), expr="${DataSource#propCode}x") is None


def test_a_lookup_datasource_is_followed_too():
    steps = [_ds("Lookup"), _groovy("search"),
             _props("generatedDatesAndProps", hcrs="MILHI"),
             _rest("GET", propCode="${Lookup#propCode}")]
    case = _case(steps, [], loop_source="Other",
                 lookup={"Lookup": {"propCode": "#generatedDatesAndProps_hcrs#"}})
    assert _bind(case, step=steps[-1], expr="${Lookup#propCode}") == (
        "${generatedDatesAndProps#hcrs}")


def test_a_properties_step_name_with_underscores_is_split_correctly():
    script = ('testRunner.testCase.getTestStepByName("Gen_Dates_Props")'
              '.setPropertyValue("hcrs", x)')
    steps = [_ds("DataSource"), _groovy("search", script),
             _props("Gen_Dates_Props", hcrs="MILHI"), GET]
    rows = [{"propCode": "#Gen_Dates_Props_hcrs#"}]
    assert _bind(_case(steps, rows)) == "${Gen_Dates_Props#hcrs}"


# ------------------------------------------------ wired into the emitter

def test_the_rest_renderer_consults_the_rule_before_binding_a_path_arg():
    src = inspect.getsource(_EMITTER._render_rest_step_body)
    i = src.find("datasheet_ref_live_key(")
    j = src.find("path_args.append(soapui_expr_to_java(expr")
    assert 0 <= i < j, "the path-arg loop must consult datasheet_ref_live_key first"
    assert "path-param-bound-to-live-key" in src, "the rebinding must be recorded"


# ------------------------------------------- the real generated tree

def _open(p):
    if os.name == "nt" and not p.startswith("\\\\?\\"):
        p = "\\\\?\\" + os.path.abspath(p)
    return io.open(p, encoding="utf-8", errors="replace", newline="")


def _suite_dirs():
    return sorted(glob.glob(os.path.join(
        ROOT, "src", "main", "java", "com", "hi", "api", "support", "*", "cases")))


def test_no_path_arg_reads_a_datasheet_cell_that_is_a_groovy_written_ref():
    """For every converted suite present: a case whose CSV column
    `DataSource_<col>` is, in every row, one `#P_f#` reference while the
    same CSV carries the snapshot column `P.f` and the suite's Hooks seed
    P.f if-absent (the emitter's own record that a Groovy writes it) must
    not bind a path argument to `Ref.ctx("DataSource.<col>")`."""
    bad = []
    converter_mtime = os.path.getmtime(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "ra_converter.py"))
    for cases_dir in _suite_dirs():
        suite = os.path.basename(os.path.dirname(cases_dir))
        # A suite generated by an OLDER converter is not evidence either
        # way: a tree where only some suites were reconverted (the user
        # scoped a run to one XML) would fail here on the stale ones.
        newest_spec = max((os.path.getmtime(p) for p in
                           glob.glob(os.path.join(cases_dir, "Specs*.java"))),
                          default=0)
        if newest_spec < converter_mtime:
            continue
        hooks = "".join(io.open(h, encoding="utf-8", errors="replace").read()
                        for h in glob.glob(os.path.join(cases_dir, "Hooks*.java")))
        written = set()
        for m in re.finditer(r'seedFromRowIfAbsent\(ctx, row, "([^"]+)\.", ([^;]*)\);', hooks):
            for f in re.findall(r'"([^"]+)"', m.group(2)):
                written.add(m.group(1) + "." + f)
        if not written:
            continue
        specs = {}
        for sp in glob.glob(os.path.join(cases_dir, "Specs*.java")):
            text = io.open(sp, encoding="utf-8", errors="replace").read()
            cls = os.path.basename(sp)[:-5]
            for m in re.finditer(r"static PhaseSpec (spec\d+)\(\) \{(.*?)\n    \}", text, re.S):
                specs[cls + "::" + m.group(1)] = m.group(2)
        case_specs = {}
        for ph in glob.glob(os.path.join(cases_dir, "*Phases.java")):
            text = io.open(ph, encoding="utf-8", errors="replace").read()
            for m in re.finditer(r'register\("[^"]+", "([^"]+)"\)(.*?);', text, re.S):
                case_specs.setdefault(m.group(1), []).extend(
                    re.findall(r"(Specs\d+)::(spec\d+)", m.group(2)))
        tests_root = os.path.join(ROOT, "src", "test", "java", "com", "hi", "api",
                                  "tests", "imported", suite)
        csv_root = os.path.join(ROOT, "src", "test", "resources", "csv", suite)
        for tj in glob.glob(os.path.join(tests_root, "**", "*.java"), recursive=True):
            text = io.open(tj, encoding="utf-8", errors="replace").read()
            rel = os.path.relpath(os.path.dirname(tj), tests_root)
            cls = os.path.basename(tj)[:-5]
            for m in re.finditer(r'@XrayTest\("([^"]+)"\).*?public void (\w+)\(', text, re.S):
                case_id, method = m.group(1), m.group(2)
                cp = os.path.join(csv_root, rel, cls, method + ".csv")
                if not os.path.exists(cp):
                    continue
                with _open(cp) as fh:
                    rows = list(csv.DictReader(fh))
                if not rows:
                    continue
                refs = {}
                for col in rows[0]:
                    if not col.startswith("DataSource_"):
                        continue
                    vals = {(r.get(col) or "").strip() for r in rows}
                    if len(vals) != 1:
                        continue
                    rm = re.match(r"^#([A-Za-z0-9_.\-]+)#$", vals.pop())
                    if not rm:
                        continue
                    tok = rm.group(1)
                    i = tok.rfind("_")
                    snap = tok[:i] + "." + tok[i + 1:] if i > 0 else tok
                    if snap in rows[0] and snap in written:
                        refs[col[len("DataSource_"):]] = snap
                if not refs:
                    continue
                for scls, sid in case_specs.get(case_id, []):
                    body = specs.get(scls + "::" + sid, "")
                    for col, snap in refs.items():
                        if 'Ref.ctx("DataSource.%s")' % col in body:
                            bad.append("%s/%s %s::%s binds a path arg to DataSource.%s "
                                       "whose every cell is #%s# (live key %s)"
                                       % (suite, case_id, scls, sid, col,
                                          snap.replace(".", "_"), snap))
    assert not bad, (
        "a path argument resolves datasheet -> row -> saved snapshot instead of "
        "the value the Groovy produced:" + "".join("\n  " + b for b in bad))


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok  " + name)
            passed += 1
    print("%d passed" % passed)
