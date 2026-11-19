// House health: what the house's caretaker watches, what it fixed by itself, and what it hands to you.
// Read from Hub (hub/caretaker.py), which reads the caretaker's folder; a tap leaves the caretaker a request that
// it checks again and answers within a minute. Ask is the way to steer it (pause, drills, "I changed the Hue
// channel"); this screen is the detail, with one brake that never needs the model: Pause. A drill plays a real
// failure on a made-up house, never the real one, and shows here while it plays.
import { useEffect, useState } from "preact/hooks";
import * as api from "./api";
import { Top } from "./screens";
import { go, lookAtCare, me, told } from "./state";
import type { CareDay, CareDot, CareDrill, CareNeed, CareState, CareSystem, Health as HealthData } from "./types";

const STATE_WORDS: Record<CareState, string> = {
  needs_you: "needs you", fixing: "being fixed", watching: "being looked at", unknown: "can’t tell", ok: "fine", off: "not here",
};
const DOT_WORDS: Record<CareDot, string> = {
  needs_you: "needed you", fixed: "fixed", by_itself: "came back by itself", ok: "fine", unknown: "not watched",
};
const OUTCOME: Record<string, string> = {
  fixed: "fixed", by_itself: "came back by itself", needs_you: "needed you", done: "you sorted it", ignored: "left alone",
  gave_up: "stopped watching", open: "still open",
};
const WEEKDAY = new Intl.DateTimeFormat(undefined, { weekday: "long", month: "short", day: "numeric" });
const INITIAL = new Intl.DateTimeFormat(undefined, { weekday: "narrow" });

function dateOf(iso: string) {
  const [y = 1970, m = 1, d = 1] = iso.split("-").map(Number);
  return new Date(y, m - 1, d);
}

const dayName = (iso: string) => WEEKDAY.format(dateOf(iso));

function dayDot(d: CareDay): CareDot {
  if (!d.watched) return "unknown";
  return d.needs_you ? "needs_you" : d.fixed ? "fixed" : d.by_itself ? "by_itself" : "ok";
}

const troubled = (s: CareSystem) => s.state !== "ok" && s.state !== "off";

function needLine(need: CareNeed) {
  return [`Since ${need.since}`, need.held && "on Today from the morning"].filter(Boolean).join(" · ");
}

/** A drill as the caretaker plays it, step by step: on this screen and under Ask's answer. */
export function DrillProgress({ drill }: { drill: CareDrill }) {
  return (
    <div class="care-drill" aria-live="polite">
      <b>{drill.done ? `Drill finished: ${drill.title ?? drill.words}` : `Drill running: ${drill.words}`}</b>
      {drill.steps.map((s, i) => <p key={i}><span>{s.at}</span>{s.words}</p>)}
      {drill.done && drill.outcome && <p class="care-when">{`It ended ${OUTCOME[drill.outcome] ?? drill.outcome}.`}</p>}
    </div>
  );
}

function History({ system }: { system: CareSystem }) {
  return (
    <div class="care-history">
      <div class="care-days" role="list" aria-label="The last 7 days">
        {system.days.map((d) => (
          <i key={d.day} role="listitem" class={"care-dot " + d.state} title={`${dayName(d.day)}: ${DOT_WORDS[d.state]}`}
            aria-label={`${dayName(d.day)}: ${DOT_WORDS[d.state]}`} />
        ))}
      </div>
      {system.history.length ? system.history.map((h) => (
        <p class="care-past" key={h.id}><b>{h.title}</b><span>{`${h.from}${h.to ? ` to ${h.to}` : ""} · ${OUTCOME[h.outcome] ?? h.outcome}`}</span></p>
      )) : <p class="quiet">Nothing happened here in the last 7 days.</p>}
    </div>
  );
}

export function Health() {
  const [found, setFound] = useState<HealthData | null>(null);
  const [failed, setFailed] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [day, setDay] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
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
  if (!found || !found.set_up || found.unreadable) {
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
      {found.paused && (
        <div class="care-acts">
          <button type="button" class="btn go" disabled={!can} onClick={() => void ask("resume", "", "Asked the caretaker to start again.")}>Resume</button>
        </div>
      )}
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
                  <button type="button" class="btn go" disabled={!can} onClick={() => void ask("restart", `care-${need.id}`)}>Restart Home Assistant</button>
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
        <h2>{night?.title ?? "Last night"}</h2>
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
          {(found.systems ?? []).filter(troubled).map((system) => (
            <div key={system.id}>
              <button type="button" class="row care-row" data-system={system.id} aria-expanded={open === system.id}
                onClick={() => setOpen(open === system.id ? null : system.id)}>
                <i class={"care-dot " + system.state} role="img" aria-label={STATE_WORDS[system.state]} />
                <div class="row-main"><b>{system.name}</b><span>{system.line}</span></div>
              </button>
              {open === system.id && <History system={system} />}
            </div>
          ))}
        </div>
        <div class="care-grid">
          {(found.systems ?? []).filter((s) => !troubled(s)).map((system) => (
            <button type="button" key={system.id} class={"care-fine" + (open === system.id ? " on" : "")} data-system={system.id}
              aria-expanded={open === system.id} onClick={() => setOpen(open === system.id ? null : system.id)}>
              <i class={"care-dot " + system.state} role="img" aria-label={STATE_WORDS[system.state]} /><span>{system.name}</span>
            </button>
          ))}
        </div>
        {(() => {
          const shown = (found.systems ?? []).find((s) => s.id === open && !troubled(s));
          return shown ? <div class="care-open"><p class="care-past"><b>{shown.name}</b><span>{shown.line}</span></p><History system={shown} /></div> : null;
        })()}
      </section>

      <section class="block">
        <h2>The last 7 days</h2>
        <div class="care-week" role="group" aria-label="The last 7 days">
          {(found.week ?? []).map((d) => {
            const count = d.fixed + d.by_itself + d.needs_you;
            return (
              <button type="button" key={d.day} class={"care-day" + (day === d.day ? " on" : "")} aria-pressed={day === d.day}
                aria-label={`${dayName(d.day)}: ${d.watched ? (count ? count + " happenings" : "quiet") : "not watched"}`}
                onClick={() => setDay(day === d.day ? null : d.day)}>
                <b>{INITIAL.format(dateOf(d.day))}</b>
                <i class={"care-dot " + dayDot(d)} />
                <small>{count || ""}</small>
              </button>
            );
          })}
        </div>
        <p class="care-headline">{found.week_says}</p>
        {(() => {
          const chosen = (found.week ?? []).find((d) => d.day === day);
          if (!chosen) return null;
          return (
            <div class="care-open">
              <p class="care-past"><b>{dayName(chosen.day)}</b><span>{DOT_WORDS[dayDot(chosen)]}</span></p>
              {chosen.lines.length ? chosen.lines.map((line, i) => (
                <p class={"care-line " + line.tone} key={i}><i aria-hidden="true" /><span>{`${line.at} · ${line.text}`}</span></p>
              )) : <p class="quiet">{chosen.watched ? "Nothing happened." : "The caretaker wasn’t watching."}</p>}
            </div>
          );
        })()}
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
                  <button type="button" class="row-act" disabled={!can} onClick={() => void ask("done", `sturdier:${item.key}`, "Noted.")}>Done</button>
                  <button type="button" class="row-act" disabled={!can} onClick={() => void ask("ignore", `sturdier:${item.key}`)}>Not needed</button>
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

      {drill && (
        <section class="block">
          <h2>Drill</h2>
          <DrillProgress drill={drill} />
        </section>
      )}

      {(found.ignoring ?? []).length > 0 && (
        <p class="quiet-line">{`Not watched: ${found.ignoring!.map((i) => i.name).join(", ")}.`}</p>
      )}
      <p class="quiet-line">Ask can pause the caretaker or run a drill.</p>
      {!found.paused && (
        <div class="care-acts">
          <button type="button" class="row-act" disabled={!can} onClick={() => void ask("pause", "forever", "Paused until you resume.")}>Pause until I resume</button>
        </div>
      )}
      {!me.value.signed_in && <p class="quiet">This screen is open on the Mac itself, which can look but not ask the caretaker for anything.</p>}
    </div>
  );
}
