// Engine: what you asked Hub for, what it may do without asking, and what its models cost. The older Engine page's
// reading and taps (hub/app/engine.py), in the app. Read from Hub each time the screen is looked at.
import { useEffect, useState } from "preact/hooks";
import * as api from "./api";
import { Top } from "./screens";
import { go, me, say } from "./state";
import type { Engine as EngineData, EngineRun } from "./types";

const DAY = new Intl.DateTimeFormat(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" });
const dollars = (n: number) => "$" + n.toFixed(2);

function Runs({ runs, what }: { runs: EngineRun[]; what: (r: EngineRun) => string }) {
  if (!runs.length) return <p class="quiet">None yet.</p>;
  return (
    <div class="rows">
      {runs.map((r) => (
        <div class="row free" key={r.at}>
          <div class="row-main"><b>{DAY.format(new Date(r.at * 1000))}</b><span>{(r.ok ? "Finished" : "Didn’t finish") + " · " + what(r)}</span></div>
          <span class="row-q">{"$" + r.cost.toFixed(4)}</span>
        </div>
      ))}
    </div>
  );
}

export function Engine() {
  const [found, setFound] = useState<EngineData | null>(null);
  const [failed, setFailed] = useState(false);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [sources, setSources] = useState(false);
  const [actions, setActions] = useState(false);
  const load = () => api.engine().then((now) => { setFound(now); setFailed(false); }, () => setFailed(true));
  useEffect(() => { void load(); }, []);

  async function tap(what: string, key: string | number = "", value?: string) {
    setBusy(true);
    const answer = await api.post("engine/" + what, { key: String(key), value });
    setBusy(false);
    say(answer.error ?? answer.said ?? "Done");
    await load();
  }
  async function tell(e: Event) {
    e.preventDefault();
    if (!text.trim()) return;
    setBusy(true);
    const answer = await api.post("engine/tell", { text });
    setBusy(false);
    say(answer.error ?? answer.said ?? "Saved");
    if (!answer.error) setText("");
    await load();
  }

  const now = found;
  const locked = busy || !me.value.signed_in;
  return (
    <div class="wrap">
      <Top />
      <button type="button" class="uplink" onClick={() => go("system")}>System</button>
      <h1 class="say">Engine</h1>
      <p class="sub">{failed ? "Can’t reach Hub to read it." : !now ? "Reading…" : "What you asked Hub for, what it may do without asking, and what its models cost."}</p>
      {now && (
        <>
          {(now.notes.length > 0 || now.recipes.length > 0) && (
            <section class="block">
              <h2>Waiting for you</h2>
              <div class="rows">
                {now.notes.map((n) => (
                  <div class="ctl one" key={"n" + n.id}>
                    <div class="ctl-name"><b>{n.text}</b><span>Ask proposed this note from “{n.said}”.{n.zone.length ? ` These speakers will play together: ${n.zone.join(", ")}.` : ""}</span></div>
                    <div class="btns tight">
                      <button type="button" class="btn go" disabled={locked || now.full} onClick={() => void tap("note-save", n.id)}>Save</button>
                      <button type="button" class="btn ghost" disabled={locked} onClick={() => void tap("note-skip", n.id)}>Not now</button>
                    </div>
                  </div>
                ))}
                {now.recipes.map((r) => (
                  <div class="ctl one" key={"r" + r.id}>
                    <div class="ctl-name"><b>{r.name}</b><span>{r.steps.map((s, i) => `${i + 1}. ${s}`).join("  ")}</span>
                      {r.said && <span>Ask proposed this routine from “{r.said}”.</span>}</div>
                    <div class="btns tight">
                      <button type="button" class="btn go" disabled={locked} onClick={() => void tap("recipe-save", r.id)}>Save</button>
                      <button type="button" class="btn ghost" disabled={locked} onClick={() => void tap("recipe-skip", r.id)}>Not now</button>
                    </div>
                  </div>
                ))}
              </div>
              {now.full && <p class="quiet">Hub keeps at most 12 notes. Remove one below first.</p>}
            </section>
          )}

          <form class="block" onSubmit={(e) => void tell(e)}>
            <h2>Tell Hub</h2>
            <input class="field raised" name="tell" maxLength={300} value={text} disabled={locked} aria-label="Tell Hub"
              placeholder="What to watch for, or what you wish it did" onInput={(e) => setText(e.currentTarget.value)} />
            <p class="quiet">{now.sorting ? "Hub keeps it as guidance it can follow tonight, or as something that needs building."
              : "Sorting is off right now, so it is kept as something to build."} It can’t give Hub a new ability by itself.</p>
            <div class="btns"><button type="submit" class="btn go" disabled={locked || !text.trim()}>Send</button></div>
          </form>

          <section class="block">
            <h2>What Hub follows</h2>
            <p class="sub">Guidance for Ask and the overnight review, and the notes you saved from Ask.</p>
            <div class="rows">
              {now.asked.map((i) => (
                <div class="ctl two" key={i.id}>
                  <div class="ctl-name"><b>{i.text}</b>{i.zone.length > 0 && <span>Speakers that play together: {i.zone.join(", ")}</span>}</div>
                  <button type="button" class="forget" disabled={locked} onClick={() => void tap("forget", i.id)}>Remove</button>
                </div>
              ))}
              {!now.asked.length && <p class="quiet">Nothing yet. For example: “Always tell me about school mail with a deadline.”</p>}
            </div>
          </section>

          {now.kept.length > 0 && (
            <section class="block">
              <h2>Routines</h2>
              <p class="sub">Name one in Ask to run it. Each step is checked again every time.</p>
              <div class="rows">
                {now.kept.map((r) => (
                  <div class="ctl two" key={r.id}>
                    <div class="ctl-name"><b>{r.name}</b><span>{r.steps.join(" · ")}</span></div>
                    <button type="button" class="forget" disabled={locked} onClick={() => void tap("recipe-remove", r.id)}>Remove</button>
                  </div>
                ))}
              </div>
            </section>
          )}

          {now.requests.length > 0 && (
            <section class="block" id="improvements">
              <h2>Asked for, not built yet</h2>
              <p class="sub">Notes for the next building session. Nothing here runs.</p>
              <div class="rows">
                {now.requests.map((r) => (
                  <div class="ctl two" key={r.kind + r.id}>
                    <div class="ctl-name"><b>{r.text}</b><span>{(r.kind === "improvement" ? "From “Improve this” · " : "") + DAY.format(new Date(r.at * 1000))}</span></div>
                    <button type="button" class="forget" disabled={locked} onClick={() => void tap(r.kind === "improvement" ? "improvement-done" : "request-done", r.id)}>Remove</button>
                  </div>
                ))}
              </div>
            </section>
          )}

          <section class="block">
            <h2>What Hub may do without asking</h2>
            <button type="button" class="row free" aria-expanded={actions} onClick={() => setActions(!actions)}>
              <div class="row-main"><b>{actions ? "Hide the list" : `${now.actions.filter((a) => a.levels.length > 1).length} things you can set`}</b>
                <span>{`${now.actions.filter((a) => a.level !== "suggest").length} of ${now.actions.length} happen with one tap or by themselves`}</span></div>
            </button>
            {actions && now.actions.map((a) => (
              <div class="trust" key={a.slug}>
                <div class="ctl-name"><b>{a.name}</b><span>{a.does} Undo: {a.undo}</span></div>
                {a.levels.length > 1 ? (
                  <div class="choices" role="radiogroup" aria-label={"How far Hub may go: " + a.name} style={{ gridTemplateColumns: `repeat(${a.levels.length}, 1fr)` }}>
                    {a.levels.map((l) => (
                      <button type="button" role="radio" aria-checked={a.level === l.value} key={l.value} disabled={locked}
                        class={"choice" + (a.level === l.value ? " on" : "")} onClick={() => a.level !== l.value && void tap("trust", a.slug, l.value)}>{l.name}</button>
                    ))}
                  </div>
                ) : <p class="quiet">{a.levels[0]?.name}. You do it on its own screen.</p>}
              </div>
            ))}
          </section>

          <section class="block">
            <h2>What the models cost</h2>
            <p class="sub">{`This month: ${dollars(now.spend.month)} of ${dollars(now.spend.month_limit)}. Today: ${now.spend.review.used} of ${now.spend.review.limit} review calls, `
              + `${now.spend.sorting.used} of ${now.spend.sorting.limit} sorting calls and ${now.spend.sorting.checks} of ${now.spend.sorting.check_limit} message checks.`}</p>
            <h2 class="minor">Overnight review</h2>
            <Runs runs={now.runs} what={(r) => `${r.calls} review`} />
            <div class="btns"><button type="button" class="btn ghost" disabled={locked || !now.spend.can_run} onClick={() => void tap("run")}>Run the review now</button></div>
            <p class="quiet">Reads the sources below once and adds suggestions to Today. Changes nothing else.</p>
            <h2 class="minor">Mail sorting</h2>
            <Runs runs={now.sorts} what={(r) => `${r.calls} sorting · ${r.checks} checks`} />
          </section>

          <section class="block">
            <button type="button" class="row free" aria-expanded={sources} onClick={() => setSources(!sources)}>
              <div class="row-main"><b>What Hub reads</b><span>{sources ? "Hide" : `${now.sources.length} sources, and what a model may see of each`}</span></div>
            </button>
            {sources && now.sources.map((s) => (
              <div class="ctl one" key={s.name}><div class="ctl-name"><b>{s.name}</b><span>Reads: {s.reads}</span><span>A model may see: {s.sends}</span></div></div>
            ))}
          </section>
          {!me.value.signed_in && <p class="quiet">Open this on your phone or laptop to change anything.</p>}
          <p class="quiet-line"><a href="/engine/ask-activity">Ask activity</a>, for checking what Ask did turn by turn.</p>
        </>
      )}
    </div>
  );
}
