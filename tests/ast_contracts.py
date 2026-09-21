"""Structural assertions that are about the CODE, not about its spelling.

Why this exists
---------------
97 of SEM's test files assert a structural fact by grepping a function's
source text::

    src = inspect.getsource(SEMCoordinator._shadow_energy_plan)
    assert "export_rate=" in src

That is one line, which is why it is everywhere — 121 of them. It is also
the shape that let #924 live for a month: the assertion names ONE function,
so three sibling call sites kept the old behaviour underneath it, and one of
those packed the plan a user reads. It is coupled to the SPELLING of the
code, not to the code. It cannot tell you:

* whether the call is REACHED (it may sit in a dead branch),
* whether siblings elsewhere lack it (the #924 defect exactly),
* whether a rename broke it (a renamed callee makes the test pass on the
  old string until someone notices),
* whether the string is in a COMMENT or a docstring rather than in code.

51 files already do it properly with ``ast``, and every one of them rolls
its own walker — thirty lines to say what the grep says in one. So the
fragile form wins on ergonomics, every time, and the ledger fills up.

This module makes the sound form one line too. Prefer these; reach for
``inspect.getsource`` only when the thing you are pinning genuinely IS text
(a comment, an annotation, a log message a user will read).

Everything here ignores strings, comments and docstrings by construction:
it walks the parsed tree, so a mention in prose is not a call.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from pathlib import Path
from typing import Callable, Iterable, Optional


def _tree_of(fn: Callable) -> ast.AST:
    return ast.parse(textwrap.dedent(inspect.getsource(fn)))


def _callee_name(node: ast.Call) -> str:
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return ""


def calls(fn: Callable, callee: str) -> bool:
    """Does ``fn`` contain a CALL to ``callee``? Matches a bare name or an
    attribute (``x.callee()``). A mention in a comment or a string is not a
    call, which is the whole point."""
    return any(isinstance(n, ast.Call) and _callee_name(n) == callee
               for n in ast.walk(_tree_of(fn)))


def call_kwargs(fn: Callable, callee: str) -> list:
    """Every call to ``callee`` inside ``fn``, as its list of keyword names.

    Use it to pin "this call passes X" without pinning how X is spelled::

        assert all("export_rate" in kw for kw in
                   call_kwargs(coord._shadow_energy_plan, "build_day_slots"))
    """
    out = []
    for n in ast.walk(_tree_of(fn)):
        if isinstance(n, ast.Call) and _callee_name(n) == callee:
            out.append([k.arg for k in n.keywords if k.arg])
    return out


def reads_attribute(fn: Callable, obj: str, attr: str) -> bool:
    """Does ``fn`` read ``obj.attr``? (``reads_attribute(f, "self", "_x")``)

    The #915 read-back was dead because three call sites read
    ``self._battery_adapter`` — a name nothing had assigned since #375. A
    grep for that string also matched a docstring and a longer sibling
    attribute; this does not."""
    for n in ast.walk(_tree_of(fn)):
        if (isinstance(n, ast.Attribute) and n.attr == attr
                and isinstance(n.value, ast.Name) and n.value.id == obj):
            return True
    return False


def assigns_attribute(fn: Callable, obj: str, attr: str) -> bool:
    """Does ``fn`` ASSIGN ``obj.attr``? The counterpart to the above — a
    read with no writer anywhere is a dead feature (#915)."""
    for n in ast.walk(_tree_of(fn)):
        if not isinstance(n, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            continue
        targets = n.targets if isinstance(n, ast.Assign) else [n.target]
        for t in targets:
            if (isinstance(t, ast.Attribute) and t.attr == attr
                    and isinstance(t.value, ast.Name) and t.value.id == obj):
                return True
    return False


def call_sites(callee: str, *, root: Optional[Path] = None,
               skip_dirs: Iterable[str] = ("tests", "scripts",
                                           "node_modules", ".git")) -> list:
    """EVERY production call to ``callee`` in the package, as
    ``[(relative_path, lineno, [kwarg names])]``.

    This is the one that answers the #924 question — "are the SIBLINGS
    right?" — which no per-function assertion can ever ask. Write coverage
    rules over this, not over one function's source."""
    root = root or Path(__file__).resolve().parent.parent
    hits = []
    for p in sorted(root.rglob("*.py")):
        rel = p.relative_to(root)
        if set(rel.parts) & set(skip_dirs):
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:          # a file we cannot parse is not a pass
            raise
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and _callee_name(n) == callee:
                hits.append((str(rel), n.lineno,
                             [k.arg for k in n.keywords if k.arg]))
    return hits


#: Directories a source contract never walks. Anything generated, vendored
#: or virtual belongs here: `_production_files` parses every file it reaches,
#: so one un-parseable tree would turn a contract into a crash rather than a
#: finding.
_SKIP_DIRS = ("tests", "scripts", "node_modules", ".git", "__pycache__",
              ".venv", "venv", "build", "dist", ".tox", ".mypy_cache")


def _rebinds(node: ast.AST, name: str) -> bool:
    """Does this statement rebind ``name``? A handler's exception name that
    has been overwritten is no longer evidence that anything was caught."""
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and n.id == name and isinstance(
                n.ctx, (ast.Store, ast.Del)):
            return True
    return False


#: Bodies Python evaluates in their OWN scope. An ``except … as e`` name is
#: deleted at handler exit, so a closure written inside the handler and called
#: later sees nothing — the binding must not leak into these.
_OWN_SCOPE = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda,
              ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def _except_bound_names(tree: ast.AST) -> dict:
    """``{id(Call node): frozenset(names an enclosing except-as has bound)}``.

    Walks handlers rather than the whole tree so the binding is SCOPED: a
    name caught three functions away does not count, a name the handler
    reassigned no longer counts, and a name referenced from a nested
    function or comprehension does not count either (Python has deleted it
    by then). ``except*`` groups bind exactly the same way."""
    bound: dict = {}

    def walk(node, names: frozenset):
        if isinstance(node, _OWN_SCOPE):
            names = frozenset()
        if isinstance(node, (ast.Try, getattr(ast, "TryStar", ast.Try))) \
                and hasattr(node, "handlers"):
            for part in (node.body, node.orelse, node.finalbody):
                for st in part:
                    walk(st, names)
            for h in node.handlers:
                if h.type is not None:
                    walk(h.type, names)
                inner = names | ({h.name} if h.name else set())
                for st in h.body:
                    walk(st, frozenset(inner))
                    if h.name and _rebinds(st, h.name):
                        inner = set(inner) - {h.name}
            return
        if isinstance(node, ast.Call):
            bound[id(node)] = names
        for child in ast.iter_child_nodes(node):
            walk(child, names)

    walk(tree, frozenset())
    return bound


def _production_files(root: Optional[Path], skip_dirs: Iterable[str]):
    root = root or Path(__file__).resolve().parent.parent
    for p in sorted(root.rglob("*.py")):
        rel = p.relative_to(root)
        if set(rel.parts) & set(skip_dirs):
            continue
        yield rel, ast.parse(p.read_text(encoding="utf-8"))


def invented_evidence_call_sites(
        callee: str, *, root: Optional[Path] = None,
        skip_dirs: Iterable[str] = _SKIP_DIRS) -> list:
    """(#945, bug class 86) Every production call to ``callee`` whose
    evidence argument is an exception SEM made up, rather than one an
    enclosing ``except … as e`` actually caught.

    This is the class-86 sweep question asked structurally, for any counter
    that takes an exception as its evidence. A caught name means something
    really happened and really refused; ``RuntimeError("the switch is off")``
    means somebody turned an OBSERVATION into the same verdict — which is
    how a restart's warm-up became "your last 3+ commands were rejected".

    Checks the first positional AND every keyword argument, because the
    parameter has a name and the keyword form is the natural spelling. Pair
    it with :func:`symbol_reference_files` — a call reached through
    ``getattr(obj, "callee")`` is invisible here BY NAME, which is exactly
    how the bug was written.

    Returns ``[(relative_path, lineno, what_was_constructed)]``."""
    hits = []
    for rel, tree in _production_files(root, skip_dirs):
        bound = _except_bound_names(tree)
        for n in ast.walk(tree):
            if not (isinstance(n, ast.Call) and _callee_name(n) == callee):
                continue
            args = list(n.args[:1]) + [k.value for k in n.keywords]
            for arg in args:
                if isinstance(arg, ast.Name):
                    if arg.id in bound.get(id(n), frozenset()):
                        continue      # a command really raised — evidence
                    hits.append((str(rel), n.lineno, arg.id))
                elif isinstance(arg, ast.Call):
                    hits.append((str(rel), n.lineno, _callee_name(arg)))
                else:
                    hits.append((str(rel), n.lineno, type(arg).__name__))
    return hits


def symbol_reference_files(name: str, *, root: Optional[Path] = None,
                           skip_dirs: Iterable[str] = _SKIP_DIRS) -> list:
    """Every production file that so much as NAMES ``name`` in code — as a
    definition, an attribute access, or the string inside a ``getattr``.

    The companion to :func:`invented_evidence_call_sites`, and the reason
    it is not enough on its own: this codebase reaches its hooks through
    ``getattr(dev, "_record_actuation_failure", None)`` and then calls the
    local, so every callee-name contract is blind to the one shape the bug
    actually used. Answer "who may even MENTION this?" instead — a question
    indirection cannot dodge.

    Returns ``[(relative_path, lineno)]``, ignoring comments and docstrings
    by construction (bare string constants are not scanned)."""
    hits = []
    for rel, tree in _production_files(root, skip_dirs):
        for n in ast.walk(tree):
            if isinstance(n, ast.Attribute) and n.attr == name:
                hits.append((str(rel), n.lineno))
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and n.name == name:
                hits.append((str(rel), n.lineno))
            elif isinstance(n, ast.Call) and _callee_name(n) == "getattr":
                for a in n.args[1:2]:
                    if isinstance(a, ast.Constant) and a.value == name:
                        hits.append((str(rel), n.lineno))
    return sorted(set(hits))


def _mentions_states_get(node: ast.AST) -> bool:
    """Does this subtree CALL ``<something>.states.get(...)``?

    Narrow on purpose: a bare ``dict.get()`` is a mapping read and says
    nothing about HA's state machine. The receiver must be named ``states``
    — as an attribute (``hass.states.get``, the spelling this codebase uses
    everywhere) or as a bare name, which covers a hoisted
    ``states = hass.states``. The bare-name arm is deliberately wider than
    the attribute one: a local mapping called ``states`` would be flagged,
    and that false positive is cheaper than missing the hoisted spelling,
    because it only matters at all inside an ABSENCE branch that also nulls
    a handle — which is the bug either way.
    """
    for n in ast.walk(node):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "get"):
            continue
        recv = n.func.value
        if isinstance(recv, ast.Attribute) and recv.attr == "states":
            return True
        if isinstance(recv, ast.Name) and recv.id == "states":
            return True
    return False


def _is_absence_test(test: ast.AST) -> bool:
    """Is this condition asking whether a state read came back EMPTY?

    ``not hass.states.get(e)`` / ``hass.states.get(e) is None`` /
    ``... == None``. A POSITIVE test (``if hass.states.get(e):``) is the
    opposite shape and is not the class — acting on a reading you actually
    have is evidence, never silence.
    """
    if not _mentions_states_get(test):
        return False
    for n in ast.walk(test):
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.Not):
            if _mentions_states_get(n.operand):
                return True
        if isinstance(n, ast.Compare) and _mentions_states_get(n.left):
            for op, cmp in zip(n.ops, n.comparators, strict=False):
                if (isinstance(op, (ast.Is, ast.Eq))
                        and isinstance(cmp, ast.Constant)
                        and cmp.value is None):
                    return True
    return False


def _null_target_names(target: ast.AST) -> list:
    """Every handle this assignment target discards, by the name a reader
    would recognise. Covers the four spellings a config handle is actually
    written in: a local (``entity = None``), an attribute
    (``device.current_entity_id = None``), a dict slot
    (``row["switch_entity"] = None`` — this tree keeps device rows in dicts,
    so it is the MOST natural way to re-acquire the class), and a tuple
    unpack."""
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, ast.Attribute):
        return [target.attr]
    if isinstance(target, ast.Subscript):
        sl = target.slice
        if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
            return [sl.value]
        base = target.value
        if isinstance(base, ast.Name):
            return [base.id]
        if isinstance(base, ast.Attribute):
            return [base.attr]
        return ["<subscript>"]
    if isinstance(target, (ast.Tuple, ast.List)):
        out = []
        for el in target.elts:
            out.extend(_null_target_names(el))
        return out
    return []


def _assigns_none(stmt: ast.AST) -> list:
    """``[(lineno, name)]`` for every ``= None`` this statement performs —
    plain, annotated, or the conditional-expression form
    (``e = None if states.get(e) is None else e``), which carries its own
    absence test and so is judged here rather than by an enclosing ``if``."""
    hits = []
    if isinstance(stmt, ast.Assign):
        targets, value = stmt.targets, stmt.value
    elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
        targets, value = [stmt.target], stmt.value
    else:
        return hits
    nulls_unconditionally = (
        (isinstance(value, ast.Constant) and value.value is None)
        # ``entity, service = None, None`` — one statement, two handles.
        or (isinstance(value, (ast.Tuple, ast.List)) and bool(value.elts)
            and all(isinstance(el, ast.Constant) and el.value is None
                    for el in value.elts)))
    nulls_on_absence = (
        isinstance(value, ast.IfExp)
        and ((_is_absence_test(value.test)
              and isinstance(value.body, ast.Constant)
              and value.body.value is None)
             or (_mentions_states_get(value.test)
                 and isinstance(value.orelse, ast.Constant)
                 and value.orelse.value is None)))
    if not (nulls_unconditionally or nulls_on_absence):
        return hits
    for t in targets:
        for name in _null_target_names(t):
            hits.append((stmt.lineno, name, nulls_on_absence))
    return hits


def absence_spent_as_config(*, root: Optional[Path] = None,
                            skip_dirs: Iterable[str] = _SKIP_DIRS) -> list:
    """(#991, bug class 86) Every production site that DISCARDS a configured
    handle on the strength of a ``hass.states.get()`` absence.

    The class-86 sweep question, asked of the other consumer. #945 asked it
    of evidence COUNTERS ("is this observation evidence, or silence?"); this
    asks it of CONFIGURATION, where the same empty read is spent not on a
    verdict but on a capability — ``current_control_entity = None`` because
    an integration had not finished loading yet, permanent for the session
    because entity ids are structural and only a reload re-derives them.

    The shape: a condition that asks whether a state read is empty, and an
    assignment of ``None`` reached by it — as an ``if``/``elif`` body (at
    any nesting depth inside it) or as the conditional-expression form,
    which carries its own test. Targets cover a local, an attribute, a dict
    slot and a tuple unpack; plain and annotated assignment both. The answer
    to "is this entity there?" belongs where the entity is USED — a
    per-cycle read that may be wrong for one cycle and right for the next —
    never at setup, where the only honest answer is "I could not ask yet".

    **The limit, named so nobody mistakes a pass for proof:** the test and
    the assignment must be syntactically connected. The two-step dataflow
    form — ``st = hass.states.get(e)`` … later … ``if st is None: e = None``
    — is invisible here, and so is any spelling that hides the absence
    behind a helper predicate (``if self._dead(e): e = None``). That is not
    a gap this contract can close without dataflow; it is why the
    behavioural pin exists, driving the real registration with the entity
    absent. Both limits are pinned in
    ``tests/test_991_warmup_absence_not_spent.py`` so a future reader sees
    them rather than discovers them.

    Returns ``[(relative_path, lineno, what_was_nulled)]``."""
    hits = []
    for rel, tree in _production_files(root, skip_dirs):
        for n in ast.walk(tree):
            # (a) the conditional-expression form carries its own test, so
            # it is judged wherever it appears — no enclosing ``if`` needed.
            if isinstance(n, (ast.Assign, ast.AnnAssign)):
                for lineno, name, conditional in _assigns_none(n):
                    if conditional:
                        hits.append((str(rel), lineno, name))
                continue
            # (b) an absence branch, and anything it reaches. ``ast.walk``
            # over each body statement so a null nested in a ``try`` / ``for``
            # / ``with`` — or in a closure defined there — is not a hiding
            # place. ``elif`` is an ``If`` in ``orelse`` and is reached by
            # the same walk of the enclosing tree.
            if not (isinstance(n, ast.If) and _is_absence_test(n.test)):
                continue
            for stmt in n.body:
                for sub in ast.walk(stmt):
                    for lineno, name, conditional in _assigns_none(sub):
                        if not conditional:
                            hits.append((str(rel), lineno, name))
    return sorted(set(hits))
