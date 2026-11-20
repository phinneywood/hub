// What the app knows and what it can do. One picture of the house, kept live, with the changes a person
// just made laid over it until the house itself agrees: a tap shows at once and is put right if it didn't take.
import { computed, effect, signal } from "@preact/signals";
import * as api from "./api";
import type { Card } from "./asking";
import type { ArchiveItem, ArchiveSource, Down, House, Me, Pick, Room, Said, Scene, Speaker } from "./types";
import { spoken } from "./words";

const KEEP = "hub.house";         // the last picture, so the app opens on something at once
const LOOK = "hub.look";
const HOLD = 5000;                // how long a tap is believed before the house's own answer wins
const SOUND_HOLD = 8000;          // speakers take longer to start, stop and join
const DRAG_HOLD = 60000;
const ALL_OFF = "all-off";
const SILENCE = 12000;            // Hub sends a beat every 5 s; this long without one and the line is taken for dead

export type Look = "auto" | "dusk" | "day";
export type Sheet = { kind: "room"; id: string } | { kind: "playing" } | { kind: "here"; speaker: string } | { kind: "change"; speaker: string } | { kind: "keep" }
  | { kind: "found"; id: string; source: ArchiveSource; page?: number | null; title?: string }
  | { kind: "viewer"; photos: ArchiveItem[]; at: number }
  | { kind: "item"; id: number } | { kind: "card"; card: Card } | { kind: "errand"; id: number; way?: number } | null;
export type Trouble = "" | "hub" | "house";
export interface Toast { text: string; batch: string | null; n: number; back?: () => void }
// "home" is Ask, the screen Hub opens to; "house" is the plan of rooms; "wall" is the brief on the kitchen iPad.
export type View = "home" | "house" | "today" | "mail" | "kitchen" | "system" | "versions" | "engine" | "mailsettings" | "changes" | "activity"
  | "errands" | "wall" | "boards" | "board" | "health" | "archive";

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
const VIEWS: View[] = ["house", "today", "mail", "kitchen", "system", "versions", "engine", "mailsettings", "changes", "activity", "errands", "wall", "boards", "health", "archive"];
// One dashboard is "#board/<id>" (hub/app/boards/): the view is "board", and which one is boardId.
const BOARD = /^#board\/([a-z0-9-]{1,60})$/;
const hashed = () => (location.hash === "#photos" ? "#archive" : location.hash.split("?")[0]);     // Photos is part of the Archive now
const here = (): View => (BOARD.test(location.hash) ? "board" : VIEWS.find((v) => hashed() === "#" + v) ?? "home");
const searched = () => (location.hash.startsWith("#archive?") ? new URLSearchParams(location.hash.slice(9)).get("q") ?? "" : "");
export const boardId = signal(BOARD.exec(location.hash)?.[1] ?? "");
export const view = signal<View>(here());
export const archiveQuery = signal(searched());     // what the Archive is searching for; in the address, so it can be opened again
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

// The places in the bar. Going to one of them replaces where you were; going anywhere else (a page Ask opened, a
// dashboard, System's pages) is a step in, so the phone's back gesture, the browser's Back and a page's own back link
// all come out the way you went in.
const PLACES: View[] = ["home", "house", "boards"];
interface Step { hub: number; from?: View }

/** What this app wrote into the history for the screen showing now: how many steps in, and from where. */
function step(): Step {
  const state = history.state as Partial<Step> | null;
  return { hub: typeof state?.hub === "number" ? state.hub : 0, from: state?.from };
}

function address(where: View, board = ""): string {
  return where === "home" ? location.pathname : where === "board" ? "#board/" + board : "#" + where;
}

/** Write the move into the history: a step in remembers where it came from. */
function remember(from: View, where: View, to: string) {
  if (PLACES.includes(where)) history.replaceState({ hub: 0 }, "", to);
  else if (where === from) history.replaceState(history.state, "", to);
  else history.pushState({ hub: step().hub + 1, from } satisfies Step, "", to);
}

export function go(where: View, board = "") {
  const from = view.value;
  remember(from, where, address(where, board));
  if (where === "board") boardId.value = board;
  view.value = where;
  sheet.value = null;
  if (where === "archive") archiveQuery.value = "";
  window.scrollTo(0, 0);
  if (where === "system" || where === "today" || where === "health") void lookAtCare();     // the caretaker's count, fresh
}

/** Where the screen showing now was opened from, when this app opened it. */
export function cameFrom(): View | undefined {
  return step().hub > 0 ? step().from : undefined;
}

/** A page's back link: one step back the way you came in, or straight to `up` when this page was opened directly
 * (a link, the Home Screen), so Back never leaves Hub. */
export function back(up: View) {
  if (step().hub > 0) history.back();
  else go(up);
}

// The back gesture, the browser's Back, or a link to "#mail": show what the address now says, once.
const follow = () => {
  const where = here(), board = BOARD.exec(location.hash)?.[1] ?? boardId.value, q = searched();
  if (where === view.value && board === boardId.value && q === archiveQuery.value) return;
  boardId.value = board;
  view.value = where;
  sheet.value = null;
  archiveQuery.value = q;
};
window.addEventListener("hashchange", follow);
window.addEventListener("popstate", follow);

/** The Archive, searching for `q`: from Ask's cards ("See all"), the phone's search button, or a search typed there. */
export function openArchive(q = "") {
  remember(view.value, "archive", "#archive" + (q.trim() ? "?" + new URLSearchParams({ q: q.trim() }).toString() : ""));
  view.value = "archive";
  sheet.value = null;
  archiveQuery.value = q.trim();
  window.scrollTo(0, 0);
}

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
    } else if (kind === "sp" || kind === "gv") {      // another music's play or pause, or a whole music's volume
      const music = now.music;
      const stream = music?.speaker === id ? { playing: music.playing, volume: music.volume } : music?.also?.find((a) => a.speaker === id);
      agrees = !stream || (kind === "sp" ? stream.playing === h.on : near(stream.volume, h.value, 2));
    } else if (kind === "show") {          // the light show ended
      agrees = !now.show;
    } else if (kind === "timing") {
      agrees = !now.show?.timing || now.show.timing.value === h.value;
    } else if (kind === "own") {
      const speaker = now.music?.speakers.find((s) => s.id === id);
      agrees = !speaker || speaker.other || (speaker.in && now.music!.playing);
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
    // A whole music's volume moves every room in it by the same step, so the rooms' own sliders follow at once.
    const step: Record<string, number> = {};
    const groups = [{ speaker: music.speaker, volume: music.volume, speakers: music.speakers.filter((s) => s.in && !s.other).map((s) => s.id) },
      ...(music.also ?? [])];
    for (const g of groups) {
      const h = holds["gv:" + g.speaker];
      if (h?.value !== undefined && typeof g.volume === "number") for (const id of g.speakers) step[id] = h.value - g.volume;
    }
    const speakers = music.speakers.map((s) => {
      const volume = holds["vol:" + s.id], member = holds["in:" + s.id], own = holds["own:" + s.id];
      if (own && !s.other && !(s.in && music!.playing)) return { ...s, in: false, other: true };     // its own music, starting
      const by = step[s.id];
      const level = volume?.value ?? (by !== undefined && s.volume !== null ? Math.max(0, Math.min(100, s.volume + by)) : s.volume);
      return volume || member || by !== undefined ? { ...s, volume: level, in: member?.on ?? s.in } : s;
    });
    const also = (music.also ?? []).map((a) => {
      const playing = holds["sp:" + a.speaker], volume = holds["gv:" + a.speaker];
      return playing || volume ? { ...a, playing: playing?.on ?? a.playing, volume: volume?.value ?? a.volume } : a;
    });
    music = { ...music, playing: holds["play"]?.on ?? music.playing, volume: holds["gv:" + music.speaker]?.value ?? music.volume, speakers, also };
    const sounding = new Set(speakers.filter((s) => (s.in && music!.playing) || s.other).map((s) => s.id));
    for (const [id, room] of Object.entries(rooms)) {
      const sounds = room.speaker !== null && sounding.has(room.speaker);
      if (sounds !== room.sounding) rooms[id] = { ...room, sounding: sounds };
    }
  }
  let show = now.show;
  if (show) {      // the light show: ended, its music played or paused or turned up, its lights moved earlier or later
    const main = show.speaker !== null && now.music?.speaker === show.speaker;
    const playing = main ? holds["play"] : holds["sp:" + show.speaker], volume = holds["gv:" + show.speaker], timing = holds["timing"];
    show = holds["show"]?.on === false ? null : {
      ...show, playing: playing?.on ?? show.playing, volume: volume?.value ?? show.volume,
      timing: show.timing && timing?.value !== undefined ? { ...show.timing, value: timing.value } : show.timing,
    };
  }
  return { ...now, rooms, music, show };
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

// ---- any one music in the house, by the speaker that leads it: the main one or one playing apart ----

const isMain = (leader: string) => seen.value?.music?.speaker === leader;

export async function streamPlay(leader: string, on: boolean) {
  if (isMain(leader)) return play(on);
  hold("sp:" + leader, { on }, SOUND_HOLD);
  answered(await api.post(`music/${encodeURIComponent(leader)}`, { action: on ? "play" : "pause" }), ["sp:" + leader], "");
}

export async function streamSkip(leader: string, to: "next" | "previous") {
  answered(await api.post(`music/${encodeURIComponent(leader)}`, { action: to }), [], "");
}

export function dragGroupVolume(leader: string, value: number) {
  hold("gv:" + leader, { value }, DRAG_HOLD);
}

/** Every room in that music up or down by the same step, so their balance stays. */
export async function setGroupVolume(leader: string, value: number) {
  hold("gv:" + leader, { value }, SOUND_HOLD);
  answered(await api.post(`music/${encodeURIComponent(leader)}`, { action: "group_volume", value }), ["gv:" + leader], "");
}

// ---- the light show (hub/tools/house/lightshow.py): its music is one of the musics above ----

/** Where the show's lights sit against the music, in milliseconds: higher is earlier. The card says where it wants
 * them, never a step from where they were, so quick taps add up. */
export async function setShowTiming(value: number) {
  const timing = seen.value?.show?.timing;
  if (!timing) return;
  const wanted = Math.min(timing.max, Math.max(timing.min, value));
  hold("timing", { value: wanted }, SOUND_HOLD);
  const answer = await api.post("show/timing", { value: wanted });
  if (answered(answer, ["timing"], "") && answer.timing) hold("timing", { value: answer.timing.value }, SOUND_HOLD);
}

/** Start the light show with one of Ask's choices for it: a handle a lookup gave, never words. */
export async function startShow(handle: string) {
  const answer = await api.post("show/start", { handle });
  answered(answer, [], answer.said ?? "Light show on");
}

/** End the show: Home Assistant stops its music and puts the lights back. */
export async function endShow() {
  hold("show", { on: false }, SOUND_HOLD);
  answered(await api.post("show/end"), ["show"], "Light show ended");
}

/** Bring a room into that music: a quiet one, or one playing something else, which then leaves it. */
export async function joinStream(leader: string, id: string, name: string, title: string) {
  if (isMain(leader)) hold("in:" + id, { on: true }, SOUND_HOLD);
  const answer = await api.post(`music/${encodeURIComponent(leader)}`, { action: "add", target: id });
  answered(answer, ["in:" + id], `${name} is playing ${title ? `“${title}”` : "it"} too`);
}

/** Take a room out of that music; it goes quiet and the rest play on. */
export async function takeOut(leader: string, id: string, name: string) {
  if (isMain(leader)) hold("in:" + id, { on: false }, SOUND_HOLD);
  const answer = await api.post(`music/${encodeURIComponent(leader)}`, { action: "remove", target: id });
  answered(answer, ["in:" + id], `${name} stopped`);
}

/** Play a pick in place of that music, in every room it plays in. */
export async function playOn(leader: string, pick: Pick) {
  sheet.value = { kind: "playing" };
  if (isMain(leader)) return playPick(pick);
  hold("sp:" + leader, { on: true }, SOUND_HOLD);
  answered(await api.post(`play/${encodeURIComponent(leader)}`, { uri: pick.uri, kind: pick.kind, name: pick.name }), ["sp:" + leader], `Playing ${pick.name}`);
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

/** Play a pick in one room only, its own music: whatever plays anywhere else plays on. */
export async function playHere(speaker: Speaker, pick: Pick) {
  hold("own:" + speaker.id, { on: true }, SOUND_HOLD);
  sheet.value = null;
  const answer = await api.post(`play/${encodeURIComponent(speaker.id)}`, { uri: pick.uri, kind: pick.kind, name: pick.name, here: true });
  answered(answer, ["own:" + speaker.id], `Playing ${pick.name} in ${spoken(speaker.name)}`);
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

/** What a tap answered: the card it brought up (an Undo that would unlock, open or disarm waits for its own tap),
 * or what happened, in a word. */
export function told(answer: Said, done: string) {
  if (answer.card) sheet.value = { kind: "card", card: answer.card };
  else say(answer.error ?? answer.said ?? done);
}

export async function undo(batch: string) {
  toast.value = null;
  const answer = await api.post(`undo/${encodeURIComponent(batch)}`);
  if (answer.card || answer.error) told(answer, "");
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
    me.value = { signed_in: first.signed_in, initial: first.initial, versions: first.versions, links: first.links, update: first.update,
      mail: first.mail, care: first.care };
    accept(first);
  } catch {
    trouble.value = "hub";
  }
}

/** The caretaker's count for the top button and System, read again now and then (the house's own feed doesn't carry it). */
export async function lookAtCare() {
  try {
    const care = await api.careTop();
    me.value = { ...me.value, care };
  } catch {
    /* Hub away: the banner says so */
  }
}

export function start() {
  connect();
  void look_now();
  window.setInterval(() => { if (document.visibilityState === "visible") void lookAtCare(); }, 60000);
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
