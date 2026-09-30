"""A placeholder with no producer, next to a producer under another name.

Three bugs this year were the same bug: a value existed, a reference asked
for it, and the two spelled it differently. Nothing joined them, so the
runtime substituted its `null` fallback and the server complained about a
field whose value had never reached it.

  * a workbook cell held `${generatedDatesAndProps#arrivalDate}` while the
    CSV wanted `#generatedDatesAndProps_arrivalDate#`
  * a REST parameter was captured as `qry_<step>_<param>` while the body
    referenced `#<step>_<param>#`
  * a ctx key was written `DataSource.propCode` and read `DataSource_propCode`

Each cost a full run against a real environment to find, and each was
visible at convert time to anyone who thought to look sideways.

`check_substitutions.py` already reports WHICH placeholders resolve to
nothing. That is the hard half, and it has been reporting these all along.
What it cannot say is the useful half: *there is a producer right there,
under a name one mechanical step away.* Without that, an unresolvable
placeholder reads as "this data is genuinely missing" -- which is
sometimes true, and was the reason these reports got triaged into a
baseline instead of fixed.

So: for every unresolvable placeholder, try the mechanical variations that
have actually bitten, and name the producer if one turns up. A hit is
either a bridge worth building or a bridge that exists and the checker
does not mirror. Both are bugs; neither is data.

Deliberately NOT fuzzy. Edit distance would find something for almost any
name and this report is only worth reading if a hit means something. Every
rule below is a transformation the converter or the resolver genuinely
performs somewhere.
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import check_substitutions as CS      # noqa: E402  (path set above)


# Prefixes the converter mints when it names a captured value. Each is a
# real naming convention in the emitter, not a guess.
_PREFIXES = ("qry_", "path_", "tpl_", "datasource_", "Properties.",
             "Properties_", "expected_")


def _snake(s: str) -> str:
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s)
    return s.lower()


def variations(raw: str) -> dict:
    """{candidate name -> the rule that produced it}.

    One mechanical step from `raw`, never two: a name reachable only by
    stacking rules is not a near miss, it is a coincidence.
    """
    out: dict = {}

    def add(name, rule):
        if name and name != raw:
            out.setdefault(name, rule)

    for p in _PREFIXES:
        add(p + raw, "captured under the `%s` prefix" % p)
        if raw.startswith(p):
            add(raw[len(p):], "stored without the `%s` prefix" % p)
    add(raw.replace("_", "."), "dot spelling")
    add(raw.replace(".", "_"), "underscore spelling")
    add(raw.replace("-", "_"), "dash written as underscore")
    add(_snake(raw), "snake_case spelling")
    return out


def near_misses(root: str) -> list:
    """[(placeholder, count, where, [(producer, rule, source)])]"""
    unresolved, _dollars, _stats, where = CS.scan(root)
    exact, wild = CS.collect_producers(root)
    exact |= CS.config_keys(root)
    cols = CS.csv_columns(root)

    # Case-insensitive indexes, so a pure case difference is findable
    # without generating every casing.
    exact_ci = {k.lower(): k for k in exact}
    cols_ci = {c.lower(): c for c in cols}

    found = []
    for raw, count in sorted(unresolved.items(), key=lambda kv: -kv[1]):
        hits = []
        for cand, rule in variations(raw).items():
            if cand in exact:
                hits.append((cand, rule, "ctx producer"))
            elif cand in cols:
                hits.append((cand, rule, "CSV column"))
            else:
                low = cand.lower()
                if low in exact_ci:
                    hits.append((exact_ci[low], rule + ", case differs",
                                 "ctx producer"))
                elif low in cols_ci:
                    hits.append((cols_ci[low], rule + ", case differs",
                                 "CSV column"))
        # A pure case difference on the name itself.
        low = raw.lower()
        if low in exact_ci and exact_ci[low] != raw:
            hits.append((exact_ci[low], "case differs", "ctx producer"))
        if low in cols_ci and cols_ci[low] != raw:
            hits.append((cols_ci[low], "case differs", "CSV column"))
        if hits:
            # de-dup, keep first rule seen per producer
            seen = {}
            for name, rule, src in hits:
                seen.setdefault(name, (rule, src))
            found.append((raw, count, where.get(raw, "?"),
                          [(n, r, s) for n, (r, s) in sorted(seen.items())]))
    return found


def load_accepted(root: str) -> dict:
    path = os.path.join(root, "tools", "near_miss_accepted.json")
    try:
        with open(CS._lp(path), encoding="utf-8") as fh:
            return json.load(fh).get("accepted") or {}
    except (OSError, ValueError):
        return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--ignore-accepted", action="store_true")
    args = ap.parse_args()

    found = near_misses(args.root)
    accepted = {} if args.ignore_accepted else load_accepted(args.root)

    if not found:
        print("no unresolvable placeholder has a producer under a "
              "near-miss name")
        return 0

    new = [f for f in found if f[0] not in accepted]
    print("placeholders that resolve to nothing while a producer exists "
          "one rename away: %d (%d already triaged)"
          % (len(found), len(found) - len(new)))
    for raw, count, src, hits in found:
        tag = "" if raw not in accepted else "   [accepted: %s]" % accepted[raw]
        print("\n  %dx  #%s#   first in %s%s" % (count, raw, src, tag))
        for name, rule, kind in hits:
            print("        -> %-52s %s, %s" % (name, kind, rule))

    if new:
        print("\nEach of these is a bridge to build or a bridge the checker "
              "does not mirror.\nNeither is missing data. Fix it, or record "
              "why not in tools/near_miss_accepted.json.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
