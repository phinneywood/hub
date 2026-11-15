// Hub's app: the house as a plan, kept live. Built into hub/static/app (see ../README.md).
import { render } from "preact";
import { useEffect } from "preact/hooks";
import { signal } from "@preact/signals";
import "./app.css";
import { Dock, Home, Rail, System } from "./screens";
import { Ask, AskSheet, PlayingSheet, RoomSheet } from "./sheets";
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
      <main class={"main" + (waking.value ? " wake" : "")}>{view.value === "system" ? <System /> : <Home />}</main>
      {wide.value && <aside class="askrail" aria-label="Ask"><h2>Ask</h2><Ask /></aside>}
      <Dock ask={ask} />
      <Sheets />
      <ToastLine />
    </div>
  );
}

const root = document.getElementById("app");
if (root) {
  render(<App />, root);
  start();
  if ("serviceWorker" in navigator) void navigator.serviceWorker.register("/app/sw.js", { scope: "/app/" }).catch(() => undefined);
}
