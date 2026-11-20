// Versions: each release of Hub, newest first, what it changed in plain words, and links to its code.
// When the deployer has a checked version waiting, it leads with Deploy and follows the update until it runs.
// For a day after an update (one Hub made by itself included) it offers Undo, which puts the version from before back.
import { useEffect, useState } from "preact/hooks";
import * as api from "./api";
import { me } from "./state";
import { Top } from "./screens";
import type { Deploy, Release, Releases, Undo } from "./types";

const FOLLOW = 3000;   // how often to look again while an update is under way
const GIVE_UP = 180;   // seconds after which the card stops waiting and says to look again later
const SHOWN = 6;       // releases listed before "Earlier versions": the newest, and down to the one running if it is older

const DAY = new Intl.DateTimeFormat(undefined, { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });

function Entry({ release }: { release: Release }) {
  const when = release.live ? `Live ${DAY.format(new Date(release.live))}` : release.running ? "Running now" : "Not live yet";
  return (
    <section class={"block release" + (release.running ? " now" : "")}>
      <h2>{release.title}</h2>
      <p class="release-when">{release.running && release.live ? "Running now · " : ""}{when}{release.short && !release.commit ? ` · ${release.short}` : ""}</p>
      <ul class="release-list">{release.changes.map((line) => <li key={line}>{line}</li>)}</ul>
      {(release.commit || release.compare) && (
        <p class="release-links">
          {release.commit && <a class="row-act" href={release.commit} target="_blank" rel="noopener noreferrer">Commit {release.short}</a>}
          {release.compare && <a class="row-act" href={release.compare} target="_blank" rel="noopener noreferrer">Every change in the code</a>}
        </p>
      )}
    </section>
  );
}

// After a tap, Hub restarts on another version: keep asking until a different version answers (a few failed
// reads in between are the restart), then reload. `failed` is said when the deployer put the running one back.
function useFollow(from: string | null, again: () => Promise<Releases | null>, asked: boolean, failed: string) {
  const [said, setSaid] = useState("");
  const [since, setSince] = useState<number | null>(asked ? Date.now() : null);
  useEffect(() => {
    if (since === null) return;
    const timer = setInterval(() => {
      if (Date.now() - since > GIVE_UP * 1000) { clearInterval(timer); setSince(null); setSaid("It’s taking longer than usual. Look again in a minute."); return; }
      void again().then((now) => {
        if (!now) return;
        if (now.running !== from) { clearInterval(timer); location.reload(); }
        else if (now.deploy?.put_back) { clearInterval(timer); setSince(null); setSaid(failed); }
      });
    }, FOLLOW);
    return () => clearInterval(timer);
  }, [since]);
  const ask = async (path: string, setBusy: (busy: boolean) => void) => {
    setBusy(true);
    const answer = await api.post(path);
    setBusy(false);
    if (answer.error) { setSaid(answer.error); return; }
    setSaid("");
    setSince(Date.now());
  };
  return { said, going: since !== null, ask };
}

// The checked version waiting, with Deploy, followed until it runs.
function Ready({ deploy, from, again }: { deploy: Deploy; from: string | null; again: () => Promise<Releases | null> }) {
  const [busy, setBusy] = useState(false);
  const { said, going, ask } = useFollow(from, again, deploy.asked, "The new version didn’t pass its checks, so Hub put the earlier one back.");
  const ready = deploy.ready!;
  const tap = () => ask("deploy", setBusy);
  return (
    <section class="block release ready">
      <h2>{going ? "Updating Hub" : "A new version is ready"}</h2>
      <p class="release-when">{going ? "Backing up, switching over, then checking. About a minute." : `Built and tested · ${ready.short}`}</p>
      {ready.subjects.length > 0 && <ul class="release-list">{ready.subjects.map((line) => <li key={line}>{line}</li>)}</ul>}
      {!going && (
        <div class="btns">
          <button type="button" class="btn go" disabled={busy || !me.value.signed_in} onClick={() => void tap()}>Deploy</button>
        </div>
      )}
      <p class="quiet">{said || (going ? "This screen reloads by itself when the new version is running."
        : me.value.signed_in ? "If a check fails after the switch, Hub goes back to this version by itself."
        : "Deploy from your phone or laptop.")}</p>
    </section>
  );
}

// The last update, while Undo can still put the version from before it back: when it went live, and Undo.
function Undoing({ undo, asked, from, again }: { undo: Undo; asked: boolean; from: string | null; again: () => Promise<Releases | null> }) {
  const [busy, setBusy] = useState(false);
  const { said, going, ask } = useFollow(from, again, false, "The version from before didn’t pass its checks, so this one is still running.");
  return (
    <section class="block release undo">
      <h2>{going ? "Going back" : undo.auto ? "Hub updated itself" : "Hub was updated"}</h2>
      <p class="release-when">{going ? "Backing up, switching back, then checking. About a minute." : `Live ${DAY.format(new Date(undo.at))} · ${undo.short}`}</p>
      {!going && !asked && (
        <div class="btns">
          <button type="button" class="btn" disabled={busy || !me.value.signed_in} onClick={() => void ask("deploy/undo", setBusy)}>Undo</button>
        </div>
      )}
      <p class="quiet">{said || (going ? "This screen reloads by itself when the version from before is running."
        : asked ? "Hub is updating. Undo once it is done."
        : me.value.signed_in ? `Undo puts back the version from before (${undo.previous}), until ${DAY.format(new Date(undo.until * 1000))}.`
        : "Undo from your phone or laptop.")}</p>
    </section>
  );
}

export function Versions() {
  const [found, setFound] = useState<Releases | null>(null);
  const [failed, setFailed] = useState(false);
  const [all, setAll] = useState(false);
  const again = () => api.versions().then((now) => { setFound(now); return now; }, () => null);
  useEffect(() => { api.versions().then(setFound, () => setFailed(true)); }, []);
  const now = found?.versions.find((r) => r.running);
  const list = found?.versions ?? [];
  const shown = all ? list : list.slice(0, Math.max(SHOWN, list.findIndex((r) => r.running) + 1));
  return (
    <div class="wrap">
      <Top />
      <h1 class="say">Versions</h1>
      <p class="sub">{failed ? "Can’t reach Hub to read the list." : !found ? "Reading…"
        : now ? `Running: ${now.title}.` : "Each release of Hub, newest first."}</p>
      {found?.deploy?.put_back && !found.deploy.ready && (
        <p class="quiet">The last update ({found.deploy.put_back}) didn’t pass its checks, so Hub put the earlier version back.</p>
      )}
      {found?.deploy?.ready && <Ready deploy={found.deploy} from={found.running} again={again} />}
      {found?.deploy?.undo && <Undoing undo={found.deploy.undo} asked={found.deploy.asked} from={found.running} again={again} />}
      {shown.map((release) => <Entry release={release} key={release.sha ?? "next"} />)}
      {shown.length < list.length && (
        <button type="button" class="btn ghost more-btn" onClick={() => setAll(true)}>Earlier versions ({list.length - shown.length})</button>
      )}
    </div>
  );
}
