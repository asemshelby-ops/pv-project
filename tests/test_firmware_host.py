"""The logger sketch and the Phase B glue, type-checked on the PC in every combination of
the new compile-time flags.

WHAT THIS IS NOT: a build. It runs `g++ -fsyntax-only` against small stub headers
(tests/firmware_host/stubs) that only DECLARE the Arduino / ESP-IDF calls the project uses,
written from those APIs' documentation. It catches typos, wrong types, missing
declarations and a flag combination that does not parse, on a machine without the ESP32
toolchain. Whether the real core accepts the code, and what it weighs, only
`python firmware/build_all.py` (arduino-cli, pinned core 3.3.8) can say.
"""
import itertools
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
STUBS = ROOT / "tests/firmware_host/stubs"
SRC = ROOT / "firmware/libraries/PvDaq/src"
INCLUDES = [STUBS, SRC, ROOT / "export/c", ROOT / "export/c/generated"]
# a function definition that starts in column 0: `type name(args) {`
DEFINITION = re.compile(r"^([A-Za-z_][\w \*&:<>]*?[ \*&])([A-Za-z_]\w*)\s*\(([^;{}]*)\)\s*\{", re.M)
# a wet build needs the two switches of the generated header the other way round
WET = ('#include "phase_b_config.h"\n#undef PHASE_B_DRY_RUN\n#define PHASE_B_DRY_RUN 0\n'
       "#undef PHASE_B_PLACEHOLDERS_CONFIRMED\n#define PHASE_B_PLACEHOLDERS_CONFIRMED 1\n")

pytestmark = pytest.mark.skipif(shutil.which("g++") is None, reason="no g++")


def sketch_as_cpp(path):
    """An .ino file as one C++ file: Arduino.h first, and a prototype of every function
    before the first one, which is what arduino-cli does before it compiles a sketch."""
    text = path.read_text(encoding="utf-8")
    protos = [f"{m.group(1)}{m.group(2)}({m.group(3)});" for m in DEFINITION.finditer(text)]
    first = DEFINITION.search(text).start()
    return "#include <Arduino.h>\n" + text[:first] + "\n".join(protos) + "\n" + text[first:]


def syntax_check(source, **defines):
    cmd = ["g++", "-std=gnu++17", "-fsyntax-only", "-Wall", "-Wextra", "-x", "c++", "-",
           *[f"-I{i}" for i in INCLUDES], *[f"-D{k}={v}" for k, v in defines.items()]]
    r = subprocess.run(cmd, input=source, capture_output=True, text=True)
    return r.returncode, r.stderr


LOGGER = sketch_as_cpp(ROOT / "firmware/logger/logger.ino")
SPRAY = (SRC / "pv_spray.cpp").read_text(encoding="utf-8")
COMBOS = list(itertools.product((0, 1), repeat=4))      # load, phase B, marker codes, timing


@pytest.mark.parametrize("load, phase_b, codes, timing", COMBOS)
def test_logger_parses_in_every_flag_combination(load, phase_b, codes, timing):
    rc, err = syntax_check(LOGGER, HAS_LOAD=load, PHASE_B_ENABLED=phase_b,
                           MARKER_CODES_ENABLED=codes, NASIM_TIMING_LOG=timing)
    assert rc == 0 and err.strip() == "", err[:2000]     # no error and no warning


@pytest.mark.parametrize("load", (0, 1))
def test_wet_build_parses(load):
    for source in (LOGGER, SPRAY):
        rc, err = syntax_check(WET + source, HAS_LOAD=load, PHASE_B_ENABLED=1,
                               MARKER_CODES_ENABLED=1, NASIM_TIMING_LOG=1)
        assert rc == 0 and err.strip() == "", err[:2000]


def test_spray_glue_parses_in_a_dry_run_and_is_empty_without_the_flag():
    rc, err = syntax_check(SPRAY, PHASE_B_ENABLED=1)
    assert rc == 0 and err.strip() == "", err[:2000]
    r = subprocess.run(["g++", "-E", "-P", "-x", "c++", "-", *[f"-I{i}" for i in INCLUDES]],
                       input=SPRAY, capture_output=True, text=True, check=True)
    assert "PvSpray" not in r.stdout and "spray_" not in r.stdout   # flag off: nothing of it


def test_a_wet_build_is_refused_while_placeholders_are_not_confirmed():
    """PHASE_B_DRY_RUN 0 with PHASE_B_PLACEHOLDERS_CONFIRMED 0 must stop the compiler."""
    only_wet = '#include "phase_b_config.h"\n#undef PHASE_B_DRY_RUN\n#define PHASE_B_DRY_RUN 0\n'
    rc, err = syntax_check(only_wet + SPRAY, PHASE_B_ENABLED=1)
    assert rc != 0 and "[PLACEHOLDER]" in err and "PHASE_B_PLACEHOLDERS_CONFIRMED" in err


def test_the_check_does_catch_an_error():
    """Guard against a check that passes everything: a call to a function that does not
    exist, inside the Phase B block, must fail with the flag on and pass with it off."""
    broken = LOGGER.replace("    spray.row(in);", "    spray.row(in);\n    noSuchCall(in);")
    assert broken != LOGGER
    assert syntax_check(broken, PHASE_B_ENABLED=1)[0] != 0
    assert syntax_check(broken, PHASE_B_ENABLED=0)[0] == 0
