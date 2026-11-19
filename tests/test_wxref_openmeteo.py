"""wxref/openmeteo.py + feeds.py: documented parameters only, parsing of recorded answers,
cache and back-off. No live calls: every answer comes from tests/fixtures/openmeteo/."""
import json
import urllib.parse

import numpy as np
import pandas as pd
import pytest

import wxref
from tests.wx_fixtures import SITE, FakeTransport, fixture_bytes, load_json
from wxref import feeds, openmeteo
from wxref.openmeteo import Client, WxError

# Parameters named on the Open-Meteo doc pages read 2026-11-19 (docs/weather-reference.md)
DOCUMENTED = {"latitude", "longitude", "hourly", "tilt", "azimuth", "start_date", "end_date",
              "timezone", "timeformat", "wind_speed_unit", "models"}
DOCUMENTED_HOURLY = {"shortwave_radiation", "global_tilted_irradiance",
                     "shortwave_radiation_clear_sky", "temperature_2m", "wind_speed_10m",
                     "wind_direction_10m", "cloud_cover"}


def _query(u):
    return dict(urllib.parse.parse_qsl(urllib.parse.urlparse(u).query))


@pytest.mark.parametrize("source,host", [("forecast", "api.open-meteo.com"),
                                         ("archive", "archive-api.open-meteo.com"),
                                         ("satellite", "satellite-api.open-meteo.com")])
def test_urls_use_documented_endpoints_and_parameters_only(source, host):
    u = openmeteo.url(source, 21.42, 39.83, "2026-11-16", "2026-11-17", 35, 180)
    p = urllib.parse.urlparse(u)
    assert p.scheme == "https" and p.netloc == host
    q = _query(u)
    assert set(q) <= DOCUMENTED
    assert set(q["hourly"].split(",")) <= DOCUMENTED_HOURLY
    assert q["timeformat"] == "unixtime" and q["timezone"] == "Asia/Riyadh"
    assert (q["tilt"], q["azimuth"]) == ("35", "0")
    if source == "satellite":
        assert q["models"] == "satellite_radiation_seamless" and "wind_speed_unit" not in q
    else:
        assert q["wind_speed_unit"] == "ms" and "models" not in q


@pytest.mark.parametrize("compass,om", [(180, 0), (90, -90), (270, 90), (0, -180), (200, 20)])
def test_azimuth_compass_to_open_meteo(compass, om):
    assert openmeteo.openmeteo_azimuth(compass) == om        # docs: 0 S, -90 E, 90 W, +-180 N


def test_parse_recorded_satellite_answer():
    rows = openmeteo.parse(load_json("satellite_recorded_2026-11-16_17.json"), "satellite", 35, 180)
    assert len(rows) == 48 and list(rows.columns) == openmeteo.ROW_COLUMNS
    assert all(c.startswith("wx_model_") for c in openmeteo.MODEL_COLUMNS)
    assert rows["t_utc"].iloc[0] == int(pd.Timestamp("2026-11-16", tz="UTC").timestamp())
    assert np.all(np.diff(rows["t_utc"]) == 3600)
    noon = rows.set_index("t_utc").loc[int(pd.Timestamp("2026-11-16 10:00", tz="UTC").timestamp())]
    assert noon["wx_model_poa_wm2"] == 984.7 and noon["wx_model_ghi_wm2"] == 797.8
    assert rows["wx_model_wind_speed_10m_ms"].isna().all()      # not a satellite variable
    assert rows["wx_source"].eq("satellite").all() and rows["wx_grid_elevation_m"].iloc[0] == 310.0


def test_parse_forecast_layout_and_refuse_wrong_units():
    raw = load_json("forecast_CONSTRUCTED_2026-11-16_17.json")
    rows = openmeteo.parse(raw, "forecast", 35, 180)
    assert rows["wx_model_wind_speed_10m_ms"].notna().all()
    assert rows["wx_model_cloud_cover_pct"].max() == 45
    raw["hourly_units"]["wind_speed_10m"] = "km/h"               # wind_speed_unit not applied
    with pytest.raises(WxError, match="km/h"):
        openmeteo.parse(raw, "forecast", 35, 180)
    raw = load_json("forecast_CONSTRUCTED_2026-11-16_17.json")
    raw["hourly_units"]["time"] = "iso8601"
    with pytest.raises(WxError, match="unixtime"):
        openmeteo.parse(raw, "forecast", 35, 180)


def test_recorded_rate_limit_answer_is_an_error_with_limit_flag():
    with pytest.raises(WxError) as e:
        openmeteo.parse(load_json("error_429_recorded.json"), "forecast", 35, 180)
    assert e.value.limit and "limit" in str(e.value)


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def test_cache_answers_without_calling_until_refresh_and_final_days_never_again(tmp_path):
    now = pd.Timestamp("2026-11-17 12:00", tz="UTC").timestamp()
    clock, fake = Clock(now), FakeTransport()
    c = Client(cache_dir=tmp_path / "cache", clock=clock, transport=fake, log=lambda m: None,
               refresh_s=3600)
    rows, err = c.get("satellite", SITE, "2026-11-16", "2026-11-17", 35)
    assert err is None and len(rows) == 48 and len(fake.urls) == 1
    cached = list((tmp_path / "cache" / "satellite").glob("*.json"))
    assert len(cached) == 1 and json.loads(cached[0].read_text())["url"] == fake.urls[0]
    clock.t += 1800                                            # within the refresh time
    rows2, _ = c.get("satellite", SITE, "2026-11-16", "2026-11-17", 35)
    assert len(fake.urls) == 1 and rows2.equals(rows)
    clock.t += 3600                                            # stale, the days not final yet
    c.get("satellite", SITE, "2026-11-16", "2026-11-17", 35)
    assert len(fake.urls) == 2
    clock.t += 10 * 86400                                      # days long past: final
    c.get("satellite", SITE, "2026-11-16", "2026-11-17", 35)
    assert len(fake.urls) == 2


def test_failure_backs_off_warns_once_and_keeps_the_cache(tmp_path):
    now = pd.Timestamp("2026-11-17 12:00", tz="UTC").timestamp()
    clock, logs = Clock(now), []
    good = Client(cache_dir=tmp_path, clock=clock, transport=FakeTransport(),
                  log=logs.append, refresh_s=3600, retry_s=(300, 3600))
    good.get("forecast", SITE, "2026-11-16", "2026-11-17", 35)
    down = FakeTransport(fail=OSError("Network is unreachable"))
    c = Client(cache_dir=tmp_path, clock=clock, transport=down, log=logs.append,
               refresh_s=3600, retry_s=(300, 3600))
    clock.t += 7200                                            # cache stale -> a call is due
    rows, err = c.get("forecast", SITE, "2026-11-16", "2026-11-17", 35)
    assert "unreachable" in err and len(rows) == 48           # the cached rows come back
    assert len(down.urls) == 1 and sum("WARNING" in m for m in logs) == 1
    clock.t += 60                                              # inside the back-off: no call
    c.get("forecast", SITE, "2026-11-16", "2026-11-17", 35)
    assert len(down.urls) == 1
    clock.t += 300                                             # next try, fails again
    c.get("forecast", SITE, "2026-11-16", "2026-11-17", 35)
    assert len(down.urls) == 2 and c.backoff("forecast")[1] - clock.t == 600   # doubled
    assert sum("WARNING" in m for m in logs) == 1              # same error: not repeated
    c.transport = FakeTransport()
    clock.t += 600
    _, err = c.get("forecast", SITE, "2026-11-16", "2026-11-17", 35)
    assert err is None and c.backoff("forecast")[0] == 0 and "reachable again" in logs[-1]


def test_rate_limit_waits_the_full_hour(tmp_path):
    clock = Clock(pd.Timestamp("2026-11-17 12:00", tz="UTC").timestamp())
    fake = FakeTransport(status=429, body=fixture_bytes("error_429_recorded.json"))
    c = Client(cache_dir=tmp_path, clock=clock, transport=fake, log=lambda m: None,
               retry_s=(300, 3600))
    rows, err = c.get("forecast", SITE, "2026-11-16", "2026-11-17", 35)
    assert not len(rows) and "limit" in err and c.backoff("forecast")[1] - clock.t == 3600
    c.transport = FakeTransport()                    # another endpoint is not held back
    rows, err = c.get("satellite", SITE, "2026-11-16", "2026-11-17", 35)
    assert err is None and len(rows) == 48
    assert c.status()["failing"] == ["forecast"] and c.status()["offline"]


def test_segments_split_by_age_source_and_tilt():
    seg = feeds.segments("2026-06-01", "2026-12-31", "2026-11-19")
    assert seg[0] == ("archive", pd.Timestamp("2026-06-01").date(), pd.Timestamp("2026-08-20").date())
    assert seg[1][0] == "forecast" and seg[1][2] == pd.Timestamp("2026-12-04").date()   # +15 days
    assert seg[2] == ("satellite", pd.Timestamp("2026-06-01").date(), pd.Timestamp("2026-11-19").date())
    per = feeds.tilt_periods(SITE, "2027-02-27", "2027-03-02")
    assert [(str(a), str(b), t) for a, b, t in per] == [("2027-02-27", "2027-02-28", 35.0),
                                                        ("2027-03-01", "2027-03-02", 20.0)]


def test_fetch_requests_each_tilt_period_with_its_own_tilt(tmp_path):
    fake = FakeTransport()
    c = Client(cache_dir=tmp_path, clock=Clock(pd.Timestamp("2027-03-02", tz="UTC").timestamp()),
               transport=fake, log=lambda m: None)
    feeds.fetch(c, SITE, "2027-02-28", "2027-03-01", "2027-03-02")
    tilts = sorted((urllib.parse.urlparse(u).netloc.split(".")[0], _query(u)["tilt"],
                    _query(u)["start_date"]) for u in fake.urls)
    assert tilts == [("api", "20", "2027-03-01"), ("api", "35", "2027-02-28"),
                     ("satellite-api", "20", "2027-03-01"), ("satellite-api", "35", "2027-02-28")]


def test_hourly_prefers_satellite_irradiance_and_forecast_weather():
    sat = openmeteo.parse(load_json("satellite_recorded_2026-11-16_17.json"), "satellite", 35, 180)
    fc = openmeteo.parse(load_json("forecast_CONSTRUCTED_2026-11-16_17.json"), "forecast", 35, 180)
    h = feeds.hourly(pd.concat([fc, sat]))
    t = int(pd.Timestamp("2026-11-16 10:00", tz="UTC").timestamp())
    r = h.set_index("t_utc").loc[t]
    assert r["wx_model_poa_wm2"] == 984.7 and r["wx_model_irr_source"] == "satellite"
    assert r["wx_model_met_source"] == "forecast" and np.isfinite(r["wx_model_wind_speed_10m_ms"])
    first = h.iloc[0]                       # 2026-11-15 21:00 UTC: only the forecast has it
    assert first["wx_model_irr_source"] == "forecast"


def test_site_is_none_until_coordinates_are_filled_in(monkeypatch):
    import config
    assert config.WX_ENABLED is False and wxref.enabled() is False      # off by default
    assert wxref.site_from_config() is None                             # placeholders
    monkeypatch.setattr(config, "WX_SITE_LAT", 21.4)
    monkeypatch.setattr(config, "WX_SITE_LON", 39.8)
    s = wxref.site_from_config()
    assert s.tilt_on("2026-11-19") == 35.0 and s.tilt_on("2027-03-01") == 20.0
    assert s.events() == {"2027-03-01": "tilt change 35 -> 20 deg"}
