"""The weather reference inside the SCADA: one thread, the /api/wx answer, the heartbeat line.

scada.run starts WeatherService only when config.WX_ENABLED is True and the data are LIVE
(a replay's timestamps are not today's weather). The thread
  * fetches the recent past and a short forecast (wxref.feeds) at most once an hour per
    request (wxref.openmeteo caches on disk and backs off after a failure);
  * writes the MODEL rows into the historian table wx_model and into <WX_DIR>/wx_model.csv;
  * runs the daily PYR20 check for the complete local days of the fetch window and keeps
    the result in the table wx_check and <WX_DIR>/wx_daily_check.csv.
It never touches `samples`, the alarms or the logger's folder, and it never raises: a
network or database problem is logged and the loop goes on, so the ingest and the HMI do
not notice it.
"""
import datetime
import time
from contextlib import closing

import numpy as np
import pandas as pd

import config
from scada import historian
from wxref import (ATTRIBUTION, MODEL_LABEL, PREFIX, check, feeds, local_today, site_from_config,
                   store)
from wxref.openmeteo import Client

TICK_S = 60.0
DAILY_INPUT = ["date", "clear_hours", "daylight_hours", "pyr20_model_ratio", "ratio_iqr",
               "meas_clear_mean_wm2", PREFIX + "clear_mean_wm2", "wx_source", "tilt_deg"]
REPLAY_STATE = {"enabled": True, "available": False, "label": MODEL_LABEL,
                "attribution": ATTRIBUTION,
                "hint": "The weather reference runs for LIVE data only: a replay's timestamps "
                        "are not today's weather."}


def _day_bounds(day):
    """Unix seconds of local 00:00 of `day` and of the next day."""
    start = pd.Timestamp(str(day)).tz_localize(config.TZ)
    return int(start.timestamp()), int((start + pd.Timedelta(days=1)).timestamp())


def measured_rows(conn, t0, t1):
    rows = conn.execute("SELECT t_utc, irradiance FROM samples WHERE t_utc > ? AND t_utc <= ? "
                        "AND irradiance IS NOT NULL ORDER BY t_utc", (t0, t1)).fetchall()
    return pd.DataFrame([tuple(r) for r in rows], columns=["t_utc", "irradiance"])


def update_checks(conn, site, days):
    """Recompute the daily check of `days` (local dates) from the historian and re-assess
    every stored day. Returns the whole check table."""
    old = store.read_checks(conn)
    keep = old[~old["date"].isin([str(d) for d in days])]
    keep = keep[DAILY_INPUT].assign(has_data=keep["status"].ne("NO_DATA").to_numpy())
    new = []
    for d in days:
        t0, t1 = _day_bounds(d)
        meas = measured_rows(conn, t0, t1)
        if not len(meas):
            continue                            # nothing logged that day: no check row
        model = store.read_model(conn, t0, t1 + 3600)
        h = check.hours(meas, model, site)
        new.append(check.daily(h, [str(d)]))
    parts = [p for p in [keep] + new if len(p)]
    if not parts:
        return pd.DataFrame(columns=check.CHECK_COLUMNS)
    daily = pd.concat(parts, ignore_index=True).sort_values("date")
    checks = check.assess(daily, site)
    store.replace_checks(conn, checks)
    return checks


class WeatherService:
    def __init__(self, db_path, site=None, client=None, clock=time.time, log=None,
                 folder=None):
        self.db_path, self.clock = str(db_path), clock
        self.log = log or (lambda m: print(m, flush=True))
        self.folder = folder or config.WX_DIR
        self.site = site if site is not None else site_from_config()
        self.client = client or Client(cache_dir=f"{self.folder}/cache", clock=clock,
                                       log=self.log)
        self._seen = None
        self._status = {"state": "starting"}
        with closing(historian.connect(self.db_path)) as conn:
            store.ensure_tables(conn)           # only here: never when the switch is off
            conn.commit()
        if self.site is None:
            self.log("WARNING weather reference: config.WX_SITE_LAT / WX_SITE_LON are not set; "
                     "nothing is fetched until they are")

    def status(self):
        """For /api/wx (another thread): a copy made at the end of each tick."""
        return dict(self._status)

    def tick(self, now=None):
        now = self.clock() if now is None else now
        if self.site is None:
            self._status = {"state": "site_not_set"}
            return
        today = local_today(now)
        start = today - datetime.timedelta(days=config.WX_PAST_DAYS)
        end = today + datetime.timedelta(days=config.WX_FORECAST_DAYS - 1)
        rows, errors = feeds.fetch(self.client, self.site, start, end, today)
        st = self.client.status()
        checks = None
        with closing(historian.connect(self.db_path)) as conn:
            newest = conn.execute("SELECT MAX(t_utc) FROM samples").fetchone()[0]
            seen = (today, st["last_ok_wall"], len(rows), newest)
            if seen != self._seen:      # new model data, new rows or a new day
                if len(rows):
                    store.upsert_model(conn, rows, now)
                days = [start + datetime.timedelta(days=k)
                        for k in range((today - start).days)]
                checks = update_checks(conn, self.site, days)
            conn.commit()
        if len(rows) and seen != self._seen:
            store.merge_model_csv(rows, self.folder)
        if checks is not None:
            store.write_check_csv(checks, self.folder)
        self._seen = seen
        self._status = {"state": "offline" if st["offline"] else "ok", "errors": errors[-3:],
                        "rows": int(len(rows)), "window": [start.isoformat(), end.isoformat()],
                        **st}

    def run(self, stop, tick_s=TICK_S):
        """Thread body: never raises, never blocks anything but itself."""
        while not stop.is_set():
            try:
                self.tick()
            except Exception as e:              # noqa: BLE001 - the loop must survive anything
                self._status = {**self._status, "state": "error", "last_error": str(e)}
                self.log(f"WARNING weather reference: {type(e).__name__}: {e}; trying again "
                         f"(logging and the SCADA are not affected)")
            stop.wait(tick_s)


# --- what the HMI and the heartbeat show --------------------------------------------------------

def _num(v, digits):
    return None if v is None or not np.isfinite(v) else round(float(v), digits)


def api_payload(conn, status, now):
    """/api/wx: today's model and measured hourly values and the latest daily checks."""
    site = site_from_config()
    today = local_today(now)
    t0, t1 = _day_bounds(today)
    model = feeds.hourly(store.read_model(conn, t0 + 3600, t1))
    meas = {r[0]: (r[1], r[2], r[3]) for r in conn.execute(
        "SELECT ((t_utc + 3599) / 3600) * 3600 AS h, AVG(irradiance), AVG(T_ambient), "
        "COUNT(irradiance) FROM samples WHERE t_utc > ? AND t_utc <= ? GROUP BY h",
        (t0, t1)).fetchall()}
    hours = [t0 + 3600 * k for k in range(1, 25)]       # hour-ending stamps of the local day
    model = model.set_index("t_utc").reindex(hours)
    col = lambda c, d: [_num(v, d) for v in model[c]]   # noqa: E731
    today_block = {
        "date": today.isoformat(), "t": hours,
        "note": "model radiation = mean of the hour ENDING at t; measured = mean of the PYR20 "
                "rows in the same hour; model temperature and wind = instant at t",
        PREFIX + "poa_wm2": col(PREFIX + "poa_wm2", 1),
        PREFIX + "ghi_wm2": col(PREFIX + "ghi_wm2", 1),
        PREFIX + "temp_2m_c": col(PREFIX + "temp_2m_c", 1),
        PREFIX + "wind_speed_10m_ms": col(PREFIX + "wind_speed_10m_ms", 2),
        PREFIX + "wind_dir_10m_deg": col(PREFIX + "wind_dir_10m_deg", 0),
        PREFIX + "cloud_cover_pct": col(PREFIX + "cloud_cover_pct", 0),
        PREFIX + "irr_source": [v if isinstance(v, str) else None
                                for v in model[PREFIX + "irr_source"]],
        "measured_poa_wm2": [_num(meas[t][0], 1) if t in meas else None for t in hours],
        "measured_T_ambient_c": [_num(meas[t][1], 2) if t in meas and meas[t][1] is not None
                                 else None for t in hours]}
    checks = store.read_checks(conn)
    recs = [{k: (None if isinstance(v, float) and not np.isfinite(v) else v)
             for k, v in r.items()} for r in checks.tail(14).to_dict("records")]
    return {"enabled": True, "available": True, "label": MODEL_LABEL,
            "attribution": ATTRIBUTION, "service": status,
            "site": None if site is None else {
                "lat": site.lat, "lon": site.lon, "azimuth_deg": site.azimuth_deg,
                "tilt_deg": site.tilt_on(today), "placeholder_note":
                    "coordinates and tilt dates are config placeholders until the roof is "
                    "surveyed"},
            "today": today_block, "latest_check": recs[-1] if recs else None, "checks": recs,
            "band_pct": 100 * config.WX_RATIO_BAND}


def heartbeat_line(conn, day):
    """One line for the 08:00 heartbeat: the PYR20/model check of local date `day`."""
    head = f"PYR20/model check {day} (Open-Meteo MODEL reference): "
    checks = store.read_checks(conn)
    r = checks[checks["date"] == str(day)]
    if not len(r):
        return head + "no result (no data that day, or the model data did not arrive)"
    r = r.iloc[0]
    ratio = r["pyr20_model_ratio"]
    if r["status"] in ("OK", "DRIFT", "BASELINE", "OUT_OF_RANGE"):
        txt = f"ratio {ratio:.3f}, {r['status']}"
        if np.isfinite(r["reference_ratio"]):
            txt += (f" (reference {r['reference_ratio']:.3f}, rolling {r['ratio_rolling']:.3f}, "
                    f"{r['drift_pct']:+.1f} %, band +/-{100 * config.WX_RATIO_BAND:g} %)")
        txt += f", {int(r['clear_hours'])} clear h"
        if r["status"] in check.FLAGS:
            txt += " - inspect the PYR20 (dome, level, tilt)"
        return head + txt
    return head + f"{r['status']}: {r['message']}"
