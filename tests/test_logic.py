import importlib.util
import pathlib
import sys

_p = pathlib.Path(__file__).parent.parent / "custom_components/home_aircon_driver/logic.py"
_s = importlib.util.spec_from_file_location("aircon_logic", _p)
logic = importlib.util.module_from_spec(_s)
sys.modules["aircon_logic"] = logic
_s.loader.exec_module(logic)

G = logic.GuardEvaluator
OK, WARN, CRIT = logic.LEVEL_OK, logic.LEVEL_WARN, logic.LEVEL_CRITICAL


def test_average_ignores_stale_and_unavailable():
    r = [(20.0, 1000), (24.0, 1000), (99.0, 0), (None, 1000)]
    assert logic.average_fresh(r, now=1100, max_age_s=300) == 22.0
    assert logic.average_fresh([(None, 1000)], 1100, 300) is None


def test_guard_levels_and_hysteresis():
    g = G(warn=26, critical=27, hysteresis=0.5)
    assert g.update(0, [(25.0, 0)]).level == OK
    assert g.update(60, [(26.2, 60)]).level == WARN
    assert g.update(120, [(27.1, 120)]).level == CRIT
    # still above 27-0.5 -> hold critical
    r = g.update(180, [(26.8, 180)])
    assert r.level == CRIT and r.reason == "holding"
    # below 26.5 -> drops to warn (still >= 26)
    assert g.update(240, [(26.3, 240)]).level == WARN
    # warn holds until below 25.5
    assert g.update(300, [(25.8, 300)]).level == WARN
    assert g.update(360, [(25.4, 360)]).level == OK


def test_guard_uses_hottest_sensor():
    g = G()
    assert g.update(0, [(24.0, 0), (27.5, 0)]).level == CRIT


def test_guard_silence_is_critical():
    g = G(stale_after_s=900)
    assert g.update(0, [(None, 0)]).reason == "no_data"
    assert g.update(2000, [(24.0, 0)]).level == CRIT  # reading too old
    # recovery once data is back and cool
    assert g.update(2100, [(24.0, 2100)]).level == OK


def test_guard_rising_rate_warns_early():
    g = G(warn=26, critical=27, rise_warn_per_hour=0.5)
    g.update(0, [(25.0, 0)])
    r = g.update(1200, [(25.5, 1200)])  # +0.5 in 20 min = 1.5 C/h, 25.5 >= 25
    assert r.level == WARN and r.reason == "rising"


def test_decide_hysteresis():
    d = logic.decide
    assert d(19.4, 20, 1.0, "heat") == "heat"
    assert d(19.8, 20, 1.0, "heat") == "idle"            # inside band, idle
    assert d(19.8, 20, 1.0, "heat", "heat") == "heat"    # keeps heating to target
    assert d(20.0, 20, 1.0, "heat", "heat") == "idle"
    assert d(24.6, 24, 1.0, "cool") == "cool"
    assert d(24.2, 24, 1.0, "cool", "cool") == "cool"
    assert d(24.0, 24, 1.0, "cool", "cool") == "idle"
    assert d(10, 24, 1.0, "off") == "idle"
    assert d(30, 24, 1.0, "heat") == "idle"
    assert d(30, 24, 1.0, "auto") == "cool"
    assert d(10, 24, 1.0, "auto") == "heat"


def test_corrected_setpoint():
    f = logic.corrected_setpoint
    assert f(22, 25.0, 23.0) == 20.0   # unit reads cold -> aim lower
    assert f(22, 21.0, 23.0) == 24.0
    assert f(22, 30.0, 20.0) == 19.0   # clamped to -3
    assert f(22, None, 23.0) == 22
