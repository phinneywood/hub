// What rises over the house: a room, the music, and Ask.
import { useEffect, useState } from "preact/hooks";
import { dimDevice, dimRoom, dragDevice, dragRoom, dragVolume, forgetScene, keepScene, loadPicks, pauseStream, picks, play, playPick,
  setVolume, sheet, skip, speakerIn, switchDevice, switchRoom } from "./state";
import type { House, Music, Room, Speaker } from "./types";
import { Sleeve } from "./screens";
import { Icon, SheetFrame, Slider, Switch } from "./ui";
import { howMany, list } from "./words";

export function SpeakerRow({ speaker, title, sub }: { speaker: Speaker; title: string; sub?: string }) {
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
  const own = (music?.also ?? []).find((stream) => speaker && stream.speakers.includes(speaker.id));   // this room's own music
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
        <SpeakerRow speaker={speaker}
          title={speaker.other ? own?.title || "Playing" : speaker.in && music?.playing ? music.title || "Playing" : "Not playing"}
          sub={speaker.in || speaker.other ? `Volume ${speaker.volume ?? 0}%` : music?.playing ? "Switch on to play what’s on" : undefined} />
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
      {reachable.map((s) => <SpeakerRow speaker={s} title={s.name} sub={s.other ? "Playing something else" : undefined} key={s.id} />)}
      {(music.also ?? []).length > 0 && <h2>Also playing</h2>}
      {(music.also ?? []).map((stream) => (
        <div class="ctl two" key={stream.speaker}>
          <div class="ctl-name"><b>{stream.title || "Music"}</b><span>{list(stream.rooms)}</span></div>
          <button type="button" class="mini" aria-label={`Pause the music in ${list(stream.rooms)}`} onClick={() => void pauseStream(stream.speaker)}>
            <Icon name="pause" />
          </button>
        </div>
      ))}
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
