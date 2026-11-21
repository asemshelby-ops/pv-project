"""The flash ring buffer's bookkeeping (firmware/libraries/PvDaq/src/pv_ring_logic.c),
compiled on the PC. It decides which segment file a row goes to and when the oldest
segment must go; the files themselves (LittleFS) exist only on the board and are not
tested here: that is a bench check (pull the card, put it back)."""
import ctypes
import pathlib
import subprocess

import pytest

from export import run as ex

SRC = pathlib.Path(__file__).resolve().parent.parent / "firmware/libraries/PvDaq/src"
SEG_ROWS = 60

pytestmark = pytest.mark.skipif(ex.find_gcc() is None, reason="no gcc")


class Ring(ctypes.Structure):          # mirrors fring_t (pv_ring_logic.h), same order
    _fields_ = [("first", ctypes.c_uint32), ("next", ctypes.c_uint32),
                ("open_rows", ctypes.c_uint32), ("open", ctypes.c_uint32)]


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    dll = tmp_path_factory.mktemp("ring") / "pv_ring_logic.dll"
    subprocess.run([ex.find_gcc(), "-std=c99", "-O2", "-Wall", "-Wextra", "-Werror", "-shared",
                    "-static-libgcc", str(SRC / "pv_ring_logic.c"), "-o", str(dll)], check=True)
    so = ctypes.CDLL(str(dll))
    u32, ptr = ctypes.c_uint32, ctypes.POINTER(Ring)
    so.fring_init.argtypes = [ptr, u32, u32]
    so.fring_count.argtypes, so.fring_count.restype = [ptr], u32
    so.fring_append.argtypes = [ptr, u32, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
    so.fring_append.restype = u32
    so.fring_must_drop.argtypes = [ptr, u32, u32, u32, u32]
    so.fring_drop_oldest.argtypes, so.fring_drop_oldest.restype = [ptr], u32
    so.fring_sizeof.restype = u32
    assert so.fring_sizeof() == ctypes.sizeof(Ring)
    return so


def new_ring(lib, first=0, nxt=0):
    r = Ring()
    lib.fring_init(r, first, nxt)
    return r


def append(lib, r, new_target=0):
    is_new = ctypes.c_int(-1)
    seq = lib.fring_append(r, SEG_ROWS, new_target, is_new)
    return seq, is_new.value


def test_first_row_creates_segment_zero_and_the_next_rows_join_it(lib):
    r = new_ring(lib)
    assert lib.fring_count(r) == 0
    assert append(lib, r) == (0, 1)
    assert append(lib, r) == (0, 0)
    assert lib.fring_count(r) == 1


def test_a_full_segment_is_followed_by_a_new_one(lib):
    r = new_ring(lib)
    rows = [append(lib, r) for _ in range(SEG_ROWS + 1)]
    assert rows[:SEG_ROWS] == [(0, 1)] + [(0, 0)] * (SEG_ROWS - 1)
    assert rows[SEG_ROWS] == (1, 1)
    assert lib.fring_count(r) == 2


def test_a_row_for_another_day_file_starts_a_new_segment(lib):
    r = new_ring(lib)
    append(lib, r)
    assert append(lib, r, new_target=1) == (1, 1)
    assert append(lib, r) == (1, 0)


def test_segments_left_by_an_earlier_boot_are_never_appended_to(lib):
    r = new_ring(lib, first=3, nxt=7)             # files 3, 4, 5, 6 found on the flash
    assert lib.fring_count(r) == 4
    assert append(lib, r) == (7, 1)
    assert lib.fring_count(r) == 5


def test_the_oldest_segment_goes_when_the_flash_is_nearly_full(lib):
    r = new_ring(lib)
    append(lib, r)
    assert lib.fring_must_drop(r, 900_000, 1_000_000, 64_000, 100) == 0
    assert lib.fring_must_drop(r, 940_000, 1_000_000, 64_000, 100) == 1


def test_the_oldest_segment_goes_when_there_are_too_many(lib):
    r = new_ring(lib, first=0, nxt=5)
    assert lib.fring_must_drop(r, 0, 1_000_000, 64_000, 6) == 0
    assert lib.fring_must_drop(r, 0, 1_000_000, 64_000, 5) == 1


def test_an_empty_ring_has_nothing_to_drop_however_full_the_flash_is(lib):
    r = new_ring(lib)
    assert lib.fring_must_drop(r, 1_000_000, 1_000_000, 64_000, 100) == 0


def test_dropping_goes_oldest_first_until_the_ring_is_empty(lib):
    r = new_ring(lib, first=3, nxt=6)
    assert [lib.fring_drop_oldest(r) for _ in range(3)] == [3, 4, 5]
    assert lib.fring_count(r) == 0


def test_dropping_the_open_segment_closes_it(lib):
    r = new_ring(lib)
    append(lib, r)                                 # segment 0, open
    assert lib.fring_drop_oldest(r) == 0            # e.g. copied to the card
    assert append(lib, r) == (1, 1)                # the next row starts a new file
