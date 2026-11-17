// Story mode of the rooftop SIMULATION page: a guided demo of about 75 s for presentations.
//
//   cloud shadow crosses the roof -> modules cool -> sun returns -> temperatures climb
//   (thermal spike) -> detector alarm -> valves open, spray -> temperatures drop -> counters
//
// EVERYTHING it shows is SIMULATED: it replays /api/sim/story, a window of one simulated day
// written by twin/showcase.py (every 10 s step of the twin, every module temperature). The
// story invents nothing: chapter boundaries, the numbers in the captions ("facts") and the
// counters all come from that file. Between two 10 s steps the values are interpolated for
// a smooth picture; valves and alarms switch at their own step.
//
// The caption TEXT is in story_captions.json (English and Arabic) -- edit or translate it
// there. A caption may contain {fact} placeholders and <bdi dir="ltr">..</bdi>, nothing else:
// the text is put into the page as text nodes and <bdi> elements, never as HTML.
//
// URL: /rooftop?story=1 opens the story, &autoplay=1 starts it, &lang=ar picks the captions,
// &export=1 hides the buttons and lets scada/export_story.py draw it frame by frame.
const R = window.RoofShared, stage = window.roof3dStage;
const $ = (id) => document.getElementById(id);
const Q = new URLSearchParams(location.search);
const BLEND_S = 1.8;                           // camera move at the start of a chapter, seconds
const S = { data: null, cap: null, lang: Q.get("lang") === "ar" ? "ar" : "en", on: false, playing: false,
  tau: 0, total: 0, chapters: [], free: false, shown: "", counterAt: 0, dirs: [], clearLevel: 0.95,
  loop: Q.get("loop") === "1", error: null, frames: 0 };
const lerp = (a, b, f) => a + (b - a) * f;

function rich(node, text) {
  // text with <bdi dir="ltr">..</bdi> spans -> text nodes and <bdi> elements (no innerHTML)
  node.replaceChildren();
  const re = /<bdi dir="ltr">(.*?)<\/bdi>/g;
  let last = 0, m;
  while ((m = re.exec(text))) {
    if (m.index > last) node.append(text.slice(last, m.index));
    const b = document.createElement("bdi"); b.dir = "ltr"; b.textContent = m[1]; node.append(b);
    last = re.lastIndex;
  }
  if (last < text.length) node.append(text.slice(last));
}
function fill(tpl, facts) {
  return tpl.replace(/\{(\w+)\}/g, (_, k) => {
    const v = facts[k];
    if (v === undefined || v === null) return "–";
    if (Array.isArray(v)) return v.join(", ");
    return k === "net_vs_ref_wh" && v > 0 ? "+" + v : String(v);
  });
}
const T = () => S.cap[S.lang];
function ltr(text) { const b = document.createElement("bdi"); b.dir = "ltr"; b.textContent = text; return b; }

// ---- time -> data ----------------------------------------------------------------------------
function chapterAt(tau) {
  const cs = S.chapters;
  let k = cs.findIndex((c) => tau < c.t0 + c.seconds);
  if (k < 0) k = cs.length - 1;
  return k;
}
function sampleAt(tau) {
  const k = chapterAt(tau), c = S.chapters[k], x = Math.min(1, Math.max(0, (tau - c.t0) / c.seconds));
  return { k, x, idx: c.i0 + (c.i1 - c.i0) * x };
}
const STATE = { available: true, latest: {}, strings: [], modules: null, progress: {} };
function buildState(idx) {
  const d = S.data, n = d.t_utc.length, i = Math.min(n - 1, Math.floor(idx)), j = Math.min(n - 1, i + 1), f = idx - i;
  const L = (a) => lerp(a[i], a[j], f);
  if (!STATE.modules) {
    Object.assign(STATE, { n_strings: d.n_strings, modules_per_string: d.modules_per_string, sensor_idx: d.sensor_idx,
      soiling: d.soiling, wind_factor: d.wind_factor, simulation: true });
    STATE.modules = d.modules[0].map((row) => row.slice());
    STATE.strings = d.modules[0].map((_, z) => ({ string: z + 1 }));
  }
  const a = d.modules[i], b = d.modules[j];
  for (let z = 0; z < a.length; z++) for (let m = 0; m < a[z].length; m++) STATE.modules[z][m] = lerp(a[z][m], b[z][m], f);
  const s = d.strings;
  STATE.strings.forEach((o, z) => {
    o.valve = s.valve[i][z]; o.bypassed = s.bypassed[i][z];
    o.t_zone = lerp(s.t_zone[i][z], s.t_zone[j][z], f); o.t_max = lerp(s.t_max[i][z], s.t_max[j][z], f);
    o.p = lerp(s.p[i][z], s.p[j][z], f); o.v = lerp(s.v[i][z], s.v[j][z], f); o.i = lerp(s.i[i][z], s.i[j][z], f);
  });
  const r = STATE.latest;
  r.t_utc = L(d.t_utc); r.g = L(d.g); r.ta = L(d.ta); r.p_dc = L(d.p_dc); r.p_ref = L(d.p_ref);
  r.p_pump = d.p_pump[i]; r.p_valves = d.p_valves[i]; r.duty = d.duty[i]; r.flow_lpm = d.flow_lpm[i];
  r.p_net = r.p_dc - r.p_pump - r.p_valves; r.t_tank = L(d.t_tank);
  r.water_used_l = L(d.water_used_l); r.water_circulated_l = L(d.water_circulated_l);
  r.g_clear = L(d.g_clear); r.i = i;
  let sum = 0, cnt = 0;
  for (const row of STATE.modules) for (const t of row) { sum += t; cnt++; }
  r.t_mean = sum / cnt;
  const open = [];
  s.alarm_shown[i].forEach((on, z) => { if (on) open.push({ code: `SIM_SPIKE_Z${z + 1}`, cleared: false }); });
  return open;
}

// ---- drawing one moment ----------------------------------------------------------------------
function show(tau, force = false) {
  const { k, x, idx } = sampleAt(tau), c = S.chapters[k], d = S.data;
  const open = buildState(idx), r = STATE.latest;
  const cl = window.roof3d.cloudFromIrradiance(r.g, r.g_clear, S.clearLevel);
  stage.cloud(cl.c, S.dirs[Math.min(d.t_utc.length - 1, Math.ceil(idx))], cl.loss);
  if (!S.free) {
    const prev = k > 0 ? S.chapters[k - 1].view : c.view;
    stage.pose(prev, c.view, k === 0 ? 1 : (tau - c.t0) / BLEND_S, (k % 2 ? -1 : 1) * 0.07 * (x - 0.5));
  }
  window.dispatchEvent(new CustomEvent("roof:state", { detail: { state: STATE, open, light: true } }));
  const key = c.id + "|" + S.lang;
  if (key !== S.shown) {
    S.shown = key;
    stage.look(c.look || "heat");
    const t = (T().chapters[c.id]) || { title: c.id, text: "" }, cap = $("stCap");
    cap.lang = S.lang; cap.dir = T().dir;
    rich($("stTitle"), fill(t.title, d.facts)); rich($("stText"), fill(t.text, d.facts));
  }
  const now = performance.now();
  if (force || now - S.counterAt > 90) { S.counterAt = now; counters(r); }
  $("stProg").childNodes.forEach((seg, n) => { seg.firstChild.style.width = n < k ? "100%" : n > k ? "0" : `${(x * 100).toFixed(1)}%`; });
  S.frames++;
}
function counterRow(name, value, unit, sub) {
  const c = R.el("div", null, "c"), v = R.el("div", null, "v");
  v.append(ltr(value)); if (unit) v.append(Object.assign(document.createElement("small"), { dir: "ltr", textContent: unit }));
  const s = R.el("div", null, "s"); if (sub) s.append(...sub);
  c.append(R.el("div", name, "n"), v, s);
  return c;
}
function counters(r) {
  const u = T().ui, box = $("stCounters"), d = S.data;
  box.lang = S.lang; box.dir = T().dir;
  const head = R.el("div", null, "head"); head.append(R.el("b", u.sim), R.el("span", d.controller));
  head.lastChild.dir = "ltr";
  const pumpW = r.p_pump + r.p_valves, openZ = STATE.strings.filter((s) => s.valve).map((s) => s.string);
  const mini = R.el("div", null, "mini");
  mini.append(R.el("span", u.g), ltr(`${r.g.toFixed(0)} W/m²`), R.el("span", u.tmean), ltr(`${r.t_mean.toFixed(1)} °C`),
    R.el("span", u.valves), ltr(`${openZ.length} / ${STATE.strings.length}`), R.el("span", u.time), ltr(R.local(Math.round(r.t_utc), false)));
  const foot = R.el("div", null, "foot");
  foot.append(u.foot + " ", ltr(Object.entries(d.parameter_tags).map(([tag, n]) => `${n} ${tag}`).join(", ")));
  box.replaceChildren(head,
    counterRow(u.pv, (r.p_dc / 1000).toFixed(2), "kW", [u.ref + " ", ltr(`${(r.p_ref / 1000).toFixed(2)} kW`)]),
    counterRow(u.pump, pumpW.toFixed(0), "W", [u.pumpsub]),
    counterRow(u.net, (r.p_net / 1000).toFixed(2), "kW", [u.netsub]),
    counterRow(u.water, r.water_used_l.toFixed(1), "L", [u.watersub + " ", ltr(`${r.water_circulated_l.toFixed(0)} L`)]),
    mini, foot);
  const m = Math.floor(S.tau / 60), s = Math.floor(S.tau % 60);
  $("stClock").textContent = `${m}:${R.pad(s)} / ${Math.floor(S.total / 60)}:${R.pad(Math.round(S.total % 60))}`;
}

// ---- controls --------------------------------------------------------------------------------
function setPlaying(on) {
  S.playing = on && S.on;
  if (S.playing && S.tau >= S.total - 0.01) S.tau = 0;
  if (S.playing) S.free = false;
  S.clock = 0;
  $("stPlay").textContent = S.playing ? "⏸" : "▶";
  stage.requestRender();
}
function seek(tau, play = S.playing) {
  S.tau = Math.min(S.total, Math.max(0, tau)); S.free = false;
  show(S.tau, true); setPlaying(play); stage.requestRender();
}
function step(dir) {
  const k = chapterAt(S.tau), c = S.chapters[k];
  if (dir > 0) seek(k + 1 < S.chapters.length ? S.chapters[k + 1].t0 : S.total);
  else seek(S.tau - c.t0 > 1.2 || k === 0 ? c.t0 : S.chapters[k - 1].t0);
}
function setLang(lang) {
  S.lang = lang; S.shown = "";
  document.querySelectorAll("#stLang button").forEach((b) => b.classList.toggle("on", b.dataset.lang === lang));
  $("stExit").textContent = T().ui.exit;
  if (S.on) { show(S.tau, true); stage.requestRender(); }
}
function enter(play = false) {
  if (!S.data || S.on) return;
  S.on = true; R.hold = true; S.lookBefore = stage.mode;
  document.body.classList.add("theater"); document.body.classList.toggle("export", stage.export);
  $("stBar").hidden = $("stCounters").hidden = false;
  R.setScale(S.scale[0], S.scale[1]);
  stage.setDriver(DRIVER);
  stage.theater(true);
  S.shown = ""; show(S.tau, true);
  setPlaying(play);
  window.dispatchEvent(new Event("resize"));
}
function exit() {
  if (!S.on) return;
  S.on = S.playing = false; R.hold = false;
  document.body.classList.remove("theater", "export");
  $("stBar").hidden = $("stCounters").hidden = true;
  R.setScale(R.SCALE_LIVE.lo, R.SCALE_LIVE.hi);
  stage.setDriver(null); stage.cloudLive(); stage.look(S.lookBefore); stage.theater(false);
  window.dispatchEvent(new Event("resize"));               // the charts below are drawn again
  if (R.last && R.live) window.dispatchEvent(new CustomEvent("roof:state", { detail: R.last }));
  stage.flyTo("overview", 900);
}
const DRIVER = {
  active: () => S.on && S.playing,
  moving: () => !S.free,
  frame(now) {
    // the story keeps real time however slowly the picture is drawn (a slow computer shows
    // fewer frames, not a slower story); a pause of the tab is not caught up
    S.tau += S.clock ? Math.min(0.5, (now - S.clock) / 1000) : 0;
    S.clock = now;
    if (S.tau >= S.total) { if (S.loop) S.tau = 0; else { S.tau = S.total; show(S.tau, true); setPlaying(false); return !S.free; } }
    show(S.tau);
    return !S.free;                              // true: the camera is the story's
  },
};

async function init() {
  const btn = $("v3story");
  try {
    const [data, cap] = await Promise.all([R.getJson("/api/sim/story"), R.getJson("/story_captions.json")]);
    if (!data || data.available === false || !data.chapters) throw new Error(data && data.hint ? data.hint : "no story data");
    S.data = data; S.cap = cap;
  } catch (e) {
    S.error = String(e.message || e);
    btn.disabled = true; btn.title = "Story mode: " + S.error;
    return;
  }
  const d = S.data;
  let t0 = 0;
  S.chapters = d.chapters.map((c) => { const o = { ...c, t0 }; t0 += c.seconds; return o; });
  S.total = t0;
  // the cloud's edge: arriving while the missing share grows, leaving while it shrinks
  const first = S.chapters[0], rel = [];
  for (let i = first.i0; i <= first.i1; i++) if (d.g_clear[i] > 80) rel.push(d.g[i] / d.g_clear[i]);
  rel.sort((a, b) => a - b);
  S.clearLevel = rel.length ? rel[Math.floor(rel.length / 2)] : 0.95;
  let dir = 1, prev = 0;
  S.dirs = d.g.map((g, i) => {
    const c = window.roof3d.cloudFromIrradiance(g, d.g_clear[i], S.clearLevel).c;
    if (c > prev + 1e-6) dir = 1; else if (c < prev - 1e-6) dir = -1;
    prev = c;
    return dir;
  });
  let lo = Infinity, hi = -Infinity;
  for (const step of d.modules) for (const row of step) for (const t of row) { lo = Math.min(lo, t); hi = Math.max(hi, t); }
  S.scale = [Math.floor((lo - 1) / 5) * 5, Math.ceil((hi + 1) / 5) * 5];
  $("stProg").replaceChildren(...S.chapters.map((c) => {
    const seg = R.el("div", null, "seg1"); seg.append(document.createElement("i"));
    seg.title = c.id; seg.onclick = () => seek(c.t0);
    return seg;
  }));
  btn.onclick = () => enter(true);
  $("stPlay").onclick = () => setPlaying(!S.playing);
  $("stPrev").onclick = () => step(-1);
  $("stNext").onclick = () => step(1);
  $("stExit").onclick = exit;
  document.querySelectorAll("#stLang button").forEach((b) => { b.onclick = () => setLang(b.dataset.lang); });
  setLang(S.lang);
  stage.onUserCamera(() => { if (S.on) S.free = true; });      // the presenter takes the camera
  document.addEventListener("keydown", (e) => {
    if (!S.on || e.target.tagName === "INPUT") return;
    if (e.key === " ") { e.preventDefault(); setPlaying(!S.playing); }
    else if (e.key === "ArrowRight") step(1);
    else if (e.key === "ArrowLeft") step(-1);
    else if (e.key === "Escape") exit();
  });
  const start = () => {
    if (window.roof3d.info.error) return true;                 // no WebGL: nothing to play on
    if (!window.roof3d.info.ready) return false;
    if (Q.get("story") === "1") enter(Q.get("autoplay") === "1" && !stage.export);
    else if (R.live === false) {                               // no running simulation: the roof shows
      R.setScale(S.scale[0], S.scale[1]); show(0, true); stage.requestRender();   // the story's first moment
    }
    return Q.get("story") === "1" || R.live !== null;
  };
  const wait = setInterval(() => { if (start()) clearInterval(wait); }, 150);
  S.ready = true;
}

// For the page tests and the video export (scada/export_story.py).
window.roofStory = {
  get info() {
    return { ready: !!S.ready && !!S.data, error: S.error, on: S.on, playing: S.playing, tau: S.tau, total: S.total, lang: S.lang,
      chapter: S.on ? S.chapters[chapterAt(S.tau)].id : null, chapters: S.chapters.map((c) => ({ id: c.id, t0: c.t0, seconds: c.seconds, view: c.view })),
      free: S.free, frames: S.frames, scale: S.scale, sample: STATE.latest.i ?? null,
      label: $("v3label").textContent, facts: S.data ? S.data.facts : null };
  },
  enter, exit, play: () => setPlaying(true), pause: () => setPlaying(false), seek, step, setLang,
  renderAt(tau, dt) {                           // export: this moment, drawn now
    if (!S.on) enter(false);
    S.tau = Math.min(S.total, Math.max(0, tau)); S.free = false;
    show(S.tau, true);
    stage.drawFrame(dt);
  },
};
init();
