// Versions: each release of Hub, newest first, what it changed in plain words, and links to its code.
// When the deployer has a checked version waiting, it leads with Deploy and follows the update until it runs.
import { useEffect, useState } from "preact/hooks";
import * as api from "./api";
import { go, me } from "./state";
import { Top } from "./screens";
import type { Deploy, Release, Releases } from "./types";

const FOLLOW = 3000;   // how often to look again while an update is under way
const GIVE_UP = 180;   // seconds after which the card stops waiting and says to look again later

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

// The checked version waiting, with Deploy. After the tap, Hub restarts on the new version: the card keeps
// asking until a different version answers (a few failed reads in between are the restart), then reloads.
function Ready({ deploy, from, again }: { deploy: Deploy; from: string | null; again: () => Promise<Releases | null> }) {
  const [said, setSaid] = useState("");
  const [busy, setBusy] = useState(false);
  const [since, setSince] = useState<number | null>(deploy.asked ? Date.now() : null);
  useEffect(() => {
    if (since === null) return;
    const timer = setInterval(() => {
      if (Date.now() - since > GIVE_UP * 1000) { clearInterval(timer); setSince(null); setSaid("It’s taking longer than usual. Look again in a minute."); return; }
      void again().then((now) => {
        if (!now) return;
        if (now.running !== from) { clearInterval(timer); location.reload(); }
        else if (now.deploy?.put_back) { clearInterval(timer); setSince(null); setSaid("The new version didn’t pass its checks, so Hub put the earlier one back."); }
      });
    }, FOLLOW);
    return () => clearInterval(timer);
  }, [since]);
  const ready = deploy.ready!;
  async function tap() {
    setBusy(true);
    const answer = await api.post("deploy");
    setBusy(false);
    if (answer.error) { setSaid(answer.error); return; }
    setSaid("");
    setSince(Date.now());
  }
  const going = since !== null;
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

export function Versions() {
  const [found, setFound] = useState<Releases | null>(null);
  const [failed, setFailed] = useState(false);
  const again = () => api.versions().then((now) => { setFound(now); return now; }, () => null);
  useEffect(() => { api.versions().then(setFound, () => setFailed(true)); }, []);
  const now = found?.versions.find((r) => r.running);
  return (
    <div class="wrap">
      <Top />
      <button type="button" class="uplink" onClick={() => go("system")}>System</button>
      <h1 class="say">Versions</h1>
      <p class="sub">{failed ? "Can’t reach Hub to read the list." : !found ? "Reading…"
        : now ? `Running: ${now.title}.` : "Each release of Hub, newest first."}</p>
      {found?.deploy?.put_back && !found.deploy.ready && (
        <p class="quiet">The last update ({found.deploy.put_back}) didn’t pass its checks, so Hub put the earlier version back.</p>
      )}
      {found?.deploy?.ready && <Ready deploy={found.deploy} from={found.running} again={again} />}
      {found?.versions.map((release) => <Entry release={release} key={release.sha ?? "next"} />)}
    </div>
  );
}
