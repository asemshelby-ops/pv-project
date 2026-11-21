"""Tag list and data model of the SCADA layer, generated from the logger's CSV schema.

The column names and the bit numbers are NOT typed here: they come from data/schema.py
(COLUMNS, LOGGER_EXTRA_COLUMNS, STATUS_BITS, ALARM_BITS), the table the analysis and the
firmware already share. This file adds what a SCADA needs on top: units, display ranges,
the tags decoded from the two bit fields, and the alarm definitions.

    python -m scada.model          writes docs/scada/tag-list.md from this model

Ranges are HMI display scales chosen for the plots [ASSUMPTION], not sensor limits,
unless the note names a source. Alarm priorities are a first proposal [ASSUMPTION].
"""
import pathlib
from dataclasses import asdict, dataclass

from data.schema import (ALARM_BITS, BACK_SENSORS, COLUMNS, LOGGER_EXTRA_COLUMNS,
                         STATUS_BITS)

# The three row formats a file can have. The logger wrote FORMAT_OLD until 2026-11-12 and
# writes FORMAT_NEW since; FORMAT_SCHEMA is the bare schema (synthetic day tables).
FORMAT_SCHEMA = list(COLUMNS)
FORMAT_OLD = FORMAT_SCHEMA + ["p", "alarm"]
FORMAT_NEW = FORMAT_OLD + list(LOGGER_EXTRA_COLUMNS)
FORMATS = {len(f): f for f in (FORMAT_SCHEMA, FORMAT_OLD, FORMAT_NEW)}

LOAD_MODES = {  # the `load_mode` column (docs/paper/07 sec. 7)
    "none": "no load fitted",
    "off": "load fitted, both branches off (open circuit)",
    "hold": "holding the operating point of the last sweep",
    "sweep": "I-V sweep running (not expected in 10 s rows)",
    "trip": "fault: both branches off until the cause is gone",
}


@dataclass(frozen=True)
class Tag:
    name: str            # tag name = historian column for logged tags
    kind: str            # analog | counter | bitfield | enum | discrete | time
    unit: str
    lo: float | None     # display range
    hi: float | None
    decimals: int | None
    source: str          # "csv:<column>", "alarm bit n", "status bit n", "derived"
    description: str
    group: str


def _tags():
    t = [Tag("date", "time", "UTC", None, None, None, "csv:date",
             "UTC date of the row (DS3231). Empty when the RTC time is invalid.", "time"),
         Tag("time", "time", "UTC", None, None, None, "csv:time",
             "UTC time of the row. Stored in the historian as t_utc (Unix seconds).", "time")]
    for s in BACK_SENSORS:
        t.append(Tag(s, "analog", "degC", 0, 90, 2, f"csv:{s}",
                     "Back-sheet temperature, DS18B20 (12 bit: 0.0625 degC steps). Empty = "
                     "no valid reading.", "temperature"))
    t += [
        Tag("T_ambient", "analog", "degC", 0, 60, 2, "csv:T_ambient",
            "Ambient air temperature, BME280.", "weather"),
        Tag("humidity", "analog", "%RH", 0, 100, 1, "csv:humidity",
            "Relative humidity, BME280.", "weather"),
        Tag("irradiance", "analog", "W/m2", 0, 1400, 0, "csv:irradiance",
            "In-plane irradiance, PYR20 over Modbus (resolution 1 W/m2).", "weather"),
        Tag("voltage", "analog", "V", 0, 30, 3, "csv:voltage",
            "Module voltage, panel INA226 (bus range 0-30 V).", "electrical"),
        Tag("current", "analog", "A", 0, 10, 3, "csv:current",
            "Module current, panel INA226 (range +/-10 A).", "electrical"),
        Tag("power", "analog", "W", 0, 120, 2, "csv:power",
            "Module power at the row's operating point (not P_mp: that comes from sweeps).",
            "electrical"),
        Tag("wind", "analog", "m/s", 0, 15, 1, "csv:wind",
            "Wind speed. 'NaN' while no anemometer is fitted (HAS_ANEMOMETER 0).", "weather"),
        Tag("p", "analog", "-", 0, 1, 3, "csv:p",
            "NASIM-GBT probability. Empty while its 10-min buffers fill or the row was "
            "skipped.", "detector"),
        Tag("alarm", "bitfield", "-", 0, sum(1 << b for b in ALARM_BITS.values()), 0,
            "csv:alarm", "One bit per on-board detector; values add up. Decoded into the "
            "ALM.* tags.", "detector"),
        Tag("D", "analog", "-", 0, 1, 3, "csv:D",
            "PWM duty of load branch A at the reading. 'NaN' = no load fitted.", "load"),
        Tag("load_mode", "enum", "/".join(LOAD_MODES), None, None, None, "csv:load_mode",
            "State of the measurement load.", "load"),
        Tag("sweep_id", "counter", "-", None, None, 0, "csv:sweep_id",
            "Id of the last finished I-V sweep (Unix seconds of its start); 0 = none yet.",
            "load"),
        Tag("uptime_ms", "counter", "ms", None, None, 0, "csv:uptime_ms",
            "millis() at the row. A fall = the board restarted. Wraps after 49.7 days.",
            "system"),
        Tag("status", "bitfield", "-", 0, sum(1 << b for b in STATUS_BITS.values()), 0,
            "csv:status", "Health bits of the row; 0 = nothing to report. Decoded into the "
            "ST.* tags. Not recorded in files written before 2026-11-12.", "system"),
        Tag("T_module", "analog", "degC", 0, 90, 2, "derived",
            "Mean of the T1..T5 values present in the row (the detectors use the mean of "
            "all five).", "temperature"),
        Tag("dT_module_ambient", "analog", "K", -10, 50, 2, "derived",
            "T_module - T_ambient.", "temperature"),
    ]
    alarm_text = {"NASIM": "NASIM-GBT alarmed at this row.",
                  "THRESHOLD": "Temperature-rise threshold rule alarmed at this row.",
                  "CUSUM": "CUSUM on the temperature increment alarmed at this row."}
    for name, bit in ALARM_BITS.items():
        t.append(Tag(f"ALM.{name}", "discrete", "0/1", 0, 1, 0, f"alarm bit {bit} (value "
                     f"{1 << bit})", alarm_text.get(name, "Detector alarm."), "detector"))
    for name, bit in STATUS_BITS.items():
        t.append(Tag(f"ST.{name}", "discrete", "0/1", 0, 1, 0, f"status bit {bit} (value "
                     f"{1 << bit})", STATUS_TEXT.get(name, "Status bit."), "system"))
    return t


STATUS_TEXT = {  # meaning of each status bit (firmware: PvDaq/src/pv_status.h)
    "RTC_INVALID": "No valid RTC time at this row: date and time are empty.",
    "TEMP_FAIL": "At least one of T1..T5 gave no valid reading.",
    "AMBIENT_FAIL": "BME280: no ambient temperature.",
    "IRRAD_FAIL": "PYR20: no irradiance.",
    "PANEL_FAIL": "Panel INA226 (or its PaHub channel): no V, I, P.",
    "TROM_UNMAPPED": "T_ROM not filled in: T1..T5 follow the bus-scan order.",
    "DET_SKIPPED": "Row not given to the detectors (an input or the time is missing).",
    "DET_RESET": "Detector state cleared at this row (5 skipped rows in a row).",
    "SD_RETRY": "SD: second write attempt, or the row(s) before did not reach the card.",
    "BUFFERED": "SD: row waited in the ESP32's flash and was copied to the card later.",
}
TAGS = _tags()
LOGGED = [t.name for t in TAGS if t.source.startswith("csv:") and t.kind != "time"]
REAL_COLUMNS = [t.name for t in TAGS if t.source.startswith("csv:") and t.kind == "analog"]
INT_COLUMNS = ["alarm", "sweep_id", "uptime_ms", "status"]


# --- alarm definitions ---------------------------------------------------------------------

@dataclass(frozen=True)
class AlarmDef:
    code: str
    text: str
    priority: int        # 1 = high, 2 = medium, 3 = low
    group: str           # detector | sensor | system
    field: str           # which row field carries the condition: alarm | status | load_mode
    key: str             # bit name, or the load_mode value
    clear_rows: int      # rows in a row without the condition before the alarm clears


ALARM_DEFS = [
    AlarmDef("DET_NASIM", "Thermal spike detected by NASIM-GBT", 2, "detector", "alarm",
             "NASIM", 1),
    AlarmDef("DET_THRESHOLD", "Thermal spike detected by the threshold rule", 2, "detector",
             "alarm", "THRESHOLD", 1),
    AlarmDef("DET_CUSUM", "Thermal spike detected by the CUSUM", 2, "detector", "alarm",
             "CUSUM", 1),
    AlarmDef("RTC_INVALID", "RTC time invalid: rows logged without date and time", 1,
             "system", "status", "RTC_INVALID", 3),
    AlarmDef("TEMP_FAIL", "Back-sheet temperature sensor not reading", 1, "sensor", "status",
             "TEMP_FAIL", 3),
    AlarmDef("AMBIENT_FAIL", "BME280: no ambient temperature", 2, "sensor", "status",
             "AMBIENT_FAIL", 3),
    AlarmDef("IRRAD_FAIL", "PYR20: no irradiance", 1, "sensor", "status", "IRRAD_FAIL", 3),
    AlarmDef("PANEL_FAIL", "Panel INA226: no voltage, current, power", 1, "sensor", "status",
             "PANEL_FAIL", 3),
    AlarmDef("TROM_UNMAPPED", "T_ROM not filled in: T1..T5 are not tied to module positions",
             3, "system", "status", "TROM_UNMAPPED", 3),
    AlarmDef("DET_RESET", "Detectors reset after 5 unusable rows: no detection for 10 min",
             2, "system", "status", "DET_RESET", 1),
    AlarmDef("SD_ERROR", "SD card write error: a retry was needed or rows were lost", 1,
             "system", "status", "SD_RETRY", 3),
    AlarmDef("LOAD_TRIP", "Measurement load tripped: both branches off", 1, "system",
             "load_mode", "trip", 1),
    AlarmDef("NO_DATA", "No data arriving from the logger", 1, "system", "pc_clock",
             "NO_DATA_LIMIT_S", 1),
]
# NO_DATA is the one alarm that does not come from a row: it is raised when no new row
# has been RECEIVED for longer than this (PC clock), and cleared by the next new row
# (scada/alarms.py check_no_data). 60 s = six missed 10 s rows.
NO_DATA_LIMIT_S = 60.0   # [ASSUMPTION] to be reviewed with the real logger and link
ALARM_BY_CODE = {d.code: d for d in ALARM_DEFS}
# DET_SKIPPED is deliberately not an alarm: it is the consequence of a sensor or RTC fault
# that already alarms. The HMI shows it in the health panel.


# --- decoding --------------------------------------------------------------------------------

def bit_names(value, table):
    """Names of the bits set in `value`, lowest first. None (not recorded) gives []."""
    if value is None:
        return []
    names = {bit: name for name, bit in table.items()}
    return [names.get(b, f"BIT{b}") for b in range(int(value).bit_length()) if value >> b & 1]


def status_names(value):
    return bit_names(value, STATUS_BITS)


def alarm_names(value):
    return bit_names(value, ALARM_BITS)


def t_module(row):
    """Mean of the back-sheet values present, or None if there is none."""
    v = [row[s] for s in BACK_SENSORS if row.get(s) is not None]
    return sum(v) / len(v) if v else None


def conditions(row):
    """Alarm conditions of one row: {code: True (on) / False (off)}. A code is ABSENT when
    the row cannot tell: its field was not recorded (old file format), so the alarm
    neither starts nor clears on that row."""
    out = {}
    for d in ALARM_DEFS:
        v = row.get(d.field)
        if v is None:
            continue
        if d.field == "load_mode":
            out[d.code] = v == d.key
        else:
            table = ALARM_BITS if d.field == "alarm" else STATUS_BITS
            out[d.code] = bool(v >> table[d.key] & 1)
    return out


def detail(code, row):
    """Short text stored with an alarm when it is raised."""
    tm = t_module(row)
    if code.startswith("DET_") and code != "DET_RESET":
        parts = [f"T_module {tm:.2f} degC" if tm is not None else None,
                 f"G {row['irradiance']:.0f} W/m2" if row.get("irradiance") is not None else None,
                 f"p {row['p']:.3f}" if row.get("p") is not None else None]
        return ", ".join(p for p in parts if p)
    if code == "TEMP_FAIL":
        return "no value: " + ", ".join(s for s in BACK_SENSORS if row.get(s) is None)
    if code == "RTC_INVALID":
        return f"time of the row taken from: {row.get('time_source', '?')}"
    return ""


def health(row):
    """Sensor health of one row for the HMI: a list of {key, label, state, detail}.
    state: ok | fault | warn | unknown (status not recorded: file older than 2026-11-12)."""
    if row is None:
        return []
    st = row.get("status")
    known = st is not None
    bits = set(status_names(st))

    def item(key, label, fault_bit, missing, warn=False, text=""):
        if fault_bit in bits or missing:
            state = "warn" if warn else "fault"
            text = text or STATUS_TEXT.get(fault_bit, "no value in the row")
        elif not known:
            state, text = "unknown", "status not recorded (old file format)"
        else:
            state, text = "ok", ""
        return {"key": key, "label": label, "state": state, "detail": text}

    out = [item("rtc", "RTC (DS3231)", "RTC_INVALID", row.get("time_source") != "rtc")]
    for s in BACK_SENSORS:
        out.append(item(s, f"{s} back sheet (DS18B20)", None, row.get(s) is None))
    out += [
        item("mapping", "T1..T5 mapped to positions", "TROM_UNMAPPED", False, warn=True),
        item("ambient", "Ambient (BME280)", "AMBIENT_FAIL", row.get("T_ambient") is None),
        item("irradiance", "Irradiance (PYR20)", "IRRAD_FAIL", row.get("irradiance") is None),
        item("panel", "Panel V/I (INA226)", "PANEL_FAIL", row.get("voltage") is None),
        item("sd", "SD card", "SD_RETRY", False),
        item("detectors", "Detectors fed this row", "DET_SKIPPED", False, warn=True),
    ]
    return out


# --- generated documentation -----------------------------------------------------------------

def tag_list_markdown():
    lines = [
        "# Tag list and data model",
        "",
        "GENERATED by `python -m scada.model` from `scada/model.py`, which takes the column",
        "names and bit numbers from `data/schema.py`. Do not edit by hand:",
        "`tests/test_scada_model.py` fails if this file and the model differ.",
        "",
        "Ranges are HMI display scales `[ASSUMPTION]`, not sensor limits, unless the",
        "description names a source. Alarm priorities are a first proposal `[ASSUMPTION]`.",
        "",
        "## Row formats",
        "",
        "| Format | Columns | Header |",
        "|---|---|---|",
        f"| new (logger since 2026-11-12) | {len(FORMAT_NEW)} | `{','.join(FORMAT_NEW)}` |",
        f"| old (logger before 2026-11-12) | {len(FORMAT_OLD)} | `{','.join(FORMAT_OLD)}` |",
        f"| bare schema (synthetic day tables) | {len(FORMAT_SCHEMA)} | "
        f"`{','.join(FORMAT_SCHEMA)}` |",
        "",
        "A column a format does not have is stored as NULL (\"not recorded\"). An empty",
        "field and the text `NaN` are both stored as NULL.",
        "",
        "## Tags",
        "",
        "| Tag | Kind | Unit | Range | Source | Description |",
        "|---|---|---|---|---|---|",
    ]
    for t in TAGS:
        rng = "" if t.lo is None else f"{t.lo:g} .. {t.hi:g}"
        lines.append(f"| `{t.name}` | {t.kind} | {t.unit} | {rng} | {t.source} | "
                     f"{t.description} |")
    lines += ["", "## Load modes (`load_mode`)", "", "| Value | Meaning |", "|---|---|"]
    lines += [f"| `{k}` | {v} |" for k, v in LOAD_MODES.items()]
    lines += ["", "## Alarm bits (`alarm`)", "", "| Bit | Value | Detector | Tag |",
              "|---|---|---|---|"]
    lines += [f"| {b} | {1 << b} | {n} | `ALM.{n}` |" for n, b in ALARM_BITS.items()]
    lines += ["", "## Status bits (`status`)", "", "| Bit | Value | Name | Meaning |",
              "|---|---|---|---|"]
    lines += [f"| {b} | {1 << b} | `{n}` | {STATUS_TEXT.get(n, '')} |"
              for n, b in STATUS_BITS.items()]
    lines += ["", "## Alarm definitions", "",
              "Priority 1 = high, 2 = medium, 3 = low. \"Clears after\" = rows in a row",
              "without the condition before the alarm counts as cleared.", "",
              "| Code | Priority | Group | Condition | Clears after | Text |",
              "|---|---|---|---|---|---|"]
    for d in ALARM_DEFS:
        cond = (f"`load_mode` = `{d.key}`" if d.field == "load_mode"
                else f"no new row received for {NO_DATA_LIMIT_S:g} s (PC clock)"
                if d.field == "pc_clock" else f"`{d.field}` bit `{d.key}`")
        lines.append(f"| `{d.code}` | {d.priority} | {d.group} | {cond} | {d.clear_rows} | "
                     f"{d.text} |")
    lines += ["", "`DET_SKIPPED` is not an alarm: it follows from a sensor or RTC fault that",
              "already alarms. The HMI shows it in the health panel.", ""]
    return "\n".join(lines)


def as_json():
    """The model as plain data, for the HMI."""
    return {"tags": [asdict(t) for t in TAGS], "alarm_bits": ALARM_BITS,
            "status_bits": STATUS_BITS, "status_text": STATUS_TEXT, "load_modes": LOAD_MODES,
            "alarm_defs": [asdict(d) for d in ALARM_DEFS], "back_sensors": BACK_SENSORS}


TAG_LIST_PATH = pathlib.Path(__file__).resolve().parent.parent / "docs/scada/tag-list.md"

if __name__ == "__main__":
    TAG_LIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    TAG_LIST_PATH.write_text(tag_list_markdown(), encoding="utf-8")
    print(f"wrote {TAG_LIST_PATH} ({len(TAGS)} tags, {len(ALARM_DEFS)} alarm definitions)")
