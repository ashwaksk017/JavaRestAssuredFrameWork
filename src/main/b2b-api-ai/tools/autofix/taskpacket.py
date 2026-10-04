"""What the agent is told -- Phase 3 of the loop.

A packet is a failure, the smallest thing that reproduces it, the rules
the fix has to satisfy, and the shape of the answer. It is deliberately
NOT "here is the repo, good luck":

  * the emitter is ~20,000 lines. Handing it over wastes the context on
    code that has nothing to do with the failure, and an agent that
    cannot see the relevant 40 lines invents a plausible fix somewhere
    else.
  * the rules are the ones Phase 1 enforces. Stating them up front turns
    most rejections into non-events; discovering them from a rejection
    costs a whole attempt.
  * the answer has to be checkable. A predicted blast radius can be
    compared against the manifest, so "this touches one case" becomes a
    claim with a verdict instead of a reassurance.

Nothing here talks to an agent. It builds text, so it can be read,
diffed and tested without a key.
"""
from __future__ import annotations

import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import failure_record as fr  # noqa: E402
import invariants as inv  # noqa: E402

ROOT = fr.ROOT
EXCERPT_CONTEXT = 30
MAX_EXCERPT_LINES = 240
MAX_FILE_INLINE_LINES = 400

# `path:line` or `path:[line,col]` (javac) anywhere in checker output.
_LINE_REF_RX = re.compile(
    r"((?:src|tools)/[A-Za-z0-9_./\-]+\.(?:java|py))[:(]\[?(\d+)")


def line_refs(lines: list[str]) -> dict[str, list[int]]:
    """{repo-relative path: [line numbers]} named in the failure output."""
    out: dict[str, list[int]] = {}
    for l in lines:
        for m in _LINE_REF_RX.finditer(l.replace("\\", "/")):
            out.setdefault(m.group(1), [])
            n = int(m.group(2))
            if n not in out[m.group(1)]:
                out[m.group(1)].append(n)
    return out


def _read(rel: str) -> list[str] | None:
    p = os.path.join(ROOT, rel.replace("/", os.sep))
    if not os.path.isfile(p):
        return None
    try:
        return io.open(p, encoding="utf-8", errors="replace").read().splitlines()
    except OSError:
        return None


def excerpt(rel: str, lines: list[int], context: int = EXCERPT_CONTEXT) -> str:
    """Numbered windows around the referenced lines, merged when they touch."""
    src = _read(rel)
    if src is None:
        return f"(missing: {rel})"
    if not lines:
        if len(src) <= MAX_FILE_INLINE_LINES:
            body = "\n".join(f"{i + 1:6d}  {l}" for i, l in enumerate(src))
            return f"--- {rel} (whole file, {len(src)} lines)\n{body}"
        # Too big to inline and nothing to anchor a window on. An outline
        # is strictly better than the first 400 lines, which is what a
        # truncation would have handed over.
        return outline(rel)
    wins: list[tuple[int, int]] = []
    for n in sorted(lines):
        lo, hi = max(1, n - context), min(len(src), n + context)
        if wins and lo <= wins[-1][1] + 1:
            wins[-1] = (wins[-1][0], max(wins[-1][1], hi))
        else:
            wins.append((lo, hi))
    out = [f"--- {rel} ({len(src)} lines)"]
    shown = 0
    for lo, hi in wins:
        if shown >= MAX_EXCERPT_LINES:
            out.append("        ... (excerpt budget reached)")
            break
        out.append(f"    @@ {lo}-{hi}")
        for i in range(lo - 1, hi):
            out.append(f"{i + 1:6d}  {src[i]}")
            shown += 1
    return "\n".join(out)


# --- anchoring on symbols, not on named files -------------------------
# Measured on a real failure: the implicated-file list for a unit check
# held the test file and two README.md paths -- the READMEs only because
# the converter logs `[ra_converter] SKIP (exists): <path>` while the test
# runs. None of the three was the fix site. What WAS useful sat in the
# failing test's NAME: `test_param_binding_sig_reports_row_vs_ref` names
# `_param_binding_sig`, which is the function that was wrong.
_SEARCH_GLOBS = ("tools/ra_converter", "tools/autofix", "tools")
_NOISE_FILES = (".md", ".txt", ".json", ".csv", ".xml", ".properties")
_FAIL_TEST_RX = re.compile(r"\b(?:FAIL|ERROR|FAILED)[: ]+\s*(test_\w+)")
_defs_cache: dict[str, tuple[str, int]] | None = None


def defs_index() -> dict[str, tuple[str, int]]:
    """{symbol: (repo-relative path, line)} for python defs/classes.

    Only the tool tree -- that is where a converter fix lands. Built once
    and small: a few dozen files.
    """
    global _defs_cache
    if _defs_cache is None:
        idx: dict[str, tuple[str, int]] = {}
        rx = re.compile(r"^\s*(?:def|class)\s+(\w+)")
        seen: set[str] = set()
        for root in _SEARCH_GLOBS:
            base = os.path.join(ROOT, root.replace("/", os.sep))
            if not os.path.isdir(base):
                continue
            for dirpath, dirs, names in os.walk(base):
                dirs[:] = [d for d in dirs if d != "__pycache__"]
                for n in sorted(names):
                    if not n.endswith(".py"):
                        continue
                    rel = os.path.relpath(os.path.join(dirpath, n),
                                          ROOT).replace("\\", "/")
                    if rel in seen:
                        continue
                    seen.add(rel)
                    for i, line in enumerate(_read(rel) or []):
                        m = rx.match(line)
                        if m and m.group(1) not in idx:
                            idx[m.group(1)] = (rel, i + 1)
        _defs_cache = idx
    return _defs_cache


def symbols_from(lines: list[str]) -> list[tuple[str, str, int]]:
    """(symbol, path, line) the failure output points at, best first.

    A failing test name is walked from the longest prefix down, on
    underscore boundaries: `param_binding_sig_reports_row_vs_ref` ->
    ... -> `param_binding_sig`, tried both bare and `_`-prefixed. That is
    what turns a test name into the function under test.
    """
    idx = defs_index()
    out: list[tuple[str, str, int]] = []
    seen: set[str] = set()

    def take(name: str) -> bool:
        for cand in (name, "_" + name):
            if cand in idx and cand not in seen:
                seen.add(cand)
                out.append((cand, *idx[cand]))
                return True
        return False

    for l in lines:
        for m in _FAIL_TEST_RX.finditer(l):
            parts = m.group(1)[len("test_"):].split("_")
            for n in range(len(parts), 1, -1):
                if take("_".join(parts[:n])):
                    break
    for l in lines:
        for word in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]{5,}\b", l):
            if word.startswith("test_"):
                continue
            take(word)
    return out


def outline(rel: str) -> str:
    """Top-level definitions, for a file too large to inline.

    An agent that knows `_normalize_jdbc_query` exists can ask for it.
    One handed 20,000 lines reads the first 2,000 and guesses.
    """
    src = _read(rel)
    if src is None:
        return f"(missing: {rel})"
    rx = (re.compile(r"^\s{0,4}(?:def|class)\s+(\w+)") if rel.endswith(".py")
          else re.compile(r"^\s{0,4}(?:public|private|protected|static|final|\s)*"
                          r"(?:class|interface|enum|void|[A-Z]\w*)\s+(\w+)\s*[({]"))
    hits = [(i + 1, m.group(1)) for i, l in enumerate(src) if (m := rx.match(l))]
    head = f"--- {rel} outline ({len(src)} lines, {len(hits)} definitions)"
    body = "\n".join(f"{n:6d}  {name}" for n, name in hits[:400])
    return f"{head}\n{body}"


RULES = """\
HARD RULES (a diff that breaks one of these is rejected before it runs,
by tools/autofix/invariants.py -- run it yourself to check):

 1. Do not edit generated output. Everything under
    src/main/java/com/hi/api/support/<suite>/, src/test/.../imported/,
    src/test/resources/csv/, src/main/resources/templates/ is rewritten by
    the next convert. A fix there looks like it worked and then vanishes.
    Fix the emitter under tools/ra_converter/ instead.
 2. These files are author-editable but their SOURCE lives elsewhere, so
    patch BOTH or neither: %s
    -> src is tools/ra_converter/framework/<name> when that file exists,
       otherwise tools/ra_converter/ra_converter.py.
 3. Never edit tools/autofix/baseline.json, invariants.py or mutations.py.
    Never edit pom.xml, .gitignore, .github/ or converter.config.json.
 4. Never touch src/main/java/com/hi/api/rest/manual/ or
    src/test/java/com/hi/api/tests/manual/.
 5. Do not delete a test, a Check(...) entry, or an assertion. If an
    assertion is wrong, change what it asserts and say why.
 6. No literal credentials. Values come from program_configuration.json
    via Config.get(). No new hostnames: this repository is public.
 7. If your change is confined to tools/check_*.py, it makes the failure
    go away by changing what is measured. That is held until a negative
    fixture proves the check still FAILS on the tree it was built to
    catch (tools/autofix/mutations.py).

HOW THIS CODEBASE EXPECTS A FIX

 * A parity gap is OUR bug until the ReadyAPI XML says otherwise. Do not
   explain a failure away as a mistake by the project's author without
   checking the source XML.
 * Prefer a positive discriminator over a broad one, fail closed, and make
   one layered change at a time.
 * The recurring bug here is a fix whose reach is wider than intended:
   merged cases share one cluster, so a change meant for one case can
   silently rewrite the body of every case merged with it. State the blast
   radius you expect; it is measured against a hash manifest.
""" % ", ".join(sorted(fr.AUTHOR_EDITABLE_BASENAMES))

ANSWER = """\
ANSWER FORMAT -- exactly these sections, in this order:

DIAGNOSIS
  What is actually wrong, in two or three sentences. Name the file and
  function. If the evidence does not identify a cause, say so and stop
  here; a guess costs more than a question.

SIDE
  emitter | checker | framework | unknown
  `checker` means the check is wrong and the generated tree is right. Say
  which, because the two are verified differently.

PATCH
  A unified diff against the repository root, applicable with `git apply`.
  Source files only.

BLAST RADIUS
  How many generated files you expect to change, and in which suites.
  Write `none` if the fix changes no generated output. This is compared
  against a before/after hash manifest.

EVIDENCE
  The command that should now pass, and what it printed before.
"""


def build(record: dict, doc: dict | None = None,
          attempt: int = 1, previous: str = "") -> str:
    """The prompt for one failure."""
    name = record.get("name", "?")
    loc = record.get("locate") or {}
    files = record.get("implicated_files") or {}
    refs = line_refs(record.get("salient", []) + record.get("output_tail", []))

    parts: list[str] = []
    parts.append(
        f"A gate check is failing in the ReadyAPI -> Java/REST Assured "
        f"converter. Fix the cause.\n\n"
        f"CHECK      {name}  ({record.get('kind', '?')})\n"
        f"GUARANTEES {record.get('why', '')}\n"
        f"REPRO      {record.get('repro', '')}\n"
        f"FINGERPRINT {record.get('salient_fingerprint', '')[:12]}")
    if doc and doc.get("git", {}).get("head"):
        parts[-1] += f"\nCOMMIT     {doc['git']['head']}"
    if loc.get("suites"):
        parts[-1] += f"\nSUITES     {', '.join(loc['suites'])}"
    if loc.get("cases"):
        parts[-1] += (f"\nCASES      {', '.join(loc['cases'][:6])}"
                      + (f" (+{len(loc['cases']) - 6})" if len(loc["cases"]) > 6 else ""))

    parts.append("WHAT THE CHECK PRINTED\n" + "\n".join(
        "  " + l for l in (record.get("salient") or record.get("output_tail") or [])[:40]))

    if files.get("generated"):
        parts.append(
            "GENERATED FILES IT NAMES (read these for evidence; do NOT edit "
            "them)\n" + "\n".join("  " + p for p in files["generated"][:20]))

    ex: list[str] = []
    done: set[str] = set()
    out_lines = (record.get("salient") or []) + (record.get("output_tail") or [])

    # 1. Symbols the failure names. The strongest anchor, and the only one
    #    that reaches the emitter when the output never mentions the file.
    by_file: dict[str, list[int]] = {}
    for sym, rel, line in symbols_from(out_lines)[:6]:
        by_file.setdefault(rel, []).append(line)
    for rel, nums in by_file.items():
        ex.append(f"(named by the failure: "
                  f"{', '.join(s for s, r, _ in symbols_from(out_lines) if r == rel)})\n"
                  + excerpt(rel, nums))
        done.add(rel)

    # 2. Explicit path:line references.
    for rel, nums in list(refs.items())[:6]:
        if rel in done or fr.classify_path(rel) == "generated":
            continue
        ex.append(excerpt(rel, nums))
        done.add(rel)

    # 3. Implicated files, minus the ones that are only in the list because
    #    the converter logged them (`SKIP (exists): .../README.md`).
    for rel in files.get("source", []):
        if rel in done or rel.lower().endswith(_NOISE_FILES):
            continue
        src = _read(rel)
        if src is None:
            continue
        ex.append(excerpt(rel, []) if len(src) <= MAX_FILE_INLINE_LINES
                  else outline(rel))
        done.add(rel)
        if len(done) >= 6:
            break
    if ex:
        parts.append("RELEVANT SOURCE\n\n" + "\n\n".join(ex))

    if attempt > 1:
        parts.append(
            f"THIS IS ATTEMPT {attempt}. The previous attempt was rejected:\n"
            f"{previous.strip()}\n"
            f"Do not resubmit it unchanged. If the rejection shows the "
            f"approach is wrong, change the approach.")

    parts.append(RULES)
    parts.append(ANSWER)
    return "\n\n".join(parts) + "\n"


def from_records(path: str, check: str | None = None, attempt: int = 1,
                 previous: str = "") -> tuple[str, dict]:
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    cands = [c for c in doc.get("checks", []) if c.get("status") == "fail"]
    if check:
        cands = [c for c in cands if c.get("name") == check]
    if not cands:
        raise SystemExit(
            f"no failing check{f' named {check!r}' if check else ''} in {path}. "
            f"An accepted or skipped check is not a task.")
    # Cheapest first: a check that runs in a second is a cheaper loop than
    # one that needs a compile.
    cands.sort(key=lambda c: (c.get("policy") == "prompt", c.get("duration_s", 0)))
    return build(cands[0], doc, attempt, previous), cands[0]


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--records", required=True)
    ap.add_argument("--check", help="which failing check (default: cheapest)")
    ap.add_argument("--out", help="write the packet here instead of stdout")
    args = ap.parse_args(argv)
    text, rec = from_records(args.records, args.check)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        io.open(args.out, "w", encoding="utf-8", newline="\n").write(text)
        print(f"{rec['name']}: {len(text)} chars, "
              f"{len(text.splitlines())} lines -> {args.out}")
        if rec.get("policy") == "prompt":
            print("NOTE: this check is policy `prompt` -- it is never fixed "
                  "without a human choosing. Packet written for review only.")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
