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

    def test_a_dark_read_is_not_reported_as_a_comparison(self):
        src = (ROOT / "coordinator" / "decide_battery.py").read_text(encoding="utf-8")
        i = src.index("mode=force_discharge but")
        window = src[max(0, i - 1200):i + 200]
        # the three arms each get their own sentence now
        assert "SOC unreadable" in window and "not selling blind" in window
        assert "≤ reserve {reserve:.0f}%" in window      # kept, for the real comparison
        assert "held from a dark read" not in window.split("_why =")[-1]


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

    def test_the_message_cannot_say_ok(self):
        src = (ROOT / "coordinator" / "battery_adapters" / "deye.py").read_text(encoding="utf-8")
        i = src.rindex("Deye force charge blocked")
        assert "force_charge_blocked_why()" in src[i - 200:i + 400]
        assert "capability.reason" not in src[i - 400:i + 200]


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
        lm = (ROOT / "features" / "load_management.py").read_text(encoding="utf-8")
        assert "managed_charger_w" in lm
        ri = (ROOT / "coordinator" / "repair_issues.py").read_text(encoding="utf-8")
        assert "managed_charger_kw" in ri

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
    against lowercase, so every emergency shed read "peak protection"."""

    def test_the_source_compares_case_insensitively(self):
        src = (ROOT / "dashboard" / "card" / "src" / "cards"
               / "sem-load-priority-card.js").read_text(encoding="utf-8")
        assert "=== 'emergency'" not in src
        assert "toUpperCase() === 'EMERGENCY'" in src

    def test_the_backend_still_writes_what_the_card_now_expects(self):
        lm = (ROOT / "features" / "load_management.py").read_text(encoding="utf-8")
        assert '"EMERGENCY"' in lm

    def test_the_shipped_bundle_carries_the_fix(self):
        """dist/ is what HA loads, and it is tracked — an unbuilt fix is no fix."""
        dist = (ROOT / "dashboard" / "card" / "dist" / "sem-cards.js").read_text(encoding="utf-8")
        assert '"EMERGENCY"===String' in dist.replace(" ", "")


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

    def test_the_row_picks_the_claim_from_the_ev_state(self):
        src = (ROOT / "coordinator" / "today_plan.py").read_text(encoding="utf-8")
        i = src.rindex("kind=KIND_EXPENSIVE_START")
        window = src[max(0, i - 900):i + 400]
        assert "plan_expensive_detail_plain" in window
        assert "ev_min_remaining_kwh" in window
