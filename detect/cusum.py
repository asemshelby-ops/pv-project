"""Baseline (b): CUSUM on the residual between measured and modelled temperature.

The model is sim/thermal.py run online, driven by measured irradiance G and
ambient temperature Ta, so it predicts how hot the module SHOULD be.

Why the model's wind is a slow baseline and not the measured wind:
our spikes ARE wind lulls. If the model were fed the measured wind, it would
predict the lull's temperature rise itself, the residual would stay near zero
and CUSUM would miss exactly the events we want. So the model sees only a slow
wind level -- a 60-min moving average (EWMA) of the anemometer, or a constant
when there is no anemometer. The residual then means "warmer than irradiance
and the usual wind explain", which separates wind-lull spikes from
irradiance-driven rises (those the model does explain through G).

Steps per sample:
  1. t     = 3-sample-median module temperature (same signal as the other detectors)
  2. model = thermal.step(model, G, Ta, v_slow) with the previous sample's inputs
  3. r     = t - model;   d = r - r_base   (r_base: 30-min EWMA of r, absorbs
             slow model bias such as sensor offsets or the wrong wind level)
  4. S     = max(0, S + d - k);  alarm when S > h, then S = 0 and 10-min silence
k (allowance) and h (alarm level) are tuned on validation days in Step 4.

Caveat for synthetic data: the generator uses the same thermal model, so the
model structure is perfect here; on the real module the residual will be larger.
Step 4 therefore also runs CUSUM with C, u0, u1 each off by +/-20 %.
"""
import config
from detect.features import N10, Ring, median3
from detect.residual import CusumCore

DT = config.DT_S
SILENCE_N = int(config.SILENCE_MIN * 60 / DT)


class CusumDetector:
    def __init__(self, use_wind=True, k=config.CUSUM_K, h=config.CUSUM_H,
                 model_params=None):
        """model_params: optional dict of c, u0, u1 for the internal model
        (default: the same values as the generator; see the mismatch study)."""
        self.h = h
        self.core = CusumCore(use_wind, k, model_params)   # steps 2-4 above
        self.reset()

    def reset(self):
        self.t_raw = Ring(config.MEDIAN_N)
        self.core.reset()
        self.n = 0
        self.quiet = 0

    def update(self, s):
        self.t_raw.push(sum(s.t_back) / len(s.t_back))
        if not self.t_raw.full():
            return False
        t = median3(self.t_raw.ago(0), self.t_raw.ago(1), self.t_raw.ago(2))
        if not self.core.update(t, s):               # first sample of the day
            return False
        self.n += 1

        if self.quiet > 0:
            self.quiet -= 1
        if self.n < N10 or self.quiet > 0:           # same 10-min warm-up as (a), (c)
            return False
        if self.core.S > self.h:
            self.core.S = 0.0
            self.quiet = SILENCE_N
            return True
        return False
