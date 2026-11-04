"""Baseline (a): temperature-rise threshold rule with an irradiance gate.

Alarm when, on the 3-sample-median module temperature,
    rise over the last 10 min >= 2.5 degC
and irradiance was steady over the same 10 min:
    (max G - min G) / G_now < 5 %.
After an alarm, no new alarm for 10 min.

Why max-min and not end points: a cloud that passes and clears inside the
window leaves G at both ends equal, yet the module re-heats strongly after it
-- exactly the irradiance-driven rise the gate exists to block.
Parameters are the team's fixed values (config.py); they are not tuned here.
"""
import config
from detect.features import FeatureExtractor

SILENCE_N = int(config.SILENCE_MIN * 60 / config.DT_S)


class ThresholdDetector:
    def __init__(self, rise_c=config.THR_RISE_C, gate=config.THR_GATE):
        self.rise_c, self.gate = rise_c, gate
        self.fx = FeatureExtractor(use_wind=False)
        self.reset()

    def reset(self):
        self.fx.reset()
        self.quiet = 0

    def update(self, s):
        f = self.fx.update(s)
        if self.quiet > 0:
            self.quiet -= 1
        if f is None or self.quiet > 0:
            return False
        if f["rise_10m"] >= self.rise_c and f["g_range_10m"] < self.gate:
            self.quiet = SILENCE_N
            return True
        return False
