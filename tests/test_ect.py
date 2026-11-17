"""Equivalent cell temperature from V_oc (analysis/ect.py; IEC 60904-5:2011, Eq. (4)) on
SYNTHETIC sweeps: the arithmetic of the equation, the irradiance limit, the fit that
replaces the placeholder values, and the lag of the back sheet behind the cells.

The sweeps come from analysis/fake_phase_b.py: a module model with ASSUMED coefficients,
and cells that lead the back sheet by an invented 45 s. The tests check that the method
finds what the generator put in. They say nothing about the real module."""
import math

import numpy as np
import pandas as pd
import pytest

import config
from analysis import ect, events, fake_logger, fake_phase_b, gamma, ingest, pipeline
from data import synth


def _rig(out, lead_s, n_days=5):
    days, _ = synth.generate(seed=5, n_days=n_days, hard=True)
    _, rows = fake_phase_b.build(days, out, cell_lead_s=lead_s)
    df = ingest.read_logger(out)
    frame = ingest.detector_frame(df)
    sweeps = gamma.sweep_parameters(*gamma.read_sweeps(out))
    return {"out": out, "rows": rows, "frame": frame, "sweeps": sweeps,
            "events": events.label(frame)}


@pytest.fixture(scope="module")
def rig(tmp_path_factory):
    r = _rig(tmp_path_factory.mktemp("ect"), fake_phase_b.SYN_CELL_LEAD_S)
    r["result"] = ect.run(r["sweeps"], r["frame"], r["events"])
    return r


# --- the equation ------------------------------------------------------------------------------

def test_equation_4_on_hand_numbers():
    par = {"V_ref": 20.0, "G_ref": 1000.0, "T_ref": 25.0, "beta": -0.004, "a": 0.05,
           "I_sc_ref": 6.0}
    assert ect.ect(20.0, 1000.0, par) == pytest.approx(25.0)             # the reference point
    # 2 % lower V_oc at the reference irradiance: 0.02 / 0.004 = 5 degC warmer
    assert ect.ect(19.6, 1000.0, par) == pytest.approx(30.0)
    # half the light at the same V_oc: the voltage "should" be lower by a*ln(2), so the cells are colder
    assert ect.ect(20.0, 500.0, par) == pytest.approx(25.0 + (-0.05 * math.log(0.5)) / -0.004)
    # the standard's Eq. (1), solved the other way round, gives the same V_oc back
    t, g = 47.0, 730.0
    v = par["V_ref"] * (1 + par["beta"] * (t - 25.0) + par["a"] * math.log(g / 1000.0))
    assert ect.ect(v, g, par) == pytest.approx(t)
    # self-reference: I_sc / I_sc,ref in place of G / G_ref
    assert ect.ect(v, 1.0, par, i_sc=6.0 * 0.73, use_isc=True) == pytest.approx(t)
    assert ect.ect(v, g, par, i_sc=1.0, use_isc=False) == pytest.approx(t)
    assert ect.ect(np.array([19.6, 20.0]), np.array([1000.0, 1000.0]), par).tolist() == pytest.approx([30.0, 25.0])


def test_placeholder_values_come_from_config_and_are_tagged():
    par = ect.placeholder_parameters()
    assert par["source"] == "placeholder" and par["V_ref"] == config.ECT_VOC_REF_V == 21.5
    assert par["beta"] == config.ECT_BETA_VOC_REL < 0 and par["a"] == config.ECT_A_IRR
    text = open(config.__file__, encoding="utf-8").read()
    for name in ("ECT_VOC_REF_V", "ECT_BETA_VOC_REL", "ECT_A_IRR", "ECT_ISC_REF_A"):
        assert f"{name} = " in text and "[PLACEHOLDER]" in text.split(f"{name} = ")[1].split("\n")[0]


def test_only_sweeps_above_the_irradiance_limit_are_used(rig):
    assert config.ECT_MIN_G_WM2 == 400.0                        # IEC 60904-5:2011+AMD1:2022
    u = ect.usable(rig["sweeps"])
    assert u["G"].min() >= 400.0 and len(u) < len(gamma.usable_sweeps(rig["sweeps"]))
    assert (u["flags"] == 0).all() and u["G_stable"].all()
    table = rig["result"]["sweeps"]
    assert len(table) == len(u) and table["ECT_placeholder_C"].notna().all()


# --- fitted in place of the placeholders -----------------------------------------------------------

def test_fit_finds_the_generator_module(rig):
    f = rig["result"]["fitted"]
    model = fake_logger.module_model()
    v25 = fake_phase_b.sweep_curve(model, 1000.0, 25.0, np.random.default_rng(0))[3]
    v45 = fake_phase_b.sweep_curve(model, 1000.0, 45.0, np.random.default_rng(0))[3]
    v_half = fake_phase_b.sweep_curve(model, 500.0, 45.0, np.random.default_rng(0))[3]
    beta_true = (v45 - v25) / 20.0 / v25                        # of the synthetic model
    a_true = (v45 - v_half) / math.log(2.0) / v45
    assert f["source"] == "fitted" and f["n"] > 500
    assert f["V_ref"] == pytest.approx(v25, abs=0.08)
    assert f["beta"] == pytest.approx(beta_true, rel=0.05)
    assert f["a"] == pytest.approx(a_true, rel=0.15)
    assert f["resid_sd_C"] < 0.5
    assert abs(f["beta"] - config.ECT_BETA_VOC_REL) > 0.001     # NOT the placeholder: it was replaced


def test_fitted_ect_follows_the_generator_cell_temperature(rig):
    table = rig["result"]["sweeps"]
    rows = rig["rows"]
    truth = rows.assign(t_local=pd.to_datetime(rows["date"] + " " + rows["time"])).set_index("t_local")["_t_cell"]
    err = table["ECT_fitted_C"].to_numpy() - truth.reindex(table["t_local"]).to_numpy()
    assert np.isfinite(err).all()
    assert abs(np.median(err)) < 0.3 and np.sqrt(np.mean(err ** 2)) < 0.6
    # the placeholder ECT has the wrong level (another beta), which is the point of the warning
    wrong = table["ECT_placeholder_C"].to_numpy() - truth.reindex(table["t_local"]).to_numpy()
    assert abs(np.median(wrong)) > 3.0


def test_offset_is_the_cell_to_back_difference_the_generator_used(rig):
    o = rig["result"]["offset"]["fitted"]
    assert o["per_1000_Wm2_C"] == pytest.approx(config.CELL_MINUS_BACK_C, abs=0.5)   # 3 degC per kW/m2
    assert 1.0 < o["median_C"] < 3.5 and o["sd_C"] < 1.0
    assert abs(rig["result"]["offset"]["placeholder"]["median_C"] - o["median_C"]) > 3.0


def test_without_enough_steady_sweeps_there_is_no_fit_and_it_says_why(rig):
    few = rig["sweeps"].iloc[:40]
    f = ect.fit_parameters(few)
    assert f["source"] is None and "needed" in f["note"]
    narrow = ect.usable(rig["sweeps"])
    narrow = narrow[(narrow["G"] > 800) & (narrow["G"] < 860)]
    f2 = ect.fit_parameters(narrow)
    assert f2["source"] is None and ("too little" in f2["note"] or "needed" in f2["note"])
    table = ect.sweep_table(few, f)
    assert table["ECT_fitted_C"].isna().all() and table["ECT_placeholder_C"].notna().all()


# --- lag -------------------------------------------------------------------------------------------------

def test_lag_by_cross_correlation_finds_a_known_shift():
    t = np.arange(0, 3600, 10.0)
    back = 40 + 6 / (1 + np.exp(-(t - 1500) / 120)) + 0.5 * np.sin(t / 300)
    for shift in (-40.0, 0.0, 25.0, 60.0, 115.0):
        ts = np.arange(0, 3600, 60.0)
        cells = np.interp(ts + shift, t, back) + 2.0             # the cells are `shift` s ahead
        lag, r, n = ect.lag_seconds(ts, cells, t, back)
        assert lag == pytest.approx(shift, abs=2.5) and r > 0.999 and n > 40
    assert np.isnan(ect.lag_seconds([0, 60], [1, 2], t, back)[0])            # too few sweeps
    assert np.isnan(ect.lag_seconds(np.arange(0, 3600, 60.0), np.ones(60), t, back)[0])   # flat


def test_back_sheet_lags_the_cells_during_spikes(rig, tmp_path):
    res = rig["result"]
    lags = res["spikes"]
    assert len(lags) >= 10 and (lags["n_sweeps"] >= config.ECT_LAG_MIN_SWEEPS).all()
    for name in ("placeholder", "fitted"):
        lag = res["lag"][name]
        assert lag["n"] == len(lags) and 20 < lag["median_s"] < 60   # 45 s first-order lead put in
        assert (lags[f"r_{name}"] > 0.9).all()
    # the lag does not depend on the calibration values: the placeholders give the same
    assert np.allclose(lags["lag_placeholder_s"], lags["lag_fitted_s"], atol=2.0)
    # cells and back sheet in step: no lag found
    none = _rig(tmp_path, 0.0, n_days=3)
    r0 = ect.run(none["sweeps"], none["frame"], none["events"])
    assert abs(r0["lag"]["fitted"]["median_s"]) < 12


# --- in the pipeline -----------------------------------------------------------------------------------------

def test_pipeline_computes_ect_only_when_its_switch_is_on(rig, tmp_path, monkeypatch):
    quiet = dict(seed=5, n_boot=20, synthetic=True, log=lambda *a: None)
    off = pipeline.run(rig["out"], tmp_path / "off", **quiet)
    assert off["ect"] is None and not list((tmp_path / "off").glob("ect_*"))
    monkeypatch.setattr(config, "ECT_ENABLED", True)
    on = pipeline.run(rig["out"], tmp_path / "on", **quiet)
    assert {p.name for p in (tmp_path / "on").glob("ect_*")} == {"ect_sweeps.csv", "ect_spikes.csv"}
    text = (tmp_path / "on" / "summary.md").read_text(encoding="utf-8")
    assert "## Equivalent cell temperature from V_oc (IEC 60904-5 approach; NOT ADOPTED)" in text
    assert "[NOT this module's calibration]" in text and "SYNTHETIC DATA" in text
    assert on["ect"]["fitted"]["source"] == "fitted"
    for name in ("events.csv", "detection_results.csv", "verdict.json", "tuned.json"):
        assert (tmp_path / "on" / name).read_bytes() == (tmp_path / "off" / name).read_bytes(), name
