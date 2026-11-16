// Ask, as the app shows it: a field, what a few letters reach at once, and the conversation.
import { useEffect, useRef, useState } from "preact/hooks";
import { post } from "./api";
import { able, ask, asked, forgetNote, loadAble, talk } from "./asking";
import type { Card, Turn } from "./asking";
import { GroceriesCard, Said } from "./kitchen";
import { RoomCell, spans } from "./screens";
import { dragVolume, go, loadPicks, picks, playPick, runScene, say, seen, setVolume, sheet, switchRoom, undo, undone } from "./state";
import type { House, KitchenMessage, Room } from "./types";
import { Icon, SheetFrame, Slider, Switch } from "./ui";
import { spoken } from "./words";

const LONG = 5 * 60 * 1000;     // after this Kitchen's answer is left to the Kitchen screen

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
  // One for the lights, one for the music, one for the list.
  const examples = [lamp && `“dim ${spoken(lamp.name)}”`, sound && `“play something calm in ${spoken(sound.name)}”`, "“add eggs to the list”"]
    .filter((e): e is string => Boolean(e));
  const such = examples.length > 1 ? examples.slice(0, -1).join(", ") + " or " + examples[examples.length - 1] : examples.join("");
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

type Statuses = { plans: Record<string, { status: string }>; orders: Record<string, { status: string }> };

/** The kitchen assistant's answer to what was passed on, drawn here in full: its words, recipes, a cart plan or
 * an order with their buttons. It can take a minute or two, so this keeps looking and says so. */
function KitchenReply({ after }: { after: number }) {
  const [replies, setReplies] = useState<KitchenMessage[]>([]);
  const [statuses, setStatuses] = useState<Statuses>({ plans: {}, orders: {} });
  const [state, setState] = useState<"working" | "done" | "long" | "down">("working");
  useEffect(() => {
    let timer = 0, stopped = false, misses = 0;
    const began = Date.now();
    const again = (ms: number) => { timer = window.setTimeout(() => void look(), document.hidden ? 15000 : ms); };
    const look = async () => {
      try {
        const answer = await fetch(`/kitchen/messages?after=${after}`, { headers: { accept: "application/json" } });
        if (!answer.ok) throw new Error("Kitchen is away");
        const found = (await answer.json()) as { messages?: KitchenMessage[]; busy?: boolean; queued?: number } & Partial<Statuses>;
        if (stopped) return;
        const theirs: KitchenMessage[] = [];
        for (const m of found.messages ?? []) {
          if (m.role === "member") break;          // the next thing someone asked: its answer isn't this one's
          theirs.push(m);
        }
        misses = 0;
        setReplies(theirs);
        setStatuses({ plans: found.plans ?? {}, orders: found.orders ?? {} });
        const open = theirs.some((m) => (m.plan && (found.plans?.[String(m.plan.id)]?.status ?? m.plan.status) === "pending")
          || (m.order && ["pending", "placing"].includes(found.orders?.[String(m.order.id)]?.status ?? m.order.status)));
        const waiting = Boolean(found.busy || found.queued) || !theirs.some((m) => m.role === "assistant");
        if (!waiting && !open) setState("done");
        else if (waiting && Date.now() - began > LONG) setState("long");
        else { setState(waiting ? "working" : "done"); again(open && !waiting ? 5000 : 3000); }     // a card that waits is kept current
      } catch {
        if (stopped) return;
        if (++misses >= 3) setState("down");
        else again(4000);
      }
    };
    void look();
    return () => { stopped = true; clearTimeout(timer); };
  }, [after]);
  return (
    <div class="kitchen-says">
      <ol class="kthread">{replies.map((m) => <Said m={m} statuses={statuses} key={m.id} />)}</ol>
      {state === "working" && <p class="took"><span class="work" />Kitchen is working on it. This can take a minute or two.</p>}
      {state === "long" && <p class="took">Kitchen is still working. Its answer will be in Kitchen, under Earlier answers.</p>}
      {state === "down" && <p class="took">Kitchen isn’t answering right now.</p>}
      {state === "long" && <button type="button" class="row-act" onClick={() => go("kitchen")}>Open Kitchen</button>}
    </div>
  );
}

/** A change that waits for a tap: what would happen, and the two ways to answer. Typing "yes" does nothing;
 * only this button, on a signed-in device, makes the change. */
function ConfirmCard({ card }: { card: Card }) {
  const id = card.id ?? 0;
  const [state, setState] = useState<"waiting" | "working" | "done" | "dismissed" | "gone">("waiting");
  const [batch, setBatch] = useState<string | null>(null);
  const [said, setSaid] = useState("");
  useEffect(() => {           // opened again later: what became of the card
    let stopped = false;
    fetch(`/app/api/setup/${id}`, { headers: { accept: "application/json" } })
      .then((answer) => answer.json() as Promise<{ status: string; batch: string | null }>)
      .then((found) => {
        if (stopped || found.status === "waiting") return;
        if (found.status === "done") setBatch(found.batch);
        setState(found.status === "done" ? "done" : found.status === "dismissed" ? "dismissed" : "gone");
      }, () => undefined);
    return () => { stopped = true; };
  }, [id]);
  const tap = async (choice: "confirm" | "dismiss") => {
    setState("working");
    const answer = await post(`setup/${id}/${choice}`);
    if (answer.error) {         // not done. A card that still waits (Hub was busy this minute) stays to be tapped again
      const still = await fetch(`/app/api/setup/${id}`, { headers: { accept: "application/json" } })
        .then((found) => found.json() as Promise<{ status?: string }>).catch(() => ({ status: "" }));
      if (still.status === "waiting") { say(answer.error); setState("waiting"); return; }
      setSaid(answer.error); setState("gone"); return;
    }
    if (choice === "confirm") setBatch(answer.batch ?? null);
    setState(choice === "confirm" ? "done" : "dismissed");
  };
  const open = state === "waiting" || state === "working";
  const back = batch !== null && undone.value.includes(batch);
  return (
    <div class={"wait" + (open ? "" : " settled")}>
      <b class="wait-t">{card.title}</b>
      <p class="wait-cap">{open ? "After your tap" : state === "done" ? "Done" : "Not done"}</p>
      <ul class="wait-lines">{(card.lines ?? []).map((line) => <li key={line}>{line}</li>)}</ul>
      {open && (
        <>
          <p class="wait-why">{card.why}</p>
          <div class="kacts">
            <button type="button" class={"btn " + (card.do === "Remove" || card.do === "Disable" ? "risk" : "go")} disabled={state === "working"}
              onClick={() => void tap("confirm")}>{card.do ?? "Do it"}</button>
            <button type="button" class="btn ghost" disabled={state === "working"} onClick={() => void tap("dismiss")}>Not now</button>
          </div>
        </>
      )}
      {state === "done" && (back ? <p class="took"><span>Put back as it was</span></p>
        : batch ? <p class="took"><button type="button" onClick={() => void undo(batch)}>Undo</button></p> : null)}
      {state === "dismissed" && <p class="took"><span>Left as it was.</span></p>}
      {state === "gone" && <p class="took"><span>{said || "This card isn’t waiting any more. Ask again."}</span></p>}
    </div>
  );
}

function CardView({ card, now }: { card: Card; now: House }) {
  if (card.kind === "confirm" && card.id !== undefined) return <ConfirmCard card={card} />;
  if (card.kind === "setup") {      // several changes from one request: each on its own line, under one Undo
    const lines = card.lines ?? [], missed = card.missed ?? [];
    return lines.length + missed.length ? (
      <ul class="did">
        {lines.map((line) => <li key={line}>{line}</li>)}
        {missed.map((line) => <li class="missed" key={line}>{line}<small>Didn’t take</small></li>)}
      </ul>
    ) : null;
  }
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
  if (card.kind === "groceries") return <GroceriesCard ids={card.items} all={card.all} cart={card.cart} since={card.since} />;
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
    // Only a finger scrolling back unpins it. What arrives late (a card under an answer) makes the box taller
    // without moving it, and must not be mistaken for that.
    let pinned = true, last = box.scrollTop;
    const moved = () => {
      const gap = box.scrollHeight - box.scrollTop - box.clientHeight;
      if (box.scrollTop < last - 2) pinned = gap < 80;
      else if (gap < 80) pinned = true;
      last = box.scrollTop;
    };
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

/** A card that came from elsewhere than Ask (an Undo that waits for a tap), as a sheet of its own. */
export function CardSheet({ card }: { card: Card }) {
  return <SheetFrame title="Waiting for your tap"><ConfirmCard card={card} /></SheetFrame>;
}

export function AskSheet() {
  return <SheetFrame tall><Ask focus /></SheetFrame>;
}
