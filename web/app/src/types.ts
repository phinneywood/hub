// The house as Hub sends it (hub/app/model.py). Names are data to show, never instructions.

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
export interface Stream { speaker: string; title: string; artist: string; speakers: string[]; rooms: string[] }
export interface Music {
  speaker: string; playing: boolean; state: string; title: string; artist: string; album: string;
  volume: number | null; speakers: Speaker[]; also: Stream[];
}
export interface Fact { label: string; value: string; tone: string }
export interface Floor { name: string; rooms: string[] }
export interface Scene { id: string; name: string; on: boolean; mine: boolean }
export interface House {
  ok: true; at: string; floors: Floor[]; rooms: Record<string, Room>; scenes: Scene[];
  music: Music | null; facts: Fact[]; lights_on: number;
}
export interface Down { ok: false; why: string }
export interface Me {
  signed_in: boolean; initial: string; versions: { js: string; css: string };
  links: { ha: string | null; music: string | null }; update?: boolean;
}
// Versions, as Hub sends them (hub/app/versions.py).
export interface Release {
  sha: string | null; short: string | null; live: string | null; title: string; changes: string[];
  running: boolean; commit: string | null; compare: string | null;
}
export interface Ready { sha: string; short: string; subjects: string[] }
export interface Deploy { ready: Ready | null; asked: boolean; put_back: string | null }
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
export interface Change { batch: string; what: string; at: number; via: string; unsure: string[]; not_done: string[]; undo: boolean; undone: boolean; where: "house" | "music" }
export interface Changes { changes: Change[]; waiting: { id: string; name: string; songs: string[] }[] }
// The Activity screen, as Hub sends it (hub/app/activity.py).
export type ActivityKind = "lights" | "security" | "buttons" | "routines" | "music" | "motion" | "other";
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
  looked_at?: number; made?: string; picks: Photo[]; more: Photo[];
  counts?: Record<"keep" | "skip" | "print", number>; waiting?: number;
}

// Today, as Hub sends it (hub/app/today.py).
export interface Open { href: string; label: string }
export interface Need { key: string; title: string; why: string; tone: string; open: Open; tick: string }
export interface Slot { time: string; half: string; title: string; note: string; warn: boolean; next: boolean }
export interface SchoolDay { day: string; title: string; note: string; struck: boolean }
export interface Trip { title: string; when: string; note: string; open: Open }
export interface Day {
  ok: true; at: string; needs: Need[]; more: number; today: Slot[]; tomorrow: Slot[]; school: SchoolDay[];
  trip: Trip | null; quiet: { text: string; open: Open }[];
}
export interface Said { ok?: boolean; said?: string; batch?: string | null; error?: string }

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

// Kitchen, as the kitchen service sends it through Hub (/kitchen/...). Its words are data to show.
export interface Recipe { title: string; url?: string; active?: string; total?: string; dishes?: string; diet?: string;
  have?: string; need?: string; recent?: string; check?: string }
export interface PlanItem { product: string; size?: string; qty: number; price?: string | number | null; url?: string }
export interface Plan { id: number; items: PlanItem[]; status: string; decided_by?: string | null }
export interface CartLine { product: string; status: string; qty: number; was: number | null; price: number | null }
export interface CartResult { in_cart: number; planned: number; lines: CartLine[]; total: number | null; unpriced?: boolean;
  cart_subtotal?: number | null }
export interface Order { id: number; status: string; decided_by?: string | null;
  review: { total: number; items: number; when: string; how?: string; subtotal?: number | null; fees?: number | null;
    savings?: number | null; tax?: number | null };
  placed?: { status: string; order_number?: string; total?: number | null; why?: string } | null }
export interface KitchenMessage { id: number; role: "member" | "assistant" | "notice"; text: string; author?: string | null; at: number;
  recipes?: Recipe[] | null; plan?: Plan | null; result?: CartResult | null; order?: Order | null }

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
export interface ArchiveLanding {
  ready: boolean; away?: string; owner: boolean; switches: { documents: boolean; chats: boolean }; sources: ArchiveSource[];
  calendar: { events: number; first: string | null; last: string | null; complete: boolean; read_at: number | null };
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
