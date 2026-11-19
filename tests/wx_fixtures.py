"""Shared helpers for the weather-reference tests: the Open-Meteo answers of
tests/fixtures/openmeteo/ served without any network, and synthetic clear-sky days."""
import json
import pathlib
import urllib.parse

import numpy as np
import pandas as pd

from wxref import Site, check

FIX = pathlib.Path(__file__).resolve().parent / "fixtures" / "openmeteo"
SITE = Site(21.42, 39.83, 180.0, (("2000-01-01", 35.0), ("2027-03-01", 20.0)))


def fixture_bytes(name):
    return (FIX / name).read_bytes()


class FakeTransport:
    """Answers like Open-Meteo from the fixtures, by host: the RECORDED satellite answer for
    the satellite API, the CONSTRUCTED forecast answer for the forecast and archive APIs.
    Records every URL asked. `fail`: an exception to raise instead (offline)."""

    def __init__(self, fail=None, status=200, body=None):
        self.urls, self.fail, self.status, self.body = [], fail, status, body

    def __call__(self, url, timeout):
        self.urls.append(url)
        if self.fail is not None:
            raise self.fail
        if self.body is not None:
            return self.status, self.body
        host = urllib.parse.urlparse(url).netloc
        name = ("satellite_recorded_2026-11-16_17.json" if host.startswith("satellite")
                else "forecast_CONSTRUCTED_2026-11-16_17.json")
        return 200, fixture_bytes(name)


def clear_days(first="2026-11-01", n=12, scale=None, site=SITE, ratio=0.97, step_s=10):
    """Synthetic PYR20 rows (06-18 local) and satellite model rows for `n` clear days.
    The model in-plane value is the clear-sky hour mean; the PYR20 reads ratio x clear sky,
    times scale(day index) if given. Returns (measured, model rows)."""
    t0 = int(pd.Timestamp(f"{first} 06:00", tz="Asia/Riyadh").timestamp())
    meas, rows = [], []
    for d in range(n):
        start = t0 + d * 86400
        ts = np.arange(start, start + 12 * 3600, step_s)
        cs = check.clear_sky_poa(ts, site)
        k = ratio * (scale(d) if scale else 1.0)
        meas.append(pd.DataFrame({"t_utc": ts, "irradiance": np.round(cs * k)}))
        hrs = np.arange(start + 3600, start + 12 * 3600 + 1, 3600)
        grid = (hrs[:, None] - 3600 + 30 + 60 * np.arange(60)[None, :]).ravel()
        poa = check.clear_sky_poa(grid, site).reshape(len(hrs), 60).mean(axis=1)
        day = pd.Timestamp(start, unit="s", tz="UTC").tz_convert("Asia/Riyadh").strftime("%Y-%m-%d")
        rows.append(pd.DataFrame({"t_utc": hrs, "wx_source": "satellite",
                                  "wx_model_poa_wm2": poa, "wx_model_tilt_deg": site.tilt_on(day),
                                  "wx_model_cloud_cover_pct": np.nan}))
    return pd.concat(meas, ignore_index=True), pd.concat(rows, ignore_index=True)


def load_json(name):
    return json.loads(fixture_bytes(name))
