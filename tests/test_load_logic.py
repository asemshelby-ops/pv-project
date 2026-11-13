"""The measurement-load logic (firmware/libraries/PvDaq/src/pv_load_logic.c), compiled on
the PC and run against a simulated module: the mode machine, the sweep (points, timing,
hand-over), the hold loop and every fault path. Test list: docs/paper/07 section 9.

The module is SYNTHETIC: the De Soto curve of analysis/fake_logger.py (seller listing,
assumed coefficients) feeding a 2000 uF capacitor [EST: the bank is not bought] and the
two 1.5 ohm branches as R / D. The INA226 is modelled as the mean of V and I over its
conversion window. Passing here shows that the logic is right, not that the hardware
works: that is the bench procedure (firmware/tests/load_bench/BENCH.md)."""
import ctypes
import math
import pathlib
import re
import subprocess

import numpy as np
import pvlib
import pytest

from analysis import fake_logger
from export import run as ex

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "firmware/libraries/PvDaq/src"
OFF, HOLD, SWEEP, TRIP = 0, 1, 2, 3
REQ_HOLD, REQ_SWEEP = 1, 2
TRIP_HOT, TRIP_SENSOR, TRIP_OVERCURRENT = 1, 2, 3
G_UNSTABLE, MAX_AT_EDGE, NON_MONOTONIC, LOW_LIGHT, INA_ERROR, NOT_SETTLED = 1, 2, 4, 8, 16, 32
C_BANK = 2000e-6            # F [EST, CLAUDE.md: C >= 2000 uF]
T_CONV_SWEEP_MS = 9         # INA226: 2 x 1.1 ms x 4 averages = 8.8 ms (TI datasheet)
T_CONV_HOLD_MS = 141        # 2 x 1.1 ms x 64 averages
ROW_MS = 10000

pytestmark = pytest.mark.skipif(ex.find_gcc() is None, reason="no gcc")


def firmware_constants():
    text = (SRC / "pv_config.h").read_text()
    found = re.findall(r"constexpr \w+ (\w+) = ([-\d.]+)f?;", text)
    return {name: float(value) for name, value in found}


K = firmware_constants()


class Cfg(ctypes.Structure):          # mirrors load_cfg_t (pv_load_logic.h), same order
    _fields_ = [("pwm_bits", ctypes.c_uint32), ("n_coarse", ctypes.c_uint32),
                ("n_fine", ctypes.c_uint32), ("d_min", ctypes.c_float),
                ("t_settle_ms", ctypes.c_uint32), ("sweep_conv_ms", ctypes.c_uint32),
                ("settle_tol", ctypes.c_float), ("sweep_budget_ms", ctypes.c_uint32),
                ("voc_settle_ms", ctypes.c_uint32),
                ("sweep_period_ms", ctypes.c_uint32), ("gamma_period_ms", ctypes.c_uint32),
                ("sweep_guard_ms", ctypes.c_uint32), ("hold_period_ms", ctypes.c_uint32),
                ("hold_gain", ctypes.c_float), ("hold_max_step", ctypes.c_float),
                ("hold_d_min", ctypes.c_float), ("vset_fraction", ctypes.c_float),
                ("g_on", ctypes.c_float), ("g_off", ctypes.c_float),
                ("g_off_ms", ctypes.c_uint32), ("voc_on", ctypes.c_float),
                ("t_trip_c", ctypes.c_float), ("i_trip_a", ctypes.c_float),
                ("trip_clear_ms", ctypes.c_uint32), ("ina_fail_limit", ctypes.c_uint32),
                ("g_unstable", ctypes.c_float), ("mono_tol", ctypes.c_float)]


def firmware_cfg(**changes):
    """The configuration the board uses: every `c.field = CONSTANT;` line of pvLoadConfig()
    in pv_load.cpp, with the constants of pv_config.h."""
    body = re.search(r"load_cfg_t pvLoadConfig\(\) \{(.*?)\n\}", (SRC / "pv_load.cpp").read_text(),
                     re.S).group(1)
    lines = re.findall(r"c\.(\w+) = (\w+)( \* 1000)?;", body)
    assert sorted(f for f, _, _ in lines) == sorted(f for f, _ in Cfg._fields_)   # none missing
    c = Cfg()
    for field, constant, to_ms in lines:
        value = K[constant] * (1000 if to_ms else 1)
        setattr(c, field, value if dict(Cfg._fields_)[field] is ctypes.c_float else int(value))
    for name, value in changes.items():
        setattr(c, name, value)
    return c


@pytest.fixture(scope="session")
def lib(tmp_path_factory):
    dll = tmp_path_factory.mktemp("load") / "pv_load_logic.dll"
    subprocess.run([ex.find_gcc(), "-std=c99", "-O2", "-Wall", "-Wextra", "-Werror", "-shared",
                    "-static-libgcc", str(SRC / "pv_load_logic.c"), "-o", str(dll), "-lm"],
                   check=True)
    so = ctypes.CDLL(str(dll))
    u32, f32, ptr = ctypes.c_uint32, ctypes.c_float, ctypes.c_void_p
    so.load_init.argtypes = [ptr, ctypes.POINTER(Cfg), u32]
    so.load_service.argtypes = [ptr, u32, f32, f32]
    so.load_service.restype = u32
    so.load_reading.argtypes = [ptr, u32, f32, f32, ctypes.c_int]
    so.load_sweep_start.argtypes = [ptr, u32, u32, f32]
    so.load_sweep_request.argtypes = [ptr, u32]
    so.load_sweep_close.argtypes = [ptr, f32]
    so.load_sweep_close.restype = u32
    so.load_gamma.argtypes = [ptr, u32, u32]
    so.load_manual_off.argtypes = [ptr, u32, ctypes.c_int]
    so.load_counts.argtypes = [ptr, f32]
    so.load_counts.restype = u32
    so.load_get.argtypes = [ptr, ctypes.c_char_p]
    so.load_get.restype = ctypes.c_double
    so.load_sweep_point.argtypes = [ptr, u32, ctypes.c_int]
    so.load_sweep_point.restype = ctypes.c_double
    for f in (so.load_mode_text, so.load_trip_text):
        f.argtypes, f.restype = [u32], ctypes.c_char_p
    so.load_sizeof.restype = so.load_cfg_sizeof.restype = u32
    assert so.load_cfg_sizeof() == ctypes.sizeof(Cfg)      # the mirror above is in step
    return so


class Module:
    """I(V) of the synthetic module at one irradiance and cell temperature."""

    def __init__(self, g, t_cell):
        self.params = fake_logger.curve(fake_logger.module_model(), g, t_cell)
        sd = pvlib.pvsystem.singlediode(*self.params)
        self.p_mp, self.v_mp = float(sd["p_mp"]), float(sd["v_mp"])
        self.dv = float(sd["v_oc"]) * 1.05 / 4000
        self.i_tab = np.asarray(pvlib.pvsystem.i_from_v(
            np.arange(4001) * self.dv, *self.params), dtype=float).tolist()
        self.v_oc = self.dv * 4000
        self.v_oc = self.steady_v(0.0)
        self._steady = {}

    def i(self, v):
        x = min(max(v / self.dv, 0.0), 3999.999)
        k = int(x)
        return self.i_tab[k] + (self.i_tab[k + 1] - self.i_tab[k]) * (x - k)

    def slope(self, v):                                    # dI/dV, A/V (negative)
        k = int(min(max(v / self.dv, 0.0), 3999.0))
        return (self.i_tab[k + 1] - self.i_tab[k]) / self.dv

    def steady_v(self, conductance):                       # I(V) = V * conductance
        lo, hi = 0.0, self.v_oc
        for _ in range(40):
            mid = 0.5 * (lo + hi)
            lo, hi = (mid, hi) if self.i(mid) > mid * conductance else (lo, mid)
        return 0.5 * (lo + hi)


class Rig:
    """Plays the part of pv_load.cpp and of the hardware, 1 ms per pass.

    dynamic=True integrates the capacitor (4 sub-steps per ms, linearised implicit Euler);
    dynamic=False puts the module straight on its steady operating point, for tests that
    only need the mode machine over many minutes."""

    def __init__(self, lib, g=1000.0, t_cell=45.0, dynamic=True, cfg=None, t0_ms=0):
        self.lib, self.dynamic = lib, dynamic
        self.cfg = cfg or firmware_cfg()
        self.mem = ctypes.create_string_buffer(lib.load_sizeof())
        self.L = ctypes.cast(self.mem, ctypes.c_void_p)
        self.now = t0_ms
        self.heatsink, self.ina_ok, self.i_fake = 40.0, True, None
        self.g_logic = None                                # None = the logic sees the true G
        self.sun(g, t_cell)
        self.v = self.mod.v_oc
        self.conv = None                                   # [kind, end_ms, sum_v, sum_i, n]
        self.sweeps, self.log, self.triggers = [], [], []
        self.stored = []                                   # (t duty set, t trigger, V at trigger)
        self._t_set, self._voc_next = 0, False
        self.mode_seq = [OFF]                              # every mode the logic went through
        self.record = False
        lib.load_init(self.L, ctypes.byref(self.cfg), self.now)

    def sun(self, g, t_cell=None):
        self.g = g
        self.t_cell = self.t_cell if t_cell is None else t_cell
        self.mod = Module(g, self.t_cell)

    def get(self, field):
        value = self.lib.load_get(self.L, field.encode())
        assert value > -1e98, field
        return value

    mode = property(lambda self: int(self.get("mode")))
    d_a = property(lambda self: self.get("d_a"))
    d_b = property(lambda self: self.get("d_b"))

    def _plant(self):
        cond = (self.d_a + self.d_b) / K["R_BRANCH"]
        if not self.dynamic:
            if cond not in self.mod._steady:
                self.mod._steady[cond] = self.mod.steady_v(cond)
            self.v = self.mod._steady[cond]
            return
        dt = 0.25e-3
        for _ in range(4):
            f = (self.mod.i(self.v) - self.v * cond) / C_BANK
            self.v += dt * f / (1.0 - dt * (self.mod.slope(self.v) - cond) / C_BANK)

    def tick(self):
        lib, L = self.lib, self.L
        g = self.g if self.g_logic is None else self.g_logic
        req = lib.load_service(L, self.now, g, self.heatsink)
        self._note_mode()
        if self.conv and self.now >= self.conv[1]:
            kind, _, sv, si, n = self.conv
            self.conv = None
            i = si / n if self.i_fake is None else self.i_fake
            n_before = self.get("sweep_n") if kind == REQ_SWEEP else None
            lib.load_reading(L, self.now, sv / n, i, 1)
            if kind == REQ_SWEEP and (self._voc_next or self.get("sweep_n") != n_before):
                if not self._voc_next:                     # this reading became a point
                    self.stored.append((self._t_set, *self.triggers[-1][::2]))
                self._voc_next = False
                self._t_set = self.now                     # the next duty is set here
        elif req:
            if not self.ina_ok:
                lib.load_reading(L, self.now, 0.0, 0.0, 0)
            else:
                dur = T_CONV_SWEEP_MS if req == REQ_SWEEP else T_CONV_HOLD_MS
                self.conv = [req, self.now + dur, 0.0, 0.0, 0]
                self.triggers.append((self.now, req, self.v))
        to_row = ROW_MS - self.now % ROW_MS
        self._note_mode()
        if lib.load_sweep_start(L, self.now, to_row, g):
            self._voc_next = True                          # its first reading is V_oc
        self._note_mode()
        if self.get("sweep_done"):
            flags = lib.load_sweep_close(L, g)
            n = int(self.get("sweep_n"))
            pts = np.array([[lib.load_sweep_point(L, k, w) for w in range(3)] for k in range(n)])
            self.sweeps.append({"id": int(self.get("sweep_id")), "flags": flags, "pts": pts,
                                "best": int(self.get("sweep_best")), "v_oc": self.get("sweep_v_oc"),
                                "t0": int(self.get("sweep_t_start")),
                                "t1": int(self.get("sweep_t_end"))})
        self._plant()
        if self.conv:
            self.conv[2] += self.v
            self.conv[3] += self.mod.i(self.v)
            self.conv[4] += 1
        if self.record:
            self.log.append((self.now, self.mode, self.d_a, self.d_b, self.v))
        self.now += 1

    def _note_mode(self):
        if self.mode != self.mode_seq[-1]:
            self.mode_seq.append(self.mode)

    def run(self, ms, until=None):
        for _ in range(int(ms)):
            self.tick()
            if until and until(self):
                return True
        return until is None

    def to_hold_after_sweep(self):
        """OFF -> HOLD -> first sweep -> HOLD again."""
        assert self.run(15000, until=lambda r: len(r.sweeps) >= 1)
        assert self.mode == HOLD


# --- safe state ---------------------------------------------------------------------------

def test_boot_state_is_off_with_both_branches_off(lib):
    r = Rig(lib)
    assert (r.mode, r.d_a, r.d_b) == (OFF, 0.0, 0.0)
    assert lib.load_mode_text(r.mode) == b"off"


def test_stays_off_without_light_or_without_a_reading(lib):
    r = Rig(lib, g=20.0, dynamic=False)                   # below LOAD_G_ON
    r.run(3000)
    assert (r.mode, r.d_a, r.d_b) == (OFF, 0.0, 0.0)
    r = Rig(lib, dynamic=False)
    r.ina_ok = False                                       # sun, but no V_oc reading
    r.run(3000)
    assert (r.mode, r.d_a, r.d_b) == (OFF, 0.0, 0.0) and r.get("ina_frozen") == 1
    r = Rig(lib, dynamic=False)
    r.g_logic = math.nan                                   # irradiance not known
    r.run(3000)
    assert r.mode == OFF


def test_pwm_counts(lib):
    r = Rig(lib)
    top = 2 ** int(K["PWM_RES_BITS"]) - 1
    assert [lib.load_counts(r.L, d) for d in (0.0, -1.0, math.nan, 1.0, 2.0, 0.5)] == \
        [0, 0, 0, top, top, round(0.5 * top)]


def test_pins_and_branch_values_are_the_decided_ones():
    text = (SRC / "pv_config.h").read_text()
    assert "constexpr int LOAD_A_PWM = 33;" in text and "constexpr int LOAD_B_PWM = 14;" in text
    assert K["R_BRANCH"] == 1.5 and K["LOAD_BRANCHES"] == 2 and K["PWM_FREQ_HZ"] == 20000
    assert "#define HAS_LOAD 0" in text                    # no load hardware yet


# --- mode machine and branch rule ---------------------------------------------------------

def test_off_hold_sweep_hold_and_branch_rule(lib):
    r = Rig(lib)
    r.record = True
    r.to_hold_after_sweep()
    r.run(1000)
    assert r.mode_seq == [OFF, HOLD, SWEEP, HOLD]
    for _, mode, d_a, d_b, _ in r.log:
        if mode == SWEEP:
            assert d_a == d_b                              # same duty on both branches
        else:
            assert d_b == 0.0                              # branch B only in a sweep
        if mode in (OFF, TRIP):
            assert d_a == 0.0
    assert r.get("last_sweep_id") == 1 and r.sweeps[0]["flags"] == 0


def test_first_hold_setpoint_is_the_fraction_of_voc(lib):
    r = Rig(lib)
    assert r.run(2000, until=lambda r: r.mode != OFF)      # the first sweep starts at once
    assert r.get("v_set") == pytest.approx(K["VSET_FRACTION_OF_VOC"] * r.mod.v_oc, rel=2e-3)


def test_low_light_turns_the_load_off_only_after_five_minutes(lib):
    r = Rig(lib, dynamic=False)
    r.to_hold_after_sweep()
    r.g_logic = 20.0
    t0 = r.now
    r.run(K["LOAD_G_OFF_MS"] - 2000)
    assert r.mode in (HOLD, SWEEP)
    r.g_logic = math.nan                                   # an unknown G changes nothing
    r.run(1000)
    r.g_logic = 20.0
    assert r.run(4000, until=lambda r: r.mode == OFF)
    assert r.now - t0 >= K["LOAD_G_OFF_MS"] and (r.d_a, r.d_b) == (0.0, 0.0)
    r.g_logic = 40.0                                       # between off and on: stays off
    r.run(2000)
    assert r.mode == OFF
    r.g_logic = None
    assert r.run(2000, until=lambda r: r.mode == HOLD)


def test_a_bright_moment_restarts_the_low_light_timer(lib):
    r = Rig(lib, dynamic=False)
    r.to_hold_after_sweep()
    r.g_logic = 20.0
    r.run(K["LOAD_G_OFF_MS"] - 5000)
    r.g_logic = 200.0
    r.run(10)
    r.g_logic = 20.0
    r.run(K["LOAD_G_OFF_MS"] - 5000)
    assert r.mode in (HOLD, SWEEP)


# --- sweep ------------------------------------------------------------------------------------

@pytest.mark.parametrize("g", [200, 400, 600, 800, 1000])
@pytest.mark.parametrize("t_cell", [25, 45, 70])
def test_sweep_finds_the_mpp(lib, g, t_cell):
    r = Rig(lib, g=g, t_cell=t_cell)
    r.to_hold_after_sweep()
    s = r.sweeps[0]
    v, i = s["pts"][s["best"], 1:]
    assert v * i == pytest.approx(r.mod.p_mp, rel=0.005)   # spec: within 0.5 %
    assert s["flags"] == 0 and len(s["pts"]) == K["SWEEP_N_COARSE"] + K["SWEEP_N_FINE"]
    assert s["v_oc"] == pytest.approx(r.mod.v_oc, rel=2e-3)
    assert r.get("v_set") == pytest.approx(v)


def test_sweep_points_follow_the_spec(lib):
    r = Rig(lib)
    r.to_hold_after_sweep()
    s, nc = r.sweeps[0], int(K["SWEEP_N_COARSE"])
    d, v = s["pts"][:, 0], s["pts"][:, 1]
    top = 2 ** int(K["PWM_RES_BITS"]) - 1
    want = np.round(np.geomspace(K["SWEEP_D_MIN"], 1.0, nc) * top) / top   # on the PWM grid
    assert d[:nc] == pytest.approx(want, abs=1e-6)
    assert v[:nc].min() < 0.5 * s["v_oc"]                  # coarse pass reaches below V_oc / 2
    assert np.all(np.diff(v[:nc]) < 0)                     # voltage falls as duty rises
    m = int(np.argmax(v[:nc] * s["pts"][:nc, 2]))
    assert d[nc] == pytest.approx(d[m - 1]) and d[-1] == pytest.approx(d[m + 1])
    assert np.all(np.diff(d[nc:]) >= 0)
    # every stored point lies on the module's own curve (the INA226 is before the capacitor)
    for vk, ik in s["pts"][:, 1:]:
        assert ik == pytest.approx(r.mod.i(vk), abs=0.03)


def test_sweep_timing(lib):
    r = Rig(lib)
    r.to_hold_after_sweep()
    s = r.sweeps[0]
    reads = [t for t, kind, _ in r.triggers if kind == REQ_SWEEP]
    assert reads[0] - s["t0"] >= K["VOC_SETTLE_MS"]        # V_oc: 50 ms at duty 0
    assert len(r.stored) == len(s["pts"]) == 48
    for t_set, t_trigger, _ in r.stored:                   # no stored point is triggered early
        assert t_trigger - t_set >= K["T_SETTLE_MS"]
    # full sun: nearly every point is settled at its first check, so it costs T_SETTLE + one
    # reading; the few large steps (start of each pass) take a reading or two more
    waits = np.array([t_trigger - t_set for t_set, t_trigger, _ in r.stored])
    assert np.sum(waits > K["T_SETTLE_MS"] + 1) <= 4
    duration = s["t1"] - s["t0"]
    assert 1300 <= duration <= 2000                        # spec: about 1.6 s
    assert duration <= K["SWEEP_BUDGET_MS"] < K["SWEEP_GUARD_MS"] - 700


def _steady_points(r, s):
    """For each stored point: (true time constant, |V - steady V| / V_oc) in the simulation.
    tau = C / (D / R_SWEEP - dI/dV) at the steady operating point of that duty."""
    out = []
    for d, v, _ in s["pts"]:
        cond = 2 * d / K["R_BRANCH"]
        v_steady = r.mod.steady_v(cond)
        out.append((C_BANK / (cond - r.mod.slope(v_steady)), abs(v - v_steady) / r.mod.v_oc))
    return np.array(out)


def test_settle_time_is_five_time_constants_in_full_sun(lib):
    """[EST]: tau depends on the capacitor bank actually bought; bench B4 measures it."""
    r = Rig(lib, g=1000)
    r.to_hold_after_sweep()
    tau = _steady_points(r, r.sweeps[0])[:, 0]
    assert K["T_SETTLE_MS"] * 1e-3 >= 5 * tau.max(), f"5 tau = {5e3 * tau.max():.1f} ms"
    for t_set, t_trigger, _ in r.stored:
        assert t_trigger - t_set >= 5e3 * tau.max()


@pytest.mark.parametrize("g", [200, 400, 600, 800, 1000])
def test_stored_points_are_settled_at_any_irradiance(lib, g):
    """tau grows as the module current falls (about 18 ms at 200 W/m2 in this simulation),
    so a fixed 20 ms is not 5 tau there. The logic then waits for two agreeing readings."""
    r = Rig(lib, g=g)
    r.to_hold_after_sweep()
    s = r.sweeps[0]
    tau, off = _steady_points(r, s).T
    assert s["flags"] == 0
    assert off.max() < 0.003                               # within 0.3 % of V_oc of the end value
    if 5 * tau.max() > K["T_SETTLE_MS"] * 1e-3:            # slow points were given more time
        assert max(t_trigger - t_set for t_set, t_trigger, _ in r.stored) > K["T_SETTLE_MS"]
    assert s["t1"] - s["t0"] <= K["SWEEP_BUDGET_MS"]


@pytest.mark.parametrize("bits", [10, 11])
def test_a_sweep_that_cannot_settle_in_its_slot_is_flagged_and_still_ends(lib, bits):
    """80 W/m2: tau about 44 ms, far too slow for the slot at either PWM resolution (stored
    points are up to 4-5 % of V_oc from their end value, so the flag is deserved).
    Not 100 W/m2: there the sweep ends within 40 ms of SWEEP_BUDGET_MS, and which side it
    falls on depends on the resolution; the next test pins that down."""
    r = Rig(lib, g=80, cfg=firmware_cfg(pwm_bits=bits))
    r.to_hold_after_sweep()
    s = r.sweeps[0]
    assert s["flags"] == NOT_SETTLED and len(s["pts"]) == 48
    assert _steady_points(r, s)[:, 1].max() > 0.02         # really unsettled, not a false flag
    assert s["t1"] - s["t0"] <= K["SWEEP_BUDGET_MS"] + K["T_SETTLE_MS"] + 2 * T_CONV_SWEEP_MS
    for t_set, t_trigger, _ in r.stored:
        assert t_trigger - t_set >= K["T_SETTLE_MS"]


def test_finer_pwm_steps_need_fewer_extra_waits_near_the_budget(lib):
    """Why the flag moved when PWM_RES_BITS went from 10 to 11. At 100 W/m2 the fine pass
    sits near D = 0.02: 10 bit has about 11 distinct duties there, so each real step is
    large (about 1.2 % of V_oc) and several points need an extra reading; 11 bit has about
    20, with smaller steps. The coarse pass is the same. The difference, about 40 ms, is
    what puts the 10 bit sweep just over SWEEP_BUDGET_MS and the 11 bit one just under."""
    out = {}
    for bits in (10, 11):
        r = Rig(lib, g=100, cfg=firmware_cfg(pwm_bits=bits))
        r.to_hold_after_sweep()
        s = r.sweeps[0]
        waits = np.array([t_trigger - t_set for t_set, t_trigger, _ in r.stored])
        out[bits] = (len(set(np.round(s["pts"][24:, 0], 6))), waits[:24].sum(), waits[24:].sum(),
                     s["t1"] - s["t0"])
    assert out[11][0] > 1.5 * out[10][0]                   # more distinct fine duties
    assert out[11][1] == out[10][1]                        # coarse pass: no difference
    assert out[11][2] < out[10][2]                         # fine pass: less waiting
    assert abs(out[10][3] - K["SWEEP_BUDGET_MS"]) < 60 and abs(out[11][3] - K["SWEEP_BUDGET_MS"]) < 60


def test_hand_over_keeps_the_operating_point(lib):
    r = Rig(lib)
    r.to_hold_after_sweep()
    s = r.sweeps[0]
    d_best, v_best = s["pts"][s["best"], :2]
    assert r.d_b == 0.0 and r.d_a == pytest.approx(2 * d_best, abs=1e-3)
    r.run(60)                                              # settled, before the next hold step
    assert r.v == pytest.approx(v_best, rel=0.01)          # spec: changes by less than 1 %


def test_sweep_that_cannot_be_halved_is_clamped(lib):
    """D_best above 0.5 cannot be held by one branch: branch A goes to 1.0, not above."""
    r = Rig(lib)
    r.to_hold_after_sweep()
    lib.load_sweep_request(r.L, r.now)
    assert r.run(4000, until=lambda r: r.mode == SWEEP)
    while r.mode == SWEEP:                                 # feed a curve that peaks at D = 1
        if lib.load_service(r.L, r.now, 1000.0, 40.0) == REQ_SWEEP:
            lib.load_reading(r.L, r.now, 12.0, max(r.d_a, 0.01) * 8.0, 1)
        r.now += 1
    assert (r.d_a, r.d_b) == (1.0, 0.0)
    assert lib.load_sweep_close(r.L, 1000.0) & MAX_AT_EDGE


def _fed_sweep(lib, voltage, g_before=1000.0, g_after=1000.0, current=lambda d: 2.0):
    """One sweep with readings chosen by the test: voltage(k, d) -> V."""
    r = Rig(lib, dynamic=False)
    r.to_hold_after_sweep()
    lib.load_sweep_request(r.L, r.now)
    while not lib.load_sweep_start(r.L, r.now, 9000, g_before):
        if lib.load_service(r.L, r.now, 1000.0, 40.0):
            lib.load_reading(r.L, r.now, 17.0, 5.0, 1)
        r.now += 1
    first = True
    while r.mode == SWEEP:
        if lib.load_service(r.L, r.now, 1000.0, 40.0) == REQ_SWEEP:
            k = int(r.get("sweep_n"))                      # index of the point being measured
            v = 21.0 if first else voltage(k, r.d_a)
            lib.load_reading(r.L, r.now, v, 0.0 if first else current(r.d_a), 1)
            first = False
        r.now += 1
    return lib.load_sweep_close(r.L, g_after), r


def test_sweep_flags(lib):
    falling = lambda k, d: 20.0 - 10.0 * d                 # noqa: E731
    peak = lambda d: 6.0 - 40.0 * (d - 0.3) ** 2           # noqa: E731  best near D = 0.3
    assert _fed_sweep(lib, falling, current=peak)[0] == 0
    assert _fed_sweep(lib, falling, 1000.0, 1030.0, peak)[0] == G_UNSTABLE          # +3 %
    assert _fed_sweep(lib, falling, 1000.0, 1015.0, peak)[0] == 0                   # +1.5 %
    assert _fed_sweep(lib, falling, math.nan, 1000.0, peak)[0] == G_UNSTABLE        # G unknown
    assert _fed_sweep(lib, falling, 40.0, 40.0, peak)[0] == LOW_LIGHT
    bump = lambda k, d: 20.0 - 10.0 * d + (0.5 if k == 7 else 0.0)                  # noqa: E731
    assert _fed_sweep(lib, bump, current=peak)[0] == NON_MONOTONIC
    noise = lambda k, d: 20.0 - 10.0 * d + (0.05 if k == 7 else 0.0)                # noqa: E731
    assert _fed_sweep(lib, noise, current=peak)[0] == 0    # below SWEEP_MONO_TOL
    assert _fed_sweep(lib, falling)[0] == MAX_AT_EDGE      # constant I: best at the edge


# --- hold ---------------------------------------------------------------------------------------

def test_hold_stays_at_the_setpoint(lib):
    r = Rig(lib)
    r.to_hold_after_sweep()
    v_set = r.get("v_set")
    r.run(100)                                             # back from the last sweep point
    r.record = True
    r.run(5000)
    assert all(abs(v - v_set) / v_set < 0.01 for _, mode, _, _, v in r.log if mode == HOLD)
    assert r.v * r.mod.i(r.v) >= 0.995 * r.mod.p_mp


def test_hold_after_a_fifty_percent_irradiance_step(lib):
    r = Rig(lib)
    r.to_hold_after_sweep()
    r.run(11000 - r.now % ROW_MS)                          # 1 s after a row: no sweep is due
    v_set = r.get("v_set")
    r.sun(500.0)
    r.record = True
    r.run(2000)
    assert all(mode == HOLD for _, mode, *_ in r.log)
    assert abs(r.v - v_set) / v_set < 0.01                 # spec: back within 1 % in <= 2 s
    assert r.v * r.mod.i(r.v) >= 0.97 * r.mod.p_mp         # spec: >= 97 % of the new P_mp


def test_hold_step_is_limited_and_clamped(lib):
    r = Rig(lib, dynamic=False)
    r.to_hold_after_sweep()
    v_set, d0 = r.get("v_set"), r.d_a
    lib.load_reading(r.L, r.now, 2.0 * v_set, 3.0, 1)      # a huge error: +10 % only
    assert r.d_a == pytest.approx(d0 * (1 + K["HOLD_MAX_STEP"]), abs=1e-3)
    for _ in range(200):
        lib.load_reading(r.L, r.now, 2.0 * v_set, 3.0, 1)
    assert r.d_a == 1.0
    for _ in range(200):
        lib.load_reading(r.L, r.now, 0.1 * v_set, 3.0, 1)
    assert r.d_a == pytest.approx(K["HOLD_D_MIN"], abs=1e-3) and r.d_b == 0.0


# --- faults ---------------------------------------------------------------------------------------

def _in_mode(lib, mode):
    r = Rig(lib, dynamic=False)
    if mode == OFF:
        r.g_logic = 0.0
        r.run(500)
    else:
        r.to_hold_after_sweep()
        if mode == SWEEP:
            lib.load_sweep_request(r.L, r.now)
            assert r.run(4000, until=lambda r: r.mode == SWEEP)
            r.run(300)
    assert r.mode == mode
    return r


@pytest.mark.parametrize("mode", [OFF, HOLD, SWEEP])
@pytest.mark.parametrize("fault", [TRIP_HOT, TRIP_SENSOR, TRIP_OVERCURRENT])
def test_every_fault_trips_from_every_mode_with_both_branches_off(lib, mode, fault):
    r = _in_mode(lib, mode)
    if fault == TRIP_HOT:
        r.heatsink = K["T_TRIP_C"]
    elif fault == TRIP_SENSOR:
        r.heatsink = math.nan
    else:
        r.i_fake = K["I_TRIP_A"] + 0.1
    assert r.run(400, until=lambda r: r.mode == TRIP)
    assert (r.d_a, r.d_b) == (0.0, 0.0) and r.get("trip_reason") == fault
    assert lib.load_mode_text(TRIP) == b"trip" and lib.load_trip_text(fault) != b"?"
    n_sweeps = len(r.sweeps)
    r.run(3000)                                            # latched, and nothing is driven
    assert (r.mode, r.d_a, r.d_b) == (TRIP, 0.0, 0.0) and len(r.sweeps) == n_sweeps


def test_heatsink_trip_acts_in_the_same_service_call(lib):
    r = _in_mode(lib, HOLD)
    assert r.d_a > 0
    lib.load_service(r.L, r.now, 1000.0, K["T_TRIP_C"] + 5)
    assert (r.mode, r.d_a, r.d_b) == (TRIP, 0.0, 0.0)


def test_just_below_the_limits_does_not_trip(lib):
    r = _in_mode(lib, HOLD)
    r.heatsink, r.i_fake = K["T_TRIP_C"] - 0.1, K["I_TRIP_A"]
    r.run(2000)
    assert r.mode in (HOLD, SWEEP)


def test_trip_clears_only_after_five_minutes_without_the_cause(lib):
    r = _in_mode(lib, HOLD)
    r.heatsink = 95.0
    r.run(60000)                                           # hot for a minute: still tripped
    assert r.mode == TRIP
    r.heatsink = 60.0
    t_clear = r.now
    r.run(K["TRIP_CLEAR_MS"] - 30000)
    r.heatsink = 95.0                                      # hot again: the wait starts over
    r.run(100)
    r.heatsink = 60.0
    t_clear2 = r.now
    r.run(K["TRIP_CLEAR_MS"] - 1000)
    assert r.mode == TRIP and r.now - t_clear > K["TRIP_CLEAR_MS"]
    assert r.run(5000, until=lambda r: r.mode != TRIP)
    assert r.now - t_clear2 >= K["TRIP_CLEAR_MS"]
    assert r.run(2000, until=lambda r: r.mode == HOLD)     # through OFF, back to work
    assert r.get("trip_reason") == TRIP_HOT                # kept for the log


def test_ina_failures_freeze_the_duty_and_suspend_sweeps(lib):
    r = _in_mode(lib, HOLD)
    d0 = r.d_a
    r.ina_ok = False
    r.run(2 * K["HOLD_PERIOD_MS"] + 10)
    assert r.get("ina_frozen") == 0                        # two failures: not yet
    r.run(K["HOLD_PERIOD_MS"])
    assert r.get("ina_frozen") == 1
    n = len(r.sweeps)
    r.run(2 * K["SWEEP_PERIOD_S"] * 1000)                  # two sweep periods pass
    assert (r.mode, r.d_a, r.d_b) == (HOLD, d0, 0.0) and len(r.sweeps) == n
    r.ina_ok = True
    assert r.run(15000, until=lambda r: len(r.sweeps) > n)
    assert r.get("ina_frozen") == 0 and r.sweeps[-1]["flags"] == 0


def test_a_failed_reading_cuts_the_sweep_short(lib):
    r = _in_mode(lib, HOLD)
    d0, v0 = r.d_a, r.get("v_set")
    lib.load_sweep_request(r.L, r.now)
    assert r.run(4000, until=lambda r: r.mode == SWEEP)
    r.run(400)
    r.ina_ok = False
    assert r.run(200, until=lambda r: r.mode == HOLD)
    s = r.sweeps[-1]
    assert s["flags"] & INA_ERROR and 0 < len(s["pts"]) < 48
    assert (r.d_a, r.d_b) == (d0, 0.0) and r.get("v_set") == v0   # the old hold point


# --- controlled test, gamma run, row grid --------------------------------------------------------

def test_manual_off_and_on(lib):
    r = _in_mode(lib, HOLD)
    lib.load_manual_off(r.L, r.now, 1)
    assert (r.mode, r.d_a, r.d_b) == (OFF, 0.0, 0.0)       # at once, not at the next pass
    r.run(20000)
    assert r.mode == OFF and r.d_a == 0.0                  # the sun does not bring it back
    lib.load_manual_off(r.L, r.now, 0)
    assert r.run(1000, until=lambda r: r.mode == HOLD)
    r = _in_mode(lib, SWEEP)
    n = len(r.sweeps)
    lib.load_manual_off(r.L, r.now, 1)
    assert (r.mode, r.d_a, r.d_b) == (OFF, 0.0, 0.0)
    r.run(3000)
    assert len(r.sweeps) == n                              # the cut sweep is not stored


def test_manual_off_does_not_clear_a_trip(lib):
    r = _in_mode(lib, HOLD)
    r.heatsink = 95.0
    r.run(300)
    lib.load_manual_off(r.L, r.now, 1)
    lib.load_manual_off(r.L, r.now, 0)
    r.run(1000)
    assert r.mode == TRIP


def test_sweeps_never_cross_a_row_normal_and_gamma(lib):
    r = Rig(lib, dynamic=False)
    r.run(3 * 60000 + 5000)
    normal = list(r.sweeps)
    starts = np.array([s["t0"] for s in normal])
    # a start waits for the hold reading in flight (<= 141 ms), so the period is not exact
    assert len(normal) == 4 and np.all(np.abs(np.diff(starts) - 60000) <= 300)
    lib.load_gamma(r.L, r.now, 2)
    r.run(2 * 60000 + 10)
    gamma = r.sweeps[len(normal):]
    assert 7 <= len(gamma) <= 9                            # every 15 s for 2 min
    assert np.all(np.diff([s["t0"] for s in gamma]) >= 12000)
    assert r.get("gamma_active") == 0                      # the run ended by its timer
    r.run(2 * 60000)
    after = r.sweeps[len(normal) + len(gamma):]
    assert 1 <= len(after) <= 2
    for s in r.sweeps:
        assert s["flags"] == 0
        next_row = (s["t0"] // ROW_MS + 1) * ROW_MS
        assert next_row - s["t0"] >= K["SWEEP_GUARD_MS"]
        assert next_row - s["t1"] >= 800                   # the DS18B20 conversion starts there
    assert [s["id"] for s in r.sweeps] == list(range(1, len(r.sweeps) + 1))


def test_time_wrap_of_millis(lib):
    r = Rig(lib, dynamic=False, t0_ms=2 ** 32 - 20000)     # millis() wraps after 49.7 days
    lib2 = r.lib
    for _ in range(40000):
        r.tick()
        r.now %= 2 ** 32
    assert r.mode == HOLD and len(r.sweeps) >= 1 and lib2.load_get(r.L, b"ina_frozen") == 0


# --- the files the logger writes (text of logger.ino; it cannot run on the PC) --------------------

LOGGER = (ROOT / "firmware/logger/logger.ino").read_text(encoding="utf-8")


def _literal(after):
    """The C string literal (possibly split over lines) that follows `after` in logger.ino."""
    text = LOGGER[LOGGER.index(after) + len(after):]
    return "".join(re.findall(r'"([^"]*)"', re.match(r'(?:\s*"[^"]*")+', text).group(0)))


def test_sweep_file_headers_are_the_ones_the_analysis_reads():
    assert _literal("sd.appendLine(points_path, points.c_str(),") == "sweep_id,k,D,V,I"
    summary = _literal("sd.appendLine(summary_path, sum.c_str(),").split(",")
    assert summary == ["sweep_id", "date", "time", "uptime_ms", "T1", "T2", "T3", "T4", "T5",
                       "G_before", "G_after", "V_oc", "V_best", "I_best", "D_best", "flags"]
    text = pathlib.Path(fake_logger.__file__).read_text()            # the synthetic twin
    assert '"V_oc", "V_best", "I_best", "D_best", "flags"]' in text


def test_load_off_and_on_write_markers_in_the_existing_format():
    assert LOGGER.count('"/markers.csv"') == 1                       # one writer, one format
    assert _literal('sd.appendLine("/markers.csv", line,') == "date,time,millis"
    assert '%02u:%02u:%02u.%03u,%lu' in LOGGER                       # millisecond UTC time
    assert 'writeMarker(ms, "load off")' in LOGGER and 'writeMarker(millis(), "load on")' in LOGGER
    assert 'writeMarker(ms, nullptr)' in LOGGER                      # the button, as before


def test_logger_keeps_the_load_out_unless_has_load_is_set():
    assert '",NaN,none,0,"' in LOGGER                                # today's row, unchanged
    assert "if (HAS_LOAD) loadLoop(next);" in LOGGER                 # the only way into the load
    assert "if (HAS_LOAD) load.rowReading();" in LOGGER
    setup = LOGGER.split("void setup() {")[1]
    assert setup.lstrip().startswith("pvOutputsOff();")              # first thing after a reset
    guard, begin = setup.index("if (HAS_LOAD) {"), setup.index("load.begin()")
    assert guard < begin and "}" not in setup[guard:begin].replace("{", "", 1)


def test_mode_names_are_the_load_mode_column_values(lib):
    assert [lib.load_mode_text(m) for m in (OFF, HOLD, SWEEP, TRIP)] == \
        [b"off", b"hold", b"sweep", b"trip"]
