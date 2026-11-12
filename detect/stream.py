"""Feed a day table to any detector one row at a time (no look-ahead).

Every detector has the same interface:
    reset()            start of a logged day
    update(sample)     -> True if it raises an alarm at this sample
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from data.schema import BACK_SENSORS


@dataclass
class Sample:
    t_back: tuple      # T1..T5, degC
    ta: float          # ambient, degC
    g: float           # in-plane irradiance, W/m2
    wind: float        # m/s (NaN if no anemometer)
    doy: int           # day of year, local date
    minute: float      # minutes since local midnight


def samples(days):
    """Yield (new_day, Sample) for each row of a schema-format day table."""
    ts = pd.to_datetime(days["date"] + " " + days["time"])
    doy = ts.dt.day_of_year.to_numpy()
    minute = (ts.dt.hour * 60 + ts.dt.minute + ts.dt.second / 60).to_numpy()
    tb = days[BACK_SENSORS].to_numpy(dtype=float)
    ta = days["T_ambient"].to_numpy(dtype=float)
    g = days["irradiance"].to_numpy(dtype=float)
    wind = (days["wind"].to_numpy(dtype=float) if "wind" in days
            else np.full(len(days), np.nan))
    # A detector restarts at each new logged day -- or, for real logger data, at each
    # new `segment` (analysis/ingest.py): a new day, a gap, or a row with a missing input.
    date = days["segment" if "segment" in days else "date"].to_numpy()
    for i in range(len(days)):
        yield (i == 0 or date[i] != date[i - 1],
               Sample(tuple(tb[i]), ta[i], g[i], wind[i], int(doy[i]), float(minute[i])))


def run(detector, days):
    """Alarm flag per row; the detector is reset at every new date."""
    out = np.zeros(len(days), dtype=bool)
    for i, (new_day, s) in enumerate(samples(days)):
        if new_day:
            detector.reset()
        out[i] = detector.update(s)
    return out
