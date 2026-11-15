// Today: what needs a person, then the day. Read from Hub when the screen is looked at and every minute while
// it is; nothing about the day is kept on the device.
import { signal } from "@preact/signals";
import { say, view } from "./state";
import type { Day, Need } from "./types";

const EVERY = 60_000;

export const day = signal<Day | null>(null);
export const dayAway = signal(false);                 // Hub couldn't be asked for the day
export const ticked = signal<string[]>([]);           // needs just ticked, shown done until Hub's list agrees
let asked = 0;

export async function loadDay() {
  asked = Date.now();
  try {
    const answer = await fetch("/app/api/today", { headers: { accept: "application/json" } });
    if (!answer.ok) throw new Error("Hub is away");
    const found = (await answer.json()) as Day;
    day.value = found;
    dayAway.value = false;
    ticked.value = ticked.value.filter((key) => found.needs.some((need) => need.key === key));
  } catch {
    dayAway.value = true;
  }
}

async function tick(key: string, back: boolean): Promise<{ ok?: boolean; said?: string; error?: string }> {
  try {
    const answer = await fetch("/app/api/today/tick", {
      method: "POST", headers: { "content-type": "application/json", accept: "application/json" }, body: JSON.stringify({ key, back }),
    });
    return (await answer.json()) as { ok?: boolean; said?: string; error?: string };
  } catch {
    return { error: "Can’t reach Hub. Nothing was changed." };
  }
}

/** The circle beside a need: done with it (an email) or not today (anything else). Shows at once; Undo puts it back. */
export async function tickNeed(need: Need) {
  ticked.value = [...ticked.value, need.key];
  const answer = await tick(need.key, false);
  if (answer.error || !answer.ok) {
    ticked.value = ticked.value.filter((key) => key !== need.key);
    say(answer.error ?? "That didn’t take.");
  } else {
    say(answer.said ?? "Done", null, () => void untick(need));
  }
  void loadDay();
}

async function untick(need: Need) {
  const answer = await tick(need.key, true);
  ticked.value = ticked.value.filter((key) => key !== need.key);
  say(answer.error ?? "Put back");
  void loadDay();
}

/** Keep the day fresh while someone is looking at it. */
export function watchDay() {
  const due = () => view.value === "today" && !document.hidden && Date.now() - asked > EVERY - 1000;
  window.setInterval(() => { if (due()) void loadDay(); }, EVERY);
  document.addEventListener("visibilitychange", () => { if (due()) void loadDay(); });
  view.subscribe((where) => { if (where === "today" && Date.now() - asked > 5000) void loadDay(); });
}
