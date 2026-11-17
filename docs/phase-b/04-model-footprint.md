# Phase B, part 4 — footprint of the NASIM model on the ESP32

Status: 2026-11-17. The tool that reads the numbers from a build exists and is tested on
a PC build; the timing log exists behind a switch that is off. **The ESP32 rows marked
"not read yet" are empty on purpose**: the machine this was written on has no ESP32
toolchain, and no board has run the timing build. Nothing here is estimated.

## 1. Memory

| Item | Flash, B | RAM, B | Source |
|---|---|---|---|
| Whole logger image, ESP32, all new flags off (build of 2026-11-13, before this work) | 462 038 (35 % of the 1 310 720 B app partition) | 30 792 global (9 % of 327 680) | arduino-cli, as recorded in `docs/paper/07` §7 |
| NASIM-GBT state, variable `det` (double precision) | 0 | 2 288 | reported by the board earlier (`docs/paper/07` §7) |
| The two baseline detectors (threshold rule, CUSUM) | 1 084 | 1 584 | build of 2026-11-13 (`docs/paper/07` §7) |
| **Trees** — `nasim_model.c.o` (50 trees, depth ≤ 4, 674 splits; generated C) in the ESP32 image | **not read yet** | 0 expected (it has no variables) | `firmware/tools/footprint.py` on the next build |
| **Features, thermal model, detector logic** — `nasim_core.c.o` (`export/c/nasim.c`; also holds the two baselines) in the ESP32 image | **not read yet** | 0 expected | the same |
| Whole image with `PHASE_B_ENABLED 1`, and with all four flags | **not built yet** | **not built yet** | `python firmware/build_all.py logger` |

For orientation only — **PC build, not the ESP32** (x86-64, gcc 13.3, `-Os`, the same
source files, `tests/test_footprint.py`):

| Item | Code, B | Constants, B | Sum, B |
|---|---|---|---|
| Trees, `nasim_model.c.o` | 21 788 | 7 936 | 29 724 |
| Features and logic, `nasim_core.c.o` | 3 671 | 408 | 4 079 |
| State: `det` 2 288 B, `det_thr` 1 032 B, `det_cusum` 552 B (RAM) | | | |

The PC numbers cannot be carried over: the ESP32 has another instruction set, and it does
double-precision arithmetic by calling library routines where the PC uses its
floating-point unit. The state sizes do agree with what the board reported (2 288 B, and
1 032 + 552 = 1 584 B for the baselines).

## 2. Time of one NASIM update

| Quantity | Value | Source |
|---|---|---|
| Update with the model (features, thermal model, CUSUM residual, 50 trees): mean, slowest | **to be measured on the board** | `/status_log.csv`, logger built with `NASIM_TIMING_LOG 1` |
| Update while the buffers fill (first 10 min of a detector run, no model call): mean, slowest | **to be measured on the board** | the same |
| Lowest free heap, lowest free stack of the loop task | **to be measured on the board** | the same |
| Earlier measurement, **earlier model** with the same tree settings: 0.89 ms mean, 1.06 ms slowest; 33 µs while the buffers fill | | board replay recorded in `docs/paper/07` §7 (`out/replay/replay.md`) |

How it is measured. In `sample()` of `logger.ino`, `micros()` is read before and after
`gbt_update()`; the difference goes to `timing_add()` (`pv_timing.c`), which keeps the
count, the sum and the largest value, separately for updates that ran the model and for
warm-up updates. Once an hour one line is appended to `/status_log.csv` and the counters
start again:

```
date,time,uptime_ms,nasim_n,nasim_us_mean,nasim_us_max,warmup_n,warmup_us_mean,warmup_us_max,heap_free_B,heap_min_B,stack_min_B
```

The timed call is NASIM-GBT alone; the two baselines are outside it. The measurement
itself costs two `micros()` calls per 10 s row. `tests/test_timing.py` checks the
counters on the PC.

## 3. How to fill the tables

```bash
python firmware/build_all.py logger                    # needs arduino-cli and the pinned ESP32 core 3.3.8
python firmware/tools/footprint.py --detail            # per build: trees, logic, state, share of the image
python firmware/tools/footprint.py --markdown          # rows to paste into section 1
python firmware/tools/footprint.py --status <card copy>/status_log.csv     # section 2, after a day on the board
```

What the tool reads (`firmware/tools/footprint.py`, standard library only):
- **the linker's map file** of the build (`logger.ino.map`): every piece the linker kept,
  with its size and its object file. The pieces of `nasim_model.c.o` are the trees; this
  is their exact share of the image, without what the linker dropped as unused;
- equal constants of all object files are merged by the linker into one block. The tool
  shares such a block out among the objects in proportion to what each brought; for one
  object this part is an allocation, for the block it is exact;
- the detector state by variable name (`det`, `det_thr`, `det_cusum`);
- the whole image as arduino-cli printed it (`build_all.py` saves it as `size.json`);
- if a build has no map file: the object files alone, which is an upper bound.

Not in the tool's numbers, and why:
- the maths routines the model calls (`exp`, `sin`, `cos`, `acos`, `pow`, software double
  arithmetic). They sit in `libm.a` / `libgcc.a` and are shared with the rest of the
  logger; the tool prints their total as context;
- stack use: it is not in a map file. The status line gives the lowest free stack seen.

The reader is tested on a real GNU linker map from a PC build of the same sources, and on
a hand-written excerpt in the layout of an ESP32 map. **It has not yet read a real ESP32
map.** If the first real one does not parse, that is a fault of the reader, to be fixed
against that file.
