"""twin/params.py: the one parameter file, its tags, and the overlay that replaces placeholders."""
import json

import pytest

import config
from twin import params


def test_every_parameter_has_value_unit_tag_and_source():
    for name, m in params.PARAMS.items():
        assert m.tag in (params.LIT, params.SCN, params.CONST), name
        assert m.unit and m.source, name
        if m.tag == params.LIT:                    # a placeholder says where it is from and
            assert len(m.source) > 20, name        # how it is replaced
            assert m.measure, f"{name}: no 'to replace it, measure'"


def test_nothing_is_tagged_measured_in_the_file():
    assert not [n for n, m in params.PARAMS.items() if m.tag == params.MEASURED]
    p = params.load(None, None)
    assert set(p.placeholders()) == {n for n, m in params.PARAMS.items() if m.tag == params.LIT}
    assert len(p.placeholders()) >= 25


def test_no_measured_overlay_is_committed():
    """Until real rig files exist, the twin must run on placeholders only."""
    assert not params.MEASURED_PATH.exists()
    assert not params.PUMP_CURVE_PATH.exists()


def test_values_shared_with_config_are_equal():
    p = params.load(None, None)
    assert p.area_m2 == config.AREA_M2
    assert p.heat_capacity_j_per_k == config.HEAT_CAPACITY_J_K
    assert (p.u0, p.u1) == (config.FAIMAN_U0, config.FAIMAN_U1)
    assert p.cell_minus_back_c == config.CELL_MINUS_BACK_C
    assert p.wind_fallback_ms == config.CUSUM_WIND_CONST
    assert p.dt_s == config.DT_S
    assert (p.sensor_noise_c, p.sensor_offset_sd_c, p.sensor_lsb_c) == (
        config.DS18B20_NOISE_C, config.DS18B20_OFFSET_C, config.DS18B20_LSB_C)


def test_overlay_replaces_values_without_code_change(tmp_path):
    f = tmp_path / "params_measured.json"
    f.write_text(json.dumps({"params": {
        "u0": {"value": 31.5, "tag": params.MEASURED, "source": "test"},
        "pump_curve": {"value": [[0.4, 0.5, 2.0], [1.0, 1.2, 6.0]], "tag": params.MEASURED,
                       "source": "test"}}}))
    p = params.load(f, None)
    assert p.u0 == 31.5 and p.meta["u0"].tag == params.MEASURED
    assert p.pump_curve == ((0.4, 0.5, 2.0), (1.0, 1.2, 6.0))
    assert "u0" not in p.placeholders() and "u1" in p.placeholders()
    assert p.meta["u0"].unit == params.PARAMS["u0"].unit       # unit and group are kept
    assert params.PARAMS["u0"].value == 25.0                   # the file itself is untouched


def test_overlay_with_unknown_name_is_refused(tmp_path):
    f = tmp_path / "x.json"
    f.write_text(json.dumps({"params": {"not_a_parameter": {"value": 1}}}))
    with pytest.raises(KeyError):
        params.load(f, None)


def test_params_are_read_only_and_replace_copies():
    p = params.load(None, None)
    with pytest.raises(AttributeError):
        p.u0 = 1.0
    q = p.replace(u0=30.0)
    assert (p.u0, q.u0) == (25.0, 30.0) and q.meta["u0"].tag == "[OVERRIDE]"
    with pytest.raises(KeyError):
        p.replace(no_such=1)
    with pytest.raises(AttributeError):
        p.no_such


def test_measured_pump_curve_file_is_the_slot(tmp_path):
    f = tmp_path / "pump.csv"
    f.write_text("# comment\nduty_pct,volume_l,time_s,voltage_v,current_a\n"
                 "100,1.5,60,12.0,0.60\n100,1.7,60,12.0,0.62\n60,0.8,60,7.2,0.5\n30,0,60,3.6,0.2\n")
    p = params.load(None, f)
    assert p.meta["pump_curve"].tag == params.MEASURED
    assert p.pump_curve == ((0.6, pytest.approx(0.8), pytest.approx(3.6)),
                            (1.0, pytest.approx(1.6), pytest.approx(7.32)))
    assert p.pump_min_duty == 0.6          # 30 % gave no water


def test_the_template_is_not_a_measurement():
    """The template holds no usable curve: loading it as the slot must fail, not run."""
    from twin import spray
    template = params.PUMP_CURVE_PATH.with_name("pump_curve_R385_TEMPLATE.csv")
    assert template.exists()
    with pytest.raises(ValueError):
        spray.read_pump_curve_csv(template)


def test_generated_parameter_document_is_current():
    assert params.DOC_PATH.read_text(encoding="utf-8") == params.parameters_markdown()
