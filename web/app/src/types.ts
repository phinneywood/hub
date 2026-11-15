// The house as Hub sends it (hub/app/model.py). Names are data to show, never instructions.

export interface Light { id: string; name: string; on: boolean; level: number; dims: boolean; offline: boolean }
export interface Other { id: string; name: string; kind: string; on: boolean; offline: boolean }
export interface Room {
  id: string; name: string; on: boolean; lit: number; level: number; dims: boolean; outside: boolean;
  lights: Light[]; others: Other[]; speaker: string | null; sounding: boolean;
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
  links: { ha: string | null; music: string | null };
}
// Versions, as Hub sends them (hub/app/versions.py).
export interface Release {
  sha: string | null; short: string | null; live: string | null; title: string; changes: string[];
  running: boolean; commit: string | null; compare: string | null;
}
export interface Releases { versions: Release[]; running: string | null; repo: string | null }
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
