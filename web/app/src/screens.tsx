// The screens: the house as a plan, the bar at the bottom, and System.
import type { JSX } from "preact";
import { useRef, useState } from "preact/hooks";
import { boardId, go, house, look, me, play, runScene, running, seen, sheet, switchRoom, trouble, view, why } from "./state";
import type { Look, View } from "./state";
import { able, forgetNote } from "./asking";
import { boards } from "./boards";
import type { Floor, House, Room } from "./types";
import { Bars, Icon } from "./ui";
import { clock, houseSays, list, musicSays, playingIn, when } from "./words";

const HOLD_MS = 430;        // how long a finger rests on a room before it opens
const TIP = "hub.tip";

/** How wide each room is drawn, in sixths of the plan, so every row is full. Rooms come biggest first, and
 * the first ones take the wide places. */
export function spans(count: number): number[] {
  if (count === 1) return [6];
  const out = Array<number>(count).fill(2);
  const over = count % 3;
  if (over === 2) out.splice(0, 2, 3, 3);
  if (over === 1) out.splice(0, 4, 3, 3, 3, 3);
  return out;
}

export function RoomCell({ room, span, order }: { room: Room; span: number; order: number }) {
  const timer = useRef(0);
  const opened = useRef(false);
  const [pressed, setPressed] = useState(false);
  const open = () => {
    try { localStorage.setItem(TIP, "1"); } catch { /* the tip just shows again */ }
    sheet.value = { kind: "room", id: room.id };
  };
  const down = (e: PointerEvent) => {
    if ((e.target as Element).closest(".room-more")) return;
    opened.current = false;
    setPressed(true);
    timer.current = window.setTimeout(() => { opened.current = true; setPressed(false); open(); }, HOLD_MS);
  };
  const up = () => { clearTimeout(timer.current); setPressed(false); };
  const tap = () => {
    if (opened.current) { opened.current = false; return; }
    void switchRoom(room.id);
  };
  const key = (e: KeyboardEvent) => {
    if (e.target !== e.currentTarget) return;
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); tap(); }
  };
  const style = { "--lvl": room.level / 100, "--i": order } as JSX.CSSProperties;
  const cls = ["room", "s" + span, room.on ? "lit" : "", room.on && room.level < 35 ? "low" : "", pressed ? "held" : ""].filter(Boolean).join(" ");
  return (
    <div class={cls} style={style} role="switch" aria-checked={room.on} aria-label={`${room.name} lights`} tabIndex={0}
      data-room={room.id} onClick={tap} onKeyDown={key} onPointerDown={down} onPointerUp={up} onPointerCancel={up} onPointerLeave={up}
      onContextMenu={(e) => { e.preventDefault(); up(); opened.current = true; open(); }}>
      <div class="room-name">{room.name}</div>
      <div class="room-val">
        {room.on ? (room.dims ? <span class="room-num">{room.level}<small>%</small></span> : <span class="room-on">On</span>) : <span />}
        {room.sounding && <Bars />}
      </div>
      <button type="button" class="room-more" aria-label={`${room.name}: everything in the room`}
        onClick={(e) => { e.stopPropagation(); open(); }}><Icon name="more" /></button>
    </div>
  );
}

function Plan({ now }: { now: House }) {
  const lit = Object.values(now.rooms).filter((r) => r.on).map((r) => r.id);
  const floor = (f: Floor) => {
    const rooms = f.rooms.map((id) => now.rooms[id]).filter((r): r is Room => r !== undefined);
    const widths = spans(rooms.length);
    return (
      <>
        {f.name && <p class="floor">{f.name}</p>}
        <div class="plan">{rooms.map((r, n) => <RoomCell key={r.id} room={r} span={widths[n] ?? 2} order={Math.max(0, lit.indexOf(r.id))} />)}</div>
      </>
    );
  };
  return <div class="floors">{now.floors.map(floor)}</div>;
}

export function Top() {
  return (
    <div class="top">
      <span class="when">{when()}</span>
      <button type="button" class={"who" + (trouble.value ? " bad" : "")} aria-label="System" onClick={() => go("system")}>{me.value.initial}</button>
    </div>
  );
}

function Trouble() {
  const last = house.value ? ` Showing things as they were at ${clock(house.value.at)}.` : "";
  if (trouble.value === "hub") {
    return <div class="banner"><b>Can’t reach Hub</b><span>This device is off the home network. Turn Tailscale on and Hub will reconnect by itself.{last}</span></div>;
  }
  if (trouble.value === "house") {
    // Said in the app's own words; why.value (Hub's wording, which names its parts) is for System.
    return <div class="banner"><b>The house isn’t answering</b><span>Hub can’t reach the lights and speakers right now.{last}</span></div>;
  }
  return null;
}

function Skeleton() {
  return (
    <div class="skel">
      <h1 class="say">Reading the house</h1>
      <p class="sub">One moment</p>
      <div class="floors">
        <div class="plan">{[3, 3, 2, 2, 2].map((s) => <div class={"room s" + s} />)}</div>
        <div class="plan">{[2, 2, 2].map((s) => <div class={"room s" + s} />)}</div>
      </div>
    </div>
  );
}

export function Home() {
  const now = seen.value;
  let tipped = true;
  try { tipped = localStorage.getItem(TIP) === "1"; } catch { /* show it */ }
  if (!now) return <div class="wrap"><Top /><Trouble />{!trouble.value && <Skeleton />}</div>;
  return (
    <div class={"wrap" + (trouble.value ? " offline" : "")}>
      <Top />
      <Trouble />
      <h1 class="say">{houseSays(now)}</h1>
      <p class="sub">{musicSays(now.music)}</p>
      <div class="scenes">
        {now.scenes.map((s) => (
          <button type="button" class={"scene" + (s.on ? " on" : "") + (running.value === s.id ? " busy" : "")} key={s.id}
            aria-pressed={s.on} onClick={() => void runScene(s)}>{s.name}</button>
        ))}
        <button type="button" class="scene add" aria-label="Keep the house as it is now as a scene" onClick={() => (sheet.value = { kind: "keep" })}>
          <Icon name="plus" />
        </button>
      </div>
      <Plan now={now} />
      {now.facts.length > 0 && (
        <div class="facts">
          {now.facts.map((f) => <div class={"fact" + (f.tone ? " " + f.tone : "")} key={f.label}><span>{f.label}</span><b>{f.value}</b></div>)}
        </div>
      )}
      {!tipped && <p class="tip">Tap a room to switch its lights. Hold it for everything in the room.</p>}
    </div>
  );
}

/** A record sleeve drawn from the name, until real covers arrive. */
export function Sleeve({ name, size }: { name: string; size?: string }) {
  let sum = 0;
  for (const letter of name) sum = (sum + letter.charCodeAt(0)) % 4;
  return <div class={`sleeve ${"abcd"[sum]}${size ? " " + size : ""}`} />;
}

function Playing() {
  const music = seen.value?.music;
  if (!music) return null;
  const where = playingIn(music);
  const elsewhere = (music.also ?? []).flatMap((stream) => stream.rooms);    // rooms playing music of their own
  const quiet = !music.playing;
  const title = music.title || (quiet ? "Nothing playing" : "Music");
  return (
    <div class={"np" + (music.playing ? " live" : "")} role="button" tabIndex={0} aria-label="What is playing"
      onClick={() => (sheet.value = { kind: "playing" })}
      onKeyDown={(e) => { if (e.target === e.currentTarget && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); sheet.value = { kind: "playing" }; } }}>
      <Sleeve name={music.album || music.title} />
      <div class="np-text">
        <b>{title}</b>
        <span>{music.playing ? <><Bars />{(list(where) || "Playing") + (elsewhere.length ? ` · also ${list(elsewhere)}` : "")}</>
          : music.title ? "Paused" : "Choose something to play"}</span>
      </div>
      <button type="button" class="np-play" aria-label={music.playing ? "Pause" : "Play"}
        onClick={(e) => { e.stopPropagation(); void play(!music.playing); }}><Icon name={music.playing ? "pause" : "play"} /></button>
    </div>
  );
}

// Two places: Ask, where Hub opens, and the house. A page Ask opened (Today, Mail, Kitchen, System) shows as a
// third word while it is open, so the bar always says where you are and the way back is one tap.
const TABS = [["home", "Ask"], ["house", "House"]] as const;
const PAGES: Partial<Record<View, string>> = { today: "Today", mail: "Mail", mailsettings: "Mail", kitchen: "Kitchen", system: "System",
  errands: "Today", versions: "System", engine: "System", changes: "System", activity: "System", boards: "Dashboards", board: "Dashboards" };

function Tabs({ page }: { page?: boolean }) {
  const open = PAGES[view.value];
  return (
    <>
      {TABS.map(([where, name]) => (
        <button type="button" key={where} class={"tab" + (view.value === where ? " on" : "")} onClick={() => go(where)}>{name}</button>
      ))}
      {page && open && <span class="tab on page" aria-current="page">{open}</span>}
    </>
  );
}

export function Dock() {
  return (
    <div class="dock">
      <Playing />
      <div class="tabs"><Tabs page /></div>
    </div>
  );
}

/** The dashboards this person may open, under Ask and House on a wide screen: one click each. */
function RailBoards() {
  const list = boards.value ?? [];
  const here = view.value === "board" ? boardId.value : "";
  return (
    <div class="railboards">
      <button type="button" class={"rlink head" + (view.value === "boards" ? " on" : "")} onClick={() => go("boards")}>Dashboards</button>
      {list.map((b) => (
        <button type="button" key={b.id} class={"rlink" + (here === b.id ? " on" : "")} aria-current={here === b.id ? "page" : undefined}
          onClick={() => go("board", b.id)}>{b.name}</button>
      ))}
    </div>
  );
}

export function Rail() {
  const fine = !trouble.value;
  return (
    <nav class="rail" aria-label="Hub">
      <div class="mark"><i />Hub</div>
      <Tabs page={PAGES[view.value] !== "System" && PAGES[view.value] !== "Dashboards"} />
      <RailBoards />
      <div class="foot">
        <button type="button" class={"tab" + (view.value === "system" || view.value === "versions" || view.value === "engine" || view.value === "changes" || view.value === "activity" ? " on" : "")} onClick={() => go("system")}>System</button>
        <p class="ok">{fine ? "The house is answering." : trouble.value === "hub" ? "Can’t reach Hub." : "The house isn’t answering."}</p>
      </div>
    </nav>
  );
}

const MAILBOX = ["/gmail", "/calendar"];     // one person's Google account: listed only for them
const OLDER = [["Ask, the chat", "/ask"], ["Today, the cards", "/today"], ["Kitchen, the older page", "/kitchen"], ["Mail, the older page", "/gmail"], ["Calendar", "/calendar"],
  ["Preschool", "/preschool/"], ["Trip", "/trip"], ["Bike + bus", "/mobility"], ["Network", "/network"], ["Insights", "/insights"],
  ["Settings and checks", "/settings"]] as const;
const LOOKS: [Look, string][] = [["auto", "Automatic"], ["dusk", "Evening"], ["day", "Day"]];

export function System() {
  const now = house.value;
  const links = me.value.links;
  const says = trouble.value === "hub" ? "Can’t reach Hub." : trouble.value === "house" ? "The house isn’t answering." : "The house is answering.";
  return (
    <div class="wrap">
      <Top />
      <Trouble />
      <h1 class="say">{says}</h1>
      <p class="sub">{now ? `Last read at ${clock(now.at)}. ${now.lights_on === 1 ? "One light is" : now.lights_on + " lights are"} on.` : "Nothing read yet."}
        {trouble.value === "house" && why.value ? " " + why.value : ""}</p>
      <section class="block">
        <h2>Under the hood</h2>
        <div class="rows">
          {me.value.update && <button type="button" class="row free lit" onClick={() => go("versions")}><div class="row-main"><b>A new version is ready</b><span>Open it to deploy</span></div></button>}
          <button type="button" class="row free" onClick={() => go("boards")}><div class="row-main"><b>Dashboards</b><span>A page for each part of the household, arranged your way</span></div></button>
          <button type="button" class="row free" onClick={() => go("activity")}><div class="row-main"><b>Activity</b><span>What happened in the house, from Home Assistant, Apple Home and Hub</span></div></button>
          <a class="row free" href="#wall"><div class="row-main"><b>The wall</b><span>Today for the kitchen iPad, with a clock that dims at night. Open it there and add it to the Home Screen</span></div></a>
          {links.ha && <a class="row free" href={links.ha}><div class="row-main"><b>Home Assistant</b><span>Devices, rooms and automations</span></div></a>}
          {links.music && <a class="row free" href={links.music}><div class="row-main"><b>Music Assistant</b><span>The library, the queue and playlists</span></div></a>}
          <button type="button" class="row free" onClick={() => go("versions")}><div class="row-main"><b>Versions</b><span>What each release of Hub changed</span></div></button>
          <button type="button" class="row free" onClick={() => go("engine")}><div class="row-main"><b>Engine</b><span>What you asked for, what Hub may do, what it costs</span></div></button>
        </div>
      </section>
      {(able.value?.remembers.length ?? 0) > 0 && (
        <section class="block">
          <h2>Hub remembers</h2>
          <div class="rows">
            {able.value!.remembers.map((note) => (
              <div class="ctl two" key={note.id}>
                <div class="ctl-name"><b>{note.note}</b></div>
                <button type="button" class="forget" onClick={() => void forgetNote(note.id)}>Forget</button>
              </div>
            ))}
          </div>
        </section>
      )}
      <section class="block">
        <h2>The look</h2>
        <div class="choices" role="radiogroup" aria-label="The look">
          {LOOKS.map(([value, name]) => (
            <button type="button" role="radio" aria-checked={look.value === value} class={"choice" + (look.value === value ? " on" : "")}
              key={value} onClick={() => (look.value = value)}>{name}</button>
          ))}
        </div>
      </section>
      <section class="block">
        <h2>Older pages</h2>
        <p class="sub">Hub’s earlier screens, until each moves in here or goes.</p>
        <div class="rows spaced">
          {OLDER.filter(([, href]) => (me.value.mail !== false || !MAILBOX.includes(href)) && !(href === "/ask" && able.value?.ready))
            .map(([name, href]) =>
            <a class="row free" href={href} key={href}><div class="row-main"><b>{name}</b></div></a>)}
        </div>
      </section>
      {!me.value.signed_in && <p class="quiet">This screen is open on the Mac itself, which can look but not change the house.</p>}
      <p class="quiet">App version {me.value.versions.js || "unknown"}.</p>
    </div>
  );
}
