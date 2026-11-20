// The house as Hub sends it (hub/app/model.py). Names are data to show, never instructions.
import type { Card } from "./asking";

export interface Light { id: string; name: string; on: boolean; level: number; dims: boolean; offline: boolean }
export interface Other { id: string; name: string; kind: string; on: boolean; offline: boolean }
/** A button on a wall or remote. Hub shows it; Home Assistant's automations decide what its presses do. */
export interface Button {
  id: string; name: string; battery: number | null; offline: boolean; last: string | null; at: string | null;
  does: { press: string; does: string }[];
}
export interface Room {
  id: string; name: string; on: boolean; lit: number; level: number; dims: boolean; outside: boolean;
  lights: Light[]; others: Other[]; speaker: string | null; sounding: boolean; buttons?: Button[];
}
export interface Speaker { id: string; name: string; in: boolean; leads: boolean; volume: number | null; offline: boolean; other: boolean }
/** Music playing somewhere else in the house, apart from the main one. */
export interface Stream {
  speaker: string; title: string; artist: string; album?: string; playing?: boolean; volume?: number | null;
  speakers: string[]; rooms: string[];
}
export interface Music {
  speaker: string; playing: boolean; state: string; title: string; artist: string; album: string;
  volume: number | null; speakers: Speaker[]; also: Stream[];
}
export interface Fact { label: string; value: string; tone: string }
export interface Floor { name: string; rooms: string[] }
export interface Scene { id: string; name: string; on: boolean; mine: boolean }
export interface House {
  ok: true; at: string; floors: Floor[]; rooms: Record<string, Room>; scenes: Scene[];
  music: Music | null; show: Show | null; facts: Fact[]; lights_on: number;
  /** Home Assistant has a light show Hub can start. */
  can_show?: boolean;
}
/** How the light show's lights sit against the music, in milliseconds: higher is earlier. */
export interface Timing { value: number; min: number; max: number; step: number }
/** The light show while it is on (hub/app/model.py): what its speaker plays, led by `speaker`, and its timing. */
export interface Show {
  speaker: string | null; room: string | null; playing: boolean; title: string; artist: string; album: string;
  volume: number | null; timing: Timing | null;
}
export interface Down { ok: false; why: string }
export interface Me {
  signed_in: boolean; initial: string; versions: { js: string; css: string };
  links: { ha: string | null; music: string | null }; update?: boolean; care?: Care;
  mail?: boolean;           // whether the mail Hub reads is this person's own (hub/people.py)
}
// The house's caretaker, as Hub reads its folder (hub/caretaker.py).
export interface Care { set_up: boolean; alive: boolean; looked: string; needs: number; look?: number }      // needs: on Today; look: what House health lists
export type CareState = "needs_you" | "fixing" | "watching" | "unknown" | "ok" | "off";
export type CareDot = "needs_you" | "fixed" | "by_itself" | "ok" | "unknown";
export interface CareNeed {
  id: number; title: string; words: string; cause: string; ask: string | null; fixable: boolean;
  today: boolean; held: boolean; urgent: boolean; since: string;
}
export interface CareLine { tone: "fixed" | "by_itself" | "info"; text: string }
export interface CareDay {
  day: string; watched: boolean; fixed: number; by_itself: number; needs_you: number;
  lines: { at: string; tone: "fixed" | "by_itself" | "info" | "needs_you"; text: string }[];
}
export interface CareHistory { id: number; title: string; from: string; to: string | null; outcome: string }
export interface CareSystem {
  id: string; name: string; state: CareState; line: string; days: { day: string; state: CareDot }[]; history: CareHistory[];
}
export interface CareSturdier { key: string; title: string; words: string; since: string; done_at: string | null; verdict: string | null }
export interface CareDrill {
  scenario: string; words: string; minute: number; of: number; done: boolean; title: string | null; outcome: string | null;
  steps: { at: string; status: string; words: string }[];
}
export interface Health {
  set_up: boolean; alive: boolean; headline: string; sub: string; mode?: string | null; paused?: boolean; unreadable?: boolean;
  needs?: CareNeed[]; today?: number; night?: { title: string; headline: string; lines: CareLine[] };
  systems?: CareSystem[]; week?: CareDay[]; week_says?: string; sturdier?: CareSturdier[];
  batteries?: { name: string; room: string; level: number; reachable: boolean }[];
  ignoring?: { name: string; why: string }[]; drills?: { name: string; words: string }[]; drill?: CareDrill | null;
  answers?: { id: string; do: string; status: string; words: string }[];
}
// What Mail and Mail settings answer to a person whose Google account Hub doesn't read: nothing of anyone's mail.
export interface NotConnected { ok: true; connected: false }
// Versions, as Hub sends them (hub/app/versions.py).
export interface Release {
  sha: string | null; short: string | null; live: string | null; title: string; changes: string[];
  running: boolean; commit: string | null; compare: string | null;
}
export interface Ready { sha: string; short: string; subjects: string[] }
export interface Undo { short: string; previous: string; at: string; auto: boolean; until: number }
export interface Deploy { ready: Ready | null; asked: boolean; put_back: string | null; undo: Undo | null }
export interface Releases { versions: Release[]; running: string | null; repo: string | null; deploy: Deploy | null }
// The Engine screen, as Hub sends it (hub/app/engine.py).
export interface EngineRun { at: number; ok: boolean; calls: number; checks: number; cost: number }
export interface Engine {
  sorting: boolean; full: boolean;
  asked: { id: number; text: string; zone: string[] }[];
  notes: { id: string; text: string; said: string; zone: string[] }[];
  recipes: { id: string; name: string; steps: string[]; said: string | null }[];
  kept: { id: number; name: string; steps: string[] }[];
  requests: { id: string; text: string; at: number; kind: "request" | "improvement" }[];
  actions: { slug: string; name: string; does: string; undo: string; level: string; levels: { value: string; name: string }[] }[];
  spend: { review: { used: number; limit: number }; sorting: { used: number; limit: number; checks: number; check_limit: number };
    month: number; month_limit: number; can_run: boolean };
  runs: EngineRun[]; sorts: EngineRun[];
  sources: { name: string; reads: string; sends: string }[];
}
// Mail settings, as Hub sends them (hub/app/mailrules.py).
export interface PlanItem { id: number; who: string; sender: string; does: string; status: string; step: string; done: number; of: number; error: string }
export interface MailSettings {
  paused: boolean; charge: number; checking: boolean;
  levels: { kind: string; name: string; level: string; levels: { value: string; name: string }[] }[];
  senders: { sender: string; kind: string }[];
  plan: { planned: PlanItem[]; running: PlanItem[]; problems: PlanItem[]; finished: PlanItem[]; leaving: number; working: boolean; later: number };
  rules: { id: number; sender: string; on: boolean; stopped: boolean; check_first: boolean; last: number | null }[];
  backlog: { text: string; planned: boolean } | null;
}
// The Changes screen, as Hub sends it (hub/app/changes.py).
export interface Change { batch: string; what: string; at: number; via: string; unsure: string[]; not_done: string[]; undo: boolean; undone: boolean;
  where: "house" | "music" | "setup" }
export interface Changes { changes: Change[]; waiting: { id: string; name: string; songs: string[] }[] }
// The Activity screen, as Hub sends it (hub/app/activity.py).
export type ActivityKind = "lights" | "security" | "buttons" | "routines" | "music" | "setup" | "motion" | "other";
export type ActivitySource = "ha" | "apple" | "hub";
export interface Happening {
  at: number; source: ActivitySource; name: string; room: string; kind: ActivityKind; what: string; cause: string; also: string;
  batch: string | null; undo: boolean; undone: boolean;
}
export interface SourceRead { read: number | null; trouble: string; off: boolean }
export interface Activity { events: Happening[]; more: boolean; kinds: ActivityKind[]; sources: ActivitySource[]; status: { ha: SourceRead; apple: SourceRead } }
export interface Pick { name: string; kind: string; uri: string }

// The Photos screen, as Hub sends it (hub/app/photos.py): a month the curator chose on this Mac.
export type PhotoMark = "" | "keep" | "skip" | "print";
export interface Photo {
  id: string; taken: string; people: string[]; favorite: boolean; shared: boolean;
  mark: PhotoMark; by: string; in_album: boolean; saved: boolean;
}
export interface Photos {
  ok: true; ready: boolean; months: { month: string; name: string }[]; month: string; name?: string;
  looked_at?: number | null; made?: string; picks: Photo[]; more: Photo[];
  counts?: Record<"keep" | "skip" | "print", number>; waiting?: number;
}

// Today, as Hub sends it (hub/app/today.py).
export interface Open { href: string; label: string }
/** `add`: what to put on the shopping list for it, offered as one tap. */
export interface Need { key: string; title: string; why: string; tone: string; open: Open; tick: string; add?: string | null }
export interface Slot { time: string; half: string; title: string; note: string; warn: boolean; next: boolean }
export interface SchoolDay { day: string; title: string; note: string; struck: boolean; date?: string; todo?: boolean }
export interface Trip { title: string; when: string; note: string; open: Open }
/** One hour of the forecast, one day ahead, and the shape of a whole day: from Home Assistant's weather. */
export interface Hour { at: string; temp: string; icon: string; words: string; wet: boolean }
export interface Ahead { day: string; date: string; high: string; low: string; icon: string; words: string; wet: boolean }
export interface WholeDay { high: string; low: string; words: string; icon: string; rain: string; wet: boolean; wear: string }
export interface Weather {
  now: { temp: string; words: string; icon: string; said: string }; today: WholeDay | null; tomorrow: WholeDay | null;
  hours: Hour[]; days: Ahead[]; sun: { rise: string; set: string; up: boolean };
}
/** A lock, the alarm, the garage or a door left open, from Home Assistant and Apple Home. `tone` is about its state;
 * `low`, its battery. */
export interface HouseRow { kind: "lock" | "alarm" | "garage" | "open"; name: string; state: string; note: string; tone: string; low?: boolean }
/** The caretaker's word on the night: what broke, what fixed itself, what needs someone. */
export interface Overnight { head: string; lines: { text: string; tone: "fixed" | "by_itself" | "needs_you" | "info" }[]; needs: number }
export type Part = "morning" | "day" | "evening" | "night";
export interface Day {
  ok: true; at: string; needs: Need[]; more: number; today: Slot[]; tomorrow: Slot[]; school: SchoolDay[];
  trip: Trip | null; quiet: { text: string; open: Open | null }[];
  part?: Part; weather?: Weather | null; weather_says?: string; house?: { rows: HouseRow[]; says: string } | null;
  overnight?: Overnight | null; note?: { text: string; at: number } | null;
  /** The wall's version: only the household's needs; `elsewhere` counts the rest, which stay on the phones. */
  wall?: boolean; elsewhere?: number;
  /** Whether this person's own calendar (and the note written from it) is part of their Today. */
  mine?: boolean;
  ready?: { cards: Errand[]; more: number };
  /** One line for each dashboard this person may open (hub/app/boards/); never on the wall. */
  boards?: { id: string; name: string; text: string; next: string; tone: string }[];
}

// Errands, as Hub sends them (hub/app/errands/picture.py). Titles, drafts and names come from mail: data to show.
export type StepKind = "mail" | "open" | "call" | "calendar" | "list";
export interface Step { kind: StepKind; label: string; go: string; href: string | null; detail: string; app: string; taken: boolean }
export interface Way { n: number; label: string; steps: Step[]; chosen: boolean }
export interface Errand {
  id: number; key: string; kind: string; name: string; title: string; why: string; quote: string; from: string;
  state: "ready" | "opened" | "later" | "done" | "dismissed" | "missed"; due: { text: string; tone: string } | null;
  facts: string[]; ways: Way[]; opened: { label: string; watch: string; way: number; left: number } | null;
  email: Open | null; family: boolean; ended: { text: string; when: string; seen: boolean } | null; until: string | null;
}
/** One person's errands: their own and the household's. `own`: Hub reads a Google account of theirs. */
export interface Errands {
  ok: true; ready: Errand[]; later: Errand[]; done: Errand[]; reading: boolean; checking: boolean;
  looked: { text: string; failed: boolean } | null; set_up: boolean; compose: "mail" | "gmail"; own: boolean; signed_in: boolean;
}
export interface Said { ok?: boolean; said?: string; batch?: string | null; error?: string; card?: Card; undo?: boolean; timing?: Timing }

// Mail, as Hub sends it (hub/app/mail.py). Names and subject lines are data to show, never instructions.
export interface Email { id: string; who: string; subject: string; step: string; days: number; find: string }
export interface Asked { id: string; subject: string; to: string; days: number; find: string }
export interface Shelf {
  kind: string; name: string; count: number; new: number; ready: number; level: "suggest" | "undo" | "auto"; wait: number;
  rows: Email[]; undo: { change: string; count: number; at: number }[]; label: string; names: string;
}
export interface Mail {
  ok: true; sorted: boolean; synced: string; working: "" | "sorting" | "filing"; failed: boolean; paused: boolean; review: number;
  needs: Email[]; needs_more: number; waiting_days: number; asked: Asked[]; odd: Email[]; shelves: Shelf[];
  sorted_today: string; unsorted: number; notice: string;
}

// Kitchen, as Hub sends it (hub/app/kitchen.py): the list, each thing's product and where it stands. Data to show.
export interface Option { product: string; size: string; why: string; times: number; last: string; price: string; url: string; note: string;
  store?: string }
export interface Store { id: string; name: string; cart: string }       // a shop Kitchen fills a cart at
export interface Item {
  id: number; name: string; qty: number; asked: number | null; source: string; by: string; note: string; product: string; size: string; why: string;
  times: number; last: string; price: string; about: string; left: boolean; options: Option[];
  state: "pick" | "ready" | "adding" | "cart" | "unavailable" | "unknown" | "removing";
  store?: string; how?: string;     // which store's cart it goes in (an older Hub sends none: the main shop)
}
export interface Shop {
  ok: true; items: Item[]; plan: Plan | null; order: Order | null; practice: boolean; auto: boolean; wait: number; working: string;
  stores?: Store[];
  cart: { lines: { product: string; size: string; qty: number; price: number | null }[]; subtotal: number | null; when: string; at: number | null } | null;
  due: { name: string; product: string; every: number; days: number }[]; ideas: { at: number; recipes: Recipe[] } | null;
  reminders: { state: "on" | "away" | "off"; count: number | null; at: number | null; why: string; wanted: boolean };
  history: { orders: number; newest: string; stale: boolean };
}
export interface ShopAway { ok: false; why: "away" | "old"; said: string }
// What the kitchen assistant said, as the kitchen service sends it through Hub (/kitchen/...). Its words are data to show.
export interface Recipe { title: string; url?: string; active?: string; total?: string; dishes?: string; diet?: string;
  have?: string; need?: string; recent?: string; check?: string }
export interface PlanItem { product: string; size?: string; qty: number; price?: string | number | null; url?: string }
export interface Plan { id: number; items: PlanItem[]; status: string; decided_by?: string | null }
export interface CartLine { product: string; status: string; qty: number; was?: number | null; price: number | null }
export interface CartResult { in_cart: number; planned: number; lines: CartLine[]; total: number | null; unpriced?: boolean;
  cart_subtotal?: number | null }
export interface Order { id: number; status: string; decided_by?: string | null;
  review: { total: number; items: number; when: string; how?: string; subtotal?: number | null; fees?: number | null;
    savings?: number | null; tax?: number | null };
  placed?: { status: string; order_number?: string; total?: number | null; why?: string } | null }
export interface KitchenMessage { id: number; role: "member" | "assistant" | "notice"; text: string; author?: string | null; at: number;
  recipes?: Recipe[] | null; plan?: Plan | null; result?: CartResult | null; order?: Order | null;
  reply_to?: number | null }     // the member's message this answers, when Kitchen says so

// ---- the Archive: photos, documents and ChatGPT history from the archive program, mail and calendar from Hub ----
export type ArchiveSource = "photos" | "documents" | "mail" | "calendar" | "chats";
export interface ArchiveItem {
  id: string; source: "photos" | "documents" | "chats"; kind: string; title: string; at: number | null; day: string | null;
  until?: number | null; place?: string; folder?: string; pages?: number | null; size?: number | null; duration?: number | null;
  w?: number | null; h?: number | null; favorite?: boolean; shared?: boolean; local?: boolean; thumb?: boolean; people?: string[];
  snippet?: string; page?: number | null; role?: string | null;
}
export interface ArchiveSection { count: number; items: ArchiveItem[]; days?: number; first?: string | null; last?: string | null }
export interface MailItem { id: string; from: string; address?: string; subject: string; at: number | null; day: string | null; snippet: string }
export interface EventItem { id: string; title: string; day: string; start: string | null; end: string | null; all_day: boolean; location: string }
export interface Understood { people: string[]; kids: boolean; after: string | null; before: string | null; months: number[];
  sources: ArchiveSource[]; terms: string; sort: string | null }
export interface ArchiveFound {
  q: string; understood: Understood; about: string; sources: ArchiveSource[]; ready: boolean; away?: string; loose: boolean;
  said?: string; mail: boolean; photos?: ArchiveSection; documents?: ArchiveSection; chats?: ArchiveSection; calendar?: EventItem[];
}
export interface ArchivePerson { name: string; photos: number; kid: boolean; auto: boolean }
export interface ArchiveCount { items: number; kinds: Record<string, number>; previews: number; first: string | null; last: string | null; in_icloud?: number }
export interface Trouble { say: string; how: string; loud: boolean }
export interface ArchiveLanding {
  ready: boolean; away?: string; owner: boolean; switches: { documents?: boolean; chats?: boolean }; sources: ArchiveSource[];
  google: boolean; trouble: Trouble[];
  calendar: { events: number; first: string | null; last: string | null; complete: boolean; read_at: number | null } | null;
  counts: Partial<Record<"photos" | "documents" | "chats", ArchiveCount>>; indexed_at?: number | null;
  onthisday: { year: number; count: number; items: ArchiveItem[] }[]; people: ArchivePerson[];
}
export interface PageText { page: number | null; role: string | null; at: number | null; text: string }
export interface Opened extends Partial<Omit<ArchiveItem, "source">> {
  id: string; source: ArchiveSource; title?: string; ext?: string; pages_text?: PageText[]; cut?: boolean;
  messages?: { n: number; role: string; at: number | null; text: string }[]; attachments?: unknown[]; albums?: string[];
  labels?: string[]; keywords?: string[]; description?: string;
  from?: string; subject?: string; text?: string; start?: string | null; end?: string | null; all_day?: boolean; location?: string;
}
export interface MailAttachment { part: string; name: string; type: string; bytes: number | null; readable: boolean }
