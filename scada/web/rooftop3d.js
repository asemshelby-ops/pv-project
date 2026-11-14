// 3D view of the rooftop SIMULATION (page /rooftop). Everything on the rooftop is SIMULATED
// (twin/, placeholder parameters); its values come from rooftop.js, which reads /api/sim/*.
//
// One object is NOT simulated: our real module, drawn apart on its own stand and labelled as
// the only measured one. Only that object reads /api/state (the real module's newest row,
// read-only). Its values are never mixed with the simulation, and no simulated value is
// ever drawn on it.
//
// Three.js 0.160.0 is vendored in vendor/three-0.160.0/ (the SCADA must work offline).
// Light by design: two instanced meshes for the 120 modules, a few dozen other meshes, no
// shadows, and a frame is drawn only when something changed (view moved, new data) or,
// while a zone is sprayed, at about 30 frames per second. Nothing is drawn while the view
// is scrolled off screen or the tab is hidden.
import * as THREE from "three";
import { OrbitControls } from "/vendor/three-0.160.0/OrbitControls.js";

const R = window.RoofShared;                  // shared with rooftop.js: one colour scale
const $ = (id) => document.getElementById(id);
const deg = Math.PI / 180;

// Display layout, NOT a plant design [ASSUMPTION]: modules of our rig's size (1200 x 540 mm)
// in portrait, tilt 20 deg facing south, one row per string. +x = east, +z = south, y = up.
const G = { w: 0.54, l: 1.2, t: 0.035, gap: 0.03, tilt: 20 * deg, pitch: 2.6, clear: 0.35 };
const SPRAY_N = 140;                          // droplets drawn per sprayed string

const V = {                                   // the view's state; window.roof3d reads it (tests)
  ready: false, error: null, renders: 0, updates: 0, tUtc: null, dims: "",
  state: null, open: [], real: null, realError: false, sel: null, visible: true,
};
let renderer, scene, camera, controls, roof, realObj, raycaster, pickables = [];
let pending = false, lastFrame = 0, sprayTimer = null;

// ---- small helpers ---------------------------------------------------------------------------
const color = (cssVar) => new THREE.Color(R.css(cssVar) || "#888");
function mat(kind, cssVar, extra = {}) {
  const m = kind === "basic" ? new THREE.MeshBasicMaterial(extra) : new THREE.MeshLambertMaterial(extra);
  m.userData.cssVar = cssVar;
  if (cssVar) m.color.set(R.css(cssVar));
  return m;
}
function box(w, h, d, material, x, y, z) {
  const b = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), material);
  b.position.set(x, y, z);
  return b;
}
function pipe(a, b, material, r = 0.035) {    // a cylinder from point a to point b
  const va = new THREE.Vector3(...a), vb = new THREE.Vector3(...b), len = va.distanceTo(vb);
  const p = new THREE.Mesh(new THREE.CylinderGeometry(r, r, len, 8, 1), material);
  p.position.copy(va).add(vb).multiplyScalar(0.5);
  p.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), vb.clone().sub(va).normalize());
  return p;
}
function label(lines, height = 0.02, strong = false) {
  // A text tag that always faces the camera, at a constant size on screen (`height` = share
  // of the view's height per line), drawn on a canvas with the page's colours.
  const cv = document.createElement("canvas"), tex = new THREE.CanvasTexture(cv);
  tex.colorSpace = THREE.SRGBColorSpace;
  const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false, transparent: true, sizeAttenuation: false }));
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
  roof = { group, n, m, geo: { x0, zRow, zTop, zBot, yTop, yPipe, xW, xE, xMan, rowLen }, strings: [] };

  // roof slab and a low parapet
  const minX = xW - 2.6, maxX = xMan + 3.6, minZ = zRow[0] - 2.2, maxZ = zS + 1.6;
  const slab = mat("lambert", "--unknown"), wall = mat("lambert", "--axis");
  group.add(box(maxX - minX, 0.3, maxZ - minZ, slab, (minX + maxX) / 2, -0.15, (minZ + maxZ) / 2));
  for (const [w, d, x, z] of [[maxX - minX, 0.2, (minX + maxX) / 2, minZ], [maxX - minX, 0.2, (minX + maxX) / 2, maxZ],
    [0.2, maxZ - minZ, minX, (minZ + maxZ) / 2], [0.2, maxZ - minZ, maxX, (minZ + maxZ) / 2]]) {
    group.add(box(w, 0.45, d, wall, x, 0.22, z));
  }
  roof.bounds = { minX, maxX, minZ, maxZ };

  // modules: an aluminium frame and a glass face coloured by temperature (two draw calls)
  const frameM = new THREE.InstancedMesh(new THREE.BoxGeometry(G.w, G.t, G.l), mat("lambert", null, { color: 0x8c9096 }), n * m);
  const cellM = new THREE.InstancedMesh(new THREE.BoxGeometry(G.w - 0.05, G.t + 0.004, G.l - 0.05), mat("basic", null), n * m);
  const sens = st.sensor_idx.reduce((k, a) => k + a.length, 0);
  const dotM = new THREE.InstancedMesh(new THREE.CylinderGeometry(0.045, 0.045, 0.01, 10), mat("basic", "--ink"), sens);
  const q = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), G.tilt), one = new THREE.Vector3(1, 1, 1);
  const up = new THREE.Vector3(0, 1, 0).applyQuaternion(q), mx = new THREE.Matrix4(), white = new THREE.Color(R.css("--unknown"));
  let d = 0;
  for (let s = 0; s < n; s++) for (let k = 0; k < m; k++) {
    const p = moduleTransform(s, k), i = s * m + k, pos = new THREE.Vector3(p.x, p.y, p.z);
    mx.compose(pos, q, one); frameM.setMatrixAt(i, mx); cellM.setMatrixAt(i, mx); cellM.setColorAt(i, white);
    if (st.sensor_idx[s].includes(k)) { mx.compose(pos.clone().addScaledVector(up, G.t / 2 + 0.008), q, one); dotM.setMatrixAt(d++, mx); }
  }
  frameM.userData.pick = cellM.userData.pick = "module";
  group.add(frameM, cellM, dotM);
  roof.cells = cellM; roof.frames = frameM; roof.q = q; roof.up = up;

  // racks: a low front rail and a high back rail under each row
  const steel = mat("lambert", "--muted");
  zRow.forEach((z, s) => {
    group.add(box(rowLen + 0.2, 0.06, 0.06, steel, 0, G.clear - 0.03, zBot[s] - 0.1));
    group.add(box(rowLen + 0.2, 0.06, 0.06, steel, 0, yTop - 0.1, zTop[s] + 0.1));
  });

  // water: tank, pump, a manifold along the east side, one branch, valve and spray pipe per string
  const pipeMat = mat("lambert", "--series-1"), tankMat = mat("lambert", null, { color: 0xf2f2ee });
  const tank = new THREE.Mesh(new THREE.CylinderGeometry(0.75, 0.75, 1.3, 24), tankMat);
  tank.position.set(xMan + 2.2, 0.65, zS); tank.userData.pick = "pump";
  const pump = box(0.6, 0.4, 0.4, mat("lambert", "--ink2"), xMan + 0.8, 0.2, zS); pump.userData.pick = "pump";
  group.add(tank, pump, pipe([xMan + 1.45, 0.25, zS], [xMan + 1.1, 0.25, zS], pipeMat),
    pipe([xMan + 0.5, 0.12, zS], [xMan, 0.12, zS], pipeMat),
    pipe([xMan, 0.12, zS], [xMan, 0.12, zTop[0] - 0.1], pipeMat));
  const pumpLabel = label(["Pump and tank", "shared by all zones"], 0.019);
  pumpLabel.position.set(xMan + 1.5, 2.0, zS); pumpLabel.userData.pick = "pump";
  group.add(pumpLabel);
  roof.pumpPos = new THREE.Vector3(xMan + 0.8, 0.4, zS);

  for (let s = 0; s < n; s++) {
    const zp = zTop[s] - 0.1, items = [];
    items.push(pipe([xMan, 0.12, zp], [xMan, yPipe, zp], pipeMat),          // riser
      pipe([xMan, yPipe, zp], [xW - 0.1, yPipe, zp], pipeMat, 0.03));         // branch + spray pipe
    const valveMat = mat("lambert", "--muted");
    const valve = box(0.2, 0.2, 0.2, valveMat, xMan - 0.45, yPipe, zp);
    const handle = box(0.36, 0.05, 0.05, valveMat, 0, 0.16, 0);
    valve.add(handle);
    const lab = label([`String ${s + 1}`], 0.02);
    lab.position.set(xW - 1.9, yTop + 0.4, zRow[s]);
    // spray: droplets running from the pipe down the glass, and a light mist over the string
    const pos = new Float32Array(SPRAY_N * 3), seed = new Float32Array(SPRAY_N * 3);
    for (let k = 0; k < SPRAY_N; k++) { seed[3 * k] = Math.random(); seed[3 * k + 1] = Math.random(); seed[3 * k + 2] = 0.6 + 0.8 * Math.random(); }
    const pg = new THREE.BufferGeometry(); pg.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    const drops = new THREE.Points(pg, new THREE.PointsMaterial({ color: 0xffffff, size: 0.09, transparent: true, opacity: 0.95, depthWrite: false }));
    drops.frustumCulled = false;
    const mist = new THREE.Mesh(new THREE.PlaneGeometry(rowLen + 0.1, G.l + 0.1),
      new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.4, depthWrite: false, side: THREE.DoubleSide }));
    mist.quaternion.copy(q).multiply(new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), -Math.PI / 2));
    mist.position.set(0, G.clear + half * Math.sin(G.tilt), zRow[s]).addScaledVector(up, 0.09);
    drops.visible = mist.visible = false;
    for (const o of [...items, valve, lab]) o.userData.pick = "string", o.userData.s = s;
    group.add(...items, valve, lab, drops, mist);
    roof.strings.push({ valve, valveMat, handle, lab, drops, mist, seed, open: false });
  }

  // a north arrow on the roof
  const arrow = new THREE.Mesh(new THREE.ConeGeometry(0.35, 0.9, 16), mat("lambert", "--ink2"));
  arrow.rotation.x = -Math.PI / 2; arrow.position.set(maxX - 1.0, 0.25, minZ + 1.0);
  const north = label(["N"], 0.02); north.position.set(maxX - 1.0, 0.9, minZ + 0.3);
  group.add(arrow, north);

  // selection outline, reused for a module, a string or the real module
  const outline = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(1, 1, 1)),
    new THREE.LineBasicMaterial({ color: R.css("--ink"), depthTest: false }));
  outline.renderOrder = 9; outline.visible = false; outline.userData.cssVar = "--ink";
  group.add(outline);
  roof.outline = outline;

  scene.add(group);
  buildReal();
  pickables = [cellM, frameM, tank, pump, pumpLabel, ...roof.strings.flatMap((x) => [x.valve, x.lab]),
    ...group.children.filter((o) => o.userData.pick === "string" && o.isMesh), ...realObj.pick];
  V.dims = `${n}x${m}`;
}

// ---- our real module: a separate stand, the only measured object ------------------------------
function buildReal() {
  if (realObj) { scene.remove(realObj.group); }
  const g = new THREE.Group(), b = roof.bounds, x = b.minX - 4.2, z = 0;
  const half = G.l / 2, yc = G.clear + half * Math.sin(G.tilt), q = roof.q;
  g.add(box(2.6, 0.08, 2.6, mat("lambert", "--grid"), x, -0.3, z));       // its own pad, not the roof
  const frame = new THREE.Mesh(new THREE.BoxGeometry(G.w, G.t, G.l), mat("lambert", null, { color: 0x8c9096 }));
  const cell = new THREE.Mesh(new THREE.BoxGeometry(G.w - 0.05, G.t + 0.004, G.l - 0.05), mat("basic", "--unknown"));
  for (const o of [frame, cell]) { o.position.set(x, yc, z); o.quaternion.copy(q); o.userData.pick = "real"; }
  const steel = mat("lambert", "--muted");
  g.add(frame, cell, box(0.05, G.clear + 0.05, 0.05, steel, x, (G.clear - 0.3) / 2, z + half * Math.cos(G.tilt) - 0.1),
    box(0.05, yc + half * Math.sin(G.tilt) - 0.1 + 0.3, 0.05, steel, x, (yc + half * Math.sin(G.tilt) - 0.4) / 2, z - half * Math.cos(G.tilt) + 0.1));
  const pyr = new THREE.Mesh(new THREE.CylinderGeometry(0.07, 0.07, 0.06, 16), mat("lambert", "--ink2"));   // PYR20, coplanar
  pyr.position.set(x + G.w / 2 + 0.16, yc, z); pyr.quaternion.copy(q); pyr.userData.pick = "real";
  const tag = label(["Our real module", "the only measured one · not part of the rooftop"], 0.022, true);
  tag.position.set(x, 2.3, z); tag.userData.pick = "real";
  g.add(pyr, tag);
  scene.add(g);
  realObj = { group: g, cell, tag, pick: [frame, cell, pyr, tag], pos: new THREE.Vector3(x, yc, z) };
}

// ---- data → scene ----------------------------------------------------------------------------
function openAlarms(s) {
  return V.open.filter((a) => !a.cleared && a.code.endsWith("_Z" + (s + 1)))
    .map((a) => a.code.replace("SIM_", "").replace(/_Z\d+$/, "").toLowerCase());
}
function applyState() {
  const st = V.state;
  if (!st || !st.available || !st.modules) return;
  if (!roof || V.dims !== `${st.n_strings}x${st.modules_per_string}`) buildRoof(st);
  const c = new THREE.Color();
  st.modules.forEach((row, s) => row.forEach((t, m) => roof.cells.setColorAt(s * roof.m + m, c.set(R.tempColor(t)))));
  roof.cells.instanceColor.needsUpdate = true;
  st.strings.forEach((x) => {
    const z = x.string - 1, o = roof.strings[z], open = !!x.valve;
    o.open = open;
    o.valveMat.color.set(R.css(open ? "--series-1" : "--muted"));
    o.valveMat.emissive.set(open ? R.css("--series-1") : "#000000"); o.valveMat.emissiveIntensity = open ? 0.45 : 0;
    o.handle.rotation.y = open ? 0 : Math.PI / 2;        // handle along the pipe = open
    o.drops.visible = o.mist.visible = open;
    const al = openAlarms(z);
    setLabel(o.lab, [`String ${z + 1}` + (open ? " · spraying" : "") + (al.length ? " · ▲ " + al.join(", ") : "")]);
  });
  V.tUtc = st.latest ? st.latest.t_utc : null;
  V.updates++;
  renderInfo();
  requestRender();
}
function applyReal() {
  if (!realObj) return;
  const r = V.real && V.real.latest, t = r ? r.T_module : null;
  realObj.cell.material.color.set(t === null || t === undefined ? R.css("--unknown") : R.tempColor(t));
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
function animateSpray(dt) {
  if (!roof) return false;
  let any = false;
  const o = roof.geo, half = G.l / 2;
  roof.strings.forEach((x, s) => {
    if (!x.open) return;
    any = true;
    const a = x.drops.geometry.attributes.position, p = a.array, sd = x.seed;
    for (let k = 0; k < SPRAY_N; k++) {
      sd[3 * k + 1] = (sd[3 * k + 1] + dt * 0.55 * sd[3 * k + 2]) % 1;      // phase down the glass
      const f = sd[3 * k + 1], along = -half + f * G.l;                         // from the top edge
      const lift = 0.06 + 0.55 * Math.sin(Math.PI * Math.min(1, f * 1.4)) * (1 - f);   // a fan from the nozzles
      p[3 * k] = o.xW + sd[3 * k] * o.rowLen;
      p[3 * k + 1] = G.clear + half * Math.sin(G.tilt) - along * Math.sin(G.tilt) + lift * roof.up.y;
      p[3 * k + 2] = o.zRow[s] + along * Math.cos(G.tilt) + lift * roof.up.z;
    }
    a.needsUpdate = true;
  });
  return any;
}

// ---- rendering on demand ---------------------------------------------------------------------
function requestRender() {
  if (!renderer || pending) return;
  if (!V.visible) return;                      // off screen: drawn again when it comes back
  pending = true;
  requestAnimationFrame(frame);
}
function frame(now) {
  pending = false;
  const dt = Math.min(0.1, lastFrame ? (now - lastFrame) / 1000 : 0);
  lastFrame = now;
  controls.update();
  const spraying = animateSpray(dt);
  renderer.render(scene, camera);
  V.renders++;
  clearTimeout(sprayTimer);
  if (spraying && V.visible && !document.hidden) sprayTimer = setTimeout(requestRender, 33);
  else lastFrame = 0;
}
function resize() {
  const st = $("v3stage"), w = st.clientWidth, h = st.clientHeight;
  if (!w || !h) return;
  renderer.setSize(w, h, false);
  camera.aspect = w / h; camera.updateProjectionMatrix();
  requestRender();
}
function resetView() {
  const b = roof ? roof.bounds : { minX: -10, maxX: 10, minZ: -8, maxZ: 8 };
  controls.target.set((b.minX + b.maxX) / 2 - 2.5, 0, (b.minZ + b.maxZ) / 2 + 0.5);
  camera.position.set(controls.target.x + 5, 16, controls.target.z + 20.5);
  controls.update(); requestRender();
}
function applyTheme() {
  scene.background = color("--page");
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
    renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "low-power" });
  } catch (e) {
    V.error = "WebGL is not available in this browser: the 3D view is off. The table below shows the same values.";
    $("v3msg").textContent = V.error; $("v3msg").hidden = false; stage.hidden = true;
    return;
  }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
  stage.prepend(renderer.domElement);
  scene = new THREE.Scene();
  camera = new THREE.PerspectiveCamera(40, 2, 0.1, 300);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x808080, 2.2));
  const sun = new THREE.DirectionalLight(0xffffff, 1.4); sun.position.set(6, 12, 9); scene.add(sun);
  controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true; controls.dampingFactor = 0.12;
  controls.minDistance = 3; controls.maxDistance = 70; controls.maxPolarAngle = 85 * deg;
  controls.addEventListener("change", requestRender);
  raycaster = new THREE.Raycaster();
  scene.background = color("--page");

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
  $("v3reset").onclick = resetView;
  new ResizeObserver(resize).observe(stage);
  new IntersectionObserver((es) => { V.visible = es[0].isIntersecting; if (V.visible) requestRender(); }).observe(stage);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) requestRender(); });
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", applyTheme);
  window.addEventListener("roof:state", (e) => {
    V.state = e.detail.state; V.open = e.detail.open || [];
    const first = !roof;
    applyState();
    if (first && roof) { applyReal(); resetView(); }
  });
  if (R.last) window.dispatchEvent(new CustomEvent("roof:state", { detail: R.last }));
  resize();
  pollReal();
  V.ready = true;
}

// Read-only hooks for the page test (tests/browser/rooftop3d_check.mjs); nothing writes here.
window.roof3d = {
  get info() {
    return { ready: V.ready, error: V.error, renders: V.renders, updates: V.updates, tUtc: V.tUtc,
      dims: V.dims, sel: V.sel, realT: V.real && V.real.latest ? V.real.latest.T_module : null };
  },
  colorAt: (s, m) => { const c = new THREE.Color(); roof.cells.getColorAt(s * roof.m + m, c); return "#" + c.getHexString(); },
  realColor: () => "#" + realObj.cell.material.color.getHexString(),
  spraying: () => (roof ? roof.strings.map((x) => x.drops.visible && x.mist.visible) : []),
  valveColor: (s) => "#" + roof.strings[s].valveMat.color.getHexString(),
  screenOf(kind, s, m) {                     // client pixel position of an object's centre
    const p = kind === "module" ? (({ x, y, z }) => new THREE.Vector3(x, y, z))(moduleTransform(s, m))
      : kind === "string" ? roof.strings[s].lab.position.clone() : realObj.pos.clone();
    p.project(camera);
    const r = renderer.domElement.getBoundingClientRect();
    return { x: r.left + (p.x + 1) / 2 * r.width, y: r.top + (1 - p.y) / 2 * r.height };
  },
};
init();
