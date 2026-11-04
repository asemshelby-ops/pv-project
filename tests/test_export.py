"""Checks of export/: generated header and model code; end-to-end C parity
on a small model (skipped when no gcc is available)."""
import subprocess

import numpy as np
import pytest

import config
from data import synth
from detect import gbt
from detect.features import N10, feature_names
from export import run as ex


def test_config_header_carries_config_values():
    h = ex.config_header()
    assert f"#define NASIM_FAIMAN_U1 {config.FAIMAN_U1!r}" in h
    assert f"#define NASIM_HYBRID_CUSUM_K {config.HYBRID_CUSUM_K!r}" in h
    assert f"#define NASIM_N10 {N10}" in h


@pytest.fixture(scope="module")
def small_models():
    days, events = synth.generate(seed=2, n_days=3)
    y, use = gbt.sample_targets(days, events)
    out = {}
    for tag, use_wind in (("nowind", False), ("wind", True)):
        names = feature_names(use_wind, True)
        clf = gbt.train(gbt.build_features(days, use_wind, hybrid=True), y, use, seed=0,
                        names=names, weighting="sqrt")
        out[tag] = (clf, use_wind, names)
    return days, out


def test_model_source_uses_real_and_unique_names(small_models):
    _, models = small_models
    clf, _, names = models["nowind"]
    src = ex.model_source(clf, "nowind", names, 0.5, "test")
    assert "double" not in src and "void nasim_score_nowind(real" in src
    assert "static real sigmoid_nowind(" in src and "R_EXP(" in src


@pytest.mark.skipif(ex.find_gcc() is None, reason="no gcc")
def test_c_detector_matches_python_double(small_models, tmp_path):
    days, models = small_models
    for tag, (clf, _, names) in models.items():
        (tmp_path / f"nasim_model_{tag}.c").write_text(ex.model_source(clf, tag, names, 0.5, "t"))
    (tmp_path / "nasim_config.h").write_text(ex.config_header())
    exe = ex.compile_parity(ex.find_gcc(), False, gen_dir=tmp_path, out=tmp_path)
    inp = tmp_path / "in.csv"
    ex.write_input(days, inp)
    for tag, (clf, use_wind, names) in models.items():
        thr = 0.3
        py = ex.python_reference(clf, use_wind, thr, days)
        outp = tmp_path / f"out_{tag}.csv"
        subprocess.run([str(exe), str(int(use_wind)), repr(thr), str(config.GBT_PERSIST),
                        str(inp), str(outp)], check=True)
        r = ex.compare(py, ex.read_c_output(outp, len(names)), names)
        assert r["warm_up_pattern_equal"] and r["alarm_mismatches"] == 0
        assert r["max_abs_dp"] < 1e-12 and r["alarms_python"] > 0
        assert max(r["max_abs_feature_diff"].values()) < 1e-9
