// The family brief's own parts, shared by Today on the phone and the wall in the kitchen: the weather drawn as
// it is said, the locks and doors, the line Ask's model wrote, and the caretaker's word on the night.
import { signal } from "@preact/signals";
import * as api from "./api";
import { say } from "./state";
import type { Day, HouseRow, Overnight, Part, Weather, WholeDay } from "./types";

// ---- the weather, drawn: a sun in lamplight, the rest in ink ----

const CLOUD = "M7.2 18.5h9.9a3.9 3.9 0 0 0 .55-7.76 5.4 5.4 0 0 0-10.36-1.2A4.5 4.5 0 0 0 7.2 18.5z";
const HIGH_CLOUD = "M7.4 15h9.4a3.6 3.6 0 0 0 .5-7.17 5 5 0 0 0-9.6-1.1A4.15 4.15 0 0 0 7.4 15z";
const SMALL_CLOUD = "M9.4 19.5h7.7a3.2 3.2 0 0 0 .45-6.37 4.4 4.4 0 0 0-8.45-.98A3.7 3.7 0 0 0 9.4 19.5z";
const RAYS = "M12 2.6v2.1M12 19.3v2.1M2.6 12h2.1M19.3 12h2.1M5.35 5.35l1.5 1.5M17.15 17.15l1.5 1.5M5.35 18.65l1.5-1.5M17.15 6.85l1.5-1.5";
const MOON = "M19.2 14.6A7.6 7.6 0 0 1 9.4 4.8a7.6 7.6 0 1 0 9.8 9.8z";

const SKY: Record<string, preact.JSX.Element> = {
  sun: <g class="lit"><circle cx="12" cy="12" r="4.3" /><path d={RAYS} /></g>,
  moon: <path d={MOON} />,
  cloud: <path d={CLOUD} />,
  partly: <><g class="lit"><circle cx="8.6" cy="8.6" r="3.2" /><path d="M8.6 2.6v1.4M2.6 8.6h1.4M4.35 4.35l1 1M12.85 4.35l-1 1M4.35 12.85l1-1" /></g><path d={SMALL_CLOUD} /></>,
  "partly-night": <><path d="M13.6 9.4A4.6 4.6 0 0 1 7.6 3.4a4.6 4.6 0 1 0 6 6z" /><path d={SMALL_CLOUD} /></>,
  rain: <><path d={HIGH_CLOUD} /><path d="M8.6 17.6l-1 2.6M12.6 17.6l-1 2.6M16.6 17.6l-1 2.6" /></>,
  pour: <><path d={HIGH_CLOUD} /><path d="M7.6 17.4l-1.3 3.4M11.1 17.4l-1.3 3.4M14.6 17.4l-1.3 3.4M18.1 17.4l-1.3 3.4" /></>,
  storm: <><path d={HIGH_CLOUD} /><path class="lit" d="M12.6 15.6l-2.2 3.4h3l-1.8 3.2" /></>,
  snow: <><path d={HIGH_CLOUD} /><path d="M8.5 18.5h.01M12 20.5h.01M15.5 18.5h.01M10.2 22h.01M13.8 22h.01" class="flakes" /></>,
  sleet: <><path d={HIGH_CLOUD} /><path d="M8.6 17.6l-1 2.6M15.6 17.6l-1 2.6" /><path d="M12 19.5h.01" class="flakes" /></>,
  hail: <><path d={HIGH_CLOUD} /><circle cx="8.6" cy="19" r=".9" /><circle cx="12.4" cy="20.6" r=".9" /><circle cx="16" cy="19" r=".9" /></>,
  fog: <path d="M4 9.5h16M6 13h12M4 16.5h16M8 20h8" />,
  wind: <path d="M3.5 9.5h10.2a2.6 2.6 0 1 0-2.5-3.2M3.5 13.5h14.4a2.7 2.7 0 1 1-2.6 3.4M3.5 17h7" />,
  alert: <path d="M12 4.2l8.6 15H3.4zM12 10v4.2M12 16.9h.01" />,
};

export function Sky({ icon, label, big }: { icon: string; label?: string; big?: boolean }) {
  return (
    <svg class={"wx" + (big ? " big" : "")} viewBox="0 0 24 24" role={label ? "img" : undefined} aria-label={label} aria-hidden={label ? undefined : "true"}>
      {SKY[icon] ?? SKY.cloud}
    </svg>
  );
}

/** The day the weather is about: today until five, then tomorrow. */
export function aboutTomorrow(part: Part | undefined): boolean {
  return part === "evening" || part === "night";
}

function dayOf(weather: Weather, part: Part | undefined): WholeDay | null {
  return aboutTomorrow(part) ? weather.tomorrow ?? weather.today : weather.today;
}

/** The weather on the phone: now and the day in one line, what to wear, the next hours, and the days after. */
export function WeatherBlock({ day }: { day: Day }) {
  const weather = day.weather;
  if (!weather) return null;
  const later = aboutTomorrow(day.part);
  const whole = dayOf(weather, day.part);
  return (
    <section class="block wxblock" aria-label="Weather">
      <h2>{later ? "Tomorrow’s weather" : "Weather"}</h2>
      <div class="wxnow">
        <Sky icon={later && whole ? whole.icon : weather.now.icon} big />
        <div class="wxnow-text">
          <b>{later && whole ? whole.high : weather.now.temp}<small>{later ? whole?.words : weather.now.words}</small></b>
          <span>{[whole && (later ? `Low ${whole.low}` : `High ${whole.high} · Low ${whole.low}`), whole?.rain].filter(Boolean).join(" · ")}</span>
        </div>
      </div>
      {whole?.wear && <p class="wear">{whole.wear}{later ? " tomorrow" : ""}</p>}
      {!later && weather.hours.length > 1 && <Hours weather={weather} />}
      <Days weather={weather} />
      {weather.sun.rise && <p class="sunline">{weather.sun.up || later ? `Sunset ${weather.sun.set}` : `Sunrise ${weather.sun.rise}`}{later ? ` · Sunrise ${weather.sun.rise}` : ""}</p>}
    </section>
  );
}

/** The days after today: the name, the sky, the high and the low. */
export function Days({ weather, most }: { weather: Weather; most?: number }) {
  if (!weather.days.length) return null;
  return (
    <div class="wxdays">
      {weather.days.slice(0, most ?? weather.days.length).map((d) => (
        <div class="wxday" key={d.date}>
          <span>{d.day}</span><Sky icon={d.icon} label={d.words} /><b>{d.high}</b><small>{d.low}</small>
        </div>
      ))}
    </div>
  );
}

export function Hours({ weather, most }: { weather: Weather; most?: number }) {
  const shown = weather.hours.slice(0, most ?? weather.hours.length);
  return (
    <div class="hours" role="list" aria-label="The next hours">
      {shown.map((h) => (
        <div class={"hour" + (h.wet ? " wet" : "")} role="listitem" key={h.at} aria-label={`${h.at}: ${h.temp}, ${h.words}`}>
          <span>{h.at}</span><Sky icon={h.icon} /><b>{h.temp}</b>
        </div>
      ))}
    </div>
  );
}

// ---- the house: locks, the alarm, the garage, doors left open ----

const KINDS: Record<HouseRow["kind"], string> = { lock: "Lock", alarm: "Alarm", garage: "Garage", open: "Open" };

export function HouseBlock({ day }: { day: Day }) {
  const house = day.house;
  if (!house || !house.rows.length) return null;
  return (
    <section class="block" aria-label="The house">
      <h2>The house</h2>
      {house.says && <p class="block-says">{house.says}</p>}
      <div class="rows">
        {house.rows.map((row) => (
          <div class={"row free houserow" + (row.tone ? " " + row.tone : "") + (row.low ? " low" : "")} key={row.kind + row.name}>
            <div class="row-main"><b>{row.name}</b>{row.note && <span>{row.note}</span>}</div>
            <span class="state" aria-label={`${KINDS[row.kind]}: ${row.state}`}>{row.state}</span>
          </div>
        ))}
      </div>
    </section>
  );
}

// ---- the line Ask's model wrote, and the night ----

export function NoteLine({ day }: { day: Day }) {
  if (!day.note) return null;
  return <p class="brief-note">{day.note.text}</p>;
}

/** Until noon, the caretaker's headline and its lines; after, only the headline, unless something still needs someone. */
export function OvernightBlock({ night, part, short }: { night: Overnight | null | undefined; part: Part | undefined; short?: boolean }) {
  if (!night) return null;
  const whole = !short && (part === "morning" || night.needs > 0);
  return (
    <section class={"overnight" + (night.needs > 0 ? " needs" : "")} aria-label="Overnight">
      <h3>Overnight</h3>
      <p>{night.head}</p>
      {whole && night.lines.length > 0 && (
        <ul>
          {night.lines.map((line) => <li class={line.tone} key={line.text}>{line.text}</li>)}
        </ul>
      )}
    </section>
  );
}

// ---- one tap onto the shopping list ----

export const adding = signal<string[]>([]);       // needs whose thing is on its way to the list

export async function addToList(key: string, thing: string) {
  adding.value = [...adding.value, key];
  const answer = await api.post("kitchen/add", { text: thing });
  adding.value = adding.value.filter((k) => k !== key);
  say(answer.error ?? answer.said ?? "That didn’t take.");
}
