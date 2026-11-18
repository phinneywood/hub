// The Archive: the family's photos, documents, mail, calendar and ChatGPT history in one search, read on this Mac
// with no model. Data from Hub (hub/app/archive.py), which asks the archive program on the Mac; nothing found is kept
// on the device. Photos open full screen and swipe; documents, conversations, emails and events open in a sheet.
import type { JSX } from "preact";
import { useEffect, useLayoutEffect, useRef, useState } from "preact/hooks";
import * as api from "./api";
import { BestOfMonth } from "./photos";
import { Top } from "./screens";
import { archiveQuery, me, openArchive, say, sheet } from "./state";
import type { ArchiveFound, ArchiveItem, ArchiveLanding, ArchiveSection, EventItem, MailAttachment, MailItem, Opened } from "./types";
import { Icon, SheetFrame, Switch } from "./ui";
import { list } from "./words";

const DAY = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", year: "numeric" });
const LONG_DAY = new Intl.DateTimeFormat(undefined, { weekday: "long", day: "numeric", month: "long", year: "numeric" });
const TIME = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" });
const MONTH = new Intl.DateTimeFormat(undefined, { month: "long" });
const KINDS: Record<string, string> = { pdf: "PDF", word: "Document", text: "Text", sheet: "Spreadsheet", slides: "Slides", image: "Scan" };
const WHO: Record<string, string> = { user: "You", assistant: "ChatGPT", file: "Attached" };
const LIVE_MS = 450;            // a pause in typing this long searches what is typed so far
const SETTLE_MS = 600;          // a photo on screen this long is asked for sharp; one swiped past is not

function onDay(day: string | null | undefined, long = false) {
  if (!day) return "";
  const at = new Date(day + "T12:00:00");
  return isNaN(at.getTime()) ? "" : (long ? LONG_DAY : DAY).format(at);
}

function counted(n: number, one: string, many = one + "s") {
  return `${n.toLocaleString()} ${n === 1 ? one : many}`;
}

function length(seconds: number | null | undefined) {
  if (!seconds) return "";
  const s = Math.round(seconds);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** A snippet with the words that matched marked. The archive puts \x02 and \x03 around them. */
function Marked({ text }: { text: string | undefined }) {
  const parts = (text ?? "").split(/(\x02[^\x03]*\x03)/);
  return <>{parts.map((part, n) => (part.startsWith("\x02") ? <mark key={n}>{part.slice(1, -1)}</mark> : part))}</>;
}

const thumb = (id: string) => `/app/api/archive/thumb/${encodeURIComponent(id)}`;
const big = (id: string) => `/app/api/archive/photo/${encodeURIComponent(id)}?size=1600`;
const sharp = (id: string) => `/app/api/archive/photo/${encodeURIComponent(id)}?size=2048&sharp=1`;     // from iCloud, by way of the Mac

// ---- photos ----

function Tile({ photo, open }: { photo: ArchiveItem; open: () => void }) {
  const said = [onDay(photo.day), photo.place, (photo.people ?? []).join(", ")].filter(Boolean).join(" · ");
  return (
    <button type="button" class="tile" onClick={open} aria-label={(photo.kind === "video" ? "Video, " : "Photo, ") + (said || "no details")}>
      {photo.thumb !== false && <img src={thumb(photo.id)} alt="" loading="lazy" decoding="async"
        onError={(e) => (e.currentTarget.style.visibility = "hidden")} />}
      {photo.kind === "video" && <span class="tile-mark"><Icon name="play" />{length(photo.duration)}</span>}
    </button>
  );
}

export function Grid({ photos, few }: { photos: ArchiveItem[]; few?: boolean }) {
  return (
    <div class={"grid" + (few ? " few" : "")}>
      {photos.map((p, n) => <Tile key={p.id} photo={p} open={() => (sheet.value = { kind: "viewer", photos, at: n })} />)}
    </div>
  );
}

/** One photo at a time, as big as the screen allows: swipe or use the arrow keys for the next, Escape to close. The
 * preview shows at once, the Mac's own best picture over it, and the sharp one from iCloud over that when it comes:
 * all three in the same frame, so nothing moves. The sharp one is asked for only once a photo has stayed on screen
 * a moment, and the next photo's only once this one's has come: swiping through asks Photos for nothing. */
export function Viewer({ photos, at }: { photos: ArchiveItem[]; at: number }) {
  const [n, setN] = useState(at);
  const [shown, setShown] = useState({ near: false, sharp: false });
  const [stayed, setStayed] = useState<string | null>(null);      // the photo that has been on screen long enough
  const start = useRef<number | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const p = photos[n];
  const move = (by: number) => { setN((now) => Math.max(0, Math.min(photos.length - 1, now + by))); setShown({ near: false, sharp: false }); };
  useLayoutEffect(() => {     // before the first paint, so a key pressed the moment it opens is not lost
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") sheet.value = null;
      else if (e.key === "ArrowRight") move(1);
      else if (e.key === "ArrowLeft") move(-1);
    };
    document.addEventListener("keydown", key);
    box.current?.focus();
    return () => document.removeEventListener("keydown", key);
  }, []);
  useEffect(() => {           // the next and the one before are fetched now, so a swipe shows them at once
    for (const near of [photos[n + 1], photos[n - 1]]) if (near) new Image().src = big(near.id);
    const id = photos[n]?.id ?? null;
    const timer = window.setTimeout(() => setStayed(id), SETTLE_MS);
    return () => clearTimeout(timer);
  }, [n]);
  const settled = () => {     // this photo's sharp picture is here: the next one's can be made ready
    setShown((s) => ({ ...s, sharp: true }));
    const next = photos[n + 1];
    if (next && next.kind === "photo") new Image().src = sharp(next.id);
  };
  if (!p) return null;
  const ratio = p.w && p.h ? `${p.w} / ${p.h}` : "4 / 3";
  return (
    <div class="viewer" role="dialog" aria-modal="true" aria-label="Photo" tabIndex={-1} ref={box}
      onPointerDown={(e) => (start.current = e.clientX)}
      onPointerUp={(e) => { if (start.current !== null && Math.abs(e.clientX - start.current) > 50) move(e.clientX < start.current ? 1 : -1); start.current = null; }}>
      <div class="viewer-bar">
        <span>{`${n + 1} of ${photos.length}`}</span>
        <button type="button" class="x" aria-label="Close" onClick={() => (sheet.value = null)}><Icon name="close" /></button>
      </div>
      <div class="viewer-pic" style={{ aspectRatio: ratio } as JSX.CSSProperties}>
        <img class="lo" src={thumb(p.id)} alt="" />
        <img class={"hi" + (shown.near ? " in" : "")} src={big(p.id)} key={"near" + p.id}
          alt={[onDay(p.day, true), p.place].filter(Boolean).join(", ") || "A photo"} onLoad={() => setShown((s) => ({ ...s, near: true }))} />
        {p.kind === "photo" && stayed === p.id && <img class={"hi" + (shown.sharp ? " in" : "")} src={sharp(p.id)} key={"sharp" + p.id}
          alt="" onLoad={settled} />}
      </div>
      <div class="viewer-facts">
        <b>{onDay(p.day, true)}</b>
        {p.place && <span>{p.place}</span>}
        {(p.people?.length ?? 0) > 0 && <span>{list(p.people ?? [])}</span>}
        {p.kind === "video" && <span>{`A video${p.duration ? ", " + length(p.duration) : ""}. It plays in Photos.`}</span>}
      </div>
      <button type="button" class="viewer-go back" aria-label="The one before" disabled={n === 0} onClick={() => move(-1)}><Icon name="up" /></button>
      <button type="button" class="viewer-go next" aria-label="The next one" disabled={n >= photos.length - 1} onClick={() => move(1)}><Icon name="up" /></button>
    </div>
  );
}

// ---- rows for everything else ----

function DocRow({ item }: { item: ArchiveItem }) {
  const where = [item.folder, onDay(item.day), item.pages ? counted(item.pages, "page") : "", item.local === false ? "in iCloud" : ""];
  return (
    <button type="button" class="hit" onClick={() => (sheet.value = { kind: "found", id: item.id, source: "documents", page: item.page, title: item.title })}>
      <span class="hit-pic">{item.thumb ? <img src={thumb(item.id)} alt="" loading="lazy" /> : <i>{KINDS[item.kind] ?? "File"}</i>}</span>
      <span class="hit-main">
        <b>{item.title}</b>
        <span>{where.filter(Boolean).join(" · ")}</span>
        {item.snippet && <span class="hit-snip"><Marked text={item.snippet} /></span>}
      </span>
    </button>
  );
}

function ChatRow({ item }: { item: ArchiveItem }) {
  return (
    <button type="button" class="hit plain" onClick={() => (sheet.value = { kind: "found", id: item.id, source: "chats", page: item.page, title: item.title })}>
      <span class="hit-main">
        <b>{item.title}</b>
        <span>{["ChatGPT", onDay(item.day)].filter(Boolean).join(" · ")}</span>
        {item.snippet && <span class="hit-snip">{item.role && WHO[item.role] ? <em>{WHO[item.role]}: </em> : null}<Marked text={item.snippet} /></span>}
      </span>
    </button>
  );
}

function MailRow({ item }: { item: MailItem }) {
  return (
    <button type="button" class="hit plain" onClick={() => (sheet.value = { kind: "found", id: item.id, source: "mail", title: item.subject })}>
      <span class="hit-main">
        <b>{item.subject || "(No subject)"}</b>
        <span>{[item.from, onDay(item.day)].filter(Boolean).join(" · ")}</span>
        {item.snippet && <span class="hit-snip">{item.snippet}</span>}
      </span>
    </button>
  );
}

function eventWhen(e: { day: string; start: string | null; all_day: boolean }) {
  if (e.all_day || !e.start || !e.start.includes("T")) return onDay(e.day);
  const at = new Date(e.start);
  return isNaN(at.getTime()) ? onDay(e.day) : `${onDay(e.day)}, ${TIME.format(at)}`;
}

function EventRow({ item }: { item: EventItem }) {
  return (
    <button type="button" class="hit plain" onClick={() => (sheet.value = { kind: "found", id: item.id, source: "calendar", title: item.title })}>
      <span class="hit-main"><b>{item.title}</b><span>{[eventWhen(item), item.location].filter(Boolean).join(" · ")}</span></span>
    </button>
  );
}

// ---- searching ----

function understoodLine(found: ArchiveFound) {
  const u = found.understood;
  const bits: string[] = [];
  if (u.kids) bits.push("The kids");
  bits.push(...u.people);
  if (u.after && u.before) {
    const a = new Date(u.after + "T12:00:00"), b = new Date(u.before + "T12:00:00");
    b.setDate(b.getDate() - 1);
    if (u.after.endsWith("-01-01") && u.before.endsWith("-01-01")) {
      bits.push(a.getFullYear() === b.getFullYear() ? String(a.getFullYear()) : `${a.getFullYear()} to ${b.getFullYear()}`);
    } else bits.push(`${DAY.format(a)} to ${DAY.format(b)}`);
  }
  if (u.months.length) bits.push(list(u.months.map((m) => MONTH.format(new Date(2000, m - 1, 1)))));
  if (u.sort === "newest") bits.push("Newest first");
  if (u.sort === "oldest") bits.push("Oldest first");
  return bits;
}

function sentence(found: ArchiveFound, mail: MailItem[] | null): string {
  if (!found.ready) return found.away ?? "The archive isn’t set up on this Mac yet.";
  const u = found.understood;
  const photos = found.photos?.count ?? 0;
  if (found.said && photos && (u.sources.includes("photos") || u.kids || u.people.length)) return found.said;
  const parts = [photos && counted(photos, "photo"), found.documents?.count && counted(found.documents.count, "document"),
    found.calendar?.length && counted(found.calendar.length, "event"), mail?.length && counted(mail.length, "email"),
    found.chats?.count && counted(found.chats.count, "conversation", "conversations")].filter(Boolean) as string[];
  const about = found.about || found.q;
  if (!parts.length) return found.mail && mail === null ? `Looking for “${about}”…` : `Nothing for “${about}”.`;
  return `${about}: ${list(parts)}.`;
}

function Section({ title, count, children, more, busy }: { title: string; count?: number; children: JSX.Element | JSX.Element[];
  more?: () => void; busy?: boolean }) {
  return (
    <section class="block found-block">
      <div class="block-head"><h2>{title}</h2>{count !== undefined && <span class="count">{count.toLocaleString()}</span>}</div>
      {children}
      {more && <button type="button" class="btn ghost more-btn" disabled={busy} onClick={more}>Show more</button>}
    </section>
  );
}

function Results({ found, setFound }: { found: ArchiveFound; setFound: (f: ArchiveFound) => void }) {
  const [mail, setMail] = useState<MailItem[] | null>(null);
  const [mailDown, setMailDown] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let gone = false;
    setMail(null);
    setMailDown(false);
    if (found.mail) {
      api.archiveMail(found.q).then((got) => { if (!gone) setMail(got.mail); }, () => { if (!gone) { setMail([]); setMailDown(true); } });
    }
    return () => { gone = true; };
  }, [found.q]);
  const more = (source: "photos" | "documents" | "chats") => async () => {
    const had = found[source];
    if (!had) return;
    setBusy(true);
    try {
      const next = await api.archiveSearch(found.q, source, had.items.length);
      const got = next[source];
      if (got) setFound({ ...found, [source]: { ...had, items: [...had.items, ...got.items] } as ArchiveSection });
    } catch {
      say("Can’t reach Hub to read more.");
    }
    setBusy(false);
  };
  const bits = understoodLine(found);
  const shown = (s: ArchiveSection | undefined) => s && s.count > 0;
  return (
    <>
      <h1 class="say">{sentence(found, mail)}</h1>
      {bits.length > 0 && <p class="chips" aria-label="What Hub understood">{bits.map((b) => <span key={b}>{b}</span>)}</p>}
      {found.loose && <p class="sub">Nothing had every word, so these have some of them.</p>}
      {shown(found.photos) && (
        <Section title="Photos" count={found.photos!.count} busy={busy}
          more={found.photos!.items.length < found.photos!.count ? more("photos") : undefined}>
          {found.photos!.days ? <p class="quiet-line">{found.photos!.days === 1 ? `On ${onDay(found.photos!.first)}.`
            : `From ${counted(found.photos!.days, "day")}, ${onDay(found.photos!.first)} to ${onDay(found.photos!.last)}.`}</p> : <></>}
          <Grid photos={found.photos!.items} />
        </Section>
      )}
      {shown(found.documents) && (
        <Section title="Documents" count={found.documents!.count} busy={busy}
          more={found.documents!.items.length < found.documents!.count ? more("documents") : undefined}>
          <div class="hits">{found.documents!.items.map((d) => <DocRow key={d.id} item={d} />)}</div>
        </Section>
      )}
      {found.mail && (
        <Section title="Mail" count={mail?.length}>
          {mail === null ? <div class="hits"><p class="hit wait"><span class="dots" role="status" aria-label="Searching Gmail"><i /><i /><i /></span>Searching Gmail</p></div>
            : mailDown ? <p class="quiet-line">Mail isn’t answering right now.</p>
            : mail.length ? <div class="hits">{mail.map((m) => <MailRow key={m.id} item={m} />)}</div>
            : <p class="quiet-line">No email matched.</p>}
        </Section>
      )}
      {(found.calendar?.length ?? 0) > 0 && (
        <Section title="Calendar" count={found.calendar!.length}><div class="hits">{found.calendar!.map((e) => <EventRow key={e.id} item={e} />)}</div></Section>
      )}
      {shown(found.chats) && (
        <Section title="ChatGPT" count={found.chats!.count} busy={busy}
          more={found.chats!.items.length < found.chats!.count ? more("chats") : undefined}>
          <div class="hits">{found.chats!.items.map((c) => <ChatRow key={c.id} item={c} />)}</div>
        </Section>
      )}
    </>
  );
}

// ---- before a search ----

/** What is wrong, in Hub's words: loud when a source can't be read, quiet for what is still to come. */
function Troubles({ landing }: { landing: ArchiveLanding }) {
  if (!landing.trouble?.length) return null;
  return (
    <>{landing.trouble.map((t) => (
      <div class={"banner" + (t.loud ? "" : " calm")} key={t.say} role={t.loud ? "alert" : undefined}><b>{t.say}</b><span>{t.how}</span></div>
    ))}</>
  );
}

function Holds({ landing }: { landing: ArchiveLanding }) {
  const c = landing.counts;
  const years = (x: { first: string | null; last: string | null } | undefined) =>
    x?.first && x.last ? (x.first.slice(0, 4) === x.last.slice(0, 4) ? x.first.slice(0, 4) : `${x.first.slice(0, 4)} to ${x.last.slice(0, 4)}`) : "";
  const rows: [string, string][] = [];
  if (c.photos) rows.push([counted(c.photos.items, "photo and video", "photos and videos"),
    years(c.photos) + (landing.owner ? ", from Photos on this Mac" : ", from the Shared Library")]);
  if (c.documents) {
    const away = c.documents.in_icloud ? `, ${counted(c.documents.in_icloud, "still in iCloud", "still in iCloud")}` : "";
    rows.push([counted(c.documents.items, "document"), "Documents, Desktop, Downloads and iCloud Drive" + away]);
  }
  if (c.chats) rows.push([counted(c.chats.items, "conversation"), "ChatGPT history, " + years(c.chats)]);
  if (landing.google && landing.calendar) {          // only one's own Google account's
    rows.push([counted(landing.calendar.events, "calendar event"), landing.calendar.first
      ? `Since ${onDay(landing.calendar.first)}${landing.calendar.complete ? "" : ", still reading further back"}` : "Read in the background"]);
    rows.push(["Mail", "Searched in Gmail as you ask"]);
  }
  return (
    <section class="block">
      <h2>What the archive holds</h2>
      <div class="rows">{rows.map(([b, s]) => <div class="row free" key={b}><div class="row-main"><b>{b}</b><span>{s}</span></div></div>)}</div>
    </section>
  );
}

export function Years({ years }: { years: { year: number; count?: number; items: ArchiveItem[] }[] }) {
  const now = new Date().getFullYear();
  const row = years.every((y) => y.items.length <= 2);      // a photo or two a year: the years side by side
  const year = (y: { year: number; count?: number; items: ArchiveItem[] }) => (
    <div class="year" key={y.year}>
      <p class="year-name">{`${y.year} · ${counted(now - y.year, "year ago", "years ago")}`}{(y.count ?? 0) > y.items.length ? ` · ${counted(y.count!, "photo")}` : ""}</p>
      <Grid photos={y.items} few />
    </div>
  );
  return row ? <div class="years-row">{years.map(year)}</div> : <>{years.map(year)}</>;
}

function OnThisDay({ landing }: { landing: ArchiveLanding }) {
  if (!landing.onthisday.length) return null;
  return (
    <section class="block">
      <h2>On this day</h2>
      <Years years={landing.onthisday} />
    </section>
  );
}

function Kids({ landing, reload }: { landing: ArchiveLanding; reload: () => void }) {
  const people = landing.people.filter((p) => p.photos >= 20 || p.kid).slice(0, 16);
  if (!people.length) return null;
  const set = async (name: string, kid: boolean) => {
    const answer = await api.post("archive/kid", { name, kid });
    say(answer.error ?? answer.said ?? "Done");
    reload();
  };
  return (
    <section class="block">
      <h2>The kids</h2>
      <p class="quiet-line">“The kids” in a search means these people, and children Photos sees but hasn’t named. Photos guessed from their faces; change it here.</p>
      <div class="rows spaced">
        {people.map((p) => (
          <div class="ctl two" key={p.name}>
            <div class="ctl-name"><b>{p.name}</b><span>{counted(p.photos, "photo")}{p.kid !== p.auto ? " · changed here" : ""}</span></div>
            <Switch on={p.kid} label={`${p.name} is one of the kids`} off={!me.value.signed_in} onChange={(on) => void set(p.name, on)} />
          </div>
        ))}
      </div>
    </section>
  );
}

function MayRead({ landing, reload }: { landing: ArchiveLanding; reload: () => void }) {
  const flip = async (name: "documents" | "chats", on: boolean) => {
    const answer = await api.post("archive/switch", { name, on });
    say(answer.error ?? answer.said ?? "Done");
    reload();
  };
  const owner = landing.owner;
  return (
    <section class="block">
      <h2>What Ask may read</h2>
      <p class="quiet-line">Searching here never uses a model. When you ask Ask, its model is given only what is switched on, and only what is yours.</p>
      <div class="rows spaced">
        {landing.google && <div class="ctl two"><div class="ctl-name"><b>Your mail and calendar</b><span>Yes, except senders with AI switched off</span></div><span class="ctl-say">On</span></div>}
        {owner && <div class="ctl two"><div class="ctl-name"><b>Documents</b><span>{landing.switches.documents ? "The passages that match what you ask, with card and account numbers hidden" : "Off: Ask only says there are matches to open"}</span></div>
          <Switch on={Boolean(landing.switches.documents)} label="Ask may read documents" onChange={(on) => void flip("documents", on)} /></div>}
        {owner && landing.sources.includes("chats") && (
          <div class="ctl two"><div class="ctl-name"><b>ChatGPT history</b><span>{landing.switches.chats ? "The passages that match what you ask, with numbers hidden" : "Off: Ask only says there are matches to open"}</span></div>
            <Switch on={Boolean(landing.switches.chats)} label="Ask may read ChatGPT history" onChange={(on) => void flip("chats", on)} /></div>
        )}
        <div class="ctl two"><div class="ctl-name"><b>Photos</b><span>Never. Hub shows them itself and tells the model nothing about them.</span></div><span class="ctl-say">Never</span></div>
      </div>
    </section>
  );
}

function Landing({ landing, reload }: { landing: ArchiveLanding | null; reload: () => void }) {
  if (!landing) return <><h1 class="say">Reading the archive</h1><p class="sub">One moment</p></>;
  if (!landing.ready) {
    return (
      <>
        <h1 class="say">{landing.away ?? "The archive isn’t set up on this Mac yet."}</h1>
        <p class="sub">{landing.google ? "Your mail and calendar can still be searched. " : ""}Photos{landing.owner ? ", documents and ChatGPT history come" : " come"} once the archive is installed.</p>
        <Troubles landing={landing} />
        <MayRead landing={landing} reload={reload} />
      </>
    );
  }
  const c = landing.counts;
  const said = [c.photos && counted(c.photos.items, "photo"), c.documents && counted(c.documents.items, "document"),
    c.chats && counted(c.chats.items, "conversation")].filter(Boolean) as string[];
  const when = landing.indexed_at ? new Date(landing.indexed_at * 1000) : null;
  return (
    <>
      <h1 class="say">{said.length ? `${list(said)}, on this Mac.` : "The archive is empty so far."}</h1>
      <p class="sub">{when ? `Brought up to date ${when.toDateString() === new Date().toDateString() ? "today" : "on " + DAY.format(when)} at ${TIME.format(when)}. ` : ""}
        Search for people, places, years or words. Nothing you search for goes to a model.</p>
      <Troubles landing={landing} />
      <OnThisDay landing={landing} />
      <BestOfMonth />
      <Holds landing={landing} />
      <Kids landing={landing} reload={reload} />
      <MayRead landing={landing} reload={reload} />
    </>
  );
}

export function Archive() {
  const q = archiveQuery.value;
  const [text, setText] = useState(q);
  const [landing, setLanding] = useState<ArchiveLanding | null>(null);
  const [found, setFound] = useState<ArchiveFound | null>(null);
  const [failed, setFailed] = useState(false);
  const typing = useRef(0);
  const reload = () => api.archive().then((l) => { setLanding(l); setFailed(false); }, () => setFailed(true));
  useEffect(() => { setText(q); }, [q]);
  useEffect(() => {
    let gone = false;
    if (!q) { setFound(null); void reload(); return undefined; }
    api.archiveSearch(q).then((f) => { if (!gone) { setFound(f); setFailed(false); } }, () => { if (!gone) setFailed(true); });
    return () => { gone = true; };
  }, [q]);
  const typed = (value: string) => {
    setText(value);
    clearTimeout(typing.current);
    typing.current = window.setTimeout(() => { if (value.trim().length > 2 || !value.trim()) openArchive(value); }, LIVE_MS);
  };
  return (
    <div class="wrap archive">
      <Top />
      <form class="seek" role="search" onSubmit={(e) => { e.preventDefault(); clearTimeout(typing.current); openArchive(text); }}>
        <input class="field" type="search" enterkeyhint="search" autocomplete="off" placeholder="Search everything"
          aria-label="Search the archive" value={text} onInput={(e) => typed(e.currentTarget.value)} />
        {text && <button type="button" class="seek-clear" aria-label="Clear the search" onClick={() => { setText(""); openArchive(""); }}><Icon name="close" /></button>}
      </form>
      {failed ? <><h1 class="say">Can’t reach Hub.</h1><p class="sub">{me.value.signed_in ? "Try again in a moment." : "Open the archive on your phone or laptop."}</p></>
        : q ? (found && found.q === q ? <Results found={found} setFound={setFound} /> : <><h1 class="say">{`Looking for “${q}”…`}</h1><p class="sub">One moment</p></>)
        : <Landing landing={landing} reload={() => void reload()} />}
    </div>
  );
}

// ---- one thing, opened ----

function DocumentView({ item, page }: { item: Opened; page?: number | null }) {
  const pages = item.pages ?? 0;
  const [n, setN] = useState(Math.max(1, page ?? 1));
  const drawn = item.local !== false && (item.kind === "pdf" || item.kind === "image");
  return (
    <>
      <div class="page-pic" style={{ aspectRatio: item.w && item.h ? `${item.w} / ${item.h}` : "3 / 4" } as JSX.CSSProperties}>
        {drawn ? <img key={n} src={`/app/api/archive/page/${encodeURIComponent(item.id)}/${n}?size=1400`} alt={`Page ${n}`} />
          : item.thumb ? <img src={thumb(item.id)} alt="The first page" /> : <span class="page-none">No preview</span>}
      </div>
      {item.kind === "pdf" && pages > 1 && (
        <div class="pager wide">
          <button type="button" class="linkish" disabled={n <= 1} onClick={() => setN(n - 1)}>Page before</button>
          <span>{`Page ${n} of ${pages}`}</span>
          <button type="button" class="linkish" disabled={n >= pages} onClick={() => setN(n + 1)}>Next page</button>
        </div>
      )}
      <div class="btns tight">
        {item.local !== false ? <a class="btn go" href={`/app/api/archive/file/${encodeURIComponent(item.id)}`} download={item.title}>Get the file</a>
          : <p class="quiet-line">This one is only in iCloud so far; the Mac brings it down on the next night’s reading.</p>}
      </div>
      {(item.pages_text?.length ?? 0) > 0 && (
        <details class="text-pages">
          <summary>The text</summary>
          {item.pages_text!.map((t, k) => <p key={k}>{t.page ? <b>{`Page ${t.page}. `}</b> : null}{t.text}</p>)}
          {item.cut && <p class="quiet-line">The rest is in the file.</p>}
        </details>
      )}
    </>
  );
}

function ChatView({ item, page }: { item: Opened; page?: number | null }) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {           // open at the message that matched
    box.current?.querySelector(".msg.here")?.scrollIntoView({ block: "center" });
  }, [item.id]);
  return (
    <div class="chat" ref={box}>
      {(item.messages ?? []).map((m, k) => (
        <div class={"msg " + m.role + (page && m.n === page ? " here" : "")} key={k}>
          <span class="msg-who">{WHO[m.role] ?? m.role}</span>
          <p>{m.text}</p>
        </div>
      ))}
      {(item.attachments?.length ?? 0) > 0 && <p class="quiet-line">{`Attached: ${(item.attachments as string[]).join(", ")}`}</p>}
    </div>
  );
}

function MailView({ item }: { item: Opened }) {
  const [read, setRead] = useState<Record<string, string>>({});
  const open = async (a: MailAttachment) => {
    try {
      const got = await api.archiveAttachment(item.id, a.part);
      setRead({ ...read, [a.part]: got.text || "Nothing could be read in it." });
    } catch {
      setRead({ ...read, [a.part]: "It couldn’t be read just now." });
    }
  };
  return (
    <>
      <p class="mail-text">{item.text}</p>
      {item.cut && <p class="quiet-line">The rest of the email is in Gmail.</p>}
      {((item.attachments ?? []) as MailAttachment[]).map((a) => (
        <div class="attached" key={a.part}>
          <div class="ctl two">
            <div class="ctl-name"><b>{a.name}</b><span>{a.readable ? "Its text can be read here" : "Open it in Gmail"}</span></div>
            {a.readable && !read[a.part] && <button type="button" class="row-act" onClick={() => void open(a)}>Read</button>}
          </div>
          {read[a.part] && <p class="mail-text">{read[a.part]}</p>}
        </div>
      ))}
    </>
  );
}

export function FoundSheet({ id, source, page, title }: { id: string; source: string; page?: number | null; title?: string }) {
  const [item, setItem] = useState<Opened | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => { api.archiveItem(id).then(setItem, () => setFailed(true)); }, [id]);
  const sub = !item ? (failed ? "It couldn’t be opened just now." : "Opening…")
    : source === "mail" ? [item.from, onDay(item.day)].filter(Boolean).join(" · ")
    : source === "calendar" ? [eventWhen({ day: item.day ?? "", start: item.start ?? null, all_day: Boolean(item.all_day) }), item.location].filter(Boolean).join(" · ")
    : source === "chats" ? ["ChatGPT", onDay(item.day)].filter(Boolean).join(" · ")
    : [item.folder, onDay(item.day)].filter(Boolean).join(" · ");
  return (
    <SheetFrame title={item?.subject ?? item?.title ?? title ?? ""} sub={sub} tall={source === "chats"}>
      {item && source === "documents" && <DocumentView item={item} page={page} />}
      {item && source === "chats" && <ChatView item={item} page={page} />}
      {item && source === "mail" && <MailView item={item} />}
    </SheetFrame>
  );
}

// ---- Ask's cards ----

export function PhotoCard({ title, items, search }: { title?: string; items: ArchiveItem[]; search?: string }) {
  return (
    <div class="acard">
      {title && <p class="acard-t">{title}</p>}
      {items.length > 0 && <Grid photos={items} few />}
      {search && <button type="button" class="row-act" onClick={() => openArchive(search)}>See all in the Archive</button>}
    </div>
  );
}

export interface FoundLine { id: string; source: "photos" | "documents" | "mail" | "calendar" | "chats"; title: string; day: string | null; sub?: string;
  page?: number | null }
const FROM: Record<string, string> = { documents: "Document", mail: "Email", calendar: "Calendar", chats: "ChatGPT" };

export function FoundCard({ items, count, search }: { items: FoundLine[]; count?: number; search?: string }) {
  return (
    <div class="acard">
      <div class="hits">
        {items.map((l) => (
          <button type="button" class="hit plain" key={l.id}
            onClick={() => (sheet.value = { kind: "found", id: l.id, source: l.source, page: l.page, title: l.title })}>
            <span class="hit-main"><b>{l.title}</b><span>{[FROM[l.source], l.sub, onDay(l.day)].filter(Boolean).join(" · ")}</span></span>
          </button>
        ))}
      </div>
      {search && (count ?? 0) > items.length && <button type="button" class="row-act" onClick={() => openArchive(search)}>{`See all ${count}`}</button>}
    </div>
  );
}
