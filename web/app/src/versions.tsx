// Versions: each release of Hub, newest first, what it changed in plain words, and links to its code.
import { useEffect, useState } from "preact/hooks";
import * as api from "./api";
import { go } from "./state";
import { Top } from "./screens";
import type { Release, Releases } from "./types";

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

export function Versions() {
  const [found, setFound] = useState<Releases | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => { api.versions().then(setFound, () => setFailed(true)); }, []);
  const now = found?.versions.find((r) => r.running);
  return (
    <div class="wrap">
      <Top />
      <button type="button" class="uplink" onClick={() => go("system")}>System</button>
      <h1 class="say">Versions</h1>
      <p class="sub">{failed ? "Can’t reach Hub to read the list." : !found ? "Reading…"
        : now ? `Running: ${now.title}.` : "Each release of Hub, newest first."}</p>
      {found?.versions.map((release) => <Entry release={release} key={release.sha ?? "next"} />)}
    </div>
  );
}
