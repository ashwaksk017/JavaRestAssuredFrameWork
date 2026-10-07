"""Check every generated request body against the API's own contract.

The server keeps telling us things the spec already knew. `Invalid JSON
Parameter Value` on a date we sent as `2026-12-17 00:00:00`; `String must
match the specified regular expression` on a roomTypeCode; a required
field carrying the literal `null`. Each of those cost a full run against a
real environment to learn, and each is a property of the payload that can
be read off the spec at convert time, before anything is sent.

REPORTS, NEVER REWRITES. That distinction is the whole design:

  * The converter's contract is fidelity to ReadyAPI. The recording is
    evidence of what the server actually accepted; the spec is a document,
    and documents drift from deployments. Where they disagree, the
    recording is the better witness and a human should decide.
  * Silently "correcting" a payload to match a spec would hide that the
    source project is stale -- and a silent substitution is exactly what
    made `null` cost weeks. Replacing one silent substitution with a
    cleverer one is not progress.

So a finding here means "look at this", not "this was fixed for you".

The spec is optional and gitignored. `src/main/resources/openapi/` ships
with a README and no spec, because a vendor contract does not belong in a
public repo. With no spec present this check passes and says so.
"""

import argparse
import glob
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from check_substitutions import _lp  # noqa: E402  (path set above)


def _load_spec(root: str):
    """(spec dict, path) for the first spec on disk, or (None, "")."""
    pat = os.path.join(root, "src/main/resources/openapi", "*")
    for f in sorted(glob.glob(pat)):
        if not f.lower().endswith((".yaml", ".yml", ".json")):
            continue
        try:
            text = io.open(_lp(f), encoding="utf-8", errors="replace").read()
            if f.lower().endswith(".json"):
                return json.loads(text), f
            import yaml
            return yaml.safe_load(text), f
        except ImportError:
            return None, "pyyaml-missing"
        except Exception:
            continue
    return None, ""


def _norm(p: str) -> str:
    """Path shape, so {guestId} and {id} compare equal."""
    return re.sub(r"\{[^}]+\}", "{}", (p or "").rstrip("/").lower())


def operations(spec: dict) -> dict:
    """{(method, normalised path): operation}"""
    out = {}
    for path, ops in (spec.get("paths") or {}).items():
        for verb, op in (ops or {}).items():
            if isinstance(op, dict) and verb.lower() in (
                    "get", "post", "put", "patch", "delete"):
                out[(verb.lower(), _norm(path))] = op
    return out


def _deref(spec: dict, node):
    """Follow one $ref. Deliberately one level: a cycle is not worth
    chasing for a report, and a schema that needs two hops still yields
    its required-field list at the first."""
    if isinstance(node, dict) and "$ref" in node:
        ref = node["$ref"]
        if ref.startswith("#/"):
            cur = spec
            for part in ref[2:].split("/"):
                cur = (cur or {}).get(part)
                if cur is None:
                    return {}
            return cur
    return node or {}


def body_schema(spec: dict, op: dict):
    for prm in op.get("parameters") or []:
        if isinstance(prm, dict) and prm.get("in") == "body":
            return _deref(spec, prm.get("schema"))
    rb = op.get("requestBody")
    if isinstance(rb, dict):
        for _ct, media in (rb.get("content") or {}).items():
            if isinstance(media, dict) and media.get("schema"):
                return _deref(spec, media["schema"])
    return None


_PLACEHOLDER = re.compile(r"^[#%@][A-Za-z0-9_.\-]+[#%@]$")


def _is_placeholder(v) -> bool:
    return isinstance(v, str) and bool(_PLACEHOLDER.match(v.strip()))


def check_body(spec, schema, body, path=""):
    """[(severity, message)] for one payload against one schema."""
    out = []
    schema = _deref(spec, schema)
    if not isinstance(schema, dict) or not isinstance(body, dict):
        return out
    props = _deref(spec, schema.get("properties") or {})
    required = schema.get("required") or []

    for name in required:
        if name not in props and props:
            # The schema requires a field it does not itself define. That
            # is the SPEC's problem, not the payload's, and saying
            # "you omitted it" would be a lie -- there is nothing to send.
            #
            # Not hypothetical: PartnerOwnerInfo requires `email` while
            # defining `emailAddress`, and every request we generate sends
            # emailAddress. An auto-fixer would have "corrected" 17 correct
            # templates by adding a field the API has no name for. This is
            # the whole reason the check reports instead of rewriting.
            out.append(("spec-inconsistent",
                        "%s%s is in `required` but not in `properties` -- the "
                        "spec contradicts itself; the payload cannot satisfy "
                        "it" % (path, name)))
        elif name not in body:
            out.append(("required", "%s%s is required and absent" % (path, name)))
        elif body[name] in (None, "", "null"):
            out.append(("required",
                        "%s%s is required and carries %r" % (path, name, body[name])))

    for name, value in body.items():
        sub = _deref(spec, props.get(name))
        if not isinstance(sub, dict) or not sub:
            if props and name not in props and not schema.get(
                    "additionalProperties", True) is True:
                out.append(("unknown", "%s%s is not in the schema" % (path, name)))
            continue
        # A placeholder's VALUE is unknown at convert time. Its presence is
        # not: an unresolved one would send the string `null`, which the
        # required-check above already reports.
        if _is_placeholder(value):
            continue
        typ, fmt = sub.get("type"), sub.get("format")
        enum = sub.get("enum")
        if enum and isinstance(value, (str, int, float)) and value not in enum:
            out.append(("enum", "%s%s=%r is not one of %s"
                        % (path, name, value, enum[:6])))
        if typ == "string" and isinstance(value, str):
            if fmt == "date" and not re.match(r"^\d{4}-\d{2}-\d{2}$", value):
                out.append(("format",
                            "%s%s=%r is not a `date` (YYYY-MM-DD)"
                            % (path, name, value)))
            if fmt == "date-time" and "T" not in value:
                out.append(("format",
                            "%s%s=%r is not a `date-time`" % (path, name, value)))
            pat = sub.get("pattern")
            if pat:
                try:
                    if not re.search(pat, value):
                        out.append(("pattern", "%s%s=%r does not match %s"
                                    % (path, name, value, pat)))
                except re.error:
                    pass
        if typ in ("integer", "number") and isinstance(value, str) \
                and not _is_placeholder(value):
            try:
                float(value)
            except ValueError:
                out.append(("type", "%s%s=%r is not a %s"
                            % (path, name, value, typ)))
        if typ == "object" and isinstance(value, dict):
            out.extend(check_body(spec, sub, value, path + name + "."))
        if typ == "array" and isinstance(value, list) and value:
            items = _deref(spec, sub.get("items") or {})
            if isinstance(value[0], dict):
                out.extend(check_body(spec, items, value[0],
                                      path + name + "[0]."))
    return out


def call_sites(root: str) -> list:
    """[(method, normalised path, template basename)] from the emitted specs."""
    rx = re.compile(
        r'\.(get|post|put|patch|delete)\("([^"]+)"\).*?'
        r'\.template\(Templates\.([A-Z0-9_]+)\)', re.S)
    out = []
    # `<Suite>Specs<N>.java` since the suite prefix; `Specs<N>.java` in a
    # tree converted before it. A *Phases.java is never one of them.
    _specs_rx = re.compile(r"^\w*?Specs\d+\.java$")
    for f in glob.glob(os.path.join(root, "src/main/java/**/cases/*Specs*.java"),
                       recursive=True):
        if not _specs_rx.match(os.path.basename(f)):
            continue
        src = io.open(_lp(f), encoding="utf-8", errors="replace").read()
        for m in rx.finditer(src):
            out.append((m.group(1), _norm(m.group(2)), m.group(3)))
    return out


def template_paths(root: str) -> dict:
    """{TEMPLATES_CONSTANT: file path on disk}"""
    out = {}
    rx = re.compile(r'String\s+([A-Z0-9_]+)\s*=\s*"([^"]+)"')
    for f in glob.glob(os.path.join(root, "src/main/java/**/Templates.java"),
                       recursive=True):
        for m in rx.finditer(io.open(_lp(f), encoding="utf-8",
                                     errors="replace").read()):
            out[m.group(1)] = os.path.join(root, "src/main/resources",
                                           m.group(2))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--spec", default="", help="override the spec path")
    ap.add_argument("--max", type=int, default=40)
    args = ap.parse_args()

    if args.spec:
        text = io.open(_lp(args.spec), encoding="utf-8",
                       errors="replace").read()
        import yaml
        spec, where = (json.loads(text) if args.spec.lower().endswith(".json")
                       else yaml.safe_load(text)), args.spec
    else:
        spec, where = _load_spec(args.root)

    if where == "pyyaml-missing":
        print("a spec is present but pyyaml is not installed "
              "(pip install pyyaml) -- nothing checked")
        return 0
    if not spec:
        print("no OpenAPI spec in src/main/resources/openapi/ -- nothing to "
              "check.\nA vendor contract is gitignored by design; drop one in "
              "to enable this.")
        return 0

    ops = operations(spec)
    tpls = template_paths(args.root)
    sites = call_sites(args.root)

    checked = matched = 0
    findings = []
    for method, path, const in sites:
        op = ops.get((method, path))
        if op is None:
            continue
        matched += 1
        schema = body_schema(spec, op)
        tpl = tpls.get(const)
        if not schema or not tpl or not os.path.isfile(_lp(tpl)):
            continue
        try:
            body = json.loads(io.open(_lp(tpl), encoding="utf-8",
                                      errors="replace").read())
        except ValueError:
            continue
        checked += 1
        for sev, msg in check_body(spec, schema, body):
            findings.append((sev, method.upper(), os.path.basename(tpl), msg))

    print("spec            : %s" % os.path.basename(where))
    print("operations      : %d" % len(ops))
    print("generated calls : %d  (matched to an operation: %d)"
          % (len(sites), matched))
    print("bodies checked  : %d" % checked)

    if not findings:
        print("\nevery checked request body satisfies the schema it is sent to")
        return 0

    uniq = sorted(set(findings))
    print("\n%d finding(s). These are REPORTS, not fixes -- the recording is "
          "evidence of\nwhat the server accepted, the spec is a document, and "
          "where they disagree a\nhuman decides which is stale:\n" % len(uniq))
    by = {}
    for sev, mth, tpl, msg in uniq:
        by.setdefault(sev, []).append((mth, tpl, msg))
    for sev in sorted(by):
        print("  [%s] %d" % (sev, len(by[sev])))
        for mth, tpl, msg in by[sev][:args.max]:
            print("      %-6s %-42s %s" % (mth, tpl[:42], msg))
    # Reporting check: findings do not fail the build, because a spec that
    # is one release ahead of the deployment would block every convert.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
