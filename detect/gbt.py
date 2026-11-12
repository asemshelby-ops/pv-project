"""Detector (c): small gradient-boosted tree classifier (LightGBM).

Training (offline, on the PC):
  * features: detect/features.py run sample by sample over each training day
    -- the very code whose C twin will run on the ESP32;
  * target per sample: 1 during the first GBT_POS_WINDOW_MIN of a "spike",
    0 elsewhere (irradiance_rise samples are deliberate hard negatives);
    samples inside neutral windows (rest of a spike, minor_lull, mixed,
    natural_spike, natural_minor) are left out, so the model is never told a
    real lull is "not a spike";
  * class imbalance: positives weighted by n_negative / n_positive ("full",
    version 1) or by its square root ("sqrt", current): full weighting pushed
    the probabilities of many negatives close to 1 and made the threshold
    unstable with few training events;
  * hybrid (current): CUSUM's residual and statistic as two extra features.
Online: p = model(features); alarm when p >= threshold for PERSIST
consecutive samples (current: 2, version 1: 1), then 10-min silence. The
threshold is tuned on validation days.

Size budget: at most GBT_N_ESTIMATORS trees of depth <= GBT_MAX_DEPTH.
Reproducible: fixed seed, deterministic mode, one thread.
"""
import lightgbm as lgb
import numpy as np
import pandas as pd

import config
from detect.features import FeatureExtractor, feature_names
from detect.stream import samples

SILENCE_N = int(config.SILENCE_MIN * 60 / config.DT_S)
NEUTRAL = {"minor_lull", "mixed", "natural_spike", "natural_minor", "ambient_rise",
           "minor", "excluded"}     # the last two: paper labels (analysis/events.py)


def build_features(days, use_wind, hybrid=False, model_params=None):
    """Feature matrix, one row per sample; NaN rows while buffers fill."""
    names = feature_names(use_wind, hybrid)
    fx = FeatureExtractor(use_wind, hybrid, model_params)
    X = np.full((len(days), len(names)), np.nan)
    for i, (new_day, s) in enumerate(samples(days)):
        if new_day:
            fx.reset()
        f = fx.update(s)
        if f is not None:
            X[i] = [f[n] for n in names]
    return X


def sample_targets(days, events):
    """y (0/1) and a use-mask per sample, from the events table."""
    ts = pd.to_datetime(days["date"] + " " + days["time"]).to_numpy()
    y = np.zeros(len(days), dtype=int)
    use = np.ones(len(days), dtype=bool)
    pos_len = np.timedelta64(int(config.GBT_POS_WINDOW_MIN * 60), "s")
    for _, e in events.iterrows():
        s, t_end = np.datetime64(pd.Timestamp(e["start"])), np.datetime64(pd.Timestamp(e["end"]))
        i0, i1 = np.searchsorted(ts, s), np.searchsorted(ts, t_end, side="right")
        if e["class"] == "spike":
            ip = np.searchsorted(ts, s + pos_len)
            y[i0:ip] = 1
            use[ip:i1] = False
        elif e["class"] in NEUTRAL:
            use[i0:i1] = False
    return y, use


def train(X, y, use, seed, names=None, weighting=config.GBT_CLASS_WEIGHT):
    ok = use & ~np.isnan(X).any(axis=1)
    Xt, yt = X[ok], y[ok]
    n_pos = int(yt.sum())
    ratio = (len(yt) - n_pos) / max(n_pos, 1)
    model = lgb.LGBMClassifier(
        n_estimators=config.GBT_N_ESTIMATORS,
        max_depth=config.GBT_MAX_DEPTH,
        num_leaves=config.GBT_NUM_LEAVES,
        learning_rate=config.GBT_LEARNING_RATE,
        min_child_samples=config.GBT_MIN_CHILD,
        scale_pos_weight=ratio if weighting == "full" else np.sqrt(ratio),
        random_state=seed, deterministic=True, force_row_wise=True,
        n_jobs=1, verbose=-1)
    model.fit(Xt, yt, feature_name=names or "auto")
    return model


class GbtDetector:
    def __init__(self, model, use_wind, p_threshold=config.GBT_P_THRESHOLD,
                 hybrid=False, persist=1, model_params=None):
        self.booster = model.booster_ if hasattr(model, "booster_") else model
        self.names = feature_names(use_wind, hybrid)
        self.p_threshold, self.persist = p_threshold, persist
        self.fx = FeatureExtractor(use_wind, hybrid, model_params)
        self.reset()

    def reset(self):
        self.fx.reset()
        self.quiet = 0
        self.run = 0              # consecutive samples with p >= threshold
        self.p = float("nan")

    def update(self, s):
        f = self.fx.update(s)
        if self.quiet > 0:
            self.quiet -= 1
        if f is None:
            self.p = float("nan")
            self.run = 0
            return False
        x = np.array([[f[n] for n in self.names]])
        self.x = x[0]             # last feature vector (for the C parity report)
        self.p = float(self.booster.predict(x)[0])
        self.run = self.run + 1 if self.p >= self.p_threshold else 0
        if self.quiet > 0:
            return False
        if self.run >= self.persist:
            self.quiet = SILENCE_N
            self.run = 0
            return True
        return False
