// Right now, at the top of Ask: the day in a sentence with what needs you, and the house in a line with its scenes.
// At most three cards, so Ask stays a conversation and never becomes a feed. A feature that needs a person right
// now (an errand due, the house unwell) takes the third place; everything else is reached by asking.
import { day } from "./day";
import { inApp, NeedRow, SlotRow } from "./today";
import { go, runScene, running, seen } from "./state";
import type { Day, House, Slot } from "./types";
import { Icon } from "./ui";
import { daySays, daySub, houseSays } from "./words";

/** The sentence Ask opens with: the day when Hub has read it, otherwise the house. */
export function Headline() {
  const now = day.value, home = seen.value;
  const outside = home?.facts.find((fact) => fact.label === "Outside")?.value ?? "";
  if (now) return <><h1 class="say">{daySays(now)}</h1>{daySub(now, outside) && <p class="sub">{daySub(now, outside)}</p>}</>;
  return home ? <h1 class="say">{houseSays(home)}</h1> : <h1 class="say">Ask Hub</h1>;
}

function upNext(now: Day): Slot | undefined {
  return now.today.find((slot) => slot.next) ?? now.today[0];
}

/** What needs you: the first few of Today's rows (`most`: fewer on a phone, where the house card shares the screen). */
export function NeedsCard({ most }: { most: number }) {
  const now = day.value;
  if (!now) return null;
  const more = Math.max(0, now.needs.length - most) + now.more;
  const next = now.needs.length ? undefined : upNext(now);
  if (!now.needs.length && !next) return null;
  return (
    <section class="now" aria-label="Needs you">
      {now.needs.length > 0 && <h2>Needs you</h2>}
      <div class="rows">
        {now.needs.slice(0, most).map((need) => <NeedRow need={need} key={need.key} />)}
        {next && <SlotRow slot={next} />}
      </div>
      <button type="button" class="now-more" onClick={() => go("today")}>
        {more > 0 ? (more === 1 ? "One more in Today" : `${more} more in Today`) : "Open Today"}<Icon name="right" />
      </button>
    </section>
  );
}

/** The house in a line: how many rooms are lit, a tiny plan, and the scenes. Opens House. What plays is on the bar below. */
export function HouseCard({ now }: { now: House }) {
  const rooms = Object.values(now.rooms).filter((room) => !room.outside).slice(0, 12);
  return (
    <section class="now house-now" aria-label="The house">
      <button type="button" class="house-line" onClick={() => go("house")}>
        <span class="house-words"><b>{houseSays(now)}</b></span>
        <span class="tiny" aria-hidden="true">
          {rooms.map((room) => <i key={room.id} class={room.on ? "lit" : ""} style={room.on ? { opacity: 0.45 + 0.55 * (room.level / 100) } : undefined} />)}
        </span>
      </button>
      {now.scenes.length > 0 && (
        <div class="chips">
          {now.scenes.map((s) => (
            <button type="button" key={s.id} class={"chip" + (s.on ? " on" : "") + (running.value === s.id ? " busy" : "")} aria-pressed={s.on}
              onClick={() => void runScene(s)}>{s.name}</button>
          ))}
        </div>
      )}
    </section>
  );
}

/** What an answer about the day, mail, the calendar or school ends in: the same rows Today draws, to tap. */
export function DayRows({ show }: { show: string[] }) {
  const now = day.value;
  if (!now) return null;
  const mail = show.includes("mail") && !show.includes("today");
  const needs = show.includes("today") || show.includes("mail")
    ? now.needs.filter((need) => !mail || inApp(need.open.href) === "#mail") : [];
  const slots = show.includes("calendar") ? [...now.today, ...now.tomorrow].slice(0, 6) : [];
  const school = show.includes("school") ? now.school : [];
  if (!needs.length && !slots.length && !school.length) return null;
  return (
    <div class="dayrows">
      {needs.length > 0 && <div class="rows">{needs.slice(0, 5).map((need) => <NeedRow need={need} key={need.key} />)}</div>}
      {slots.length > 0 && <div class="rows">{slots.map((slot, n) => <SlotRow slot={slot} key={n} />)}</div>}
      {school.length > 0 && (
        <div class="rows">
          {school.map((s, n) => (
            <div class={"row" + (s.struck ? " struck" : "")} key={n}>
              <span class="row-t day">{s.day}</span>
              <div class="row-main"><b>{s.title}</b>{s.note && <span>{s.note}</span>}</div>
            </div>
          ))}
        </div>
      )}
      <button type="button" class="now-more" onClick={() => go(mail ? "mail" : "today")}>{mail ? "Open Mail" : "Open Today"}<Icon name="right" /></button>
    </div>
  );
}
