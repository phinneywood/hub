// House health: what the house's caretaker watches, what it fixed by itself, and what it hands to you.
// Read from Hub (hub/caretaker.py), which reads the caretaker's folder; a tap leaves the caretaker a request that
// it checks again and answers within a minute. A drill plays a real failure on a made-up house, never the real one.
import { useEffect, useState } from "preact/hooks";
import * as api from "./api";
import { Top } from "./screens";
import { go, lookAtCare, me, told } from "./state";
import type { CareDay, CareNeed, CareState, Health as HealthData } from "./types";

const STATE_WORDS: Record<CareState, string> = {
  needs_you: "needs you", fixing: "had trouble", watching: "being watched", unknown: "not watched", ok: "fine", off: "not here",
};
const OUTCOME: Record<string, string> = {
  fixed: "fixed", by_itself: "came back by itself", needs_you: "needed you", done: "you sorted it", ignored: "left alone",
  gave_up: "stopped watching", open: "still open",
};
const DRILL_NAMES: Record<string, string> = {
  "hub-down": "Hub stops", "hue-flapping": "A lamp flaps", "lock-battery": "A lock’s battery", "setup-retry": "An integration retries",
  "broker-hung": "The house connection hangs", "homeclaw-closed": "HomeClaw closes", "internet-down": "The internet drops",
};
const WEEKDAY = new Intl.DateTimeFormat(undefined, { weekday: "long", month: "short", day: "numeric" });

function dayName(iso: string) {
  const [y = 1970, m = 1, d = 1] = iso.split("-").map(Number);
  return WEEKDAY.format(new Date(y, m - 1, d));
}

function dayWords(d: CareDay) {
  if (!d.watched) return "Not watching";
  const said = [d.fixed && `${d.fixed} fixed`, d.by_itself && `${d.by_itself} came back by itself`, d.needs_you && `${d.needs_you} needed you`]
    .filter(Boolean);
  return said.length ? said.join(" · ") : "Quiet";
}

function needLine(need: CareNeed) {
  return [`Since ${need.since}`, need.held && "on Today from the morning", !need.today && "here only, not on Today"].filter(Boolean).join(" · ");
}

export function Health() {
  const [found, setFound] = useState<HealthData | null>(null);
  const [failed, setFailed] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [chosen, setChosen] = useState("");
  const playing = !!found?.drill && !found.drill.done;
  const load = () => api.health().then((now) => { setFound(now); setFailed(false); }, () => setFailed(true));
  useEffect(() => {
    void load();
    const every = window.setInterval(() => { if (!document.hidden) void load(); }, playing ? 2000 : 15000);
    return () => window.clearInterval(every);
  }, [playing]);

  async function ask(what: string, target = "", done = "Asked the caretaker.") {
    setBusy(true);
    const answer = await api.healthAsk(what, target);
    setBusy(false);
    told(answer, done);
    if (!answer.error) {
      await load();
      void lookAtCare();
      window.setTimeout(() => { void load(); void lookAtCare(); }, 3000);     // the caretaker answers within a pass
    }
  }

  const head = (
    <>
      <Top />
      <button type="button" class="uplink" onClick={() => go("system")}>System</button>
    </>
  );
  if (!found || !found.set_up) {
    return (
      <div class="wrap">
        {head}
        <h1 class="say">{found ? found.headline : "House health"}</h1>
        <p class="sub">{failed ? "Can’t reach Hub to read it." : found ? found.sub : "Reading…"}</p>
      </div>
    );
  }
  const can = me.value.signed_in && !busy;
  const needs = found.needs ?? [];
  const night = found.night;
  const drill = found.drill;
  const last = found.answers?.[0];
  return (
    <div class="wrap">
      {head}
      <h1 class={"say" + (found.alive ? "" : " warn-line")}>{found.headline}</h1>
      <p class="sub">{found.sub}</p>
      {last && <p class="quiet-line">{`The caretaker answered your last request: ${last.words}`}</p>}

      {needs.length > 0 && (
        <section class="block">
          <h2>Needs you</h2>
          {needs.map((need) => (
            <div class="care-need" key={need.id}>
              <b>{need.title}</b>
              <p>{need.words}</p>
              <span class="care-when">{needLine(need)}</span>
              <div class="care-acts">
                {need.ask === "ha.restart" && (
                  <button type="button" class="btn go" disabled={!can} onClick={() => void ask("fix", `care-${need.id}`)}>Restart Home Assistant</button>
                )}
                {need.ask !== "ha.restart" && need.fixable && (
                  <button type="button" class="row-act" disabled={!can} onClick={() => void ask("fix", `care-${need.id}`)}>Try again</button>
                )}
                <button type="button" class="row-act" disabled={!can} onClick={() => void ask("done", `care-${need.id}`, "Marked as sorted.")}>Done</button>
                <button type="button" class="row-act" disabled={!can} onClick={() => void ask("ignore", `care-${need.id}`)}>Ignore</button>
              </div>
            </div>
          ))}
        </section>
      )}

      <section class="block">
        <h2>{`Last night, from ${night?.from ?? "9:00 PM"}`}</h2>
        {night?.headline && <p class="care-headline">{night.headline}</p>}
        {night && night.lines.length > 0 ? (
          <div class="rows">
            {night.lines.map((line, i) => (
              <p class={"care-line " + line.tone} key={i}><i aria-hidden="true" /><span>{line.text}</span></p>
            ))}
          </div>
        ) : <p class="quiet">Nothing happened overnight.</p>}
      </section>

      <section class="block">
        <h2>Watching</h2>
        <div class="rows">
          {(found.systems ?? []).map((system) => (
            <div key={system.id}>
              <button type="button" class="row care-row" aria-expanded={open === system.id}
                onClick={() => setOpen(open === system.id ? null : system.id)}>
                <i class={"care-dot " + system.state} role="img" aria-label={STATE_WORDS[system.state]} />
                <div class="row-main"><b>{system.name}</b><span>{system.line}</span></div>
              </button>
              {open === system.id && (
                <div class="care-history">
                  <div class="care-days" role="list" aria-label="The last 7 days">
                    {system.days.map((d) => (
                      <i key={d.day} role="listitem" class={"care-dot " + d.state} title={`${dayName(d.day)}: ${STATE_WORDS[d.state]}`}
                        aria-label={`${dayName(d.day)}: ${STATE_WORDS[d.state]}`} />
                    ))}
                  </div>
                  {system.history.length ? system.history.map((h) => (
                    <p class="care-past" key={h.id}><b>{h.title}</b><span>{`${h.from}${h.to ? ` to ${h.to}` : ""} · ${OUTCOME[h.outcome] ?? h.outcome}`}</span></p>
                  )) : <p class="quiet">Nothing happened here in the last 7 days.</p>}
                </div>
              )}
            </div>
          ))}
        </div>
      </section>

      <section class="block">
        <h2>The last 7 days</h2>
        <div class="rows">
          {(found.week ?? []).slice().reverse().map((d) => (
            <div class="ctl one" key={d.day}>
              <div class="ctl-name"><b>{dayName(d.day)}</b><span>{dayWords(d)}</span></div>
            </div>
          ))}
        </div>
      </section>

      {(found.sturdier ?? []).length > 0 && (
        <section class="block">
          <h2>Make it sturdier</h2>
          {found.sturdier!.map((item) => (
            <div class="care-need" key={item.key}>
              <b>{item.title}</b>
              <p>{item.words}</p>
              {item.verdict && <span class="care-when">{item.verdict}</span>}
              {!item.verdict && item.done_at && <span class="care-when">You changed it; the caretaker is checking whether it helped.</span>}
              {!item.done_at && (
                <div class="care-acts">
                  <button type="button" class="row-act" disabled={!can} onClick={() => void ask("done", `sturdier:${item.key}`, "Noted.")}>I did this</button>
                  <button type="button" class="row-act" disabled={!can} onClick={() => void ask("ignore", `sturdier:${item.key}`)}>Not for us</button>
                </div>
              )}
            </div>
          ))}
        </section>
      )}

      {(found.batteries ?? []).length > 0 && (
        <section class="block">
          <h2>Batteries</h2>
          <div class="rows">
            {found.batteries!.map((b) => (
              <div class="ctl one" key={b.name}>
                <div class="ctl-name"><b>{b.name}</b><span>{`${b.level}%${b.reachable ? "" : " · not responding"}${b.room ? " · " + b.room : ""}`}</span></div>
              </div>
            ))}
          </div>
        </section>
      )}

      <section class="block">
        <h2>Pause</h2>
        <p class="sub">Paused, the caretaker keeps watching and telling you, but fixes nothing.</p>
        <div class="care-acts">
          {found.paused
            ? <button type="button" class="btn go" disabled={!can} onClick={() => void ask("resume", "", "Asked the caretaker to start again.")}>Resume</button>
            : (
              <>
                <button type="button" class="row-act" disabled={!can} onClick={() => void ask("pause", "60", "Paused for an hour.")}>Pause for an hour</button>
                <button type="button" class="row-act" disabled={!can} onClick={() => void ask("pause", "forever", "Paused until you resume.")}>Until I resume</button>
              </>
            )}
        </div>
      </section>

      <section class="block">
        <h2>Run a drill</h2>
        <p class="sub">A drill plays a real kind of failure on a made-up house, so you can watch what the caretaker does. Nothing real is touched.</p>
        <div class="scenes filters" role="radiogroup" aria-label="Which drill">
          {(found.drills ?? []).map((d) => (
            <button type="button" role="radio" aria-checked={chosen === d.name} key={d.name} class={"scene" + (chosen === d.name ? " on" : "")}
              onClick={() => setChosen(d.name)}>{DRILL_NAMES[d.name] ?? d.name}</button>
          ))}
        </div>
        {chosen && <p class="quiet-line">{found.drills?.find((d) => d.name === chosen)?.words}</p>}
        <div class="care-acts">
          <button type="button" class="btn go" disabled={!chosen || !can || playing} onClick={() => void ask("drill", chosen, "Starting the drill.")}>Run the drill</button>
        </div>
        {drill && (
          <div class="care-drill" aria-live="polite">
            <b>{drill.done ? `Drill finished: ${drill.title ?? drill.words}` : `Drill running: ${drill.words}`}</b>
            {drill.steps.map((s, i) => <p key={i}><span>{s.at}</span>{s.words}</p>)}
            {drill.done && drill.outcome && <p class="care-when">{`It ended ${OUTCOME[drill.outcome] ?? drill.outcome}.`}</p>}
          </div>
        )}
      </section>

      {(found.ignoring ?? []).length > 0 && (
        <p class="quiet-line">{`Not watched: ${found.ignoring!.map((i) => i.name).join(", ")}.`}</p>
      )}
      {!me.value.signed_in && <p class="quiet">This screen is open on the Mac itself, which can look but not ask the caretaker for anything.</p>}
    </div>
  );
}
