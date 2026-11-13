"""twin/thermal.py and twin/spray.py: the lumped thermal model, the spray term, the pump table."""
import numpy as np
import pytest

from sim import thermal as paper_thermal
from twin import params, spray, thermal

P = params.load(None, None)


def test_same_step_as_the_paper_model_without_spray():
    """sim/thermal.py is the model behind the synthetic data and the CUSUM detector."""
    t = 41.3
    for g, ta, v in [(900.0, 30.0, 1.5), (300.0, 24.0, 0.2), (0.0, 20.0, 4.0)]:
        new, _ = thermal.step(P, t, g, ta, v, 10.0)
        assert new == pytest.approx(paper_thermal.step(t, g, ta, v), abs=1e-12)
        assert thermal.steady_state(P, g, ta, v) == pytest.approx(
            paper_thermal.steady_state(g, ta, v))
    assert thermal.time_constant(P, 1.5) == pytest.approx(paper_thermal.time_constant(1.5))
    assert thermal.time_constant(P, 1.5) / 60 == pytest.approx(6.3, abs=0.1)   # Herteleer 2023


def test_simulate_equals_repeated_steps_and_the_paper_simulation():
    rng = np.random.default_rng(0)
    g, ta, v = 600 + 300 * rng.random(200), 25 + rng.random(200), 0.5 + 2 * rng.random(200)
    out = thermal.simulate(P, 30.0, g, ta, v, 10.0)
    assert np.allclose(out, paper_thermal.simulate(30.0, g, ta, v), atol=1e-10)


def test_the_step_is_exact_for_any_step_length():
    """One 60 s step = six 10 s steps when the inputs do not change."""
    t = 55.0
    for _ in range(6):
        t, _ = thermal.step(P, t, 800.0, 30.0, 1.0, 10.0, k_w=30.0, t_w=28.0, q_evap=100.0)
    once, _ = thermal.step(P, 55.0, 800.0, 30.0, 1.0, 60.0, k_w=30.0, t_w=28.0, q_evap=100.0)
    assert once == pytest.approx(t, abs=1e-10)


def test_step_conserves_energy():
    """Stored heat = absorbed - lost to air - taken by water - evaporated, over the step."""
    t0, g, ta, v, dt, k_w, t_w, q_e = 52.0, 850.0, 31.0, 1.2, 10.0, 34.0, 29.0, 150.0
    t1, t_mean = thermal.step(P, t0, g, ta, v, dt, k_w, t_w, q_e)
    stored = P.heat_capacity_j_per_k * (t1 - t0)
    flows = (P.area_m2 * g - P.area_m2 * thermal.u_loss(P, v) * (t_mean - ta)
             - k_w * (t_mean - t_w) - q_e) * dt
    assert stored == pytest.approx(flows, rel=1e-9)


def test_vectorised_over_modules():
    t = np.array([40.0, 50.0, 60.0])
    new, mean = thermal.step(P, t, 900.0, 30.0, np.array([0.5, 1.5, 3.0]), 10.0,
                             k_w=np.array([0.0, 30.0, 0.0]), t_w=28.0)
    assert new.shape == mean.shape == (3,)
    assert new[1] < 50.0          # the sprayed one cools


def test_pump_table_interpolation_and_minimum_duty():
    assert spray.pump_point(P, 1.0) == (1.75, 7.2)
    assert spray.pump_point(P, 0.5) == (0.875, 3.6)
    q, w = spray.pump_point(P, 0.625)
    assert (q, w) == (pytest.approx(1.09375), pytest.approx(4.5))
    assert spray.pump_point(P, 0.3) == (0.0, 0.0)      # below the minimum duty: not running
    assert spray.pump_point(P, 0.0) == (0.0, 0.0)


def test_spray_reproduces_the_working_estimate_it_was_set_from():
    """CLAUDE.md [EST]: 2 L/min warms by ~5 degC (~0.70 kW) on a module 20 degC above the water."""
    k_w, q_evap, m_evap = spray.cooling(P.replace(evap_enabled=0), 50.0, 30.0, 2.0, 30.0, 45.0, 1.5)
    assert float(k_w) * 20.0 == pytest.approx(700.0, rel=0.03)
    rise = float(k_w) * 20.0 / (2.0 * P.water_rho_kg_per_l / 60 * P.water_cp)
    assert rise == pytest.approx(5.0, abs=0.15)
    assert q_evap == 0 and m_evap == 0


def test_water_cannot_take_more_than_it_can_carry_or_the_surface_can_pass():
    for q in (0.2, 0.875, 1.75, 5.0, 50.0):
        k_w = float(spray.cooling(P, 50.0, 30.0, q, 30.0, 45.0, 1.5)[0])
        m_cp = q * P.water_rho_kg_per_l / 60 * P.water_cp
        assert 0 < k_w <= m_cp + 1e-9
        assert k_w <= P.spray_u_module_water * P.area_m2 + 1e-9
    # more flow never cools less
    k = [float(spray.cooling(P, 50.0, 30.0, q, 30.0, 45.0, 1.5)[0]) for q in (0.5, 1.0, 2.0, 4.0)]
    assert k == sorted(k)


def test_no_flow_means_no_cooling_and_no_water():
    k_w, q_e, m_e = spray.cooling(P, np.array([50.0, 50.0]), 30.0, np.array([0.0, 1.75]),
                                  30.0, 45.0, 1.5)
    assert (k_w[0], q_e[0], m_e[0]) == (0.0, 0.0, 0.0)
    assert k_w[1] > 0 and q_e[1] > 0 and m_e[1] > 0


def test_evaporation_grows_with_dry_air_and_wind_and_stops_in_saturated_cool_air():
    base = float(spray.cooling(P, 45.0, 30.0, 1.75, 30.0, 45.0, 1.5)[1])
    assert float(spray.cooling(P, 45.0, 30.0, 1.75, 30.0, 15.0, 1.5)[1]) > base
    assert float(spray.cooling(P, 45.0, 30.0, 1.75, 30.0, 45.0, 3.0)[1]) > base
    # film at 20 degC under saturated 30 degC air: vapour would condense -> clipped to zero
    assert float(spray.cooling(P, 20.0, 20.0, 1.75, 30.0, 100.0, 1.5)[1]) == 0.0
    q_e, m_e = (float(x) for x in spray.cooling(P, 45.0, 30.0, 1.75, 30.0, 45.0, 1.5)[1:])
    assert q_e == pytest.approx(m_e * P.water_h_fg)


def test_saturation_density_against_steam_table_values():
    # saturated vapour density: about 17.3 g/m3 at 20 degC and 30.4 g/m3 at 30 degC
    assert float(spray.sat_vapour_density(20.0)) == pytest.approx(0.0173, rel=0.02)
    assert float(spray.sat_vapour_density(30.0)) == pytest.approx(0.0304, rel=0.02)


def test_pump_csv_errors_are_reported(tmp_path):
    f = tmp_path / "p.csv"
    f.write_text("duty_pct,volume_l,time_s\n100,1,60\n")
    with pytest.raises(ValueError, match="missing column"):
        spray.read_pump_curve_csv(f)
    f.write_text("duty_pct,volume_l,time_s,voltage_v,current_a\n100,1.5,60,12,0.6\n")
    with pytest.raises(ValueError, match="at least two"):
        spray.read_pump_curve_csv(f)
    f.write_text("duty_pct,volume_l,time_s,voltage_v,current_a\n150,1.5,60,12,0.6\n")
    with pytest.raises(ValueError, match="duty"):
        spray.read_pump_curve_csv(f)
