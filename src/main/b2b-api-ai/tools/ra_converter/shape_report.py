"""Print the call-shape report from fluent_catalog.json.

    python tools/ra_converter/shape_report.py [--out target/shape-report.txt]

Reads what the last converter run(s) registered (see phase_model). Safe to
paste: paths and business names only -- no hosts, ids or emails.
"""
from __future__ import annotations

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--out", default=None, help="also write the report here")
    a = p.parse_args(argv)
    from fluent_scenario import load_fluent_catalog
    from phase_model import ShapeRegistry, render_report
    cat = load_fluent_catalog()
    reg = ShapeRegistry(cat.get("shapes"))
    if not reg.data:
        print("no shapes registered yet -- run the converter first "
              "(the registry is filled as suites are converted)")
        return 1
    text = render_report(reg, phases_today=len(cat.get("phases") or {}) or None)
    print(text, end="")
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"[shape_report] written to {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
