"""#1012 — a plain HACS install must always find a version to download.

HACS asks GitHub for one page of releases — thirty — and skips every
pre-release unless the user ticked "show beta versions". SEM cuts a beta per
fix, so once thirty betas stood above v2.0.0 the newest full release had
scrolled off that page. HACS had no version to offer, fell back to the branch
head's short commit sha, and — `hacs.json` declares `zip_release` — asked
GitHub for a release asset at a commit sha:

    Got status code 404 when trying to download
    .../releases/download/45438ef/solar_energy_management.zip

Nobody could install SEM for nine days. `scripts/hacs_release_window.py`
retires the oldest betas standing above the newest full release until it is
back inside the page. These tests pin what it picks, and what it must never
touch.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load():
    """Load the script by path — `scripts/` is not an importable package in
    the CI layout. Same approach as #855's baseline test."""
    spec = importlib.util.spec_from_file_location(
        "hacs_release_window", ROOT / "scripts" / "hacs_release_window.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


window = _load()


def _beta(number: int) -> dict:
    return {"tag_name": f"v2.1.0-beta.{number}", "prerelease": True,
            "draft": False}


def _full(name: str = "v2.0.0") -> dict:
    return {"tag_name": name, "prerelease": False, "draft": False}


def _feed(betas: int, *, full: bool = True) -> list[dict]:
    """Newest first, the order GitHub lists releases in."""
    feed = [_beta(n) for n in range(betas, 0, -1)]
    return feed + ([_full()] if full else [])


# --- the window itself ------------------------------------------------------

def test_the_guard_can_still_bite():
    """Keeping more betas than HACS reads would make the retire pointless."""
    assert window.KEEP < window.PAGE


def test_hacs_reads_thirty():
    """HACS calls the releases endpoint with no page size, so it gets
    GitHub's default of thirty. The whole bug is that number."""
    assert window.PAGE == 30


def test_a_full_release_inside_the_page_is_installable():
    assert window.hacs_has_a_version(_feed(29))


def test_a_full_release_at_the_page_edge_is_not():
    """Index 30 is the first one off the page — this is the reported bug."""
    assert not window.hacs_has_a_version(_feed(30))


def test_betas_only_is_not_installable():
    assert not window.hacs_has_a_version(_feed(41, full=False))


# --- what gets retired -----------------------------------------------------

def test_a_healthy_window_is_left_alone():
    assert window.to_retire(_feed(window.KEEP)) == []


def test_the_reported_state_retires_the_oldest_betas():
    """41 betas above v2.0.0 — what the reporter's install hit. Enough go
    back to draft to seat the full release at KEEP, oldest first."""
    tags = window.to_retire(_feed(41))
    assert len(tags) == 41 - window.KEEP
    assert tags[0] == "v2.1.0-beta.1"
    assert tags[-1] == f"v2.1.0-beta.{41 - window.KEEP}"


def test_retiring_seats_the_full_release_at_keep():
    feed = _feed(41)
    retired = set(window.to_retire(feed))
    left = [r for r in feed if r["tag_name"] not in retired]
    assert window.full_release_index(left) == window.KEEP
    assert window.hacs_has_a_version(left)


def test_a_full_release_is_never_retired():
    feed = _feed(41)
    tags = window.to_retire(feed)
    assert "v2.0.0" not in tags
    assert all(
        r["prerelease"] for r in feed if r["tag_name"] in set(tags))


def test_betas_only_retires_nothing():
    """Retiring betas cannot conjure a full release, so it must not try —
    it would delete the whole history and still answer 404."""
    assert window.to_retire(_feed(41, full=False)) == []


def test_one_run_is_bounded():
    assert len(window.to_retire(_feed(500))) <= window.MAX_PER_RUN


def test_a_draft_takes_up_no_slot():
    """Drafts are hidden from every reader without write access, so they are
    not in HACS's page. Counting them would retire betas for nothing."""
    feed = [_beta(n) for n in range(41, 0, -1)]
    for release in feed[:20]:
        release["draft"] = True
    feed.append(_full())
    assert window.full_release_index(feed) == 21
    assert window.to_retire(feed) == ["v2.1.0-beta.1"]


def test_an_older_full_release_does_not_count():
    """Only the NEWEST full release matters — an ancient one is already off
    the page and cannot save the install."""
    feed = _feed(41) + [_full("v1.7.9")]
    assert window.full_release_index(feed) == 41


# --- the wiring ------------------------------------------------------------

@pytest.mark.parametrize("workflow", [
    "hacs-release-window.yml",   # every release cut with a user token
    "create-release.yml",        # the GITHUB_TOKEN path, which fires no event
])
def test_every_release_path_runs_the_retire(workflow):
    text = (ROOT / ".github" / "workflows" / workflow).read_text()
    assert "scripts/hacs_release_window.py --retire" in text


def test_the_release_event_triggers_it():
    spec = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "hacs-release-window.yml").read_text())
    assert "published" in spec[True]["release"]["types"]


def test_the_window_matters_because_of_zip_release():
    """Without `zip_release` a missing version reads differently. This pins
    the premise the script's reasoning rests on."""
    hacs = json.loads((ROOT / "hacs.json").read_text())
    assert hacs["zip_release"] is True
    assert hacs["filename"] == "solar_energy_management.zip"
