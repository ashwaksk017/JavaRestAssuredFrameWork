"""No generated row file may ever be tracked by git.

WHY THIS IS A CHECK AND NOT A CONVENTION
----------------------------------------
`src/test/resources/csv/` holds the generated row files for every
imported suite. Measured over the 1,064 of them: 3,721 email addresses,
2,571 account/guest/confirmation ids, 685 vendor hostnames and 416
internal Jira URLs. This repository is public.

Committing one is silent at the time and unrecoverable afterwards -- it
is in the history, and rewriting published history is not a fix anyone
gets to apply casually. A `.gitignore` entry is the right mechanism but
the wrong guarantee: it can be narrowed by one careless negation, or
bypassed by `git add -f`, and nothing would say so until the push.

The tracked exception is `csv/manual/`, for author-written row files.
Anything else under csv/ that git knows about is a finding.

    python tools/check_tracked_csv.py
"""
from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_ROOT = "src/test/resources/csv/"
ALLOWED_PREFIX = CSV_ROOT + "manual/"


def repo_prefix() -> str:
    p = subprocess.run(("git", "rev-parse", "--show-prefix"), cwd=ROOT,
                       capture_output=True, text=True)
    return p.stdout.strip().replace("\\", "/") if p.returncode == 0 else ""


def tracked_csv() -> tuple[list[str], str]:
    """(repo-relative tracked paths under csv/, error)."""
    p = subprocess.run(("git", "ls-files", "--", CSV_ROOT), cwd=ROOT,
                       capture_output=True, text=True)
    if p.returncode != 0:
        return [], p.stderr.strip() or "git ls-files failed"
    prefix = repo_prefix()
    out = []
    for line in p.stdout.splitlines():
        rel = line.strip().replace("\\", "/")
        if not rel:
            continue
        if prefix and rel.startswith(prefix):
            rel = rel[len(prefix):]
        out.append(rel)
    return out, ""


def in_git_repo() -> bool:
    p = subprocess.run(("git", "rev-parse", "--is-inside-work-tree"), cwd=ROOT,
                       capture_output=True, text=True)
    return p.returncode == 0 and p.stdout.strip().lower() == "true"


def main() -> int:
    # "git is broken" and "there is no repository here" are different
    # facts and deserve different answers. This used to fail closed on
    # both, so an unzipped copy of the framework -- which cannot commit
    # anything, and therefore cannot commit a generated row file -- failed
    # a guard about committing generated row files, every single run.
    #
    # Nothing to guard is not the same as guarded, so it does not pass
    # quietly either: it reports that the protection is ABSENT, which
    # verify_all surfaces as SKIP rather than PASS.
    if not in_git_repo():
        print("not a git repository -- nothing to check.\n"
              "This protection is ABSENT here: it works by asking git what "
              "is tracked, and an unzipped copy has no answer. The risk it "
              "guards (publishing 1,064 generated row files carrying "
              "customer emails, account ids and internal hostnames) only "
              "exists where a commit is possible, so a copy is safe for a "
              "different reason -- not because this check passed.")
        return 0

    tracked, err = tracked_csv()
    if err:
        print(f"check_tracked_csv: git is present but the query failed "
              f"({err}); treating as a failure rather than a pass, because "
              f"the thing being guarded is unrecoverable.")
        return 1

    offenders = sorted(p for p in tracked if not p.startswith(ALLOWED_PREFIX))
    allowed = sorted(p for p in tracked if p.startswith(ALLOWED_PREFIX))

    print(f"tracked under {CSV_ROOT}: {len(tracked)} file(s)")
    print(f"  author row files (csv/manual/, allowed): {len(allowed)}")
    print(f"  anything else                          : {len(offenders)}")

    if not offenders:
        print("\nNo generated row file is tracked.")
        return 0

    print("\nGENERATED ROW FILES ARE TRACKED. This repository is public and "
          "these carry customer emails, account ids and internal hostnames:")
    for p in offenders[:25]:
        print(f"  {p}")
    if len(offenders) > 25:
        print(f"  ... and {len(offenders) - 25} more")
    print("\nUntrack them before committing anything further:")
    print(f"  git rm --cached -r -- {CSV_ROOT}")
    print(f"  # then re-add only {ALLOWED_PREFIX}")
    print("If any of these has already been PUSHED, the data is in the "
          "published history and untracking does not remove it -- say so "
          "rather than assuming the problem is fixed.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
