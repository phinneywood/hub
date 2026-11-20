// Home, the screen Hub opens to: the time, a field that asks Hub or finds anything, a big tile for each place used every
// day (the house, the music, today, the kitchen) and a tile for each dashboard with its one live line. Every tile says
// what is going on there now, so the screen is worth a glance before anything is tapped.
import type { JSX } from "preact";
import { useEffect, useState } from "preact/hooks";
import { ask, asked } from "./asking";
import { Found, onePlace } from "./ask";
import { boards } from "./boards";
import { day } from "./day";
import { kitchenSays, loadShop, shop } from "./kitchen";
import { Top } from "./screens";
import { go, play, seen, sheet, view } from "./state";
import { mood } from "./palette";
import type { House, Room } from "./types";
import { Bars, Icon } from "./ui";
import { list, playingIn } from "./words";

/** The time, kept to the minute while Home is looked at. */
function Clock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 10_000);
    const back = () => { if (!document.hidden) setNow(new Date()); };       // a phone stops the timer while the app is away
    document.addEventListener("visibilitychange", back);
    return () => { clearInterval(id); document.removeEventListener("visibilitychange", back); };
  }, []);
  const [time, half] = now.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }).split(/\s/);
  return <p class="clock">{time}{half && <small>{half}</small>}</p>;
}

/** Ask Hub, or a few letters to find a room, a scene, a playlist, a page or a dashboard: worked right here. What
 * is sent goes to Ask, which answers it. The words typed are Ask's own, so they are still there on Ask's page. With
 * nothing typed, the button opens Ask itself: the conversation, with what Hub said this morning. */
function Field({ now }: { now: House | null }) {
  const text = asked.value, typed = text.trim();
  const send = () => {
    const place = onePlace(text);
    if (place) { asked.value = ""; place(); return; }
    if (!typed) return;
    go("ask");
    void ask(typed);
  };
  return (
    <>
      <form class="startask" onSubmit={(e) => { e.preventDefault(); send(); }}>
        <Icon name="search" />
        <input id="startq" type="text" autocomplete="off" autocapitalize="sentences" enterkeyhint="send" placeholder="Ask Hub, or find anything"
          aria-label="Ask Hub, or find a room, a scene, a playlist or a page" value={text} onInput={(e) => (asked.value = e.currentTarget.value)} />
        {typed ? <button type="submit" class="send on" aria-label="Ask"><Icon name="up" /></button>
          : <button type="button" class="toask" onClick={() => go("ask")}>Ask</button>}
      </form>
      {typed && (
        <div class="startfound">
          <button type="button" class="sug askit" onClick={send}><span>Ask “{typed}”<small>Hub answers in Ask</small></span><span class="go"><Icon name="right" /></span></button>
          {now && <Found now={now} text={typed} />}
        </div>
      )}
    </>
  );
}

function inside(now: House): Room[] {
  const order = now.floors.flatMap((f) => f.rooms);
  return order.map((id) => now.rooms[id]).filter((r): r is Room => !!r && !r.outside);
}

function HouseTile({ now }: { now: House | null }) {
  const rooms = now ? inside(now) : [];
  const lit = rooms.filter((r) => r.on).length;
  return (
    <button type="button" class="tile" onClick={() => go("house")}>
      <span class="tile-k">House</span>
      <span class="tile-v">{!now ? "…" : lit ? `${lit} lit` : "All off"}</span>
      <span class="windows" aria-hidden="true">
        {rooms.slice(0, 15).map((r) => <i key={r.id} class={r.on ? "lit" : ""} style={{ "--lvl": r.on ? r.level / 100 : 0 } as JSX.CSSProperties} />)}
      </span>
    </button>
  );
}

function MusicTile({ now }: { now: House | null }) {
  const music = now?.music ?? null, show = now?.show ?? null;
  const where = music?.playing ? list(playingIn(music)) : "";
  const title = show ? "Light show" : music?.title || (music ? "Nothing playing" : "Music");
  const line = show ? show.title || "The lights follow the music" : !music ? "" : music.playing ? where || "Playing" : music.title ? "Paused" : "Choose something to play";
  const open = () => { if (show) go("ask"); else if (music) sheet.value = { kind: "playing" }; else go("house"); };
  return (
    <div class="tile music">
      <button type="button" class="tile-hit" onClick={open} aria-label={title === "Music" ? "Music" : `Music: ${title}`}>
        <span class="tile-k">Music</span>
        <span class="tile-song"><b>{title}</b><span>{music?.playing && <Bars />}{line}</span></span>
      </button>
      {music && !show && (
        <button type="button" class="tile-act" aria-label={music.playing ? "Pause" : "Play"} onClick={() => void play(!music.playing)}>
          <Icon name={music.playing ? "pause" : "play"} />
        </button>
      )}
    </div>
  );
}

function TodayTile() {
  const now = day.value;
  // counted as Hub's greeting counts it: what is listed, what is folded under "more", and the errands ready for a tap
  const waiting = now ? now.needs.length + now.more + (now.ready ? now.ready.cards.length + now.ready.more : 0) : 0;
  const next = now?.today.find((s) => s.next) ?? now?.today[0];
  const line = !now ? "" : now.needs[0]?.title ?? (next ? `${next.time} ${next.half} ${next.title}`.replace(/\s+/g, " ") : "Nothing else today");
  return (
    <button type="button" class="tile" onClick={() => go("today")}>
      <span class="tile-k">Today</span>
      <span class="tile-v">{!now ? "…" : waiting ? <>{waiting}<small> need{waiting === 1 ? "s" : ""} you</small></> : "All clear"}</span>
      <span class="tile-l">{line}</span>
    </button>
  );
}

function KitchenTile() {
  const now = shop.value;
  const says = !now ? "" : now.ok ? kitchenSays(now) : now.said;
  return (
    <button type="button" class="tile" onClick={() => go("kitchen")}>
      <span class="tile-k">Kitchen</span>
      <span class="tile-l big">{says || "The list and the cart"}</span>
    </button>
  );
}

/** A tile for each dashboard this person may open, with its line for today, in their order. */
function Dashboards() {
  const listed = boards.value ?? [];
  if (!listed.length) return null;
  return (
    <section class="dashes" aria-label="Dashboards">
      <div class="dashes-head"><h2>Dashboards</h2><button type="button" onClick={() => go("boards")}>Arrange</button></div>
      <div class="dash-grid">
        {listed.map((b) => (
          <button type="button" key={b.id} class={"dash" + (b.line?.tone ? " " + b.line.tone : "")} onClick={() => go("board", b.id)}>
            <b>{b.name}</b><span>{b.line?.text || b.what}</span>
          </button>
        ))}
      </div>
    </section>
  );
}

/** `wide`: on a wide screen Ask is open beside Home already, so Home has no field of its own. */
export function Start({ wide }: { wide: boolean }) {
  useEffect(() => {
    void loadShop();
    return () => { if (view.value !== "ask") asked.value = ""; };     // letters typed here and left behind don't follow into Ask later
  }, []);
  const now = seen.value;
  return (
    <div class="wrap start">
      <Top />
      <Clock />
      {mood.value && mood.value.palette !== "dusk" && (       // why Home looks as it does today; a tap opens The look
        <button type="button" class="why" onClick={() => go("system")}>{mood.value.name} today, {mood.value.why}</button>
      )}
      {!wide && <Field now={now} />}
      <div class="tiles">
        <HouseTile now={now} />
        <MusicTile now={now} />
        <TodayTile />
        <KitchenTile />
      </div>
      <Dashboards />
    </div>
  );
}
