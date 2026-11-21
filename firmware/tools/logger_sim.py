"""The logger's row logic on the PC, with FAKE sensor drivers.

firmware/libraries/PvDaq/src/pv_log_logic.c is the code the ESP32 runs for every 10 s row.
Here it is compiled with gcc and driven by fake drivers that can be told to fail (a read
error), to time out (the call takes its whole time-out) or to disappear (no answer at all,
as when a cable is pulled). Nothing here is a measurement: every reading is made up, and
the time each fake call "takes" is an [EST] taken from the driver's own time-out constants,
only so that the length of a worst-case row can be bounded.

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
C_FILES = ["pv_log_logic.c"]

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
    _LIB = so
    return so


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


class Sim:
    """The logger loop on the PC: pvlog_read -> row text -> card, events -> /events.csv.
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
        self.files, self.rows, self.cycle_ms = {}, [], []
        self.n = 0

    def header(self):
        return self.so.pvlog_header().decode()

    def get(self, what, sensor="T1"):
        return self.so.pvlog_get(self.L, what.encode(), S[sensor])

    def _append(self, path, line, header):
        if path not in self.files:
            self.files[path] = list(header)
        self.files[path].append(line)

    def events(self):
        """The /events.csv lines as (event, detail)."""
        return [tuple(l.split(",")[3:5]) for l in self.files.get("/events.csv", [])[1:]]

    def cycle(self, p=math.nan, alarm=0):
        """One 10 s row. Returns it as a dict of the CSV fields (text)."""
        rig, so = self.rig, self.so
        rig.ms = max(rig.ms, BOOT_MS + self.n * PERIOD_MS)       # the 10 s grid
        start = rig.ms
        row = Row()
        so.pvlog_read(self.L, ctypes.byref(self.io), ctypes.byref(row))
        row.p, row.alarm = p, alarm
        path = ctypes.create_string_buffer(20)
        so.pvlog_day_path(self.L, ctypes.byref(row), path)
        row.sens_fail |= so.pvlog_note_sd(self.L, 1)
        line = ctypes.create_string_buffer(400)
        assert so.pvlog_format_row(self.L, ctypes.byref(row), 0, 0, line, 400) > 0
        rig.ms += 30                                             # one SD append [EST]
        self._append(path.value.decode(), line.value.decode(),
                     ["# nasim-logger-csv v2", self.header()])
        while so.pvlog_next_event(self.L, rig.ms & 0xFFFFFFFF, line, 400):
            rig.serial.append("# event: " + line.value.decode())
            self._append("/events.csv", line.value.decode(), ["date,time,uptime_ms,event,detail"])
        so.pvlog_end_cycle(self.L, rig.ms & 0xFFFFFFFF)
        self.cycle_ms.append(rig.ms - start)
        self.n += 1
        fields = dict(zip(self.header().split(","), self.files[path.value.decode()][-1].split(",")))
        fields["_path"] = path.value.decode()
        self.rows.append(fields)
        return fields

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


def main():
    for title, make in (("One DS18B20 (T3) missing for 6 rows", scenario_probe_missing),
                        ("PaHub absent for 6 rows", scenario_pahub_absent)):
        sim = make()
        print(f"\n== {title} (SIMULATION with fake drivers; values are made up) ==")
        print(table(sim.rows))
        print("-- /events.csv")
        print("\n".join(sim.files.get("/events.csv", ["(none)"])))


if __name__ == "__main__":
    main()
