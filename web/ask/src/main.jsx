import "./policy.js";
import { StrictMode, createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  AssistantRuntimeProvider, ComposerPrimitive, MessagePrimitive, ThreadPrimitive, useAuiState,
} from "@assistant-ui/react";
import { useChatRuntime } from "@assistant-ui/react-ai-sdk";
import { MarkdownTextPrimitive } from "@assistant-ui/react-markdown";
import { DefaultChatTransport } from "ai";
import "./ask.css";

const DOWN = "Hub isn’t answering right now.";

const api = (path, method = "GET") =>
  fetch("/ask" + path, { method }).then(async (r) => {
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.error || DOWN);
    return body;
  });

const newId = () => (crypto.randomUUID ? crypto.randomUUID() : "t-" + Date.now() + "-" + Math.random().toString(16).slice(2, 10));
const when = (at) => new Date(at * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });

// The server answers a refused message with {"error": "..."} in the owner's words; anything else gets one fixed line.
const said = (error) => {
  try { return JSON.parse(error.message).error || DOWN; } catch { return DOWN; }
};

let tools = {};

/* Text: Markdown, but links and images the model wrote are never rendered. When the answer comes from the
   kitchen assistant, its reply is the answer: the chat model's own words for that turn are not shown. */
const markdown = { a: ({ children }) => <span>{children}</span>, img: () => null };
const Text = () => {
  const handed = useAuiState((s) => s.message.parts.some((p) => p.type === "tool-call" && p.toolName === "kitchen"));
  return handed ? null : <MarkdownTextPrimitive smooth components={markdown} className="ask-md" />;
};

/* A lookup: a quiet chip that opens into what Hub read and what the model saw, in the catalog's words. */
function Lookup({ toolName, status }) {
  const [open, setOpen] = useState(false);
  const info = tools[toolName] || { name: "Hub" };
  const running = status?.type === "running";
  const failed = status?.type === "incomplete";
  return (
    <div className={"ask-tool" + (open ? " open" : "")}>
      <button type="button" className="ask-chip" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className={"ask-dot" + (running ? " spin" : failed ? " bad" : "")} aria-hidden="true" />
        <span>{running ? "Looking at " : failed ? "Couldn’t read " : "Looked at "}<b>{info.name}</b></span>
        <span className="ask-chev" aria-hidden="true">›</span>
      </button>
      {open && (
        <dl className="ask-tool-body">
          <dt>Read</dt><dd>{info.reads}</dd>
          <dt>Sent to the model</dt><dd>{info.sends}</dd>
          {info.page && <dd className="ask-tool-open"><a href={info.page}>{info.open} ›</a></dd>}
        </dl>
      )}
    </div>
  );
}

/* ---- Cards: something Ask left waiting for the owner's tap. The buttons post to the same signed-in Hub routes
   as the Notes, Recipes and Playlists pages; those routes still check the tap. Asked again on a reopened chat,
   a card shows what became of it. ---- */

const TAP = {
  note: { title: "Save this note?", go: "Save", ended: "saved", post: (id) => `/engine/notes/${id}/save`,
    skip: (id) => `/engine/notes/${id}/skip`, page: ["/engine/notes", "Notes"] },
  recipe: { title: "Save this recipe?", go: "Save", ended: "saved", post: (id) => `/engine/recipes/${id}/save`,
    skip: (id) => `/engine/recipes/${id}/skip`, page: ["/engine/recipes", "Recipes"] },
  playlist: { title: "Make this playlist?", go: "Make playlist", ended: "made", post: (id) => `/house/playlists/make/${id}`,
    skip: (id) => `/house/playlists/skip/${id}`, page: ["/house/playlists", "Playlists"] },
  delete: { title: "Delete this playlist?", go: "Delete", ended: "deleted", danger: true, warn: "It can’t be undone.",
    post: (id) => `/house/playlists/delete/${id}`, page: ["/house/playlists", "Playlists"] },
};
const ENDED = { saved: "Saved", skipped: "Not now", made: "Made", deleted: "Deleted", expired: "Expired: ask again in the chat",
  gone: "No longer waiting" };

const tap = (path) => fetch(path, { method: "POST", headers: { Accept: "application/json" } }).then(async (r) => {
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.error || DOWN);
  return body;
});

function TapCard({ card }) {
  const kind = TAP[card.kind];
  const id = encodeURIComponent(card.id);
  const [state, setState] = useState(null);       // null while checking, then "waiting" or how it ended
  const [said, setSaid] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    api(`/card/${card.kind}/${id}`).then((d) => setState(d.status), () => setState("waiting"));
  }, [card.kind, id]);
  if (!kind) return null;
  const decide = (path, ended) => {
    setBusy(true); setError("");
    tap(path).then((d) => { setSaid(d.said || ""); setState(ended); }, (e) => { setError(e.message); setBusy(false); });
  };
  return (
    <div className="k-card k-plan ask-tap">
      <strong className="k-card-title">{kind.title}</strong>
      <div className="k-line"><b>{card.text}</b></div>
      {card.lines?.length > 0 && <ol className="ask-tap-lines">{card.lines.map((line, i) => <li key={i}>{line}</li>)}</ol>}
      <div className="k-plan-acts">
        {state === "waiting" ? (
          <>
            <button type="button" className={"k-go" + (kind.danger ? " ask-danger" : "")} disabled={busy}
              onClick={() => decide(kind.post(id), kind.ended)}>{kind.go}</button>
            {kind.skip && <button type="button" disabled={busy} onClick={() => decide(kind.skip(id), "skipped")}>Not now</button>}
          </>
        ) : state && (
          <span className={"k-plan-status" + (state === kind.ended ? " ok" : "")}>{said || ENDED[state] || state}</span>
        )}
      </div>
      {error && <p className="ask-fine bad">{error}</p>}
      {state === "waiting" && kind.warn && <span className="k-small">{kind.warn}</span>}
      <a className="k-small" href={kind.page[0]}>Open {kind.page[1]} ›</a>
    </div>
  );
}

// A tool's chip, and its card when it left something waiting for a tap.
const Tool = (props) => (
  <>
    <Lookup {...props} />
    {props.result?.card && props.status?.type !== "running" && <TapCard card={props.result.card} />}
  </>
);

/* “Improve this”: files a short brief of what Hub could and couldn't do on this answer, for the next building
   session. The brief never holds the message or the answer; Hub builds it from the tools it used. */
const ThreadId = createContext("");

function Improve() {
  const threadId = useContext(ThreadId);
  // Which of the owner's messages this answers: the server finds that turn in the saved thread.
  const turn = useAuiState((s) => {
    const all = s.thread.messages, at = all.findIndex((m) => m.id === s.message.id);
    return all.slice(0, at).filter((m) => m.role === "user").length - 1;
  });
  const running = useAuiState((s) => s.message.status?.type === "running");
  const [said, setSaid] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (running) return null;
  const file = () => {
    setBusy(true); setError("");
    fetch(`/ask/threads/${threadId}/improve`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ turn }) })
      .then(async (r) => { const body = await r.json().catch(() => ({})); if (!r.ok) throw new Error(body.error || DOWN); return body; })
      .then((d) => setSaid(d.said), (e) => { setError(e.message); setBusy(false); });
  };
  return (
    <div className="ask-improve">
      {said ? <span className="ask-fine">{said}</span>
        : <button type="button" className="ask-improve-go" disabled={busy} onClick={file}>Improve this</button>}
      {error && <span className="ask-fine bad">{error}</span>}
    </div>
  );
}

/* ---- Kitchen: Ask passes a message on; the reply, cards and buttons come straight from the kitchen service ---- */

const KDOWN = "Cooking and groceries aren’t answering right now. Try again in a minute.";
const kitchen = (path, method = "GET") =>
  fetch("/kitchen" + path, { method }).then(async (r) => {
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.error || KDOWN);
    return body;
  });

// Sends a new message through Ask, as if typed: a card's “Add missing to cart” goes to Kitchen the same way.
const Say = createContext(() => {});

const KLINK = /\[([^\]]+)\]\((https:\/\/[^\s)]+)\)|(https:\/\/[^\s)]+)/g;
function Line({ text }) {
  const out = [];
  let at = 0;
  const line = text.replace(/\*\*(.+?)\*\*/g, "$1").replace(/__(.+?)__/g, "$1").replace(/^#{1,6}\s+/, "");
  for (const m of line.matchAll(KLINK)) {
    out.push(line.slice(at, m.index));
    out.push(<a key={m.index} href={m[2] || m[3]} target="_blank" rel="noopener">{m[1] || m[3]}</a>);
    at = m.index + m[0].length;
  }
  out.push(line.slice(at));
  return out;
}
const Paragraphs = ({ text }) => text.split(/\n{2,}/).filter((p) => p.trim()).map((p, i) => (
  <p key={i}>{p.split("\n").map((l, j) => <span key={j}>{j > 0 && "\n"}<Line text={l} /></span>)}</p>
));

const away = (url, text, cls) => (/^https:\/\//.test(url || "")
  ? <a className={cls} href={url} target="_blank" rel="noopener">{text}</a> : <span className={cls}>{text}</span>);
const money = (v) => { const n = parseFloat(String(v ?? "").replace(/[^0-9.]/g, "")); return isNaN(n) ? null : n; };

function Recipe({ r }) {
  const say = useContext(Say);
  const [asked, setAsked] = useState(false);
  const shelf = r.recent || r.check ? [["Bought recently", r.recent], ["Check your shelf", r.check]] : [["Have", r.have]];
  return (
    <div className="k-card">
      {away(r.url, r.title, "k-card-title")}
      <div className="k-facts">{[r.active && "Active " + r.active, r.total && "Total " + r.total, r.dishes].filter(Boolean).map((f) => <span key={f} className="k-fact">{f}</span>)}</div>
      {r.diet && <div className={"k-diet" + (/swap|check|leave out|without|side/i.test(r.diet) ? " warn" : "")}>{r.diet}</div>}
      {[...shelf, ["Need", r.need]].filter(([, v]) => v).map(([k, v]) => <div key={k} className="k-line"><b>{k}: </b>{v}</div>)}
      <button type="button" className="k-pill" disabled={asked}
        onClick={() => { setAsked(true); say(`Plan the ingredients we still need for “${r.title}”.`); }}>Add missing to cart</button>
    </div>
  );
}

const PLAN_WORDS = { approved: "Approved, adding to the cart…", done: "Added to the cart", dismissed: "Dismissed",
  superseded: "Replaced by a newer plan", expired: "Expired", unknown: "Not confirmed: check the cart" };

const EDIT_WORDS = { approved: "Changing the cart…", done: "Done", unknown: "Not confirmed: check the cart" };
const editLine = (i) => (i.op === "remove" ? "Remove" : i.op === "set" ? (i.qty ? `Set to ${i.qty}` : "Remove") : `Add ${i.qty}`);

// Changes a member asked for in chat: made straight away, so the card only reports.
const Edits = ({ plan }) => (
  <div className="k-card k-plan">
    <strong className="k-card-title">Cart changes</strong>
    <ul className="k-plan-items">
      {plan.items.map((i, n) => <li key={n}><span className="k-what">{away(i.url, i.product)}<small>{editLine(i)}</small></span></li>)}
    </ul>
    <div className="k-plan-acts"><span className={"k-plan-status" + (plan.status === "done" ? " ok" : "")}>{EDIT_WORDS[plan.status] || plan.status}</span></div>
  </div>
);

const ORDER_WORDS = { placing: "Placing the order…", placed: "Order placed", stopped: "Not placed", dismissed: "Not now",
  expired: "Expired: ask again for a fresh total", superseded: "Replaced by a newer check", unknown: "Not confirmed: check your orders" };
const dollars = (v) => (v === null || v === undefined ? null : "$" + v.toFixed(2));

// An order read back from checkout. Nothing is ordered until the tap; the turn that places it checks the
// total and time again first.
function Order({ order, onChange }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const r = order.review;
  const decide = (choice) => {
    setBusy(true); setError("");
    kitchen(`/orders/${order.id}/${choice}`, "POST").then(onChange, (e) => { setError(e.message); setBusy(false); });
  };
  const rows = [["Items", String(r.items)], ["Subtotal", dollars(r.subtotal)], ["Fees", dollars(r.fees)],
    ["Savings", r.savings ? "−" + dollars(r.savings) : null], ["Tax", dollars(r.tax)]].filter(([, v]) => v);
  const placed = order.placed?.status === "placed" ? order.placed : null;
  return (
    <div className="k-card k-plan">
      <strong className="k-card-title">{placed ? "Order placed" : "Order to place"}</strong>
      <div className="k-line"><b>{r.how || "When"}: </b>{r.when}</div>
      {r.where && <div className="k-line"><b>To: </b>{r.where}</div>}
      {r.payment && <div className="k-line"><b>Paying with: </b>{r.payment}</div>}
      {rows.map(([k, v]) => <div key={k} className="k-total"><span>{k}</span><span>{v}</span></div>)}
      <div className="k-total"><span><b>Total</b></span><span><b>{dollars(r.total)}</b></span></div>
      <div className="k-plan-acts">
        {order.status === "pending" ? (
          <>
            <button type="button" className="k-go" disabled={busy} onClick={() => decide("place")}>Place order · {dollars(r.total)}</button>
            <button type="button" disabled={busy} onClick={() => decide("dismiss")}>Not now</button>
          </>
        ) : (
          <span className={"k-plan-status" + (order.status === "placed" ? " ok" : "")}>
            {(ORDER_WORDS[order.status] || order.status) + (placed?.order_number ? " · " + placed.order_number : "")
              + (order.decided_by ? " · " + order.decided_by : "")}
          </span>
        )}
      </div>
      {error && <p className="ask-fine bad">{error}</p>}
      {order.status === "pending" && <span className="k-small">Places this order on fredmeyer.com. It stops if the total or time has changed.</span>}
    </div>
  );
}

function Plan({ plan, onChange }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  let total = 0, priced = 0;
  plan.items.forEach((i) => { const p = money(i.price); if (p !== null) { total += p * i.qty; priced++; } });
  const decide = (choice) => {
    setBusy(true); setError("");
    kitchen(`/plans/${plan.id}/${choice}`, "POST").then(onChange, (e) => { setError(e.message); setBusy(false); });
  };
  return (
    <div className="k-card k-plan">
      <strong className="k-card-title">Cart plan</strong>
      <ul className="k-plan-items">
        {plan.items.map((i, n) => {
          const p = money(i.price);
          return (
            <li key={n}>
              <span className="k-what">{away(i.url, i.product)}<small>{[i.size, "× " + i.qty].filter(Boolean).join(" · ")}</small></span>
              <span className="k-price">{p !== null ? "$" + (p * i.qty).toFixed(2) : ""}</span>
            </li>
          );
        })}
      </ul>
      {priced > 0 && <div className="k-total"><span>Estimate{priced < plan.items.length ? " (some unpriced)" : ""}</span><span>${total.toFixed(2)}</span></div>}
      <div className="k-plan-acts">
        {plan.status === "pending" ? (
          <>
            <button type="button" className="k-go" disabled={busy} onClick={() => decide("approve")}>Add to cart</button>
            <button type="button" disabled={busy} onClick={() => decide("dismiss")}>Not now</button>
          </>
        ) : (
          <span className={"k-plan-status" + (plan.status === "done" ? " ok" : "")}>
            {(PLAN_WORDS[plan.status] || plan.status) + (plan.decided_by ? " · " + plan.decided_by : "")}
          </span>
        )}
      </div>
      {error && <p className="ask-fine bad">{error}</p>}
      <span className="k-small">Adds to your Fred Meyer cart only. You check out.</span>
    </div>
  );
}

const RESULT_WORDS = { added: "Added", already: "Already in cart", removed: "Removed", updated: "Quantity changed",
  unavailable: "Unavailable", not_found: "Not in the cart" };
const Result = ({ r }) => (
  <div className="k-card k-result">
    <strong className="k-card-title">{r.kind === "edit" ? `${r.done} of ${r.planned} changes made` : `${r.in_cart} of ${r.planned} in the cart`}</strong>
    <ul className="k-plan-items">
      {r.lines.map((l, n) => (
        <li key={n} className={["unavailable", "not_found", "removed"].includes(l.status) ? "k-gone" : ""}>
          <span className="k-what"><span>{l.product}</span>
            <small>{[RESULT_WORDS[l.status], l.qty ? "× " + l.qty : "", l.was !== null && l.was !== undefined ? "was $" + l.was.toFixed(2) : ""].filter(Boolean).join(" · ")}</small></span>
          <span className="k-price">{l.price !== null && ["added", "already", "updated"].includes(l.status) ? "$" + (l.price * l.qty).toFixed(2) : ""}</span>
        </li>
      ))}
    </ul>
    {[["Plan items" + (r.unpriced ? " (some unpriced)" : ""), r.total], ["Cart subtotal shown", r.cart_subtotal]]
      .filter(([, v]) => v !== null && v !== undefined)
      .map(([k, v]) => <div key={k} className="k-total"><span>{k}</span><span>${v.toFixed(2)}</span></div>)}
    {away("https://www.fredmeyer.com/cart", "Open the cart", "k-pill")}
  </div>
);

// The part of Kitchen's conversation that answers this message: everything after it, up to the next member message.
function answer(messages) {
  const out = [];
  for (const m of messages) { if (m.role === "member") break; out.push(m); }
  return out;
}
// Keep checking while Kitchen is working, before its first reply, or while a plan or order waits on a tap or a turn.
const stillOpen = (msgs, plans, orders, working) => working || !msgs.some((m) => m.role !== "member")
  || msgs.some((m) => m.plan && ["pending", "approved"].includes(plans[m.plan.id]?.status || m.plan.status))
  || msgs.some((m) => m.order && ["pending", "placing"].includes(orders[m.order.id]?.status || m.order.status));

function KitchenReply({ result, status }) {
  const asked = typeof result?.message === "number" ? result.message : null;
  const [msgs, setMsgs] = useState([]);
  const [plans, setPlans] = useState({});
  const [orders, setOrders] = useState({});
  const [working, setWorking] = useState(false);
  const [down, setDown] = useState(false);
  const [round, setRound] = useState(0);
  useEffect(() => {
    if (asked === null) return undefined;
    let timer, stop = false;
    const poll = () => kitchen(`/messages?after=${asked}`).then((d) => {
      const mine = answer(d.messages || []);
      const busy = !!(d.busy || d.queued);
      setMsgs(mine); setPlans(d.plans || {}); setOrders(d.orders || {}); setWorking(busy); setDown(false);
      return stillOpen(mine, d.plans || {}, d.orders || {}, busy);
    }, () => { setDown(true); return true; }).then((more) => {
      if (more && !stop) timer = setTimeout(poll, document.hidden ? 15000 : 3000);
    });
    poll();
    return () => { stop = true; clearTimeout(timer); };
  }, [asked, round]);

  if (status?.type === "running") return null;   // the thread's own typing dots cover this moment
  if (asked === null) return result?.already ? null : <p className="ask-fine bad">{KDOWN}</p>;
  const replied = msgs.some((m) => m.role === "assistant");
  return (
    <div className="ask-kitchen">
      {msgs.map((m) => (m.role === "notice" ? <p key={m.id} className="ask-kitchen-notice">{m.text}</p> : (
        <div key={m.id} className="ask-kitchen-msg">
          <Paragraphs text={m.result ? m.text.split(/\n{2,}/).slice(1).join("\n\n") : m.text} />
          {(m.recipes || []).map((r, i) => <Recipe key={i} r={r} />)}
          {m.plan && (m.plan.kind === "edit"
            ? <Edits plan={{ ...m.plan, ...(plans[m.plan.id] || {}) }} />
            : <Plan plan={{ ...m.plan, ...(plans[m.plan.id] || {}) }} onChange={() => setRound((n) => n + 1)} />)}
          {m.result && <Result r={m.result} />}
          {m.order && <Order order={{ ...m.order, ...(orders[m.order.id] || {}) }} onChange={() => setRound((n) => n + 1)} />}
        </div>
      )))}
      {(!replied || working) && !down && (
        <p className="ask-kitchen-notice"><span className="ask-dot spin" aria-hidden="true" /> {working ? "Working on it. Store and recipe pages take a minute or two." : "Working on it…"}</p>
      )}
      {down && <p className="ask-fine bad">{KDOWN}</p>}
    </div>
  );
}

/* Citations: links to Hub's own pages, attached by Hub to each lookup. One per page used. */
function Sources() {
  const parts = useAuiState((s) => s.message.parts);
  const running = useAuiState((s) => s.message.status?.type === "running");
  const seen = new Map();
  parts.forEach((p) => {
    if (p.type === "source" && /^\/(?!\/)/.test(p.url || "") && !seen.has(p.url)) seen.set(p.url, p.title || "Hub");
  });
  if (running || !seen.size) return null;
  return (
    <nav className="ask-sources" aria-label="Where this came from">
      {[...seen].map(([url, title], i) => <a key={url} href={url}><sup>{i + 1}</sup> {title} ›</a>)}
    </nav>
  );
}

const UserMessage = () => (
  <MessagePrimitive.Root className="ask-msg user">
    <MessagePrimitive.Parts components={{ Text: ({ text }) => <p>{text}</p> }} />
  </MessagePrimitive.Root>
);

const AssistantMessage = () => (
  <MessagePrimitive.Root className="ask-msg hub">
    <MessagePrimitive.Parts components={{ Text, Source: () => null, tools: { by_name: { kitchen: KitchenReply }, Fallback: Tool } }} />
    <MessagePrimitive.Error><p className="ask-fine bad">Hub couldn’t finish that answer. Nothing was changed. Try again.</p></MessagePrimitive.Error>
    <Sources />
    <Improve />
  </MessagePrimitive.Root>
);

const SUGGESTIONS = ["What needs me today?", "What’s on this week?", "Which emails need me?", "What should we cook tonight?"];

// “Ask about this” on a card or chart opens this page with ?about=<reference>. The reference is read once;
// Hub turns it into the question, which waits in the box for the owner to send or change.
const ABOUT = new URLSearchParams(location.search).get("about");

function Chat({ threadId, initial, about, onDrop, onActivity, onRefused }) {
  const transport = useMemo(() => new DefaultChatTransport({ api: `/ask/threads/${threadId}/chat` }), [threadId]);
  const runtime = useChatRuntime({
    id: threadId, messages: initial, transport,
    onFinish: onActivity,
    onError: (error) => onRefused(said(error)),
  });
  useEffect(() => { if (about) runtime.thread.composer.setText(about.question); }, [about, runtime]);
  const drop = () => { runtime.thread.composer.setText(""); onDrop(); };
  const say = useCallback((text) => runtime.thread.append({ role: "user", content: [{ type: "text", text }] }), [runtime]);
  return (
    <ThreadId.Provider value={threadId}>
    <Say.Provider value={say}>
    <AssistantRuntimeProvider runtime={runtime}>
      <ThreadPrimitive.Root className="ask-thread">
        <ThreadPrimitive.Viewport className="ask-viewport">
          <ThreadPrimitive.Empty>
            <div className="ask-empty">
              <h1>Ask Hub</h1>
              <p>Today’s list, mail, the calendar, the trip and the kitchen, in one place. It reads, and hands cooking and groceries to Kitchen. Only your tap fills the cart.</p>
              <div className="ask-suggest">
                {SUGGESTIONS.map((s) => (
                  <ThreadPrimitive.Suggestion key={s} prompt={s} send asChild><button type="button">{s}</button></ThreadPrimitive.Suggestion>
                ))}
              </div>
            </div>
          </ThreadPrimitive.Empty>
          <ThreadPrimitive.Messages components={{ UserMessage, AssistantMessage }} />
          <ThreadPrimitive.If running><div className="ask-typing" aria-label="Working"><span /><span /><span /></div></ThreadPrimitive.If>
          <div className="ask-spacer" />
        </ThreadPrimitive.Viewport>
        <div className="ask-dock">
          {about && (
            <ThreadPrimitive.Empty>
              <div className="ask-context" role="note">
                <span className="ask-context-text">
                  <span>About <b>{about.label}</b>, from {about.from}</span>
                  {about.looks_at.length > 0 && <small>Hub can look at: {about.looks_at.join(", ")}</small>}
                </span>
                <button type="button" onClick={drop} aria-label="Start without this question">✕</button>
              </div>
            </ThreadPrimitive.Empty>
          )}
          <ThreadPrimitive.ScrollToBottom asChild><button type="button" className="ask-to-bottom" aria-label="Scroll to the latest"><svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" focusable="false"><path d="M12 5v14M6 13l6 6 6-6" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"/></svg></button></ThreadPrimitive.ScrollToBottom>
          {/* The page's own script dims the page on any form submit; this form doesn't navigate. */}
          <ComposerPrimitive.Root className="ask-composer" onSubmit={(e) => e.stopPropagation()}>
            <ComposerPrimitive.Input className="ask-input" rows={1} maxLength={2000} placeholder="Message Hub" enterKeyHint="send" aria-label="Message" />
            <ThreadPrimitive.If running={false}><ComposerPrimitive.Send className="ask-send" aria-label="Send"><svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" focusable="false"><path d="M12 19V5M6 11l6-6 6 6" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"/></svg></ComposerPrimitive.Send></ThreadPrimitive.If>
            <ThreadPrimitive.If running><ComposerPrimitive.Cancel className="ask-send stop" aria-label="Stop"><i /></ComposerPrimitive.Cancel></ThreadPrimitive.If>
          </ComposerPrimitive.Root>
        </div>
      </ThreadPrimitive.Root>
    </AssistantRuntimeProvider>
    </Say.Provider>
    </ThreadId.Provider>
  );
}

function App() {
  const [threadId, setThreadId] = useState(() => (ABOUT ? "" : location.hash.slice(1)) || newId());
  const [about, setAbout] = useState(null);
  const [initial, setInitial] = useState(null);
  const [threads, setThreads] = useState([]);
  const [notice, setNotice] = useState("");
  const [sheet, setSheet] = useState(false);

  const refresh = useCallback(() => {
    api("/threads").then(setThreads, () => {});
    api("/status").then((s) => { tools = s.tools; setNotice(s.unavailable || ""); }, () => setNotice(DOWN));
  }, []);

  useEffect(() => {
    setInitial(null);
    history.replaceState(null, "", location.pathname + "#" + threadId);   // and drops ?about, so a reload starts clean
    Promise.all([api(`/threads/${threadId}/messages`), api("/status")]).then(
      ([messages, s]) => { tools = s.tools; setNotice(s.unavailable || ""); setInitial(messages); },
      () => { setNotice(DOWN); setInitial([]); });
    api("/threads").then(setThreads, () => {});
  }, [threadId]);

  useEffect(() => {
    if (ABOUT) api("/about?ref=" + encodeURIComponent(ABOUT)).then(setAbout, () => {});
  }, []);

  const open = (id) => { setSheet(false); setAbout(null); setThreadId(id); };
  const remove = (id) => api("/threads/" + id, "DELETE").then(() => { if (id === threadId) open(newId()); else refresh(); }, () => {});
  return (
    <>
      <div className="ask-bar">
        <span className="ask-bar-title">{threads.find((t) => t.id === threadId)?.title || "New chat"}</span>
        <button type="button" className="ask-pill" onClick={() => { refresh(); setSheet(true); }}>Chats</button>
        <button type="button" className="ask-pill" onClick={() => open(newId())} aria-label="New chat">＋</button>
      </div>
      {notice && <p className="banner warn ask-notice" role="status">{notice}</p>}
      {initial === null ? <div className="ask-loading" /> : <Chat key={threadId} threadId={threadId} initial={initial} about={about} onDrop={() => setAbout(null)} onActivity={refresh} onRefused={setNotice} />}
      {sheet && (
        <div className="ask-sheet-wrap" onClick={() => setSheet(false)}>
          <div className="ask-sheet" role="dialog" aria-label="Chats" onClick={(e) => e.stopPropagation()}>
            <div className="ask-grab" />
            <h2>Chats</h2>
            <ul>
              {threads.map((t) => (
                <li key={t.id} className={t.id === threadId ? "cur" : ""}>
                  <button type="button" className="ask-open" onClick={() => open(t.id)}><span>{t.title}</span><small>{when(t.updated)}</small></button>
                  <button type="button" className="ask-del" onClick={() => remove(t.id)} aria-label={"Delete chat: " + t.title}>Delete</button>
                </li>
              ))}
              {!threads.length && <li className="none">No saved chats yet.</li>}
            </ul>
            <p className="ask-fine">Chats are kept on this Mac for 30 days, then removed.</p>
          </div>
        </div>
      )}
    </>
  );
}

createRoot(document.getElementById("ask-root")).render(<StrictMode><App /></StrictMode>);
