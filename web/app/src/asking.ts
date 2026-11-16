// Ask: what was said and what came back. Hub answers a request as it happens, in small events; this keeps the
// conversation the app shows, and hands the words to Hub's older chat where Ask itself isn't set up yet.
import { signal } from "@preact/signals";
import { say } from "./state";

// What the app draws under an answer. `setup`: changes to how the house is set up; `confirm`: one that waits for a tap.
export interface Card { kind: string; rooms?: string[]; speakers?: string[]; batch?: string; after?: number; id?: number; note?: string;
  lines?: string[]; missed?: string[]; title?: string; why?: string; do?: string; items?: number[]; all?: boolean; cart?: boolean; since?: number;
  ids?: number[]; show?: string[] }
// `waiting`: asked while another request was still going; it goes as soon as that one ends. `at`: when it was asked (ms).
export interface Turn { n: number; asked: string; answer: string; cards: Card[]; busy: boolean; waiting: boolean; doing: string; ms: number;
  status: string; at: number }
interface Kept { id: number; at: number; asked: string; answer: string; cards: Card[]; ms: number; status: string }
interface Able { ready: boolean; why: string | null; recent: Kept[]; usual: string[]; remembers: { id: number; note: string }[] }
type Event = { t: "say"; text: string } | { t: "doing"; what: string } | { t: "card"; card: Card }
  | { t: "done"; id: number | null; ms: number; status: string };

const SAY = "hub.say";      // the chat page reads this once and sends it (web/ask): only Hub's own pages can set it
const DOING: Record<string, string> = { lights: "Switching the lights", scene: "Setting the scene", music: "Finding the music",
  kitchen: "Asking Kitchen", groceries: "Working the list", look_up: "Looking", errands: "Looking at your errands", remember: "Noting that", find: "Looking around the house", details: "Reading it",
  arrange: "Moving things", rooms: "Changing the rooms", scene_setup: "Changing the scene", routine: "Writing it into Home Assistant",
  device: "Working on it", setting: "Changing the settings", integration: "Reloading it", dashboard: "Making the dashboard" };

export const asked = signal("");                 // what is in the field
export const talk = signal<Turn[]>([]);          // this sitting's requests and answers
export const able = signal<Able | null>(null);   // whether Ask itself can run here, and what Hub remembers
let count = 0;

export async function loadAble() {
  try {
    const answer = await fetch("/app/api/ask", { headers: { accept: "application/json" } });
    if (!answer.ok) return;
    const found = (await answer.json()) as Able;
    able.value = found;
    if (!talk.value.length && found.recent.length) {     // today's conversation, so it is still there when the app opens again
      talk.value = found.recent.map((kept) => ({ n: ++count, asked: kept.asked, answer: kept.answer, cards: kept.cards, busy: false,
        waiting: false, doing: "", ms: 0, status: kept.status, at: kept.at * 1000 }));
    }
  } catch {
    /* asked again the next time Ask opens */
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

async function send(n: number, text: string, signal: AbortSignal) {
  const change = (make: (turn: Turn) => Partial<Turn>) => {
    talk.value = talk.value.map((turn) => (turn.n === n ? { ...turn, ...make(turn) } : turn));
  };
  change(() => ({ waiting: false, busy: true }));
  const take = (event: Event) => {
    if (event.t === "say") change((turn) => ({ answer: turn.answer + event.text, doing: "" }));
    else if (event.t === "doing") change(() => ({ doing: DOING[event.what] ?? "Working" }));
    else if (event.t === "card") change((turn) => ({ cards: [...turn.cards, event.card] }));
    else change(() => ({ busy: false, doing: "", ms: event.ms, status: event.status }));
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
    change((turn) => (turn.busy ? { busy: false, status: turn.status || "failed", answer: turn.answer || "Hub stopped before it answered." } : {}));
    if (talk.value.some((turn) => turn.n === n && turn.cards.some((card) => card.kind === "memory"))) void loadAble();
  } catch {
    if (signal.aborted) {
      change((turn) => ({ busy: false, doing: "", status: "stopped",
        answer: (turn.answer ? turn.answer + "\n" : "") + (turn.cards.some((card) => card.batch) ? "Stopped. What Hub already did is below, with Undo." : "Stopped.") }));
    } else {
      change((turn) => ({ answer: turn.answer || "Can’t reach Hub. Nothing was asked.", busy: false, status: "failed" }));
    }
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
