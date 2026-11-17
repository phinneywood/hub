// Errands: what Hub got ready for a tap, from the mail and the class guide. Read from Hub (hub/app/errands/) when
// they are looked at; nothing about them is kept on the device. A tap opens the exact place to finish one, filled in
// (a reply in the mail app, an event in Google Calendar, the sender's own page, a call), and Hub notes it, so it can
// watch for the proof and close the errand by itself. Putting things on the shopping list is the one thing Hub does.
// Each person sees their own errands and the household's (the class guide's); Hub picks by the device's sign-in.
import { signal } from "@preact/signals";
import { useEffect } from "preact/hooks";
import { loadDay } from "./day";
import { Top } from "./screens";
import { go, say, sheet, undone, view } from "./state";
import type { Errand, Errands as ErrandsData, Step, Way } from "./types";
import { Icon, SheetFrame } from "./ui";

const EVERY = 60_000;
const BUSY_EVERY = 2500;          // while Hub reads or looks for proof
const NUMBERS = ["Nothing", "One thing", "Two things", "Three things", "Four things", "Five things", "Six things", "Seven things",
  "Eight things", "Nine things", "Ten things"];
const STEP_ICONS = { mail: "mail", open: "out", call: "call", calendar: "calendar", list: "bag" } as const;

export const errands = signal<ErrandsData | null>(null);
export const errandsAway = signal(false);
const known = signal<Record<number, Errand>>({});     // every errand card seen lately, for the sheet
let asked = 0, timer = 0;

function keep(cards: Errand[]) {
  const all = { ...known.value };
  for (const card of cards) all[card.id] = card;
  known.value = all;
}

let toldAt = 0;               // when the screen last said that Hub saw an errand finished

/** Reads the errands again. True when it said that one finished. */
export async function loadErrands(): Promise<boolean> {
  asked = Date.now();
  let told = false;
  clearTimeout(timer);
  try {
    const answer = await fetch("/app/api/errands", { headers: { accept: "application/json" } });
    if (!answer.ok) throw new Error("Hub is away");
    const found = (await answer.json()) as ErrandsData;
    const before = errands.value;
    if (before) {             // what Hub saw finished since the last look: told once
      const was = new Set(before.ready.map((e) => e.id));
      const closed = found.done.find((e) => was.has(e.id) && e.ended?.seen);
      if (closed?.ended) { say(`${closed.ended.text}: ${closed.title}`); toldAt = Date.now(); told = true; }
    }
    errands.value = found;
    errandsAway.value = false;
    keep([...found.ready, ...found.later, ...found.done]);
    if ((found.reading || found.checking) && (view.value === "errands" || view.value === "today")) {
      timer = window.setTimeout(() => void loadErrands(), BUSY_EVERY);
    }
  } catch {
    errandsAway.value = true;
  }
  return told;
}

/** Look again: a finished errand found by this read (or by a look already under way) is what the screen says,
 *  not "Looking", which would cover it. */
async function lookAgain() {
  const at = Date.now();
  const answer = await send("read");
  const told = await loadErrands();
  if (answer.error) say(answer.error);
  else if (!told && toldAt < at) say(answer.said ?? "Looking");
}

export function seeErrands(cards: Errand[]) {
  keep(cards);
}

async function send(path: string, body: Record<string, unknown> = {}, keepalive = false) {
  try {
    const answer = await fetch("/app/api/errands/" + path, {
      method: "POST", keepalive, headers: { "content-type": "application/json", accept: "application/json" }, body: JSON.stringify(body),
    });
    return (await answer.json()) as { ok?: boolean; said?: string; error?: string; undo?: boolean; card?: Errand | null };
  } catch {
    return { error: "Can’t reach Hub. Nothing was changed." };
  }
}

function fresh() {
  void loadErrands();
  if (view.value === "today") void loadDay();
}

/** A step was opened (the link itself goes on as usual): Hub notes it, and the card shows it started. */
function opened(errand: Errand, way: Way, step: Step) {
  const ways = errand.ways.map((w) => (w.n === way.n
    ? { ...w, chosen: true, steps: w.steps.map((s) => (s.kind === step.kind ? { ...s, taken: true } : s)) } : { ...w, chosen: false }));
  keep([{ ...errand, state: "opened", ways, opened: errand.opened ?? { label: "Opened just now", watch: "", way: way.n, left: 0 } }]);
  void send(`${errand.id}/go`, { choice: way.n, step: step.kind }, true).then((answer) => {
    if (answer.error) say(answer.error);
    else if (answer.card) keep([answer.card]);
  });
}

async function act(errand: Errand, what: "done" | "later" | "not" | "list", body: Record<string, unknown> = {}) {
  const answer = await send(`${errand.id}/${what}`, body);
  if (answer.error) { say(answer.error); return; }
  if (answer.card) keep([answer.card]);
  if (what !== "list" || answer.card?.state === "done") {
    if (sheet.value?.kind === "errand") sheet.value = null;
  }
  say(answer.said ?? "Done", null, answer.undo ? () => void back(errand) : undefined);
  fresh();
}

async function back(errand: Errand) {
  const answer = await send(`${errand.id}/back`);
  say(answer.error ?? answer.said ?? "Put back");
  if (answer.card) keep([answer.card]);
  fresh();
}

/** One step: a link that opens the place to finish it, or (for the list) a button Hub acts on. Its words are the way's
 * when it is the way's only step, the step's own once a way is chosen, and where it opens under its heading in a sheet. */
function StepButton({ errand, way, step, main, said }: { errand: Errand; way: Way; step: Step; main: boolean; said?: "step" | "go" }) {
  const cls = "btn " + (main ? "go" : "ghost") + (step.taken ? " taken" : "");
  const words = said === "go" ? step.go : said === "step" ? step.label : way.label;
  if (step.kind === "list" || !step.href) {
    return <button type="button" class={cls} onClick={() => void act(errand, "list", { choice: way.n })}>{words}</button>;
  }
  const away = step.href.startsWith("http");
  return (
    <a class={cls} href={step.href} target={away ? "_blank" : undefined} rel={away ? "noopener noreferrer" : undefined}
      onClick={() => opened(errand, way, step)}>
      {step.taken && <Icon name="check" />}{words}{away && !step.taken && <Icon name="out" />}
    </a>
  );
}

/** A way of finishing: with one step, the button is that step; with more, it opens them in a sheet. */
function WayButton({ errand, way, main }: { errand: Errand; way: Way; main: boolean }) {
  const only = way.steps.length === 1 ? way.steps[0] : undefined;
  if (only) return <StepButton errand={errand} way={way} step={only} main={main} />;
  return <button type="button" class={"btn " + (main ? "go" : "ghost")} onClick={() => (sheet.value = { kind: "errand", id: errand.id, way: way.n })}>{way.label}</button>;
}

function Kicker({ errand }: { errand: Errand }) {
  return (
    <p class="er-kick">
      <span>{[errand.name, errand.from].filter(Boolean).join(" · ")}</span>
      {errand.due && <b class={errand.due.tone}>{errand.due.text}</b>}
    </p>
  );
}

/** One errand, ready for its tap. `brief`: on Today and under an answer in Ask, without the sentence from the email. */
export function ErrandCard({ errand, brief }: { errand: Errand; brief?: boolean }) {
  const now = known.value[errand.id] ?? errand;
  if (now.ended) {
    return (
      <div class="errand settled">
        <Kicker errand={now} />
        <b class="er-t">{now.title}</b>
        <p class="took"><span>{now.ended.text} · {now.ended.when}</span></p>
      </div>
    );
  }
  const chosen = now.opened ? now.ways.find((w) => w.n === now.opened!.way) : undefined;
  const more = () => (sheet.value = { kind: "errand", id: now.id });
  return (
    <div class={"errand" + (now.opened ? " started" : "")}>
      <Kicker errand={now} />
      <button type="button" class="er-t" onClick={more}>{now.title}</button>
      {now.why && <p class="er-why">{now.why}</p>}
      {!brief && now.quote && <blockquote class="er-quote">{now.quote}</blockquote>}
      {now.facts.length > 0 && <p class="er-facts">{now.facts.map((f) => <span key={f}>{f}</span>)}</p>}
      {now.opened && <p class="er-open"><Icon name="check" /><span>{now.opened.label}. {now.opened.watch}</span></p>}
      {chosen ? (
        <div class="kacts">
          {chosen.steps.map((step) => <StepButton errand={now} way={chosen} step={step} main={!step.taken} said="step" key={step.kind} />)}
          <button type="button" class="btn ghost" onClick={() => void act(now, "done")}>Done</button>
        </div>
      ) : now.ways.length > 0 ? (
        <div class="kacts">{now.ways.map((way, n) => <WayButton errand={now} way={way} main={n === 0} key={way.n} />)}</div>
      ) : (
        <div class="kacts"><button type="button" class="btn go" onClick={() => void act(now, "done")}>Done</button></div>
      )}
      <p class="er-quiet">
        {now.until ? <span>{now.until}</span>
          : <button type="button" onClick={() => void act(now, "later")}>Tomorrow</button>}
        <button type="button" onClick={() => void act(now, "not")}>Not this</button>
        <button type="button" onClick={more}>More</button>
      </p>
    </div>
  );
}

/** Everything about one errand: why, the sentence from the email, each way with its steps and the words drafted. */
export function ErrandSheet({ id, way }: { id: number; way?: number }) {
  const now = known.value[id];
  if (!now) return null;
  const ways = way === undefined ? now.ways : now.ways.filter((w) => w.n === way);
  return (
    <SheetFrame title={now.title} sub={[now.name, now.from, now.due?.text].filter(Boolean).join(" · ")}>
      {now.why && <p class="er-why big">{now.why}</p>}
      {now.quote && <blockquote class="er-quote">{now.quote}</blockquote>}
      {now.facts.length > 0 && <p class="er-facts">{now.facts.map((f) => <span key={f}>{f}</span>)}</p>}
      {ways.map((w) => (
        <section class="er-way" key={w.n}>
          <h2>{w.label}</h2>
          <ol class="er-steps">
            {w.steps.map((step) => (
              <li class={step.taken ? "taken" : ""} key={step.kind}>
                <span class="er-ic"><Icon name={step.taken ? "check" : STEP_ICONS[step.kind]} /></span>
                <div class="er-step">
                  <b>{step.label}</b>
                  {step.kind === "mail" ? <p class="er-draft">{step.detail}</p> : step.detail && <span>{step.detail}</span>}
                  <div class="er-go"><StepButton errand={now} way={w} step={step} main={!step.taken} said="go" /></div>
                </div>
              </li>
            ))}
          </ol>
          <p class="wait-why">{w.steps.some((s) => s.kind === "mail") ? "Nothing is sent until you send it from your mail app."
            : w.steps.some((s) => s.kind === "open") ? "Hub only opens the page. You finish it there."
              : w.steps.some((s) => s.kind === "calendar") ? "Nothing is saved until you save it in Google Calendar."
                : w.steps.some((s) => s.kind === "list") ? "Undo takes it off the list again." : ""}</p>
        </section>
      ))}
      {way !== undefined && now.ways.length > 1 && (
        <button type="button" class="linkish er-other" onClick={() => (sheet.value = { kind: "errand", id })}>Other ways</button>
      )}
      <div class="kacts er-end">
        <button type="button" class="btn ghost" onClick={() => void act(now, "done")}>Done</button>
        {!now.until && <button type="button" class="btn ghost" onClick={() => void act(now, "later")}>Tomorrow</button>}
        <button type="button" class="btn ghost" onClick={() => void act(now, "not")}>Not this</button>
      </div>
      {now.email && (
        <p class="quiet-line"><a href={now.email.href} target={now.email.href.startsWith("http") ? "_blank" : undefined}
          rel="noopener noreferrer">{now.email.label}</a></p>
      )}
    </SheetFrame>
  );
}

function errandsSay(now: ErrandsData): string {
  const n = now.ready.length;
  if (!n) return "Nothing is waiting for you.";
  return `${NUMBERS[n] ?? `${n} things`} ${n === 1 ? "is" : "are"} ready for your tap.`;
}

function errandsSub(now: ErrandsData): string {
  if (now.reading) return now.own ? "Reading your mail and the class guide now." : "Reading the class guide now.";
  if (!now.set_up) return "Hub can’t draft errands yet: its private model key isn’t set up.";
  const looked = now.looked ? `${now.looked.text}.` : "Hub hasn’t looked yet.";
  if (!now.own) return `${looked} Only the class guide’s are here: Hub doesn’t read your own mail.`;
  return `${looked} Nothing is sent, paid or booked until you do it.`;
}

export function Errands() {
  useEffect(() => { void loadErrands(); }, []);
  const now = errands.value;
  if (!now) {
    return (
      <div class="wrap">
        <Top />
        {errandsAway.value
          ? <div class="banner"><b>Can’t reach Hub</b><span>This device is off the home network. Turn Tailscale on and open Errands again.</span></div>
          : <div class="skel"><h1 class="say">Reading errands</h1><p class="sub">One moment</p></div>}
      </div>
    );
  }
  return (
    <div class={"wrap errands" + (errandsAway.value ? " offline" : "")}>
      <Top />
      <button type="button" class="uplink" onClick={() => go("today")}>Today</button>
      {errandsAway.value && <div class="banner"><b>Can’t reach Hub</b><span>Showing errands as they were when Hub last answered.</span></div>}
      {now.looked?.failed && !now.reading && <div class="banner"><b>The last read didn’t finish</b><span>Hub will try again in the morning, or tap Look again.</span></div>}
      <h1 class="say">{errandsSay(now)}</h1>
      <p class="sub">{errandsSub(now)}</p>
      {now.ready.length > 0 && <div class="er-list">{now.ready.map((e) => <ErrandCard errand={e} key={e.id} />)}</div>}
      {now.later.length > 0 && (
        <details class="klist block">
          <summary><h2>For tomorrow</h2><span>{now.later.length === 1 ? "One errand" : `${now.later.length} errands`}</span></summary>
          <div class="er-list">{now.later.map((e) => <ErrandCard errand={e} key={e.id} />)}</div>
        </details>
      )}
      {now.done.length > 0 && (
        <section class="block">
          <h2>Done lately</h2>
          <div class="rows">
            {now.done.map((e) => (
              <div class="row free" key={e.id}>
                <div class="row-main"><b>{e.title || "An errand"}</b><span>{e.ended?.text} · {e.ended?.when}</span></div>
              </div>
            ))}
          </div>
        </section>
      )}
      <div class="btns">
        <button type="button" class="btn ghost" disabled={now.reading} onClick={() => void lookAgain()}>
          {now.reading ? "Looking…" : "Look again"}</button>
      </div>
      {now.own && (
        <section class="block">
          <h2>Replies open in</h2>
          <div class="choices two" role="radiogroup" aria-label="Replies open in">
            {([["mail", "Your mail app"], ["gmail", "Gmail on the web"]] as const).map(([value, name]) => (
              <button type="button" role="radio" aria-checked={now.compose === value} class={"choice" + (now.compose === value ? " on" : "")} key={value}
                onClick={() => void send("settings", { compose: value }).then((a) => { say(a.error ?? a.said ?? "Done"); void loadErrands(); })}>{name}</button>
            ))}
          </div>
        </section>
      )}
      {now.own ? (
        <p class="quiet">Each morning Hub reads the emails that need you, bills, invitations and the class guide’s to-dos, and drafts what
          would finish each one. It never sends, pays, books or orders anything: your tap opens it filled in, and you finish it there.
          It closes an errand by itself when it sees your reply, the event on your calendar or a confirmation email. Your errands
          are yours alone; the class guide’s are everyone’s at home, and one tap of Done finishes them for all.</p>
      ) : (
        <p class="quiet">Each morning Hub reads the class guide’s to-dos and gets each one ready for a tap. They are everyone’s at
          home: when one of you finishes one, it’s done for all. Errands from mail show only to the person whose mail it is.</p>
      )}
    </div>
  );
}

/** On Today, right after what needs you: the few most pressing errands, and the way to the rest. */
export function TodayErrands({ cards, more }: { cards: Errand[]; more: number }) {
  useEffect(() => { seeErrands(cards); }, [cards]);
  if (!cards.length) return null;
  return (
    <section class="block">
      <h2>Ready for your tap</h2>
      <div class="er-list">{cards.map((e) => <ErrandCard errand={e} brief key={e.id} />)}</div>
      <p class="quiet-line">{more > 0 ? `${more === 1 ? "One more errand" : `${more} more errands`}. ` : ""}
        <a href="#errands" onClick={(e) => { e.preventDefault(); go("errands"); }}>All errands</a></p>
    </section>
  );
}

/** Under an answer in Ask: the errands it was about, as cards, as they are now (an answer that finished or set one
 * aside shows it so, and its Undo puts it back). */
export function ErrandsCard({ ids }: { ids: number[] }) {
  useEffect(() => { void loadErrands(); }, [ids.join("."), undone.value.length]);
  const now = errands.value;
  if (!now || !ids.length) return null;           // where replies open changed: no errand to draw, only its Undo
  const cards = ids.map((id) => known.value[id]).filter((e): e is Errand => e !== undefined).slice(0, 4);
  if (!cards.length) return <p class="took">Those errands are finished.</p>;
  return (
    <div class="er-list in-ask">
      {cards.map((e) => <ErrandCard errand={e} brief key={e.id} />)}
      {ids.length > cards.length && <button type="button" class="row-act" onClick={() => go("errands")}>All errands</button>}
    </div>
  );
}

/** Keep errands fresh while someone looks at them, and look for proof when they come back to the app. */
export function watchErrands() {
  const due = () => (view.value === "errands") && !document.hidden && Date.now() - asked > EVERY - 1000;
  window.setInterval(() => { if (due()) void loadErrands(); }, EVERY);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && (view.value === "errands" || view.value === "today") && Date.now() - asked > 3000) fresh();
  });
  view.subscribe((where) => { if (where === "errands" && Date.now() - asked > 5000) void loadErrands(); });
}
