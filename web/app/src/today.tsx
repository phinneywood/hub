// Today, the family brief on the phone: a sentence and the weather, the line Ask's model wrote, the night, then
// what needs you, the errands ready for a tap, the day, school, the house, tomorrow and a trip when one is near. In
// the evening tomorrow comes up.
import type { ComponentChildren, JSX } from "preact";
import { HouseBlock, NoteLine, OvernightBlock, WeatherBlock, addToList, adding, aboutTomorrow } from "./brief";
import { day, dayAway, tickNeed, ticked } from "./day";
import { BoardLines } from "./boards";
import { TodayErrands } from "./errands";
import { Top } from "./screens";
import { me, seen } from "./state";
import type { Day, Need, Slot } from "./types";
import { Icon } from "./ui";
import { daySays, daySub, partOfDay } from "./words";

// Mail, Kitchen and Engine are screens of the app now: their older addresses open here instead.
const HERE: Record<string, string> = {
  "/gmail": "#mail", "/kitchen": "#kitchen", "/engine": "#engine", "/engine#improvements": "#engine",
  "/engine/notes": "#engine", "/engine/recipes": "#engine", "/app/#versions": "#versions", "/app/#health": "#health",
  "/house/changes": "#changes", "/house/playlists": "#changes", "/calendar": "#board/calendar", "/insights": "#board/spend", "/gmail/plan": "#mailsettings", "/gmail/delayed": "#mailsettings", "/gmail/backlog": "#mailsettings",
};
export const inApp = (href: string) => HERE[href] ?? href;
const HERE_ALREADY = new Set(["/app/#today", "#today"]);       // a need about something this screen shows needs no link

export function NeedRow({ need }: { need: Need }) {
  const done = ticked.value.includes(need.key);
  const busy = adding.value.includes(need.key);
  const add = need.add && me.value.signed_in;
  return (
    <div class={"row tick need" + (done ? " on" : "")}>
      <button type="button" class="tickbtn" aria-label={`${need.tick}: ${need.title}`} disabled={done} onClick={() => void tickNeed(need)}>
        <span class="ring"><Icon name="check" /></span>
      </button>
      <div class="row-main"><b>{need.title}</b>{need.why && <span>{need.why}</span>}</div>
      {!done && add ? (
        <button type="button" class="row-act" disabled={busy} aria-label={`Add ${need.add} to the shopping list`}
          onClick={() => void addToList(need.key, need.add!)}>{busy ? "Adding" : "Add to list"}</button>
      ) : !done && !HERE_ALREADY.has(need.open.href) && <a class="row-act" href={inApp(need.open.href)}>{need.open.label}</a>}
    </div>
  );
}

export function SlotRow({ slot }: { slot: Slot }) {
  const words = slot.time === "All day" || slot.time === "Now" || slot.time === "Still";
  return (
    <div class={"row" + (slot.warn ? " warn" : "") + (slot.next ? " now" : "")}>
      <span class={"row-t" + (words ? " day" : "")}>{slot.time}{slot.half && <small>{slot.half}</small>}</span>
      <div class="row-main"><b>{slot.title}</b>{slot.note && <span>{slot.note}</span>}</div>
    </div>
  );
}

function Block({ title, children }: { title: string; children: ComponentChildren }) {
  return <section class="block"><h2>{title}</h2>{children}</section>;
}

function Needs({ now }: { now: Day }) {
  if (!now.needs.length) return null;
  return (
    <Block title="Needs you">
      <div class="rows">{now.needs.map((need) => <NeedRow need={need} key={need.key} />)}</div>
      {now.more > 0 && <p class="quiet-line">{now.more === 1 ? "One more thing can wait." : `${now.more} more things can wait.`}</p>}
    </Block>
  );
}

function Calendar({ title, slots }: { title: string; slots: Slot[] }) {
  if (!slots.length) return null;
  return <Block title={title}><div class="rows">{slots.map((slot, n) => <SlotRow slot={slot} key={n} />)}</div></Block>;
}

function School({ now }: { now: Day }) {
  if (!now.school.length) return null;
  return (
    <Block title="This week at school">
      <div class="rows">
        {now.school.map((s, n) => (
          <div class={"row" + (s.struck ? " struck" : "") + (s.todo ? " todo" : "")} key={n}>
            <span class="row-t day">{s.day}</span>
            <div class="row-main"><b>{s.title}</b>{s.note && <span>{s.note}</span>}</div>
          </div>
        ))}
      </div>
    </Block>
  );
}

function TripBlock({ now }: { now: Day }) {
  if (!now.trip) return null;
  return (
    <Block title={now.trip.title}>
      <div class="rows">
        <div class="row free">
          <div class="row-main"><b>{now.trip.when}</b>{now.trip.note && <span>{now.trip.note}</span>}</div>
          <a class="row-act" href={now.trip.open.href}>{now.trip.open.label}</a>
        </div>
      </div>
    </Block>
  );
}

/** Two columns on a wide screen, one after the other on a phone: what needs you and the errands ready for a tap,
 * then the rest of today and the weather. In the evening the rest of the evening, then tomorrow and its weather, come up. */
function Sections({ now }: { now: Day }) {
  const later = aboutTomorrow(now.part);
  const today = <Calendar title={partOfDay(now.at)} slots={now.today} key="today" />;
  const tomorrow = <Calendar title="Tomorrow" slots={now.tomorrow} key="tomorrow" />;
  const weather = <WeatherBlock day={now} key="weather" />;
  const first = [<Needs now={now} key="needs" />,
    now.ready && <TodayErrands cards={now.ready.cards} more={now.ready.more} key="ready" />];
  const left = later ? [...first, today, tomorrow] : [...first, today, weather];
  const right: JSX.Element[] = later
    ? [weather, <HouseBlock day={now} key="house" />, <School now={now} key="school" />, <TripBlock now={now} key="trip" />]
    : [<School now={now} key="school" />, <HouseBlock day={now} key="house" />, tomorrow, <TripBlock now={now} key="trip" />];
  right.push(<BoardLines lines={now.boards ?? []} key="boards" />);
  return <div class="cols"><div>{left}</div><div>{right}</div></div>;
}

export function Today() {
  const now = day.value;
  if (!now) {
    return (
      <div class="wrap">
        <Top />
        {dayAway.value
          ? <div class="banner"><b>Can’t reach Hub</b><span>This device is off the home network. Turn Tailscale on and open Today again.</span></div>
          : <div class="skel"><h1 class="say">Reading the day</h1><p class="sub">One moment</p></div>}
      </div>
    );
  }
  const outside = seen.value?.facts.find((fact) => fact.label === "Outside")?.value ?? "";
  const bare = !now.needs.length && !now.today.length && !now.tomorrow.length && !now.school.length && !now.trip
    && !now.weather && !now.house?.rows.length && !now.ready?.cards.length && !now.boards?.length;
  return (
    <div class={"wrap day" + (dayAway.value ? " offline" : "") + (bare ? " bare" : "")}>
      <Top />
      {dayAway.value && <div class="banner"><b>Can’t reach Hub</b><span>Showing the day as it was when Hub last answered.</span></div>}
      <h1 class="say">{daySays(now)}</h1>
      <p class="sub">{daySub(now, outside)}</p>
      <NoteLine day={now} />
      <OvernightBlock night={now.overnight} part={now.part} />
      {!bare && <Sections now={now} />}
      {now.quiet.map((line) => (
        <p class="quiet-line" key={line.text}>{line.text}{line.open && <> <a href={inApp(line.open.href)}>{line.open.label}</a></>}</p>
      ))}
    </div>
  );
}
