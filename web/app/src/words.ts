// Hub speaks in sentences. These write them from the house as it is.
import type { Day, House, Music, Slot } from "./types";

export const NUMBER_WORDS = ["No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve"];
// Rooms said the way people say them: "in the kitchen", "in the front yard". A room made only of these everyday
// words is said that way; one with a name in it ("Robin’s room") keeps its name.
const EVERYDAY = new Set(("kitchen living dining room bedroom office hallway hall bath bathroom porch yard basement garage "
  + "entry study nursery den deck pantry laundry foyer stairs guest family sun attic patio garden front back side "
  + "upstairs downstairs primary main sitting area door play playroom mud mudroom closet loft landing lower upper "
  + "tv media library workshop shed driveway balcony terrace hallway lounge kids").split(" "));

export function list(items: string[]): string {
  if (items.length < 2) return items.join("");
  return items.slice(0, -1).join(", ") + " and " + items[items.length - 1];
}

// A few rooms are named in shorthand on the plan and said in full.
const FULL: Record<string, string> = { dining: "dining room", bath: "bathroom", guest: "guest room", living: "living room" };

export function spoken(name: string): string {
  const plain = name.toLowerCase().trim();
  return plain.split(/\s+/).every((word) => EVERYDAY.has(word)) ? "the " + (FULL[plain] ?? plain) : name;
}

/** How lit the house is. Lights outdoors are not the house: a lit porch on a dark house is still a dark house. */
export function houseSays(house: House): string {
  const lit = Object.values(house.rooms).filter((r) => r.on && !r.outside).length;
  if (lit === 0) return "The house is dark.";
  if (lit === 1) return "One room is lit.";
  return `${NUMBER_WORDS[lit] ?? lit} rooms are lit.`;
}

export function playingIn(music: Music): string[] {
  return music.speakers.filter((s) => s.in && !s.offline).map((s) => s.name);
}

export function musicSays(music: Music | null): string {
  if (!music || !music.playing) return "The house is quiet.";
  const where = playingIn(music).map(spoken);
  const what = music.title ? `“${music.title}”` : "Music";
  const elsewhere = (music.also ?? []).flatMap((stream) => stream.rooms).map(spoken);
  return (where.length ? `${what} is playing in ${list(where)}.` : `${what} is playing.`)
    + (elsewhere.length ? ` Something else is playing in ${list(elsewhere)}.` : "");
}

export function when(now = new Date()): string {
  const day = now.toLocaleDateString(undefined, { weekday: "long" });
  const hour = now.getHours();
  const part = hour < 5 ? "night" : hour < 12 ? "morning" : hour < 17 ? "afternoon" : hour < 21 ? "evening" : "night";
  return `${day} ${part}`;
}

export function clock(at: string): string {
  const date = new Date(at);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

/** "just now", "5 minutes ago", "3 hours ago", else the day and time. */
export function ago(at: string, now = new Date()): string {
  const date = new Date(at);
  if (Number.isNaN(date.getTime())) return "";
  const minutes = Math.round((now.getTime() - date.getTime()) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? "" : "s"} ago`;
  if (minutes < 12 * 60) { const hours = Math.round(minutes / 60); return `${hours} hour${hours === 1 ? "" : "s"} ago`; }
  return date.toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" });
}

export function howMany(count: number, thing: string): string {
  return `${NUMBER_WORDS[count] ?? count} ${thing}${count === 1 ? "" : "s"}`;
}

// ---- Today ----

const HOURS = ["twelve", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven"];

/** A time the way it is said: "seven", "noon", "9:30". */
function saidTime(slot: Slot): string {
  const [hour, minute] = slot.time.split(":");
  if (minute !== "00") return slot.time;
  if (hour === "12") return slot.half === "pm" ? "noon" : "midnight";
  return HOURS[Number(hour) % 12] ?? slot.time;
}

function timed(slot: Slot): boolean {
  return slot.time.includes(":");
}

/** The headline: the next thing today, then how much needs you: the list, and the errands ready for a tap. */
export function daySays(day: Day): string {
  const count = day.needs.length + day.more + (day.ready ? day.ready.cards.length + day.ready.more : 0);
  const needs = count === 0 ? "Nothing needs you." : count === 1 ? "One thing needs you." : `${NUMBER_WORDS[count] ?? count} things need you.`;
  const next = day.today.find((slot) => slot.next);
  if (!next) return needs;
  return (next.time === "Now" ? `${next.title} is on now. ` : timed(next) ? `${next.title} is at ${saidTime(next)}. ` : "") + needs;
}

/** Under the headline: the weather as Hub says it (or the temperature outside, when there's no forecast), and how
 * tomorrow starts once today is done. */
export function daySub(day: Day, outside: string): string {
  const weather = day.weather_says || (outside ? `${outside} outside.` : "");
  const first = day.tomorrow.find(timed);
  const tomorrow = !day.today.some((slot) => slot.next) && first ? `Tomorrow starts with ${first.title} at ${saidTime(first)}.` : "";
  return [weather, tomorrow].filter(Boolean).join(" ");
}

/** What the rest of today is called at this hour. */
export function partOfDay(at: string): string {
  const hour = new Date(at).getHours();
  return hour < 12 ? "Today" : hour < 17 ? "This afternoon" : "This evening";
}

/** A family of scenes: this many or more sharing a name before "·" (a light bridge's presets, "Window glow·Amber 1"
 * and 19 more) are shown as one, so the scenes people use stay in view. */
export const FAMILY = 4;
export const familyOf = (name: string) => { const cut = name.indexOf("·"); return cut > 0 ? name.slice(0, cut).trim() : ""; };
export function familySizes(scenes: { name: string }[]): Map<string, number> {
  const sizes = new Map<string, number>();
  for (const s of scenes) { const f = familyOf(s.name); if (f) sizes.set(f, (sizes.get(f) ?? 0) + 1); }
  for (const [f, n] of sizes) if (n < FAMILY) sizes.delete(f);
  return sizes;
}

