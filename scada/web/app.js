"use strict";
// HMI of the PV rig SCADA. Plain JavaScript, no library, works without internet.
// It only reads /api/* and posts alarm acknowledgements. All text that comes from the
// data is put into the page with textContent.

const $ = (id) => document.getElementById(id);
const TZ_S = 3 * 3600;                       // Makkah = UTC+3, no daylight saving
const RANGES = [["1 h", 3600], ["3 h", 10800], ["12 h", 43200], ["24 h", 86400], ["7 d", 604800]];
const M = { L: 104, R: 14, T: 8, B: 22 };    // plot margins, the same in every chart so the time axes line up
const S = { span: 43200, follow: true, end: null, data: null, state: null, model: null,
            hover: -1, tab: "open", key: "", alarmKey: "" };

const LINES = {
  cTemp: { series: [["T_module", "--series-1", "Module"], ["T_ambient", "--series-2", "Ambient"]],
           zero: false, alarms: true, unit: "degC", digits: 1 },
  cIrr: { series: [["irradiance", "--series-4", "Irradiance"]], zero: true, unit: "W/m²", digits: 0 },
  cPow: { series: [["power", "--series-3", "Power"]], zero: true, unit: "W", digits: 1 },
};
const LANES = [
  { label: "Load mode", kind: "mode" },
  { label: "NASIM-GBT", kind: "alarm", name: "NASIM" },
  { label: "Threshold rule", kind: "alarm", name: "THRESHOLD" },
  { label: "CUSUM", kind: "alarm", name: "CUSUM" },
  { label: "Marker button", kind: "marker" },
  { label: "RTC", kind: "status", name: "RTC_INVALID", color: "--critical" },
  { label: "T1..T5", kind: "status", name: "TEMP_FAIL", color: "--critical" },
  { label: "Ambient", kind: "status", name: "AMBIENT_FAIL", color: "--critical" },
  { label: "Irradiance", kind: "status", name: "IRRAD_FAIL", color: "--critical" },
  { label: "Panel V/I", kind: "status", name: "PANEL_FAIL", color: "--critical" },
  { label: "SD card", kind: "status", name: "SD_RETRY", color: "--critical" },
  { label: "T mapping", kind: "status", name: "TROM_UNMAPPED", color: "--warning" },
  { label: "Detectors fed", kind: "status", name: "DET_SKIPPED", color: "--warning" },
  { label: "Detector reset", kind: "status", name: "DET_RESET", color: "--warning" },
];
const MODE_COLOR = { off: "--muted", hold: "--series-1", sweep: "--series-3", trip: "--critical" };
const STATE_TEXT = {
  ACTIVE_UNACKED: "● Active, not acknowledged", ACTIVE_ACKED: "● Active, acknowledged",
  CLEARED_UNACKED: "○ Cleared, not acknowledged", CLEARED_ACKED: "Cleared, acknowledged",
};

// ---- small helpers ---------------------------------------------------------------------
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const pad = (n) => String(n).padStart(2, "0");
function clock(t, offset, withDate) {
  if (t === null || t === undefined) return "–";
  const d = new Date((t + offset) * 1000);
  const hms = `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`;
  return withDate ? `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${hms}` : hms;
}
const local = (t, withDate = true) => clock(t, TZ_S, withDate);
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
const bits = (value, table) =>
  value === null || value === undefined ? [] : Object.keys(table).filter((n) => (value >> table[n]) & 1);

// ---- charts ------------------------------------------------------------------------------
function prepare(canvas) {
  if (!canvas.dataset.h) { canvas.dataset.h = canvas.getAttribute("height"); canvas.style.height = canvas.dataset.h + "px"; }
  const dpr = window.devicePixelRatio || 1, w = canvas.clientWidth, h = +canvas.dataset.h;
  canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
  const g = canvas.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, w, h);
  g.font = "11px system-ui, 'Segoe UI', sans-serif";
  return { g, w, h, pw: w - M.L - M.R, ph: h - M.T - M.B };
}
function xOf(c, t) { const d = S.data; return M.L + ((t - (d.end - d.span)) / d.span) * c.pw; }
function timeAxis(c) {
  const d = S.data, { g } = c, t0 = d.end - d.span;
  const step = [60, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400].find((s) => d.span / s <= 8) || 86400;
  g.fillStyle = css("--muted"); g.strokeStyle = css("--grid"); g.lineWidth = 1; g.textAlign = "center"; g.textBaseline = "top";
  for (let t = Math.ceil((t0 + TZ_S) / step) * step - TZ_S; t <= d.end; t += step) {
    const x = Math.round(xOf(c, t)) + 0.5, loc = new Date((t + TZ_S) * 1000);
    g.beginPath(); g.moveTo(x, M.T); g.lineTo(x, M.T + c.ph); g.stroke();
    const midnight = loc.getUTCHours() === 0 && loc.getUTCMinutes() === 0;
    g.fillText(step >= 86400 || midnight ? `${pad(loc.getUTCMonth() + 1)}-${pad(loc.getUTCDate())}`
      : `${pad(loc.getUTCHours())}:${pad(loc.getUTCMinutes())}`, x, M.T + c.ph + 6);
  }
  g.strokeStyle = css("--axis");
  g.beginPath(); g.moveTo(M.L, M.T + c.ph + 0.5); g.lineTo(M.L + c.pw, M.T + c.ph + 0.5); g.stroke();
}
function niceStep(range, count) {
  const raw = range / count, mag = Math.pow(10, Math.floor(Math.log10(raw))), f = raw / mag;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * mag;
}
function crosshair(c) {
  if (S.hover < 0) return null;
  const x = Math.round(xOf(c, S.data.t[S.hover])) + 0.5;
  c.g.strokeStyle = css("--muted"); c.g.lineWidth = 1;
  c.g.beginPath(); c.g.moveTo(x, M.T); c.g.lineTo(x, M.T + c.ph); c.g.stroke();
  return x;
}
function emptyNote(c) {
  c.g.fillStyle = css("--muted"); c.g.textAlign = "center"; c.g.textBaseline = "middle";
  c.g.fillText("no rows in this range", M.L + c.pw / 2, M.T + c.ph / 2);
}

function drawLines(id) {
  const cfg = LINES[id], c = prepare($(id)), { g } = c, d = S.data;
  if (!d || !d.newest) return;
  let lo = Infinity, hi = -Infinity;
  for (const [key] of cfg.series) for (const v of d[key]) if (v !== null) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
  if (lo === Infinity) { lo = 0; hi = 1; }
  if (cfg.zero) lo = Math.min(lo, 0);
  if (hi - lo < 1e-9) hi = lo + 1;
  const step = niceStep(hi - lo, 4);
  lo = Math.floor(lo / step) * step; hi = Math.ceil(hi / step) * step;
  const y = (v) => M.T + c.ph - ((v - lo) / (hi - lo)) * c.ph;
  timeAxis(c);
  g.textAlign = "right"; g.textBaseline = "middle";
  for (let v = lo; v <= hi + step / 2; v += step) {
    const yy = Math.round(y(v)) + 0.5;
    g.strokeStyle = css("--grid"); g.beginPath(); g.moveTo(M.L, yy); g.lineTo(M.L + c.pw, yy); g.stroke();
    g.fillStyle = css("--muted"); g.fillText(String(+v.toFixed(6)), M.L - 8, yy);
  }
  if (!d.t.length) return emptyNote(c);
  if (cfg.alarms) {                            // a tick at the top edge for every detector alarm
    g.fillStyle = css("--detector");
    d.alarm.forEach((a, i) => { if (a) g.fillRect(Math.round(xOf(c, d.t[i])) - 1, M.T, 2, 10); });
  }
  const gap = 2.5 * d.bucket_s;                // a hole in the data breaks the line
  for (const [key, color] of cfg.series) {
    g.strokeStyle = css(color); g.lineWidth = 2; g.lineJoin = "round"; g.beginPath();
    let pen = false;
    d[key].forEach((v, i) => {
      if (v === null || (i > 0 && d.t[i] - d.t[i - 1] > gap)) pen = false;
      if (v === null) return;
      if (pen) g.lineTo(xOf(c, d.t[i]), y(v)); else g.moveTo(xOf(c, d.t[i]), y(v));
      pen = true;
    });
    g.stroke();
  }
  const x = crosshair(c);
  if (x !== null) for (const [key, color] of cfg.series) {
    const v = d[key][S.hover];
    if (v === null) continue;
    g.fillStyle = css("--surface"); g.beginPath(); g.arc(x, y(v), 6, 0, 6.3); g.fill();
    g.fillStyle = css(color); g.beginPath(); g.arc(x, y(v), 4, 0, 6.3); g.fill();
  }
}

function drawState() {
  const c = prepare($("cState")), { g } = c, d = S.data, m = S.model;
  if (!d || !d.newest || !m) return;
  const lane = c.ph / LANES.length, bar = Math.min(12, lane - 6);
  timeAxis(c);
  const w = Math.max(2, (d.bucket_s / d.span) * c.pw + 0.5);
  LANES.forEach((ln, k) => {
    const y0 = M.T + k * lane + (lane - bar) / 2;
    g.fillStyle = css("--ink2"); g.textAlign = "right"; g.textBaseline = "middle";
    g.fillText(ln.label, M.L - 8, y0 + bar / 2);
    g.strokeStyle = css("--grid"); g.lineWidth = 1;
    g.beginPath(); g.moveTo(M.L, Math.round(y0 + bar) + 0.5); g.lineTo(M.L + c.pw, Math.round(y0 + bar) + 0.5); g.stroke();
    if (ln.kind === "marker") {
      g.fillStyle = css("--ink");
      for (const mk of d.markers) g.fillRect(Math.round(xOf(c, mk.t_utc_ms / 1000)) - 1, y0, 2, bar);
      return;
    }
    d.t.forEach((t, i) => {
      let color = null;
      if (ln.kind === "mode") {
        const mode = d.load_mode[i];
        color = mode === null ? "--unknown" : MODE_COLOR[mode] || null;
      } else if (ln.kind === "alarm") {
        const a = d.alarm[i];
        color = a === null ? "--unknown" : (a >> m.alarm_bits[ln.name]) & 1 ? "--detector" : null;
      } else {
        const s = d.status[i];
        color = s === null ? "--unknown" : (s >> m.status_bits[ln.name]) & 1 ? ln.color : null;
      }
      if (color) { g.fillStyle = css(color); g.fillRect(xOf(c, t), y0, w, bar); }
    });
  });
  if (!d.t.length) emptyNote(c);
  crosshair(c);
}
function drawAll() { Object.keys(LINES).forEach(drawLines); drawState(); }

function showTip(ev) {
  const d = S.data, tip = $("tip"), i = S.hover, m = S.model;
  if (i < 0 || !m) { tip.hidden = true; return; }
  tip.replaceChildren(el("div", `${local(d.t[i])} local · ${clock(d.t[i], 0, false)} UTC` +
    (d.bucket_s > 10 ? ` · mean of ${d.bucket_s} s` : ""), "when"));
  const rows = [["Module", d.T_module[i], 2, "degC", "--series-1"], ["Ambient", d.T_ambient[i], 2, "degC", "--series-2"],
    ["Irradiance", d.irradiance[i], 0, "W/m²", "--series-4"], ["Power", d.power[i], 2, "W", "--series-3"],
    ["NASIM p", d.p[i], 3, "", null]];
  for (const [name, v, digits, unit, color] of rows) {
    const r = el("div", null, "r"), key = el("i");
    key.style.background = color ? css(color) : "transparent";
    r.append(key, el("span", name), el("b", v === null ? "–" : `${v.toFixed(digits)} ${unit}`));
    tip.append(r);
  }
  const alarmsOn = bits(d.alarm[i], m.alarm_bits), flags = bits(d.status[i], m.status_bits);
  tip.append(el("div", `load: ${d.load_mode[i] ?? "not recorded"} · detector alarms: ${alarmsOn.join(", ") || "none"}`, "flags"));
  tip.append(el("div", d.status[i] === null ? "status: not recorded (old file format)" : `status: ${flags.join(", ") || "0 (nothing to report)"}`, "flags"));
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
  let lo = 0, hi = d.t.length - 1;                     // nearest row to the pointer's time
  while (lo < hi) { const mid = (lo + hi) >> 1; if (d.t[mid] < t) lo = mid + 1; else hi = mid; }
  if (lo > 0 && t - d.t[lo - 1] < d.t[lo] - t) lo -= 1;
  S.hover = lo; drawAll(); showTip(ev);
}
function onLeave() { S.hover = -1; $("tip").hidden = true; drawAll(); }

// ---- newest row: tiles, header, health ---------------------------------------------------
function tile(name, value, unit, sub) {
  const t = el("div", null, "tile"), v = el("div", value, "value");
  if (unit) v.append(el("small", unit));
  t.append(el("div", name, "name"), v, el("div", sub || "", "sub"));
  return t;
}
function renderState() {
  const st = S.state, r = st.latest, replay = st.mode === "replay";
  $("mode").textContent = replay ? "REPLAY" : "LIVE";
  $("mode").className = "badge" + (replay ? " replay" : "");
  $("replayBanner").hidden = !replay;
  $("replayBanner").textContent = replay ? `REPLAY of ${st.replay_note || "recorded files"} at ${st.speed}x: not live measurements.` : "";
  $("tLocal").textContent = r ? local(r.t_utc) : "no data";
  $("tUtc").textContent = r ? `· ${clock(r.t_utc, 0, true)} UTC` : "";
  const age = st.last_row_wall === null ? null : st.server_wall - st.last_row_wall;
  $("age").textContent = age === null ? "no rows received yet" : age < 90 ? `last row received ${Math.round(age)} s ago`
    : `no new rows for ${age < 5400 ? Math.round(age / 60) + " min" : Math.round(age / 3600) + " h"}`;
  $("age").className = "pill" + (age !== null && age > 30 ? " warn" : "");
  const a = st.alarms;
  $("alarmBadge").textContent = `${a.unacked} unacknowledged · ${a.active} active`;
  $("alarmBadge").className = "pill" + (a.unacked ? (a.top_priority === 1 ? " bad" : " warn") : "");
  document.title = (a.unacked ? `(${a.unacked}) ` : "") + "PV rig SCADA";

  const tiles = $("tiles");
  if (!r) { tiles.replaceChildren(tile("Historian", "empty", "", "waiting for logger rows")); }
  else {
    const m = S.model, temps = m ? m.back_sensors.map((s) => r[s]).filter((v) => v !== null) : [];
    const mode = r.load_mode, flags = r.status_names;
    tiles.replaceChildren(
      tile("Module temperature", num(r.T_module, 1), "degC",
        temps.length ? `T1..T5: ${Math.min(...temps).toFixed(1)} to ${Math.max(...temps).toFixed(1)} (${temps.length} of 5)` : "no back-sheet value"),
      tile("Ambient", num(r.T_ambient, 1), "degC", r.humidity === null ? "" : `humidity ${r.humidity.toFixed(0)} %RH`),
      tile("Module − ambient", num(r.dT_module_ambient, 1), "K", ""),
      tile("Irradiance", num(r.irradiance, 0), "W/m²", "in the module plane"),
      tile("Power", num(r.power, 1), "W", `${num(r.voltage, 2)} V · ${num(r.current, 2)} A`),
      tile("Load mode", mode ?? "not recorded", "", mode === null ? "old file format" : r.D === null ? (m ? m.load_modes[mode] || "" : "") : `duty D = ${r.D.toFixed(3)}`),
      tile("NASIM-GBT p", num(r.p, 3), "", r.alarm_names.length ? `ALARM: ${r.alarm_names.join(", ")}` : "no detector alarm at this row"),
      tile("Row status", r.status === null ? "not recorded" : flags.length ? `${flags.length} flag${flags.length > 1 ? "s" : ""}` : "OK", "",
        r.status === null ? "old file format" : flags.join(", ") || `time from ${r.time_source}`),
    );
  }
  const list = $("health");
  list.replaceChildren(...st.health.map((h) => {
    const li = el("li");
    li.append(el("span", h.state === "ok" ? "OK" : h.state === "unknown" ? "unknown" : h.state === "warn" ? "warning" : "FAULT", `st ${h.state}`), el("span", h.label));
    if (h.detail) li.append(el("span", h.detail, "d"));
    return li;
  }));
  const n = st.counts;
  $("counts").textContent = `Historian: ${n.samples} rows, ${n.markers} markers. Rows still without a usable time: ${n.unplaced_rows}. Lines that were not rows: ${n.bad_lines}.`;
}

// ---- alarms --------------------------------------------------------------------------------
function operator() { return $("operator").value.trim(); }
async function ack(id) {
  if (!operator()) { $("alarmMsg").textContent = "Type the operator's name before acknowledging."; $("operator").focus(); return; }
  try { localStorage.setItem("scada.operator", operator()); } catch (e) { /* private window: not kept */ }
  const r = await fetch("/api/ack", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id, by: operator() }) });
  $("alarmMsg").textContent = r.ok ? "" : `Acknowledge failed: ${(await r.json()).error}`;
  S.alarmKey = ""; await renderAlarms();
}
async function renderAlarms() {
  const journal = S.tab === "journal";
  const rows = await getJson(journal ? "/api/journal" : `/api/alarms?which=${S.tab}`);
  const key = S.tab + JSON.stringify(rows);
  if (key === S.alarmKey) return;                      // nothing changed: keep the table (and focus)
  S.alarmKey = key;
  const head = el("tr"), body = [];
  const cols = journal ? ["Data time (local)", "Event", "Alarm", "Detail", "PC time (local)"]
    : ["State", "Prio", "Raised (local)", "Alarm", "Cleared", "Rows", "Acknowledged"];
  head.append(...cols.map((c) => el("th", c)));
  for (const a of rows) {
    const tr = el("tr");
    if (journal) {
      tr.append(el("td", local(a.t_data)), el("td", a.event), el("td", `${a.code}: ${a.text}`, "wrap"),
        el("td", a.detail, "wrap"), el("td", local(Math.round(a.t_wall))));
    } else {
      if (a.acked_wall === null) tr.className = "unacked";
      const what = el("td", null, "wrap");
      what.append(el("div", a.text), el("div", `${a.code}${a.detail ? " · " + a.detail : ""}`, "d"));
      const ackCell = el("td");
      if (a.acked_wall === null) {
        const b = el("button", "Acknowledge"); b.type = "button"; b.onclick = () => ack(a.id); ackCell.append(b);
      } else ackCell.textContent = `${a.acked_by} · ${local(Math.round(a.acked_wall))}`;
      const prio = el("td");
      prio.append(el("span", `P${a.priority}`, `prio p${a.priority}`));
      tr.append(el("td", STATE_TEXT[a.state]), prio, el("td", a.t_raised === null ? `no data time (PC ${local(Math.round(a.raised_wall))})` : local(a.t_raised)),
        what, el("td", a.cleared ? local(a.t_cleared) : "–"), el("td", String(a.n_rows)), ackCell);
    }
    body.push(tr);
  }
  if (!rows.length) { const td = el("td", journal ? "No alarm events yet." : S.tab === "open" ? "No active or unacknowledged alarms." : "No alarms yet."); td.colSpan = cols.length; const tr = el("tr"); tr.append(td); body.push(tr); }
  $("alarmTable").tHead.replaceChildren(head);
  $("alarmTable").tBodies[0].replaceChildren(...body);
}

// ---- table view (every charted value is also readable here) ------------------------------
function renderRows() {
  if ($("tableView").hidden || !S.data || !S.model) return;
  const d = S.data, m = S.model, head = el("tr"), body = [];
  head.append(...["Local time", "Module degC", "Ambient degC", "Irradiance W/m²", "Power W", "V", "A", "NASIM p", "Detector alarms", "Load", "Status"].map((c) => el("th", c)));
  for (let i = d.t.length - 1; i >= Math.max(0, d.t.length - 300); i--) {
    const tr = el("tr");
    tr.append(el("td", local(d.t[i])), el("td", num(d.T_module[i], 2)), el("td", num(d.T_ambient[i], 2)),
      el("td", num(d.irradiance[i], 0)), el("td", num(d.power[i], 2)), el("td", num(d.voltage[i], 3)), el("td", num(d.current[i], 3)),
      el("td", num(d.p[i], 3)), el("td", bits(d.alarm[i], m.alarm_bits).join(", ") || "–"), el("td", d.load_mode[i] ?? "not recorded"),
      el("td", d.status[i] === null ? "not recorded" : bits(d.status[i], m.status_bits).join(", ") || "0"));
    body.push(tr);
  }
  $("rowTable").tHead.replaceChildren(head);
  $("rowTable").tBodies[0].replaceChildren(...body);
}

// ---- refresh loop ----------------------------------------------------------------------------
async function tick() {
  try {
    if (!S.model) S.model = await getJson("/api/model");
    S.state = await getJson("/api/state");
    renderState();
    const newest = S.state.latest ? S.state.latest.t_utc : 0;
    const key = `${S.follow ? newest : S.end}|${S.span}`;
    if (key !== S.key) {
      const data = await getJson(`/api/series?span=${S.span}` + (S.follow || S.end === null ? "" : `&end=${S.end}`));
      const at = S.hover >= 0 && S.data ? S.data.t[S.hover] : null;   // keep the crosshair on its row
      S.data = data; S.key = key;
      S.hover = at === null ? -1 : S.data.t.indexOf(at);
      if (S.hover < 0) $("tip").hidden = true;
      drawAll(); renderRows();
    }
    await renderAlarms();
  } catch (e) {
    $("age").textContent = "SCADA server not reachable"; $("age").className = "pill bad";
  } finally { setTimeout(tick, 1000); }
}

function init() {
  for (const [label, span] of RANGES) {
    const b = el("button", label, span === S.span ? "on" : ""); b.type = "button";
    b.onclick = () => { S.span = span; [...$("ranges").children].forEach((x) => x.classList.toggle("on", x === b)); S.key = ""; };
    $("ranges").append(b);
  }
  $("follow").onchange = () => { S.follow = $("follow").checked; if (S.follow) { $("endAt").value = ""; S.end = null; } S.key = ""; };
  $("endAt").onchange = () => {
    const v = $("endAt").value;                        // typed as Makkah local time
    if (!v) return;
    const [date, time] = v.split("T"), [y, mo, dd] = date.split("-").map(Number), [hh, mi] = time.split(":").map(Number);
    S.end = Date.UTC(y, mo - 1, dd, hh, mi) / 1000 - TZ_S; S.follow = false; $("follow").checked = false; S.key = "";
  };
  $("tableBtn").onclick = () => { $("tableView").hidden = !$("tableView").hidden; $("tableBtn").textContent = $("tableView").hidden ? "Table view" : "Hide table"; renderRows(); };
  for (const b of $("tabs").children) b.onclick = () => { S.tab = b.dataset.tab; [...$("tabs").children].forEach((x) => x.classList.toggle("on", x === b)); S.alarmKey = ""; renderAlarms(); };
  $("ackAll").onclick = () => ack("all");
  try { $("operator").value = localStorage.getItem("scada.operator") || ""; } catch (e) { /* no storage */ }
  for (const id of [...Object.keys(LINES), "cState"]) { $(id).addEventListener("mousemove", onMove); $(id).addEventListener("mouseleave", onLeave); }
  window.addEventListener("resize", drawAll);
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", drawAll);
  tick();
}
init();
