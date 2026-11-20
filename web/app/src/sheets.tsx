// What rises over the house: a room, the music, and Ask.
import { useEffect, useState } from "preact/hooks";
import { dimDevice, dimRoom, dragDevice, dragGroupVolume, dragRoom, dragVolume, forgetScene, go, joinStream, keepScene, loadPicks, loadShowSongs, pauseStream,
  picks, play, playHere, playOn, playPick, setGroupVolume, setVolume, sheet, skip, speakerIn, startShow, streamPlay, streamSkip, switchDevice,
  showSongs, switchRoom, takeOut } from "./state";
import type { Button, House, Music, Pick, Room, Speaker, Stream } from "./types";
import { Sleeve } from "./screens";
import { Bars, Icon, SheetFrame, Slider, Switch } from "./ui";
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
        <button type="button" class="pill go snd" aria-label={`Play something in ${speaker.name}`}
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

/** One music playing in the house, as the sheet shows it: the main one, or one some rooms play apart. */
interface Tune {
  leader: string; main: boolean; title: string; artist: string; album: string; playing: boolean;
  volume: number | null; rooms: Speaker[];
}

/** Every music in the house, the main one first and the others in the order Hub found them, which a pause doesn't change. */
function tunes(music: Music): Tune[] {
  const by = new Map(music.speakers.map((s) => [s.id, s]));
  const main: Tune = { leader: music.speaker, main: true, title: music.title, artist: music.artist, album: music.album,
    playing: music.playing, volume: music.volume, rooms: music.speakers.filter((s) => s.in && !s.other && !s.offline) };
  const also = (music.also ?? []).map((a): Tune => {
    const rooms = a.speakers.map((id) => by.get(id)).filter((s): s is Speaker => s !== undefined && !s.offline);
    const levels = rooms.map((s) => s.volume).filter((v): v is number => v !== null);
    return { leader: a.speaker, main: false, title: a.title, artist: a.artist, album: a.album ?? "", playing: a.playing ?? true,
      volume: a.volume ?? (levels.length ? Math.round(levels.reduce((x, y) => x + y, 0) / levels.length) : null), rooms };
  });
  return [...(main.rooms.length || main.title ? [main] : []), ...also];
}

const named = (tune: Tune) => tune.title ? `“${tune.title}”` : "the music";

/** One music: what it is and where, play or pause and next, one volume for all its rooms, and its rooms opened up. */
function TuneCard({ tune, all, open, quiet, bare }: { tune: Tune; all: Tune[]; open: boolean; quiet: Speaker[]; bare?: boolean }) {
  const [shown, setShown] = useState(open);
  const [adding, setAdding] = useState(false);
  const many = tune.rooms.length > 1;
  const solo = tune.rooms[0];
  // Rooms that can come in: quiet ones, and rooms of another music that don't lead it while others follow.
  const others = all.filter((t) => t !== tune).flatMap((t) => t.rooms.filter((s) => !(s.id === t.leader && t.rooms.length > 1)).map((s) => ({ s, from: t })));
  const can = [...quiet.map((s) => ({ s, from: undefined as Tune | undefined })), ...others];
  return (
    <section class={(bare ? "tune bare" : "tune") + (tune.playing ? "" : " paused")} aria-label={tune.title || "Music"}>
      {!bare && <div class="tune-head">
        <Sleeve name={tune.album || tune.title || tune.leader} />
        <div class="tune-text">
          <b>{tune.title || (tune.playing ? "Music" : "Nothing playing")}</b>
          {tune.artist && <span>{tune.artist}</span>}
          <span class="tune-where">{tune.playing ? <Bars /> : null}{list(tune.rooms.map((s) => s.name)) || "No room"}{tune.playing ? "" : " · paused"}</span>
        </div>
        {all.length > 1 && <>
          <button type="button" class="mini" aria-label={`${tune.playing ? "Pause" : "Play"} ${named(tune)}`}
            onClick={() => void streamPlay(tune.leader, !tune.playing)}><Icon name={tune.playing ? "pause" : "play"} /></button>
          <button type="button" class="mini bare" aria-label={`Next, in ${list(tune.rooms.map((s) => spoken(s.name)))}`}
            onClick={() => void streamSkip(tune.leader, "next")}><Icon name="next" /></button>
        </>}
      </div>}
      {solo && (many
        ? <div class="ctl vol"><div class="ctl-name"><b>All rooms</b><span>{tune.volume ?? 0}%</span></div>
            <Slider value={tune.volume ?? 0} sound label={`Volume of ${named(tune)}, every room`}
              onDrag={(v) => dragGroupVolume(tune.leader, v)} onSet={(v) => void setGroupVolume(tune.leader, v)} /></div>
        : <div class="ctl vol"><div class="ctl-name"><b>{solo.name}</b><span>{solo.volume ?? 0}%</span></div>
            <Slider value={solo.volume ?? 0} sound label={`${solo.name} volume`}
              onDrag={(v) => dragVolume(solo.id, v)} onSet={(v) => void setVolume(solo.id, v)} /></div>)}
      {shown && many && tune.rooms.map((s) => (
        <div class="ctl" key={s.id}>
          <div class="ctl-name"><b>{s.name}</b><span>{s.volume ?? 0}%</span></div>
          <Slider value={s.volume ?? 0} sound label={`${s.name} volume`} onDrag={(v) => dragVolume(s.id, v)} onSet={(v) => void setVolume(s.id, v)} />
          {s.id !== tune.leader
            ? <button type="button" class="mini" aria-label={`Take ${s.name} out`} title="Take out" onClick={() => void takeOut(tune.leader, s.id, s.name)}><Icon name="close" /></button>
            : <span />}
        </div>
      ))}
      {shown && adding && can.map(({ s, from }) => (
        <div class="ctl acts" key={s.id}>
          <div class="ctl-name"><b>{s.name}</b><span>{from ? `Leaves ${named(from)}` : "Quiet"}</span></div>
          <button type="button" class="pill" aria-label={`${s.name} plays ${named(tune)}`}
            onClick={() => { setAdding(false); void joinStream(tune.leader, s.id, s.name, tune.title); }}><Icon name="plus" />Add</button>
        </div>
      ))}
      <div class="tune-acts">
        {(many || can.length > 0) && <button type="button" class="pill" aria-expanded={shown}
          onClick={() => { setShown(!shown); setAdding(!many && !shown); }}>{shown ? "Done" : many ? `Rooms · ${tune.rooms.length}` : <><Icon name="plus" />Add a room</>}</button>}
        {shown && can.length > 0 && !adding && <button type="button" class="pill" onClick={() => setAdding(true)}><Icon name="plus" />Add a room</button>}
        {all.length > 1 && <button type="button" class="pill" onClick={() => (sheet.value = { kind: "change", speaker: tune.leader })}>Change music</button>}
      </div>
    </section>
  );
}

/** A quiet room: music of its own, or join one that plays. With two or more playing, Join asks which. */
function Quiet({ speaker, all }: { speaker: Speaker; all: Tune[] }) {
  const [choosing, setChoosing] = useState(false);
  const playing = all.filter((t) => t.playing && t.rooms.length > 0);
  const only = playing.length === 1 ? playing[0] : undefined;
  if (choosing) {
    return (
      <div class="ctl acts choose">
        <div class="ctl-name"><b>{speaker.name}</b><span>Join which?</span></div>
        <div class="pills wrap">
          {playing.map((t) => <button type="button" class="pill" key={t.leader}
            onClick={() => { setChoosing(false); void joinStream(t.leader, speaker.id, speaker.name, t.title); }}>{t.title || list(t.rooms.map((s) => s.name))}</button>)}
          <button type="button" class="pill bare" onClick={() => setChoosing(false)}>Cancel</button>
        </div>
      </div>
    );
  }
  return (
    <div class="ctl acts">
      <div class="ctl-name"><b>{speaker.name}</b><span>Quiet</span></div>
      <div class="pills">
        <button type="button" class="pill go snd" aria-label={`Play something in ${speaker.name}`}
          onClick={() => (sheet.value = { kind: "here", speaker: speaker.id })}><Icon name="play" />Play here</button>
        {playing.length > 0 && <button type="button" class="pill" aria-label={only ? `${speaker.name} joins ${named(only)}` : `${speaker.name} joins some music`}
          onClick={() => only ? void joinStream(only.leader, speaker.id, speaker.name, only.title) : setChoosing(true)}>Join</button>}
      </div>
    </div>
  );
}

const COUNT = ["", "One thing is", "Two things are", "Three things are", "Four things are"];

/** Everything playing in the house, one card for each music, and the quiet rooms under them. */
export function PlayingSheet({ music }: { music: Music }) {
  useEffect(() => void loadPicks(), []);
  const all = tunes(music);
  const reachable = music.speakers.filter((s) => !s.offline);
  const busy = new Set(all.flatMap((t) => t.rooms.map((s) => s.id)));
  const quiet = reachable.filter((s) => !busy.has(s.id));
  const silent = music.speakers.length - reachable.length;
  const found = picks.value ?? [];
  const one = all.length <= 1;
  const main = all[0];
  const playing = all.filter((t) => t.playing).length;
  const title = one ? music.title || (music.playing ? "Music" : "Nothing playing") : "Music in the house";
  const sub = one ? [music.artist, music.album].filter(Boolean).join(", ") || undefined
    : playing ? `${COUNT[playing] ?? `${playing} things are`} playing, in ${howMany(busy.size, "room").toLowerCase()}.` : "All paused.";
  return (
    <SheetFrame title={title} sub={sub}>
      {one && (
        <div class="transport">
          <button type="button" aria-label="Back" onClick={() => void skip("previous")}><Icon name="previous" /></button>
          <button type="button" class="big" aria-label={music.playing ? "Pause" : "Play"} onClick={() => void play(!music.playing)}>
            <Icon name={music.playing ? "pause" : "play"} />
          </button>
          <button type="button" aria-label="Next" onClick={() => void skip("next")}><Icon name="next" /></button>
        </div>
      )}
      {one && main && main.rooms.length > 0 && <h2>Where it plays</h2>}
      {one ? main && main.rooms.length > 0 && <TuneCard tune={main} all={all} open quiet={quiet} bare key={main.leader} />
        : all.map((t) => <TuneCard tune={t} all={all} open={false} quiet={quiet} key={t.leader} />)}
      {quiet.length > 0 && <h2>{busy.size ? "Quiet rooms" : "Rooms"}</h2>}
      {quiet.map((s) => <Quiet speaker={s} all={all} key={s.id} />)}
      {silent > 0 && <p class="quiet">{howMany(silent, "speaker")} can’t be reached right now.</p>}
      {one && found.length > 0 && <h2>{music.playing && main?.rooms.length ? `Play something else in ${list(main.rooms.map((s) => spoken(s.name)))}` : "Play something"}</h2>}
      {one && found.length > 0 && <Picks found={found} onPick={(p) => void playPick(p)} />}
    </SheetFrame>
  );
}

/** Pick something to play in place of one music, in every room it plays in. */
export function ChangeSheet({ leader, music }: { leader: string; music: Music }) {
  useEffect(() => void loadPicks(), []);
  const found = picks.value;
  const tune = tunes(music).find((t) => t.leader === leader);
  const where = list((tune?.rooms ?? []).map((s) => spoken(s.name)));
  return (
    <SheetFrame title={tune?.title ? `In place of “${tune.title}”` : "Play something"} sub={where ? `In ${where}. Music anywhere else plays on.` : undefined}>
      {found === null && <p class="quiet">Finding playlists…</p>}
      {found !== null && found.length === 0 && <p class="quiet">Hub can’t find any playlists right now.</p>}
      {found !== null && found.length > 0 && <Picks found={found} all onPick={(p) => void playOn(leader, p)} />}
    </SheetFrame>
  );
}

/** Playlists as sleeves, in full rows: four or eight beside the music, all of them when choosing for a room.
 * `busy`: the one starting now; the others wait for it. */
function Picks({ found, onPick, all, busy }: { found: Pick[]; onPick: (pick: Pick) => void; all?: boolean; busy?: string }) {
  return (
    <div class="picks">
      {(all ? found : found.slice(0, found.length >= 8 ? 8 : found.length >= 4 ? 4 : found.length)).map((p) => (
        <button type="button" class={"pick" + (busy === p.uri ? " busy" : "")} key={p.uri} disabled={!!busy} onClick={() => onPick(p)}>
          <Sleeve name={p.name} size="mid" /><b>{p.name}</b>
        </button>
      ))}
    </div>
  );
}

/** The light show, one tap away: the songs that have a hand-made show, or all of them shuffled. When it is on, its
 * card at the top of Ask has the music, the timing and End show, so the sheet goes there. */
export function ShowSheet() {
  useEffect(() => void loadShowSongs(), []);
  const songs = showSongs.value;
  const [busy, setBusy] = useState<Pick | null>(null);
  const found: Pick[] | null = songs && [
    ...(songs.length > 1 ? [{ name: "All of them, shuffled", kind: "all", uri: "all" }] : []),
    ...songs.map((s) => ({ name: s.title, kind: "track", uri: s.id })),
  ];
  const start = async (pick: Pick) => {
    setBusy(pick);
    const on = await startShow(pick.kind === "all" ? { all: true } : { song: pick.uri });
    setBusy(null);
    if (on) { sheet.value = null; go("ask"); }
  };
  return (
    <SheetFrame title="Light show" sub="Songs with a hand-made show. The lights follow the music, and go back as they were when it ends.">
      {found === null && <p class="quiet">Finding the songs…</p>}
      {found !== null && found.length === 0 && <p class="quiet">No song has a light show yet.</p>}
      {busy && <p class="quiet show-starting">Starting the light show{busy.kind === "all" ? "" : ` with ${busy.name}`}…</p>}
      {found !== null && found.length > 0 && <Picks found={found} all busy={busy?.uri} onPick={(p) => void start(p)} />}
    </SheetFrame>
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
