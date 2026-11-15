// What rises over the house: a room, the music, and Ask.
import { useEffect, useState } from "preact/hooks";
import { dimDevice, dimRoom, dragDevice, dragRoom, dragVolume, forgetScene, keepScene, loadPicks, pauseStream, picks, play, playHere,
  playPick, setVolume, sheet, skip, speakerIn, switchDevice, switchRoom } from "./state";
import type { Button, House, Music, Pick, Room, Speaker, Stream } from "./types";
import { Sleeve } from "./screens";
import { Icon, SheetFrame, Slider, Switch } from "./ui";
import { ago, howMany, list, playingIn, spoken } from "./words";

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

/** The music a speaker plays on its own, apart from the house's main music. */
function ownMusic(speaker: Speaker, music: Music): Stream | undefined {
  return speaker.other ? (music.also ?? []).find((stream) => stream.speakers.includes(speaker.id)) : undefined;
}

/** A room playing music of its own: its volume, and a pause that stops only that music. */
function OwnRow({ speaker, stream, title, sub }: { speaker: Speaker; stream: Stream | undefined; title: string; sub: string }) {
  const where = list(stream?.rooms ?? [speaker.name]);
  return (
    <div class="ctl">
      <div class="ctl-name"><b>{title}</b><span>{sub}</span></div>
      <Slider value={speaker.volume ?? 0} label={`${speaker.name} volume`} sound onDrag={(v) => dragVolume(speaker.id, v)}
        onSet={(v) => void setVolume(speaker.id, v)} />
      <button type="button" class="mini" aria-label={`Pause the music in ${where}`} onClick={() => void pauseStream(stream?.speaker ?? speaker.id)}>
        <Icon name="pause" />
      </button>
    </div>
  );
}

/** A quiet room: start music of its own there, or bring it into what is playing. */
function QuietRow({ speaker, music, title, sub }: { speaker: Speaker; music: Music; title: string; sub: string }) {
  const joins = music.playing && playingIn(music).length > 0;
  return (
    <div class="ctl acts">
      <div class="ctl-name"><b>{title}</b><span>{sub}</span></div>
      <div class="pills">
        <button type="button" class="pill go" aria-label={`Play something in ${speaker.name}`}
          onClick={() => (sheet.value = { kind: "here", speaker: speaker.id })}><Icon name="play" />Play here</button>
        {joins && <button type="button" class="pill" aria-label={`${speaker.name} joins “${music.title || "the music"}”`}
          onClick={() => void speakerIn(speaker.id, speaker.name, true)}>Join</button>}
      </div>
    </div>
  );
}

function ButtonRow({ button }: { button: Button }) {
  const note = button.offline ? "Not answering"
    : [button.last && button.at ? `${button.last}, ${ago(button.at)}` : "Not pressed yet",
       button.battery !== null ? `battery ${button.battery}%` : ""].filter(Boolean).join(" · ");
  return (
    <div class="ctl one" key={button.id}>
      <div class="ctl-name"><b>{button.name}</b><span>{note}</span>
        {button.does.length > 0 && <ul class="presses">{button.does.map((d) => (
          <li key={d.press + d.does}>{d.press && <b>{d.press}</b>}{d.does}</li>))}</ul>}
      </div>
    </div>
  );
}

export function RoomSheet({ room, music }: { room: Room; music: Music | null }) {
  const speaker = music?.speakers.find((s) => s.id === room.speaker);
  const own = speaker && music ? ownMusic(speaker, music) : undefined;      // this room's own music
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
      {speaker && !speaker.offline && music && <h2>Music</h2>}
      {speaker && !speaker.offline && music && (
        speaker.other ? <OwnRow speaker={speaker} stream={own} title={own?.title || "Playing"} sub={`Volume ${speaker.volume ?? 0}%`} />
        : speaker.in && music.playing ? <SpeakerRow speaker={speaker} title={music.title || "Playing"} sub={`Volume ${speaker.volume ?? 0}%`} />
        : <QuietRow speaker={speaker} music={music} title="Nothing playing here"
            sub={music.playing && playingIn(music).length ? `Or join “${music.title || "the music"}”` : "Starts softly"} />
      )}
      {room.others.length > 0 && <h2>Also here</h2>}
      {room.others.map((o) => (
        <div class="ctl two" key={o.id}>
          <div class="ctl-name"><b>{o.name}</b>{o.offline && <span>Not answering</span>}</div>
          <Switch on={o.on} label={o.name} off={o.offline} onChange={(on) => void switchDevice(o.id, o.name, on)} />
        </div>
      ))}
      {(room.buttons ?? []).length > 0 && <h2>Buttons</h2>}
      {(room.buttons ?? []).map((b) => <ButtonRow button={b} key={b.id} />)}
    </SheetFrame>
  );
}

export function PlayingSheet({ music }: { music: Music }) {
  useEffect(() => void loadPicks(), []);
  const reachable = music.speakers.filter((s) => !s.offline).sort((a, b) => Number(b.leads) - Number(a.leads) || Number(b.in) - Number(a.in));
  const here = reachable.filter((s) => s.in && !s.other);
  // Every other room: those playing their own music first, then the quiet ones, each with what it can do.
  const rest = reachable.filter((s) => !(s.in && !s.other)).sort((a, b) => Number(b.other) - Number(a.other));
  const silent = music.speakers.length - reachable.length;
  const found = picks.value ?? [];
  const where = playingIn(music).map(spoken);
  return (
    <SheetFrame title={music.title || (music.playing ? "Music" : "Nothing playing")} sub={[music.artist, music.album].filter(Boolean).join(", ") || undefined}>
      <div class="transport">
        <button type="button" aria-label="Back" onClick={() => void skip("previous")}><Icon name="previous" /></button>
        <button type="button" class="big" aria-label={music.playing ? "Pause" : "Play"} onClick={() => void play(!music.playing)}>
          <Icon name={music.playing ? "pause" : "play"} />
        </button>
        <button type="button" aria-label="Next" onClick={() => void skip("next")}><Icon name="next" /></button>
      </div>
      {here.length > 0 && <h2>Where it plays</h2>}
      {here.map((s) => <SpeakerRow speaker={s} title={s.name} key={s.id} />)}
      {rest.length > 0 && <h2>{here.length ? "Other rooms" : "Rooms"}</h2>}
      {rest.map((s) => s.other
        ? <OwnRow speaker={s} stream={ownMusic(s, music)} title={s.name} key={s.id}
            sub={ownMusic(s, music)?.title ? `Playing “${ownMusic(s, music)!.title}”` : "Playing something else"} />
        : <QuietRow speaker={s} music={music} title={s.name} sub="Quiet" key={s.id} />)}
      {silent > 0 && <p class="quiet">{howMany(silent, "speaker")} can’t be reached right now.</p>}
      {found.length > 0 && <h2>{music.playing && where.length ? `Play something else in ${list(where)}` : "Play something"}</h2>}
      {found.length > 0 && <Picks found={found} onPick={(p) => void playPick(p)} />}
    </SheetFrame>
  );
}

/** Playlists as sleeves, in full rows: four or eight beside the music, all of them when choosing for a room. */
function Picks({ found, onPick, all }: { found: Pick[]; onPick: (pick: Pick) => void; all?: boolean }) {
  return (
    <div class="picks">
      {(all ? found : found.slice(0, found.length >= 8 ? 8 : found.length >= 4 ? 4 : found.length)).map((p) => (
        <button type="button" class="pick" key={p.uri} onClick={() => onPick(p)}>
          <Sleeve name={p.name} size="mid" /><b>{p.name}</b>
        </button>
      ))}
    </div>
  );
}

/** Pick something to play in one room only. What plays anywhere else plays on. */
export function HereSheet({ speaker, music }: { speaker: Speaker; music: Music }) {
  useEffect(() => void loadPicks(), []);
  const found = picks.value;
  const elsewhere = [...(music.playing ? playingIn(music) : []), ...(music.also ?? []).flatMap((s) => s.rooms)]
    .filter((name) => name !== speaker.name).map(spoken);
  const sub = `Only in ${spoken(speaker.name)}, starting softly.`
    + (elsewhere.length ? ` Music in ${list(elsewhere)} plays on.` : "");
  return (
    <SheetFrame title={`Play in ${spoken(speaker.name)}`} sub={sub}>
      {found === null && <p class="quiet">Finding playlists…</p>}
      {found !== null && found.length === 0 && <p class="quiet">Hub can’t find any playlists right now.</p>}
      {found !== null && found.length > 0 && <Picks found={found} all onPick={(p) => void playHere(speaker, p)} />}
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
