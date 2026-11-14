// Page test of the navigation bar, run by tests/test_scada_together.py:
//   node tests/browser/nav_check.cjs http://127.0.0.1:<port>
// Opens the dashboard, follows the bar to /rooftop and back, and prints what it found.
const { chromium } = require("playwright");
const base = process.argv[2];

(async () => {
  const out = { errors: [], steps: [] };
  const browser = await chromium.launch({ args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
  try {
    const page = await browser.newPage({ viewport: { width: 1300, height: 900 } });
    page.on("pageerror", (e) => out.errors.push("pageerror: " + e.message));
    page.on("console", (m) => { if (m.type() === "error") out.errors.push("console: " + m.text()); });
    const where = async () => ({ path: new URL(page.url()).pathname, title: await page.title(),
      current: await page.locator('.pages a[aria-current="page"]').innerText() });
    await page.goto(base + "/");
    await page.waitForFunction(() => document.getElementById("mode").textContent !== "…", null, { timeout: 20000 });
    out.steps.push({ ...(await where()), mode: await page.locator("#mode").innerText() });
    await page.locator('.pages a[href="/rooftop"]').click();
    await page.waitForFunction(() => window.roof3d && window.roof3d.info.updates > 0, null, { timeout: 30000 });
    out.steps.push({ ...(await where()), badge: await page.locator("#v3stage .badge").innerText() });
    await page.locator('.pages a[href="/"]').click();
    await page.waitForLoadState("load");
    out.steps.push(await where());
  } catch (e) {
    out.errors.push("check: " + e.message);
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(out));
})();
