// Drives the real Ask page at phone width against demo_server.py and saves screenshots.
// Usage: node check.cjs <output-folder> [base-url]      (needs Playwright; see README.md)
const { chromium } = require("playwright");
const out = process.argv[2];
const base = process.argv[3] || "http://127.0.0.1:8792";
if (!out) { console.error("usage: node check.cjs <output-folder> [base-url]"); process.exit(2); }

(async () => {
  const browser = await chromium.launch();
  const failures = [], hosts = new Set();
  const expect = (ok, what) => { if (!ok) failures.push(what); };
  for (const scheme of ["light", "dark"]) {
    const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true, colorScheme: scheme });
    const page = await ctx.newPage();
    page.on("console", (m) => m.type() === "error" && failures.push("console: " + m.text()));
    page.on("pageerror", (e) => failures.push("page error: " + e));
    page.on("request", (r) => hosts.add(new URL(r.url()).host));
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
  expect([...hosts].every((h) => h === new URL(base).host), "the page contacted another host: " + [...hosts].join(", "));
  await browser.close();
  console.log(JSON.stringify({ ok: !failures.length, failures, hosts: [...hosts] }, null, 2));
  process.exit(failures.length ? 1 : 0);
})();
