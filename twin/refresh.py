"""One command for the day real data arrives: refit the twin from the logger's files and
regenerate the presentation views. SIMULATION (the twin stays a model; only its parameters
and the weather of the shown day become measured).

    python -m twin.refresh --data <copy of the SD card> --real
    python -m twin.refresh --data <copy of the SD card> --real --gamma -0.0041 --export
    python -m twin.refresh --data out/twin/validation_demo/raw        a dry run on SYNTHETIC files

What it does, in this order:
  1. twin.validate   the twin as it is against the files      -> <out>/1_before/report.md
  2. twin.fit        single-diode values from the sweep files, heat capacity and Faiman
                     coefficients from the day files. With --real: twin/params_measured.json,
                     tag [MEASURED], used by every twin program from then on. Without
                     --real: <out>/fit_synthetic.json, tag [FIT-SYNTHETIC], used by this run
                     only (a fit on synthetic files never enters the measured slot).
  3. twin.validate   the fitted twin against the same files   -> <out>/2_after/report.md
                     (the same days as the fit: validate again on days kept apart)
  4. twin.showcase   story mode and controller comparison of /rooftop, with the new
                     parameters and the weather of a LOGGED day (--date, or the day with the
                     deepest midday cloud; --scenario-weather keeps the scenario day)
  5. --export        scada.export_story: the MP4 and the stills for slides

Then start the page as usual: python -m scada.run --sim   (or --showcase).
The pump curve has its own slot (twin/data/pump_curve_R385_measured.csv, docs/twin/README.md);
evaporation, spray conductance and pump power are NOT fitted by this command: they need the
bucket test and the spray test. Until then the label of the views stays as it is.
"""
import argparse
import pathlib

from twin import SIM_NOTICE, fit, params, showcase, validate

OUT = pathlib.Path("out/twin/refresh")


def refresh(data, real=False, gamma=None, date=None, scenario_weather=False, out=OUT,
            showcase_out=showcase.OUT, measured_path=params.MEASURED_PATH, do_validate=True,
            log=print):
    """Returns {"overlay": path, "values": {...}, "tags": {...}, "showcase": {...}}."""
    out = pathlib.Path(out)
    out.mkdir(parents=True, exist_ok=True)
    kind = "REAL rig files" if real else "logger files NOT declared real (dry run)"
    source = f"{kind} in {data}"
    p0 = params.load(measured_path, None) if real else params.load(None, None)
    if do_validate:
        log(f"1/4 validation before the fit ({source})")
        validate.run_folder(data, out / "1_before", p0, source,
                            title="Twin validation BEFORE the refit", log=log)
    log("2/4 fit")
    values, diag, _ = fit.fit_folder(data, p0, gamma=gamma, log=log)
    overlay = pathlib.Path(measured_path) if real else out / "fit_synthetic.json"
    fit.write_overlay(overlay, values, params.MEASURED if real else params.FIT_SYNTH,
                      f"twin.refresh on {data}", diag)
    p1 = params.load(overlay, None)
    log(f"    {len(values)} parameter(s) {params.MEASURED if real else params.FIT_SYNTH} -> {overlay}")
    for name, value in values.items():
        log(f"      {name}: {getattr(p0, name):.6g} -> {value:.6g} {p0.meta[name].unit}")
    if do_validate:
        log("3/4 validation after the fit (same days as the fit)")
        validate.run_folder(data, out / "2_after", p1, source + "; parameters fitted to these files",
                            title="Twin validation AFTER the refit", log=log)
    log("4/4 story mode and controller comparison")
    built = showcase.build(showcase_out, None if scenario_weather else data, date,
                           params_file=None if real else overlay, log=log)
    left = p1.placeholders()
    log(f"{len(left)} parameters are still [LIT-PLACEHOLDER], among them the ones that decide "
        "the sign of the net energy (docs/twin/ranking.md): evaporation, pump power, spray "
        "flow. The views keep their SIMULATION label.")
    log(SIM_NOTICE)
    return {"overlay": overlay, "values": values, "tags": p1.tags(), "showcase": built}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", required=True, help="folder with the logger's files (day files, SW*/SS*)")
    ap.add_argument("--real", action="store_true",
                    help="the folder holds REAL rig files: write twin/params_measured.json")
    ap.add_argument("--gamma", type=float, help="measured gamma of P_mp in 1/degC (analysis/gamma.py)")
    ap.add_argument("--date", help="logged day to show (YYYY-MM-DD); default: deepest midday cloud")
    ap.add_argument("--scenario-weather", action="store_true",
                    help="keep the scenario day's weather in the views (only the parameters change)")
    ap.add_argument("--no-validate", action="store_true", help="skip the two validation reports")
    ap.add_argument("--export", action="store_true", help="also render the MP4 and the stills")
    ap.add_argument("--lang", choices=("en", "ar", "both"), default="en", help="captions of the export")
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()
    refresh(a.data, a.real, a.gamma, a.date, a.scenario_weather, a.out,
            do_validate=not a.no_validate)
    if a.export:
        from scada import export_story
        export_story.export(langs=("en", "ar") if a.lang == "both" else (a.lang,))
    print("Open the views:  python -m scada.run --sim   ->  http://127.0.0.1:8050/rooftop?story=1")


if __name__ == "__main__":
    main()
