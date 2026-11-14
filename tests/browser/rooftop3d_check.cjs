// Page test of the 3D view of /rooftop, run by tests/test_scada_rooftop3d.py.
//
//   node tests/browser/rooftop3d_check.cjs http://127.0.0.1:<port>/rooftop
//
// Loads the page in headless Chromium (software WebGL) while the simulation runs, and prints
// one JSON object with what it found; the Python test makes the assertions. It only reads
// the page: the hooks of window.roof3d are read-only.
const { chromium } = require("playwright");

const url = process.argv[2];
const origin = new URL(url).origin;

(async () => {
  const out = { errors: [], external: [] };
  const browser = await chromium.launch({ args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
  try {
    const page = await browser.newPage({ viewport: { width: 1300, height: 900 }, colorScheme: "light" });
    page.on("pageerror", (e) => out.errors.push("pageerror: " + e.message));
    page.on("console", (m) => { if (m.type() === "error") out.errors.push("console: " + m.text()); });
    page.on("requestfailed", (r) => out.errors.push("request failed: " + r.url()));
    page.on("request", (r) => { if (!r.url().startsWith(origin) && !r.url().startsWith("data:")) out.external.push(r.url()); });
    await page.goto(url);
    const info = () => page.evaluate(() => window.roof3d.info);

    // 1. it loads: the scene is built from the first simulated step and the real module's row
    await page.waitForFunction(() => window.roof3d && window.roof3d.info.updates > 0 &&
      window.roof3d.info.realT !== null, null, { timeout: 30000 });
    out.first = await info();
    out.badge = await page.locator("#v3stage .badge").innerText();

    // 2. it updates: a newer simulated step arrives and is drawn
    await page.waitForFunction((t) => window.roof3d.info.tUtc > t, out.first.tUtc, { timeout: 30000 });
    out.later = await info();
    out.check = await page.evaluate(() => {
      const R = window.RoofShared, st = R.last.state, v = window.roof3d;
      const wrong = [];
      st.modules.forEach((row, s) => row.forEach((t, m) => {
        if (v.colorAt(s, m) !== R.tempColor(t)) wrong.push([s, m, v.colorAt(s, m), R.tempColor(t)]);
      }));
      return { tUtc: st.latest.t_utc, wrongColours: wrong, spraying: v.spraying(),
        valves: st.strings.map((x) => x.valve), valveColour: v.valveColor(0), series1: R.css("--series-1"),
        realColour: v.realColor(), realExpected: R.tempColor(v.info.realT) };
    });

    // 3. click a module, a string and our real module
    await page.locator("#view3d").scrollIntoViewIfNeeded();
    const click = async (kind, s, m) => {
      const at = await page.evaluate(([k, a, b]) => window.roof3d.screenOf(k, a, b), [kind, s, m]);
      await page.mouse.click(at.x, at.y);
      await page.waitForTimeout(200);
      return { sel: (await info()).sel, text: await page.locator("#v3info").innerText() };
    };
    out.clickModule = await click("module", 1, 4);
    out.clickString = await click("string", 2);
    out.clickReal = await click("real");

    // 4. light: while spraying it animates on screen, and stops drawing off screen
    const renders = async (ms) => { const a = (await info()).renders; await page.waitForTimeout(ms); return (await info()).renders - a; };
    out.rendersOnScreen = await renders(1500);
    await page.locator("#alarms").scrollIntoViewIfNeeded();
    await page.waitForTimeout(300);
    out.rendersOffScreen = await renders(1500);
  } catch (e) {
    out.errors.push("check: " + e.message);
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(out));
})();
