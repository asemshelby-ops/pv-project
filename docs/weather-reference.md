# Weather reference (Open-Meteo MODEL data)

A free, external, **model** reference for our sensors, on the PC side only (SCADA, analysis,
twin). It is a daily cross-check, not a measurement and not a replacement for any sensor.
Code: `wxref/`. Off by default.

> Everything from Open-Meteo is MODEL data -- weather models, reanalysis and satellite
> retrievals for a grid cell kilometres wide -- never a measurement at our rig. Every column
> starts with `wx_model_`; every chart, panel and message says "model".

## What it does

1. **Fetches** hourly model values for the site, for the same local days as our data: the
   recent past (`WX_PAST_DAYS`, default 2 days) and a short forecast (`WX_FORECAST_DAYS`,
   today + tomorrow):
   - global horizontal irradiance (`wx_model_ghi_wm2`) and the in-plane irradiance for our
     tilt and azimuth (`wx_model_poa_wm2`, Open-Meteo's `global_tilted_irradiance`)
   - air temperature at 2 m, wind speed and direction at 10 m, cloud cover
   - from three documented endpoints: forecast (weather models; its "past" is the models'
     own analysis), archive (reanalysis, for days older than ~90 days), satellite radiation
     (`models=satellite_radiation_seamless`; used for the PYR20 check, `WX_CHECK_SOURCE`).
2. **Daily PYR20 check** (`wxref/check.py`): measured in-plane irradiance against the
   model's, on **clear-sky hours only**; cloudy or dusty hours and days are skipped, not
   flagged. Reports the daily ratio, a rolling ratio, its trend (% per week) and a status:
   `OK`, `DRIFT` (likely a dirty dome, a tilt/level change or a sensor fault),
   `OUT_OF_RANGE`, `BASELINE` (reference being built), `KNOWN_EVENT`, `NO_CLEAR_HOURS`,
   `NO_DATA`. The tilt change 35 -> 20 deg (`WX_TILT_SCHEDULE`) is a **known event**: that
   day is reported as such and the reference starts again; it is never flagged as a fault.
3. **Model wind** as an extra input for analysis and the twin: `wxref.join` adds
   `wx_model_wind_speed_10m_ms` (and the other model columns) to logger rows by timestamp.
   The logger's own `wind` column is never written, so no detector -- on the board or
   offline -- sees model wind (tested). `python -m twin.validate --data <folder>
   --wx-model-wind` drives the twin with it; its report says so.
4. **SCADA**: a "Weather reference (model)" panel next to the sensor health: today's model
   vs PYR20 irradiance (hourly means), model wind, the latest daily check, the attribution
   link. One line is added to the 08:00 Telegram heartbeat: yesterday's PYR20/model ratio and
   status. Live data only (a replay's timestamps are not today's weather).
5. **Analysis**: with the switch on, `analysis.powerbi` adds `wx_model_*` / `pyr20_*`
   columns to `daily_summary.csv` and a fifth table `wx_model_hourly.csv`;
   `analysis.pipeline` writes `wx_model_hourly.csv`, `wx_daily_check.csv` and a summary
   section. `--weather` on either command turns it on for one run.

## The daily PYR20 check, step by step

Per hour (the hour **ending** at the model's stamp, because Open-Meteo gives radiation as
the mean of the preceding hour):

| Step | Rule (config.py) |
|---|---|
| clear-sky in-plane irradiance `cs` | pvlib Ineichen with Linke climatology, transposed to the tilt of that day, isotropic sky, albedo 0.2 (the model's own assumption). The clear-sky index is always taken in the plane of the module, never against horizontal GHI. |
| sun high enough | `cs >= WX_CS_MIN_POA_WM2` (300 W/m2) |
| model sees no cloud | model in-plane / `cs` >= `WX_CS_MIN_K_MODEL` (0.8), and model cloud cover at both ends of the hour <= `WX_CS_MAX_CLOUD_PCT` (20 %) when a weather source gives it |
| steady sky at the rig | std/mean of PYR20 / `cs` per minute <= `WX_CS_MAX_CV` (3 %), and >= 80 % of the hour's 10 s rows present |
| hourly ratio | mean PYR20 in the hour / model in-plane |

The measured **level** is not a selection criterion and the steadiness test is
scale-free, so a dome that reads 10 % low does not hide its own hours (tested).

Per day: median of the clear hours' ratios (at least `WX_CHECK_MIN_HOURS`, 3).
Over days: the reference is `WX_RATIO_REFERENCE` if set, else the median of the first
`WX_BASELINE_DAYS` (7) valid days after the start or the last known event. The rolling
ratio (median of the last `WX_DRIFT_DAYS`, 3) outside reference x (1 +/- `WX_RATIO_BAND`,
5 %) is a `DRIFT`; a daily ratio outside `WX_RATIO_SANE` (0.7-1.3) is flagged at once.
One odd day is no flag: a 10 % drop is flagged on its second day (tested with the
irradiance scaled by 0.9).

**The ratio is not expected to be 1.** The model has its own errors (aerosol and dust,
the isotropic transposition) and the PYR20 is a silicon sensor (400-1100 nm) compared with
a broadband model value. The check watches the **change** of the ratio. All limits are
`[ASSUMPTION]` until tuned on real days.

## Licence and attribution

- Open-Meteo API data are offered under **CC BY 4.0** (https://open-meteo.com/en/licence,
  read 2026-11-19). Attribution is required, with a link to the licence, saying if changes
  were made, and "you must include a link next to any location Open-Meteo data are
  displayed", e.g. `<a href="https://open-meteo.com/">Weather data by Open-Meteo.com</a>`.
  The SCADA panel shows exactly that link plus the CC BY 4.0 link and states the change
  (values renamed `wx_model_*`, joined by time, compared with the PYR20). The analysis
  summary names the source and licence. In the thesis or a paper, cite:
  Zippenfenig, P. (2023). *Open-Meteo.com Weather API* [Computer software]. Zenodo.
  https://doi.org/10.5281/ZENODO.7970649 -- and the underlying data providers listed on the
  licence page (e.g. ECMWF, DWD, NOAA, Copernicus C3S for ERA5) where relevant.
- Free API terms (https://open-meteo.com/en/terms): **non-commercial use only** (public
  research at a public institution and educational use qualify), fewer than 10 000 calls a
  day, 5 000 an hour, 600 a minute. The data come without any warranty.
- Our use: about one call per endpoint per hour while the SCADA runs (`WX_REFRESH_S`), none
  for complete past days already cached; after a failure no call to that endpoint for
  5 min, doubling up to 1 h (`WX_RETRY_S`); a rate-limit answer waits the full hour. Each
  endpoint backs off on its own (on 2026-11-19 the forecast API refused the dev container
  with its daily limit while the satellite API answered).

## Model versus measured: caveats

- **Grid cell, not the rig.** Values are for a model grid cell (about 2-25 km depending on
  the source); the answer reports the cell's coordinates and elevation (`wx_grid_*`).
- **Wind at 10 m** over the grid cell's surface, not at the module (about 1 m, between
  buildings on a roof). It is an exploratory input, not a substitute for the anemometer, and
  not the wind the twin's u1 placeholder assumes (pvlib: Faiman's wind speed is measured at
  the height its factor was determined for).
- **Time base.** Radiation = mean of the hour ending at the stamp; temperature, wind and
  cloud = instant at the stamp. Our 10 s rows are averaged over the same hour; the join
  interpolates instant values linearly and assigns hour means to the hour a row falls in.
- **Hourly only.** The forecast's 15-minute data are interpolated from hourly values
  outside North America and Central Europe (forecast docs), so they add no information
  for Makkah. The satellite source has native 10-15 min data (`temporal_resolution=native`
  in its docs); not used yet.
- **Transposition.** The model's in-plane value assumes an isotropic sky and 20 % albedo;
  a roof's albedo and the horizon differ.
- **Dust.** Makkah's aerosol load changes fast; on dusty days the model's clear-sky index
  falls and the hours are skipped. If many days are skipped, the check has little to say;
  that is reported as `NO_CLEAR_HOURS`, not as a fault.
- **Never** quote a model value as a measurement, and never let it into a detector.

## Configuration (`config.py`, section "Weather reference")

| Name | Default | Meaning |
|---|---|---|
| `WX_ENABLED` | `False` | the one switch; off = nothing runs, every output byte-identical |
| `WX_SITE_LAT`, `WX_SITE_LON` | `None` **[PLACEHOLDER]** | roof coordinates; nothing is fetched until both are set |
| `WX_SITE_AZIMUTH_DEG` | `180.0` | compass azimuth (180 = south); converted to Open-Meteo's 0 = south |
| `WX_TILT_SCHEDULE` | 35 deg, then 20 deg from `2027-03-01` **[PLACEHOLDER date]** | tilt in effect from each local date |
| `WX_KNOWN_EVENTS` | `()` | more known events (re-levelling, dome replaced ...) |
| `WX_DIR` | `out/wxref` | cache, `wx_model.csv`, `wx_daily_check.csv` |
| `WX_PAST_DAYS`, `WX_FORECAST_DAYS` | 2, 2 | fetch window around today |
| `WX_REFRESH_S`, `WX_RETRY_S`, `WX_TIMEOUT_S` | 3600, (300, 3600), 10 | call frequency, back-off, timeout |
| `WX_CHECK_SOURCE` | `satellite` | irradiance for the PYR20 check |
| `WX_CS_*`, `WX_CHECK_MIN_HOURS`, `WX_BASELINE_DAYS`, `WX_RATIO_*`, `WX_DRIFT_DAYS`, `WX_TREND_DAYS` | see above | the check's limits, all [ASSUMPTION] |

Turn it on: set `WX_SITE_LAT` / `WX_SITE_LON` (and the real re-tilt date), then
`WX_ENABLED = True`, or use `--weather` for one analysis run.

## Where things go

| What | Where |
|---|---|
| raw answers (cache) | `out/wxref/cache/<source>/<start>_<end>_<hash>.json` |
| all model rows | `out/wxref/wx_model.csv` and historian table `wx_model` |
| daily checks | `out/wxref/wx_daily_check.csv` and historian table `wx_check` |
| Power BI | `daily_summary.csv` columns `wx_model_*`, `pyr20_*`; `wx_model_hourly.csv` |
| pipeline | `wx_model_hourly.csv`, `wx_daily_check.csv`, a section of `summary.md` |

Nothing is ever written into the logger's files or folder, or into the historian's
`samples` table; the two historian tables are created only when the service runs.

## Offline

No internet: one warning in the console, the panel says "not reachable", cached model data
are used where they exist, and the ingest, historian, alarms, Telegram and analysis carry
on unchanged (`tests/test_wxref_offline.py`). The thread catches every error.

## Tests

| File | What |
|---|---|
| `tests/test_wxref_openmeteo.py` | documented parameters only, parsing of a recorded answer, units, cache, back-off, rate limit |
| `tests/test_wxref_check.py` | irradiance x 0.9 is flagged; tilt change is a known event; cloudy and unsteady hours skipped; gross error flagged |
| `tests/test_wxref_join.py` | join by time, wind direction across north, Power BI columns, model wind to the twin, never into `wind` |
| `tests/test_wxref_scada.py` | own tables and files only, panel only when on, heartbeat line, `scada.run` starts it only for live data |
| `tests/test_wxref_offline.py` | no network: warning, ingest goes on, every table written |
| `tests/test_wxref_golden.py` | switch off: 42 outputs byte-identical to main before this feature; switch on: only the weather outputs change. The hashes were recorded on main (commit c1cd44c) on Linux, Python 3.13 with `requirements-lock.txt`; on another platform the test is skipped with a note (numeric output can differ in the last digit between builds) -- `tests/golden_wx_off.py` says how to record for it |

Fixtures (`tests/fixtures/openmeteo/`): a **recorded** satellite answer and a **recorded**
rate-limit answer; the forecast answer is **constructed** in the documented layout (the free
tier's daily limit was used up on the dev container's shared IP) -- see the README there.

## Still needed from the team

- The real roof coordinates (`WX_SITE_LAT`, `WX_SITE_LON`) and the real re-tilt date.
- About two weeks of real logged days to set the reference ratio and to tune the clear-sky
  limits and the band (all [ASSUMPTION] now).
- A recorded forecast answer for the fixture (command in the fixtures README).
