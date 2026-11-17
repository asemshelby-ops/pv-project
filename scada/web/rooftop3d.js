// 3D view of the rooftop SIMULATION (page /rooftop). Everything on the rooftop is SIMULATED
// (twin/, placeholder parameters); its values come from rooftop.js, which reads /api/sim/*.
//
// One object is NOT simulated: our real module, drawn apart on its own stand on the ground
// and labelled as the only measured one. Only that object reads /api/state (the real
// module's newest row, read-only). Its values are never mixed with the simulation, and no
// simulated value is ever drawn on it.
//
// What is drawing only, not data: the look of the modules (an illustrative 4 x 9 cell grid:
// our module's cell count is NOT known), the roof, its parapet, AC units and access door, the
// sky, and the sun, placed for Makkah at the SIMULATED time of the newest step. Nothing here
// changes the simulation or its values.
//
// Two module looks: "heat" (every module in the shared colour scale of the page, exact, no
// lighting on the colour) and "realistic" (cells, frame, glass reflections; temperatures
// then come from a click and from the table).
//
// Fast on a normal laptop: the 120 modules are instanced, textures are drawn once on canvases
// (offline, no image files), a frame is drawn only when something changes or animates, and
// nothing is drawn off screen. Quality has three tiers (pixel ratio, shadow map, droplets);
// "auto" measures the frame time and steps down when it is slow.
// Three.js 0.160.0 is vendored in vendor/three-0.160.0/ (the SCADA must work offline).
//
// Drawing aids added for presentations (none of them is data of the twin):
//   * cloud shadow: the twin applies ONE irradiance to all modules. The view turns the
//     drop of that irradiance below clear sky into a shadow whose edge slides over the roof
//     while the logged irradiance ramps (a few lines in the lit materials' shaders, no
//     extra geometry). Heat-map faces keep their exact colour: colour there is temperature.
//   * sun path: the sun's arc over Makkah for the simulated day, with hour marks.
//   * spray jets from the pipe on the medium and high tiers.
// window.roof3dStage lets the story mode (rooftop_story.js) feed states, set the camera
// and draw single frames for the video export; the live page does not use it.
import * as THREE from "three";
import { OrbitControls } from "/vendor/three-0.160.0/OrbitControls.js";

const R = window.RoofShared;                  // shared with rooftop.js: one colour scale
const $ = (id) => document.getElementById(id);
const deg = Math.PI / 180;
const params = new URLSearchParams(location.search);

// Display layout, NOT a plant design [ASSUMPTION]: modules of our rig's size (1200 x 540 mm)
// in portrait, tilt 20 deg facing south, one row per string. +x = east, +z = south, y = up.
const G = { w: 0.54, l: 1.2, t: 0.035, gap: 0.03, tilt: 20 * deg, pitch: 2.6, clear: 0.35, bldgH: 3.6 };
const SITE = { lat: 21.4225, lon: 39.8262 };  // Makkah: only to place the drawn sun
// New effects cost nothing on "low": jets = spray lines per nozzle (0 = none).
const TIERS = {
  low: { pr: 1, shadow: 0, drops: 110, mist: 10, jets: 0 },
  medium: { pr: 1.25, shadow: 1024, drops: 240, mist: 18, jets: 2 },
  high: { pr: 2, shadow: 2048, drops: 420, mist: 30, jets: 4 },
};
const JETS_MAX = TIERS.high.jets, SUN_PATH_R = 17;   // m: radius of the drawn sun path
const EXPORT = params.get("export") === "1";         // frames are drawn one by one (video)
const ORDER = ["low", "medium", "high"];
const DROPS_MAX = TIERS.high.drops, MIST_MAX = TIERS.high.mist;
// Frame-time limits. "Slow" = a median frame above 45 ms (below about 22 frames/s): a steady
// 30 frames/s (33 ms; also what Chrome's energy saver caps animation to) is smooth enough
// here and must not push the view down to the lowest tier.
const SLOW_MS = 45, SLOW_CPU_MS = 22, FAST_MS = 20, FAST_CPU_MS = 7;

function stored(key, fallback, allowed) {
  let v = params.get(key);
  if (!v) { try { v = localStorage.getItem("scada.3d." + key); } catch (e) { v = null; } }
  return allowed.includes(v) ? v : fallback;
}
function store(key, v) { try { localStorage.setItem("scada.3d." + key, v); } catch (e) { /* not kept */ } }

const V = {                                   // the view's state; window.roof3d reads it (tests)
  ready: false, error: null, renders: 0, updates: 0, tUtc: null, dims: "",
  state: null, open: [], real: null, realError: false, sel: null, visible: true,
  mode: stored("mode", "heat", ["heat", "realistic"]),
  choice: stored("quality", "auto", ["auto", ...ORDER]),
  tier: "medium", frameMs: null, cpuMs: null, decided: false,
  sun: { el: 60, az: 180 }, envKey: "", tween: null, intro: false, introPlayed: false, view: "overview",
  cloud: { c: 0, target: 0, dir: 1, loss: 0, direct: false }, pathKey: "", sunPath: stored("sunpath", "on", ["on", "off"]) === "on",
  driver: null, infoAt: 0,
};
let renderer, scene, camera, controls, roof, realObj, raycaster, pickables = [];
let sky, skyMat, envScene, pmrem, envRT, sunLight, hemi, sunPath;
// cloud shadow, shared by every lit material: the leading edge a and the trailing edge b
// (0..1 across the roof along the cloud's travel), how dark, the travel axis, soft noise
const CLOUD = { a: { value: -0.3 }, b: { value: -100 }, dark: { value: 0 }, axis: { value: new THREE.Vector4(1, 0, 0.05, 0.5) }, noise: { value: null } };
const THEME = {};                              // colours read from the page's CSS, cached
let pending = false, insideFrame = false, chained = false, dirty = true, lastFrame = 0, lastRender = 0;
const perf = { intervals: [], cpu: [] };
const TEX = {};

// ---- small helpers ---------------------------------------------------------------------------
function mat(kind, cssVar, extra = {}) {
  const m = kind === "basic" ? new THREE.MeshBasicMaterial(extra) : new THREE.MeshStandardMaterial(extra);
  m.userData.cssVar = cssVar;
  if (cssVar) m.color.set(R.css(cssVar));
  return m;
}
function box(w, h, d, material, x, y, z, shadows = true) {
  const b = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), material);
  b.position.set(x, y, z);
  b.castShadow = b.receiveShadow = shadows;
  return b;
}
function pipe(a, b, material, r = 0.035) {    // a cylinder from point a to point b
  const va = new THREE.Vector3(...a), vb = new THREE.Vector3(...b), len = va.distanceTo(vb);
  const p = new THREE.Mesh(new THREE.CylinderGeometry(r, r, len, 10, 1), material);
  p.position.copy(va).add(vb).multiplyScalar(0.5);
  p.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), vb.clone().sub(va).normalize());
  p.castShadow = true;
  return p;
}
const pct = (a, q) => { if (!a.length) return null; const s = [...a].sort((x, y) => x - y); return s[Math.min(s.length - 1, Math.floor(q * s.length))]; };
const median = (a) => pct(a, 0.5);
const ease = (x) => (x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2);
const mix = (a, b, t) => a + (b - a) * t;
const clamp = (x, a, b) => Math.min(b, Math.max(a, x));

// ---- textures, drawn once on canvases (no image files, works offline) ------------------------
function canvasTex(w, h, draw, srgb = true) {
  const cv = document.createElement("canvas"); cv.width = w; cv.height = h;
  draw(cv.getContext("2d"), w, h);
  const t = new THREE.CanvasTexture(cv);
  if (srgb) t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = Math.min(8, renderer.capabilities.getMaxAnisotropy());
  return t;
}
function rng(seed) { let s = seed >>> 0; return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296); }
function makeTextures() {
  // module face: an ILLUSTRATIVE 4 x 9 grid of mono cells with busbars; not our module's count
  TEX.cells = canvasTex(256, 568, (g, w, h) => {
    g.fillStyle = "#dfe3e8"; g.fillRect(0, 0, w, h);                     // backsheet between cells
    const cols = 4, rows = 9, m = 7, gap = 3, cw = (w - 2 * m - (cols - 1) * gap) / cols, ch = (h - 2 * m - (rows - 1) * gap) / rows;
    const r = rng(7);
    for (let i = 0; i < cols; i++) for (let j = 0; j < rows; j++) {
      const x = m + i * (cw + gap), y = m + j * (ch + gap), c = 6;      // cut corners of mono cells
      const gr = g.createLinearGradient(x, y, x + cw, y + ch);
      const k = 0.9 + 0.2 * r();
      gr.addColorStop(0, `rgb(${18 * k},${36 * k},${72 * k})`); gr.addColorStop(1, `rgb(${26 * k},${50 * k},${96 * k})`);
      g.fillStyle = gr;
      g.beginPath(); g.moveTo(x + c, y); g.lineTo(x + cw - c, y); g.lineTo(x + cw, y + c); g.lineTo(x + cw, y + ch - c);
      g.lineTo(x + cw - c, y + ch); g.lineTo(x + c, y + ch); g.lineTo(x, y + ch - c); g.lineTo(x, y + c); g.closePath(); g.fill();
      g.strokeStyle = "rgba(70,100,150,.35)"; g.lineWidth = 0.6;               // fingers
      for (let f = y + 3; f < y + ch; f += 3.2) { g.beginPath(); g.moveTo(x + 1, f); g.lineTo(x + cw - 1, f); g.stroke(); }
      g.strokeStyle = "#b9c1cc"; g.lineWidth = 1.4;                             // busbars
      for (let b = 1; b <= 3; b++) { const bx = x + (cw * b) / 4; g.beginPath(); g.moveTo(bx, y); g.lineTo(bx, y + ch); g.stroke(); }
    }
  });
  TEX.concrete = canvasTex(512, 512, (g, w, h) => {
    g.fillStyle = "#b8b3a8"; g.fillRect(0, 0, w, h);
    const r = rng(11);
    for (let k = 0; k < 22000; k++) {                                      // aggregate speckle
      const v = 150 + 70 * r(); g.fillStyle = `rgba(${v},${v - 4},${v - 10},${0.25 + 0.4 * r()})`;
      g.fillRect(r() * w, r() * h, 1 + 2 * r(), 1 + 2 * r());
    }
    for (let k = 0; k < 14; k++) {                                         // stains
      const x = r() * w, y = r() * h, s = 30 + 90 * r(), gr = g.createRadialGradient(x, y, 0, x, y, s);
      gr.addColorStop(0, `rgba(90,85,75,${0.06 + 0.08 * r()})`); gr.addColorStop(1, "rgba(90,85,75,0)");
      g.fillStyle = gr; g.fillRect(x - s, y - s, 2 * s, 2 * s);
    }
    g.strokeStyle = "rgba(70,66,58,.55)"; g.lineWidth = 3; g.strokeRect(1.5, 1.5, w - 3, h - 3);  // slab joints
  });
  TEX.concrete.wrapS = TEX.concrete.wrapT = THREE.RepeatWrapping;
  TEX.sand = canvasTex(256, 256, (g, w, h) => {
    g.fillStyle = "#c8b593"; g.fillRect(0, 0, w, h);
    const r = rng(3);
    for (let k = 0; k < 9000; k++) { const v = 160 + 60 * r(); g.fillStyle = `rgba(${v},${v - 18},${v - 50},.35)`; g.fillRect(r() * w, r() * h, 1.5, 1.5); }
  });
  TEX.sand.wrapS = TEX.sand.wrapT = THREE.RepeatWrapping; TEX.sand.repeat.set(40, 40);
  TEX.fan = canvasTex(128, 128, (g, w) => {
    g.fillStyle = "#c9ccd0"; g.fillRect(0, 0, w, w);
    g.fillStyle = "#2b2e33"; g.beginPath(); g.arc(64, 64, 54, 0, 7); g.fill();
    g.strokeStyle = "#8d9299"; g.lineWidth = 2;
    for (let r0 = 12; r0 < 54; r0 += 7) { g.beginPath(); g.arc(64, 64, r0, 0, 7); g.stroke(); }
    for (let a = 0; a < 4; a++) { g.beginPath(); g.moveTo(64, 64); g.lineTo(64 + 54 * Math.cos(a * Math.PI / 2), 64 + 54 * Math.sin(a * Math.PI / 2)); g.stroke(); }
  });
  TEX.louvre = canvasTex(64, 64, (g, w) => {
    g.fillStyle = "#d6d9dd"; g.fillRect(0, 0, w, w);
    g.fillStyle = "#9aa0a8"; for (let y = 4; y < w; y += 8) g.fillRect(4, y, w - 8, 3);
  });
  TEX.drop = canvasTex(64, 64, (g) => {
    const gr = g.createRadialGradient(32, 32, 0, 32, 32, 32);
    gr.addColorStop(0, "rgba(255,255,255,1)"); gr.addColorStop(0.25, "rgba(225,240,255,.85)"); gr.addColorStop(1, "rgba(200,225,255,0)");
    g.fillStyle = gr; g.fillRect(0, 0, 64, 64);
  });
  TEX.cloud = canvasTex(128, 128, (g, w) => {                              // soft blobs, tiles seamlessly
    g.fillStyle = "#808080"; g.fillRect(0, 0, w, w);
    const r = rng(23);
    for (let k = 0; k < 70; k++) {
      const x = r() * w, y = r() * w, rad = 10 + 26 * r(), v = r() < 0.5 ? 255 : 0, al = 0.10 + 0.14 * r();
      for (const dx of [-w, 0, w]) for (const dy of [-w, 0, w]) {
        const gr = g.createRadialGradient(x + dx, y + dy, 0, x + dx, y + dy, rad);
        gr.addColorStop(0, `rgba(${v},${v},${v},${al})`); gr.addColorStop(1, `rgba(${v},${v},${v},0)`);
        g.fillStyle = gr; g.fillRect(x + dx - rad, y + dy - rad, 2 * rad, 2 * rad);
      }
    }
  }, false);
  TEX.cloud.wrapS = TEX.cloud.wrapT = THREE.RepeatWrapping;
  CLOUD.noise.value = TEX.cloud;
  TEX.mist = canvasTex(64, 64, (g) => {
    const gr = g.createRadialGradient(32, 32, 0, 32, 32, 32);
    gr.addColorStop(0, "rgba(255,255,255,.55)"); gr.addColorStop(1, "rgba(255,255,255,0)");
    g.fillStyle = gr; g.fillRect(0, 0, 64, 64);
  });
}

// ---- labels ----------------------------------------------------------------------------------
function label(lines, height = 0.02, strong = false) {
  // A text tag that always faces the camera, at a constant size on screen (`height` = share
  // of the view's height per line), drawn on a canvas with the page's colours.
  const cv = document.createElement("canvas"), tex = new THREE.CanvasTexture(cv);
  const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false, transparent: true, sizeAttenuation: false, toneMapped: false, fog: false }));
  sp.renderOrder = 10;
  sp.userData.label = { lines: null, height, strong, cv, tex };
  setLabel(sp, lines);
  return sp;
}
function setLabel(sp, lines, force = false) {
  const L = sp.userData.label, key = JSON.stringify(lines);
  if (!force && L.lines === key) return;
  L.lines = key;
  const px = 30, padX = 14, padY = 8, lh = px * 1.25, cv = L.cv, g = cv.getContext("2d");
  const font = (k) => `${k === 0 && L.strong ? 700 : k === 0 ? 600 : 400} ${k === 0 ? px : px * 0.8}px system-ui, "Segoe UI", sans-serif`;
  let w = 0;
  lines.forEach((t, k) => { g.font = font(k); w = Math.max(w, g.measureText(t).width); });
  cv.width = Math.ceil(w + 2 * padX); cv.height = Math.ceil(lines.length * lh + 2 * padY);
  // the real module's tag is inverted ink: never the orange of the SIMULATION badge
  g.fillStyle = L.strong ? R.css("--ink") : R.css("--surface");
  g.strokeStyle = L.strong ? R.css("--ink") : R.css("--muted"); g.lineWidth = 3;
  g.beginPath(); g.roundRect(1.5, 1.5, cv.width - 3, cv.height - 3, 10); g.fill(); g.stroke();
  g.textBaseline = "middle";
  lines.forEach((t, k) => {
    g.font = font(k); g.fillStyle = L.strong ? R.css("--surface") : k === 0 ? R.css("--ink") : R.css("--ink2");
    g.fillText(t, padX, padY + lh * (k + 0.5));
  });
  L.tex.dispose();
  L.tex = new THREE.CanvasTexture(cv); L.tex.colorSpace = THREE.SRGBColorSpace;
  sp.material.map = L.tex; sp.material.needsUpdate = true;
  const h = L.height * (cv.height / (px * 1.25));
  sp.scale.set(h * cv.width / cv.height, h, 1);
}

// ---- sky, sun and light ----------------------------------------------------------------------
function sunPosition(tUtc) {
  // Sun elevation and azimuth (deg, azimuth from north, clockwise) at Makkah; low-precision
  // almanac (about 0.5 deg), enough to place the drawn sun and its shadows.
  const d = tUtc / 86400 - 10957.5;                                     // days since J2000.0
  const g = (357.529 + 0.98560028 * d) * deg, q = 280.459 + 0.98564736 * d;
  const L = (q + 1.915 * Math.sin(g) + 0.020 * Math.sin(2 * g)) * deg, e = (23.439 - 3.6e-7 * d) * deg;
  const ra = Math.atan2(Math.cos(e) * Math.sin(L), Math.cos(L)), dec = Math.asin(Math.sin(e) * Math.sin(L));
  const gmst = (18.697374558 + 24.06570982441908 * d) * 15;
  const H = ((gmst + SITE.lon) * deg - ra), lat = SITE.lat * deg;
  const el = Math.asin(Math.sin(lat) * Math.sin(dec) + Math.cos(lat) * Math.cos(dec) * Math.cos(H));
  const az = Math.atan2(-Math.sin(H), Math.tan(dec) * Math.cos(lat) - Math.sin(lat) * Math.cos(H));
  return { el: el / deg, az: ((az / deg) % 360 + 360) % 360 };
}
function sunDir(s) {
  const el = s.el * deg, az = s.az * deg;
  return new THREE.Vector3(Math.sin(az) * Math.cos(el), Math.sin(el), -Math.cos(az) * Math.cos(el));
}
function skyColours(el) {
  const c = (h) => new THREE.Color(h), day = clamp((el - 2) / 14, 0, 1), night = clamp((-el - 1) / 8, 0, 1);
  const top = c("#5d7fb5").lerp(c("#3d7bd0"), day).lerp(c("#0b1430"), night);
  const hor = c("#f0b47a").lerp(c("#d2e4f3"), day).lerp(c("#1c2846"), night);
  const bot = c("#8b8174").lerp(c("#a49b8b"), day).lerp(c("#0f1116"), night);
  return { top, hor, bot, day, night };
}
function buildSky() {
  skyMat = new THREE.ShaderMaterial({
    side: THREE.BackSide, depthWrite: false, fog: false,
    uniforms: { top: { value: new THREE.Color() }, hor: { value: new THREE.Color() }, bot: { value: new THREE.Color() },
      sunDir: { value: new THREE.Vector3(0, 1, 0) }, sunGlow: { value: 1 } },
    vertexShader: `varying vec3 vDir;
      void main() { vDir = normalize(position); gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
    fragmentShader: `uniform vec3 top; uniform vec3 hor; uniform vec3 bot; uniform vec3 sunDir; uniform float sunGlow;
      varying vec3 vDir;
      void main() {
        float h = vDir.y;
        vec3 c = h > 0.0 ? mix(hor, top, pow(h, 0.5)) : mix(hor, bot, pow(-h, 0.35));
        float s = max(dot(normalize(vDir), sunDir), 0.0);
        c += sunGlow * (vec3(1.0, 0.95, 0.85) * pow(s, 900.0) * 3.0 + vec3(1.0, 0.85, 0.6) * pow(s, 10.0) * 0.18);
        gl_FragColor = vec4(c, 1.0);
        #include <tonemapping_fragment>
        #include <colorspace_fragment>
      }`,
  });
  sky = new THREE.Mesh(new THREE.SphereGeometry(200, 32, 16), skyMat);
  sky.frustumCulled = false;
  scene.add(sky);
  envScene = new THREE.Scene();
  envScene.add(new THREE.Mesh(sky.geometry, skyMat));
  pmrem = new THREE.PMREMGenerator(renderer);
}
function applySun() {
  const st = V.state, r = st && st.latest;
  if (r) V.sun = sunPosition(r.t_utc);
  const s = V.sun, dir = sunDir(s), k = skyColours(s.el);
  skyMat.uniforms.top.value.copy(k.top); skyMat.uniforms.hor.value.copy(k.hor); skyMat.uniforms.bot.value.copy(k.bot);
  skyMat.uniforms.sunDir.value.copy(dir); skyMat.uniforms.sunGlow.value = (s.el > -2 ? 1 : 0) * (1 - 0.7 * V.cloud.c);
  // brightness follows the elevation. A cloud does not dim the whole picture any more: the
  // drop of the simulated irradiance below clear sky is drawn as a shadow over the roof
  // (cloudFromIrradiance); only the sky light is taken back a little under it.
  const clear = Math.max(0, Math.sin(s.el * deg));
  if (!V.cloud.direct) {
    const k2 = cloudFromIrradiance(r ? r.g : null, st && st.progress ? st.progress.g_clear : null);
    V.cloud.target = k2.c;
    if (k2.c > 0) V.cloud.loss = k2.loss;            // kept while the trailing edge leaves
  }
  sunLight.intensity = 3.2 * Math.pow(clear, 0.5);
  sunLight.color.setHSL(0.09, clamp(0.9 - k.day * 0.8, 0.05, 0.9), 0.5 + 0.45 * k.day);
  hemi.intensity = 0.35 + 1.15 * k.day * (1 - 0.25 * V.cloud.c);
  hemi.color.copy(k.top).lerp(new THREE.Color("#ffffff"), 0.72); hemi.groundColor.copy(k.bot);
  scene.fog.color.copy(k.hor);
  if (roof) {
    const c = roof.center;
    sunLight.target.position.copy(c);
    sunLight.position.copy(c).addScaledVector(dir, 45);
    if (r) buildSunPath(r.t_utc);
    placeSunMarker();
  }
  const key = `${Math.round(s.el / 4)}|${Math.round(s.az / 20)}`;      // the reflections follow the sky
  if (key !== V.envKey) {
    V.envKey = key;
    const old = envRT;
    envRT = pmrem.fromScene(envScene, 0.02, 0.1, 400);
    scene.environment = envRT.texture;
    if (old) old.dispose();
  }
}

// ---- cloud shadow (drawing of the twin's irradiance drop) ------------------------------------
function cloudPatch(m) {
  // Adds the shadow to a lit material: a world-space mask between the two edges, with a
  // soft, slightly irregular border. One texture read per pixel, no extra geometry.
  if (!m || !m.isMeshStandardMaterial || m.userData.cloud) return;
  m.userData.cloud = true;
  m.onBeforeCompile = (sh) => {
    sh.uniforms.uCloudA = CLOUD.a; sh.uniforms.uCloudB = CLOUD.b; sh.uniforms.uCloudDark = CLOUD.dark;
    sh.uniforms.uCloudAxis = CLOUD.axis; sh.uniforms.uCloudNoise = CLOUD.noise;
    sh.vertexShader = "varying vec2 vCloudXZ;\n" + sh.vertexShader.replace("#include <project_vertex>", `#include <project_vertex>
      vec4 cloudW = vec4(transformed, 1.0);
      #ifdef USE_INSTANCING
        cloudW = instanceMatrix * cloudW;
      #endif
      vCloudXZ = (modelMatrix * cloudW).xz;`);
    sh.fragmentShader = "varying vec2 vCloudXZ; uniform float uCloudA; uniform float uCloudB; uniform float uCloudDark; uniform vec4 uCloudAxis; uniform sampler2D uCloudNoise;\n" +
      sh.fragmentShader.replace("#include <opaque_fragment>", `
      if (uCloudDark > 0.0) {
        float cloudN = texture2D(uCloudNoise, vCloudXZ * 0.023).r;
        float cloudU = dot(vCloudXZ, uCloudAxis.xy) * uCloudAxis.z + uCloudAxis.w + (cloudN - 0.5) * 0.24;
        float cloudM = (1.0 - smoothstep(uCloudA - 0.05, uCloudA + 0.05, cloudU)) * smoothstep(uCloudB - 0.05, uCloudB + 0.05, cloudU);
        outgoingLight *= 1.0 - uCloudDark * cloudM * (0.88 + 0.24 * cloudN);
      }
      #include <opaque_fragment>`);
  };
  m.customProgramCacheKey = () => "cloud-shadow";
  m.needsUpdate = true;
}
function cloudPatchAll(root) {
  root.traverse((o) => { for (const m of Array.isArray(o.material) ? o.material : [o.material]) cloudPatch(m); });
}
function cloudFromIrradiance(g, gClear, clearLevel = 0.95) {
  // share of the clear-sky irradiance that is missing -> how much of the roof the shadow
  // covers (all of it once 30 % is missing) -- a drawing rule, not a model
  if (g === null || g === undefined || !gClear || gClear < 80) return { c: 0, loss: 0 };
  const loss = clamp(1 - g / (gClear * clearLevel), 0, 1);
  return { c: clamp((loss - 0.03) / 0.27, 0, 1), loss };
}
function setCloudUniforms(c, dir, loss) {
  // arriving (dir > 0): the leading edge crosses; leaving: the trailing edge follows it
  // (a large cloud: everything behind its leading edge is shaded until the trailing edge)
  CLOUD.a.value = c <= 0 ? -0.3 : dir >= 0 ? -0.3 + 1.6 * c : 100;
  CLOUD.b.value = c <= 0 || dir >= 0 ? -100 : -0.3 + 1.6 * (1 - c);
  CLOUD.dark.value = c <= 0 ? 0 : Math.min(0.78, 0.5 + 0.35 * loss);   // a clear edge from the start
  if (skyMat) skyMat.uniforms.sunGlow.value = (V.sun.el > -2 ? 1 : 0) * (1 - 0.7 * c);
}
function animateCloud(dt) {
  // live page: steps arrive about once a second; the edge eases to its new place
  const k = V.cloud;
  if (k.direct) return false;
  const d = k.target - k.c;
  if (Math.abs(d) < 0.002) { if (k.c !== k.target) { k.c = k.target; setCloudUniforms(k.c, k.dir, k.loss); } return false; }
  k.dir = d > 0 ? 1 : -1;
  k.c += Math.sign(d) * Math.min(Math.abs(d), dt / 1.4);
  setCloudUniforms(k.c, k.dir, k.loss);
  return true;
}

// ---- sun path of the simulated day ----------------------------------------------------------
function buildSunPath(tUtc) {
  // The sun's arc over Makkah for the local day of tUtc, on a dome of SUN_PATH_R metres
  // around the roof: a line, a dot each full hour, the hour written every three hours.
  const day = Math.floor((tUtc + 3 * 3600) / 86400), key = String(day);
  if (key === V.pathKey || !roof) return;
  V.pathKey = key;
  if (sunPath) { scene.remove(sunPath.group); sunPath.group.traverse((o) => { o.geometry && o.geometry.dispose(); }); }
  const g = new THREE.Group(), t0 = day * 86400 - 3 * 3600, pts = [], hours = [], c = roof.center;
  const at = (s) => c.clone().addScaledVector(sunDir(s), SUN_PATH_R);
  for (let m = 0; m <= 1440; m += 10) {
    const s = sunPosition(t0 + m * 60);
    if (s.el < 0) continue;
    pts.push(at(s));
    if (m % 60 === 0 && s.el > 3) hours.push({ h: m / 60, p: at(s) });
  }
  const col = new THREE.Color("#f2a100");
  const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts),
    new THREE.LineBasicMaterial({ color: col, transparent: true, opacity: 0.85, fog: false, toneMapped: false }));
  const dots = new THREE.InstancedMesh(new THREE.SphereGeometry(0.13, 8, 6), new THREE.MeshBasicMaterial({ color: col, fog: false, toneMapped: false }), Math.max(1, hours.length));
  const mx = new THREE.Matrix4();
  hours.forEach((h, i) => { mx.setPosition(h.p); dots.setMatrixAt(i, mx); });
  dots.count = hours.length;
  g.add(line, dots);
  for (const h of hours) if (h.h % 3 === 0) { const l = label([`${String(h.h).padStart(2, "0")}:00`], 0.015); l.position.copy(h.p).add(new THREE.Vector3(0, 0.7, 0)); g.add(l); }
  const ball = new THREE.Mesh(new THREE.SphereGeometry(0.32, 16, 12), new THREE.MeshBasicMaterial({ color: 0xffd23f, fog: false, toneMapped: false }));
  const ray = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3(0, 1, 0)]),
    new THREE.LineBasicMaterial({ color: col, transparent: true, opacity: 0.35, fog: false, toneMapped: false }));
  const tag = label(["Sun"], 0.017);
  g.add(ball, ray, tag);
  g.visible = V.sunPath;
  scene.add(g);
  sunPath = { group: g, ball, ray, tag };
}
function placeSunMarker() {
  if (!sunPath || !roof) return;
  const up = V.sun.el > 0, p = roof.center.clone().addScaledVector(sunDir(V.sun), SUN_PATH_R);
  sunPath.ball.visible = sunPath.ray.visible = sunPath.tag.visible = up;
  if (!up) return;
  sunPath.ball.position.copy(p);
  sunPath.tag.position.copy(p).add(new THREE.Vector3(0, 1.0, 0));
  const a = sunPath.ray.geometry.attributes.position;
  a.setXYZ(0, p.x, p.y, p.z); a.setXYZ(1, roof.center.x, 0.4, roof.center.z); a.needsUpdate = true;
  sunPath.ray.geometry.computeBoundingSphere();
  if (V.tUtc !== null) { const d = new Date((V.tUtc + 3 * 3600) * 1000); setLabel(sunPath.tag, [`Sun ${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")}`]); }
}

// ---- quality tiers and the frame-time watch -------------------------------------------------
function decideTier(tier, m, allowUp) {
  // m = {median, p90} of the frame interval (ms, between chained animation frames: includes the
  // GPU's time) and cpu = median time of the render call. Step down when frames are slow; step
  // up once, at the first decision, only when nearly all frames are fast.
  const i = ORDER.indexOf(tier);
  const slow = (m.median !== null && m.median > SLOW_MS) || (m.cpu !== null && m.cpu > SLOW_CPU_MS);
  const fast = m.p90 !== null && m.p90 < FAST_MS && m.cpu !== null && m.cpu < FAST_CPU_MS;
  if (slow && i > 0) return ORDER[i - 1];
  if (fast && allowUp && i < ORDER.length - 1) return ORDER[i + 1];
  return tier;
}
// Story mode fills the window: several times the pixels of the normal view. So that "low"
// and "medium" do not get slower there, the drawn pixels are capped (the picture is scaled
// up by the browser); the normal view and the video export are not capped.
const THEATER_PX = { low: 0.5e6, medium: 1.0e6, high: Infinity };
function pixelRatio() {
  const T = TIERS[V.tier], st = $("v3stage"), base = Math.min(window.devicePixelRatio || 1, T.pr);
  if (EXPORT || !document.body.classList.contains("theater") || !st.clientWidth) return base;
  return Math.min(base, Math.sqrt(THEATER_PX[V.tier] / (st.clientWidth * st.clientHeight)));
}
function setTier(t) {
  V.tier = t;
  const T = TIERS[t];
  renderer.setPixelRatio(pixelRatio());
  const on = T.shadow > 0;
  if (renderer.shadowMap.enabled !== on || (on && sunLight.shadow.mapSize.x !== T.shadow)) {
    renderer.shadowMap.enabled = on; sunLight.castShadow = on;
    if (on) sunLight.shadow.mapSize.set(T.shadow, T.shadow);
    if (sunLight.shadow.map) { sunLight.shadow.map.dispose(); sunLight.shadow.map = null; }
    scene.traverse((o) => { if (o.material) o.material.needsUpdate = true; });
  }
  if (roof) roof.strings.forEach((x) => {
    x.drops.geometry.setDrawRange(0, T.drops); x.mist.geometry.setDrawRange(0, T.mist);
    x.jets.geometry.setDrawRange(0, roof.m * T.jets * 2); x.jets.visible = x.open && T.jets > 0;
  });
  perf.intervals.length = perf.cpu.length = 0;
  resize();
  showPerf();
}
function watchPerf() {
  if (V.choice !== "auto") return;
  const iv = perf.intervals, n = iv.length;
  if (n < 30 && perf.cpu.length < 30) return;
  const m = { median: n >= 30 ? median(iv) : null, p90: n >= 90 ? pct(iv, 0.9) : null, cpu: median(perf.cpu) };
  const next = decideTier(V.tier, m, !V.decided);
  if (next !== V.tier) { V.decided = true; setTier(next); return; }
  if (n >= 90) { V.decided = true; perf.intervals.length = 0; perf.cpu.length = 0; }   // judged: keep the tier
}
function showPerf() {
  const el = $("v3perf");
  if (!el) return;
  const iv = median(perf.intervals), cpu = median(perf.cpu);
  const ms = iv !== null && perf.intervals.length >= 10 ? iv : cpu;
  if (ms !== null) V.frameMs = ms;                     // keep the last value after a tier change
  el.textContent = `quality ${V.choice === "auto" ? "auto → " : ""}${V.tier}` +
    (V.frameMs !== null ? ` · ${V.frameMs.toFixed(0)} ms/frame` : "");
}

// ---- the rooftop -----------------------------------------------------------------------------
function moduleTransform(s, m) {
  const o = roof.geo, x = o.x0 + m * (G.w + G.gap), z = o.zRow[s];
  return { x, z, y: G.clear + (G.l / 2) * Math.sin(G.tilt) };
}
function buildRoof(st) {
  if (roof) { scene.remove(roof.group); roof.group.traverse((o) => { o.geometry && o.geometry.dispose(); }); }
  const n = st.n_strings, m = st.modules_per_string, group = new THREE.Group();
  const rowLen = m * G.w + (m - 1) * G.gap, x0 = -rowLen / 2 + G.w / 2;
  const zRow = Array.from({ length: n }, (_, s) => (s - (n - 1) / 2) * G.pitch);   // string 1 = north
  const half = (G.l / 2), yTop = G.clear + 2 * half * Math.sin(G.tilt);
  const zTop = zRow.map((z) => z - half * Math.cos(G.tilt)), zBot = zRow.map((z) => z + half * Math.cos(G.tilt));
  const xW = -rowLen / 2, xE = rowLen / 2, xMan = xE + 1.4, yPipe = yTop + 0.07;
  const zS = zRow[n - 1] + 2.4;                              // pump and tank, south-east corner
  const minX = xW - 2.6, maxX = xMan + 3.6, minZ = zRow[0] - 4.2, maxZ = zS + 1.6;
  const W = maxX - minX, D = maxZ - minZ, cx = (minX + maxX) / 2, cz = (minZ + maxZ) / 2;
  roof = { group, n, m, geo: { x0, zRow, zTop, zBot, yTop, yPipe, xW, xE, xMan, rowLen }, strings: [],
    bounds: { minX, maxX, minZ, maxZ }, center: new THREE.Vector3(cx, 0, cz) };

  // building, roof slab with a concrete texture, parapet with a coping, ground around it
  const conc = TEX.concrete.clone(); conc.needsUpdate = true; conc.repeat.set(W / 3, D / 3);
  const slab = new THREE.Mesh(new THREE.BoxGeometry(W, 0.3, D), new THREE.MeshStandardMaterial({ map: conc, roughness: 0.95 }));
  slab.position.set(cx, -0.15, cz); slab.receiveShadow = true;
  const wallTex = TEX.concrete.clone(); wallTex.needsUpdate = true; wallTex.repeat.set(W / 4, 1);
  const plaster = new THREE.MeshStandardMaterial({ map: wallTex, color: 0xe9dfcc, roughness: 0.9 });
  const body = box(W, G.bldgH - 0.3, D, plaster, cx, -0.3 - (G.bldgH - 0.3) / 2, cz);
  const ground = new THREE.Mesh(new THREE.PlaneGeometry(400, 400), new THREE.MeshStandardMaterial({ map: TEX.sand, roughness: 1 }));
  ground.rotation.x = -Math.PI / 2; ground.position.y = -G.bldgH; ground.receiveShadow = true;
  group.add(slab, body, ground);
  const pH = 0.9, pT = 0.25, cap = new THREE.MeshStandardMaterial({ color: 0xd9d4ca, roughness: 0.8 });
  for (const [w, d, x, z] of [[W, pT, cx, minZ + pT / 2], [W, pT, cx, maxZ - pT / 2],
    [pT, D, minX + pT / 2, cz], [pT, D, maxX - pT / 2, cz]]) {
    group.add(box(w, pH, d, plaster, x, pH / 2, z), box(w + 0.08, 0.06, d + 0.08, cap, x, pH + 0.03, z));
  }
  // roof details: an access stair house with its door, and three AC condensing units
  const sx = minX + 1.7, sz = minZ + 1.45;
  group.add(box(2.6, 2.5, 2.2, plaster, sx, 1.25, sz), box(2.8, 0.12, 2.4, cap, sx, 2.56, sz));
  const door = box(0.95, 2.05, 0.06, new THREE.MeshStandardMaterial({ color: 0x5b4636, roughness: 0.6 }), sx + 0.4, 1.03, sz + 1.12);
  const knob = box(0.05, 0.05, 0.06, new THREE.MeshStandardMaterial({ color: 0xc8c8c8, metalness: 0.9, roughness: 0.3 }), sx + 0.75, 1.0, sz + 1.17, false);
  group.add(door, knob, label(["Roof access"], 0.016)).children.at(-1).position.set(sx, 3.2, sz);
  const acBody = new THREE.MeshStandardMaterial({ color: 0xe4e6e8, roughness: 0.55, metalness: 0.2 });
  const acSide = new THREE.MeshStandardMaterial({ map: TEX.louvre, roughness: 0.6 });
  [2.0, 3.6, 5.2].forEach((dx) => {
    const x = sx + 1.6 + dx, z = minZ + 1.2;
    const unit = new THREE.Mesh(new THREE.BoxGeometry(1.0, 0.75, 0.8), [acSide, acSide, acBody, acBody, acSide, acSide]);
    unit.position.set(x, 0.45, z); unit.castShadow = unit.receiveShadow = true;
    const fan = new THREE.Mesh(new THREE.CircleGeometry(0.33, 24), new THREE.MeshStandardMaterial({ map: TEX.fan, roughness: 0.7 }));
    fan.rotation.x = -Math.PI / 2; fan.position.set(x, 0.83, z);
    group.add(unit, fan, box(1.05, 0.08, 0.85, cap, x, 0.04, z));
  });

  // modules: aluminium frame + a face that is either the heat map or the realistic cells
  const frameMat = new THREE.MeshStandardMaterial({ color: 0xc9cdd2, metalness: 0.85, roughness: 0.32 });
  const faceGeo = new THREE.BoxGeometry(G.w - 0.05, G.t + 0.004, G.l - 0.05);
  const frameM = new THREE.InstancedMesh(new THREE.BoxGeometry(G.w, G.t, G.l), frameMat, n * m);
  const heatM = new THREE.InstancedMesh(faceGeo, new THREE.MeshBasicMaterial({ toneMapped: false }), n * m);
  const realM = new THREE.InstancedMesh(faceGeo, new THREE.MeshStandardMaterial({ map: TEX.cells, metalness: 0.15,
    roughness: 0.16, envMapIntensity: 1.15 }), n * m);
  frameM.castShadow = frameM.receiveShadow = realM.receiveShadow = true;
  const sens = st.sensor_idx.reduce((k, a) => k + a.length, 0);
  const dotM = new THREE.InstancedMesh(new THREE.CylinderGeometry(0.045, 0.045, 0.01, 10), mat("basic", "--ink", { toneMapped: false }), sens);
  const q = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), G.tilt), one = new THREE.Vector3(1, 1, 1);
  const up = new THREE.Vector3(0, 1, 0).applyQuaternion(q), mx = new THREE.Matrix4(), grey = new THREE.Color(R.css("--unknown"));
  let d = 0;
  for (let s = 0; s < n; s++) for (let k = 0; k < m; k++) {
    const p = moduleTransform(s, k), i = s * m + k, pos = new THREE.Vector3(p.x, p.y, p.z);
    mx.compose(pos, q, one); frameM.setMatrixAt(i, mx); heatM.setMatrixAt(i, mx); realM.setMatrixAt(i, mx); heatM.setColorAt(i, grey);
    if (st.sensor_idx[s].includes(k)) { mx.compose(pos.clone().addScaledVector(up, G.t / 2 + 0.008), q, one); dotM.setMatrixAt(d++, mx); }
  }
  frameM.userData.pick = heatM.userData.pick = realM.userData.pick = "module";
  group.add(frameM, heatM, realM, dotM);
  Object.assign(roof, { heat: heatM, real: realM, frames: frameM, dots: dotM, q, up });

  // racks: a front and a back rail on legs under each row (legs in one instanced mesh)
  const alu = new THREE.MeshStandardMaterial({ color: 0xaeb3b9, metalness: 0.7, roughness: 0.4 });
  const nLeg = 6, legs = new THREE.InstancedMesh(new THREE.BoxGeometry(0.05, 1, 0.05), alu, n * nLeg * 2);
  legs.castShadow = true;
  let li = 0;
  zRow.forEach((z, s) => {
    group.add(box(rowLen + 0.2, 0.05, 0.05, alu, 0, G.clear - 0.03, zBot[s] - 0.1), box(rowLen + 0.2, 0.05, 0.05, alu, 0, yTop - 0.1, zTop[s] + 0.1));
    for (let k = 0; k < nLeg; k++) {
      const x = xW + 0.2 + k * (rowLen - 0.4) / (nLeg - 1);
      for (const [h, zz] of [[G.clear - 0.03, zBot[s] - 0.1], [yTop - 0.1, zTop[s] + 0.1]]) {
        mx.compose(new THREE.Vector3(x, h / 2, zz), new THREE.Quaternion(), new THREE.Vector3(1, h, 1)); legs.setMatrixAt(li++, mx);
      }
    }
  });
  group.add(legs);

  // water: tank, pump, a manifold along the east side, one branch, valve and spray pipe per string
  const pipeMat = new THREE.MeshStandardMaterial({ color: 0xd5d9dd, roughness: 0.45 });
  const tank = new THREE.Mesh(new THREE.CylinderGeometry(0.75, 0.75, 1.3, 32), new THREE.MeshStandardMaterial({ color: 0xf1f1ec, roughness: 0.5 }));
  tank.position.set(xMan + 2.2, 0.65, zS); tank.castShadow = tank.receiveShadow = true; tank.userData.pick = "pump";
  const lid = new THREE.Mesh(new THREE.CylinderGeometry(0.3, 0.3, 0.08, 24), new THREE.MeshStandardMaterial({ color: 0x3b6fb6, roughness: 0.5 }));
  lid.position.set(xMan + 2.2, 1.34, zS);
  const pump = box(0.6, 0.4, 0.4, new THREE.MeshStandardMaterial({ color: 0x3a3f46, metalness: 0.5, roughness: 0.45 }), xMan + 0.8, 0.2, zS);
  pump.userData.pick = "pump";
  group.add(tank, lid, pump, pipe([xMan + 1.45, 0.25, zS], [xMan + 1.1, 0.25, zS], pipeMat),
    pipe([xMan + 0.5, 0.12, zS], [xMan, 0.12, zS], pipeMat), pipe([xMan, 0.12, zS], [xMan, 0.12, zTop[0] - 0.1], pipeMat));
  const pumpLabel = label(["Pump and tank", "shared by all zones"], 0.019);
  pumpLabel.position.set(xMan + 1.5, 2.0, zS); pumpLabel.userData.pick = "pump";
  group.add(pumpLabel);
  roof.pumpPos = new THREE.Vector3(xMan + 0.8, 0.4, zS);

  const sheenQ = q.clone().multiply(new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), -Math.PI / 2));
  for (let s = 0; s < n; s++) {
    const zp = zTop[s] - 0.1, items = [];
    items.push(pipe([xMan, 0.12, zp], [xMan, yPipe, zp], pipeMat), pipe([xMan, yPipe, zp], [xW - 0.1, yPipe, zp], pipeMat, 0.03));
    const valveMat = mat("std", "--muted", { roughness: 0.4, metalness: 0.3 });
    const valve = box(0.2, 0.2, 0.2, valveMat, xMan - 0.45, yPipe, zp);
    const handle = box(0.36, 0.05, 0.05, valveMat, 0, 0.16, 0);
    valve.add(handle);
    const lab = label([`String ${s + 1}`], 0.02);
    lab.position.set(xW - 1.9, yTop + 0.4, zRow[s]);
    // spray: droplets that glint (additive, per-droplet sparkle), soft mist, and a wet sheen
    const dg = new THREE.BufferGeometry();
    dg.setAttribute("position", new THREE.BufferAttribute(new Float32Array(DROPS_MAX * 3), 3));
    dg.setAttribute("color", new THREE.BufferAttribute(new Float32Array(DROPS_MAX * 3), 3));
    const drops = new THREE.Points(dg, new THREE.PointsMaterial({ map: TEX.drop, size: 0.1, vertexColors: true, transparent: true,
      depthWrite: false, blending: THREE.AdditiveBlending, toneMapped: false }));
    const mg = new THREE.BufferGeometry();
    mg.setAttribute("position", new THREE.BufferAttribute(new Float32Array(MIST_MAX * 3), 3));
    const mist = new THREE.Points(mg, new THREE.PointsMaterial({ map: TEX.mist, size: 1.1, transparent: true, opacity: 0.3,
      depthWrite: false, color: 0xffffff }));
    const sheen = new THREE.Mesh(new THREE.PlaneGeometry(rowLen + 0.05, G.l - 0.02),
      new THREE.MeshStandardMaterial({ color: 0xe6f2ff, transparent: true, opacity: 0, roughness: 0.03, metalness: 0,
        envMapIntensity: 2.6, depthWrite: false }));
    sheen.quaternion.copy(sheenQ);
    sheen.position.set(0, G.clear + half * Math.sin(G.tilt), zRow[s]).addScaledVector(up, G.t / 2 + 0.012);
    // jets (medium and high tiers): thin lines fanning from a nozzle above each module
    const jv = new Float32Array(m * JETS_MAX * 6), jr = rng(300 + s);
    for (let k = 0; k < m; k++) for (let j = 0; j < JETS_MAX; j++) {
      const x = x0 + k * (G.w + G.gap), f = 0.18 + 0.5 * jr(), o = 6 * (k * JETS_MAX + j);
      jv[o] = x; jv[o + 1] = yPipe; jv[o + 2] = zp;
      jv[o + 3] = x + (jr() - 0.5) * (G.w + 0.1); jv[o + 4] = yTop - f * G.l * Math.sin(G.tilt) + 0.03; jv[o + 5] = zTop[s] + f * G.l * Math.cos(G.tilt);
    }
    const jg = new THREE.BufferGeometry(); jg.setAttribute("position", new THREE.BufferAttribute(jv, 3));
    const jets = new THREE.LineSegments(jg, new THREE.LineBasicMaterial({ color: 0xdff0ff, transparent: true, opacity: 0.4,
      depthWrite: false, blending: THREE.AdditiveBlending, toneMapped: false }));
    jets.frustumCulled = false; jets.visible = false;
    drops.frustumCulled = mist.frustumCulled = false;
    drops.visible = mist.visible = sheen.visible = false;
    const seed = new Float32Array(DROPS_MAX * 4), r = rng(100 + s);
    for (let k = 0; k < DROPS_MAX * 4; k++) seed[k] = r();
    for (const o of [...items, valve, lab]) o.userData.pick = "string", o.userData.s = s;
    group.add(...items, valve, lab, drops, mist, sheen, jets);
    roof.strings.push({ valve, valveMat, handle, lab, drops, mist, sheen, jets, seed, open: false, wet: 0 });
  }

  // a north arrow on the roof
  const arrow = new THREE.Mesh(new THREE.ConeGeometry(0.35, 0.9, 16), new THREE.MeshStandardMaterial({ color: 0x3a3f46 }));
  arrow.rotation.x = -Math.PI / 2; arrow.position.set(maxX - 1.0, 0.25, minZ + 1.0);
  const north = label(["N"], 0.02); north.position.set(maxX - 1.0, 0.9, minZ + 0.3);
  group.add(arrow, north);

  // selection outline, reused for a module, a string or the real module
  const outline = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(1, 1, 1)),
    new THREE.LineBasicMaterial({ color: R.css("--ink"), depthTest: false, toneMapped: false }));
  outline.renderOrder = 9; outline.visible = false; outline.material.userData.cssVar = "--ink";
  group.add(outline);
  roof.outline = outline;

  // the sun's shadow camera covers the building and our real module beside it
  const ext = Math.max(W, D) / 2 + 8, sc = sunLight.shadow.camera;
  sc.left = -ext; sc.right = ext; sc.top = ext; sc.bottom = -ext; sc.near = 1; sc.far = 120; sc.updateProjectionMatrix();

  // the cloud shadow travels from the north-west to the south-east across the roof (drawing)
  const ax = new THREE.Vector2(1, 0.5).normalize(), len = Math.abs(W * ax.x) + Math.abs(D * ax.y);
  CLOUD.axis.value.set(ax.x, ax.y, 1 / len, 0.5 - (cx * ax.x + cz * ax.y) / len);
  V.pathKey = "";
  scene.add(group);
  buildReal();
  cloudPatchAll(group); cloudPatchAll(realObj.group);
  pickables = [heatM, realM, frameM, tank, pump, pumpLabel, ...roof.strings.flatMap((x) => [x.valve, x.lab]),
    ...group.children.filter((o) => o.userData.pick === "string" && o.isMesh), ...realObj.pick];
  V.dims = `${n}x${m}`;
  setTier(V.tier);
  applyMode();
}

// ---- our real module: on its own stand on the ground, the only measured object ---------------
function buildReal() {
  if (realObj) { scene.remove(realObj.group); }
  const g = new THREE.Group(), b = roof.bounds, x = b.minX - 4.5, z = b.maxZ - 2.0, y0 = -G.bldgH;   // south-west, clear of the labels
  const half = G.l / 2, yc = y0 + G.clear + half * Math.sin(G.tilt), q = roof.q;
  const frame = new THREE.Mesh(new THREE.BoxGeometry(G.w, G.t, G.l), roof.frames.material);
  const heatMat = new THREE.MeshBasicMaterial({ color: R.css("--unknown"), toneMapped: false });
  const cell = new THREE.Mesh(new THREE.BoxGeometry(G.w - 0.05, G.t + 0.004, G.l - 0.05), heatMat);
  for (const o of [frame, cell]) { o.position.set(x, yc, z); o.quaternion.copy(q); o.userData.pick = "real"; o.castShadow = true; }
  const steel = new THREE.MeshStandardMaterial({ color: 0x6d7076, metalness: 0.6, roughness: 0.5 });   // mild-steel stand
  g.add(frame, cell, box(0.05, G.clear + 0.05, 0.05, steel, x, y0 + G.clear / 2, z + half * Math.cos(G.tilt) - 0.1),
    box(0.05, yc + half * Math.sin(G.tilt) - 0.1 - y0, 0.05, steel, x, (yc + half * Math.sin(G.tilt) - 0.1 + y0) / 2, z - half * Math.cos(G.tilt) + 0.1));
  const pyr = new THREE.Mesh(new THREE.CylinderGeometry(0.07, 0.07, 0.06, 16), steel);   // PYR20, coplanar
  pyr.position.set(x + G.w / 2 + 0.16, yc, z); pyr.quaternion.copy(q); pyr.userData.pick = "real";
  const tag = label(["Our real module", "the only measured one · not part of the rooftop"], 0.022, true);
  tag.position.set(x, y0 + 2.3, z); tag.userData.pick = "real";
  g.add(pyr, tag);
  scene.add(g);
  realObj = { group: g, cell, heatMat, tag, pick: [frame, cell, pyr, tag], pos: new THREE.Vector3(x, yc, z) };
}

// ---- data → scene ----------------------------------------------------------------------------
function openAlarms(s) {
  return V.open.filter((a) => !a.cleared && a.code.endsWith("_Z" + (s + 1)))
    .map((a) => a.code.replace("SIM_", "").replace(/_Z\d+$/, "").toLowerCase());
}
function applyMode() {
  const heat = V.mode === "heat";
  if (roof) { roof.heat.visible = roof.dots.visible = heat; roof.real.visible = !heat; }   // sensor dots: heat map only
  if (realObj) realObj.cell.material = heat ? realObj.heatMat : roof.real.material;
  document.querySelectorAll("#v3mode button").forEach((b) => b.classList.toggle("on", b.dataset.mode === V.mode));
  const note = $("v3modeNote");
  if (note) note.hidden = heat;
  $("v3stage").classList.toggle("realistic", !heat);     // the colour legend belongs to the heat map
  requestRender();
}
const tmpColor = new THREE.Color();
function applyState(light = false) {
  // light = true: called for every drawn frame of the story mode; the side panel and the
  // frame-time read-out are then refreshed a few times a second only
  const st = V.state;
  if (!st || !st.available || !st.modules) return;
  if (!roof || V.dims !== `${st.n_strings}x${st.modules_per_string}`) buildRoof(st);
  if (!THEME.open) { THEME.open = R.css("--series-1"); THEME.closed = R.css("--muted"); }
  const c = tmpColor;
  st.modules.forEach((row, s) => row.forEach((t, m) => roof.heat.setColorAt(s * roof.m + m, c.set(R.tempColor(t)))));
  roof.heat.instanceColor.needsUpdate = true;
  st.strings.forEach((x) => {
    const z = x.string - 1, o = roof.strings[z], open = !!x.valve;
    if (o.open !== open || !o.painted) {
      o.open = open; o.painted = true;
      o.valveMat.color.set(open ? THEME.open : THEME.closed);
      o.valveMat.emissive.set(open ? THEME.open : "#000000"); o.valveMat.emissiveIntensity = open ? 0.5 : 0;
      o.handle.rotation.y = open ? 0 : Math.PI / 2;      // handle along the pipe = open
    }
    o.drops.visible = o.mist.visible = open;
    o.jets.visible = open && TIERS[V.tier].jets > 0;
    o.sheen.visible = open || o.wet > 0.01;
    const al = openAlarms(z);
    setLabel(o.lab, [`String ${z + 1}` + (open ? " · spraying" : "") + (al.length ? " · ▲ " + al.join(", ") : "")]);
  });
  V.tUtc = st.latest ? st.latest.t_utc : null;
  V.updates++;
  applySun();
  const now = performance.now();
  if (!light || now - V.infoAt > 300) { V.infoAt = now; renderInfo(); showPerf(); }
  if (!light) requestRender();
}
function applyReal() {
  if (!realObj) return;
  const r = V.real && V.real.latest, t = r ? r.T_module : null;
  realObj.heatMat.color.set(t === null || t === undefined ? R.css("--unknown") : R.tempColor(t));
  const mode = V.real ? (V.real.mode === "replay" ? "REPLAY, not live" : "measured") : "not reachable";
  setLabel(realObj.tag, ["Our real module", `the only measured one · ${t === null || t === undefined ? "no row yet" : t.toFixed(1) + " degC, " + mode}`]);
  if (V.sel && V.sel.kind === "real") renderInfo();
  requestRender();
}
async function pollReal() {
  try {
    const r = await fetch("/api/state", { cache: "no-store" });
    if (!r.ok) throw new Error(String(r.status));
    V.real = await r.json(); V.realError = false;
  } catch (e) { V.real = null; V.realError = true; }
  applyReal();
  setTimeout(pollReal, 5000);
}

// ---- spray animation -------------------------------------------------------------------------
let sprayClock = 0;
function animateSpray(dt) {
  if (!roof) return false;
  let busy = false;
  sprayClock += dt;
  const o = roof.geo, half = G.l / 2, T = TIERS[V.tier], up = roof.up;
  const sunK = clamp(sunLight.intensity / 3.2, 0.15, 1), heat = V.mode === "heat";
  roof.strings.forEach((x, s) => {
    // wet sheen: comes up in about 2 s, dries in about 25 s after the valve closes (drawing only)
    const target = x.open ? 1 : 0, rate = x.open ? 0.5 : 0.04;
    x.wet = target > x.wet ? Math.min(1, x.wet + rate * dt) : Math.max(0, x.wet - rate * dt);
    x.sheen.material.opacity = x.wet * (heat ? 0.16 : 0.4);
    x.sheen.visible = x.open || x.wet > 0.01;
    if (x.wet > 0 && x.wet < 1) busy = true;
    if (!x.open) return;
    busy = true;
    const p = x.drops.geometry.attributes.position.array, col = x.drops.geometry.attributes.color.array, sd = x.seed;
    for (let k = 0; k < T.drops; k++) {
      const a = sd[4 * k], b = sd[4 * k + 1], sp = 0.55 + 0.9 * sd[4 * k + 2], tw = sd[4 * k + 3];
      const f = (b + sprayClock * 0.5 * sp) % 1, along = -half + f * G.l;          // down the glass
      const lift = 0.04 + 0.6 * Math.sin(Math.PI * Math.min(1, f * 1.5)) * (1 - f) * (0.6 + 0.6 * tw);
      p[3 * k] = o.xW + a * o.rowLen + 0.04 * Math.sin(30 * tw + sprayClock * 3);
      p[3 * k + 1] = G.clear + half * Math.sin(G.tilt) - along * Math.sin(G.tilt) + lift * up.y;
      p[3 * k + 2] = o.zRow[s] + along * Math.cos(G.tilt) + lift * up.z;
      const glint = sunK * (0.35 + 0.65 * Math.pow(0.5 + 0.5 * Math.sin(sprayClock * (6 + 10 * tw) + 40 * a), 6));
      col[3 * k] = col[3 * k + 1] = glint * 1.1; col[3 * k + 2] = glint * 1.2;
    }
    x.drops.geometry.attributes.position.needsUpdate = x.drops.geometry.attributes.color.needsUpdate = true;
    const mp = x.mist.geometry.attributes.position.array;
    for (let k = 0; k < T.mist; k++) {
      const a = sd[4 * k + 2], f = (sd[4 * k] + sprayClock * 0.08 * (0.5 + a)) % 1;
      mp[3 * k] = o.xW + sd[4 * k + 1] * o.rowLen + 0.3 * Math.sin(sprayClock * 0.7 + 9 * a);
      mp[3 * k + 1] = G.clear + 2 * half * Math.sin(G.tilt) + 0.15 + 0.25 * f - f * half * Math.sin(G.tilt);
      mp[3 * k + 2] = o.zTop[s] + f * G.l * Math.cos(G.tilt) * 0.8;
    }
    x.mist.geometry.attributes.position.needsUpdate = true;
    if (T.jets) x.jets.material.opacity = (0.26 + 0.14 * Math.sin(sprayClock * 17 + s)) * (0.5 + 0.5 * sunK);
  });
  return busy;
}

// ---- camera: intro, presets, tweens ----------------------------------------------------------
function views() {
  const b = roof ? roof.bounds : { minX: -10, maxX: 10, minZ: -8, maxZ: 8 };
  const t = new THREE.Vector3((b.minX + b.maxX) / 2 - 2.5, 0, (b.minZ + b.maxZ) / 2 + 0.5);
  return {
    overview: { target: t, pos: t.clone().add(new THREE.Vector3(5, 16, 20.5)) },
    presentation: { target: t.clone().add(new THREE.Vector3(-1.6, -1.0, 1.5)), pos: t.clone().add(new THREE.Vector3(-4.6, 9.8, 24.5)) },
    intro: { target: t, pos: t.clone().add(new THREE.Vector3(34, 38, -22)) },
    // story mode: the whole roof with its surroundings (cloud shadow, sun path) ...
    wide: { target: t.clone().add(new THREE.Vector3(1.5, 0.5, -1)), pos: t.clone().add(new THREE.Vector3(15, 29, 37)) },
  };
}
function stringView(z) {
  // ... and one string from its valve end: label, valve and spray in the picture
  const o = roof.geo, tgt = new THREE.Vector3(o.xE * 0.3, 0.7, o.zRow[z]);
  return { target: tgt, pos: tgt.clone().add(new THREE.Vector3(9.5, 6.2, 8.5)) };
}
function poseOf(name) {
  if (name && name.startsWith("string:") && roof) return stringView(clamp(+name.slice(7) - 1, 0, roof.n - 1));
  const v = views();
  return v[name] || v.overview;
}
function blendPose(from, to, x, drift = 0) {
  // camera pose between two views (x = 0..1, eased), swung around the target like flyTo;
  // drift (rad) keeps the picture alive inside a chapter
  const e = ease(clamp(x, 0, 1)), a = poseOf(from), b = poseOf(to);
  const tgt = a.target.clone().lerp(b.target, e);
  const s0 = new THREE.Spherical().setFromVector3(a.pos.clone().sub(a.target)), s1 = new THREE.Spherical().setFromVector3(b.pos.clone().sub(b.target));
  let dTheta = s1.theta - s0.theta;
  if (dTheta > Math.PI) dTheta -= 2 * Math.PI; if (dTheta < -Math.PI) dTheta += 2 * Math.PI;
  const sp = new THREE.Spherical(mix(s0.radius, s1.radius, e), mix(s0.phi, s1.phi, e), s0.theta + dTheta * e + drift);
  controls.target.copy(tgt);
  camera.position.copy(tgt).add(new THREE.Vector3().setFromSpherical(sp));
  camera.lookAt(tgt);
  V.tween = null; V.intro = false; V.view = to;
}
function flyTo(name, ms = 1600) {
  const v = views()[name];
  V.view = name;
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduce || ms <= 0) {
    controls.target.copy(v.target); camera.position.copy(v.pos); controls.update();
    V.tween = null; V.intro = false; requestRender(); return;
  }
  // interpolate around the end target in spherical coordinates: a smooth swoop, not a straight cut
  const s0 = new THREE.Spherical().setFromVector3(camera.position.clone().sub(v.target));
  const s1 = new THREE.Spherical().setFromVector3(v.pos.clone().sub(v.target));
  let dTheta = s1.theta - s0.theta;
  if (dTheta > Math.PI) dTheta -= 2 * Math.PI; if (dTheta < -Math.PI) dTheta += 2 * Math.PI;
  V.tween = { t0: performance.now(), ms, s0, s1, dTheta, tgt0: controls.target.clone(), tgt1: v.target.clone() };
  requestRender();
}
function stepTween(now) {
  const w = V.tween;
  if (!w) return false;
  const x = clamp((now - w.t0) / w.ms, 0, 1), e = ease(x);
  const s = new THREE.Spherical(mix(w.s0.radius, w.s1.radius, e), mix(w.s0.phi, w.s1.phi, e), w.s0.theta + w.dTheta * e);
  controls.target.lerpVectors(w.tgt0, w.tgt1, e);
  camera.position.copy(controls.target).add(new THREE.Vector3().setFromSpherical(s));
  camera.lookAt(controls.target);
  if (x >= 1) { V.tween = null; V.intro = false; return false; }
  return true;
}

// ---- rendering on demand ---------------------------------------------------------------------
function requestRender() {
  dirty = true;
  if (!renderer || pending || EXPORT) return;  // export: frames are drawn by drawFrame only
  if (!V.visible) return;                      // off screen: drawn again when it comes back
  pending = true;
  if (insideFrame) chained = true;
  requestAnimationFrame(frame);
}
function animating() {
  return !!V.tween || (!!V.driver && V.driver.active()) || (!V.cloud.direct && Math.abs(V.cloud.target - V.cloud.c) >= 0.002) ||
    (!!roof && roof.strings.some((x) => x.open || (x.wet > 0 && x.wet < 1)));
}
function drawFrame(dt) {
  // one frame, drawn now (video export and tests): everything that animates advances by dt
  if (!renderer) return;
  animateSpray(dt);
  renderer.render(scene, camera);
  V.renders++;
}
function frame(now) {
  // Every animation runs on chained animation frames, so the interval between two of them
  // includes the GPU's work: that is the frame time the quality watch judges. While only the
  // spray moves, a frame is drawn about 30 times a second and the others are skipped.
  pending = false;
  const wasChained = chained; chained = false;
  if (!V.visible || document.hidden) { lastFrame = lastRender = 0; return; }   // drawn again when back
  if (wasChained && lastFrame) { perf.intervals.push(now - lastFrame); if (perf.intervals.length > 120) perf.intervals.shift(); }
  lastFrame = now;
  const anim = animating() && V.visible && !document.hidden;
  const skip = anim && !dirty && !V.tween && !(V.driver && V.driver.active() && V.driver.moving()) && lastRender && now - lastRender < 30;
  if (!skip) {
    const dt = Math.min(0.1, lastRender ? (now - lastRender) / 1000 : 0);
    lastRender = now; dirty = false;
    insideFrame = true;
    const driven = V.driver && V.driver.active() ? V.driver.frame(now, dt) : false;   // story mode
    if (!driven && !stepTween(now)) controls.update();   // may ask for the next frame while damping
    animateCloud(dt);
    animateSpray(dt);
    const t0 = performance.now();
    renderer.render(scene, camera);
    perf.cpu.push(performance.now() - t0); if (perf.cpu.length > 120) perf.cpu.shift();
    V.renders++;
    insideFrame = false;
    watchPerf();
  }
  if (!pending && animating() && V.visible && !document.hidden) { chained = true; pending = true; requestAnimationFrame(frame); }
  else if (!pending) { lastFrame = 0; lastRender = 0; }
}
function resize() {
  const st = $("v3stage"), w = st.clientWidth, h = st.clientHeight;
  if (!w || !h) return;
  renderer.setPixelRatio(pixelRatio());
  renderer.setSize(w, h, false);
  camera.aspect = w / h; camera.updateProjectionMatrix();
  requestRender();
}
function applyTheme() {
  THEME.open = null;
  if (roof) roof.strings.forEach((x) => { x.painted = false; });
  scene.traverse((o) => {
    const m = o.material;
    if (m && m.userData && m.userData.cssVar) m.color.set(R.css(m.userData.cssVar));
    if (o.userData.label) setLabel(o, JSON.parse(o.userData.label.lines), true);
  });
  applyState(); applyReal();
}

// ---- picking and the info panel --------------------------------------------------------------
function pickAt(cx, cy) {
  const rect = renderer.domElement.getBoundingClientRect();
  const ndc = new THREE.Vector2(((cx - rect.left) / rect.width) * 2 - 1, -((cy - rect.top) / rect.height) * 2 + 1);
  raycaster.setFromCamera(ndc, camera);
  const hit = raycaster.intersectObjects(pickables, false).find((h) => h.object.visible);
  if (!hit) return null;
  const o = hit.object, kind = o.userData.pick;
  if (kind === "module") return { kind, s: Math.floor(hit.instanceId / roof.m), m: hit.instanceId % roof.m };
  if (kind === "string") return { kind, s: o.userData.s };
  return { kind };
}
function select(sel) {
  V.sel = sel;
  const ol = roof && roof.outline;
  if (ol) {
    ol.visible = !!sel;
    if (sel && sel.kind === "module") {
      const p = moduleTransform(sel.s, sel.m);
      ol.position.set(p.x, p.y, p.z); ol.quaternion.copy(roof.q); ol.scale.set(G.w + 0.04, 0.12, G.l + 0.04);
    } else if (sel && sel.kind === "string") {
      ol.position.set(0, G.clear + (G.l / 2) * Math.sin(G.tilt), roof.geo.zRow[sel.s]); ol.quaternion.copy(roof.q);
      ol.scale.set(roof.geo.rowLen + 0.1, 0.14, G.l + 0.1);
    } else if (sel && sel.kind === "real") {
      ol.position.copy(realObj.pos); ol.quaternion.copy(roof.q); ol.scale.set(G.w + 0.06, 0.12, G.l + 0.06);
    } else if (sel && sel.kind === "pump") {
      ol.position.set(roof.geo.xMan + 1.5, 0.66, roof.pumpPos.z); ol.quaternion.identity(); ol.scale.set(2.4, 1.4, 1.7);
    }
  }
  renderInfo(); requestRender();
}
function kv(rows) {
  const d = R.el("dl", null, "kv");
  for (const [k, v] of rows) d.append(R.el("dt", k), R.el("dd", v));
  return d;
}
function stringRows(z) {
  const s = V.state.strings[z], al = openAlarms(z);
  return [["Valve", s.valve ? "OPEN: zone sprayed" : "closed"], ["Sensors (mean)", `${R.num(s.t_zone, 1)} degC`],
    ["Hottest module", `${R.num(s.t_max, 1)} degC`], ["Power", `${R.num(s.p / 1000, 3)} kW`],
    ["Voltage · current", `${R.num(s.v, 0)} V · ${R.num(s.i, 2)} A`], ["Bypassed modules", String(s.bypassed ?? 0)],
    ["Open alarms", al.length ? al.join(", ") : "none"]];
}
function renderInfo() {
  const box = $("v3info"), sel = V.sel, st = V.state;
  if (!box) return;
  const out = [];
  if (!sel || !st || !st.strings || !st.strings.length) {
    out.push(R.el("p", "Click a module, a string's label, valve or pipe, the pump, or our real module to see its values.", "note"));
  } else if (sel.kind === "module") {
    const t = st.modules[sel.s][sel.m], sensor = st.sensor_idx[sel.s].includes(sel.m);
    out.push(R.el("div", "SIMULATED", "simtag"), R.el("h3", `String ${sel.s + 1}, module ${sel.m + 1}`),
      kv([["Temperature", `${t.toFixed(1)} degC`], ["Sensor", sensor ? "carries one of the string's five sensors" : "none on this module"],
        ["Soiling factor", st.soiling[sel.s][sel.m].toFixed(3)], ["Wind exposure", st.wind_factor[sel.s][sel.m].toFixed(2)]]),
      R.el("h3", `String ${sel.s + 1} · zone ${sel.s + 1}`), kv(stringRows(sel.s)));
  } else if (sel.kind === "string") {
    const temps = st.modules[sel.s];
    out.push(R.el("div", "SIMULATED", "simtag"), R.el("h3", `String ${sel.s + 1} · zone ${sel.s + 1}`),
      kv([...stringRows(sel.s), ["Modules", `${temps.length}, ${Math.min(...temps).toFixed(1)} to ${Math.max(...temps).toFixed(1)} degC`]]));
  } else if (sel.kind === "pump") {
    const r = st.latest || {};
    out.push(R.el("div", "SIMULATED", "simtag"), R.el("h3", "Pump, valves and tank"),
      kv([["Pump", r.duty > 0 ? `duty ${R.num(r.duty, 2)} · ${R.num(r.flow_lpm, 0)} L/min` : "off"],
        ["Pump and valves", `${R.num((r.p_pump ?? 0) + (r.p_valves ?? 0), 0)} W`],
        ["Open valves", st.strings.filter((x) => x.valve).map((x) => x.string).join(", ") || "none"],
        ["Spray water (tank)", `${R.num(r.t_tank, 1)} degC`], ["Water used today", `${R.num(r.water_used_l, 0)} L`]]));
  } else if (sel.kind === "real") {
    const q = V.real, r = q && q.latest;
    out.push(R.el("div", "MEASURED · our real module", "realtag"), R.el("h3", "Our real module"),
      R.el("p", "The only measured module. It stands apart from the simulated rooftop; none of its values enter the simulation.", "note"));
    if (!q) out.push(R.el("p", "The module's data could not be read.", "note"));
    else if (!r) out.push(R.el("p", "No measured row yet: the module page shows how to connect the logger.", "note"));
    else {
      const age = q.last_row_wall === null ? null : q.server_wall - q.last_row_wall;
      out.push(R.el("p", q.mode === "replay" ? `REPLAY of ${q.replay_note || "recorded files"}: not live measurements.`
        : age === null ? "live" : `live; last row received ${age < 90 ? Math.round(age) + " s" : Math.round(age / 60) + " min"} ago`, "note"),
      kv([["Time", R.local(r.t_utc)], ["Module temperature", `${R.num(r.T_module, 1)} degC (mean of the back sensors)`],
        ["Ambient", `${R.num(r.T_ambient, 1)} degC`], ["Irradiance", `${R.num(r.irradiance, 0)} W/m²`],
        ["Power", `${R.num(r.power, 1)} W`], ["Voltage · current", `${R.num(r.voltage, 2)} V · ${R.num(r.current, 2)} A`]]));
    }
    const a = R.el("a", "Open the module page →"); a.href = "/"; out.push(a);
  }
  box.replaceChildren(...out);
}

// ---- start -----------------------------------------------------------------------------------
function init() {
  const stage = $("v3stage");
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "default", preserveDrawingBuffer: EXPORT });
  } catch (e) {
    V.error = "WebGL is not available in this browser: the 3D view is off. The table below shows the same values.";
    $("v3msg").textContent = V.error; $("v3msg").hidden = false; stage.hidden = true;
    return;
  }
  renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.toneMappingExposure = 0.84;   // noon concrete no longer burns out
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  stage.prepend(renderer.domElement);
  makeTextures();
  scene = new THREE.Scene();
  scene.fog = new THREE.Fog(0xd2e4f3, 70, 190);
  camera = new THREE.PerspectiveCamera(40, 2, 0.1, 500);
  hemi = new THREE.HemisphereLight(0xffffff, 0x8b8174, 1.2); scene.add(hemi);
  sunLight = new THREE.DirectionalLight(0xffffff, 3); scene.add(sunLight, sunLight.target);
  sunLight.shadow.bias = -0.0004; sunLight.shadow.normalBias = 0.03;
  buildSky();
  controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true; controls.dampingFactor = 0.12;
  controls.minDistance = 3; controls.maxDistance = 80; controls.maxPolarAngle = 86 * deg;
  controls.addEventListener("change", requestRender);
  controls.addEventListener("start", () => { V.tween = null; V.intro = false; });   // the user takes over
  raycaster = new THREE.Raycaster();
  const forced = params.get("quality");
  V.tier = V.choice === "auto" ? (forced && ORDER.includes(forced) ? forced : "medium") : V.choice;
  setTier(V.tier);
  applySun();

  let down = null;
  renderer.domElement.addEventListener("pointerdown", (e) => { down = [e.clientX, e.clientY]; });
  renderer.domElement.addEventListener("pointerup", (e) => {
    if (down && Math.hypot(e.clientX - down[0], e.clientY - down[1]) < 5 && roof) select(pickAt(e.clientX, e.clientY));
    down = null;
  });
  let hoverAt = 0;
  renderer.domElement.addEventListener("pointermove", (e) => {
    if (e.buttons || !roof || performance.now() - hoverAt < 60) return;
    hoverAt = performance.now();
    renderer.domElement.style.cursor = pickAt(e.clientX, e.clientY) ? "pointer" : "";
  });
  $("v3reset").onclick = () => flyTo("overview", 1200);
  $("v3present").onclick = () => flyTo("presentation", 1800);
  document.querySelectorAll("#v3mode button").forEach((b) => {
    b.onclick = () => { V.mode = b.dataset.mode; store("mode", V.mode); applyMode(); };
  });
  const sp = $("v3sunpath");
  if (sp) {
    sp.classList.toggle("on", V.sunPath);
    sp.onclick = () => { V.sunPath = !V.sunPath; store("sunpath", V.sunPath ? "on" : "off"); sp.classList.toggle("on", V.sunPath);
      if (sunPath) sunPath.group.visible = V.sunPath; requestRender(); };
  }
  const qsel = $("v3quality");
  qsel.value = V.choice;
  qsel.onchange = () => {
    V.choice = qsel.value; store("quality", V.choice); V.decided = false;
    setTier(V.choice === "auto" ? "medium" : V.choice);
  };
  new ResizeObserver(resize).observe(stage);
  new IntersectionObserver((es) => { V.visible = es[0].isIntersecting; if (V.visible) requestRender(); }).observe(stage);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) requestRender(); });
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", applyTheme);
  window.addEventListener("roof:state", (e) => {
    V.state = e.detail.state; V.open = e.detail.open || [];
    const first = !roof;
    applyState(!!e.detail.light);
    if (first && roof && e.detail.light) { applyReal(); flyTo("overview", 0); }
    else if (first && roof) {
      applyReal();
      const v = views(), intro = params.get("intro") !== "0";
      controls.target.copy(v.overview.target);
      camera.position.copy(intro ? v.intro.pos : v.overview.pos); controls.update();
      if (intro) { V.intro = V.introPlayed = true; flyTo(params.get("view") === "presentation" ? "presentation" : "overview", 2600); }
      else flyTo(params.get("view") === "presentation" ? "presentation" : "overview", 0);
    }
  });
  if (R.last) window.dispatchEvent(new CustomEvent("roof:state", { detail: R.last }));
  resize();
  pollReal();
  V.ready = true;
}

// Read-only hooks for the page tests (tests/browser/*.cjs); nothing writes here.
window.roof3d = {
  get info() {
    return { ready: V.ready, error: V.error, renders: V.renders, updates: V.updates, tUtc: V.tUtc,
      dims: V.dims, sel: V.sel, realT: V.real && V.real.latest ? V.real.latest.T_module : null,
      mode: V.mode, quality: V.tier, choice: V.choice, frameMs: V.frameMs, shadows: renderer ? renderer.shadowMap.enabled : false,
      intro: V.intro || !!V.tween, introPlayed: V.introPlayed, view: V.view, sun: { ...V.sun }, sunIntensity: sunLight ? sunLight.intensity : null,
      pixelRatio: renderer ? renderer.getPixelRatio() : null };
  },
  colorAt: (s, m) => { const c = new THREE.Color(); roof.heat.getColorAt(s * roof.m + m, c); return "#" + c.getHexString(); },
  realColor: () => "#" + realObj.heatMat.color.getHexString(),
  spraying: () => (roof ? roof.strings.map((x) => x.drops.visible && x.sheen.visible) : []),
  wet: () => (roof ? roof.strings.map((x) => +x.wet.toFixed(2)) : []),
  valveColor: (s) => "#" + roof.strings[s].valveMat.color.getHexString(),
  faceVisible: () => (roof ? { heat: roof.heat.visible, realistic: roof.real.visible } : null),
  decideTier, sunPosition, tiers: TIERS, cloudFromIrradiance,
  cloud: () => ({ a: CLOUD.a.value, b: CLOUD.b.value, dark: CLOUD.dark.value, c: V.cloud.c }),
  sunPath: () => (sunPath ? { visible: sunPath.group.visible, day: V.pathKey, points: sunPath.group.children[0].geometry.attributes.position.count,
    ball: sunPath.ball.position.toArray(), center: roof.center.toArray(), radius: SUN_PATH_R } : null),
  jets: () => (roof ? roof.strings.map((x) => x.jets.visible) : []),
  screenOf(kind, s, m) {                     // client pixel position of an object's centre
    const p = kind === "module" ? (({ x, y, z }) => new THREE.Vector3(x, y, z))(moduleTransform(s, m))
      : kind === "string" ? roof.strings[s].lab.position.clone() : realObj.pos.clone();
    p.project(camera);
    const r = renderer.domElement.getBoundingClientRect();
    return { x: r.left + (p.x + 1) / 2 * r.width, y: r.top + (1 - p.y) / 2 * r.height };
  },
};
// For the story mode (rooftop_story.js): it feeds states through the "roof:state" event and
// uses these to place the cloud shadow and the camera and to draw frames one by one.
window.roof3dStage = {
  get ready() { return V.ready && !!roof; },
  export: EXPORT,
  setDriver(d) { V.driver = d; requestRender(); },          // {active(), moving(), frame(now, dt)}
  cloud(c, dir, loss) { V.cloud.direct = true; V.cloud.c = c; V.cloud.dir = dir; V.cloud.loss = loss; setCloudUniforms(c, dir, loss); },
  cloudLive() { V.cloud.direct = false; V.cloud.target = 0; },
  pose: blendPose, flyTo, drawFrame, requestRender,
  theater(on) {
    // Story mode fills the window. With quality "auto" the tier chosen for the small view
    // says nothing about the large one: start again from "low" (never slower than before)
    // and let the frame-time watch step up once if nearly all frames are fast.
    if (V.choice !== "auto" || EXPORT) { resize(); return; }
    V.decided = false;
    setTier(on ? "low" : "medium");
  },
  look(mode) { if (mode !== V.mode && (mode === "heat" || mode === "realistic")) { V.mode = mode; applyMode(); } },   // not stored
  get mode() { return V.mode; },
  onUserCamera(fn) { controls.addEventListener("start", fn); },
};
init();
