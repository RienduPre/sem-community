"""#992 — the class-99 sweep: nine verdicts that named a cause their scope refutes.

Class 99 was minted from two instances on one install (#983). A third turned
up the next day on another (#967: ``sun gone`` printed at 828 W of production
while the house exported 316 W). Three in two days is a class that has not
been swept — so three adversarial reviewers swept it, and these are the pins
for what they found.

The class's own sweep question, applied literally: **can the reader's own
state contradict this sentence?** Not "is the quoted number the real gate" —
that weaker question is what cleared ``decide.py``'s ``sun gone`` when the
class was written, one day before a user hit it.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _issue(key: str) -> dict:
    return json.loads((ROOT / "strings.json").read_text(encoding="utf-8"))["issues"][key]


# ═══════════════════════════════════════════════════════════════════════
# The decision surfaces
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestTheExportRowSaysWhichStateItIsIn:
    """`refused` means the adapter declined the write — the meter is NOT held.
    One sentence served all three states, on the very surface someone reads to
    verify the #955 zero-export guarantee."""

    def _published(self, standing):
        from custom_components.solar_energy_management.coordinator import actuate_export as ax
        seen = {}

        class _C:
            def publish_observer_decision(self, **kw):
                seen.update(kw)

        ax._publish_standing(_C(), standing)
        return seen.get("reason", "")

    def test_refused_does_not_claim_the_meter_is_held(self):
        said = self._published("refused")
        assert "refused" in said and "NOT held" in said
        assert "holding the meter shut" not in said

    def test_engaged_still_says_it_is_holding(self):
        assert "holding the meter shut" in self._published("engaged")

    def test_releasing_says_it_is_handing_back(self):
        said = self._published("releasing")
        assert "handing the meter back" in said
        assert "holding the meter shut" not in said


@pytest.mark.unit
class TestTheSolarGateIsNotDarkness:
    """@alexmc1510 read `sun gone (solar 800W < 1000W)` while his own
    dashboard showed 828 W of production and 316 W of export (#967)."""

    def _reason(self, solar_w, min_solar_w):
        from custom_components.solar_energy_management.coordinator.decide import (
            _idle_bridgeable,
        )
        from types import SimpleNamespace
        view = SimpleNamespace(fleet=SimpleNamespace(
            solar_w=solar_w, min_solar_w=min_solar_w, tariff_level=None,
            battery_soc=50.0, buffer_soc=70.0, battery_soc_known=True,
            battery_may_assist_ev=True))
        view.mode = "solar_only"
        return _idle_bridgeable(view)

    def test_it_names_the_gate_not_the_sky(self):
        bridgeable, said = self._reason(800.0, 1000.0)
        assert bridgeable is False
        assert "sun gone" not in said
        assert "solar minimum" in said and "800" in said and "1000" in said

    def test_the_gate_itself_is_unchanged(self):
        """The wording moved; the threshold did not — just below still trips
        it, and the message still quotes both numbers. (Above the gate the
        function goes on to read tariff/battery terms this stub does not
        carry, so the boundary is pinned from below.)"""
        bridgeable, said = self._reason(999.0, 1000.0)
        assert bridgeable is False
        assert "solar minimum" in said


@pytest.mark.unit
class TestTheHoldDoesNotAssertAnInequalityNobodyEvaluated:
    """The guard is a three-way OR and only one arm is a comparison. A pack
    held at 80 % from a dark read was told it was ≤ a 70 % reserve."""

    def _reason(self, *, available, soc, reserve=70.0):
        from types import SimpleNamespace
        from custom_components.solar_energy_management.coordinator.decide_battery import (
            decide_battery,
        )
        rt = SimpleNamespace(battery_id="b1", available=available, soc=soc)
        view = SimpleNamespace(
            runtime=rt, mode="force_discharge", reserve_soc=reserve,
            config={"battery_max_discharge_power": 5000.0},
        )
        try:
            return decide_battery(view).reason
        except Exception:                      # the view shape is richer live
            pytest.skip("decide_battery needs the full view here")

    def test_the_sentence_is_composed_per_arm_not_hard_coded(self):
        """Structural (AST): the hold's reason interpolates a resolver
        variable rather than baking the comparison into one f-string — the
        three arms of the guard can no longer share one claim."""
        import ast
        tree = ast.parse((ROOT / "coordinator" / "decide_battery.py")
                         .read_text(encoding="utf-8"))
        holds = [n for n in ast.walk(tree) if isinstance(n, ast.JoinedStr)
                 and any(isinstance(v, ast.Constant)
                         and "mode=force_discharge but" in str(v.value)
                         for v in n.values)]
        assert holds, "the hold's reason is gone — this pin needs rewriting"
        for node in holds:
            literal = "".join(str(v.value) for v in node.values
                              if isinstance(v, ast.Constant))
            assert "≤ reserve" not in literal, (
                "the comparison is still hard-coded into the sentence every "
                "arm of the guard shares")
            assert any(isinstance(v, ast.FormattedValue) for v in node.values)


@pytest.mark.unit
class TestTheDeyeBlockNamesTheGateThatIsShut:
    """`supports_forced_charge` ANDs thirteen terms; the message named three
    and otherwise quoted a capability reason that says `ok` whenever the
    ENTITIES validate — so a default `deye_program_control` produced
    "blocked: ok"."""

    def test_one_resolver_exists_and_covers_the_gates(self):
        from custom_components.solar_energy_management.coordinator.battery_adapters import (
            deye,
        )
        fn = getattr(deye.DeyeBatteryAdapter, "force_charge_blocked_why", None)
        assert callable(fn), "no resolver — the boolean and the sentence can still disagree"
        src = (ROOT / "coordinator" / "battery_adapters" / "deye.py").read_text(encoding="utf-8")
        body = src[src.index("def force_charge_blocked_why"):src.index("def supports_forced_charge")]
        for gate in ("_program_control", "_actuation_enabled", "_observer_mode",
                     "_unsafe_latched", "snapshot_supported", "readback_supported",
                     "restore_supported", "_config_entry_id", "_battery_id",
                     "_readback_attempts", "_readback_delay_s"):
            assert gate in body, f"{gate} can shut the gate but the resolver never names it"

    def test_the_refusal_is_built_from_the_resolver(self):
        """Structural (AST): the message comes from the resolver, so the
        boolean and the sentence are one evaluation — it can no longer quote
        a capability reason that covers four of the thirteen gates."""
        from custom_components.solar_energy_management.coordinator.battery_adapters import (
            deye,
        )
        from custom_components.solar_energy_management.tests import ast_contracts
        cmd = deye.DeyeBatteryAdapter.command_force_charge
        assert ast_contracts.calls(cmd, "force_charge_blocked_why"), (
            "command_force_charge does not ask the resolver")
        assert not ast_contracts.reads_attribute(cmd, "capability", "reason"), (
            "the refusal still quotes capability.reason")


# ═══════════════════════════════════════════════════════════════════════
# The surfaces that send someone to do something
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestTheRepairsHedgeWhereTheyMust:
    def test_futile_shed_names_the_charger_sem_already_manages(self):
        """`_shed_plan` skips EVERY charger before it checks registration, so
        the draw is "uncontrolled" even when SEM controls it — and the Repair
        told people to add a charger they had already added."""
        it = _issue("load_shed_futile")
        assert "{managed_charger_kw}" in it["description"]
        assert "already manages" in it["description"]
        assert "adding it again will not help" in it["description"]
        from custom_components.solar_energy_management.tests import ast_contracts
        sites = ast_contracts.call_sites("raise_load_shed_futile")
        assert sites, "nothing raises the futile-shed Repair"
        for rel, line, kwargs in sites:
            assert "managed_charger_kw" in kwargs, (
                f"{rel}:{line} files the Repair without the charger figure")

    def test_force_discharge_unsupported_does_not_blame_the_firmware_as_fact(self):
        d = _issue("battery_force_discharge_unsupported")["description"]
        assert "firmware simply does not implement" not in d
        assert "ruled out ONE cause" in d and "{error}" in d

    def test_the_failsafe_repair_offers_the_second_controller(self):
        """Its sibling, raised from the same edge in the same function, has
        always offered it."""
        d = _issue("charger_failsafe_suspected")["description"]
        assert "another controller" in d.lower()
        war = _issue("charger_stop_war_stand_down")["description"]
        assert "another controller" in war.lower()      # the sibling, unchanged

    def test_every_repair_text_survives_its_own_placeholders(self):
        """A Repair that cannot render is worse than one that misleads."""
        import re
        issues = json.loads((ROOT / "strings.json").read_text(encoding="utf-8"))["issues"]
        en = json.loads((ROOT / "translations" / "en.json").read_text(encoding="utf-8"))["issues"]
        for key in ("load_shed_futile", "battery_force_discharge_unsupported",
                    "charger_failsafe_suspected"):
            assert issues[key] == en[key], f"{key}: strings.json and en.json disagree"
            for field in ("title", "description"):
                for ph in re.findall(r"\{(\w+)\}", issues[key][field]):
                    assert ph.isidentifier(), (key, ph)


@pytest.mark.unit
class TestTheCardLabelsAnEmergencyShedAsOne:
    """The backend only ever writes EMERGENCY/PROGRESSIVE; the card compared
    against lower case, so every emergency shed read "peak protection".

    The RULE is pinned where it can be executed —
    ``dashboard/card/test/shed-reason.test.js``, in the card-test job. What
    is left here is the question Python can answer: does the tracked bundle
    HA actually loads carry the fix, or is `dist/` stale?"""

    def test_the_backend_still_writes_what_the_helper_expects(self):
        """Structural (AST): the shed reason the card reads is whatever is
        passed to ``_shed_toward`` — and every call passes an UPPER-CASE
        literal. The helper is case-insensitive now, so this pin is about the
        contract not moving silently rather than about the helper breaking."""
        import ast
        lm = ast.parse((ROOT / "features" / "load_management.py").read_text(encoding="utf-8"))
        passed = {a.value for n in ast.walk(lm)
                  if isinstance(n, ast.Call)
                  and getattr(n.func, "attr", None) == "_shed_toward"
                  for a in n.args
                  if isinstance(a, ast.Constant) and isinstance(a.value, str)}
        assert passed, "nothing calls _shed_toward with a literal — pin needs rewriting"
        assert "EMERGENCY" in passed, f"the emergency reason changed: {passed}"
        assert all(p == p.upper() for p in passed), (
            f"a shed reason is no longer upper case: {passed}")

    def test_the_shipped_bundle_is_not_stale(self):
        """dist/ is tracked and is the only thing HA loads; an unbuilt fix is
        no fix. The helper's own name is the marker."""
        dist = (ROOT / "dashboard" / "card" / "dist" / "sem-cards.js").read_text(encoding="utf-8")
        assert "shed_emergency" in dist
        assert '"EMERGENCY"===String' in dist.replace(" ", ""), (
            "the bundle predates the shed-label fix — run npm run build")


@pytest.mark.unit
class TestThePlanClaimsThePauseOnlyWhenThereIsOne:
    def test_the_plain_variant_exists_in_every_language(self):
        d = json.loads((ROOT / "dashboard" / "translations.json").read_text(encoding="utf-8"))
        langs = [k for k, v in d.items() if isinstance(v, dict) and "plan_expensive_detail" in v]
        assert len(langs) >= 16
        for lang in langs:
            plain = d[lang].get("plan_expensive_detail_plain")
            assert plain, f"{lang} has no plain variant"
            assert "{end}" in plain and "{price}" in plain
            assert len(plain) < len(d[lang]["plan_expensive_detail"])

    def test_the_row_makes_the_claim_conditional(self):
        """Structural (AST): the expensive row's ``detail`` is chosen, not
        fixed — and the plain variant is one of the choices."""
        import ast
        tree = ast.parse((ROOT / "coordinator" / "today_plan.py")
                         .read_text(encoding="utf-8"))
        rows = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                and getattr(n.func, "id", None) == "PlanRow"
                and any(k.arg == "kind"
                        and getattr(k.value, "id", "") == "KIND_EXPENSIVE_START"
                        for k in n.keywords)]
        assert rows, "the expensive row is gone — this pin needs rewriting"
        for row in rows:
            detail = next(k.value for k in row.keywords if k.arg == "detail")
            assert isinstance(detail, ast.IfExp), (
                "the expensive row still asserts the pause unconditionally")
            names = {c.value for c in ast.walk(detail)
                     if isinstance(c, ast.Constant) and isinstance(c.value, str)}
            assert "plan_expensive_detail_plain" in names
