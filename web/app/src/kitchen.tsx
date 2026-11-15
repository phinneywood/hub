// Kitchen: the household's conversation with the kitchen service, the shopping list, and the cart plans and orders
// that wait for a tap. Everything goes through Hub's /kitchen relay, as the older page did; nothing is kept on the
// device. Kitchen's words are data to show: drawn as text, never as HTML.
import { signal } from "@preact/signals";
import { useEffect, useRef, useState } from "preact/hooks";
import { Top } from "./screens";
import { say, view } from "./state";
import type { CartResult, KitchenMessage, Order, Plan, Recipe } from "./types";
import { Icon } from "./ui";

const EVERY = 3000;
const HIDDEN_EVERY = 15000;

export const thread = signal<KitchenMessage[]>([]);
export const working = signal(false);
export const kitchenAway = signal(false);
const plans = signal<Record<string, { status: string; decided_by?: string | null }>>({});
const orders = signal<Record<string, { status: string; decided_by?: string | null; placed?: Order["placed"] }>>({});
const list = signal<string[] | null>(null);
const sending = signal(false);
let last = 0, timer = 0, loaded = false;

async function call<T>(method: string, path: string, body?: unknown): Promise<T & { error?: string }> {
  const answer = await fetch("/kitchen" + path, {
    method, headers: body !== undefined ? { "content-type": "application/json", accept: "application/json" } : { accept: "application/json" },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (answer.status === 502) throw new Error("Kitchen is away");
  return (await answer.json()) as T & { error?: string };
}

async function look() {
  clearTimeout(timer);
  try {
    const found = await call<{ messages?: KitchenMessage[]; plans?: typeof plans.value; orders?: typeof orders.value; busy?: string | null;
      queued?: number }>("GET", `/messages?after=${last}`);
    kitchenAway.value = false;
    const fresh = found.messages ?? [];
    if (fresh.length) {
      thread.value = [...thread.value, ...fresh];
      last = Math.max(last, ...fresh.map((m) => m.id));
      void loadList();
    }
    plans.value = found.plans ?? plans.value;
    orders.value = found.orders ?? orders.value;
    working.value = Boolean(found.busy || found.queued);
    loaded = true;
  } catch {
    kitchenAway.value = true;
  }
  if (view.value === "kitchen") timer = window.setTimeout(() => void look(), document.hidden ? HIDDEN_EVERY : EVERY);
}

async function loadList() {
  try {
    const found = await call<{ text?: string }>("GET", "/list");
    list.value = (found.text ?? "").split("\n")
      .map((line) => line.replace(/<!--.*?-->/g, "").replace(/^\s*(?:[-*+]|\d+\.)\s+(?:\[[ xX]\]\s+)?/, "").replace(/\*\*(.+?)\*\*/g, "$1").trim())
      .filter((line) => line && !line.startsWith("#"));
  } catch {
    /* the list just isn't shown */
  }
}

export async function send(text: string): Promise<boolean> {
  if (sending.value) return false;
  sending.value = true;
  try {
    const found = await call<{ id?: number }>("POST", "/messages", { text });
    if (found.error) { say(found.error); return false; }
    working.value = true;
    void look();
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
    const found = await call<{ ok?: boolean }>("POST", path);
    if (found.error) say(found.error);
  } catch {
    say("Kitchen isn’t answering right now.");
  }
  void look();
}

/** Start following Kitchen while its screen is looked at, and stop when it isn't. */
export function watchKitchen() {
  view.subscribe((where) => { if (where === "kitchen") { void look(); if (list.value === null) void loadList(); } else clearTimeout(timer); });
  document.addEventListener("visibilitychange", () => { if (view.value === "kitchen" && !document.hidden) void look(); });
}

// ---- drawing ----

function clock(at: number): string {
  const when = new Date(at * 1000);
  const time = when.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  return when.toDateString() === new Date().toDateString() ? time : when.toLocaleDateString([], { month: "short", day: "numeric" }) + ", " + time;
}

const money = (n: number) => "$" + n.toFixed(2);
function price(value: unknown): number | null {
  const n = parseFloat(String(value ?? "").replace(/[^0-9.]/g, ""));
  return Number.isNaN(n) ? null : n;
}
const safe = (url?: string) => (url && /^https:\/\//.test(url) ? url : undefined);

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

function RecipeCard({ recipe }: { recipe: Recipe }) {
  const [asked, setAsked] = useState(false);
  const facts = [recipe.active && "Active " + recipe.active, recipe.total && "Total " + recipe.total, recipe.dishes].filter(Boolean);
  const lines = (recipe.recent || recipe.check ? [["Bought recently", recipe.recent], ["Check the shelf", recipe.check]] : [["Have", recipe.have]])
    .concat([["Need", recipe.need]]).filter(([, value]) => value);
  return (
    <div class="kcard">
      {safe(recipe.url) ? <a class="kcard-t" href={safe(recipe.url)} target="_blank" rel="noopener">{recipe.title}</a> : <b class="kcard-t">{recipe.title}</b>}
      {facts.length > 0 && <p class="kfacts">{facts.join(" · ")}</p>}
      {recipe.diet && <p class={"kdiet" + (/swap|check|leave out|without|side/i.test(recipe.diet) ? " warn" : "")}>{recipe.diet}</p>}
      {lines.map(([label, value]) => <p class="kline" key={label}><b>{label}:</b> {value}</p>)}
      <button type="button" class="row-act" disabled={asked}
        onClick={() => { setAsked(true); void send(`Plan the ingredients we still need for “${recipe.title}”.`); }}>Add what’s missing to the cart</button>
    </div>
  );
}

const PLAN_WORDS: Record<string, string> = { approved: "Approved, adding to the cart…", done: "Added to the cart", dismissed: "Dismissed",
  superseded: "Replaced by a newer plan", expired: "Expired", unknown: "Not confirmed: check the cart" };

function PlanCard({ plan }: { plan: Plan }) {
  const now = plans.value[String(plan.id)] ?? plan;
  const [busy, setBusy] = useState(false);
  let total = 0, priced = 0;
  for (const item of plan.items) {
    const each = price(item.price);
    if (each !== null) { total += each * item.qty; priced++; }
  }
  return (
    <div class="kcard">
      <b class="kcard-t">Cart plan</b>
      <ul class="kitems">
        {plan.items.map((item, n) => {
          const each = price(item.price);
          return (
            <li key={n}>
              <span>{safe(item.url) ? <a href={safe(item.url)} target="_blank" rel="noopener">{item.product}</a> : item.product}
                <small>{[item.size, "× " + item.qty].filter(Boolean).join(" · ")}</small></span>
              <span class="kprice">{each !== null ? money(each * item.qty) : ""}</span>
            </li>
          );
        })}
      </ul>
      {priced > 0 && <p class="ktotal"><span>Estimate{priced < plan.items.length ? " (some unpriced)" : ""}</span><span>{money(total)}</span></p>}
      {now.status === "pending" ? (
        <div class="kacts">
          <button type="button" class="btn go" disabled={busy} onClick={() => { setBusy(true); void decide(`/plans/${plan.id}/approve`).finally(() => setBusy(false)); }}>Add to cart</button>
          <button type="button" class="btn" disabled={busy} onClick={() => { setBusy(true); void decide(`/plans/${plan.id}/dismiss`).finally(() => setBusy(false)); }}>Not now</button>
        </div>
      ) : <p class={"kstate" + (now.status === "done" ? " ok" : "")}>{(PLAN_WORDS[now.status] ?? now.status) + (now.decided_by ? " · " + now.decided_by : "")}</p>}
      <p class="kfine">Adds to the shop’s cart only. You check out.</p>
    </div>
  );
}

const LINE_WORDS: Record<string, string> = { added: "Added", already: "Already in cart", unavailable: "Unavailable" };

function ResultCard({ result }: { result: CartResult }) {
  return (
    <div class="kcard">
      <b class="kcard-t">{result.in_cart} of {result.planned} in the cart</b>
      <ul class="kitems">
        {result.lines.map((line, n) => (
          <li key={n} class={line.status === "unavailable" ? "gone" : ""}>
            <span>{line.product}<small>{[LINE_WORDS[line.status], "× " + line.qty, line.was !== null ? "was " + money(line.was) : ""].filter(Boolean).join(" · ")}</small></span>
            <span class="kprice">{line.price !== null && line.status !== "unavailable" ? money(line.price * line.qty) : ""}</span>
          </li>
        ))}
      </ul>
      {result.total != null && <p class="ktotal"><span>Plan items{result.unpriced ? " (some unpriced)" : ""}</span><span>{money(result.total)}</span></p>}
      {result.cart_subtotal != null && <p class="ktotal"><span>Cart subtotal shown</span><span>{money(result.cart_subtotal)}</span></p>}
    </div>
  );
}

const ORDER_WORDS: Record<string, string> = { placing: "Placing the order…", placed: "Order placed", dismissed: "Dismissed", expired: "Expired: ask again",
  superseded: "Replaced by a newer review", stopped: "Not placed", unknown: "Not confirmed: check your orders" };

function OrderCard({ order }: { order: Order }) {
  const now = orders.value[String(order.id)] ?? order;
  const [busy, setBusy] = useState(false);
  const review = order.review;
  const rows: [string, number | null | undefined][] = [["Subtotal", review.subtotal], ["Fees", review.fees], ["Savings", review.savings], ["Tax", review.tax]];
  return (
    <div class="kcard">
      <b class="kcard-t">Order to place</b>
      <p class="kfacts">{[review.items + " items", review.how, review.when].filter(Boolean).join(" · ")}</p>
      {rows.filter(([, value]) => value != null).map(([label, value]) => <p class="ktotal quiet" key={label}><span>{label}</span><span>{money(value!)}</span></p>)}
      <p class="ktotal"><span>Total</span><span>{money(review.total)}</span></p>
      {now.status === "pending" ? (
        <div class="kacts">
          <button type="button" class="btn go" disabled={busy} onClick={() => { setBusy(true); void decide(`/orders/${order.id}/place`).finally(() => setBusy(false)); }}>Place order</button>
          <button type="button" class="btn" disabled={busy} onClick={() => { setBusy(true); void decide(`/orders/${order.id}/dismiss`).finally(() => setBusy(false)); }}>Not now</button>
        </div>
      ) : <p class={"kstate" + (now.status === "placed" ? " ok" : "")}>{(ORDER_WORDS[now.status] ?? now.status) + (now.decided_by ? " · " + now.decided_by : "")}</p>}
    </div>
  );
}

function Message({ m }: { m: KitchenMessage }) {
  if (m.role === "notice") return <li class="kmsg notice">{m.text}</li>;
  const text = m.result ? m.text.split(/\n{2,}/).slice(1).join("\n\n") : m.text;     // a result card says the same numbers
  return (
    <li class={"kmsg " + (m.role === "member" ? "me" : "them")}>
      {text && <div class="kwords"><Words text={text} /></div>}
      {(m.recipes ?? []).map((recipe, n) => <RecipeCard recipe={recipe} key={n} />)}
      {m.plan && <PlanCard plan={m.plan} />}
      {m.result && <ResultCard result={m.result} />}
      {m.order && m.order.review && "total" in m.order.review && <OrderCard order={m.order} />}
      <span class="kmeta">{(m.role === "member" ? m.author || "You" : "Kitchen") + " · " + clock(m.at)}</span>
    </li>
  );
}

const STARTERS = ["Something easy for dinner tonight", "What are we due to restock?", "Add bananas and eggs to the cart"];

function Compose() {
  const [text, setText] = useState("");
  const box = useRef<HTMLTextAreaElement>(null);
  const grow = () => { const el = box.current; if (el) { el.style.height = "auto"; el.style.height = Math.min(el.scrollHeight, 144) + "px"; } };
  const go = async () => {
    const said = text.trim();
    if (!said) return;
    if (await send(said)) { setText(""); requestAnimationFrame(grow); }
  };
  return (
    <form class="composer kcompose" onSubmit={(e) => { e.preventDefault(); void go(); }}>
      <textarea ref={box} class="field" rows={1} maxLength={2000} enterkeyhint="send" placeholder="Message Kitchen" aria-label="Message Kitchen"
        value={text} disabled={sending.value}
        onInput={(e) => { setText(e.currentTarget.value); grow(); }}
        onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); void go(); } }} />
      <button type="submit" class={"send" + (text.trim() ? " on" : "")} aria-label="Send" disabled={!text.trim() || sending.value}><Icon name="up" /></button>
    </form>
  );
}

export function Kitchen() {
  const end = useRef<HTMLDivElement>(null);
  const count = thread.value.length;
  useEffect(() => { if (count) end.current?.scrollIntoView({ block: "end" }); }, [count, working.value]);
  const items = list.value;
  const waiting = Object.values(plans.value).some((p) => p.status === "pending") || Object.values(orders.value).some((o) => o.status === "pending");
  return (
    <div class="wrap kitchen">
      <Top />
      <h1 class="say">{waiting ? "A cart waits for your tap." : working.value ? "Kitchen is working on it." : "Kitchen"}</h1>
      <p class="sub">Dinner ideas, the shopping list and the shop’s cart. It knows your usual products and the household’s diets.</p>
      {kitchenAway.value && <div class="banner"><b>Kitchen isn’t answering</b><span>The kitchen service may be restarting. Hub keeps trying.</span></div>}
      <details class="klist block">
        <summary><h2>Shopping list</h2><span>{items === null ? "" : items.length ? `${items.length} item${items.length === 1 ? "" : "s"}` : "Empty"}</span></summary>
        <ul>{items?.length ? items.map((item, n) => <li key={n}>{item}</li>) : <li class="none">Nothing on the list. Say “add eggs” to start one.</li>}</ul>
      </details>
      {loaded && !count && !working.value && (
        <section class="block">
          <h2>Try</h2>
          <div class="rows">
            {STARTERS.map((words) => (
              <button type="button" class="row free" key={words} onClick={() => void send(words)}>
                <div class="row-main"><b>{words}</b></div><span class="row-act">Ask</span>
              </button>
            ))}
          </div>
        </section>
      )}
      <ol class="kthread" aria-live="polite">{thread.value.map((m) => <Message m={m} key={m.id} />)}</ol>
      {working.value && <p class="took"><span class="work" />Kitchen is working on it. This can take a minute or two.</p>}
      <div ref={end} />
      <Compose />
      <p class="quiet-line">
        <button type="button" class="linkish" onClick={() => void call("POST", "/learn").then((d) => { if (d.error) say(d.error); else say("Reading new orders"); void look(); }, () => say("Kitchen isn’t answering right now."))}>Update from orders</button>
        {" · "}<a href="/kitchen">Notifications and the older page</a>
      </p>
    </div>
  );
}
