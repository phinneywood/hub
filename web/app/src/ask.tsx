// Ask, as the app shows it: a field, what a few letters reach at once, and the conversation.
import { useEffect, useRef, useState } from "preact/hooks";
import { able, ask, asked, forgetNote, loadAble, talk } from "./asking";
import type { Card, Turn } from "./asking";
import { FoundCard, PhotoCard } from "./archive";
import type { FoundLine } from "./archive";
import { RoomCell, spans } from "./screens";
import { dragVolume, loadPicks, picks, playPick, runScene, seen, setVolume, sheet, switchRoom, undo, undone } from "./state";
import type { ArchiveItem, House, Room } from "./types";
import { Icon, SheetFrame, Slider, Switch } from "./ui";
import { spoken } from "./words";

const LONG = 5 * 60 * 1000;     // after this Kitchen's answer is left to the Kitchen tab

function marked(name: string, text: string) {
  const at = name.toLowerCase().indexOf(text.toLowerCase());
  if (at < 0 || !text) return <>{name}</>;
  return <>{name.slice(0, at)}<mark>{name.slice(at, at + text.length)}</mark>{name.slice(at + text.length)}</>;
}

/** What a few letters reach without asking anything: rooms, scenes and playlists, worked right in the list. */
function Found({ now, text, brief }: { now: House; text: string; brief: boolean }) {
  const has = (name: string) => name.toLowerCase().includes(text.toLowerCase());
  const rooms = text ? Object.values(now.rooms).filter((r) => has(r.name)).slice(0, 4) : [];
  const scenes = now.scenes.filter((s) => (text ? has(s.name) : !brief));
  const lists = text ? (picks.value ?? []).filter((p) => has(p.name)).slice(0, 3) : [];
  const set = able.value?.ready !== false;
  const away = () => { if (sheet.value?.kind === "ask") sheet.value = null; };
  if (brief && !rooms.length && !scenes.length && !lists.length) return null;
  // Two things to say, in this house's own rooms.
  const all = Object.values(now.rooms).filter((r) => !r.outside);
  const lamp = all.find((r) => r.dims), sound = all.find((r) => r.speaker && r !== lamp) ?? all.find((r) => r.speaker);
  const such = [lamp && `“dim ${spoken(lamp.name)}”`, sound && `“play something calm in ${spoken(sound.name)}”`].filter(Boolean).join(" or ");
  return (
    <div class={brief ? "found brief" : "found"}>
      {!text && <p class="ask-hint">{set ? `Say what you’d like${such ? `, such as ${such}` : ""}. A few letters find a room, a scene or a playlist.`
        : "Scenes run at once. A few letters find a room or a playlist. Anything else goes to the chat."}</p>}
      {!text && !brief && (able.value?.usual ?? []).map((words) => (
        <button type="button" class="sug" key={words} onClick={() => void ask(words)}>
          <span>{words}<small>You often ask this around now</small></span><span class="go"><Icon name="up" /></span>
        </button>
      ))}
      {rooms.map((r) => (
        <div class="sug" key={r.id}>
          <span>{marked(r.name, text)}<small>{r.on ? (r.dims ? `Lights on at ${r.level}%` : "Lights on") : "Lights off"}</small></span>
          <Switch on={r.on} label={`${r.name} lights`} onChange={() => void switchRoom(r.id)} />
        </div>
      ))}
      {scenes.map((s) => (
        <button type="button" class="sug" key={s.id} onClick={() => { void runScene(s); asked.value = ""; away(); }}>
          <span>{marked(s.name, text)}<small>Scene</small></span><span class="go"><Icon name="bolt" /></span>
        </button>
      ))}
      {lists.map((p) => (
        <button type="button" class="sug" key={p.uri} onClick={() => { void playPick(p); asked.value = ""; away(); }}>
          <span>{marked(p.name, text)}<small>Playlist. Plays where music is on now.</small></span><span class="go"><Icon name="play" /></span>
        </button>
      ))}
      {!text && !set && <a class="sug" href="/ask"><span>Ask in the chat<small>For anything this list can’t do yet</small></span><span class="how">Open</span></a>}
    </div>
  );
}

interface KitchenSays { id: number; role: string; text?: string; recipes?: { title?: string; total?: string }[]; plan?: unknown;
  result?: unknown; order?: unknown }

/** Kitchen's answer to what was passed on. It can take a minute or two, so this keeps looking and says so. */
function KitchenReply({ after }: { after: number }) {
  const [replies, setReplies] = useState<KitchenSays[]>([]);
  const [state, setState] = useState<"working" | "done" | "long" | "down">("working");
  useEffect(() => {
    let timer = 0, stopped = false, misses = 0;
    const began = Date.now();
    const again = (ms: number) => { timer = window.setTimeout(() => void look(), document.hidden ? 15000 : ms); };
    const look = async () => {
      try {
        const answer = await fetch(`/kitchen/messages?after=${after}`, { headers: { accept: "application/json" } });
        if (!answer.ok) throw new Error("Kitchen is away");
        const found = (await answer.json()) as { messages?: KitchenSays[]; busy?: boolean; queued?: number };
        if (stopped) return;
        const theirs: KitchenSays[] = [];
        for (const m of found.messages ?? []) {
          if (m.role === "member") break;          // the next thing someone asked: its answer isn't this one's
          theirs.push(m);
        }
        misses = 0;
        setReplies(theirs);
        const waiting = Boolean(found.busy || found.queued) || !theirs.some((m) => m.role === "assistant");
        if (!waiting) setState("done");
        else if (Date.now() - began > LONG) setState("long");
        else { setState("working"); again(3000); }
      } catch {
        if (stopped) return;
        if (++misses >= 3) setState("down");
        else again(4000);
      }
    };
    void look();
    return () => { stopped = true; clearTimeout(timer); };
  }, [after]);
  const more = replies.some((m) => m.plan || m.result || m.order);
  return (
    <div class="kitchen-says">
      {replies.map((m) => (
        <div key={m.id}>
          {m.text && <p class={m.role === "notice" ? "took" : "says"}>{m.result ? m.text.split(/\n{2,}/).slice(1).join("\n\n") : m.text}</p>}
          {(m.recipes?.length ?? 0) > 0 && (
            <ul class="recipes">{m.recipes!.map((r, n) => <li key={n}><b>{r.title}</b>{r.total && <small>{r.total}</small>}</li>)}</ul>
          )}
        </div>
      ))}
      {state === "working" && <p class="took"><span class="work" />Kitchen is working on it. This can take a minute or two.</p>}
      {state === "long" && <p class="took">Kitchen is still working. Its answer will be in Kitchen.</p>}
      {state === "down" && <p class="took">Kitchen isn’t answering right now.</p>}
      {(more || state === "long" || replies.some((m) => m.recipes?.length)) && (
        <a class="row-act" href="#kitchen">{more ? "Open Kitchen to see the cart" : "Open Kitchen"}</a>
      )}
    </div>
  );
}

function CardView({ card, now }: { card: Card; now: House }) {
  if (card.kind === "rooms") {
    const rooms = (card.rooms ?? []).map((id) => now.rooms[id]).filter((r): r is Room => r !== undefined).slice(0, 6);
    const widths = rooms.length === 1 ? [6] : rooms.length === 2 ? [3, 3] : spans(rooms.length);
    return rooms.length ? <div class="plan">{rooms.map((r, n) => <RoomCell key={r.id} room={r} span={widths[n] ?? 2} order={0} />)}</div> : null;
  }
  if (card.kind === "music" && now.music) {       // the speakers this touched, each with its volume
    const touched = now.music.speakers.filter((s) => card.speakers?.includes(s.id) && !s.offline);
    return touched.length ? (
      <div class="rows">
        {touched.map((s) => (
          <div class="ctl vol" key={s.id}>
            <div class="ctl-name"><b>{s.name}</b></div>
            <Slider value={s.volume ?? 0} label={`${s.name} volume`} sound onDrag={(v) => dragVolume(s.id, v)} onSet={(v) => void setVolume(s.id, v)} />
          </div>
        ))}
      </div>
    ) : null;
  }
  if (card.kind === "kitchen" && card.after !== undefined) return <KitchenReply after={card.after} />;
  if (card.kind === "photos") return <PhotoCard title={card.title} items={(card.items ?? []) as ArchiveItem[]} search={card.search} />;
  if (card.kind === "found") return <FoundCard items={(card.items ?? []) as FoundLine[]} count={card.count} search={card.search} />;
  if (card.kind === "memory" && card.id !== undefined) {
    const id = card.id;
    const kept = !able.value || able.value.remembers.some((note) => note.id === id);
    return kept ? (
      <div class="ctl two">
        <div class="ctl-name"><b>{card.note}</b><span>Hub will keep this in mind. It’s listed under System.</span></div>
        <button type="button" class="forget" onClick={() => void forgetNote(id)}>Forget</button>
      </div>
    ) : <p class="took">Forgotten.</p>;
  }
  return null;
}

function TurnView({ turn, now }: { turn: Turn; now: House }) {
  // A change to the house says how long it took and can be put back. An answer needs neither.
  const batch = turn.cards.find((card) => card.batch)?.batch;
  const back = batch !== undefined && undone.value.includes(batch);
  const took = !turn.ms ? "" : turn.ms < 950 ? "Done in under a second" : `Done in ${(turn.ms / 1000).toFixed(1)} seconds`;
  return (
    <div class={"turn" + (back ? " back" : "")}>
      <p class="asked">{turn.asked}</p>
      {turn.answer ? <p class={"answer" + (turn.answer.length > 160 ? " long" : "")}>{turn.answer}</p>
        : <span class="dots" role="status" aria-label="Hub is working"><i /><i /><i /></span>}
      {turn.busy && turn.doing && <p class="took">{turn.doing}</p>}
      {turn.cards.map((card, n) => <CardView card={card} now={now} key={n} />)}
      {!turn.busy && batch && (back
        ? <p class="took"><span>Put back as it was</span></p>
        : <p class="took">{took && <span>{took}</span>}<button type="button" onClick={() => void undo(batch)}>Undo</button></p>)}
    </div>
  );
}

export function Ask({ focus }: { focus?: boolean }) {
  const scroll = useRef<HTMLDivElement>(null);
  useEffect(() => { void loadPicks(); void loadAble(); }, []);
  const now = seen.value;
  const text = asked.value.trim();
  const turns = talk.value;
  const busy = turns.some((turn) => turn.busy);
  useEffect(() => {           // a new request is always in view
    const box = scroll.current;
    if (box) box.scrollTop = box.scrollHeight;
  }, [turns.length]);
  useEffect(() => {           // and what arrives under it stays in view, unless a finger has scrolled back to read
    const box = scroll.current, said = box?.firstElementChild;
    if (!box || !said) return undefined;
    let pinned = true;
    const moved = () => { pinned = box.scrollHeight - box.scrollTop - box.clientHeight < 80; };
    const grew = new ResizeObserver(() => { if (pinned) box.scrollTop = box.scrollHeight; });
    box.addEventListener("scroll", moved, { passive: true });
    grew.observe(said);
    return () => { box.removeEventListener("scroll", moved); grew.disconnect(); };
  }, []);
  return (
    <div class="ask">
      <div class="ask-scroll" ref={scroll}>
        <div class="ask-said" aria-live="polite">
          {now && turns.map((turn) => <TurnView turn={turn} now={now} key={turn.n} />)}
          {now && <Found now={now} text={text} brief={turns.length > 0} />}
        </div>
      </div>
      <form class="composer" onSubmit={(e) => { e.preventDefault(); if (text) void ask(text); }}>
        <input class="field" id="askq" autocomplete="off" autocapitalize="sentences" enterkeyhint="send" placeholder="Say it, or type a room"
          aria-label="Ask Hub, or type a room, a scene or a playlist" value={asked.value} autofocus={focus}
          onInput={(e) => (asked.value = e.currentTarget.value)} />
        <button type="submit" class={"send" + (text && !busy ? " on" : "")} aria-label="Ask Hub" disabled={!text || busy}><Icon name="up" /></button>
      </form>
    </div>
  );
}

export function AskSheet() {
  return <SheetFrame tall><Ask focus /></SheetFrame>;
}
