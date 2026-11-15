// Hub speaks in sentences. These write them from the house as it is.
import type { House, Music } from "./types";

const NUMBERS = ["No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve"];
// Rooms said the way people say them: "in the kitchen", but a named room keeps its name.
const PLAIN = new Set(["kitchen", "living room", "dining", "dining room", "bedroom", "office", "hallway", "hall", "bath",
  "bathroom", "porch", "yard", "basement", "garage", "entry", "study", "nursery", "den", "deck", "pantry", "laundry",
  "foyer", "stairs", "guest room", "family room", "sun room", "attic", "patio", "garden"]);

export function list(items: string[]): string {
  if (items.length < 2) return items.join("");
  return items.slice(0, -1).join(", ") + " and " + items[items.length - 1];
}

export function spoken(name: string): string {
  return PLAIN.has(name.toLowerCase()) ? "the " + name.toLowerCase() : name;
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
