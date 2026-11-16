// Today: a sentence, what needs you, the errands ready for a tap, then the day, tomorrow, the week at school and a
// trip when one is near.
import { day, dayAway, tickNeed, ticked } from "./day";
import { TodayErrands } from "./errands";
import { Top } from "./screens";
import { seen } from "./state";
import type { Day, Need, Slot } from "./types";
import { Icon } from "./ui";

// Mail, Kitchen and Engine are screens of the app now: their older addresses open here instead.
const HERE: Record<string, string> = {
  "/gmail": "#mail", "/kitchen": "#kitchen", "/engine": "#engine", "/engine#improvements": "#engine",
  "/engine/notes": "#engine", "/engine/recipes": "#engine", "/app/#versions": "#versions",
  "/house/changes": "#changes", "/house/playlists": "#changes", "/gmail/plan": "#mailsettings", "/gmail/delayed": "#mailsettings", "/gmail/backlog": "#mailsettings",
};
const inApp = (href: string) => HERE[href] ?? href;
import { daySays, daySub, partOfDay } from "./words";

function NeedRow({ need }: { need: Need }) {
  const done = ticked.value.includes(need.key);
  return (
    <div class={"row tick need" + (done ? " on" : "")}>
      <button type="button" class="tickbtn" aria-label={`${need.tick}: ${need.title}`} disabled={done} onClick={() => void tickNeed(need)}>
        <span class="ring"><Icon name="check" /></span>
      </button>
      <div class="row-main"><b>{need.title}</b>{need.why && <span>{need.why}</span>}</div>
      {!done && <a class="row-act" href={inApp(need.open.href)}>{need.open.label}</a>}
    </div>
  );
}

function SlotRow({ slot }: { slot: Slot }) {
  const words = slot.time === "All day" || slot.time === "Now" || slot.time === "Still";
  return (
    <div class={"row" + (slot.warn ? " warn" : "") + (slot.next ? " now" : "")}>
      <span class={"row-t" + (words ? " day" : "")}>{slot.time}{slot.half && <small>{slot.half}</small>}</span>
      <div class="row-main"><b>{slot.title}</b>{slot.note && <span>{slot.note}</span>}</div>
    </div>
  );
}

function Sections({ now }: { now: Day }) {
  return (
    <div class="cols">
      <div>
        {now.needs.length > 0 && (
          <section class="block">
            <h2>Needs you</h2>
            <div class="rows">{now.needs.map((need) => <NeedRow need={need} key={need.key} />)}</div>
            {now.more > 0 && <p class="quiet-line">{now.more === 1 ? "One more thing can wait." : `${now.more} more things can wait.`}</p>}
          </section>
        )}
        {now.ready && <TodayErrands cards={now.ready.cards} more={now.ready.more} />}
        {now.today.length > 0 && (
          <section class="block">
            <h2>{partOfDay(now.at)}</h2>
            <div class="rows">{now.today.map((slot, n) => <SlotRow slot={slot} key={n} />)}</div>
          </section>
        )}
      </div>
      <div>
        {now.tomorrow.length > 0 && (
          <section class="block">
            <h2>Tomorrow</h2>
            <div class="rows">{now.tomorrow.map((slot, n) => <SlotRow slot={slot} key={n} />)}</div>
          </section>
        )}
        {now.school.length > 0 && (
          <section class="block">
            <h2>This week at school</h2>
            <div class="rows">
              {now.school.map((s, n) => (
                <div class={"row" + (s.struck ? " struck" : "")} key={n}>
                  <span class="row-t day">{s.day}</span>
                  <div class="row-main"><b>{s.title}</b>{s.note && <span>{s.note}</span>}</div>
                </div>
              ))}
            </div>
          </section>
        )}
        {now.trip && (
          <section class="block">
            <h2>{now.trip.title}</h2>
            <div class="rows">
              <div class="row free">
                <div class="row-main"><b>{now.trip.when}</b>{now.trip.note && <span>{now.trip.note}</span>}</div>
                <a class="row-act" href={now.trip.open.href}>{now.trip.open.label}</a>
              </div>
            </div>
          </section>
        )}
      </div>
    </div>
  );
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
  const bare = !now.needs.length && !now.today.length && !now.tomorrow.length && !now.school.length && !now.trip && !now.ready?.cards.length;
  return (
    <div class={"wrap day" + (dayAway.value ? " offline" : "") + (bare ? " bare" : "")}>
      <Top />
      {dayAway.value && <div class="banner"><b>Can’t reach Hub</b><span>Showing the day as it was when Hub last answered.</span></div>}
      <h1 class="say">{daySays(now)}</h1>
      <p class="sub">{daySub(now, outside)}</p>
      {!bare && <Sections now={now} />}
      {now.quiet.map((line) => (
        <p class="quiet-line" key={line.text}>{line.text} <a href={inApp(line.open.href)}>{line.open.label}</a></p>
      ))}
    </div>
  );
}
