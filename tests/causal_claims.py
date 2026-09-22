"""(#998) Enumerate the reasons SEM gives, and say which assert a CAUSE.

Bug class 99 is *a verdict naming a cause its own scope refutes*. #992 swept
it by reading, three times, and two of the three extra instances were found
by a reviewer INSIDE the fixes themselves, after the class had been declared
closed. A thirteenth turned up minutes after the merge in a file the sweep
never opened. Nobody can hold the causal claim of 170 strings in their head.

A lint cannot judge truth. It can do the thing that actually catches these,
which is force the claim to be LOCAL and CHECKED:

* a reason that merely describes the decision ("manual sell to grid") needs
  nothing — the scope obviously knows its own mode;
* a reason that asserts something about the WORLD ("sensor unavailable",
  "forecast covers consumption", "below the solar minimum") is a claim, and
  claims carry a ``# CAUSE:`` annotation naming the state in THAT scope that
  proves it.

Writing the annotation is the work. It is what surfaces the ones that cannot
be written, which is precisely the defect — "sensor unavailable" cannot cite
``inputs_degraded``, because that flag has three different causes and two of
them are sensors that answered perfectly well.

This module only enumerates. ``test_998_causal_claims_ratchet.py`` owns the
ledger.

**What it cannot see, stated plainly.** Only the LITERAL text of a reason is
judged. A claim assembled inside a helper — ``reason=f"… ({_phrase(view)})"``
— is invisible here, and the very first fix written beside this module did
exactly that. Two defensible readings: moving a claim into a named function
makes it testable, which is the point, and that helper does carry its own
tests; or it is an escape hatch that launders an unproven claim through a
call. Both are true. Treat a helper that builds reason text as owing the
same debt, and test it — there is no lint standing behind you there.

Also out of scope: Repair issue copy that lives in ``strings.json``, and the
dashboard's own strings. Those are text files, not decisions.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Iterable, List, NamedTuple

#: Directories a source contract never walks (mirrors ast_contracts).
SKIP_DIRS = ("tests", "scripts", "node_modules", ".git", "__pycache__",
             ".venv", "venv", "build", "dist", ".tox", ".mypy_cache",
             "dashboard")

#: Words that assert something about the WORLD rather than about the
#: decision being taken. Deliberately broad: a false positive costs one
#: annotation, a false negative costs a user chasing a fault that is not
#: there. Tuned against the 170 sites present when this was written.
CAUSAL = re.compile(
    r"\b("
    r"unavailable|unreadable|unknown|missing|gone|dark|never|"
    r"no |not |nothing|none|"
    r"failed|refused|blocked|rejected|unsupported|"
    r"disabled|off|stale|expired|"
    r"below|above|past|outside|beyond|under|over|"
    r"because|since|while|due to"
    r")\b", re.I)

#: The annotation that discharges a claim. Goes on the line or just above.
ANNOTATION = "# CAUSE:"


class Claim(NamedTuple):
    file: str
    line: int
    text: str
    annotated: bool


def _reason_values(tree: ast.AST) -> List[ast.AST]:
    """Every expression assigned to something called ``reason``."""
    out: List[ast.AST] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.keyword) and n.arg == "reason":
            out.append(n.value)
        elif isinstance(n, ast.Assign):
            for t in n.targets:
                if ((isinstance(t, ast.Name) and t.id == "reason")
                        or (isinstance(t, ast.Attribute) and t.attr == "reason")):
                    out.append(n.value)
        elif (isinstance(n, ast.AnnAssign) and n.value is not None
              and isinstance(n.target, ast.Name) and n.target.id == "reason"):
            out.append(n.value)
    return out


def _literal_text(node: ast.AST) -> str | None:
    """The literal half of a reason expression, or None if it has none.

    An f-string's constant parts are what a reader sees as the claim; the
    interpolated values are data.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        text = "".join(p.value for p in node.values
                       if isinstance(p, ast.Constant) and isinstance(p.value, str))
        return text or None
    return None


def _enclosing_stmt(tree: ast.AST, node: ast.AST) -> ast.AST:
    """The smallest statement containing ``node``.

    A ``reason=`` expression routinely spans four or five lines, so the
    annotation naturally sits above the whole STATEMENT rather than above
    the string literal. Looking only at the line above the literal found
    nothing and reported every annotated claim as unproven — the first
    version of this module did exactly that.
    """
    best = None
    for s in ast.walk(tree):
        if not isinstance(s, ast.stmt):
            continue
        start, end = getattr(s, "lineno", None), getattr(s, "end_lineno", None)
        if start is None or end is None:
            continue
        if start <= node.lineno and end >= getattr(node, "end_lineno", node.lineno):
            if best is None or start > best.lineno:
                best = s
    return best or node


def _is_annotated(lines: List[str], tree: ast.AST, node: ast.AST) -> bool:
    """True when ``# CAUSE:`` sits inside the statement or in the comment
    block immediately above it."""
    stmt = _enclosing_stmt(tree, node)
    lo = getattr(stmt, "lineno", node.lineno)
    hi = getattr(stmt, "end_lineno", node.lineno)
    if any(ANNOTATION in lines[i - 1] for i in range(lo, hi + 1)
           if 0 < i <= len(lines)):
        return True
    # …and the contiguous comment block directly above the statement.
    i = lo - 1
    while i >= 1:
        stripped = lines[i - 1].strip()
        if not stripped:
            break
        if not stripped.startswith("#"):
            break
        if ANNOTATION in stripped:
            return True
        i -= 1
    return False


def claims(root: Path | None = None,
           skip_dirs: Iterable[str] = SKIP_DIRS) -> List[Claim]:
    """Every reason string in production code that asserts a cause."""
    root = root or Path(__file__).resolve().parent.parent
    found: List[Claim] = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if set(rel.parts) & set(skip_dirs):
            continue
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source)
        except SyntaxError:          # a file we cannot parse is not a pass
            raise
        lines = source.split("\n")
        for node in _reason_values(tree):
            text = _literal_text(node)
            if text is None or not CAUSAL.search(text):
                continue
            found.append(Claim(str(rel), node.lineno, text.strip(),
                               _is_annotated(lines, tree, node)))
    return found


def unannotated(root: Path | None = None) -> List[Claim]:
    return [c for c in claims(root) if not c.annotated]
