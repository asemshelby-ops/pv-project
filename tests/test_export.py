"""Checks of export/: generated headers and model code; end-to-end C parity of the three
on-board detectors on a small model (skipped when no gcc is available)."""
import pathlib
import re
import subprocess

import numpy as np
import pytest

import config
from analysis import export_model
from data import synth
from data.schema import ALARM_BITS, has_alarm
from detect import gbt, stream
from detect.cusum_inc import CusumIncDetector
from detect.features import N10, feature_names
from detect.threshold import ThresholdDetector
from export import run as ex

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_config_header_carries_config_values():
    h = ex.config_header()
    assert f"#define NASIM_FAIMAN_U1 {config.FAIMAN_U1!r}" in h
    assert f"#define NASIM_HYBRID_CUSUM_K {config.HYBRID_CUSUM_K!r}" in h
    assert f"#define NASIM_N10 {N10}" in h
    assert f"#define NASIM_GBT_PERSIST {config.GBT_PERSIST}\n" in h
    for name, bit in ALARM_BITS.items():
        assert f"#define NASIM_ALARM_{name} {1 << bit}\n" in h


def test_alarm_column_bits():
    import pandas as pd
    assert sorted(ALARM_BITS.values()) == [0, 1, 2] and ALARM_BITS["NASIM"] == 0
    s = pd.Series([0, 1, 6, None, 7])            # 1 = files written with NASIM only
    assert list(has_alarm(s, "NASIM")) == [False, True, False, False, True]
    assert list(has_alarm(s, "THRESHOLD")) == [False, False, True, False, True]
    assert list(has_alarm(s, "CUSUM")) == [False, False, True, False, True]


def test_tuned_settings_reach_the_firmware_only_through_generated_files():
    tuned = {"nasim_p_threshold": 0.98, "threshold_rise_C": 2.7,
             "cusum_kappa_C_per_sample": 0.02, "cusum_h_C": 2}
    h = export_model.tuned_header(tuned, "test")
    for line in ("#define NASIM_TUNED_P_THRESHOLD 0.98", "#define NASIM_TUNED_THR_RISE_C 2.7",
                 "#define NASIM_TUNED_CUSUM_KAPPA_C 0.02", "#define NASIM_TUNED_CUSUM_H_C 2.0"):
        assert line + "\n" in h
    # the committed header and the committed model come from the same run
    gen = ROOT / "export/c/generated"
    p_thr = re.search(r"NASIM_TUNED_P_THRESHOLD (\S+)", (gen / "nasim_tuned.h").read_text())[1]
    assert f"Alarm rule: p >= {p_thr} for" in (gen / "nasim_model_nowind.c").read_text()
    # and the logger holds names, not numbers
    ino = (ROOT / "firmware/logger/logger.ino").read_text()
    for name, macro in (("NASIM_P_THRESHOLD", "NASIM_TUNED_P_THRESHOLD"),
                        ("NASIM_PERSIST", "NASIM_GBT_PERSIST"),
                        ("THR_RISE_C", "NASIM_TUNED_THR_RISE_C"),
                        ("CUSUM_KAPPA_C", "NASIM_TUNED_CUSUM_KAPPA_C"),
                        ("CUSUM_H_C", "NASIM_TUNED_CUSUM_H_C")):
        assert re.search(rf"static const \w+ {name} = {macro};", ino), name


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


@pytest.mark.skipif(ex.find_gcc() is None, reason="no gcc")
def test_c_baselines_match_python_double(small_models, tmp_path):
    """Threshold rule and increment CUSUM: the C detectors against the Python streaming
    classes (which tests/test_analysis.py ties to the pipeline's array rules)."""
    days, models = small_models
    for tag, (clf, _, names) in models.items():
        (tmp_path / f"nasim_model_{tag}.c").write_text(ex.model_source(clf, tag, names, 0.5, "t"))
    (tmp_path / "nasim_config.h").write_text(ex.config_header())
    exe = ex.compile_parity(ex.find_gcc(), False, gen_dir=tmp_path, out=tmp_path)
    inp, outp = tmp_path / "in.csv", tmp_path / "out.csv"
    ex.write_input(days, inp)
    n = len(models["nowind"][2])
    for rise_c, kappa, h in ((2.0, 0.02, 1.0), (2.7, 0.02, 2.0), (1.0, 0.005, 0.5)):
        subprocess.run([str(exe), "0", "0.3", str(config.GBT_PERSIST), str(inp), str(outp),
                        repr(rise_c), repr(kappa), repr(h)], check=True)
        a_thr, a_cusum = ex.read_c_output(outp, n, baselines=True)[3:]
        py_thr = stream.run(ThresholdDetector(rise_c=rise_c, gate=None), days)
        py_cusum = stream.run(CusumIncDetector(kappa, h), days)
        np.testing.assert_array_equal(a_thr, py_thr)
        np.testing.assert_array_equal(a_cusum, py_cusum)
        assert py_thr.sum() > 0 and py_cusum.sum() > 0
