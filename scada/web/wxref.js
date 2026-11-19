"use strict";
// Weather reference panel (wxref/, docs/weather-reference.md). The server adds this script to
// the page ONLY when config.WX_ENABLED is on; with the switch off the page is unchanged.
// Everything it draws from Open-Meteo is MODEL data and is labelled so; the PYR20 line is our
// measurement. Plain JavaScript, no library; data text goes in with textContent.
(function () {
  const TZ_S = 3 * 3600;                      // Makkah = UTC+3
  const OM_URL = "https://open-meteo.com/";
  const LICENCE_URL = "https://creativecommons.org/licenses/by/4.0/";
  const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const mk = (tag, text, cls) => {
    const e = document.createElement(tag);
    if (text !== undefined && text !== null) e.textContent = text;
    if (cls) e.className = cls;
    return e;
  };
  const fmt = (v, d) => (v === null || v === undefined ? "–" : Number(v).toFixed(d));

  const card = mk("article", null, "card");
  card.id = "wxPanel";
  const h2 = mk("h2", "Weather reference ");
  h2.appendChild(mk("small", "MODEL data (Open-Meteo), not a measurement"));
  card.appendChild(h2);
  const service = mk("p", "loading…", "note");
  const legend = mk("div", null, "legend");
  const key = (color, text, dashed) => {
    const s = mk("span");
    const i = mk("i", null, "key");
    i.style.background = dashed ? "transparent" : css(color);
    if (dashed) i.style.borderTop = `2px dashed ${css(color)}`;
    s.append(i, text);
    return s;
  };
  legend.append(key("--series-1", "PYR20, hourly mean (measured)"),
                key("--series-4", "model in-plane, hourly mean", true));
  const cIrr = mk("canvas"); cIrr.height = 150; cIrr.style.width = "100%"; cIrr.style.height = "150px";
  const windTitle = mk("p", "Model wind at 10 m (grid cell, not at the module), m/s", "note");
  const cWind = mk("canvas"); cWind.height = 70; cWind.style.width = "100%"; cWind.style.height = "70px";
  const checkHead = mk("h2", "Daily PYR20 check ");
  checkHead.style.marginTop = "12px";
  checkHead.appendChild(mk("small", "measured / model on clear-sky hours"));
  const checkLine = mk("p", "–");
  const checkMsg = mk("p", "", "note");
  const attrib = mk("p", null, "note");
  const a1 = mk("a", "Weather data by Open-Meteo.com"); a1.href = OM_URL; a1.rel = "noopener";
  const a2 = mk("a", "CC BY 4.0"); a2.href = LICENCE_URL; a2.rel = "noopener";
  attrib.append(a1, " (", a2, "); hourly values renamed wx_model_* and compared with the PYR20.");
  card.append(service, legend, cIrr, windTitle, cWind, checkHead, checkLine, checkMsg, attrib);
  (document.querySelector("aside.side") || document.body).appendChild(card);

  function chart(canvas, series, unit) {
    if (!canvas.dataset.h) canvas.dataset.h = canvas.height;   // CSS height, kept across redraws
    const dpr = window.devicePixelRatio || 1, w = canvas.clientWidth, h = +canvas.dataset.h;
    canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
    const g = canvas.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.font = "11px system-ui, 'Segoe UI', sans-serif";
    const L = 40, R = 6, T = 6, B = 16, pw = w - L - R, ph = h - T - B;
    let hi = 0;
    series.forEach((s) => s.v.forEach((v) => { if (v !== null && v > hi) hi = v; }));
    hi = hi > 0 ? hi * 1.1 : 1;
    const x = (hr) => L + (hr / 24) * pw, y = (v) => T + ph - (v / hi) * ph;
    g.strokeStyle = css("--grid"); g.fillStyle = css("--ink2"); g.lineWidth = 1;
    for (let k = 0; k <= 2; k++) {
      const v = (hi / 2) * k;
      g.beginPath(); g.moveTo(L, y(v)); g.lineTo(L + pw, y(v)); g.stroke();
      g.fillText(v.toFixed(v < 10 ? 1 : 0) + (k === 2 ? " " + unit : ""), 2, y(v) + 4);
    }
    for (let hr = 0; hr <= 24; hr += 6) g.fillText(String(hr).padStart(2, "0"), x(hr) - 6, h - 3);
    series.forEach((s) => {
      g.strokeStyle = css(s.color); g.lineWidth = 2; g.setLineDash(s.dashed ? [5, 4] : []);
      g.beginPath();
      let pen = false;
      s.t.forEach((t, k) => {
        const v = s.v[k];
        if (v === null) { pen = false; return; }
        const hr = (((t + TZ_S) % 86400) / 3600) || 24;     // hour ending at t, local
        const px = x(s.center ? hr - 0.5 : hr), py = y(v);
        if (pen) g.lineTo(px, py); else g.moveTo(px, py);
        pen = true;
      });
      g.stroke();
    });
    g.setLineDash([]);
  }

  function show(d) {
    if (!d.available) { service.textContent = d.hint || "not available"; return; }
    const s = d.service || {};
    const site = d.site;
    let txt = site ? `Site ${fmt(site.lat, 3)}, ${fmt(site.lon, 3)} (config placeholder until surveyed), tilt ${site.tilt_deg}°. `
      : "Site coordinates not set in config.py (WX_SITE_LAT / WX_SITE_LON): nothing fetched. ";
    if (s.state === "offline") txt += `Open-Meteo not reachable (${s.last_error || "?"}): showing cached model data; logging is not affected.`;
    else if (s.state === "ok") txt += "Model data up to date (fetched at most hourly).";
    else if (s.state) txt += `State: ${s.state}.`;
    service.textContent = txt;
    const t = d.today || { t: [] };
    chart(cIrr, [
      { t: t.t, v: t.measured_poa_wm2 || [], color: "--series-1", center: true },
      { t: t.t, v: t.wx_model_poa_wm2 || [], color: "--series-4", dashed: true, center: true },
    ], "W/m²");
    chart(cWind, [{ t: t.t, v: t.wx_model_wind_speed_10m_ms || [], color: "--series-3", dashed: true }], "m/s");
    const c = d.latest_check;
    checkLine.replaceChildren();
    if (!c) { checkLine.textContent = "No daily check yet (needs one complete logged day with model data)."; checkMsg.textContent = ""; return; }
    const pill = mk("span", c.status, "pill" + (c.status === "DRIFT" || c.status === "OUT_OF_RANGE" ? " bad"
      : c.status === "OK" ? "" : " warn"));
    checkLine.append(`${c.date}: `, pill,
      `  ratio ${fmt(c.pyr20_model_ratio, 3)}, rolling ${fmt(c.ratio_rolling, 3)}, reference ${fmt(c.reference_ratio, 3)}`
      + (c.drift_pct !== null ? `, ${c.drift_pct >= 0 ? "+" : ""}${fmt(c.drift_pct, 1)} %` : "")
      + (c.trend_pct_per_week !== null ? `, trend ${fmt(c.trend_pct_per_week, 2)} %/week` : "")
      + `, ${c.clear_hours} clear h (band ±${fmt(d.band_pct, 0)} %)`);
    checkMsg.textContent = c.message || "";
  }

  async function refresh() {
    try {
      const r = await fetch("/api/wx", { cache: "no-store" });
      if (!r.ok) throw new Error(`/api/wx: ${r.status}`);
      show(await r.json());
    } catch (e) {
      service.textContent = `Weather reference: ${e.message}`;
    }
  }
  refresh();
  setInterval(refresh, 60000);
  window.addEventListener("resize", refresh);
})();
