// Page test of the story mode and the controller comparison of /rooftop, run by
// tests/test_scada_rooftop3d.py.
//
//   node tests/browser/story_check.cjs http://127.0.0.1:<port>/rooftop
//
// The server has showcase data but NO running simulation: the page must still show the
// story and the comparison. Prints one JSON object; the Python test makes the assertions.
const { chromium } = require("playwright");

const url = process.argv[2];
const origin = new URL(url).origin;
const ARGS = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"];

async function open(browser, out, query) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 }, colorScheme: "light" });
  page.on("pageerror", (e) => out.errors.push("pageerror: " + e.message));
  page.on("console", (m) => { if (m.type() === "error") out.errors.push("console: " + m.text()); });
  page.on("request", (r) => { if (!r.url().startsWith(origin) && !r.url().startsWith("data:")) out.external.push(r.url()); });
  await page.goto(url + query);
  await page.waitForFunction(() => window.roofStory && (window.roofStory.info.ready || window.roofStory.info.error), null, { timeout: 60000 });
  return page;
}
const snap = (page) => page.evaluate(() => ({ story: window.roofStory.info, cloud: window.roof3d.cloud(), spraying: window.roof3d.spraying(),
  jets: window.roof3d.jets(), faces: window.roof3d.faceVisible(), quality: window.roof3d.info.quality, sunPath: window.roof3d.sunPath(),
  sun: window.roof3d.info.sun, scale: [window.RoofShared.SCALE.lo, window.RoofShared.SCALE.hi], hold: window.RoofShared.hold,
  title: document.getElementById("stTitle").textContent, text: document.getElementById("stText").textContent,
  pv: document.querySelector("#stCounters .c .v") ? document.querySelector("#stCounters .c .v").textContent : null,
  counters: document.getElementById("stCounters").innerText, theater: document.body.classList.contains("theater"),
  legend: document.getElementById("v3legend").innerText, legendShown: getComputedStyle(document.getElementById("v3legend")).display !== "none" }));

(async () => {
  const out = { errors: [], external: [] };
  const browser = await chromium.launch({ args: ARGS });
  try {
    // 1. no simulation running: the roof shows the story's first moment, the comparison is drawn
    let page = await open(browser, out, "?intro=0&quality=low");
    await page.waitForFunction(() => window.roof3d.info.updates > 0 && window.roofCompare.info.ready, null, { timeout: 60000 });
    out.idle = { ...(await snap(page)), compare: await page.evaluate(() => window.roofCompare.info),
      nolive: await page.evaluate(() => document.body.classList.contains("nolive")),
      tilesShown: await page.locator("#tiles").isVisible(), compareShown: await page.locator("#compare").isVisible(),
      cards: await page.locator(".cmpcard").count(), sims: await page.locator(".cmpcard .sim").count(),
      label: await page.locator("#v3label").innerText(), labelShown: await page.locator("#v3label").isVisible(),
      compareLabel: await page.locator("#compare .simlabel").innerText() };
    // 2. the button starts the story; it plays, pauses and steps
    await page.click("#v3story");
    await page.waitForFunction(() => window.roofStory.info.playing && window.roofStory.info.tau > 1.0, null, { timeout: 60000 });
    out.playing = await snap(page);
    await page.click("#stPlay");
    out.paused = (await snap(page)).story;
    await page.waitForTimeout(500);
    out.pausedLater = (await snap(page)).story;
    await page.click("#stNext");
    out.next = (await snap(page)).story;
    await page.click("#stPrev");
    out.prev = (await snap(page)).story;
    // 3. each chapter, in order: caption, cloud shadow, look, spray
    out.chapters = [];
    for (const c of out.next.chapters) {
      await page.evaluate((t) => window.roofStory.seek(t, false), c.t0 + c.seconds * (c.id === "spray" ? 0.3 : 0.6));
      await page.waitForTimeout(250);
      out.chapters.push({ id: c.id, ...(await snap(page)) });
    }
    // 4. Arabic: right to left, IBM Plex Sans Arabic loaded, Latin runs isolated
    await page.click('#stLang button[data-lang="ar"]');
    await page.evaluate(() => document.fonts.ready);
    await page.waitForTimeout(300);
    out.arabic = await page.evaluate(() => {
      const cap = document.getElementById("stCap"), cs = getComputedStyle(cap);
      return { dir: cap.dir, lang: cap.lang, direction: cs.direction, font: cs.fontFamily, textAlign: cs.textAlign,
        loaded: document.fonts.check('16px "IBM Plex Sans Arabic"', "ماذا"),
        bdi: [...cap.querySelectorAll("bdi")].map((b) => [b.dir, b.textContent, getComputedStyle(b).direction]),
        title: document.getElementById("stTitle").textContent, html: cap.querySelectorAll("*:not(bdi):not(div)").length,
        label: document.getElementById("v3label").textContent, labelShown: getComputedStyle(document.getElementById("v3label")).display !== "none" };
    });
    // 5. leaving the story gives the page back
    await page.click("#stExit");
    out.exited = await snap(page);
    await page.close();
    // 6. frame by frame, as the export does: the label stays, buttons are hidden, high tier has jets
    page = await open(browser, out, "?story=1&export=1&intro=0&quality=high&lang=en");
    await page.waitForFunction(() => window.roofStory.info.on, null, { timeout: 60000 });
    const spray = out.next.chapters.find((c) => c.id === "spray");
    const r0 = await page.evaluate(() => window.roof3d.info.renders);
    for (let k = 0; k < 4; k++) await page.evaluate(([t, d]) => window.roofStory.renderAt(t, d), [spray.t0 + 2 + k * 0.04, 0.04]);
    out.export = { ...(await snap(page)), renders: (await page.evaluate(() => window.roof3d.info.renders)) - r0,
      controlsShown: await page.locator("#stPlay").isVisible(), qualityShown: await page.locator("#v3quality").isVisible(),
      labelShown: await page.locator("#v3label").isVisible(), label: await page.locator("#v3label").innerText(),
      countersShown: await page.locator("#stCounters").isVisible(), captionShown: await page.locator("#stTitle").isVisible() };
    await page.waitForTimeout(600);
    out.export.rendersIdle = (await page.evaluate(() => window.roof3d.info.renders)) - r0 - out.export.renders;
    await page.close();
  } catch (e) {
    out.errors.push("check: " + e.message);
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(out));
})();
