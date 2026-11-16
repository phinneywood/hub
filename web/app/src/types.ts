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
  links: { ha: string | null; music: string | null }; update?: boolean; care?: Care;
}
// The house's caretaker, as Hub reads its folder (hub/caretaker.py).
export interface Care { set_up: boolean; alive: boolean; looked: string; needs: number }
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
  set_up: boolean; alive: boolean; headline: string; sub: string; mode?: string | null; paused?: boolean;
  needs?: CareNeed[]; today?: number; night?: { title: string; headline: string; lines: CareLine[] };
  systems?: CareSystem[]; week?: CareDay[]; week_says?: string; sturdier?: CareSturdier[];
  batteries?: { name: string; room: string; level: number; reachable: boolean }[];
  ignoring?: { name: string; why: string }[]; drills?: { name: string; words: string }[]; drill?: CareDrill | null;
  answers?: { id: string; do: string; status: string; words: string }[];
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
export interface Said { ok?: boolean; said?: string; batch?: string | null; error?: string; card?: Card }

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
export interface Option { product: string; size: string; why: string; times: number; last: string; price: string; url: string; note: string }
export interface Item {
  id: number; name: string; qty: number; asked: number | null; source: string; by: string; note: string; product: string; size: string; why: string;
  times: number; last: string; price: string; about: string; left: boolean; options: Option[];
  state: "pick" | "ready" | "adding" | "cart" | "unavailable" | "unknown" | "removing";
}
export interface Shop {
  ok: true; items: Item[]; plan: Plan | null; order: Order | null; practice: boolean; auto: boolean; wait: number; working: string;
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
  recipes?: Recipe[] | null; plan?: Plan | null; result?: CartResult | null; order?: Order | null }
