// Ask, one tap from Home: a conversation with Hub, which speaks first with what needs you and the house.
import { Fragment } from "preact";
import type { JSX, VNode } from "preact";
import { useEffect, useLayoutEffect, useRef, useState } from "preact/hooks";
import { health, healthAsk, post } from "./api";
import { able, ask, asked, forgetNote, loadAble, loadOlder, older, stop, talk } from "./asking";
import type { Card, MailRow, Thing, Turn } from "./asking";
import { FoundCard, PhotoCard, Years } from "./archive";
import type { FoundLine } from "./archive";
import { BoardCard, boards } from "./boards";
import { BestOfMonth } from "./photos";
import { ErrandsCard } from "./errands";
import { DrillProgress } from "./health";
import { GroceriesCard, KitchenReply } from "./kitchen";
import { ChatHead, DayRows, Greeting, HouseCard, NeedsCard } from "./now";
import { RoomCell, spans } from "./screens";
import { ShowCard, ShowPick } from "./show";
import type { ShowChoice } from "./show";
import { inApp } from "./today";
import { dimRoom, dragRoom, dragVolume, go, lookAtCare, loadPicks, picks, playPick, runScene, say, seen, setVolume, switchRoom, undo, undone } from "./state";
import type { View } from "./state";
import type { ArchiveItem, CareDrill, House, Room } from "./types";
import { Glow, Icon, SheetFrame, Slider, Switch } from "./ui";


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
  ["boards", "Dashboards", "One page for each part of the household"],
];

/** The one page or dashboard a few typed letters name (one word, a name that starts with it, or is it), for Return to
 * open instead of sending the letters to the model ("Kitc"). Rooms, scenes and playlists are left to their own taps. */
export function onePlace(text: string): (() => void) | null {
  const typed = text.trim().toLowerCase();
  if (typed.length < 2 || /\s/.test(typed)) return null;
  const pages = PAGES.filter(([, name]) => name.toLowerCase().startsWith(typed));
  const dashboards = (boards.value ?? []).filter((b) => b.name.toLowerCase().startsWith(typed));
  const exact = pages.find(([, name]) => name.toLowerCase() === typed);
  if (exact) return () => go(exact[0]);
  if (pages.length + dashboards.length !== 1) return null;
  return pages.length ? () => go(pages[0]![0]) : () => go("board", dashboards[0]!.id);
}

/** What a few letters reach without asking anything: rooms, scenes, playlists and pages, worked right in the list.
 * With nothing typed, nothing, except where Ask isn't set up: there, where the words will go. */
export function Found({ now, text }: { now: House; text: string }) {
  const has = (name: string) => name.toLowerCase().includes(text.toLowerCase());
  const set = able.value?.ready !== false;
  if (!text) {         // where Ask isn't set up, say where words go; otherwise the suggested questions say it
    if (set) return null;
    return (
      <div class="found">
        <p class="ask-hint">Scenes run at once. A few letters find a room, a playlist or a page. Anything else goes to the chat.</p>
        <a class="sug" href="/ask"><span>Ask in the chat<small>For anything this list can’t do yet</small></span><span class="how">Open</span></a>
      </div>
    );
  }
  const rooms = Object.values(now.rooms).filter((r) => has(r.name)).slice(0, 4);
  const scenes = now.scenes.filter((s) => has(s.name));
  const lists = (picks.value ?? []).filter((p) => has(p.name)).slice(0, 3);
  const pages = text.length > 1 ? PAGES.filter(([, name]) => name.toLowerCase().startsWith(text.toLowerCase())).slice(0, 2) : [];
  const dashboards = text.length > 1 ? (boards.value ?? []).filter((b) => b.name.toLowerCase().startsWith(text.toLowerCase())).slice(0, 2) : [];
  if (!rooms.length && !scenes.length && !lists.length && !pages.length && !dashboards.length) return null;
  return (
    <div class="found brief">
      {pages.map(([where, name, what]) => (
        <button type="button" class="sug" key={where} onClick={() => { asked.value = ""; go(where); }}>
          <span>{marked(name, text)}<small>{what}</small></span><span class="go"><Icon name="right" /></span>
        </button>
      ))}
      {dashboards.map((b) => (
        <button type="button" class="sug" key={"board-" + b.id} onClick={() => { asked.value = ""; go("board", b.id); }}>
          <span>{marked(b.name, text)}<small>Dashboard. {b.line?.text ?? b.what}</small></span><span class="go"><Icon name="right" /></span>
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

/** A change that waits for a tap: what would happen, and the two ways to answer. Typing "yes" does nothing;
 * only this button, on a signed-in device, makes the change. A card for the house (`setup`) is undone through the
 * house's own Undo; one for mail (`mail/change`) through its own. */
function ConfirmCard({ card }: { card: Card }) {
  const id = card.id ?? 0;
  const base = card.path ?? "setup";
  const [state, setState] = useState<"waiting" | "working" | "done" | "dismissed" | "gone" | "undone">("waiting");
  const [batch, setBatch] = useState<string | null>(null);
  const [own, setOwn] = useState(false);          // a done change this card itself can put back (mail)
  const [said, setSaid] = useState("");
  useEffect(() => {           // opened again later: what became of the card
    let stopped = false;
    fetch(`/app/api/${base}/${id}`, { headers: { accept: "application/json" } })
      .then((answer) => answer.json() as Promise<{ status: string; batch: string | null; undo?: boolean }>)
      .then((found) => {
        if (stopped || found.status === "waiting") return;
        if (found.status === "done") { setBatch(found.batch); setOwn(found.undo === true); }
        setState(found.status === "done" ? "done" : found.status === "dismissed" ? "dismissed" : found.status === "undone" ? "undone" : "gone");
      }, () => undefined);
    return () => { stopped = true; };
  }, [id, base]);
  const tap = async (choice: "confirm" | "dismiss") => {
    setState("working");
    const answer = await post(`${base}/${id}/${choice}`);
    if (answer.error) {         // not done. A card that still waits (Hub was busy this minute) stays to be tapped again
      const still = await fetch(`/app/api/${base}/${id}`, { headers: { accept: "application/json" } })
        .then((found) => found.json() as Promise<{ status?: string }>).catch(() => ({ status: "" }));
      if (still.status === "waiting") { say(answer.error); setState("waiting"); return; }
      setSaid(answer.error); setState("gone"); return;
    }
    if (choice === "confirm") { setBatch(answer.batch ?? null); setOwn(answer.undo === true); }
    if (answer.said && base !== "setup") say(answer.said);
    setState(choice === "confirm" ? "done" : "dismissed");
  };
  const putBack = async () => {
    const answer = await post(`${base}/${id}/undo`);
    if (answer.error) { say(answer.error); return; }
    say(answer.said ?? "Put back");
    setState("undone");
  };
  const open = state === "waiting" || state === "working";
  const back = batch !== null && undone.value.includes(batch);
  return (
    <div class={"wait" + (open ? "" : " settled")}>
      <b class="wait-t">{card.title}</b>
      <p class="wait-cap">{open ? "After your tap" : state === "done" || state === "undone" ? "Done" : "Not done"}</p>
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
        : batch ? <p class="took"><button type="button" onClick={() => void undo(batch)}>Undo</button></p>
        : own ? <p class="took"><button type="button" onClick={() => void putBack()}>Undo</button></p> : null)}
      {state === "undone" && <p class="took"><span>Put back as it was</span></p>}
      {state === "dismissed" && <p class="took"><span>Left as it was.</span></p>}
      {state === "gone" && <p class="took"><span>{said || "This card isn’t waiting any more. Ask again."}</span></p>}
    </div>
  );
}

/** Hub's morning message: a numbered line each, with its one button. Answered by typing "done 2", or by the button. */
function MorningCard({ things, hint }: { things: Thing[]; hint: string }) {
  return (
    <div class="morning">
      <ol class="rows">
        {things.map((thing) => (
          <li class="row" key={thing.key}>
            <span class="row-t day">{thing.n}</span>
            <div class="row-main"><b>{thing.text}</b></div>
            {thing.open && <a class="row-act" href={inApp(thing.open.href)}
              onClick={() => void post("ask/morning/opened", { key: thing.key })}>{thing.open.label}</a>}
          </li>
        ))}
      </ol>
      {hint && <p class="took">{hint}</p>}
    </div>
  );
}

// The emails a mail search found, newest first: who and the subject, when, and a link that opens each in Gmail. The one
// Hub read is marked. Read-only: nothing here changes the mail.
function MailRows({ rows, opened }: { rows: MailRow[]; opened: number[] }) {
  if (!rows.length) return null;
  return (
    <div class="rows mailrows">
      {rows.map((row) => (
        <div class={"row" + (opened.includes(row.n) ? " now" : "")} key={row.n}>
          <span class="row-t day">{row.when}</span>
          <div class="row-main"><b>{row.who}</b><span>{row.subject || "(no subject)"}{row.off && <small class="off"> · AI off</small>}</span></div>
          <a class="row-act" href={row.href} target="_blank" rel="noopener noreferrer" aria-label={`Open ${row.who}: ${row.subject} in Gmail`}>Open</a>
        </div>
      ))}
    </div>
  );
}

/** A request for the house's caretaker that waits for a tap: restarting Home Assistant, or leaving a problem alone.
 * Typing "yes" does nothing; only this button, on a signed-in device, leaves the request, which Hub and then the
 * caretaker check again. */
function CareCard({ card }: { card: Card }) {
  const [state, setState] = useState<"waiting" | "working" | "asked" | "refused" | "dismissed">("waiting");
  const [said, setSaid] = useState("");
  const tap = async () => {
    setState("working");
    const answer = await healthAsk(card.do ?? "", card.target ?? "");
    setSaid(answer.error ?? answer.said ?? "Asked the caretaker.");
    setState(answer.error ? "refused" : "asked");
    if (!answer.error) void lookAtCare();
  };
  const open = state === "waiting" || state === "working";
  return (
    <div class={"wait" + (open ? "" : " settled")}>
      <b class="wait-t">{card.title}</b>
      <p class="wait-cap">{open ? "After your tap" : state === "asked" ? "Asked" : "Not asked"}</p>
      <ul class="wait-lines">{(card.lines ?? []).map((line) => <li key={line}>{line}</li>)}</ul>
      {open && (
        <>
          <p class="wait-why">{card.why}</p>
          <div class="kacts">
            <button type="button" class={"btn " + (card.do === "ignore" ? "risk" : "go")} disabled={state === "working"}
              onClick={() => void tap()}>{card.button ?? "Do it"}</button>
            <button type="button" class="btn ghost" disabled={state === "working"} onClick={() => setState("dismissed")}>Not now</button>
          </div>
        </>
      )}
      {(state === "asked" || state === "refused") && <p class="took"><span>{said}</span></p>}
      {state === "dismissed" && <p class="took"><span>Left as it was.</span></p>}
      <p class="took"><button type="button" onClick={() => go("health")}>Open House health</button></p>
    </div>
  );
}

/** Under an answer about the house's health: the way to House health and, after "run the hub drill", the drill
 * itself, followed as the caretaker plays it on its made-up house. */
function HealthCard({ card }: { card: Card }) {
  const [drill, setDrill] = useState<CareDrill | null>(null);
  useEffect(() => {
    const name = card.drill;
    if (!name) return undefined;
    let stopped = false, timer = 0, looks = 0;
    const look = async () => {
      try {
        const found = (await health()).drill;
        if (stopped) return;
        const mine = found && found.scenario === name ? found : null;
        setDrill(mine);
        if (mine?.done) return;
      } catch { /* looked at again below */ }
      if (!stopped && ++looks < 90) timer = window.setTimeout(() => void look(), 2000);     // the caretaker starts it within a minute
    };
    void look();
    return () => { stopped = true; clearTimeout(timer); };
  }, [card.drill]);
  return (
    <div class="care-under">
      {card.drill && (drill ? <DrillProgress drill={drill} /> : <p class="took"><span class="work" />The caretaker starts the drill within a minute.</p>)}
      <button type="button" class="row-act" onClick={() => go("health")}>Open House health</button>
    </div>
  );
}

function CardView({ card, now, said = "" }: { card: Card; now: House; said?: string }) {
  if (card.kind === "morning" && card.things) return <MorningCard things={card.things} hint={card.hint ?? ""} />;
  if (card.kind === "confirm" && card.id !== undefined) return <ConfirmCard card={card} />;
  if (card.kind === "care") return <CareCard card={card} />;
  if (card.kind === "health") return <HealthCard card={card} />;
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
    const one = rooms.length === 1 && rooms[0]!.dims ? rooms[0]! : null;
    if (!rooms.length) return null;
    // One room that dims: its brightness right under the answer, to set it just so without opening the room.
    return (
      <>
        <div class="plan">{rooms.map((r, n) => <RoomCell key={r.id} room={r} span={widths[n] ?? 2} order={0} />)}</div>
        {one && <div class="ask-dim"><Slider big value={one.on ? one.level : 0} label={`${one.name} brightness`}
          onDrag={(v) => dragRoom(one.id, v)} onSet={(v) => void dimRoom(one.id, v)} /></div>}
      </>
    );
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
  if (card.kind === "showpick") return <ShowPick items={(card.items ?? []) as ShowChoice[]} />;
  if (card.kind === "photos") {       // Hub's sentence heads the card only where the answer above doesn't already say it
    return <PhotoCard title={card.title && said.includes(card.title) ? undefined : card.title} items={(card.items ?? []) as ArchiveItem[]} search={card.search} />;
  }
  if (card.kind === "found") return <FoundCard items={(card.items ?? []) as FoundLine[]} count={card.count} search={card.search} />;
  if (card.kind === "best") return <BestOfMonth start={card.month} />;
  if (card.kind === "onthisday") return <div class="acard"><Years years={(card.years ?? []) as { year: number; items: ArchiveItem[] }[]} /></div>;
  if (card.kind === "day") return <DayRows show={card.show ?? []} />;
  if (card.kind === "groceries") return <GroceriesCard ids={card.items as number[]} all={card.all} cart={card.cart} since={card.since} />;
  if (card.kind === "errands" && card.ids) return <ErrandsCard ids={card.ids} />;
  if (card.kind === "board" && card.board) return <BoardCard id={card.board} />;
  if (card.kind === "money") return <BoardCard id="money" fresh />;
  if (card.kind.startsWith("career")) return <BoardCard id="career" fresh />;     // the desk, read again after a note, tick or job
  if (card.kind === "mail" && card.rows) return <MailRows rows={card.rows} opened={card.opened ?? []} />;
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

const FRESH = 30 * 60 * 1000;      // a pause this long ends a conversation; what came before it is earlier
const SHOWN_EARLIER = 2;      // earlier requests shown as a pair of small bubbles each; the rest gather under one line

/** The day a turn was asked, as a chat marks it: Today, Yesterday, a weekday this week, else the date. */
function dayName(at: number): string {
  const day = new Date(at), today = new Date();
  const days = Math.round((new Date(today.toDateString()).getTime() - new Date(day.toDateString()).getTime()) / 86400000);
  if (days <= 0) return "Earlier today";
  if (days === 1) return "Yesterday";
  if (days < 7) return day.toLocaleDateString(undefined, { weekday: "long" });
  return day.toLocaleDateString(undefined, { month: "long", day: "numeric" });
}

/** Where the conversation going on now starts: after its last pause of half an hour, if its last word was in the last half hour. */
function sessionStart(turns: Turn[]): number {
  const last = turns[turns.length - 1];
  if (!last || !(last.busy || last.waiting || Date.now() - last.at < FRESH)) return turns.length;
  let start = turns.length - 1;
  while (start > 0 && turns[start]!.at - turns[start - 1]!.at < FRESH) start -= 1;
  return start;
}

const ITEM = /^\s*(?:[-*•]|\d+[.)])\s+(.*)$/;      // a line a model wrote as a list item

/** A line's **bold** drawn bold; a stray mark is dropped, never shown. */
function bolded(line: string) {
  return line.split(/(\*\*[^*\n]+\*\*)/g).map((part, n) =>
    part.length > 4 && part.startsWith("**") && part.endsWith("**") ? <b key={n}>{part.slice(2, -2)}</b> : part.replace(/\*\*/g, ""));
}

/** The answer as plain text, its marks taken out: for a folded turn and for screen readers. */
const unmarked = (text: string) => text.replace(/\*\*/g, "").split("\n").map((line) => line.replace(ITEM, "$1")).join("\n");

/** An answer as it reads: lines as lines, "- " and "1. " lines as a list, **bold** in bold. The marks never show. */
function Said({ text }: { text: string }) {
  const out: VNode[] = [];
  let list: string[] = [];
  const done = () => {
    if (list.length) out.push(<ul class="said-list" key={out.length}>{list.map((item, n) => <li key={n}>{bolded(item)}</li>)}</ul>);
    list = [];
  };
  for (const line of text.split("\n")) {
    const item = ITEM.exec(line);
    if (item) { list.push(item[1]!); continue; }
    done();
    out.push(<span class="said-line" key={out.length}>{bolded(line)}</span>);
  }
  done();
  return <>{out}</>;
}

function TurnView({ turn, now, open, onOpen }: { turn: Turn; now: House; open: boolean; onOpen: () => void }) {
  // A change to the house says how long it took and can be put back. An answer needs neither.
  const batch = turn.cards.find((card) => card.batch)?.batch;
  const back = batch !== undefined && undone.value.includes(batch);
  if (!open) {         // an earlier turn: both bubbles, a line each, small; a tap opens it, and its Undo stays
    const first = (unmarked(turn.answer).split("\n")[0] ?? "").slice(0, 140);
    return (
      <div class={"turn folded" + (back ? " back" : "") + (batch ? " undoable" : "")}>
        <button type="button" class="fold" aria-expanded="false" aria-label={`${turn.asked}. Hub: ${unmarked(turn.answer)}`} onClick={onOpen}>
          {turn.asked && <span class="fold-asked">{turn.asked}</span>}
          {first && <span class="fold-answer"><span class="fold-text">{first}</span><Icon name="right" /></span>}
        </button>
        {batch && (back ? <span class="fold-back">Undone</span>
          : <button type="button" class="fold-undo" onClick={() => void undo(batch)}>Undo</button>)}
      </div>
    );
  }
  const morning = turn.cards.some((card) => card.kind === "morning");
  const shown = morning ? (turn.answer.split("\n")[0] ?? "") : turn.answer;
  return (
    <div class={"turn" + (back ? " back" : "")}>
      {turn.asked && <p class="asked">{turn.asked}</p>}
      {turn.waiting ? <p class="took waits">Waits for the last request to finish</p>
        : <div class="hub">{shown ? <div class={"answer" + (shown.length > 160 ? " long" : "")}><Said text={shown} /></div>
          : <span class="dots" role="status" aria-label="Hub is working"><i /><i /><i /></span>}</div>}
      {turn.busy && turn.doing && <p class="took">{turn.doing}</p>}
      {turn.cards.map((card, n) => <CardView card={card} now={now} said={turn.answer} key={n} />)}
      {!turn.busy && batch && (back
        ? <p class="took"><span>Put back as it was</span></p>
        : <p class="took"><button type="button" onClick={() => void undo(batch)}>Undo</button></p>)}
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
  const send = () => {
    const place = onePlace(text);
    if (place) { asked.value = ""; place(); return; }
    if (text.trim()) void ask(text);
  };
  return (
    <form class="composer" onSubmit={(e) => { e.preventDefault(); send(); }}>
      <textarea class="field" id="askq" ref={field} rows={1} autocomplete="off" autocapitalize="sentences" enterkeyhint="send"
        placeholder="Message Hub" aria-label="Message Hub, or type a room, a scene, a playlist or a page" value={text}
        onInput={(e) => (asked.value = e.currentTarget.value)}
        onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); } }} />
      {busy && !text.trim()
        ? <button type="button" class="send stop" aria-label="Stop" onClick={stop}><Icon name="stop" /></button>
        : <button type="submit" class={"send" + (text.trim() ? " on" : "")} aria-label="Send" disabled={!text.trim()}><Icon name="up" /></button>}
    </form>
  );
}

// Questions anyone can ask without changing anything, offered as a start beside what this person often asks.
const STARTERS = ["What needs me today?", "What’s on tomorrow?", "What’s on the list?"];
const PLACES: [View, string][] = [["today", "Today"], ["kitchen", "Kitchen"]];      // pages with no tab, one tap away at the start

/** Suggested messages above the field, as a chat offers them, until the person says something (Hub speaking first
 * with the morning message doesn't count). One tap sends it. */
function Prompts({ quiet }: { quiet: boolean }) {
  const usual = able.value?.usual ?? [];
  if (!quiet || able.value?.ready === false || asked.value.trim()) return null;
  const offered = [...usual, ...STARTERS.filter((words) => !usual.includes(words))].slice(0, 4);
  return (
    <div class="prompts" role="group" aria-label="Things to ask">
      {offered.map((words) => (
        <button type="button" key={words} class={"prompt" + (usual.includes(words) ? " usual" : "")}
          title={usual.includes(words) ? "You often ask this around now" : undefined} onClick={() => void ask(words)}>{words}</button>
      ))}
      {PLACES.map(([where, name]) => (
        <button type="button" key={where} class="prompt place" onClick={() => go(where)}>{name}<Icon name="right" /></button>
      ))}
    </div>
  );
}

/** Ask, as a conversation with Hub. `home`: the screen Hub opens to, where Hub speaks first with the day (and the
 * house too when it isn't beside it). Without `home`: the conversation alone, beside another screen on a wide one.
 * It is one conversation that keeps going: what came before the last pause sits above what Hub says now, folded to
 * small bubbles under the day it was said, and reads back as far as Hub keeps it; what is going on now follows. */
export function Ask({ home, house }: { home?: boolean; house?: boolean }) {
  const scroll = useRef<HTMLDivElement>(null);
  const [opened, setOpened] = useState<number[]>([]);
  const [all, setAll] = useState(false);
  useEffect(() => { void loadPicks(); void loadAble(); }, []);
  const now = seen.value;
  const text = asked.value.trim();
  const turns = talk.value;
  const start = sessionStart(turns);
  const earlier = turns.slice(0, start), current = turns.slice(start);
  const pinned = useRef(true);
  const asking = turns.some((turn) => turn.busy || turn.waiting);
  useEffect(() => {           // what is asked and what a few letters find are brought into view
    const box = scroll.current;
    if (box && (asking || text)) { pinned.current = true; box.scrollTop = box.scrollHeight; }
  }, [turns.length, asking, text.length > 0]);
  useEffect(() => {           // and what arrives under it stays in view, unless a finger has scrolled back to read
    const box = scroll.current, said = box?.firstElementChild;
    if (!box || !said) return undefined;
    // Only a finger scrolling back unpins it. What arrives late (a card under an answer) makes the box taller
    // without moving it, and must not be mistaken for that. The keyboard coming up makes the box shorter.
    let last = box.scrollTop, held = false, settled = 0;
    // While a finger is on the conversation, and for a moment after it lets go (the scroll still coasting), nothing
    // moves it but the finger: a card growing then must not pull the view back down against the drag.
    const down = () => { held = true; };
    const up = () => { held = false; settled = Date.now() + 800; };
    const moved = () => {
      const gap = box.scrollHeight - box.scrollTop - box.clientHeight;
      if (box.scrollTop < last - 2) pinned.current = gap < 80;
      else if (gap < 80) pinned.current = true;
      last = box.scrollTop;
    };
    const follow = () => {
      if (!pinned.current || held || Date.now() < settled) return;
      // What is newest is read from its start: the last request, or Hub's greeting, stays at the top when what
      // follows it is taller than the screen, instead of showing only its last lines.
      const start = [...box.querySelectorAll(".nowtop, .turn:not(.folded) .asked")].pop();
      const top = start ? start.getBoundingClientRect().top - box.getBoundingClientRect().top + box.scrollTop - 12 : 0;
      if (start && box.scrollHeight - top > box.clientHeight) { box.scrollTop = top; pinned.current = false; last = box.scrollTop; }
      else box.scrollTop = box.scrollHeight;
    };
    const grew = new ResizeObserver(follow);
    box.addEventListener("scroll", moved, { passive: true });
    box.addEventListener("touchstart", down, { passive: true });
    box.addEventListener("touchend", up, { passive: true });
    box.addEventListener("touchcancel", up, { passive: true });
    grew.observe(said);
    grew.observe(box);
    return () => {
      box.removeEventListener("scroll", moved);
      box.removeEventListener("touchstart", down);
      box.removeEventListener("touchend", up);
      box.removeEventListener("touchcancel", up);
      grew.disconnect();
    };
  }, []);
  const hidden = all ? [] : earlier.slice(0, Math.max(0, earlier.length - SHOWN_EARLIER)).map((turn) => turn.n);
  const back = (turn: Turn) => { pinned.current = false; setOpened([...opened, turn.n]); };   // read where it is
  const showing = earlier.filter((turn) => !hidden.includes(turn.n));
  const quiet = !current.some((turn) => turn.asked);
  // The band of light behind Hub's greeting is as warm as the house: brighter the more rooms are lit.
  const lit = now ? Object.values(now.rooms).filter((room) => room.on && !room.outside).length : 0;
  return (
    <div class={"ask" + (home ? " askpage" : "")} style={home ? { "--warm": Math.min(1, lit / 4) } as JSX.CSSProperties : undefined}>
      {home && <Glow />}
      {home && <ChatHead />}
      <ShowCard />
      <div class="ask-scroll" ref={scroll}>
        <div class="ask-said" aria-live="polite">
          {hidden.length > 0 ? (
            <button type="button" class="fold-more" onClick={() => { pinned.current = false; setAll(true); }}>
              {hidden.length === 1 ? "Show one earlier message" : `Show ${hidden.length} earlier messages`}
            </button>
          ) : older.value && (
            <button type="button" class="fold-more" onClick={() => { pinned.current = false; void loadOlder(); }}>Show earlier messages</button>
          )}
          {now && showing.map((turn, i) => (
            <Fragment key={turn.n}>
              {(i === 0 || dayName(showing[i - 1]!.at) !== dayName(turn.at)) && <p class="chat-time">{dayName(turn.at)}</p>}
              <TurnView turn={turn} now={now} open={opened.includes(turn.n)} onOpen={() => back(turn)} />
            </Fragment>
          ))}
          {home && (
            <div class="nowtop">
              {earlier.length > 0 && <p class="chat-time">Now</p>}
              <Greeting />
              <NeedsCard most={able.value?.morning ? 0 : house ? 2 : 3} />
              {house && now && <HouseCard now={now} />}
            </div>
          )}
          {now && current.map((turn) => <TurnView turn={turn} now={now} key={turn.n} open onOpen={() => undefined} />)}
          {now && <Found now={now} text={text} />}
          {home && <Prompts quiet={quiet} />}
        </div>
      </div>
      {!home && <Prompts quiet={quiet} />}
      <Composer />
    </div>
  );
}

/** A card that came from elsewhere than Ask (an Undo that waits for a tap), as a sheet of its own. */
export function CardSheet({ card }: { card: Card }) {
  return <SheetFrame title="Waiting for your tap"><ConfirmCard card={card} /></SheetFrame>;
}
