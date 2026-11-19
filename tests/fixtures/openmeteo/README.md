# Open-Meteo answers used by the tests (no live calls in tests)

| File | What it is |
|---|---|
| `satellite_recorded_2026-11-16_17.json` | **Recorded** answer of the satellite radiation API, 2026-11-19 from the dev container: `https://satellite-api.open-meteo.com/v1/archive?latitude=21.42&longitude=39.83&hourly=shortwave_radiation,global_tilted_irradiance,shortwave_radiation_clear_sky&tilt=35&azimuth=0&models=satellite_radiation_seamless&start_date=2026-11-16&end_date=2026-11-17&timeformat=unixtime` (UTC days; Makkah city coordinates, not the roof). MODEL data (satellite retrieval). |
| `error_429_recorded.json` | **Recorded** body of an HTTP 429 answer of the forecast and archive APIs on the same day ("Daily API request limit exceeded"): the dev container shares its IP with other users, so the free tier's daily limit was already used up. |
| `forecast_CONSTRUCTED_2026-11-16_17.json` | **Constructed, NOT recorded** (the daily limit above blocked the recording): the documented forecast JSON layout (`hourly`, `hourly_units`, `timeformat=unixtime`, `timezone=Asia/Riyadh`, `wind_speed_unit=ms`) with the radiation of the satellite recording x 0.97 and invented temperature, wind and cloud values. It tests the parser and the joins only; its numbers are not weather. Replace it with a recording when the quota allows: `curl -o forecast_recorded.json "https://api.open-meteo.com/v1/forecast?latitude=21.4200&longitude=39.8300&hourly=shortwave_radiation,global_tilted_irradiance,temperature_2m,wind_speed_10m,wind_direction_10m,cloud_cover&tilt=35&azimuth=0&start_date=2026-11-16&end_date=2026-11-17&timezone=Asia/Riyadh&timeformat=unixtime&wind_speed_unit=ms"` |

Data: Open-Meteo.com, CC BY 4.0 (https://open-meteo.com/en/licence).
