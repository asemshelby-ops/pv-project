"""twin/validate.py: the replay of logger files and its error report."""
import numpy as np
import pytest

from tests.test_twin_fit import _hidden_days
from twin import SIM_NOTICE, fit, params, validate

P = params.load(None, None)


def test_a_twin_with_the_right_parameters_has_only_sensor_noise_as_error():
    table, has_wind = validate.replay(_hidden_days(P, True), P)
    stats, days = validate.summarise(table)
    assert has_wind and len(days) == 3
    assert stats["temperature_c"]["rmse"] < 0.05
    assert abs(stats["temperature_c"]["bias"]) < 0.02
    assert stats["power_w"]["rmse_rel"] < 0.002
    assert abs(stats["energy_rel_difference"]) < 0.001


def test_wrong_parameters_show_up_as_error_and_the_fit_removes_it():
    true = P.replace(heat_capacity_j_per_k=7000.0, u0=30.0, u1=5.0)
    frame = _hidden_days(true, True)
    before = validate.summarise(validate.replay(frame, P)[0])[0]
    assert before["temperature_c"]["bias"] > 0.5         # placeholder loses less heat: too warm
    values, _ = fit.thermal_from_days(frame, P)
    after = validate.summarise(validate.replay(frame, P.replace(**values))[0])[0]
    assert after["temperature_c"]["rmse"] < 0.1 < before["temperature_c"]["rmse"]


def test_files_without_wind_use_the_fallback_and_say_so(tmp_path):
    frame = _hidden_days(P, False)
    table, has_wind = validate.replay(frame, P)
    assert not has_wind
    stats = validate.run(frame, tmp_path, P, "SYNTHETIC test table", log=lambda *a: None)
    text = (tmp_path / "report.md").read_text()
    assert SIM_NOTICE in text and "SYNTHETIC test table" in text
    assert "cannot follow wind lulls" in text and "[LIT-PLACEHOLDER]" in text
    assert stats["temperature_c"]["rmse"] > 0.3          # the wind it could not see
    assert (tmp_path / "per_day.csv").exists() and (tmp_path / "example_day.png").exists()


def test_power_is_scored_only_where_the_load_holds_the_operating_point():
    frame = _hidden_days(P, True, n_days=1)
    frame["load_mode"] = "hold"
    frame.loc[frame.index[2000:2100], "load_mode"] = "sweep"
    frame.loc[frame.index[2100:2200], "load_mode"] = "trip"
    frame.loc[frame.index[2200:2300], "power"] = 0.2      # load disconnected
    table, _ = validate.replay(frame, P)
    assert not table["scored_p"].iloc[2000:2300].any()
    assert table["scored_p"].iloc[1900:2000].all()
    assert table["scored_t"].iloc[2000:2300].all()        # temperature is still scored
    assert not table["scored_t"].iloc[0]                  # the row a segment starts from
    assert not table.loc[table["g"] < validate.MIN_G, "scored_t"].any()


def test_errors_of_an_empty_selection():
    assert validate.errors([], []) == {"n": 0}
    e = validate.errors([1.0, 3.0, np.nan], [0.0, 1.0, 5.0])
    assert (e["n"], e["bias"], e["mae"]) == (2, 1.5, 1.5)


def test_demo_runs_on_synthetic_rig_files_and_the_fit_lowers_the_error(tmp_path):
    """The hook on files in the logger's own format (UTC, wind = NaN) plus sweep files."""
    res = validate.demo(tmp_path, seed=11, n_days=3, log=lambda *a: None)
    a, b = res["placeholders_files"], res["fitted_files"]
    assert not a["has_wind"] and res["placeholders_wind"]["has_wind"]
    assert res["placeholders_wind"]["temperature_c"]["rmse"] < 0.3 < a["temperature_c"]["rmse"]
    assert b["temperature_c"]["rmse"] < a["temperature_c"]["rmse"]
    assert b["power_w"]["rmse_rel"] < 0.03 < a["power_w"]["rmse_rel"]
    assert b["sweeps"]["rmse_rel"] < 0.01 < a["sweeps"]["rmse_rel"]
    assert (tmp_path / "fit_synthetic.json").exists()
    assert "SYNTHETIC" in (tmp_path / "3_fitted_logger_files/report.md").read_text()
