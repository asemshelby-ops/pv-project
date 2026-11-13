"""twin/fit.py: the fit must find a hidden module that is NOT the placeholder, and its
output must replace placeholders through the overlay file only."""
import json

import numpy as np
import pandas as pd
import pvlib
import pytest

from analysis import fake_logger
from twin import electrical, fit, params, thermal, validate

P = params.load(None, None)


def test_listing_fit_gives_the_placeholders_of_the_parameter_file():
    v = fit.electrical_from_listing()
    for name, value in v.items():
        assert getattr(P, name) == pytest.approx(value, rel=2e-6), name
    s = electrical.stc(P.replace(**v))
    assert (s["i_sc"], s["v_oc"]) == (pytest.approx(5.88, abs=1e-3), pytest.approx(21.5, abs=1e-3))


def test_listing_fit_refuses_impossible_points():
    with pytest.raises(ValueError):
        fit.electrical_from_listing(i_sc=5.0, i_mp=5.5)       # I_mp above I_sc


def _hidden_sweeps(n=36, seed=0):
    """Sweeps of fake_logger's module (n = 1.1, R_s 0.25, R_sh 300: not the placeholder) at
    known irradiance and cell temperature."""
    rng, model = np.random.default_rng(seed), fake_logger.module_model()
    pts, rows = [], []
    for sid in range(1, n + 1):
        g, t = rng.uniform(600, 1000), rng.uniform(30, 62)
        p, _ = fake_logger.sweep_points(model, g, t, rng)
        pts.append(p.assign(sweep_id=sid))
        rows.append({"sweep_id": sid, "G": g, "T_cell": t})
    return pd.concat(pts, ignore_index=True), pd.DataFrame(rows), model


def test_sweep_fit_recovers_a_hidden_module():
    points, sweeps, model = _hidden_sweeps()
    values, d = fit.electrical_from_sweeps(points, sweeps, P)
    fitted = P.replace(**values)
    assert d["eg_ref_fitted"] and d["rmse_current_a"] < 0.02
    for g, t in [(1000.0, 25.0), (800.0, 50.0), (650.0, 60.0)]:
        true = float(pvlib.pvsystem.singlediode(*fake_logger.curve(model, g, t))["p_mp"])
        assert float(electrical.module_mpp(fitted, g, t)[0]) == pytest.approx(true, rel=0.005)
        # ... and the placeholder is NOT that module, so the test can fail
        assert abs(float(electrical.module_mpp(P, g, t)[0]) / true - 1) > 0.03
    temps = np.linspace(25, 65, 9)
    slope, at25 = np.polyfit(temps - 25, [float(pvlib.pvsystem.singlediode(
        *fake_logger.curve(model, 1000.0, t))["p_mp"]) for t in temps], 1)
    assert d["model_gamma_per_c"] == pytest.approx(slope / at25, rel=0.03)


def test_sweeps_at_one_temperature_do_not_fit_the_band_gap():
    points, sweeps, _ = _hidden_sweeps(n=12)
    sweeps = sweeps.assign(T_cell=45.0)
    values, d = fit.electrical_from_sweeps(points, sweeps, P)
    assert "eg_ref" not in values and not d["eg_ref_fitted"]
    with pytest.raises(ValueError):
        fit.electrical_from_sweeps(points, sweeps.head(2), P)


@pytest.mark.parametrize("gamma", [-0.0030, -0.0041, -0.0050])
def test_gamma_calibration_hits_the_measured_coefficient(gamma):
    eg = fit.calibrate_gamma(P, gamma)
    assert electrical.gamma_pmp(P.replace(eg_ref=eg)) == pytest.approx(gamma, abs=2e-6)


def test_gamma_out_of_reach_is_refused():
    with pytest.raises(ValueError):
        fit.calibrate_gamma(P, +0.01)


def _hidden_days(true, with_wind, seed=1, n_days=3):
    """Day tables whose module temperature follows `true`, with sensor noise."""
    rng = np.random.default_rng(seed)
    parts = []
    for d in range(n_days):
        t = pd.date_range(f"2026-12-0{d + 1} 06:00:00", periods=4320, freq="10s")
        h = (t.hour + t.minute / 60).to_numpy()
        g = np.clip(950 * np.sin(np.pi * (h - 6) / 12), 0, None) * (1 - 0.4 * (np.abs(h - 11 - d) < 0.2))
        ta = 26 + 5 * np.sin(2 * np.pi * (h - 9) / 24)
        v = np.clip(2.0 + 0.8 * np.sin(h * 2.1 + d) + 0.6 * np.sin(h * 7.3), 0.3, None)
        tm = thermal.simulate(true, ta[0], g, ta, v, 10.0)
        df = pd.DataFrame({"date": t.strftime("%Y-%m-%d"), "time": t.strftime("%H:%M:%S")})
        for k in range(5):
            df[f"T{k + 1}"] = tm + rng.normal(0, 0.05, len(t))
        df["T_ambient"], df["irradiance"] = ta, g
        df["wind"] = v if with_wind else np.nan
        df["power"] = electrical.module_mpp(true, g, tm + 3 * g / 1000)[0]
        parts.append(df)
    return validate.frame_from_days(pd.concat(parts, ignore_index=True))


def test_thermal_fit_recovers_hidden_values_with_wind():
    true = P.replace(heat_capacity_j_per_k=7000.0, u0=30.0, u1=5.0)
    values, d = fit.thermal_from_days(_hidden_days(true, True), P)
    assert d["wind_measured"] and set(values) == {"heat_capacity_j_per_k", "u0", "u1"}
    assert values["heat_capacity_j_per_k"] == pytest.approx(7000.0, rel=0.03)
    assert values["u0"] == pytest.approx(30.0, rel=0.03)
    assert values["u1"] == pytest.approx(5.0, rel=0.05)
    assert d["rmse_c"] < 0.1 < d["rmse_placeholder_c"]


def test_thermal_fit_without_anemometer_keeps_u1_and_fits_an_effective_u0():
    true = P.replace(heat_capacity_j_per_k=7000.0, u0=30.0, u1=5.0)
    values, d = fit.thermal_from_days(_hidden_days(true, False), P)
    assert not d["wind_measured"] and "u1" not in values
    # effective loss at the fallback wind is close to the true loss at the mean wind (2 m/s)
    u_eff = values["u0"] + P.u1 * P.wind_fallback_ms
    assert u_eff == pytest.approx(30.0 + 5.0 * 2.0, rel=0.06)
    assert d["rmse_c"] < d["rmse_placeholder_c"]


def test_overlay_file_round_trip_and_tags(tmp_path):
    f = fit.write_overlay(tmp_path / "o.json", {"u0": 31.0}, params.FIT_SYNTH, "test", {"a": 1})
    fit.write_overlay(f, {"heat_capacity_j_per_k": 7100.0}, params.FIT_SYNTH, "test 2", {"b": 2})
    data = json.loads(f.read_text())
    assert set(data["params"]) == {"u0", "heat_capacity_j_per_k"}       # the first fit is kept
    assert data["diagnostics"] == {"a": 1, "b": 2}
    p = params.load(f, None)
    assert (p.u0, p.heat_capacity_j_per_k) == (31.0, 7100.0)
    assert p.meta["u0"].tag == params.FIT_SYNTH
    with pytest.raises(KeyError):
        fit.write_overlay(f, {"nope": 1.0}, params.FIT_SYNTH, "x", {})


def test_a_synthetic_fit_is_never_written_to_the_measured_slot(tmp_path, monkeypatch):
    """Without --real the result goes to out/ with the tag [FIT-SYNTHETIC]."""
    import sys
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["twin.fit", "--gamma", "-0.004"])
    fit.main()
    out = tmp_path / "out/twin/fit_synthetic.json"
    assert json.loads(out.read_text())["params"]["eg_ref"]["tag"] == params.FIT_SYNTH
    assert not params.MEASURED_PATH.exists()


def test_a_value_measured_some_other_way_is_entered_without_code(tmp_path, monkeypatch):
    import sys
    out = tmp_path / "o.json"
    monkeypatch.setattr(sys, "argv", ["twin.fit", "--out", str(out), "--set",
                                      "spray_u_module_water=48", "--set", "cells_in_series=36",
                                      "--source", "spray test (pytest)"])
    fit.main()
    p = params.load(out, None)
    assert p.spray_u_module_water == 48.0 and p.meta["spray_u_module_water"].source == "spray test (pytest)"
    assert p.cells_in_series == 36 and isinstance(p.cells_in_series, int)
    for bad in (["--set", "pump_curve=1", "--source", "x"], ["--set", "nope=1", "--source", "x"],
                ["--set", "u0=30"]):
        monkeypatch.setattr(sys, "argv", ["twin.fit", "--out", str(out), *bad])
        with pytest.raises(SystemExit):
            fit.main()
