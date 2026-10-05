"""One command: Jira URL in, story packet out.

    python tools/jira/run.py --url https://jira.example.com/browse/B2B-1234
    python tools/jira/run.py --key B2B-1234 --on-duplicate skip
    python tools/jira/run.py --key B2B-1234 --api-path /rest/api/3

Chains fetch -> extract -> match -> packet, stopping at the first gate
that says stop. Everything lands in `target/jira/<KEY>/`.

WHY A CHAIN AND NOT A SINGLE STEP
---------------------------------
Each stage writes a file a person can read and argue with: the story
snapshot, the request as read from it, the verdict, the brief. A tester
who disagrees with the verdict can see the fingerprint it came from; one
who disagrees with the request can see the curl it was read out of. A
single opaque command would have hidden all four decisions behind one
answer, and the answer most worth checking here is the middle one.

Each stage is also runnable on its own, so a story that needs its
candidate written by hand can rejoin the chain at `shape_match.py`.

THE GATES, IN ORDER
-------------------
  fetch   -- no clear acceptance criteria   -> stop, exit 1
  extract -- no request could be READ       -> stop, exit 1
  match   -- a suspected duplicate          -> ask, or stop in a pipeline
  packet  -- any of the above recorded      -> packet says so on line one

A stop is never an error in the story's author. It is this tool refusing
to invent the part that is missing.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import extract  # noqa: E402
import fetch  # noqa: E402
import packet as packet_mod  # noqa: E402
import projectconfig  # noqa: E402
import shape_match  # noqa: E402
import shapes  # noqa: E402

ROOT = projectconfig.ROOT


def _rule(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--url", help="a Jira browse URL")
    g.add_argument("--key", help="an issue key, e.g. B2B-1234")
    ap.add_argument("--api-path", default=fetch.DEFAULT_API_PATH,
                    help="Jira REST base (default %(default)s; Cloud uses "
                         "/rest/api/3)")
    ap.add_argument("--max-parent-depth", type=int,
                    default=fetch.MAX_PARENT_DEPTH)
    ap.add_argument("--no-attachments", action="store_true")
    ap.add_argument("--index", default="target/shape-index.json",
                    help="shape index; rebuilt with --rebuild-index")
    ap.add_argument("--rebuild-index", action="store_true",
                    help="reconvert and re-index first. Needed after a "
                         "convert, because the index is a snapshot of what "
                         "was converted and a stale one answers for a tree "
                         "that no longer exists")
    ap.add_argument("--on-duplicate", default="prompt",
                    choices=("prompt", "skip", "create", "fail"))
    args = ap.parse_args(argv)

    # --- 1. the story ------------------------------------------------
    _rule("1/4  fetch -- the story, its parents, comments and attachments")
    fetch_argv = ["--api-path", args.api_path,
                  "--max-parent-depth", str(args.max_parent_depth)]
    fetch_argv += ["--url", args.url] if args.url else ["--key", args.key]
    if args.no_attachments:
        fetch_argv.append("--no-attachments")
    rc_fetch = fetch.main(fetch_argv)

    try:
        key, _ = fetch.normalize_issue_key(args.url or args.key)
    except ValueError as e:
        print(f"refused: {e}")
        return 2
    outdir = os.path.join(ROOT, "target", "jira", key)
    story_path = os.path.join(outdir, "story.json")

    if rc_fetch != 0:
        # 2 is a REFUSAL (no config, unapproved host, unreadable key, no
        # token); 1 is the acceptance-criteria gate. Reporting both as
        # "the story's rule is unclear" sent a reader looking at the
        # story for a problem that was in their config file.
        if rc_fetch == 2:
            print("\nStopping at stage 1: the fetch was refused before it "
                  "reached Jira. Nothing above is a problem with the story.")
        else:
            if os.path.isfile(story_path):
                print("\nThe snapshot was still written, so the acceptance "
                      "criteria can be read and argued with:")
                print(f"  {story_path}")
            print("\nStopping at stage 1. Nothing downstream may run on a "
                  "story whose rule is unclear.")
        return rc_fetch
    if not os.path.isfile(story_path):
        print(f"\nfetch reported success but wrote no {story_path}")
        return 1

    # --- 2. the index ------------------------------------------------
    index_path = (args.index if os.path.isabs(args.index)
                  else os.path.join(ROOT, args.index.replace("/", os.sep)))
    if args.rebuild_index or not os.path.isfile(index_path):
        _rule("2a/4  shapes -- indexing what is already converted")
        why = ("asked for" if args.rebuild_index
               else f"{args.index} does not exist yet")
        print(f"rebuilding ({why}); this parses every input XML -- "
              f"{len(shapes.suites_from_inputs())} suite(s), a few minutes.")
        # verbose=True: it prints a line per suite. Silence here meant
        # minutes with no output on a 29-suite tree, which is
        # indistinguishable from a hang.
        shapes.save(shapes.build(verbose=True), index_path)
        print(f"-> {index_path}")

    # --- 3. the request ---------------------------------------------
    _rule("2/4  extract -- the request, read from the story or not at all")
    candidate_path = os.path.join(outdir, "candidate.json")
    rc_extract = extract.main(["--story", story_path, "--index", index_path,
                               "--json", candidate_path])
    if rc_extract != 0:
        print("\nStopping at stage 2. The story is readable but no request "
              "could be read OUT of it, and guessing a verb or a path is the "
              "one mistake that survives review.")
        _write_partial_packet(story_path, outdir, candidate_path)
        return rc_extract

    # --- 4. the verdict ----------------------------------------------
    _rule("3/4  shape_match -- is this already automated?")
    verdict_path = os.path.join(outdir, "verdict.json")
    rc_match = shape_match.main(["--candidate", candidate_path,
                                 "--index", index_path,
                                 "--json", verdict_path,
                                 "--on-duplicate", args.on_duplicate])
    duplicate_choice = ""
    if os.path.isfile(verdict_path):
        try:
            with io.open(verdict_path, encoding="utf-8") as fh:
                doc = json.load(fh)
            duplicate_choice = ((doc.get("match") or {})
                                .get("duplicate_decision") or "")
        except (OSError, ValueError):
            pass

    # --- 5. the packet -----------------------------------------------
    _rule("4/4  packet -- the brief Cursor reads")
    packet_argv = ["--story", story_path, "--candidate", candidate_path,
                   "--out-dir", outdir]
    if os.path.isfile(verdict_path):
        packet_argv += ["--verdict", verdict_path]
    if duplicate_choice:
        packet_argv += ["--duplicate-choice", duplicate_choice]
    rc_packet = packet_mod.main(packet_argv)

    print(f"\n{'-' * 72}")
    print(f"artefacts in {outdir}:")
    for name in ("story.json", "candidate.json", "verdict.json",
                 "packet.md", "packet.json"):
        p = os.path.join(outdir, name)
        print(f"  {'yes' if os.path.isfile(p) else ' no'}  {name}")
    return rc_packet or rc_match


def _write_partial_packet(story_path: str, outdir: str,
                          candidate_path: str) -> None:
    """A packet even when extraction failed.

    The alternative is printing a refusal to a terminal and leaving no
    artefact, which is how a story quietly gets handed to an agent
    anyway -- with the one file that explains the refusal missing.
    """
    argv = ["--story", story_path, "--out-dir", outdir]
    if os.path.isfile(candidate_path):
        argv += ["--candidate", candidate_path]
    try:
        packet_mod.main(argv)
    except SystemExit:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
