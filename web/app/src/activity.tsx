// Activity: what happened in the house, newest first, from Home Assistant, Apple Home and Hub's own changes.
// Read from Hub (hub/app/activity.py) when it is looked at and once a minute while it is; Hub's own rows keep Undo.
import { useEffect, useState } from "preact/hooks";
import * as api from "./api";
import { Top } from "./screens";
import { go, me, say } from "./state";
import type { Activity as ActivityData, ActivityKind, ActivitySource, Happening, SourceRead } from "./types";

const KINDS: [ActivityKind, string][] = [["lights", "Lights"], ["security", "Doors and locks"], ["buttons", "Buttons"],
  ["routines", "Automations"], ["music", "Music"], ["motion", "Motion"], ["other", "Everything else"]];
const SHOWN: ActivityKind[] = ["lights", "security", "buttons", "routines", "music"];
const SOURCES: [ActivitySource, string][] = [["ha", "Home Assistant"], ["apple", "Apple Home"], ["hub", "Hub"]];
const KEPT = "hub.activity";
const TIME = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" });
const DAY = new Intl.DateTimeFormat(undefined, { weekday: "long", month: "short", day: "numeric" });

interface Choice { kinds: ActivityKind[]; sources: ActivitySource[] }

function kept(): Choice {
  try {
    const found = JSON.parse(localStorage.getItem(KEPT) ?? "null") as Choice | null;
    if (found && found.kinds.length && found.sources.length) return found;
  } catch {
    /* nothing kept, or a private window */
  }
  return { kinds: SHOWN, sources: SOURCES.map(([s]) => s) };
}

function keep(choice: Choice) {
  try {
    localStorage.setItem(KEPT, JSON.stringify(choice));
  } catch {
    /* the choice just isn't remembered */
  }
}

function dayOf(at: number) {
  const when = new Date(at * 1000);
  const today = new Date();
  const yesterday = new Date(today.getTime() - 86400000);
  return when.toDateString() === today.toDateString() ? "Today"
    : when.toDateString() === yesterday.toDateString() ? "Yesterday" : DAY.format(when);
}

function toggled<T>(list: T[], item: T): T[] {
  const next = list.includes(item) ? list.filter((x) => x !== item) : [...list, item];
  return next.length ? next : list;
}

function from(e: Happening) {
  if (e.source === "hub") return ["Hub" + (e.cause ? ", " + e.cause : ""), e.undone ? "undone" : ""].filter(Boolean).join(" · ");
  const where = SOURCES.find(([s]) => s === e.source)?.[1] ?? "";
  return [e.cause ? `by ${e.cause}` : "", e.also ? `${where} and ${e.also}` : where, e.undone ? "undone" : ""].filter(Boolean).join(" · ");
}

function trouble(name: string, read: SourceRead | undefined) {
  if (!read) return null;
  if (read.off) return `${name} isn’t being recorded: switch on Enable Event Logging in HomeClaw’s settings on the Mac mini.`;
  if (read.trouble) return `${name}: ${read.trouble}`;
  return null;
}

export function Activity() {
  const [choice, setChoice] = useState<Choice>(kept);
  const [found, setFound] = useState<ActivityData | null>(null);
  const [earlier, setEarlier] = useState<Happening[]>([]);
  const [more, setMore] = useState(false);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = () => api.activity(choice.kinds, choice.sources).then((now) => {
    setFound(now); setFailed(false); setEarlier([]); setMore(now.more);
  }, () => setFailed(true));
  useEffect(() => {
    void load();
    const every = window.setInterval(() => { if (!document.hidden) void load(); }, 60000);
    return () => window.clearInterval(every);
  }, [choice]);

  function choose(next: Choice) { keep(next); setChoice(next); }

  async function loadEarlier() {
    const shown = [...(found?.events ?? []), ...earlier];
    const last = shown[shown.length - 1];
    if (!last) return;
    setBusy(true);
    try {
      const page = await api.activity(choice.kinds, choice.sources, last.at);
      setEarlier([...earlier, ...page.events]);
      setMore(page.more);
    } catch {
      say("Can’t reach Hub to read more.");
    }
    setBusy(false);
  }

  async function undo(batch: string) {
    setBusy(true);
    const answer = await api.post("undo/" + batch);
    setBusy(false);
    say(answer.error ?? answer.said ?? "Put back");
    await load();
  }

  const rows = [...(found?.events ?? []), ...earlier];
  const notes = [trouble("Home Assistant", found?.status.ha), choice.sources.includes("apple") ? trouble("Apple Home", found?.status.apple) : null]
    .filter((x): x is string => !!x);
  let day = "";
  return (
    <div class="wrap">
      <Top />
      <button type="button" class="uplink" onClick={() => go("system")}>System</button>
      <h1 class="say">What happened in the house</h1>
      <p class="sub">{failed ? "Can’t reach Hub to read it." : !found ? "Reading…"
        : "From Home Assistant, Apple Home and Hub, newest first. Hub keeps 90 days."}</p>
      <div class="scenes filters" role="group" aria-label="What to show">
        {KINDS.map(([kind, name]) => (
          <button type="button" key={kind} class={"scene" + (choice.kinds.includes(kind) ? " on" : "")}
            aria-pressed={choice.kinds.includes(kind)} onClick={() => choose({ ...choice, kinds: toggled(choice.kinds, kind) })}>{name}</button>
        ))}
      </div>
      <div class="scenes filters" role="group" aria-label="Where from">
        {SOURCES.map(([source, name]) => (
          <button type="button" key={source} class={"scene" + (choice.sources.includes(source) ? " on" : "")}
            aria-pressed={choice.sources.includes(source)} onClick={() => choose({ ...choice, sources: toggled(choice.sources, source) })}>{name}</button>
        ))}
      </div>
      {notes.map((note) => <p class="quiet-line" key={note}>{note}</p>)}
      {found && (
        <section class="block">
          {rows.map((e, i) => {
            const heading = dayOf(e.at) !== day ? (day = dayOf(e.at)) : null;
            return (
              <div key={`${e.source}${e.at}${i}`}>
                {heading && <h2 class="act-day">{heading}</h2>}
                <div class={e.undo ? "ctl two" : "ctl one"}>
                  <div class="ctl-name">
                    <b>{e.name ? `${e.name}${e.room && !e.name.startsWith(e.room) ? ` (${e.room})` : ""} ${e.what}` : e.what}</b>
                    <span>{`${TIME.format(new Date(e.at * 1000))} · ${from(e)}`}</span>
                  </div>
                  {e.undo && e.batch && <button type="button" class="forget" disabled={busy || !me.value.signed_in}
                    onClick={() => void undo(e.batch!)}>Undo</button>}
                </div>
              </div>
            );
          })}
          {!rows.length && <p class="quiet">Nothing recorded for these yet.</p>}
          {more && <button type="button" class="btn ghost act-more" disabled={busy} onClick={() => void loadEarlier()}>Earlier</button>}
        </section>
      )}
      <p class="quiet-line"><a href="#changes" onClick={(ev) => { ev.preventDefault(); go("changes"); }}>Hub’s own changes and playlists waiting</a></p>
    </div>
  );
}
