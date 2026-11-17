"use strict";
// Controller comparison of the rooftop SIMULATION page: the five strategies on the SAME
// simulated day, the same rooftop and the same sensor noise (/api/sim/compare, written by
// twin/showcase.py). Every number here is SIMULATED with placeholder parameters; the cards,
// the chart titles and the tooltip say so. Small multiples: one measure per chart, one time
// axis for all four, one crosshair. Plain JavaScript, no library; text goes in as text.
(function () {
  const R = window.RoofShared, $ = (id) => document.getElementById(id);
  const M = { L: 56, R: 118, T: 8, B: 22 };           // right margin: the lines' own labels
  // colour follows the strategy, in a fixed order (the page's series colours); the
  // reference without cooling is neutral and dashed
  const LOOK = { none: ["--ink2", "No cooling"], continuous: ["--series-1", "Continuous"], fixed: ["--series-2", "Fixed 45/35"],
    spike: ["--series-3", "Spike-triggered"], predictive: ["--series-4", "Predictive"] };
  const CHARTS = [["cmpTemp", "t_mean", 1, "°C", true], ["cmpGain", "gain_wh", 0, "Wh", false],
    ["cmpPump", "pump_wh", 0, "Wh", false], ["cmpWater", "water_l", 0, "L", false]];
  const S = { data: null, hover: -1 };

  function prepare(cv) {
    if (!cv.dataset.h) { cv.dataset.h = cv.getAttribute("height"); cv.style.height = cv.dataset.h + "px"; }
    const dpr = window.devicePixelRatio || 1, w = cv.clientWidth, h = +cv.dataset.h;
    cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr);
    const g = cv.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, w, h);
    g.font = "11px system-ui, 'Segoe UI', sans-serif";
    return { g, w, h, pw: w - M.L - M.R, ph: h - M.T - M.B };
  }
  function draw(id, key, digits, unit, withRef) {
    const d = S.data, c = prepare($(id)), { g } = c, t = d.t_utc, t0 = t[0], t1 = t[t.length - 1];
    const x = (v) => M.L + ((v - t0) / (t1 - t0)) * c.pw;
    const series = d.controllers.filter((s) => withRef || s.key !== "none");
    let lo = Infinity, hi = -Infinity;
    for (const s of series) for (const v of s[key]) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
    if (key !== "t_mean") { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
    if (hi - lo < 1e-9) hi = lo + 1;
    const step = R.niceStep(hi - lo, 4);
    lo = Math.floor(lo / step) * step; hi = Math.ceil(hi / step) * step;
    const y = (v) => M.T + c.ph - ((v - lo) / (hi - lo)) * c.ph;
    if (d.window_utc) {                                  // the window the story mode plays
      g.fillStyle = R.css("--grid"); g.fillRect(x(d.window_utc[0]), M.T, Math.max(2, x(d.window_utc[1]) - x(d.window_utc[0])), c.ph);
      if (key === "t_mean") { g.fillStyle = R.css("--muted"); g.textAlign = "left"; g.textBaseline = "top"; g.fillText("story", x(d.window_utc[0]) + 3, M.T + 2); }
    }
    g.lineWidth = 1; g.textAlign = "right"; g.textBaseline = "middle";
    for (let v = lo; v <= hi + step / 2; v += step) {
      const yy = Math.round(y(v)) + 0.5;
      g.strokeStyle = R.css("--grid"); g.beginPath(); g.moveTo(M.L, yy); g.lineTo(M.L + c.pw, yy); g.stroke();
      g.fillStyle = R.css("--muted"); g.fillText(String(+v.toFixed(6)), M.L - 8, yy);
    }
    g.textAlign = "center"; g.textBaseline = "top";
    for (let h = Math.ceil((t0 + R.TZ_S) / 7200) * 7200 - R.TZ_S; h <= t1; h += 7200) {
      const xx = Math.round(x(h)) + 0.5, loc = new Date((h + R.TZ_S) * 1000);
      g.strokeStyle = R.css("--grid"); g.beginPath(); g.moveTo(xx, M.T); g.lineTo(xx, M.T + c.ph); g.stroke();
      g.fillStyle = R.css("--muted"); g.fillText(`${R.pad(loc.getUTCHours())}:00`, xx, M.T + c.ph + 6);
    }
    g.strokeStyle = R.css("--axis"); g.beginPath(); g.moveTo(M.L, M.T + c.ph + 0.5); g.lineTo(M.L + c.pw, M.T + c.ph + 0.5); g.stroke();
    if (key !== "t_mean") {                               // zero = the same as no cooling / nothing used
      g.strokeStyle = R.css("--ink2"); g.setLineDash([5, 4]); g.beginPath(); g.moveTo(M.L, Math.round(y(0)) + 0.5); g.lineTo(M.L + c.pw, Math.round(y(0)) + 0.5); g.stroke(); g.setLineDash([]);
    }
    const ends = [];
    for (const s of series) {
      g.strokeStyle = R.css(LOOK[s.key][0]); g.lineWidth = 2; g.lineJoin = "round"; g.setLineDash(s.key === "none" ? [5, 4] : []);
      g.beginPath(); s[key].forEach((v, i) => (i ? g.lineTo(x(t[i]), y(v)) : g.moveTo(x(t[i]), y(v)))); g.stroke(); g.setLineDash([]);
      ends.push({ s, y: y(s[key][s[key].length - 1]) });
    }
    // each line is named at its end; labels that would collide are moved apart
    ends.sort((a, b) => a.y - b.y);
    for (let i = 1; i < ends.length; i++) if (ends[i].y - ends[i - 1].y < 12) ends[i].y = ends[i - 1].y + 12;
    const over = ends.length ? ends[ends.length - 1].y - (M.T + c.ph) : 0;
    if (over > 0) ends.forEach((e) => { e.y -= over; });
    g.textAlign = "left"; g.textBaseline = "middle";
    for (const e of ends) {
      g.fillStyle = R.css(LOOK[e.s.key][0]); g.fillRect(M.L + c.pw + 6, e.y - 1, 10, 2);
      g.fillStyle = R.css("--ink2"); g.fillText(LOOK[e.s.key][1], M.L + c.pw + 20, e.y);
    }
    if (S.hover >= 0) {
      const xx = Math.round(x(t[S.hover])) + 0.5;
      g.strokeStyle = R.css("--muted"); g.lineWidth = 1; g.beginPath(); g.moveTo(xx, M.T); g.lineTo(xx, M.T + c.ph); g.stroke();
      for (const s of series) {
        g.fillStyle = R.css("--surface"); g.beginPath(); g.arc(xx, y(s[key][S.hover]), 6, 0, 6.3); g.fill();
        g.fillStyle = R.css(LOOK[s.key][0]); g.beginPath(); g.arc(xx, y(s[key][S.hover]), 4, 0, 6.3); g.fill();
      }
    }
  }
  function drawAll() { if (S.data) for (const c of CHARTS) draw(...c); }

  function tip(ev) {
    const d = S.data, box = $("tip"), i = S.hover;
    if (i < 0) { box.hidden = true; return; }
    box.replaceChildren(R.el("div", `SIMULATED · ${R.local(d.t_utc[i])} · irradiance ${d.g[i].toFixed(0)} W/m²`, "when"));
    for (const s of d.controllers) {
      const r = R.el("div", null, "r"), k = R.el("i");
      k.style.background = R.css(LOOK[s.key][0]);
      r.append(k, R.el("span", LOOK[s.key][1]), R.el("b", `${s.t_mean[i].toFixed(1)} °C · ${s.key === "none" ? "reference" :
        (s.gain_wh[i] >= 0 ? "+" : "") + s.gain_wh[i].toFixed(0) + " Wh"} · pump ${s.pump_wh[i].toFixed(0)} Wh · ${s.water_l[i].toFixed(0)} L`));
      box.append(r);
    }
    box.append(R.el("div", "module temperature · net energy minus no cooling · pump and valve energy · water used; all since the morning", "flags"));
    box.hidden = false;
    const b = box.getBoundingClientRect();
    box.style.left = Math.min(ev.clientX + 16, window.innerWidth - b.width - 8) + "px";
    box.style.top = Math.min(ev.clientY + 16, window.innerHeight - b.height - 8) + "px";
  }
  function onMove(ev) {
    const d = S.data, box = ev.currentTarget.getBoundingClientRect(), pw = box.width - M.L - M.R, t = d.t_utc;
    const at = t[0] + ((ev.clientX - box.left - M.L) / pw) * (t[t.length - 1] - t[0]);
    S.hover = Math.min(t.length - 1, Math.max(0, Math.round((at - t[0]) / d.bucket_s)));
    drawAll(); tip(ev);
  }
  function onLeave() { S.hover = -1; $("tip").hidden = true; drawAll(); }

  const kwh = (wh, digits = 2) => (wh / 1000).toFixed(digits);
  function render() {
    const d = S.data, tags = Object.entries(d.parameter_tags).map(([t, n]) => `${n} ${t}`).join(", ");
    $("cmpNote").textContent = `Simulated day ${d.date}. Weather: ${d.weather}. Parameters: ${tags}. ` +
      "The order of the strategies and the size and sign of every gain depend on placeholders, above all evaporation and pump power: " +
      "no number here says what cooling gains on a real roof.";
    $("cmpCards").replaceChildren(...d.controllers.map((s) => {
      const card = R.el("div", null, "cmpcard"), t = s.totals, dl = document.createElement("dl");
      card.style.borderTopColor = R.css(LOOK[s.key][0]);
      const h = R.el("h3", s.name); h.prepend(R.el("span", "SIM", "sim"));
      const rows = [["Net energy", `${kwh(t.e_net_wh)} kWh`],
        ["against no cooling", s.key === "none" ? "reference" : `${t.gain_wh >= 0 ? "+" : ""}${kwh(t.gain_wh)} kWh (${t.gain_rel >= 0 ? "+" : ""}${(100 * t.gain_rel).toFixed(2)} %)`],
        ["Pump + valves", `${kwh(t.e_pump_wh + t.e_valves_wh)} kWh`], ["Water used", `${t.water_used_l.toFixed(0)} L`],
        ["Mean module temp.", `${t.t_module_mean_c.toFixed(1)} °C`], ["Pump time", `${t.pump_hours.toFixed(2)} h`]];
      for (const [k, v] of rows) dl.append(R.el("dt", k), R.el("dd", v));
      card.append(h, R.el("div", s.note, "why"), dl);
      return card;
    }));
    $("cmpLegend").replaceChildren(...d.controllers.map((s) => {
      const sp = R.el("span"), k = R.el("i", null, s.key === "none" ? "key dashk" : "key");
      k.style.background = R.css(LOOK[s.key][0]); sp.append(k, s.name); return sp;
    }), R.el("span", "grey band: the window the story mode plays"));
    const head = R.el("tr");
    head.append(...["Strategy (simulated)", "DC energy, kWh", "Pump + valves, kWh", "Net energy, kWh", "Net vs no cooling, kWh", "Water used, L", "Water circulated, m³", "Pump time, h", "Mean module T, °C", "Valve openings"]
      .map((c, i) => R.el("th", c, i ? "num" : "")));
    $("cmpTable").tHead.replaceChildren(head);
    $("cmpTable").tBodies[0].replaceChildren(...d.controllers.map((s) => {
      const t = s.totals, tr = R.el("tr");
      tr.append(R.el("td", s.name), ...[kwh(t.e_dc_wh), kwh(t.e_pump_wh + t.e_valves_wh), kwh(t.e_net_wh), (t.gain_wh >= 0 ? "+" : "") + kwh(t.gain_wh),
        t.water_used_l.toFixed(0), (t.water_circulated_l / 1000).toFixed(1), t.pump_hours.toFixed(2), t.t_module_mean_c.toFixed(1), String(t.valve_openings)]
        .map((v) => R.el("td", v, "num")));
      return tr;
    }));
    drawAll();
  }
  async function init() {
    try {
      const d = await R.getJson("/api/sim/compare");
      if (!d || d.available === false) { $("cmpNote").textContent = (d && d.hint) || "No comparison data."; $("cmpCharts").hidden = true; return; }
      S.data = d;
    } catch (e) { $("cmpNote").textContent = "The comparison could not be loaded."; $("cmpCharts").hidden = true; return; }
    for (const [id] of CHARTS) { $(id).addEventListener("mousemove", onMove); $(id).addEventListener("mouseleave", onLeave); }
    window.addEventListener("resize", drawAll);
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", render);
    render();
  }
  window.roofCompare = { get info() { return { ready: !!S.data, n: S.data ? S.data.controllers.length : 0, keys: S.data ? S.data.controllers.map((c) => c.key) : [] }; } };
  init();
})();
