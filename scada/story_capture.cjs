// Draws the story mode of /rooftop frame by frame and pipes the frames to ffmpeg.
// Run by scada/export_story.py; not meant to be started by hand.
//
//   node scada/story_capture.cjs <config.json>
//
// config: {url, out_mp4, stills_dir, lang, width, height, fps, quality, gpu, label, ffmpeg, compare_png}
// Every frame is the page itself (3D view + caption + counters + the SIMULATION label), so
// the label is burned into the video and the stills; the script refuses to record when the
// label is not visible on the page.
const { chromium } = require("playwright");
const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");

(async () => {
  const cfg = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
  const args = cfg.gpu ? ["--enable-gpu", "--ignore-gpu-blocklist"] : ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"];
  const browser = await chromium.launch({ args });
  const result = { frames: 0, stills: [], errors: [] };
  let ff = null;
  try {
    const page = await browser.newPage({ viewport: { width: cfg.width, height: cfg.height }, colorScheme: "light", deviceScaleFactor: 1 });
    page.on("pageerror", (e) => result.errors.push("pageerror: " + e.message));
    await page.goto(`${cfg.url}/rooftop?story=1&export=1&intro=0&quality=${cfg.quality}&lang=${cfg.lang}`);
    await page.waitForFunction(() => window.roofStory && window.roofStory.info.ready && window.roofStory.info.on, null, { timeout: 120000 });
    await page.evaluate(() => document.fonts.ready);
    const info = await page.evaluate(() => window.roofStory.info);
    const label = page.locator("#v3label");
    if (info.label !== cfg.label || !(await label.isVisible())) throw new Error(`the label "${cfg.label}" is not on the page: nothing recorded`);
    result.total = info.total; result.chapters = info.chapters; result.label = info.label;
    const n = Math.round(info.total * cfg.fps), dt = 1 / cfg.fps;
    // stills: the middle of these chapters (those that exist in this story)
    const want = new Map();
    for (const c of info.chapters) if (["cloud", "cool", "spike", "alarm", "spray", "result", "noalarm", "refused"].includes(c.id)) {
      want.set(Math.round((c.t0 + c.seconds * (c.id === "spray" ? 0.4 : c.id === "result" ? 0.9 : 0.6)) * cfg.fps), c.id);
    }
    ff = spawn(cfg.ffmpeg, ["-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", String(cfg.fps), "-c:v", "mjpeg", "-i", "-",
      "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-preset", "medium", "-movflags", "+faststart", cfg.out_mp4], { stdio: ["pipe", "inherit", "inherit"] });
    const done = new Promise((res, rej) => { ff.on("close", (code) => (code === 0 ? res() : rej(new Error("ffmpeg exit " + code)))); ff.on("error", rej); });
    for (let k = 0; k <= n; k++) {
      await page.evaluate(([t, d]) => window.roofStory.renderAt(t, d), [k * dt, k ? dt : 0]);
      const jpg = await page.screenshot({ type: "jpeg", quality: 94 });
      if (!ff.stdin.write(jpg)) await new Promise((r) => ff.stdin.once("drain", r));
      if (want.has(k)) {
        const f = path.join(cfg.stills_dir, `story_${cfg.lang}_${String(result.stills.length + 1).padStart(2, "0")}_${want.get(k)}.png`);
        await page.screenshot({ path: f, type: "png" });
        result.stills.push(f);
      }
      result.frames++;
      if (k % 100 === 0) process.stderr.write(`  frame ${k} of ${n}\n`);
    }
    ff.stdin.end();
    await done;
    if (cfg.compare_png) {                       // the controller comparison as a still
      const p2 = await browser.newPage({ viewport: { width: 1500, height: 1000 }, colorScheme: "light", deviceScaleFactor: 1.5 });
      await p2.goto(`${cfg.url}/rooftop?intro=0&quality=low`);
      await p2.waitForFunction(() => window.roofCompare && window.roofCompare.info.ready, null, { timeout: 60000 });
      await p2.locator("#compare").screenshot({ path: cfg.compare_png });
      result.stills.push(cfg.compare_png);
    }
  } catch (e) {
    result.errors.push("capture: " + e.message);
    if (ff) try { ff.stdin.end(); } catch (x) { /* already closed */ }
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(result));
  process.exit(result.errors.length ? 1 : 0);
})();
