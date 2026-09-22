"""#992, class 99 — the verdict names the read that actually went dark.

``inputs_degraded`` is raised by THREE different gates:

* an entity that will not read at all;
* a battery power no battery could produce (#902);
* a solar zero the energy balance refutes (#988).

The charger's hold verdict said **"sensor unavailable"** for all three. Two
of them are sensors that answered perfectly well with a number SEM chose to
disbelieve, so a user reading that line went hunting for a broken entity
that was working. #988 added one of those two the same week, which is how
this surfaced: our own recent fix made an older message less true.

Seen live on the .175 rig, 21.09: ``ev:keba_… charge_at_amps | inputs
degraded (sensor unavailable) — holding 10A``.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.solar_energy_management.coordinator.charge_stability import (
    _dark_inputs_phrase,
)


def _view(dark):
    return SimpleNamespace(fleet=SimpleNamespace(dark_inputs=dark))


@pytest.mark.unit
class TestTheVerdictNamesWhatWentDark:

    def test_a_solar_read_the_balance_refuted_is_not_a_missing_sensor(self):
        phrase = _dark_inputs_phrase(_view(("solar",)))
        assert "solar" in phrase
        assert "unavailable" not in phrase, (
            "the sensor answered — SEM disbelieved the number (#988)")

    def test_a_battery_value_no_battery_could_produce_says_battery(self):
        assert "battery" in _dark_inputs_phrase(_view(("battery",)))

    def test_several_dark_reads_are_all_named(self):
        phrase = _dark_inputs_phrase(_view(("battery", "solar")))
        assert "battery" in phrase and "solar" in phrase

    def test_no_names_falls_back_to_the_honest_general_phrase(self):
        """Not to the specific claim that was wrong."""
        phrase = _dark_inputs_phrase(_view(()))
        assert "dark" in phrase
        assert "unavailable" not in phrase

    def test_a_fleet_that_never_carried_the_field_does_not_crash(self):
        assert _dark_inputs_phrase(SimpleNamespace(fleet=SimpleNamespace())) 
        assert _dark_inputs_phrase(SimpleNamespace())


@pytest.mark.unit
class TestTheNamesTravelFromTheReader:

    def test_power_readings_carries_the_names(self):
        from custom_components.solar_energy_management.coordinator.types import (
            PowerReadings,
        )
        assert PowerReadings().dark_inputs == ()

    def test_the_fleet_context_carries_them_too(self):
        from custom_components.solar_energy_management.coordinator.charger_types import (
            FleetContext,
        )
        assert FleetContext().dark_inputs == ()

    def test_build_view_threads_them(self):
        """An AST contract: the seam must pass the field, not re-derive it."""
        from .ast_contracts import call_kwargs
        from custom_components.solar_energy_management.coordinator import build_view

        kwargs = call_kwargs(build_view.build_charger_view, "FleetContext")
        assert any("dark_inputs" in k for k in kwargs), kwargs
