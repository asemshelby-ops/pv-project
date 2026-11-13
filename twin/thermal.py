"""Thermal model of the module: one lumped heat capacity with the Faiman loss term and,
when a module is sprayed, the heat taken by the water. SIMULATION, placeholder values.

    C dT/dt = A G - A U(v) (T - Ta) - K_w (T - T_w) - q_evap        U(v) = u0 + u1 v

T     module (back-sheet) temperature, degC      G    in-plane irradiance, W/m2
Ta    ambient temperature, degC                  v    wind speed at the module, m/s
K_w   conductance module -> spray water, W/K     T_w  temperature of the sprayed water
q_evap  heat taken by evaporation, W (twin/spray.py); K_w = q_evap = 0 without spray

It is the equation of sim/thermal.py (the model behind the synthetic data and the CUSUM
detector) with the spray written as a conductance, in vectorised form for 120 modules.
tests/test_twin_thermal.py checks that both give the same number without spray.

Inputs are held constant over one step, so the step is solved exactly:
    T_ss = (A G + A U Ta + K_w T_w - q_evap) / (A U + K_w)      tau = C / (A U + K_w)
    T(t + dt) = T_ss + (T(t) - T_ss) exp(-dt / tau)
The mean temperature over the step is returned too: the energy that went into the water
is K_w * (T_mean - T_w) * dt, which keeps the tank balance exact.
"""
import numpy as np


def u_loss(p, wind):
    """Faiman heat-loss coefficient, W/(m2 K)."""
    return p.u0 + p.u1 * np.asarray(wind, dtype=float)


def time_constant(p, wind, k_w=0.0):
    """Thermal time constant in seconds."""
    return p.heat_capacity_j_per_k / (p.area_m2 * u_loss(p, wind) + k_w)


def steady_state(p, g, ta, wind, k_w=0.0, t_w=0.0, q_evap=0.0):
    au = p.area_m2 * u_loss(p, wind)
    return (p.area_m2 * g + au * ta + k_w * t_w - q_evap) / (au + k_w)


def step(p, t, g, ta, wind, dt, k_w=0.0, t_w=0.0, q_evap=0.0):
    """Advance by dt seconds. Returns (temperature at the end, mean over the step)."""
    k = p.area_m2 * u_loss(p, wind) + k_w
    t_ss = (p.area_m2 * g + (k - k_w) * ta + k_w * t_w - q_evap) / k
    x = dt * k / p.heat_capacity_j_per_k
    e = np.exp(-x)
    return t_ss + (t - t_ss) * e, t_ss + (t - t_ss) * (1.0 - e) / x


def simulate(p, t0, g, ta, wind, dt):
    """Uncooled module over input arrays. dt: scalar or one value per step (seconds from
    sample k to k + 1; len(g) - 1 values). out[0] = t0 and out[k + 1] follows from holding
    the inputs of sample k for dt[k]."""
    g, ta, wind = (np.asarray(x, dtype=float) for x in (g, ta, wind))
    n = len(g)
    dt = np.broadcast_to(np.asarray(dt, dtype=float), (max(n - 1, 0),))
    k = p.area_m2 * u_loss(p, wind)
    t_ss = ta + p.area_m2 * g / k
    e = np.exp(-dt * k[:-1] / p.heat_capacity_j_per_k)
    out = np.empty(n)
    out[0] = t = t0
    for i in range(n - 1):
        t = t_ss[i] + (t - t_ss[i]) * e[i]
        out[i + 1] = t
    return out
