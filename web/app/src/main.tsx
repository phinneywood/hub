// Hub's app: the house as a plan, kept live. Built into hub/static/app (see ../README.md).
import { render } from "preact";
import { useEffect } from "preact/hooks";
import { signal } from "@preact/signals";
import "./app.css";
import { Dock, Home, Rail, System } from "./screens";
import { Ask, AskSheet } from "./ask";
import { loadAble } from "./asking";
import { loadDay, watchDay } from "./day";
import { Today } from "./today";
import { KeepSheet, PlayingSheet, RoomSheet } from "./sheets";
import { seen, sheet, start, view } from "./state";
import { ToastLine } from "./ui";

const wideScreen = matchMedia("(min-width: 1040px)");
const wide = signal(wideScreen.matches);
wideScreen.addEventListener("change", (e) => (wide.value = e.matches));
// The one thing that moves by itself: for a moment after opening, the lit rooms come on one after another.
const waking = signal(true);
window.setTimeout(() => (waking.value = false), 1800);

function ask() {
  if (wide.value) document.getElementById("askq")?.focus();
  else sheet.value = { kind: "ask" };
}

function Sheets() {
  const open = sheet.value, now = seen.value;
  if (!open || !now) return null;
  if (open.kind === "room") {
    const room = now.rooms[open.id];
    return room ? <RoomSheet room={room} music={now.music} /> : null;
  }
  if (open.kind === "playing") return now.music ? <PlayingSheet music={now.music} /> : null;
  if (open.kind === "keep") return <KeepSheet now={now} />;
  return wide.value ? null : <AskSheet />;
}

function App() {
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (e.key === "k" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); ask(); }
    };
    document.addEventListener("keydown", key);
    return () => document.removeEventListener("keydown", key);
  }, []);
  return (
    <div class="app">
      <Rail />
      <main class={"main" + (waking.value ? " wake" : "")}>{view.value === "system" ? <System /> : view.value === "today" ? <Today /> : <Home />}</main>
      {wide.value && <aside class="askrail" aria-label="Ask"><h2>Ask</h2><Ask /></aside>}
      <Dock ask={ask} />
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
  if (view.value === "today") void loadDay();
  void keepOnDevice().catch(() => undefined);     // without it the app still works; it just needs Hub to open
}
