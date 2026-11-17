"""Load-off spikes as their own class (config.LOADOFF_CLASS_ENABLED, off by default):
coded marker rows for the three controlled tests, the `ctl_load` origin and `load_off`
cause, and the verdict on the detector claim with and without those spikes.

All data is SYNTHETIC. The load-off rises are invented (analysis/fake_logger.py,
add_load_off_tests): whether the real module heats by 3 degC in 10 min at open circuit is
not known (docs/paper/03 sec. 11 estimates 1.9 to 4.7 degC)."""
import json
import pathlib
import re

import pandas as pd
import pytest

import config
from analysis import detection, events, fake_logger, ingest, metrics, pipeline, report, verdict
from data import synth
from data.schema import MARKER_CODES
from export import phase_b_header

ROOT = pathlib.Path(__file__).resolve().parent.parent
B = config.PREREG_BUDGET_FA_PER_DAY


@pytest.fixture(scope="module")
def campaign(tmp_path_factory):
    """10 synthetic days with a cleaning every morning and two open-circuit tests a day,
    written with coded markers."""
    out = tmp_path_factory.mktemp("loadoff")
    days, drivers = synth.generate(seed=5, n_days=10, hard=True)
    dates = sorted(days["date"].unique())
    tests = [(d, hms, 12) for d in dates for hms in ("10:30:00", "14:00:00")]
    days, schedule = fake_logger.add_load_off_tests(days, tests)
    for d in dates:
        schedule[d] += [("06:10:00", "cleaning start", ""), ("06:20:00", "cleaning end", "")]
    schedule[dates[1]] += [("11:20:00", "mesh on", ""), ("11:40:00", "mesh off", "")]
    fake_logger.write_logger_files(days, out)
    fake_logger.write_markers(schedule, out, codes=True)
    return {"out": out, "days": days, "dates": dates, "tests": tests}


# --- marker codes -------------------------------------------------------------------------------

def test_codes_are_distinct_and_reach_the_firmware_through_the_generated_header():
    assert MARKER_CODES[0] == "button" and len(set(MARKER_CODES.values())) == len(MARKER_CODES)
    for kind in ("mesh", "windbreak", "load"):                  # shade cloth, windbreak, load-off
        pair = [c for c, name in MARKER_CODES.items() if name.startswith(kind)]
        assert len(pair) == 2 and pair[0] // 10 == pair[1] // 10 and pair[0] % 10 == 0
    kinds = {c // 10 for c in MARKER_CODES if c}
    assert len(kinds) == 3                                      # three tests, three decades
    header = phase_b_header.header()
    for code, name in MARKER_CODES.items():
        assert f"#define MARKER_CODE_{name.upper().replace(' ', '_')} {code}\n" in header
    ino = (ROOT / "firmware/logger/logger.ino").read_text(encoding="utf-8")
    named = dict(re.findall(r'if \(!strcmp\(what, "([a-z ]+)"\)\) return (MARKER_CODE_\w+);', ino))
    assert named == {name: f"MARKER_CODE_{name.upper().replace(' ', '_')}"
                     for name in MARKER_CODES.values() if name != "button"}
    assert 'writeMarker(ms, "load off")' in ino and 'writeMarker(millis(), "load on")' in ino
    for cmd, name in (("MARK MESH ON", "mesh on"), ("MARK WINDBREAK OFF", "windbreak off")):
        assert f'if (!strcmp(c, "{cmd}")) writeMarker(ms, "{name}");' in ino


def test_plain_marker_file_reads_exactly_as_before(tmp_path):
    fake_logger.write_markers({"2026-12-01": [("06:10:00", "cleaning start", ""),
                                              ("10:00:00", "mesh on", "")]}, tmp_path)
    assert (tmp_path / "markers.csv").read_text().splitlines()[0] == "date,time,millis"
    m, no_time = ingest.read_markers(tmp_path / "markers.csv")
    assert list(m.columns) == ["t_utc", "t_local", "date_local", "marker"] and no_time == 0
    pd.testing.assert_frame_equal(ingest._read_marker_rows(tmp_path / "markers.csv"),
                                  pd.read_csv(tmp_path / "markers.csv", dtype=str))


def test_coded_rows_are_read_and_name_their_own_event(campaign, tmp_path):
    m, no_time = ingest.read_markers(campaign["out"] / "markers.csv")
    assert "code" in m and no_time == 0 and set(m["code"]) == {0, 10, 11, 30, 31}
    # with the typed sheets: the names agree
    joined, problems = ingest.join_logsheets(m, campaign["out"] / "logsheets")
    assert problems == []
    assert (joined.loc[joined["code"] == 30, "event"] == "load off").all()
    # WITHOUT any sheet the coded rows are still known; the button rows are not
    blind, problems = ingest.join_logsheets(m, tmp_path / "no_sheets")
    assert len(problems) == len(campaign["dates"])
    assert (blind.loc[blind["code"] == 31, "event"] == "load on").all()
    assert (blind.loc[blind["code"] == 10, "event"] == "mesh on").all()
    assert (blind.loc[blind["code"] == 0, "event"] == "").all()
    # a sheet that says something else is reported; the code is used
    day = campaign["dates"][0]
    sheets = tmp_path / "sheets"
    sheets.mkdir()
    for f in (campaign["out"] / "logsheets").glob("*.csv"):
        text = f.read_text()
        (sheets / f.name).write_text(text.replace("load off", "windbreak on", 1) if day in f.name else text)
    wrong, problems = ingest.join_logsheets(m, sheets)
    assert len(problems) == 1 and "code 30 = 'load off'" in problems[0] and "windbreak on" in problems[0]
    assert (wrong.loc[wrong["code"] == 30, "event"] == "load off").all()


def test_marker_file_begun_without_codes_and_continued_with_them(tmp_path):
    (tmp_path / "markers.csv").write_text(
        "date,time,millis\n2026-12-01,03:10:00.137,160137\n"
        "date,time,millis,code\n2026-12-01,07:30:00.274,7000274,30\n"
        "2026-12-01,07:42:00.411,7720411,31\n,,9000000,0\n2026-12-02,03:10:00.500,90000500,99\n")
    m, no_time = ingest.read_markers(tmp_path / "markers.csv")
    assert no_time == 1 and list(m["code"]) == [0, 30, 31, 99]
    assert m["t_local"].iloc[1] == pd.Timestamp("2026-12-01 10:30:00.274")
    joined, problems = ingest.join_logsheets(m, tmp_path / "none")
    assert list(joined["event"]) == ["", "load off", "load on", ""]
    assert any("unknown marker code 99" in p for p in problems)
    (tmp_path / "bad.csv").write_text("date,time,millis,code\n2026-12-01,03:10:00.1,5,30,extra\n")
    with pytest.raises(ValueError):
        ingest.read_markers(tmp_path / "bad.csv")


# --- origin and cause --------------------------------------------------------------------------------

def _labelled(campaign):
    frame = ingest.detector_frame(ingest.read_logger(campaign["out"]))
    m, _ = ingest.read_markers(campaign["out"] / "markers.csv")
    joined, _ = ingest.join_logsheets(m, campaign["out"] / "logsheets")
    tests = ingest.controlled_tests(joined)
    return frame, events.label(frame, ingest.excluded_windows(joined), tests), tests


def test_switch_off_nothing_knows_about_load_off(campaign):
    assert config.LOADOFF_CLASS_ENABLED is False and metrics.causes() == metrics.CAUSES
    frame, ev, tests = _labelled(campaign)
    assert "ctl_load" not in set(tests["origin"]) and "ctl_mesh" in set(tests["origin"])
    assert list(ev.columns) == events.COLUMNS and "cause_rule" not in ev
    assert set(ev["cause"]) <= set(metrics.CAUSES) and "ctl_load" not in set(ev["origin"])


def test_switch_on_load_off_rises_get_their_own_origin_and_cause(campaign, monkeypatch):
    monkeypatch.setattr(config, "LOADOFF_CLASS_ENABLED", True)
    assert metrics.causes() == metrics.CAUSES + ["load_off"]
    frame, ev, tests = _labelled(campaign)
    assert (tests["origin"] == "ctl_load").sum() == len(campaign["tests"])
    lo = ev[ev["origin"] == "ctl_load"]
    spikes = lo[(lo["class"] == "spike") & lo["valid"]]
    assert len(spikes) >= 0.6 * len(campaign["tests"])          # 4.8 degC in 10 min was put in;
    # some tests fall into a cooling module or merge with a natural rise and give no spike
    assert (lo["cause"] == "load_off").all() and (ev.loc[ev["origin"] != "ctl_load", "cause"] != "load_off").all()
    assert list(ev.columns) == events.COLUMNS + ["cause_rule"]
    # the irradiance rule itself, kept: most load-off rises have no irradiance step
    assert (spikes["cause_rule"] == "non_irradiance").mean() >= 0.7
    assert set(ev["cause_rule"]) <= set(metrics.CAUSES)
    for _, s in spikes.iterrows():                 # the rise reaches 3 degC while the load is off
        t_th = pd.Timestamp(s["t_theta"])
        assert ((tests["on"] <= t_th) & (t_th <= tests["off"] + pd.Timedelta(minutes=2))).any()
    # the rule is checked against the known cause, not against the new label
    sens = events.cause_sensitivity(ev)
    assert sens["known"].iloc[0] >= len(spikes)
    # scoring counts them: per cause and in the totals
    al = frame["t_local"].isin(pd.to_datetime(spikes["t_theta"])).to_numpy()
    m, fa, _ = metrics.score(frame["t_local"].to_numpy(), al, ev)
    table = metrics.per_day(m, fa, sorted(frame["date"].unique()))
    s = metrics.summarise(table, m)
    assert s["n_load_off"] == len(spikes) and s["recall_load_off"] == 1.0
    assert s["spikes"] == int(((ev["class"] == "spike") & ev["valid"]).sum())
    assert "load_off" in report.CAUSE_STYLE


# --- the verdict, with and without ---------------------------------------------------------------------

def _row(detector, spikes, non, mixed, load_off, fa=0.07, recall=1.0, lead=3.8, left=0.92, d=None):
    out = {"budget_FA_per_day": B, "detector": detector, "setting": "x", "test_FA_per_day": fa,
           "test_recall": recall, "test_lead_median_min": lead, "test_remaining_median": left,
           "test_spikes": spikes, "test_n_irradiance": spikes - non - mixed - load_off,
           "test_n_non_irradiance": non, "test_n_mixed": mixed, "test_n_load_off": load_off}
    if d is not None:
        out.update(dict(zip(["nasim_minus_delay", "nasim_minus_delay_lo", "nasim_minus_delay_hi"], d)))
        out.update({"nasim_minus_recall": 0.0, "nasim_minus_recall_lo": -0.1, "nasim_minus_recall_hi": 0.1})
    return out


def _table(spikes, non, mixed, load_off):
    return pd.DataFrame([
        _row("Threshold (tuned)", spikes, non, mixed, load_off, fa=0.18, lead=0.3, left=0.56, d=(-4.1, -5.2, -2.9)),
        _row("CUSUM", spikes, non, mixed, load_off, recall=0.85, lead=2.3, left=0.84, d=(-0.1, -1.0, 0.8)),
        {"budget_FA_per_day": B, "detector": "Irradiance step", "setting": float("nan")},
        _row("NASIM-GBT", spikes, non, mixed, load_off)])


def test_load_off_spikes_count_as_not_irradiance_driven_only_when_they_are_in_the_table():
    # 24 test spikes: 14 irradiance, 3 non, 2 mixed, 5 load-off -> 10 not irradiance-driven
    v = verdict.evaluate(_table(24, 3, 2, 5), train_spikes=60)
    assert v["precondition"][0] and "not irradiance-driven 10" in v["precondition"][1]
    assert v["outcome"] == "holds_distinctive"
    plain = _table(24, 3, 2, 0).drop(columns="test_n_load_off")  # the table of a run with the switch off
    assert not verdict.evaluate(plain, train_spikes=60)["precondition"][0]   # 5 < 8


def test_verdict_is_given_with_and_without_and_the_two_can_differ():
    with_lo = _table(24, 3, 2, 5)                   # precondition met only thanks to the load-off spikes
    without = _table(19, 3, 2, 0)                   # 19 test spikes, 5 not irradiance-driven
    counts = {"train": 12, "val": 4, "test": 5}
    both = verdict.with_and_without(with_lo, 60, without, 48, counts)
    assert both["with_load_off"]["outcome"] == "holds_distinctive"
    assert both["without_load_off"]["outcome"] == "not_tested"
    assert both["load_off_spikes"] == counts
    assert "training spikes 48 (need >= 50)" in both["without_load_off"]["precondition"][1]
    assert "test spikes 19 (need >= 20)" in both["without_load_off"]["precondition"][1]
    text = "\n".join(verdict.markdown_both(both, synthetic=True))
    assert "### With load-off spikes" in text and "### Without load-off spikes" in text
    assert "12 on the training days, 4 on the validation days, 5 on the test days" in text
    assert "NOT decided" in text and "The two outcomes differ" in text and "SYNTHETIC DATA" in text
    assert text.count("- Precondition:") == 2
    same = verdict.with_and_without(with_lo, 60, _table(21, 6, 3, 0), 55, counts)
    assert same["without_load_off"]["outcome"] == "holds_distinctive"
    assert "The two outcomes are the same." in "\n".join(verdict.markdown_both(same))
    json.dumps(both, default=str)                   # it goes into verdict.json


# --- in the pipeline -------------------------------------------------------------------------------------

def test_pipeline_reports_both_verdicts_when_the_switch_is_on(campaign, tmp_path, monkeypatch):
    quiet = dict(seed=5, n_boot=30, synthetic=True, log=lambda *a: None)
    off = pipeline.run(campaign["out"], tmp_path / "off", **quiet)
    assert "outcome" in off["verdict"] and "with_load_off" not in off["verdict"]
    assert not (tmp_path / "off" / "detection_matched_without_load_off.csv").exists()
    assert "cause_rule" not in pd.read_csv(tmp_path / "off" / "events.csv")

    monkeypatch.setattr(config, "LOADOFF_CLASS_ENABLED", True)
    on = pipeline.run(campaign["out"], tmp_path / "on", **quiet)
    ev, det, both = on["events"], on["detection"], on["verdict"]
    lo = (ev["class"] == "spike") & ev["valid"] & (ev["cause"] == "load_off")
    per_part = {k: int((lo & ev["date_local"].isin(v)).sum()) for k, v in det["parts"].items()}
    assert both["load_off_spikes"] == per_part and per_part["test"] >= 2 and sum(per_part.values()) >= 12
    assert json.loads((tmp_path / "on" / "verdict.json").read_text())["load_off_spikes"] == per_part
    assert set(both) == {"with_load_off", "without_load_off", "load_off_spikes", "note"}

    # without = the same settings, the load-off spikes neutral
    w = pd.read_csv(tmp_path / "on" / "detection_matched_without_load_off.csv")
    m = det["matched"]
    assert list(w["detector"]) == list(m["detector"]) and list(w["budget_FA_per_day"]) == list(m["budget_FA_per_day"])
    has = m["setting"].notna().to_numpy()
    assert (w.loc[has, "setting"].astype(str) == m.loc[has, "setting"].astype(str)).all()
    assert (m.loc[has, "test_spikes"] - w.loc[has, "test_spikes"] == per_part["test"]).all()
    assert (w.loc[has, "test_n_load_off"] == 0).all() and (m.loc[has, "test_n_load_off"] == per_part["test"]).all()
    assert (w.loc[has, "test_FP"] <= m.loc[has, "test_FP"]).all()   # an alarm in a neutral rise is no false alarm
    for name in ("with_load_off", "without_load_off"):
        assert both[name]["outcome"] in ("holds_distinctive", "holds_not_distinctive",
                                         "does_not_hold", "not_tested")
    # 10 days are far too few for the real limits; with limits these days can meet, the
    # precondition is met with the load-off spikes and not without them
    nasim = m[(m["detector"] == "NASIM-GBT") & ((m["budget_FA_per_day"] - B).abs() < 1e-9)].iloc[0]
    if isinstance(nasim["setting"], str):
        n_with = int(nasim["test_spikes"])
        monkeypatch.setattr(config, "PREREG_MIN_TRAIN_SPIKES", 1)
        monkeypatch.setattr(config, "PREREG_MIN_TEST_NON_IRR", 1)
        monkeypatch.setattr(config, "PREREG_MIN_TEST_SPIKES", n_with)
        again = verdict.with_and_without(m, det["spikes_per_part"]["train"], w,
                                         det["spikes_per_part"]["train"] - per_part["train"], per_part)
        assert again["with_load_off"]["precondition"][0]
        assert not again["without_load_off"]["precondition"][0]

    text = (tmp_path / "on" / "summary.md").read_text(encoding="utf-8")
    assert "## Verdict on the detector claim, with and without load-off spikes" in text
    assert "### Without load-off spikes" in text
    assert "## Verdict on the detector claim (criteria fixed in advance" not in text
    events_csv = pd.read_csv(tmp_path / "on" / "events.csv")
    assert "cause_rule" in events_csv and (events_csv["cause"] == "load_off").sum() == int(
        (ev["cause"] == "load_off").sum())
    t3 = pd.read_csv(tmp_path / "on" / "table3_per_cause.csv")
    assert "load_off" in set(t3["cause"].dropna()) and len(t3) == 4 * len(report.DETECTORS)
    t1 = pd.read_csv(tmp_path / "on" / "table1_spike_statistics.csv")
    assert "load_off" in set(t1["cause"])
    t6 = pd.read_csv(tmp_path / "on" / "table6_cause_check.csv")       # the rule against known causes
    assert "ctl_load" in set(t6["known cause"]) and list(t6.columns)[1:] == metrics.CAUSES
    # nothing else moved: the spikes themselves are the same rises in both runs
    assert len(on["events"]) == len(off["events"])
    assert (on["events"]["start"] == off["events"]["start"]).all()


def test_rescoring_with_nothing_left_out_gives_the_same_table(campaign, tmp_path):
    res = pipeline.run(campaign["out"], tmp_path, seed=5, n_boot=30, synthetic=True, log=lambda *a: None)
    frame, ev, det = res["frame"], res["events"], res["detection"]
    m, _ = ingest.read_markers(campaign["out"] / "markers.csv")
    excluded = ingest.excluded_windows(ingest.join_logsheets(m, campaign["out"] / "logsheets")[0])
    again = detection.matched_rescored(det, frame, ev, excluded, seed=5, n_boot=30)
    pd.testing.assert_frame_equal(again, det["matched"])
