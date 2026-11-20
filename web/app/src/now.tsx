// What Hub says first in Ask: the day in a sentence, in its own bubble, with what needs you and the house in a line
// with its scenes under it. At most three cards, so Ask stays a conversation and never becomes a feed. A feature that
// needs a person right now (an errand due, the house unwell) takes the third place; everything else is reached by asking.
import { day } from "./day";
import { inApp, NeedRow, SlotRow } from "./today";
import { go, me, runScene, running, seen, trouble } from "./state";
import type { Day, House, Slot } from "./types";
import { talk } from "./asking";
import { Bars, Icon } from "./ui";
import { daySays, daySub, familyOf, familySizes, houseSays, list, playingIn, saysNext, when } from "./words";

/** The top of Ask, as at the top of a conversation: who you are talking to, and whether it can hear you. */
export function ChatHead() {
  const wrong = trouble.value === "hub" ? "Can’t reach Hub" : trouble.value === "house" ? "The house isn’t answering" : "";
  const busy = talk.value.some((turn) => turn.busy);
  // One clock for the line at the top and Hub's greeting, so "Thursday afternoon" never sits over "Good evening".
  const at = day.value ? new Date(day.value.at) : undefined;
  return (
    <div class="top chathead">
      <span class={"hubmark" + (busy ? " busy" : "") + (wrong ? " away" : "")} aria-hidden="true" />
      <span class="chat-who"><b>Hub</b><span>{wrong || when(at)}</span></span>
      <button type="button" class={"who" + (trouble.value ? " bad" : "")} aria-label="System" onClick={() => go("system")}>{me.value.initial}</button>
    </div>
  );
}

function hello(at: Date): string {
  const hour = at.getHours();
  return hour < 5 ? "Hello." : hour < 12 ? "Good morning." : hour < 17 ? "Good afternoon." : "Good evening.";
}

/** Hub speaks first: the day in a sentence, in Hub's own bubble, with what needs you and the house under it. */
export function Greeting() {
  const now = day.value, home = seen.value;
  const outside = home?.facts.find((fact) => fact.label === "Outside")?.value ?? "";
  const said = now ? daySays(now) : home ? houseSays(home) : "What would you like?";
  const sub = now ? daySub(now, outside) : "";
  const waiting = now ? now.needs.length + now.more : 0;
  return (
    <div class="hub greet">
      <p class="greet-text"><span class="hello">{hello(now ? new Date(now.at) : new Date())}</span> {said}</p>
      {sub && <p class="greet-sub">{sub}</p>}
      {waiting > 0 && <button type="button" class="greet-more" onClick={() => go("today")}>Open Today<Icon name="right" /></button>}
    </div>
  );
}

function upNext(now: Day): Slot | undefined {
  return now.today.find((slot) => slot.next) ?? now.today[0];
}

/** What needs you: the first few of Today's rows (`most`: fewer on a phone, where the house card shares the screen;
 * 0 once the day's morning message is in the conversation, which says it instead: then only the day's next thing). */
export function NeedsCard({ most }: { most: number }) {
  const now = day.value;
  if (!now) return null;
  const more = Math.max(0, now.needs.length - most) + now.more;
  const next = now.needs.length && most ? undefined : upNext(now);
  if (!most) {      // the morning message in the conversation says what needs you; this keeps the day's next thing,
    if (next && saysNext(now)) return null;      // unless Hub's greeting already said it, with Open Today under it
    return next ? (
      <section class="now" aria-label="Next">
        <div class="rows"><SlotRow slot={next} /></div>
        <button type="button" class="now-more" onClick={() => go("today")}>Open Today<Icon name="right" /></button>
      </section>
    ) : null;
  }
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
  const families = familySizes(now.scenes);
  const rooms = Object.values(now.rooms).filter((room) => !room.outside).slice(0, 12);
  const music = now.music?.playing ? now.music : null;
  const where = music ? list(playingIn(music)) : "";
  return (
    <section class="now house-now" aria-label="The house">
      <button type="button" class="house-line" onClick={() => go("house")}>
        <span class="house-words"><b>{houseSays(now)}</b>
          {music && <span><Bars />{music.title ? `${music.title}${where ? " · " + where : ""}` : where}</span>}</span>
        <span class="tiny" aria-hidden="true">
          {rooms.map((room) => <i key={room.id} class={room.on ? "lit" : ""} style={room.on ? { opacity: 0.45 + 0.55 * (room.level / 100) } : undefined} />)}
        </span>
      </button>
      {now.scenes.length > 0 && (
        <div class="chips">
          {now.scenes.filter((s) => !families.has(familyOf(s.name))).map((s) => (
            <button type="button" key={s.id} class={"chip" + (s.on ? " on" : "") + (running.value === s.id ? " busy" : "")} aria-pressed={s.on}
              onClick={() => void runScene(s)}>{s.name}</button>
          ))}
          {[...families].map(([f, n]) => (          // a family of presets: one chip, opening them on House
            <button type="button" key={"family-" + f} class="chip family" onClick={() => go("house")}>{f} · {n}</button>
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
