"""CSV schema shared by synthetic and real data (CLAUDE.md, Data format + wind)."""

COLUMNS = [
    "date", "time", "T1", "T2", "T3", "T4", "T5", "T_ambient", "humidity",
    "irradiance", "voltage", "current", "power", "wind",
]
BACK_SENSORS = ["T1", "T2", "T3", "T4", "T5"]

EVENT_COLUMNS = ["start", "end", "class", "effect_C", "note"]
EVENT_CLASSES = ["spike", "minor_lull", "irradiance_rise", "mixed",
                 "natural_spike", "natural_minor"]
