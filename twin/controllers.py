"""The cooling-control strategies compared on the simulated rooftop. SIMULATION.

Every controller sees only the observation (twin/rooftop.py Obs: sensor readings, string
powers, weather, tank temperature) and answers with the valves it wants open and one pump
duty. Valve dwell times and the pump limit are enforced by the plant, the same for all.

  NoCooling        nothing
  Continuous       every valve open while there is daylight
  FixedThreshold   per zone: on at 45 degC, off at 35 degC (Moharram et al. 2013)
  SpikeTriggered   per zone: a detector of detect/ (NASIM-GBT, the threshold rule or the
                   CUSUM) raises an alarm; the net-power rule decides whether a burst is
                   worth it and how long it lasts
  Predictive       per zone: spray only while the expected power gained exceeds the pump
                   power, with the pump duty that gives the largest net gain

The two model-based rules (burst length, expected gain) use a COPY of the twin's
parameters as the controller's knowledge of the plant; compare.py also runs the predictive
controller with a deliberately wrong copy.

Expected gain (Predictive). Taking heat E out of a module lowers its temperature by E / C,
and that difference decays with tau = C / (A U): the integral of the temperature
difference is E / (A U), whatever the heat capacity and however the heat was taken. With
k = -gamma * P_mp the power gained per degC, one second of pumping that removes q_cool
watts is worth k * q_cool / (A U) joules (CLAUDE.md, pump break-even). So

    gain = k(G) * q_cool(T_zone, T_water, duty, RH, wind) / (A * U(wind))     [W per module]
    cost = pump power per module at that duty + valve power share

and a zone is sprayed while gain / cost stays above the switching ratio. It predicts by
persistence: it assumes irradiance and wind stay as they are for the next minutes.
"""
import json
import pathlib

import numpy as np

import config
from detect.stream import Sample
from twin import electrical, spray, thermal
from twin.rooftop import Command


class Controller:
    name = "controller"

    def reset(self, roof):
        self.p = roof.p
        self.n = roof.shape[0]
        self.m = roof.shape[1]

    def none(self):
        return np.zeros(self.n, dtype=bool)

    def step(self, obs):
        raise NotImplementedError


class NoCooling(Controller):
    name = "No cooling"

    def step(self, obs):
        return Command(self.none(), 0.0)


class Continuous(Controller):
    name = "Continuous"

    def __init__(self, duty=None):
        self.duty = duty
        if duty is not None:
            self.name = f"Continuous, duty {duty:g}"

    def step(self, obs):
        on = obs.g >= self.p.cooling_min_g
        duty = self.p.baseline_duty if self.duty is None else self.duty
        return Command(np.full(self.n, on), duty if on else 0.0)


class FixedThreshold(Controller):
    name = "Fixed threshold"

    def __init__(self, on_c=None, off_c=None, duty=None):
        """Limits and duty default to params.py (fixed_on_c, fixed_off_c, baseline_duty)."""
        self.on_c, self.off_c, self.duty = on_c, off_c, duty
        if on_c is not None and off_c is not None:
            self.name = self._name(on_c, off_c)

    def _name(self, on, off):
        return f"Fixed threshold {on:g}/{off:g} degC" + (
            "" if self.duty is None else f", duty {self.duty:g}")

    def reset(self, roof):
        super().reset(roof)
        self.on = self.p.fixed_on_c if self.on_c is None else self.on_c
        self.off = self.p.fixed_off_c if self.off_c is None else self.off_c
        self.d = self.p.baseline_duty if self.duty is None else self.duty
        self.name = self._name(self.on, self.off)
        self.state = self.none()

    def step(self, obs):
        t = obs.t_zone
        self.state = np.where(t >= self.on, True, np.where(t <= self.off, False, self.state))
        if obs.g < self.p.cooling_min_g:
            self.state = self.none()
        return Command(self.state.copy(), self.d if self.state.any() else 0.0)


# --- the controller's model of one module -----------------------------------------------------

class ModuleKnowledge:
    """What a model-based controller knows about a module: gamma, P_mp at reference
    conditions, the thermal and the spray model, all from a parameter set `mp`."""

    def __init__(self, mp):
        self.mp = mp
        self.gamma = electrical.gamma_pmp(mp)
        self.p_ref = float(electrical.module_mpp(mp, 1000.0, 25.0)[0])

    def wind(self, obs):
        return obs.wind if np.isfinite(obs.wind) else self.mp.wind_fallback_ms

    def gain_per_degc(self, g):
        """Power gained per degC of cooling, W per module."""
        return -self.gamma * self.p_ref * g / 1000.0

    def q_cool(self, t_zone, obs, duty):
        """Heat a module loses to the spray at `duty`, W, and the pump power per module."""
        flow, p_unit = spray.pump_point(self.mp, duty)
        if flow <= 0:
            return np.zeros_like(t_zone), 0.0
        k_w, q_evap, _ = spray.cooling(self.mp, t_zone, obs.t_water, flow, obs.ta, obs.rh,
                                       self.wind(obs))
        return k_w * (t_zone - obs.t_water) + q_evap, p_unit * self.mp.plant_pump_power_factor

    def loss_w_per_k(self, obs):
        return self.mp.area_m2 * float(thermal.u_loss(self.mp, self.wind(obs)))


class Predictive(Controller):
    name = "Predictive (gain > pump power)"

    def __init__(self, model_params=None, name=None, on_ratio=None, off_ratio=None):
        """model_params: the controller's own picture of the plant (default: the plant's
        parameters). on_ratio / off_ratio default to params.py."""
        self.model_params, self.on_ratio, self.off_ratio = model_params, on_ratio, off_ratio
        if name:
            self.name = name

    def reset(self, roof):
        super().reset(roof)
        self.know = ModuleKnowledge(self.model_params or roof.p)
        self.r_on = self.p.predictive_on_ratio if self.on_ratio is None else self.on_ratio
        self.r_off = self.p.predictive_off_ratio if self.off_ratio is None else self.off_ratio
        self.state = self.none()
        self.ratio = np.zeros(self.n)

    def step(self, obs):
        p = self.p
        if obs.g < p.cooling_min_g:
            self.state = self.none()
            return Command(self.state.copy(), 0.0)
        t = obs.t_zone
        scale = self.know.gain_per_degc(obs.g) / self.know.loss_w_per_k(obs)
        best = (-np.inf, self.none(), 0.0, np.zeros(self.n))
        for duty in p.predictive_duties:
            q, p_pump = self.know.q_cool(t, obs, duty)
            cost = p_pump + p.valve_hold_w / self.m
            if cost <= 0:
                continue
            ratio = scale * q / cost
            on = np.where(self.state, ratio >= self.r_off, ratio >= self.r_on)
            net = float(((scale * q - cost) * on).sum())
            if on.any() and net > best[0]:
                best = (net, on, duty, ratio)
        _, self.state, duty, self.ratio = best
        return Command(self.state.copy(), duty)


class SpikeTriggered(Controller):
    """A detector per zone; an alarm starts a burst if the net-power rule allows it.

    Rule (the team simulation of CLAUDE.md, with the controller's model in place of the
    fixed numbers): rise = larger of the rise seen over the last 10 min and the spike
    definition (config.SPIKE_THETA_C, 3 degC: the alarm says such a rise is under way);
        burst  = C * rise / q_cool, between spike_min_burst_s and spike_max_burst_s
        gain   = k(G) * rise * tau            tau = C / (A U): the spike's stay
        cost   = pump power per module * burst
    A burst is fired when gain > cost. After a burst the module re-heats by about what
    was cooled; the detector must not read that as a new spike. So the zone's detector is
    not fed during the burst and for spike_blank_s after it, and is then restarted (its
    own 10-min warm-up follows): about 25 min without detection per burst and zone."""

    def __init__(self, make_detector, name, model_params=None):
        self.make_detector, self.name, self.model_params = make_detector, name, model_params

    def reset(self, roof):
        super().reset(roof)
        self.know = ModuleKnowledge(self.model_params or roof.p)
        self.det = [self.make_detector() for _ in range(self.n)]
        for d in self.det:
            d.reset()
        self.left = np.zeros(self.n)                  # seconds of burst left per zone
        self.blank = np.zeros(self.n)                 # seconds of blanking left per zone
        n10 = int(config.DET_WINDOW_MIN * 60 / self.p.dt_s)
        self.hist = np.full((n10 + 1, self.n), np.nan)
        self.fired = 0
        self.refused = 0

    def step(self, obs):
        p, t = self.p, obs.t_zone
        self.hist = np.roll(self.hist, -1, axis=0)
        self.hist[-1] = t
        alarms = self.none()
        was_burst, was_blank = self.left > 0, self.blank > 0
        self.left = np.maximum(self.left - p.dt_s, 0.0)
        self.blank = np.where(was_burst & (self.left <= 0), p.spike_blank_s,
                              np.maximum(self.blank - p.dt_s, 0.0))
        for z in np.flatnonzero(was_blank & (self.blank <= 0)):   # blanking over: restart
            self.det[z].reset()
        for z in np.flatnonzero((self.left <= 0) & (self.blank <= 0) & ~was_burst):
            s = Sample(tuple(obs.t_sensors[z]), obs.ta, obs.g, float("nan"), obs.doy, obs.minute)
            alarms[z] = bool(self.det[z].update(s))
        if alarms.any() and obs.g >= p.cooling_min_g:
            q, p_pump = self.know.q_cool(t, obs, p.spike_duty)
            c = self.know.mp.heat_capacity_j_per_k
            tau = c / self.know.loss_w_per_k(obs)
            for z in np.flatnonzero(alarms & (self.left <= 0)):
                rise = max(float(t[z] - np.nanmin(self.hist[:, z])), config.SPIKE_THETA_C)
                if q[z] <= 0:
                    self.refused += 1
                    continue
                burst = float(np.clip(c * rise / q[z], p.spike_min_burst_s, p.spike_max_burst_s))
                gain = self.know.gain_per_degc(obs.g) * rise * tau
                cost = (p_pump + p.valve_hold_w / self.m) * burst
                if gain > cost:
                    self.left[z] = burst
                    self.fired += 1
                else:
                    self.refused += 1
        on = self.left > 0
        return Command(on, p.spike_duty if on.any() else 0.0, alarms)


# --- detectors for SpikeTriggered -------------------------------------------------------------

NASIM_RUN = pathlib.Path("out/paper_demo") / f"seed{config.EXPORT_SEED}" / "results"


def tuned_settings(run=NASIM_RUN):
    """The detector settings chosen on the validation days of a pipeline run."""
    f = pathlib.Path(run) / "tuned.json"
    if not f.exists():
        raise FileNotFoundError(
            f"{f} not found. The NASIM detector is trained by the paper pipeline; create "
            f"it with:  python -m analysis.demo_synth   (synthetic data, about 10 min)")
    return json.loads(f.read_text())


def nasim_factory(run=NASIM_RUN):
    """NASIM-GBT exactly as the pipeline trained and tuned it (hybrid, no wind): the model
    file and the probability threshold of `run`. Trained on SYNTHETIC data today."""
    import lightgbm as lgb

    from detect.gbt import GbtDetector
    tuned = tuned_settings(run)
    booster = lgb.Booster(model_file=str(pathlib.Path(run) / "nasim_gbt_nowind.txt"))
    return lambda: GbtDetector(booster, use_wind=False, p_threshold=tuned["nasim_p_threshold"],
                               hybrid=True, persist=config.GBT_PERSIST)


def threshold_factory(rise_c=None, run=NASIM_RUN):
    """The temperature-rise rule without irradiance gate (the paper's baseline a)."""
    from detect.threshold import ThresholdDetector
    rise = rise_c if rise_c is not None else tuned_settings(run)["threshold_rise_C"]
    return lambda: ThresholdDetector(rise_c=rise, gate=None)


def cusum_factory(kappa=None, h=None, run=NASIM_RUN):
    """CUSUM on the temperature increment (the paper's baseline b)."""
    from detect.cusum_inc import CusumIncDetector
    if kappa is None or h is None:
        tuned = tuned_settings(run)
        kappa, h = tuned["cusum_kappa_C_per_sample"], tuned["cusum_h_C"]
    return lambda: CusumIncDetector(kappa, h)
