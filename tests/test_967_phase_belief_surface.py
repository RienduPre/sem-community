"""#967 — SEM's phase belief is refuted by its own measurements, and nobody
is told.

@alexmc1510 (Victron EVCS, Madrid, 2.1.0-beta.29) watched his car take a
fraction of the power SEM said it was giving it, night after night. Every
current SEM commands is ``watts ÷ (phases × volts)``, and his charger row
carried no ``ev_phases`` at all — so SEM used its default of 3 and believed
one amp bought 690 W.

The part that makes this a SEM bug rather than a settings mistake: **SEM
could see it, and only ever used the sight to look away.** ``WattsPerAmpLearner``
guards itself with a plausibility band around nameplate, and a 1-phase draw
under a 3-phase belief lands at 0.33 of it — dead centre of what the band
rejects. The learner even names the rejection ``phase_belief``, i.e. *the
draw fits a DIFFERENT phase count*. That name went into a dict that no
Repair, no card, no notification and not even the diagnostics download ever
read, while every amp SEM commanded kept being converted through the
nameplate the meter had already refuted — and the learner, refusing every
sample, could never correct itself either.

Both directions are live. Believing 3 where 1 is true starves the car.
Believing 1 where 3 is true commands three times the watts SEM thinks it
bought — through a peak limit and a phase guard.

Second instance in the same reply thread: the IDLE classifier called the
user's own **Minimum Solar Power** slider "sun gone" at 828 W of production
with 316 W going to the grid.
"""
from __future__ import annotations

import json
import pathlib
from unittest.mock import MagicMock

import pytest

from custom_components.solar_energy_management.coordinator.watts_per_amp import (
    MIN_SAMPLES,
    PHASE_VERDICT_REFUSALS,
    WattsPerAmpLearner,
)

_ROOT = pathlib.Path(__file__).resolve().parents[1]

# @alexmc1510's charger: 6 A minimum, 230 V, SEM believing 3 phases.
BELIEF_3 = 3 * 230.0      # 690 W/A nameplate
BELIEF_1 = 1 * 230.0      # 230 W/A nameplate


def _at(learner, amps, watts, *, cid="ev_charger", phases=3, n=10,
        nominal=BELIEF_3):
    """``n`` steady cycles of ``watts`` drawn at a commanded ``amps``."""
    for _ in range(n):
        learner.record(cid, phases=phases, commanded_amps=amps,
                       observed_w=watts, nominal_wpa=nominal)


def _feed(learner, *, cid="ev_charger", phases=3, watts_per_amp=230.0,
          nominal=BELIEF_3, ladder=(7, 16), n=10):
    """The reporter's night: SEM walks its ladder, the draw scales with the
    offer. Two setpoints, because one can never answer the question."""
    for amps in ladder:
        _at(learner, amps, amps * watts_per_amp, cid=cid, phases=phases,
            n=n, nominal=nominal)


@pytest.mark.unit
class TestTheLearnerAnswersInsteadOfOnlyRefusing:
    def test_a_one_phase_draw_under_a_three_phase_belief_is_a_verdict(self):
        """The reporter's night: the draw scales with the offer — 230 W/A at
        7 A AND at 16 A. A power cap cannot do that."""
        l = WattsPerAmpLearner()
        _feed(l)
        v = l.phase_verdict("ev_charger", 3)
        assert v is not None, "the refusals knew, and said nothing"
        assert v["believed"] == 3 and v["measured"] == 1
        assert v["samples"] >= PHASE_VERDICT_REFUSALS
        assert v["watts_per_amp"] == pytest.approx(230.0, abs=1.0)
        assert v["setpoints"] == [7, 16]

    def test_a_car_that_declines_part_of_the_offer_is_not_convicted(self):
        """THE false positive this verdict must never produce. A correctly
        configured 3-phase wallbox whose car caps at 3.7 kW reads "1 phase"
        at 16 A (231 W/A) by nearest fit — and the fix would then be to set
        1, after which SEM commands 16 A believing it bought 3.7 kW while
        11 kW flows. The ladder refuses the story: the same cap reads 2 phases
        at 8 A, because a cap gives fewer watts per amp as the offer rises."""
        l = WattsPerAmpLearner()
        _at(l, 16, 3700.0, n=12)
        _at(l, 8, 3700.0, n=12)
        # 3.7 kW at 8 A is 0.67 of nameplate — plausible, so the belief has a
        # trusted bucket and is not on trial at all.
        assert l.measured("ev_charger", 3), "the 8 A bucket should be trusted"
        assert l.phase_verdict("ev_charger", 3) is None

    def test_a_cap_low_enough_to_be_refused_everywhere_is_still_refused(self):
        """The same story with a 2.5 kW cap, where BOTH setpoints fall
        outside the band and the trusted-bucket guard cannot help: 156 W/A at
        16 A reads 0.68 phases, 250 W/A at 10 A reads 1.09. They disagree,
        which is exactly what a cap looks like and what a phase count never
        does."""
        l = WattsPerAmpLearner()
        _at(l, 16, 2500.0, n=12)
        _at(l, 10, 2500.0, n=12)
        assert not l.measured("ev_charger", 3)
        assert l.refused("ev_charger", 3) >= PHASE_VERDICT_REFUSALS
        assert l.phase_verdict("ev_charger", 3) is None

    def test_one_setpoint_can_never_answer_the_low_question(self):
        """PROD's Zoe read "1 phase" at 10.15 kW on a 32 A offer — impossible
        at 7.36 kW per phase (#804). With one setpoint the physics is
        under-determined, and silence is the honest answer."""
        l = WattsPerAmpLearner()
        _at(l, 7, 7 * 230.0, n=PHASE_VERDICT_REFUSALS + 5)
        assert l.refused("ev_charger", 3) >= PHASE_VERDICT_REFUSALS
        assert l.phase_verdict("ev_charger", 3) is None

    def test_two_setpoints_too_close_together_do_not_count(self):
        """8 A and 10 A cannot separate a cap from a phase count."""
        l = WattsPerAmpLearner()
        _feed(l, ladder=(8, 10), n=12)
        assert l.phase_verdict("ev_charger", 3) is None

    def test_the_dangerous_direction_needs_no_ladder(self):
        """Believed 1, wired 3: 690 W/A cannot come off one 230 V phase at
        all, so ONE setpoint refutes the belief outright. This is the
        direction that commands three times the watts SEM thinks it bought,
        through a peak limit — it may not be the quiet one."""
        l = WattsPerAmpLearner()
        _at(l, 16, 16 * 690.0, phases=1, n=PHASE_VERDICT_REFUSALS,
            nominal=BELIEF_1)
        v = l.phase_verdict("ev_charger", 1)
        assert v is not None and v["believed"] == 1 and v["measured"] == 3

    def test_the_dangerous_direction_survives_a_partial_draw(self):
        """The review's catch: a car capping at 7.4 kW on a 16 A offer reads
        462 W/A — two phases' worth. Nearest-fit calls that ``implausible``
        and says nothing while SEM over-commands by 2x. One phase at 230 V
        cannot buy 462 W per amp, so the LOWER BOUND still refutes 1."""
        l = WattsPerAmpLearner()
        _at(l, 16, 7400.0, phases=1, n=PHASE_VERDICT_REFUSALS,
            nominal=BELIEF_1)
        v = l.phase_verdict("ev_charger", 1)
        assert v is not None and v["measured"] == 3

    def test_a_belief_that_explains_real_draw_is_not_on_trial(self):
        """Once a bucket under this belief has earned trust, an outlier may
        not convict a configuration that demonstrably works."""
        l = WattsPerAmpLearner()
        for _ in range(MIN_SAMPLES):
            l.record("ev_charger", phases=3, commanded_amps=12,
                     observed_w=12 * 620.0, nominal_wpa=BELIEF_3)
        _feed(l)
        assert l.phase_verdict("ev_charger", 3) is None

    def test_a_handful_of_refusals_is_not_a_verdict(self):
        """MIN_SAMPLES is enough to LEARN from; contradicting the owner's own
        configuration needs more than that."""
        l = WattsPerAmpLearner()
        _feed(l, ladder=(7, 16), n=4)      # 8 refusals, two setpoints
        assert l.phase_verdict("ev_charger", 3) is None

    def test_correcting_the_setting_clears_the_verdict_by_itself(self):
        """The fix must retire the notice without a restart: the learner files
        per (charger, phases), so the corrected belief starts clean."""
        l = WattsPerAmpLearner()
        _feed(l)
        assert l.phase_verdict("ev_charger", 3) is not None
        assert l.phase_verdict("ev_charger", 1) is None

    def test_the_verdict_survives_a_restart(self):
        """#638 night 2: learned state that gates behaviour is not allowed to
        die at boot — otherwise the 20-cycle bar is re-earned every restart,
        or never reached on a car that charges in short bursts."""
        l = WattsPerAmpLearner()
        _feed(l)
        state = json.loads(json.dumps(l.as_state()))   # through storage
        restored = WattsPerAmpLearner()
        restored.restore(state)
        assert restored.phase_verdict("ev_charger", 3) == l.phase_verdict(
            "ev_charger", 3)

    def test_a_corrupt_entry_is_dropped_alone(self):
        """#563: per-entry repair, never an all-or-nothing restore."""
        l = WattsPerAmpLearner()
        _feed(l)
        state = l.as_state()
        state["refused_wpa"]["garbage"] = [1.0]
        state["refused_wpa"]["ev_charger|3|0"] = [-1.0]
        state["nominal"]["ev_charger|9"] = 0.0
        restored = WattsPerAmpLearner()
        restored.restore(state)
        assert restored.phase_verdict("ev_charger", 3) is not None

    def test_the_quoted_evidence_is_evidence_still_held(self):
        """``samples`` counts the windowed buckets, so the number a Repair
        prints never drifts from the median beside it — 40 000 cycles quoted
        against a 20-sample median is a sentence nobody can check."""
        l = WattsPerAmpLearner()
        _feed(l, n=200)
        v = l.phase_verdict("ev_charger", 3)
        assert v["samples"] <= 2 * 20 and v["samples"] >= PHASE_VERDICT_REFUSALS

    def test_the_diagnostic_row_carries_the_alternative(self):
        l = WattsPerAmpLearner()
        _feed(l)
        row = l.as_dict()["ev_charger"]["3"]
        assert set(row["implied_phases"]) == {"7", "16"}
        assert row["implied_phases"]["16"] == pytest.approx(1.0, abs=0.05)
        assert row["phase_verdict"]["measured"] == 1

    def test_the_rig_can_fail(self):
        """Bug class 8 — a verdict helper that always answers None would pass
        every 'is None' test above."""
        l = WattsPerAmpLearner()
        assert l.phase_verdict("ev_charger", 3) is None
        _feed(l)
        assert l.phase_verdict("ev_charger", 3) is not None


@pytest.mark.unit
class TestTheVerdictReachesTheOwner:
    """The point of #967: the sight has to leave the learner."""

    def _coord(self, *, observer=False, phases=None, switching=False):
        from custom_components.solar_energy_management.coordinator.coordinator import (
            SEMCoordinator,
        )
        c = MagicMock(spec=SEMCoordinator)
        c.hass = MagicMock()
        cfg = {"id": "ev_charger", "ev_voltage": 230}
        if phases is not None:
            cfg["ev_phases"] = phases
        if switching:
            cfg["ev_phase_switching_enabled"] = True
        c.config = {"ev_chargers": [cfg], "ev_voltage": 230}
        c._observer_mode = observer
        c._phase_believed = {"ev_charger": 3}
        c._phase_contradictions = {}
        c._wpa_learner = WattsPerAmpLearner()
        c._ev_charger_cfg = SEMCoordinator._ev_charger_cfg.__get__(c)
        c._wpa_phases_for = SEMCoordinator._wpa_phases_for.__get__(c)
        c._surface_phase_verdict = SEMCoordinator._surface_phase_verdict.__get__(c)
        return c

    @staticmethod
    def _spy(monkeypatch):
        import custom_components.solar_energy_management.coordinator.repair_issues as ri
        raised, cleared = [], []
        monkeypatch.setattr(ri, "raise_charger_phase_count_mismatch",
                            lambda *a, **k: raised.append(k))
        monkeypatch.setattr(ri, "clear_charger_phase_count_mismatch",
                            lambda *a, **k: cleared.append(a))
        return raised, cleared

    def test_a_mismatch_raises_the_repair(self, monkeypatch):
        raised, cleared = self._spy(monkeypatch)
        c = self._coord()
        _feed(c._wpa_learner)
        dev = MagicMock()
        dev.name = "EV Charger"
        out = c._surface_phase_verdict("ev_charger", dev, connected=True)
        assert out["measured"] == 1
        assert raised and not cleared
        assert raised[0]["believed"] == 3 and raised[0]["measured"] == 1
        assert raised[0]["name"] == "EV Charger"
        # the two numbers the owner can check against their own meter
        assert raised[0]["nominal_wpa"] == pytest.approx(690.0)
        assert raised[0]["watts_per_amp"] == pytest.approx(230.0, abs=1.0)

    def test_no_mismatch_clears_it(self, monkeypatch):
        raised, cleared = self._spy(monkeypatch)
        c = self._coord()
        assert c._surface_phase_verdict(
            "ev_charger", MagicMock(), connected=True) is None
        assert cleared and not raised

    def test_an_unchanged_accusation_is_not_refiled(self, monkeypatch):
        """``async_create_issue`` fires a registry event whenever a
        placeholder moves, and ``samples`` moves every cycle — so a naive
        raise-per-cycle re-renders the repairs card every 10 seconds for as
        long as the car charges. Once per (believed, measured), like #944's
        once-per-ceasefire serial."""
        raised, _cleared = self._spy(monkeypatch)
        c = self._coord()
        _feed(c._wpa_learner)
        for _ in range(30):
            _feed(c._wpa_learner, n=2)      # the evidence keeps growing
            c._surface_phase_verdict("ev_charger", MagicMock(), connected=True)
        assert len(raised) == 1, f"{len(raised)} registry writes for one fault"

    def test_nothing_is_argued_with_no_car_on_the_plug(self, monkeypatch):
        """#708's rule: an idle box has nothing to be wrong about this cycle.
        The standing notice is held — neither re-filed nor retracted."""
        raised, cleared = self._spy(monkeypatch)
        c = self._coord()
        _feed(c._wpa_learner)
        out = c._surface_phase_verdict(
            "ev_charger", MagicMock(), connected=False)
        assert out["measured"] == 1, "the surface still tells the truth"
        assert not raised and not cleared

    def test_observer_mode_never_accuses(self, monkeypatch):
        """SEM is not commanding the setpoint, so the amps the draw is divided
        by are not SEM's number to defend (#898/#743 hands-off rule)."""
        raised, _cleared = self._spy(monkeypatch)
        c = self._coord(observer=True)
        _feed(c._wpa_learner)
        assert c._surface_phase_verdict(
            "ev_charger", MagicMock(), connected=True) is None
        assert not raised

    def test_a_phase_switching_charger_is_not_told_to_edit_ev_phases(
            self, monkeypatch):
        """There the belief is the SEQUENCER's, not ``ev_phases`` — the Repair
        would name a field that changes nothing, so its own "clears itself"
        promise would fail. A mismatch under SEM's own commanded count is
        #804's not-taking question and wants a different notice."""
        raised, _cleared = self._spy(monkeypatch)
        c = self._coord(switching=True)
        _feed(c._wpa_learner)
        assert c._surface_phase_verdict(
            "ev_charger", MagicMock(), connected=True) is None
        assert not raised


@pytest.mark.unit
class TestTheCycleActuallyAsksTheLearner:
    """The wiring itself, pinned structurally (#925: a contract about the
    code, not its spelling). Deleting the one call site left every other test
    in this file green."""

    def test_the_per_charger_loop_calls_the_surface(self):
        from .ast_contracts import call_sites
        sites = call_sites("_surface_phase_verdict")
        assert sites, (
            "nothing in production asks the learner for a phase verdict — "
            "the Repair cannot ship"
        )
        assert all(f == "coordinator/coordinator.py" for f, _l, _k in sites)
        # the connected term is what keeps an idle box from being argued with
        assert all("connected" in kw for _f, _l, kw in sites), sites


class TestTheDownloadCarriesTheEvidence:
    """#967 had to be answered from a screenshot and a multiplication, because
    the one block that holds the answer was never in the file. Driven through
    the real builder, not grepped for."""

    @pytest.mark.asyncio
    async def test_the_download_carries_the_learner_and_the_phase_belief(
            self, tmp_path):
        from custom_components.solar_energy_management.diagnostics import (
            async_get_config_entry_diagnostics,
        )
        from custom_components.solar_energy_management.coordinator.coordinator import (
            SEMCoordinator,
        )

        hass = MagicMock()
        hass.config = MagicMock()
        hass.config.config_dir = str(tmp_path)

        async def _executor(func, *a, **k):
            return func(*a, **k)
        hass.async_add_executor_job = _executor

        entry = MagicMock()
        entry.entry_id = "e967"
        entry.version = 1
        entry.title = "SEM"
        entry.domain = "solar_energy_management"
        entry.data, entry.options = {}, {}

        learner = WattsPerAmpLearner()
        _feed(learner)
        coord = MagicMock()
        coord.last_update_success = True
        coord._observer_mode = False
        coord._load_manager = None
        coord._energy_dashboard_config = None
        coord._wpa_learner = learner
        coord._charger_adapters = {"ev_charger": None}
        coord.config = {"ev_chargers": [{"id": "ev_charger", "ev_phases": 3,
                                         "ev_voltage": 230}]}
        dev = MagicMock()
        dev.name = "EV Charger"
        coord._ev_devices = {"ev_charger": dev}
        coord._ev_charger_cfg = SEMCoordinator._ev_charger_cfg.__get__(coord)
        coord._wpa_phases_for = SEMCoordinator._wpa_phases_for.__get__(coord)
        coord.data = {
            "ev_watts_per_amp": learner.as_dict(),
            "solar_power": 1.0,
        }
        entry.runtime_data = coord
        hass.data = {"solar_energy_management": {entry.entry_id: coord}}

        out = await async_get_config_entry_diagnostics(hass, entry)
        # the measured table + the refusals, at all — the gap that made #967
        # unanswerable from the file
        assert out["ev_watts_per_amp"]["ev_charger"]["3"][
            "refusal_reasons"]["phase_belief"] == PHASE_VERDICT_REFUSALS
        assert "ev_watts_per_amp_replay" in out
        # and the belief beside it, so the reader does not have to multiply
        phases = out["charger_adapters"]["ev_charger"]["phases"]
        assert phases["configured"] == 3 and phases["believed"] == 3
        assert phases["verdict"]["measured"] == 1


@pytest.mark.unit
class TestTheRepairIsLegibleEverywhere:
    LANGS = sorted((_ROOT / "translations").glob("*.json"))

    def test_the_issue_exists_in_every_shipped_language(self):
        assert len(self.LANGS) >= 16
        strings = json.loads(
            (_ROOT / "strings.json").read_text(encoding="utf-8"))
        assert "charger_phase_count_mismatch" in strings["issues"]
        for path in self.LANGS:
            issues = json.loads(path.read_text(encoding="utf-8"))["issues"]
            assert "charger_phase_count_mismatch" in issues, path.name

    def test_every_language_keeps_every_placeholder(self):
        """A dropped placeholder prints nothing; an invented one raises
        KeyError at render time and turns a Repair into a traceback (#674)."""
        import re
        want = {"name", "believed", "measured", "nominal", "watts_per_amp",
                "cycles"}
        for path in [_ROOT / "strings.json", *self.LANGS]:
            body = json.loads(path.read_text(encoding="utf-8"))[
                "issues"]["charger_phase_count_mismatch"]
            found = set(re.findall(r"\{([a-z_]+)\}",
                                   body["title"] + body["description"]))
            assert found == want, f"{path.name}: {found ^ want}"

    def test_the_docs_link_resolves(self):
        from custom_components.solar_energy_management.coordinator.repair_issues import (
            _DOCS_ANCHORS,
        )
        import re
        anchor = _DOCS_ANCHORS["charger_phase_count_mismatch"]
        text = (_ROOT / "docs" / "TROUBLESHOOTING.md").read_text(encoding="utf-8")

        def slug(h):
            h = h.strip("# ").strip().lower()
            h = re.sub(r"[^\w\- ]", "", h)
            return re.sub(r"\s+", "-", h)

        assert anchor in {slug(h) for h in re.findall(r"^#+ .+$", text, re.M)}

    def test_the_placeholders_the_helper_passes_are_the_ones_declared(
            self, monkeypatch):
        """The helper and the string are a hand-maintained pair (class 24) —
        a missing one prints nothing, an extra one raises KeyError at render
        time. Checked by CALLING the raiser and rendering the real string."""
        import re
        from custom_components.solar_energy_management.coordinator import (
            repair_issues as ri,
        )
        seen = {}
        monkeypatch.setattr(ri.ir, "async_create_issue",
                            lambda *a, **k: seen.update(k))
        ri.raise_charger_phase_count_mismatch(
            MagicMock(), "ev_charger", name="EV Charger", believed=3,
            measured=1, watts_per_amp=230.0, nominal_wpa=690.0, samples=20)
        passed = seen["translation_placeholders"]
        for path in [_ROOT / "strings.json", *self.LANGS]:
            body = json.loads(path.read_text(encoding="utf-8"))[
                "issues"]["charger_phase_count_mismatch"]
            for text in (body["title"], body["description"]):
                rendered = text.format(**passed)      # KeyError = the bug
                assert not re.search(r"\{[a-z_]+\}", rendered), path.name
        assert passed["measured"] == "1" and passed["believed"] == "3"


@pytest.mark.unit
class TestAThresholdIsNotAFactAboutTheSky:
    """Second instance, same reporter, same reply: the IDLE classifier named
    the user's own slider "sun gone" while his panels made 828 W and 316 W of
    it went to the grid."""

    def _view(self, solar_w):
        from custom_components.solar_energy_management.coordinator.charger_types import (
            ChargerEnergy, ChargerPower, ChargerView, FleetContext,
        )
        return ChargerView(
            power=ChargerPower(charger_id="ev_charger", power_w=0.0,
                               connected=True, charging=False),
            energy=ChargerEnergy(charger_id="ev_charger"),
            mode="solar_only",
            config={"ev_min_current": 6, "ev_phases": 3, "ev_voltage": 230,
                    "ev_max_current": 16},
            fleet=FleetContext(solar_w=solar_w, home_w=319.0,
                               battery_soc=100.0, min_solar_w=1000.0),
        )

    def test_the_reason_names_the_setting_it_crossed(self):
        from custom_components.solar_energy_management.coordinator.decide import (
            _idle_bridgeable,
        )
        ok, why = _idle_bridgeable(self._view(828.0))
        assert ok is False
        assert "800W < the 1000W minimum" in why
        assert "sun" not in why.lower(), (
            "the sentence claims a fact about the sky that the same cycle's "
            "own export refutes"
        )

    def test_above_the_floor_is_still_bridgeable(self):
        from custom_components.solar_energy_management.coordinator.decide import (
            _idle_bridgeable,
        )
        ok, _why = _idle_bridgeable(self._view(5000.0))
        assert ok is True
