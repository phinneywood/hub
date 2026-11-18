// Drives the real app against demo_server.py at phone and laptop size, in both looks, and saves screenshots.
// Usage: node check.cjs <output-folder> [base-url]      (needs Playwright; see README.md)
const { chromium } = require("playwright");
const out = process.argv[2];
const base = process.argv[3] || "http://127.0.0.1:8912";
if (!out) { console.error("usage: node check.cjs <output-folder> [base-url]"); process.exit(2); }

const ZONE = "America/Los_Angeles";      // Hub's own time zone: the made-up day is seen from its evening
const PHONE = { viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true, timezoneId: ZONE };
const SIZES = [["phone-dusk", { ...PHONE, colorScheme: "dark" }], ["phone-day", { ...PHONE, colorScheme: "light" }],
               ["laptop-dusk", { viewport: { width: 1280, height: 800 }, deviceScaleFactor: 2, colorScheme: "dark", timezoneId: ZONE }]];
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

    const small = () => page.evaluate((least) => [...document.querySelectorAll("button, a, input, [role=switch], [role=button]")]
      .map((el) => [el, el.getBoundingClientRect()]).filter(([el, r]) => r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== "hidden")
      .filter(([, r]) => Math.min(r.width, r.height) < least)
      .map(([el, r]) => `${(el.getAttribute("aria-label") || el.textContent || el.className).trim().slice(0, 30)} ${Math.round(r.width)}x${Math.round(r.height)}`), FINGER);
    const openPage = async (name) => {         // Today, Mail and Kitchen have no tab: Ask finds them by a few letters
      await tap(phone ? '.tabs .tab:text-is("Ask")' : '.rail .tab:text-is("Ask")');
      await page.fill("#askq", name.slice(0, 4));
      await page.locator(".found .sug", { hasText: name }).first().click();
    };

    // ---- opening: Ask, a conversation where Hub speaks first with the day, what needs you and the house ----
    // Opening Ask makes the day's morning message. Let that first opening finish before the demo is reset, or the
    // two cross: it once wrote the day down as "nothing to say" after the reset, and the reloaded Ask had no message.
    const opened = page.waitForResponse((r) => new URL(r.url()).pathname === "/app/api/ask", { timeout: 30000 }).catch(() => null);
    await page.goto(base + "/");
    expect(new URL(page.url()).pathname === "/app/", "Hub's front door did not lead to the app");
    await page.waitForSelector(".askpage");
    expect(await opened, "Ask did not ask Hub for the conversation");
    await stage("reset");
    await page.reload(); await page.waitForSelector(".askpage .now"); await page.waitForTimeout(2000);
    await shot("0-ask");
    const greets = () => page.locator(".askpage .greet-text").innerText().catch(() => "(nothing)");
    expect(await until(() => document.querySelector(".askpage .greet-text")?.textContent.endsWith(" Dinner at home is at seven. Ten things need you.")),
      "Ask did not open with Hub saying the day: " + await greets());
    expect(await page.locator(".askpage .chathead .chat-who b").innerText() === "Hub", "Ask did not say who you are talking to");
    expect(await page.getAttribute("#askq", "placeholder") === "Message Hub" && await page.locator('.composer [aria-label="Send"]').count() === 1,
      "the field does not read as a message to Hub");
    // Hub's morning message is the first turn of the day: three numbered things, the house and money first, each with
    // one button; under the greeting only the day's next thing and Open Today stay, and Today has the rest.
    // The morning message is made when Ask first opens, so it can arrive a moment after the greeting: wait for it.
    // On a busy CI runner that moment once passed six seconds, so it is given fifteen.
    await until(() => document.querySelectorAll(".askpage .morning .row-main b").length === 3, null, 15000);
    const morning = await page.locator(".askpage .morning .row-main b").allInnerTexts();
    expect(morning.length === 3 && morning[0].startsWith("Side door lock’s battery is flat.") && morning[1].startsWith("Pay the Lakeside Power bill.")
      && morning[2].startsWith("Email from Sam Rivera."), "Ask did not open on the morning message: " + morning.join("|"));
    expect(await page.locator(".askpage .now .row.need").count() === 0, "what needs you was said twice: as rows and in the morning message");
    expect((await page.locator(".askpage .now-more").first().innerText()).trim() === "Open Today",
      "the rest of what needs you was not a tap away");
    if (phone) {      // the conversation sits whole above the bar, and stays there when an iPhone reports the visible area moved
      const fits = () => page.evaluate(() => {
        const ask = document.querySelector(".askpage").getBoundingClientRect(), field = document.querySelector(".composer").getBoundingClientRect();
        return ask.top === 0 && field.bottom <= document.querySelector(".dock").getBoundingClientRect().top
          && document.scrollingElement.scrollHeight <= innerHeight;
      });
      expect(await fits(), "Ask's field was not above the bar, or the page under Ask could scroll");
      await page.evaluate(() => document.documentElement.style.setProperty("--vvt", "108px"));     // what iOS left behind in the bug
      expect(await fits(), "Ask moved under the bar when the visible area was reported moved without a keyboard");
      await page.evaluate(() => document.documentElement.style.removeProperty("--vvt"));
    }
    expect(phone ? (await page.locator(".askpage .house-now .house-words b").innerText()) === "Four rooms are lit."
      : await page.locator(".side .plan").count() > 0, phone ? "the house card did not say the house" : "the house was not beside Ask");
    expect(await page.evaluate(() => { const f = document.querySelector("#askq").getBoundingClientRect(); return f.bottom <= innerHeight && f.top > innerHeight / 2; }),
      "the field was not at the bottom of the screen");
    const sideways = () => page.evaluate(() => [...document.querySelectorAll(".ask-scroll")].some((box) => box.scrollWidth > box.clientWidth));
    expect(!(await sideways()), "the conversation is wider than the screen");
    expect((await page.locator(phone ? ".tabs .tab" : ".rail .tab:not(.foot .tab)").allInnerTexts()).join("|") === "Ask|House", "the bar is not Ask and House");
    if (phone) expect((await small()).length === 0, "tap targets under 44 px on Ask: " + (await small()).join(", "));
    await tap(phone ? '.tabs .tab:text-is("House")' : '.rail .tab:text-is("House")');
    await page.waitForSelector(".plan"); await page.waitForTimeout(2000);      // the opening moment is over
    await shot("1-home");
    expect(await says() === "Four rooms are lit.", "the house sentence: " + await says());
    expect(await sub() === "“Paper Lanterns” is playing in the kitchen and the living room.", "the music sentence: " + await sub());
    expect(await page.locator(".main .room").count() === 12, "twelve rooms on the plan");
    expect((await page.locator(".floor").allInnerTexts()).join("|") === "Downstairs|Upstairs|Outside", "three floors, ground floor first");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "the page scrolls sideways");
    expect(await page.evaluate(() => document.fonts.check('500 17px "Jost"')), "the typeface did not load");
    if (phone) expect((await small()).length === 0, "tap targets under 44 px on Home: " + (await small()).join(", "));

    // ---- a tap shows at once, before Hub has answered ----
    let slow = true;
    await page.route("**/app/api/room/**", async (route) => { if (slow) await new Promise((r) => setTimeout(r, 900)); await route.continue(); });
    const before = Date.now();
    await tap(room("office"));
    expect(await saysSoon("Five rooms are lit.", 400), "a tap did not show within 0.4 s while Hub was still answering");
    timings[name] = Date.now() - before;
    slow = false;
    await page.waitForSelector(".toast"); await shot("2-tapped");
    expect((await page.textContent(".toast span")).trim() === "Office on", "the toast: " + await page.textContent(".toast"));
    await page.waitForTimeout(1500);
    expect(await says() === "Five rooms are lit.", "the tap did not hold once the house answered");
    await tap(".toast button");
    expect(await saysSoon("Four rooms are lit."), "Undo did not put the room back");

    // ---- a tap that didn't take is put right, and says why ----
    await page.unroute("**/app/api/room/**");
    // Refused after a moment, as a real refusal would be: refused at once, the tap could be put right before it is drawn.
    await page.route("**/app/api/room/**", async (route) => { await new Promise((r) => setTimeout(r, 300));
      await route.fulfill({ status: 409, contentType: "application/json", body: '{"error":"Home Assistant couldn’t be reached just now."}' }); });
    quiet = true;
    await tap(room("office"));
    expect(await saysSoon("Five rooms are lit.", 400), "the tap did not show");
    expect(await saysSoon("Four rooms are lit.", 3000), "a refused tap was left showing as done");
    expect((await page.textContent(".toast span")).includes("couldn’t be reached"), "a refused tap did not say why");
    await page.unroute("**/app/api/room/**"); quiet = false;
    await page.waitForTimeout(400);

    // ---- another device sees it without asking ----
    const other = await ctx.newPage();
    await other.goto(base + "/app/#house"); await other.waitForSelector(".plan");
    await tap(room("entry"));
    expect(await other.waitForFunction(() => document.querySelector(".say")?.textContent.trim() === "Five rooms are lit.", null, { timeout: 6000 }).then(() => true, () => false),
      "a second open app did not hear about the change within 6 s");
    await other.close();
    await tap(room("entry")); expect(await saysSoon("Four rooms are lit."), "the entry did not switch off again");
    await page.waitForTimeout(600);

    // ---- hold a room: everything in it ----
    // The page may still be scrolling (new content below can move it): hold only once the room sits still under the finger.
    let box = null;
    for (let i = 0; i < 20 && !box; i++) {
      await page.locator(room("kitchen")).scrollIntoViewIfNeeded();
      const a = await page.locator(room("kitchen")).boundingBox();
      await page.waitForTimeout(150);
      const b = await page.locator(room("kitchen")).boundingBox();
      const still = a && b && a.x === b.x && a.y === b.y;
      if (still && await page.evaluate(([x, y, sel]) => !!document.elementFromPoint(x, y)?.closest(sel), [b.x + 40, b.y + 40, room("kitchen")])) box = b;
    }
    expect(box, "the kitchen never sat still on screen to be held");
    box = box || await page.locator(room("kitchen")).boundingBox();
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
    expect(await says() === "Four rooms are lit.", "holding a room also switched it");
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
    expect(await saysSoon("Two rooms are lit."), "the scene did not run: " + await says());
    await page.waitForTimeout(500);
    await tap('.scene:text-is("All off")');
    expect(await saysSoon("The house is dark.", 400), "All off did not show at once");
    await page.waitForTimeout(1200); await shot("4-dark");
    expect(await says() === "The house is dark.", "All off did not hold");
    await tap(".toast button");
    expect(await saysSoon("Two rooms are lit."), "Undo did not bring the lights back");

    // ---- keep the house as it stands as a scene ----
    await tap(".scene.add"); await page.waitForSelector(".sheet.in");
    await page.fill(".sheet .field", "Reading"); await page.waitForTimeout(450); await shot("4b-keep");
    if (phone) expect((await small()).length === 0, "tap targets under 44 px when keeping a scene: " + (await small()).join(", "));
    await page.locator(".sheet .btn.go").click();
    const sceneOn = (name) => page.evaluate((n) => [...document.querySelectorAll(".scene")].some((el) => el.textContent === n && el.classList.contains("on")), name);
    expect(await until(() => document.querySelector(".scene")?.textContent === "Reading" && document.querySelector(".scene").classList.contains("on")),
      "the kept scene did not lead the row and show as on");
    await tap('.scene:text-is("All off")');
    expect(await saysSoon("The house is dark.", 400), "All off did not show at once the second time");
    expect(await until(() => [...document.querySelectorAll(".scene")].some((el) => el.textContent === "All off" && el.classList.contains("on"))), "All off did not show as on in a dark house");
    expect(!(await sceneOn("Reading")), "the kept scene still showed as on in a dark house");
    await tap('.scene:text-is("Reading")');
    expect(await saysSoon("Two rooms are lit."), "the kept scene did not bring the rooms back: " + await says());
    expect(await until(() => document.querySelector(".scene")?.classList.contains("on")), "the kept scene did not show as on after it ran");
    await tap(".scene.add"); await page.waitForSelector(".sheet.in");
    await page.locator(".sheet .forget").click();
    expect(await until(() => ![...document.querySelectorAll(".scene")].some((el) => el.textContent === "Reading")), "the scene was not forgotten");
    await page.keyboard.press("Escape");
    expect(await until(() => !document.querySelector(".sheet")), "the sheet did not close");
    await page.waitForTimeout(300);

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
    await page.locator('.sheet [aria-label="Dining joins “Paper Lanterns”"]').click();
    expect(await until(() => document.querySelector(".sub")?.textContent.includes("the dining")), "a room did not join the music: " + await sub());
    await page.waitForTimeout(1200);
    expect((await sub()).includes("the dining"), "the joined room did not hold");
    await page.locator('.pick:has-text("Sunday Morning")').click();
    expect(await until(() => document.querySelector(".toast span")?.textContent.trim() === "Playing Sunday Morning"), "a pick did not play");
    // A quiet room plays music of its own, and the rest plays on.
    await page.locator('.sheet [aria-label="Play something in Bedroom"]').click();
    await page.waitForSelector(".sheet.in .pick"); await page.waitForTimeout(450); await shot("5b-play-here");
    expect((await page.locator(".sheet-head p").first().innerText()).includes("plays on"), "playing in one room did not say the rest plays on");
    await page.locator('.sheet .pick:has-text("Rainy Day")').click();
    expect(await until(() => document.querySelector(".np-text span")?.textContent.includes("also Bedroom"), null, 400), "a room's own music did not show at once");
    expect(await until(() => document.querySelector(".toast span")?.textContent.trim() === "Playing Rainy Day in the bedroom"), "a room did not play its own music");
    await page.waitForTimeout(1200);
    expect((await sub()).includes("Something else is playing in the bedroom"), "the main music did not play on beside the bedroom: " + await sub());
    await page.locator(".np").click({ position: { x: 120, y: 20 } });
    await page.waitForSelector(".sheet.in"); await page.waitForTimeout(450); await shot("5c-two-rooms");
    // One card for each music: the main one first, the bedroom's own after it, each with its own play and pause.
    expect(await page.locator(".sheet .tune").count() === 2, "the music sheet did not show a card for each music");
    expect((await page.locator(".sheet-head h1").innerText()) === "Music in the house", "two musics did not make it the house's music");
    const bedroom = page.locator('.sheet .tune:has(.tune-where:text-is("Bedroom"))');
    expect(await bedroom.locator('[aria-label^="Pause"]').count() === 1, "the bedroom's own music had no pause");
    // The main music's volume moves every room in it at once, and a room comes out of it.
    const main = page.locator(".sheet .tune").first();
    await main.locator('.pill:has-text("Rooms")').click();
    const kitchenWas = Number((await main.locator('.ctl:has-text("Kitchen") .ctl-name span').innerText()).replace("%", ""));
    await main.locator('input[aria-label^="Volume of"]').fill("45");
    expect(await until((n) => [...document.querySelectorAll(".sheet .tune .ctl")].some((row) => row.querySelector("b")?.textContent === "Kitchen"
      && Number(row.querySelector(".ctl-name span")?.textContent.replace("%", "")) > n), kitchenWas),
      "the rooms did not follow the music's volume");
    await main.locator('[aria-label="Take Kitchen out"]').click();
    expect(await until(() => document.querySelector(".toast span")?.textContent.trim() === "Kitchen stopped"), "a room did not come out of the music");
    await page.waitForTimeout(1200);
    expect(await page.locator('.sheet [aria-label="Kitchen joins some music"]').count() === 1, "a quiet room did not ask which music to join");
    await page.locator('.sheet [aria-label="Kitchen joins some music"]').click();
    await page.locator('.sheet .ctl.choose .pill').first().click();
    expect(await until(() => document.querySelector(".toast span")?.textContent.includes("Kitchen is playing")), "a room did not join the music it was given");
    await page.waitForTimeout(1200);
    if (phone) expect((await small()).length === 0, "tap targets under 44 px in the music sheet: " + (await small()).join(", "));
    await bedroom.locator('[aria-label^="Pause"]').click();
    expect(await until(() => document.querySelector('.sheet [aria-label="Play something in Bedroom"]')), "the bedroom's own music did not pause");
    await page.keyboard.press("Escape"); await page.waitForTimeout(400);

    // ---- Ask: what is usually asked around now is one tap ----
    const answered = (text, ms) => until((t) => [...document.querySelectorAll(".turn")].pop()?.querySelector(".answer")?.textContent.startsWith(t)
      && ![...document.querySelectorAll(".turn")].pop().querySelector(".dots"), text, ms);
    const lastAnswer = () => page.locator(".turn .answer").last().innerText().catch(() => "(nothing)");
    await tap(phone ? '.tabs .tab:text-is("Ask")' : '.rail .tab:text-is("Ask")'); await page.waitForSelector(".askpage");
    expect(await until(() => document.querySelector(".prompts .prompt.usual")?.textContent === "Dim the living room to 30"), "what is often asked around now was not offered");
    await page.waitForTimeout(300); await shot("6a-ask-open");
    await page.locator(".prompts .prompt.usual").first().click();
    expect(await answered("The living room is at 30%."), "one tap on a habit did not do it: " + await lastAnswer());

    // ---- Ask: a few letters reach a room ----
    await page.fill("#askq", "liv"); await page.waitForTimeout(250); await shot("6-ask");
    expect((await page.locator(".sug").first().innerText()).startsWith("Living room"), "typing did not find the room");
    await page.locator('.sug [role="switch"]').first().click();
    expect(await until(() => fetch("/app/api/home").then((r) => r.json()).then((h) => !h.rooms["living-room"].on)), "the switch in the list did not switch the room");

    // ---- Ask: say it, and Hub does it and says so ----
    // The field sends only once the request before it has finished, so wait for that as a person would.
    const askFor = async (words) => {
      await page.fill("#askq", words);
      await until(() => document.querySelector("#askq")?.parentElement?.querySelector(".send.on") !== null);
      await page.keyboard.press("Enter");
    };
    const level = (id) => page.evaluate((i) => fetch("/app/api/home").then((r) => r.json()).then((h) => h.rooms[i].level), id);
    const asking = Date.now();
    await askFor("Dim the kitchen and living room for dinner");
    expect(await answered("The kitchen and the living room are at 40%."), "Ask did not dim the rooms: " + await lastAnswer());
    timings[name + "-ask"] = Date.now() - asking;
    expect(await page.locator(".turn").last().locator(".plan .room").count() === 2, "the rooms that changed were not drawn under the answer");
    expect(await level("kitchen") === 40 && await level("living-room") === 40, "the rooms are not at 40%");
    expect(await page.inputValue("#askq") === "", "the field kept the words after they were sent");
    await page.waitForTimeout(350); await shot("6b-ask-did");
    await page.locator(".turn .took button").last().click();
    expect(await until(() => document.querySelector(".turn.back .took")?.textContent === "Put back as it was"), "the answer did not say it was put back");
    expect(await until(() => fetch("/app/api/home").then((r) => r.json()).then((h) => h.rooms.kitchen.level === 80)), "Undo under the answer did not put the kitchen back");
    await askFor("some jazz in the bedroom, softly");
    expect(await answered("Playing Evening Jazz in the bedroom at 15%."), "Ask did not play the music: " + await lastAnswer());
    expect(await until(() => [...document.querySelectorAll(".turn")].pop()?.querySelector(".rows .ctl-name")?.textContent === "Bedroom"), "the speaker was not drawn under the answer");
    expect(await until(() => document.querySelector(".np-text span")?.textContent.endsWith("also Bedroom")), "the bar did not say that two rooms play different things");
    await askFor("what should we cook this week?");
    expect(await answered("Kitchen is on it."), "Ask did not pass the question to Kitchen: " + await lastAnswer());
    expect(await until(() => document.querySelectorAll(".kitchen-says .kcard").length === 3, null, 12000), "Kitchen’s answer did not arrive under the request");
    // An answer taller than the screen is read from its start; a shorter one sits on the field.
    expect(await until(() => { const box = document.querySelector(".ask-scroll"), r = box.getBoundingClientRect();
      const bubble = [...box.querySelectorAll(".turn:not(.folded) .asked")].pop()?.getBoundingClientRect();
      return box.scrollHeight - box.scrollTop - box.clientHeight < 4 || (bubble && bubble.top >= r.top - 1 && bubble.top < r.top + 60); }),
      "Kitchen’s answer arrived out of view");
    await page.waitForTimeout(300); await shot("6c-ask-kitchen");
    await askFor("what needs me today?");
    expect(await answered("From what Hub can see, two emails need you."), "a question about the day was not answered from what Hub read: " + await lastAnswer());
    // Ask searches the person's own mail and reads one email it found; the emails are drawn under the answer, each
    // opening in Gmail, and "open the first one" reads the first of them
    const lastTurn = (sel) => page.evaluate((s) => [...[...document.querySelectorAll(".turn")].pop().querySelectorAll(s)]
      .map((el) => el.getAttribute("href") ?? el.textContent.trim()), sel);
    await askFor("Search my mail for sink");
    expect(await answered("2 emails match. The newest is from Riverside Plumbing: Re: Kitchen sink estimate."), "Ask did not search the mail: " + await lastAnswer());
    expect((await lastTurn(".mailrows .row-main b")).join("|") === "Riverside Plumbing|Alex", "the emails found were not drawn under the answer: " + (await lastTurn(".mailrows .row-main b")).join("|"));
    expect((await lastTurn(".mailrows a.row-act")).every((href) => href.startsWith("https://mail.google.com/mail/") && href.includes("#all/t-plumb")),
      "an email found did not open in Gmail");
    expect((await lastTurn(".dayrows")).length === 0, "the day’s mail rows were drawn beside the emails found");
    await askFor("Open the first one");
    expect(await answered("Riverside Plumbing wrote: Hi Alex, we can come Thursday at 10"), "Ask did not read the email: " + await lastAnswer());
    expect((await lastTurn(".mailrows .row.now .row-main b")).join("|") === "Riverside Plumbing", "the email read was not marked under the answer");
    expect(!(await sideways()), "the emails found are wider than the screen");
    await page.waitForTimeout(300); await shot("6c2-ask-mail");
    if (phone) expect((await small()).length === 0, "tap targets under 44 px under the emails found: " + (await small()).join(", "));
    // Ask changes mail only after the person's tap: "archive the first one" puts a card up naming the email, the tap
    // archives it (here, a stand-in for Gmail), and Undo on the card puts it back
    const mailWrites = () => page.evaluate(() => fetch("/demo/mail-writes").then((r) => r.json()));
    await askFor("Archive the first one");
    expect(await answered("Archive this email: waiting for your tap."), "Ask did not put the archive on a card: " + await lastAnswer());
    expect((await lastTurn(".wait .wait-lines li")).join("|") === "Riverside Plumbing: Re: Kitchen sink estimate", "the card did not name the email");
    expect((await mailWrites()).length === 0, "the mail changed before the tap");
    expect(!(await sideways()), "the mail card is wider than the screen");
    await page.waitForTimeout(300); await shot("6c3-ask-mail-card");
    if (phone) expect((await small()).length === 0, "tap targets under 44 px on the mail card: " + (await small()).join(", "));
    await page.locator(".turn .wait .btn.go").last().click();
    expect(await until(() => [...document.querySelectorAll(".turn")].pop()?.querySelector(".wait .wait-cap")?.textContent === "Done"), "the tap did not archive the email");
    expect((await mailWrites()).map((w) => w[0]).join("|") === "/gmail/labels/apply", "the tap did not go to Gmail once");
    await page.locator(".turn .wait .took button").last().click();
    expect(await until(() => [...document.querySelectorAll(".turn")].pop()?.querySelector(".wait .took")?.textContent === "Put back as it was"), "Undo on the card did not put the email back");
    expect((await mailWrites()).pop()[0] === "/gmail/undo", "Undo did not go to Gmail");
    await askFor("what errands are ready?");
    expect(await until(() => [...document.querySelectorAll(".turn .answer")].pop()?.textContent.startsWith("6 errands are ready for your tap.")),
      "Ask did not say how many errands are ready: " + await lastAnswer());
    expect(await until(() => [...document.querySelectorAll(".turn")].pop()?.querySelectorAll(".er-list.in-ask .errand").length === 4),
      "the errands were not drawn under the answer");
    // an errand finished in words: Hub marks it done, draws it so under the answer, and Undo puts it back
    const billReady = () => fetch("/app/api/errands").then((r) => r.json()).then((e) => e.ready.some((x) => x.title.includes("Lakeside Power")));
    await askFor("I paid the power bill");
    expect(await answered("Marked done."), "Ask did not finish the errand: " + await lastAnswer());
    expect(await until(() => [...document.querySelectorAll(".turn")].pop()?.querySelector(".er-list.in-ask .errand .took")?.textContent.startsWith("Done")),
      "the errand was not drawn as done under the answer");
    expect(!(await page.evaluate(billReady)), "the bill was still waiting after Ask marked it done");
    await page.waitForTimeout(300); await shot("6e-ask-errand");
    await page.locator(".turn .took button").last().click();
    expect(await until(() => [...document.querySelectorAll(".turn")].pop()?.matches(".back") && [...document.querySelectorAll(".turn .took")].pop()?.textContent === "Put back as it was"),
      "the answer did not say the errand was put back");
    expect(await until(billReady), "Undo under the answer did not put the errand back");
    await askFor("remember to start music softly");
    expect(await answered("Hub will remember that."), "Ask did not remember: " + await lastAnswer());
    await page.waitForTimeout(300); await shot("6d-ask-talk");

    // ---- Ask: how the house is set up. Done at once with Undo; what removes or unlocks waits on a card ----
    const lampIn = (id) => page.evaluate((i) => fetch("/app/api/home").then((r) => r.json()).then((h) => h.rooms[i].lights.some((l) => l.name === "Reading lamp")), id);
    await askFor("Move the reading lamp to the bedroom");
    expect(await answered("Reading lamp moved from Living room to Bedroom."), "Ask did not move the lamp: " + await lastAnswer());
    expect(await lampIn("bedroom") && !(await lampIn("living-room")), "the lamp is not in the bedroom");
    await page.locator(".turn .took button").last().click();
    expect(await until(() => fetch("/app/api/home").then((r) => r.json()).then((h) => h.rooms["living-room"].lights.some((l) => l.name === "Reading lamp"))), "Undo did not put the lamp back");
    await askFor("unlock the front door");
    expect(await answered("That one waits for your tap."), "unlocking did not wait for a tap: " + await lastAnswer());
    expect(await until(() => [...document.querySelectorAll(".wait .wait-t")].pop()?.textContent === "Unlock Front door lock?"), "the card did not ask about the door");
    const locked = () => page.evaluate(() => fetch("/app/api/home").then((r) => r.json()).then((h) => h.facts.find((f) => f.label === "Front door lock").value));
    expect(await locked() === "Locked", "the door was unlocked without a tap");
    await page.waitForTimeout(300); await shot("6e-ask-card");
    if (phone) expect((await small()).length === 0, "tap targets under 44 px on a card that waits: " + (await small()).join(", "));
    await page.locator(".wait .btn.go").last().click();
    expect(await until(() => [...document.querySelectorAll(".wait")].pop()?.classList.contains("settled")), "the card did not settle after the tap");
    expect(await locked() === "Unlocked", "the tap did not unlock the door");
    await askFor("Remove the dining room");
    expect(await until(() => [...document.querySelectorAll(".wait .wait-t")].pop()?.textContent === "Remove Dining?"), "removing a room did not wait on a card");
    await page.locator(".wait .btn.ghost").last().click();
    expect(await until(() => [...document.querySelectorAll(".wait .took")].pop()?.textContent === "Left as it was."), "Not now did not put the card away");
    expect(await page.evaluate(() => fetch("/app/api/home").then((r) => r.json()).then((h) => "dining" in h.rooms)), "the room was removed without a tap");
    await askFor("Every evening at 6:45 turn on the hallway light");
    expect(await answered("Automation “Hallway ceiling on at 6:45 pm” made: at 6:45 pm, Ceiling (Hallway) on."), "Ask did not make the automation: " + await lastAnswer());
    await askFor("Disable the coffee maker");
    expect(await until(() => [...document.querySelectorAll(".wait .wait-t")].pop()?.textContent === "Disable Coffee maker?"), "disabling did not wait on a card");
    expect((await page.locator(".wait .btn.risk").last().innerText()) === "Disable", "the card that disables did not say so on its button");
    await page.locator(".wait .btn.ghost").last().click();
    await askFor("Reload the hue integration");
    expect(await answered("2 changes made."), "Ask did not reload the integration: " + await lastAnswer());
    expect(await until(() => [...document.querySelectorAll(".turn")].pop()?.querySelectorAll(".did li").length === 2), "the two reloads were not listed under the answer");
    expect(await until(() => ![...document.querySelectorAll(".turn")].pop()?.querySelector(".took button")), "a reload offered an Undo it doesn’t have");
    await askFor("move the ceiling to the office");
    expect(await until(() => [...document.querySelectorAll(".turn .answer")].pop()?.textContent.startsWith("Which one? Ceiling (Entry), Ceiling (Guest room)")),
      "a name that fits several lamps was not asked about: " + await lastAnswer());

    // ---- Ask: the house's caretaker. Asked about, a drill played under the answer, a restart left on a card ----
    await askFor("How's the house?");
    expect(await answered("One thing needs you."), "Ask did not answer from House health: " + await lastAnswer());
    expect(await until(() => [...document.querySelectorAll(".turn")].pop()?.querySelector('.care-under .row-act')?.textContent === "Open House health"),
      "an answer about the house's health did not end with Open House health");
    await askFor("Run the hub drill");
    expect(await answered("Starting the drill: an update leaves Hub stopped."), "Ask did not start the drill: " + await lastAnswer());
    expect(await until(() => [...document.querySelectorAll(".turn")].pop()?.querySelector(".care-under .care-drill b")?.textContent.startsWith("Drill finished"), null, 25000),
      "the drill didn't play through under the answer");
    expect(await until(() => [...[...document.querySelectorAll(".turn")].pop().querySelectorAll(".care-drill p")].some((p) => p.textContent.includes("start Hub again"))),
      "the drill didn't show its steps under the answer");
    await page.waitForTimeout(300); await shot("6g-ask-drill");
    await askFor("Restart Home Assistant");
    expect(await answered("That one waits for your tap."), "restarting Home Assistant did not wait for a tap: " + await lastAnswer());
    expect(await until(() => [...document.querySelectorAll(".wait .wait-t")].pop()?.textContent === "Restart Home Assistant?"), "the caretaker's card did not ask about the restart");
    expect((await page.locator(".wait .btn.go").last().innerText()) === "Restart", "the restart card's button didn't say Restart");
    await page.waitForTimeout(300); await shot("6h-ask-restart-card");
    if (phone) expect((await small()).length === 0, "tap targets under 44 px on the caretaker's card: " + (await small()).join(", "));
    await page.locator(".wait .btn.ghost").last().click();
    expect(await until(() => [...document.querySelectorAll(".wait .took span")].pop()?.textContent === "Left as it was."), "Not now did not put the caretaker's card away");
    expect(await page.evaluate(() => fetch("/app/api/health").then((r) => r.json()).then((h) => h.needs.some((n) => n.title === "Sonos keeps failing"))),
      "the restart was asked for without a tap");

    // ---- Ask: the shopping list. One assistant: the list is worked here, and its cards are drawn here ----
    await askFor("Add sponges and dragon fruit to the list");
    expect(await answered("Sponges and dragon fruit are on the list. One thing needs a product picked below."), "Ask did not add to the list: " + await lastAnswer());
    expect(await until(() => [...document.querySelectorAll(".turn")].pop()?.querySelector(".groceries .row.item .row-main span")?.textContent === "Brightside Non-Scratch Scrub Sponges, 6 ct"),
      "the product Hub picked was not drawn under the answer");
    expect(await until(() => [...document.querySelectorAll(".turn")].pop()?.querySelectorAll(".pickblock .opt").length === 2), "what needs a pick was not offered under the answer");
    await page.waitForTimeout(300); await shot("6f-ask-list");
    if (phone) expect((await small()).length === 0, "tap targets under 44 px on the list in Ask: " + (await small()).join(", "));
    await page.locator(".turn").last().locator(".pickblock .opt").first().click();
    expect(await until(() => [...document.querySelectorAll(".turn")].pop()?.querySelectorAll(".groceries .row.item").length === 2), "a pick in Ask did not take");
    await askFor("what's in the cart");
    expect(await answered("Reading the cart. This takes a minute."), "Ask did not read the cart: " + await lastAnswer());
    expect(await until(() => [...document.querySelectorAll(".turn")].pop()?.querySelectorAll(".groceries .kitems li").length >= 2, null, 8000),
      "the cart as its page showed it was not drawn under the answer");
    expect(await until(() => [...document.querySelectorAll(".turn")].pop()?.querySelector(".groceries .quiet-line")?.textContent.startsWith("As the cart’s own page showed it")),
      "the cart under the answer did not say when it was read");
    await askFor("add 6 eggs to the list");
    expect(await answered("Eggs changed to six."), "a thing said again with a number was not changed: " + await lastAnswer());
    expect(await until(() => [...document.querySelectorAll(".turn")].pop()?.querySelector(".groceries .row.item .row-main b")?.textContent === "eggs × 1 (for 6)"),
      "a pack that covers the number asked for was not shown as one");
    if (phone) expect((await small()).length === 0, "tap targets under 44 px in Ask: " + (await small()).join(", "));
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "Ask made the page scroll sideways");
    expect(!(await sideways()), "the conversation grew wider than the screen");
    await page.fill("#askq", "");
    await tap(phone ? ".who" : ".rail .foot .tab");
    expect(await until(() => [...document.querySelectorAll(".block h2")].some((h) => h.textContent === "Hub remembers")), "System did not list what Hub remembers");
    await page.locator(".block .forget").first().click();
    expect(await until(() => ![...document.querySelectorAll(".block h2")].some((h) => h.textContent === "Hub remembers")), "Forget did not forget");
    await tap(phone ? '.tabs .tab:text-is("House")' : '.rail .tab:text-is("House")');
    await page.waitForSelector(".plan");

    // ---- where Ask isn't set up yet, what is typed still goes somewhere: Hub's older chat ----
    await stage("reset"); await stage("ask-away");
    await page.reload(); await page.waitForSelector(".plan");
    await tap(phone ? '.tabs .tab:text-is("Ask")' : '.rail .tab:text-is("Ask")'); await page.waitForSelector(".askpage");
    expect(await until(() => document.querySelector(".ask-hint")?.textContent.endsWith("Anything else goes to the chat.")), "Ask did not say that it hands over to the chat");
    await page.route(base + "/ask", (route) => route.fulfill({ status: 200, contentType: "text/html", body: "<title>The chat</title>" }));
    await askFor("play something calm");
    expect(await page.waitForURL(base + "/ask", { timeout: 5000 }).then(() => true, () => false), "what was typed did not open the chat");
    expect(await page.evaluate(() => sessionStorage.getItem("hub.say")) === "play something calm", "what was typed did not go to the chat with it");
    await page.unroute(base + "/ask");
    await stage("ask-back");
    await page.goto(base + "/app/#house"); await page.waitForSelector(".plan"); await page.waitForTimeout(1900);

    // ---- Today: the family brief. What needs you, the rest of the evening, tomorrow and its weather, the house ----
    await stage("reset");
    await page.evaluate(() => fetch("/app/api/today").then(() => new Promise((r) => setTimeout(r, 300))));     // the note, written as on a first look
    await page.reload(); await page.waitForSelector(".plan");      // and the day read again with it, as the next look would
    await openPage("Today");
    expect(await saysSoon("Dinner at home is at seven. Ten things need you."), "Today’s sentence: " + await says());
    expect(await sub() === "Tomorrow: sunny, high 56°, low 41°. Dry all day.", "Today’s weather sentence: " + await sub());
    expect((await page.locator(".row.need .row-main b").allInnerTexts()).join("|")
      === "Side door lock’s battery is flat|Email from Sam Rivera|Approve the grocery cart|Email from Maple Street Dental",
      "what needs you: the flat lock first, then the longest wait");
    expect((await page.locator(".block h2").allInnerTexts()).join("|")
      === "Needs you|Ready for your tap|This evening|Tomorrow|Tomorrow’s weather|The house|This week at school|Lisbon|Dashboards", "Today’s sections, in order");
    expect(await until(() => document.querySelector(".brief-note")?.textContent.startsWith("Alex is away tomorrow")), "the note from Ask’s model was not shown");
    expect((await page.locator(".houserow .state").allInnerTexts()).join("|") === "Not responding|Locked|Locked|Disarmed|Closed", "the house: " +
      (await page.locator(".houserow .state").allInnerTexts()).join("|"));
    expect(await page.locator(".wxday").count() === 4, "the days ahead were not drawn");
    expect(await page.locator(".block .errand").count() === 3, "Today did not show the three most pressing errands");
    expect(await page.locator('.block .quiet-line:has-text("3 more errands.") a:text-is("All errands")').count() === 1, "Today did not lead to the other errands");
    expect(!(await page.locator(".row-main b").allInnerTexts()).includes("Bring a small pumpkin"), "a class to-do with an errand on Today was listed twice");
    expect(await page.locator(".row.warn .row-main span").innerText() === "Overlaps Dentist at 4:00 pm", "two things at once were not marked on the day");
    expect(await page.locator(".row.struck").count() === 1, "a cancelled class date was not struck through");
    expect((await page.locator(".quiet-line").last().innerText()).startsWith("Hub filed 12 emails."), "housekeeping was not told quietly");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "Today scrolls sideways");
    await page.waitForTimeout(300); await shot("10-today");
    if (phone) expect((await small()).length === 0, "tap targets under 44 px on Today: " + (await small()).join(", "));
    await page.locator('.row.need:has-text("Email from Sam Rivera") .tickbtn').click();
    expect(await saysSoon("Dinner at home is at seven. Nine things need you.", 3000), "a tick did not take the email off the list: " + await says());
    expect(await until(() => document.querySelector(".toast span")?.textContent.trim() === "Marked as handled"), "a tick did not say what it did");
    await page.locator(".toast button").click();
    expect(await saysSoon("Dinner at home is at seven. Ten things need you.", 3000), "Undo did not put the email back: " + await says());
    await tap('.row.need .row-act:text-is("Add to list")');
    expect(await until(() => document.querySelector(".toast span")?.textContent.includes("AA batteries")), "Add to list did not put the batteries on the list: "
      + await page.textContent(".toast span").catch(() => "no toast"));
    await stage("quiet-day");
    await page.reload(); await page.waitForSelector(".say");
    expect(await saysSoon("Nothing needs you.", 4000), "a quiet day: " + await says());
    await page.waitForTimeout(1900); await shot("11-today-quiet");
    await stage("reset");

    // ---- Errands: what Hub got ready from the mail and the class guide, each finished with a tap ----
    await page.reload(); await page.waitForSelector(".say");
    expect(await saysSoon("Dinner at home is at seven. Ten things need you."), "Today, after a quiet day: " + await says());
    await tap('.block a:text-is("All errands")');
    expect(await saysSoon("Six things are ready for your tap."), "Errands’ sentence: " + await says());
    expect((await sub()).endsWith("Nothing is sent, paid or booked until you do it."), "Errands’ second line: " + await sub());
    expect(await page.locator(".errands .errand").count() === 6, "Errands did not show the six errands");
    expect((await page.locator(".errands .block h2").allInnerTexts()).join("|") === "Done lately|Replies open in", "Errands’ sections, in order");
    expect((await page.locator(".errands .block .row-main b").allInnerTexts()).join("|") === "Water bill pays itself", "a bill that pays itself was not under Done lately");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "Errands scrolls sideways");
    await page.waitForTimeout(300); await shot("14-errands");
    if (phone) expect((await small()).length === 0, "tap targets under 44 px on Errands: " + (await small()).join(", "));
    const errand = (words) => page.locator(".errands .errand").filter({ hasText: words });
    // the one thing Hub does itself: the class guide's pumpkin goes on the shopping list, with Undo
    await errand("small pumpkin").locator(".btn.go").click();
    expect(await until(() => document.querySelector(".toast span")?.textContent.trim() === "Small pumpkin is on the list"), "Add to the list did not say what it did");
    expect(await saysSoon("Five things are ready for your tap.", 3000), "an errand stayed after its only step: " + await says());
    await page.locator(".toast button").click();
    expect(await saysSoon("Six things are ready for your tap.", 3000), "Undo did not put the errand back: " + await says());
    // everything about one errand: its ways, their steps and the words drafted
    await errand("Jordan’s birthday party").locator(".er-t").click();
    expect(await until(() => document.querySelector(".sheet.in .er-way h2")?.textContent === "Yes, we’ll come"), "the errand did not open");
    expect((await page.locator(".sheet .er-way h2").allInnerTexts()).join("|") === "Yes, we’ll come|Can’t make it", "the errand did not offer both ways");
    expect((await page.locator(".sheet .er-step b").allInnerTexts()).join("|") === "Reply to Jamie Chen|Add to calendar|Reply to Jamie Chen", "the steps of each way");
    expect((await page.locator(".sheet .er-draft").first().innerText()).includes("We’d love to come to Jordan’s party."), "the drafted reply was not shown");
    await page.waitForTimeout(400); await shot("14b-errand");
    if (phone) expect((await small()).length === 0, "tap targets under 44 px on an errand: " + (await small()).join(", "));
    await page.keyboard.press("Escape");
    expect(await until(() => !document.querySelector(".sheet")), "the errand did not close");
    // a reply opened in the mail app is noted, and Hub closes the errand once the reply is in sent mail
    await page.evaluate(() => document.addEventListener("click", (e) => {
      if (e.target.closest?.("a[href^='mailto:'], a[target='_blank']")) e.preventDefault();       // no mail app and no other site here
    }, true));
    await errand("Jordan’s birthday party").locator('.btn:text-is("Can’t make it")').click();
    expect(await until(() => document.querySelector(".errand.started .er-open")?.textContent.includes("Hub closes this when it sees your reply.")),
      "opening the reply was not noted on its errand");
    await page.waitForTimeout(300); await shot("14c-errand-opened");
    await stage("errands-replied");
    await tap('.btn:text-is("Look again")');
    expect(await until(() => document.querySelector(".toast span")?.textContent.trim() === "You replied: RSVP to Jordan’s birthday party", null, 10000),
      "the reply in sent mail did not close the errand");
    expect(await saysSoon("Five things are ready for your tap.", 3000), "the errand that was replied to stayed: " + await says());
    expect((await page.locator(".errands .block .row-main b").allInnerTexts()).includes("RSVP to Jordan’s birthday party"), "the errand replied to was not under Done lately");
    await stage("reset");
    // the other person at home, whose own mail Hub doesn't read: the class guide's two only, in Errands, Today and Ask
    const partnerCtx = await browser.newContext({ ...options, extraHTTPHeaders: { "tailscale-user-login": "casey@example.com" } });
    const partner = await partnerCtx.newPage();
    partner.on("console", (m) => m.type() === "error" && failures.push(`${name}: partner console: ${m.text()}`));
    partner.on("pageerror", (e) => failures.push(`${name}: partner page error: ${e}`));
    partner.on("request", (r) => hosts.add(new URL(r.url()).host));
    partner.on("response", (r) => r.status() >= 400 && failures.push(`${name}: partner ${r.status()} ${new URL(r.url()).pathname}`));
    await partner.goto(base + "/app/#errands");
    expect(await partner.waitForFunction(() => document.querySelector(".say")?.textContent.trim() === "Two things are ready for your tap.", null, { timeout: 6000 }).then(() => true, () => false),
      "the partner’s Errands sentence: " + await partner.textContent(".say"));
    expect((await partner.locator(".errands .errand .er-t").allInnerTexts()).every((t) => /pumpkin|photo order form/.test(t)), "the partner saw an errand from the owner’s mail");
    expect((await partner.textContent(".sub")).trim().endsWith("Only the class guide’s are here: Hub doesn’t read your own mail."), "the partner’s Errands second line");
    expect(!(await partner.locator(".errands .block h2").allInnerTexts()).includes("Replies open in"), "the partner was offered where replies open");
    await partner.waitForTimeout(300); await partner.screenshot({ path: `${out}/${name}-14d-errands-partner.png` });
    const partnerToday = await partner.evaluate(() => fetch("/app/api/today").then((r) => r.json()).then((t) => t.ready.cards.map((c) => c.title)));
    expect(partnerToday.length === 2 && partnerToday.every((t) => /pumpkin|photo order form/.test(t)), "the partner’s Today showed another’s errands: " + partnerToday.join("|"));
    // ... Ask is the partner's own conversation: their own morning message, none of the owner's mail or bills in it,
    // what they ask stays in theirs (and is still there when they come back), and the owner's is untouched
    const ownerLast = async () => (await page.evaluate(() => fetch("/app/api/ask").then((r) => r.json()))).recent.map((t) => t.id).join(",");
    const ownerBefore = await ownerLast();
    await partner.goto(base + "/app/");
    await partner.waitForSelector(".askpage .morning .row-main b", { timeout: 6000 }).catch(() => undefined);
    const partnerMorning = (await partner.locator(".askpage .morning .row-main b").allInnerTexts()).join("|");
    expect(partnerMorning.startsWith("Side door lock’s battery is flat."), "the partner’s morning message: " + partnerMorning);
    expect(!/Sam Rivera|Lakeside Power/.test(partnerMorning), "the partner’s morning message had the owner’s mail or bills: " + partnerMorning);
    await partner.fill("#askq", "dim the kitchen");
    await partner.waitForFunction(() => document.querySelector("#askq")?.parentElement?.querySelector(".send.on") !== null, null, { timeout: 6000 }).catch(() => undefined);
    await partner.keyboard.press("Enter");
    const partnerAnswered = () => partner.waitForFunction(() => [...document.querySelectorAll(".turn .answer")].some((a) => a.textContent.startsWith("The kitchen is at")),
      null, { timeout: 8000 }).then(() => true, () => false);
    expect(await partnerAnswered(), "the partner’s Ask did not answer");
    await partner.reload(); await partner.waitForSelector(".askpage");
    expect(await partnerAnswered(), "the partner’s conversation did not keep their turn");
    await partner.waitForTimeout(300); await partner.screenshot({ path: `${out}/${name}-6g-ask-partner.png` });
    expect(await ownerLast() === ownerBefore, "the partner’s turn reached the owner’s conversation");
    // ... and the mail is the owner's alone: Mail and Mail settings say the partner's isn't connected and show none of
    // the owner's, System doesn't offer the older Gmail and Calendar pages, and opened anyway they say whose they are
    const partnerSays = (words) => partner.waitForFunction((w) => document.querySelector(".say")?.textContent.trim() === w, words, { timeout: 6000 })
      .then(() => true, () => false);
    await partner.goto(base + "/app/#mail");
    expect(await partnerSays("Your mail isn’t connected yet"), "the partner’s Mail: " + await partner.textContent(".say"));
    expect(!/Sam Rivera|Maple Street Dental|Saturday plans/.test(await partner.textContent("body")), "the partner saw the owner’s mail");
    expect(await partner.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "the partner’s Mail scrolls sideways");
    await partner.waitForTimeout(300); await partner.screenshot({ path: `${out}/${name}-12c-mail-partner.png` });
    await partner.goto(base + "/app/#mailsettings");
    expect(await partnerSays("Your mail isn’t connected yet"), "the partner’s Mail settings: " + await partner.textContent(".say"));
    expect(!(await partner.textContent("body")).includes("news@example.com"), "the partner saw the owner’s mail settings");
    await partner.goto(base + "/app/#system");
    await partner.waitForSelector('a.row.free[href="/trip"]', { timeout: 6000 });
    const olderPages = await partner.locator("a.row.free").evaluateAll((links) => links.map((a) => a.getAttribute("href")));
    expect(!olderPages.includes("/gmail") && !olderPages.includes("/calendar"), "the partner was offered the owner’s Gmail or Calendar: " + olderPages.join(" "));
    expect((await partner.locator('.block:has(h2:text-is("How Hub checks itself")) .row b').allInnerTexts()).join("|") === "Status|Checks|Golden tests|Ask activity",
      "System did not list how Hub checks itself");
    const oldMenu = await partner.goto(base + "/settings");
    expect(new URL(partner.url()).hash === "#system" && oldMenu.ok(), "the older Settings address did not open System: " + partner.url());
    const olderGmail = await partner.goto(base + "/gmail");
    expect(olderGmail.status() === 200 && (await partner.textContent("main h1")).trim() === "Not yours to open", "the older Gmail page opened for the partner");
    // ... and Ask searches none of it for the partner
    await partner.goto(base + "/app/");
    await partner.waitForSelector(".askpage #askq");
    await partner.fill("#askq", "Search my mail for sink");
    await partner.waitForFunction(() => document.querySelector("#askq")?.parentElement?.querySelector(".send.on") !== null, null, { timeout: 6000 }).catch(() => {});
    await partner.keyboard.press("Enter");
    expect(await partner.waitForFunction(() => [...document.querySelectorAll(".turn .answer")].pop()?.textContent.startsWith("Hub shows mail and calendars only to the person"),
      null, { timeout: 8000 }).then(() => true, () => false), "Ask searched the owner’s mail for the partner: " + await partner.locator(".turn .answer").last().innerText().catch(() => "(nothing)"));
    expect(await partner.locator(".mailrows").count() === 0 && !(await partner.textContent("body")).includes("Riverside"), "the partner saw the owner’s emails in Ask");
    await partner.waitForTimeout(300); await partner.screenshot({ path: `${out}/${name}-6c3-ask-mail-partner.png` });
    await partnerCtx.close();

    // ---- Mail: what needs you, who never answered, what Hub sorted ----
    await openPage("Mail");
    expect(await saysSoon("Two emails need you."), "Mail’s sentence: " + await says());
    expect((await page.locator(".wrap.mail .row.need .row-main b").allInnerTexts()).join("|") === "Sam Rivera|Maple Street Dental|Saturday plans?",
      "Mail: the emails that need you, the longest wait first, then the unanswered question");
    expect((await page.locator(".wrap.mail .block h2").allInnerTexts()).join("|") === "Needs you|You asked, no answer yet|Sorted", "Mail’s sections, in order");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "Mail scrolls sideways");
    await page.waitForTimeout(300); await shot("12-mail");
    if (phone) expect((await small()).length === 0, "tap targets under 44 px on Mail: " + (await small()).join(", "));
    await page.locator(".wrap.mail .tickbtn").first().click();
    expect(await saysSoon("One email needs you.", 3000), "a tick did not take the email off Mail: " + await says());
    await page.locator(".toast button").click();
    expect(await saysSoon("Two emails need you.", 3000), "Undo did not put the email back on Mail: " + await says());
    await tap('.row-act:text-is("File 3")');
    expect(await until(() => !document.querySelector('.row-act') || ![...document.querySelectorAll(".row-act")].some((b) => b.textContent === "File 3"), null, 4000),
      "filing did not empty the reading shelf");
    await tap('.btn:text-is("Rules and settings")');
    expect(await saysSoon("Mail settings"), "Mail settings: " + await says());
    expect(await until(() => document.body.textContent.includes("news@example.com")), "Mail settings didn't show the plan");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "Mail settings scrolls sideways");
    await page.waitForTimeout(300); await shot("12b-mail-settings");
    if (phone) expect((await small()).length === 0, "tap targets under 44 px on Mail settings: " + (await small()).join(", "));
    await tap(".uplink");
    expect(await saysSoon("emails need you", 3000) || await saysSoon("email needs you", 1000) || true, "back to Mail");

    // ---- Dashboards: a page for each part of the household, arranged by each person; Ask answers with one ----
    await stage("reset");
    await openPage("Dashboards");
    expect(await saysSoon("Dashboards"), "the Dashboards page: " + await says());
    expect(await until(() => document.querySelectorAll(".wrap .rows .row b").length === 7), "the owner did not get seven dashboards");
    await page.locator(".wrap .rows .row", { hasText: "Pregnancy" }).first().click();
    expect(await saysSoon("Week 15: about the size of an apple"), "the pregnancy board: " + await says());
    expect(new URL(page.url()).hash === "#board/pregnancy", "a board has no address of its own: " + page.url());
    expect((await page.locator(".board .bhero b").first().innerText()) === "14 weeks, 6 days", "where the pregnancy is");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "the pregnancy board scrolls sideways");
    await page.waitForTimeout(300); await shot("16-board-pregnancy");
    if (phone) expect((await small()).length === 0, "tap targets under 44 px on a board: " + (await small()).join(", "));
    const leave = page.locator('[data-block="prep"] .row', { hasText: "parental leave" });
    await leave.locator(".tickbtn").click();
    expect(await until(() => [...document.querySelectorAll('[data-block="prep"] .row.on')].some((r) => r.textContent.includes("parental leave"))), "a tick on Getting ready did not take");
    await leave.locator(".tickbtn").click();
    expect(await until(() => ![...document.querySelectorAll('[data-block="prep"] .row.on')].some((r) => r.textContent.includes("parental leave"))), "a second tick did not put it back");
    await page.locator('.btns .btn:text-is("Arrange")').click();
    await page.locator('[data-block="months"] .bhide').click();
    expect(await until(() => !document.querySelector('[data-block="months"]')), "Hide did not take the block away");
    await page.waitForTimeout(300); await shot("16b-board-arranging");
    await page.locator('.row', { hasText: "Month by month" }).locator('.row-act:text-is("Show")').click();
    expect(await until(() => !!document.querySelector('[data-block="months"]')), "Show did not bring the block back");
    await page.locator('[data-block="numbers"] button[aria-label="Move up"]').click();
    expect(await until(() => document.querySelector(".bgrid > .block")?.dataset.block === "numbers"), "Move up did not move the block");
    await page.locator('[data-block="numbers"] .down').click();
    expect(await until(() => document.querySelector(".bgrid > .block")?.dataset.block === "progress"), "Move down did not move it back");
    await page.locator('.btns .btn:text-is("Done")').click();
    if (phone) await page.goto(base + "/app/#board/network");
    else {          // on a wide screen the rail lists each dashboard under Ask and House: one click opens it
      const listed = await page.locator(".rail .railboards .rlink:not(.head)").allInnerTexts();
      expect(listed.join("|") === "Pregnancy|Calendar|School|Trip|Network|Mail|AI spend", "the rail's dashboards: " + listed.join("|"));
      await page.locator('.rail .railboards .rlink:text-is("Network")').click();
    }
    expect(await saysSoon("Internet is up"), "the network board: " + await says());
    if (!phone) expect(await page.locator('.rail .railboards .rlink.on').innerText() === "Network", "the rail did not mark the open dashboard");
    expect(await page.locator('[data-block="download"] polyline').count() === 1, "the network board drew no download line");
    await page.waitForTimeout(300); await shot("16c-board-network");
    await page.goto(base + "/app/#board/mail");
    expect(await saysSoon("2 emails need you"), "the mail board: " + await says());
    expect(await page.locator('[data-block="kinds"] .bbar').count() > 0, "the mail board drew no bars");
    await page.waitForTimeout(300); await shot("16d-board-mail");
    // The older Calendar and Insights pages are boards now: their addresses open them.
    await page.goto(base + "/calendar");
    expect(await until(() => location.hash === "#board/calendar"), "the older Calendar address did not open the board: " + page.url());
    expect(await until(() => document.querySelectorAll('[data-block="today"] .row, [data-block="week"] .row').length > 0), "the calendar board listed nothing this week");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "the calendar board scrolls sideways");
    await page.waitForTimeout(300); await shot("16f-board-calendar");
    await page.goto(base + "/insights");
    expect(await until(() => location.hash === "#board/spend"), "the older Insights address did not open the AI spend board: " + page.url());
    expect(await until(() => document.querySelector(".say")?.textContent.startsWith("Within limits in")), "the AI spend board: " + await says());
    expect(await page.locator('[data-block="month"] .btile').count() === 2, "the AI spend board did not show both limits");
    await page.waitForTimeout(300); await shot("16g-board-spend");
    // The trip and the class guide have boards; their own pages stay as the places to change them.
    await page.goto(base + "/app/#board/trip");
    expect(await until(() => / in \d+ days$|tomorrow$|on now$/.test(document.querySelector(".say")?.textContent ?? "")), "the trip board: " + await says());
    expect(await page.locator('[data-block="edit"] a[href="/trip"]').count() === 1, "the trip board did not lead to the trip workspace");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "the trip board scrolls sideways");
    await page.waitForTimeout(300); await shot("16h-board-trip");
    await page.goto(base + "/app/#board/school");
    expect(await until(() => document.querySelector('[data-block="more"] a[href="/preschool/"]') !== null), "the school board did not lead to the class guide: " + await says());
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "the school board scrolls sideways");
    await page.waitForTimeout(300); await shot("16i-board-school");
    quiet = true;       // the mail board is the owner's: the partner is told no
    const partnerBoard = await page.evaluate(() => fetch("/app/api/board/mail", { headers: { "tailscale-user-login": "casey@example.com" } }).then((r) => r.status));
    const partnerList = await page.evaluate(() => fetch("/app/api/boards", { headers: { "tailscale-user-login": "casey@example.com" } }).then((r) => r.json()).then((b) => b.boards.map((x) => x.id).join("|")));
    quiet = false;
    expect(partnerBoard === 404 && partnerList === "pregnancy|school|trip|network|spend", `the partner's dashboards: ${partnerList}, mail ${partnerBoard}`);
    await tap(phone ? '.tabs .tab:text-is("Ask")' : '.rail .tab:text-is("Ask")');
    await page.fill("#askq", "how far along are we?");
    await page.keyboard.press("Enter");
    expect(await until(() => [...document.querySelectorAll(".bcard .bcard-say")].pop()?.textContent === "Week 15: about the size of an apple", null, 8000),
      "Ask did not answer with the pregnancy board");
    await page.waitForTimeout(300); await shot("16e-ask-board");

    // ---- Kitchen: the list on its way into the cart, what needs a person, and no second conversation ----
    await stage("reset");
    await openPage("Kitchen");
    expect(await saysSoon("A cart waits for your tap."), "Kitchen’s sentence: " + await says());
    expect(await sub() === "Two of four are in the cart. This is a practice cart: nothing in the shop is touched.", "Kitchen’s second line: " + await sub());
    const stands = () => page.locator(".kitchen .rows .row.item .row-q").allInnerTexts().then((all) => all.join("|"));
    expect(await stands() === "In the cart|In the cart|Waiting|Needs a pick", "where each thing on the list stands: " + await stands());
    expect((await page.locator(".kitchen .rows .row.item").nth(1).locator(".row-main span").innerText()) === "Meadow Farm Large Brown Eggs, 12 ct", "the product picked for eggs");
    expect(await page.locator(".kitchen .pickblock .opt").count() === 1 && await page.locator(".kitchen .kcard").count() >= 3, "what needs you, and the ideas");
    expect(await page.locator(".kitchen .composer").count() === 1 && await page.locator(".kitchen textarea").count() === 0, "Kitchen has a field for the list and no second conversation");
    await page.waitForTimeout(300); await shot("13-kitchen");
    if (phone) expect((await small()).length === 0, "tap targets under 44 px on Kitchen: " + (await small()).join(", "));
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "Kitchen scrolls sideways");
    await page.locator(".kitchen .rows .row.item").nth(2).click();
    expect(await until(() => document.querySelector(".sheet h1")?.textContent === "coffee beans" && document.querySelector(".sheet .chosen span:last-child")?.textContent === "Your usual."),
      "a thing on the list did not open with why Hub chose its product");
    await page.waitForTimeout(350); await shot("13b-kitchen-item");
    if (phone) expect((await small()).length === 0, "tap targets under 44 px on a thing’s sheet: " + (await small()).join(", "));
    await page.locator('.sheet .stepper button[aria-label="One more"]').click();
    expect(await until(() => document.querySelector(".sheet h1")?.textContent === "coffee beans × 2" && document.querySelector(".sheet .stepper output")?.textContent === "2"),
      "one more did not make it two");
    expect(await page.locator('.sheet .stepper button[aria-label="One fewer"]').isEnabled(), "one fewer was not offered at two");
    await page.locator('.sheet .stepper button[aria-label="One fewer"]').click();
    expect(await until(() => document.querySelector(".sheet h1")?.textContent === "coffee beans"), "one fewer did not make it one again");
    expect(await page.locator('.sheet .stepper button[aria-label="One fewer"]').isDisabled(), "one fewer was offered at one");
    await page.keyboard.press("Escape");
    await page.fill(".kadd .field", "sponges, 2 avocados");
    await page.keyboard.press("Enter");
    expect(await until(() => document.querySelectorAll(".kitchen .rows .row.item").length === 6), "typing on the list did not add to it");
    expect((await page.locator(".kitchen .rows .row.item").nth(5).locator(".row-main").innerText()).replace(/\s+/g, " ") === "avocados × 2 Green Valley Large Ripe Avocado, 1 each", "two avocados, and which");
    await tap('.kitchen .kcard .btn.go:text-is("Add to cart")');
    expect(await saysSoon("One thing needs a pick."), "approving the plan: " + await says());
    await page.locator(".kitchen .pickblock .opt").first().click();
    expect(await saysSoon("Four things are ready for the cart."), "after a pick: " + await says());
    await tap('.kitchen .btn.go:text-is("Add all 4 to the cart")');
    expect(await saysSoon("Four things are going in the cart."), "filling the cart: " + await says());
    expect(await page.locator(".kitchen .row.item.going").count() === 4, "the things going in are not marked");
    await page.waitForTimeout(200); await shot("13c-kitchen-going");
    expect(await saysSoon("Everything on the list is in the cart.", 9000) && (await stands()) === "In the cart|In the cart|In the cart|In the cart|In the cart|In the cart", "the list did not land in the cart: " + await stands());
    await tap('.kitchen .btn.ghost:text-is("Read the cart")');
    expect(await until(() => document.querySelectorAll(".kitchen .kitems li").length === 7, null, 6000), "the cart as its page shows it was not read");
    // A recipe's "Plan what’s missing": Kitchen's answer shows under the recipe, and the button never sticks.
    const idea = (n) => `.kitchen .ideas > .kcard:nth-of-type(${n})`;
    await tap(idea(1) + " > .row-act");
    expect(await until((sel) => document.querySelector(sel + " .kitchen-says .took")?.textContent.includes("working on it"), idea(1)),
      "planning a recipe did not say that Kitchen is working on it");
    expect(await page.locator(idea(1) + " > .row-act").count() === 0, "the recipe’s button was still offered while Kitchen worked");
    expect(await until((sel) => document.querySelector(sel + " .kitchen-says .kcard .btn.go")?.textContent === "Add to cart", idea(1), 12000),
      "the recipe’s cart plan did not arrive under it");
    expect(await saysSoon("A cart waits for your tap.", 4000), "the recipe’s plan did not wait under Needs you: " + await says());
    await page.locator(idea(1)).scrollIntoViewIfNeeded(); await page.waitForTimeout(300); await shot("13g-kitchen-recipe-plan");
    if (phone) expect((await small()).length === 0, "tap targets under 44 px on a recipe’s plan: " + (await small()).join(", "));
    await tap(idea(1) + ' .kitchen-says .btn.go:text-is("Add to cart")');
    expect(await until((sel) => document.querySelector(sel + " .kitchen-says .kstate")?.textContent.startsWith("Added to the cart"), idea(1), 12000),
      "adding the recipe’s plan did not say so under the recipe");
    // Kitchen's Claude plan at its limit: the recipe says so, and offers the button again.
    await stage("kitchen-limit");
    await tap(idea(2) + " > .row-act");
    expect(await until((sel) => document.querySelector(sel + " .kitchen-says .kmsg.notice")?.textContent.includes("usage limit"), idea(2), 12000),
      "a Kitchen at its limit did not say so under the recipe");
    expect(await until((sel) => { const b = document.querySelector(sel + " > .row-act"); return b?.textContent === "Plan what’s missing" && !b.disabled; }, idea(2)),
      "the recipe’s button did not come back after Kitchen couldn’t answer");
    await page.locator(idea(2)).scrollIntoViewIfNeeded(); await page.waitForTimeout(300); await shot("13h-kitchen-recipe-limit");
    // An older Kitchen passed the limit on as if it were the answer: its words show, and the button comes back again.
    await stage("kitchen-limit-before");
    await tap(idea(2) + " > .row-act");
    expect(await until((sel) => document.querySelector(sel + " .kitchen-says .kwords")?.textContent.includes("session limit"), idea(2), 12000),
      "an older Kitchen’s limit did not show under the recipe");
    expect(await until((sel) => { const b = document.querySelector(sel + " > .row-act"); return b?.textContent === "Plan what’s missing" && !b.disabled; }, idea(2)),
      "the recipe’s button did not come back after an older Kitchen’s limit");
    await stage("kitchen-unlimited");
    // A cart turn that stopped before it could say: Kitchen asks for the one thing that settles it.
    await stage("kitchen-unsure");
    await tap(phone ? '.tabs .tab:text-is("House")' : '.rail .tab:text-is("House")');
    await openPage("Kitchen");
    expect(await saysSoon("One thing to check in the cart."), "a thing nobody could confirm: " + await says());
    expect((await page.locator(".kitchen .ctl.check .ctl-name b").innerText()) === "One thing to check", "what to check was not asked for under Needs you");
    if (phone) expect((await small()).length === 0, "tap targets under 44 px on a thing to check: " + (await small()).join(", "));
    await page.waitForTimeout(200); await shot("13e-kitchen-check");
    await tap('.kitchen .ctl.check .pill');
    expect(await saysSoon("Everything on the list is in the cart.", 6000), "reading the cart did not settle it: " + await says());
    await page.locator('.kitchen [role="switch"]').first().click();
    expect(await until(() => document.querySelector('.kitchen [role="switch"]')?.getAttribute("aria-checked") === "true"), "the switch that fills the cart by itself did not switch");
    await page.fill(".kadd .field", "limes");
    await page.keyboard.press("Enter");
    expect(await saysSoon("One thing is going in the cart."), "switched on, a new thing did not go in by itself: " + await says());
    await tap('.kitchen .btn.go:text-is("Check out")');
    expect(await saysSoon("An order waits for your tap.", 9000), "checkout was not read back: " + await says());
    await page.waitForTimeout(300); await shot("13d-kitchen-order");
    await tap('.kitchen .kcard .btn.go:text-is("Place order")');
    expect(await saysSoon("The list is empty.", 6000), "placing the order did not clear the list: " + await says());
    expect(await until(() => document.querySelector(".kitchen .block .quiet")?.textContent.startsWith("Nothing on it.")), "an empty list did not say how to add to it");
    // Two stores: a thing bought at Costco says so, moves to Fred Meyer with one tap, and each store's cart has its link.
    await page.locator('.kitchen [role="switch"]').first().click();
    expect(await until(() => document.querySelector('.kitchen [role="switch"]')?.getAttribute("aria-checked") === "false"), "the switch that fills the cart by itself did not switch off");
    await page.fill(".kadd .field", "paper towels");
    await page.keyboard.press("Enter");
    expect(await until(() => document.querySelector(".kitchen .rows .row.item .store-tag")?.textContent === " · Costco"), "a thing bought at Costco did not say so");
    expect((await page.locator(".kitchen .store-carts a").allInnerTexts()).join("|") === "Open the Fred Meyer cart|Open the Costco cart", "each store’s cart was not offered");
    await page.locator(".kitchen .rows .row.item").first().click();
    expect(await until(() => document.querySelector('.sheet .store-pick .pill[aria-pressed="true"]')?.textContent === "Costco"), "a thing’s sheet did not say its store");
    await page.waitForTimeout(350); await shot("13i-kitchen-store");
    if (phone) expect((await small()).length === 0, "tap targets under 44 px on a thing’s store: " + (await small()).join(", "));
    await tap('.sheet .store-pick .pill:text-is("Fred Meyer")');
    expect(await until(() => document.querySelector('.sheet .store-pick .pill[aria-pressed="true"]')?.textContent === "Fred Meyer"), "moving a thing to Fred Meyer did not move it");
    expect(await until(() => !document.querySelector(".kitchen .rows .row.item .store-tag")), "the list still said Costco after the move");
    await page.keyboard.press("Escape");
    // The kitchen service stopped, and one from before it kept the list: each says so, and nothing looks broken.
    // (A recipe asked about above looks for its answer again when Kitchen is drawn, and is refused while it is away.)
    quiet = true;
    await stage("kitchen-away");
    await tap(phone ? '.tabs .tab:text-is("House")' : '.rail .tab:text-is("House")');
    await openPage("Kitchen");
    expect(await until(() => document.querySelector(".kitchen .banner b")?.textContent === "Kitchen isn’t answering"), "a Kitchen that is away did not say so");
    await stage("kitchen-old");
    await tap(phone ? '.tabs .tab:text-is("House")' : '.rail .tab:text-is("House")');
    await openPage("Kitchen");
    expect(await until(() => document.querySelector(".kitchen .banner b")?.textContent === "Kitchen needs its update"), "an older Kitchen did not say it needs its update");
    expect(await page.locator('.kitchen input[aria-label="Message Kitchen"]').count() === 1, "an older Kitchen was left with no way to be asked");
    await page.waitForTimeout(200); await shot("13f-kitchen-old");
    await stage("reset");
    await tap(phone ? '.tabs .tab:text-is("House")' : '.rail .tab:text-is("House")');
    await page.waitForSelector(".plan");
    quiet = false;

    // ---- System, and the look ----
    await tap(phone ? ".who" : ".rail .foot .tab");
    expect(await saysSoon("The house is answering."), "System: " + await says());
    await page.waitForTimeout(300); await shot("7-system");
    if (phone) expect((await small()).length === 0, "tap targets under 44 px on System: " + (await small()).join(", "));
    await tap('.choice:text-is("Day")');
    expect(await page.evaluate(() => document.documentElement.dataset.theme) === "day", "choosing Day did not change the look");
    await tap('.choice:text-is("Automatic")');
    expect(await page.evaluate(() => document.documentElement.dataset.theme) === (options.colorScheme === "dark" ? "dusk" : "day"), "Automatic did not follow the device");
    expect(await page.locator('.row:has-text("A new version is ready")').count() === 1, "System didn't say a version is waiting");
    await tap('.row:has-text("A new version is ready")');
    expect(await saysSoon("Versions"), "Versions: " + await says());
    expect(await until(() => document.querySelectorAll(".release").length > 2), "Versions listed no releases");
    // A made-up update Hub made by itself an hour ago: Undo, until a request (this one, or an earlier run's) is queued.
    expect(await page.locator('.release.undo h2:text-is("Hub updated itself")').count() === 1, "Versions didn't offer Undo for the update Hub made by itself");
    if (await page.locator(".release.undo .btn").count()) {
      await tap(".release.undo .btn");
      expect(await until(() => document.querySelector(".release.undo h2")?.textContent === "Going back"), "Undo didn't say it is going back");
    } else {
      expect((await page.textContent(".release.undo .quiet")).startsWith("Hub is updating"), "Undo was offered while an update is under way");
    }
    if (await page.locator('.release.ready .btn.go').count()) {
      await page.waitForTimeout(300); await shot("7b-deploy-ready");
      await tap('.release.ready .btn.go');
    }
    expect(await until(() => document.querySelector(".release.ready h2")?.textContent === "Updating Hub"), "Deploy didn't say it is updating");
    await page.waitForTimeout(300); await shot("7b-versions");
    if (phone) expect((await small()).length === 0, "tap targets under 44 px on Versions: " + (await small()).join(", "));
    await tap(".uplink");
    expect(await saysSoon("The house is answering."), "Back from Versions: " + await says());
    await tap('.row:has-text("Activity")');
    expect(await saysSoon("What happened in the house"), "Activity: " + await says());
    expect(await until(() => [...document.querySelectorAll(".ctl-name b")].some((b) => b.textContent.startsWith("Front door opened"))),
      "Activity didn't list Home Assistant's rows");
    expect(await until(() => [...document.querySelectorAll(".ctl-name b")].some((b) => b.textContent.startsWith("Side door (Entry) locked"))),
      "Activity didn't list Apple Home's rows");
    expect(await until(() => document.querySelectorAll(".ctl .forget").length > 0), "Activity showed no Undo for Hub's own changes");
    await page.waitForTimeout(300); await shot("7c-activity");
    if (phone) expect((await small()).length === 0, "tap targets under 44 px on Activity: " + (await small()).join(", "));
    await tap('.scene:text-is("Apple Home")');
    expect(await until(() => ![...document.querySelectorAll(".ctl-name span")].some((s) => s.textContent.includes("Apple Home"))),
      "turning off Apple Home still showed its rows");
    await tap('.scene:text-is("Apple Home")');
    await tap('.quiet-line a:has-text("Hub’s own changes")');
    expect(await saysSoon("Everything Hub changed"), "Changes: " + await says());
    expect(await until(() => document.querySelectorAll(".ctl").length > 0), "Changes listed nothing after the taps above");
    await page.waitForTimeout(300); await shot("7d-changes");
    if (phone) expect((await small()).length === 0, "tap targets under 44 px on Changes: " + (await small()).join(", "));
    await tap(".uplink");
    expect(await saysSoon("What happened in the house"), "Back from Changes: " + await says());
    await tap(".uplink");
    expect(await saysSoon("The house is answering."), "Back from Activity: " + await says());
    // ---- House health: the caretaker's night, what it watches, a drill, and the one thing that needs a tap ----
    expect(await until((sel) => document.querySelectorAll(sel).length === 1, phone ? ".who.care" : ".rail .foot .tab.care"),
      "the top button didn't turn amber for what needs you");
    await tap('.row:has-text("House health")');
    expect(await until(() => document.querySelector(".say")?.textContent.includes("One thing needs you") ||
      document.querySelector(".say")?.textContent.includes("waits for the morning")), "House health: " + await says());
    expect(await until(() => document.body.textContent.includes("Sonos keeps failing")), "House health didn't list what needs you");
    expect(await page.locator('.care-need .btn.go:has-text("Restart Home Assistant")').count() === 1, "no Restart Home Assistant on the item that asks for it");
    expect(await page.locator(".care-row, .care-fine").count() === 14, "House health didn't list every system");
    expect(await page.locator('.care-row[data-system="home_assistant"]').count() === 1, "the system with trouble wasn't a full row");
    await tap('.care-fine[data-system="hue"]');
    expect(await until(() => document.querySelectorAll(".care-history .care-days .care-dot").length === 7), "a system's history didn't show seven days");
    expect(await page.locator(".care-week .care-day").count() === 7, "House health didn't show the last seven days as a strip");
    expect(await until(() => document.querySelector(".care-week + .care-headline")?.textContent.startsWith("This week:")), "no line for the week");
    await tap('.care-week .care-day:has(small:not(:empty))');
    expect(await until(() => document.querySelectorAll(".care-open .care-line").length > 0), "a tapped day didn't show its lines");
    await page.waitForTimeout(300); await shot("7e-health");
    if (phone) expect((await small()).length === 0, "tap targets under 44 px on House health: " + (await small()).join(", "));
    expect(await page.locator('.quiet-line:has-text("Ask can pause the caretaker or run a drill.")').count() === 1, "House health didn't say Ask steers the caretaker");
    expect(await page.locator('.care-acts .row-act:has-text("Pause until I resume")').count() === 1, "House health lost its one brake, Pause");
    expect(await page.locator('.scene:has-text("Hub stops"), .care-acts .btn.go:has-text("Run the drill")').count() === 0, "House health still has the drill controls");
    await tap('.care-need .btn.go:has-text("Restart Home Assistant")');
    expect(await until(() => !document.body.textContent.includes("Sonos keeps failing"), null, 12000), "Restart Home Assistant didn't clear the item");
    expect(await until(() => document.querySelectorAll(".who.care, .rail .foot .tab.care").length === 0, null, 12000),
      "the top button stayed amber after the last thing was sorted");
    await tap(".uplink");
    expect(await saysSoon("The house is answering."), "Back from House health: " + await says());
    await tap('.row:has-text("Engine")');
    expect(await saysSoon("Engine"), "Engine: " + await says());
    expect(await until(() => document.body.textContent.includes("Always tell me about school mail")), "Engine didn't list what Hub follows");
    await page.waitForTimeout(300); await shot("7c-engine");
    if (phone) expect((await small()).length === 0, "tap targets under 44 px on Engine: " + (await small()).join(", "));
    if (await page.locator('.ctl.one:has-text("porch light") .btn.go').count()) {
      await tap('.ctl.one:has-text("porch light") .btn.go');
      expect(await until(() => [...document.querySelectorAll(".ctl.two b")].some((b) => b.textContent.includes("porch light"))), "saving a note didn't list it");
    }
    await tap(".uplink");
    expect(await saysSoon("The house is answering."), "Back from Engine: " + await says());
    await tap(phone ? '.tabs .tab:text-is("House")' : '.rail .tab:text-is("House")');
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

  // ---- the wall: the brief on the kitchen iPad, in the morning, the evening and at night ----
  {
    const name = "wall";
    const expect = (ok, what) => { if (!ok) failures.push(`${name}: ${what}`); };
    const ctx = await browser.newContext({ viewport: { width: 1180, height: 820 }, deviceScaleFactor: 2, hasTouch: true, colorScheme: "light",
      timezoneId: "America/Los_Angeles" });
    const page = await ctx.newPage();
    let quiet = false;
    const live = [];
    page.on("console", (m) => m.type() === "error" && !quiet && failures.push(`${name}: console: ${m.text()}`));
    page.on("pageerror", (e) => failures.push(`${name}: page error: ${e}`));
    page.on("request", (r) => { hosts.add(new URL(r.url()).host); if (new URL(r.url()).pathname === "/app/api/live") live.push(r.url()); });
    page.on("response", (r) => r.status() >= 400 && !quiet && failures.push(`${name}: ${r.status()} ${new URL(r.url()).pathname}`));
    const until = (fn, arg, ms = 6000) => page.waitForFunction(fn, arg, { timeout: ms }).then(() => true, () => false);
    const texts = (sel) => page.locator(sel).allInnerTexts().then((all) => all.join("|"));
    await page.goto(base + "/static/app/build.json");
    const at = async (part) => {         // the made-up clock and this browser's, both at `part`; the note asked for first
      const staged = await page.evaluate((w) => fetch("/demo/" + w, { method: "POST" }).then((r) => r.json()), part);
      await page.evaluate(() => fetch("/app/api/today?wall=1").then(() => new Promise((r) => setTimeout(r, 300))));
      return new Date(staged.at);
    };
    await page.clock.install({ time: await at("morning") });
    await page.goto(base + "/app/#wall");
    await page.waitForSelector(".wall .wblock");
    expect((await page.textContent(".wclock")).trim() === "7:40am", "the wall’s clock: " + await page.textContent(".wclock"));
    expect(await texts(".wall h2") === "Needs attention|The house", "the wall’s morning: " + await texts(".wall h2"));
    expect(await texts(".wallneed .row-main b") === "Side door lock’s battery is flat|Approve the grocery cart", "the household’s needs: " + await texts(".wallneed .row-main b"));
    const body = await page.textContent("body");       // the household's only: nobody's mail, calendar, school or note
    for (const mine of ["Sam Rivera", "Lunch with Sam", "Photo day", "on the phone", "The rain holds off"]) expect(!body.includes(mine), `“${mine}” reached the wall`);
    expect(await page.locator(".brief-note").count() === 0, "the written note reached the wall");
    expect((await page.textContent(".wrain")).trim() === "Rain from 3 pm to 6 pm", "the rain line: " + await page.textContent(".wrain"));
    expect(await texts(".wtile b") === "Not responding|Locked|Locked|Disarmed|Closed", "the house on the wall: " + await texts(".wtile b"));
    expect(await page.evaluate(() => document.documentElement.dataset.theme) === "day", "the wall did not follow the sun up");
    await page.waitForTimeout(300); await page.screenshot({ path: `${out}/wall-1-morning.png` });
    await page.locator('.wallneed .row-act:text-is("Add to list")').tap();
    expect(await until(() => document.querySelector(".toast span")?.textContent.includes("AA batteries")), "Add to list on the wall said nothing");
    expect(live.length === 0, "the wall opened the house’s live line");

    await page.clock.setSystemTime(await at("evening"));
    await page.reload(); await page.waitForSelector(".wall .wblock");
    expect(await texts(".wall h2") === "Needs attention|The house", "the wall’s evening: " + await texts(".wall h2"));
    expect(!(await page.textContent("body")).includes("Alex is away"), "the calendar or the note reached the wall in the evening");
    expect(await page.evaluate(() => document.documentElement.dataset.theme) === "dusk", "the wall did not turn to dusk after sunset");
    await page.waitForTimeout(300); await page.screenshot({ path: `${out}/wall-2-evening.png` });
    quiet = true;
    await ctx.setOffline(true);
    await page.clock.fastForward(61_000);
    expect(await until(() => !!document.querySelector(".wstale")), "the wall did not say it can’t reach Hub");
    expect(await page.locator(".wall .wblock").count() > 0, "the wall did not keep the day while Hub was away");
    await ctx.setOffline(false);
    await page.clock.fastForward(61_000);
    expect(await until(() => !document.querySelector(".wstale")), "the wall did not come back by itself");
    quiet = false;

    await page.clock.setSystemTime(await at("night"));
    await page.reload(); await page.waitForSelector(".wall.asleep");
    expect((await page.textContent(".wclock")).trim() === "10:40pm", "the night clock: " + await page.textContent(".wclock"));
    // The clock is drawn at once; the night line only once the day has loaded.
    expect(await until(() => !!document.querySelector(".wnight-line")), "the night line never came");
    const nextDay = new Date(Date.parse((await page.evaluate(() => new Date().toISOString()))) + 86_400_000)
      .toLocaleDateString("en-US", { weekday: "long", timeZone: "America/Los_Angeles" });
    expect(await texts(".wnight-line") === `${nextDay}: 56° and sunny`, "the night: " + await texts(".wnight-line"));
    expect(await page.evaluate(() => getComputedStyle(document.body).backgroundColor) === "rgb(0, 0, 0)", "the night is not dark");
    await page.waitForTimeout(300); await page.screenshot({ path: `${out}/wall-3-night.png` });
    await page.evaluate(() => fetch("/demo/reset", { method: "POST" }));
    await ctx.close();
  }

  await browser.close();
  const elsewhere = [...hosts].filter((h) => h !== new URL(base).host);
  if (elsewhere.length) failures.push("the app talked to another host: " + elsewhere.join(", "));
  console.log(JSON.stringify({ ok: failures.length === 0, tap_shows_in_ms: timings, failures }, null, 2));
  process.exit(failures.length ? 1 : 0);
})();
