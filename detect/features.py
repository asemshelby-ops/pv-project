"""Streaming features, one 10 s sample at a time, using only past samples.

Written so that it translates line by line into C for the ESP32:
  * fixed-size ring buffers (no growing lists, no pandas),
  * scalar arithmetic and math.h functions only,
  * every feature depends only on the current and earlier samples.
The same class produces the training features and the online features, so
training and inference cannot drift apart (feature parity, Step 5 test).

Module temperature signal: mean of the five back-sheet sensors, then the
median of the last 3 such means (removes single-sample glitches).
"""
import math

import config
from detect.residual import CusumCore

DT = config.DT_S
N2 = int(2 * 60 / DT)                       # 12 samples = 2 min
N5 = int(5 * 60 / DT)                       # 30 samples = 5 min
N10 = int(config.DET_WINDOW_MIN * 60 / DT)  # 60 samples = 10 min

BASE_FEATURES = [
    "rise_2m", "rise_5m", "rise_10m",   # degC, temperature rise over the window
    "g_change_2m",                      # relative irradiance change over 2 min
    "g_range_10m",                      # (max - min) / now, irradiance, 10 min
    "kc",                               # clear-sky index in the module plane
    "cos_zenith",                       # solar geometry (time of day / season)
    "excess_per_g",                     # (T - Ta) / G * 1000 ~ 1000 / U (Faiman)
]
WIND_FEATURES = [
    "wind_2m",                          # m/s, 2-min mean wind
    "wind_drop",                        # 2-min mean 10 min ago minus now
]
HYBRID_FEATURES = [
    "cusum_d",                          # degC, thermal-model residual minus its baseline
    "cusum_S",                          # CUSUM statistic of cusum_d (never reset here)
]


def feature_names(use_wind, hybrid=False):
    return (BASE_FEATURES + (WIND_FEATURES if use_wind else [])
            + (HYBRID_FEATURES if hybrid else []))


class Ring:
    """Fixed-size ring buffer; ago(0) is the newest value."""

    def __init__(self, size):
        self.size = size
        self.buf = [0.0] * size
        self.head = -1
        self.count = 0

    def push(self, x):
        self.head = (self.head + 1) % self.size
        self.buf[self.head] = x
        self.count = min(self.count + 1, self.size)

    def ago(self, k):
        return self.buf[(self.head - k + self.size) % self.size]

    def full(self):
        return self.count == self.size


def median3(a, b, c):
    return max(min(a, b), min(max(a, b), c))


# --- solar geometry and simple clear-sky irradiance -------------------------

def solar_angles(doy, minute_local):
    """cos(zenith) and cos(angle of incidence on the module).

    NOAA "General Solar Position Calculations" (fractional-year series) and
    Duffie & Beckman eq. 1.6.2 for a tilted surface. Accurate to a fraction of a
    degree, which is ample for a normaliser.
    """
    d2r = math.pi / 180.0
    gam = 2.0 * math.pi / 365.0 * (doy - 1 + (minute_local / 60.0 - 12.0) / 24.0)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(gam) - 0.032077 * math.sin(gam)
                       - 0.014615 * math.cos(2 * gam) - 0.040849 * math.sin(2 * gam))
    decl = (0.006918 - 0.399912 * math.cos(gam) + 0.070257 * math.sin(gam)
            - 0.006758 * math.cos(2 * gam) + 0.000907 * math.sin(2 * gam)
            - 0.002697 * math.cos(3 * gam) + 0.00148 * math.sin(3 * gam))
    tz_hours = 3.0                                  # Asia/Riyadh, UTC+3
    true_solar_min = minute_local + eqtime + 4.0 * config.LON - 60.0 * tz_hours
    ha = (true_solar_min / 4.0 - 180.0) * d2r       # hour angle
    lat, beta = config.LAT * d2r, config.TILT_DEG * d2r
    gs = (config.AZIMUTH_DEG - 180.0) * d2r         # surface azimuth from south
    sd, cd = math.sin(decl), math.cos(decl)
    cos_z = math.sin(lat) * sd + math.cos(lat) * cd * math.cos(ha)
    cos_aoi = (sd * math.sin(lat) * math.cos(beta)
               - sd * math.cos(lat) * math.sin(beta) * math.cos(gs)
               + cd * math.cos(lat) * math.cos(beta) * math.cos(ha)
               + cd * math.sin(lat) * math.sin(beta) * math.cos(gs) * math.cos(ha)
               + cd * math.sin(beta) * math.sin(gs) * math.sin(ha))
    return cos_z, cos_aoi


def clear_sky_poa_simple(cos_z, cos_aoi):
    """Clear-sky in-plane irradiance, W/m2 (Meinel DNI + Kasten-Young air mass)."""
    if cos_z <= 0.01:
        return 0.0
    z_deg = math.degrees(math.acos(cos_z))
    am = 1.0 / (cos_z + 0.50572 * (96.07995 - z_deg) ** -1.6364)
    dni = 1353.0 * 0.7 ** (am ** 0.678)
    dhi = config.CS_DIFFUSE_FRAC * dni
    beta = math.radians(config.TILT_DEG)
    return dni * max(cos_aoi, 0.0) + dhi * (1.0 + math.cos(beta)) / 2.0


# --- the extractor ------------------------------------------------------------

class FeatureExtractor:
    def __init__(self, use_wind=config.GBT_USE_WIND, hybrid=False, model_params=None):
        """hybrid: add CUSUM's residual and statistic (detect/residual.py, the
        same code as the CUSUM baseline). model_params: its thermal model."""
        self.use_wind, self.hybrid = use_wind, hybrid
        self.core = (CusumCore(use_wind, config.HYBRID_CUSUM_K, model_params)
                     if hybrid else None)
        self.reset()

    def reset(self):
        """Call at the start of each logged day (the overnight gap)."""
        self.t_raw = Ring(config.MEDIAN_N)
        self.t_med = Ring(N10 + 1)
        self.g = Ring(N10 + 1)
        self.wind = Ring(N10)
        if self.core is not None:
            self.core.reset()

    def update(self, s):
        """s: a Sample (detect/stream.py). Returns a dict of features, or None
        while the buffers are still filling (first ~10 min of a day)."""
        t_mean = sum(s.t_back) / len(s.t_back)
        self.t_raw.push(t_mean)
        self.g.push(s.g)
        self.wind.push(s.wind if s.wind == s.wind else 0.0)   # NaN -> 0 (unused)
        if not self.t_raw.full():
            return None
        t = median3(self.t_raw.ago(0), self.t_raw.ago(1), self.t_raw.ago(2))
        self.t_med.push(t)
        if self.core is not None:
            self.core.update(t, s)          # every sample, also during warm-up
        if not (self.t_med.full() and self.g.full() and self.wind.full()):
            return None

        g0 = self.g.ago(0)
        g_max = g_min = g0
        for k in range(1, N10 + 1):
            g_max = max(g_max, self.g.ago(k))
            g_min = min(g_min, self.g.ago(k))
        cos_z, cos_aoi = solar_angles(s.doy, s.minute)
        g_cs = clear_sky_poa_simple(cos_z, cos_aoi)

        f = {
            "rise_2m": t - self.t_med.ago(N2),
            "rise_5m": t - self.t_med.ago(N5),
            "rise_10m": t - self.t_med.ago(N10),
            "g_change_2m": (g0 - self.g.ago(N2)) / max(self.g.ago(N2), config.G_FLOOR),
            "g_range_10m": (g_max - g_min) / max(g0, config.G_FLOOR),
            "kc": g0 / max(g_cs, config.G_FLOOR),
            "cos_zenith": cos_z,
            "excess_per_g": (t - s.ta) / max(g0, config.G_FLOOR) * 1000.0,
        }
        if self.use_wind:
            now = sum(self.wind.ago(k) for k in range(N2)) / N2
            before = sum(self.wind.ago(k) for k in range(N10 - N2, N10)) / N2
            f["wind_2m"] = now
            f["wind_drop"] = before - now
        if self.hybrid:
            f["cusum_d"] = self.core.d
            f["cusum_S"] = self.core.S
        f["t"] = t           # filtered temperature, used by the detectors (not a model input)
        return f
