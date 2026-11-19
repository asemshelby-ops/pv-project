"""Weather reference: Open-Meteo MODEL data as a daily cross-check for our sensors.

PC side only (SCADA, analysis, twin). Off by default (config.WX_ENABLED); with the switch off
nothing here runs and every output is byte-identical to before. Never used by the firmware
or by any detector. Start at docs/weather-reference.md.

  openmeteo  documented endpoints and parameters, disk cache, back-off, offline handling
  feeds      which endpoint covers which days; one hourly table per site
  store      the model CSV + daily-check CSV and the historian tables wx_model / wx_check
  check      the daily PYR20 check on clear-sky hours (ratio, trend, drift, known events)
  join       model columns joined to logger rows by timestamp (wx_model_* only)
  service    the SCADA thread, the /api/wx answer and the heartbeat line

Everything Open-Meteo returns is MODEL data (weather models, reanalysis, satellite
retrievals), not a measurement: every column starts with "wx_model_", and every chart and
message says "model".
"""
import datetime
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import config

PREFIX = "wx_model_"
MODEL_LABEL = "MODEL data (Open-Meteo), not a measurement"
# Licence (https://open-meteo.com/en/licence, read 2026-11-19): API data are CC BY 4.0; a
# link must be shown next to any place the data are displayed.
ATTRIBUTION_TEXT = "Weather data by Open-Meteo.com"
ATTRIBUTION_URL = "https://open-meteo.com/"
LICENCE = "CC BY 4.0"
LICENCE_URL = "https://creativecommons.org/licenses/by/4.0/"
ATTRIBUTION = {"text": ATTRIBUTION_TEXT, "url": ATTRIBUTION_URL, "licence": LICENCE,
               "licence_url": LICENCE_URL,
               "changes": "hourly values renamed to wx_model_*, joined to our rows by time, "
                          "and compared with the PYR20"}


def enabled():
    """The one switch. Read at call time, so a test can turn it on and off."""
    return bool(getattr(config, "WX_ENABLED", False))


@dataclass(frozen=True)
class Site:
    lat: float
    lon: float
    azimuth_deg: float                  # compass, 180 = south
    tilt_schedule: tuple                # ((local date ISO, tilt deg), ...), sorted
    known_events: tuple = ()            # ((local date ISO, text), ...)

    def tilt_on(self, day):
        """Tilt in effect on a local date (ISO string or date)."""
        day = str(day)
        tilt = self.tilt_schedule[0][1]
        for start, t in self.tilt_schedule:
            if day >= start:
                tilt = t
        return float(tilt)

    def events(self):
        """{local date: text} of every known event: each tilt change and WX_KNOWN_EVENTS."""
        out = {}
        for (_, a), (d, b) in zip(self.tilt_schedule, self.tilt_schedule[1:]):
            out[d] = f"tilt change {a:g} -> {b:g} deg"
        for d, text in self.known_events:
            out[d] = f"{out[d]}; {text}" if d in out else str(text)
        return out


def site_from_config():
    """The site of config.WX_SITE_*, or None while the coordinates are not filled in."""
    lat, lon = getattr(config, "WX_SITE_LAT", None), getattr(config, "WX_SITE_LON", None)
    if lat is None or lon is None:
        return None
    sched = tuple(sorted((str(d), float(t)) for d, t in config.WX_TILT_SCHEDULE))
    return Site(float(lat), float(lon), float(config.WX_SITE_AZIMUTH_DEG), sched,
                tuple((str(d), str(t)) for d, t in config.WX_KNOWN_EVENTS))


def local_today(now):
    """Local (config.TZ, Makkah) date of a Unix second."""
    return datetime.datetime.fromtimestamp(now, ZoneInfo(config.TZ)).date()
