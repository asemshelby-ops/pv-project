"""firmware/tools/footprint.py: flash and RAM of the NASIM model from a build, and the
update time from the board's status file.

There is no ESP32 build on the machine that runs these tests, so two stand-ins are used:
  * a real GNU linker map: the same model sources (firmware/logger/nasim_core.c and
    nasim_model.c) compiled and linked for the PC, laid out like an arduino-cli build
    folder. It checks the reader against what the PC tools say about the same files;
  * a hand-written excerpt in the layout of an ESP32 map (32-bit addresses, .flash.text,
    literal pools, Windows paths), with sizes chosen by hand. It checks the names and the
    arithmetic, NOT that a real ESP32 map looks exactly like it.
No number in this file is an ESP32 measurement."""
import importlib.util
import json
import pathlib
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
TOOL = ROOT / "firmware/tools/footprint.py"
spec = importlib.util.spec_from_file_location("footprint", TOOL)
fp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fp)

MAIN = r"""
#include "nasim.h"
void nasim_score_nowind(real *input, real *output);
gbt_detector_t det;
thr_detector_t det_thr;
cusum_inc_t det_cusum;                 /* never used below: the linker must drop it */
int main(void) {
  sample_t s = {{30, 30, 30, 30, 30}, 30, 800, 0, 100, 700};
  gbt_init(&det, nasim_score_nowind, 0, 1, 0.95, 2);
  gbt_reset(&det);
  thr_init(&det_thr, 3.0);
  return gbt_update(&det, &s) + thr_update(&det_thr, &s);
}
"""


@pytest.fixture(scope="module")
def pc_build(tmp_path_factory):
    """The model sources built for the PC into <tmp>/logger, like an arduino-cli build path:
    sketch/*.o, logger.ino.elf and logger.ino.map."""
    if shutil.which("gcc") is None:
        pytest.skip("no gcc")
    build = tmp_path_factory.mktemp("fw") / "logger"
    (build / "sketch").mkdir(parents=True)
    main = build.parent / "main.c"
    main.write_text(MAIN)
    flags = ["-std=c99", "-Os", "-ffunction-sections", "-fdata-sections",
             f"-I{ROOT / 'export/c'}", f"-I{ROOT / 'export/c/generated'}"]
    for src, obj in ((ROOT / "firmware/logger/nasim_core.c", "nasim_core.c.o"),
                     (ROOT / "firmware/logger/nasim_model.c", "nasim_model.c.o"),
                     (main, "logger.ino.cpp.o")):
        subprocess.run(["gcc", *flags, "-c", str(src), "-o", str(build / "sketch" / obj)], check=True)
    subprocess.run(["gcc", "-Wl,--gc-sections", f"-Wl,-Map={build / 'logger.ino.map'}",
                    *map(str, sorted((build / "sketch").glob("*.o"))),
                    "-o", str(build / "logger.ino.elf"), "-lm"], check=True)
    return build


def _size_tool(obj):
    """{section: bytes} from binutils `size -A`."""
    r = subprocess.run(["size", "-A", str(obj)], capture_output=True, text=True, check=True)
    rows = [l.split() for l in r.stdout.splitlines()[2:] if len(l.split()) == 3 and l.split()[0] != "Total"]
    return {name: int(size) for name, size, _ in rows}


# --- object files ---------------------------------------------------------------------------------

def test_object_sizes_agree_with_binutils(pc_build):
    if shutil.which("size") is None:
        pytest.skip("no binutils size")
    for obj in sorted((pc_build / "sketch").glob("*.o")):
        ref = _size_tool(obj)
        mine = fp.object_footprint(obj)
        pick = lambda *prefixes: sum(v for k, v in ref.items() if k.startswith(prefixes))
        assert mine["code"] == pick(".text") and mine["const"] == pick(".rodata")
        assert mine["data"] == pick(".data") and mine["bss"] == pick(".bss")
        names = {n for n, *_ in fp.elf_sections(obj)}
        assert set(ref) <= names
    model = fp.object_footprint(pc_build / "sketch/nasim_model.c.o")
    assert model["code"] > 5000 and model["const"] > 1000 and model["data"] == model["bss"] == 0
    with pytest.raises(ValueError):
        fp.elf_sections(TOOL)


# --- a real linker map (PC) ---------------------------------------------------------------------------

def test_every_byte_of_an_output_section_is_accounted_for(pc_build):
    """The reader's own check: inside .text, .rodata and .bss the pieces (padding included),
    each cut where the next one begins, add up to the size the linker prints for the
    section. Debug and comment sections (address 0) are not read at all."""
    outputs = {name: (addr, size, items) for name, addr, size, items in fp.map_outputs(pc_build / "logger.ino.map")}
    assert {".text", ".rodata", ".bss"} <= set(outputs) and ".comment" not in outputs
    for name in (".text", ".rodata", ".bss"):
        addr, size, items = outputs[name]
        ends = [it["addr"] for it in items[1:]] + [addr + size]
        real = [max(0, min(it["size"], end - it["addr"])) for it, end in zip(items, ends)]
        assert sum(real) == size - (items[0]["addr"] - addr), name


def test_map_gives_what_the_linker_kept_of_each_object(pc_build):
    rep = fp.build_report(pc_build)
    assert rep["map"] == "logger.ino.map" and rep["image"] is None
    model, core = rep["parts"]["nasim_model.c.o"], rep["parts"]["nasim_core.c.o"]
    obj_model, obj_core = rep["objects"]["nasim_model.c.o"], rep["objects"]["nasim_core.c.o"]
    # the trees are one function, and it is used: all of its code is in the image
    assert model["code"] == obj_model["code"] and set(model["functions"]) == {"nasim_score_nowind"}
    # nasim.c also holds what this small program never calls: the linker dropped it
    assert 0 < core["code"] < obj_core["code"]
    assert "gbt_update" in core["functions"] and "thr_update" in core["functions"]
    assert "cusum_inc_update" not in core["functions"]
    assert sum(core["functions"].values()) == core["code"]
    assert model["data"] == model["bss"] == core["data"] == core["bss"] == 0
    assert fp.ram(model) == 0 and fp.flash(model) == model["code"] + model["const"]
    assert set(model["placed"]) >= {".text", ".rodata"}


def test_merged_constants_are_shared_out_and_not_counted_twice(pc_build):
    """The 8-byte constants of all objects are merged into one block, which the map lists
    under the first object. Shared out, the three shares add up to the block exactly and
    no object gets more than it brought."""
    rows = [r for r in fp.map_sections(pc_build / "logger.ino.map") if r[1] == ".rodata.cst8"]
    block = next(size for name, addr, size, items in fp.map_outputs(pc_build / "logger.ino.map")
                 if name == ".rodata" for it in items if it["name"] == ".rodata.cst8" and it["before"]
                 for size in [it["size"]])
    assert sum(r[2] for r in rows) == block
    by_object = {fp._file_parts(r[3])[1]: r[2] for r in rows}
    brought = {o: _size_tool(pc_build / "sketch" / o).get(".rodata.cst8", 0) for o in by_object} \
        if shutil.which("size") else None
    if brought:
        assert all(0 < by_object[o] <= brought[o] for o in by_object)
        assert sum(brought.values()) >= block                      # duplicates were merged away
    rep = fp.build_report(pc_build)
    model = rep["parts"]["nasim_model.c.o"]
    assert rep["objects"]["nasim_model.c.o"]["const"] - 64 <= model["const"] <= rep["objects"]["nasim_model.c.o"]["const"]


def test_state_variables_are_found_by_name_and_a_dropped_one_is_not(pc_build):
    rep = fp.build_report(pc_build)
    sketch = {n: size for n, _, _, size in fp.elf_sections(pc_build / "sketch/logger.ino.cpp.o")}
    assert rep["state"]["det"] == sketch[".bss.det"] > 1000
    assert rep["state"]["det_thr"] == sketch[".bss.det_thr"] > 0
    assert "det_cusum" not in rep["state"] and sketch[".bss.det_cusum"] > 0       # dropped by the linker
    text = "\n".join(fp.text_report(rep, detail=True))
    assert "not found by name in the map: det_cusum" in text and "nasim_score_nowind" in text
    assert "whole image" not in text


# --- the layout of an ESP32 map (hand-written, sizes chosen by hand) --------------------------------

ESP32_STYLE = r"""Archive member included to satisfy reference by file (symbol)

c:/tools/lib/libm.a(lib_a-e_exp.o)
                              C:\Users\A B\sketches\X\sketch\nasim_core.c.o (exp)

Discarded input sections

 .text          0x00000000        0x0 C:\Users\A B\sketches\X\sketch\nasim_core.c.o
 .text.cusum_core_reset
                0x00000000       0x30 C:\Users\A B\sketches\X\sketch\nasim_core.c.o

Memory Configuration

Name             Origin             Length             Attributes
drom0_0_seg      0x3f400020         0x003fffe0         r
*default*        0x00000000         0xffffffff

Linker script and memory map

LOAD C:\Users\A B\sketches\X\sketch\logger.ino.cpp.o
                0x00000000                        IDF_TARGET_ESP32 = 0x0

.dram0.data     0x3ffb0000       0x18
                0x3ffb0000                        _data_start = ABSOLUTE (.)
 *(.gnu.linkonce.d.*)
 .data.thr_table
                0x3ffb0000        0x8 C:\Users\A B\sketches\X\sketch\nasim_core.c.o
 .data          0x3ffb0008       0x10 c:/tools/lib/libc.a(lib_a-impure.o)

.dram0.bss      0x3ffb0018      0xf20
 .bss.det       0x3ffb0018      0x8f0 C:\Users\A B\sketches\X\sketch\logger.ino.cpp.o
                0x3ffb0018                det
 .bss.det_thr   0x3ffb0908      0x408 C:\Users\A B\sketches\X\sketch\logger.ino.cpp.o
 .bss.det_cusum
                0x3ffb0d10      0x228 C:\Users\A B\sketches\X\sketch\logger.ino.cpp.o
                0x3ffb0d10                det_cusum
 *fill*         0x3ffb0f38        0x0 

.flash.rodata   0x3f400020      0x140
 *(.rodata .rodata.*)
 .rodata.cst8   0x3f400020      0x100 C:\Users\A B\sketches\X\sketch\logger.ino.cpp.o
                                 0x10 (size before relaxing)
 .rodata.cst8   0x3f400120       0xf0 C:\Users\A B\sketches\X\sketch\nasim_model.c.o
 .rodata.str1.4
                0x3f400120       0x20 C:\Users\A B\sketches\X\sketch\nasim_core.c.o
                                 0x24 (size before relaxing)

.flash.rodata_noload
                0x3f400160        0x0
 *(.rodata_wlog_debug .rodata_wlog_debug.*)

.flash.text     0x400d0020     0x7b40
 *(.stub)
 .literal.nasim_score_nowind
                0x400d0020     0x1f00 C:\Users\A B\sketches\X\sketch\nasim_model.c.o
                               0x1f40 (size before relaxing)
 .text.nasim_score_nowind
                0x400d1f20     0x5000 C:\Users\A B\sketches\X\sketch\nasim_model.c.o
                0x400d1f20                nasim_score_nowind
 .literal.gbt_update
                0x400d6f20       0x18 C:\Users\A B\sketches\X\sketch\nasim_core.c.o
                                 0x20 (size before relaxing)
 .text.gbt_update
                0x400d6f38      0x120 C:\Users\A B\sketches\X\sketch\nasim_core.c.o
                0x400d6f38                gbt_update
 *fill*         0x400d7058        0x8 
 .text.exp      0x400d7060      0x300 c:/tools/lib/libm.a(lib_a-e_exp.o)
 .text          0x400d7360      0x200 c:/tools/lib/gcc/libgcc.a(_muldf3.o)
 .text.loop     0x400d7560      0x600 C:\Users\A B\sketches\X\sketch\logger.ino.cpp.o

.debug_info     0x00000000    0x90000
 .debug_info    0x00000000     0x4000 C:\Users\A B\sketches\X\sketch\nasim_model.c.o

.xt.prop        0x00000000      0x800
 .xt.prop       0x00000000      0x400 C:\Users\A B\sketches\X\sketch\nasim_model.c.o
"""


@pytest.fixture()
def esp32_style(tmp_path):
    build = tmp_path / "logger_all"
    build.mkdir()
    (build / "logger.ino.map").write_text(ESP32_STYLE, encoding="utf-8")
    return build


def test_esp32_style_map_names_paths_and_sums(esp32_style):
    rep = fp.build_report(esp32_style)
    model, core = rep["parts"]["nasim_model.c.o"], rep["parts"]["nasim_core.c.o"]
    # literal pool and code together; the merged constants shared out: 0x100 between 0x10 and 0xf0
    assert model["code"] == 0x1F00 + 0x5000 and model["const"] == 0xF0 and model["other"] == 0
    assert model["functions"] == {"nasim_score_nowind": 0x1F00 + 0x5000}
    assert model["placed"] == {".flash.text": 0x1F00 + 0x5000, ".flash.rodata": 0xF0}
    assert core == {"code": 0x18 + 0x120, "const": 0x20, "data": 8, "bss": 0, "other": 0,
                    "placed": {".flash.text": 0x138, ".flash.rodata": 0x20, ".dram0.data": 8},
                    "functions": {"gbt_update": 0x138}}
    assert fp.flash(core) == 0x138 + 0x20 + 8 and fp.ram(core) == 8      # start values: flash and RAM
    # a name alone on a line, a path with a space and backslashes, 32-bit addresses
    assert rep["state"] == {"det": 0x8F0, "det_thr": 0x408, "det_cusum": 0x228}
    assert rep["shared"] == {"libm.a": 0x300, "libgcc.a": 0x200}
    names = {(o, n) for o, n, _, _ in fp.map_sections(esp32_style / "logger.ino.map")}
    assert (".flash.text", ".text.loop") in names
    assert not any(n in (".debug_info", ".xt.prop", ".text.cusum_core_reset") for _, n in names)
    assert not any(o == ".flash.rodata_noload" for o, _ in names)


def test_report_with_the_image_size_and_as_markdown(esp32_style):
    sys.path.insert(0, str(ROOT / "firmware"))
    try:
        import build_all
    finally:
        sys.path.pop(0)
    out = ("Sketch uses 462038 bytes (35%) of program storage space. Maximum is 1310720 bytes.\n"
           "Global variables use 30792 bytes (9%) of dynamic memory, leaving 296888 bytes for local "
           "variables. Maximum is 327680 bytes.\n")
    flash, ram = build_all.image_size(out)
    size = build_all.save_size(esp32_style, "logger_all", " -DHAS_LOAD=1 -DPHASE_B_ENABLED=1", flash, ram)
    assert size == {"build": "logger_all", "defines": "HAS_LOAD=1 PHASE_B_ENABLED=1", "flash_B": 462038,
                    "flash_pct": 35, "ram_B": 30792, "ram_pct": 9}
    assert build_all.image_size("error: something failed") == (None, None)
    rep = fp.build_report(esp32_style)
    assert rep["image"] == json.loads((esp32_style / "size.json").read_text())
    text = "\n".join(fp.text_report(rep))
    assert "flash 462038 B (35 %)" in text and "share of the image" in text
    rows = fp.markdown_rows(rep)
    assert rows[0] == "| logger_all | whole image | 462038 | 30792 | arduino-cli |"
    assert any("`det`" in r and "| 0 | 2288 |" in r for r in rows)
    assert any(r.startswith("| logger_all | nasim_model.c.o") and f"| {0x1F00 + 0x5000 + 0xF0} | 0 |" in r for r in rows)


def test_a_folder_without_a_map_gives_the_objects_only_and_says_so(pc_build, tmp_path):
    build = tmp_path / "logger"
    shutil.copytree(pc_build / "sketch", build / "sketch")
    rep = fp.build_report(build)
    assert rep["map"] is None and "parts" not in rep and set(rep["objects"]) == set(fp.PARTS)
    assert "no map file" in "\n".join(fp.text_report(rep))
    (build / "broken.map").write_text("not a map")
    with pytest.raises(ValueError):
        fp.build_report(build)


# --- the status file of the board ----------------------------------------------------------------------

STATUS = """date,time,uptime_ms,nasim_n,nasim_us_mean,nasim_us_max,warmup_n,warmup_us_mean,warmup_us_max,heap_free_B,heap_min_B,stack_min_B
2026-12-01,04:00:00,3600000,300,900.0,1100,60,35.0,48,250000,240000,5200
2026-12-01,05:00:00,7200000,100,1000.0,1500,0,,0,249000,238000,5100
,,10800000,0,,0,0,,0,249500,238000,5100
"""


def test_status_file_gives_the_time_per_update_weighted_by_updates(tmp_path):
    path = tmp_path / "status_log.csv"
    path.write_text(STATUS + STATUS.splitlines()[0] + "\n" + STATUS.splitlines()[1] + "\n")   # a second header
    s = fp.status_summary(path)
    assert s["lines"] == 4
    assert s["inference"] == {"updates": 700, "mean_us": (300 * 900 + 100 * 1000 + 300 * 900) / 700, "max_us": 1500}
    assert s["warm_up"] == {"updates": 120, "mean_us": 35.0, "max_us": 48}
    assert s["lowest"] == {"heap_free_B": 249000, "heap_min_B": 238000, "stack_min_B": 5100}
    text = "\n".join(fp.status_report(s))
    assert "n     700" in text and "slowest 1500 us" in text
    empty = tmp_path / "empty.csv"
    empty.write_text(STATUS.splitlines()[0] + "\n")
    e = fp.status_summary(empty)
    assert e["lines"] == 0 and e["inference"]["mean_us"] is None and "-" in "\n".join(fp.status_report(e))


def test_status_columns_are_the_ones_the_firmware_writes():
    """The reader's column names against pv_timing.c and the line built in logger.ino."""
    timing = (ROOT / "firmware/libraries/PvDaq/src/pv_timing.c").read_text(encoding="utf-8")
    ino = (ROOT / "firmware/logger/logger.ino").read_text(encoding="utf-8")
    header = STATUS.splitlines()[0]
    assert '"nasim_n,nasim_us_mean,nasim_us_max,warmup_n,warmup_us_mean,warmup_us_max"' in timing
    assert 'String("date,time,uptime_ms,") + timing_header()' in ino
    assert '",heap_free_B,heap_min_B,stack_min_B"' in ino
    assert header == ("date,time,uptime_ms,nasim_n,nasim_us_mean,nasim_us_max,warmup_n,warmup_us_mean,"
                      "warmup_us_max,heap_free_B,heap_min_B,stack_min_B")


# --- the command ---------------------------------------------------------------------------------------

def test_command_line(pc_build, tmp_path):
    def run(*args):
        return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True)
    r = run(str(pc_build), "--detail")
    assert r.returncode == 0 and "nasim_model.c.o" in r.stdout and "placed in:" in r.stdout
    r = run(str(pc_build), "--markdown")
    assert r.returncode == 0 and r.stdout.startswith("| build | part | flash, B | RAM, B | source |")
    data = json.loads(run(str(pc_build), "--json").stdout)
    assert data[0]["build"] == "logger" and data[0]["parts"]["nasim_model.c.o"]["code"] > 5000
    r = run(str(tmp_path / "nothing_here"))
    assert r.returncode == 1 and "no build found" in r.stdout and "build_all.py" in r.stdout
    status = tmp_path / "status_log.csv"
    status.write_text(STATUS)
    r = run("--status", str(status))
    assert r.returncode == 0 and "3 hourly lines" in r.stdout
