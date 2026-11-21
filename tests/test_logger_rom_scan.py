"""The ROMS command of the logger (follow-up of 2026-11-21): one bus scan -> the whole
T_ROM statement of logger.ino, ready to paste. The block is built by pvlog_trom_block
(pv_log_logic.c), compiled here on the PC. What these tests cannot show: the real 1-Wire
scan and the real temperatures (BRINGUP step 5, on the bench)."""
import ctypes
import importlib.util
import math
import pathlib
import re
import subprocess

import pytest

from scada.ingest import RowParser

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("logger_sim", ROOT / "firmware/tools/logger_sim.py")
ls = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ls)

pytestmark = pytest.mark.skipif(ls.find_gcc() is None, reason="no gcc")
SKETCH = (ROOT / "firmware/logger/logger.ino").read_text(encoding="utf-8")
T_ROM_LINE = re.compile(r"^static const uint8_t T_ROM\[PVLOG_N_T\]\[8\] = .*?;$", re.M | re.S)


def probe(k):
    """A ROM for probe k with a CORRECT CRC (a real scan only returns those)."""
    return ls.with_crc(ls.rom(k)[:7])


def table(block):
    """The 7 ROMs of a printed block, by compiling it as C (what pasting it does)."""
    start = block.index("static const uint8_t T_ROM[PVLOG_N_T][8] = {")
    code = "\n".join(block[start:block.index("};") + 1])
    src = ("#include <stdint.h>\n#include <stdio.h>\n#define PVLOG_N_T 7\n" + code + "\n"
           "int main(void) { for (int k = 0; k < PVLOG_N_T; k++) { for (int b = 0; b < 8; b++)"
           " printf(\"%02X\", T_ROM[k][b]); printf(\"\\n\"); } return 0; }\n")
    work = pathlib.Path(ls.tempfile.mkdtemp(prefix="trom_"))
    (work / "t.c").write_text(src)
    subprocess.run([ls.find_gcc(), "-std=c99", "-Wall", "-Wextra", "-Werror", str(work / "t.c"),
                    "-o", str(work / "t")], check=True)
    out = subprocess.run([str(work / "t")], capture_output=True, text=True, check=True).stdout
    return [bytes.fromhex(x) for x in out.split()]


def test_the_crc_is_the_maxim_one():
    so = ls.lib()
    an27 = bytes([0x02, 0x1C, 0xB8, 0x01, 0x00, 0x00, 0x00, 0xA2])   # Maxim App Note 27 example
    assert so.pvlog_rom_crc_ok((ctypes.c_uint8 * 8)(*an27)) == 1
    assert so.pvlog_rom_crc_ok((ctypes.c_uint8 * 8)(*an27[:7], 0xA3)) == 0
    assert ls.with_crc(an27[:7]) == an27


def test_the_block_replaces_the_statement_of_the_sketch_exactly():
    """The sketch has exactly one T_ROM statement, and the block's statement starts as it
    does, so pasting it over that statement is all there is to do."""
    assert len(T_ROM_LINE.findall(SKETCH)) == 1
    lines, _ = ls.trom_block([None] * 7, [probe(k) for k in range(7)])
    statement = "\n".join(lines[lines.index("static const uint8_t T_ROM[PVLOG_N_T][8] = {"):
                                lines.index("};") + 1])
    pasted = T_ROM_LINE.sub(lambda _: statement, SKETCH)
    assert len(T_ROM_LINE.findall(pasted)) == 1 and statement in pasted


def test_empty_table_seven_probes_fills_every_row_in_bus_order():
    roms = [probe(k) for k in (3, 0, 6, 1, 5, 2, 4)]                # the bus search order
    lines, placed = ls.trom_block([None] * 7, roms, temps=[30.0 + k for k in range(7)],
                                  when="2026-11-25,06:00:00")
    assert placed == 7
    assert table(lines) == roms                                      # T1 = bus #0, ...
    rows = [l for l in lines if l.lstrip().startswith("{")]
    assert len(rows) == 7
    assert all("NEW" in r and "position NOT checked" in r for r in rows)
    assert "// T1 NEW (bus #0), position NOT checked, 30.00 C" in rows[0]
    assert "UTC 2026-11-25,06:00:00" in lines[0]
    assert "7 DS18B20 on the bus; 0 row(s) kept, 7 new, 0 empty" in lines[1]


def test_filled_rows_keep_their_place_and_new_probes_take_the_empty_ones():
    t_rom = [probe(0), None, probe(2), None, None, None, None]
    roms = [probe(5), probe(2), probe(0), probe(9)]                  # 2 known, 2 new
    lines, placed = ls.trom_block(t_rom, roms, temps=[41.0, 42.0, 43.0, math.nan])
    got = table(lines)
    assert placed == 2
    assert got[0] == probe(0) and got[2] == probe(2)                 # kept where they were
    assert got[1] == probe(5) and got[3] == probe(9)                 # new: first empty rows
    assert got[4:] == [bytes(8)] * 3                                 # the rest stays {0}
    assert any("T1 kept, on the bus (#2), 43.00 C" in l for l in lines)
    assert any("T4 NEW (bus #3), position NOT checked, no reading" in l for l in lines)
    assert sum("empty: its column stays empty" in l for l in lines) == 3


def test_a_filled_row_whose_probe_is_gone_is_kept_and_named():
    t_rom = [probe(0), probe(1)] + [None] * 5
    lines, placed = ls.trom_block(t_rom, [probe(0)])
    assert placed == 0 and table(lines)[1] == probe(1)               # never removed silently
    assert any(l.startswith("// T2: ") and "is not on the bus" in l for l in lines)


def test_heatsink_bad_crc_and_too_many_probes_are_not_placed():
    heatsink = probe(8)
    bad = ls.rom(9)[:7] + bytes([(probe(9)[7] + 1) & 0xFF])          # CRC off by one
    roms = [heatsink, bad] + [probe(k) for k in range(8)]            # 8 good unknown ones
    lines, placed = ls.trom_block([None] * 7, roms, known=[heatsink])
    got = table(lines)
    assert placed == 7 and heatsink not in got and bad not in got
    assert got == [probe(k) for k in range(7)]
    notes = [l for l in lines if l.startswith("// not placed:")]
    assert any("a known_rom (heatsink)" in l for l in notes)
    assert any("CRC BAD" in l for l in notes)
    assert any("all 7 rows are taken" in l for l in notes)


def test_a_clone_pattern_is_flagged_but_placed():
    clone = ls.with_crc(bytes([0x28, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66]))   # bytes 5, 6 not 0
    lines, placed = ls.trom_block([None] * 7, [clone])
    assert placed == 1 and table(lines)[0] == clone
    assert any("T1 NEW" in l and "clone?" in l for l in lines)


def test_no_probe_on_the_bus_gives_a_valid_block_of_seven_empty_rows():
    lines, placed = ls.trom_block([None] * 7, [])
    assert placed == 0 and table(lines) == [bytes(8)] * 7
    assert lines[-1] == "// ---- end of T_ROM block ----"


def test_every_line_of_the_block_is_skipped_by_the_scada_serial_ingest():
    lines, _ = ls.trom_block([probe(0)] + [None] * 6, [probe(0), probe(1)], known=[probe(5)])
    p = RowParser("serial:COM3")
    assert [p.parse(l, 0.0)[0] for l in lines] == ["skip"] * len(lines)
    assert p.bad_lines == 0
    assert all(len(l) < 160 for l in lines)                          # the C buffer's size


def test_the_sketch_answers_roms_in_every_build_and_reads_commands_without_the_load():
    assert 'if (!strcmp(c, "ROMS")) romsCommand();' in SKETCH
    assert "if (!HAS_LOAD) readCommands();" in SKETCH                # HAS_LOAD 0 reads Serial too
    assert "pvlog_trom_block(&plog.cfg, probes.rom, temp, probes.count" in SKETCH
