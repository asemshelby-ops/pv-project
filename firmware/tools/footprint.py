"""Flash and RAM of the NASIM model inside the logger, read from a finished build; and the
time of one NASIM update, read from the status file the board writes.

    python firmware/build_all.py logger                  needs arduino-cli; leaves the builds in
                                                         out/firmware_build/<name>/
    python firmware/tools/footprint.py                   every logger build found there
    python firmware/tools/footprint.py out/firmware_build/logger_all --detail
    python firmware/tools/footprint.py --markdown        the rows for docs/phase-b/04
    python firmware/tools/footprint.py --status <card copy>/status_log.csv

Where the numbers come from (nothing is estimated here):
  1. the linker's map file <build>/logger.ino.map. It lists every piece (input section) the
     linker kept, with its size and the object file it came from. Summing the pieces of
     sketch/nasim_model.c.o gives what the trees occupy in the image; nasim_core.c.o is
     export/c/nasim.c (features, thermal model, the detector logic and the two baseline
     detectors). This is the exact contribution to the image: what the linker dropped as
     unused is not counted.
  2. without a map file: the section table of the object files themselves. That is an
     upper bound (nothing dropped yet).
  3. the detector's state in RAM: the variables det, det_thr and det_cusum of logger.ino,
     which the map lists by name (the ESP32 core compiles with -fdata-sections).
  4. the whole image: <build>/size.json, the two lines arduino-cli printed
     (firmware/build_all.py saves them).
Kinds of bytes:  code (instructions, and on the ESP32 the literal pools next to them),
const (read-only data), data (variables with a start value: they take flash AND RAM),
bss (variables that start at zero: RAM only).   flash = code + const + data;  RAM = data + bss.

Not in these numbers: the maths routines the model calls (exp, sin, cos, acos, pow and the
software double arithmetic). They live in libm.a / libgcc.a and are shared with the rest of
the logger; the report shows their total as context, not as the model's cost. Stack use
is not in a map file either: the status file gives the lowest free stack seen on the board.

Works on any GNU build (ESP32 / Xtensa or a PC build made for the tests,
tests/test_footprint.py). Standard library only.
"""
import argparse
import csv
import json
import pathlib
import re
import struct
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
BUILDS = ROOT / "out/firmware_build"
# object file -> what it holds
PARTS = {"nasim_model.c.o": "trees (generated code of the model)",
         "nasim_core.c.o": "features, thermal model, detector logic (also the two baselines)"}
# variable of logger.ino -> what it holds
STATE = {"det": "NASIM-GBT state (gbt_detector_t)",
         "det_thr": "threshold rule state", "det_cusum": "CUSUM state",
         "nasim_timing": "timing counters (NASIM_TIMING_LOG)"}
SHARED = ("libm.a", "libgcc.a")
KINDS = ("code", "const", "data", "bss")
# sections of a PC object that an ESP32 image has no counterpart of
PC_ONLY = (".eh_frame", ".note", ".comment")

SHT_NOBITS, SHF_WRITE, SHF_ALLOC, SHF_EXEC = 8, 1, 2, 4


# --- object files: the section table of an ELF file ---------------------------------------------

def elf_sections(path):
    """[(name, type, flags, size)] of an ELF file (32 or 64 bit, little-endian)."""
    b = pathlib.Path(path).read_bytes()
    if b[:4] != b"\x7fELF" or b[5] != 1:
        raise ValueError(f"{path}: not a little-endian ELF file")
    if b[4] == 1:       # 32 bit (ESP32)
        shoff, = struct.unpack_from("<I", b, 0x20)
        entsize, num, strndx = struct.unpack_from("<HHH", b, 0x2E)
        fmt, pick = "<IIIIII", lambda f: (f[0], f[1], f[2], f[4], f[5])     # name type flags offset size
    elif b[4] == 2:     # 64 bit (PC)
        shoff, = struct.unpack_from("<Q", b, 0x28)
        entsize, num, strndx = struct.unpack_from("<HHH", b, 0x3A)
        fmt, pick = "<IIQQQQ", lambda f: (f[0], f[1], f[2], f[4], f[5])
    else:
        raise ValueError(f"{path}: unknown ELF class")
    heads = [pick(struct.unpack_from(fmt, b, shoff + i * entsize)) for i in range(num)]
    str_off = heads[strndx][3]

    def name(k):
        return b[str_off + k:b.index(b"\0", str_off + k)].decode("ascii", "replace")
    return [(name(h[0]), h[1], h[2], h[4]) for h in heads]


def object_footprint(path):
    """{code, const, data, bss} in bytes from an object file's own sections: an upper
    bound of what it can add to an image, before the linker drops what is not used."""
    out = dict.fromkeys(KINDS, 0)
    for name, kind, flags, size in elf_sections(path):
        if not flags & SHF_ALLOC or name.startswith(PC_ONLY):
            continue
        if kind == SHT_NOBITS:
            out["bss"] += size
        elif flags & SHF_EXEC:
            out["code"] += size
        elif flags & SHF_WRITE:
            out["data"] += size
        else:
            out["const"] += size
    return out


# --- the linker's map file ------------------------------------------------------------------------

_NUM = r"0x[0-9a-fA-F]+"
_OUT = re.compile(rf"^(\S+)\s+({_NUM})\s+({_NUM})\b")                  # output section, at column 0
_IN = re.compile(rf"^ (\S+)\s+({_NUM})\s+({_NUM})(?:\s+(\S.*?))?\s*$")  # input section, one space in
_CONT = re.compile(rf"^\s+({_NUM})\s+({_NUM})(?:\s+(\S.*?))?\s*$")      # address and size on the next line
_BEFORE = re.compile(rf"^\s+({_NUM}) \(size before relaxing\)")


def map_outputs(path):
    """The memory map, one entry per output section that is part of the image:
    (name, address, size, items); an item is a dict name, addr, size, file (None for the
    linker's own padding, "*fill*") and before (the size before the linker merged equal
    constants, or None).

    Reads the part after "Linker script and memory map": what stands before it are the
    sections that were thrown away. An output section placed at address 0 (debug
    information, notes, comments) is not part of the image and is left out."""
    text = pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
    start = text.find("Linker script and memory map")
    if start < 0:
        raise ValueError(f"{path}: not a GNU ld map file")
    outputs, cur, pending = [], None, None

    def open_output(name, addr, size):
        nonlocal cur
        cur = (name, addr, size, [])
        if addr:
            outputs.append(cur)

    def add(name, addr, size, file):
        if cur is not None:
            cur[3].append({"name": name, "addr": addr, "size": size, "file": file, "before": None})

    for line in text[start:].splitlines():
        if not line.strip():
            pending = None
            continue
        if pending is not None:                     # a name stood alone on the line before
            where, name = pending
            pending = None
            m = _CONT.match(line)
            if m:
                if where == "out":
                    open_output(name, int(m.group(1), 16), int(m.group(2), 16))
                elif m.group(3):
                    add(name, int(m.group(1), 16), int(m.group(2), 16), m.group(3))
                continue
        if line[0] not in " \t":
            m = _OUT.match(line)
            if m:
                open_output(m.group(1), int(m.group(2), 16), int(m.group(3), 16))
            elif re.fullmatch(r"\S+", line) and (line[0] == "." or line == "COMMON"):
                pending = ("out", line)
            continue
        m = _BEFORE.match(line)
        if m:
            if cur is not None and cur[3]:
                cur[3][-1]["before"] = int(m.group(1), 16)
            continue
        if line.startswith(" *fill*"):
            m = _IN.match(line)
            if m:
                add("*fill*", int(m.group(2), 16), int(m.group(3), 16), None)
            continue
        if line.startswith(" *") or line.startswith("  "):
            continue                                # a pattern of the script, a symbol, a note
        m = _IN.match(line)
        if m and m.group(4):
            add(m.group(1), int(m.group(2), 16), int(m.group(3), 16), m.group(4))
        elif re.fullmatch(r" \S+", line):
            pending = ("in", line.strip())
    return outputs


def map_sections(path):
    """Every input section the linker kept: (output section, input section, bytes, file),
    with the bytes it really occupies in the image.

    Two things in a map file are not what they seem, and both are handled here:
      * the linker merges equal constants of all objects (sections like .rodata.cst8, and
        on the ESP32 the literal pools). The merged block is then listed under the FIRST
        object that had such a section, and the others are listed with their old size at
        an address where they occupy nothing. The real size of an entry is therefore what
        fits before the next entry begins;
      * such a merged block is shared out here among the objects that fed it, in
        proportion to what each brought (their sizes before merging). Which object's
        constants were the duplicates cannot be read from a map file, so this part of
        `const` is exact for the group and an allocation for each object."""
    for out_name, out_addr, out_size, items in map_outputs(path):
        end = out_addr + out_size
        for i, it in enumerate(items):
            nxt = items[i + 1]["addr"] if i + 1 < len(items) else end
            it["real"] = max(0, min(it["size"], nxt - it["addr"])) if nxt >= it["addr"] else it["size"]
        for i, head in enumerate(items):
            if head["file"] is None or head["before"] is None or head.get("grouped"):
                continue
            members = [m for m in items[i + 1:] if m["name"] == head["name"] and m["file"] is not None
                       and m["real"] == 0 and m["size"] > 0 and not m.get("grouped")]
            if not members:
                continue
            brought = [head["before"]] + [m["size"] for m in members]
            total, left = head["real"], head["real"]
            for it, own in zip([head] + members, brought):
                it["grouped"] = True
                it["real"] = min(left, round(total * own / sum(brought))) if sum(brought) else 0
                left -= it["real"]
            head["real"] += left                    # rounding remainder stays with the first
        for it in items:
            if it["file"] is not None:
                yield out_name, it["name"], it["real"], it["file"]


def section_kind(name):
    """code / const / data / bss / other, from an input section's name."""
    for prefix, kind in ((".literal", "code"), (".text", "code"), (".iram", "code"),
                         (".rodata", "const"), (".srodata", "const"),
                         (".data", "data"), (".sdata", "data"),
                         (".bss", "bss"), (".sbss", "bss"), ("COMMON", "bss")):
        if name == prefix or name.startswith(prefix + "."):
            return kind
    return "other"


def _file_parts(file):
    """("libm.a", "lib_a-e_exp.o") for an archive member, (None, "nasim_model.c.o") for an
    object file. Works with / and \\ in the path."""
    m = re.fullmatch(r"(.*?)\(([^()]*)\)", file.strip())
    base = lambda p: re.split(r"[\\/]", p)[-1]
    return (base(m.group(1)), m.group(2)) if m else (None, base(file.strip()))


def map_footprint(path):
    """What the image holds of the model, from a map file:
      parts    {object: {code, const, data, bss, other, placed: {output section: bytes},
                         functions: {name: bytes}}}
      state    {variable: bytes}        the detector's RAM, by variable of the sketch
      shared   {archive: bytes}         libm.a and libgcc.a as a whole (context only)"""
    parts = {o: {**dict.fromkeys(KINDS + ("other",), 0), "placed": {}, "functions": {}} for o in PARTS}
    state, shared = {}, dict.fromkeys(SHARED, 0)
    for out_name, name, size, file in map_sections(path):
        archive, member = _file_parts(file)
        kind = section_kind(name)
        if archive in shared:
            shared[archive] += size
        elif archive is None and member in parts:
            p = parts[member]
            p[kind] += size
            p["placed"][out_name] = p["placed"].get(out_name, 0) + size
            if kind == "code" and name.count(".") >= 2:
                fn = name.split(".", 2)[2]
                p["functions"][fn] = p["functions"].get(fn, 0) + size
        elif archive is None and kind in ("bss", "data") and name.count(".") >= 2:
            var = name.split(".", 2)[2]
            if var in STATE:
                state[var] = state.get(var, 0) + size
    return {"parts": parts, "state": state, "shared": shared}


def flash(k):
    return k["code"] + k["const"] + k["data"]


def ram(k):
    return k["data"] + k["bss"]


# --- one build -----------------------------------------------------------------------------------

def build_report(build):
    """Everything that can be read from one build folder (arduino-cli --build-path)."""
    build = pathlib.Path(build)
    maps = sorted(build.glob("*.map"))
    objects = {o: p for o in PARTS for p in build.glob(f"sketch/{o}")}
    rep = {"build": build.name, "map": maps[0].name if maps else None, "image": None,
           "objects": {o: object_footprint(p) for o, p in objects.items()}}
    if (build / "size.json").exists():
        rep["image"] = json.loads((build / "size.json").read_text(encoding="utf-8"))
    if maps:
        rep.update(map_footprint(maps[0]))
    return rep


def text_report(rep, detail=False):
    lines = [f"build: {rep['build']}"]
    img = rep.get("image")
    if img:
        lines.append(f"  whole image (arduino-cli): flash {img['flash_B']} B ({img['flash_pct']} %), "
                     f"global RAM {img['ram_B']} B ({img['ram_pct']} %)"
                     + (f"   flags: {img['defines']}" if img.get("defines") else ""))
    head = f"  {'part':16s} {'code':>8s} {'const':>8s} {'data':>8s} {'bss':>8s} {'flash':>8s} {'RAM':>8s}"
    if rep.get("parts"):
        lines += [f"  in the image (from {rep['map']}):", head]
        total = dict.fromkeys(KINDS, 0)
        for o, p in rep["parts"].items():
            lines.append(f"  {o:16s} " + " ".join(f"{p[k]:8d}" for k in KINDS)
                         + f" {flash(p):8d} {ram(p):8d}   {PARTS[o]}"
                         + (f"   [other sections: {p['other']} B]" if p["other"] else ""))
            for k in KINDS:
                total[k] += p[k]
            if detail:
                lines.append("      placed in: " + ", ".join(f"{k} {v}" for k, v in sorted(p["placed"].items())))
                for fn, size in sorted(p["functions"].items(), key=lambda kv: -kv[1])[:12]:
                    lines.append(f"      {size:8d}  {fn}")
        lines.append(f"  {'both':16s} " + " ".join(f"{total[k]:8d}" for k in KINDS)
                     + f" {flash(total):8d} {ram(total):8d}")
        if img:
            lines.append(f"  share of the image: flash {100 * flash(total) / img['flash_B']:.1f} %")
        for var, what in STATE.items():
            if var in rep["state"]:
                lines.append(f"  RAM of {var:13s} {rep['state'][var]:8d} B   {what}")
        missing = [v for v in ("det", "det_thr", "det_cusum") if v not in rep["state"]]
        if missing:
            lines.append(f"  state variables not found by name in the map: {', '.join(missing)}")
        lines.append("  shared maths code in the image (also used by the rest of the logger): "
                     + ", ".join(f"{a} {b} B" for a, b in rep["shared"].items()))
    else:
        lines.append("  no map file in this folder: the image's own numbers are not available")
    if rep["objects"]:
        lines += ["  object files, before linking (upper bound):", head]
        for o, k in rep["objects"].items():
            lines.append(f"  {o:16s} " + " ".join(f"{k[x]:8d}" for x in KINDS) + f" {flash(k):8d} {ram(k):8d}")
    return lines


def markdown_rows(rep):
    """Rows for the table of docs/phase-b/04-model-footprint.md."""
    rows = []
    img = rep.get("image")
    if img:
        rows.append(f"| {rep['build']} | whole image | {img['flash_B']} | {img['ram_B']} | arduino-cli |")
    for o, p in (rep.get("parts") or {}).items():
        rows.append(f"| {rep['build']} | {o}: {PARTS[o]} | {flash(p)} | {ram(p)} | map file |")
    for var, size in (rep.get("state") or {}).items():
        rows.append(f"| {rep['build']} | `{var}`: {STATE[var]} | 0 | {size} | map file |")
    return rows


# --- the status file of the board -------------------------------------------------------------------

def status_summary(path):
    """Time of the NASIM update from /status_log.csv (one line per hour, logger built with
    NASIM_TIMING_LOG 1). Means are weighted by the number of updates in each line."""
    with open(path, newline="", encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh) if r.get("nasim_n") not in (None, "", "nasim_n")]

    def num(r, k):
        try:
            return float(r[k])
        except (KeyError, TypeError, ValueError):
            return None

    def part(n_col, mean_col, max_col):
        used = [(num(r, n_col), num(r, mean_col), num(r, max_col)) for r in rows]
        used = [u for u in used if u[0] and u[1] is not None]
        n = sum(u[0] for u in used)
        return {"updates": int(n), "mean_us": sum(u[0] * u[1] for u in used) / n if n else None,
                "max_us": max((u[2] for u in used if u[2] is not None), default=None)}
    lows = {k: min((v for v in (num(r, k) for r in rows) if v is not None), default=None)
            for k in ("heap_free_B", "heap_min_B", "stack_min_B")}
    return {"lines": len(rows), "inference": part("nasim_n", "nasim_us_mean", "nasim_us_max"),
            "warm_up": part("warmup_n", "warmup_us_mean", "warmup_us_max"), "lowest": lows}


def status_report(s):
    f = lambda v, unit: "-" if v is None else f"{v:.0f} {unit}"
    lines = [f"status file: {s['lines']} hourly lines"]
    for key, what in (("inference", "NASIM update with the model"), ("warm_up", "update while the buffers fill")):
        p = s[key]
        lines.append(f"  {what:32s} n {p['updates']:7d}   mean {f(p['mean_us'], 'us')}   slowest {f(p['max_us'], 'us')}")
    lines.append("  lowest seen: " + ", ".join(f"{k} {f(v, 'B')}" for k, v in s["lowest"].items()))
    return lines


def main():
    ap = argparse.ArgumentParser(description="Flash and RAM of the NASIM model from a build; "
                                             "update time from the board's status file")
    ap.add_argument("builds", nargs="*", help="build folders (default: out/firmware_build/logger*)")
    ap.add_argument("--detail", action="store_true", help="output sections and the largest functions")
    ap.add_argument("--markdown", action="store_true", help="table rows for the docs")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--status", help="status_log.csv copied from the card")
    a = ap.parse_args()
    if a.status:
        s = status_summary(a.status)
        print(json.dumps(s, indent=2) if a.json else "\n".join(status_report(s)))
        if not a.builds:
            return 0
    builds = [pathlib.Path(b) for b in a.builds] or sorted(BUILDS.glob("logger*"))
    builds = [b for b in builds if b.is_dir()]
    if not builds:
        print(f"no build found in {BUILDS}. Build first: python firmware/build_all.py logger "
              "(needs arduino-cli with the pinned ESP32 core).")
        return 1
    reports = [build_report(b) for b in builds]
    if a.json:
        print(json.dumps(reports, indent=2))
    elif a.markdown:
        print("| build | part | flash, B | RAM, B | source |\n|---|---|---|---|---|")
        print("\n".join(r for rep in reports for r in markdown_rows(rep)))
    else:
        print("\n\n".join("\n".join(text_report(rep, a.detail)) for rep in reports))
    return 0


if __name__ == "__main__":
    sys.exit(main())
