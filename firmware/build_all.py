"""Compile every Phase 0 sketch with its pinned profile (sketch.yaml) and all warnings on.

    python firmware/build_all.py            (all sketches)
    python firmware/build_all.py logger     (names containing "logger")

Compile only: nothing is flashed. The logger also compiles export/c (NASIM), so it
gets the include paths to export/c and export/c/generated as extra compiler flags.
The logger is compiled four times: as it is flashed today (HAS_LOAD 0, ENABLE_PUMP 0 in
pv_config.h), with -DHAS_LOAD=1 ("logger_load"), with the pump switched on ("logger_pump")
and with all of them ("logger_full"), so the load and pump code are always build-checked.
Prints, per sketch, OK / FAILED, the flash and RAM use, and every warning that
comes from this project's own files (warnings inside the pinned third-party
libraries, which live under Arduino15, are only counted).
"""
import pathlib
import re
import subprocess
import sys

FW = pathlib.Path(__file__).resolve().parent
ROOT = FW.parent
SKETCHES = sorted(p.parent for p in FW.glob("tests/*/sketch.yaml")) + [FW / "logger"]
BUILDS = [(s, s.name, "") for s in SKETCHES] + [
    (FW / "logger", "logger_load", " -DHAS_LOAD=1"),
    (FW / "logger", "logger_pump", " -DENABLE_PUMP=1"),
    (FW / "logger", "logger_full", " -DHAS_LOAD=1 -DENABLE_PUMP=1")]


def compile_sketch(sketch, name, define):
    cmd = ["arduino-cli", "compile", "--warnings", "all", "--build-path",
           str(ROOT / "out/firmware_build" / name), str(sketch)]
    if sketch.name == "logger":
        inc = f"-I{ROOT / 'export/c'} -I{ROOT / 'export/c/generated'}".replace("\\", "/") + define
        cmd += ["--build-property", f"compiler.c.extra_flags={inc}",
                "--build-property", f"compiler.cpp.extra_flags={inc}"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    out = r.stdout + r.stderr
    warnings = [l for l in out.splitlines() if "warning:" in l]
    own = [l for l in warnings if "arduino15" not in l.lower()]   # third-party: (.)arduino15
    flash = re.search(r"Sketch uses (\d+) bytes \((\d+)%\)", out)
    ram = re.search(r"Global variables use (\d+) bytes \((\d+)%\)", out)
    return r.returncode == 0, out, own, len(warnings) - len(own), flash, ram


def main():
    pick = sys.argv[1] if len(sys.argv) > 1 else ""
    failed = 0
    for s, name, define in BUILDS:
        if pick not in name:
            continue
        ok, out, own, lib_warn, flash, ram = compile_sketch(s, name, define)
        rel = s.relative_to(FW).as_posix() + (f"  [{define.replace('-D', '').strip()}]" if define else "")
        if not ok:
            failed += 1
            errors = [l for l in out.splitlines() if "error" in l.lower()]
            print(f"FAILED  {rel}\n" + "\n".join("        " + l for l in errors or out.splitlines()))
            continue
        print(f"OK      {rel:38s} flash {flash[1]:>7} B ({flash[2]}%)  RAM {ram[1]:>6} B ({ram[2]}%)"
              f"  own warnings {len(own)}  library warnings {lib_warn}")
        for w in own:
            print("        " + w)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
