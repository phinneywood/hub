// Changes: everything Hub changed in the house and the music library, newest first, each with Undo for a day,
// and the playlists Ask proposed, waiting for a tap. Read from Hub (hub/app/changes.py) when it is looked at.
import { useEffect, useState } from "preact/hooks";
import * as api from "./api";
import { Top } from "./screens";
import { go, me, say } from "./state";
import type { Changes as ChangesData } from "./types";

const WHEN = new Intl.DateTimeFormat(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" });

export function Changes() {
  const [found, setFound] = useState<ChangesData | null>(null);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const load = () => api.changes().then((now) => { setFound(now); setFailed(false); }, () => setFailed(true));
  useEffect(() => { void load(); }, []);

  async function tap(path: string, body: Record<string, unknown> = {}) {
    setBusy(true);
    const answer = await api.post(path, body);
    setBusy(false);
    say(answer.error ?? answer.said ?? "Done");
    await load();
  }

  const locked = busy || !me.value.signed_in;
  return (
    <div class="wrap">
      <Top />
      <button type="button" class="uplink" onClick={() => go("system")}>System</button>
      <h1 class="say">Everything Hub changed</h1>
      <p class="sub">{failed ? "Can’t reach Hub to read it." : !found ? "Reading…"
        : "Lights, plugs, fans, scenes, speakers and playlists, newest first. Each can be undone for a day."}</p>
      {found && found.waiting.length > 0 && (
        <section class="block">
          <h2>Playlists waiting for you</h2>
          {found.waiting.map((p) => (
            <div class="ctl one" key={p.id}>
              <div class="ctl-name"><b>Make “{p.name}”?</b><span>Asked for in Ask{p.songs.length ? ` · ${p.songs.join(", ")}` : ""}</span></div>
              <div class="btns tight">
                <button type="button" class="btn go" disabled={locked} onClick={() => void tap("playlist/make", { key: p.id })}>Make it</button>
                <button type="button" class="btn ghost" disabled={locked} onClick={() => void tap("playlist/skip", { key: p.id })}>Not now</button>
              </div>
            </div>
          ))}
        </section>
      )}
      {found && (
        <section class="block">
          {found.changes.map((c) => (
            <div class={c.undo ? "ctl two" : "ctl one"} key={c.where + c.batch}>
              <div class="ctl-name"><b>{c.what || "Nothing"}</b>
                <span>{`${WHEN.format(new Date(c.at * 1000))} · ${c.via}${c.where === "music" ? " · playlist" : ""}${c.undone ? " · undone" : ""}`}</span>
                {c.unsure.length > 0 && <span class="warn-line">Not confirmed: {c.unsure.join(", ")}</span>}
                {c.not_done.length > 0 && <span class="warn-line">Not done: {c.not_done.join(", ")}</span>}</div>
              {c.undo && <button type="button" class="forget" disabled={locked}
                onClick={() => void tap(c.where === "music" ? "playlist/undo" : "undo/" + c.batch, { key: c.batch })}>Undo</button>}
            </div>
          ))}
          {!found.changes.length && <p class="quiet">Hub hasn’t changed anything yet.</p>}
        </section>
      )}
      {!me.value.signed_in && <p class="quiet">Open this on your phone or laptop to undo anything.</p>}
    </div>
  );
}
