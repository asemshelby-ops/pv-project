// Frame-time benchmark of the 3D view of /rooftop, run by tests/browser/bench3d.py.
//
//   node tests/browser/rooftop3d_bench.cjs <url of /rooftop> <seconds> <repeat> <gpu|swiftshader> <story|nostory>
//
// For each quality tier and module look it opens the page with the tier forced, waits until
// every zone sprays (the view then animates all the time), and measures over <seconds>:
//   ms_per_frame   = time / frames drawn (the view's own counter, window.roof3d.info.renders)
//   raf_median_ms  = median interval between animation frames (what the page shows as ms/frame)
// The best of <repeat> runs is kept (other programs can only make a run slower).
// It only reads the page: nothing is changed in the simulation.
const { chromium } = require("playwright");

const [url, seconds = "12", repeat = "3", gpu = "swiftshader", story = "nostory"] = process.argv.slice(2);
const ARGS = gpu === "gpu" ? ["--enable-gpu", "--ignore-gpu-blocklist"] : ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"];
const VIEW = { width: 1300, height: 900 };

async function measure(browser, query, ms, out, prepare) {
  const page = await browser.newPage({ viewport: VIEW, colorScheme: "light" });
  page.on("pageerror", (e) => out.errors.push(query + " pageerror: " + e.message));
  await page.goto(url + query);
  await page.locator("#view3d").scrollIntoViewIfNeeded();
  await page.waitForFunction(() => window.roof3d && window.roof3d.info.updates > 0, null, { timeout: 120000 });
  if (prepare) await prepare(page);
  else await page.waitForFunction(() => window.roof3d.spraying().every(Boolean), null, { timeout: 120000 });
  await page.waitForTimeout(2500);                       // shaders compiled, wet sheen up
  const r = await page.evaluate((T) => new Promise((done) => {
    const iv = []; let last = 0; const r0 = window.roof3d.info.renders, t0 = performance.now();
    const tick = (now) => {
      if (last) iv.push(now - last);
      last = now;
      if (now - t0 < T) requestAnimationFrame(tick);
      else {
        iv.sort((a, b) => a - b);
        const st = document.getElementById("v3stage").getBoundingClientRect();
        done({ frames: window.roof3d.info.renders - r0, ms: now - t0, raf: iv[Math.floor(iv.length / 2)],
          stage: `${Math.round(st.width)}x${Math.round(st.height)}`,
          pr: window.roof3d.info.pixelRatio, quality: window.roof3d.info.quality });
      }
    };
    requestAnimationFrame(tick);
  }), ms);
  if (!out.renderer) {
    out.renderer = await page.evaluate(() => {
      const gl = document.createElement("canvas").getContext("webgl2"), e = gl && gl.getExtension("WEBGL_debug_renderer_info");
      return e ? gl.getParameter(e.UNMASKED_RENDERER_WEBGL) : "unknown";
    });
    out.stage = r.stage;
  }
  await page.close();
  return r;
}

(async () => {
  const out = { errors: [], cases: [], viewport: `${VIEW.width}x${VIEW.height}`, renderer: null, stage: null };
  const browser = await chromium.launch({ args: ARGS });
  try {
    const cases = [];
    for (const mode of ["heat", "realistic"]) for (const q of ["low", "medium", "high"]) {
      cases.push({ name: `${q}, ${mode}, all zones spraying`, query: `?quality=${q}&intro=0&mode=${mode}` });
    }
    if (story === "story") for (const q of ["low", "medium", "high"]) {
      cases.push({ name: `${q}, story mode playing`, query: `?quality=${q}&intro=0&story=1&autoplay=1&loop=1`,
        prepare: (page) => page.waitForFunction(() => window.roofStory && window.roofStory.info.playing, null, { timeout: 120000 }) });
    }
    for (const c of cases) {
      let best = null;
      for (let k = 0; k < +repeat; k++) {
        const r = await measure(browser, c.query, +seconds * 1000, out, c.prepare);
        const v = { ms_per_frame: r.ms / Math.max(1, r.frames), raf_median_ms: r.raf, quality: r.quality, pixelRatio: r.pr };
        if (!best || v.ms_per_frame < best.ms_per_frame) best = v;
      }
      out.cases.push({ name: c.name, ...best });
    }
  } catch (e) {
    out.errors.push("bench: " + e.message);
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(out));
})();
