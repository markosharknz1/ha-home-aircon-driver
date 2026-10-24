"""Constants for Home Aircon Driver."""
DOMAIN = "home_aircon_driver"

CONF_KIND = "kind"
KIND_ZONE = "zone"
KIND_GUARD = "guard"

CONF_NAME = "name"
CONF_SLUG = "slug"

# Zone
CONF_TEMP_SENSORS = "temperature_sensors"
CONF_CLIMATES = "climates"
CONF_HEATERS = "heaters"
CONF_COOLERS = "coolers"
CONF_DAMPERS = "dampers"
CONF_SENSOR_MAX_AGE = "sensor_max_age_min"
CONF_SENSOR_TIMEOUTS = "sensor_timeouts"  # {entity_id: minutes}, per-sensor override
CONF_REFRESH = "poll_quiet_sensors"  # opt-in; see README (can mask a dead sensor)

# Guard
CONF_TANK_SENSORS = "tank_sensors"
CONF_ZONES = "zones"
CONF_WARN = "warn_temp"
CONF_CRITICAL = "critical_temp"
CONF_HYSTERESIS = "hysteresis"
CONF_WARN_SETPOINT = "warn_setpoint"
CONF_CRITICAL_SETPOINT = "critical_setpoint"
CONF_STALE_MIN = "stale_min"
CONF_NOTIFY = "notify_service"

DEFAULT_TARGET = 22.0
DEFAULT_DEADBAND = 1.0
DEFAULT_SENSOR_MAX_AGE = 30
DEFAULT_WARN = 26.0
DEFAULT_CRITICAL = 27.0
DEFAULT_HYSTERESIS = 1.0
DEFAULT_WARN_SETPOINT = 22.0
DEFAULT_CRITICAL_SETPOINT = 18.0
DEFAULT_STALE_MIN = 15

MODES = ["off", "heat", "cool", "auto"]
TICK_SECONDS = 60
