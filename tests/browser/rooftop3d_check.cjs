// Page test of the 3D view of /rooftop, run by tests/test_scada_rooftop3d.py.
//
//   node tests/browser/rooftop3d_check.cjs http://127.0.0.1:<port>/rooftop
//
// Loads the page in headless Chromium (software WebGL) while the simulation runs, and prints
// one JSON object with what it found; the Python test makes the assertions. It only reads
// the page and presses its buttons: the hooks of window.roof3d are read-only.
const { chromium } = require("playwright");

const url = process.argv[2];
const origin = new URL(url).origin;
const ARGS = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"];

async function open(browser, out, query) {
  const page = await browser.newPage({ viewport: { width: 1300, height: 900 }, colorScheme: "light" });
  page.on("pageerror", (e) => out.errors.push("pageerror: " + e.message));
  page.on("console", (m) => { if (m.type() === "error") out.errors.push("console: " + m.text()); });
  page.on("requestfailed", (r) => out.errors.push("request failed: " + r.url()));
  page.on("request", (r) => { if (!r.url().startsWith(origin) && !r.url().startsWith("data:")) out.external.push(r.url()); });
  await page.goto(url + query);
  await page.locator("#view3d").scrollIntoViewIfNeeded();
  await page.waitForFunction(() => window.roof3d && window.roof3d.info.updates > 0, null, { timeout: 60000 });
  return page;
}

(async () => {
  const out = { errors: [], external: [] };
  const browser = await chromium.launch({ args: ARGS });
  try {
    // 1. it loads (auto quality, with the camera intro), with the real module's row
    const page = await open(browser, out, "");
    const info = () => page.evaluate(() => window.roof3d.info);
    await page.waitForFunction(() => window.roof3d.info.realT !== null && !window.roof3d.info.intro, null, { timeout: 60000 });
    out.first = await info();
    out.badge = await page.locator("#v3stage .badge").innerText();

    // 2. it updates: a newer simulated step arrives and is drawn, in the shared colour scale
    await page.waitForFunction((t) => window.roof3d.info.tUtc > t, out.first.tUtc, { timeout: 60000 });
    const check = () => page.evaluate(() => {
      const R = window.RoofShared, st = R.last.state, v = window.roof3d, wrong = [];
      st.modules.forEach((row, s) => row.forEach((t, m) => {
        if (v.colorAt(s, m) !== R.tempColor(t)) wrong.push([s, m, v.colorAt(s, m), R.tempColor(t)]);
      }));
      return { tUtc: st.latest.t_utc, wrongColours: wrong, spraying: v.spraying(), wet: v.wet(),
        valves: st.strings.map((x) => x.valve), valveColour: v.valveColor(0), series1: R.css("--series-1"),
        realColour: v.realColor(), realExpected: R.tempColor(v.info.realT), faces: v.faceVisible(),
        sun: v.info.sun, sunTUtc: v.info.tUtc };
    });
    out.later = await info();
    out.check = await check();

    // 3. click a module, a string and our real module
    const click = async (kind, s, m) => {
      const at = await page.evaluate(([k, a, b]) => window.roof3d.screenOf(k, a, b), [kind, s, m]);
      await page.mouse.click(at.x, at.y);
      await page.waitForTimeout(300);
      return { sel: (await info()).sel, text: await page.locator("#v3info").innerText() };
    };
    out.clickModule = await click("module", 1, 4);
    out.clickString = await click("string", 2);
    out.clickReal = await click("real");

    // 4. the module look: realistic hides the heat map, which keeps following the data
    await page.click('#v3mode button[data-mode="realistic"]');
    await page.waitForTimeout(1500);
    out.realistic = { info: await info(), check: await check(), note: await page.locator("#v3modeNote").isVisible() };
    await page.click('#v3mode button[data-mode="heat"]');
    out.heatAgain = await page.evaluate(() => window.roof3d.faceVisible());

    // 5. the presentation preset and back
    await page.click("#v3present");
    await page.waitForFunction(() => !window.roof3d.info.intro, null, { timeout: 60000 });
    out.presentation = (await info()).view;
    await page.click("#v3reset");
    await page.waitForFunction(() => !window.roof3d.info.intro, null, { timeout: 60000 });
    out.overview = (await info()).view;

    // 6. the frame-time rule (pure function) and light: drawing on screen, none off screen
    out.rule = await page.evaluate(() => {
      const d = window.roof3d.decideTier;
      return {
        slowHigh: d("high", { median: 55, p90: 60, cpu: 3 }, false),
        steady30: d("medium", { median: 38, p90: 42, cpu: 3 }, false),
        slowCpu: d("medium", { median: null, p90: null, cpu: 30 }, false),
        slowLow: d("low", { median: 200, p90: 250, cpu: 3 }, false),
        fastFirst: d("medium", { median: 16.7, p90: 17, cpu: 2 }, true),
        fastLater: d("medium", { median: 16.7, p90: 17, cpu: 2 }, false),
        mixed: d("medium", { median: 17, p90: 40, cpu: 2 }, true),
      };
    });
    const renders = async (ms) => { const a = (await info()).renders; await page.waitForTimeout(ms); return (await info()).renders - a; };
    out.rendersOnScreen = await renders(2000);
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));   // the view fully off screen
    await page.waitForFunction(() => document.getElementById("v3stage").getBoundingClientRect().bottom < 0);
    await page.waitForTimeout(400);
    out.rendersOffScreen = await renders(2000);
    out.autoInfo = await info();
    await page.close();

    // 7. forced quality tiers
    for (const q of ["high", "low"]) {
      const p = await open(browser, out, `?quality=${q}&intro=0`);
      out["forced_" + q] = await p.evaluate(() => window.roof3d.info);
      await p.close();
    }
  } catch (e) {
    out.errors.push("check: " + e.message);
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(out));
})();
