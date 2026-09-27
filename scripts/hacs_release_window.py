#!/usr/bin/env python3
"""#1012 — keep a full release inside the window HACS reads.

HACS asks GitHub for ONE page of releases — thirty — and skips every
pre-release unless the user has ticked "show beta versions". SEM cuts a beta
per fix, so once thirty betas stood above v2.0.0 the newest full release had
scrolled off that page. HACS then had no version to offer: it fell back to the
branch head's short commit sha, and because `hacs.json` declares
`zip_release` it asked GitHub for a release asset at a commit sha. No release
lives at a commit sha, so every plain install answered 404 — reported
27.09.2026 with 41 betas standing above v2.0.0.

The rule kept here: **the newest full release must sit inside the first
``PAGE`` releases GitHub lists.** When it does not, the OLDEST betas above it
go back to draft until it sits at index ``KEEP``, which leaves ``PAGE - KEEP``
spare slots for the betas still to come. A draft keeps its tag and its notes,
and ``gh release edit <tag> --draft=false`` puts one back.

Only a pre-release is ever touched. A full release is never touched.

Usage::

    python3 scripts/hacs_release_window.py --check
    python3 scripts/hacs_release_window.py --retire [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

#: What HACS asks GitHub for. `get_releases()` in HACS calls the releases
#: endpoint with no page size, so it gets GitHub's default: one page of 30.
PAGE = 30

#: How many betas may stand above the newest full release after a retire.
#: Ten spare slots means the window survives ten failed runs in a row.
KEEP = 20

#: Refuse to retire more than this in one run — a runaway guard.
MAX_PER_RUN = 30

REPO = os.environ.get("GITHUB_REPOSITORY") or "traktore-org/sem-community"


def listed(releases: list[dict]) -> list[dict]:
    """The releases a reader without write access sees — drafts are hidden
    from everybody else, so they take up no slot in HACS's page."""
    return [r for r in releases if not r.get("draft")]


def full_release_index(releases: list[dict]) -> int | None:
    """Where the newest full release sits in the list HACS reads, or None
    when there is no full release at all."""
    for index, release in enumerate(listed(releases)):
        if not release.get("prerelease"):
            return index
    return None


def hacs_has_a_version(releases: list[dict]) -> bool:
    """True when a plain HACS install can find a version to download."""
    index = full_release_index(releases)
    return index is not None and index < PAGE


def to_retire(releases: list[dict], keep: int = KEEP) -> list[str]:
    """Tags of the oldest betas standing above the newest full release —
    enough of them to bring it down to index ``keep``. Empty when the window
    is already wide enough, and empty when there is no full release to save
    (retiring betas cannot conjure one)."""
    index = full_release_index(releases)
    if index is None or index <= keep:
        return []
    above = [r for r in listed(releases)[:index] if r.get("prerelease")]
    need = min(index - keep, len(above), MAX_PER_RUN)
    oldest_first = list(reversed(above))
    return [r["tag_name"] for r in oldest_first[:need]]


def _gh(*args: str) -> str:
    return subprocess.run(
        ["gh", *args], check=True, capture_output=True, text=True).stdout


def fetch_releases() -> list[dict]:
    """One page, big enough to see past the betas. The first ``PAGE`` entries
    of this list are exactly the page HACS reads — same endpoint, same order."""
    return json.loads(_gh("api", f"repos/{REPO}/releases?per_page=100"))


def retire(tag: str) -> None:
    """Send one release back to draft, after reading it again to be sure it is
    a pre-release. The tag and the notes stay."""
    release = json.loads(
        _gh("api", f"repos/{REPO}/releases/tags/{tag}"))
    if not release.get("prerelease"):
        raise SystemExit(f"refusing to retire {tag}: it is a full release")
    _gh("release", "edit", tag, "-R", REPO, "--draft=true")


def _say(line: str) -> None:
    print(line)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="fail when HACS can see no version to install")
    parser.add_argument("--retire", action="store_true",
                        help="draft the oldest betas until the window fits")
    parser.add_argument("--dry-run", action="store_true",
                        help="with --retire: say what it would do")
    args = parser.parse_args(argv)
    if not (args.check or args.retire):
        parser.error("pass --check or --retire")

    releases = fetch_releases()
    index = full_release_index(releases)
    where = "none" if index is None else str(index)
    _say(f"Newest full release at index {where} of the {PAGE} HACS reads.")

    if args.retire:
        tags = to_retire(releases)
        if not tags:
            _say("Nothing to retire.")
        for tag in tags:
            if args.dry_run:
                _say(f"would retire {tag}")
                continue
            retire(tag)
            _say(f"retired {tag} (draft — tag and notes kept)")
        if tags and not args.dry_run:
            releases = fetch_releases()
            index = full_release_index(releases)
            _say(f"Newest full release now at index {index}.")

    if index is None:
        _say("No full release at all — HACS has nothing to offer. Cut one.")
        return 1
    if index >= PAGE:
        _say(f"{index} pre-releases stand above it, so a plain HACS install "
             f"finds no version and answers 404. Retire some.")
        return 1
    _say("A plain HACS install can find a version.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
