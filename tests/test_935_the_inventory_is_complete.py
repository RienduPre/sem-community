"""#935 — every per-entry Store SEM creates is in the removal inventory.

`cleanup.py` is the one place that says what SEM leaves behind, and removal
builds its delete list from it. A list like that rots the moment someone adds
a store and does not think of it — and that is exactly what happened: #935
created `sem.parked.{entry_id}` so a parked wallbox could outlive the process
that parked it, and then removal took every file except that one. Its own
issue's inventory, missing its own issue's store.

"Outlive the process" is not "outlive removal". On removal the box is handed
back first, so the debt is paid and the record of it is a leftover — one of
the 119 Spook found on #908, in kind if not in name.

So this does not re-read the list. It walks the AST for every `Store(...)`
construction in production code whose key mentions an entry id, and asks the
inventory about each one.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from .. import cleanup

ROOT = Path(__file__).resolve().parent.parent
SKIP = {"tests", "scripts", "node_modules", "__pycache__", "dashboard"}
ENTRY = "01ABCDEF"


def _key_shapes():
    """Every Store(...) key in production code, as a literal shape.

    Returns (file, line, shape) where shape is the f-string's constant text
    with its interpolations collapsed to ``{}`` — enough to tell a per-entry
    key from an install-wide one without evaluating anything.
    """
    out = []
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT)
        if set(rel.parts) & SKIP:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if not (isinstance(n, ast.Call)
                    and getattr(n.func, "id", getattr(n.func, "attr", "")) == "Store"):
                continue
            if len(n.args) < 3:
                continue
            key = n.args[2]
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                out.append((str(rel), n.lineno, key.value))
            elif isinstance(key, ast.JoinedStr):
                shape = "".join(
                    p.value if isinstance(p, ast.Constant) else "{}"
                    for p in key.values)
                out.append((str(rel), n.lineno, shape))
    return out


@pytest.mark.unit
class TestEveryPerEntryStoreIsInTheInventory:

    def test_the_park_store_is_covered(self):
        """The one that was missing, named so a revert is loud."""
        assert "sem.parked.{entry_id}" in cleanup._PER_ENTRY_STORE_FORMATS
        assert f"sem.parked.{ENTRY}" in cleanup.per_entry_store_keys(ENTRY)

    def test_no_per_entry_store_is_created_outside_the_inventory(self):
        """The guard that would have caught it.

        A key whose shape carries an interpolation and is not an install-wide
        name is per-entry: the inventory must produce it, or match it by one
        of its prefixes.
        """
        keys = set(cleanup.per_entry_store_keys(ENTRY))
        prefixes = tuple(p.format(entry_id=ENTRY)
                         for p in cleanup._PER_ENTRY_STORE_PREFIXES)
        install_wide = set(cleanup._INSTALL_WIDE_STORES) | set(cleanup._LEGACY_STORES)
        # Stores owned by Home Assistant itself, not by SEM.
        FOREIGN = ("lovelace", "auth", "core.")

        missing = []
        for file, line, shape in _key_shapes():
            if not shape or shape.startswith(FOREIGN):
                continue
            if "{}" not in shape:                       # a fixed name
                if shape in install_wide:
                    continue
                missing.append(f"{file}:{line} {shape!r} (fixed name, not install-wide)")
                continue
            # A shaped key: does the inventory produce something like it?
            pattern = "^" + "".join(
                ".+" if part == "" else re.escape(part)
                for part in re.split(r"\{\}", shape)
            ).replace("", "") + "$"
            pattern = "^" + ".+".join(re.escape(p) for p in shape.split("{}")) + "$"
            if any(re.match(pattern, k) for k in keys):
                continue
            if any(k.startswith(prefixes) for k in [shape.split("{}")[0]]):
                continue
            if any(shape.split("{}")[0].startswith(p.split("{")[0]) for p in prefixes):
                continue
            missing.append(f"{file}:{line} {shape!r}")

        assert not missing, (
            "per-entry Store(s) SEM creates and removal would leave behind:\n  "
            + "\n  ".join(missing)
            + "\n\nAdd the shape to cleanup._PER_ENTRY_STORE_FORMATS (or the "
              "prefix list). #935's rule is that SEM takes its own files — all "
              "of them.")
