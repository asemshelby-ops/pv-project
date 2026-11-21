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
    "BUFFERED": 9,        # row waited in the ESP32's flash, copied to the card later
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


def has_alarm(series, name):
    """Boolean Series: did detector `name` alarm at the row? Missing value gives False."""
    return (series.fillna(0).astype("int64") & (1 << ALARM_BITS[name])) != 0


def decode_status(value):
    """Names of the bits set in one `status` value, lowest bit first. A missing value
    (old file) gives []. A set bit this table does not know appears as 'BIT<n>'."""
    if value is None or pd.isna(value):
        return []
    value = int(value)
    names = {bit: name for name, bit in STATUS_BITS.items()}
    return [names.get(b, f"BIT{b}") for b in range(value.bit_length()) if value >> b & 1]


def has_status(series, name):
    """Boolean Series: is bit `name` set? Rows with no status (old files) give False."""
    return (series.fillna(0).astype("int64") & (1 << STATUS_BITS[name])) != 0

EVENT_COLUMNS = ["start", "end", "class", "effect_C", "note"]
EVENT_CLASSES = ["spike", "minor_lull", "irradiance_rise", "mixed",
                 "natural_spike", "natural_minor", "ambient_rise"]
