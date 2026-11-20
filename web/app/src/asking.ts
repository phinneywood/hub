// Ask: what was said and what came back. Hub answers a request as it happens, in small events; this keeps the
// conversation the app shows, and hands the words to Hub's older chat where Ask itself isn't set up yet.
import { signal } from "@preact/signals";
import { say } from "./state";

// What the app draws under an answer. `setup`: changes to how the house is set up; `confirm`: one that waits for a tap;
// `photos` and `found`: what a search of the archive found (`items`: photos, or the lines to open).
export interface Card { kind: string; rooms?: string[]; speakers?: string[]; batch?: string; after?: number; id?: number; note?: string;
  lines?: string[]; missed?: string[]; title?: string; why?: string; do?: string; items?: unknown[]; all?: boolean; cart?: boolean; since?: number;
  ids?: number[]; show?: string[]; things?: Thing[]; hint?: string; rows?: MailRow[]; opened?: number[]; board?: string;
  target?: string; button?: string; drill?: string;
  path?: string; count?: number; search?: string; month?: string; years?: unknown[] }
// One email a mail search found (hub/app/ask/mailbox.py): its number for "open the second one", and where it opens.
export interface MailRow { n: number; who: string; subject: string; when: string; href: string; off: boolean }
// One line of Hub's morning message (hub/app/ask/morning.py): its number, the sentence, and the one button it has.
export interface Thing { n: number; key: string; text: string; open: { href: string; label: string } | null }
// `waiting`: asked while another request was still going; it goes as soon as that one ends. `at`: when it was asked (ms).
// `id`: the turn's number on Hub, for one kept there; reading back through the conversation starts before the oldest.
export interface Turn { n: number; asked: string; answer: string; cards: Card[]; busy: boolean; waiting: boolean; doing: string; ms: number;
  status: string; at: number; id?: number }
interface Kept { id: number; at: number; asked: string; answer: string; cards: Card[]; ms: number; status: string }
interface Able { ready: boolean; why: string | null; recent: Kept[]; more: boolean; usual: string[]; remembers: { id: number; note: string }[];
  morning?: boolean }
type Event = { t: "say"; text: string } | { t: "doing"; what: string } | { t: "card"; card: Card }
  | { t: "done"; id: number | null; ms: number; status: string };

const SAY = "hub.say";      // the chat page reads this once and sends it (web/ask): only Hub's own pages can set it
const DOING: Record<string, string> = { lights: "Switching the lights", scene: "Setting the scene", music: "Finding the music",
  kitchen: "Asking Kitchen", groceries: "Working the list", look_up: "Looking", mail_search: "Searching your mail", mail_read: "Reading the email", mail_change: "Getting the card ready", errands: "Looking at your errands", remember: "Noting that", find: "Looking around the house", details: "Reading it",
  arrange: "Moving things", rooms: "Changing the rooms", scene_setup: "Changing the scene", routine: "Writing it into Home Assistant",
  device: "Working on it", setting: "Changing the settings", integration: "Reloading it", dashboard: "Making the dashboard",
  house_health: "Reading House health", caretaker: "Asking the caretaker",
  search: "Searching the archive", read: "Reading it", archive: "Opening the archive" };

export const asked = signal("");                 // what is in the field
export const talk = signal<Turn[]>([]);          // the one conversation: what was read back from Hub, then this sitting's
export const older = signal(false);               // whether Hub keeps more of the conversation than is shown
export const able = signal<Able | null>(null);   // whether Ask itself can run here, and what Hub remembers
let count = 0;

export async function loadAble() {
  try {
    const answer = await fetch("/app/api/ask", { headers: { accept: "application/json" } });
    if (!answer.ok) return;
    const found = (await answer.json()) as Able;
    able.value = found;
    if (!talk.value.length && found.recent.length) {     // the conversation as Hub keeps it, so it is there when the app opens again
      talk.value = found.recent.map(fromKept);
      older.value = found.more;
    }
  } catch {
    /* asked again the next time Ask opens */
  }
}

const fromKept = (kept: Kept): Turn => ({ n: ++count, id: kept.id, asked: kept.asked, answer: kept.answer, cards: kept.cards,
  busy: false, waiting: false, doing: "", ms: 0, status: kept.status, at: kept.at * 1000 });

/** Read further back through the one conversation: the turns before the oldest one shown. */
export async function loadOlder() {
  const first = talk.value.find((turn) => turn.id !== undefined);
  if (!first) { older.value = false; return; }
  try {
    const answer = await fetch(`/app/api/ask?before=${first.id}`, { headers: { accept: "application/json" } });
    if (!answer.ok) return;
    const found = (await answer.json()) as Able;
    talk.value = [...found.recent.map(fromKept), ...talk.value];
    older.value = found.more;
  } catch {
    say("Can’t reach Hub.");
  }
}

/** Hand what was typed to Hub's older chat, which answers while Ask itself isn't set up. */
export function toChat(text: string) {
  try {
    if (text) sessionStorage.setItem(SAY, text.slice(0, 2000));
  } catch {
    /* the chat just opens empty */
  }
  location.href = "/ask";
}

const STILL = "Hub is still on your last request.";      // the server's word while a turn of this person's is going
let going: AbortController | null = null;

/** Ask Hub. A request made while another is going waits its turn under it instead of being dropped. */
export async function ask(words: string) {
  const text = words.trim();
  if (!text) return;
  if (!able.value) await loadAble();
  if (able.value && !able.value.ready) return toChat(text);
  const n = ++count;
  talk.value = [...talk.value, { n, asked: text, answer: "", cards: [], busy: false, waiting: true, doing: "", ms: 0, status: "", at: Date.now() }];
  asked.value = "";
  void next();
}

/** Stop waiting for the request that is going. What Hub already did stays done, with its Undo. */
export function stop() {
  going?.abort();
}

async function next() {
  if (going) return;
  const turn = talk.value.find((t) => t.waiting);
  if (!turn) return;
  going = new AbortController();
  try {
    await send(turn.n, turn.asked, going.signal);
  } finally {
    going = null;
  }
  void next();
}

// A request has one mail card, which a later step can add to (an email read): it takes the earlier one's place, and
// the day's mail rows give way to it.
function placed(cards: Card[], card: Card): Card[] {
  if (card.kind !== "mail") return [...cards, card];
  const rest = cards.filter((c) => c.kind !== "mail")
    .map((c) => (c.kind === "day" ? { ...c, show: (c.show ?? []).filter((t) => t !== "mail") } : c))
    .filter((c) => c.kind !== "day" || (c.show ?? []).length > 0);
  return [...rest, card];
}

async function send(n: number, text: string, signal: AbortSignal) {
  const change = (make: (turn: Turn) => Partial<Turn>) => {
    talk.value = talk.value.map((turn) => (turn.n === n ? { ...turn, ...make(turn) } : turn));
  };
  const known = Math.max(0, ...talk.value.map((turn) => turn.id ?? 0));     // the newest turn Hub kept before this one
  let reached = false;      // Hub took the request: from here, a break is a lost connection, not a request never made
  change(() => ({ waiting: false, busy: true }));
  const take = (event: Event) => {
    if (event.t === "say") change((turn) => ({ answer: turn.answer + event.text, doing: "" }));
    else if (event.t === "doing") change(() => ({ doing: DOING[event.what] ?? "Working" }));
    else if (event.t === "card") change((turn) => ({ cards: placed(turn.cards, event.card) }));
    else change(() => ({ busy: false, doing: "", ms: event.ms, status: event.status, id: event.id ?? undefined }));
  };
  try {
    let answer: Response;
    // A request stopped a moment ago can still be finishing on Hub; the next one waits for it rather than failing.
    for (let tries = 0; ; tries++) {
      answer = await fetch("/app/api/ask", {
        method: "POST", headers: { "content-type": "application/json", accept: "text/event-stream" }, body: JSON.stringify({ text }), signal,
      });
      if (answer.ok || answer.status !== 409 || tries >= 15) break;
      const told = (await answer.clone().json().catch(() => ({}))) as { error?: string };
      if (told.error !== STILL) break;
      await new Promise((done) => window.setTimeout(done, 2000));
    }
    if (!answer.ok || !answer.body) {
      const told = (await answer.json().catch(() => ({}))) as { error?: string };
      change(() => ({ answer: told.error ?? "Hub couldn’t take that just now.", busy: false, status: "failed" }));
      return;
    }
    reached = true;
    const reader = answer.body.getReader(), decoder = new TextDecoder();
    let rest = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      rest += decoder.decode(value, { stream: true });
      for (let cut = rest.indexOf("\n\n"); cut >= 0; cut = rest.indexOf("\n\n")) {
        const line = rest.slice(0, cut);
        rest = rest.slice(cut + 2);
        if (line.startsWith("data: ")) take(JSON.parse(line.slice(6)) as Event);
      }
    }
    // The stream ended without Hub saying how the turn ended: the connection went partway, so read the turn back.
    if (talk.value.some((turn) => turn.n === n && turn.busy)) await readBack(text, known, signal, change);
    if (talk.value.some((turn) => turn.n === n && turn.cards.some((card) => card.kind === "memory"))) void loadAble();
  } catch {
    if (!signal.aborted && reached) await readBack(text, known, signal, change);
    if (signal.aborted) {
      change((turn) => ({ busy: false, doing: "", status: "stopped",
        answer: (turn.answer ? turn.answer + "\n" : "") + (turn.cards.some((card) => card.batch) ? "Stopped. What Hub already did is below, with Undo." : "Stopped.") }));
    } else if (!reached) {
      change((turn) => ({ answer: turn.answer || "Can’t reach Hub. Nothing was asked.", busy: false, status: "failed" }));
    }
  }
}

/** Hub took the request and the connection went while it answered (a locked screen, a weak signal). Hub finishes the
 *  turn and keeps it either way, so wait for it there and show the turn as Hub kept it: its whole answer and cards. */
async function readBack(text: string, known: number, signal: AbortSignal, change: (make: (turn: Turn) => Partial<Turn>) => void) {
  change(() => ({ busy: true, doing: "Lost the connection. Waiting for Hub’s answer" }));
  const said = text.split(/\s+/).join(" ");
  for (let tries = 0; tries < 40 && !signal.aborted; tries++) {
    await new Promise((done) => window.setTimeout(done, tries < 6 ? 1500 : 3000));
    try {
      const answer = await fetch("/app/api/ask", { headers: { accept: "application/json" } });
      if (!answer.ok) continue;
      const kept = ((await answer.json()) as Able).recent.find((turn) => turn.id > known && said.startsWith(turn.asked));
      if (kept) {
        change(() => ({ answer: kept.answer, cards: kept.cards, status: kept.status, id: kept.id, ms: kept.ms, busy: false, doing: "" }));
        return;
      }
    } catch {
      /* still away: look again */
    }
  }
  if (!signal.aborted) {
    change((turn) => ({ busy: false, doing: "", status: "failed",
      answer: (turn.answer ? turn.answer + "\n" : "") + "The connection went while Hub was answering, and Hub kept no answer to this. Ask again." }));
  }
}

export async function forgetNote(id: number) {
  try {
    await fetch(`/app/api/memory/${id}/forget`, { method: "POST", headers: { accept: "application/json" } });
    say("Forgotten");
  } catch {
    say("Can’t reach Hub.");
  }
  void loadAble();
}
