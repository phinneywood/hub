// Dashboards: one page per category (hub/app/boards/), each made of blocks a person can hide and put in their own
// order. Read from Hub when looked at and every minute while open; nothing of a board is kept on the device. The app
// draws a few kinds of block and leaves out any it doesn't know, so every board is drawn by this one file.
import { signal } from "@preact/signals";
import { useEffect, useState } from "preact/hooks";
import { get, post } from "./api";
import { Top } from "./screens";
import { boardId, go, say, view } from "./state";
import type { View } from "./state";
import { Icon } from "./ui";

const EVERY = 60_000;

export interface BoardLine { id: string; name: string; text: string; next: string; tone: string }
interface Listed { id: string; name: string; what: string; line: { text: string; next: string; tone: string } | null }
interface Row { title: string; sub?: string; right?: string; tone?: string; href?: string; view?: string }
interface Tile { value: string; label: string; tone?: string }
interface Check { id: string; title: string; sub: string; done: boolean; tone: string }
interface Block {
  id: string; title: string; kind: "hero" | "tiles" | "rows" | "checks" | "line" | "bars" | "note";
  big?: string; small?: string; tone?: string; progress?: number; marks?: { at: number; label: string }[];
  tiles?: Tile[]; rows?: Row[]; items?: Check[]; sub?: string; empty?: string; text?: string; view?: string;
  segments?: string[]; start?: string; end?: string; now?: string; top?: string; bars?: { label: string; value: number; text: string }[];
}
interface Setting { key: string; label: string; type: "date" | "text" | "switch"; value: string }
interface Board { ok: true; id: string; name: string; say: string; sub: string; blocks: Block[]; hidden: { id: string; title: string }[];
  settings: Setting[] }

export const boards = signal<Listed[] | null>(null);
const shown = signal<Record<string, Board>>({});
const away = signal(false);
const arranging = signal(false);
let asked = 0;

export async function loadBoards() {
  try {
    boards.value = (await get<{ boards: Listed[] }>("boards")).boards;
  } catch {
    /* the list just isn't offered */
  }
}

export async function loadBoard(id: string) {
  if (!id) return;
  asked = Date.now();
  try {
    const found = await get<Board>("board/" + id);
    shown.value = { ...shown.value, [id]: found };
    away.value = false;
  } catch {
    away.value = true;
  }
}

/** Keep the open board fresh while someone looks at it, and read one as soon as it opens. */
export function watchBoards() {
  const due = () => view.value === "board" && !document.hidden && !arranging.value && Date.now() - asked > EVERY - 1000;
  window.setInterval(() => { if (due()) void loadBoard(boardId.value); }, EVERY);
  document.addEventListener("visibilitychange", () => { if (due()) void loadBoard(boardId.value); });
  view.subscribe((where) => {
    if (where === "board") void loadBoard(boardId.value);
    if (where === "boards" || where === "today") void loadBoards();
  });
  boardId.subscribe((id) => { if (view.value === "board") void loadBoard(id); });
}

// ---- the blocks ----

function Hero({ block }: { block: Block }) {
  return (
    <div class={"bhero" + (block.tone ? " " + block.tone : "")}>
      <b>{block.big}</b>
      {block.small && <span>{block.small}</span>}
      {block.progress !== undefined && (
        <div class="bprog" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(block.progress * 100)}>
          <i style={{ width: `${Math.max(1.5, block.progress * 100)}%` }} />
          {(block.marks ?? []).map((m) => <em key={m.label} style={{ left: `${m.at * 100}%` }}><small>{m.label}</small></em>)}
        </div>
      )}
    </div>
  );
}

function Tiles({ block }: { block: Block }) {
  const tiles = block.tiles ?? [];
  return (
    <div class={"btiles n" + Math.min(tiles.length, 4)}>
      {tiles.map((t) => <div class={"btile" + (t.tone ? " " + t.tone : "")} key={t.label}><b>{t.value}</b><span>{t.label}</span></div>)}
    </div>
  );
}

function RowView({ row }: { row: Row }) {
  const inner = (
    <>
      <div class="row-main"><b>{row.title}</b>{row.sub && <span>{row.sub}</span>}</div>
      {row.right ? <span class="row-q">{row.right}</span> : (row.href || row.view) ? <span class="go"><Icon name="right" /></span> : <span />}
    </>
  );
  const cls = "row free" + (row.tone ? " " + row.tone : "");
  if (row.view) return <button type="button" class={cls} onClick={() => go(row.view as View)}>{inner}</button>;
  if (row.href) return <a class={cls} href={row.href}>{inner}</a>;
  return <div class={cls}>{inner}</div>;
}

function Rows({ block }: { block: Block }) {
  const rows = block.rows ?? [];
  if (!rows.length) return <p class="quiet-line">{block.empty ?? "Nothing here."}</p>;
  return <div class="rows">{rows.map((row, n) => <RowView row={row} key={n} />)}</div>;
}

function Checks({ block, board }: { block: Block; board: string }) {
  const [done, setDone] = useState<Record<string, boolean>>({});
  const flip = async (item: Check) => {
    const now = !(done[item.id] ?? item.done);
    setDone({ ...done, [item.id]: now });
    const answer = await post(`board/${board}/tick`, { item: item.id, done: now });
    if (answer.error) {
      setDone({ ...done, [item.id]: !now });
      say(answer.error);
    }
    void loadBoard(board);
  };
  return (
    <>
      {block.sub && <p class="sub bsub">{block.sub}</p>}
      <div class="rows">
        {(block.items ?? []).map((item) => {
          const on = done[item.id] ?? item.done;
          return (
            <div class={"row tick need" + (on ? " on" : "") + (item.tone && !on ? " " + item.tone : "")} key={item.id}>
              <button type="button" class="tickbtn" aria-pressed={on} aria-label={(on ? "Not done: " : "Done: ") + item.title} onClick={() => void flip(item)}>
                <span class="ring"><Icon name="check" /></span>
              </button>
              <div class="row-main"><b>{item.title}</b><span>{item.sub}</span></div>
              <span />
            </div>
          );
        })}
      </div>
    </>
  );
}

function Line({ block }: { block: Block }) {
  const segments = block.segments ?? [];
  if (!segments.length) return <p class="quiet-line">{block.empty ?? "No history yet."}</p>;
  return (
    <figure class="bline">
      {(block.now || block.top) && <figcaption><b>{block.now}</b><span>{block.top}</span></figcaption>}
      <svg viewBox="0 0 720 180" preserveAspectRatio="none" role="img" aria-label={block.title}>
        {segments.map((points, n) => <polyline points={points} key={n} vector-effect="non-scaling-stroke" />)}
      </svg>
      <div class="baxis"><span>{block.start}</span><span>{block.end}</span></div>
    </figure>
  );
}

function Bars({ block }: { block: Block }) {
  const bars = block.bars ?? [];
  if (!bars.length) return <p class="quiet-line">{block.empty ?? "Nothing to show."}</p>;
  const most = Math.max(...bars.map((b) => b.value), 1);
  return (
    <div class="bbars">
      {bars.map((b) => (
        <div class="bbar" key={b.label}>
          <span>{b.label}</span><i style={{ width: `${Math.max(2, (100 * b.value) / most)}%` }} /><b>{b.text}</b>
        </div>
      ))}
    </div>
  );
}

function Body({ block, board }: { block: Block; board: string }) {
  if (block.kind === "hero") return <Hero block={block} />;
  if (block.kind === "tiles") return <Tiles block={block} />;
  if (block.kind === "rows") return <Rows block={block} />;
  if (block.kind === "checks") return <Checks block={block} board={board} />;
  if (block.kind === "line") return <Line block={block} />;
  if (block.kind === "bars") return <Bars block={block} />;
  if (block.kind === "note") return <p class="bnote">{block.text}</p>;
  return null;
}

// ---- arranging: up, down, hide, show; kept for this person only ----

async function keep(board: Board, order: string[], hidden: string[]) {
  const answer = await post(`board/${board.id}/layout`, { order, hidden });
  if (answer.error) say(answer.error);
  await loadBoard(board.id);
}

function Mover({ board, at }: { board: Board; at: number }) {
  const ids = board.blocks.map((b) => b.id);
  const hidden = board.hidden.map((h) => h.id);
  const move = (by: number) => {
    const order = [...ids];
    const taken = order.splice(at, 1)[0] ?? "";
    order.splice(at + by, 0, taken);
    void keep(board, [...order, ...hidden], hidden);
  };
  return (
    <div class="bmove">
      <button type="button" aria-label="Move up" disabled={at === 0} onClick={() => move(-1)}><Icon name="up" /></button>
      <button type="button" class="down" aria-label="Move down" disabled={at === ids.length - 1} onClick={() => move(1)}><Icon name="up" /></button>
      <button type="button" class="bhide" onClick={() => void keep(board, [...ids, ...hidden], [...hidden, ids[at] ?? ""])}>Hide</button>
    </div>
  );
}

function Settings({ board }: { board: Board }) {
  const [values, setValues] = useState<Record<string, string>>({});
  if (!board.settings.length) return null;
  const save = async (s: Setting, value?: string) => {
    const answer = await post(`board/${board.id}/set`, { key: s.key, value: value ?? values[s.key] ?? s.value });
    say(answer.error ?? answer.said ?? "Kept");
    setValues({ ...values, [s.key]: s.type === "text" ? "" : values[s.key] ?? "" });
    void loadBoard(board.id);
  };
  return (
    <section class="block">
      <h2>Settings</h2>
      <div class="rows">
        {board.settings.map((s) => s.type === "switch" ? (
          <div class="row free bset" key={s.key}>
            <div class="row-main"><b>{s.label}</b></div>
            <button type="button" class={"row-act" + (s.value ? " on" : "")} role="switch" aria-checked={!!s.value} aria-label={s.label}
              onClick={() => void save(s, s.value ? "" : "on")}>{s.value ? "On" : "Off"}</button>
          </div>
        ) : (
          <label class="row free bset" key={s.key}>
            <div class="row-main"><b>{s.label}</b></div>
            <span class="bfield">
              <input type={s.type} autocomplete="off" value={values[s.key] ?? s.value} onInput={(e) => setValues({ ...values, [s.key]: (e.target as HTMLInputElement).value })} />
              <button type="button" class="row-act" disabled={(values[s.key] ?? s.value) === s.value} onClick={() => void save(s)}>Save</button>
            </span>
          </label>
        ))}
      </div>
    </section>
  );
}

// ---- the screens ----

export function BoardScreen() {
  const id = boardId.value;
  const board = shown.value[id];
  useEffect(() => () => { arranging.value = false; }, [id]);
  if (!board) {
    return (
      <div class="wrap">
        <Top />
        {away.value
          ? <div class="banner"><b>Can’t open this dashboard</b><span>It isn’t here, or Hub can’t be reached. Turn Tailscale on and try again.</span></div>
          : <div class="skel"><h1 class="say">Reading</h1><p class="sub">One moment</p></div>}
      </div>
    );
  }
  const editing = arranging.value;
  return (
    <div class={"wrap board" + (editing ? " arranging" : "")} data-board={board.id}>
      <Top />
      <p class="beyebrow">{board.name}</p>
      <h1 class="say">{board.say}</h1>
      {board.sub && <p class="sub">{board.sub}</p>}
      <div class="bgrid">
        {board.blocks.map((block, n) => (
          <section class={"block b-" + block.kind} key={block.id} data-block={block.id}>
            <div class="bhead"><h2>{block.title}</h2>{editing && <Mover board={board} at={n} />}</div>
            <Body block={block} board={board.id} />
          </section>
        ))}
      </div>
      {editing && board.hidden.length > 0 && (
        <section class="block">
          <h2>Hidden</h2>
          <div class="rows">
            {board.hidden.map((h) => (
              <div class="row free" key={h.id}>
                <div class="row-main"><b>{h.title}</b></div>
                <button type="button" class="row-act" onClick={() => void keep(board, [...board.blocks.map((b) => b.id), h.id],
                  board.hidden.map((x) => x.id).filter((x) => x !== h.id))}>Show</button>
              </div>
            ))}
          </div>
        </section>
      )}
      {editing && <Settings board={board} />}
      <div class="btns">
        <button type="button" class={"btn " + (editing ? "go" : "ghost")} onClick={() => (arranging.value = !editing)}>{editing ? "Done" : "Arrange"}</button>
        <button type="button" class="btn ghost" onClick={() => go("boards")}>All dashboards</button>
      </div>
      {!editing && <p class="quiet">Arrange puts the blocks in your order and hides the ones you don’t want. It changes this page for you only.</p>}
    </div>
  );
}

export function BoardsScreen() {
  const list = boards.value;
  return (
    <div class="wrap">
      <Top />
      <h1 class="say">Dashboards</h1>
      <p class="sub">One page for each part of the household. Ask knows what’s on them.</p>
      {list === null ? <p class="quiet-line">Reading</p> : !list.length ? <p class="quiet-line">No dashboards are yours yet.</p> : (
        <div class="rows spaced">
          {list.map((b) => (
            <button type="button" class={"row free" + (b.line?.tone ? " " + b.line.tone : "")} key={b.id} onClick={() => go("board", b.id)}>
              <div class="row-main"><b>{b.name}</b><span>{b.line ? [b.line.text, b.line.next].filter(Boolean).join(". ") : b.what}</span></div>
              <span class="go"><Icon name="right" /></span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/** Today's part: one line for each dashboard, a tap opening it. */
export function BoardLines({ lines }: { lines: BoardLine[] }) {
  if (!lines.length) return null;
  return (
    <section class="block">
      <h2>Dashboards</h2>
      <div class="rows">
        {lines.map((l) => (
          <button type="button" class={"row free" + (l.tone ? " " + l.tone : "")} key={l.id} onClick={() => go("board", l.id)}>
            <div class="row-main"><b>{l.name}: {l.text}</b>{l.next && <span>{l.next}</span>}</div>
            <span class="go"><Icon name="right" /></span>
          </button>
        ))}
      </div>
    </section>
  );
}

/** Under an answer in Ask: the board's headline and its first numbers, read from Hub for whoever is looking. */
export function BoardCard({ id, fresh = false }: { id: string; fresh?: boolean }) {
  const board = shown.value[id];
  useEffect(() => { if (!board || fresh) void loadBoard(id); }, [id]);
  if (!board) return null;
  const first = board.blocks.find((b) => b.kind === "tiles") ?? board.blocks.find((b) => b.kind === "hero");
  return (
    <div class="dayrows bcard">
      <p class="beyebrow">{board.name}</p>
      <b class="bcard-say">{board.say}</b>
      {first && <Body block={first} board={board.id} />}
      <button type="button" class="now-more" onClick={() => go("board", board.id)}>Open {board.name}<Icon name="right" /></button>
    </div>
  );
}
