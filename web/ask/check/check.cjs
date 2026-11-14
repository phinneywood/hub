// Drives the real Ask page at phone width against demo_server.py and saves screenshots.
// Usage: node check.cjs <output-folder> [base-url]      (needs Playwright; see README.md)
const { chromium } = require("playwright");
const out = process.argv[2];
const base = process.argv[3] || "http://127.0.0.1:8792";
if (!out) { console.error("usage: node check.cjs <output-folder> [base-url]"); process.exit(2); }

(async () => {
  // ASK_CHECK_CHROME: a Chrome or Chromium to use instead of Playwright's own download.
  const browser = await chromium.launch(process.env.ASK_CHECK_CHROME ? { executablePath: process.env.ASK_CHECK_CHROME } : {});
  const failures = [], hosts = new Set();
  const expect = (ok, what) => { if (!ok) failures.push(what); };
  for (const scheme of ["light", "dark"]) {
    const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true, colorScheme: scheme });
    const page = await ctx.newPage();
    page.on("console", (m) => m.type() === "error" && !/\/favicon\.ico$/.test(m.location()?.url || "") && failures.push("console: " + m.text() + " " + (m.location()?.url || "")));
    page.on("pageerror", (e) => failures.push("page error: " + e));
    page.on("request", (r) => hosts.add(new URL(r.url()).host));
    page.on("response", (r) => r.status() >= 400 && failures.push(`${r.status()} ${new URL(r.url()).pathname}`));
    const shot = (name) => page.screenshot({ path: `${out}/${scheme}-${name}.png` });
    const idle = async () => { await page.waitForSelector(".ask-send:not(.stop)"); await page.waitForTimeout(700); };
    const say = async (text) => { await page.fill(".ask-input", text); await page.click(".ask-send"); };

    await page.goto(base + "/ask");
    await page.waitForSelector(".ask-empty"); await shot("1-empty");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "the page scrolls sideways");
    const fits = () => page.evaluate(() => {
      const box = document.querySelector(".ask-composer").getBoundingClientRect(), bar = document.querySelector(".ask-bar").getBoundingClientRect();
      const head = document.querySelector("header.shell").getBoundingClientRect();
      return box.bottom <= innerHeight && bar.top >= head.bottom - 1 && document.documentElement.scrollHeight <= innerHeight;
    });
    expect(await fits(), "the composer or the chat bar is off screen when empty");
    await say("What needs me today?");
    await page.waitForSelector(".ask-md"); await page.waitForTimeout(250); await shot("2-streaming");
    expect(!(await page.evaluate(() => document.body.classList.contains("loading"))), "sending dimmed the page");
    await idle(); await shot("3-answer");
    expect(await fits(), "the composer or the chat bar is off screen after an answer");
    expect((await page.locator(".ask-chip").count()) === 2, "two lookup chips");
    expect((await page.locator(".ask-sources a").allInnerTexts()).join("|").includes("Calendar"), "citations to Hub pages");
    await page.click(".ask-chip >> nth=1"); await page.waitForSelector(".ask-tool-body"); await shot("4-lookup-open");
    expect((await page.locator(".ask-tool-body").innerText()).includes("Sent to the model"), "the card says what the model saw");
    await page.click(".ask-chip >> nth=1");
    await say("Which emails need me?"); await idle(); await shot("5-mail");
    await say("Please file my sorted mail"); await idle(); await shot("6-read-only");
    expect(await fits(), "the composer or the chat bar is off screen in a long thread");
    expect((await page.locator(".ask-msg.hub").last().innerText()).includes("read-only"), "a change request is declined");
    await page.reload(); await page.waitForSelector(".ask-sources"); await page.waitForTimeout(500); await shot("7-after-reload");
    expect((await page.locator(".ask-msg.user").count()) === 3, "the thread comes back after a reload");
    expect((await page.locator(".ask-sources").count()) === 2, "citations come back after a reload");
    await page.click('.ask-pill:has-text("Chats")'); await page.waitForSelector(".ask-sheet"); await page.waitForTimeout(300); await shot("8-chats");
    expect((await page.locator(".ask-open").count()) >= 1, "the saved chat is listed");
    await ctx.close();
  }
  {
    // Signed in, as through Tailscale: a note proposed in the chat is saved from its card, and an answer is filed with Improve this.
    const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true,
      extraHTTPHeaders: { "tailscale-user-login": "me@example.com" } });
    const page = await ctx.newPage();
    page.on("console", (m) => m.type() === "error" && !/\/favicon\.ico$/.test(m.location()?.url || "") && failures.push("console: " + m.text() + " " + (m.location()?.url || "")));
    page.on("pageerror", (e) => failures.push("page error: " + e));
    page.on("request", (r) => hosts.add(new URL(r.url()).host));
    page.on("response", (r) => r.status() >= 400 && failures.push(`${r.status()} ${new URL(r.url()).pathname}`));
    const shot = (name) => page.screenshot({ path: `${out}/signed-in-${name}.png` });
    await page.goto(base + "/ask"); await page.waitForSelector(".ask-empty");
    await page.fill(".ask-input", "remember the kitchen and living room play music together"); await page.click(".ask-send");
    await page.waitForSelector(".ask-tap .k-go"); await page.waitForSelector(".ask-send:not(.stop)"); await page.waitForTimeout(500); await shot("9-note-card");
    expect((await page.locator(".ask-tap").innerText()).includes("kitchen and living room"), "the note card shows the note");
    await page.click(".ask-tap .k-go"); await page.waitForSelector(".ask-tap .k-plan-status.ok"); await shot("10-note-saved");
    await page.click(".ask-improve-go"); await page.waitForSelector(".ask-improve .ask-fine"); await shot("11-improve-filed");
    expect((await page.locator(".ask-improve").innerText()).includes("Filed"), "Improve this files a brief");
    await page.reload(); await page.waitForSelector(".ask-tap .k-plan-status"); await page.waitForTimeout(300); await shot("12-after-reload");
    expect((await page.locator(".ask-tap .k-plan-status").innerText()).includes("Saved"), "a reopened card says it was saved");
    await page.locator(".ask-improve-go").last().click(); await page.waitForSelector(".ask-improve .ask-fine");
    const filed = await (await page.request.get(base + "/engine")).text();
    expect(filed.includes("remember (needs_tap)"), "Improve this after a reload files the right answer");
    const engine = await (await page.request.get(base + "/engine/notes")).text();
    expect(engine.includes("kitchen and living room play music together"), "the saved note is on the Notes page");
    await ctx.close();
  }
  for (const scheme of ["light", "dark"]) {
    // Signed in: music started by talking gets a live Now playing card; talk and taps both show on it.
    const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true,
      colorScheme: scheme, extraHTTPHeaders: { "tailscale-user-login": "me@example.com" } });
    const page = await ctx.newPage();
    page.on("console", (m) => m.type() === "error" && !/\/favicon\.ico$/.test(m.location()?.url || "") && failures.push("console: " + m.text() + " " + (m.location()?.url || "")));
    page.on("pageerror", (e) => failures.push("page error: " + e));
    page.on("request", (r) => hosts.add(new URL(r.url()).host));
    page.on("response", (r) => r.status() >= 400 && failures.push(`${r.status()} ${new URL(r.url()).pathname}`));
    const shot = (name) => page.screenshot({ path: `${out}/music-${scheme}-${name}.png` });
    const idle = async () => { await page.waitForSelector(".ask-send:not(.stop)"); await page.waitForTimeout(500); };
    const say = async (text) => { await page.fill(".ask-input", text); await page.click(".ask-send"); await idle(); };
    const card = ".np:not(.np-old)";
    const inRoom = (name) => page.waitForSelector(`${card} .np-room.in:has-text("${name}")`, { timeout: 8000 });
    const level = (label) => page.locator(`${card} .np-level:has(span:text-is("${label}")) output`).innerText();
    await page.goto(base + "/ask"); await page.waitForSelector(".ask-empty");
    await say("play calm piano in the living room");
    await page.waitForSelector(`${card} .np-what small:text-is("Now playing")`); await inRoom("Living Room Speaker");
    await page.locator(card).scrollIntoViewIfNeeded(); await shot("1-playing");
    expect((await page.locator(`${card} .np-what b`).innerText()).length > 0, "the card shows what is playing");
    expect((await page.locator(`${card} .np-room`).count()) === 3, "every speaker is a chip");
    expect(await level("Living Room Speaker") === "15%", "music from silence starts at 15%");
    await say("add the kitchen"); await page.locator(card).scrollIntoViewIfNeeded(); await inRoom("Kitchen Speaker");
    expect(await level("All rooms") === "15%", "the kitchen joins at the playing volume");
    await say("a bit louder"); await page.locator(card).scrollIntoViewIfNeeded();
    await page.waitForFunction(() => document.querySelector(".np:not(.np-old) .np-level output")?.textContent === "25%", null, { timeout: 8000 });
    await page.locator(card).scrollIntoViewIfNeeded(); await shot("2-talked-louder");
    await page.click(`${card} .np-room:has-text("Bedroom Speaker")`); await inRoom("Bedroom Speaker");
    expect(await level("Bedroom Speaker") === "25%", "a tapped room joins at the group's volume");
    await page.locator(`${card} .np-level:has(span:text-is("All rooms")) input`).fill("40");
    await page.waitForSelector(`${card} .np-said:has-text("Bedroom Speaker volume")`, { timeout: 8000 });
    expect(await level("Bedroom Speaker") === "40%", "the group slider moves every room");
    await page.locator(card).scrollIntoViewIfNeeded(); await shot("3-tapped");
    await page.click(`${card} .np-undo`); await page.waitForSelector(`${card} .np-said:has-text("Put back")`);
    await page.waitForFunction(() => document.querySelector(".np:not(.np-old) .np-level output")?.textContent === "25%", null, { timeout: 8000 });
    await page.click(`${card} .np-key[aria-label="Pause"]`); await page.waitForSelector(`${card} .np-key[aria-label="Play"]`);
    await page.click(`${card} .np-room.in:has-text("Kitchen Speaker")`);
    await page.waitForSelector(`${card} .np-room:not(.in):has-text("Kitchen Speaker")`, { timeout: 8000 });
    await page.locator(card).scrollIntoViewIfNeeded(); await shot("4-paused-kitchen-out");
    await say("play morning light in the bedroom");
    await page.waitForSelector(".np.np-old"); await inRoom("Bedroom Speaker");
    expect((await page.locator(".np").count()) === 2 && (await page.locator(".np-old").innerText()).includes("Replaced"), "an older card folds to Replaced");
    await shot("5-replaced");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "the music card scrolls sideways");
    expect((await page.locator(`${card} .np-links`).innerText()).includes("Open in Music Assistant"), "the card opens Music Assistant");
    // Stage 2: the mini-player on another page, opening to the same controls.
    await page.goto(base + "/today"); await page.waitForSelector("#mp-bar:not([hidden]) .mp-open");
    expect((await page.locator(".mp-open").innerText()).includes("Bedroom Speaker"), "the bar says where music plays");
    await shot("6-bar");
    await page.click(".mp-open"); await page.waitForSelector(".mp-sheet .mp-panel");
    await page.click('.mp-sheet .mp-panel .mp-go[aria-label="Pause"]');
    await page.waitForSelector('.mp-sheet .mp-panel .mp-go[aria-label="Play"]', { timeout: 8000 });
    expect((await page.locator(".mp-sheet .mp-said").innerText()).includes("paused"), "a tap in the bar says what it did");
    await shot("7-bar-open");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "the mini-player scrolls sideways");
    // Stage 3: the Music screen, every speaker with the same controls.
    await page.goto(base + "/house/music"); await page.waitForSelector(".mp-screen .mp-panel");
    expect((await page.locator(".mp-screen .mp-panel").count()) >= 2, "every speaker group has a panel");
    await page.click('.mp-screen .mp-panel >> nth=0 >> .mp-room:has-text("Kitchen Speaker")');
    await page.waitForSelector('.mp-screen .mp-panel >> nth=0 >> .mp-room.in:has-text("Kitchen Speaker")', { timeout: 8000 });
    await page.waitForTimeout(400); await shot("8-music-screen");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "the Music screen scrolls sideways");
    await ctx.close();
  }
  expect([...hosts].every((h) => h === new URL(base).host), "the page contacted another host: " + [...hosts].join(", "));
  await browser.close();
  console.log(JSON.stringify({ ok: !failures.length, failures, hosts: [...hosts] }, null, 2));
  process.exit(failures.length ? 1 : 0);
})();
