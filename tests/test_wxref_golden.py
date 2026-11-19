"""Golden test of the weather-reference switch (tests/golden_wx_off.py).

Switch OFF (the default): every output the weather reference touches -- Power BI tables,
the pipeline's text outputs, the twin validation, the SCADA historian, its HTTP answers,
the heartbeat -- is byte-identical to what main gave before wxref/ existed.

Switch ON: only the outputs that are meant to change do (new model files, new columns of
daily_summary, a summary.md section, one heartbeat line); every detector output, the twin
and the measured tables stay byte-identical, so no model value reached a detector."""
import pytest

import config
from tests import golden_wx_off
from tests.wx_fixtures import SITE, FakeTransport
from wxref import openmeteo

same_platform = pytest.mark.skipif(
    golden_wx_off.recorded_on() != golden_wx_off.fingerprint(),
    reason=f"golden hashes recorded on {golden_wx_off.recorded_on()}; this machine is "
           f"{golden_wx_off.fingerprint()} -- see tests/golden_wx_off.py to record for it")

CHANGED_WHEN_ON = {"powerbi/daily_summary.csv", "powerbi/wx_model_hourly.csv",
                   "pipeline/summary.md", "pipeline/wx_model_hourly.csv",
                   "pipeline/wx_daily_check.csv", "scada/heartbeat.txt"}


@same_platform
def test_switch_off_every_output_is_byte_identical_to_main(tmp_path):
    assert config.WX_ENABLED is False                     # the default stays off
    got = golden_wx_off.collect(tmp_path)
    assert golden_wx_off.differences(got) == []


@same_platform
def test_switch_on_changes_only_the_weather_outputs(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WX_ENABLED", True)
    monkeypatch.setattr(config, "WX_SITE_LAT", SITE.lat)
    monkeypatch.setattr(config, "WX_SITE_LON", SITE.lon)
    monkeypatch.setattr(config, "WX_DIR", str(tmp_path / "wxref"))
    monkeypatch.setattr(openmeteo, "urllib_transport", FakeTransport())   # no live calls
    got = golden_wx_off.collect(tmp_path / "run")
    diff = set(golden_wx_off.differences(got))
    assert diff == CHANGED_WHEN_ON
    assert b"PYR20/model check" in got["scada/heartbeat.txt"]
    assert b"Weather reference - Open-Meteo MODEL data" in got["pipeline/summary.md"]
