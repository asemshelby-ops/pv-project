"""twin/electrical.py: single-diode module model and series strings, checked against pvlib."""
import numpy as np
import pvlib
import pytest

from twin import electrical as el
from twin import params

P = params.load(None, None)


def _pvlib_params(g, t):
    return pvlib.pvsystem.calcparams_desoto(
        g, t, alpha_sc=P.alpha_sc_rel * P.i_l_ref, a_ref=P.a_ref, I_L_ref=P.i_l_ref,
        I_o_ref=P.i_o_ref, R_sh_ref=P.r_sh_ref, R_s=P.r_s, EgRef=P.eg_ref, dEgdT=P.d_eg_dt)


@pytest.mark.parametrize("g,t", [(1000, 25), (800, 45), (400, 60), (150, 30), (30, 20)])
def test_same_equations_and_same_mpp_as_pvlib(g, t):
    ref = _pvlib_params(g, t)
    assert np.allclose(el.desoto(P, g, t), ref, rtol=1e-12)
    sd = pvlib.pvsystem.singlediode(*ref)
    p, v, i = el.module_mpp(P, g, t)
    assert p == pytest.approx(sd["p_mp"], rel=1e-4)
    assert v * i == pytest.approx(p)
    assert v == pytest.approx(sd["v_mp"], rel=5e-3)


def test_voltage_at_a_current_matches_pvlib():
    ref = _pvlib_params(900.0, 50.0)
    i = np.linspace(0.0, 0.999 * ref[0], 50)
    assert np.allclose(el.v_from_i(i, *el.desoto(P, 900.0, 50.0)),
                       pvlib.pvsystem.v_from_i(i, *ref), atol=1e-6)


def test_placeholders_reproduce_the_seller_listing():
    s = el.stc(P)
    assert s["i_sc"] == pytest.approx(5.88, abs=0.005)
    assert s["v_oc"] == pytest.approx(21.5, abs=0.01)
    assert s["p_mp"] == pytest.approx(99.9, abs=0.05)       # 18 V x 5.55 A
    assert s["v_mp"] == pytest.approx(18.0, abs=0.05)
    assert s["i_mp"] == pytest.approx(5.55, abs=0.02)


def test_power_falls_with_temperature_and_rises_with_irradiance():
    assert el.module_mpp(P, 1000, 60)[0] < el.module_mpp(P, 1000, 30)[0]
    assert el.module_mpp(P, 500, 40)[0] < el.module_mpp(P, 900, 40)[0]
    g = el.gamma_pmp(P)
    assert -0.006 < g < -0.003          # a crystalline-silicon order of magnitude, model value


def test_dark_module_gives_zero_and_arrays_keep_their_shape():
    p, v, i = el.module_mpp(P, np.array([[0.0, 0.5], [600.0, 900.0]]), 40.0)
    assert p.shape == (2, 2)
    assert p[0, 0] == 0 and p[0, 1] == 0 and p[1, 1] > p[1, 0] > 0


def _brute(il, io, rs, rsh, a, bypass):
    """String MPP from a 40 000-point current scan, one string at a time."""
    out = []
    for s in range(il.shape[0]):
        i = np.linspace(1e-3, il[s].max(), 40001)
        v = el.v_from_i(i[None, :], il[s][:, None], io[s][:, None], rs, rsh[s][:, None],
                        a[s][:, None], iterations=12)
        out.append((i * np.maximum(v, -bypass).sum(axis=0)).max())
    return np.array(out)


def _spread(g, seed):
    rng = np.random.default_rng(seed)
    soil = np.clip(rng.normal(0.97, 0.015, (6, 20)), 0.9, 1.0)
    t = 30 + g / 40 + rng.normal(0, 4, (6, 20))
    return soil, t, rng.normal(1, 0.01, (6, 20)), np.exp(rng.normal(0, 0.05, (6, 20)))


@pytest.mark.parametrize("g", [1000.0, 600.0, 200.0, 60.0])
def test_string_mpp_matches_a_brute_force_scan(g):
    soil, t, f_il, f_io = _spread(g, int(g))
    if g == 600.0:                      # one badly shaded module, two half-shaded, one cool string
        soil[0, 3], soil[1, 5], soil[1, 6] = 0.3, 0.6, 0.6
        t[2] -= 15
    args = el.desoto(P, g * soil, t, f_il, f_io)
    r = el.mpp_strings(*args, P.bypass_drop_v)
    assert np.allclose(r["p"], _brute(*args, P.bypass_drop_v), rtol=1e-4)
    assert np.allclose(r["p"], r["v"] * r["i"])


def test_identical_modules_in_series_add_their_power():
    il, io, rs, rsh, a = el.desoto(P, np.full((1, 20), 800.0), np.full((1, 20), 45.0))
    r = el.mpp_strings(il, io, rs, rsh, a, P.bypass_drop_v)
    assert r["p"][0] == pytest.approx(20 * float(el.module_mpp(P, 800.0, 45.0)[0]), rel=1e-4)
    assert r["bypassed"][0] == 0


def test_a_shaded_module_is_bypassed_and_costs_about_one_module():
    g = np.full((1, 20), 900.0)
    il, io, rs, rsh, a = el.desoto(P, g, np.full((1, 20), 50.0))
    clean = el.mpp_strings(il, io, rs, rsh, a, P.bypass_drop_v)["p"][0]
    g[0, 3] = 0.3 * 900.0
    il, io, rs, rsh, a = el.desoto(P, g, np.full((1, 20), 50.0))
    r = el.mpp_strings(il, io, rs, rsh, a, P.bypass_drop_v)
    assert r["bypassed"][0] == 1
    assert 0.93 * clean < r["p"][0] < 0.955 * clean     # 19 of 20 modules, minus the diode drop


def test_mismatch_loss_is_never_negative():
    """A string cannot give more than the sum of its modules' own maximum powers."""
    soil, t, f_il, f_io = _spread(900.0, 3)
    args = el.desoto(P, 900.0 * soil, t, f_il, f_io)
    string = el.mpp_strings(*args, P.bypass_drop_v)["p"]
    alone = [np.reshape(x, (-1, 1)) if np.ndim(x) else x for x in args]   # 120 strings of 1
    single = el.mpp_strings(*alone, P.bypass_drop_v)["p"].reshape(6, 20)
    assert (string <= single.sum(axis=1) + 1e-6).all()
    assert (string >= 0.97 * single.sum(axis=1)).all()      # small spread -> small loss


def test_all_dark_strings_give_zero():
    il, io, rs, rsh, a = el.desoto(P, np.full((2, 5), 1e-6), np.full((2, 5), 20.0))
    r = el.mpp_strings(il * 0, io, rs, rsh, a, 0.5)
    assert (r["p"] == 0).all() and (r["i"] == 0).all()
