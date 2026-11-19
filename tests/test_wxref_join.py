"""wxref/join.py and wxref/analysis.py: model columns joined by time, never into a detector."""
import numpy as np
import pandas as pd
import pytest

import config
from analysis import fake_logger, ingest, powerbi
from data import synth
from tests.wx_fixtures import FakeTransport
from wxref import join
from wxref import openmeteo

T0 = int(pd.Timestamp("2026-11-16 09:00", tz="UTC").timestamp())


def _hourly(**cols):
    h = pd.DataFrame({"t_utc": [T0, T0 + 3600, T0 + 7200]})
    for c, v in cols.items():
        h[c] = v
    return h


def test_instant_values_interpolate_and_hour_means_belong_to_the_hour_they_end():
    h = _hourly(wx_model_wind_speed_10m_ms=[1.0, 3.0, np.nan], wx_model_poa_wm2=[100.0, 200.0, 300.0])
    rows = pd.DataFrame({"t_utc": [T0, T0 + 1800, T0 + 3600, T0 + 3610, T0 + 5400, T0 + 9000],
                         "wind": np.nan})
    out = join.add_model_columns(rows, h)
    # next to a missing hourly value (and outside the model's hours) there is no value
    assert list(out["wx_model_wind_speed_10m_ms"]) == pytest.approx(
        [1.0, 2.0, 3.0, np.nan, np.nan, np.nan], nan_ok=True)
    # radiation: (t-1h, t] -> the stamp t; T0 itself ends the hour stamped T0
    assert list(out["wx_model_poa_wm2"]) == pytest.approx([100.0, 200.0, 200.0, 300.0, 300.0,
                                                           np.nan], nan_ok=True)
    assert out["wind"].isna().all()                            # the logger's column untouched


def test_wind_direction_interpolates_across_north():
    h = _hourly(wx_model_wind_dir_10m_deg=[350.0, 10.0, 10.0])
    out = join.add_model_columns(pd.DataFrame({"t_utc": [T0 + 1800]}), h)
    assert out["wx_model_wind_dir_10m_deg"].iloc[0] in (0.0, 360.0)


def test_only_model_columns_can_be_joined_and_datetimes_work():
    h = _hourly(wx_model_temp_2m_c=[20.0, 22.0, 24.0])
    with pytest.raises(ValueError):
        join.add_model_columns(pd.DataFrame({"t_utc": [T0]}), h, columns=["wind"])
    rows = pd.DataFrame({"t_utc": pd.to_datetime([T0 + 1800], unit="s", utc=True)})
    assert join.add_model_columns(rows, h)["wx_model_temp_2m_c"].iloc[0] == pytest.approx(21.0)


@pytest.fixture(scope="module")
def rig(tmp_path_factory):
    root = tmp_path_factory.mktemp("wxrig")
    days, _ = synth.generate(seed=3, n_days=3, hard=False)       # 2026-11-15..17 local
    fake_logger.write_logger_files(days, root / "raw")
    return root / "raw"


def _model_on(monkeypatch, tmp_path, transport):
    monkeypatch.setattr(config, "WX_SITE_LAT", 21.42)
    monkeypatch.setattr(config, "WX_SITE_LON", 39.83)
    monkeypatch.setattr(config, "WX_DIR", str(tmp_path / "wxref"))
    monkeypatch.setattr(openmeteo, "urllib_transport", transport)


def test_powerbi_weather_columns_add_to_the_tables_and_change_nothing_else(rig, tmp_path,
                                                                         monkeypatch):
    off = powerbi.run(rig, tmp_path / "off", log=lambda *a: None)
    fake = FakeTransport()
    _model_on(monkeypatch, tmp_path, fake)
    on = powerbi.run(rig, tmp_path / "on", log=lambda *a: None, weather=True)
    assert fake.urls and all("open-meteo.com" in u for u in fake.urls)
    assert set(on) == set(off) | {"wx_model_hourly"}
    for name in off:
        cols = list(off[name].columns)
        pd.testing.assert_frame_equal(on[name][cols], off[name])  # the old columns, as before
    ds = on["daily_summary"].set_index("date")
    new = [c for c in ds.columns if c not in off["daily_summary"].columns]
    assert new and all(c.startswith(("wx_model_", "pyr20_")) for c in new)
    assert np.isfinite(ds.loc["2026-11-16", "wx_model_poa_kWh_m2"])
    assert np.isfinite(ds.loc["2026-11-16", "wx_model_mean_wind_10m_ms"])
    hourly = pd.read_csv(tmp_path / "on" / "wx_model_hourly.csv")
    assert {"source", "date", "time_local", "wx_model_poa_wm2", "measured_poa_wm2"} <= set(hourly)
    assert not (tmp_path / "off" / "wx_model_hourly.csv").exists()
    assert sorted(p.name for p in rig.iterdir()) == sorted(                     # logger files
        p.name for p in rig.iterdir() if p.suffix == ".csv")                      # untouched
    assert (tmp_path / "wxref" / "cache").is_dir()


def test_model_wind_reaches_analysis_and_the_twin_but_not_the_wind_column(rig, tmp_path,
                                                                        monkeypatch):
    from twin import params, validate
    from wxref import analysis as wxa
    _model_on(monkeypatch, tmp_path, FakeTransport())
    frame = ingest.read_logger(rig)
    joined = wxa.frame_with_model(frame, wxa.model_for(frame, log=lambda *a: None))
    assert joined["wind"].isna().all() and frame["wind"].isna().all()
    joined = joined[joined["date"].isin(["2026-11-16", "2026-11-17"])]   # the fixture's days
    assert joined["wx_model_wind_speed_10m_ms"].notna().mean() > 0.9
    res = validate.run(joined, tmp_path / "twin", params.load(None, None), "test",
                       log=lambda *a: None, wind_column=validate.MODEL_WIND)
    assert res["has_wind"]
    assert "MODEL wind at 10 m" in (tmp_path / "twin" / "report.md").read_text(encoding="utf-8")
