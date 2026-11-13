"""twin/rooftop.py: the simulated rooftop, its spread, the valves, the pump and the tank."""
import numpy as np
import pandas as pd
import pytest

from twin import electrical, params, rooftop, thermal
from twin.rooftop import Command

P = params.load(None, None)
CONST = {"dist": "const", "value": 1.0}
# a rooftop without any spread and with ideal sensors
PLAIN = P.replace(mismatch_i_l=CONST, mismatch_i_o=CONST, soiling_module=CONST,
                  soiling_string=CONST, wind_exposure_module=CONST, wind_exposure_first=1.0,
                  wind_exposure_last=1.0, sensor_noise_c=0.0, sensor_offset_sd_c=0.0,
                  sensor_lsb_c=1e-9)


def weather(n=360, g=900.0, ta=30.0, rh=40.0, wind=1.5, date="2026-12-01"):
    """Constant weather (1 h by default) as a Weather object."""
    t = pd.date_range(f"{date} 10:00:00", periods=n, freq="10s")
    df = pd.DataFrame({"date": t.strftime("%Y-%m-%d"), "time": t.strftime("%H:%M:%S"),
                       "irradiance": g, "T_ambient": ta, "humidity": rh, "wind": wind})
    return rooftop.weather_days(df)[0]


class Fixed:
    """Test controller: a fixed command, or a function of the observation."""
    name = "test"

    def __init__(self, valves, duty=1.0):
        self.valves, self.duty = valves, duty

    def reset(self, roof):
        self.n = roof.shape[0]

    def step(self, obs):
        v = self.valves(obs) if callable(self.valves) else np.asarray(self.valves, dtype=bool)
        return Command(v, self.duty)


def test_draw_distributions_and_clipping():
    rng = np.random.default_rng(0)
    x = rooftop.draw({"dist": "normal", "mean": 1.0, "sd": 0.5, "lo": 0.9, "hi": 1.1}, rng, 2000)
    assert x.min() == 0.9 and x.max() == 1.1
    x = rooftop.draw({"dist": "lognormal", "median": 2.0, "sigma": 0.1}, rng, 4000)
    assert np.median(x) == pytest.approx(2.0, rel=0.02) and (x > 0).all()
    x = rooftop.draw({"dist": "uniform", "lo": 0.2, "hi": 0.4}, rng, 1000)
    assert 0.2 <= x.min() and x.max() <= 0.4
    assert (rooftop.draw({"dist": "const", "value": 3}, rng, (2, 2)) == 3.0).all()
    with pytest.raises(ValueError):
        rooftop.draw({"dist": "weibull"}, rng, 3)


def test_rooftop_is_120_modules_in_6_strings_and_repeatable():
    a, b, c = rooftop.build(P, 1), rooftop.build(P, 1), rooftop.build(P, 2)
    assert a.shape == (6, 20) and a.n_modules == 120
    assert np.array_equal(a.soiling, b.soiling) and not np.array_equal(a.soiling, c.soiling)
    assert a.sensor_idx.shape == (6, 5)
    assert all(len(set(row)) == 5 for row in a.sensor_idx)      # five different modules
    assert (a.f_il >= 0.97).all() and (a.f_il <= 1.03).all()
    assert (a.soiling <= 1.0).all() and a.soiling.std() > 0
    # string 1 is the windward edge, string 6 the most sheltered
    assert a.wind_factor[0].mean() > a.wind_factor[5].mean()


def test_spread_is_configurable():
    r = rooftop.build(PLAIN, 0)
    assert (r.f_il == 1).all() and (r.soiling == 1).all() and (r.wind_factor == 1).all()
    r = rooftop.build(P.replace(n_strings=3, modules_per_string=8, sensors_per_string=2,
                                soiling_module={"dist": "uniform", "lo": 0.5, "hi": 0.6}), 0)
    assert r.shape == (3, 8) and r.sensor_idx.shape == (3, 2)
    assert 0.49 <= r.soiling.min() and r.soiling.max() <= 0.6


def test_uncooled_plain_rooftop_is_120_times_the_module_twin():
    roof = rooftop.build(PLAIN, 0)
    w = weather(n=1500)                                         # > 30 time constants
    table, s = rooftop.simulate_day(roof, w, Fixed(np.zeros(6)))
    t_ss = thermal.steady_state(PLAIN, 900.0, 30.0, 1.5)
    assert table["t_string_3"].iloc[-1] == pytest.approx(t_ss, abs=1e-3)
    p_mod = float(electrical.module_mpp(PLAIN, 900.0, t_ss + 3.0 * 0.9)[0])
    assert table["p_dc"].iloc[-1] == pytest.approx(120 * p_mod, rel=2e-4)
    assert s["e_pump_wh"] == 0 and s["water_used_l"] == 0 and s["pump_hours"] == 0
    assert s["e_net_wh"] == pytest.approx(s["e_dc_wh"])


def test_only_the_open_zone_is_cooled_and_its_power_rises():
    roof = rooftop.build(PLAIN, 0)
    table, s = rooftop.simulate_day(roof, weather(), Fixed([1, 0, 0, 0, 0, 0]))
    last = table.iloc[-1]
    assert last["t_string_1"] < last["t_string_2"] - 8
    assert last["t_string_2"] == pytest.approx(last["t_string_6"])
    assert last["p_string_1"] > 1.03 * last["p_string_2"]
    assert last["valve_1"] == 1 and last["valve_2"] == 0
    # the pump serves 20 modules: 20 units of the placeholder table, one valve held
    assert last["p_pump"] == pytest.approx(20 * 7.2) and last["p_valves"] == 5.0
    assert last["flow_lpm"] == pytest.approx(20 * 1.75)
    assert s["zone_hours"] == pytest.approx(1.0) and s["pump_hours"] == pytest.approx(1.0)
    assert s["e_net_wh"] == pytest.approx(s["e_dc_wh"] - s["e_pump_wh"] - s["e_valves_wh"])


def test_water_balance_and_tank_heat_balance():
    """No evaporation, no loss, no tank heat loss: every joule taken from the modules
    is in the tank, and no water is used."""
    p = PLAIN.replace(evap_enabled=0, spray_loss_fraction=0.0, tank_ua_w_per_k_per_module=0.0)
    table, s = rooftop.simulate_day(rooftop.build(p, 0), weather(), Fixed(np.ones(6)))
    heat = table["q_water_w"].sum() * p.dt_s
    mass = p.tank_l_per_module * 120 * p.water_rho_kg_per_l
    t_end = table["t_tank"].iloc[-1] + (table["q_water_w"].iloc[-1] * p.dt_s) / (mass * p.water_cp)
    assert (t_end - 30.0) * mass * p.water_cp == pytest.approx(heat, rel=1e-9)
    assert s["water_used_l"] == 0
    assert s["water_circulated_l"] == pytest.approx(120 * 1.75 * 60)        # 1 h
    assert table["t_tank"].is_monotonic_increasing and table["t_tank"].iloc[-1] > 31


def test_water_used_is_evaporation_plus_the_lost_share():
    p = PLAIN.replace(spray_loss_fraction=0.05)
    table, s = rooftop.simulate_day(rooftop.build(p, 0), weather(), Fixed(np.ones(6)))
    evaporated = table["q_evap_w"].sum() * p.dt_s / p.water_h_fg / p.water_rho_kg_per_l
    assert evaporated > 0
    assert s["water_used_l"] == pytest.approx(evaporated + 0.05 * s["water_circulated_l"])


def test_valve_dwell_times_are_enforced_for_every_controller():
    flip = Fixed(lambda obs: np.full(6, obs.k % 2 == 0))        # wants to toggle every 10 s
    table, s = rooftop.simulate_day(rooftop.build(PLAIN, 0), weather(), flip)
    v = table["valve_1"].to_numpy().astype(int)
    change = np.flatnonzero(np.diff(v) != 0) + 1
    runs = np.diff(np.r_[change, len(v)])
    on_runs = runs[v[change] == 1][:-1]
    off_runs = runs[v[change] == 0][:-1]
    assert (on_runs * PLAIN.dt_s >= PLAIN.valve_min_on_s).all()
    assert (off_runs * PLAIN.dt_s >= PLAIN.valve_min_off_s).all()
    assert s["valve_openings"] == 6 * (np.diff(v) == 1).sum() + 6 * v[0]


def test_an_undersized_pump_serves_the_hottest_strings_first():
    p = P.replace(max_zones_open=2)                 # default spread: string 6 is sheltered
    roof = rooftop.build(p, 1)
    table, _ = rooftop.simulate_day(roof, weather(n=720), Fixed(
        lambda obs: np.full(6, obs.k >= 360)))      # heat up for 1 h, then ask for all zones
    valves = table[[f"valve_{z}" for z in range(1, 7)]].to_numpy()
    assert valves.sum(axis=1).max() == 2
    first = valves[360]
    hottest = np.argsort(-table[[f"t_zone_{z}" for z in range(1, 7)]].iloc[360].to_numpy())[:2]
    assert set(np.flatnonzero(first)) == set(hottest)


def test_duty_below_the_pump_minimum_gives_no_flow():
    table, s = rooftop.simulate_day(rooftop.build(PLAIN, 0), weather(n=60),
                                    Fixed(np.ones(6), duty=0.2))
    assert s["e_pump_wh"] == 0 and s["water_circulated_l"] == 0
    assert (table["duty"] == 0).all()
    assert s["e_valves_wh"] > 0                      # the valves were open all the same


def test_soiling_lowers_power_not_temperature_and_shelter_raises_temperature():
    dirty = rooftop.build(PLAIN.replace(soiling_string={"dist": "const", "value": 0.9}), 0)
    clean = rooftop.build(PLAIN, 0)
    w = weather()
    td, _ = rooftop.simulate_day(dirty, w, Fixed(np.zeros(6)))
    tc, _ = rooftop.simulate_day(clean, w, Fixed(np.zeros(6)))
    assert td["p_dc"].iloc[-1] == pytest.approx(0.9 * tc["p_dc"].iloc[-1], rel=0.01)
    assert td["t_string_1"].iloc[-1] == pytest.approx(tc["t_string_1"].iloc[-1])
    sheltered = rooftop.build(PLAIN.replace(wind_exposure_last=0.5), 0)
    ts, _ = rooftop.simulate_day(sheltered, w, Fixed(np.zeros(6)))
    assert ts["t_string_6"].iloc[-1] > ts["t_string_1"].iloc[-1] + 1.0


def test_a_mismatched_string_gives_less_than_its_modules_alone():
    roof = rooftop.build(P.replace(soiling_module={"dist": "uniform", "lo": 0.8, "hi": 1.0}), 3)
    plant = rooftop.Plant(roof)
    plant.reset(45.0)
    string = plant.electrical(900.0)["p"]
    t_cell = 45.0 + 3.0 * 0.9
    alone = electrical.module_mpp(P, 900.0 * roof.soiling, t_cell)[0]      # without mismatch factors
    assert (string < alone.sum(axis=1) * 1.03).all()
    assert string.sum() < 0.99 * 120 * float(electrical.module_mpp(P, 900.0 * roof.soiling.mean(), t_cell)[0])


def test_same_seed_same_run_and_sensors_are_measurements():
    roof, w = rooftop.build(P, 1), weather(n=120)
    a, _ = rooftop.simulate_day(roof, w, Fixed(np.zeros(6)), noise_seed=3)
    b, _ = rooftop.simulate_day(roof, w, Fixed(np.zeros(6)), noise_seed=3)
    c, _ = rooftop.simulate_day(roof, w, Fixed(np.zeros(6)), noise_seed=4)
    assert a.equals(b) and not a["t_zone_1"].equals(c["t_zone_1"])
    assert a["p_dc"].equals(c["p_dc"])               # noise is on the sensors, not the plant
    plant = rooftop.Plant(roof, 3)
    plant.reset(30.0)
    obs = plant.observe(w, 0)
    assert obs.t_sensors.shape == (6, 5)
    assert np.allclose(obs.t_sensors / P.sensor_lsb_c, np.round(obs.t_sensors / P.sensor_lsb_c))
    assert not hasattr(obs, "t")                     # no true state in the observation


def test_weather_days_splits_by_date_and_keeps_missing_wind():
    t = pd.date_range("2026-12-01 17:59:40", periods=4, freq="10s")
    df = pd.DataFrame({"date": ["2026-12-01"] * 2 + ["2026-12-02"] * 2,
                       "time": t.strftime("%H:%M:%S"), "irradiance": [5.0, -1.0, 0.0, 3.0],
                       "T_ambient": 25.0, "humidity": 40.0, "wind": np.nan})
    days = rooftop.weather_days(df)
    assert [d.date for d in days] == ["2026-12-01", "2026-12-02"] and len(days[0]) == 2
    assert days[0].g.min() == 0.0 and np.isnan(days[0].wind).all()
    assert days[0].doy == 335
    plant = rooftop.Plant(rooftop.build(PLAIN, 0))
    assert plant.wind_at_modules(float("nan"))[0, 0] == PLAIN.wind_fallback_ms


def test_night_gives_zero_power():
    table, s = rooftop.simulate_day(rooftop.build(P, 1), weather(n=30, g=0.0), Fixed(np.zeros(6)))
    assert (table["p_dc"] == 0).all() and s["e_dc_wh"] == 0
