"""Every placeholder the generated code sends must be resolvable from ctx.

`check_ctx_dataflow.py` covers ONE substitution channel: `ctxGet(ctx, KEY)`.
There are two more, and a value lost in either reaches the server as literal
text rather than a value:

  1. `#KEY#` placeholders, resolved at runtime by `PlaceholderResolver`
     against ctx. They live in request-body templates on disk, in query and
     path values, and in CSV cells. An unresolvable one is transmitted
     verbatim -- the server sees `#Properties_Email#` as an email address.
  2. `${...}` ReadyAPI refs the translator failed to convert. Those should
     never survive into generated Java at all.

`PlaceholderResolver.replaceIfKnown` tries several spellings of a key before
giving up, so a placeholder counts as satisfiable when ANY of them is
produced somewhere. This mirrors that list -- it must stay in step with it.

    python tools/check_substitutions.py [--root .] [--top 25]

Exit 1 when a placeholder has no possible producer, or a raw ${...} survives.
"""
from __future__ import annotations

import argparse
import collections
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from check_ctx_dataflow import (  # noqa: E402
    _bodies, _lp, _read, _walk_java, _METHOD_HEAD_RX, _BOOTSTRAP_HEAD_RX,
    _SETUP_HEAD_RX, method_effects,
)

HASH_RX = re.compile(r"#([A-Za-z0-9_.-]+)#")
DOLLAR_RX = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_.#\-\[\]'$ ]*)\}")
# Not placeholders: emitted comments and the RawRequest bookkeeping keys.
_COMMENT_RX = re.compile(r"^\s*(//|\*|/\*)")


def candidate_keys(raw: str) -> set:
    """Every ctx spelling PlaceholderResolver will try for `#raw#`.

    Must mirror `replaceIfKnown` AND its `Properties.`-prefixed fallback --
    a bare `#propcode#` resolves from `Properties.propcode`, which is how a
    ReadyAPI Properties-step value reaches ctx. Omitting that fallback
    reported resolvable placeholders as broken.
    """
    keys = {raw, raw.replace("_", "."), raw.replace(".", "_"),
            raw.replace("_", "-")}
    if "_" in raw:
        ns, tail = raw.split("_", 1)
        keys.add(f"{ns}.{tail}")
        keys.add(f"{ns}.{tail.replace('_', '.')}")
    return keys


def properties_fallback_keys(raw: str) -> set:
    """The `Properties.`-prefixed spellings the resolver tries last.

    Checked ONLY against exactly-produced keys and CSV columns -- never
    against the `Properties.` wildcard that `seedFromRow` registers. That
    wildcard means "whatever columns this CSV has", so matching a
    synthesized `Properties.<anything>` against it would make every
    placeholder look satisfiable and turn this check into a rubber stamp.
    """
    return {f"Properties.{raw}", f"Properties_{raw}",
            f"Properties.{raw.replace('_', '.')}"}


def collect_producers(root: str) -> tuple:
    """(exact keys, wildcard prefixes) written anywhere in the tree."""
    exact: set = set()
    wild: set = set()
    for path in _walk_java(os.path.join(root, "src/main/java")):
        text = _read(path)
        if "putExtracted" not in text and "seedFromRow" not in text:
            continue
        for head in (_METHOD_HEAD_RX, _BOOTSTRAP_HEAD_RX, _SETUP_HEAD_RX):
            for _n, body in _bodies(text, head, group=0):
                w, _r, _s = method_effects(body)
                for k in w:
                    (wild if k.endswith("*") else exact).add(
                        k[:-1] if k.endswith("*") else k)
    return exact, wild


def config_keys(root: str) -> set:
    """Keys resolvable from configuration rather than ctx.

    `SalesforceAuth` fills `#salesforce_assertion#` from
    `Config.get(...)`, and the OAuth templates carry `#client_id#` /
    `#c_sec#`. Those never appear in ctx, so treating ctx as the only
    producer reported them as broken.
    """
    keys: set = set()
    cfg = _read(os.path.join(root, "src/main/java/com/ak/api/config/Config.java"))
    keys |= set(re.findall(r'LEGACY_ALIASES\.put\("([^"]+)"', cfg))
    keys |= set(re.findall(r'Config\.get\("([^"]+)"', cfg))
    for path in _walk_java(os.path.join(root, "src/main/java")):
        keys |= set(re.findall(r'Config\.get\("([^"]+)"', _read(path)))
    import json
    try:
        with open(_lp(os.path.join(root, "src/main/resources",
                                   "program_configuration.json")),
                  encoding="utf-8") as fh:
            def walk(o, prefix=""):
                if isinstance(o, dict):
                    for k, v in o.items():
                        keys.add(k)
                        keys.add(f"{prefix}{k}" if prefix else k)
                        walk(v, f"{prefix}{k}.")
            walk(json.load(fh))
    except (OSError, ValueError):
        pass
    return keys


def csv_columns(root: str) -> set:
    """CSV headers are seeded into ctx under the seedFromRow prefixes."""
    cols: set = set()
    for dirpath, _d, files in os.walk(_lp(os.path.join(root, "src/test/resources"))):
        for fn in files:
            if not fn.endswith(".csv"):
                continue
            head = _read(os.path.join(dirpath, fn)).split("\n", 1)[0]
            for c in head.split(","):
                c = c.strip().strip('"')
                if c:
                    cols.add(c)
    return cols


def satisfiable(raw: str, exact: set, wild: set, cols: set) -> bool:
    for k in candidate_keys(raw):
        if k in exact:
            return True
        for w in wild:
            if k.startswith(w):
                return True
        # seedFromRow puts every CSV column under its prefix, so a bare
        # column name is reachable as `<prefix><column>`.
        tail = k.split(".")[-1]
        if tail in cols or k in cols:
            return True
    # Last resort, and deliberately NOT wildcard-matched -- see the docstring
    # on properties_fallback_keys.
    for k in properties_fallback_keys(raw):
        if k in exact or k.split(".")[-1] in cols:
            return True
    return False


def scan(root: str) -> tuple:
    exact, wild = collect_producers(root)
    # Config-backed keys are producers too, just not via ctx.
    exact |= config_keys(root)
    cols = csv_columns(root)
    stats = collections.Counter()
    unresolved = collections.Counter()
    dollars = collections.Counter()
    where: dict = {}

    def note(raw, src):
        unresolved[raw] += 1
        where.setdefault(raw, src)

    # ---- request-body templates on disk
    #
    # These live under src/main/resources/templates -- the emitted
    # `Templates.X` constants are paths into it. Scanning only
    # src/test/resources saw 3 unrelated fixtures and none of the actual
    # request bodies, which is where an unresolved placeholder does the
    # most damage: it is transmitted to the server as literal text.
    tpl_roots = [os.path.join(root, "src/main/resources/templates"),
                 os.path.join(root, "src/test/resources")]
    for tpl_root in tpl_roots:
      for dirpath, _d, files in os.walk(_lp(tpl_root)):
        for fn in files:
            if not fn.endswith(".json"):
                continue
            text = _read(os.path.join(dirpath, fn))
            stats["templates"] += 1
            for m in HASH_RX.finditer(text):
                stats["placeholders"] += 1
                if not satisfiable(m.group(1), exact, wild, cols):
                    note(m.group(1), "template " + fn)

    # ---- generated Java: string literals only
    for path in _walk_java(os.path.join(root, "src/main/java")):
        text = _read(path)
        if "#" not in text and "${" not in text:
            continue
        stats["java_files"] += 1
        for line in text.split("\n"):
            if _COMMENT_RX.match(line):
                continue
            for lit in re.findall(r'"((?:[^"\\]|\\.)*)"', line):
                for m in HASH_RX.finditer(lit):
                    stats["placeholders"] += 1
                    if not satisfiable(m.group(1), exact, wild, cols):
                        note(m.group(1), os.path.basename(path))
                for m in DOLLAR_RX.finditer(lit):
                    dollars[m.group(1)] += 1
    return unresolved, dollars, stats, where


def load_baseline(root: str) -> tuple:
    """Placeholders / refs already traced to a cause, with that reason.

    Anything NOT listed fails the build. A value that stops resolving is a
    value the server receives as literal text.
    """
    path = os.path.join(root, "tools", "substitutions_baseline.json")
    try:
        import json
        with open(_lp(path), encoding="utf-8") as fh:
            data = json.load(fh)
        return (data.get("accepted_placeholders") or {},
                data.get("accepted_dollar_refs") or {})
    except (OSError, ValueError):
        return {}, {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--ignore-baseline", action="store_true",
                    help="report everything, triaged or not")
    args = ap.parse_args()

    unresolved, dollars, stats, where = scan(args.root)
    ok_ph, ok_ref = ({}, {}) if args.ignore_baseline else load_baseline(args.root)
    new_ph = {k: n for k, n in unresolved.items() if k not in ok_ph}
    new_ref = {k: n for k, n in dollars.items() if k not in ok_ref}
    print(f"templates scanned        : {stats['templates']}")
    print(f"java files scanned       : {stats['java_files']}")
    print(f"placeholders seen        : {stats['placeholders']}")
    print(f"unresolvable placeholders: {sum(unresolved.values())} "
          f"across {len(unresolved)} key(s)")
    print(f"surviving ${{...}} refs    : {sum(dollars.values())} "
          f"across {len(dollars)} ref(s)")

    if ok_ph or ok_ref:
        print(f"  accepted (triaged)     : "
              f"{sum(n for k, n in unresolved.items() if k in ok_ph)} placeholder(s), "
              f"{sum(n for k, n in dollars.items() if k in ok_ref)} ref(s)")

    if new_ph:
        print("\nNEW placeholders with NO producer "
              "(sent to the server verbatim):")
        for raw, n in collections.Counter(new_ph).most_common(args.top):
            print(f"  {n:5d}x  #{raw}#   first in {where[raw]}")
    if new_ref:
        print("\nNEW untranslated ReadyAPI refs surviving into Java:")
        for raw, n in collections.Counter(new_ref).most_common(args.top):
            print(f"  {n:5d}x  ${{{raw}}}")
    if not new_ph and not new_ref:
        print("\nEvery placeholder resolves and no ${...} ref survived,"
              "\nexcept the triaged cases in tools/substitutions_baseline.json.")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
