// Kitchen: the household's list on its way into the shop's cart. Each thing on the list is shown with the exact
// product Hub picked for it from what the household buys, and where it stands; what needs a person comes first.
// There is no second conversation here: asking is Ask's. Everything comes from Hub (hub/app/kitchen.py and the
// /kitchen relay), nothing is kept on the device, and Kitchen's words are drawn as text, never as HTML.
import { signal } from "@preact/signals";
import { useEffect, useState } from "preact/hooks";
import { get, post } from "./api";
import { Top } from "./screens";
import { go, say, sheet, view } from "./state";
import type { CartResult, Item, KitchenMessage, Option, Order, Plan, Recipe, Shop, ShopAway } from "./types";
import { Icon, SheetFrame, Switch } from "./ui";

const QUICK = 3000;         // how often to look while something is on its way into the cart
const SLOW = 15000;
const NUMBERS = ["No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve"];
const DAY = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" });
const TIME = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" });

export const shop = signal<Shop | ShopAway | null>(null);
const thread = signal<KitchenMessage[]>([]);            // earlier answers, read only when they are opened
const sending = signal(false);
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

/** Words for the kitchen assistant: what a recipe still needs, and anything typed while Kitchen is an older version. */
export async function send(text: string): Promise<boolean> {
  if (sending.value) return false;
  sending.value = true;
  try {
    const found = await relay<{ id?: number }>("POST", "/messages", { text });
    if (found.error) { say(found.error); return false; }
    say("Kitchen is working on it. This can take a minute or two.");
    void loadShop().then(again);
    return true;
  } catch {
    say("Kitchen isn’t answering right now.");
    return false;
  } finally {
    sending.value = false;
  }
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

/** Why this product: the household's own habit, said plainly. */
function why(of: { why: string; times: number; last: string }): string {
  if (of.why === "usual") return "Your usual";
  if (of.why === "chosen") return "You picked this";
  if (of.why === "history") return `Bought ${of.times} times${of.last ? ", last on " + day(of.last) : ""}`;
  if (of.why === "once") return `Bought once${of.last ? ", on " + day(of.last) : ""}`;
  if (of.why === "maybe") return "Something you’ve bought that mentions it";
  return of.why === "shop" ? "From the shop’s search" : "";
}

const them = (n: number) => (n === 1 ? "it" : n === 2 ? "both" : `all ${n}`);
const STANDS: Record<Item["state"], string> = { cart: "In the cart", adding: "Going in", ready: "Waiting", pick: "Needs a pick",
  unavailable: "Not available", unknown: "Check the cart", removing: "Coming out" };

export function kitchenSays(now: Shop): string {
  const picks = now.items.filter((i) => i.state === "pick" || i.state === "unavailable").length;
  const going = now.items.filter((i) => i.state === "adding").length;
  const ready = now.items.filter((i) => i.state === "ready").length;
  const left = now.items.filter((i) => i.left).length;
  if (now.order?.status === "pending") return "An order waits for your tap.";
  if (now.plan) return "A cart waits for your tap.";
  if (picks) return `${count(picks, "thing")} ${picks === 1 ? "needs" : "need"} a pick.`;
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

function ItemRow({ item, plain }: { item: Item; plain?: boolean }) {
  const cls = "row tick item" + (item.state === "cart" ? " on" : "") + (item.state === "adding" || item.state === "removing" ? " going" : "")
    + (item.state === "pick" || item.state === "unavailable" || item.state === "unknown" ? " asks" : "");
  const inside = (
    <>
      <span class="ring"><Icon name="check" /></span>
      <span class="row-main">
        <b>{item.name}{item.qty > 1 && <i class="qty"> × {item.qty}</i>}</b>
        <span>{item.product ? item.product + (item.size ? ", " + item.size : "") : "No product yet"}</span>
      </span>
      <span class="row-q">{STANDS[item.state]}</span>
    </>
  );
  if (plain) return <div class={cls}>{inside}</div>;
  return <button type="button" class={cls} aria-label={`${item.name}: ${STANDS[item.state]}`} onClick={() => (sheet.value = { kind: "item", id: item.id })}>{inside}</button>;
}

function OptionRow({ option, busy, onPick }: { option: Option; busy: boolean; onPick: () => void }) {
  const each = price(option.price);
  return (
    <button type="button" class="opt" disabled={busy} onClick={onPick}>
      <span class="row-main"><b>{option.product}</b><span>{[option.size, why(option)].filter(Boolean).join(". ")}</span></span>
      <span class="opt-pick">{each !== null ? money(each) : "Pick"}</span>
    </button>
  );
}

/** A thing on the list that needs a person: nothing like it was bought before, or the shop doesn't have it. */
function PickBlock({ item }: { item: Item }) {
  const [busy, setBusy] = useState(false);
  const pick = async (o: Option) => { setBusy(true); await act("pick", { id: item.id, product: o.product, size: o.size, url: o.url, price: o.price }); setBusy(false); };
  const gone = item.state === "unavailable";
  return (
    <div class="pickblock">
      <b class="pick-t">{item.name}{item.qty > 1 && <i class="qty"> × {item.qty}</i>}</b>
      <p class="pick-why">{gone ? `The shop doesn’t have ${item.product} right now. Pick another, or take it off the list.`
        : !item.options.length ? "Hub hasn’t bought this before, and is looking in the shop. Choices will show here."
        : item.options.every((o) => o.why === "shop") ? "Hub hasn’t bought this before. These are from the shop’s search. Pick one, and it will know next time."
        : "Hub hasn’t bought this often enough to choose. Pick one, and it will know next time."}</p>
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

/** One thing on the list, opened: its product, why Hub chose it, the other things it could be, and the way off the list. */
export function ItemSheet({ item }: { item: Item }) {
  const [busy, setBusy] = useState(false);
  const still = item.state === "adding" || item.state === "removing";
  const pick = async (o: Option) => {
    setBusy(true);
    if (await act("pick", { id: item.id, product: o.product, size: o.size, url: o.url, price: o.price })) sheet.value = null;
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
      {can && item.options.length > 0 && <h2>{item.product ? "Or one of these" : "Pick one"}</h2>}
      {can && item.options.map((o) => <OptionRow option={o} busy={busy} onPick={() => void pick(o)} key={o.product} />)}
      {can && item.options.length > 0 && <p class="quiet-line">Hub will choose your pick next time too.</p>}
      {item.state === "cart" && <p class="quiet-line">To change the product, take it out and add it again.</p>}
      {item.state === "unknown" && <p class="quiet-line">Hub couldn’t confirm this went in. Read the cart, under The cart, and Hub will know.</p>}
      <div class="btns">
        <button type="button" class="btn risk" disabled={busy || still}
          onClick={() => { setBusy(true); void act("remove", { id: item.id }).then(() => { sheet.value = null; }); }}>
          {item.state === "cart" || item.state === "unknown" ? "Take it out of the cart" : "Take it off the list"}</button>
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

export function RecipeCard({ recipe }: { recipe: Recipe }) {
  const [asked, setAsked] = useState(false);
  const facts = [recipe.active && "Active " + recipe.active, recipe.total && "Total " + recipe.total, recipe.dishes].filter(Boolean);
  const lines = (recipe.recent || recipe.check ? [["Bought recently", recipe.recent], ["Check the shelf", recipe.check]] : [["Have", recipe.have]])
    .concat([["Need", recipe.need]]).filter(([, value]) => value);
  return (
    <div class="kcard">
      {safe(recipe.url) ? <a class="kcard-t" href={safe(recipe.url)} target="_blank" rel="noopener">{recipe.title}</a> : <b class="kcard-t">{recipe.title}</b>}
      {facts.length > 0 && <p class="kfacts">{facts.join(", ")}</p>}
      {recipe.diet && <p class={"kdiet" + (/swap|check|leave out|without|side/i.test(recipe.diet) ? " warn" : "")}>{recipe.diet}</p>}
      {lines.map(([label, value]) => <p class="kline" key={label}><b>{label}:</b> {value}</p>)}
      <button type="button" class="row-act" disabled={asked}
        onClick={() => { setAsked(true); void send(`Plan the ingredients we still need for “${recipe.title}”.`); }}>
        {asked ? "Planning. It will wait in Kitchen" : "Plan what’s missing"}</button>
    </div>
  );
}

const PLAN_WORDS: Record<string, string> = { approved: "Adding to the cart", done: "Added to the cart", dismissed: "Dismissed",
  superseded: "Replaced by a newer plan", expired: "Expired", unknown: "Not confirmed: check the cart" };

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

export function GroceriesCard({ ids, all }: { ids?: number[]; all?: boolean }) {
  const now = useShop();
  if (!now) return null;
  if (!now.ok) return <p class="took">{now.said}</p>;
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

type Statuses = { plans: Record<string, { status: string; decided_by?: string | null }>; orders: Record<string, { status: string; decided_by?: string | null }> };

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
    if (words) void send(words).then((ok) => { if (ok) setText(""); });
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
  return (
    <section class="block">
      <h2>The cart</h2>
      {seen ? (
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
      ) : <p class="quiet">Hub hasn’t read the cart’s own page yet. The list says what Hub put in.</p>}
      <div class="btns tight">
        <button type="button" class="btn ghost" disabled={now.working === "cartread"} onClick={() => void act("read")}>
          {now.working === "cartread" ? "Reading the cart" : seen ? "Read it again" : "Read the cart"}</button>
        {carted && !now.order && <button type="button" class="btn go" disabled={now.working === "review"} onClick={() => void act("order")}>
          {now.working === "review" ? "Reading checkout" : "Check out"}</button>}
      </div>
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
      <p class="quiet-line">{now.history.orders ? `Hub picks from ${now.history.orders} past orders, the newest from ${day(now.history.newest)}.` : "Hub has no past orders to pick from yet."}{" "}
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
  const ready = now.items.filter((i) => i.state === "ready").length;
  const waiting = now.order !== null && now.order.status !== "placed";
  return (
    <div class="wrap kitchen">
      <Top />
      <h1 class="say">{kitchenSays(now)}</h1>
      <p class="sub">{kitchenSub(now)}</p>
      <AddField />
      {(picks.length > 0 || left.length > 0 || now.plan !== null || waiting) && (
        <section class="block">
          <h2>Needs you</h2>
          {waiting && <OrderCard order={now.order!} />}
          {now.plan && <PlanCard plan={now.plan} />}
          {picks.map((i) => <PickBlock item={i} key={i.id} />)}
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
        <section class="block">
          <h2>Ideas</h2>
          <p class="sub">Kitchen’s latest. For more, say “something new for dinner this week” in Ask.</p>
          {now.ideas.recipes.map((recipe, n) => <RecipeCard recipe={recipe} key={n} />)}
        </section>
      )}
      <Earlier />
      <Settings now={now} />
    </div>
  );
}
