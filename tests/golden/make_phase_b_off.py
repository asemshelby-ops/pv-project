"""Golden reference for "every new flag OFF = the project as it was before the Phase B
preparation" (main at commit 51d18aa, 2026-11-14).

    python tests/golden/make_phase_b_off.py --root <checkout of 51d18aa> --write

writes tests/golden/phase_b_off.json: SHA-256 hashes of three sets of files.

  firmware   every source file the logger is built from, with LF line endings, after the
             new compile-time flags are resolved to 0 (flag_off_source below). On the
             reference commit no file mentions a new flag, so this is the file itself.
  logger     the synthetic logger files written by analysis/fake_logger.py, in the present
             (21 columns) and the old (16 columns) row format.
  pipeline   every text output of analysis/pipeline.py on a 10-day SYNTHETIC campaign
             built by analysis/demo_synth.py (logger files, markers, log sheets, sweep
             files, reference export): tables, events, settings, verdict, summary.

tests/test_phase_b_flags.py recomputes the three sets with the code as it is now and
requires the same hashes. The first set is text, so it must match on every machine. The
other two contain computed numbers: they are byte-identical between two versions of the
code ON THE SAME MACHINE, but the last digit of a few values depends on the platform's
maths library (seen 2026-11-17: the exported tree thresholds on cos_zenith differ in the
17th digit between the Windows development PC and Linux). The file therefore records the
machine it was made on; on a different one a mismatch is reported as "cannot be checked
here", not as a failure. To check there, make the reference again on that machine from a
checkout of 51d18aa (the command above with --out <a scratch file>) and compare.
"""
import argparse
import hashlib
import json
import pathlib
import platform
import re
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
GOLDEN = HERE / "phase_b_off.json"
REFERENCE_COMMIT = "51d18aa04b9fb8e3734bf54c3583ad17ec33b481"

# Compile-time flags added by the Phase B preparation (firmware/libraries/PvDaq/src/
# pv_config.h). All default to 0.
NEW_FLAGS = ("PHASE_B_ENABLED", "MARKER_CODES_ENABLED", "NASIM_TIMING_LOG")
FIRMWARE_GLOBS = ("firmware/logger/*", "firmware/libraries/PvDaq/library.properties",
                  "firmware/libraries/PvDaq/src/*", "export/c/*.c", "export/c/*.h",
                  "export/c/generated/*")
N_DAYS, N_BOOT, SEED = 10, 50, 5
SKIP_SUFFIXES = {".pdf", ".png"}          # figures carry a creation date


def sha(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def flag_off_source(text, flags=NEW_FLAGS):
    """The source text the compiler sees with every flag of `flags` equal to 0, written
    back as source: the blocks `#if FLAG ... #endif` are removed, of `#if FLAG ... #else
    ... #endif` and `#if !FLAG ... #endif` the live branch is kept without its directive
    lines, and the block `#ifndef FLAG ... #endif` that gives a flag its default is
    removed. Other conditionals (HAS_LOAD, ...) are left as they are.

    A flag may be used ONLY in those four forms. Anything else that names a flag (an
    `#elif`, a combined condition, a plain `if (FLAG)`) raises ValueError: the proof that
    "flag off = the earlier source" would no longer be a matter of reading directives."""
    name = "|".join(flags)
    out, stack = [], []                   # stack: [ours, live, parent_live]
    live = True
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        m = re.match(r"#\s*(if|ifdef|ifndef|elif|else|endif)\b(.*)", line)
        if m is None:
            if live:
                if re.search(rf"\b({name})\b", re.sub(r"//.*", "", raw)):
                    raise ValueError(f"flag used outside a preprocessor directive: {raw!r}")
                out.append(raw)
            continue
        kind, rest = m.group(1), re.sub(r"//.*|/\*.*?\*/", "", m.group(2)).strip()
        mentions = re.search(rf"\b({name})\b", rest) is not None
        if kind in ("if", "ifdef", "ifndef"):
            ours = mentions
            if ours:
                if kind == "if" and re.fullmatch(rf"({name})", rest):
                    branch = False
                elif kind == "if" and re.fullmatch(rf"!\s*({name})", rest):
                    branch = True
                elif kind == "ifndef" and re.fullmatch(rf"({name})", rest):
                    branch = False            # the default-definition block: FLAG exists (= 0)
                else:
                    raise ValueError(f"unsupported use of a flag: {raw!r}")
                stack.append([True, branch, live])
                live = live and branch
            else:
                stack.append([False, True, live])
                if live:
                    out.append(raw)
        elif kind == "elif":
            if not stack or stack[-1][0] or mentions:
                raise ValueError(f"unsupported use of a flag: {raw!r}")
            if live:
                out.append(raw)
        elif kind == "else":
            if not stack:
                raise ValueError("#else without #if")
            if stack[-1][0]:
                stack[-1][1] = not stack[-1][1]
                live = stack[-1][2] and stack[-1][1]
            elif live:
                out.append(raw)
        else:                                 # endif
            if not stack:
                raise ValueError("#endif without #if")
            ours, _, parent = stack.pop()
            if not ours and live:
                out.append(raw)
            live = parent
    if stack:
        raise ValueError("unterminated #if")
    return "\n".join(out)


def firmware_files(root):
    root = pathlib.Path(root)
    files = sorted({p for g in FIRMWARE_GLOBS for p in root.glob(g) if p.is_file()})
    return {p.relative_to(root).as_posix(): p for p in files}


def firmware_hashes(root):
    return {name: sha(flag_off_source(p.read_text(encoding="utf-8")))
            for name, p in firmware_files(root).items()}


def _tree_hashes(folder):
    folder = pathlib.Path(folder)
    return {p.relative_to(folder).as_posix(): sha(p.read_bytes())
            for p in sorted(folder.rglob("*")) if p.is_file() and p.suffix not in SKIP_SUFFIXES}


def logger_hashes(tmp):
    """Synthetic 10 s rows as analysis/fake_logger.py writes them (2 days, base variant)."""
    from analysis import fake_logger
    from data import synth
    days = synth.generate(seed=3, n_days=2, hard=False)[0]
    tmp = pathlib.Path(tmp)
    fake_logger.write_logger_files(days, tmp / "new")
    fake_logger.write_logger_files(days, tmp / "old", extra_columns=False)
    return _tree_hashes(tmp)


def pipeline_hashes(tmp):
    """The paper pipeline on a 10-day synthetic campaign; inputs and text outputs."""
    from analysis import demo_synth, pipeline
    root = pathlib.Path(tmp) / "demo"
    raw, drivers, val_date, _ = demo_synth.build(SEED, N_DAYS, root)
    pipeline.run(raw, root / "results", reference=raw / "reference.csv",
                 validation_date=val_date, drivers=drivers, seed=SEED, n_boot=N_BOOT,
                 synthetic=True, log=lambda *a: None)
    return _tree_hashes(root)


def environment():
    import lightgbm
    import numpy
    import pandas
    import pvlib
    import scipy
    return {"system": platform.system(), "machine": platform.machine(),
            "python": platform.python_version(), "numpy": numpy.__version__,
            "pandas": pandas.__version__, "scipy": scipy.__version__,
            "pvlib": pvlib.__version__, "lightgbm": lightgbm.__version__}


def make(root):
    """All three sets, computed with the code found in `root`."""
    root = pathlib.Path(root).resolve()
    sys.path.insert(0, str(root))
    with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
        return {"reference": f"main at {REFERENCE_COMMIT[:7]} (before the Phase B preparation)",
                "environment": environment(), "firmware": firmware_hashes(root),
                "logger": logger_hashes(a), "pipeline": pipeline_hashes(b)}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=str(HERE.parent.parent), help="checkout to compute from")
    ap.add_argument("--write", action="store_true", help=f"write {GOLDEN.name}")
    ap.add_argument("--out", default=None, help="write to this file instead")
    a = ap.parse_args()
    result = make(a.root)
    target = pathlib.Path(a.out) if a.out else GOLDEN if a.write else None
    if target is None:
        print(json.dumps(result, indent=1))
    else:
        target.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
        print(f"{sum(len(result[k]) for k in ('firmware', 'logger', 'pipeline'))} hashes -> {target}")


if __name__ == "__main__":
    main()
