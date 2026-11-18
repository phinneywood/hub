// Mail settings: how far Hub files each kind, the charge alert, senders you sorted yourself, the plan of inbox
// changes with Apply and Undo, and the archive-after-30-days rules. Read from Hub (hub/app/mailrules.py) each time
// it is looked at; the taps are the older Mail pages' own.
import { useEffect, useState } from "preact/hooks";
import * as api from "./api";
import { NotConnected } from "./mail";
import { Top } from "./screens";
import { go, me, say } from "./state";
import type { MailSettings, PlanItem } from "./types";

const BUSY_EVERY = 3000;
const n = (x: number) => x.toLocaleString();

function Item({ item, act, locked }: { item: PlanItem; act?: [string, string]; locked: boolean }) {
  return (
    <div class={act ? "ctl two" : "ctl one"}>
      <div class="ctl-name"><b>{item.who}</b>
        <span>{item.step ? `${item.step}${item.done ? ` · ${n(item.done)}${item.of ? " of about " + n(item.of) : ""} so far` : ""}` : item.status === "undone" ? "Undone" : item.does}</span>
        {item.error && <span>{item.error}</span>}</div>
      {act && <button type="button" class="forget" disabled={locked} onClick={() => void tapIt(act[0], item.id)}>{act[1]}</button>}
    </div>
  );
}

// ---- quiet your loudest senders, one at a time (the older Quiet page's card). Each choice goes into the plan above. ----

interface Quiet {
  ok: true; done: boolean; reviewed: number; left: number; later: number; sender?: string; name?: string; count?: number; exact?: boolean;
  unread_share?: number | null; covered?: boolean; recent?: { subject: string; unread: boolean }[];
}

function QuietCard({ locked, after }: { locked: boolean; after: () => void }) {
  const [q, setQ] = useState<Quiet | null>(null);
  const [problem, setProblem] = useState("");
  const [busy, setBusy] = useState(false);
  const read = () => api.get<Quiet | { error: string }>("mail-settings/quiet").then(
    (found) => { if ("error" in found) setProblem(found.error); else { setProblem(""); setQ(found); } },
    () => setProblem("Gmail isn’t answering just now."));
  useEffect(() => { void read(); }, []);
  const decide = async (decision: string) => {
    if (!q?.sender) return;
    setBusy(true);
    const answer = await api.post("mail-settings/quiet", { key: q.sender, value: { decision, count: q.count, exact: q.exact, name: q.name } });
    say(answer.error ?? answer.said ?? "Done");
    await read();
    setBusy(false);
    after();
  };
  if (problem) return <p class="quiet-line">{problem}</p>;
  if (!q) return <p class="quiet-line">Finding your loudest sender…</p>;
  if (q.done) return <p class="quiet-line">Every loud sender is decided{q.later ? `, with ${q.later} left for later` : ""}.</p>;
  const many = `${n(q.count ?? 0)}${q.exact ? "" : "+"}`;
  const reads = q.unread_share != null && q.unread_share <= 20 && (q.count ?? 0) >= 5;
  const off = locked || busy;
  return (
    <div class="quietcard">
      <p class="sub">{q.reviewed} decided · {q.left} left in this batch</p>
      <div class="ctl-name"><b>{q.name || q.sender}</b>{q.name && <span>{q.sender}</span>}
        <span>{many} in the inbox{q.unread_share != null ? `, ${q.unread_share}% unread` : ""}</span></div>
      <div class="rows">{(q.recent ?? []).map((m, i) => <div class="row free" key={i}><div class="row-main"><span>{m.unread ? "● " : ""}{m.subject}</span></div></div>)}</div>
      {q.covered && <p class="sub">A filter already skips the inbox for this sender, but these are still here.</p>}
      <div class="btns">
        {q.covered
          ? <button type="button" class={"btn" + (reads ? " ghost" : " go")} disabled={off} onClick={() => void decide("archive")}>Archive these {many}</button>
          : <button type="button" class={"btn" + (reads ? " ghost" : " go")} disabled={off} onClick={() => void decide("filter_archive")}>Skip the inbox, and archive {many}</button>}
        {!q.covered && <button type="button" class="btn ghost" disabled={off} onClick={() => void decide("filter")}>New mail only</button>}
        <button type="button" class={"btn" + (reads ? " go" : " ghost")} disabled={off} onClick={() => void decide("keep")}>Keep</button>
        <button type="button" class="btn ghost" disabled={off} onClick={() => void decide("later")}>Later</button>
      </div>
      <p class="quiet-line">Choices go into your plan; nothing changes in Gmail until you apply it. Labels, 30-day rules and Luna’s advice are on <a href="/gmail/quiet">the older page</a>.</p>
    </div>
  );
}

let tapIt: (what: string, key: string | number, value?: unknown) => Promise<void> = async () => {};

export function MailSettings() {
  const [found, setFound] = useState<MailSettings | null>(null);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [charge, setCharge] = useState("");
  const [mine, setMine] = useState(true);
  const load = () => api.mailSettings().then((now) => {
    setFailed(false);
    setMine(!("connected" in now));
    if ("connected" in now) return null;
    setFound(now);
    setCharge(String(now.charge));
    return now;
  }, () => { setFailed(true); return null; });
  useEffect(() => { void load(); }, []);
  useEffect(() => {
    if (!found || !(found.plan.working || found.checking || found.plan.running.length)) return;
    const timer = setTimeout(() => void load(), BUSY_EVERY);
    return () => clearTimeout(timer);
  }, [found]);

  tapIt = async (what, key, value) => {
    setBusy(true);
    const answer = await api.post("mail-settings/" + what, { key: String(key), value });
    setBusy(false);
    say(answer.error ?? answer.said ?? "Done");
    await load();
  };

  const now = found;
  const locked = busy || !me.value.signed_in;
  const plan = now?.plan;
  if (!mine) return <NotConnected />;
  return (
    <div class="wrap">
      <Top />
      <button type="button" class="uplink" onClick={() => go("mail")}>Mail</button>
      <h1 class="say">Mail settings</h1>
      <p class="sub">{failed ? "Can’t reach Hub to read them." : !now ? "Reading…"
        : now.paused ? "Changes to Gmail are paused right now. Hub saves what you choose and waits." : "How Hub sorts and tidies the inbox."}</p>
      {now && plan && (
        <>
          {(plan.planned.length > 0 || plan.running.length > 0 || plan.problems.length > 0) && (
            <section class="block">
              <h2>{plan.working ? "Applying your plan" : "Your plan"}</h2>
              <p class="sub">{plan.planned.length === 1 ? "One change" : `${plan.planned.length} changes`} waiting
                {plan.leaving ? `, about ${n(plan.leaving)} emails leave the inbox` : ""}. Nothing is deleted, and each can be undone.</p>
              {plan.running.map((i) => <Item key={i.id} item={i} locked={locked} />)}
              {plan.problems.map((i) => <Item key={i.id} item={i} locked={locked} />)}
              {plan.planned.map((i) => <Item key={i.id} item={i} act={["remove", "Remove"]} locked={locked || plan.working} />)}
              {plan.planned.length > 0 && !plan.working && (
                <div class="btns"><button type="button" class="btn go" disabled={locked || now.paused} onClick={() => void tapIt("apply", "")}>
                  {now.paused ? "Paused" : `Apply ${plan.planned.length === 1 ? "it" : "all " + plan.planned.length}`}</button></div>
              )}
              {plan.working && <p class="quiet">You can close this. It keeps going on the Mac.</p>}
            </section>
          )}

          {now.backlog && !now.backlog.planned && (
            <section class="block">
              <h2>Old mail</h2>
              <p class="sub">{now.backlog.text} inbox emails are over 30 days old. Hub can archive them in one step, except starred mail.</p>
              <div class="btns"><button type="button" class="btn ghost" disabled={locked} onClick={() => void tapIt("backlog", "")}>Add it to the plan</button></div>
            </section>
          )}

          <section class="block">
            <h2>How far Hub files each kind</h2>
            {now.levels.map((k) => (
              <div class="trust" key={k.kind}>
                <div class="ctl-name"><b>{k.name}</b></div>
                <div class="choices" role="radiogroup" aria-label={"How far Hub files: " + k.name} style={{ gridTemplateColumns: `repeat(${k.levels.length}, 1fr)` }}>
                  {k.levels.map((l) => (
                    <button type="button" role="radio" aria-checked={k.level === l.value} key={l.value} disabled={locked}
                      class={"choice" + (k.level === l.value ? " on" : "")} onClick={() => k.level !== l.value && void tapIt("level", k.kind, l.value)}>{l.name}</button>
                  ))}
                </div>
              </div>
            ))}
            <p class="quiet">Filing adds a label and takes mail out of the inbox. It never marks it read, and each batch can be put back for 14 days.</p>
          </section>

          <section class="block">
            <h2>Charges</h2>
            <form class="charge" onSubmit={(e) => { e.preventDefault(); void tapIt("charge", "", Number(charge)); }}>
              <label class="ctl-name" for="charge"><b>Show me a charge of this much or more</b><span>In dollars</span></label>
              <input id="charge" class="field raised" type="number" inputMode="numeric" min={1} max={100000} value={charge} disabled={locked}
                onInput={(e) => setCharge(e.currentTarget.value)} />
              <button type="submit" class="btn ghost" disabled={locked || String(now.charge) === charge || !charge}>Save</button>
            </form>
          </section>

          {now.senders.length > 0 && (
            <section class="block">
              <h2>Senders you sorted yourself</h2>
              {now.senders.map((s) => (
                <div class="ctl two" key={s.sender}>
                  <div class="ctl-name"><b>{s.sender}</b><span>Always {s.kind.toLowerCase()}</span></div>
                  <button type="button" class="forget" disabled={locked} onClick={() => void tapIt("sender", s.sender)}>Let Hub decide</button>
                </div>
              ))}
            </section>
          )}

          {now.rules.length > 0 && (
            <section class="block">
              <h2>Archive after 30 days</h2>
              <p class="sub">Each rule archives a sender’s inbox mail once it is 30 days old, never starred mail.</p>
              {now.rules.map((r) => (
                <div class="ctl two" key={r.id}>
                  <div class="ctl-name"><b>{r.sender}</b>
                    <span>{r.stopped ? (r.check_first ? "Stopped. A change may not have finished: look in Gmail before resuming." : "Stopped. Nothing changed when it stopped.") : "On"}</span></div>
                  {r.stopped
                    ? <button type="button" class="forget" disabled={locked} onClick={() => void tapIt("rule-resume", r.id, r.check_first)}>{r.check_first ? "I checked, resume" : "Resume"}</button>
                    : <button type="button" class="forget" disabled={locked} onClick={() => void tapIt("rule-stop", r.id)}>Stop</button>}
                </div>
              ))}
              <div class="btns"><button type="button" class="btn ghost" disabled={locked || now.checking || now.paused} onClick={() => void tapIt("rules-check", "")}>
                {now.checking ? "Checking…" : "Check them now"}</button></div>
            </section>
          )}

          {plan.finished.length > 0 && (
            <section class="block">
              <h2>Applied</h2>
              {plan.finished.map((i) => <Item key={i.id} item={i} act={i.status === "done" && !now.paused ? ["undo", "Undo"] : undefined} locked={locked} />)}
            </section>
          )}

          <section class="block">
            <h2>Quiet your loudest senders</h2>
            <QuietCard locked={locked} after={() => void load()} />
          </section>
          <section class="block">
            <h2>More ways to clear the inbox</h2>
            <p class="sub">These open Hub’s older Mail pages, until each moves in here.</p>
            <div class="rows">
              <a class="row free" href="/gmail/sweep"><div class="row-main"><b>Clean up in bulk</b><span>Mail over a year old, old Promotions, old unread</span></div></a>
              <a class="row free" href="/gmail/health"><div class="row-main"><b>Tidy filters</b><span>Broken, duplicate and unused filters</span></div></a>
            </div>
          </section>
          {!me.value.signed_in && <p class="quiet">Open this on your phone or laptop to change anything.</p>}
          <p class="quiet">Hub never sends or deletes mail.</p>
        </>
      )}
    </div>
  );
}
