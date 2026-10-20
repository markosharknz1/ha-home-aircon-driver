"""Pure decision logic — no Home Assistant imports, so it is unit-testable.

Three pieces:
  * average_fresh(): combine room sensors, ignoring stale/unavailable ones.
  * GuardEvaluator: tank-water protection state machine (ok/warn/critical)
    with hysteresis, rate-of-rise detection and "silence is critical".
  * decide() / corrected_setpoint(): per-zone thermostat decision and the
    offset correction that lets an external (accurate) sensor drive a unit
    whose own reading is off.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

# Guard levels (ordered: higher = worse)
LEVEL_OK = 0
LEVEL_WARN = 1
LEVEL_CRITICAL = 2
LEVEL_NAMES = {LEVEL_OK: "ok", LEVEL_WARN: "warn", LEVEL_CRITICAL: "critical"}

# Zone actions
ACT_IDLE = "idle"
ACT_HEAT = "heat"
ACT_COOL = "cool"

# (value, last_updated_epoch_seconds); value None = unavailable/unknown
Reading = tuple[float | None, float]


def fresh_values(readings: list[Reading], now: float, max_age_s: float) -> list[float]:
    """Values that are numeric and updated within max_age_s."""
    return [v for v, ts in readings if v is not None and (now - ts) <= max_age_s]


def average_fresh(
    readings: list[Reading], now: float, max_age_s: float
) -> float | None:
    vals = fresh_values(readings, now, max_age_s)
    return sum(vals) / len(vals) if vals else None


@dataclass
class GuardResult:
    level: int
    hottest: float | None
    rise_per_hour: float | None
    reason: str  # ok | above_warn | rising | above_critical | no_data | holding

    @property
    def name(self) -> str:
        return LEVEL_NAMES[self.level]


class GuardEvaluator:
    """Tank-water temperature guard.

    The hottest fresh tank sensor drives the level. No fresh data at all is
    CRITICAL: a dead sensor on a tank that might be cooking is not "fine".
    Escalation is immediate; de-escalation needs the water to fall
    `hysteresis` below the threshold of the level it is leaving.
    """

    def __init__(
        self,
        warn: float = 26.0,
        critical: float = 27.0,
        hysteresis: float = 0.5,
        rise_warn_per_hour: float = 0.5,
        stale_after_s: float = 900.0,
        rise_window_s: float = 1800.0,
    ) -> None:
        self.warn = warn
        self.critical = critical
        self.hysteresis = hysteresis
        self.rise_warn_per_hour = rise_warn_per_hour
        self.stale_after_s = stale_after_s
        self.rise_window_s = rise_window_s
        self.level = LEVEL_OK
        self._history: deque[tuple[float, float]] = deque()

    def _rise(self, now: float, hottest: float) -> float | None:
        self._history.append((now, hottest))
        while self._history and now - self._history[0][0] > self.rise_window_s:
            self._history.popleft()
        t0, v0 = self._history[0]
        span = now - t0
        if span < 600:  # need >=10 min of history for a meaningful slope
            return None
        return (hottest - v0) / (span / 3600.0)

    def update(self, now: float, readings: list[Reading]) -> GuardResult:
        vals = fresh_values(readings, now, self.stale_after_s)
        if not vals:
            self._history.clear()
            self.level = LEVEL_CRITICAL
            return GuardResult(LEVEL_CRITICAL, None, None, "no_data")

        hottest = max(vals)
        rise = self._rise(now, hottest)

        if hottest >= self.critical:
            raw, reason = LEVEL_CRITICAL, "above_critical"
        elif hottest >= self.warn:
            raw, reason = LEVEL_WARN, "above_warn"
        elif (
            rise is not None
            and rise >= self.rise_warn_per_hour
            and hottest >= self.warn - 1.0
        ):
            raw, reason = LEVEL_WARN, "rising"
        else:
            raw, reason = LEVEL_OK, "ok"

        if raw >= self.level:
            self.level = raw
        else:
            leaving = self.critical if self.level == LEVEL_CRITICAL else self.warn
            if hottest < leaving - self.hysteresis:
                self.level = raw
            else:
                reason = "holding"  # still above the release margin
        return GuardResult(self.level, hottest, rise, reason)


def decide(
    temp: float,
    target: float,
    deadband: float,
    mode: str,
    current: str = ACT_IDLE,
) -> str:
    """Thermostat decision with hysteresis.

    Start heating/cooling at target -/+ deadband/2; once running, continue
    until the target itself is reached (so it does not flap at the edge).
    mode: "off" | "heat" | "cool" | "auto".
    """
    half = deadband / 2.0
    if mode in ("heat", "auto"):
        if temp <= target - half:
            return ACT_HEAT
        if current == ACT_HEAT and temp < target:
            return ACT_HEAT
    if mode in ("cool", "auto"):
        if temp >= target + half:
            return ACT_COOL
        if current == ACT_COOL and temp > target:
            return ACT_COOL
    return ACT_IDLE


def corrected_setpoint(
    target: float,
    sensor_temp: float | None,
    unit_temp: float | None,
    max_adjust: float = 3.0,
) -> float:
    """Setpoint to send to a unit so the room (per the accurate sensor)
    reaches `target`. If the unit reads 23 while the sensor says 25, the
    unit's 22 is the sensor's 24: send target + (unit - sensor).
    """
    if sensor_temp is None or unit_temp is None:
        return target
    adj = max(-max_adjust, min(max_adjust, unit_temp - sensor_temp))
    return target + adj


def override_reached(kind: str, temp: float | None, target: float) -> bool:
    """A "cool to X" / "heat to X" override is finished once X is reached.
    With no reading it cannot finish early (the max-minutes timeout does)."""
    if temp is None:
        return False
    return temp <= target if kind == ACT_COOL else temp >= target


PROGRAM_SLOTS = 3


def programs_from_options(cfg: dict) -> list[dict]:
    """Flat options (program1_schedule, program1_mode, program1_target,
    program1_months ...) -> ordered list of program dicts. A slot without a
    schedule is unused."""
    out = []
    for i in range(1, PROGRAM_SLOTS + 1):
        sched = cfg.get(f"program{i}_schedule")
        if not sched:
            continue
        out.append(
            {
                "slot": i,
                "schedule": sched,
                "mode": cfg.get(f"program{i}_mode") or "heat",
                "target": float(cfg.get(f"program{i}_target") or 21.0),
                "months": sorted(int(m) for m in (cfg.get(f"program{i}_months") or [])),
            }
        )
    return out


def pick_program(programs: list[dict], schedule_on: dict[str, bool], month: int) -> dict | None:
    """First program (slot order) whose schedule is on and whose months
    include `month` (empty months = all year)."""
    for p in programs:
        if schedule_on.get(p["schedule"]) and (not p["months"] or month in p["months"]):
            return p
    return None
