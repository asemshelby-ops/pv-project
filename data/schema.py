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
# firmware/libraries/PvDaq/src/pv_status.h (tests/test_logger_format.py compares the two).
STATUS_BITS = {
    "RTC_INVALID": 0,     # no valid RTC time: date and time empty, use uptime_ms
    "TEMP_FAIL": 1,       # at least one of T1..T5 gave no valid reading
    "AMBIENT_FAIL": 2,    # BME280: no ambient temperature
    "IRRAD_FAIL": 3,      # PYR20: no irradiance
    "PANEL_FAIL": 4,      # panel INA226: no V, I, P
    "TROM_UNMAPPED": 5,   # a T1..T5 row of T_ROM is not filled in. CSV v2: that column is
                          # empty. Older files: T1..T5 followed the bus-scan order
    "DET_SKIPPED": 6,     # row not given to the on-board detector
    "DET_RESET": 7,       # on-board detector state cleared at this row
    "SD_RETRY": 8,        # not on the card at the first attempt, or the row(s) before were lost
    "SD_BUFFERED": 9,     # CSV v2: the row waited in the LittleFS ring buffer
}

# --- CSV v2: the logger since 2026-11-21 (docs/paper/13-logger-robustness.md) -------------
# Every block of rows the logger writes (a new file, and the first rows after each boot)
# starts with the line "# nasim-logger-csv v2" and a header line. Files without that line
# are older: 16 columns (version 0) or 21 (version 1). analysis/ingest.py puts the version
# of each row in the column `csv_version`.
LOGGER_VERSION_PREFIX = "# nasim-logger-csv v"
LOGGER_CSV_VERSION = 2

# Sensor numbers: bit n of `sens_fail` = that sensor failed at the row, and the order of
# the fail-counter columns. Must equal the PV_SENSOR_* definitions in pv_status.h.
SENSOR_BITS = {
    "T1": 0, "T2": 1, "T3": 2, "T4": 3, "T5": 4, "T6": 5, "T7": 6,   # DS18B20 by ROM (T_ROM)
    "env": 7,             # BME280
    "rtc": 8,             # DS3231: no answer, time not set, or a time jump not accepted
    "hub": 9,             # PaHub: no answer (the INA226 behind it is then not tried)
    "ina": 10,            # panel INA226 itself
    "pyr": 11,            # PYR20 over RS485 Modbus
    "sd": 12,             # microSD: the row did not reach the card at once
}
FAIL_COUNTER_COLUMNS = [f"f_{name}" for name in SENSOR_BITS]   # failed rows since boot

# Appended after `status`, in this order. T6, T7: the two further DS18B20 (empty = no
# reading or not fitted). pyr_mV: the PYR20 0-2 V output on GPIO 34, raw millivolts
# (manual: 1 mV = 1 W/m2; the ESP32 ADC is usable from about 150 mV). ring_rows: rows
# waiting in the LittleFS buffer when the row was built; ring_dropped: rows the buffer had
# to give up since it was last empty; cycle_ms: how long the row BEFORE this one took.
LOGGER_V2_COLUMNS = ["T6", "T7", "pyr_mV", "sens_fail", *FAIL_COUNTER_COLUMNS,
                     "ring_rows", "ring_dropped", "cycle_ms"]

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


def decode_sensors(value):
    """Names of the sensors that failed at a row, from its `sens_fail` value. A missing
    value (file older than CSV v2) gives []."""
    if value is None or pd.isna(value):
        return []
    value = int(value)
    names = {bit: name for name, bit in SENSOR_BITS.items()}
    return [names.get(b, f"BIT{b}") for b in range(value.bit_length()) if value >> b & 1]


def has_sensor_fail(series, name):
    """Boolean Series: did sensor `name` fail at the row? No value (older file): False."""
    return (series.fillna(0).astype("int64") & (1 << SENSOR_BITS[name])) != 0

EVENT_COLUMNS = ["start", "end", "class", "effect_C", "note"]
EVENT_CLASSES = ["spike", "minor_lull", "irradiance_rise", "mixed",
                 "natural_spike", "natural_minor", "ambient_rise"]
