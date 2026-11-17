// Mail: what needs you, the questions you sent that nobody answered, then one line per kind Hub sorted, with
// filing and Undo. Read from Hub (hub/app/mail.py) when the screen is looked at; nothing about mail is kept on the
// device. Rules, levels and the backlog stay on the older Mail page.
import { signal } from "@preact/signals";
import { Top } from "./screens";
import { go, me, say, view } from "./state";
import type { Email, Mail as MailData, NotConnected as Elsewhere, Shelf } from "./types";
import { Icon } from "./ui";

const EVERY = 60_000;
const BUSY_EVERY = 3000;

export const mail = signal<MailData | null>(null);
export const mailAway = signal(false);
export const mailMine = signal(true);     // false: the mail Hub reads is someone else's, and none of it came here
const ticked = signal<string[]>([]);
let asked = 0, timer = 0;

export async function loadMail() {
  asked = Date.now();
  clearTimeout(timer);
  try {
    const answer = await fetch("/app/api/mail", { headers: { accept: "application/json" } });
    if (!answer.ok) throw new Error("Hub is away");
    const found = (await answer.json()) as MailData | Elsewhere;
    mailAway.value = false;
    mailMine.value = !("connected" in found);
    if ("connected" in found) {
      mail.value = null;
      return;
    }
    mail.value = found;
    if (found.notice) say(found.notice);
    const open = new Set([...found.needs, ...found.odd].map((e) => e.id).concat(found.asked.map((a) => a.id)));
    ticked.value = ticked.value.filter((key) => open.has(key));
    if (found.working && view.value === "mail") timer = window.setTimeout(() => void loadMail(), BUSY_EVERY);
  } catch {
    mailAway.value = true;
  }
}

async function tap(what: string, key = ""): Promise<{ ok?: boolean; said?: string; error?: string }> {
  try {
    const answer = await fetch("/app/api/mail/" + what, {
      method: "POST", headers: { "content-type": "application/json", accept: "application/json" }, body: JSON.stringify({ key }),
    });
    return (await answer.json()) as { ok?: boolean; said?: string; error?: string };
  } catch {
    return { error: "Can’t reach Hub. Nothing was changed." };
  }
}

/** A circle: shown done at once, put right if Hub says no. `back` is what Undo asks for, when there is one. */
async function tick(what: string, key: string, back?: string) {
  ticked.value = [...ticked.value, key];
  const answer = await tap(what, key);
  if (answer.error || !answer.ok) {
    ticked.value = ticked.value.filter((k) => k !== key);
    say(answer.error ?? "That didn’t take.");
  } else {
    say(answer.said ?? "Done", null, back ? () => void tap(back, key).then((again) => {
      ticked.value = ticked.value.filter((k) => k !== key);
      say(again.error ?? again.said ?? "Put back");
      void loadMail();
    }) : undefined);
  }
  void loadMail();
}

async function act(what: string, key = "") {
  const answer = await tap(what, key);
  say(answer.error ?? answer.said ?? "Done");
  void loadMail();
}

/** Keep mail fresh while someone is looking at it. */
export function watchMail() {
  const due = () => view.value === "mail" && !document.hidden && Date.now() - asked > EVERY - 1000;
  window.setInterval(() => { if (due()) void loadMail(); }, EVERY);
  document.addEventListener("visibilitychange", () => { if (due()) void loadMail(); });
  view.subscribe((where) => { if (where === "mail" && Date.now() - asked > 5000) void loadMail(); });
}

const NUMBERS = ["No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten"];
const ago = (days: number) => (days < 1 ? "today" : days === 1 ? "yesterday" : `${days} days ago`);
const emails = (n: number) => `${n.toLocaleString()} email${n === 1 ? "" : "s"}`;

function mailSays(now: MailData): string {
  const n = now.needs.length + now.needs_more;
  if (!now.sorted) return "Mail hasn’t been sorted yet.";
  if (n === 0) return now.asked.length ? "Nothing needs you." : "Nothing needs you. Nobody is waiting.";
  return `${NUMBERS[n] ?? n} email${n === 1 ? " needs" : "s need"} you.`;
}

function mailSub(now: MailData): string {
  const parts = [];
  if (now.working === "sorting") parts.push("Sorting the inbox now.");
  else if (now.working === "filing") parts.push("Filing now.");
  else if (now.synced) parts.push(`Sorted ${now.synced}.`);
  if (now.sorted_today) parts.push(`Today: ${now.sorted_today}.`);
  return parts.join(" ");
}

function EmailRow({ email, waiting, what, back, label }: { email: Email; waiting: number; what: string; back?: string; label: string }) {
  const done = ticked.value.includes(email.id);
  const waited = email.days >= waiting;
  const why = email.step || email.subject;
  return (
    <div class={"row tick need" + (done ? " on" : "") + (waited && !done ? " warn" : "")}>
      <button type="button" class="tickbtn" aria-label={`${label}: ${email.who}`} disabled={done} onClick={() => void tick(what, email.id, back)}>
        <span class="ring"><Icon name="check" /></span>
      </button>
      <div class="row-main">
        <b>{email.who}</b>
        <span>{waited ? `Waiting ${email.days} days. ` : ""}{why}</span>
        {email.step && email.subject && <small class="row-sm">{email.subject} · {ago(email.days)}</small>}
      </div>
      {!done && <a class="row-act" href={email.find}>Find</a>}
    </div>
  );
}

function ShelfRow({ shelf, busy, paused }: { shelf: Shelf; busy: boolean; paused: boolean }) {
  const can = shelf.level !== "suggest" && shelf.ready > 0 && !busy && !paused;
  const left = shelf.count ? `${emails(shelf.count)} in the inbox` : "None in the inbox";
  const how = shelf.level === "suggest" ? "Left in the inbox" : shelf.level === "auto" ? "Filed each morning" : "Filed when you tap";
  return (
    <div class="shelf">
      <div class="row free">
        <div class="row-main"><b>{shelf.name}</b><span>{left}. {how}{shelf.wait ? `, after ${shelf.wait} days` : ""}.</span></div>
        {can && <button type="button" class="row-act" onClick={() => void act("file", shelf.kind)}>File {shelf.ready}</button>}
      </div>
      {shelf.undo.map((batch) => (
        <p class="quiet-line" key={batch.change}>Filed {emails(batch.count)} to {shelf.label}.{" "}
          <button type="button" class="linkish" onClick={() => void act("undo", batch.change)}>Put back</button></p>
      ))}
    </div>
  );
}

/** Mail for someone whose Google account Hub doesn't read: theirs isn't here yet, and no one else's is shown. */
export function NotConnected() {
  const here = me.value.signed_in;
  return (
    <div class="wrap">
      <Top />
      <h1 class="say">{here ? "Your mail isn’t connected yet" : "Mail stays on each person’s phone"}</h1>
      <p class="sub">{here ? "Hub shows each person only their own mail. Once your Google account is connected, yours shows here, and only to you."
        : "Open Hub on your own phone or laptop to see yours."}</p>
    </div>
  );
}

export function Mail() {
  const now = mail.value;
  if (!mailMine.value) return <NotConnected />;
  if (!now) {
    return (
      <div class="wrap">
        <Top />
        {mailAway.value
          ? <div class="banner"><b>Can’t reach Hub</b><span>This device is off the home network. Turn Tailscale on and open Mail again.</span></div>
          : <div class="skel"><h1 class="say">Reading mail</h1><p class="sub">One moment</p></div>}
      </div>
    );
  }
  const busy = Boolean(now.working);
  return (
    <div class={"wrap mail" + (mailAway.value ? " offline" : "")}>
      <Top />
      {mailAway.value && <div class="banner"><b>Can’t reach Hub</b><span>Showing mail as it was when Hub last answered.</span></div>}
      {now.failed && <div class="banner"><b>The last sort didn’t finish</b><span>Hub will try again. You can sort now below.</span></div>}
      {now.review > 0 && <div class="banner"><b>Hub couldn’t confirm a filing</b><span>Open the older Mail page to check what moved.</span></div>}
      <h1 class="say">{mailSays(now)}</h1>
      <p class="sub">{mailSub(now)}</p>
      {now.needs.length > 0 && (
        <section class="block">
          <h2>Needs you</h2>
          <div class="rows">{now.needs.map((e) => <EmailRow email={e} waiting={now.waiting_days} what="done" back="back" label="Handled" key={e.id} />)}</div>
          {now.needs_more > 0 && <p class="quiet-line">{now.needs_more === 1 ? "One more" : `${now.needs_more} more`} on the older page. <a href="/gmail">Mail</a></p>}
        </section>
      )}
      {now.asked.length > 0 && (
        <section class="block">
          <h2>You asked, no answer yet</h2>
          <div class="rows">
            {now.asked.map((a) => {
              const done = ticked.value.includes(a.id);
              return (
                <div class={"row tick need" + (done ? " on" : "")} key={a.id}>
                  <button type="button" class="tickbtn" aria-label={`Stop watching: ${a.subject}`} disabled={done} onClick={() => void tick("answered", a.id)}>
                    <span class="ring"><Icon name="check" /></span>
                  </button>
                  <div class="row-main"><b>{a.subject || "(no subject)"}</b><span>{a.to ? `To ${a.to}, ` : "Sent "}{ago(a.days)}</span></div>
                  {!done && <a class="row-act" href={a.find}>Find</a>}
                </div>
              );
            })}
          </div>
        </section>
      )}
      {now.odd.length > 0 && (
        <section class="block">
          <h2>Worth a look</h2>
          <p class="sub">Records Hub kept in the inbox: a large amount, or something unusual.</p>
          <div class="rows">{now.odd.map((e) => <EmailRow email={e} waiting={999} what="fine" label="Looks fine" key={e.id} />)}</div>
        </section>
      )}
      <section class="block">
        <h2>Sorted</h2>
        {now.paused && <p class="sub">Changes to Gmail are paused, so nothing is filed.</p>}
        <div class="rows">{now.shelves.map((shelf) => <ShelfRow shelf={shelf} busy={busy} paused={now.paused} key={shelf.kind} />)}</div>
        {now.unsorted > 0 && <p class="quiet-line">{emails(now.unsorted)} Hub couldn’t place stay in the inbox.</p>}
      </section>
      <div class="btns">
        <button type="button" class="btn ghost" disabled={busy} onClick={() => void act("sort")}>{now.working === "sorting" ? "Sorting…" : "Sort now"}</button>
        <button type="button" class="btn ghost" onClick={() => go("mailsettings")}>Rules and settings</button>
      </div>
      <p class="quiet">Hub only reads your mail, and files it only at the level you chose. It never sends.</p>
    </div>
  );
}
