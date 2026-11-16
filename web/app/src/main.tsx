// Hub's app: Ask at the center, the house a tap away, kept live. Built into hub/static/app (see ../README.md).
import { render } from "preact";
import { useEffect } from "preact/hooks";
import { signal } from "@preact/signals";
import "./app.css";
import { Dock, Home, Rail, System } from "./screens";
import { Ask, CardSheet } from "./ask";
import { loadAble } from "./asking";
import { loadDay, watchDay } from "./day";
import { Today } from "./today";
import { Versions } from "./versions";
import { Engine } from "./engine";
import { MailSettings } from "./mailrules";
import { Activity } from "./activity";
import { Changes } from "./changes";
import { ItemSheet, Kitchen, shop, watchKitchen } from "./kitchen";
import { Mail, watchMail } from "./mail";
import { ErrandSheet, Errands, watchErrands } from "./errands";
import { HereSheet, KeepSheet, PlayingSheet, RoomSheet } from "./sheets";
import { go, seen, sheet, start, view } from "./state";
import { ToastLine } from "./ui";
import { watchDock, watchViewport } from "./viewport";

const wideScreen = matchMedia("(min-width: 1040px)");
const wide = signal(wideScreen.matches);
wideScreen.addEventListener("change", (e) => (wide.value = e.matches));
// The one thing that moves by itself: for a moment after opening, the lit rooms come on one after another.
const waking = signal(true);
window.setTimeout(() => (waking.value = false), 1800);

/** Cmd-K, from anywhere: Ask, with the field ready. */
function toAsk() {
  if (view.value !== "home" && !wide.value) go("home");
  requestAnimationFrame(() => document.getElementById("askq")?.focus());
}

function Sheets() {
  const open = sheet.value, now = seen.value;
  if (open?.kind === "card") return <CardSheet card={open.card} />;
  if (open?.kind === "item") {        // one thing on the shopping list: it needs Kitchen's picture, not the house's
    const list = shop.value;
    const item = list?.ok ? list.items.find((i) => i.id === open.id) : undefined;
    return item ? <ItemSheet item={item} /> : null;
  }
  if (open?.kind === "errand") return <ErrandSheet id={open.id} way={open.way} />;
  if (!open || !now) return null;
  if (open.kind === "room") {
    const room = now.rooms[open.id];
    return room ? <RoomSheet room={room} music={now.music} /> : null;
  }
  if (open.kind === "playing") return now.music ? <PlayingSheet music={now.music} /> : null;
  if (open.kind === "here") {
    const speaker = now.music?.speakers.find((s) => s.id === open.speaker);
    return now.music && speaker ? <HereSheet speaker={speaker} music={now.music} /> : null;
  }
  if (open.kind === "keep") return <KeepSheet now={now} />;
  return null;
}

function Screen() {
  const where = view.value;
  return where === "home" ? <Ask home house={!wide.value} /> : where === "house" ? <Home /> : where === "system" ? <System /> : where === "versions" ? <Versions /> : where === "engine" ? <Engine /> : where === "mailsettings" ? <MailSettings /> : where === "changes" ? <Changes /> : where === "activity" ? <Activity /> : where === "today" ? <Today /> : where === "errands" ? <Errands /> : where === "mail" ? <Mail /> : where === "kitchen" ? <Kitchen /> : <Home />;
}

/** Beside the screen on a wide one: the house while Ask is in the middle, and Ask beside everything else. */
function Side() {
  if (view.value === "home") return <aside class="side" aria-label="The house"><Home /></aside>;
  return <aside class="askrail" aria-label="Ask"><h2>Ask</h2><Ask /></aside>;
}

function App() {
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (e.key === "k" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); toAsk(); }
    };
    document.addEventListener("keydown", key);
    watchDock();
    return () => document.removeEventListener("keydown", key);
  }, []);
  const asking = view.value === "home";
  return (
    <div class={"app" + (asking ? " asking" : "")}>
      <Rail />
      <main class={"main" + (waking.value ? " wake" : "")}><Screen /></main>
      {wide.value && <Side />}
      <Dock />
      <Sheets />
      <ToastLine />
    </div>
  );
}

/** Keep the app's own files on this device (sw.js serves them), so it opens at once and can say when it
 * can't reach home. Only the page, the two built files and the typeface; never anything about the house. */
async function keepOnDevice() {
  if (!("serviceWorker" in navigator) || !("caches" in window)) return;
  await navigator.serviceWorker.register("/app/sw.js", { scope: "/app/" });
  const built = [...document.querySelectorAll<HTMLLinkElement>('link[rel="stylesheet"]')].map((el) => el.href)
    .concat([...document.querySelectorAll<HTMLScriptElement>("script[src]")].map((el) => el.src));
  const wanted = [new URL("/app/", location.href).href, new URL("/static/app/fonts/Jost.ttf", location.href).href, ...built];
  const cache = await caches.open("hub-app-1");
  for (const kept of await cache.keys()) {
    const same = wanted.find((url) => new URL(url).pathname === new URL(kept.url).pathname);
    if (same !== undefined && same !== kept.url) await cache.delete(kept);       // an older version of the same file
  }
  for (const url of wanted) if (!(await cache.match(url))) await cache.add(url);
}

const root = document.getElementById("app");
if (root) {
  render(<App />, root);
  start();
  void loadAble();
  watchDay();
  watchViewport();
  watchMail();
  watchErrands();
  watchKitchen();
  void loadDay();         // Ask opens on the day, so it is read at once wherever the app opens
  void keepOnDevice().catch(() => undefined);     // without it the app still works; it just needs Hub to open
}
