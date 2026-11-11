// Drives Hub's three main views against demo_server.py, checks them, and saves screenshots.
// Usage: node shots.cjs <output-folder> [base-url]      (needs Playwright; see README.md)
const { chromium } = require("playwright");
const out = process.argv[2];
const base = process.argv[3] || "http://127.0.0.1:8793";
if (!out) { console.error("usage: node shots.cjs <output-folder> [base-url]"); process.exit(2); }

const SIZES = [
  { name: "phone-light", viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true, colorScheme: "light" },
  { name: "phone-dark", viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true, colorScheme: "dark" },
  { name: "desktop", viewport: { width: 1280, height: 800 }, deviceScaleFactor: 2, colorScheme: "light" },
];

(async () => {
  const browser = await chromium.launch();
  const failures = [], hosts = new Set();
  const expect = (ok, what) => { if (!ok) failures.push(what); };
  for (const { name, ...size } of SIZES) {
    const ctx = await browser.newContext(size);
    const page = await ctx.newPage();
    page.on("console", (m) => m.type() === "error" && failures.push(`${name} console: ${m.text()}`));
    page.on("pageerror", (e) => failures.push(`${name} page error: ${e}`));
    page.on("request", (r) => hosts.add(new URL(r.url()).host));
    const shot = (file, fullPage = true) => page.screenshot({ path: `${out}/${name}-${file}.png`, fullPage });
    const narrow = async (where) => expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${name}: ${where} scrolls sideways`);
    const tap = (selector) => (size.hasTouch ? page.tap(selector) : page.click(selector));

    await page.goto(base + "/");
    await page.waitForSelector(".ask-empty"); await narrow("Ask"); await shot("1-ask", false);
    expect((await page.locator(".shell-nav a").allInnerTexts()).join("|") === "Ask|Today|Insights", `${name}: the main navigation is Ask, Today, Insights`);
    expect((await page.locator(".shell-nav a.on").innerText()) === "Ask", `${name}: Hub opens on Ask`);

    await page.goto(base + "/today");
    await page.waitForSelector(".decision"); await narrow("Today"); await shot("2-today");
    expect((await page.locator(".decision").count()) === 4, `${name}: one card per decision`);
    expect((await page.locator('.tile a[href="/kitchen"]').count()) > 0, `${name}: Kitchen has its own card`);
    const small = await page.evaluate(() => [...document.querySelectorAll(".decision-acts .btn, .shell-icon")].filter((b) => b.getBoundingClientRect().height < 40).length);
    expect(small === 0, `${name}: a button on Today is under 40px tall`);

    await page.goto(base + "/insights");
    await page.waitForSelector(".viz"); await narrow("Insights"); await shot("3-insights");
    expect((await page.locator("svg.viz").count()) === 3 && (await page.locator(".trend-chart").count()) >= 2, `${name}: the charts are drawn`);
    await page.locator("#network").scrollIntoViewIfNeeded();
    await page.locator("#network").screenshot({ path: `${out}/${name}-4-insights-network.png` });
    if (!size.hasTouch) {
      await page.locator("section[aria-labelledby=viz-spend]").scrollIntoViewIfNeeded();
      const column = await page.locator(".viz-col").nth(20).boundingBox();
      await page.waitForTimeout(300);   // let the scroll settle: scrolling puts the tooltip away
      await page.mouse.move(column.x + column.width / 2, column.y + column.height / 2 - 1);
      await page.mouse.move(column.x + column.width / 2, column.y + column.height / 2);
      await page.waitForSelector("#viz-tip:not([hidden])");
      expect((await page.locator("#viz-tip").innerText()).includes("$"), `${name}: hovering a day shows its cost`);
      await shot("3b-insights-hover", false);
      await page.mouse.move(0, 0);
    }

    await page.goto(base + "/today");
    await tap(".decision >> nth=1 >> .ask-about");
    await page.waitForSelector(".ask-context");
    await page.waitForFunction(() => document.querySelector(".ask-input").value.includes("3 emails need you"));
    expect(!(await page.url()).includes("about="), `${name}: the reference is dropped from the address once read`);
    expect((await page.locator(".ask-msg").count()) === 0, `${name}: nothing is sent until the owner sends it`);
    await narrow("Ask about this"); await shot("5-ask-about-this", false);
    await page.click(".ask-send");
    await page.waitForSelector(".ask-sources"); await page.waitForTimeout(600); await shot("6-ask-about-answer", false);
    expect((await page.locator(".ask-context").count()) === 0, `${name}: the context note goes once the question is sent`);

    await page.goto(base + "/insights");
    await tap("section[aria-labelledby=viz-spend] .ask-about");
    await page.waitForFunction(() => document.querySelector(".ask-input")?.value.includes("spent on AI"));

    await page.goto(base + "/tools");
    await page.waitForSelector(".menu"); await narrow("All tools"); await shot("7-all-tools");
    expect((await page.locator(".menu a").count()) === 7 && (await page.locator('.menu a[href="/kitchen"]').count()) === 1, `${name}: All tools lists every tool`);
    await page.goto(base + "/settings");
    await page.waitForSelector(".menu"); await narrow("Settings"); await shot("8-settings");
    expect((await page.locator(".menu a").allInnerTexts()).join(" ").includes("Engine"), `${name}: Engine and Status are under Settings`);
    await page.goto(base + "/gmail"); await narrow("a tool page");
    expect((await page.locator(".shell-icon.on").count()) === 1, `${name}: a tool page marks All tools`);
    await ctx.close();
  }
  await browser.close();
  expect([...hosts].every((h) => h === new URL(base).host), "the pages contacted another host: " + [...hosts].join(", "));
  console.log(failures.length ? "FAILED\n- " + failures.join("\n- ") : "All checks passed.");
  process.exit(failures.length ? 1 : 0);
})();
