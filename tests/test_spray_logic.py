"""Phase B trial logic (firmware/libraries/PvDaq/src/pv_spray_logic.c), compiled on the PC:
the seeded block randomization, the life of a spike event in both arms, every stop
condition, the reasons an alarm opens no event, the three metered columns, and the row
text. Also the Python copy of the allocation (analysis/phase_b.py), which must give the
same arm as the C function for every event.

The pump, the flow sensor and the INA226 are SYNTHETIC here: a rig class that lets water
flow and power be drawn while the logic's duty is above zero. Passing shows that the logic
is right, not that hardware works: nothing of Phase B has run on a board."""
import ctypes
import math
import pathlib
import re
import subprocess

import numpy as np
import pytest

import config
from analysis import phase_b
from data.schema import PHASE_B_COLUMNS, PHASE_B_STATUS_BITS, STATUS_BITS
from export import run as ex

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "firmware/libraries/PvDaq/src"
GEN = ROOT / "export/c/generated/phase_b_config.h"
IDLE, ACTIVE, WINDOW, LOCKOUT = 0, 1, 2, 3
(OK, NO_EVENT_OPEN, NO_DISARMED, NO_LOCKOUT, NO_FAULT_LATCHED, NO_SENSOR, NO_STORAGE, NO_LATE,
 NO_LOW_LIGHT, NO_LOAD, NO_PUMP_SENSOR, NO_TANK_SENSOR, NO_WATER_WARM, NO_THERMAL_SHOCK,
 NO_BAD_CONFIG) = range(15)
(STOP_NONE, STOP_MAX_TIME, STOP_TARGET, STOP_WATER_WARM, STOP_SENSOR, STOP_NO_FLOW,
 STOP_OVERCURRENT, STOP_PUMP_SENSOR, STOP_COMMAND, STOP_HARD_TIMER) = range(10)
NOTE_OPENED, NOTE_REFUSED, NOTE_STOPPED, NOTE_CLOSED = 1, 2, 4, 8
BIT = {name: 1 << bit for name, bit in PHASE_B_STATUS_BITS.items()}
ROW_MS = 10000
CONV_MS = 145               # one 64-average INA226 reading (pv_spray.h, PUMP_CONV_MS)

pytestmark = pytest.mark.skipif(ex.find_gcc() is None, reason="no gcc")


class Cfg(ctypes.Structure):          # mirrors spray_cfg_t (pv_spray_logic.h), same order
    _fields_ = [("seed", ctypes.c_uint32), ("block", ctypes.c_uint32),
                ("dry_run", ctypes.c_uint32), ("row_period_ms", ctypes.c_uint32),
                ("window_ms", ctypes.c_uint32), ("lockout_ms", ctypes.c_uint32),
                ("pump_duty", ctypes.c_float), ("spray_max_ms", ctypes.c_uint32),
                ("stop_drop_c", ctypes.c_float), ("min_dt_water_c", ctypes.c_float),
                ("max_dt_water_c", ctypes.c_float), ("min_g", ctypes.c_float),
                ("need_t_water", ctypes.c_uint32), ("i_max_a", ctypes.c_float),
                ("no_flow_ms", ctypes.c_uint32), ("pump_sample_ms", ctypes.c_uint32),
                ("pump_fail_limit", ctypes.c_uint32), ("pulses_per_l", ctypes.c_float)]


class RowIn(ctypes.Structure):        # mirrors spray_row_in_t
    _fields_ = [("t_module", ctypes.c_float), ("t_water", ctypes.c_float),
                ("g", ctypes.c_float), ("alarm", ctypes.c_uint32),
                ("row_fault", ctypes.c_uint32), ("load_ok", ctypes.c_uint32),
                ("storage_ok", ctypes.c_uint32), ("window_fits", ctypes.c_uint32)]


def firmware_values():
    """Every name the board's configuration uses: the generated header (from config.py)
    and the constants of pv_config.h."""
    out = {}
    for name, value in re.findall(r"^#define (\w+) (.+?)\s*/\*", GEN.read_text(), re.M):
        if re.fullmatch(r"-?[\d.]+(e-?\d+)?[fu]?", value):
            out[name] = float(value.rstrip("fu"))
    for name, value in re.findall(r"constexpr \w+ (\w+) = ([-\d.]+)f?;",
                                  (SRC / "pv_config.h").read_text()):
        out[name] = float(value)
    return out


def firmware_cfg(**changes):
    """The configuration the board uses: every `c.field = NAME;` line of pvSprayConfig()
    in pv_spray.cpp. The default here is a WET run (dry_run = 0), since most tests are
    about the pump; the generated header's own value is checked separately."""
    body = re.search(r"spray_cfg_t pvSprayConfig\(\) \{(.*?)\n\}",
                     (SRC / "pv_spray.cpp").read_text(), re.S).group(1)
    lines = re.findall(r"c\.(\w+) = (\w+);", body)
    assert sorted(f for f, _ in lines) == sorted(f for f, _ in Cfg._fields_)   # none missing
    values, c = firmware_values(), Cfg()
    for field, name in lines:
        v = values[name]
        setattr(c, field, v if dict(Cfg._fields_)[field] is ctypes.c_float else int(v))
    c.dry_run = 0
    for name, value in changes.items():
        setattr(c, name, value)
    return c


@pytest.fixture(scope="session")
def lib(tmp_path_factory):
    dll = tmp_path_factory.mktemp("spray") / "pv_spray_logic.dll"
    subprocess.run([ex.find_gcc(), "-std=c99", "-O2", "-Wall", "-Wextra", "-Werror", "-shared",
                    "-fPIC", "-static-libgcc", str(SRC / "pv_spray_logic.c"), "-o", str(dll),
                    "-lm"], check=True)
    so = ctypes.CDLL(str(dll))
    u32, f32, ptr, i32 = ctypes.c_uint32, ctypes.c_float, ctypes.c_void_p, ctypes.c_int
    so.spray_alloc.argtypes, so.spray_alloc.restype = [u32, u32, u32], i32
    so.spray_init.argtypes = [ptr, ctypes.POINTER(Cfg), u32, u32, i32, u32]
    so.spray_service.argtypes = [ptr, u32]
    so.spray_flow.argtypes = [ptr, u32, u32]
    so.spray_pump_due.argtypes, so.spray_pump_due.restype = [ptr, u32], i32
    so.spray_pump_reading.argtypes = [ptr, u32, f32, f32, i32]
    so.spray_row.argtypes = [ptr, u32, ctypes.POINTER(RowIn)]
    so.spray_commit.argtypes = [ptr, u32, i32]
    so.spray_row_fields.argtypes = [ptr, u32, ctypes.c_char_p, u32]
    so.spray_row_fields.restype = u32
    so.spray_row_header.restype = ctypes.c_char_p
    so.spray_arm.argtypes = [ptr, u32, i32]
    so.spray_reset_fault.argtypes = [ptr]
    so.spray_hard_stop.argtypes = [ptr, u32]
    so.spray_take_notes.argtypes, so.spray_take_notes.restype = [ptr], u32
    so.spray_get.argtypes, so.spray_get.restype = [ptr, ctypes.c_char_p], ctypes.c_double
    for f in (so.spray_mode_text, so.spray_reason_text, so.spray_stop_text):
        f.argtypes, f.restype = [u32], ctypes.c_char_p
    so.spray_sizeof.restype = so.spray_cfg_sizeof.restype = so.spray_row_in_sizeof.restype = u32
    assert so.spray_cfg_sizeof() == ctypes.sizeof(Cfg)        # the mirrors above are in step
    assert so.spray_row_in_sizeof() == ctypes.sizeof(RowIn)
    return so


class Rig:
    """Plays the part of pv_spray.cpp and of the hardware. Synthetic plant: while the
    logic's duty is above zero, water flows at `flow_lpm` and the pump rail draws
    `pump_w`; otherwise both are zero (plus `standby_w`)."""

    def __init__(self, lib, cfg=None, n_wet=0, n_dry=0, armed=1, t0=0, dt_ms=50):
        self.lib, self.cfg, self.dt = lib, cfg or firmware_cfg(), dt_ms
        self.mem = ctypes.create_string_buffer(lib.spray_sizeof())
        self.S = ctypes.cast(self.mem, ctypes.c_void_p)
        self.now, self.t0 = t0, t0
        self.flow_lpm, self.pump_w, self.pump_v, self.standby_w = 1.5, 20.0, 12.0, 0.0
        self.t_module, self.t_water, self.g = 52.0, 30.0, 850.0
        self.row_fault, self.load_ok, self.storage_ok, self.window_fits = 0, 1, 1, 1
        self.store_ok, self.ina_ok = True, True
        self.alarms = set()                       # row numbers (0 = the first row) with an alarm
        self.rows, self.notes, self.stored = [], [], []
        self._frac, self._conv_end, self._n_row = 0.0, None, 0
        self.true_j = self.true_l = 0.0           # what the synthetic plant really did
        self.on_ms = 0
        lib.spray_init(self.S, ctypes.byref(self.cfg), n_wet, n_dry, armed, t0)
        self._take()

    def get(self, field):
        value = self.lib.spray_get(self.S, field.encode())
        assert value > -1e98, field
        return value

    mode = property(lambda self: int(self.get("mode")))
    duty = property(lambda self: self.get("duty"))

    def _take(self):
        n = self.lib.spray_take_notes(self.S)
        if n:
            self.notes.append((self.now, n))
        return n

    def _row(self):
        inp = RowIn(self.t_module, self.t_water, self.g, int(self._n_row in self.alarms),
                    self.row_fault, self.load_ok, self.storage_ok, self.window_fits)
        self.lib.spray_row(self.S, self.now, ctypes.byref(inp))
        if self.get("commit_pending"):
            self.stored.append((int(self.get("pend_dry")), int(self.get("pend_count"))))
            self.lib.spray_commit(self.S, self.now, int(self.store_ok))
        buf = ctypes.create_string_buffer(128)
        status = self.lib.spray_row_fields(self.S, self.now, buf, 128)
        f = buf.value.decode().split(",")
        assert f[0] == "" and len(f) == 7, buf.value
        self.rows.append({"n": self._n_row, "t": self.now, "text": buf.value.decode(),
                          "status": status, "spike_id": int(f[1]), "assign": int(f[2]),
                          "duty": float(f[3]), "flow": float(f[4]), "litres": float(f[5]),
                          "p": float(f[6]) if f[6] else math.nan, "mode": self.mode})
        self._n_row += 1

    def tick(self):
        lib, S = self.lib, self.S
        on = self.duty > 0
        p_true = (self.pump_w if on else 0.0) + self.standby_w
        if on:
            self.on_ms += self.dt
            self._frac += self.flow_lpm / 60.0 * self.cfg.pulses_per_l * self.dt / 1000.0
            self.true_l += self.flow_lpm / 60.0 * self.dt / 1000.0
        self.true_j += p_true * self.dt / 1000.0
        self.now = (self.now + self.dt) & 0xFFFFFFFF
        pulses, self._frac = int(self._frac), self._frac - int(self._frac)
        lib.spray_flow(S, self.now, pulses)
        lib.spray_service(S, self.now)
        if self._conv_end is not None and ((self.now - self._conv_end) & 0xFFFFFFFF) < 2 ** 31:
            self._conv_end = None
            lib.spray_pump_reading(S, self.now, self.pump_v, p_true / self.pump_v,
                                   int(self.ina_ok))
        elif self._conv_end is None and lib.spray_pump_due(S, self.now):
            self._conv_end = (self.now + CONV_MS) & 0xFFFFFFFF
        if ((self.now - self.t0) & 0xFFFFFFFF) % ROW_MS == 0:
            self._row()
        self._take()

    def run(self, ms):
        for _ in range(int(ms // self.dt)):
            self.tick()
        return self

    def run_rows(self, n):
        """Until n more rows have been written."""
        target = len(self.rows) + n
        while len(self.rows) < target:
            self.tick()
        return self

    def alarm_next_row(self):
        self.alarms.add(self._n_row)
        return self.run_rows(1)

    def noted(self, bit):
        return [t for t, n in self.notes if n & bit]


def first_event_with(seed, block, arm, dry=False):
    """Number (from 1) of the first event of the sequence that gets `arm`."""
    s = phase_b.dry_seed(seed) if dry else seed
    return next(n + 1 for n in range(64) if phase_b.allocation(s, n, block) == arm)


def rig_at_event(lib, arm, **cfg):
    """A rig whose NEXT event will be in `arm` (the stored counter is set just before it)."""
    c = firmware_cfg(**cfg)
    n = first_event_with(c.seed, c.block, arm, dry=bool(c.dry_run))
    kw = {"n_dry": n - 1} if c.dry_run else {"n_wet": n - 1}
    return Rig(lib, c, **kw), n


# --- allocation -----------------------------------------------------------------------------

def test_python_allocation_equals_the_firmware_function(lib):
    rng = np.random.default_rng(0)
    seeds = [0, 1, 0xFFFFFFFF, config.PHASE_B_SEED, *rng.integers(0, 2 ** 32, 40).tolist()]
    for block in (2, 4, 6, 8, 16):
        for seed in seeds:
            c = [lib.spray_alloc(seed, block, n) for n in range(96)]
            assert c == [phase_b.allocation(seed, n, block) for n in range(96)], (seed, block)
    for n in (10 ** 6, 2 ** 32 - 5, 2 ** 32 - 1):               # far along the sequence
        assert lib.spray_alloc(7, 4, n) == phase_b.allocation(7, n, 4)
    assert phase_b.expected_assign(3, 11) == phase_b.allocation(11, 2)
    assert phase_b.expected_assign(-3, 11) == phase_b.allocation(phase_b.dry_seed(11), 2)
    with pytest.raises(ValueError):
        phase_b.expected_assign(0, 11)


def test_every_block_is_balanced_and_arms_stay_within_two(lib):
    seed, block = config.PHASE_B_SEED, config.PHASE_B_BLOCK_SIZE
    arms = [lib.spray_alloc(seed, block, n) for n in range(4000)]
    assert set(arms) == {0, 1}
    for b in range(0, 4000, block):
        assert sum(arms[b:b + block]) == block // 2             # 2 spray + 2 no-spray
    imbalance = np.abs(np.cumsum(2 * np.array(arms) - 1))       # |spray - no-spray| so far
    assert imbalance.max() <= block // 2
    assert lib.spray_alloc(seed, 3, 0) == 0 and lib.spray_alloc(seed, 18, 0) == 0   # bad block
    with pytest.raises(ValueError):
        phase_b.allocation(seed, 0, 3)


def test_the_six_orders_of_a_block_are_equally_likely_and_blocks_are_independent(lib):
    """SYNTHETIC check of the generator: 60 000 blocks of one seed."""
    n_blocks = 60000
    arms = np.array([lib.spray_alloc(12345, 4, n) for n in range(4 * n_blocks)]).reshape(-1, 4)
    code = arms @ np.array([8, 4, 2, 1])
    orders, counts = np.unique(code, return_counts=True)
    assert len(orders) == 6                                     # 4 choose 2
    expected = n_blocks / 6
    chi2 = float(((counts - expected) ** 2 / expected).sum())
    assert chi2 < 20.5                                          # p = 0.001 at 5 degrees of freedom
    pairs = np.zeros((6, 6))
    idx = {o: k for k, o in enumerate(orders)}
    for a, b in zip(code[:-1], code[1:]):
        pairs[idx[a], idx[b]] += 1
    chi2_pairs = float(((pairs - (n_blocks - 1) / 36) ** 2 / ((n_blocks - 1) / 36)).sum())
    assert chi2_pairs < 66.6                                    # p = 0.001 at 35 degrees
    assert abs(arms[:, 0].mean() - 0.5) < 0.01                  # the first event of a block
    other = np.array([lib.spray_alloc(12346, 4, n) for n in range(400)])
    assert 0.3 < (other == arms.ravel()[:400]).mean() < 0.7     # another seed: another sequence


def test_dry_run_draws_from_its_own_sequence(lib):
    seed = config.PHASE_B_SEED
    wet = [phase_b.allocation(seed, n) for n in range(200)]
    dry = [phase_b.allocation(phase_b.dry_seed(seed), n) for n in range(200)]
    assert wet != dry and 0.3 < np.mean(np.array(wet) == np.array(dry)) < 0.7


# --- configuration --------------------------------------------------------------------------

def test_the_generated_header_is_safe_by_default():
    v = firmware_values()
    assert v["PHASE_B_DRY_RUN"] == 1 and v["PHASE_B_PLACEHOLDERS_CONFIRMED"] == 0
    assert config.PHASE_B_DRY_RUN is True and config.PHASE_B_PLACEHOLDERS_CONFIRMED is False
    assert v["PHASE_B_BLOCK_SIZE"] == 4 and v["PHASE_B_SEED"] == config.PHASE_B_SEED
    assert v["SPRAY_MAX_MS"] + v["SAMPLE_PERIOD_MS"] < v["PHASE_B_EVENT_WINDOW_MS"]
    assert config.PHASE_B_BASELINE_MIN <= config.PHASE_B_LOCKOUT_MIN
    assert 0 < v["PUMP_DUTY"] <= 1 and v["FLOW_PULSES_PER_L"] > 0


def test_pins_do_not_clash():
    text = (SRC / "pv_config.h").read_text()
    pins = {name: int(v) for name, v in re.findall(r"constexpr int (\w+) = (\d+);", text)}
    assert (pins["LOAD_A_PWM"], pins["LOAD_B_PWM"]) == (33, 14)   # the load's two gates
    assert pins["PUMP_PWM"] not in (33, 14)                     # the pump is never on one of them
    assert config.FLOW_PIN not in pins.values()                 # a pin nothing else uses
    assert config.FLOW_PIN in (35, 36, 39)                      # the spare pins of PINMAP.md
    header = (SRC / "pv_spray.h").read_text()                   # and the build checks it too
    assert "pins::PUMP_PWM != pins::LOAD_A_PWM && pins::PUMP_PWM != pins::LOAD_B_PWM" in header
    for name in pins:
        assert f"FLOW_PIN != pins::{name}" in header, name


def test_status_bits_and_row_header_match_the_schema(lib):
    text = (SRC / "pv_spray_logic.h").read_text()
    defined = {name: int(bit) for name, bit in
               re.findall(r"^#define SPRAY_ST_(\w+)\s+\(1u << (\d+)\)", text, re.M)}
    assert defined == PHASE_B_STATUS_BITS
    assert min(defined.values()) == max(STATUS_BITS.values()) + 1   # they continue pv_status.h
    assert not set(defined) & set(STATUS_BITS)
    assert lib.spray_row_header().decode().split(",") == [""] + PHASE_B_COLUMNS


def test_a_configuration_that_cannot_work_never_arms(lib):
    for change in (dict(block=3), dict(block=18), dict(pump_duty=0.0), dict(pump_duty=1.2),
                   dict(pulses_per_l=0.0), dict(spray_max_ms=1800000), dict(pump_fail_limit=0)):
        r = Rig(lib, firmware_cfg(**change))
        assert r.get("armed") == 0, change
        r.alarm_next_row()
        assert r.get("last_reason") == NO_BAD_CONFIG and r.duty == 0 and r.mode == IDLE


# --- the life of an event ---------------------------------------------------------------------

def test_pump_is_off_after_init_and_without_an_alarm(lib):
    r = Rig(lib).run_rows(30)
    assert r.duty == 0 and r.mode == IDLE and r.on_ms == 0
    assert all(x["spike_id"] == 0 and x["assign"] == -1 and x["duty"] == 0 for x in r.rows)
    assert r.get("n_wet") == 0 and r.stored == [] and r.noted(NOTE_OPENED) == []


def test_no_spray_arm_keeps_the_pump_off_for_the_whole_window(lib):
    r, n = rig_at_event(lib, 0)
    r.run_rows(3).alarm_next_row()
    assert r.mode == WINDOW and r.duty == 0
    assert r.rows[-1]["spike_id"] == n and r.rows[-1]["assign"] == 0   # the alarm row itself
    assert r.stored == [(0, n)] and r.get("n_wet") == n
    window_rows = r.cfg.window_ms // ROW_MS
    r.run_rows(window_rows + 70)
    tagged = [x for x in r.rows if x["spike_id"] == n]
    assert len(tagged) == window_rows and all(x["assign"] == 0 for x in tagged)
    assert r.on_ms == 0 and all(x["duty"] == 0 and x["flow"] == 0 for x in r.rows)
    assert r.noted(NOTE_STOPPED) == [] and len(r.noted(NOTE_CLOSED)) == 1
    assert r.get("done_spike_id") == n and r.get("done_sprayed") == 0
    after = [x for x in r.rows if x["n"] > tagged[-1]["n"]]
    assert all(x["spike_id"] == 0 and x["assign"] == -1 for x in after)


def test_spray_arm_runs_the_pump_at_the_set_duty_for_the_longest_spray(lib):
    r, n = rig_at_event(lib, 1)
    r.run_rows(3).alarm_next_row()
    assert r.mode == ACTIVE and r.duty == pytest.approx(r.cfg.pump_duty)
    assert r.rows[-1]["spike_id"] == n and r.rows[-1]["assign"] == 1
    r.run_rows(r.cfg.window_ms // ROW_MS + 5)
    assert r.on_ms == pytest.approx(r.cfg.spray_max_ms, abs=r.dt)   # never longer
    assert r.get("done_stop_reason") == STOP_MAX_TIME and r.get("done_sprayed") == 1
    assert r.get("done_spray_ms") == pytest.approx(r.cfg.spray_max_ms, abs=r.dt)
    assert r.get("fault_latched") == 0
    assert not any(x["status"] & BIT["SPRAY_ABORT"] for x in r.rows)
    tagged = [x for x in r.rows if x["spike_id"] == n]
    assert len(tagged) == r.cfg.window_ms // ROW_MS and all(x["assign"] == 1 for x in tagged)
    assert r.duty == 0


def test_window_and_lockout_are_counted_in_rows(lib):
    r, n = rig_at_event(lib, 0, window_ms=300000, lockout_ms=120000, spray_max_ms=60000)
    r.run_rows(2).alarm_next_row()
    first = r.rows[-1]["n"]
    r.run_rows(60)
    modes = {x["n"] - first: x["mode"] for x in r.rows if x["n"] >= first}
    assert all(modes[k] == WINDOW for k in range(0, 30))        # 30 rows: 0 .. 29
    assert all(modes[k] == LOCKOUT for k in range(30, 42))      # 12 rows of lock-out
    assert modes[42] == IDLE
    # an alarm in the lock-out opens nothing; at the first idle row it does
    r2, _ = rig_at_event(lib, 0, window_ms=300000, lockout_ms=120000, spray_max_ms=60000)
    r2.run_rows(2).alarm_next_row()
    first = r2.rows[-1]["n"]
    r2.alarms |= {first + 35, first + 42}
    r2.run_rows(50)
    assert r2.get("last_reason") == NO_LOCKOUT and len(r2.noted(NOTE_OPENED)) == 2
    ids = sorted({x["spike_id"] for x in r2.rows} - {0})
    assert ids == [ids[0], ids[0] + 1]
    assert next(x["n"] for x in r2.rows if x["spike_id"] == ids[1]) == first + 42


def test_alarms_inside_an_open_event_belong_to_it(lib):
    r, n = rig_at_event(lib, 1)
    r.run_rows(1).alarm_next_row()
    first = r.rows[-1]["n"]
    r.alarms |= {first + 61, first + 62, first + 130}           # re-heating after the spray
    r.run_rows(r.cfg.window_ms // ROW_MS + 2)
    assert len(r.noted(NOTE_OPENED)) == 1 and r.get("n_wet") == n
    assert len(r.noted(NOTE_REFUSED)) == 3 and r.get("last_reason") == NO_EVENT_OPEN
    assert not any(x["status"] & BIT["SPRAY_BLOCKED"] for x in r.rows[first:first + 170])


def test_dry_run_never_drives_the_pump_and_numbers_its_events_below_zero(lib):
    c = firmware_cfg(dry_run=1)
    n = first_event_with(c.seed, c.block, 1, dry=True)
    r = Rig(lib, c, n_dry=n - 1, n_wet=7)
    r.t_water = math.nan                                        # no tank sensor, no pump INA226:
    r.ina_ok = False                                            # a dry run needs neither
    r.run_rows(2).alarm_next_row()
    assert r.rows[-1]["spike_id"] == -n and r.rows[-1]["assign"] == 1 and r.mode == ACTIVE
    r.run_rows(r.cfg.window_ms // ROW_MS + 3)
    assert max(x["duty"] for x in r.rows) == 0 and r.on_ms == 0 and r.true_l == 0
    assert r.get("done_sprayed") == 1 and r.get("done_stop_reason") == STOP_MAX_TIME
    assert r.get("done_spray_ms") == pytest.approx(c.spray_max_ms, abs=r.dt)   # it would have run
    assert r.stored == [(1, n)] and r.get("n_dry") == n and r.get("n_wet") == 7
    assert r.get("fault_latched") == 0


def test_event_numbers_go_on_after_a_restart_and_are_never_reused(lib):
    c = firmware_cfg(window_ms=200000, lockout_ms=0, spray_max_ms=30000)
    r = Rig(lib, c, n_wet=17)
    r.alarms |= {3, 30, 60}
    r.run_rows(90)
    assert sorted({x["spike_id"] for x in r.rows} - {0}) == [18, 19, 20]
    assert [n for _, n in r.stored] == [18, 19, 20]
    arms = {x["spike_id"]: x["assign"] for x in r.rows if x["spike_id"]}
    assert arms == {k: phase_b.allocation(c.seed, k - 1, c.block) for k in (18, 19, 20)}
    again = Rig(lib, c, n_wet=int(r.get("n_wet")))              # the board restarts
    again.alarm_next_row()
    assert again.rows[-1]["spike_id"] == 21


def test_forty_events_give_twenty_per_arm(lib):
    c = firmware_cfg(window_ms=120000, lockout_ms=0, spray_max_ms=20000)
    r = Rig(lib, c)
    r.alarms |= set(range(2, 2 + 40 * 13, 13))
    r.run_rows(40 * 13 + 15)
    arms = {}
    for x in r.rows:
        if x["spike_id"]:
            arms[x["spike_id"]] = x["assign"]
    assert sorted(arms) == list(range(1, 41))
    assert sum(arms.values()) == 20
    assert all(sum(arms[k] for k in range(b, b + 4)) == 2 for b in range(1, 41, 4))


# --- an alarm that opens no event --------------------------------------------------------------

@pytest.mark.parametrize("change, reason", [
    (dict(row_fault=1), NO_SENSOR), (dict(t_module=math.nan), NO_SENSOR),
    (dict(storage_ok=0), NO_STORAGE), (dict(window_fits=0), NO_LATE),
    (dict(g=150.0), NO_LOW_LIGHT), (dict(g=math.nan), NO_LOW_LIGHT),
    (dict(load_ok=0), NO_LOAD), (dict(ina_ok=False), NO_PUMP_SENSOR),
    (dict(t_water=math.nan), NO_TANK_SENSOR), (dict(t_water=51.0), NO_WATER_WARM),
    (dict(t_water=5.0), NO_THERMAL_SHOCK)])
def test_each_reason_keeps_the_event_closed_and_is_decided_before_the_arm(lib, change, reason):
    for arm in (0, 1):                                          # the same in both arms
        r, n = rig_at_event(lib, arm)
        for name, value in change.items():
            setattr(r, name, value)
        r.run_rows(3).alarm_next_row()
        assert r.get("last_reason") == reason, lib.spray_reason_text(reason)
        assert r.mode == IDLE and r.duty == 0 and r.get("n_wet") == n - 1 and r.stored == []
        assert r.rows[-1]["spike_id"] == 0 and r.rows[-1]["assign"] == -1
        assert r.rows[-1]["status"] & BIT["SPRAY_BLOCKED"]
        assert len(r.noted(NOTE_REFUSED)) == 1 and r.noted(NOTE_OPENED) == []


def test_blocked_bit_follows_the_conditions_row_by_row(lib):
    r = Rig(lib)
    r.run_rows(4)
    assert not any(x["status"] & BIT["SPRAY_BLOCKED"] for x in r.rows[1:])
    r.g = 100.0
    r.run_rows(3)
    assert all(x["status"] & BIT["SPRAY_BLOCKED"] for x in r.rows[-3:])
    r.g = 850.0
    r.run_rows(2)
    assert not r.rows[-1]["status"] & BIT["SPRAY_BLOCKED"]


def test_a_number_that_cannot_be_stored_is_not_used(lib):
    r, n = rig_at_event(lib, 1)
    r.store_ok = False
    r.run_rows(2).alarm_next_row()
    assert r.get("last_reason") == NO_STORAGE and r.mode == IDLE and r.duty == 0
    assert r.get("n_wet") == n - 1 and r.rows[-1]["spike_id"] == 0
    r.store_ok = True
    r.alarm_next_row()
    assert r.rows[-1]["spike_id"] == n and r.mode == ACTIVE     # the same number, now used


def test_spray_off_command_blocks_and_stops(lib):
    r, n = rig_at_event(lib, 1)
    lib.spray_arm(r.S, r.now, 0)
    r.run_rows(2).alarm_next_row()
    assert r.get("last_reason") == NO_DISARMED and r.mode == IDLE
    lib.spray_arm(r.S, r.now, 1)
    r.alarm_next_row()
    assert r.mode == ACTIVE and r.duty > 0
    r.run(3000)
    lib.spray_arm(r.S, r.now, 0)                                # typed during the spray
    assert r.duty == 0 and r.mode == WINDOW and r.get("stop_reason") == STOP_COMMAND
    r.run_rows(2)
    assert r.get("fault_latched") == 0
    assert not any(x["status"] & BIT["SPRAY_ABORT"] for x in r.rows)


# --- stop conditions ------------------------------------------------------------------------------

def spraying(lib, **cfg):
    r, n = rig_at_event(lib, 1, **cfg)
    r.run_rows(2).alarm_next_row()
    assert r.mode == ACTIVE and r.duty > 0
    return r


def test_any_row_sensor_fault_stops_the_pump(lib):
    for change in (dict(row_fault=1), dict(t_module=math.nan), dict(t_water=math.nan)):
        r = spraying(lib)
        r.run_rows(1)
        for name, value in change.items():
            setattr(r, name, value)
        r.run_rows(1)
        assert r.duty == 0 and r.mode == WINDOW, change
        assert r.get("stop_reason") == STOP_SENSOR and r.get("fault_latched") == 0
        assert r.rows[-1]["status"] & BIT["SPRAY_ABORT"]
        r.run_rows(1)
        assert not r.rows[-1]["status"] & BIT["SPRAY_ABORT"]    # flagged once
        assert r.on_ms < 25000


def test_no_flow_stops_the_pump_and_latches(lib):
    r, n = rig_at_event(lib, 1)
    r.flow_lpm = 0.0                                            # empty tank
    r.run_rows(2).alarm_next_row()
    r.run_rows(2)
    assert r.duty == 0 and r.get("stop_reason") == STOP_NO_FLOW
    assert r.on_ms == pytest.approx(r.cfg.no_flow_ms, abs=r.dt)
    assert r.get("fault_latched") == STOP_NO_FLOW
    r.flow_lpm = 1.5
    r.run_rows(r.cfg.window_ms // ROW_MS + r.cfg.lockout_ms // ROW_MS + 2)
    assert r.mode == IDLE
    r.alarm_next_row()
    assert r.get("last_reason") == NO_FAULT_LATCHED and r.duty == 0
    assert r.rows[-1]["status"] & BIT["SPRAY_BLOCKED"]
    lib.spray_reset_fault(r.S)                                  # SPRAY RESET, after the repair
    r.alarm_next_row()
    assert r.rows[-1]["spike_id"] == n + 1


def test_flow_that_stops_during_the_spray_is_caught(lib):
    r = spraying(lib)
    r.run(8000)
    on_before = r.on_ms
    r.flow_lpm = 0.0
    r.run(r.cfg.no_flow_ms + 2000)
    assert r.duty == 0 and r.get("stop_reason") == STOP_NO_FLOW
    # pulses come one at a time (~11 per s here), so the clock starts at the last pulse
    assert r.on_ms - on_before == pytest.approx(r.cfg.no_flow_ms, abs=200)


def test_overcurrent_stops_the_pump_and_latches(lib):
    r = spraying(lib)
    r.run(2000)
    r.pump_w = 12.0 * (r.cfg.i_max_a + 0.5)                     # a stalled pump
    r.run(1000)
    assert r.duty == 0 and r.get("stop_reason") == STOP_OVERCURRENT
    assert r.get("fault_latched") == STOP_OVERCURRENT
    r.run_rows(1)
    assert r.rows[-1]["status"] & BIT["SPRAY_ABORT"]


def test_failing_pump_readings_stop_the_pump(lib):
    r = spraying(lib)
    r.run(2000)
    on_before = r.on_ms
    r.ina_ok = False
    r.run(3000)
    assert r.duty == 0 and r.get("stop_reason") == STOP_PUMP_SENSOR
    limit = r.cfg.pump_fail_limit * (r.cfg.pump_sample_ms + CONV_MS)
    assert r.on_ms - on_before <= limit + 2 * r.dt
    assert r.get("fault_latched") == 0 and r.get("pump_ok") == 0


def test_water_as_warm_as_the_module_ends_the_spray(lib):
    r = spraying(lib)
    r.run_rows(1)
    r.t_module = r.t_water + r.cfg.min_dt_water_c - 0.5         # cooled down to the water
    r.run_rows(1)
    assert r.duty == 0 and r.get("stop_reason") == STOP_WATER_WARM
    assert r.get("fault_latched") == 0
    assert not r.rows[-1]["status"] & BIT["SPRAY_ABORT"]        # a normal end, not a fault


def test_target_cooling_ends_the_spray_when_it_is_set(lib):
    r = spraying(lib, stop_drop_c=4.0)
    r.run_rows(1)
    r.t_module -= 3.0
    r.run_rows(1)
    assert r.duty > 0
    r.t_module -= 1.5                                           # 4.5 below the alarm row
    r.run_rows(1)
    assert r.duty == 0 and r.get("stop_reason") == STOP_TARGET
    r2 = spraying(lib)                                          # 0 = not used: runs to the end
    r2.t_module -= 20.0
    r2.run_rows(4)
    assert r2.duty > 0


def test_hard_timer_latches(lib):
    r = spraying(lib)
    r.run(1000)
    lib.spray_hard_stop(r.S, r.now)
    assert r.duty == 0 and r.get("stop_reason") == STOP_HARD_TIMER
    assert r.get("fault_latched") == STOP_HARD_TIMER
    idle = Rig(lib)
    lib.spray_hard_stop(idle.S, idle.now)                       # fired with the pump off
    assert idle.get("fault_latched") == STOP_HARD_TIMER and idle.duty == 0


# --- metering ----------------------------------------------------------------------------------------

def test_rows_add_up_to_the_litres_and_the_energy_of_the_event(lib):
    r, n = rig_at_event(lib, 1, spray_max_ms=47000)             # not a whole number of rows
    r.dt = 10
    r.standby_w = 0.06
    r.run_rows(2).alarm_next_row()
    r.run_rows(r.cfg.window_ms // ROW_MS + 3)
    ev = [x for x in r.rows if x["spike_id"] == n]
    # litres: the running count, and the flow column integrated
    litres = r.rows[-1]["litres"]
    assert litres == pytest.approx(r.true_l, abs=1.5 / r.cfg.pulses_per_l)
    assert sum(x["flow"] for x in r.rows) * 10 / 60 == pytest.approx(r.true_l, rel=0.002)
    assert r.get("done_pulses") / r.cfg.pulses_per_l == pytest.approx(r.true_l, abs=0.005)
    # energy: the power column integrated over ALL rows equals what the rail really drew
    energy = sum(x["p"] for x in r.rows[1:]) * 10
    assert energy == pytest.approx(r.true_j - r.standby_w * 10, rel=0.01)
    spray_j = r.pump_w * r.cfg.spray_max_ms / 1000
    assert sum(x["p"] for x in ev) * 10 == pytest.approx(
        spray_j + r.standby_w * len(ev) * 10, rel=0.01)
    assert r.get("done_energy_j") == pytest.approx(sum(x["p"] for x in ev) * 10, rel=0.01)
    # duty: mean over each row; the rows add up to the pump-on time
    on_s = sum(x["duty"] for x in r.rows) * 10 / r.cfg.pump_duty
    assert on_s == pytest.approx(r.cfg.spray_max_ms / 1000, abs=0.02)
    # an event opens AT a row, so the spray starts on the row grid: the alarm row itself
    # shows no duty yet, then 4 full rows, then the row in which the last 7 s fall
    assert [round(x["duty"], 3) for x in ev[:7]] == [0.0, 0.6, 0.6, 0.6, 0.6, 0.42, 0.0]
    assert max(x["flow"] for x in ev) == pytest.approx(r.flow_lpm, rel=0.02)
    assert not any(x["status"] & BIT["PUMP_FAIL"] for x in r.rows[1:])


def test_missing_pump_readings_leave_the_power_field_empty(lib):
    r = Rig(lib)
    r.run_rows(3)
    r.ina_ok = False
    r.run_rows(3)
    last = r.rows[-1]
    assert math.isnan(last["p"]) and last["status"] & BIT["PUMP_FAIL"]
    assert last["text"].endswith(",")                           # an empty field, like the logger's
    r.ina_ok = True
    r.run_rows(3)
    assert r.rows[-1]["p"] == pytest.approx(0.0, abs=1e-6)
    assert not r.rows[-1]["status"] & BIT["PUMP_FAIL"]


def test_row_text_is_exactly_the_six_columns(lib):
    r, n = rig_at_event(lib, 1)
    r.run_rows(2)
    assert r.rows[-1]["text"] == ",0,-1,0.000,0.000,0.0000,0.000"
    r.alarm_next_row()
    assert r.rows[-1]["text"] == f",{n},1,0.000,0.000,0.0000,0.000"   # opened at this very row
    r.run_rows(2)
    f = r.rows[-1]["text"].split(",")
    assert f[1:4] == [str(n), "1", "0.600"]
    assert float(f[4]) == pytest.approx(1.5, rel=0.02) and float(f[6]) == pytest.approx(20.0, rel=0.02)
    assert re.fullmatch(r"\d+\.\d{4}", f[5])


def test_millis_wrap_around_does_not_disturb_an_event(lib):
    t0 = 2 ** 32 - 400000                                       # the counter wraps mid-window
    c = firmware_cfg()
    n = first_event_with(c.seed, c.block, 1)
    r = Rig(lib, c, n_wet=n - 1, t0=t0)
    r.run_rows(2).alarm_next_row()
    r.run_rows(c.window_ms // ROW_MS + c.lockout_ms // ROW_MS + 3)
    assert r.now < t0                                           # it did wrap
    tagged = [x for x in r.rows if x["spike_id"] == n]
    assert len(tagged) == c.window_ms // ROW_MS
    assert r.on_ms == pytest.approx(c.spray_max_ms, abs=r.dt) and r.mode == IDLE
    assert sum(x["flow"] for x in r.rows) * 10 / 60 == pytest.approx(r.true_l, rel=0.01)


def test_texts_cover_every_code(lib):
    assert [lib.spray_mode_text(k).decode() for k in range(4)] == ["idle", "spray", "window", "lockout"]
    reasons = [lib.spray_reason_text(k).decode() for k in range(15)]
    stops = [lib.spray_stop_text(k).decode() for k in range(10)]
    assert "?" not in reasons + stops and len(set(reasons)) == 15 and len(set(stops)) == 10
    assert lib.spray_reason_text(15) == b"?" and lib.spray_stop_text(10) == b"?"
    assert not any("," in t for t in reasons + stops)           # they go into a CSV field
    text = (SRC / "pv_spray_logic.h").read_text()               # the numbers used in this file
    for name, value in (("SPRAY_NO_BAD_CONFIG", NO_BAD_CONFIG), ("SPRAY_STOP_HARD_TIMER", STOP_HARD_TIMER),
                        ("SPRAY_NO_WATER_WARM", NO_WATER_WARM), ("SPRAY_STOP_NO_FLOW", STOP_NO_FLOW)):
        assert re.search(rf"{name} = {value},", text), name
