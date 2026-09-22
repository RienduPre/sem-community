#!/usr/bin/env python3
"""Regenerate the #998 causal-claim ledger. Run DELIBERATELY.

The ledger only shrinks. Regenerating it after fixing or annotating claims
is routine; regenerating it to silence a new one is the thing this exists to
prevent, so the diff should always show the total going DOWN.
"""
from __future__ import annotations

import json
from pathlib import Path

from causal_claims import claims

HERE = Path(__file__).parent


def main() -> None:
    per: dict = {}
    for c in claims(HERE.parent):
        if not c.annotated:
            per[c.file] = per.get(c.file, 0) + 1
    out = {
        "_why": ("#998 — reason strings that assert something about the WORLD "
                 "and carry no `# CAUSE:` annotation naming the state in their "
                 "own scope that proves it. Bug class 99 lives here. The "
                 "ledger only shrinks; see "
                 "tests/test_998_causal_claims_ratchet.py."),
        "total": sum(per.values()),
        "per_file": dict(sorted(per.items(), key=lambda x: (-x[1], x[0]))),
    }
    path = HERE / "causal_claim_baseline.json"
    before = json.loads(path.read_text(encoding="utf-8"))["total"]
    path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(f"causal-claim ledger: {before} -> {out['total']}")


if __name__ == "__main__":
    main()
