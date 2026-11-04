"""Chronological split by whole days -- never random samples.

Neighbouring 10 s samples are nearly identical, so a random split would put
near-copies of test samples into training and inflate every metric.
"""
import pandas as pd

import config


def split_days(days, events, sizes=config.SPLIT_DAYS):
    """Return {"train"|"val"|"test": (days_part, events_part)} in date order."""
    dates = sorted(days["date"].unique())
    n_tr, n_va, n_te = sizes
    assert n_tr + n_va + n_te <= len(dates), "not enough days for the split"
    parts = {"train": dates[:n_tr], "val": dates[n_tr:n_tr + n_va],
             "test": dates[n_tr + n_va:n_tr + n_va + n_te]}
    ev_date = pd.to_datetime(events["start"]).dt.strftime("%Y-%m-%d")
    return {name: (days[days["date"].isin(d)].reset_index(drop=True),
                   events[ev_date.isin(d)].reset_index(drop=True))
            for name, d in parts.items()}
