"""twin/controllers.py and twin/compare.py: the five strategies on the simulated rooftop."""
import numpy as np
import pandas as pd
import pytest

import config
from tests.test_twin_rooftop import PLAIN, weather
from twin import SIM_NOTICE, compare, controllers, params, rooftop
from twin.rooftop import Obs

P = params.load(None, None)
SMALL = PLAIN.replace(n_strings=2, modules_per_string=4, sensors_per_string=2)


def obs(t_zone, g=900.0, ta=30.0, t_water=30.0, valves=None, k=0, rh=40.0, n_sensors=5):
    t_zone = np.asarray(t_zone, dtype=float)
    return Obs(k=k, doy=335, minute=720.0 + k / 6, g=g, ta=ta, rh=rh, wind=float("nan"),
               t_sensors=np.repeat(t_zone[:, None], n_sensors, axis=1),
               p_string=np.zeros(len(t_zone)), t_water=t_water,
               valves=np.zeros(len(t_zone), dtype=bool) if valves is None else valves, duty=0.0)


def ready(c, p=P, seed=1):
    c.reset(rooftop.build(p, seed))
    return c


def lull_day(n_strings=6):
    """A clear morning with one wind lull (2 -> 0.4 m/s for 20 min): the spike scenario."""
    t = pd.date_range("2026-12-01 09:00:00", periods=1080, freq="10s")     # 3 h
    wind = np.full(len(t), 2.0)
    wind[540:660] = 0.4
    df = pd.DataFrame({"date": t.strftime("%Y-%m-%d"), "time": t.strftime("%H:%M:%S"),
                       "irradiance": 850.0, "T_ambient": 30.0, "humidity": 40.0, "wind": wind})
    return rooftop.weather_days(df)[0]


# --- the three simple strategies ----------------------------------------------------------------

def test_no_cooling_never_opens_a_valve():
    c = ready(controllers.NoCooling())
    cmd = c.step(obs([80.0] * 6))
    assert not cmd.valves.any() and cmd.duty == 0


def test_continuous_sprays_in_daylight_only():
    c = ready(controllers.Continuous())
    day, night = c.step(obs([20.0] * 6)), c.step(obs([20.0] * 6, g=10.0))
    assert day.valves.all() and day.duty == P.baseline_duty
    assert not night.valves.any() and night.duty == 0
    assert ready(controllers.Continuous(0.5)).step(obs([20.0] * 6)).duty == 0.5


def test_fixed_threshold_hysteresis_per_zone():
    c = ready(controllers.FixedThreshold())
    assert c.name == "Fixed threshold 45/35 degC"
    seq = [([44.0, 46.0], [0, 1]),      # zone 2 passes 45
           ([44.9, 40.0], [0, 1]),      # between the limits: keeps its state
           ([45.0, 35.0], [1, 0]),      # zone 1 on at 45, zone 2 off at 35
           ([36.0, 44.0], [1, 0])]
    for temps, want in seq:
        cmd = c.step(obs(temps + [20.0] * 4))
        assert list(cmd.valves[:2]) == [bool(x) for x in want]
    assert not c.step(obs([60.0] * 6, g=10.0)).valves.any()      # no spraying in the dark
    assert ready(controllers.FixedThreshold(50, 40, 0.5)).name == "Fixed threshold 50/40 degC, duty 0.5"


# --- predictive ---------------------------------------------------------------------------------

def test_predictive_sprays_hot_zones_only_and_picks_a_duty_from_its_list():
    c = ready(controllers.Predictive())
    cmd = c.step(obs([60.0, 60.0, 60.0, 31.0, 31.0, 31.0], t_water=30.0, rh=100.0, ta=45.0))
    assert list(cmd.valves) == [True] * 3 + [False] * 3
    assert cmd.duty in P.predictive_duties
    assert (c.ratio[:3] >= P.predictive_on_ratio).all() and (c.ratio[3:] < P.predictive_on_ratio).all()


def test_predictive_never_sprays_when_the_pump_costs_more_than_the_gain():
    dear = P.replace(plant_pump_power_factor=50.0)
    c = ready(controllers.Predictive(), dear)
    assert not c.step(obs([65.0] * 6)).valves.any()
    c = ready(controllers.Predictive())
    assert not c.step(obs([65.0] * 6, g=10.0)).valves.any()              # dark
    assert not c.step(obs([65.0] * 6, g=60.0)).valves.any()              # too little sun to gain


def test_predictive_refuses_warm_water_without_evaporation():
    """CLAUDE.md: the rule must use the measured water temperature."""
    c = ready(controllers.Predictive(), P.replace(evap_enabled=0))
    assert c.step(obs([55.0] * 6, t_water=25.0)).valves.all()
    c = ready(controllers.Predictive(), P.replace(evap_enabled=0))
    assert not c.step(obs([55.0] * 6, t_water=54.0)).valves.any()


def test_predictive_switches_off_as_soon_as_gain_no_longer_exceeds_pump_power():
    assert P.predictive_off_ratio >= 1.0 < P.predictive_on_ratio
    c = ready(controllers.Predictive(), P.replace(evap_enabled=0))
    assert c.step(obs([55.0] * 6, t_water=25.0)).valves.all()
    t = 55.0
    while c.step(obs([t] * 6, t_water=25.0)).valves.all():
        t -= 0.25
    assert (c.ratio < P.predictive_off_ratio).all() or not c.state.any()
    q, pump = c.know.q_cool(np.array([t + 0.25]), obs([t + 0.25] * 6, t_water=25.0), min(P.predictive_duties))
    gain = c.know.gain_per_degc(900.0) * q[0] / c.know.loss_w_per_k(obs([t] * 6))
    assert gain >= (pump + P.valve_hold_w / 20) * 0.99       # one step earlier it still paid


def test_predictive_switching_ratio_can_be_set():
    hot = obs([45.0] * 6, t_water=30.0, rh=100.0, ta=45.0)     # no evaporation in saturated air
    assert ready(controllers.Predictive()).step(hot).valves.all()
    strict = ready(controllers.Predictive(on_ratio=50.0, off_ratio=40.0))
    assert not strict.step(hot).valves.any() and strict.r_on == 50.0


def test_predictive_expected_gain_formula():
    """gain = k * q_cool / (A U): the pump break-even of CLAUDE.md, per module."""
    know = controllers.ModuleKnowledge(P)
    o = obs([50.0], g=1000.0)
    assert know.gain_per_degc(1000.0) == pytest.approx(-know.gamma * know.p_ref)
    assert 0.35 < know.gain_per_degc(900.0) < 0.50           # CLAUDE.md [EST]: ~0.36 W/degC
    assert know.loss_w_per_k(o) == pytest.approx(0.648 * (25 + 6.84 * 1.5))
    q, pump = know.q_cool(np.array([50.0]), o, 1.0)
    assert pump == 7.2 and q[0] > 0
    q, pump = know.q_cool(np.array([50.0]), o, 0.1)           # below the pump's minimum duty
    assert q[0] == 0 and pump == 0


def test_predictive_on_a_hot_day_gains_and_on_a_bad_model_day_does_not_lose():
    roof = rooftop.build(P, 1)
    w = weather(n=720, g=900.0, ta=32.0)
    _, base = rooftop.simulate_day(roof, w, controllers.NoCooling())
    table, s = rooftop.simulate_day(roof, w, controllers.Predictive())
    assert s["e_net_wh"] > base["e_net_wh"] and s["pump_hours"] > 0.5
    # a plant where cooling cannot pay: no evaporation and a pump five times as hungry
    bad = P.replace(evap_enabled=0, plant_pump_power_factor=5.0)
    roof = rooftop.build(bad, 1)
    _, base = rooftop.simulate_day(roof, w, controllers.NoCooling())
    _, s = rooftop.simulate_day(roof, w, controllers.Predictive())
    _, cont = rooftop.simulate_day(roof, w, controllers.Continuous())
    assert s["e_net_wh"] >= base["e_net_wh"] * 0.9995
    assert cont["e_net_wh"] < 0.9 * base["e_net_wh"]         # continuous loses badly there


# --- spike-triggered ----------------------------------------------------------------------------

class Stub:
    """A detector that alarms at given sample numbers and counts how it is used."""

    def __init__(self, at):
        self.at, self.fed, self.resets = set(at), 0, 0

    def reset(self):
        self.resets += 1
        self.count = 0

    def update(self, s):
        self.fed += 1
        self.count += 1
        return self.fed in self.at


def test_spike_burst_length_blanking_and_restart():
    stubs = []

    def make():
        stubs.append(Stub({5}))
        return stubs[-1]
    c = ready(controllers.SpikeTriggered(make, "stub"))
    states, alarms = [], []
    for k in range(200):
        cmd = c.step(obs([50.0] * 6, k=k))
        states.append(cmd.valves[0])
        alarms.append(cmd.alarms[0])
    assert alarms[4] and sum(alarms) == 1                    # the 5th update, reported once
    on = int(np.sum(states))
    assert P.spike_min_burst_s <= on * P.dt_s <= P.spike_max_burst_s
    assert states[4] and not states[4 + on]
    # not fed for the rest of the burst and for spike_blank_s after it; then restarted
    blind = (on - 1) + int(P.spike_blank_s / P.dt_s)
    assert stubs[0].fed == 200 - blind
    assert stubs[0].resets == 2 and c.fired == 6 and c.refused == 0
    assert cmd.duty == 0.0


def test_spike_burst_uses_the_spike_definition_as_the_least_rise():
    c = ready(controllers.SpikeTriggered(lambda: Stub({1}), "stub"), P.replace(evap_enabled=0))
    cmd = c.step(obs([50.0] * 6, t_water=30.0))
    q, _ = c.know.q_cool(np.array([50.0]), obs([50.0], t_water=30.0), P.spike_duty)
    want = P.heat_capacity_j_per_k * config.SPIKE_THETA_C / q[0]
    assert cmd.valves.all() and c.left[0] == pytest.approx(np.clip(want, 20, 180))


def test_spike_net_power_rule_refuses_a_burst_that_cannot_pay():
    c = ready(controllers.SpikeTriggered(lambda: Stub({1}), "stub"),
              P.replace(plant_pump_power_factor=100.0))
    cmd = c.step(obs([50.0] * 6))
    assert cmd.alarms.all() and not cmd.valves.any()
    assert c.refused == 6 and c.fired == 0
    c = ready(controllers.SpikeTriggered(lambda: Stub({1}), "stub"))
    assert not c.step(obs([50.0] * 6, g=20.0)).valves.any()          # dark: no burst


def test_spike_triggered_with_the_threshold_detector_answers_a_wind_lull():
    roof = rooftop.build(P, 1)
    c = controllers.SpikeTriggered(controllers.threshold_factory(rise_c=2.5), "Spike (threshold)")
    table, s = rooftop.simulate_day(roof, lull_day(), c)
    alarm = table[[f"alarm_{z}" for z in range(1, 7)]].to_numpy()
    valve = table[[f"valve_{z}" for z in range(1, 7)]].to_numpy()
    # The simulated day starts with the modules at ambient: that warm-up is a rise too,
    # one alarm per zone. Then nothing until the lull at sample 540.
    assert alarm[:300].sum() == 6 and alarm[300:540].sum() == 0
    assert alarm[540:700].any(axis=0).sum() >= 4             # most zones see the lull
    assert valve[300:540].sum() == 0 and valve[540:720].sum() > 0
    assert s["valve_openings"] == c.fired and 0 < s["pump_hours"] < 0.5


@pytest.fixture(scope="module")
def tiny_nasim():
    """A small NASIM-GBT trained here on three synthetic days: the detector class of the
    paper inside the controller (the real model comes from the pipeline run)."""
    from data import synth
    from detect import gbt
    days, events = synth.generate(0, n_days=3, hard=False)
    X = gbt.build_features(days, use_wind=False, hybrid=True)
    y, use = gbt.sample_targets(days, events)
    model = gbt.train(X, y, use, seed=0)
    return lambda: gbt.GbtDetector(model, use_wind=False, p_threshold=0.5, hybrid=True,
                                   persist=config.GBT_PERSIST)


def test_spike_triggered_runs_nasim_gbt_per_zone(tiny_nasim):
    roof = rooftop.build(P, 1)
    c = controllers.SpikeTriggered(tiny_nasim, "Spike-triggered (NASIM-GBT)")
    table, s = rooftop.simulate_day(roof, lull_day(), c)
    assert len(c.det) == 6 and all(d.__class__.__name__ == "GbtDetector" for d in c.det)
    assert np.isfinite(table["p_dc"]).all()
    assert s["alarms"] == c.fired + c.refused
    assert s["valve_openings"] == c.fired


def test_missing_nasim_run_says_how_to_create_it(tmp_path):
    with pytest.raises(FileNotFoundError, match="analysis.demo_synth"):
        controllers.nasim_factory(tmp_path)


# --- comparison ---------------------------------------------------------------------------------

def test_all_strategies_on_the_same_weather_and_rooftop(tiny_nasim, tmp_path):
    ctrls = [controllers.NoCooling(), controllers.Continuous(), controllers.FixedThreshold(),
             controllers.SpikeTriggered(tiny_nasim, "Spike-triggered (NASIM-GBT)"),
             controllers.Predictive()]
    ws = [lull_day(), weather(n=360, g=700.0, date="2026-12-02")]
    res, per_day, tables = compare.run_set(P, ws, ctrls, log=lambda *a: None)
    assert list(res["strategy"]) == [c.name for c in ctrls] and len(per_day) == 10
    r = res.set_index("strategy")
    assert r.loc["No cooling", "gain_wh"] == 0 and r.loc["No cooling", "water_used_l"] == 0
    assert np.allclose(res["e_net_wh"], res["e_dc_wh"] - res["e_pump_wh"] - res["e_valves_wh"])
    assert r.loc["Continuous", "pump_hours"] == pytest.approx(4.0)        # 3 h + 1 h
    assert r.loc["Continuous", "e_dc_wh"] > r.loc["No cooling", "e_dc_wh"]
    assert (res["water_circulated_l"] >= res["water_used_l"]).all()
    # the same weather for everyone: irradiance and ambient columns are identical
    assert all(tables[c.name]["g"].equals(tables["No cooling"]["g"]) for c in ctrls)
    # litres per net kWh only where there is a net gain
    assert np.isnan(r.loc["No cooling", "litres_per_net_kwh"])
    text = compare.report(res, [], P, rooftop.build(P, compare.ROOF_SEED), ws,
                          {"weather": "test weather", "nasim": "tiny test model"})
    assert SIM_NOTICE in text and "placeholder" in text and "[LIT-PLACEHOLDER]" in text
    assert "Pump hours" in text and "Water used" in text and "Net energy" in text
    compare.figure(tables, P, tmp_path / "f.png", ws[0].date)
    assert (tmp_path / "f.png").stat().st_size > 10000


def test_comparison_strategy_lists():
    names = [c.name for c in compare.settings_grid(P)]
    assert names[0] == "No cooling" and len(names) == len(set(names)) == 11
    assert [c.name for c in compare.strategies(P, nasim=False)][:2] == ["No cooling", "Continuous"]
    wrong = compare.wrong_models(P)
    assert wrong[2].model_params.spray_u_module_water == pytest.approx(0.7 * P.spray_u_module_water)
    assert (wrong[5].on_ratio, wrong[5].off_ratio) == (2.0, 1.6) and wrong[5].model_params is None
    assert len({c.name for c in wrong}) == 6
    assert compare.WEATHER_SEED != config.EXPORT_SEED           # not NASIM's training run
