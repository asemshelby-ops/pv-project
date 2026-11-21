"""Flow-sensor arithmetic (firmware/libraries/PvDaq/src/pv_flow_logic.c), compiled on the PC:
pulses since the last reading of a hardware counter that starts again at 0 when it reaches
its limit, and litres per minute from pulses. The counting itself (ESP32 PCNT on GPIO35) is
hardware and is not tested here."""
import ctypes
import math
import pathlib
import subprocess

import pytest

from export import run as ex

SRC = pathlib.Path(__file__).resolve().parent.parent / "firmware/libraries/PvDaq/src"
LIMIT = 30000

pytestmark = pytest.mark.skipif(ex.find_gcc() is None, reason="no gcc")


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    dll = tmp_path_factory.mktemp("flow") / "pv_flow_logic.dll"
    subprocess.run([ex.find_gcc(), "-std=c99", "-O2", "-Wall", "-Wextra", "-Werror", "-shared",
                    "-static-libgcc", str(SRC / "pv_flow_logic.c"), "-o", str(dll), "-lm"],
                   check=True)
    so = ctypes.CDLL(str(dll))
    so.flow_pulses_since.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int]
    so.flow_pulses_since.restype = ctypes.c_uint32
    so.flow_l_per_min.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_float]
    so.flow_l_per_min.restype = ctypes.c_float
    return so


def test_pulses_since_the_last_reading(lib):
    assert lib.flow_pulses_since(150, 100, LIMIT) == 50
    assert lib.flow_pulses_since(100, 100, LIMIT) == 0


def test_pulses_are_not_lost_when_the_counter_starts_again_at_zero(lib):
    assert lib.flow_pulses_since(20, LIMIT - 30, LIMIT) == 50
    assert lib.flow_pulses_since(0, LIMIT - 1, LIMIT) == 1


def test_litres_per_minute_from_pulses(lib):
    assert lib.flow_l_per_min(450, 60_000, 450.0) == pytest.approx(1.0)
    assert lib.flow_l_per_min(150, 10_000, 450.0) == pytest.approx(2.0)
    assert lib.flow_l_per_min(0, 10_000, 450.0) == 0.0


def test_no_flow_figure_without_a_calibration_or_a_time_span(lib):
    assert math.isnan(lib.flow_l_per_min(150, 10_000, 0.0))     # pulses per litre not measured
    assert math.isnan(lib.flow_l_per_min(150, 0, 450.0))
