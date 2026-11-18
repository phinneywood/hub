// Kitchen: the household's list on its way into the shop's cart. Each thing on the list is shown with the exact
// product Hub picked for it from what the household buys, and where it stands; what needs a person comes first.
// There is no second conversation here: asking is Ask's. Everything comes from Hub (hub/app/kitchen.py and the
// /kitchen relay), nothing is kept on the device, and Kitchen's words are drawn as text, never as HTML.
import { signal } from "@preact/signals";
import { useEffect, useState } from "preact/hooks";
import { get, post } from "./api";
import { Top } from "./screens";
import { go, say, sheet, view } from "./state";
import type { CartResult, Item, KitchenMessage, Option, Order, Plan, Recipe, Shop, ShopAway, Store } from "./types";
import { Icon, SheetFrame, Switch } from "./ui";

const QUICK = 3000;         // how often to look while something is on its way into the cart
const SLOW = 15000;
const LONG = 5 * 60 * 1000;     // after this Kitchen's answer to one message is said to be on its way to Earlier answers
const GIVE_UP = 30 * 60 * 1000; // and after this it is no longer looked for: Kitchen gives up on a turn after 20 minutes
const NUMBERS = ["No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve"];
const DAY = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" });
const TIME = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" });

export const shop = signal<Shop | ShopAway | null>(null);
const thread = signal<KitchenMessage[]>([]);            // earlier answers, read only when they are opened
const sending = signal(false);
const planning = new Map<string, number>();    // a recipe's title: the message that asked Kitchen to plan it, while the app is open
let timer = 0;
let watchers = 0;           // Ask's cards that show the list right now

const moving = (now: Shop) => now.working !== "" || now.items.some((i) => i.state === "adding" || i.state === "removing");

export async function loadShop() {
  try {
    shop.value = await get<Shop | ShopAway>("kitchen");
  } catch {
    shop.value = { ok: false, why: "away", said: "Can’t reach Hub." };
  }
}

/** Keep the list fresh while something that shows it is looked at: quickly while the cart is being filled. */
function again() {
  clearTimeout(timer);
  const now = shop.value;
  timer = window.setTimeout(() => {
    if (!document.hidden && (view.value === "kitchen" || watchers > 0)) void loadShop().then(again);
    else again();
  }, now && now.ok && moving(now) ? QUICK : SLOW);
}

/** One tap on the list: Hub says what happened, and the list is read again. */
export async function act(what: string, body: Record<string, unknown> = {}): Promise<boolean> {
  const answer = await post(`kitchen/${what}`, body);
  say(answer.error ?? answer.said ?? "Done");
  await loadShop();
  again();
  return !answer.error;
}

async function relay<T>(method: string, path: string, body?: unknown): Promise<T & { error?: string }> {
  const answer = await fetch("/kitchen" + path, {
    method, headers: body !== undefined ? { "content-type": "application/json", accept: "application/json" } : { accept: "application/json" },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (answer.status === 502) throw new Error("Kitchen is away");
  return (await answer.json()) as T & { error?: string };
}

/** Words for the kitchen assistant: what a recipe still needs, and anything typed while Kitchen is an older version.
 * Returns what Kitchen gave back (the message's `id`, to follow its answer by), or null when it wasn't sent.
 * `followed`: the caller shows the answer as it comes, so there is nothing to say now. */
export async function send(text: string, followed = false): Promise<{ id?: number } | null> {
  if (sending.value) return null;
  sending.value = true;
  try {
    const found = await relay<{ id?: number }>("POST", "/messages", { text });
    if (found.error) { say(found.error); return null; }
    if (!followed || typeof found.id !== "number") say("Kitchen is working on it. This can take a minute or two.");
    void loadShop().then(again);
    return found;
  } catch {
    say("Kitchen isn’t answering right now.");
    return null;
  } finally {
    sending.value = false;
  }
}

export interface Answer { replies: KitchenMessage[]; statuses: Statuses; state: "working" | "done" | "long" | "down" }

/** Kitchen's answer to one message (`after`, the id Kitchen gave it): what it said next, up to the next thing
 * someone asked. It can take a minute or two, so this keeps looking while Kitchen works, and while a card it brought
 * waits for a tap. It is answered once Kitchen says a message answers this one (`reply_to`), its words or a notice
 * that it couldn't; or, for a Kitchen that doesn't say (an older one, and checkout's answer), once something it
 * said follows and it has nothing left to do. Other notices, such as a plan someone approved, answer nothing. */
export function useAnswer(after: number): Answer {
  const [replies, setReplies] = useState<KitchenMessage[]>([]);
  const [statuses, setStatuses] = useState<Statuses>({ plans: {}, orders: {} });
  const [state, setState] = useState<Answer["state"]>("working");
  useEffect(() => {
    let timer = 0, stopped = false, misses = 0, settled = false;
    const began = Date.now();
    const again = (ms: number) => { timer = window.setTimeout(() => void look(), document.hidden ? 15000 : ms); };
    const look = async () => {
      try {
        const answer = await fetch(`/kitchen/messages?after=${after}`, { headers: { accept: "application/json" } });
        if (!answer.ok) throw new Error("Kitchen is away");
        const found = (await answer.json()) as { messages?: KitchenMessage[]; busy?: unknown; queued?: number } & Partial<Statuses>;
        if (stopped) return;
        const theirs: KitchenMessage[] = [];
        for (const m of found.messages ?? []) {
          if (m.role === "member") break;          // the next thing someone asked: its answer isn't this one's
          theirs.push(m);
        }
        misses = 0;
        setReplies(theirs);
        setStatuses({ plans: found.plans ?? {}, orders: found.orders ?? {} });
        const open = theirs.some((m) => (m.plan && ["pending", "approved"].includes(found.plans?.[String(m.plan.id)]?.status ?? m.plan.status))
          || (m.order && ["pending", "placing"].includes(found.orders?.[String(m.order.id)]?.status ?? m.order.status)));
        const waiting = !theirs.some((m) => m.reply_to === after)
          && (Boolean(found.busy || found.queued) || !theirs.some((m) => m.role === "assistant"));
        if (!waiting && !settled) { settled = true; void loadShop(); }       // what it brought shows on Kitchen at once
        const took = Date.now() - began;
        if (!waiting && !open) setState("done");
        else if (waiting && took > LONG) { setState("long"); if (took < GIVE_UP) again(20000); }     // still looked for, now and then
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
  return { replies, statuses, state };
}

/** What Kitchen said in answer, drawn in full with the cards it brought, and where it stands while it works. */
export function Answered({ answer }: { answer: Answer }) {
  return (
    <div class="kitchen-says">
      <ol class="kthread">{answer.replies.map((m) => <Said m={m} statuses={answer.statuses} key={m.id} />)}</ol>
      {answer.state === "working" && <p class="took"><span class="work" />Kitchen is working on it. This can take a minute or two.</p>}
      {answer.state === "long" && <p class="took">Kitchen is still working. Its answer will be in Kitchen, under Earlier answers.</p>}
      {answer.state === "down" && <p class="took">Kitchen isn’t answering right now.</p>}
      {answer.state === "long" && (view.value !== "kitchen" || sheet.value !== null)
        && <button type="button" class="row-act" onClick={() => go("kitchen")}>Open Kitchen</button>}
    </div>
  );
}

/** Kitchen's answer to what was passed on from Ask. */
export function KitchenReply({ after }: { after: number }) {
  return <Answered answer={useAnswer(after)} />;
}

async function decide(path: string) {
  try {
    const found = await relay<{ ok?: boolean }>("POST", path);
    if (found.error) say(found.error);
  } catch {
    say("Kitchen isn’t answering right now.");
  }
  await loadShop();
  again();
}

/** For a card in Ask: read the list when the card appears, and follow it while the card is on screen. The card
 * draws only once that reading is in: what it is about was changed a moment ago. */
export function useShop(): Shop | ShopAway | null {
  const [fresh, setFresh] = useState(false);
  useEffect(() => {
    watchers++;
    void loadShop().then(() => { setFresh(true); again(); });
    return () => { watchers--; };
  }, []);
  return fresh ? shop.value : null;
}

export function watchKitchen() {
  view.subscribe((where) => { if (where === "kitchen") void loadShop().then(again); });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && (view.value === "kitchen" || watchers > 0)) void loadShop().then(again);
  });
}

// ---- words ----

const money = (n: number) => "$" + n.toFixed(2);
function price(value: unknown): number | null {
  const n = parseFloat(String(value ?? "").replace(/[^0-9.]/g, ""));
  return Number.isNaN(n) ? null : n;
}
const safe = (url?: string) => (url && /^https:\/\//.test(url) ? url : undefined);
const count = (n: number, thing: string) => `${NUMBERS[n] ?? n} ${thing}${n === 1 ? "" : "s"}`;
function day(iso: string): string {
  const when = new Date(iso + "T12:00:00");
  return Number.isNaN(when.getTime()) ? "" : DAY.format(when);
}

/** Why this product: the household's own habit, said plainly. `offered`: it is one of the other choices, not the pick. */
function why(of: { why: string; times: number; last: string }, offered = false): string {
  if (of.why === "usual") return offered ? "One of your usuals" : "Your usual";
  if (of.why === "chosen") return "You picked this";
  if (of.why === "history") return of.times > 1 ? `Bought ${of.times} times${of.last ? ", last on " + day(of.last) : ""}` : "One of your usuals";
  if (of.why === "once") return `Bought once${of.last ? ", on " + day(of.last) : ""}`;
  if (of.why === "maybe") return "Something you’ve bought that mentions it";
  if (of.why === "near") return "Spelled nearly the same";
  return of.why === "shop" ? "From the shop’s search" : "";
}

/** Why a thing waits for a person, from what there is to choose between. */
function pickWhy(item: Item): string {
  const kinds = new Set(item.options.map((o) => o.why));
  if (item.state === "unavailable") {
    return `The shop doesn’t have ${item.product} right now. ` + (item.options.length ? "Pick another for this order, or take it off the list."
      : "Hub is looking for others like it. You can also take it off the list.");
  }
  if (!item.options.length) return "Hub hasn’t bought this before, and is looking in the shop. Choices will show here.";
  if (kinds.has("history") || kinds.has("usual")) return "You buy more than one thing this could be. Pick one, and Hub will know next time.";
  if (kinds.has("once")) return "You have bought this once. Pick it, or another, and Hub will know next time.";
  if (kinds.has("near")) return "Hub doesn’t know this word. Is it one of these, spelled another way?";
  if (kinds.has("maybe")) return "Nothing you’ve bought is called this, though these mention it. The shop’s own choices follow.";
  return "Hub hasn’t bought this before. These are from the shop’s search. Pick one, and it will know next time.";
}

const them = (n: number) => (n === 1 ? "it" : n === 2 ? "both" : `all ${n}`);
const STANDS: Record<Item["state"], string> = { cart: "In the cart", adding: "Going in", ready: "Waiting", pick: "Needs a pick",
  unavailable: "Not available", unknown: "Check the cart", removing: "Coming out" };

export function kitchenSays(now: Shop): string {
  const picks = now.items.filter((i) => i.state === "pick" || i.state === "unavailable").length;
  const going = now.items.filter((i) => i.state === "adding").length;
  const ready = now.items.filter((i) => i.state === "ready").length;
  const left = now.items.filter((i) => i.left).length;
  const unsure = now.items.filter((i) => i.state === "unknown").length;
  if (now.order?.status === "pending") return "An order waits for your tap.";
  if (now.plan) return "A cart waits for your tap.";
  if (picks) return `${count(picks, "thing")} ${picks === 1 ? "needs" : "need"} a pick.`;
  if (unsure) return `${count(unsure, "thing")} to check in the cart.`;
  if (left) return `${count(left, "thing")} came off your list.`;
  if (going) return `${count(going, "thing")} ${going === 1 ? "is" : "are"} going in the cart.`;
  if (ready && !now.auto) return `${count(ready, "thing")} ${ready === 1 ? "is" : "are"} ready for the cart.`;
  if (!now.items.length) return "The list is empty.";
  return now.items.every((i) => i.state === "cart") ? "Everything on the list is in the cart." : `${count(now.items.length, "thing")} on the list.`;
}

function kitchenSub(now: Shop): string {
  const carted = now.items.filter((i) => i.state === "cart").length, all = now.items.length;
  const parts = [];
  if (all && carted < all) parts.push(`${NUMBERS[carted] ?? carted} of ${(NUMBERS[all] ?? String(all)).toLowerCase()} ${carted === 1 ? "is" : "are"} in the cart.`);
  if (now.working === "review") parts.push("Reading checkout now.");
  else if (now.working === "place") parts.push("Placing the order now.");
  else if (now.working === "chat") parts.push("Kitchen is working on an answer.");
  if (now.practice) parts.push("This is a practice cart: nothing in the shop is touched.");
  return parts.join(" ") || "Add what you need. Hub picks the product you buy.";
}

// ---- the list ----

const MAIN = "fredmeyer";
/** A product with its size, unless its name already ends with it (Costco writes the size into the name). */
const sized = (product: string, size: string) => (size && !product.toLowerCase().endsWith(size.toLowerCase()) ? `${product}, ${size}` : product);
/** The household's stores: the main shop alone until Kitchen names another. */
const storesOf = (now: Shop | ShopAway | null): Store[] => (now && now.ok && now.stores?.length ? now.stores : []);
/** The store's name, said only for a thing that goes somewhere other than the main shop. */
function elsewhere(item: { store?: string }): string {
  if (!item.store || item.store === MAIN) return "";
  return storesOf(shop.value).find((s) => s.id === item.store)?.name ?? "";
}

/** Which store's cart a thing goes in, with one tap to move it to another. Only for what isn't in a cart yet. */
function StorePick({ item }: { item: Item }) {
  const stores = storesOf(shop.value);
  const [busy, setBusy] = useState(false);
  if (stores.length < 2 || !["pick", "ready", "unavailable"].includes(item.state)) return null;
  const here = item.store ?? MAIN;
  const move = async (to: string) => { setBusy(true); await act("store", { id: item.id, store: to }); setBusy(false); };
  return (
    <div class="ctl acts store-pick">
      <div class="ctl-name"><b>Store</b><span>Hub chose where you buy it most lately.</span></div>
      <div class="pills" role="group" aria-label={`Which store for ${item.name}`}>
        {stores.map((s) => <button type="button" key={s.id} class={"pill" + (s.id === here ? " go" : "")} aria-pressed={s.id === here}
          disabled={busy || s.id === here} onClick={() => void move(s.id)}>{s.name}</button>)}
      </div>
    </div>
  );
}

function ItemRow({ item, plain }: { item: Item; plain?: boolean }) {
  const covers = item.asked !== null && item.asked !== item.qty && Boolean(item.product);      // a pack that covers the number asked for
  const cls = "row tick item" + (item.state === "cart" ? " on" : "") + (item.state === "adding" || item.state === "removing" ? " going" : "")
    + (item.state === "pick" || item.state === "unavailable" || item.state === "unknown" ? " asks" : "");
  const inside = (
    <>
      <span class="ring"><Icon name="check" /></span>
      <span class="row-main">
        <b>{item.name}{(item.qty > 1 || covers) && <i class="qty"> × {item.qty}{covers ? ` (for ${item.asked})` : ""}</i>}</b>
        <span>{item.product ? sized(item.product, item.size) : "No product yet"}{elsewhere(item) && <i class="store-tag"> · {elsewhere(item)}</i>}</span>
      </span>
      <span class="row-q">{STANDS[item.state]}</span>
    </>
  );
  if (plain) return <div class={cls}>{inside}</div>;
  const read = [item.name + (item.qty > 1 ? `, ${item.qty}` : ""), item.product || "no product yet", elsewhere(item) && "at " + elsewhere(item),
    STANDS[item.state]].filter(Boolean).join(". ");
  return <button type="button" class={cls} aria-label={read} onClick={() => (sheet.value = { kind: "item", id: item.id })}>{inside}</button>;
}

function OptionRow({ option, busy, onPick }: { option: Option; busy: boolean; onPick: () => void }) {
  const each = price(option.price);
  return (
    <button type="button" class="opt" disabled={busy} onClick={onPick}>
      <span class="row-main"><b>{option.product}</b><span>{[option.size, why(option, true)].filter(Boolean).join(". ")}</span></span>
      <span class="opt-pick">{each !== null ? money(each) : "Pick"}</span>
    </button>
  );
}

/** A thing on the list that needs a person: nothing like it was bought before, or the shop doesn't have it. */
function PickBlock({ item }: { item: Item }) {
  const [busy, setBusy] = useState(false);
  const pick = async (o: Option) => {
    setBusy(true); await act("pick", { id: item.id, product: o.product, size: o.size, url: o.url, price: o.price, store: o.store }); setBusy(false);
  };
  return (
    <div class="pickblock">
      <b class="pick-t">{item.name}{item.qty > 1 && <i class="qty"> × {item.qty}</i>}</b>
      <p class="pick-why">{pickWhy(item)}</p>
      {item.options.map((o) => <OptionRow option={o} busy={busy} onPick={() => void pick(o)} key={o.product} />)}
      <button type="button" class="linkish" disabled={busy} onClick={() => void act("remove", { id: item.id })}>Take it off the list</button>
    </div>
  );
}

function LeftRow({ item }: { item: Item }) {
  return (
    <div class="ctl acts">
      <div class="ctl-name"><b>{item.name}</b><span>Ticked off your Groceries list. It is still in the cart.</span></div>
      <div class="pills">
        <button type="button" class="pill" onClick={() => void act("remove", { id: item.id })}>Take out</button>
        <button type="button" class="pill go" onClick={() => void act("keep", { id: item.id })}>Keep</button>
      </div>
    </div>
  );
}

/** How many of a thing: one more, one fewer. What is already in the cart is set to the new number at the next fill. */
function HowMany({ item }: { item: Item }) {
  const [busy, setBusy] = useState(false);
  // What is in another store's cart stays as it went in: Hub never changes that cart.
  const fixed = item.state === "adding" || item.state === "removing" || (item.state === "cart" && elsewhere(item) !== "");
  const set = async (qty: number) => { setBusy(true); await act("qty", { id: item.id, qty, carted: item.state === "cart" }); setBusy(false); };
  const covers = item.asked !== null && item.asked !== item.qty
    ? `You asked for ${item.asked}. ${item.qty === 1 ? "One pack covers" : `${NUMBERS[item.qty] ?? item.qty} packs cover`} that.` : "";
  return (
    <div class="ctl acts howmany">
      <div class="ctl-name"><b>How many</b>{covers && <span>{covers}</span>}</div>
      <div class="stepper" role="group" aria-label={`How many ${item.name}`}>
        <button type="button" aria-label="One fewer" disabled={busy || fixed || item.qty <= 1} onClick={() => void set(item.qty - 1)}>−</button>
        <output aria-live="polite">{item.qty}</output>
        <button type="button" aria-label="One more" disabled={busy || fixed || item.qty >= 24} onClick={() => void set(item.qty + 1)}>+</button>
      </div>
    </div>
  );
}

/** One thing on the list, opened: its product, why Hub chose it, the other things it could be, and the way off the list. */
export function ItemSheet({ item }: { item: Item }) {
  const [busy, setBusy] = useState(false);
  const still = item.state === "adding" || item.state === "removing";
  const pick = async (o: Option) => {
    setBusy(true);
    if (await act("pick", { id: item.id, product: o.product, size: o.size, url: o.url, price: o.price, store: o.store })) sheet.value = null;
    setBusy(false);
  };
  const each = price(item.price);
  const can = item.state === "pick" || item.state === "ready" || item.state === "unavailable" || item.state === "unknown";
  const from = item.by ? `Added by ${item.by}.` : item.source === "reminders" ? "From your Groceries list." : "";
  return (
    <SheetFrame title={item.name + (item.qty > 1 ? ` × ${item.qty}` : "")} sub={[STANDS[item.state] + ".", from].filter(Boolean).join(" ")}>
      {item.product && (
        <div class="chosen">
          <b>{item.product}</b>
          <span>{[item.size, each !== null ? money(each) + " last time" : ""].filter(Boolean).join(", ")}</span>
          <span>{why(item)}.</span>
          {item.about && <span class="chosen-note">{item.about}</span>}
          {item.note && <span class="chosen-note">On the list: “{item.note}”</span>}
        </div>
      )}
      {item.product && <HowMany item={item} />}
      <StorePick item={item} />
      {can && item.options.length > 0 && <h2>{item.product ? "Or one of these" : "Pick one"}</h2>}
      {can && item.options.map((o) => <OptionRow option={o} busy={busy} onPick={() => void pick(o)} key={o.product} />)}
      {can && item.options.length > 0 && <p class="quiet-line">{item.state === "unavailable"
        ? "A pick here is for this order. Your usual stays your usual." : "Hub will choose your pick next time too."}</p>}
      {item.state === "cart" && (elsewhere(item)
        ? <p class="quiet-line">Hub only ever adds to the {elsewhere(item)} cart. Taking it off the list leaves it there.</p>
        : <p class="quiet-line">To change the product, take it out and add it again.</p>)}
      {item.state === "unknown" && (elsewhere(item)
        ? <p class="quiet-line">Hub couldn’t confirm this went in the {elsewhere(item)} cart. Look in that cart on its own site; take it off the list here once you know.</p>
        : <p class="quiet-line">Hub couldn’t confirm this went in. Read the cart, under The cart, and Hub will know.</p>)}
      <div class="btns">
        <button type="button" class="btn risk" disabled={busy || still}
          onClick={() => { setBusy(true); void act("remove", { id: item.id }).then(() => { sheet.value = null; }); }}>
          {(item.state === "cart" || item.state === "unknown") && !elsewhere(item) ? "Take it out of the cart" : "Take it off the list"}</button>
      </div>
    </SheetFrame>
  );
}

function AddField() {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const add = async (e: Event) => {
    e.preventDefault();
    const said = text.trim();
    if (!said || busy) return;
    setBusy(true);
    if (await act("add", { text: said })) setText("");
    setBusy(false);
  };
  return (
    <form class="composer kadd" onSubmit={add}>
      <input class="field" autocomplete="off" autocapitalize="none" enterkeyhint="done" maxLength={300} placeholder="Add to the list"
        aria-label="Add to the list. Commas part several things." value={text} onInput={(e) => setText(e.currentTarget.value)} />
      <button type="submit" class={"send" + (text.trim() && !busy ? " on" : "")} aria-label="Add" disabled={!text.trim() || busy}><Icon name="plus" /></button>
    </form>
  );
}

// ---- cards the kitchen assistant's answers bring: recipes, a cart plan, an order to place ----

/** The way to ask Kitchen for a recipe's cart plan. Once asked, Kitchen's answer shows right under the recipe: the
 * plan with its buttons, or what Kitchen said instead. Only an answer without a plan brings the button back. */
export function RecipeCard({ recipe }: { recipe: Recipe }) {
  const [after, setAfter] = useState<number | null>(() => planning.get(recipe.title) ?? null);     // asked before this card was drawn
  const [asking, setAsking] = useState(false);
  const facts = [recipe.active && "Active " + recipe.active, recipe.total && "Total " + recipe.total, recipe.dishes].filter(Boolean);
  const lines = (recipe.recent || recipe.check ? [["Bought recently", recipe.recent], ["Check the shelf", recipe.check]] : [["Have", recipe.have]])
    .concat([["Need", recipe.need]]).filter(([, value]) => value);
  const plan = async () => {
    setAsking(true);
    const sent = await send(`Plan the ingredients we still need for “${recipe.title}”.`, true);
    setAsking(false);
    if (typeof sent?.id === "number") { planning.set(recipe.title, sent.id); setAfter(sent.id); }
  };
  const button = <button type="button" class="row-act" disabled={asking || sending.value} onClick={() => void plan()}>{asking ? "Planning" : "Plan what’s missing"}</button>;
  return (
    <div class="kcard">
      {safe(recipe.url) ? <a class="kcard-t" href={safe(recipe.url)} target="_blank" rel="noopener">{recipe.title}</a> : <b class="kcard-t">{recipe.title}</b>}
      {facts.length > 0 && <p class="kfacts">{facts.join(", ")}</p>}
      {recipe.diet && <p class={"kdiet" + (/swap|check|leave out|without|side/i.test(recipe.diet) ? " warn" : "")}>{recipe.diet}</p>}
      {lines.map(([label, value]) => <p class="kline" key={label}><b>{label}:</b> {value}</p>)}
      {after === null ? button : <Planned after={after} again={button} key={after} />}
    </div>
  );
}

const OVER = ["superseded", "dismissed", "expired", "stopped"];     // a plan or order that no longer stands

/** Kitchen's answer to a recipe's "Plan what's missing". `again`: the button, for when no plan that still stands
 * came, or Kitchen has been at it so long that asking again is the way on. */
function Planned({ after, again }: { after: number; again: preact.JSX.Element }) {
  const answer = useAnswer(after);
  const { plans, orders } = answer.statuses;
  const planned = answer.replies.some((m) => (m.plan && !OVER.includes(plans[String(m.plan.id)]?.status ?? m.plan.status))
    || (m.order && !OVER.includes(orders[String(m.order.id)]?.status ?? m.order.status)));
  return (
    <>
      <Answered answer={answer} />
      {answer.state !== "working" && !planned && again}
    </>
  );
}

const PLAN_WORDS: Record<string, string> = { approved: "Adding to the cart", done: "Added to the cart", dismissed: "Dismissed",
  superseded: "Replaced by a newer plan", expired: "Expired", unknown: "Not confirmed: check the cart",
  failed: "Not started: Kitchen couldn’t reach the Mac’s own Chrome" };

export function PlanCard({ plan, status }: { plan: Plan; status?: { status: string; decided_by?: string | null } }) {
  const now = status ?? plan;
  const [busy, setBusy] = useState(false);
  let total = 0, priced = 0;
  for (const item of plan.items) {
    const each = price(item.price);
    if (each !== null) { total += each * item.qty; priced++; }
  }
  const tap = (choice: string) => { setBusy(true); void decide(`/plans/${plan.id}/${choice}`).finally(() => setBusy(false)); };
  return (
    <div class="kcard">
      <b class="kcard-t">Cart plan</b>
      <ul class="kitems">
        {plan.items.map((item, n) => {
          const each = price(item.price);
          return (
            <li key={n}>
              <span>{safe(item.url) ? <a href={safe(item.url)} target="_blank" rel="noopener">{item.product}</a> : item.product}
                <small>{[item.size, "× " + item.qty].filter(Boolean).join(", ")}</small></span>
              <span class="kprice">{each !== null ? money(each * item.qty) : ""}</span>
            </li>
          );
        })}
      </ul>
      {priced > 0 && <p class="ktotal"><span>Estimate{priced < plan.items.length ? " (some unpriced)" : ""}</span><span>{money(total)}</span></p>}
      {now.status === "pending" ? (
        <div class="kacts">
          <button type="button" class="btn go" disabled={busy} onClick={() => tap("approve")}>Add to cart</button>
          <button type="button" class="btn ghost" disabled={busy} onClick={() => tap("dismiss")}>Not now</button>
        </div>
      ) : <p class={"kstate" + (now.status === "done" ? " ok" : "")}>{(PLAN_WORDS[now.status] ?? now.status) + (now.decided_by ? ", by " + now.decided_by : "")}</p>}
      <p class="kfine">Adds to the shop’s cart only. Nothing is ordered.</p>
    </div>
  );
}

const ORDER_WORDS: Record<string, string> = { placing: "Placing the order", placed: "Order placed", dismissed: "Dismissed", expired: "Expired: ask again",
  superseded: "Replaced by a newer review", stopped: "Not placed", unknown: "Not confirmed: check your orders" };

export function OrderCard({ order, status }: { order: Order; status?: { status: string; decided_by?: string | null } }) {
  const now = status ?? order;
  const [busy, setBusy] = useState(false);
  const review = order.review;
  const rows: [string, number | null | undefined][] = [["Subtotal", review.subtotal], ["Fees", review.fees], ["Savings", review.savings], ["Tax", review.tax]];
  const tap = (choice: string) => { setBusy(true); void decide(`/orders/${order.id}/${choice}`).finally(() => setBusy(false)); };
  return (
    <div class="kcard">
      <b class="kcard-t">{now.status === "placed" ? "Order placed" : "Order to place"}</b>
      <p class="kfacts">{[count(review.items, "item").toLowerCase(), review.how, review.when].filter(Boolean).join(", ")}</p>
      {rows.filter(([, value]) => value != null && value !== 0).map(([label, value]) => <p class="ktotal quiet" key={label}><span>{label}</span><span>{money(value!)}</span></p>)}
      <p class="ktotal"><span>Total</span><span>{money(review.total)}</span></p>
      {now.status === "pending" ? (
        <div class="kacts">
          <button type="button" class="btn go" disabled={busy} onClick={() => tap("place")}>Place order</button>
          <button type="button" class="btn ghost" disabled={busy} onClick={() => tap("dismiss")}>Not now</button>
        </div>
      ) : now.status !== "placed" && <p class="kstate">{(ORDER_WORDS[now.status] ?? now.status) + (now.decided_by ? ", by " + now.decided_by : "")}</p>}
      {now.status === "pending" && <p class="kfine">Only this tap places the order. It stops if the total or the time has changed.</p>}
    </div>
  );
}

const LINE_WORDS: Record<string, string> = { added: "Added", already: "Already in the cart", unavailable: "Not available", removed: "Taken out",
  updated: "Changed", not_found: "Not found" };

export function ResultCard({ result }: { result: CartResult }) {
  return (
    <div class="kcard">
      <b class="kcard-t">{result.in_cart} of {result.planned} in the cart</b>
      <ul class="kitems">
        {result.lines.map((line, n) => (
          <li key={n} class={line.status === "unavailable" || line.status === "not_found" ? "gone" : ""}>
            <span>{line.product}<small>{[LINE_WORDS[line.status], "× " + line.qty, line.was != null ? "was " + money(line.was) : ""].filter(Boolean).join(", ")}</small></span>
            <span class="kprice">{line.price != null && line.status !== "unavailable" ? money(line.price * line.qty) : ""}</span>
          </li>
        ))}
      </ul>
      {result.cart_subtotal != null && <p class="ktotal"><span>Cart subtotal shown</span><span>{money(result.cart_subtotal)}</span></p>}
    </div>
  );
}

// ---- in Ask: the things a request touched, each with its product and where it stands ----

/** The cart as its own page showed it: what is in it, the subtotal, and when that was. */
function CartLines({ seen }: { seen: NonNullable<Shop["cart"]> }) {
  return (
    <>
      <ul class="kitems">
        {seen.lines.map((line, n) => (
          <li key={n}><span>{line.product}<small>{[line.size, "× " + line.qty].filter(Boolean).join(", ")}</small></span>
            <span class="kprice">{line.price !== null ? money(line.price * line.qty) : ""}</span></li>
        ))}
      </ul>
      {!seen.lines.length && <p class="quiet">The cart is empty.</p>}
      {seen.subtotal !== null && <p class="ktotal"><span>Subtotal</span><span>{money(seen.subtotal)}</span></p>}
      <p class="quiet-line">As the cart’s own page showed it{seen.at ? " at " + clock(seen.at) : ""}{seen.when ? `. ${seen.when}` : ""}.</p>
    </>
  );
}

/** In Ask: the cart, once its page has been read. `since`: when the reading was asked for; an older one isn't it. */
function CartCard({ now, since }: { now: Shop; since?: number }) {
  const seen = now.cart && (since === undefined || (now.cart.at ?? 0) >= since - 5) ? now.cart : null;
  if (!seen) return <p class="took"><span class="work" />Reading the cart’s own page. This can take a minute.</p>;
  return (
    <div class="groceries">
      <CartLines seen={seen} />
      <div class="kacts">
        {seen.lines.length > 0 && !now.order && <button type="button" class="btn go" disabled={now.working === "review"} onClick={() => void act("order")}>
          {now.working === "review" ? "Reading checkout" : "Check out"}</button>}
        <button type="button" class="btn ghost" onClick={() => go("kitchen")}>Open Kitchen</button>
      </div>
    </div>
  );
}

export function GroceriesCard({ ids, all, cart, since }: { ids?: number[]; all?: boolean; cart?: boolean; since?: number }) {
  const now = useShop();
  if (!now) return null;
  if (!now.ok) return <p class="took">{now.said}</p>;
  if (cart) return <CartCard now={now} since={since} />;
  const items = all ? now.items : now.items.filter((i) => ids?.includes(i.id));
  const gone = all ? 0 : (ids?.length ?? 0) - items.length;
  const picks = items.filter((i) => i.state === "pick" || i.state === "unavailable");
  const ready = now.items.filter((i) => i.state === "ready").length;
  return (
    <div class="groceries">
      {items.length > picks.length && <div class="rows">{items.filter((i) => !picks.includes(i)).map((i) => <ItemRow item={i} plain key={i.id} />)}</div>}
      {picks.map((i) => <PickBlock item={i} key={i.id} />)}
      {gone > 0 && <p class="took">{gone === 1 ? "One is off the list now." : `${gone} are off the list now.`}</p>}
      <div class="kacts">
        {ready > 0 && !now.auto && <button type="button" class="btn go" onClick={() => void act("fill")}>Add {them(ready)} to the cart</button>}
        <button type="button" class="btn ghost" onClick={() => go("kitchen")}>Open Kitchen</button>
      </div>
    </div>
  );
}

// ---- earlier answers, and the older Kitchen ----

function clock(at: number): string {
  const when = new Date(at * 1000);
  return when.toDateString() === new Date().toDateString() ? TIME.format(when) : DAY.format(when) + ", " + TIME.format(when);
}

/** Replies are meant to be plain text. Stray Markdown is tidied and links become tappable, as text nodes only. */
function Words({ text }: { text: string }) {
  return (
    <>
      {text.split(/\n{2,}/).filter((para) => para.trim()).map((para, n) => {
        const parts: (string | preact.JSX.Element)[] = [];
        const plain = para.replace(/\*\*(.+?)\*\*/g, "$1").replace(/__(.+?)__/g, "$1").replace(/^#{1,6}\s+/gm, "");
        const found = /\[([^\]]+)\]\((https:\/\/[^\s)]+)\)|(https:\/\/[^\s)]+)/g;
        let at = 0, m: RegExpExecArray | null;
        while ((m = found.exec(plain))) {
          parts.push(plain.slice(at, m.index));
          parts.push(<a href={m[2] ?? m[3]} target="_blank" rel="noopener" key={m.index}>{m[1] ?? m[3]}</a>);
          at = found.lastIndex;
        }
        parts.push(plain.slice(at));
        return <p key={n}>{parts}</p>;
      })}
    </>
  );
}

export type Statuses = { plans: Record<string, { status: string; decided_by?: string | null }>; orders: Record<string, { status: string; decided_by?: string | null }> };

/** One thing Kitchen said, with the cards it brought. */
export function Said({ m, statuses }: { m: KitchenMessage; statuses?: Statuses }) {
  if (m.role === "notice") return <li class="kmsg notice">{m.text}</li>;
  const text = m.result ? m.text.split(/\n{2,}/).slice(1).join("\n\n") : m.text;     // a result card says the same numbers
  return (
    <li class={"kmsg " + (m.role === "member" ? "me" : "them")}>
      {text && <div class="kwords"><Words text={text} /></div>}
      {(m.recipes ?? []).map((recipe, n) => <RecipeCard recipe={recipe} key={n} />)}
      {m.plan && <PlanCard plan={m.plan} status={statuses?.plans[String(m.plan.id)]} />}
      {m.result && <ResultCard result={m.result} />}
      {m.order && m.order.review && "total" in m.order.review && <OrderCard order={m.order} status={statuses?.orders[String(m.order.id)]} />}
      <span class="kmeta">{(m.role === "member" ? m.author || "You" : "Kitchen") + ", " + clock(m.at)}</span>
    </li>
  );
}

/** What Kitchen answered before, for looking back. Read when opened; nothing is asked from here. */
function Earlier({ open }: { open?: boolean }) {
  const [statuses, setStatuses] = useState<Statuses>({ plans: {}, orders: {} });
  const load = async () => {
    try {
      const found = await relay<{ messages?: KitchenMessage[] } & Partial<Statuses>>("GET", "/messages?after=0");
      thread.value = (found.messages ?? []).slice(-30);
      setStatuses({ plans: found.plans ?? {}, orders: found.orders ?? {} });
    } catch {
      /* it just stays as it was */
    }
  };
  useEffect(() => { if (open) void load(); }, []);
  return (
    <details class="klist block" open={open} onToggle={(e) => { if (e.currentTarget.open) void load(); }}>
      <summary><h2>Earlier answers</h2><span>Recipes, cart reports, notices</span></summary>
      <ol class="kthread">{thread.value.map((m) => <Said m={m} statuses={statuses} key={m.id} />)}</ol>
      {!thread.value.length && <p class="quiet">Nothing yet. Ask for a dinner idea and its answer is kept here.</p>}
    </details>
  );
}

/** An older kitchen service, from before it kept the list itself: its conversation still works, in words. */
function Older({ said }: { said: string }) {
  const [text, setText] = useState("");
  const go = (e: Event) => {
    e.preventDefault();
    const words = text.trim();
    if (words) void send(words).then((sent) => { if (sent) setText(""); });
  };
  return (
    <>
      <div class="banner"><b>Kitchen needs its update</b><span>{said} Until then it still answers in words.</span></div>
      <Earlier open />
      <form class="composer kadd" onSubmit={go}>
        <input class="field" maxLength={2000} enterkeyhint="send" placeholder="Message Kitchen" aria-label="Message Kitchen" value={text}
          onInput={(e) => setText(e.currentTarget.value)} />
        <button type="submit" class={"send" + (text.trim() ? " on" : "")} aria-label="Send" disabled={!text.trim() || sending.value}><Icon name="up" /></button>
      </form>
    </>
  );
}

// ---- the screen ----

function Cart({ now }: { now: Shop }) {
  const seen = now.cart;
  const carted = now.items.some((i) => i.state === "cart") || (seen?.lines.length ?? 0) > 0;
  const stores = storesOf(now);
  return (
    <section class="block">
      <h2>{stores.length > 1 ? "Your carts" : "The cart"}</h2>
      {stores.length > 1 && <p class="quiet-line">Read and checkout are for the {stores[0]?.name} cart. Hub only adds to {stores.slice(1).map((s) => s.name).join(" and ")}’s: check out on its own site.</p>}
      {seen ? <CartLines seen={seen} /> : <p class="quiet">Hub hasn’t read the cart’s own page yet. The list says what Hub put in.</p>}
      <div class="btns tight">
        <button type="button" class="btn ghost" disabled={now.working === "cartread"} onClick={() => void act("read")}>
          {now.working === "cartread" ? "Reading the cart" : seen ? "Read it again" : "Read the cart"}</button>
        {carted && !now.order && <button type="button" class="btn go" disabled={now.working === "review"} onClick={() => void act("order")}>
          {now.working === "review" ? "Reading checkout" : "Check out"}</button>}
      </div>
      {stores.length > 1 && <div class="pills store-carts">
        {stores.map((s) => <a class="pill" key={s.id} href={safe(s.cart)} target="_blank" rel="noopener noreferrer">Open the {s.name} cart</a>)}
      </div>}
    </section>
  );
}

function Settings({ now }: { now: Shop }) {
  const r = now.reminders;
  const read = r.state === "on" ? `${r.count === null ? "It was" : count(r.count, "thing") + " on it,"} read ${r.at ? "at " + clock(r.at) : "just now"}.`
    : r.state === "away" ? `Can’t be read right now${r.why ? ": " + r.why : ""}.` : "Not connected yet.";
  const learn = () => relay("POST", "/learn").then((d) => { say(d.error ?? "Reading new orders"); void loadShop(); }, () => say("Kitchen isn’t answering right now."));
  return (
    <section class="block">
      <h2>How it works</h2>
      <div class="rows">
        <div class="ctl two">
          <div class="ctl-name"><b>Fill the cart by itself</b>
            <span>{now.auto ? "A minute after the last thing is added, Hub puts what is ready in the cart." : "Off. What is ready waits for your tap."}</span></div>
          <Switch on={now.auto} label="Fill the cart by itself" onChange={(on) => void act("auto", { on })} />
        </div>
        <div class="ctl two">
          <div class="ctl-name"><b>Groceries reminders</b><span>{r.wanted || r.state !== "off" ? read : "Off. Turn it on and what you add to that list lands here."}</span></div>
          <Switch on={r.wanted || r.state === "on"} label="Read the Groceries reminders list" onChange={(on) => void act("reminders", { on })} />
        </div>
      </div>
      <p class="quiet-line">{now.history.orders ? `Hub picks from ${now.history.orders} past orders, the newest from ${day(now.history.newest)}.` : "Hub has no past orders to pick from yet."}
        {now.history.orders > 0 && now.history.stale ? " Orders since then haven’t been read yet." : ""}{" "}
        <button type="button" class="linkish" onClick={() => void learn()}>Update from orders</button></p>
      <p class="quiet-line"><a href="/kitchen">Notifications and the older page</a></p>
    </section>
  );
}

export function Kitchen() {
  const now = shop.value;
  if (!now) return <div class="wrap kitchen"><Top /><h1 class="say">Reading the list</h1><p class="sub">One moment</p></div>;
  if (!now.ok) {
    return (
      <div class="wrap kitchen">
        <Top />
        <h1 class="say">Kitchen</h1>
        {now.why === "old" ? <Older said={now.said} />
          : <div class="banner"><b>Kitchen isn’t answering</b><span>The kitchen service may be restarting. Hub keeps trying.</span></div>}
      </div>
    );
  }
  const picks = now.items.filter((i) => i.state === "pick" || i.state === "unavailable");
  const left = now.items.filter((i) => i.left);
  const unsure = now.items.filter((i) => i.state === "unknown").length;
  const ready = now.items.filter((i) => i.state === "ready").length;
  const waiting = now.order !== null && now.order.status !== "placed";
  return (
    <div class="wrap kitchen">
      <Top />
      <h1 class="say">{kitchenSays(now)}</h1>
      <p class="sub">{kitchenSub(now)}</p>
      <AddField />
      {(picks.length > 0 || left.length > 0 || unsure > 0 || now.plan !== null || waiting) && (
        <section class="block">
          <h2>Needs you</h2>
          {waiting && <OrderCard order={now.order!} />}
          {now.plan && <PlanCard plan={now.plan} />}
          {picks.map((i) => <PickBlock item={i} key={i.id} />)}
          {unsure > 0 && (
            <div class="ctl acts check">
              <div class="ctl-name"><b>{count(unsure, "thing")} to check</b>
                <span>Hub couldn’t confirm {unsure === 1 ? "it" : "they"} went in the cart. Reading the cart settles it.</span></div>
              <div class="pills"><button type="button" class="pill go" disabled={now.working === "cartread"} onClick={() => void act("read")}>
                {now.working === "cartread" ? "Reading" : "Read the cart"}</button></div>
            </div>
          )}
          {left.map((i) => <LeftRow item={i} key={i.id} />)}
        </section>
      )}
      <section class="block">
        <h2>The list</h2>
        {now.items.length > 0 ? <div class="rows">{now.items.map((i) => <ItemRow item={i} key={i.id} />)}</div>
          : <p class="quiet">Nothing on it. Type above, say “add eggs” in Ask, or add to your Groceries reminders.</p>}
        {ready > 0 && !now.auto && (
          <div class="btns tight"><button type="button" class="btn go" onClick={() => void act("fill")}>Add {them(ready)} to the cart</button></div>
        )}
        {ready > 0 && now.auto && <p class="quiet-line">What is waiting goes in the cart a minute after the last thing was added.</p>}
      </section>
      <Cart now={now} />
      {now.order?.status === "placed" && <section class="block"><h2>Last order</h2><OrderCard order={now.order} /></section>}
      {now.due.length > 0 && (
        <section class="block">
          <h2>Due soon</h2>
          <div class="rows">
            {now.due.map((d) => (
              <div class="row free" key={d.product}>
                <div class="row-main"><b>{d.name}</b><span>Usually every {d.every} days. It has been {d.days}.</span></div>
                <button type="button" class="row-act" aria-label={`Add ${d.name} to the list`} onClick={() => void act("add", { text: d.name })}>Add</button>
              </div>
            ))}
          </div>
        </section>
      )}
      {now.ideas && now.ideas.recipes.length > 0 && (
        <section class="block ideas">
          <h2>Ideas</h2>
          <p class="sub">Kitchen’s latest. For more, say “something new for dinner this week” in Ask.</p>
          {now.ideas.recipes.map((recipe, n) => <RecipeCard recipe={recipe} key={`${n} ${recipe.title}`} />)}
        </section>
      )}
      <Earlier />
      <Settings now={now} />
    </div>
  );
}
