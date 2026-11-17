"""NASIM update timing on the board (firmware/libraries/PvDaq/src/pv_timing.c), compiled on
the PC: what goes into the hourly line of /status_log.csv. Only the arithmetic and the
text are tested here; the times themselves exist only once the board has run."""
import ctypes
import pathlib
import re
import subprocess

import pytest

from export import run as ex

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "firmware/libraries/PvDaq/src"
HOUR_MS = 3600000

pytestmark = pytest.mark.skipif(ex.find_gcc() is None, reason="no gcc")


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    dll = tmp_path_factory.mktemp("timing") / "pv_timing.dll"
    subprocess.run([ex.find_gcc(), "-std=c99", "-O2", "-Wall", "-Wextra", "-Werror", "-shared",
                    "-fPIC", "-static-libgcc", str(SRC / "pv_timing.c"), "-o", str(dll)],
                   check=True)
    so = ctypes.CDLL(str(dll))
    u32, ptr = ctypes.c_uint32, ctypes.c_void_p
    so.timing_init.argtypes = [ptr, u32, u32]
    so.timing_add.argtypes = [ptr, u32, ctypes.c_int]
    so.timing_due.argtypes, so.timing_due.restype = [ptr, u32], ctypes.c_int
    so.timing_line.argtypes = [ptr, u32, ctypes.c_char_p, u32]
    so.timing_line.restype = ctypes.c_int
    so.timing_header.restype = ctypes.c_char_p
    so.timing_sizeof.restype = u32
    return so


def new(lib, now=0):
    mem = ctypes.create_string_buffer(lib.timing_sizeof())
    t = ctypes.cast(mem, ctypes.c_void_p)
    lib.timing_init(t, HOUR_MS, now)
    return mem, t


def line(lib, t, now):
    buf = ctypes.create_string_buffer(128)
    n = lib.timing_line(t, now, buf, 128)
    assert n == len(buf.value)
    return buf.value.decode()


def test_one_line_per_hour_with_mean_and_slowest_update(lib):
    _, t = new(lib)
    for k in range(60):                              # the first 10 min: buffers filling
        lib.timing_add(t, 30 + k % 5, 0)
    for us in (880, 900, 1060, 890):
        lib.timing_add(t, us, 1)
    assert not lib.timing_due(t, HOUR_MS - 1) and lib.timing_due(t, HOUR_MS)
    text = line(lib, t, HOUR_MS)
    assert text == "4,932.5,1060,60,32.0,34"
    assert len(text.split(",")) == len(lib.timing_header().decode().split(","))
    assert lib.timing_header().decode().startswith("nasim_n,nasim_us_mean,nasim_us_max,")
    # the next hour starts empty, and is due one period after the line was written
    assert not lib.timing_due(t, 2 * HOUR_MS - 1) and lib.timing_due(t, 2 * HOUR_MS)
    assert line(lib, t, 2 * HOUR_MS) == "0,,0,0,,0"   # nothing measured: means are empty


def test_period_survives_the_millis_wrap(lib):
    start = 2 ** 32 - 1000
    _, t = new(lib, start)
    lib.timing_add(t, 500, 1)
    assert not lib.timing_due(t, (start + HOUR_MS - 1) & 0xFFFFFFFF)
    assert lib.timing_due(t, (start + HOUR_MS) & 0xFFFFFFFF)


def test_logger_uses_it_only_behind_its_flag():
    ino = (ROOT / "firmware/logger/logger.ino").read_text()
    block = re.search(r"#if NASIM_TIMING_LOG\n(.*?gbt_update.*?)#else\n(.*?)#endif", ino, re.S)
    assert "micros()" in block.group(1) and "timing_add(&nasim_timing" in block.group(1)
    assert block.group(2).strip() == "if (gbt_update(&det, &s)) alarm |= NASIM_ALARM_NASIM;"
    assert "/status_log.csv" in ino and "TIMING_PERIOD_MS = 3600000" in ino
    assert re.search(r"#ifndef NASIM_TIMING_LOG\n(//.*\n)*#define NASIM_TIMING_LOG 0\n#endif",
                     (SRC / "pv_config.h").read_text())
