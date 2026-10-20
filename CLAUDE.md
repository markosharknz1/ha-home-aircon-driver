# Home Aircon Driver — working notes

HACS integration, domain `home_aircon_driver`, in `custom_components/home_aircon_driver/`.
Sibling of the reef pack at `C:\Claude\ha-feedandwater-cards` (same conventions).

- `logic.py` — pure, no HA imports: GuardEvaluator, decide(), corrected_setpoint(), average_fresh().
- `controllers.py` — ZoneController (thermostat loop, drives climate/switch) and GuardController
  (tank level -> pushes into zones every 60 s; notifications). Guard override lives in the zone.
- `config_flow.py` — menu: zone | guard; OptionsFlow rewires. Entry data = kind/name/slug,
  entry.options = hardware + thresholds (merge with `cfg_of`).
- Entities: select mode, number target/deadband, sensors (zone temp/status, guard level/hottest).

Rules the owner set: tank sensors matter (27 °C = urgent cooling; heat soak is the problem);
Zigbee sensors drive control, not the aircon's own reading; no presence logic;
silent tank sensor = critical; sensor entity IDs may change, so pickers + Options rewire.
Owner's sources: Weathercloud station (outdoor), Met.no (forecast), Zigbee room sensors, tank sensors.

Tests: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests -q` (local machine has stray
global pytest plugins that break plain pytest; CI is clean). Needs `homeassistant` installed.

TODO: live-verify in the WSL rig (see reef pack); dashboard card; forecast pre-cool; releases
(HACS installs GitHub *releases*, not master).
