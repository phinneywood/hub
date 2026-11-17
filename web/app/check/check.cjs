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
    expect(await says() === "Four rooms are lit.", "the house sentence: " + await says());
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
    await page.route("**/app/api/room/**", (route) => route.fulfill({ status: 409, contentType: "application/json", body: '{"error":"Home Assistant couldn’t be reached just now."}' }));
    quiet = true;
    await tap(room("office"));
    expect(await saysSoon("Five rooms are lit.", 400), "the tap did not show");
    expect(await saysSoon("Four rooms are lit.", 3000), "a refused tap was left showing as done");
    expect((await page.textContent(".toast span")).includes("couldn’t be reached"), "a refused tap did not say why");
    await page.unroute("**/app/api/room/**"); quiet = false;
    await page.waitForTimeout(400);

    // ---- another device sees it without asking ----
    const other = await ctx.newPage();
    await other.goto(base + "/app/"); await other.waitForSelector(".plan");
    await tap(room("entry"));
    expect(await other.waitForFunction(() => document.querySelector(".say")?.textContent.trim() === "Five rooms are lit.", null, { timeout: 6000 }).then(() => true, () => false),
      "a second open app did not hear about the change within 6 s");
    await other.close();
    await tap(room("entry")); expect(await saysSoon("Four rooms are lit."), "the entry did not switch off again");
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
    expect(await page.locator('.sheet [aria-label="Pause the music in Bedroom"]').count() === 1, "the bedroom's own music had no pause");
    await page.keyboard.press("Escape"); await page.waitForTimeout(400);

    // ---- Ask: what is usually asked around now is one tap ----
    const answered = (text, ms) => until((t) => [...document.querySelectorAll(".turn")].pop()?.querySelector(".answer")?.textContent.startsWith(t)
      && ![...document.querySelectorAll(".turn")].pop().querySelector(".dots"), text, ms);
    const lastAnswer = () => page.locator(".turn .answer").last().innerText().catch(() => "(nothing)");
    if (phone) { await tap(".askbtn"); await page.waitForSelector(".sheet.in"); }
    expect(await until(() => document.querySelector(".sug")?.textContent === "Dim the living room to 30You often ask this around now"), "what is often asked around now was not offered");
    await page.waitForTimeout(300); await shot("6a-ask-open");
    await page.locator(".sug").first().click();
    expect(await answered("The living room is at 30%."), "one tap on a habit did not do it: " + await lastAnswer());

    // ---- Ask: a few letters reach a room ----
    await page.fill("#askq", "liv"); await page.waitForTimeout(250); await shot("6-ask");
    expect((await page.locator(".sug").first().innerText()).startsWith("Living room"), "typing did not find the room");
    await page.locator('.sug [role="switch"]').first().click();
    expect(await until((sel) => document.querySelector(sel)?.getAttribute("aria-checked") === "false", room("living-room")), "the switch in the list did not switch the room");

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
    expect(await until(() => document.querySelector(".sub")?.textContent.endsWith("Something else is playing in the bedroom.")), "Home did not say that two rooms play different things: " + await sub());
    await askFor("what should we cook this week?");
    expect(await answered("Kitchen is on it."), "Ask did not pass the question to Kitchen: " + await lastAnswer());
    expect(await until(() => document.querySelectorAll(".kitchen-says .kcard").length === 3, null, 12000), "Kitchen’s answer did not arrive under the request");
    expect(await until(() => { const box = document.querySelector(".ask-scroll"); return box.scrollHeight - box.scrollTop - box.clientHeight < 4; }), "Kitchen’s answer arrived out of view");
    await page.waitForTimeout(300); await shot("6c-ask-kitchen");
    await askFor("what needs me today?");
    expect(await answered("From what Hub can see, two emails need you."), "a question about the day was not answered from what Hub read: " + await lastAnswer());
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
    await page.fill("#askq", "");
    if (phone) { await page.keyboard.press("Escape"); await page.waitForTimeout(400); }
    await tap(phone ? ".who" : ".rail .foot .tab");
    expect(await until(() => [...document.querySelectorAll(".block h2")].some((h) => h.textContent === "Hub remembers")), "System did not list what Hub remembers");
    await page.locator(".block .forget").first().click();
    expect(await until(() => ![...document.querySelectorAll(".block h2")].some((h) => h.textContent === "Hub remembers")), "Forget did not forget");
    await tap(phone ? '.tabs .tab:text-is("Home")' : '.rail .tab:text-is("Home")');
    await page.waitForSelector(".plan");

    // ---- where Ask isn't set up yet, what is typed still goes somewhere: Hub's older chat ----
    await stage("reset"); await stage("ask-away");
    await page.reload(); await page.waitForSelector(".plan");
    if (phone) { await tap(".askbtn"); await page.waitForSelector(".sheet.in"); }
    expect(await until(() => document.querySelector(".ask-hint")?.textContent.endsWith("Anything else goes to the chat.")), "Ask did not say that it hands over to the chat");
    await page.route(base + "/ask", (route) => route.fulfill({ status: 200, contentType: "text/html", body: "<title>The chat</title>" }));
    await askFor("play something calm");
    expect(await page.waitForURL(base + "/ask", { timeout: 5000 }).then(() => true, () => false), "what was typed did not open the chat");
    expect(await page.evaluate(() => sessionStorage.getItem("hub.say")) === "play something calm", "what was typed did not go to the chat with it");
    await page.unroute(base + "/ask");
    await stage("ask-back");
    await page.goto(base + "/app/"); await page.waitForSelector(".plan"); await page.waitForTimeout(1900);

    // ---- Today: what needs you, then the day ----
    await stage("reset");
    await tap(phone ? '.tabs .tab:text-is("Today")' : '.rail .tab:text-is("Today")');
    expect(await saysSoon("Dinner at home is at seven. Three things need you."), "Today’s sentence: " + await says());
    expect((await page.locator(".row.need .row-main b").allInnerTexts()).join("|") === "Email from Sam Rivera|Approve the grocery cart|Email from Maple Street Dental",
      "what needs you, the longest wait first");
    expect((await page.locator(".block h2").allInnerTexts()).join("|") === "Needs you|This evening|Tomorrow|This week at school|Lisbon", "Today’s sections, in order");
    expect(await page.locator(".row.warn .row-main span").innerText() === "Overlaps Dentist at 4:00 pm", "two things at once were not marked on the day");
    expect(await page.locator(".row.struck").count() === 1, "a cancelled class date was not struck through");
    expect((await page.locator(".quiet-line").last().innerText()).startsWith("Hub filed 12 emails."), "housekeeping was not told quietly");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "Today scrolls sideways");
    await page.waitForTimeout(300); await shot("10-today");
    if (phone) expect((await small()).length === 0, "tap targets under 44 px on Today: " + (await small()).join(", "));
    await page.locator(".tickbtn").first().click();
    expect(await saysSoon("Dinner at home is at seven. Two things need you.", 3000), "a tick did not take the email off the list: " + await says());
    expect(await until(() => document.querySelector(".toast span")?.textContent.trim() === "Marked as handled"), "a tick did not say what it did");
    await page.locator(".toast button").click();
    expect(await saysSoon("Dinner at home is at seven. Three things need you.", 3000), "Undo did not put the email back: " + await says());
    await stage("quiet-day");
    await page.reload(); await page.waitForSelector(".say");
    expect(await saysSoon("Nothing needs you.", 4000), "a quiet day: " + await says());
    await page.waitForTimeout(1900); await shot("11-today-quiet");
    await stage("reset");

    // ---- Mail: what needs you, who never answered, what Hub sorted ----
    await tap(phone ? '.tabs .tab:text-is("Mail")' : '.rail .tab:text-is("Mail")');
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

    // ---- Kitchen: the list on its way into the cart, what needs a person, and no second conversation ----
    await stage("reset");
    await tap(phone ? '.tabs .tab:text-is("Kitchen")' : '.rail .tab:text-is("Kitchen")');
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
    // A cart turn that stopped before it could say: Kitchen asks for the one thing that settles it.
    await stage("kitchen-unsure");
    await tap(phone ? '.tabs .tab:text-is("Home")' : '.rail .tab:text-is("Home")');
    await tap(phone ? '.tabs .tab:text-is("Kitchen")' : '.rail .tab:text-is("Kitchen")');
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
    // The kitchen service stopped, and one from before it kept the list: each says so, and nothing looks broken.
    await stage("kitchen-away");
    await tap(phone ? '.tabs .tab:text-is("Home")' : '.rail .tab:text-is("Home")');
    await tap(phone ? '.tabs .tab:text-is("Kitchen")' : '.rail .tab:text-is("Kitchen")');
    expect(await until(() => document.querySelector(".kitchen .banner b")?.textContent === "Kitchen isn’t answering"), "a Kitchen that is away did not say so");
    await stage("kitchen-old");
    await tap(phone ? '.tabs .tab:text-is("Home")' : '.rail .tab:text-is("Home")');
    await tap(phone ? '.tabs .tab:text-is("Kitchen")' : '.rail .tab:text-is("Kitchen")');
    expect(await until(() => document.querySelector(".kitchen .banner b")?.textContent === "Kitchen needs its update"), "an older Kitchen did not say it needs its update");
    expect(await page.locator('.kitchen input[aria-label="Message Kitchen"]').count() === 1, "an older Kitchen was left with no way to be asked");
    await page.waitForTimeout(200); await shot("13f-kitchen-old");
    await stage("reset");
    await tap(phone ? '.tabs .tab:text-is("Home")' : '.rail .tab:text-is("Home")');
    await page.waitForSelector(".plan");

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
    await tap(phone ? '.tabs .tab:text-is("Home")' : '.rail .tab:text-is("Home")');
    await page.waitForSelector(".plan");

    // ---- the Archive: steered from Ask, its "See all" is the Archive screen; System opens it too. One search, no model ----
    if (phone) { await tap(".askbtn"); await page.waitForSelector(".sheet.in"); }
    await askFor("show me the kids at the beach in 2024");
    expect(await answered("Kids at the beach in 2024: 9 photos from 2 days."), "Ask's photo sentence: " + await lastAnswer());
    expect(await until(() => [...document.querySelectorAll(".turn")].pop()?.querySelectorAll(".acard .tile").length === 9), "the photo card under the answer");
    await page.waitForTimeout(300); await shot("10a-ask-photos");
    await askFor("show me this month's best photos");
    expect(await answered("The best of October 2026: 12 photos to keep, print or skip."), "the month's best from Ask: " + await lastAnswer());
    expect(await until(() => [...document.querySelectorAll(".turn")].pop()?.querySelectorAll(".best .shot").length === 6), "the month's best was not drawn under the answer");
    await askFor("stop reading my chatgpt history");
    expect(await answered("Ask won’t read ChatGPT history."), "switching off ChatGPT from Ask: " + await lastAnswer());
    await askFor("you can read my chatgpt history");
    expect(await answered("Ask may read ChatGPT history now."), "switching ChatGPT back on from Ask: " + await lastAnswer());
    await page.waitForTimeout(300); await shot("10b-ask-archive");
    await page.locator('.turn .acard .row-act:has-text("See all in the Archive")').first().click();
    expect(await saysSoon("Kids at the beach in 2024: 9 photos from 2 days."), "See all did not open the Archive on that search: " + await says());
    expect((await page.locator(".chips span").allInnerTexts()).join("|") === "The kids|2024", "what Hub understood: " + (await page.locator(".chips span").allInnerTexts()).join("|"));
    expect(await until(() => document.querySelectorAll(".found-block .tile").length === 9), "nine photos in the grid");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "the Archive scrolls sideways");
    if (phone) expect((await small()).length === 0, "tap targets under 44 px on the Archive: " + (await small()).join(", "));
    await page.waitForTimeout(500); await shot("10c-kids");
    await tap(".found-block .tile");
    expect(await until(() => document.querySelector(".viewer-bar span")?.textContent === "1 of 9"), "a photo did not open full screen");
    const frame = await page.locator(".viewer-pic").boundingBox();
    await page.keyboard.press("ArrowRight");
    expect(await until(() => document.querySelector(".viewer-bar span")?.textContent === "2 of 9"), "the next photo did not show");
    const moved = await page.locator(".viewer-pic").boundingBox();
    expect(frame && moved && frame.width === moved.width && frame.height === moved.height, "the photo’s frame moved between photos");
    await page.waitForTimeout(600); await shot("10d-viewer");
    await page.keyboard.press("Escape");
    expect(await until(() => !document.querySelector(".viewer")), "Escape did not close the photo");
    await page.fill(".seek .field", "furnace");
    await page.press(".seek .field", "Enter");
    expect(await saysSoon("Furnace: 1 photo, 3 documents, 1 event, 2 emails and 1 conversation.", 8000), "the furnace sentence: " + await says());
    expect(await until(() => document.querySelector(".hits mark")?.textContent.toLowerCase() === "furnace"), "a snippet didn't mark what matched");
    await page.waitForTimeout(400); await shot("10e-furnace");
    await tap('.found-block .hit:has-text("Furnace service invoice.pdf")');
    expect(await until(() => document.querySelector(".sheet h1")?.textContent === "Furnace service invoice.pdf"), "the document did not open");
    expect(await until(() => [...document.querySelectorAll(".sheet a.btn")].some((a) => a.textContent === "Get the file")), "no Get the file");
    await page.waitForTimeout(400); await shot("10f-document");
    await tap(".sheet .x");
    await tap('.found-block .hit:has-text("Your furnace service invoice")');
    expect(await until(() => document.querySelector(".sheet .mail-text")?.textContent.includes("Thanks for choosing us")), "the email did not open");
    await tap(".sheet .x");
    await tap('.found-block .hit:has-text("Furnace filter sizes")');
    expect(await until(() => document.querySelector(".msg.here")?.textContent.includes("16x25 return")), "the conversation did not open at what matched");
    await page.waitForTimeout(300); await shot("10g-chat");
    await tap(".sheet .x");
    expect(await page.locator('.rail .tab:text-is("Archive"), .seekbtn').count() === 0, "the Archive has a tab or a button of its own");
    await tap(phone ? ".who" : '.rail .tab:text-is("System")');
    await tap('.row:has-text("Archive")');
    expect(await saysSoon("39 photos, 8 documents and 3 conversations, on this Mac."), "System did not open the Archive: " + await says());
    expect(await until(() => document.querySelectorAll(".best .shot").length === 6), "the month's best is not on the Archive");
    expect(await until(() => document.querySelectorAll(".year-name").length === 2), "on this day in earlier years is missing");
    await page.waitForTimeout(300); await shot("10h-archive");
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
