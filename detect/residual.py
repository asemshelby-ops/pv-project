"""Online thermal-model residual and its CUSUM statistic, shared by
detect/cusum.py (baseline b) and the hybrid NASIM-GBT features, so both run
exactly the same code (and one C function on the ESP32).

Per sample, given the filtered module temperature t:
  model  = thermal.step(model, G, Ta, v_slow) with the previous sample's inputs
  r      = t - model
  d      = r - r_base      r_base: 30-min EWMA of r (absorbs slow model bias)
  S      = max(0, S + d - k)
v_slow is a 60-min EWMA of the measured wind, or a constant without an
anemometer -- see detect/cusum.py for why the model must not see the lull.
"""
import math

import config
from sim import thermal

DT = config.DT_S


class CusumCore:
    def __init__(self, use_wind=True, k=config.CUSUM_K, model_params=None):
        self.use_wind, self.k = use_wind, k
        self.model_params = model_params or {}
        self.a_wind = 1.0 - math.exp(-DT / (config.CUSUM_WIND_TAU_MIN * 60))
        self.a_res = 1.0 - math.exp(-DT / (config.CUSUM_RES_TAU_MIN * 60))
        self.reset()

    def reset(self):
        self.model = None
        self.prev = None          # (G, Ta, v_slow) of the previous sample
        self.v_slow = None
        self.r_base = 0.0
        self.S = 0.0
        self.d = 0.0

    def update(self, t, s):
        """Returns False on the day's first sample (model initialised), else True."""
        if self.use_wind and s.wind == s.wind:
            self.v_slow = s.wind if self.v_slow is None else \
                self.v_slow + self.a_wind * (s.wind - self.v_slow)
        else:
            self.v_slow = config.CUSUM_WIND_CONST
        if self.model is None:
            self.model, self.prev = t, (s.g, s.ta, self.v_slow)
            return False
        self.model = thermal.step(self.model, *self.prev, **self.model_params)
        self.prev = (s.g, s.ta, self.v_slow)
        r = t - self.model
        self.d = r - self.r_base
        self.r_base += self.a_res * (r - self.r_base)
        self.S = max(0.0, self.S + self.d - self.k)
        return True
