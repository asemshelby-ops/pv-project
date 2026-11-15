"""analysis/powerbi.py: alarm onsets, alarm-to-spike matching, energy without sweep rows."""
import pandas as pd

from analysis import powerbi


def _df(alarm, mode=None, power=50.0):
    t = pd.date_range("2026-11-20 09:00:00", periods=len(alarm), freq="10s", tz="UTC")
    return pd.DataFrame({"t_utc": t, "t_local": t.tz_convert("Asia/Riyadh").tz_localize(None),
                         "alarm": alarm, "power": power,
                         "load_mode": mode or ["hold"] * len(alarm)})


def test_alarm_onsets_one_row_per_rising_edge_and_detector():
    al = powerbi.alarm_onsets(_df([0, 1, 1, 0, 5, 4, 0]))   # 5 = NASIM + CUSUM
    assert list(al["detector"]) == ["NASIM", "NASIM", "CUSUM"]
    assert list(al["time_local"]) == ["2026-11-20 12:00:10", "2026-11-20 12:00:40",
                                      "2026-11-20 12:00:40"]


def test_spike_gets_first_alarm_in_window_and_delay():
    ev = pd.DataFrame({"event_id": [1], "date_local": ["2026-11-20"],
                       "start": ["2026-11-20 12:00:00"], "end": ["2026-11-20 12:05:00"],
                       "class": ["spike"], "size_C": [3.2], "duration_s": [300.0],
                       "max_rate_C_per_min": [0.8], "dG": [10.0], "f_G": [0.1],
                       "cause": ["non_irradiance"], "origin": ["natural"], "valid": [True],
                       "note": [""]})
    al = pd.DataFrame({"date": ["2026-11-20"] * 2, "detector": ["NASIM", "CUSUM"],
                       "time_local": ["2026-11-20 12:03:00", "2026-11-20 12:30:00"]})
    s = powerbi.spikes_table(ev, al)
    assert s.loc[0, "first_alarm_NASIM"] == "2026-11-20 12:03:00"
    assert s.loc[0, "delay_min_NASIM"] == 3.0
    assert s.loc[0, "first_alarm_CUSUM"] == ""          # 25 min after the peak: not matched


def test_row_hours_cap_gaps():
    d = _df([0] * 3)
    d.loc[2, "t_utc"] += pd.Timedelta(minutes=10)       # a gap adds no energy
    assert list(powerbi._row_dt_h(d) * 3600) == [10.0, 10.0, 10.0]
