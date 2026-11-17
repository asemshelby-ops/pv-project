"""CSV schema shared by synthetic and real data (CLAUDE.md, Data format + wind)."""
import pandas as pd

COLUMNS = [
    "date", "time", "T1", "T2", "T3", "T4", "T5", "T_ambient", "humidity",
    "irradiance", "voltage", "current", "power", "wind",
]
BACK_SENSORS = ["T1", "T2", "T3", "T4", "T5"]

# Appended to every logger row since 2026-11-12 (docs/paper/07 sec. 7). Older files do
# not have them; analysis/ingest.py then fills them with "not recorded" (NaN / <NA>).
LOGGER_EXTRA_COLUMNS = ["D", "load_mode", "sweep_id", "uptime_ms", "status"]

# Bits of the `status` column: name -> bit number. Must equal the ST_* definitions in
# firmware/libraries/PvDaq/src/pv_status.h (tests/test_status.py compares the two).
STATUS_BITS = {
    "RTC_INVALID": 0,     # no valid RTC time: date and time empty, use uptime_ms
    "TEMP_FAIL": 1,       # at least one of T1..T5 gave no valid reading
    "AMBIENT_FAIL": 2,    # BME280: no ambient temperature
    "IRRAD_FAIL": 3,      # PYR20: no irradiance
    "PANEL_FAIL": 4,      # panel INA226: no V, I, P
    "TROM_UNMAPPED": 5,   # T_ROM not filled in: T1..T5 follow the bus-scan order
    "DET_SKIPPED": 6,     # row not given to the on-board detector
    "DET_RESET": 7,       # on-board detector state cleared at this row
    "SD_RETRY": 8,        # second SD write attempt, or the row(s) before were lost
}

# Bits of the `alarm` column: one bit per on-board detector, name -> bit number. A row
# reads 0 (no alarm) or the sum of the detectors that alarmed at it: 1 = NASIM-GBT only,
# as in every file written while NASIM was the only detector on the board; 6 = threshold
# and CUSUM together. The firmware takes the numbers from here (nasim_config.h).
ALARM_BITS = {
    "NASIM": 0,           # NASIM-GBT
    "THRESHOLD": 1,       # temperature-rise threshold (detect/threshold.py, no gate)
    "CUSUM": 2,           # CUSUM on the temperature increment (detect/cusum_inc.py)
}


# --- Phase B preparation (2026-11-17): nothing below is written while the flags are off ----
# Six columns the logger appends AFTER `status` when it is built with PHASE_B_ENABLED 1
# (firmware/libraries/PvDaq/src/pv_spray_logic.h, spray_row_fields). A file without them
# loads as before; analysis/ingest.py adds none of them to a table that has none.
#   spike_id      id of the open spike event, 0 = none. Counted over the whole trial (the
#                 counter is kept on the board across restarts), so an id is never reused
#   spray_assign  -1 = no event open; 0 = no-spray arm; 1 = spray arm
#   pump_duty     PWM duty of the pump, 0-1, MEAN over the 10 s that end at this row
#   flow_Lpm      water flow, L/min, MEAN over the same 10 s (from the pulse count)
#   water_L_cum   litres since the board started (falls back to 0 at a restart)
#   P_pump_W      electrical power of the pump rail, W, MEAN over the same 10 s
PHASE_B_COLUMNS = ["spike_id", "spray_assign", "pump_duty", "flow_Lpm", "water_L_cum",
                   "P_pump_W"]

# More bits of `status`, used only by a Phase B logger: name -> bit number. Kept apart from
# STATUS_BITS (and from pv_status.h) until Phase B is adopted, so that nothing that reads
# STATUS_BITS changes. Must equal the SPRAY_ST_* definitions in pv_spray_logic.h
# (tests/test_spray_logic.py compares the two) and must start after the last STATUS_BITS bit.
PHASE_B_STATUS_BITS = {
    "PUMP_FAIL": 9,       # pump INA226: no pump power for this row (P_pump_W empty)
    "SPRAY_BLOCKED": 10,  # an alarm at this row would NOT open an event (reason: /spray_log.csv)
    "SPRAY_ABORT": 11,    # a spray was stopped by a fault since the row before
}

# `code` column of /markers.csv, written only by a logger built with MARKER_CODES_ENABLED 1:
# what a marker row IS, so the three controlled tests no longer depend on the typed log
# sheet alone. code -> event name, the same names the log sheet uses (docs/paper/05).
# 0 = a button press (meaning on the sheet, as before). Proposed numbering: tens = the kind
# of test, units = 0 start / 1 end. The firmware gets the numbers from here (generated
# phase_b_config.h).
MARKER_CODES = {
    0: "button",
    10: "mesh on", 11: "mesh off",                # shade cloth over module and PYR20
    20: "windbreak on", 21: "windbreak off",
    30: "load off", 31: "load on",                # controlled open-circuit test (LOAD OFF / ON)
}


def has_alarm(series, name):
    """Boolean Series: did detector `name` alarm at the row? Missing value gives False."""
    return (series.fillna(0).astype("int64") & (1 << ALARM_BITS[name])) != 0


def decode_status(value):
    """Names of the bits set in one `status` value, lowest bit first. A missing value
    (old file) gives []. A set bit this table does not know appears as 'BIT<n>'."""
    if value is None or pd.isna(value):
        return []
    value = int(value)
    names = {bit: name for name, bit in {**STATUS_BITS, **PHASE_B_STATUS_BITS}.items()}
    return [names.get(b, f"BIT{b}") for b in range(value.bit_length()) if value >> b & 1]


def has_status(series, name):
    """Boolean Series: is bit `name` set? Rows with no status (old files) give False."""
    bit = STATUS_BITS[name] if name in STATUS_BITS else PHASE_B_STATUS_BITS[name]
    return (series.fillna(0).astype("int64") & (1 << bit)) != 0

EVENT_COLUMNS = ["start", "end", "class", "effect_C", "note"]
EVENT_CLASSES = ["spike", "minor_lull", "irradiance_rise", "mixed",
                 "natural_spike", "natural_minor", "ambient_rise"]
