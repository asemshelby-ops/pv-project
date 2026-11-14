"use strict";
// Rooftop twin page. EVERYTHING shown here is a SIMULATION (twin/, placeholder parameters).
// It reads /api/sim/* only: the simulation's own file, never the historian of the real
// module. Plain JavaScript, no library. Text from the server goes in with textContent.
// The 3D view (rooftop3d.js) gets each state from here and uses the same colour scale.

const $ = (id) => document.getElementById(id);
const TZ_S = 3 * 3600;                       // Makkah = UTC+3
const RANGES = [["1 h", 3600], ["3 h", 10800], ["12 h", 43200], ["24 h", 86400]];
const M = { L: 72, R: 14, T: 8, B: 22 };     // the same margins in every chart: time axes line up
const SCALE = { lo: 20, hi: 70 };            // degC, fixed so that a colour always means the same
// one hue, light to dark (sequential): the module temperature
const RAMP = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5",
              "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"];
const S = { span: 10800, data: null, state: null, hover: -1, key: "", tab: "open", alarmKey: "", open: [] };

const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const dark = () => window.matchMedia("(prefers-color-scheme: dark)").matches;
const pad = (n) => String(n).padStart(2, "0");
function local(t, withDate = true) {
  if (t === null || t === undefined) return "–";
  const d = new Date((t + TZ_S) * 1000);
  const hms = `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`;
  return withDate ? `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${hms}` : hms;
}
const num = (v, digits) => (v === null || v === undefined ? "–" : v.toFixed(digits));
function el(tag, text, cls) {
  const e = document.createElement(tag);
  if (text !== undefined && text !== null) e.textContent = text;
  if (cls) e.className = cls;
  return e;
}
async function getJson(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.json();
}
function tempColor(t) {
  // light = cool on a light page; on a dark page the ramp is reversed so that cool recedes
  const x = Math.min(1, Math.max(0, (t - SCALE.lo) / (SCALE.hi - SCALE.lo)));
  const i = Math.round(x * (RAMP.length - 1));
  return RAMP[dark() ? RAMP.length - 1 - i : i];
}

// ---- tiles and the overview ----------------------------------------------------------------
function tile(name, value, unit, sub) {
  const t = el("div", null, "tile"), v = el("div", value, "value");
  if (unit) v.append(el("small", unit));
  t.append(el("div", name, "name"), v, el("div", sub || "", "sub"));
  return t;
}
function renderState() {
  const st = S.state, r = st.latest;
  $("simBanner").textContent = st.label + " The only real data source of this SCADA is the module page.";
  $("ctrl").textContent = "controller: " + (st.controller || "–");
  $("ctrl").title = "Spike alarms come from: " + (st.alarm_detector || "–");
  $("tSim").textContent = r ? local(r.t_utc) : "no data";
  const prog = st.progress || {};
  const age = r ? st.server_wall - r.ingested_wall : null;
  $("age").textContent = prog.finished ? "simulation finished" : age === null ? "starting" :
    `day ${prog.day} of ${st.days} · ${st.speed || "?"}x`;
  const a = st.alarms;
  $("alarmBadge").textContent = `${a.unacked} unacknowledged · ${a.active} active`;
  $("alarmBadge").className = "pill" + (a.unacked ? " warn" : "");
  const tags = st.parameter_tags || {};
  $("setup").textContent = `${st.n_strings} strings × ${st.modules_per_string} modules, simulated. ` +
    `Weather: ${st.weather}. Spike alarms: ${st.alarm_detector}. Parameters: ${Object.entries(tags).map(([k, v]) => `${v} ${k}`).join(", ")}.`;
  if (!r) { $("tiles").replaceChildren(tile("Simulation", "starting", "", "no step stored yet")); return; }
  const gain = r.e_ref_wh > 0 ? 100 * (r.e_net_wh / r.e_ref_wh - 1) : null;
  $("tiles").replaceChildren(
    tile("Irradiance", num(r.g, 0), "W/m²", `ambient ${num(r.ta, 1)} degC · RH ${num(r.rh, 0)} %`),
    tile("String power", num(r.p_dc / 1000, 2), "kW", `without cooling: ${num(r.p_ref / 1000, 2)} kW`),
    tile("Pump and valves", num(r.p_pump + r.p_valves, 0), "W", r.duty > 0 ? `pump duty ${num(r.duty, 2)} · ${num(r.flow_lpm, 0)} L/min` : "pump off"),
    tile("Net power", num(r.p_net / 1000, 2), "kW", "strings − pump − valves"),
    tile("Net energy today", num(r.e_net_wh / 1000, 2), "kWh", gain === null ? "" : `${gain >= 0 ? "+" : ""}${gain.toFixed(2)} % against no cooling`),
    tile("Water used today", num(r.water_used_l, 0), "L", `circulated ${num(r.water_circulated_l / 1000, 1)} m³`),
    tile("Pump time today", num(r.pump_s / 3600, 2), "h", ""),
    tile("Spray water", num(r.t_tank, 1), "degC", `tank; warm above ${st.tank_warm_c} degC`),
  );
  renderRoof();
}
function renderRoof() {
  const st = S.state, mods = st.modules, n = st.modules_per_string;
  const head = el("tr");
  head.append(...["String / zone", "Valve", `Modules 1 to ${n}`, "Sensors, degC", "Hottest, degC", "Power, kW", "V", "A", "State"]
    .map((c, k) => el("th", c, k >= 3 && k <= 7 ? "num" : "")));
  const rows = st.strings.map((s) => {
    const z = s.string - 1, tr = el("tr");
    const cells = el("div", null, "cells" + (s.valve ? " spray" : ""));
    cells.style.setProperty("--n", n);
    (mods ? mods[z] : []).forEach((t, m) => {
      const c = el("div", null, "cell" + (st.sensor_idx[z].includes(m) ? " sensor" : ""));
      c.style.background = tempColor(t);
      c.title = `SIMULATED string ${s.string}, module ${m + 1}: ${t.toFixed(1)} degC · soiling factor ${st.soiling[z][m].toFixed(3)} · wind exposure ${st.wind_factor[z][m].toFixed(2)}`;
      cells.append(c);
    });
    const cellTd = el("td"); cellTd.append(cells);
    const valve = el("td"); valve.append(el("span", s.valve ? "OPEN" : "closed", "chip " + (s.valve ? "open" : "closed")));
    const state = el("td");
    const mine = S.open.filter((a) => !a.cleared && a.code.endsWith("_Z" + s.string));
    if (!mine.length) state.append(el("span", "no alarm", "chip closed"));
    for (const a of mine) state.append(el("span", a.code.replace("SIM_", "").replace(/_Z\d+$/, "").toLowerCase(), "chip " + (a.code.includes("SPIKE") ? "alarm" : "fault")));
    tr.append(el("td", `String ${s.string} · zone ${s.string}`), valve, cellTd, el("td", num(s.t_zone, 1), "num"),
      el("td", num(s.t_max, 1), "num"), el("td", num(s.p / 1000, 3), "num"), el("td", num(s.v, 0), "num"), el("td", num(s.i, 2), "num"), state);
    return tr;
  });
  $("roof").tHead.replaceChildren(head);
  $("roof").tBodies[0].replaceChildren(...rows);
  // every scale bar of the page (table and 3D view) shows the same scale
  document.querySelectorAll(".scaleLo").forEach((e) => { e.textContent = SCALE.lo; });
  document.querySelectorAll(".scaleHi").forEach((e) => { e.textContent = SCALE.hi; });
  const ramp = dark() ? [...RAMP].reverse() : RAMP;
  document.querySelectorAll(".scalebar").forEach((e) => { e.style.background = `linear-gradient(90deg, ${ramp.join(",")})`; });
}

// ---- charts ----------------------------------------------------------------------------------
function prepare(canvas) {
  if (!canvas.dataset.h) { canvas.dataset.h = canvas.getAttribute("height"); canvas.style.height = canvas.dataset.h + "px"; }
  const dpr = window.devicePixelRatio || 1, w = canvas.clientWidth, h = +canvas.dataset.h;
  canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
  const g = canvas.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, w, h);
  g.font = "11px system-ui, 'Segoe UI', sans-serif";
  return { g, w, h, pw: w - M.L - M.R, ph: h - M.T - M.B };
}
function xOf(c, t) { const d = S.data; return M.L + ((t - (d.end - d.span)) / d.span) * c.pw; }
function timeAxis(c) {
  const d = S.data, { g } = c, t0 = d.end - d.span;
  const step = [300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200].find((s) => d.span / s <= 8) || 43200;
  g.fillStyle = css("--muted"); g.strokeStyle = css("--grid"); g.lineWidth = 1; g.textAlign = "center"; g.textBaseline = "top";
  for (let t = Math.ceil((t0 + TZ_S) / step) * step - TZ_S; t <= d.end; t += step) {
    const x = Math.round(xOf(c, t)) + 0.5, loc = new Date((t + TZ_S) * 1000);
    g.beginPath(); g.moveTo(x, M.T); g.lineTo(x, M.T + c.ph); g.stroke();
    g.fillText(`${pad(loc.getUTCHours())}:${pad(loc.getUTCMinutes())}`, x, M.T + c.ph + 6);
  }
  g.strokeStyle = css("--axis");
  g.beginPath(); g.moveTo(M.L, M.T + c.ph + 0.5); g.lineTo(M.L + c.pw, M.T + c.ph + 0.5); g.stroke();
}
function niceStep(range, count) {
  const raw = range / count, mag = Math.pow(10, Math.floor(Math.log10(raw))), f = raw / mag;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * mag;
}
function yAxis(c, values, zero) {
  let lo = Infinity, hi = -Infinity;
  for (const arr of values) for (const v of arr) if (v !== null) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
  if (lo === Infinity) { lo = 0; hi = 1; }
  if (zero) lo = Math.min(lo, 0);
  if (hi - lo < 1e-9) hi = lo + 1;
  const step = niceStep(hi - lo, 4);
  lo = Math.floor(lo / step) * step; hi = Math.ceil(hi / step) * step;
  const y = (v) => M.T + c.ph - ((v - lo) / (hi - lo)) * c.ph, { g } = c;
  g.textAlign = "right"; g.textBaseline = "middle";
  for (let v = lo; v <= hi + step / 2; v += step) {
    const yy = Math.round(y(v)) + 0.5;
    g.strokeStyle = css("--grid"); g.beginPath(); g.moveTo(M.L, yy); g.lineTo(M.L + c.pw, yy); g.stroke();
    g.fillStyle = css("--muted"); g.fillText(String(+v.toFixed(6)), M.L - 8, yy);
  }
  return y;
}
function line(c, y, t, values, color, dash) {
  const { g } = c, gap = 2.5 * S.data.bucket_s;
  g.strokeStyle = css(color); g.lineWidth = 2; g.lineJoin = "round"; g.setLineDash(dash || []); g.beginPath();
  let pen = false;
  values.forEach((v, i) => {
    if (v === null || (i > 0 && t[i] - t[i - 1] > gap)) pen = false;   // a night breaks the line
    if (v === null) return;
    if (pen) g.lineTo(xOf(c, t[i]), y(v)); else g.moveTo(xOf(c, t[i]), y(v));
    pen = true;
  });
  g.stroke(); g.setLineDash([]);
}
function crosshair(c, y, marks) {
  if (S.hover < 0) return;
  const { g } = c, x = Math.round(xOf(c, S.data.t[S.hover])) + 0.5;
  g.strokeStyle = css("--muted"); g.lineWidth = 1;
  g.beginPath(); g.moveTo(x, M.T); g.lineTo(x, M.T + c.ph); g.stroke();
  for (const [v, color] of marks || []) {
    if (v === null || v === undefined) continue;
    g.fillStyle = css("--surface"); g.beginPath(); g.arc(x, y(v), 6, 0, 6.3); g.fill();
    g.fillStyle = css(color); g.beginPath(); g.arc(x, y(v), 4, 0, 6.3); g.fill();
  }
}
function empty(c) {
  c.g.fillStyle = css("--muted"); c.g.textAlign = "center"; c.g.textBaseline = "middle";
  c.g.fillText("no simulated steps in this range", M.L + c.pw / 2, M.T + c.ph / 2);
}
function drawTemp() {
  const c = prepare($("cTemp")), d = S.data, { g } = c;
  if (!d || !d.newest) return;
  timeAxis(c);
  const y = yAxis(c, [d.t_min, d.t_max, d.t_tank], false);
  if (!d.t.length) return empty(c);
  const gap = 2.5 * d.bucket_s;                 // band: coolest to hottest string
  g.fillStyle = css("--series-1"); g.globalAlpha = 0.22;
  let start = 0;
  for (let i = 1; i <= d.t.length; i++) {
    if (i < d.t.length && d.t[i] - d.t[i - 1] <= gap) continue;
    g.beginPath();
    for (let k = start; k < i; k++) g.lineTo(xOf(c, d.t[k]), y(d.t_max[k]));
    for (let k = i - 1; k >= start; k--) g.lineTo(xOf(c, d.t[k]), y(d.t_min[k]));
    g.closePath(); g.fill(); start = i;
  }
  g.globalAlpha = 1;
  line(c, y, d.t, d.t_tank, "--series-2");
  line(c, y, d.t, d.t_mean, "--series-1");
  crosshair(c, y, S.hover >= 0 ? [[d.t_tank[S.hover], "--series-2"], [d.t_mean[S.hover], "--series-1"]] : []);
}
function drawPow() {
  const c = prepare($("cPow")), d = S.data;
  if (!d || !d.newest) return;
  timeAxis(c);
  const k = (a) => a.map((v) => (v === null ? null : v / 1000));
  const net = k(d.p_net), ref = k(d.p_ref), pump = k(d.p_pump);
  const y = yAxis(c, [net, ref, pump], true);
  if (!d.t.length) return empty(c);
  line(c, y, d.t, ref, "--ink2", [5, 4]);
  line(c, y, d.t, pump, "--series-4");
  line(c, y, d.t, net, "--series-3");
  crosshair(c, y, S.hover >= 0 ? [[pump[S.hover], "--series-4"], [net[S.hover], "--series-3"]] : []);
}
function drawZones() {
  const c = prepare($("cZones")), d = S.data, { g } = c;
  if (!d || !d.newest) return;
  timeAxis(c);
  const n = S.state ? S.state.n_strings : 6, lane = c.ph / n, bar = Math.min(14, lane - 6);
  const w = Math.max(2, (d.bucket_s / d.span) * c.pw + 0.5);
  for (let z = 0; z < n; z++) {
    const y0 = M.T + z * lane + (lane - bar) / 2;
    g.fillStyle = css("--ink2"); g.textAlign = "right"; g.textBaseline = "middle";
    g.fillText(`Zone ${z + 1}`, M.L - 8, y0 + bar / 2);
    g.strokeStyle = css("--grid"); g.lineWidth = 1;
    g.beginPath(); g.moveTo(M.L, Math.round(y0 + bar) + 0.5); g.lineTo(M.L + c.pw, Math.round(y0 + bar) + 0.5); g.stroke();
    d.t.forEach((t, i) => {
      if (d.valve[i][z]) { g.fillStyle = css("--series-1"); g.fillRect(xOf(c, t), y0 + 4, w, bar - 4); }
      if (d.alarm[i][z]) { g.fillStyle = css("--detector"); g.fillRect(Math.round(xOf(c, t)) - 1, y0 - 2, 3, bar + 2); }
    });
  }
  if (!d.t.length) empty(c);
  crosshair(c);
}
function drawAll() { drawTemp(); drawPow(); drawZones(); }

function showTip(ev) {
  const d = S.data, tip = $("tip"), i = S.hover;
  if (i < 0) { tip.hidden = true; return; }
  tip.replaceChildren(el("div", `SIMULATED · ${local(d.t[i])}` + (d.bucket_s > 10 ? ` · mean of ${d.bucket_s} s` : ""), "when"));
  const rows = [["String mean", d.t_mean[i], 1, "degC", "--series-1"], ["Coolest string", d.t_min[i], 1, "degC", null],
    ["Hottest string", d.t_max[i], 1, "degC", null], ["Spray water", d.t_tank[i], 1, "degC", "--series-2"],
    ["Net power", d.p_net[i] === null ? null : d.p_net[i] / 1000, 2, "kW", "--series-3"],
    ["Without cooling", d.p_ref[i] === null ? null : d.p_ref[i] / 1000, 2, "kW", "--ink2"],
    ["Pump", d.p_pump[i], 0, "W", "--series-4"], ["Irradiance", d.g[i], 0, "W/m²", null]];
  for (const [name, v, digits, unit, color] of rows) {
    const r = el("div", null, "r"), key = el("i");
    key.style.background = color ? css(color) : "transparent";
    r.append(key, el("span", name), el("b", v === null ? "–" : `${v.toFixed(digits)} ${unit}`));
    tip.append(r);
  }
  const open = d.valve[i].map((v, z) => (v ? z + 1 : 0)).filter(Boolean), al = d.alarm[i].map((v, z) => (v ? z + 1 : 0)).filter(Boolean);
  tip.append(el("div", `open zones: ${open.join(", ") || "none"} · detector alarms: ${al.length ? "zone " + al.join(", ") : "none"}`, "flags"));
  tip.hidden = false;
  const r = tip.getBoundingClientRect();
  tip.style.left = Math.min(ev.clientX + 16, window.innerWidth - r.width - 8) + "px";
  tip.style.top = Math.min(ev.clientY + 16, window.innerHeight - r.height - 8) + "px";
}
function onMove(ev) {
  const d = S.data;
  if (!d || !d.t.length) return;
  const box = ev.currentTarget.getBoundingClientRect(), pw = box.width - M.L - M.R;
  const t = d.end - d.span + ((ev.clientX - box.left - M.L) / pw) * d.span;
  let lo = 0, hi = d.t.length - 1;
  while (lo < hi) { const mid = (lo + hi) >> 1; if (d.t[mid] < t) lo = mid + 1; else hi = mid; }
  if (lo > 0 && t - d.t[lo - 1] < d.t[lo] - t) lo -= 1;
  S.hover = lo; drawAll(); showTip(ev);
}
function onLeave() { S.hover = -1; $("tip").hidden = true; drawAll(); }

// ---- alarms ------------------------------------------------------------------------------------
const STATE_TEXT = { ACTIVE_UNACKED: "● Active, not acknowledged", ACTIVE_ACKED: "● Active, acknowledged",
  CLEARED_UNACKED: "○ Cleared, not acknowledged", CLEARED_ACKED: "Cleared, acknowledged" };
function operator() { return $("operator").value.trim(); }
async function ack(id) {
  if (!operator()) { $("alarmMsg").textContent = "Type the operator's name before acknowledging."; $("operator").focus(); return; }
  try { localStorage.setItem("scada.operator", operator()); } catch (e) { /* not kept */ }
  const r = await fetch("/api/sim/ack", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id, by: operator() }) });
  if (!r.ok) $("alarmMsg").textContent = `Acknowledge failed: ${(await r.json()).error}`;
  S.alarmKey = ""; await renderAlarms();
}
async function renderAlarms() {
  S.open = await getJson("/api/sim/alarms?which=open");
  const journal = S.tab === "journal";
  const rows = journal ? await getJson("/api/sim/journal") : S.tab === "open" ? S.open : await getJson("/api/sim/alarms?which=all");
  const key = S.tab + JSON.stringify(rows);
  if (key === S.alarmKey) return;
  S.alarmKey = key;
  const head = el("tr"), body = [];
  const cols = journal ? ["Simulated time", "Event", "Alarm", "Detail"] : ["State", "Prio", "Raised (simulated time)", "Alarm", "Cleared", "Steps", "Acknowledged"];
  head.append(...cols.map((c) => el("th", c)));
  for (const a of rows) {
    const tr = el("tr");
    if (journal) {
      tr.append(el("td", local(a.t_data)), el("td", a.event), el("td", `${a.code}: ${a.text}`, "wrap"), el("td", a.detail, "wrap"));
    } else {
      if (a.acked_wall === null) tr.className = "unacked";
      const what = el("td", null, "wrap");
      what.append(el("div", a.text), el("div", `${a.code}${a.detail ? " · " + a.detail : ""}`, "d"));
      const ackCell = el("td");
      if (a.acked_wall === null) { const b = el("button", "Acknowledge"); b.type = "button"; b.onclick = () => ack(a.id); ackCell.append(b); }
      else ackCell.textContent = a.acked_by;
      const prio = el("td"); prio.append(el("span", `P${a.priority}`, `prio p${a.priority}`));
      tr.append(el("td", STATE_TEXT[a.state]), prio, el("td", local(a.t_raised)), what, el("td", a.cleared ? local(a.t_cleared) : "–"), el("td", String(a.n_rows)), ackCell);
    }
    body.push(tr);
  }
  if (!rows.length) { const td = el("td", "No simulation alarms."); td.colSpan = cols.length; const tr = el("tr"); tr.append(td); body.push(tr); }
  $("alarmTable").tHead.replaceChildren(head);
  $("alarmTable").tBodies[0].replaceChildren(...body);
}

// ---- refresh loop ------------------------------------------------------------------------------
async function tick() {
  try {
    S.state = await getJson("/api/sim/state");
    const on = S.state.available;
    $("off").hidden = on; $("content").hidden = !on;
    if (!on) { $("offHint").textContent = S.state.hint; return; }
    await renderAlarms();
    renderState();
    RoofShared.last = { state: S.state, open: S.open };
    window.dispatchEvent(new CustomEvent("roof:state", { detail: RoofShared.last }));
    const newest = S.state.latest ? S.state.latest.t_utc : 0, key = `${newest}|${S.span}`;
    if (key !== S.key) {
      const at = S.hover >= 0 && S.data ? S.data.t[S.hover] : null;
      S.data = await getJson(`/api/sim/series?span=${S.span}`); S.key = key;
      S.hover = at === null ? -1 : S.data.t.indexOf(at);
      if (S.hover < 0) $("tip").hidden = true;
      drawAll();
    }
  } catch (e) {
    $("age").textContent = "SCADA server not reachable"; $("age").className = "pill bad";
  } finally { setTimeout(tick, 1000); }
}
// shared with the 3D view (a module script): one colour scale, one way to write values
const RoofShared = { SCALE, tempColor, css, dark, el, num, local, last: null };
window.RoofShared = RoofShared;

function init() {
  for (const [label, span] of RANGES) {
    const b = el("button", label, span === S.span ? "on" : ""); b.type = "button";
    b.onclick = () => { S.span = span; [...$("ranges").children].forEach((x) => x.classList.toggle("on", x === b)); S.key = ""; };
    $("ranges").append(b);
  }
  for (const b of $("tabs").children) b.onclick = () => { S.tab = b.dataset.tab; [...$("tabs").children].forEach((x) => x.classList.toggle("on", x === b)); S.alarmKey = ""; renderAlarms(); };
  $("ackAll").onclick = () => ack("all");
  try { $("operator").value = localStorage.getItem("scada.operator") || ""; } catch (e) { /* no storage */ }
  for (const id of ["cTemp", "cPow", "cZones"]) { $(id).addEventListener("mousemove", onMove); $(id).addEventListener("mouseleave", onLeave); }
  window.addEventListener("resize", drawAll);
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { drawAll(); if (S.state && S.state.latest) renderRoof(); });
  tick();
}
init();
