"""Spray cooling, evaporation and the pump. SIMULATION, placeholder values.

Water side of one sprayed module (flow q in L/min, m = rho q / 60 in kg/s):

    f_wet = min(1, q / q_full)                       wetted share of the glass
    NTU   = U_mw A f_wet / (m c_p)                   U_mw: conductance module -> water film
    eps   = 1 - exp(-NTU)                            how far the water warms towards the module
    K_w   = eps m c_p                                W/K; heat to the water = K_w (T - T_w)

The film enters at the tank temperature T_w and warms towards the module temperature T, so
no more heat can leave than the water can carry (K_w <= m c_p) and no more than the
surface can pass (K_w <= U_mw A). Evaporation from the wetted glass (optional):

    T_film = T - (T - T_w) eps / NTU                 mean film temperature
    h_m    = (hc0 + hc1 v) / (rho_air c_p,air Le^(2/3))      Chilton-Colburn analogy
    m_evap = f_wet A h_m max(0, rho_sat(T_film) - RH rho_sat(Ta))
    q_evap = m_evap h_fg                             taken from the module

Pump: a table duty -> (flow per module, electrical power) of ONE pump unit feeding one
module's nozzles. The placeholder table is in twin/params.py; the measured R385 table
replaces it (read_pump_curve_csv). The rooftop multiplies it by the number of sprayed
modules (twin/rooftop.py).

Not modelled: the water film left on the glass after the spray stops, the optical effect
of water on the glass, the washing of dust, and the cooling of the returning water by
its own evaporation. See docs/twin/assumptions.md.
"""
import csv
import pathlib

import numpy as np

R_VAPOUR = 461.5        # J/(kg K), gas constant of water vapour


def sat_vapour_density(t_c):
    """Saturation density of water vapour, kg/m3. Magnus formula with the coefficients of
    Alduchov & Eskridge (J. Appl. Meteorol. 35, 1996): e_s = 610.94 exp(17.625 t /
    (t + 243.04)) Pa."""
    t = np.asarray(t_c, dtype=float)
    return 610.94 * np.exp(17.625 * t / (t + 243.04)) / (R_VAPOUR * (t + 273.15))


def pump_point(p, duty):
    """(flow per module in L/min, electrical power in W) of one pump unit at `duty`.
    Below pump_min_duty the pump does not run: (0, 0). Above the table: its last point."""
    if duty < p.pump_min_duty - 1e-9:
        return 0.0, 0.0
    d, q, w = np.asarray(p.pump_curve, dtype=float).T
    return float(np.interp(duty, d, q)), float(np.interp(duty, d, w))


def wetted_fraction(p, flow_lpm):
    return np.minimum(1.0, np.asarray(flow_lpm, dtype=float) / p.spray_full_wetting_lpm)


def cooling(p, t_mod, t_water, flow_lpm, ta, rh, wind):
    """Cooling of sprayed modules. flow_lpm per module (0 = not sprayed); arrays broadcast.
    rh in percent. Returns (K_w in W/K, q_evap in W, evaporated water in kg/s)."""
    t_mod, flow = np.broadcast_arrays(np.asarray(t_mod, dtype=float),
                                      np.asarray(flow_lpm, dtype=float))
    on = flow > 0
    m_cp = np.where(on, flow, 1.0) * p.water_rho_kg_per_l / 60.0 * p.water_cp
    f_wet = wetted_fraction(p, np.where(on, flow, 1.0))
    ntu = p.spray_u_module_water * p.area_m2 * f_wet / m_cp
    eps = -np.expm1(-ntu)
    k_w = np.where(on, eps * m_cp, 0.0)
    if not p.evap_enabled:
        zero = np.zeros(t_mod.shape)
        return k_w, zero, zero
    t_film = t_mod - (t_mod - t_water) * eps / ntu
    h_m = (p.evap_hc0 + p.evap_hc1 * np.asarray(wind, dtype=float)) / (
        p.air_rho * p.air_cp * p.lewis_number ** (2.0 / 3.0))
    drive = sat_vapour_density(t_film) - np.asarray(rh, dtype=float) / 100.0 \
        * sat_vapour_density(ta)
    m_evap = np.where(on, f_wet * p.area_m2 * h_m * np.maximum(drive, 0.0), 0.0)
    return k_w, m_evap * p.water_h_fg, m_evap


# --- the slot for the measured pump curve -----------------------------------------------------

PUMP_CSV_COLUMNS = ["duty_pct", "volume_l", "time_s", "voltage_v", "current_a"]


def read_pump_curve_csv(path):
    """The bench table of the bucket test (one line per run; lines starting with # are
    comments):

        duty_pct,volume_l,time_s,voltage_v,current_a
        100,1.50,60.0,12.05,0.61

    flow = volume / time, power = voltage x current; runs at the same duty are averaged.
    Returns (table for params.pump_curve, lowest duty that gave flow, a source note).
    A duty whose runs gave no water is taken as "the pump does not spray there"."""
    path = pathlib.Path(path)
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(line for line in f if not line.lstrip().startswith("#")))
    if not rows:
        raise ValueError(f"{path}: no data rows")
    missing = [c for c in PUMP_CSV_COLUMNS if c not in rows[0]]
    if missing:
        raise ValueError(f"{path}: missing column(s) {missing}")
    by_duty = {}
    for r in rows:
        duty = float(r["duty_pct"]) / 100.0
        time_s = float(r["time_s"])
        if not (0 < duty <= 1.0 and time_s > 0):
            raise ValueError(f"{path}: duty must be 0-100 % and time > 0 (got {r})")
        flow = float(r["volume_l"]) / time_s * 60.0
        by_duty.setdefault(round(duty, 4), []).append(
            (flow, float(r["voltage_v"]) * float(r["current_a"])))
    table = tuple((d, float(np.mean([x[0] for x in v])), float(np.mean([x[1] for x in v])))
                  for d, v in sorted(by_duty.items()) if np.mean([x[0] for x in v]) > 0)
    if len(table) < 2:
        raise ValueError(f"{path}: at least two duties with flow are needed")
    note = f"measured pump curve {path.name} ({len(rows)} runs, {len(table)} duties)"
    return table, table[0][0], note
