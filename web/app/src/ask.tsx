// Ask, the screen Hub opens to: what needs you and the house right now, the conversation, and the field.
import { useEffect, useLayoutEffect, useRef, useState } from "preact/hooks";
import { post } from "./api";
import { able, ask, asked, forgetNote, loadAble, stop, talk } from "./asking";
import type { Card, Turn } from "./asking";
import { ErrandsCard } from "./errands";
import { GroceriesCard, Said } from "./kitchen";
import { DayRows, Headline, HouseCard, NeedsCard } from "./now";
import { RoomCell, spans, Top } from "./screens";
import { dragVolume, go, loadPicks, picks, playPick, runScene, say, seen, setVolume, switchRoom, undo, undone } from "./state";
import type { View } from "./state";
import type { House, KitchenMessage, Room } from "./types";
import { Icon, SheetFrame, Slider, Switch } from "./ui";
import { spoken } from "./words";

const LONG = 5 * 60 * 1000;     // after this Kitchen's answer is left to the Kitchen screen

function marked(name: string, text: string) {
  const at = name.toLowerCase().indexOf(text.toLowerCase());
  if (at < 0 || !text) return <>{name}</>;
  return <>{name.slice(0, at)}<mark>{name.slice(at, at + text.length)}</mark>{name.slice(at + text.length)}</>;
}

// Hub's pages, found by a few letters like a room: Ask opens them, so none of them needs a place in the bar.
const PAGES: [View, string, string][] = [
  ["today", "Today", "The day, what needs you, tomorrow and school"], ["mail", "Mail", "What needs you and what Hub sorted"],
  ["kitchen", "Kitchen", "The shopping list on its way into the cart"], ["house", "House", "Every room, its lights and music"],
  ["system", "System", "How Hub is doing, the look and older pages"], ["activity", "Activity", "What happened in the house"],
  ["changes", "Changes", "Everything Hub changed, with Undo"], ["versions", "Versions", "Each version of Hub"],
  ["engine", "Engine", "What Hub learned and what was asked of it"], ["mailsettings", "Mail settings", "How Hub sorts mail"],
];

/** What a few letters reach without asking anything: rooms, scenes, playlists and pages, worked right in the list.
 * With nothing typed and no conversation going on, a hint and what this person often asks around now. */
function Found({ now, text, quiet }: { now: House; text: string; quiet: boolean }) {
  const has = (name: string) => name.toLowerCase().includes(text.toLowerCase());
  const set = able.value?.ready !== false;
  if (!text) {
    if (!quiet && set) return null;       // a conversation is going on; where Ask isn't set up, always say where words go
    // Three things to say, in this house's own rooms: one for the lights, one for the day, one for the list.
    const all = Object.values(now.rooms).filter((r) => !r.outside);
    const lamp = all.find((r) => r.dims);
    const examples = [lamp && `“dim ${spoken(lamp.name)}”`, "“what needs me today?”", "“add eggs to the list”"].filter((e): e is string => Boolean(e));
    const such = examples.slice(0, -1).join(", ") + " or " + examples[examples.length - 1];
    return (
      <div class="found">
        <p class="ask-hint">{set ? `Ask for anything, such as ${such}. A few letters find a room, a scene, a playlist or a page.`
          : "Scenes run at once. A few letters find a room, a playlist or a page. Anything else goes to the chat."}</p>
        {(able.value?.usual ?? []).map((words) => (
          <button type="button" class="sug" key={words} onClick={() => void ask(words)}>
            <span>{words}<small>You often ask this around now</small></span><span class="go"><Icon name="up" /></span>
          </button>
        ))}
        {!set && <a class="sug" href="/ask"><span>Ask in the chat<small>For anything this list can’t do yet</small></span><span class="how">Open</span></a>}
      </div>
    );
  }
  const rooms = Object.values(now.rooms).filter((r) => has(r.name)).slice(0, 4);
  const scenes = now.scenes.filter((s) => has(s.name));
  const lists = (picks.value ?? []).filter((p) => has(p.name)).slice(0, 3);
  const pages = text.length > 1 ? PAGES.filter(([, name]) => name.toLowerCase().startsWith(text.toLowerCase())).slice(0, 2) : [];
  if (!rooms.length && !scenes.length && !lists.length && !pages.length) return null;
  return (
    <div class="found brief">
      {pages.map(([where, name, what]) => (
        <button type="button" class="sug" key={where} onClick={() => { asked.value = ""; go(where); }}>
          <span>{marked(name, text)}<small>{what}</small></span><span class="go"><Icon name="right" /></span>
        </button>
      ))}
      {rooms.map((r) => (
        <div class="sug" key={r.id}>
          <span>{marked(r.name, text)}<small>{r.on ? (r.dims ? `Lights on at ${r.level}%` : "Lights on") : "Lights off"}</small></span>
          <Switch on={r.on} label={`${r.name} lights`} onChange={() => void switchRoom(r.id)} />
        </div>
      ))}
      {scenes.map((s) => (
        <button type="button" class="sug" key={s.id} onClick={() => { void runScene(s); asked.value = ""; }}>
          <span>{marked(s.name, text)}<small>Scene</small></span><span class="go"><Icon name="bolt" /></span>
        </button>
      ))}
      {lists.map((p) => (
        <button type="button" class="sug" key={p.uri} onClick={() => { void playPick(p); asked.value = ""; }}>
          <span>{marked(p.name, text)}<small>Playlist. Plays where music is on now.</small></span><span class="go"><Icon name="play" /></span>
        </button>
      ))}
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
  if (card.kind === "day") return <DayRows show={card.show ?? []} />;
  if (card.kind === "groceries") return <GroceriesCard ids={card.items} all={card.all} cart={card.cart} since={card.since} />;
  if (card.kind === "errands" && card.ids) return <ErrandsCard ids={card.ids} />;
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

const FRESH = 30 * 60 * 1000;      // a conversation older than this is folded away; Ask opens on now
const SHOWN_FOLDED = 3;       // earlier requests shown as a line each; the rest gather under one

function TurnView({ turn, now, open, onOpen }: { turn: Turn; now: House; open: boolean; onOpen: () => void }) {
  // A change to the house says how long it took and can be put back. An answer needs neither.
  const batch = turn.cards.find((card) => card.batch)?.batch;
  const back = batch !== undefined && undone.value.includes(batch);
  if (!open) {         // an earlier turn: what was asked, on one line, still with its Undo
    return (
      <div class={"turn folded" + (back ? " back" : "")}>
        <button type="button" class="fold" aria-expanded="false" aria-label={`${turn.asked}. Hub: ${turn.answer}`} onClick={onOpen}>
          <span class="fold-asked">{turn.asked}</span>
        </button>
        {batch && (back ? <span class="fold-back">Undone</span>
          : <button type="button" class="fold-undo" onClick={() => void undo(batch)}>Undo</button>)}
      </div>
    );
  }
  const took = !turn.ms ? "" : turn.ms < 950 ? "Done in under a second" : `Done in ${(turn.ms / 1000).toFixed(1)} seconds`;
  return (
    <div class={"turn" + (back ? " back" : "")}>
      <p class="asked">{turn.asked}</p>
      {turn.waiting ? <p class="took">Waiting for the last request to finish</p>
        : turn.answer ? <p class={"answer" + (turn.answer.length > 160 ? " long" : "")}>{turn.answer}</p>
        : <span class="dots" role="status" aria-label="Hub is working"><i /><i /><i /></span>}
      {turn.busy && turn.doing && <p class="took">{turn.doing}</p>}
      {turn.cards.map((card, n) => <CardView card={card} now={now} key={n} />)}
      {!turn.busy && batch && (back
        ? <p class="took"><span>Put back as it was</span></p>
        : <p class="took">{took && <span>{took}</span>}<button type="button" onClick={() => void undo(batch)}>Undo</button></p>)}
    </div>
  );
}

/** The field: a few lines at most, Return sends, and while Hub works on something with nothing typed, Stop. */
function Composer() {
  const field = useRef<HTMLTextAreaElement>(null);
  const text = asked.value;
  const busy = talk.value.some((turn) => turn.busy);
  useLayoutEffect(() => {           // grow with what is typed, up to a few lines
    const box = field.current;
    if (!box) return;
    box.style.height = "auto";
    box.style.height = `${Math.min(box.scrollHeight, 140)}px`;
  }, [text]);
  const send = () => { if (text.trim()) void ask(text); };
  return (
    <form class="composer" onSubmit={(e) => { e.preventDefault(); send(); }}>
      <textarea class="field" id="askq" ref={field} rows={1} autocomplete="off" autocapitalize="sentences" enterkeyhint="send"
        placeholder="Ask Hub, or type a room" aria-label="Ask Hub, or type a room, a scene, a playlist or a page" value={text}
        onInput={(e) => (asked.value = e.currentTarget.value)}
        onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); } }} />
      {busy && !text.trim()
        ? <button type="button" class="send stop" aria-label="Stop" onClick={stop}><Icon name="stop" /></button>
        : <button type="submit" class={"send" + (text.trim() ? " on" : "")} aria-label="Ask Hub" disabled={!text.trim()}><Icon name="up" /></button>}
    </form>
  );
}

/** Ask. `home`: the screen Hub opens to, with now on top (and the house too when it isn't beside it).
 * Without `home`: the conversation alone, beside another screen on a wide one. */
export function Ask({ home, house }: { home?: boolean; house?: boolean }) {
  const scroll = useRef<HTMLDivElement>(null);
  const [opened, setOpened] = useState<number[]>([]);
  const [all, setAll] = useState(false);
  useEffect(() => { void loadPicks(); void loadAble(); }, []);
  const now = seen.value;
  const text = asked.value.trim();
  const turns = talk.value;
  const last = turns[turns.length - 1];
  const live = last !== undefined && (last.busy || last.waiting || Date.now() - last.at < FRESH);
  const open = (turn: Turn) => turn.busy || turn.waiting || opened.includes(turn.n) || (live && turn === last)
    || (live && turns.slice(turns.indexOf(turn) + 1).every((later) => later.waiting || later.busy));
  const pinned = useRef(!home);
  const placed = useRef(false);
  const asking = turns.some((turn) => turn.busy || turn.waiting);
  useEffect(() => {           // what is asked, what a few letters find, and a conversation still going are brought into view
    const box = scroll.current;
    if (!box) return;
    const going = !placed.current && turns.length > 0 && live;      // opened again in the middle of one
    if (turns.length) placed.current = true;
    if (asking || text || going) { pinned.current = true; box.scrollTop = box.scrollHeight; }
  }, [turns.length, asking, text.length > 0]);
  useEffect(() => {           // and what arrives under it stays in view, unless a finger has scrolled back to read
    const box = scroll.current, said = box?.firstElementChild;
    if (!box || !said) return undefined;
    // Only a finger scrolling back unpins it. What arrives late (a card under an answer) makes the box taller
    // without moving it, and must not be mistaken for that. The keyboard coming up makes the box shorter.
    let last = box.scrollTop;
    const moved = () => {
      const gap = box.scrollHeight - box.scrollTop - box.clientHeight;
      if (box.scrollTop < last - 2) pinned.current = gap < 80;
      else if (gap < 80) pinned.current = true;
      last = box.scrollTop;
    };
    const follow = () => {
      if (!pinned.current) return;
      // An answer taller than the screen is read from its start: the request stays at the top, not its last line.
      const bubble = [...box.querySelectorAll(".turn:not(.folded) .asked")].pop();
      const top = bubble ? bubble.getBoundingClientRect().top - box.getBoundingClientRect().top + box.scrollTop - 12 : 0;
      if (bubble && box.scrollHeight - top > box.clientHeight) { box.scrollTop = top; pinned.current = false; last = box.scrollTop; }
      else box.scrollTop = box.scrollHeight;
    };
    const grew = new ResizeObserver(follow);
    box.addEventListener("scroll", moved, { passive: true });
    grew.observe(said);
    grew.observe(box);
    return () => { box.removeEventListener("scroll", moved); grew.disconnect(); };
  }, []);
  // Earlier requests fold to a line each; past the last few they gather under one line until it is opened.
  const folded = turns.filter((turn) => !open(turn));
  const hidden = all ? [] : folded.slice(0, Math.max(0, folded.length - SHOWN_FOLDED)).map((turn) => turn.n);
  return (
    <div class={"ask" + (home ? " askpage" : "")}>
      <div class="ask-scroll" ref={scroll}>
        <div class="ask-said" aria-live="polite">
          {home && (
            <div class="nowtop">
              <Top />
              <Headline />
              <NeedsCard most={house ? 2 : 3} />
              {house && now && <HouseCard now={now} />}
            </div>
          )}
          {hidden.length > 0 && (
            <button type="button" class="fold-more" onClick={() => setAll(true)}>
              {hidden.length === 1 ? "One earlier request" : `${hidden.length} earlier requests`}
            </button>
          )}
          {now && turns.filter((turn) => !hidden.includes(turn.n)).map((turn) => (
            <TurnView turn={turn} now={now} key={turn.n} open={open(turn)} onOpen={() => setOpened([...opened, turn.n])} />
          ))}
          {now && <Found now={now} text={text} quiet={!live} />}
        </div>
      </div>
      <Composer />
    </div>
  );
}

/** A card that came from elsewhere than Ask (an Undo that waits for a tap), as a sheet of its own. */
export function CardSheet({ card }: { card: Card }) {
  return <SheetFrame title="Waiting for your tap"><ConfirmCard card={card} /></SheetFrame>;
}
