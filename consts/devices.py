"""Device discovery patterns and load management constants for SEM."""
from typing import Final

# Device discovery patterns for load management
LOAD_MANAGEMENT_DEVICE_PATTERNS: Final = {
    # Shelly devices
    "shelly": {
        "switch_pattern": "switch.shelly_*",
        "power_pattern": "sensor.shelly_*_power",
        "description": "Shelly Smart Switch"
    },
    # ESPHome devices
    "esphome": {
        "switch_pattern": "switch.*_switch",
        "power_pattern": "sensor.*_power",
        "description": "ESPHome Device"
    },
    # Generic smart switches (Tasmota, custom, etc.)
    "smart_switch": {
        "switch_pattern": "switch.*",
        "power_pattern": "sensor.*_power",
        "description": "Smart Switch with Power Monitoring"
    }
}

# EV Charger manufacturer groupings

# (#915) EV_CHARGER_MANUFACTURERS lived here: 11 brands x entity-id globs,
# the pre-registry detection matrix. It has been dead since #814 moved
# detection to the entity registry — the only reference left was a docstring
# example — and it named boxes (tesla_wall_connector, myenergi_zappi) that
# exist nowhere else in SEM. Deleting it rather than carrying it: brand
# knowledge now lives in ONE place per question — hardware_matrix.py for what
# SEM claims to support, _BRAND_HINTS for how detection recognises it, and
# consts/integration_roster.py for what the ecosystem publishes.

SYSTEM_COMPONENT_WEIGHTS: Final = {
    "solar_power": 25,      # Essential - solar production
    "grid_power": 25,       # Essential - grid monitoring
    "battery_soc": 20,      # Important - battery state
    "battery_power": 15,    # Important - battery power
    "ev_connected": 8,      # Useful - EV detection
    "ev_charging": 8,       # Useful - EV charging state
    "ev_power": 7,          # Useful - EV power monitoring
    "battery_temp": 5,      # Nice to have - battery temperature
    "ev_current": 3,        # Nice to have - EV current
    "ev_energy": 2          # Nice to have - EV energy tracking
}

# Confidence thresholds for system validation
CONFIDENCE_EXCELLENT: Final = 90    # Complete system, same manufacturer
CONFIDENCE_GOOD: Final = 70         # Most components found, mixed manufacturers
CONFIDENCE_BASIC: Final = 50        # Minimum required components only
CONFIDENCE_POOR: Final = 30         # Missing important components


# (#801) SG-Ready contacts that are not switches.
#
# The SG-Ready standard's two contacts are a pair of booleans, but the HA
# surface that carries them varies by hardware: a relay switch on most heat
# pumps, and on a Buderus/Bosch behind EMS-ESP a pair of ``text`` entities
# holding a bit string (``010000000000000``). Writing a contact's boolean is
# the same operation either way — only the service and the payload differ.
#
# A domain ABSENT from this table is a TOGGLE domain, driven by
# ``homeassistant.turn_on``/``turn_off`` exactly as SG-Ready always has been.
# A domain PRESENT is a VALUE domain: the user gives the ON and the OFF value
# for that contact and SEM writes it verbatim.
CONTACT_VALUE_SERVICES: Final[dict] = {
    # domain: (service domain, service, payload key)
    "text":         ("text", "set_value", "value"),
    "input_text":   ("input_text", "set_value", "value"),
    "number":       ("number", "set_value", "value"),
    "input_number": ("input_number", "set_value", "value"),
    "select":       ("select", "select_option", "option"),
    "input_select": ("input_select", "select_option", "option"),
}

# Every domain a SG-Ready contact may point at, in picker order: the two
# toggle domains first (what every existing install uses), then the value
# domains. Used by the config flow's EntitySelector for both contacts.
SG_READY_CONTACT_DOMAINS: Final[list] = [
    "switch", "input_boolean",
] + list(CONTACT_VALUE_SERVICES)
