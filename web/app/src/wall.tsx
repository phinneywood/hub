// The wall: the family brief on the kitchen iPad, all day, for anyone in the kitchen. A clock, the weather, the night,
// what the household needs and the house, kept fresh by itself. At night it dims to a clock and the morning's weather,
// and it shifts a few pixels now and then so nothing is burned into the screen. It is the household's only (the
// owner's decision): calendars, school, mail and the written note stay on each person's phone.
import { signal } from "@preact/signals";
import { useEffect } from "preact/hooks";
import * as api from "./api";
import { Days, Hours, OvernightBlock, Sky, aboutTomorrow, addToList, adding } from "./brief";
import { look, me } from "./state";
import type { Day, Need } from "./types";
import { ToastLine } from "./ui";

const EVERY = 60_000;
const NIGHT_EVERY = 15 * 60_000;      // asleep, the wall shows only the clock and the morning: a look each quarter hour
const CHECK_EVERY = 10 * 60_000;      // a new version of the app is picked up within ten minutes
const DIM = 22, BRIGHT = 5.5;         // from ten at night to half past five the wall is dim

const wall = signal<Day | null>(null);
const away = signal(false);           // Hub hasn't answered the last time it was asked
const now = signal(new Date());
const signedIn = signal(false);       // a wall that isn't signed in to Hub shows the list's button nowhere
let asked = 0;
let watching = false;

async function load() {
  asked = Date.now();
  try {
    const answer = await fetch("/app/api/today?wall=1", { headers: { accept: "application/json" } });
    if (!answer.ok) throw new Error("Hub is away");
    wall.value = (await answer.json()) as Day;
    away.value = false;
  } catch {
    away.value = true;
  }
}

/** The version of the app this page was opened with, from its own script's address. */
function built(): string {
  const script = document.querySelector<HTMLScriptElement>('script[src*="/static/app/app.js"]');
  return script ? new URL(script.src).searchParams.get("v") ?? "" : "";
}

/** Who the wall is to Hub, and whether a newer app is out: a wall nobody reloads would otherwise run the old one. */
async function checkIn() {
  try {
    const found = await api.home();
    signedIn.value = found.signed_in;
    me.value = { ...me.value, signed_in: found.signed_in, initial: found.initial, versions: found.versions };
    const mine = built();
    if (mine && found.versions?.js && found.versions.js !== mine) location.reload();
  } catch {
    /* Hub away: the wall says so, and the next check tries again */
  }
}

let lock: { release: () => Promise<void> } | null = null;
async function stayAwake() {
  // Keeps the iPad's screen on while the wall is showing, where the browser allows it. Without it the iPad's own
  // Auto-Lock setting decides.
  try {
    const wake = (navigator as unknown as { wakeLock?: { request: (kind: string) => Promise<{ release: () => Promise<void> }> } }).wakeLock;
    if (wake && document.visibilityState === "visible" && !lock) {
      lock = await wake.request("screen");
      (lock as unknown as EventTarget).addEventListener?.("release", () => (lock = null));
    }
  } catch {
    lock = null;
  }
}

function asleep(moment: Date): boolean {
  const hour = moment.getHours() + moment.getMinutes() / 60;
  return hour >= DIM || hour < BRIGHT;
}

/** Start keeping the wall fresh. Once, when it is first shown. */
export function watchWall() {
  if (watching) return;
  watching = true;
  void load();
  void checkIn();
  void stayAwake();
  window.setInterval(() => {
    if (document.visibilityState !== "visible") return;
    if (Date.now() - asked >= (asleep(new Date()) ? NIGHT_EVERY : EVERY) - 1000) void load();
  }, EVERY);
  window.setInterval(() => {
    const was = asleep(now.value);
    now.value = new Date();
    if (was && !asleep(now.value)) void load();          // morning: the whole day, at once
  }, 5_000);
  window.setInterval(() => void checkIn(), CHECK_EVERY);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible") return;
    now.value = new Date();
    void load();
    void stayAwake();
  });
  window.addEventListener("online", () => void load());
}

function clockParts(moment: Date): [string, string] {
  const text = moment.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
  const [time, half] = text.split(" ");
  return [time ?? text, (half ?? "").toLowerCase()];
}

/** Day or dusk follows the sun on the wall, unless this iPad was given a look of its own; at night it is dark. */
function useLook(day: Day | null, night: boolean) {
  useEffect(() => {
    const root = document.documentElement;
    root.dataset.wall = night ? "night" : "day";
    const up = day?.weather?.sun.rise ? day.weather.sun.up : null;
    if (night) root.dataset.theme = "dusk";
    else if (look.value !== "auto") root.dataset.theme = look.value;
    else if (up !== null) root.dataset.theme = up ? "day" : "dusk";
  });
}

function NeedLine({ need }: { need: Need }) {
  const busy = adding.value.includes(need.key);
  return (
    <div class={"row free wallneed " + need.tone}>
      <div class="row-main"><b>{need.title}</b>{need.why && <span>{need.why}</span>}</div>
      {need.add && signedIn.value && (
        <button type="button" class="row-act" disabled={busy} aria-label={`Add ${need.add} to the shopping list`}
          onClick={() => void addToList(need.key, need.add!)}>{busy ? "Adding" : "Add to list"}</button>
      )}
    </div>
  );
}

/** The locks, the alarm and the garage as tiles, so the house fits beside the day: its state in large type, red when
 * something is wrong, and the battery under it, red when it is low. */
function House({ day }: { day: Day }) {
  const house = day.house;
  if (!house || !house.rows.length) return null;
  return (
    <section class="wblock" aria-label="The house">
      <h2>The house</h2>
      {house.says && <p class="wsays">{house.says}</p>}
      <div class="wtiles">
        {house.rows.map((row) => (
          <div class={"wtile" + (row.tone ? " " + row.tone : "") + (row.low ? " low" : "")} key={row.kind + row.name}>
            <span>{row.name}</span><b>{row.state}</b>{row.note && <small>{row.note}</small>}
          </div>
        ))}
      </div>
    </section>
  );
}

const shift = (moment: Date) => ({ "--shift": String(moment.getMinutes() % 7) }) as preact.JSX.CSSProperties;

/** Asleep: the time, and the next day's weather, by its name so it reads the same before and after midnight. */
function Night({ day, moment }: { day: Day | null; moment: Date }) {
  const [time, half] = clockParts(moment);
  const early = moment.getHours() < 12;               // after midnight the morning is today's
  const sky = early ? day?.weather?.today : day?.weather?.tomorrow;
  const next = new Date(moment);
  if (!early) next.setDate(next.getDate() + 1);
  const name = next.toLocaleDateString("en-US", { weekday: "long" });
  return (
    <div class="wall asleep" style={shift(moment)}>
      <div class="wnight">
        <p class="wclock">{time}<small>{half}</small></p>
        {sky && (
          <p class="wnight-line"><Sky icon={sky.icon} label={sky.words} /> {name}: {sky.high} and {sky.words.toLowerCase()}{sky.wet ? `. ${sky.rain}` : ""}</p>
        )}
      </div>
    </div>
  );
}

export function Wall() {
  useEffect(() => {
    watchWall();
    return () => {          // leaving the wall for the app: the app's own look again
      const root = document.documentElement;
      delete root.dataset.wall;
      root.dataset.theme = look.value === "auto" ? (matchMedia("(prefers-color-scheme: dark)").matches ? "dusk" : "day") : look.value;
    };
  }, []);
  const day = wall.value, moment = now.value;
  const night = asleep(moment);
  useLook(day, night);
  if (night) return <Night day={day} moment={moment} />;
  const [time, half] = clockParts(moment);
  const date = moment.toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" });
  if (!day) {
    return (
      <div class="wall" style={shift(moment)}>
        <div class="wcol wtime"><p class="wclock">{time}<small>{half}</small></p><p class="wdate">{date}</p></div>
        <div class="wcol"><p class="wquiet">{away.value ? "Can’t reach Hub. The wall tries again every minute." : "Reading the day…"}</p></div>
      </div>
    );
  }
  const later = aboutTomorrow(day.part);
  const weather = day.weather;
  const whole = weather ? (later ? weather.tomorrow : weather.today) : null;
  const elsewhere = day.elsewhere ?? 0;
  const needs = (
    <section class="wblock">
      <h2>Needs attention</h2>
      {day.needs.length ? <div class="rows">{day.needs.map((need) => <NeedLine need={need} key={need.key} />)}</div>
        : <p class="wquiet">Nothing for the household.</p>}
      {elsewhere > 0 && <p class="wquiet">{elsewhere === 1 ? "One more thing is on the phone." : `${elsewhere} more things are on the phone.`}</p>}
    </section>
  );
  return (
    <div class={"wall" + (away.value ? " stale" : "")} style={shift(moment)}>
      <div class="wcol wtime">
        <p class="wclock">{time}<small>{half}</small></p>
        <p class="wdate">{date}</p>
        {weather && (
          <div class="wweather" aria-label="Weather">
            <div class="wnow">
              <Sky icon={weather.now.icon} big label={weather.now.words} />
              <b>{weather.now.temp}</b>
              <span>{weather.now.words}{whole && <><br />{later ? "Tomorrow " : ""}{whole.high} / {whole.low}</>}</span>
            </div>
            {whole?.rain && <p class={"wrain" + (whole.wet ? " wet" : "")}>{later ? `Tomorrow: ${whole.rain.toLowerCase()}` : whole.rain}</p>}
            {whole?.wear && <p class="wear">{whole.wear}{later ? " tomorrow" : ""}</p>}
            {weather.hours.length > 1 && <Hours weather={weather} most={8} />}
            <Days weather={weather} most={4} />
          </div>
        )}
      </div>
      <div class="wcol wside">
        {!later && <OvernightBlock night={day.overnight} part={day.part} short />}
        {needs}
        <House day={day} />
      </div>
      {away.value && <p class="wstale">Can’t reach Hub. Showing the day as it was.</p>}
      <ToastLine />
    </div>
  );
}
