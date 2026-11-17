"""The ground rule of the Phase B preparation: every new switch is OFF by default, and with
all of them off the project gives what it gave before (main at 51d18aa).

  * firmware: with the three compile-time flags resolved to 0, every source file of the
    logger is, character for character, the earlier file; what was added is either removed
    by the flags or is never referred to;
  * logger rows: the synthetic logger files come out byte-identical;
  * analysis: every text output of the pipeline on a 10-day SYNTHETIC campaign comes out
    byte-identical.
The reference is tests/golden/phase_b_off.json, made from the earlier commit by
tests/golden/make_phase_b_off.py (read its header for what the hashes can and cannot
show on another machine)."""
import importlib.util
import json
import pathlib
import re

import pytest

import config
from analysis import pipeline
from export import phase_b_header

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("make_golden", ROOT / "tests/golden/make_phase_b_off.py")
golden_maker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(golden_maker)
GOLDEN = json.loads(golden_maker.GOLDEN.read_text(encoding="utf-8"))
FLAGS = golden_maker.NEW_FLAGS
# New firmware files that are compiled or installed even with the flags off, with the names
# through which the rest of the firmware could reach them. They are plain C logic (tested
# on the PC) and one generated header of constants: harmless as long as nothing refers to
# them, which test_new_firmware_files_are_inert_with_flags_off checks.
UNGUARDED = {
    "firmware/libraries/PvDaq/src/pv_spray_logic.h": ("pv_spray_logic", "spray_", "SPRAY_"),
    "firmware/libraries/PvDaq/src/pv_spray_logic.c": ("pv_spray_logic", "spray_", "SPRAY_"),
    "firmware/libraries/PvDaq/src/pv_timing.h": ("pv_timing", "timing_"),
    "firmware/libraries/PvDaq/src/pv_timing.c": ("pv_timing", "timing_"),
    "export/c/generated/phase_b_config.h": ("phase_b_config", "PHASE_B_", "MARKER_CODE_"),
}


def _compare(part, now):
    """Names whose hash differs from the golden reference, and names missing or new."""
    ref = GOLDEN[part]
    return ([k for k in ref if k in now and ref[k] != now[k]],
            sorted(set(ref) - set(now)), sorted(set(now) - set(ref)))


def _same_machine():
    env = golden_maker.environment()
    return all(env[k] == GOLDEN["environment"][k] for k in env)


# --- the switches ----------------------------------------------------------------------------

def test_every_switch_is_off_by_default():
    assert config.PHASE_B_ENABLED is False
    assert config.LOADOFF_CLASS_ENABLED is False
    assert config.ECT_ENABLED is False
    text = (ROOT / "firmware/libraries/PvDaq/src/pv_config.h").read_text(encoding="utf-8")
    for flag in FLAGS:
        assert re.search(rf"#ifndef {flag}\n(?://.*\n)*#define {flag} 0\n#endif", text), flag
    assert set(re.findall(r"#ifndef (\w+)", text)) == {"HAS_LOAD", *FLAGS}


def test_generated_header_is_current():
    assert phase_b_header.PATH.read_text(encoding="utf-8") == phase_b_header.header()


def test_placeholders_are_in_one_place_and_tagged():
    """Every Phase B value the firmware uses comes from the generated header, and every
    value that is not known yet carries the [PLACEHOLDER] tag in config.py."""
    block = (ROOT / "config.py").read_text(encoding="utf-8").split("PHASE B PREPARATION")[1]
    tagged = set(re.findall(r"^(\w+) = .*?# \[PLACEHOLDER\]", block, re.M))
    for name in ("PUMP_DUTY", "SPRAY_MAX_S", "FLOW_PULSES_PER_L", "FLOW_PIN", "FLOW_GLITCH_NS",
                 "PUMP_INA226_ADDR", "PUMP_INA226_SHUNT_OHM", "PUMP_INA226_MAX_A",
                 "PUMP_I_MAX_A", "PUMP_NO_FLOW_S", "TANK_ROM", "PHASE_B_SEED", "ECT_VOC_REF_V",
                 "ECT_BETA_VOC_REL", "ECT_A_IRR", "SPRAY_MIN_DT_WATER_C", "SPRAY_MAX_DT_WATER_C"):
        assert name in tagged, name
    # no Phase B number is typed in the firmware: the glue and the sketch use the names only
    src = ROOT / "firmware/libraries/PvDaq/src"
    glue = (src / "pv_spray.cpp").read_text(encoding="utf-8")
    body = re.search(r"spray_cfg_t pvSprayConfig\(\) \{(.*?)\n\}", glue, re.S).group(1)
    assert re.findall(r"c\.\w+ = ([\d.]+f?);", body) == []
    for path in (src / "pv_spray.h", src / "pv_spray.cpp", ROOT / "firmware/logger/logger.ino"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"#define (PUMP_DUTY|FLOW_PULSES_PER_L|FLOW_PIN|SPRAY_MAX_MS)\b", text)
        assert not re.search(r"constexpr \w+ (FLOW_GLITCH_NS|SPRAY_HARD_MARGIN_MS|FLOW_PIN)\b", text)
    for name in ("FLOW_GLITCH_NS", "SPRAY_HARD_MARGIN_MS"):       # used, and only from the header
        assert name in glue and f"#define {name} " in phase_b_header.header()


# --- the reducer itself ------------------------------------------------------------------------

def test_flag_off_source_keeps_exactly_what_the_compiler_would_see():
    f = golden_maker.flag_off_source
    src = "\n".join(["a", "#if PHASE_B_ENABLED", "b", "#if HAS_LOAD", "c", "#endif", "#else",
                     "d", "#endif", "#ifndef NASIM_TIMING_LOG", "#define NASIM_TIMING_LOG 0",
                     "#endif", "#if HAS_LOAD", "e", "#else", "f", "#endif",
                     "#if !MARKER_CODES_ENABLED", "g", "#else", "h", "#endif  // x", "i"])
    assert f(src).split("\n") == ["a", "d", "#if HAS_LOAD", "e", "#else", "f", "#endif", "g", "i"]
    assert f("x\r\ny\r\n") == "x\ny\n"
    for bad in ("#if PHASE_B_ENABLED && HAS_LOAD\n#endif", "if (PHASE_B_ENABLED) run();",
                "#if HAS_LOAD\n#elif PHASE_B_ENABLED\n#endif", "#ifdef PHASE_B_ENABLED\n#endif",
                "#if PHASE_B_ENABLED\nx"):
        with pytest.raises(ValueError):
            f(bad)
    assert f("// PHASE_B_ENABLED in a comment is fine") == "// PHASE_B_ENABLED in a comment is fine"


# --- firmware ------------------------------------------------------------------------------------

def test_firmware_sources_with_flags_off_are_the_earlier_sources():
    changed, missing, _ = _compare("firmware", golden_maker.firmware_hashes(ROOT))
    assert changed == [] and missing == []


def test_new_firmware_files_are_inert_with_flags_off():
    files = golden_maker.firmware_files(ROOT)
    new = sorted(set(files) - set(GOLDEN["firmware"]))
    old_text = "\n".join(golden_maker.flag_off_source(files[n].read_text(encoding="utf-8"))
                         for n in GOLDEN["firmware"])
    for name in new:
        reduced = golden_maker.flag_off_source(files[name].read_text(encoding="utf-8"))
        code = re.sub(r"/\*.*?\*/", "", reduced, flags=re.S)
        code = [l.strip() for l in re.sub(r"//.*", "", code).split("\n")]
        code = [l for l in code if l and l != "#pragma once" and not l.startswith("#include")]
        if name in UNGUARDED:
            for token in UNGUARDED[name]:               # nothing reaches it with the flags off
                assert token not in old_text, (name, token)
        else:
            assert code == [], f"{name} still holds code with every flag off: {code[:3]}"
    assert set(UNGUARDED) <= set(new)


def test_logger_header_line_is_the_earlier_one_unless_phase_b_is_on():
    ino = (ROOT / "firmware/logger/logger.ino").read_text(encoding="utf-8")
    reduced = golden_maker.flag_off_source(ino)
    assert "spray" not in reduced and "PHASE" not in reduced and "timing" not in reduced
    assert reduced.count("sd.appendLine(day_path, line.c_str(), HEADER);") == 1
    assert 'sd.appendLine("/markers.csv", line, "date,time,millis");' in reduced


# --- logger rows and analysis results ----------------------------------------------------------------

def _check(part, now):
    changed, missing, new = _compare(part, now)
    if (changed or missing or new) and not _same_machine():
        pytest.skip(f"{part}: {len(changed)} files differ from the reference, but it was made "
                    f"on {GOLDEN['environment']} and this machine is {golden_maker.environment()}."
                    " Last-digit differences between maths libraries cannot be told from a "
                    "change here: make the reference again on this machine from commit "
                    f"{golden_maker.REFERENCE_COMMIT[:7]} (tests/golden/make_phase_b_off.py).")
    assert changed == [] and missing == [] and new == []


def test_synthetic_logger_rows_are_byte_identical(tmp_path):
    _check("logger", golden_maker.logger_hashes(tmp_path))


def test_analysis_results_are_byte_identical(tmp_path, monkeypatch):
    """Also: with the switches off the pipeline never enters the new code."""
    def forbidden(*a, **k):
        raise AssertionError("Phase B code was called with every switch off")
    for module, names in (("phase_b", ("event_table", "arm_comparison", "cleaning_check", "run")),
                          ("ect", ("run",))):
        mod = getattr(pipeline, module, None)
        for name in names:
            if mod is not None and hasattr(mod, name):
                monkeypatch.setattr(mod, name, forbidden)
    _check("pipeline", golden_maker.pipeline_hashes(tmp_path))
