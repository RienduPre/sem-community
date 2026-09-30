"""#871 step 1 — a negative export price is a COST, and the planner may see it.

``build_day_slots`` wrote ``price=max(0.0, export_rate)``. On a dynamic
feed-in tariff the export price follows EPEX and is regularly NEGATIVE —
you pay to export — so the clamp made a hostile meter and a generous one
the same number. A planner cannot prefer another sink over a cost it
cannot see, which is #871's whole complaint.

Split out of arc #921 deliberately. Everything else in that arc is behind a
default-off switch; this one line changes the planner's arithmetic for every
install on a dynamic feed-in tariff the moment a price goes negative, so it
rides on its own and carries its own proof.

``or 0.0`` still handles an ABSENT rate — the only case the clamp actually
covered, and the one every existing test asserts.
"""
from datetime import datetime, timezone

from custom_components.solar_energy_management.coordinator.day_ledger import (
    build_day_slots,
)

TZ = timezone.utc
T0 = datetime(2026, 9, 15, 10, 0, tzinfo=TZ)


def _slots(export_rate):
    return build_day_slots(
        start=T0, end=T0.replace(hour=12), day_kwh=20.0,
        sunrise=T0.replace(hour=7), sunset=T0.replace(hour=19),
        home_w_at=lambda t: 300.0, price_at=lambda t: 0.30,
        level_cheap_at=lambda t: False, export_rate=export_rate,
    )


class TestTheLedgerSeesTheCost:
    def test_a_negative_export_rate_reaches_the_slot(self):
        s = _slots(-0.05)
        assert s, "the fixture must produce at least one surplus slot"
        assert s[0].price == -0.05

    def test_a_positive_rate_is_unchanged(self):
        assert _slots(0.075)[0].price == 0.075

    def test_a_missing_rate_is_still_zero(self):
        """The only case the clamp actually covered — kept by ``or 0.0``."""
        assert _slots(None)[0].price == 0.0

    def test_the_sign_survives_every_slot_not_just_the_first(self):
        """A cost the planner sees for one slot and forgets for the rest is
        not a cost it can plan around."""
        s = _slots(-0.05)
        assert all(slot.price == -0.05 for slot in s), [x.price for x in s]
