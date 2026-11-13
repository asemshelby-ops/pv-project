"""Soiling housekeeping checks (analysis/soiling.py): days flagged after a dust storm line
and power per irradiance before and after a cleaning. Synthetic data; dust is imitated by
scaling the logged power of one day."""
import pandas as pd
import pytest

import config
from analysis import fake_logger, ingest, pipeline, soiling
from data import synth

CLEAN = [("06:10:00", "cleaning start", ""), ("06:20:00", "cleaning end", "")]


@pytest.fixture(scope="module")
def dusty_rig(tmp_path_factory):
    """Five days. Day 1: dust storm noted at 16:00. Day 2: NOT cleaned, power 7 % low all
    day. Day 3: cleaned as usual, power back. Days 4 and 5: normal."""
    out = tmp_path_factory.mktemp("dust")
    days = synth.generate(seed=4, n_days=5, hard=False)[0]
    dates = sorted(days["date"].unique())
    low = days["date"] == dates[1]
    days.loc[low, ["current", "power"]] = days.loc[low, ["current", "power"]] * 0.93
    fake_logger.write_logger_files(days, out)
    schedule = {d: list(CLEAN) for d in dates if d != dates[1]}
    schedule[dates[0]].append(("16:00:00", "dust storm", "haze from the west, visible film"))
    fake_logger.write_markers(schedule, out)
    m, _ = ingest.read_markers(out / "markers.csv")
    joined, problems = ingest.join_logsheets(m, out / "logsheets")
    assert not problems
    return out, dates, ingest.read_logger(out), joined


def test_days_after_a_dust_storm_are_flagged_until_the_next_cleaning(dusty_rig):
    _, dates, _, joined = dusty_rig
    d = soiling.dust_days(joined)
    assert list(d["date"]) == dates[:3]            # storm day, uncleaned day, day of the cleaning
    assert (d["dust_line"].dt.strftime("%Y-%m-%d %H:%M") == f"{dates[0]} 16:00").all()


def test_no_dust_line_means_no_flag(dusty_rig):
    _, _, _, joined = dusty_rig
    assert len(soiling.dust_days(joined[joined["event"] != "dust storm"])) == 0


def test_a_dust_storm_never_cleaned_flags_every_day_to_the_end(dusty_rig):
    _, dates, _, joined = dusty_rig
    never = joined[~((joined["event"] == "cleaning end") & (joined["date_local"] > dates[0]))]
    assert list(soiling.dust_days(never)["date"]) == dates


def test_cleaning_check_sees_the_power_come_back(dusty_rig):
    _, dates, df, joined = dusty_rig
    c = soiling.cleaning_check(df, joined).set_index("date")
    assert list(c.index) == [dates[0], dates[2], dates[3], dates[4]]   # one line per cleaning
    assert c.loc[dates[0], "note"] == "no day before or after in the data"
    third = c.loc[dates[2]]                        # cleaned after the dusty, uncleaned day
    assert (third["day_before"], third["day_after"]) == (dates[1], dates[2])
    assert third["change"] == pytest.approx(1 / 0.93 - 1, abs=0.02) and third["note"] == "check"
    assert third["after_dust"] and not c.loc[dates[4], "after_dust"]
    for d in dates[3:]:                            # ordinary days: nothing to report
        assert abs(c.loc[d, "change"]) < config.SOIL_CHECK_REL and c.loc[d, "note"] == ""
        assert c.loc[d, "rows_before"] >= config.SOIL_MIN_ROWS


def test_cleaning_check_says_so_when_there_is_no_power(dusty_rig):
    _, dates, df, joined = dusty_rig
    off = df.assign(power=0.0)                     # today's logger: module at open circuit
    c = soiling.cleaning_check(off, joined)
    assert c["change"].isna().all()
    assert (c["note"].iloc[1:] == "no power or not enough sun at midday (load off, cloud, night)").all()
    tripped = df.assign(load_mode="trip")          # power logged while the load is not holding
    assert soiling.cleaning_check(tripped, joined)["change"].isna().all()


def test_pipeline_writes_the_flags_and_the_check_and_excludes_nothing(dusty_rig, tmp_path):
    out, dates, df, joined = dusty_rig
    res = pipeline.run(out, tmp_path / "res", n_boot=20, synthetic=True, log=lambda *_: None)
    q = pd.read_csv(tmp_path / "res" / "day_quality.csv")
    assert list(q["after_dust"]) == [True, True, True, False, False]
    soil = pd.read_csv(tmp_path / "res" / "soiling_check.csv")
    assert (soil["note"] == "check").sum() == 1
    notes = "\n".join(res["notes"])
    assert "3 days flagged after a dust storm line" in notes and "NOT excluded" in notes
    assert "1 with a change above 3%" in notes
    assert res["frame"]["date"].nunique() == len(dates)        # no day was dropped
