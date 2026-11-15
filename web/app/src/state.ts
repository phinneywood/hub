// What the app knows and what it can do. One picture of the house, kept live, with the changes a person
// just made laid over it until the house itself agrees: a tap shows at once and is put right if it didn't take.
import { computed, effect, signal } from "@preact/signals";
import * as api from "./api";
import type { Down, House, Me, Pick, Room, Said, Scene } from "./types";

const KEEP = "hub.house";         // the last picture, so the app opens on something at once
const LOOK = "hub.look";
const HOLD = 5000;                // how long a tap is believed before the house's own answer wins
const SOUND_HOLD = 8000;          // speakers take longer to start, stop and join
const DRAG_HOLD = 60000;
const ALL_OFF = "all-off";
const SILENCE = 12000;            // Hub sends a beat every 5 s; this long without one and the line is taken for dead

export type Look = "auto" | "dusk" | "day";
export type Sheet = { kind: "room"; id: string } | { kind: "playing" } | { kind: "ask" } | { kind: "keep" } | null;
export type Trouble = "" | "hub" | "house";
export interface Toast { text: string; batch: string | null; n: number; back?: () => void }
export type View = "home" | "today" | "system";

function kept(): House | null {
  try {
    const found = JSON.parse(localStorage.getItem(KEEP) ?? "null") as House | null;
    return found && found.ok && found.rooms ? found : null;
  } catch {
    return null;
  }
}

function keptLook(): Look {
  try {
    const found = localStorage.getItem(LOOK);
    return found === "dusk" || found === "day" ? found : "auto";
  } catch {
    return "auto";
  }
}

export const house = signal<House | null>(kept());
export const live = signal(false);            // a picture has arrived since the app opened
export const trouble = signal<Trouble>("");   // "hub": can't reach Hub; "house": Hub can't reach the house
export const why = signal("");
export const me = signal<Me>({ signed_in: true, initial: "", versions: { js: "", css: "" }, links: { ha: null, music: null } });
export const sheet = signal<Sheet>(null);
const here = (): View => (location.hash === "#system" ? "system" : location.hash === "#today" ? "today" : "home");
export const view = signal<View>(here());
export const toast = signal<Toast | null>(null);
export const look = signal<Look>(keptLook());
export const picks = signal<Pick[] | null>(null);
export const running = signal("");        // the scene being run, until the house answers

// ---- the look: evening or day, following the phone unless told otherwise ----

const prefersDark = matchMedia("(prefers-color-scheme: dark)");
const dark = signal(prefersDark.matches);
prefersDark.addEventListener("change", (e) => (dark.value = e.matches));
effect(() => {
  document.documentElement.dataset.theme = look.value === "auto" ? (dark.value ? "dusk" : "day") : look.value;
  try {
    localStorage.setItem(LOOK, look.value);
  } catch {
    /* a private window keeps nothing; the look just isn't remembered */
  }
});

// What a toast says is about the last thing done; a sheet that opens starts a new thing.
let sheetOpen = false;
effect(() => {
  const open = sheet.value !== null;
  if (open && !sheetOpen) toast.value = null;
  sheetOpen = open;
});

export function go(where: View) {
  view.value = where;
  sheet.value = null;
  history.replaceState(null, "", where === "home" ? location.pathname : "#" + where);
  window.scrollTo(0, 0);
}
window.addEventListener("hashchange", () => (view.value = here()));

// ---- what a person just changed, laid over the picture ----

interface Held { on?: boolean; level?: number; value?: number; until: number }
const held = signal<Record<string, Held>>({});
const levels: Record<string, number> = {};      // the level each room was last lit at, for a tap that turns it back on
let sweeping = 0;

function hold(key: string, what: Omit<Held, "until">, ms = HOLD) {
  held.value = { ...held.value, [key]: { ...what, until: Date.now() + ms } };
  if (!sweeping) sweeping = window.setInterval(sweep, 500);
}

function drop(...keys: string[]) {
  const left = { ...held.value };
  for (const key of keys) delete left[key];
  held.value = left;
}

function sweep() {
  const now = Date.now();
  const left = Object.fromEntries(Object.entries(held.value).filter(([, h]) => h.until > now));
  if (Object.keys(left).length !== Object.keys(held.value).length) held.value = left;
  if (!Object.keys(left).length) {
    clearInterval(sweeping);
    sweeping = 0;
  }
}

function near(a: number | null | undefined, b: number | undefined, by: number) {
  return b === undefined || (typeof a === "number" && Math.abs(a - b) <= by);
}

/** Forget each change the house now agrees with, so its own numbers show again. */
function settle(now: House) {
  const left: Record<string, Held> = {};
  for (const [key, h] of Object.entries(held.value)) {
    const cut = key.indexOf(":");
    const kind = cut < 0 ? key : key.slice(0, cut);
    const id = key.slice(cut + 1);
    let agrees = false;
    if (kind === "room") {
      const room = now.rooms[id];
      agrees = !room || (room.on === (h.on ?? room.on) && (!room.on || near(room.level, h.level, 3)));
    } else if (kind === "dev") {
      const all = Object.values(now.rooms).flatMap((r) => [...r.lights, ...r.others]);
      const device = all.find((d) => d.id === id);
      agrees = !device || (device.on === (h.on ?? device.on) && (!device.on || !("level" in device) || near(device.level, h.level, 3)));
    } else if (kind === "play") {
      agrees = !now.music || now.music.playing === h.on;
    } else {
      const speaker = now.music?.speakers.find((s) => s.id === id);
      agrees = !speaker || (kind === "vol" ? near(speaker.volume, h.value, 1) : speaker.in === h.on);
    }
    if (!agrees && h.until > Date.now()) left[key] = h;
  }
  if (Object.keys(left).length !== Object.keys(held.value).length) held.value = left;
}

function overlay(now: House, holds: Record<string, Held>): House {
  const rooms: Record<string, Room> = {};
  for (const [id, room] of Object.entries(now.rooms)) {
    let seenRoom = room;
    const whole = holds["room:" + id];
    if (whole) {
      const on = whole.on ?? room.on;
      const level = whole.level ?? (room.on ? room.level : (levels[id] ?? 100));
      seenRoom = {
        ...room, on, level: on ? level : 0, lit: on ? Math.max(room.lit, 1) : 0,
        lights: room.lights.map((l) =>
          !on ? { ...l, on: false, level: 0 } : l.on || !room.on ? { ...l, on: true, level: l.dims ? level : 100 } : l),
      };
    }
    if (seenRoom.lights.some((l) => holds["dev:" + l.id]) || seenRoom.others.some((o) => holds["dev:" + o.id])) {
      const lights = seenRoom.lights.map((l) => {
        const h = holds["dev:" + l.id];
        if (!h) return l;
        const on = h.on ?? l.on;
        return { ...l, on, level: on ? (h.level ?? (l.level || 100)) : 0 };
      });
      const lit = lights.filter((l) => l.on);
      seenRoom = {
        ...seenRoom, lights, on: lit.length > 0, lit: lit.length,
        level: lit.length ? Math.round(lit.reduce((sum, l) => sum + l.level, 0) / lit.length) : 0,
        others: seenRoom.others.map((o) => {
          const h = holds["dev:" + o.id];
          return h && h.on !== undefined ? { ...o, on: h.on } : o;
        }),
      };
    }
    rooms[id] = seenRoom;
  }
  let music = now.music;
  if (music) {
    const speakers = music.speakers.map((s) => {
      const volume = holds["vol:" + s.id], member = holds["in:" + s.id];
      return volume || member ? { ...s, volume: volume?.value ?? s.volume, in: member?.on ?? s.in } : s;
    });
    music = { ...music, playing: holds["play"]?.on ?? music.playing, speakers };
    const sounding = new Set(music.playing ? speakers.filter((s) => s.in).map((s) => s.id) : []);
    for (const [id, room] of Object.entries(rooms)) {
      const sounds = room.speaker !== null && sounding.has(room.speaker);
      if (sounds !== room.sounding) rooms[id] = { ...room, sounding: sounds };
    }
  }
  return { ...now, rooms, music };
}

/** The house as the app shows it: Hub's picture with the last taps laid over it. */
export const seen = computed<House | null>(() => {
  const now = house.value;
  if (!now) return null;
  return Object.keys(held.value).length ? overlay(now, held.value) : now;
});

// ---- saying what happened ----

let said = 0;

/** The line that says what just happened. `batch`: a change to the house that Undo can put back; `back`: any
 * other way to put it back. */
export function say(text: string, batch: string | null = null, back?: () => void) {
  toast.value = { text, batch, n: ++said, back };
}

function answered(answer: Said, keys: string[], words: string): boolean {
  if (answer.error || answer.ok === false) {      // refused, or sent and not confirmed: show the house as it is
    drop(...keys);
    say(answer.error ?? answer.said ?? "That didn’t take.");
    return false;
  }
  if (words) say(words, answer.batch ?? null);
  return true;
}

// ---- lights ----

export async function switchRoom(id: string) {
  const room = seen.value?.rooms[id];
  if (!room) return;
  const on = !room.on;
  hold("room:" + id, { on });
  answered(await api.post(`room/${id}`, { on }), ["room:" + id], `${room.name} ${on ? "on" : "off"}`);
}

export function dragRoom(id: string, level: number) {
  hold("room:" + id, { on: level > 0, level }, DRAG_HOLD);
}

export async function dimRoom(id: string, level: number) {
  const room = seen.value?.rooms[id];
  if (!room) return;
  hold("room:" + id, { on: level > 0, level });
  answered(await api.post(`room/${id}`, { level }), ["room:" + id], level > 0 ? `${room.name} at ${level}%` : `${room.name} off`);
}

export async function switchDevice(id: string, name: string, on: boolean) {
  hold("dev:" + id, { on });
  answered(await api.post(`device/${encodeURIComponent(id)}`, { on }), ["dev:" + id], `${name} ${on ? "on" : "off"}`);
}

export function dragDevice(id: string, level: number) {
  hold("dev:" + id, { on: level > 0, level }, DRAG_HOLD);
}

export async function dimDevice(id: string, name: string, level: number) {
  hold("dev:" + id, { on: level > 0, level });
  answered(await api.post(`device/${encodeURIComponent(id)}`, { level }), ["dev:" + id], level > 0 ? `${name} at ${level}%` : `${name} off`);
}

export async function runScene(scene: Scene) {
  const keys: string[] = [];
  if (scene.id === ALL_OFF) {
    for (const room of Object.values(seen.value?.rooms ?? {})) {
      if (room.on && !room.outside) {
        hold("room:" + room.id, { on: false });
        keys.push("room:" + room.id);
      }
    }
  }
  running.value = scene.id;
  const answer = await api.post(`scene/${encodeURIComponent(scene.id)}`);
  running.value = "";
  answered(answer, keys, scene.id === ALL_OFF ? "All lights off" : `${scene.name} is on`);
}

/** Remember the lights of these rooms as they are now, under a name. */
export async function keepScene(name: string, rooms: string[], restOff: boolean): Promise<boolean> {
  const answer = await api.post("scenes", { name, rooms, rest_off: restOff });
  if (answer.error) {
    say(answer.error);
    return false;
  }
  say(answer.said ?? "Kept");
  return true;
}

export async function forgetScene(scene: Scene) {
  const answer = await api.post(`scenes/${encodeURIComponent(scene.id)}/forget`);
  say(answer.error ?? `${scene.name} is forgotten`);
}

// ---- music ----

export async function play(on: boolean) {
  const music = seen.value?.music;
  if (!music) return;
  hold("play", { on }, SOUND_HOLD);
  answered(await api.post(`music/${encodeURIComponent(music.speaker)}`, { action: on ? "play" : "pause" }), ["play"], "");
}

/** Pause music that is playing somewhere else in the house, apart from the main one. */
export async function pauseStream(speaker: string) {
  answered(await api.post(`music/${encodeURIComponent(speaker)}`, { action: "pause" }), [], "Paused");
}

export async function skip(to: "next" | "previous") {
  const music = seen.value?.music;
  if (music) answered(await api.post(`music/${encodeURIComponent(music.speaker)}`, { action: to }), [], "");
}

export function dragVolume(id: string, value: number) {
  hold("vol:" + id, { value }, DRAG_HOLD);
}

export async function setVolume(id: string, value: number) {
  const music = seen.value?.music;
  if (!music) return;
  hold("vol:" + id, { value }, SOUND_HOLD);
  answered(await api.post(`music/${encodeURIComponent(music.speaker)}`, { action: "volume", target: id, value }), ["vol:" + id], "");
}

export async function speakerIn(id: string, name: string, on: boolean) {
  const music = seen.value?.music;
  if (!music) return;
  hold("in:" + id, { on }, SOUND_HOLD);
  const answer = await api.post(`music/${encodeURIComponent(music.speaker)}`, { action: on ? "add" : "remove", target: id });
  answered(answer, ["in:" + id], on ? `${name} is playing too` : `${name} stopped`);
}

export async function playPick(pick: Pick) {
  const music = seen.value?.music;
  if (!music) return;
  hold("play", { on: true }, SOUND_HOLD);
  answered(await api.post(`play/${encodeURIComponent(music.speaker)}`, { uri: pick.uri, kind: pick.kind, name: pick.name }), ["play"], `Playing ${pick.name}`);
}

export async function loadPicks() {
  if (picks.value) return;
  try {
    picks.value = (await api.picks()).picks;
  } catch {
    picks.value = [];
  }
}

export const undone = signal<string[]>([]);      // the changes put back this sitting, so their Undo isn't offered twice

export async function undo(batch: string) {
  toast.value = null;
  const answer = await api.post(`undo/${encodeURIComponent(batch)}`);
  if (answer.error) say(answer.error);
  else {
    undone.value = [...undone.value, batch];
    say("Put back");
  }
}

// ---- staying live ----

let source: EventSource | null = null;
let heard = Date.now();     // when Hub was last heard from
let tried = 0;              // when the line was last opened

function accept(found: House | Down) {
  heard = Date.now();
  live.value = true;
  if (!found.ok) {
    trouble.value = "house";
    why.value = found.why;
    return;
  }
  for (const room of Object.values(found.rooms)) if (room.on) levels[room.id] = room.level;
  house.value = found;
  trouble.value = "";
  settle(found);
  try {
    localStorage.setItem(KEEP, JSON.stringify(found));
  } catch {
    /* nothing kept; the next opening starts empty */
  }
}

function connect() {
  tried = Date.now();
  source?.close();
  source = new EventSource("/app/api/live");
  source.addEventListener("beat", () => {
    heard = Date.now();
    if (trouble.value === "hub") trouble.value = "";
  });
  source.addEventListener("house", (event) => {
    try {
      accept(JSON.parse((event as MessageEvent<string>).data) as House | Down);
    } catch {
      /* a line that isn't a picture is skipped */
    }
  });
  source.onopen = () => {
    heard = Date.now();
    if (trouble.value === "hub") trouble.value = "";
  };
}

async function look_now() {
  try {
    const first = await api.home();
    me.value = { signed_in: first.signed_in, initial: first.initial, versions: first.versions, links: first.links };
    accept(first);
  } catch {
    trouble.value = "hub";
  }
}

export function start() {
  connect();
  void look_now();
  window.setInterval(() => {
    // A phone that leaves the home network is told nothing: the line just goes silent. So listen for silence.
    if (document.visibilityState !== "visible") return;
    if (Date.now() - heard > SILENCE || !navigator.onLine) {
      trouble.value = "hub";
      if (Date.now() - tried > 5000) connect();
    }
  }, 2000);
  window.addEventListener("offline", () => (trouble.value = "hub"));
  // Hub reads the house only while an app is listening. An app nobody is looking at stops listening after a
  // short while, and picks up again the moment it is looked at.
  let away = 0;
  document.addEventListener("visibilitychange", () => {
    clearTimeout(away);
    if (document.visibilityState !== "visible") {
      away = window.setTimeout(() => { source?.close(); source = null; }, 20000);
      return;
    }
    heard = Date.now();
    connect();              // a phone that slept may hold a dead line without knowing
    void look_now();
  });
  window.addEventListener("online", connect);
}
