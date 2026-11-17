"""Phase B analysis (analysis/phase_b.py) on SYNTHETIC rig files written by
analysis/fake_phase_b.py: reading the six columns and the spray log, the per-event
table, the comparison of the arms, the energy balance, the cleaning check and the audit.

Every number here is synthetic. The generator puts effects of a known size into the data
(a cooling per second of spraying, a pump power, a flow, a cleaning step), and the tests
check that the analysis finds them again. Nothing here says what a real spray does."""
import json

import numpy as np
import pandas as pd
import pytest

import config
from analysis import fake_logger, fake_phase_b, gamma, ingest, phase_b, pipeline
from data import synth
from data.schema import PHASE_B_COLUMNS

WINDOW_ROWS = config.PHASE_B_EVENT_WINDOW_MIN * 6


@pytest.fixture(scope="module")
def rig(tmp_path_factory):
    out = tmp_path_factory.mktemp("phase_b")
    days, _ = synth.generate(seed=5, n_days=8, hard=True)
    truth, rows = fake_phase_b.build(days, out)
    df = ingest.read_logger(out)
    sweeps = gamma.sweep_parameters(*gamma.read_sweeps(out))
    m, _ = ingest.read_markers(out / "markers.csv")
    excluded = ingest.excluded_windows(ingest.join_logsheets(m, out / "logsheets")[0])
    log = ingest.read_spray_log(out / "spray_log.csv")
    result = phase_b.run(df, None, log, sweeps, excluded, n_boot=500, n_perm=4000, seed=1)
    return {"out": out, "days": days, "truth": truth, "df": df, "sweeps": sweeps,
            "excluded": excluded, "log": log, "result": result}


# --- reading -----------------------------------------------------------------------------------

def test_the_six_columns_are_read_and_typed(rig):
    df = rig["df"]
    assert phase_b.has_columns(df) and set(PHASE_B_COLUMNS) <= set(df.columns)
    assert str(df["spike_id"].dtype) == "Int64" and str(df["spray_assign"].dtype) == "Int64"
    assert set(df["spray_assign"].unique()) == {-1, 0, 1}
    assert (df.loc[df["spike_id"] == 0, "spray_assign"] == -1).all()
    assert df["water_L_cum"].is_monotonic_increasing
    per_event = df[df["spike_id"] > 0].groupby("spike_id").size()
    assert (per_event == WINDOW_ROWS).all() and len(per_event) == len(rig["truth"])
    header = sorted(rig["out"].glob("2*.csv"))[0].read_text().splitlines()[0].split(",")
    assert header == fake_logger.HEADER + fake_logger.EXTRA + PHASE_B_COLUMNS   # after `status`


def test_a_file_without_the_columns_gets_none_of_them(rig, tmp_path):
    fake_logger.write_logger_files(rig["days"], tmp_path)
    df = ingest.read_logger(tmp_path)
    assert not set(PHASE_B_COLUMNS) & set(df.columns) and not phase_b.has_columns(df)
    assert len(phase_b.event_table(df)) == 0


def test_spray_log_gives_the_boot_settings_and_every_event(rig):
    log = rig["log"]
    boot = phase_b.boot_settings(log)[1]
    assert boot["seed"] == config.PHASE_B_SEED and boot["block"] == 4 and boot["dry_run"] == 0
    assert boot["duty"] == pytest.approx(config.PUMP_DUTY) and boot["max_ms"] == 60000
    assert pd.isna(log["t_utc"].iloc[0])                       # written before the clock was read
    opened = log[log["event"] == "opened"]
    assert list(opened["spike_id"]) == list(rig["truth"]["spike_id"])
    assert [int(d["assign"]) for d in opened["detail"]] == list(rig["truth"]["assign"])
    stopped = log[log["event"] == "stopped"].iloc[0]["detail"]
    assert stopped["reason"] == "max time" and float(stopped["litres"]) == pytest.approx(1.5)
    assert {"refused", "closed"} <= set(log["event"])
    assert (log[log["event"] == "refused"]["detail"].map(lambda d: d["reason"]) != "").all()
    assert len(ingest.read_spray_log(rig["out"] / "no_such_file.csv")) == 0


def test_day_file_that_changes_format_part_way_is_read_piece_by_piece(rig, tmp_path):
    """A day begun by a build without the columns: the Phase B build writes its header line
    again, then rows with six more fields."""
    old, new = tmp_path / "old", tmp_path / "new"
    fake_logger.write_logger_files(rig["days"], old)
    name = sorted(rig["out"].glob("2*.csv"))[0].name
    a = (old / name).read_text().splitlines()
    b = (rig["out"] / name).read_text().splitlines()
    new.mkdir()
    (new / name).write_text("\n".join(a[:2001] + [b[0]] + b[2001:]) + "\n")
    df = ingest.read_logger(new)
    assert len(df) == len(a) - 1                                # no row lost, no header kept
    assert df["spike_id"].iloc[:2000].isna().all() and df["spike_id"].iloc[2000:].notna().all()
    assert df["T1"].notna().all() and (df["time_source"] == "rtc").all()
    ev = phase_b.event_table(df)
    assert len(ev) >= 1 and (ev["t_alarm"] >= df["t_local"].iloc[2000]).all()


# --- the per-event table ---------------------------------------------------------------------------

def test_event_table_gives_back_what_the_generator_put_in(rig):
    ev, truth = rig["result"]["events"], rig["truth"]
    assert list(ev["spike_id"]) == list(truth["spike_id"])
    assert list(ev["assign"]) == list(truth["assign"]) and ev["assign_ok"].all()
    assert (ev["t_alarm"] == truth["t_alarm"]).all()
    assert (ev["n_rows"] == WINDOW_ROWS).all() and ev["valid"].all() and (ev["note"] == "").all()
    assert not ev["dry_run"].any() and ev["delivered"].all()
    spray, none = ev[ev["assign"] == 1], ev[ev["assign"] == 0]
    assert spray["spray_s"].tolist() == pytest.approx([config.SPRAY_MAX_S] * len(spray))
    assert (none["spray_s"] == 0).all() and (none["litres"] == 0).all()
    litres = fake_phase_b.SYN_FLOW_LPM * config.SPRAY_MAX_S / 60
    assert spray["litres"].to_numpy() == pytest.approx(litres, rel=0.02)
    offset_wh = fake_phase_b.SYN_PUMP_OFFSET_W * config.PHASE_B_EVENT_WINDOW_MIN / 60
    assert none["pump_Wh"].to_numpy() == pytest.approx(offset_wh, rel=1e-3)
    pump_wh = fake_phase_b.SYN_PUMP_W * config.SPRAY_MAX_S / 3600
    assert (spray["pump_Wh"] - offset_wh).to_numpy() == pytest.approx(pump_wh, rel=0.02)
    assert (spray["dT_after_spray_C"] < -2.0).all() and (none["dT_after_spray_C"] > -1.5).all()
    assert ev["yield_W_per_kWm2"].between(80, 100).all()       # ~100 W module, hot
    assert ev["rows_frac"].eq(1.0).all() and ev["baseline_frac"].eq(1.0).all()


def test_water_count_survives_a_restart_of_the_board():
    assert phase_b._litres([10.0, 10.5, 11.2]) == pytest.approx(1.2)
    assert phase_b._litres([10.0, 10.5, 0.3, 0.9]) == pytest.approx(0.5 + 0.3 + 0.6)   # fell to 0
    assert phase_b._litres([np.nan, 4.0]) == 0.0 and phase_b._litres([]) == 0.0


def test_an_event_that_cannot_be_compared_is_kept_out_and_says_why(rig):
    df, log = rig["df"], rig["log"]
    first, second, third = (int(k) for k in rig["truth"]["spike_id"].iloc[:3])
    # (a) the logged arm differs from the seeded sequence
    bad = df.copy()
    rows = bad["spike_id"] == first
    bad.loc[rows, "spray_assign"] = 1 - bad.loc[rows, "spray_assign"]
    # (b) the load tripped during the window: no power to compare
    rows = np.flatnonzero((bad["spike_id"] == second).to_numpy())[20:60]
    bad.loc[bad.index[rows], "load_mode"] = "trip"
    # (c) a cleaning was logged inside the window
    t3 = df.loc[df["spike_id"] == third, "t_local"].iloc[0]
    excluded = pd.DataFrame({"start": [t3 + pd.Timedelta(minutes=5)],
                             "end": [t3 + pd.Timedelta(minutes=45)], "reason": ["cleaning"]})
    ev = phase_b.event_table(bad, log, excluded).set_index("spike_id")
    assert not ev.at[first, "assign_ok"] and "allocation sequence" in ev.at[first, "note"]
    assert "incomplete" in ev.at[second, "note"] and ev.at[second, "rows_frac"] < 0.95
    assert ev.at[third, "note"] == "excluded: cleaning"
    assert not ev.loc[[first, second, third], "valid"].any() and ev["valid"].sum() == len(ev) - 3
    a = phase_b.audit(ev.reset_index())
    assert a["arm_differs_from_sequence"] == [first] and a["not_valid"] == 3
    # a number that never reached the card (power lost between the counter and the row)
    gone = phase_b.event_table(df[df["spike_id"] != second], log)
    assert phase_b.audit(gone)["numbers_never_logged"] == [second]


def test_spray_that_gave_no_water_stays_in_its_arm_but_is_reported(rig):
    df, log = rig["df"].copy(), rig["log"]
    k = int(rig["truth"].loc[rig["truth"]["assign"] == 1, "spike_id"].iloc[0])
    in_event = df["spike_id"] == k                               # that spray never happened
    df.loc[in_event, ["pump_duty", "flow_Lpm"]] = 0.0
    df.loc[in_event, "water_L_cum"] = df.loc[in_event, "water_L_cum"].iloc[0]
    df.loc[in_event, "P_pump_W"] = fake_phase_b.SYN_PUMP_OFFSET_W
    ev = phase_b.event_table(df, log)
    row = ev.set_index("spike_id").loc[k]
    assert row["assign"] == 1 and not row["delivered"] and row["valid"]
    assert k in set(phase_b.analysis_set(ev)["spike_id"])                    # as assigned
    assert k not in set(phase_b.analysis_set(ev, "as delivered")["spike_id"])
    assert phase_b.audit(ev)["not_delivered"] == [k]


# --- the two arms ------------------------------------------------------------------------------------

def test_arms_side_by_side_find_the_cooling_and_the_metered_quantities(rig):
    arms = rig["result"]["arms"].set_index("quantity")
    truth = rig["truth"]
    assert arms.at["litres", "spray_n"] == int((truth["assign"] == 1).sum())
    assert arms.at["litres", "no_spray_n"] == int((truth["assign"] == 0).sum())
    assert abs(arms.at["litres", "spray_n"] - arms.at["litres", "no_spray_n"]) <= 2   # blocks of 4
    cool = arms.loc["dT_after_spray_C"]
    assert cool["hi"] < -2.5 and cool["p_permutation"] < 0.01    # ~4.5 degC put in, less re-heating
    assert arms.at["litres", "difference"] == pytest.approx(1.5, rel=0.02)
    assert arms.at["pump_Wh", "difference"] == pytest.approx(20.0 * 60 / 3600, rel=0.02)
    assert arms.at["spray_s", "difference"] == pytest.approx(60.0)
    for q in ("yield_W_per_kWm2", "gain_rel"):
        assert arms.at[q, "lo"] < arms.at[q, "difference"] < arms.at[q, "hi"]
    # each event against its own baseline: the same effect with less spread
    rel_width = lambda q, scale: (arms.at[q, "hi"] - arms.at[q, "lo"]) / scale     # noqa: E731
    assert rel_width("gain_rel", 1.0) < rel_width("yield_W_per_kWm2", arms.at["yield_W_per_kWm2", "no_spray_mean"])


def test_energy_balance_finds_the_gain_the_generator_put_in(rig):
    bal = rig["result"]["balance"].set_index("estimate")
    truth = rig["truth"]
    true_gain = truth.loc[truth["assign"] == 1, "true_gain_Wh"].mean()
    assert 0.15 < true_gain < 0.6                               # the synthetic spray: a fraction of a Wh
    row = bal.loc["relative to each event's baseline"]
    assert row["gain_lo"] < true_gain < row["gain_hi"]
    assert row["pump_Wh"] == pytest.approx(20.0 * 60 / 3600, rel=0.02)   # the offset has cancelled
    assert row["net_Wh"] == pytest.approx(row["gain_Wh"] - row["pump_Wh"])
    assert row["litres"] == pytest.approx(1.5, rel=0.02)
    assert row["L_per_kWh_gross"] == pytest.approx(1000 * row["litres"] / row["gain_Wh"])
    if row["net_Wh"] > 0:
        assert row["L_per_kWh_net"] == pytest.approx(1000 * row["litres"] / row["net_Wh"])
    else:                                                       # nothing gained: nothing to divide by
        assert np.isnan(row["L_per_kWh_net"]) and "net energy not above zero" in row["note"]
    plain = bal.loc["normalized energy"]
    assert plain["gain_lo"] < true_gain < plain["gain_hi"]
    assert (row["gain_hi"] - row["gain_lo"]) < (plain["gain_hi"] - plain["gain_lo"])


def test_dry_run_is_an_a_a_test_and_shows_no_effect(tmp_path):
    """Dry run: arms are drawn, nothing is sprayed. The comparison must find nothing."""
    days, _ = synth.generate(seed=7, n_days=8, hard=True)
    truth, _ = fake_phase_b.build(days, tmp_path, dry_run=True, sweeps=False)
    df = ingest.read_logger(tmp_path)
    log = ingest.read_spray_log(tmp_path / "spray_log.csv")
    ev = phase_b.event_table(df, log)
    assert (ev["spike_id"] < 0).all() and ev["dry_run"].all() and ev["assign_ok"].all()
    assert set(ev["assign"]) == {0, 1} and (ev["litres"] == 0).all() and (ev["spray_s"] == 0).all()
    assert len(phase_b.analysis_set(ev)) == 0                   # never mixed with wet events
    a = phase_b.audit(ev)
    assert a["dry_run"] == len(ev) and a["wet"] == 0
    aa = ev.assign(dry_run=False)                               # compare the two arms anyway
    arms = phase_b.arm_comparison(aa, n_perm=4000).set_index("quantity")
    for q in ("gain_rel", "dT_after_spray_C", "yield_W_per_kWm2"):
        assert arms.at[q, "lo"] < 0 < arms.at[q, "hi"], q       # no difference between the arms
        assert arms.at[q, "p_permutation"] > 0.05, q
    assert phase_b.spray_windows(ev).empty                      # nothing to exclude


def test_permutation_test_and_welch_interval_on_known_numbers():
    rng = np.random.default_rng(0)
    assign = np.array([1, 1, 0, 0] * 6)
    blocks = np.repeat(np.arange(6), 4)
    noise = rng.normal(0, 1, 24)
    assert phase_b.permutation_p(noise + 4.0 * assign, assign, blocks, 5000) < 0.001
    assert phase_b.permutation_p(noise, assign, blocks, 5000) > 0.05
    assert np.isnan(phase_b.permutation_p(noise, np.ones(24, int), blocks))
    d, se, dof, lo, hi = phase_b.welch([1.0, 2.0, 3.0, 4.0], [0.0, 1.0, 2.0, 3.0])
    assert d == 1.0 and se == pytest.approx(np.sqrt(2 * (5 / 3) / 4)) and dof == pytest.approx(6.0)
    assert lo == pytest.approx(1.0 - 2.4469 * se, abs=1e-3) and hi == pytest.approx(1.0 + 2.4469 * se, abs=1e-3)
    assert np.isnan(phase_b.welch([1.0], [2.0, 3.0])[1])


# --- cleaning or cooling ----------------------------------------------------------------------------------

def test_cleaning_check_separates_cleaner_glass_from_cooling(tmp_path):
    days, _ = synth.generate(seed=11, n_days=8, hard=True)
    results = {}
    for name, gain in (("cleaned", 0.02), ("cooled only", 0.0)):
        out = tmp_path / name
        truth, _ = fake_phase_b.build(days, out, clean_gain=gain)
        df = ingest.read_logger(out)
        sweeps = gamma.sweep_parameters(*gamma.read_sweeps(out))
        ev = phase_b.event_table(df, ingest.read_spray_log(out / "spray_log.csv"))
        clean = phase_b.cleaning_check(ev, sweeps)
        results[name] = (clean, phase_b.cleaning_summary(clean, ev).set_index("change of I_sc/G"), ev)
    late = "last sweep of the window (module re-heated, glass dry)"
    clean, summary, ev = results["cleaned"]
    assert (clean["note"] == "").mean() > 0.9 and len(clean) == len(ev)
    # 2 % put in at the first spray of a day; later sprays of that day find clean glass
    assert summary.at[late, "lo"] > 0 and 0.003 < summary.at[late, "difference"] < 0.02
    assert abs(summary.at[late, "no_spray_mean"]) < 0.005       # no water: I_sc / G stays
    _, summary0, _ = results["cooled only"]
    assert summary0.at[late, "lo"] < 0 < summary0.at[late, "hi"]  # cooling alone: no change
    assert abs(summary0.at[late, "difference"]) < 0.004
    # the sweeps are picked by the alarm time and config.py, never by the arm
    span = pd.Timedelta(minutes=config.CLEAN_SWEEP_WINDOW_MIN)
    s = gamma.usable_sweeps(sweeps).set_index("sweep_id")["t_local"]
    ok = clean[clean["note"] == ""]
    before = s.loc[ok["sweep_before"].astype("int64")].to_numpy()
    after = s.loc[ok["sweep_after"].astype("int64")].to_numpy()
    late_t = s.loc[ok["sweep_late"].astype("int64")].to_numpy()
    t0 = ok["t_alarm"].to_numpy()
    assert ((t0 - before) >= np.timedelta64(0, "s")).all() and ((t0 - before) <= span.to_timedelta64()).all()
    assert ((after - t0) >= np.timedelta64(config.SPRAY_MAX_S, "s")).all()
    end = t0 + np.timedelta64(config.PHASE_B_EVENT_WINDOW_MIN * 60, "s")
    assert (late_t < end).all() and (late_t > after).all()


def test_cleaning_check_without_sweeps_says_so(rig):
    ev = rig["result"]["events"]
    clean = phase_b.cleaning_check(ev, pd.DataFrame())
    assert len(clean) == len(ev) and clean["note"].str.startswith("no usable sweep").all()
    assert clean["d_isc_g_late"].isna().all()


# --- in the pipeline -------------------------------------------------------------------------------------

def test_pipeline_runs_the_trial_only_when_its_switch_is_on(rig, tmp_path, monkeypatch):
    quiet = dict(seed=5, n_boot=20, synthetic=True, log=lambda *a: None)
    off = pipeline.run(rig["out"], tmp_path / "off", **quiet)
    assert off["phase_b"] is None and not list((tmp_path / "off").glob("phase_b_*"))
    assert "Phase B" not in (tmp_path / "off" / "summary.md").read_text(encoding="utf-8")

    monkeypatch.setattr(config, "PHASE_B_ENABLED", True)
    on = pipeline.run(rig["out"], tmp_path / "on", **quiet)
    names = {p.name for p in (tmp_path / "on").glob("phase_b_*")}
    assert names == {f"phase_b_{n}.csv" for n in ("events", "arms", "arms_delivered", "balance",
                                                   "cleaning", "cleaning_summary")} | {"phase_b_audit.json"}
    text = (tmp_path / "on" / "summary.md").read_text(encoding="utf-8")
    assert "## Phase B - randomized spray trial (NOT ADOPTED" in text and "SYNTHETIC DATA" in text
    assert "wet spray events are excluded from the spike statistics" in text
    audit = json.loads((tmp_path / "on" / "phase_b_audit.json").read_text())
    assert audit["wet"] == len(rig["truth"]) and audit["arm_differs_from_sequence"] == []
    # the re-heating after a spray is not counted as a spike
    n_sprays = int((rig["truth"]["assign"] == 1).sum())
    ev_on, ev_off = on["events"], off["events"]
    assert (ev_on["note"] == "excluded: spray event").sum() >= n_sprays // 2
    assert (ev_off["note"] == "excluded: spray event").sum() == 0
    assert len(pd.read_csv(tmp_path / "on" / "phase_b_events.csv")) == len(rig["truth"])


# --- the held-out week [PROPOSAL, docs/paper/03 sec. 12] ------------------------------------------

def test_no_week_is_named_by_default_and_then_nothing_is_held_out(rig):
    assert config.PHASE_B_HELDOUT_WEEK is None and config.PHASE_B_HELDOUT_UNLOCKED is False
    r = rig["result"]
    assert not r["events"]["heldout"].any() and "arms_heldout" not in r
    assert r["audit"]["heldout_week"] is None and r["audit"]["heldout_locked"] is False
    assert len(phase_b.analysis_set(r["events"], part="all")) == len(phase_b.analysis_set(r["events"]))


def test_a_locked_week_is_counted_and_shown_in_no_table(rig, monkeypatch, tmp_path):
    days = sorted(rig["result"]["events"]["date_local"].unique())
    week = (days[-3], days[-1])
    open_run = rig["result"]
    monkeypatch.setattr(config, "PHASE_B_HELDOUT_WEEK", week)
    r = phase_b.run(rig["df"], tmp_path, rig["log"], rig["sweeps"], rig["excluded"],
                    n_boot=500, n_perm=4000, seed=1)
    inside = open_run["events"]["date_local"].between(*week)
    assert 0 < inside.sum() < len(inside)
    assert r["audit"]["heldout_week"] == list(week) and r["audit"]["heldout_locked"] is True
    assert r["audit"]["heldout_events"] == int(inside.sum())
    assert r["audit"]["events"] == len(open_run["events"])                # counted ...
    kept = set(open_run["events"].loc[~inside, "spike_id"])
    assert set(r["events"]["spike_id"]) == kept                           # ... and shown nowhere
    assert set(r["cleaning"]["spike_id"]) == kept
    assert set(pd.read_csv(tmp_path / "phase_b_events.csv")["spike_id"]) == kept
    assert set(pd.read_csv(tmp_path / "phase_b_cleaning.csv")["spike_id"]) == kept
    assert not list(tmp_path.glob("*heldout*"))
    valid = open_run["events"][open_run["events"]["valid"] & ~inside]
    row = r["arms"].set_index("quantity").loc["gain_rel"]
    assert row["spray_n"] == (valid["assign"] == 1).sum() and row["no_spray_n"] == (valid["assign"] == 0).sum()
    assert row["spray_n"] < open_run["arms"].set_index("quantity").loc["gain_rel", "spray_n"]
    text = "\n".join(phase_b.markdown(r, synthetic=True))
    assert "LOCKED" in text and "held-out week alone" not in text
    # the functions refuse too, also on a table that still holds the rows
    full = phase_b.event_table(rig["df"], rig["log"], rig["excluded"])
    assert full["heldout"].sum() == inside.sum()
    for part in ("heldout", "all"):
        with pytest.raises(PermissionError):
            phase_b.analysis_set(full, part=part)
        with pytest.raises(PermissionError):
            phase_b.arm_comparison(full, n_perm=200, part=part)
    assert set(phase_b.analysis_set(full)["spike_id"]) <= kept


def test_a_week_that_is_not_written_as_two_dates_in_order_is_refused(rig, monkeypatch):
    """The dates are compared as text, so a date in another form would silently match nothing."""
    for bad in (("2026-11-9", "2026-11-15"), ("09/11/2026", "15/11/2026"), ("2026-11-20", "2026-11-14")):
        monkeypatch.setattr(config, "PHASE_B_HELDOUT_WEEK", bad)
        with pytest.raises(ValueError):
            phase_b.event_table(rig["df"], rig["log"], rig["excluded"])
        with pytest.raises(ValueError):
            phase_b.in_heldout_week(["2026-11-15"])
    monkeypatch.setattr(config, "PHASE_B_HELDOUT_WEEK", ("2026-11-14", "2026-11-20"))
    assert list(phase_b.in_heldout_week(["2026-11-13", "2026-11-14", "2026-11-20", "2026-11-21"])) == \
        [False, True, True, False]


def test_unlocking_adds_the_week_as_its_own_table_and_changes_nothing_else(rig, monkeypatch, tmp_path):
    days = sorted(rig["result"]["events"]["date_local"].unique())
    monkeypatch.setattr(config, "PHASE_B_HELDOUT_WEEK", (days[-3], days[-1]))
    args = (rig["df"], None, rig["log"], rig["sweeps"], rig["excluded"])
    locked = phase_b.run(*args, n_boot=500, n_perm=4000, seed=1)
    monkeypatch.setattr(config, "PHASE_B_HELDOUT_UNLOCKED", True)
    r = phase_b.run(rig["df"], tmp_path, *args[2:], n_boot=500, n_perm=4000, seed=1)
    pd.testing.assert_frame_equal(r["arms"], locked["arms"])              # the other weeks: as before
    pd.testing.assert_frame_equal(r["balance"], locked["balance"])
    held = r["events"][r["events"]["heldout"] & r["events"]["valid"]]
    assert len(r["events"]) == r["audit"]["events"] and r["audit"]["heldout_locked"] is False
    row = r["arms_heldout"].set_index("quantity").loc["gain_rel"]
    assert row["spray_n"] == (held["assign"] == 1).sum() and row["no_spray_n"] == (held["assign"] == 0).sum()
    assert row["spray_n"] + row["no_spray_n"] == len(held) > 0
    assert (tmp_path / "phase_b_arms_heldout.csv").exists() and (tmp_path / "phase_b_balance_heldout.csv").exists()
    both = phase_b.analysis_set(r["events"], part="all")
    assert len(both) == len(held) + len(phase_b.analysis_set(r["events"]))
    text = "\n".join(phase_b.markdown(r, synthetic=True))
    assert "UNLOCKED" in text and "held-out week alone" in text
