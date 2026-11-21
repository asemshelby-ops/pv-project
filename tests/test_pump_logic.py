"""Pump protection logic (firmware/libraries/PvDaq/src/pv_pump_logic.c), compiled on the PC:
PWM soft-start, over-current cut-off, dry-run cut-off and the latched trip. The currents
here are made-up test inputs, not properties of our pump; the thresholds the board uses
(pv_config.h) are assumptions until the bucket test."""
import ctypes
import pathlib
import subprocess

import pytest

from export import run as ex

SRC = pathlib.Path(__file__).resolve().parent.parent / "firmware/libraries/PvDaq/src"
IDLE, RAMP, RUN, TRIP = 0, 1, 2, 3
NONE, OVERCURRENT, DRY, SENSOR = 0, 1, 2, 3

pytestmark = pytest.mark.skipif(ex.find_gcc() is None, reason="no gcc")
u32, f32 = ctypes.c_uint32, ctypes.c_float


class Cfg(ctypes.Structure):           # mirrors pump_cfg_t (pv_pump_logic.h), same order
    _fields_ = [("ramp_ms", u32), ("max_step_ms", u32), ("d_min", f32),
                ("i_trip_a", f32), ("i_trip_n", u32),
                ("i_dry_a", f32), ("prime_ms", u32), ("dry_ms", u32),
                ("ina_fail_limit", u32)]


class Pump(ctypes.Structure):          # mirrors pump_t
    _fields_ = [("cfg", Cfg), ("state", u32), ("trip_reason", u32), ("d", f32), ("d_req", f32),
                ("i_last", f32), ("t_last_ms", u32), ("t_start_ms", u32), ("over_n", u32),
                ("fail_n", u32), ("dry_active", u32), ("dry_since_ms", u32)]


def cfg(**changes):
    c = Cfg(ramp_ms=1000, max_step_ms=20, d_min=0.5, i_trip_a=1.5, i_trip_n=3,
            i_dry_a=0.2, prime_ms=10000, dry_ms=3000, ina_fail_limit=3)
    for k, v in changes.items():
        setattr(c, k, v)
    return c


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    dll = tmp_path_factory.mktemp("pump") / "pv_pump_logic.dll"
    subprocess.run([ex.find_gcc(), "-std=c99", "-O2", "-Wall", "-Wextra", "-Werror", "-shared",
                    "-static-libgcc", str(SRC / "pv_pump_logic.c"), "-o", str(dll), "-lm"],
                   check=True)
    so = ctypes.CDLL(str(dll))
    ptr = ctypes.POINTER(Pump)
    so.pump_init.argtypes = [ptr, ctypes.POINTER(Cfg), u32]
    so.pump_request.argtypes = [ptr, f32]
    so.pump_service.argtypes, so.pump_service.restype = [ptr, u32], f32
    so.pump_reading.argtypes = [ptr, u32, f32, ctypes.c_int]
    so.pump_clear.argtypes = [ptr]
    for f in (so.pump_state_text, so.pump_trip_text):
        f.argtypes, f.restype = [u32], ctypes.c_char_p
    so.pump_sizeof.restype = so.pump_cfg_sizeof.restype = u32
    assert so.pump_cfg_sizeof() == ctypes.sizeof(Cfg) and so.pump_sizeof() == ctypes.sizeof(Pump)
    return so


class Rig:
    def __init__(self, lib, t0=0, **changes):
        self.lib, self.p, self.now = lib, Pump(), t0
        lib.pump_init(self.p, cfg(**changes), t0)

    def run(self, ms, step=1, current=None):
        """Service every `step` ms for `ms`; a reading every 100 ms if `current` is given."""
        duties = []
        for k in range(0, ms, step):
            self.now = (self.now + step) % 2 ** 32
            duties.append(self.lib.pump_service(self.p, self.now))
            if current is not None and (k + step) % 100 == 0:
                self.lib.pump_reading(self.p, self.now, current, 1)
        return duties


def test_starts_idle_with_the_pump_off(lib):
    r = Rig(lib)
    assert r.run(50) == [0.0] * 50 and r.p.state == IDLE


def test_soft_start_ramps_the_duty_linearly_to_the_request(lib):
    r = Rig(lib)
    lib.pump_request(r.p, 1.0)
    d = r.run(1200)
    assert d[0] == pytest.approx(0.001) and d[499] == pytest.approx(0.5, abs=1e-3)
    assert r.p.state == RUN and d[-1] == 1.0
    assert max(b - a for a, b in zip([0.0] + d, d)) <= 0.001 + 1e-6      # never a jump
    assert next(k for k, x in enumerate(d) if x == 1.0) == pytest.approx(999, abs=2)


def test_ramp_stops_at_a_partial_request(lib):
    r = Rig(lib)
    lib.pump_request(r.p, 0.6)
    assert r.run(2000)[-1] == pytest.approx(0.6) and r.p.state == RUN


def test_a_stalled_loop_does_not_turn_the_ramp_into_a_jump(lib):
    r = Rig(lib)
    lib.pump_request(r.p, 1.0)
    r.run(100)                                            # duty 0.1
    r.now += 2000                                         # the loop was busy for 2 s
    assert lib.pump_service(r.p, r.now) == pytest.approx(0.1 + 20 / 1000, abs=1e-3)


def test_a_request_below_the_minimum_running_duty_keeps_the_pump_off(lib):
    r = Rig(lib)
    lib.pump_request(r.p, 0.3)
    assert max(r.run(500)) == 0.0 and r.p.state == IDLE


def test_lowering_or_stopping_acts_at_once(lib):
    r = Rig(lib)
    lib.pump_request(r.p, 1.0)
    r.run(1500)
    lib.pump_request(r.p, 0.6)
    assert r.run(1) == [pytest.approx(0.6)]
    lib.pump_request(r.p, 0.0)
    assert r.run(1) == [0.0] and r.p.state == IDLE


def test_over_current_cuts_the_pump_after_three_readings_in_a_row(lib):
    r = Rig(lib)
    lib.pump_request(r.p, 1.0)
    r.run(1500, current=0.6)
    lib.pump_reading(r.p, r.now, 2.0, 1)
    lib.pump_reading(r.p, r.now, 2.0, 1)
    assert r.p.state == RUN                               # two are not enough
    lib.pump_reading(r.p, r.now, 2.0, 1)
    assert r.p.state == TRIP and r.p.trip_reason == OVERCURRENT
    assert r.p.d == 0.0 and r.run(100) == [0.0] * 100     # off in the same call, and stays off
    assert lib.pump_trip_text(OVERCURRENT) == b"over-current"


def test_one_high_reading_between_normal_ones_does_not_trip(lib):
    r = Rig(lib)
    lib.pump_request(r.p, 1.0)
    r.run(1500, current=0.6)
    for i in (2.0, 2.0, 0.6, 2.0, 2.0, 0.6):
        lib.pump_reading(r.p, r.now, i, 1)
    assert r.p.state == RUN


def test_over_current_is_also_watched_during_the_soft_start(lib):
    r = Rig(lib)
    lib.pump_request(r.p, 1.0)
    r.run(300)
    for _ in range(3):
        lib.pump_reading(r.p, r.now, 2.0, 1)
    assert r.p.state == TRIP and r.p.trip_reason == OVERCURRENT


def test_a_trip_is_latched_until_it_is_cleared(lib):
    r = Rig(lib)
    lib.pump_request(r.p, 1.0)
    r.run(1500)
    for _ in range(3):
        lib.pump_reading(r.p, r.now, 2.0, 1)
    lib.pump_request(r.p, 1.0)                            # asking again is not enough
    assert max(r.run(60_000)) == 0.0 and r.p.state == TRIP
    lib.pump_clear(r.p)
    assert r.p.state == IDLE and r.p.trip_reason == NONE and max(r.run(100)) == 0.0
    lib.pump_request(r.p, 1.0)                            # a new start is a new soft start
    assert r.run(1)[0] == pytest.approx(0.001)


def test_low_current_while_priming_is_not_a_dry_run(lib):
    r = Rig(lib)
    lib.pump_request(r.p, 1.0)
    r.run(9000, current=0.05)
    assert r.p.state == RUN


def test_dry_run_cuts_the_pump_after_priming_time_plus_three_seconds(lib):
    r = Rig(lib)
    lib.pump_request(r.p, 1.0)
    r.run(12_900, current=0.05)                           # prime 10 s, then < 3 s of low current
    assert r.p.state == RUN
    r.run(300, current=0.05)
    assert r.p.state == TRIP and r.p.trip_reason == DRY and r.p.d == 0.0


def test_current_coming_back_restarts_the_dry_run_timer(lib):
    r = Rig(lib)
    lib.pump_request(r.p, 1.0)
    r.run(10_000, current=0.6)
    r.run(2000, current=0.05)
    r.run(200, current=0.6)                               # water again
    r.run(2500, current=0.05)
    assert r.p.state == RUN
    r.run(700, current=0.05)
    assert r.p.state == TRIP and r.p.trip_reason == DRY


def test_readings_that_fail_three_times_in_a_row_stop_the_pump(lib):
    r = Rig(lib)
    lib.pump_request(r.p, 1.0)
    r.run(1500, current=0.6)
    for _ in range(2):
        lib.pump_reading(r.p, r.now, float("nan"), 0)
    assert r.p.state == RUN
    lib.pump_reading(r.p, r.now, float("nan"), 0)
    assert r.p.state == TRIP and r.p.trip_reason == SENSOR  # no current reading = no protection


def test_readings_while_the_pump_is_off_never_trip(lib):
    r = Rig(lib)
    for i, ok in ((0.0, 1), (5.0, 1), (float("nan"), 0)) * 5:
        lib.pump_reading(r.p, r.now, i, ok)
    assert r.p.state == IDLE


def test_timing_survives_the_millis_wrap(lib):
    r = Rig(lib, t0=2 ** 32 - 5000)
    lib.pump_request(r.p, 1.0)
    d = r.run(13_500, current=0.05)                       # wraps 5 s after the start
    assert d[999] == pytest.approx(1.0, abs=2e-3) and d[12_000] == 1.0
    assert r.p.state == TRIP and r.p.trip_reason == DRY


# --- the board's settings ----------------------------------------------------------------------

def test_every_setting_of_the_logic_comes_from_a_named_constant_in_pv_config():
    import re
    body = re.search(r"pump_cfg_t pvPumpConfig\(\) \{(.*?)\n\}", (SRC / "pv_pump.cpp").read_text(),
                     re.S).group(1)
    lines = dict(re.findall(r"c\.(\w+) = (\w+);", body))
    assert sorted(lines) == sorted(f for f, _ in Cfg._fields_)           # none missing
    constants = dict(re.findall(r"constexpr \w+ (PUMP_\w+) = ([-\d.]+)f?;",
                                (SRC / "pv_config.h").read_text()))
    assert set(lines.values()) <= set(constants)
    k = {name: float(v) for name, v in constants.items()}
    assert 0 < k["PUMP_I_DRY_A"] < k["PUMP_I_TRIP_A"] < 10.0             # inside the INA226 range
    assert 0 < k["PUMP_D_MIN"] <= 1 and k["PUMP_RAMP_MAX_STEP_MS"] < k["PUMP_RAMP_MS"]


def test_the_pump_stays_off_by_default_and_never_starts_by_itself():
    config = (SRC / "pv_config.h").read_text()
    assert "#ifndef ENABLE_PUMP\n#define ENABLE_PUMP 0" in config
    ino = (SRC.parents[2] / "logger/logger.ino").read_text(encoding="utf-8")
    assert ino.count("pump.request(") == 1                # only the typed PUMP command
    assert ino.split("void setup() {")[1].lstrip().startswith("pvOutputsOff();")
