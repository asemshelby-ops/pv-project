"""twin/showcase.py and twin/refresh.py: the presentation data of /rooftop (story mode,
controller comparison) is a cut-out of the twin's own run, says SIMULATION everywhere, and
is served and regenerated without touching the model."""
import hashlib
import json
import pathlib
import re
import urllib.request
import threading

import numpy as np
import pytest

from scada import run, server
from twin import SIM_NOTICE, VIEW_LABEL, controllers, params, refresh, rooftop, showcase, validate

WEB = pathlib.Path(server.__file__).resolve().parent / "web"
P = params.load(None, None)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("showcase")
    # the threshold rule for both: no model file of the paper pipeline is needed
    res = showcase.build(out, detector="threshold", story_detector="threshold",
                         params_file=None, log=lambda *a: None)
    return out, res


def test_label_is_the_same_text_on_the_page_and_in_the_package():
    assert VIEW_LABEL == "SIMULATION — placeholder parameters, not measured"
    page = (WEB / "rooftop.html").read_text(encoding="utf-8")
    assert page.count(VIEW_LABEL) == 2          # on the 3D view and on the comparison
    assert f'<span id="v3label" class="badge replay simlabel">{VIEW_LABEL}</span>' in page


def test_scenario_day_has_one_clean_cloud_near_noon():
    table, note = showcase.scenario_day()
    assert "[SCENARIO]" in note and "not a measured day" in note
    w = rooftop.weather_days(table)[0]
    assert len(w) == 4321 and w.date == showcase.SCENARIO_DATE
    dip = showcase.find_cloud(w, showcase.clear_sky(w))
    assert dip is not None and 0.6 < dip["depth"] < 0.8
    assert 11.8 < w.minute[dip["start"]] / 60 < 12.0
    assert dip["start"] < dip["dark"] < dip["sun"] < dip["back"]
    # a cloudless day has no story
    clear, _ = showcase.scenario_day(clouds=())
    w = rooftop.weather_days(clear)[0]
    assert showcase.find_cloud(w, showcase.clear_sky(w)) is None


def test_step_hook_only_reads_the_run_is_unchanged():
    w = rooftop.weather_days(showcase.scenario_day()[0].iloc[2000:2400])[0]
    roof = rooftop.build(P, 1)
    seen = []
    a, sa = rooftop.simulate_day(roof, w, controllers.Continuous(), 5)
    b, sb = rooftop.simulate_day(roof, w, controllers.Continuous(), 5,
                                 on_step=lambda k, plant, rec: seen.append(rec["t_modules"]))
    assert sa == sb and a.equals(b)
    assert len(seen) == len(w) and seen[0].shape == (6, 20)
    # the hook sees the temperatures BEFORE the step: their mean is the logged string mean
    assert seen[7].mean(axis=1) == pytest.approx(a.loc[7, [f"t_string_{z}" for z in range(1, 7)]])


def test_story_is_a_cut_out_of_the_twins_run(built):
    out, res = built
    st = json.loads((out / "story.json").read_text(encoding="utf-8"))
    assert st["simulation"] is True and st["label"] == VIEW_LABEL and st["notice"] == SIM_NOTICE
    assert st["parameter_tags"] == P.tags() and st["weather_kind"] == "scenario"
    assert st["controller"] == "Spike-triggered (threshold rule)"
    n = len(st["t_utc"])
    assert np.diff(st["t_utc"]).tolist() == [10] * (n - 1)
    assert np.asarray(st["modules"]).shape == (n, 6, 20)
    # 2026-11-21 11:50:00 Makkah = 08:50:00 UTC
    assert st["t_utc"][0] == 1795251000
    ids = [c["id"] for c in st["chapters"]]
    assert ids == ["intro", "cloud", "cool", "sun", "spike", "alarm", "spray", "result"]
    assert 60 <= st["seconds"] <= 90
    assert st["chapters"][0]["i0"] == 0 and st["chapters"][-1]["i1"] == n - 1
    assert all(a["i1"] == b["i0"] for a, b in zip(st["chapters"], st["chapters"][1:]))
    assert {c["id"]: c["look"] for c in st["chapters"]}["cloud"] == "realistic"
    f, ch = st["facts"], {c["id"]: c for c in st["chapters"]}
    valve, alarm = np.asarray(st["strings"]["valve"]), np.asarray(st["strings"]["alarm"])
    g, tm = np.asarray(st["g"]), np.asarray(st["modules"]).mean(axis=(1, 2))
    # the captions' numbers are the run's numbers
    assert f["g_cloud"] == g.min() and f["g_before"] == round(g[:ch["intro"]["i1"]].mean())
    assert f["cool_c"] == pytest.approx(f["t_before"] - f["t_cool"], abs=0.05) and f["cool_c"] > 5
    first = int(np.flatnonzero(alarm.any(axis=1))[0])
    assert f["first_string"] == int(np.flatnonzero(alarm[first])[0]) + 1
    assert ch["spike"]["i1"] <= first <= ch["alarm"]["i1"]
    assert not valve[:first].any() and valve[ch["alarm"]["i0"]:ch["spray"]["i1"] + 1].any()
    assert not valve[ch["result"]["i0"] + 1:].any()
    assert f["n_spray"] == int(valve.any(axis=0).sum()) and f["spray_drop_c"] > 0
    assert f["water_used_l"] == pytest.approx(st["water_used_l"][-1], abs=0.05)
    assert f["net_vs_ref_wh"] == pytest.approx(st["e_net_wh"][-1] - st["e_ref_wh"][-1], abs=0.06)
    # temperatures: down under the cloud, up again before the alarm, down while sprayed
    assert tm[ch["cool"]["i1"]] < tm[0] - 5 and tm[first] > tm[ch["sun"]["i0"]] + 2
    assert "threshold rule" in f["detector"]
    # an alarm stays on the picture while its burst runs
    shown = np.asarray(st["strings"]["alarm_shown"])
    assert (shown >= alarm).all() and shown[first + 3].any()


def test_comparison_has_the_five_strategies_on_the_same_day(built):
    out, res = built
    cmp = json.loads((out / "compare.json").read_text(encoding="utf-8"))
    assert cmp["simulation"] is True and cmp["label"] == VIEW_LABEL
    assert [c["key"] for c in cmp["controllers"]] == list(showcase.KEYS)
    by = {c["key"]: c for c in cmp["controllers"]}
    n = len(cmp["t_utc"])
    assert n == 720 and all(len(c[k]) == n for c in cmp["controllers"]
                            for k in ("t_mean", "gain_wh", "pump_wh", "water_l"))
    none = by["none"]
    assert none["totals"]["gain_wh"] == 0 and set(none["gain_wh"]) == {0.0}
    assert none["pump_wh"][-1] == 0 and none["water_l"][-1] == 0
    for c in cmp["controllers"]:
        t = c["totals"]
        assert c["gain_wh"][-1] == pytest.approx(t["gain_wh"], abs=1.0)
        assert c["water_l"][-1] == pytest.approx(t["water_used_l"], abs=0.5)
        assert c["pump_wh"][-1] == pytest.approx(t["e_pump_wh"] + t["e_valves_wh"], abs=1.0)
    # the same numbers as twin.rooftop gives for that day (nothing is re-modelled here)
    w = rooftop.weather_days(showcase.scenario_day()[0])[0]
    _, s = rooftop.simulate_day(rooftop.build(P, 1), w, controllers.Continuous(), 5)
    assert by["continuous"]["totals"]["e_net_wh"] == pytest.approx(s["e_net_wh"], abs=0.01)
    # the story's window is marked on the comparison
    st = json.loads((out / "story.json").read_text(encoding="utf-8"))
    assert cmp["window_utc"] == [st["t_utc"][0], st["t_utc"][-1]]
    assert (out / "compare.png").stat().st_size > 20000


def test_nasim_story_puts_the_alarm_where_it_happens():
    """With an early alarm (NASIM-GBT fires while the sun returns) no chapter is cut after
    its own event; checked with a stand-in for the timing, without the model file."""
    st = {"t_utc": list(range(0, 1200, 10)), "g": [900.0] * 30 + [300.0] * 40 + [900.0] * 50,
          "strings": {"t_zone": [[40.0] * 2] * 120}, "p_pump": [0.0] * 120, "p_valves": [0.0] * 120,
          "water_used_l": [0.0] * 120, "water_circulated_l": [0.0] * 120, "e_pump_wh": [0.0] * 120,
          "e_net_wh": [0.0] * 120, "e_ref_wh": [0.0] * 120}
    dip = {"start": 29, "dark": 33, "sun": 66, "back": 70}
    mods = np.full((120, 2, 3), 40.0)
    alarm = np.zeros((120, 2), dtype=bool)
    valve = np.zeros((120, 2), dtype=bool)
    alarm[68] = True                 # before the irradiance is back
    valve[68:72] = True
    ch, facts = showcase.chapters(st, dip, 0, mods, valve, alarm, 10.0)
    ids = [c["id"] for c in ch]
    assert ids == ["intro", "cloud", "cool", "sun", "alarm", "spray", "result"]
    by = {c["id"]: c for c in ch}
    assert by["sun"]["i1"] <= 68 <= by["alarm"]["i1"] and by["spray"]["i1"] >= 72
    # no alarm at all: the story says so
    ch, facts = showcase.chapters(st, dip, 0, mods, valve & False, alarm & False, 10.0)
    assert [c["id"] for c in ch][-1] == "noalarm" and "first_string" not in facts


def test_captions_cover_every_chapter_and_arabic_wraps_latin_runs():
    cap = json.loads((WEB / "story_captions.json").read_text(encoding="utf-8"))
    ids = {"intro", "cloud", "cool", "sun", "spike", "alarm", "spray", "result", "noalarm", "refused"}
    assert set(cap["en"]["chapters"]) == ids == set(cap["ar"]["chapters"])
    assert cap["en"]["dir"] == "ltr" and cap["ar"]["dir"] == "rtl"
    assert set(cap["en"]["ui"]) == set(cap["ar"]["ui"])
    for cid, c in cap["ar"]["chapters"].items():
        for text in (c["title"], c["text"]):
            # only <bdi dir="ltr">..</bdi> as markup, and outside it no Latin letter, digit
            # or placeholder: those would be reordered in a right-to-left line
            outside = re.sub(r'<bdi dir="ltr">.*?</bdi>', "", text)
            assert "<" not in outside and ">" not in outside, cid
            assert not re.search(r"[A-Za-z0-9{}]", outside), (cid, outside)
    # the same facts are quoted in both languages (the detector has a short Arabic form)
    for cid in ids:
        en = set(re.findall(r"\{(\w+)\}", cap["en"]["chapters"][cid]["text"]))
        ar = set(re.findall(r"\{(\w+)\}", cap["ar"]["chapters"][cid]["text"]))
        assert en == {k.replace("detector_ar", "detector") for k in ar}, cid
    # no claim about a real plant in the result caption
    assert "Placeholder parameters" in cap["en"]["chapters"]["result"]["text"]


def test_font_is_vendored_unchanged_and_served(tmp_path, built):
    folder = WEB / "vendor" / "ibm-plex-sans-arabic-5.3.0"
    readme = (folder / "README.md").read_text(encoding="utf-8")
    files = sorted(p.name for p in folder.iterdir() if p.name != "README.md")
    assert len(files) == 5
    for name in files:
        assert hashlib.sha256((folder / name).read_bytes()).hexdigest() in readme, name
    css = (WEB / "style.css").read_text(encoding="utf-8")
    assert css.count('font-family: "IBM Plex Sans Arabic"') == 5 and "https://" not in css
    live = tmp_path / "historian.sqlite"
    run.open_historian(live, "live")
    empty = tmp_path / "none"
    for folder, has in ((empty, False), (built[0], True)):
        httpd = server.make_server(live, {"mode": "live"}, port=0, showcase_dir=folder)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        try:
            for name in ("story", "compare"):          # served without a running simulation
                with urllib.request.urlopen(f"{base}/api/sim/{name}") as r:
                    body = json.loads(r.read())
                assert body["simulation"] is True
                assert (body.get("label") == VIEW_LABEL) if has else (body["available"] is False)
            with urllib.request.urlopen(base + "/vendor/ibm-plex-sans-arabic-5.3.0/"
                                        "ibm-plex-sans-arabic-arabic-400-normal.woff2") as r:
                assert r.headers["Content-Type"] == "font/woff2" and len(r.read()) > 30000
            for path in ("/rooftop_story.js", "/rooftop_compare.js", "/story_captions.json"):
                with urllib.request.urlopen(base + path) as r:
                    assert r.status == 200
            with urllib.request.urlopen(base + "/api/sim/state") as r:
                assert json.loads(r.read())["available"] is False
        finally:
            httpd.shutdown()
            httpd.server_close()


def test_refresh_refits_and_regenerates_without_touching_the_measured_slot(tmp_path):
    raw, _ = validate.build_demo_files(tmp_path / "rig", seed=11, n_days=4)
    measured = tmp_path / "params_measured.json"
    res = refresh.refresh(raw, real=False, out=tmp_path / "refresh", showcase_out=tmp_path / "show",
                          measured_path=measured, do_validate=False, log=lambda *a: None)
    assert not measured.exists()                           # synthetic files: never [MEASURED]
    assert not params.MEASURED_PATH.exists() or params.read_measured(params.MEASURED_PATH)
    overlay = json.loads(res["overlay"].read_text(encoding="utf-8"))
    assert overlay["tag"] == params.FIT_SYNTH and {"u0", "heat_capacity_j_per_k"} <= set(overlay["params"])
    assert res["tags"][params.FIT_SYNTH] == len(res["values"])
    cmp = json.loads((tmp_path / "show" / "compare.json").read_text(encoding="utf-8"))
    assert cmp["weather_kind"] == "logged" and "LOGGED by the rig" in cmp["weather"]
    assert cmp["parameter_tags"][params.FIT_SYNTH] == len(res["values"])
    assert cmp["label"] == VIEW_LABEL and len(cmp["controllers"]) == 5
    # declared real: the overlay goes to the measured slot given, tagged [MEASURED]
    res = refresh.refresh(raw, real=True, scenario_weather=True, out=tmp_path / "refresh2",
                          showcase_out=tmp_path / "show2", measured_path=measured,
                          do_validate=False, log=lambda *a: None)
    assert json.loads(measured.read_text(encoding="utf-8"))["tag"] == params.MEASURED
