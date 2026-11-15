// What rises over the house: a room, the music, and Ask.
import { signal } from "@preact/signals";
import { useEffect, useState } from "preact/hooks";
import { dimDevice, dimRoom, dragDevice, dragRoom, dragVolume, forgetScene, keepScene, loadPicks, picks, play, playPick, runScene, seen,
  setVolume, sheet, skip, speakerIn, switchDevice, switchRoom } from "./state";
import type { House, Music, Room, Speaker } from "./types";
import { Sleeve } from "./screens";
import { Icon, SheetFrame, Slider, Switch } from "./ui";
import { howMany } from "./words";

function SpeakerRow({ speaker, title, sub }: { speaker: Speaker; title: string; sub?: string }) {
  return (
    <div class="ctl">
      <div class="ctl-name"><b>{title}</b>{sub && <span>{sub}</span>}</div>
      <Slider value={speaker.volume ?? 0} label={`${speaker.name} volume`} sound dim={!speaker.in}
        onDrag={(v) => dragVolume(speaker.id, v)} onSet={(v) => void setVolume(speaker.id, v)} />
      <Switch on={speaker.in} sound label={`${speaker.name} speaker`} onChange={(on) => void speakerIn(speaker.id, speaker.name, on)} />
    </div>
  );
}

export function RoomSheet({ room, music }: { room: Room; music: Music | null }) {
  const speaker = music?.speakers.find((s) => s.id === room.speaker);
  const says = room.lit ? `${howMany(room.lit, "light")} on.` : room.lights.length === 1 ? "The light is off." : "All lights are off.";
  return (
    <SheetFrame title={room.name} sub={says}>
      {room.dims
        ? <Slider big value={room.on ? room.level : 0} label={`${room.name} brightness`} onDrag={(v) => dragRoom(room.id, v)} onSet={(v) => void dimRoom(room.id, v)} />
        : <div class="ctl two first"><div class="ctl-name"><b>{room.lights.length === 1 ? "The light" : "All lights"}</b></div>
            <Switch on={room.on} label={`${room.name} lights`} onChange={() => void switchRoom(room.id)} /></div>}
      {(room.lights.length > 1 || room.dims) && <h2>Lights</h2>}
      {(room.lights.length > 1 || room.dims) && room.lights.map((l) => (
        <div class={l.dims ? "ctl" : "ctl two"} key={l.id}>
          <div class="ctl-name"><b>{l.name}</b>{l.offline && <span>Not answering</span>}</div>
          {l.dims && <Slider value={l.level} label={`${l.name} brightness`} dim={!l.on} onDrag={(v) => dragDevice(l.id, v)} onSet={(v) => void dimDevice(l.id, l.name, v)} />}
          <Switch on={l.on} label={l.name} off={l.offline} onChange={(on) => void switchDevice(l.id, l.name, on)} />
        </div>
      ))}
      {speaker && !speaker.offline && <h2>Speaker</h2>}
      {speaker && !speaker.offline && (
        <SpeakerRow speaker={speaker} title={speaker.in && music?.playing ? music.title || "Playing" : "Not playing"}
          sub={speaker.in ? `Volume ${speaker.volume ?? 0}%` : music?.playing ? "Switch on to play what’s on" : undefined} />
      )}
      {room.others.length > 0 && <h2>Also here</h2>}
      {room.others.map((o) => (
        <div class="ctl two" key={o.id}>
          <div class="ctl-name"><b>{o.name}</b>{o.offline && <span>Not answering</span>}</div>
          <Switch on={o.on} label={o.name} off={o.offline} onChange={(on) => void switchDevice(o.id, o.name, on)} />
        </div>
      ))}
    </SheetFrame>
  );
}

export function PlayingSheet({ music }: { music: Music }) {
  useEffect(() => void loadPicks(), []);
  const reachable = music.speakers.filter((s) => !s.offline).sort((a, b) => Number(b.leads) - Number(a.leads) || Number(b.in) - Number(a.in));
  const silent = music.speakers.length - reachable.length;
  const found = picks.value ?? [];
  return (
    <SheetFrame title={music.title || (music.playing ? "Music" : "Nothing playing")} sub={[music.artist, music.album].filter(Boolean).join(", ") || undefined}>
      <div class="transport">
        <button type="button" aria-label="Back" onClick={() => void skip("previous")}><Icon name="previous" /></button>
        <button type="button" class="big" aria-label={music.playing ? "Pause" : "Play"} onClick={() => void play(!music.playing)}>
          <Icon name={music.playing ? "pause" : "play"} />
        </button>
        <button type="button" aria-label="Next" onClick={() => void skip("next")}><Icon name="next" /></button>
      </div>
      <h2>Where it plays</h2>
      {reachable.map((s) => <SpeakerRow speaker={s} title={s.name} key={s.id} />)}
      {silent > 0 && <p class="quiet">{howMany(silent, "speaker")} can’t be reached right now.</p>}
      {found.length > 0 && <h2>Play something else</h2>}
      {found.length > 0 && (
        <div class="picks">
          {found.slice(0, found.length >= 8 ? 8 : found.length >= 4 ? 4 : found.length).map((p) => (
            <button type="button" class="pick" key={p.uri} onClick={() => void playPick(p)}>
              <Sleeve name={p.name} size="mid" /><b>{p.name}</b>
            </button>
          ))}
        </div>
      )}
    </SheetFrame>
  );
}

/** Keep the house as it stands as a scene: pick the rooms, name it. */
export function KeepSheet({ now }: { now: House }) {
  const rooms = now.floors.flatMap((f) => f.rooms).map((id) => now.rooms[id]).filter((r): r is Room => r !== undefined);
  const [name, setName] = useState("");
  const [picked, setPicked] = useState(() => new Set(rooms.filter((r) => r.on && !r.outside).map((r) => r.id)));
  const [restOff, setRestOff] = useState(false);
  const [busy, setBusy] = useState(false);
  const flip = (id: string) => {
    const next = new Set(picked);
    if (!next.delete(id)) next.add(id);
    setPicked(next);
  };
  const keep = async (e: Event) => {
    e.preventDefault();
    setBusy(true);
    if (await keepScene(name, [...picked], restOff)) sheet.value = null;
    else setBusy(false);
  };
  const mine = now.scenes.filter((s) => s.mine);
  return (
    <SheetFrame title="Keep this as a scene" sub="Hub remembers these rooms’ lights as they are now. One tap brings them back.">
      <form onSubmit={keep}>
        <input class="field named" placeholder="Its name, such as Bedtime" aria-label="The scene’s name" maxLength={30} value={name}
          onInput={(e) => setName(e.currentTarget.value)} />
        <h2>Rooms</h2>
        <div class="rows">
          {rooms.map((r) => (
            <button type="button" role="checkbox" aria-checked={picked.has(r.id)} class={"row tick" + (picked.has(r.id) ? " on" : "")} key={r.id}
              onClick={() => flip(r.id)}>
              <span class="ring"><Icon name="check" /></span>
              <span class="row-main"><b>{r.name}</b></span>
              <span class="row-q">{r.on ? (r.dims ? r.level + "%" : "On") : "Off"}</span>
            </button>
          ))}
        </div>
        <div class="ctl two">
          <div class="ctl-name"><b>Turn every other light off</b><span>Indoors, when the scene runs</span></div>
          <Switch on={restOff} label="Turn every other light off" onChange={setRestOff} />
        </div>
        <div class="btns"><button type="submit" class="btn go" disabled={busy || !name.trim() || picked.size === 0}>Keep it</button></div>
      </form>
      {mine.length > 0 && <h2>Kept scenes</h2>}
      {mine.map((s) => (
        <div class="ctl two" key={s.id}>
          <div class="ctl-name"><b>{s.name}</b></div>
          <button type="button" class="forget" onClick={() => void forgetScene(s)}>Forget</button>
        </div>
      ))}
    </SheetFrame>
  );
}

// ---- Ask: for now, a list you type into. Names switch things at once; anything else goes to the chat. ----

const asked = signal("");
const SAY = "hub.say";      // the chat page reads this once and sends it (web/ask): only Hub's own pages can set it

/** Hand what was typed to Hub's chat, which still answers everything this list can't. */
function toChat(text: string) {
  try {
    if (text) sessionStorage.setItem(SAY, text.slice(0, 2000));
  } catch {
    /* the chat just opens empty */
  }
  location.href = "/ask";
}

function marked(name: string, text: string) {
  const at = name.toLowerCase().indexOf(text.toLowerCase());
  if (at < 0 || !text) return <>{name}</>;
  return <>{name.slice(0, at)}<mark>{name.slice(at, at + text.length)}</mark>{name.slice(at + text.length)}</>;
}

function Found({ now, text }: { now: House; text: string }) {
  const has = (name: string) => name.toLowerCase().includes(text.toLowerCase());
  const rooms = text ? Object.values(now.rooms).filter((r) => has(r.name)) : [];
  const scenes = now.scenes.filter((s) => !text || has(s.name));
  const lists = text ? (picks.value ?? []).filter((p) => has(p.name)).slice(0, 4) : [];
  return (
    <>
      {!text && <p class="ask-hint">Scenes run at once. A few letters find a room or a playlist. Anything else goes to the chat.</p>}
      {rooms.map((r) => (
        <div class="sug" key={r.id}>
          <span>{marked(r.name, text)}<small>{r.on ? (r.dims ? `Lights on at ${r.level}%` : "Lights on") : "Lights off"}</small></span>
          <Switch on={r.on} label={`${r.name} lights`} onChange={() => void switchRoom(r.id)} />
        </div>
      ))}
      {scenes.map((s) => (
        <button type="button" class="sug" key={s.id} onClick={() => { void runScene(s); if (sheet.value?.kind === "ask") sheet.value = null; }}>
          <span>{marked(s.name, text)}<small>Scene</small></span><span class="go"><Icon name="bolt" /></span>
        </button>
      ))}
      {lists.map((p) => (
        <button type="button" class="sug" key={p.uri} onClick={() => { void playPick(p); if (sheet.value?.kind === "ask") sheet.value = null; }}>
          <span>{marked(p.name, text)}<small>Playlist. Plays where music is on now.</small></span><span class="go"><Icon name="play" /></span>
        </button>
      ))}
      {text
        ? <button type="button" class="sug" onClick={() => toChat(text)}><span>Ask Hub “{text}”<small>Opens the chat and asks</small></span><span class="go"><Icon name="up" /></span></button>
        : <a class="sug" href="/ask"><span>Ask in the chat<small>For anything this list can’t do yet</small></span><span class="how">Open</span></a>}
    </>
  );
}

export function Ask({ focus }: { focus?: boolean }) {
  useEffect(() => void loadPicks(), []);
  const now = seen.value;
  return (
    <div class="ask">
      <div class="ask-scroll"><div>{now && <Found now={now} text={asked.value.trim()} />}</div></div>
      <form class="composer" onSubmit={(e) => { e.preventDefault(); if (asked.value.trim()) toChat(asked.value.trim()); }}>
        <input class="field" id="askq" autocomplete="off" autocapitalize="sentences" enterkeyhint="send" placeholder="Say it, or type a room"
          aria-label="Ask Hub, or type a room, a scene or a playlist" value={asked.value} autofocus={focus}
          onInput={(e) => (asked.value = e.currentTarget.value)} />
      </form>
    </div>
  );
}

export function AskSheet() {
  return <SheetFrame tall><Ask focus /></SheetFrame>;
}
