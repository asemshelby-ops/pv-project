"""Open-Meteo client: documented endpoints and parameters only, a disk cache, and no error
that can reach the logging, the historian or the SCADA.

Read on 2026-11-19 (docs/weather-reference.md quotes what each item rests on):
  forecast   https://api.open-meteo.com/v1/forecast            weather models; start_date /
             end_date may lie in the recent past (the past is the models' own analysis)
  archive    https://archive-api.open-meteo.com/v1/archive     reanalysis (ERA5 and others),
             for days older than the forecast endpoint keeps
  satellite  https://satellite-api.open-meteo.com/v1/archive   satellite radiation retrievals
             (models=satellite_radiation_seamless)
Parameters used, all from those pages: latitude, longitude, hourly, tilt, azimuth,
start_date, end_date, timezone, timeformat=unixtime, wind_speed_unit=ms (forecast and
archive), models (satellite). Hourly data only: outside North America and Central Europe
the forecast's 15-minute data are interpolated from hourly values (forecast docs), so they
would add rows, not information.

Time semantics (docs): radiation variables are the MEAN OF THE HOUR ENDING at the stamp;
temperature, wind and cloud cover are INSTANT values at the stamp. With timeformat=unixtime
the stamps are Unix seconds UTC; `timezone` only sets which local days start_date/end_date
mean (we ask for config.TZ, so dates are Makkah days).

Free API terms (https://open-meteo.com/en/terms): non-commercial use (public research at a
public institution qualifies), < 10 000 calls a day, 5 000 an hour, 600 a minute. This
client makes about one call per source per hour while the SCADA runs.
"""
import datetime
import hashlib
import http.client
import json
import os
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request

import numpy as np
import pandas as pd

import config
from wxref import PREFIX

ENDPOINTS = {"forecast": "https://api.open-meteo.com/v1/forecast",
             "archive": "https://archive-api.open-meteo.com/v1/archive",
             "satellite": "https://satellite-api.open-meteo.com/v1/archive"}
SATELLITE_MODEL = "satellite_radiation_seamless"
MET = ["temperature_2m", "wind_speed_10m", "wind_direction_10m", "cloud_cover"]
RADIATION = ["shortwave_radiation", "global_tilted_irradiance"]
HOURLY = {"forecast": RADIATION + MET, "archive": RADIATION + MET,
          "satellite": RADIATION + ["shortwave_radiation_clear_sky"]}
# Open-Meteo variable -> (our column, unit the response must state, meaning)
VARIABLES = {
    "shortwave_radiation": (PREFIX + "ghi_wm2", "W/m²",
                            "global horizontal irradiance, mean of the hour ending at t"),
    "global_tilted_irradiance": (PREFIX + "poa_wm2", "W/m²",
                                 "in-plane (tilted) irradiance for our tilt and azimuth, mean "
                                 "of the hour ending at t; isotropic sky, albedo 0.2 (docs)"),
    "shortwave_radiation_clear_sky": (PREFIX + "ghi_clearsky_wm2", "W/m²",
                                      "clear-sky GHI of the satellite source, mean of the hour "
                                      "ending at t"),
    "temperature_2m": (PREFIX + "temp_2m_c", "°C", "air temperature at 2 m, instant at t"),
    "wind_speed_10m": (PREFIX + "wind_speed_10m_ms", "m/s", "wind speed at 10 m, instant at t"),
    "wind_direction_10m": (PREFIX + "wind_dir_10m_deg", "°",
                           "wind direction at 10 m (from), instant at t"),
    "cloud_cover": (PREFIX + "cloud_cover_pct", "%", "total cloud cover, instant at t"),
}
MODEL_COLUMNS = [v[0] for v in VARIABLES.values()]
ROW_COLUMNS = (["t_utc", "wx_source"] + MODEL_COLUMNS
               + [PREFIX + "tilt_deg", PREFIX + "azimuth_deg", "wx_grid_lat", "wx_grid_lon",
                  "wx_grid_elevation_m"])
# a cached answer that covers only days older than this is final: never asked again
FINAL_AFTER_DAYS = {"forecast": 2, "satellite": 2, "archive": 7}
USER_AGENT = "pv-rig-weather-reference/1.0 (python urllib)"


class WxError(Exception):
    """Any failed call: offline, refused (HTTP error / rate limit), or an unexpected answer.
    `limit`: True when Open-Meteo said a request limit was reached."""

    def __init__(self, message, limit=False):
        super().__init__(message)
        self.limit = limit


def openmeteo_azimuth(compass_deg):
    """Compass azimuth (0 N, 90 E, 180 S, 270 W) -> Open-Meteo's (0 S, -90 E, 90 W, +-180 N)."""
    a = (float(compass_deg) - 180.0 + 180.0) % 360.0 - 180.0
    return 0.0 if a == 0 else a


def params(source, lat, lon, start, end, tilt, azimuth_compass):
    """URL parameters of one request, in a fixed order (the cache key depends on it)."""
    if source not in ENDPOINTS:
        raise ValueError(f"unknown source {source!r}; use one of {sorted(ENDPOINTS)}")
    p = [("latitude", f"{float(lat):.4f}"), ("longitude", f"{float(lon):.4f}"),
         ("hourly", ",".join(HOURLY[source])), ("tilt", f"{float(tilt):g}"),
         ("azimuth", f"{openmeteo_azimuth(azimuth_compass):g}"),
         ("start_date", str(start)), ("end_date", str(end)), ("timezone", config.TZ),
         ("timeformat", "unixtime")]
    if source == "satellite":
        p.append(("models", SATELLITE_MODEL))
    else:
        p.append(("wind_speed_unit", "ms"))
    return p


def url(source, *args):
    return ENDPOINTS[source] + "?" + urllib.parse.urlencode(params(source, *args), safe=",/")


def parse(response, source, tilt, azimuth_compass):
    """One Open-Meteo JSON answer -> rows (ROW_COLUMNS), t_utc in Unix seconds. Raises WxError
    for an error answer, a wrong time format or a unit other than the one we asked for."""
    if not isinstance(response, dict):
        raise WxError("answer is not a JSON object")
    if response.get("error"):
        reason = str(response.get("reason", "no reason given"))
        raise WxError(f"Open-Meteo refused: {reason}", limit="limit" in reason.lower())
    hourly, units = response.get("hourly"), response.get("hourly_units") or {}
    if not isinstance(hourly, dict) or "time" not in hourly:
        raise WxError("answer has no hourly data")
    if units.get("time") != "unixtime":
        raise WxError(f"time format {units.get('time')!r}, expected unixtime")
    n = len(hourly["time"])
    out = pd.DataFrame({"t_utc": np.asarray(hourly["time"], dtype="int64"),
                        "wx_source": source})
    for var, (col, unit, _) in VARIABLES.items():
        if var in hourly:
            if units.get(var) != unit:
                raise WxError(f"{var}: unit {units.get(var)!r}, expected {unit!r}")
            if len(hourly[var]) != n:
                raise WxError(f"{var}: {len(hourly[var])} values for {n} times")
            out[col] = pd.to_numeric(pd.Series(hourly[var], dtype="object"),
                                     errors="coerce").astype(float).to_numpy()
        else:
            out[col] = np.nan
    out[PREFIX + "tilt_deg"] = float(tilt)
    out[PREFIX + "azimuth_deg"] = float(azimuth_compass)
    out["wx_grid_lat"] = response.get("latitude", np.nan)
    out["wx_grid_lon"] = response.get("longitude", np.nan)
    out["wx_grid_elevation_m"] = response.get("elevation", np.nan)
    return out[ROW_COLUMNS]


def urllib_transport(u, timeout):
    """GET -> (HTTP status, body bytes). OSError for a network problem."""
    req = urllib.request.Request(u, headers={"User-Agent": USER_AGENT,
                                             "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class Client:
    """Fetches through a disk cache. get() never raises: it returns (rows, error text or
    None); on failure it returns the cached rows if there are any.

    A request is sent only when
      * its answer is not cached, or the cached one is older than WX_REFRESH_S and covers a
        day that is not yet final (FINAL_AFTER_DAYS: complete past days are never asked
        again), and
      * no call to the SAME endpoint failed in the back-off time (WX_RETRY_S: 5 min
        doubling up to 1 h; a request limit waits the full hour). Each endpoint backs off on
        its own: on 2026-11-19 the forecast API refused us (daily limit) while the
        satellite API answered.
    The first failure of an outage is logged once, as a warning; the next success says so."""

    def __init__(self, cache_dir=None, clock=time.time, transport=None, log=None,
                 timeout=None, refresh_s=None, retry_s=None):
        self.cache_dir = pathlib.Path(cache_dir or pathlib.Path(config.WX_DIR) / "cache")
        self.clock, self.transport = clock, transport or urllib_transport
        self.log = log or (lambda m: print(m, flush=True))
        self.timeout = config.WX_TIMEOUT_S if timeout is None else timeout
        self.refresh_s = config.WX_REFRESH_S if refresh_s is None else refresh_s
        self.retry_min, self.retry_max = retry_s or config.WX_RETRY_S
        self._fails = {}                # source -> {"n", "next", "error"} while failing
        self.calls, self.last_ok_wall = 0, None

    def backoff(self, source):
        """(failed calls in a row, earliest next call, last error) of one endpoint."""
        f = self._fails.get(source)
        return (f["n"], f["next"], f["error"]) if f else (0, 0.0, None)

    def status(self):
        f = self._fails
        return {"failures": sum(v["n"] for v in f.values()), "failing": sorted(f),
                "last_error": "; ".join(f"{k}: {v['error']}" for k, v in sorted(f.items()))
                or None,
                "next_try_wall": min((v["next"] for v in f.values()), default=None),
                "calls": self.calls, "last_ok_wall": self.last_ok_wall, "offline": bool(f)}

    def _path(self, source, start, end, u):
        key = hashlib.sha256(u.encode("utf-8")).hexdigest()[:16]
        return self.cache_dir / source / f"{start}_{end}_{key}.json"

    def _final(self, source, end, now):
        today = datetime.datetime.fromtimestamp(now, datetime.UTC).date()
        return (today - datetime.date.fromisoformat(str(end))).days > FINAL_AFTER_DAYS[source]

    @staticmethod
    def _read(path):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _write(self, path, entry):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(entry), encoding="utf-8")
        os.replace(tmp, path)

    def get(self, source, site, start, end, tilt):
        """Hourly MODEL rows of `source` for local dates start..end at a fixed tilt."""
        now = self.clock()
        u = url(source, site.lat, site.lon, start, end, tilt, site.azimuth_deg)
        path = self._path(source, start, end, u)
        cached = self._read(path)
        rows = None
        if cached is not None:
            try:
                rows = parse(cached["response"], source, tilt, site.azimuth_deg)
            except (WxError, KeyError, TypeError, ValueError):
                cached, rows = None, None          # a damaged cache file is fetched again
        if cached is not None and (self._final(source, end, now)
                                   or now - cached["fetched_wall"] < self.refresh_s):
            return rows, None
        n, next_try, last = self.backoff(source)
        if now < next_try:
            return self._fallback(rows, f"waiting after a failed call ({last})")
        try:
            self.calls += 1
            status, body = self.transport(u, self.timeout)
            try:
                response = json.loads(body.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                raise WxError(f"HTTP {status}: answer is not JSON") from None
            if status != 200 and not (isinstance(response, dict) and response.get("error")):
                raise WxError(f"HTTP {status}")
            try:
                fresh = parse(response, source, tilt, site.azimuth_deg)
            except WxError as e:
                raise WxError(f"HTTP {status}: {e}", e.limit) from None
        except (WxError, OSError, http.client.HTTPException, ValueError) as e:
            return self._failed(source, e, rows, now)
        self._write(path, {"url": u, "fetched_wall": now, "response": response})
        if n:
            self.log(f"weather reference: Open-Meteo {source} reachable again after {n} "
                     f"failed call(s)")
            del self._fails[source]
        self.last_ok_wall = now
        return fresh, None

    def _failed(self, source, e, rows, now):
        reason = getattr(e, "reason", None) or e
        text = f"{type(e).__name__}: {reason}" if not isinstance(e, WxError) else str(e)
        n, _, last = self.backoff(source)
        n += 1
        wait = min(self.retry_max, self.retry_min * 2 ** (n - 1))
        if getattr(e, "limit", False):
            wait = self.retry_max
        self._fails[source] = {"n": n, "next": now + wait, "error": text}
        if n == 1 or text != last:
            self.log(f"WARNING weather reference: Open-Meteo {source} not available ({text}); "
                     f"{'using cached model data' if rows is not None else 'no model data'}"
                     f" until it is. Logging and the SCADA are not affected; next try in "
                     f"{wait / 60:.0f} min.")
        return self._fallback(rows, text)

    @staticmethod
    def _fallback(rows, error):
        return (rows if rows is not None else pd.DataFrame(columns=ROW_COLUMNS)), error
