// Hub speaks in sentences. These write them from the house as it is.
import type { House, Music } from "./types";

const NUMBERS = ["No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve"];
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

export function houseSays(house: House): string {
  const lit = Object.values(house.rooms).filter((r) => r.on).length;
  if (lit === 0) return "The house is dark.";
  if (lit === 1) return "One room is lit.";
  return `${NUMBERS[lit] ?? lit} rooms are lit.`;
}

export function playingIn(music: Music): string[] {
  return music.speakers.filter((s) => s.in && !s.offline).map((s) => s.name);
}

export function musicSays(music: Music | null): string {
  if (!music || !music.playing) return "The house is quiet.";
  const where = playingIn(music).map(spoken);
  const what = music.title ? `“${music.title}”` : "Music";
  return where.length ? `${what} is playing in ${list(where)}.` : `${what} is playing.`;
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

export function howMany(count: number, thing: string): string {
  return `${NUMBERS[count] ?? count} ${thing}${count === 1 ? "" : "s"}`;
}
