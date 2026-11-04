"""First-order thermal model of the PV module (lumped capacitance + Faiman loss).

    C dT/dt = A * (G - U(v) * (T - Ta)) - q_cool,     U(v) = u0 + u1 * v

T      module temperature, degC        G   in-plane irradiance, W/m2
Ta     ambient temperature, degC       v   wind speed, m/s
C      heat capacity, J/K              A   module area, m2
q_cool heat removed by cooling, W (0 when the pump is off)

As in pvlib's Faiman model, G is the full in-plane irradiance; the absorbed
fraction and the electrical output are folded into U0 and U1. With q_cool = 0
the steady state is exactly pvlib's Faiman temperature T = Ta + G / U.

Two derived quantities explain all the behaviour:
  steady state   T_ss = Ta + (A*G - q_cool) / (A*U)
  time constant  tau  = C / (A*U)        (~6.3 min at 1.5 m/s with config.py values)

Discretisation: inputs are held constant over one sample (10 s), so the
equation is solved exactly over the step:
  T[k+1] = T_ss + (T[k] - T_ss) * exp(-dt / tau)
This is stable for any dt and is two lines of C on the ESP32.
"""
import math

import numpy as np

import config


def faiman_u(v, u0=config.FAIMAN_U0, u1=config.FAIMAN_U1):
    """Heat-loss coefficient U(v) in W/(m2 K)."""
    return u0 + u1 * v


def time_constant(v, c=config.HEAT_CAPACITY_J_K, area=config.AREA_M2):
    """Thermal time constant tau = C / (A U) in seconds."""
    return c / (area * faiman_u(v))


def steady_state(g, ta, v, q_cool=0.0, area=config.AREA_M2):
    """Temperature the module settles at for constant inputs, degC."""
    return ta + (area * g - q_cool) / (area * faiman_u(v))


def step(t, g, ta, v, q_cool=0.0, dt=config.DT_S):
    """Advance the module temperature by one sample of length dt (exact)."""
    t_ss = steady_state(g, ta, v, q_cool)
    return t_ss + (t - t_ss) * math.exp(-dt / time_constant(v))


def simulate(t0, g, ta, v, q_cool=None, dt=config.DT_S):
    """Run the model over input arrays (one value per sample).

    Returns module temperature at each sample; out[0] = t0, and out[k+1]
    is the result of holding inputs k for one step.
    """
    g, ta, v = (np.asarray(x, dtype=float) for x in (g, ta, v))
    q = np.zeros_like(g) if q_cool is None else np.asarray(q_cool, dtype=float)
    out = np.empty(len(g))
    out[0] = t0
    for k in range(len(g) - 1):
        out[k + 1] = step(out[k], g[k], ta[k], v[k], q[k], dt)
    return out
