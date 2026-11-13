"""Electrical model of the module and of a series string. SIMULATION, placeholder values.

Single-diode model with the temperature and irradiance scaling of De Soto et al. (2006):

    I = I_L - I_0 (exp((V + I R_s) / a) - 1) - (V + I R_s) / R_sh

    a    = a_ref * Tk / Tref                         Tk = cell temperature in kelvin
    I_L  = S / 1000 * (I_L,ref + alpha_sc (Tk - Tref))        S = effective irradiance
    I_0  = I_0,ref (Tk / Tref)^3 exp(Eg_ref / (k Tref) - Eg / (k Tk))
    Eg   = Eg_ref (1 + dEg/dT (Tk - Tref))
    R_sh = R_sh,ref * 1000 / S                       R_s constant

The same equations as pvlib.pvsystem.calcparams_desoto (tests compare the two); they are
written out here because the rooftop needs them for 120 modules at every 10 s step.

A string is modules in series: one current, voltages add. A module that cannot carry the
string current is clamped at -bypass_drop_v by its bypass diode (one diode per module
is assumed). Each string has its own ideal maximum-power-point tracker.
"""
import numpy as np

K_EV = 8.617332478e-05      # Boltzmann constant, eV/K (the value pvlib uses)
T_REF_K = 298.15
G_REF = 1000.0
G_DARK = 1.0                # W/m2: below this a module delivers nothing
N_COARSE, N_FINE = 32, 13   # current grid of the string MPP search: against a 40 000-point
                            # search the power differs by < 1e-4 (tests/test_twin_electrical.py)


def desoto(p, g_eff, t_cell, f_il=1.0, f_io=1.0):
    """Single-diode parameters (I_L, I_0, R_s, R_sh, a) at effective irradiance g_eff
    (W/m2) and cell temperature t_cell (degC). f_il, f_io: per-module mismatch factors.
    Arrays broadcast."""
    g = np.maximum(np.asarray(g_eff, dtype=float), 1e-6)
    tk = np.asarray(t_cell, dtype=float) + 273.15
    a = p.a_ref * tk / T_REF_K
    eg = p.eg_ref * (1.0 + p.d_eg_dt * (tk - T_REF_K))
    alpha_sc = p.alpha_sc_rel * p.i_l_ref
    il = g / G_REF * (p.i_l_ref * f_il + alpha_sc * (tk - T_REF_K))
    io = (p.i_o_ref * f_io * (tk / T_REF_K) ** 3
          * np.exp(p.eg_ref / (K_EV * T_REF_K) - eg / (K_EV * tk)))
    rsh = p.r_sh_ref * G_REF / g
    return il, io, p.r_s, rsh, a


def v_from_i(i, il, io, rs, rsh, a, iterations=6):
    """Module voltage at current i (no bypass diode). Newton on x = V + i R_s, started at
    the solution for R_sh = infinity, which lies on the side from which Newton converges
    monotonically (the function is concave and falling)."""
    net = il - i
    x = np.where(net > 0, a * np.log1p(np.maximum(net, 0.0) / io), net * rsh)
    for _ in range(iterations):
        e = io * np.exp(np.minimum(x / a, 200.0))
        x = x - (net - e + io - x / rsh) / (-e / a - 1.0 / rsh)
    return x - i * rs


def _string_power(i, il, io, rs, rsh, a, bypass_v):
    """i: (..., K) string currents; module arrays (..., M). Returns the string voltage
    (..., K) and how many modules are bypassed (..., K)."""
    v = v_from_i(i[..., None, :], il[..., None], io[..., None], rs, rsh[..., None],
                 a[..., None], iterations=4)
    bypassed = v < -bypass_v
    return np.where(bypassed, -bypass_v, v).sum(axis=-2), bypassed.sum(axis=-2)


def mpp_strings(il, io, rs, rsh, a, bypass_v):
    """Maximum power point of series strings. Module arrays have shape (S, M): S strings
    of M modules. Returns a dict of arrays (S,): p (W), v (V), i (A), bypassed (count).
    Search: a coarse grid of the string current between half the weakest and the strongest
    photocurrent, then a fine grid around the best coarse point."""
    il, io, rsh, a = (np.broadcast_to(np.asarray(x, dtype=float), np.shape(il)).copy()
                      for x in (il, io, rsh, a))
    s = il.shape[0]
    lo, hi = 0.5 * il.min(axis=1), il.max(axis=1)
    out = {k: np.zeros(s) for k in ("p", "v", "i")}
    out["bypassed"] = np.zeros(s, dtype=int)
    lit = hi > 1e-6
    if not lit.any():
        return out
    frac = np.linspace(0.0, 1.0, N_COARSE)
    i1 = lo[:, None] + (hi - lo)[:, None] * frac
    v1, _ = _string_power(i1, il, io, rs, rsh, a, bypass_v)
    best = np.argmax(i1 * v1, axis=1)
    rows = np.arange(s)
    step = (hi - lo) / (N_COARSE - 1)
    centre = i1[rows, best]
    i2 = centre[:, None] + step[:, None] * np.linspace(-1.0, 1.0, N_FINE)
    i2 = np.clip(i2, 1e-9, None)
    v2, byp = _string_power(i2, il, io, rs, rsh, a, bypass_v)
    p2 = i2 * v2
    k = np.argmax(p2, axis=1)
    out["p"] = np.where(lit, np.maximum(p2[rows, k], 0.0), 0.0)
    out["v"] = np.where(lit, v2[rows, k], 0.0)
    out["i"] = np.where(lit, i2[rows, k], 0.0)
    out["bypassed"] = np.where(lit, byp[rows, k], 0)
    return out


def module_mpp(p, g_eff, t_cell):
    """Maximum power point of ONE module at each (g_eff, t_cell). Returns (p, v, i) with
    the broadcast shape of the inputs; zero below G_DARK."""
    g, t = np.broadcast_arrays(np.asarray(g_eff, dtype=float), np.asarray(t_cell, dtype=float))
    shape = g.shape
    g, t = g.reshape(-1, 1), t.reshape(-1, 1)
    il, io, rs, rsh, a = desoto(p, np.maximum(g, G_DARK), t)
    r = mpp_strings(il, io, rs, rsh, a, p.bypass_drop_v)
    dark = g[:, 0] < G_DARK
    return tuple(np.where(dark, 0.0, r[k]).reshape(shape) for k in ("p", "v", "i"))


def gamma_pmp(p, g=1000.0, t_lo=25.0, t_hi=65.0):
    """Relative temperature coefficient of P_mp of the MODEL, 1/degC: slope of a straight
    line through P_mp(T_cell) between t_lo and t_hi, divided by its value at 25 degC (the
    definition of analysis/gamma.py). A property of the placeholder parameters, not of
    our module."""
    t = np.linspace(t_lo, t_hi, 9)
    slope, at25 = np.polyfit(t - 25.0, module_mpp(p, g, t)[0], 1)
    return float(slope / at25)


def stc(p):
    """P_mp, V_mp, I_mp, V_oc, I_sc of the model at 1000 W/m2 and 25 degC."""
    il, io, rs, rsh, a = desoto(p, G_REF, 25.0)
    pm, vm, im = (float(x) for x in module_mpp(p, G_REF, 25.0))
    return {"p_mp": pm, "v_mp": vm, "i_mp": im,
            "v_oc": float(v_from_i(0.0, il, io, rs, rsh, a)),
            "i_sc": float(_i_at_v0(il, io, rs, rsh, a))}


def _i_at_v0(il, io, rs, rsh, a):
    """Short-circuit current: solve V(I) = 0 by bisection (used for reporting only)."""
    lo, hi = 0.0, float(il)
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if v_from_i(mid, il, io, rs, rsh, a) > 0:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)
