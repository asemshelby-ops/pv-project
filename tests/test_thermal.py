"""Checks of sim/thermal.py against the physics numbers recorded in CLAUDE.md."""
import numpy as np
import pytest

from sim import thermal

DT = 10.0


def test_steady_rise_wind_2_to_1_at_900():
    # CLAUDE.md: wind 2 -> 1 m/s at 900 W/m2 gives ~5 degC steady rise
    rise = thermal.steady_state(900, 30, 1.0) - thermal.steady_state(900, 30, 2.0)
    assert rise == pytest.approx(5.0, abs=0.1)


def test_simulated_lull_reaches_steady_rise_and_partial_rises():
    # Settle at 2 m/s, then drop to 1 m/s; CLAUDE.md: ~3 degC in 6 min, ~4 in 10 min
    n = int(3 * 3600 / DT)
    g, ta = np.full(n, 900.0), np.full(n, 30.0)
    v = np.where(np.arange(n) < n // 2, 2.0, 1.0)
    t = thermal.simulate(thermal.steady_state(900, 30, 2.0), g, ta, v)
    k0 = n // 2  # first sample whose step uses v = 1
    base = t[k0]
    assert t[k0 + 36] - base == pytest.approx(3.0, abs=0.2)  # 6 min
    assert t[k0 + 60] - base == pytest.approx(4.0, abs=0.25)  # 10 min
    assert t[-1] - base == pytest.approx(5.0, abs=0.1)


def test_time_constant_about_6_3_min():
    # Herteleer et al. 2023: 6.3 +/- 1 min; our C was derived at ~1.5 m/s
    assert thermal.time_constant(1.5) / 60 == pytest.approx(6.3, abs=0.1)


def test_step_response_reaches_63_percent_at_tau():
    v = 1.5
    tau = thermal.time_constant(v)
    n = int(4 * tau / DT)
    t = thermal.simulate(30.0, np.full(n, 900.0), np.full(n, 30.0), np.full(n, v))
    final = thermal.steady_state(900, 30, v)
    k = np.argmax(t - 30.0 >= 0.632 * (final - 30.0))
    assert k * DT == pytest.approx(tau, abs=DT)


def test_exact_step_matches_fine_euler():
    # Exact exponential step vs explicit Euler at 0.1 s: same physics
    t_exact = thermal.step(40.0, 800, 28, 1.2, q_cool=100.0, dt=DT)
    t = 40.0
    c, a, u = 8700.0, 0.648, thermal.faiman_u(1.2)
    for _ in range(100):
        t += 0.1 * (a * (800 - u * (t - 28)) - 100.0) / c
    assert t_exact == pytest.approx(t, abs=1e-3)


def test_cooling_power_to_hold_10_degc_lower():
    # PROJECT_STATE sec.2: holding the module 10 degC cooler at 1.5 m/s needs ~230 W
    a, u = 0.648, thermal.faiman_u(1.5)
    q = a * u * 10.0
    drop = thermal.steady_state(900, 30, 1.5) - thermal.steady_state(900, 30, 1.5, q)
    assert drop == pytest.approx(10.0)
    assert q == pytest.approx(230, abs=5)
