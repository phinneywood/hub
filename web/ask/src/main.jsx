import "./policy.js";
import { StrictMode, useCallback, useEffect, useMemo, useState } from "react";
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

/* Text: Markdown, but links and images the model wrote are never rendered. */
const markdown = { a: ({ children }) => <span>{children}</span>, img: () => null };
const Text = () => <MarkdownTextPrimitive smooth components={markdown} className="ask-md" />;

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
    <MessagePrimitive.Parts components={{ Text, Source: () => null, tools: { Fallback: Lookup } }} />
    <MessagePrimitive.Error><p className="ask-fine bad">Hub couldn’t finish that answer. Nothing was changed. Try again.</p></MessagePrimitive.Error>
    <Sources />
  </MessagePrimitive.Root>
);

const SUGGESTIONS = ["What needs me today?", "What’s on this week?", "Which emails need me?", "How’s the trip plan?"];

function Chat({ threadId, initial, onActivity, onRefused }) {
  const transport = useMemo(() => new DefaultChatTransport({ api: `/ask/threads/${threadId}/chat` }), [threadId]);
  const runtime = useChatRuntime({
    id: threadId, messages: initial, transport,
    onFinish: onActivity,
    onError: (error) => onRefused(said(error)),
  });
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ThreadPrimitive.Root className="ask-thread">
        <ThreadPrimitive.Viewport className="ask-viewport">
          <ThreadPrimitive.Empty>
            <div className="ask-empty">
              <h1>Ask Hub</h1>
              <p>Today’s list, mail, the calendar and the trip, in one place. It only reads: nothing is changed from here.</p>
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
          <ThreadPrimitive.ScrollToBottom asChild><button type="button" className="ask-to-bottom" aria-label="Scroll to the latest">↓</button></ThreadPrimitive.ScrollToBottom>
          {/* The page's own script dims the page on any form submit; this form doesn't navigate. */}
          <ComposerPrimitive.Root className="ask-composer" onSubmit={(e) => e.stopPropagation()}>
            <ComposerPrimitive.Input className="ask-input" rows={1} maxLength={2000} placeholder="Message Hub" enterKeyHint="send" aria-label="Message" />
            <ThreadPrimitive.If running={false}><ComposerPrimitive.Send className="ask-send" aria-label="Send">↑</ComposerPrimitive.Send></ThreadPrimitive.If>
            <ThreadPrimitive.If running><ComposerPrimitive.Cancel className="ask-send stop" aria-label="Stop"><i /></ComposerPrimitive.Cancel></ThreadPrimitive.If>
          </ComposerPrimitive.Root>
        </div>
      </ThreadPrimitive.Root>
    </AssistantRuntimeProvider>
  );
}

function App() {
  const [threadId, setThreadId] = useState(() => location.hash.slice(1) || newId());
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
    history.replaceState(null, "", "#" + threadId);
    Promise.all([api(`/threads/${threadId}/messages`), api("/status")]).then(
      ([messages, s]) => { tools = s.tools; setNotice(s.unavailable || ""); setInitial(messages); },
      () => { setNotice(DOWN); setInitial([]); });
    api("/threads").then(setThreads, () => {});
  }, [threadId]);

  const open = (id) => { setSheet(false); setThreadId(id); };
  const remove = (id) => api("/threads/" + id, "DELETE").then(() => { if (id === threadId) open(newId()); else refresh(); }, () => {});
  return (
    <>
      <div className="ask-bar">
        <span className="ask-bar-title">{threads.find((t) => t.id === threadId)?.title || "New chat"}</span>
        <button type="button" className="ask-pill" onClick={() => { refresh(); setSheet(true); }}>Chats</button>
        <button type="button" className="ask-pill" onClick={() => open(newId())} aria-label="New chat">＋</button>
      </div>
      {notice && <p className="banner warn ask-notice" role="status">{notice}</p>}
      {initial === null ? <div className="ask-loading" /> : <Chat key={threadId} threadId={threadId} initial={initial} onActivity={refresh} onRefused={setNotice} />}
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
