"""#983 — a verdict must not name a cause its own scope refutes.

RienduPre, 2.1.0-beta.29, 18.09.2026: 5.8 kW exporting while the house
battery sat at 97.5 % and an Audi e-tron at 54 % (target 80 %) refused every
start SEM offered. The *control* was right — the pack was full, the car
declined five ladders, and all fifteen surplus devices were on
``control_mode: off`` — but both sentences SEM gave him were false in his own
diagnostic:

* ``no battery assist (SoC 98% < buffer 70%)`` — 98 % is not below 70 %. The
  gate had grown two more disjuncts (#875 never-read, #893 the owner's
  ``may_assist_ev`` permission) and the sentence kept quoting the comparison
  it was written for in 2026-06. The real cause was a switch he could flip,
  and the line hid it behind arithmetic that cannot happen.
* ``stability: full-car backoff — car declined 5 start ladders`` — the car was
  at 54 %. And the branch can never SUPPORT that word either way: where SEM
  has a ceiling reading, #548 idles in ``decide`` and the ladder is
  unreachable; where it has none (#610's own PROD case — kWh target, no
  vehicle SOC sensor) there is nothing to read. Instrument or no instrument,
  every give-up that printed "full" was guessing.

The guard has two halves, because the class does. ``failing_claims`` is a
contradiction detector: any clause stating ``A < B`` must state a relation
that actually holds, with both operands present and comparable — it runs over
the whole ``decide`` mode matrix. ``uninstrumented`` is a vocabulary lint for
the half with no arithmetic in it: the EV path may report the setpoint it
wrote and the watts it measured, and may conclude nothing about the car from
them. Either way a future reason that asserts an unchecked cause fails CI.
"""
import re

import pytest

from custom_components.solar_energy_management.coordinator.charge_stability import (
    ChargeStability,
    _meanwhile,
    FULL_CAR_GIVEUP_STREAK,
    START_KICK_GIVEUP_S,
    START_KICK_GRACE_S,
)
from custom_components.solar_energy_management.coordinator.charger_types import (
    ChargerDecision,
    ChargerEnergy,
    ChargerIntent,
    ChargerPower,
    ChargerView,
    FleetContext,
)
from custom_components.solar_energy_management.coordinator.decide import (
    _assist_blocked_why,
    _idle_bridgeable,
    decide,
)

# ─────────────────────────────────────────────────────────────────
# The detector
# ─────────────────────────────────────────────────────────────────

# Reason strings are built by concatenation — " — " joins a filter's verdict
# to the decision it wrapped, ";" joins a clause to its aside. A comparison
# never spans one of those, so the operands are resolved WITHIN a clause: a
# number from the next sentence must never be borrowed as an operand.
_CLAUSE_SPLIT = re.compile(r"\s+—\s+|;\s+|\s+\+\s+")
# ``->`` is an arrow, not a relation (SEM writes U+2192, but the next author
# may not). Everything else that looks like a comparison is one.
_OPERATOR = re.compile(r"(?<![-=])(<=|>=|<|>)")
# Thousands separators are stripped before parsing, so a decimal point is the
# only fraction marker left: "5,800W" must not be read as 5.8 W.
_NUMBER = re.compile(r"(-?\d+(?:\.\d+)?)\s*(%|Wh|kWh|kW|W|A)?")
# An aside in brackets carries no relation of its own — "(bare=9000W +
# redirect=0W)" and "(=6A)" are decorations on the operand beside them, and
# leaving them in let the ` + ` seam decapitate the real left operand of
# ``solar_only``'s commonest line. Brackets that DO contain a relation are
# kept whole: "(SoC 98% < buffer 70%)" is the claim, not an aside.
_INERT_BRACKET = re.compile(r"\([^()<>]*\)")
_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")

# How far a stated relation may be wrong before it is a LIE rather than the
# display rounding. Watts are printed to the nearest 100 (``decide._cw``),
# percents and amps to the nearest whole unit.
_TOLERANCE = {"W": 100.0, "kW": 0.1, "%": 1.0, "A": 1.0, "kWh": 0.1, None: 0.01}


def failing_claims(reason: str) -> list[str]:
    """Every comparison in ``reason`` that its own operands refute.

    A claim fails when the stated relation does not hold (past the display
    tolerance), or when an operand is missing entirely — ``SoC unknown <
    buffer 70%`` asserts a relation nobody could have evaluated, which is the
    same defect wearing a word instead of a number.

    Mixed units are skipped, not failed: ``4100W < 6A`` is not a claim this
    detector can adjudicate, and inventing a conversion here would be the
    class itself.
    """
    out: list[str] = []
    reason = _INERT_BRACKET.sub(" ", _THOUSANDS.sub("", reason))
    for clause in _CLAUSE_SPLIT.split(reason):
        for m in _OPERATOR.finditer(clause):
            op = m.group(1)
            before, after = clause[:m.start()], clause[m.end():]
            lhs = list(_NUMBER.finditer(before))
            rhs = _NUMBER.search(after)
            if not lhs or rhs is None:
                out.append(f"{clause.strip()!r}: {op} with a missing operand")
                continue
            lhs = lhs[-1]                      # the number nearest the operator
            lu, ru = lhs.group(2), rhs.group(2)
            if lu != ru:
                continue                        # not comparable — not judged
            a = float(lhs.group(1))
            b = float(rhs.group(1))
            tol = _TOLERANCE.get(lu, 0.01)
            bad = {
                "<": a > b + tol,
                "<=": a > b + tol,
                ">": a < b - tol,
                ">=": a < b - tol,
            }[op]
            if bad:
                out.append(f"{clause.strip()!r}: {a}{lu or ''} {op} {b}{ru or ''}")
    return out


class TestTheDetectorItself:
    """Vacuity twins — the oracle must fire on the lines #983 was reported
    from, and stay quiet on the ones that were always true."""

    def test_the_reported_line_is_caught(self):
        assert failing_claims(
            "no battery assist (SoC 98% < buffer 70%) + EV surplus 100W "
            "< min charge 4100W"
        )

    def test_a_word_where_an_operand_belongs_is_caught(self):
        assert failing_claims(
            "ev plugged in + battery SoC unknown < buffer 70% "
            "(self-consumption floor)"
        )

    def test_a_true_claim_passes(self):
        assert not failing_claims(
            "no battery assist (SoC 45% < buffer 70%) + EV surplus 100W "
            "< min charge 4100W"
        )

    def test_rounding_is_not_a_lie(self):
        # ``_cw`` rounds both operands to 100 W, so a true 5 799 < 5 800 can
        # print as equal. That is the display, not a false claim.
        assert not failing_claims("surplus=5800W < min=5800W")

    def test_a_number_across_a_clause_break_is_not_borrowed(self):
        # The right-hand clause's 200 must not be pressed into service as an
        # operand of the left clause's comparison, and vice versa.
        assert failing_claims(
            "SoC 98% < buffer 70% — sun gone, solar 90W < 200W"
        ) == ["'SoC 98% < buffer 70%': 98.0% < 70.0%"]

    def test_an_inert_aside_does_not_decapitate_the_operand(self):
        # ``solar_only``'s commonest line (decide.py) puts a ` + ` INSIDE the
        # left operand's aside. Splitting on that seam handed the detector
        # ``redirect=0W < min=4140W`` — true — and a blatant lie walked past.
        assert failing_claims(
            "solar_only: surplus=9000W (bare=9000W + redirect=0W) "
            "< min=4140W (=6A) — idle"
        )
        assert not failing_claims(
            "solar_only: surplus=100W (bare=100W + redirect=0W) "
            "< min=4140W (=6A) — idle"
        )

    def test_an_aside_that_holds_the_claim_is_kept(self):
        # …but the reported line's claim LIVES in its brackets. Stripping
        # every bracket would have deleted the defect instead of catching it.
        assert failing_claims("no battery assist (SoC 98% < buffer 70%)")

    def test_mixed_units_are_not_adjudicated(self):
        assert not failing_claims("surplus 100W < min 6A")

    def test_a_thousands_separator_is_not_a_decimal_point(self):
        assert failing_claims("surplus 5,800W < min 4,100W")

    def test_an_ascii_arrow_is_not_a_relation(self):
        assert not failing_claims("ramping 6A -> 10A")
        assert not failing_claims("ramping 6A → 10A")

    def test_watt_hours_are_not_watts(self):
        assert not failing_claims("stored 5kWh < load 6000W")


# ─────────────────────────────────────────────────────────────────
# Instance 1 — _idle_bridgeable names the disjunct that fired
# ─────────────────────────────────────────────────────────────────

def _view(
    *,
    mode: str = "solar_only",
    solar_w: float = 7700.0,
    home_w: float = 7600.0,
    battery_soc: float = 97.5,
    buffer_soc: float = 70.0,
    battery_soc_known: bool = True,
    battery_may_assist_ev: bool = True,
    tariff_level: str | None = None,
    connected: bool = True,
    soc_ceiling_reached: bool = False,
) -> ChargerView:
    return ChargerView(
        power=ChargerPower(charger_id="wb", power_w=0.0, connected=connected),
        energy=ChargerEnergy(charger_id="wb"),
        mode=mode,
        config={"ev_min_current": 6, "ev_phases": 3, "ev_voltage": 230,
                "ev_max_current": 16},
        fleet=FleetContext(
            solar_w=solar_w, home_w=home_w, min_solar_w=200.0,
            battery_soc=battery_soc, buffer_soc=buffer_soc,
            battery_soc_known=battery_soc_known,
            battery_may_assist_ev=battery_may_assist_ev,
            tariff_level=tariff_level,
        ),
        soc_ceiling_reached=soc_ceiling_reached,
    )


class TestAssistBlockedNamesTheRealCause:

    def test_the_prod_shape_names_the_permission_not_the_soc(self):
        """RienduPre's install verbatim: pack at 97.5 % against a 70 %
        buffer, EV-assist switched OFF by the owner, 100 W of real surplus."""
        v = _view(battery_soc=97.5, buffer_soc=70.0,
                  battery_may_assist_ev=False)
        bridgeable, why = _idle_bridgeable(v)
        assert bridgeable is False
        assert not failing_claims(why), why
        assert "your setting" in why
        assert "< buffer" not in why

    def test_a_never_read_pack_says_so(self):
        v = _view(battery_soc=0.0, battery_soc_known=False)
        _, why = _idle_bridgeable(v)
        assert "never read" in why
        assert not failing_claims(why), why

    def test_a_genuinely_low_pack_still_quotes_the_comparison(self):
        v = _view(battery_soc=45.0, buffer_soc=70.0)
        bridgeable, why = _idle_bridgeable(v)
        assert bridgeable is False
        assert "SoC 45% < buffer 70%" in why
        assert not failing_claims(why), why

    def test_an_assisting_pack_is_bridgeable(self):
        # 97.5 % >= 70 %, permitted, read — nothing blocks assist, so the dip
        # is transient even though the surplus is tiny.
        v = _view(battery_soc=97.5, buffer_soc=70.0)
        assert _idle_bridgeable(v)[0] is True
        assert _assist_blocked_why(v.fleet) == ""

    @pytest.mark.parametrize("known", [True, False])
    @pytest.mark.parametrize("permitted", [True, False])
    @pytest.mark.parametrize("soc", [10.0, 45.0, 97.5])
    def test_the_whole_gate_matrix_tells_the_truth(self, known, permitted, soc):
        """The gate is a disjunction; every corner of it must produce a
        sentence its own operands support."""
        v = _view(battery_soc=soc, buffer_soc=70.0,
                  battery_soc_known=known, battery_may_assist_ev=permitted)
        blocked = _assist_blocked_why(v.fleet)
        # The resolver IS the gate — same truth value as the disjunction it
        # replaced, so the boolean and the sentence cannot drift apart.
        assert bool(blocked) == (
            soc < 70.0 or not known or not permitted)
        _, why = _idle_bridgeable(v)
        assert not failing_claims(why), why


class TestEveryDecideReasonHoldsUp:
    """The class sweep: run the real ``decide`` over the mode × fleet matrix
    and adjudicate every comparison it publishes."""

    @pytest.mark.parametrize("mode", [
        "solar_only", "min_plus_solar", "solar_plus_cheap",
        "solar_plus_battery", "always_max", "off",
    ])
    @pytest.mark.parametrize("soc,known,permitted", [
        (97.5, True, False),   # #983's install
        (97.5, True, True),
        (45.0, True, True),
        (0.0, False, True),
        (10.0, True, False),
    ])
    @pytest.mark.parametrize("solar_w,home_w", [
        (7700.0, 7600.0),      # sun high, house eats it — the #983 cycle
        (7700.0, 1900.0),      # real surplus
        (90.0, 400.0),         # sun gone
    ])
    @pytest.mark.parametrize("tariff", [None, "normal", "cheap"])
    def test_no_decide_reason_states_a_relation_it_refutes(
            self, mode, soc, known, permitted, solar_w, home_w, tariff):
        d = decide(_view(mode=mode, solar_w=solar_w, home_w=home_w,
                         battery_soc=soc, battery_soc_known=known,
                         battery_may_assist_ev=permitted,
                         tariff_level=tariff))
        assert not failing_claims(d.reason), d.reason


# ─────────────────────────────────────────────────────────────────
# Instance 2 — the start give-up reports what it SAW
# ─────────────────────────────────────────────────────────────────

class _FakeAdapter:
    def __init__(self, last_intent=None, min_current_a=6, max_current_a=16):
        self.last_intent = last_intent
        self.min_current_a = min_current_a
        self.max_current_a = max_current_a

    def actual_charging(self, power):
        return power.power_w > 500.0


def _charge(budget_w=4140.0):
    return ChargerDecision(
        charger_id="wb", mode="min_plus_solar",
        intent=ChargerIntent.CHARGE_AT_AMPS, commanded_amps=6,
        budget_w=budget_w, reason="min_plus_solar: surplus ok",
    )


def _plugged_never_draws():
    return ChargerView(
        power=ChargerPower(charger_id="wb", power_w=120.0, connected=True),
        energy=ChargerEnergy(charger_id="wb"),
        mode="min_plus_solar",
        config={"ev_min_current": 6, "ev_phases": 3, "ev_voltage": 230,
                "ev_max_current": 16},
        fleet=FleetContext(solar_w=3000.0, min_solar_w=200.0,
                           tariff_level=None),
    )


def _ladder_to_giveup(st, adapter, t0, *, first=False, budget_w=4140.0):
    """One full start → escalate → give-up round, faithful to the live loop
    (``last_intent`` stays CHARGE_AT_AMPS across a give-up — the #610
    harness's own lesson)."""
    view = _plugged_never_draws()

    def _f(now):
        return st.filter(_charge(budget_w), view, adapter,
                         enable_delay_s=60, disable_delay_s=300, now_ts=now)

    if first:
        adapter.last_intent = None
        _f(t0)
        d = _f(t0 + 60.0)
        assert d.intent is ChargerIntent.CHARGE_AT_AMPS
        adapter.last_intent = ChargerIntent.CHARGE_AT_AMPS
        t = t0 + 60.0
    else:
        t = t0
    for _ in range(1, 5):
        t += START_KICK_GRACE_S + 1
        d = _f(t)
        if "start backoff" in d.reason:
            return d, t
    t += START_KICK_GIVEUP_S + 5
    return _f(t), t


class TestTheGiveUpReportsWhatItSaw:

    def test_it_never_calls_the_car_full(self):
        st, ad = ChargeStability(), _FakeAdapter()
        d, _ = _ladder_to_giveup(st, ad, 1000.0, first=True)
        assert d.intent is ChargerIntent.IDLE
        assert "full" not in d.reason.lower(), d.reason
        assert "did not accept the start" in d.reason
        assert not failing_claims(d.reason), d.reason

    def test_it_points_at_the_one_thing_the_owner_can_check(self):
        st, ad = ChargeStability(), _FakeAdapter()
        d, _ = _ladder_to_giveup(st, ad, 1000.0, first=True)
        assert "charge limit" in d.reason and "departure timer" in d.reason

    def test_the_backoff_line_names_the_declines_not_a_diagnosis(self):
        st, ad = ChargeStability(), _FakeAdapter()
        t = 1000.0
        d, t = _ladder_to_giveup(st, ad, t, first=True)
        for _ in range(FULL_CAR_GIVEUP_STREAK - 1):
            d, t = _ladder_to_giveup(st, ad, t + 5.0)
        d = st.filter(_charge(), _plugged_never_draws(), ad,
                      enable_delay_s=60, disable_delay_s=300, now_ts=t + 10.0)
        assert d.intent is ChargerIntent.IDLE
        assert "start backoff" in d.reason
        assert "full" not in d.reason.lower(), d.reason
        assert "declined" in d.reason
        assert not failing_claims(d.reason), d.reason

    def test_the_hold_says_what_it_is_holding_back(self):
        """Class 82's sweep question — what keeps running while SEM stands
        down? Here it is the surplus the car is no longer being offered."""
        st, ad = ChargeStability(), _FakeAdapter()
        t = 1000.0
        d, t = _ladder_to_giveup(st, ad, t, first=True, budget_w=7536.0)
        for _ in range(FULL_CAR_GIVEUP_STREAK - 1):
            d, t = _ladder_to_giveup(st, ad, t + 5.0, budget_w=7536.0)
        d = st.filter(_charge(7536.0), _plugged_never_draws(), ad,
                      enable_delay_s=60, disable_delay_s=300, now_ts=t + 10.0)
        assert "start backoff" in d.reason          # the BACKOFF gate, not
        assert "escalation" not in d.reason         # the give-up line
        # The offer, never its funding: ``budget_w`` is grid headroom under a
        # night peak clamp and solar+pack assist in Zone 3/4, so calling it
        # "surplus going to the grid" would be this class committed by its
        # own fix (#983 review).
        assert "SEM is withholding the 7500W it had sized for this car" in d.reason
        assert "surplus goes" not in d.reason and "export" not in d.reason

    def test_a_zero_budget_hold_claims_no_cost(self):
        st, ad = ChargeStability(), _FakeAdapter()
        t = 1000.0
        d, t = _ladder_to_giveup(st, ad, t, first=True, budget_w=0.0)
        for _ in range(FULL_CAR_GIVEUP_STREAK - 1):
            d, t = _ladder_to_giveup(st, ad, t + 5.0, budget_w=0.0)
        d = st.filter(_charge(0.0), _plugged_never_draws(), ad,
                      enable_delay_s=60, disable_delay_s=300, now_ts=t + 10.0)
        assert "start backoff" in d.reason
        assert "withholding" not in d.reason

    @pytest.mark.parametrize("budget", [float("nan"), float("inf"), "8A",
                                        None, -1.0, object()])
    def test_a_text_helper_never_raises_into_the_control_path(self, budget):
        """``_meanwhile`` decorates a decision; it must not be able to end
        one. NaN and inf blew up on ``int(round(...))`` outside the guard."""
        assert isinstance(_meanwhile(_charge(budget)), str)


# ─────────────────────────────────────────────────────────────────
# Shape (C) — a cause the code has no instrument for
# ─────────────────────────────────────────────────────────────────

# Words that assert something about the CAR's internal state. Nothing in the
# EV control path can read a BMS: the whole evidence is a setpoint SEM wrote
# and a wattage it measured. A reason may report those; it may not conclude
# from them. (``soc_ceiling_reached`` IS an instrument — but it lives one
# layer up, in ``decide``, and idles before the ladder exists.)
UNINSTRUMENTED_CAUSES = ("full", "refusing", "refuses", "asleep", "broken",
                         "faulty", "dead", "satisfied")


def uninstrumented(reason: str) -> list[str]:
    """Whole words only — ``Deadline`` is not a claim that the car is dead."""
    low = reason.lower()
    return [w for w in UNINSTRUMENTED_CAUSES
            if re.search(rf"\b{w}\b", low)]


def _user_strings(module: str) -> list[tuple[int, str]]:
    """Every literal a user can end up reading out of one coordinator module:
    each ``_LOGGER.*`` call's message, and every ``reason=`` value — including
    the literal parts of an f-string, which is how every reason in this tree
    is built. Docstrings and comments are excluded: they are for the author.
    """
    import ast
    import pathlib
    from custom_components.solar_energy_management import coordinator

    path = pathlib.Path(coordinator.__file__).parent / f"{module}.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))

    def _flatten(node) -> str:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):
            return " ".join(_flatten(v) for v in node.values)
        if isinstance(node, ast.BinOp):
            return f"{_flatten(node.left)} {_flatten(node.right)}"
        return ""

    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if (isinstance(fn, ast.Attribute)
                    and isinstance(fn.value, ast.Name)
                    and fn.value.id == "_LOGGER"
                    and node.args):
                found.append((node.lineno, _flatten(node.args[0])))
            for kw in node.keywords:
                if kw.arg == "reason":
                    found.append((node.lineno, _flatten(kw.value)))
    assert found, f"the lint found no messages in {module} — it stopped working"
    return found


class TestNoVerdictDiagnosesTheCar:
    """The detector above adjudicates ARITHMETIC claims — and the half of
    class 99 #983 was actually reported for has no arithmetic in it at all.
    This is its twin: the give-up and the backoff must report the offer and
    the draw, and name no cause they cannot read."""

    def test_the_vocabulary_lint_catches_the_old_wording(self):
        assert uninstrumented(
            "stability: no draw at 10A after escalation — car not latching "
            "(full/refusing)") == ["full", "refusing"]
        assert uninstrumented("stability: full-car backoff — car declined 5 "
                              "start ladders") == ["full"]

    def test_the_give_up_and_the_backoff_diagnose_nothing(self):
        st, ad = ChargeStability(), _FakeAdapter()
        t = 1000.0
        d, t = _ladder_to_giveup(st, ad, t, first=True)
        assert "escalation" in d.reason            # really the give-up line
        assert uninstrumented(d.reason) == [], d.reason
        for _ in range(FULL_CAR_GIVEUP_STREAK - 1):
            d, t = _ladder_to_giveup(st, ad, t + 5.0)
        d = st.filter(_charge(), _plugged_never_draws(), ad,
                      enable_delay_s=60, disable_delay_s=300, now_ts=t + 10.0)
        assert "start backoff" in d.reason         # really the backoff gate
        assert uninstrumented(d.reason) == [], d.reason

    @pytest.mark.parametrize("module", ["charge_stability", "ev_control"])
    def test_no_ev_control_message_diagnoses_the_car(self, module):
        """#983 review, HIGH 4: the give-up's own comment hands the refusal to
        `ev_control`'s stall detector — which has the same evidence (a setpoint
        SEM wrote, a wattage it measured) and printed the same unsupported
        diagnosis one module over. An unswept sibling is the class surviving
        its own fix.

        An AST lint over the MESSAGES, not a substring search over the source
        (#925): every string a user can read out of these two modules, from
        every logger call and every reason, is held to the same rule. A new
        line anywhere in them fails CI, whatever it is called."""
        for where, text in _user_strings(module):
            assert uninstrumented(text) == [], f"{module}:{where}: {text!r}"


class TestFullWasNeverObservableThere:
    """One half of why "full" was a guess: WHERE SEM has the instrument, the
    ladder is unreachable. #548 idles in ``decide``, one layer up, and the
    stability filter sees an IDLE it must not bridge. The other half is
    ``TestNoVerdictDiagnosesTheCar`` — where SEM has no instrument (#610's
    own kWh-target, no-SOC-sensor case) there was never anything to read.
    Neither branch alone proves the universal; together they cover it."""

    @pytest.mark.parametrize("mode", [
        "solar_only", "min_plus_solar", "solar_plus_cheap",
        "solar_plus_battery", "always_max",
    ])
    def test_a_car_at_its_ceiling_is_idled_before_the_ladder(self, mode):
        d = decide(_view(mode=mode, solar_w=7700.0, home_w=1900.0,
                         soc_ceiling_reached=True))
        assert d.intent is ChargerIntent.IDLE
        assert d.bridgeable is False
        assert "max SOC/target reached" in d.reason

    def test_the_ladder_never_runs_on_a_ceiling_decision(self):
        st, ad = ChargeStability(), _FakeAdapter(
            last_intent=ChargerIntent.CHARGE_AT_AMPS)
        ceiling = decide(_view(mode="min_plus_solar", solar_w=7700.0,
                               home_w=1900.0, soc_ceiling_reached=True))
        out = st.filter(ceiling, _plugged_never_draws(), ad,
                        enable_delay_s=60, disable_delay_s=300, now_ts=5000.0)
        assert out.intent is ChargerIntent.IDLE
        assert "escalation" not in out.reason
        assert "backoff" not in out.reason
