"""Baseline (b) of the paper: CUSUM on the temperature increment.

    S[k] = max(0, S[k-1] + (t[k] - t[k-1]) - kappa)        alarm when S >= h

t is the 3-sample-median module temperature (the same signal as the other detectors).
kappa is the rise per sample that is allowed without accumulating (normal heating is
about 0.1 degC/min = 0.017 degC per 10 s sample); h is the accumulated excess rise that
raises the alarm. After an alarm S restarts at 0 and the detector is silent for 10 min.

Why not the model-residual CUSUM of detect/cusum.py: that model is driven by the
measured irradiance, so it explains away exactly the rises that follow a cloud -- which
count as spikes since 2026-11-10. This version uses temperature only and sees every
cause. The residual and its CUSUM remain inputs of NASIM-GBT.
"""
import config
from detect.features import N10, Ring, median3

SILENCE_N = int(config.SILENCE_MIN * 60 / config.DT_S)


class CusumIncDetector:
    def __init__(self, kappa, h):
        self.kappa, self.h = kappa, h
        self.reset()

    def reset(self):
        self.t_raw = Ring(config.MEDIAN_N)
        self.prev = None
        self.S = 0.0
        self.n = 0
        self.quiet = 0

    def update(self, s):
        self.t_raw.push(sum(s.t_back) / len(s.t_back))
        if not self.t_raw.full():
            return False
        t = median3(self.t_raw.ago(0), self.t_raw.ago(1), self.t_raw.ago(2))
        if self.prev is None:                  # first filtered value of the day
            self.prev = t
            return False
        self.S = max(0.0, self.S + (t - self.prev) - self.kappa)
        self.prev = t
        self.n += 1
        if self.quiet > 0:
            self.quiet -= 1
        if self.n < N10 or self.quiet > 0:     # same 10-min warm-up as the other detectors
            return False
        if self.S >= self.h:
            self.S = 0.0
            self.quiet = SILENCE_N
            return True
        return False
