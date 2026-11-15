// Drives the real app against demo_server.py at phone and laptop size, in both looks, and saves screenshots.
// Usage: node check.cjs <output-folder> [base-url]      (needs Playwright; see README.md)
const { chromium } = require("playwright");
const out = process.argv[2];
const base = process.argv[3] || "http://127.0.0.1:8912";
if (!out) { console.error("usage: node check.cjs <output-folder> [base-url]"); process.exit(2); }

const PHONE = { viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true };
const SIZES = [["phone-dusk", { ...PHONE, colorScheme: "dark" }], ["phone-day", { ...PHONE, colorScheme: "light" }],
               ["laptop-dusk", { viewport: { width: 1280, height: 800 }, deviceScaleFactor: 2, colorScheme: "dark" }]];
const FINGER = 44;      // the smallest side of anything meant to be tapped, in px

(async () => {
  // APP_CHECK_CHROME: a Chrome or Chromium to use instead of Playwright's own download.
  const browser = await chromium.launch(process.env.APP_CHECK_CHROME ? { executablePath: process.env.APP_CHECK_CHROME } : {});
  const failures = [], hosts = new Set(), timings = {};
  for (const [name, options] of SIZES) {
    const phone = name.startsWith("phone");
    const expect = (ok, what) => { if (!ok) failures.push(`${name}: ${what}`); };
    const ctx = await browser.newContext(options);
    const page = await ctx.newPage();
    let quiet = false;      // while the check itself cuts the line, failed requests are expected
    page.on("console", (m) => m.type() === "error" && !quiet && failures.push(`${name}: console: ${m.text()} ${m.location()?.url || ""}`));
    page.on("pageerror", (e) => failures.push(`${name}: page error: ${e}`));
    page.on("request", (r) => hosts.add(new URL(r.url()).host));
    page.on("response", (r) => r.status() >= 400 && !quiet && failures.push(`${name}: ${r.status()} ${new URL(r.url()).pathname}`));
    const shot = (n) => page.screenshot({ path: `${out}/${name}-${n}.png` });
    const stage = (what) => page.evaluate((w) => fetch("/demo/" + w, { method: "POST" }).then((r) => r.ok), what);
    const says = async () => (await page.textContent(".say")).trim();
    const sub = async () => (await page.textContent(".sub")).trim();
    const tap = (sel) => { const el = page.locator(sel).locator("visible=true").first(); return phone ? el.tap() : el.click(); };
    const until = (fn, arg, ms = 6000) => page.waitForFunction(fn, arg, { timeout: ms }).then(() => true, () => false);
    const saysSoon = (text, ms) => until((t) => document.querySelector(".say")?.textContent.trim() === t, text, ms);
    const room = (id) => `.main .room[data-room="${id}"]`;

    // ---- opening ----
    await page.goto(base + "/");
    expect(new URL(page.url()).pathname === "/app/", "Hub's front door did not lead to the app");
    await page.waitForSelector(".plan");
    await stage("reset");
    await page.reload(); await page.waitForSelector(".plan"); await page.waitForTimeout(2000);      // the opening moment is over
    await shot("1-home");
    expect(await says() === "Five rooms are lit.", "the house sentence: " + await says());
    expect(await sub() === "“Paper Lanterns” is playing in the kitchen and the living room.", "the music sentence: " + await sub());
    expect(await page.locator(".main .room").count() === 12, "twelve rooms on the plan");
    expect((await page.locator(".floor").allInnerTexts()).join("|") === "Downstairs|Upstairs|Outside", "three floors, ground floor first");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "the page scrolls sideways");
    expect(await page.evaluate(() => document.fonts.check('500 17px "Jost"')), "the typeface did not load");
    const small = () => page.evaluate((least) => [...document.querySelectorAll("button, a, input, [role=switch], [role=button]")]
      .map((el) => [el, el.getBoundingClientRect()]).filter(([el, r]) => r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== "hidden")
      .filter(([, r]) => Math.min(r.width, r.height) < least)
      .map(([el, r]) => `${(el.getAttribute("aria-label") || el.textContent || el.className).trim().slice(0, 30)} ${Math.round(r.width)}x${Math.round(r.height)}`), FINGER);
    if (phone) expect((await small()).length === 0, "tap targets under 44 px on Home: " + (await small()).join(", "));

    // ---- a tap shows at once, before Hub has answered ----
    let slow = true;
    await page.route("**/app/api/room/**", async (route) => { if (slow) await new Promise((r) => setTimeout(r, 900)); await route.continue(); });
    const before = Date.now();
    await tap(room("office"));
    expect(await saysSoon("Six rooms are lit.", 400), "a tap did not show within 0.4 s while Hub was still answering");
    timings[name] = Date.now() - before;
    slow = false;
    await page.waitForSelector(".toast"); await shot("2-tapped");
    expect((await page.textContent(".toast span")).trim() === "Office on", "the toast: " + await page.textContent(".toast"));
    await page.waitForTimeout(1500);
    expect(await says() === "Six rooms are lit.", "the tap did not hold once the house answered");
    await tap(".toast button");
    expect(await saysSoon("Five rooms are lit."), "Undo did not put the room back");

    // ---- a tap that didn't take is put right, and says why ----
    await page.unroute("**/app/api/room/**");
    await page.route("**/app/api/room/**", (route) => route.fulfill({ status: 409, contentType: "application/json", body: '{"error":"Home Assistant couldn’t be reached just now."}' }));
    quiet = true;
    await tap(room("office"));
    expect(await saysSoon("Six rooms are lit.", 400), "the tap did not show");
    expect(await saysSoon("Five rooms are lit.", 3000), "a refused tap was left showing as done");
    expect((await page.textContent(".toast span")).includes("couldn’t be reached"), "a refused tap did not say why");
    await page.unroute("**/app/api/room/**"); quiet = false;
    await page.waitForTimeout(400);

    // ---- another device sees it without asking ----
    const other = await ctx.newPage();
    await other.goto(base + "/app/"); await other.waitForSelector(".plan");
    await tap(room("entry"));
    expect(await other.waitForFunction(() => document.querySelector(".say")?.textContent.trim() === "Six rooms are lit.", null, { timeout: 6000 }).then(() => true, () => false),
      "a second open app did not hear about the change within 6 s");
    await other.close();
    await tap(room("entry")); expect(await saysSoon("Five rooms are lit."), "the entry did not switch off again");
    await page.waitForTimeout(600);

    // ---- hold a room: everything in it ----
    const box = await page.locator(room("kitchen")).boundingBox();
    if (phone) {
      const cdp = await ctx.newCDPSession(page);
      const point = { x: box.x + 40, y: box.y + 40 };
      await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [point] });
      await page.waitForTimeout(650);
      await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    } else {
      await page.mouse.move(box.x + 40, box.y + 40); await page.mouse.down(); await page.waitForTimeout(650); await page.mouse.up();
    }
    await page.waitForSelector(".sheet.in"); await page.waitForTimeout(450); await shot("3-room");
    expect((await page.textContent(".sheet h1")).trim() === "Kitchen", "holding a room did not open it");
    expect(await says() === "Five rooms are lit.", "holding a room also switched it");
    if (phone) expect((await small()).length === 0, "tap targets under 44 px in a room: " + (await small()).join(", "));
    await page.locator('.sheet .dimmer input').fill("30");
    await page.locator('.sheet .dimmer input').dispatchEvent("change");
    expect(await until((sel) => document.querySelector(sel + " .room-num")?.textContent.trim() === "30%", room("kitchen")), "the dimmer did not dim the room");
    await page.locator('.sheet [aria-label="Coffee maker"]').click();
    expect(await until(() => document.querySelector('.sheet [aria-label="Coffee maker"]')?.getAttribute("aria-checked") === "true"), "the plug did not switch on");
    await page.keyboard.press("Escape");
    expect(await until(() => !document.querySelector(".sheet")), "Escape did not put the sheet away");

    // ---- a scene, and all off ----
    await tap('.scene:text-is("Movie night")');
    expect(await saysSoon("Three rooms are lit."), "the scene did not run: " + await says());
    await page.waitForTimeout(500);
    await tap('.scene:text-is("All off")');
    expect(await saysSoon("The house is dark.", 400), "All off did not show at once");
    await page.waitForTimeout(1200); await shot("4-dark");
    expect(await says() === "The house is dark.", "All off did not hold");
    await tap(".toast button");
    expect(await saysSoon("Three rooms are lit."), "Undo did not bring the lights back");

    // ---- music ----
    await tap(".np-play");
    expect(await until(() => document.querySelector(".sub")?.textContent.trim() === "The house is quiet.", null, 400), "Pause did not show at once");
    await page.waitForTimeout(1500);
    expect(await sub() === "The house is quiet.", "Pause did not hold");
    await tap(".np-play");
    expect(await until(() => document.querySelector(".sub")?.textContent.includes("is playing")), "Play did not start it again");
    await page.locator(".np").click({ position: { x: 120, y: 20 } });
    await page.waitForSelector(".sheet.in"); await page.waitForSelector(".pick"); await page.waitForTimeout(450); await shot("5-music");
    if (phone) expect((await small()).length === 0, "tap targets under 44 px in the music sheet: " + (await small()).join(", "));
    await page.locator('.sheet [aria-label="Dining speaker"]').click();
    expect(await until(() => document.querySelector(".sub")?.textContent.includes("the dining")), "a room did not join the music: " + await sub());
    await page.waitForTimeout(1200);
    expect((await sub()).includes("the dining"), "the joined room did not hold");
    await page.locator('.pick:has-text("Sunday Morning")').click();
    expect(await until(() => document.querySelector(".toast span")?.textContent.trim() === "Playing Sunday Morning"), "a pick did not play");
    await page.keyboard.press("Escape"); await page.waitForTimeout(400);

    // ---- Ask: a few letters reach a room ----
    if (phone) { await tap(".askbtn"); await page.waitForSelector(".sheet.in"); }
    await page.fill("#askq", "liv"); await page.waitForTimeout(250); await shot("6-ask");
    expect((await page.locator(".sug").first().innerText()).startsWith("Living room"), "typing did not find the room");
    await page.locator('.sug [role="switch"]').first().click();
    expect(await until((sel) => document.querySelector(sel)?.getAttribute("aria-checked") === "false", room("living-room")), "the switch in the list did not switch the room");
    await page.fill("#askq", "");
    if (phone) { await page.keyboard.press("Escape"); await page.waitForTimeout(400); }

    // ---- System, and the look ----
    await tap(phone ? ".who" : ".rail .foot .tab");
    expect(await saysSoon("The house is answering."), "System: " + await says());
    await page.waitForTimeout(300); await shot("7-system");
    if (phone) expect((await small()).length === 0, "tap targets under 44 px on System: " + (await small()).join(", "));
    await tap('.choice:text-is("Day")');
    expect(await page.evaluate(() => document.documentElement.dataset.theme) === "day", "choosing Day did not change the look");
    await tap('.choice:text-is("Automatic")');
    expect(await page.evaluate(() => document.documentElement.dataset.theme) === (options.colorScheme === "dark" ? "dusk" : "day"), "Automatic did not follow the device");
    await tap(phone ? '.tabs .tab:text-is("Home")' : '.rail .tab:text-is("Home")');
    await page.waitForSelector(".plan");

    // ---- when the house stops answering, and when Hub can't be reached ----
    quiet = true;
    await stage("down");
    expect(await until(() => document.querySelector(".banner b")?.textContent === "The house isn’t answering", null, 8000), "no word that the house stopped answering");
    await page.waitForTimeout(300); await shot("8-house-down");
    expect(await page.locator(".main .room").count() === 12, "the last picture was not kept while the house was down");
    await stage("up");
    expect(await until(() => !document.querySelector(".banner"), null, 8000), "the banner stayed after the house came back");
    await ctx.setOffline(true);
    expect(await until(() => document.querySelector(".banner b")?.textContent === "Can’t reach Hub", null, 12000), "no word that Hub can’t be reached");
    await page.waitForTimeout(300); await shot("9-hub-away");
    await page.reload().catch(() => undefined);
    expect(await page.waitForSelector(".plan", { timeout: 6000 }).then(() => true, () => false), "the app did not open without Hub (its files were not kept on the device)");
    await ctx.setOffline(false);
    expect(await until(() => !document.querySelector(".banner"), null, 15000), "the app did not reconnect by itself");
    quiet = false;
    await stage("reset");
    await ctx.close();
  }
  await browser.close();
  const elsewhere = [...hosts].filter((h) => h !== new URL(base).host);
  if (elsewhere.length) failures.push("the app talked to another host: " + elsewhere.join(", "));
  console.log(JSON.stringify({ ok: failures.length === 0, tap_shows_in_ms: timings, failures }, null, 2));
  process.exit(failures.length ? 1 : 0);
})();
