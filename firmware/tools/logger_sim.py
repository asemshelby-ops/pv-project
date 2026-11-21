"""The logger's row logic on the PC, with FAKE sensor drivers, a FAKE SD card and a FAKE
internal flash.

firmware/libraries/PvDaq/src/pv_log_logic.c and pv_store_logic.c are the code the ESP32
runs for every 10 s row. Here they are compiled with gcc and driven by fake drivers that
can be told to fail (a read error), to time out (the call takes its whole time-out) or to
disappear (no answer at all, as when a cable is pulled); the fake card can be pulled out
and put back, and both it and the fake flash can refuse writes. Nothing here is a
measurement: every reading is made up, and the time each fake call "takes" is an [EST]
taken from the driver's own time-out constants, only so that the length of a worst-case
row can be bounded.

    python firmware/tools/logger_sim.py            prints the scenarios as CSV rows

Used by tests/test_logger_robustness.py.
"""
import ctypes
import math
import pathlib
import shutil
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = ROOT / "firmware/libraries/PvDaq/src"
C_FILES = ["pv_log_logic.c", "pv_store_logic.c"]

N_T = 7
SENSORS = ["T1", "T2", "T3", "T4", "T5", "T6", "T7", "env", "rtc", "hub", "ina", "pyr", "sd"]
S = {name: k for k, name in enumerate(SENSORS)}
PERIOD_MS = 10000
T0_UNIX = 1795586400            # 2026-11-25 06:00:00 UTC, an arbitrary start
BOOT_MS = 5000                  # uptime of the first row

u8, u32, i32, f32 = ctypes.c_uint8, ctypes.c_uint32, ctypes.c_int32, ctypes.c_float
ptr = ctypes.c_void_p
PF = ctypes.POINTER(ctypes.c_float)
PU8 = ctypes.POINTER(ctypes.c_uint8)


class Io(ctypes.Structure):           # mirrors pvlog_io_t (pv_log_logic.h), same order
    _fields_ = [
        ("ctx", ptr),
        ("millis", ctypes.CFUNCTYPE(u32, ptr)),
        ("begin", ctypes.CFUNCTYPE(ctypes.c_int, ptr, ctypes.c_int)),
        ("detail", ctypes.CFUNCTYPE(ptr, ptr, ctypes.c_int)),
        ("rtc_read", ctypes.CFUNCTYPE(ctypes.c_int, ptr, ctypes.POINTER(u32),
                                      ctypes.POINTER(ctypes.c_int))),
        ("pyr_read", ctypes.CFUNCTYPE(ctypes.c_int, ptr, PF)),
        ("pyr_analog", ctypes.CFUNCTYPE(ctypes.c_int, ptr, PF)),
        ("env_read", ctypes.CFUNCTYPE(ctypes.c_int, ptr, PF, PF)),
        ("hub_check", ctypes.CFUNCTYPE(ctypes.c_int, ptr)),
        ("ina_read", ctypes.CFUNCTYPE(ctypes.c_int, ptr, PF, PF, PF)),
        ("ds_convert", ctypes.CFUNCTYPE(ctypes.c_int, ptr)),
        ("ds_read", ctypes.CFUNCTYPE(ctypes.c_int, ptr, PU8, PF)),
        ("ds_scan", ctypes.CFUNCTYPE(ctypes.c_int, ptr, PU8, ctypes.c_int)),
        ("log", ctypes.CFUNCTYPE(None, ptr, ctypes.c_char_p)),
    ]


class StoreIo(ctypes.Structure):      # mirrors pvstore_io_t (pv_store_logic.h), same order
    _fields_ = [
        ("ctx", ptr),
        ("millis", ctypes.CFUNCTYPE(u32, ptr)),
        ("sd_mount", ctypes.CFUNCTYPE(ctypes.c_int, ptr)),
        ("sd_size", ctypes.CFUNCTYPE(i32, ptr, ctypes.c_char_p)),
        ("sd_append", ctypes.CFUNCTYPE(ctypes.c_int, ptr, ctypes.c_char_p, PU8, ctypes.c_int)),
        ("sd_tail", ctypes.CFUNCTYPE(ctypes.c_int, ptr, ctypes.c_char_p, PU8, ctypes.c_int)),
        ("fs_range", ctypes.CFUNCTYPE(ctypes.c_int, ptr, ctypes.POINTER(u32),
                                      ctypes.POINTER(u32))),
        ("fs_size", ctypes.CFUNCTYPE(i32, ptr, u32)),
        ("fs_append", ctypes.CFUNCTYPE(ctypes.c_int, ptr, u32, PU8, ctypes.c_int)),
        ("fs_read", ctypes.CFUNCTYPE(ctypes.c_int, ptr, u32, u32, PU8, ctypes.c_int)),
        ("fs_lines", ctypes.CFUNCTYPE(i32, ptr, u32, u32)),
        ("fs_remove", ctypes.CFUNCTYPE(ctypes.c_int, ptr, u32)),
        ("fs_save", ctypes.CFUNCTYPE(ctypes.c_int, ptr, ctypes.c_char_p)),
        ("fs_load", ctypes.CFUNCTYPE(ctypes.c_int, ptr, PU8, ctypes.c_int)),
        ("log", ctypes.CFUNCTYPE(None, ptr, ctypes.c_char_p)),
    ]


class Cfg(ctypes.Structure):          # mirrors pvlog_cfg_t
    _fields_ = [("t_rom", (u8 * 8) * N_T), ("known_rom", (u8 * 8) * 2),
                ("fail_report_after", u32), ("scan_every", u32)]


class Row(ctypes.Structure):          # mirrors pvlog_row_t
    _fields_ = [("p", ctypes.c_double), ("uptime_ms", u32), ("unix_s", u32), ("have_time", i32),
                ("t", f32 * N_T), ("ta", f32), ("rh", f32), ("g", f32), ("v", f32), ("i", f32),
                ("p_el", f32), ("pyr_mv", f32), ("d", f32), ("alarm", i32), ("sweep_id", u32),
                ("status", u32), ("sens_fail", u32), ("load_mode", ctypes.c_char_p)]


def find_gcc():
    return shutil.which("gcc")


_LIB = None


def lib():
    """The logic compiled as a shared library (once per process), warnings as errors."""
    global _LIB
    if _LIB is not None:
        return _LIB
    out = pathlib.Path(tempfile.mkdtemp(prefix="pvlog_")) / "pv_log_logic.dll"
    subprocess.run([find_gcc(), "-std=c99", "-O2", "-Wall", "-Wextra", "-Werror", "-shared",
                    "-fPIC", "-static-libgcc", *[str(SRC / f) for f in C_FILES], "-o", str(out),
                    "-lm"], check=True)
    so = ctypes.CDLL(str(out))
    so.pvlog_init.argtypes = [ptr, ctypes.POINTER(Cfg)]
    so.pvlog_read.argtypes = [ptr, ctypes.POINTER(Io), ctypes.POINTER(Row)]
    so.pvlog_note_sd.argtypes = [ptr, ctypes.c_int]
    so.pvlog_note_sd.restype = u32
    so.pvlog_day_path.argtypes = [ptr, ctypes.POINTER(Row), ctypes.c_char_p]
    so.pvlog_header.restype = ctypes.c_char_p
    so.pvlog_format_row.argtypes = [ptr, ctypes.POINTER(Row), u32, u32, ctypes.c_char_p,
                                    ctypes.c_int]
    so.pvlog_event.argtypes = [ptr, ctypes.c_char_p, ctypes.c_char_p]
    so.pvlog_next_event.argtypes = [ptr, u32, ctypes.c_char_p, ctypes.c_int]
    so.pvlog_end_cycle.argtypes = [ptr, u32]
    so.pvlog_utc_text.argtypes = [u32, ctypes.c_char_p, ctypes.c_int]
    so.pvlog_time_at.argtypes = [ptr, u32, ctypes.POINTER(u32)]
    so.pvlog_sensor_name.argtypes = [ctypes.c_int]
    so.pvlog_sensor_name.restype = ctypes.c_char_p
    so.pvlog_rom_text.argtypes = [PU8, ctypes.c_char_p]
    so.pvlog_get.argtypes = [ptr, ctypes.c_char_p, ctypes.c_int]
    so.pvlog_get.restype = u32
    for f in (so.pvlog_sizeof, so.pvlog_row_sizeof, so.pvlog_cfg_sizeof, so.pvlog_io_sizeof):
        f.restype = u32
    assert so.pvlog_row_sizeof() == ctypes.sizeof(Row)      # the mirrors above are in step
    assert so.pvlog_cfg_sizeof() == ctypes.sizeof(Cfg)
    assert so.pvlog_io_sizeof() == ctypes.sizeof(Io)
    so.pvlog_commit.argtypes = [ptr, ptr, ctypes.POINTER(StoreIo), ctypes.POINTER(Row),
                                ctypes.c_char_p, ctypes.c_int]
    so.pvlog_file_head.restype = ptr     # kept as a pointer: the store does not copy it
    so.pvstore_init.argtypes = [ptr, ptr, ctypes.c_char_p, ctypes.c_char_p]
    so.pvstore_begin.argtypes = [ptr, ctypes.POINTER(StoreIo)]
    so.pvstore_service.argtypes = [ptr, ctypes.POINTER(StoreIo)]
    so.pvstore_put.argtypes = [ptr, ctypes.POINTER(StoreIo), ctypes.c_int, ctypes.c_char_p,
                               ctypes.c_char_p, ctypes.c_int]
    so.pvstore_get.argtypes = [ptr, ctypes.c_char_p]
    so.pvstore_get.restype = u32
    for f in (so.pvstore_sizeof, so.pvstore_io_sizeof, so.pvstore_waiting, so.pvstore_dropped):
        f.restype = u32
    so.pvstore_waiting.argtypes = so.pvstore_dropped.argtypes = [ptr]
    assert so.pvstore_io_sizeof() == ctypes.sizeof(StoreIo)
    so.pvlog_rom_crc_ok.argtypes = [PU8]
    so.pvlog_trom_block.argtypes = [ctypes.POINTER(Cfg), PU8, ctypes.POINTER(ctypes.c_float),
                                    ctypes.c_int, ctypes.c_char_p, EMIT, ptr]
    _LIB = so
    return so


EMIT = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_char_p)


def with_crc(r7):
    """A ROM from its first 7 bytes, with the right Maxim CRC-8 as byte 8."""
    crc = 0
    for b in r7:
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8C if (crc ^ b) & 1 else crc >> 1
            b >>= 1
    return bytes(r7) + bytes([crc])


def trom_block(t_rom, roms, temps=None, known=(), when=None):
    """The ROMS command's output: (lines, new rows placed). t_rom: 7 ROMs or None."""
    so, cfg = lib(), Cfg()
    for k, r in enumerate(t_rom):
        cfg.t_rom[k][:] = list(r or bytes(8))
    for k, r in enumerate(known):
        cfg.known_rom[k][:] = list(r)
    buf = (ctypes.c_uint8 * (8 * max(len(roms), 1)))(*b"".join(roms))
    t = (ctypes.c_float * max(len(roms), 1))(*(temps or [math.nan] * len(roms)))
    lines = []
    cb = EMIT(lambda _, s: lines.append(s.decode()))
    placed = so.pvlog_trom_block(ctypes.byref(cfg), buf, t, len(roms),
                                 when.encode() if when else None, cb, None)
    return lines, placed


def rom(k):
    """A made-up ROM for probe k (family 0x28, bytes 5 and 6 zero like a genuine part)."""
    return bytes([0x28, 0xA0 + k, 0x11 * (k + 1) & 0xFF, 0x3C, 0x5D, 0x00, 0x00, 0xB0 + k])


def rom_text(r):
    return "-".join(f"{b:02X}" for b in r)


class Dev:
    """One fake device.
    present = False   it has disappeared: no answer, begin() and read() fail at once
                      (an I2C NACK), or after `timeout_ms` where the bus has no NACK
                      (RS485: the driver waits its whole reply time-out).
    fail = True       it answers but the read is bad (CRC error, lost mid-read).
    slow = True       the slowest it can be: begin() works but takes as long as the driver
                      allows (begin_max_ms), and the read then runs into its limit and fails
                      (read_max_ms: every I2C transaction of the call running into the 50 ms
                      Wire time-out, the full Modbus reply time-out, ...)."""

    def __init__(self, cost_ms, timeout_ms=0, begin_max_ms=0, read_max_ms=0):
        self.present, self.fail, self.slow = True, False, False
        self.cost_ms, self.timeout_ms = cost_ms, timeout_ms
        self.begin_max_ms, self.read_max_ms = begin_max_ms, read_max_ms
        self.detail = ""
        self.begins = self.reads = 0


class Rig:
    """The fake hardware: a clock, the sensors, the 1-Wire bus. Times are [EST]."""

    def __init__(self, n_probes=N_T):
        self.ms = BOOT_MS
        self.rtc_offset_s = 0          # added to the RTC time (to fake a wrong read)
        self.rtc_valid = True
        # Longest time per call [EST], from the drivers: I2C transactions x 50 ms time-out
        # (pv_config.h I2C_TIMEOUT_MS), the BME280 library's 2 s wait, the 300 ms Modbus
        # reply time-out. Table: docs/paper/13-logger-robustness.md, section 2.
        self.rtc = Dev(cost_ms=2, begin_max_ms=100, read_max_ms=100)
        self.env = Dev(cost_ms=12, begin_max_ms=350, read_max_ms=2700)
        self.hub = Dev(cost_ms=1, begin_max_ms=100, read_max_ms=50)
        self.ina = Dev(cost_ms=4, begin_max_ms=600, read_max_ms=550)
        self.pyr = Dev(cost_ms=25, timeout_ms=300, begin_max_ms=350, read_max_ms=350)
        self.dev = {S["rtc"]: self.rtc, S["env"]: self.env, S["hub"]: self.hub,
                    S["ina"]: self.ina, S["pyr"]: self.pyr}
        self.probes = {rom(k): {"present": True, "t": 40.0 + k, "fail": False}
                       for k in range(n_probes)}
        self.scan_order = list(self.probes)            # the order the bus search returns
        self.bus_shorted = False                       # 1-Wire line held low
        self.convert_ms, self.ds_read_ms, self.scan_ms = 750, 12, 15
        self.g, self.ta, self.rh, self.v, self.i = 800.0, 30.0, 20.0, 18.0, 5.0
        self.serial = []
        self._detail = ctypes.create_string_buffer(64)
        # -- the fake microSD card: {path: bytes}. Times are [EST], not measured.
        self.card = {}
        self.card_in = True            # False = pulled out: mount and every access fail
        self.card_fail_appends = 0     # the next n appends fail and write nothing
        self.card_ghost_appends = 0    # the next n appends REPORT failure but did write
        self.card_torn_appends = 0     # the next n appends write only half, then fail
        self.card_mounted = False
        self.sd_write_ms, self.sd_mount_ms, self.sd_mount_fail_ms = 30, 150, 1500
        self.sd_write_extra = []       # extra ms for the next appends, one per append (a stall)
        self.mounts = 0
        # -- the fake internal flash (LittleFS): ring segments {number: bytes} + state text
        self.flash = {}
        self.flash_state = None
        self.flash_ok = True           # False = the file system cannot be used at all
        self.flash_fail_appends = 0    # the next n appends fail (write nothing)
        self.flash_capacity = 1 << 30  # bytes the segments may hold together
        self.flash_ms = 10

    def reboot(self):
        """A restart of the board (watchdog, power): uptime starts again, the RTC went on,
        and what was on the card and in the flash is still there."""
        self.rtc_offset_s += (self.ms - BOOT_MS) // 1000 + 3     # ~3 s to boot [EST]
        self.ms = BOOT_MS
        self.card_mounted = False

    # -- the driver functions, in the order of pvlog_io_t --------------------------------
    def _use(self, d, begin=False):
        """Time passes; returns True if the device answered."""
        if begin:
            d.begins += 1
        else:
            d.reads += 1
        if d.slow:                        # begin() answers, slowly; the read times out
            self.ms += d.begin_max_ms if begin else d.read_max_ms
            d.detail = "" if begin else "time-out"
            return begin
        if not d.present:
            self.ms += d.timeout_ms
            d.detail = "no answer"
            return False
        self.ms += d.cost_ms
        if d.fail:
            d.detail = "read error"
            return False
        d.detail = ""
        return True

    def millis(self, _):
        return self.ms & 0xFFFFFFFF

    def begin(self, _, dev):
        return int(self._use(self.dev[dev], begin=True))

    def detail(self, _, dev):
        self._detail.value = self.dev[dev].detail.encode() if dev in self.dev else b""
        return ctypes.addressof(self._detail)

    def rtc_read(self, _, unix_s, valid):
        if not self._use(self.rtc):
            return 0
        unix_s[0] = T0_UNIX + (self.ms - BOOT_MS) // 1000 + self.rtc_offset_s
        valid[0] = int(self.rtc_valid)
        return 1

    def pyr_read(self, _, g):
        if not self._use(self.pyr):
            return 0
        g[0] = self.g
        return 1

    def pyr_analog(self, _, mv):
        self.ms += 4
        mv[0] = self.g                     # 1 mV = 1 W/m2 (PYR20 manual)
        return 1

    def env_read(self, _, t, rh):
        if not self._use(self.env):
            return 0
        t[0], rh[0] = self.ta, self.rh
        return 1

    def hub_check(self, _):
        return int(self._use(self.hub))

    def ina_read(self, _, v, i, p):
        if not self.hub.present or not self._use(self.ina):
            return 0
        v[0], i[0], p[0] = self.v, self.i, self.v * self.i
        return 1

    def ds_convert(self, _):
        self.ms += self.convert_ms
        return int(not self.bus_shorted)

    def ds_read(self, _, rom_p, t):
        self.ms += self.ds_read_ms
        p = self.probes.get(bytes(rom_p[:8]))
        if self.bus_shorted or p is None or not p["present"] or p["fail"]:
            return 0
        t[0] = p["t"]
        return 1

    def ds_scan(self, _, roms, max_n):
        found = [r for r in self.scan_order if self.probes[r]["present"]]
        self.ms += self.scan_ms * max(len(found), 1)
        if self.bus_shorted:
            return 0
        for k, r in enumerate(found[:max_n]):
            for b in range(8):
                roms[8 * k + b] = r[b]
        return min(len(found), max_n)

    def log(self, _, text):
        self.serial.append(text.decode())

    def io(self):
        io = Io()
        for name, kind in Io._fields_[1:]:
            setattr(io, name, kind(getattr(self, name)))
        return io

    # -- the card's driver functions, in the order of pvstore_io_t ------------------------------
    def sd_mount(self, _):
        self.mounts += 1
        self.ms += self.sd_mount_ms if self.card_in else self.sd_mount_fail_ms
        self.card_mounted = self.card_in
        return int(self.card_mounted)

    def _card_ok(self):
        if not (self.card_in and self.card_mounted):
            self.card_mounted = False      # an access to a pulled card: mount again first
            return False
        return True

    def sd_size(self, _, path):
        if not self._card_ok():
            return -2
        return len(self.card[path.decode()]) if path.decode() in self.card else -1

    def sd_append(self, _, path, data, n):
        self.ms += self.sd_write_ms + (self.sd_write_extra.pop(0) if self.sd_write_extra else 0)
        if not self._card_ok():
            return 0
        chunk = bytes(data[:n])
        if self.card_fail_appends:
            self.card_fail_appends -= 1
            self.card_mounted = False
            return 0
        if self.card_torn_appends:
            self.card_torn_appends -= 1
            self.card.setdefault(path.decode(), bytearray()).extend(chunk[:n // 2])
            self.card_mounted = False
            return 0
        self.card.setdefault(path.decode(), bytearray()).extend(chunk)
        if self.card_ghost_appends:
            self.card_ghost_appends -= 1
            self.card_mounted = False
            return 0
        return 1

    def sd_tail(self, _, path, buf, n):
        self.ms += 5
        if not self._card_ok() or path.decode() not in self.card:
            return -1
        tail = bytes(self.card[path.decode()][-n:])
        for k, b in enumerate(tail):
            buf[k] = b
        return len(tail)

    # -- the flash's driver functions -------------------------------------------------------------
    def fs_range(self, _, first, last):
        if not self.flash_ok:
            return -1
        if not self.flash:
            return 0
        first[0], last[0] = min(self.flash), max(self.flash)
        return 1

    def fs_size(self, _, seq):
        return len(self.flash[seq]) if self.flash_ok and seq in self.flash else -1

    def fs_append(self, _, seq, data, n):
        self.ms += self.flash_ms
        used = sum(len(v) for v in self.flash.values())
        if not self.flash_ok or used + n > self.flash_capacity:
            return 0
        if self.flash_fail_appends:
            self.flash_fail_appends -= 1
            return 0
        self.flash.setdefault(seq, bytearray()).extend(bytes(data[:n]))
        return 1

    def fs_read(self, _, seq, off, buf, n):
        self.ms += 2
        if not self.flash_ok or seq not in self.flash:
            return -1
        chunk = bytes(self.flash[seq][off:off + n])
        for k, b in enumerate(chunk):
            buf[k] = b
        return len(chunk)

    def fs_lines(self, _, seq, off):
        self.ms += 2
        if not self.flash_ok or seq not in self.flash:
            return -1
        return bytes(self.flash[seq][off:]).count(b"\n")

    def fs_remove(self, _, seq):
        self.ms += self.flash_ms
        return int(self.flash.pop(seq, None) is not None)

    def fs_save(self, _, text):
        self.ms += self.flash_ms
        if not self.flash_ok:
            return 0
        self.flash_state = text
        return 1

    def fs_load(self, _, buf, n):
        if not self.flash_ok or self.flash_state is None:
            return -1
        for k, b in enumerate(self.flash_state[:n]):
            buf[k] = b
        return min(len(self.flash_state), n)

    def store_io(self):
        io = StoreIo()
        for name, kind in StoreIo._fields_[1:]:
            setattr(io, name, kind(getattr(self, name)))
        return io

    def card_lines(self, path):
        """The lines of a file on the fake card (as text, line ends removed)."""
        return self.card.get(path, b"").decode().splitlines()


class Sim:
    """The logger loop on the PC, as logger.ino runs it: pvlog_read, then pvlog_commit
    (card first, LittleFS ring when the card is away). One Sim = one boot of the board;
    Sim(rig=old.rig) after rig.reboot() is the next boot with the same card and flash.
    files: {path: [lines]} = what is on the fake SD card."""

    def __init__(self, rig=None, t_rom=None, known=(), fail_report_after=3, scan_every=6):
        self.so = lib()
        self.rig = rig or Rig()
        cfg = Cfg()
        t_rom = [rom(k) for k in range(N_T)] if t_rom is None else t_rom
        for k, r in enumerate(t_rom):
            cfg.t_rom[k][:] = list(r or bytes(8))
        for k, r in enumerate(known):
            cfg.known_rom[k][:] = list(r)
        cfg.fail_report_after, cfg.scan_every = fail_report_after, scan_every
        self.L = ctypes.create_string_buffer(self.so.pvlog_sizeof())
        self.so.pvlog_init(self.L, ctypes.byref(cfg))
        self.io = self.rig.io()
        self.sio = self.rig.store_io()
        self.S = ctypes.create_string_buffer(self.so.pvstore_sizeof())
        # the store keeps these pointers, so the two Python strings must live as long as it
        self._headers = (ctypes.c_char_p(b"date,time,uptime_ms,event,detail"),
                         ctypes.c_char_p(b"date,time,millis"))
        self.so.pvstore_init(self.S, self.so.pvlog_file_head(), *self._headers)
        ms = self.rig.ms
        self.so.pvstore_begin(self.S, ctypes.byref(self.sio))
        self.rig.ms = ms                 # the time setup() takes before row 0 is not modelled
        self.rows, self.cycle_ms, self.where = [], [], []
        self.n = 0

    def header(self):
        return self.so.pvlog_header().decode()

    def get(self, what, sensor="T1"):
        return self.so.pvlog_get(self.L, what.encode(), S[sensor])

    def store(self, what):
        return self.so.pvstore_get(self.S, what.encode())

    @property
    def files(self):
        return {path: self.rig.card_lines(path) for path in self.rig.card}

    def data_rows(self, path):
        """The rows of a day file on the card as dicts (header and version lines left out)."""
        names = self.header().split(",")
        return [dict(zip(names, line.split(","))) for line in self.rig.card_lines(path)
                if line and not line.startswith(("#", "date,"))]

    def events(self):
        """The /events.csv lines as (event, detail)."""
        return [tuple(l.split(",")[3:5]) for l in self.files.get("/events.csv", [])[1:]]

    def marker(self, text="2026-11-25,06:00:05.123,10123"):
        """A button press: one line for /markers.csv through the same store."""
        return self.so.pvstore_put(self.S, ctypes.byref(self.sio), 2, b"/markers.csv",
                                   text.encode(), 0)

    def cycle(self, p=math.nan, alarm=0):
        """One 10 s row. Returns it as a dict of the CSV fields (text), as it was stored."""
        rig, so = self.rig, self.so
        rig.ms = max(rig.ms, BOOT_MS + self.n * PERIOD_MS)       # the 10 s grid
        start = rig.ms
        row = Row()
        so.pvlog_read(self.L, ctypes.byref(self.io), ctypes.byref(row))
        row.p, row.alarm = p, alarm
        line = ctypes.create_string_buffer(400)
        where = so.pvlog_commit(self.L, self.S, ctypes.byref(self.sio), ctypes.byref(row), line, 400)
        self.cycle_ms.append(rig.ms - start)
        self.where.append(where)
        self.n += 1
        text = line.value.decode()
        fields = dict(zip(self.header().split(","), text.split(",")))
        fields["_path"] = self._find(text)
        fields["_where"] = {0: "LOST", 1: "sd", 2: "sd_retry", 3: "ring"}[where]
        self.rows.append(fields)
        return fields

    def _find(self, text):
        """The file the logger chose for this row: where it is on the card, or the path in
        its ring record."""
        for path in self.rig.card:
            lines = self.rig.card_lines(path)
            if lines and lines[-1] == text:
                return path
        for seq in sorted(self.rig.flash, reverse=True):
            for rec in reversed(self.rig.flash[seq].decode().splitlines()):
                parts = rec.split("\t")
                if len(parts) == 4 and parts[3] == text:
                    return parts[2]
        return None

    def run(self, n, plan=None):
        """n rows; plan(k, rig) is called before row k to change the fake hardware."""
        for _ in range(n):
            if plan:
                plan(self.n, self.rig)
            self.cycle()
        return self.rows


# ---- the scenarios shown in the report ---------------------------------------------------

SHOW = ["time", "T1", "T2", "T3", "T4", "voltage", "current", "status", "sens_fail",
        "f_T3", "f_hub", "f_ina", "cycle_ms"]


def table(rows, cols=SHOW):
    out = [",".join(cols)]
    out += [",".join(r[c] for c in cols) for r in rows]
    return "\n".join(out)


def scenario_probe_missing():
    """T3's probe is unplugged before row 3 and plugged back before row 9: 6 rows."""
    sim = Sim()

    def plan(k, rig):
        rig.probes[rom(2)]["present"] = not 3 <= k < 9
    sim.run(12, plan)
    return sim


def scenario_pahub_absent():
    """The PaHub is unplugged before row 3 and plugged back before row 9."""
    sim = Sim()

    def plan(k, rig):
        rig.hub.present = not 3 <= k < 9
    sim.run(12, plan)
    return sim


SHOW_SD = ["time", "T1", "voltage", "uptime_ms", "status", "sens_fail", "f_sd", "ring_rows",
           "ring_dropped", "cycle_ms"]


def scenario_sd_missing(rows=20, out_from=3, out_rows=12):
    """The card is pulled before row 3 and put back before row 15: 12 rows."""
    sim = Sim()

    def plan(k, rig):
        rig.card_in = not out_from <= k < out_from + out_rows
    sim.run(rows, plan)
    return sim


def main():
    for title, make in (("One DS18B20 (T3) missing for 6 rows", scenario_probe_missing),
                        ("PaHub absent for 6 rows", scenario_pahub_absent)):
        sim = make()
        print(f"\n== {title} (SIMULATION with fake drivers; values are made up) ==")
        print(table(sim.rows))
        print("-- /events.csv")
        print("\n".join(sim.files.get("/events.csv", ["(none)"])))
    sim = scenario_sd_missing()
    print("\n== SD card missing for 12 rows, then back (SIMULATION; values are made up) ==")
    print("-- rows as the logger made them (and printed on Serial), with where each went first")
    print(table(sim.rows, SHOW_SD + ["_where"]))
    print("-- /20261125.csv on the card afterwards")
    print(table(sim.data_rows("/20261125.csv"), SHOW_SD))
    print("-- /events.csv on the card afterwards")
    print("\n".join(sim.files.get("/events.csv", ["(none)"])))


if __name__ == "__main__":
    main()
