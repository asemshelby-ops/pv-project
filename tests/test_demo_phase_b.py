"""analysis/demo_phase_b.py: the SYNTHETIC end-to-end demonstration of the Phase B
preparation. It turns switches of config.py on for one run; they must be off again
afterwards, whatever happens."""
import pytest

import config
from analysis import demo_phase_b

SWITCHES = ("PHASE_B_ENABLED", "ECT_ENABLED", "LOADOFF_CLASS_ENABLED")


def test_switches_are_on_inside_the_run_and_off_again_after_it_even_after_an_error():
    seen = demo_phase_b._with_switches(SWITCHES[:2], lambda: [getattr(config, n) for n in SWITCHES])
    assert seen == [True, True, False]
    assert [getattr(config, n) for n in SWITCHES] == [False, False, False]

    def fails():
        assert config.LOADOFF_CLASS_ENABLED is True
        raise RuntimeError("stopped half-way")
    with pytest.raises(RuntimeError):
        demo_phase_b._with_switches(("LOADOFF_CLASS_ENABLED",), fails)
    assert [getattr(config, n) for n in SWITCHES] == [False, False, False]


def test_only_the_new_sections_of_a_summary_are_printed(tmp_path):
    summary = tmp_path / "summary.md"
    summary.write_text("# title\n\n## Table 1 - spikes\n\nold\n\n## Phase B - randomized spray trial\n\n"
                       "new\n### arms\n\nrows\n\n## Gamma\n\nold\n\n## Verdict on the detector claim\n\nboth\n",
                       encoding="utf-8")
    assert demo_phase_b.new_sections(summary) == [
        "## Phase B - randomized spray trial", "", "new", "### arms", "", "rows", "",
        "## Verdict on the detector claim", "", "both"]


def test_trial_demo_runs_on_synthetic_files_and_leaves_the_switches_off(tmp_path):
    res = demo_phase_b.trial(seed=5, n_days=8, root=tmp_path, n_boot=20, log=lambda *a: None)
    assert [getattr(config, n) for n in SWITCHES] == [False, False, False]
    assert res["phase_b"]["audit"]["wet"] > 10 and res["ect"] is not None
    text = "\n".join(demo_phase_b.new_sections(tmp_path / "trial" / "results" / "summary.md"))
    assert "## Phase B - randomized spray trial (NOT ADOPTED" in text
    assert "## Equivalent cell temperature from V_oc" in text
    assert text.count("SYNTHETIC DATA") >= 2                      # every new section says so
    assert (tmp_path / "trial" / "generator_events.csv").exists()


def test_loadoff_demo_prints_both_verdicts(tmp_path):
    res = demo_phase_b.loadoff(seed=5, n_days=10, root=tmp_path, n_boot=20, log=lambda *a: None)
    assert config.LOADOFF_CLASS_ENABLED is False
    text = "\n".join(demo_phase_b.new_sections(tmp_path / "loadoff" / "results" / "summary.md"))
    assert "with and without load-off spikes" in text
    assert (res["events"]["cause"] == "load_off").sum() >= 10
