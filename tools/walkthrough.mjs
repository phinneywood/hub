// Look-only usability walkthrough of live Hub at phone and laptop size.
// Every request that is not a GET is aborted, except the one Ask question the walkthrough types on purpose,
// so nothing in the house, the kitchen or the mail can change. It taps only menus, tabs, sheets and folds.
// The deployer runs it after each deploy with SKIP_ASK set (home-ops docs/HUB-DEPLOYER.md); it never gates one.
// What it saves (screenshots of the real household) stays in the out folder on the Mac, never in Git.
//
// Usage: node tools/walkthrough.mjs <out-folder> <hub-url>
//   PW_DIR   folder holding node_modules/playwright-core (kept outside any backed-up folder: it holds symlinks)
//   ONLY     comma-separated journeys to run (ask, house, dashboards, Today, Mail, Kitchen, system)
//   SKIP_ASK set: type the question but don't send it (keeps the household's conversation clean on a rerun)
//   CHROME   Chrome to drive (default: /Applications/Google Chrome.app)
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";

const require = createRequire(path.join(process.env.PW_DIR || process.cwd(), "package.json"));
const { chromium } = require("playwright-core");
const OUT = process.argv[2];
const BASE = (process.argv[3] || process.env.HUB_URL || "").replace(/\/$/, "");
const CHROME = process.env.CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
if (!OUT || !BASE) { console.error("usage: node tools/walkthrough.mjs <out-folder> <hub-url>"); process.exit(2); }
fs.mkdirSync(OUT, { recursive: true });

const ONLY = process.env.ONLY ? process.env.ONLY.split(",") : null;      // e.g. ONLY=dashboards,Today
const QUESTION = "what's on today?";
const VIEWPORTS = [
  ["phone", { viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true }],
  ["laptop", { viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 }],
];
// Never tapped, whatever a journey says: they act on the house, the kitchen, mail or settings.
const NEVER_CLASS = /\b(chip|scene|np-play|forget|choice|fold-undo|send|prompt|switch|tickbtn|undo)\b/;
const NEVER_TEXT = /^(All off|Keep the house|Place order|Fill the cart|Send|Undo|Delete|Restart|Pause|Deploy|Forget|Archive|Run|Mark|Add)/i;

const report = { base: BASE, started: new Date().toISOString(), screens: [], blocked: [], taps: {} };
const browser = await chromium.launch({ executablePath: CHROME, headless: true });

for (const [vp, options] of VIEWPORTS) {
  const phone = vp === "phone";
  const ctx = await browser.newContext({ ...options, ignoreHTTPSErrors: true, timezoneId: "America/Los_Angeles", colorScheme: "light" });
  const page = await ctx.newPage();
  let allowAsk = false, seq = 0, taps = 0;
  let errors = [], failed = [];
  await page.route("**/*", (route) => {
    const r = route.request(), u = new URL(r.url());
    if (r.method() === "GET" || r.method() === "HEAD") return route.continue();
    if (allowAsk && r.method() === "POST" && u.pathname === "/app/api/ask") { allowAsk = false; return route.continue(); }
    report.blocked.push({ vp, method: r.method(), path: u.pathname });
    return route.abort();
  });
  page.on("console", (m) => m.type() === "error" && errors.push(m.text().slice(0, 300)));
  page.on("pageerror", (e) => errors.push("page error: " + String(e).slice(0, 300)));
  page.on("requestfailed", (r) => { const f = r.failure()?.errorText || ""; if (!/ERR_ABORTED/.test(f) || r.method() === "GET") failed.push(`${r.method()} ${new URL(r.url()).pathname} ${f}`); });
  page.on("response", (r) => r.status() >= 400 && failed.push(`${r.status()} ${r.request().method()} ${new URL(r.url()).pathname}`));

  const wait = (ms) => page.waitForTimeout(ms);
  const tapEl = async (loc, why) => {
    const el = loc.first();
    await el.waitFor({ state: "visible", timeout: 8000 });
    const cls = (await el.getAttribute("class")) || "", txt = ((await el.innerText().catch(() => "")) || "").trim();
    if (NEVER_CLASS.test(cls) || NEVER_TEXT.test(txt)) throw new Error(`refused to tap "${txt}" .${cls} (${why})`);
    await el.scrollIntoViewIfNeeded().catch(() => {});
    phone ? await el.tap() : await el.click();
    taps++;
    await wait(1200);
  };
  const tryTap = async (loc, why) => { try { await tapEl(loc, why); return true; } catch (e) { console.log(vp, "skip:", why, String(e.message).split("\n")[0]); return false; } };
  const home = async () => { await page.goto(BASE + "/app/"); await page.waitForSelector(".askpage", { timeout: 20000 }).catch(() => {}); await wait(6000); taps = 0; };
  const tab = (name) => page.locator(phone ? ".dock .tabs .tab" : ".rail .tab").filter({ hasText: new RegExp(`^${name}$`) });
  // Ask's few-letter search, the documented way to reach Today, Mail and Kitchen (they have no tab).
  const byLetters = async (name, desc) => {
    if (!(await page.locator("#askq").isVisible().catch(() => false))) await tapEl(tab("Ask"), "back to Ask");
    await page.locator("#askq").fill(name.slice(0, 4)); taps++;
    await wait(600);
    const sug = page.locator(".found button.sug").filter({ hasText: desc || name });
    if (!(await sug.count())) { await page.locator("#askq").fill(""); throw new Error("no page suggestion for " + name); }
    await tapEl(sug, "open " + name);
  };

  // Audit what is on screen now: overflow, things under the fixed bars, small targets and small text.
  const audit = () => page.evaluate(({ phone }) => {
    // Inside a closed fold only the fold's own opener can be seen; the rest keeps a size but isn't drawn.
    const folded = (el) => { for (let d = el.closest("details:not([open])"); d; d = d.parentElement?.closest("details:not([open])")) { if (el.closest("summary")?.parentElement !== d) return true; } return false; };
    const vis = (el) => { const r = el.getBoundingClientRect(), s = getComputedStyle(el); return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none" && +s.opacity > 0 && !folded(el); };
    const label = (el) => (el.getAttribute("aria-label") || el.innerText || el.className || el.tagName).toString().trim().replace(/\s+/g, " ").slice(0, 50);
    const sideScroller = (el) => { for (let p = el.parentElement; p; p = p.parentElement) { const s = getComputedStyle(p); if (/(auto|scroll|hidden|clip)/.test(s.overflowX) && p !== document.body && p !== document.documentElement) return true; } return false; };
    const W = innerWidth, H = innerHeight;
    // Wider than the screen only matters where a finger can scroll sideways (the wall clips its few pixels of drift).
    const sideways = ![document.documentElement, document.body].some((e) => getComputedStyle(e).overflowX === "hidden");
    const out = { overflow: sideways && document.documentElement.scrollWidth > W + 1 ? `page is ${document.documentElement.scrollWidth}px wide in ${W}px` : "", wide: [], small: [], tiny: [], under: [] };
    for (const el of document.querySelectorAll("body *")) {
      if (!vis(el)) continue;
      const r = el.getBoundingClientRect();
      if (sideways && r.right > W + 1 && !sideScroller(el) && out.wide.length < 8) out.wide.push(`${label(el)} right=${Math.round(r.right)}`);
    }
    const targets = [...document.querySelectorAll("button, a[href], input, select, textarea, [role=button], [role=switch], [role=radio], summary")].filter(vis);
    // Under a bar only counts when no scroll brings it clear: it is pinned itself, or its scroller has run out of room.
    // What an open sheet or its scrim covers is meant to be covered.
    const pinned = (el) => { for (let q = el; q; q = q.parentElement) if (/(fixed|sticky)/.test(getComputedStyle(q).position)) return true; return false; };
    const scroller = (el) => { for (let q = el.parentElement; q && q !== document.body; q = q.parentElement) { if (/(auto|scroll)/.test(getComputedStyle(q).overflowY) && q.scrollHeight > q.clientHeight + 1) return q; } return document.scrollingElement; };
    for (const el of targets) {
      // A box or a dot inside its label is tapped through the whole label.
      const r = (el.tagName === "INPUT" && el.closest("label") || el).getBoundingClientRect();
      if (phone && Math.min(r.width, r.height) < 44 && out.small.length < 25) out.small.push(`${label(el)} ${Math.round(r.width)}x${Math.round(r.height)}`);
      if (r.top >= 0 && r.bottom <= H && r.left >= 0 && r.right <= W) {
        const cx = r.left + r.width / 2, cy = r.top + r.height / 2, hit = document.elementFromPoint(cx, cy);
        if (hit && !el.contains(hit) && !hit.contains(el)) {
          let p = hit, fixed = false; for (; p; p = p.parentElement) if (/(fixed|sticky)/.test(getComputedStyle(p).position)) { fixed = true; break; }
          if (!fixed || hit.closest('[aria-modal="true"]')) continue;
          const b = p.getBoundingClientRect();
          if (b.width >= W * 0.9 && b.height >= H * 0.9) continue;
          const box = scroller(el), below = b.top + b.height / 2 > H / 2;
          // Scrolled out of its own box (Ask's conversation, say) it is out of sight, not under the bar beside the box.
          const seen = box === document.scrollingElement ? null : box.getBoundingClientRect();
          if (seen && (cy < seen.top || cy > seen.bottom)) continue;
          const room = below ? box.scrollHeight - box.clientHeight - box.scrollTop : box.scrollTop;
          const need = below ? r.bottom - b.top : b.bottom - r.top;
          if ((pinned(el) || need > room) && out.under.length < 10) out.under.push(`${label(el)} under ${label(p)}`);
        }
      }
    }
    const seen = new Set();
    for (const el of document.querySelectorAll("body *")) {
      if (!vis(el) || ![...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim())) continue;
      const fs = parseFloat(getComputedStyle(el).fontSize);
      if (fs < 12) { const t = `${Math.round(fs * 10) / 10}px "${el.innerText.trim().slice(0, 40)}"`; if (!seen.has(t) && out.tiny.length < 15) { seen.add(t); out.tiny.push(t); } }
    }
    out.title = (document.querySelector("h1")?.innerText || document.title || "").trim().slice(0, 80);
    out.back = !!document.querySelector(".tabs .tab, .rail .tab, a.back, button.back, [aria-label*=Back i], .sheet-close, [aria-label*=Close i]");
    return out;
  }, { phone });

  // Screenshot the visible viewport and the full page; if the page scrolls inside a panel, step through that too.
  const shot = async (name, note = "") => {
    await wait(500);
    const id = `${vp}-${String(++seq).padStart(2, "0")}-${name}`;
    const files = [`${id}.png`, `${id}-full.png`];
    await page.screenshot({ path: path.join(OUT, files[0]) });
    await page.screenshot({ path: path.join(OUT, files[1]), fullPage: true });
    const inner = await page.evaluate(() => {
      let best = null;
      for (const el of document.querySelectorAll("body *")) {
        const s = getComputedStyle(el);
        if (/(auto|scroll)/.test(s.overflowY) && el.scrollHeight > el.clientHeight + 40 && el.clientHeight > 200 && (!best || el.clientHeight * el.clientWidth > best.clientHeight * best.clientWidth)) best = el;
      }
      if (!best) return 0;
      best.setAttribute("data-walk-scroller", "1");
      return Math.min(5, Math.ceil(best.scrollHeight / best.clientHeight));
    });
    if (inner > 1) {
      const start = await page.evaluate(() => document.querySelector("[data-walk-scroller]").scrollTop);
      for (let i = 0; i < inner; i++) {
        await page.evaluate((i) => { const el = document.querySelector("[data-walk-scroller]"); el.scrollTop = i * el.clientHeight * 0.9; }, i);
        await wait(300);
        const f = `${id}-scroll${i + 1}.png`; files.push(f);
        await page.screenshot({ path: path.join(OUT, f) });
      }
      await page.evaluate((s) => { const el = document.querySelector("[data-walk-scroller]"); el.scrollTop = s; el.removeAttribute("data-walk-scroller"); }, start);
    }
    const a = await audit();
    report.screens.push({ vp, id, name, note, url: page.url().replace(BASE, ""), taps, files, ...a, errors, failed });
    console.log(vp, id, `taps=${taps}`, a.overflow, `small=${a.small.length} tiny=${a.tiny.length} under=${a.under.length} wide=${a.wide.length} err=${errors.length} fail=${failed.length}`);
    errors = []; failed = [];
  };
  const journey = (name, fn) => (ONLY && !ONLY.includes(name) ? Promise.resolve() : step(name, fn));
  const step = async (name, fn) => { try { await fn(); } catch (e) { console.log(vp, "step failed:", name, String(e.message).split("\n")[0]); report.screens.push({ vp, id: `${vp}-xx-${name}`, name, failedStep: String(e.message).split("\n")[0] }); } };

  // ---- Ask ----
  await journey("ask", async () => {
    await home();
    await shot("ask-landing", "first open, before touching anything");
    if (await tryTap(page.locator("button.fold").filter({ hasText: /Good morning/ }), "open the morning message")) await shot("ask-morning-open", "the morning message unfolded");
    if (await tryTap(page.locator("button.fold-more"), "earlier messages")) await shot("ask-earlier", "after Show earlier messages");
    await page.locator("#askq").scrollIntoViewIfNeeded();
    await page.locator("#askq").fill(QUESTION);
    await shot("ask-typed", "question typed, not sent");
    if (process.env.SKIP_ASK) { await page.locator("#askq").fill(""); return; }      // a rerun that leaves the conversation alone
    allowAsk = true;
    await page.locator("#askq").press("Enter"); taps++;
    await wait(2500);
    await shot("ask-thinking", "while Hub answers");
    await page.waitForFunction(() => !document.querySelector(".askpage .busy, .askpage [aria-busy=true], .doing"), null, { timeout: 90000 }).catch(() => {});
    await wait(4000);
    allowAsk = false;
    await shot("ask-reply", "the answer to " + QUESTION);
    await page.evaluate(() => { for (const el of document.querySelectorAll("*")) if (el.scrollHeight > el.clientHeight + 40 && /(auto|scroll)/.test(getComputedStyle(el).overflowY)) el.scrollTop = 0; window.scrollTo(0, 0); });
    await shot("ask-scrolled-back", "scrolled back to the top of the conversation");
  });

  // ---- House ----
  await journey("house", async () => {
    await home();
    await tapEl(tab("House"), "House tab");
    await shot("house", "House tab");
    const more = page.locator(".room.lit .room-more, .room-more");
    if (await tryTap(more, "open a room without switching it")) {
      await shot("house-room-sheet", "a room opened with its own button (not toggled)");
      const light = page.locator(".sheet button, .sheet [role=button]").filter({ hasText: /lamp|light|ceiling/i });
      await page.keyboard.press("Escape"); await wait(800);
    }
    const locks = page.getByText(/lock|garage|door/i).first();
    if (await locks.count()) { await locks.scrollIntoViewIfNeeded().catch(() => {}); await shot("house-locks", "scrolled to locks / doors"); }
  });

  // ---- Dashboards ----
  await journey("dashboards", async () => {
    await home();
    const way = phone ? tab("Dashboards") : page.locator(".rail .rlink.head");
    await tapEl(way, "Dashboards from home");
    await shot("dashboards", "the list of dashboards");
    const names = await page.evaluate(() => [...document.querySelectorAll(".rlink:not(.head), .boards button, .boards a, .board-list button, main button.row, .rows button")]
      .map((e) => e.innerText.trim().split("\n")[0]).filter(Boolean));
    const wanted = [...new Set(names)].slice(0, 12);      // every dashboard the list offers, as a person sees it
    // Each board from home the way a person goes: the Dashboards tab (phone) or the left column (laptop), then its row.
    for (const n of wanted) {
      await step("board-" + n, async () => {
        await home();
        if (phone) {
          await tapEl(tab("Dashboards"), "Dashboards tab");
          const rows = page.locator(".wrap button.row");
          const at = (await rows.allInnerTexts()).findIndex((t) => t.split("\n")[0].trim() === n);
          if (at < 0) throw new Error("no dashboard row " + n);
          await tapEl(rows.nth(at), "board " + n);
        } else await tapEl(page.locator(".rlink").filter({ hasText: new RegExp("^" + n + "$") }), "board " + n);
        await wait(2500);
        await shot("board-" + n.toLowerCase().replace(/\s+/g, "-"), `dashboard ${n} (seen in list: ${names.length})`);
      });
    }
  });

  // ---- Today, Errands, Mail, Kitchen (no tab: reached by Ask's letters) ----
  for (const [name, desc] of [["Today", "The day, what needs you"], ["Mail", "What needs you and what Hub sorted"], ["Kitchen", "The shopping list"]]) {
    await journey(name, async () => {
      await home();
      // Does the home screen offer a visible way there without typing?
      const visible = await page.locator("button, a").filter({ hasText: new RegExp(`^(Open )?${name}$`) }).locator("visible=true").count();
      report.taps[`${vp}:${name}:visible-link-on-home`] = visible;
      await byLetters(name, desc);
      await wait(2500);
      await shot(name.toLowerCase(), `reached by typing "${name.slice(0, 4)}" in Ask${visible ? "" : " (no visible link on home)"}`);
      if (name === "Today") {
        if (await tryTap(page.locator("a").filter({ hasText: /^All errands$/ }), "Errands from Today")) await shot("errands", "Errands");
      }
      if (name === "Kitchen") {
        const item = page.locator("button.row.item");
        if (await tryTap(item, "open one thing on the list")) { await shot("kitchen-item", "one thing on the list: why this product"); await page.keyboard.press("Escape"); await wait(800); }
      }
      if (name === "Mail") {
        if (await tryTap(page.locator("button").filter({ hasText: /^Rules and settings$/ }), "Mail settings")) await shot("mail-settings", "Mail settings");
      }
    });
  }

  // ---- System and everything in its menu ----
  await journey("system", async () => {
    await home();
    await tapEl(phone ? page.locator("button.who") : page.locator(".rail .foot .tab"), "System");
    await shot("system", "System");
    const rows = ["House health", "Dashboards", "Activity", "Versions", "Engine"];
    for (const r of rows) {
      await step("system-" + r, async () => {
        await page.goto(BASE + "/app/#system"); await wait(2000); taps = 1;
        await tapEl(page.locator(".wrap button.row").filter({ hasText: new RegExp("^" + r) }), r);
        await wait(2500);
        await shot("system-" + r.toLowerCase().replace(/\s+/g, "-"), r + " from System");
      });
    }
    await step("system-wall", async () => {
      await page.goto(BASE + "/app/#system"); await wait(2000); taps = 1;
      await tapEl(page.locator("a.row").filter({ hasText: /^The wall/ }), "The wall");
      await wait(4000);
      await shot("system-the-wall", "The wall, opened from System");
    });
    // The other links in System's menu: checks and older pages (GET only; nothing on them is tapped).
    await page.goto(BASE + "/app/#system"); await wait(2000);
    const links = await page.evaluate(() => [...document.querySelectorAll(".wrap a.row")].map((a) => [a.innerText.split("\n")[0].trim(), a.getAttribute("href")]).filter(([, h]) => h && h.startsWith("/")));
    for (const [n, href] of links) {
      await step("older-" + n, async () => {
        taps = 2;
        await page.goto(BASE + href); await wait(3500);
        await shot("system-link-" + n.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/-$/, ""), `${n} (${href}) from System`);
      });
    }
  });
  await ctx.close();
}
await browser.close();
report.finished = new Date().toISOString();
fs.writeFileSync(path.join(OUT, "audit.json"), JSON.stringify(report, null, 2));
// The findings in short, for whoever looks next: what broke, what sits under a bar, what is too small to tap.
const flagged = report.screens.filter((s) => s.failedStep || s.overflow || s.under?.length || s.errors?.length || s.failed?.length || s.small?.length);
const lines = [`Hub walkthrough ${report.started} to ${report.finished}: ${report.screens.length} screens, ${flagged.length} with something to look at; ${report.blocked.length} writes blocked.`, ""];
for (const s of flagged) {
  const said = [s.failedStep && `step failed: ${s.failedStep}`, s.overflow, s.under?.length && `${s.under.length} under a bar`,
    s.small?.length && `${s.small.length} under 44 px`, s.errors?.length && `${s.errors.length} console errors`, s.failed?.length && `${s.failed.length} failed requests`];
  lines.push(`- ${s.vp} ${s.name}: ${said.filter(Boolean).join("; ")}`);
}
fs.writeFileSync(path.join(OUT, "summary.md"), lines.join("\n") + "\n");
console.log("blocked writes:", report.blocked.length, JSON.stringify(report.blocked.slice(0, 20)));
