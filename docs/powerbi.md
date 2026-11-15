# Power BI tables from the logger files

One command turns the SD-card files into four CSV tables for Power BI.

```bash
python -m analysis.powerbi --data <folder with the SD-card files>
```

Output goes to `out/powerbi/` (change it with `--out`). `<folder>` has the same layout as
for the paper pipeline: `YYYYMMDD.csv` logger files, and if they exist `markers.csv` +
`logsheets/` and the sweep files `SWYYYYMMDD.csv` / `SSYYYYMMDD.csv`.

No rig data yet? `python -m analysis.powerbi --demo` writes 20 days of SYNTHETIC files and
exports them, so the report can be built now and pointed at real files later. Every row
then has `source = synthetic`; never quote those numbers.

## The tables

All times are local Makkah time (UTC+3), `YYYY-MM-DD HH:MM:SS`; decimal point `.`.

| File | One row per | Main columns |
|---|---|---|
| `daily_summary.csv` | local day | `energy_Wh` (sweep and trip rows left out), `insolation_kWh_m2`, `peak_power_W`, `max_T_module_C` (mean of T1..T5), `max_T_sensor_C`, `mean/max_irradiance_Wm2`, `completeness`, `performance_ratio` (vs the 100 W listing), `spikes`, `spikes_<cause>`, `minor_rises`, `alarms_NASIM/THRESHOLD/CUSUM/total`, `sweeps` |
| `spikes.csv` | labelled rise (`class` = spike or minor) | `start` (onset), `peak`, `size_C`, `duration_min`, `max_rate_C_per_min`, `dG_Wm2`, `f_G`, `cause` (irradiance / non_irradiance / mixed), `origin` (natural or controlled test), `valid`, `first_alarm_<detector>`, `delay_min_<detector>` |
| `sweeps.csv` | I-V sweep | `P_mp_W`, `V_mp_V`, `V_oc_V`, `I_sc_A`, `FF`, `G_Wm2`, `G_stable`, `T_module_C`, `T_cell_C`, `flags`, `usable` |
| `alarms.csv` | alarm onset of one on-board detector | `time_local`, `detector` |

Where the numbers come from:
- Spikes are the paper's offline reference labels (`analysis/events.py`, whole-day
  hindsight), not what the board saw. Count spikes with `class = spike` and `valid = TRUE`
  (that is what `daily_summary.spikes` counts).
- Alarms are what the ESP32 logged in its `alarm` column (bit 0 NASIM, 1 threshold,
  2 CUSUM); nothing is re-run. An alarm is attached to a spike if it falls between 2 min
  before the onset and 10 min after the peak; `delay_min` is alarm time minus onset.
  Files written before the alarm column existed give empty alarm columns.
- Sweep parameters are those of `analysis/gamma.py`. Use `usable = TRUE` for any
  temperature trend (no firmware flag, steady irradiance, enough sun).

## Loading the folder in Power BI Desktop

1. **Get data -> Folder**, choose `out/powerbi`, then **Transform data** (not Combine:
   the four files have different columns).
2. In Power Query, for each file row click **Binary** on `daily_summary.csv`,
   `spikes.csv`, `sweeps.csv`, `alarms.csv`; each becomes its own query. Rename the
   queries to the file names.
   (Simpler alternative: **Get data -> Text/CSV** four times, one per file.)
3. In each query: **Use first row as headers**; then set types:
   - `date` -> Date; `start`, `peak`, `time_local`, `first_alarm_*` -> Date/Time
   - numbers -> Decimal number; `valid`, `usable`, `G_stable` -> True/False
   - If your Windows uses a comma decimal separator, set the type with
     **Change type -> Using locale -> English (United States)**, or numbers are misread.
4. **Close & Apply**. In **Model view** connect `daily_summary[date]` (one) to
   `spikes[date]`, `sweeps[date]` and `alarms[date]` (many), single direction. For month or
   week slicers add a date table (`CALENDAR(MIN(daily_summary[date]), MAX(daily_summary[date]))`)
   and link it to `daily_summary[date]`.
5. After new SD-card data: rerun the command, then **Home -> Refresh**. Keep the folder
   path the same so the queries still find the files.

Suggested visuals: energy and max module temperature per day (line + column), spikes per
day stacked by `cause`, a scatter of `P_mp_W` / `G_Wm2` against `T_cell_C` from usable
sweeps (the gamma trend), and a table of spikes with their alarm delays per detector.
