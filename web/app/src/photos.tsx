// Photos: each month's best photos, chosen on this Mac by the photo curator, to mark Keep, Print or Skip.
// Read from Hub (hub/app/photos.py) when it is looked at. Previews come from Hub, which reads them from the
// curator's folder; nothing about a photo is kept on the device.
import { useEffect, useState } from "preact/hooks";
import * as api from "./api";
import { Top } from "./screens";
import { go, me, say } from "./state";
import type { Photo, PhotoMark, Photos as PhotosData } from "./types";

const DAY = new Intl.DateTimeFormat(undefined, { weekday: "short", month: "short", day: "numeric" });
const MARKS: [Exclude<PhotoMark, "">, string][] = [["keep", "Keep"], ["print", "Print"], ["skip", "Skip"]];

function when(photo: Photo) {
  const at = new Date(photo.taken);
  return isNaN(at.getTime()) ? "" : DAY.format(at);
}

function Tile(props: { photo: Photo; locked: boolean; mark: (photo: Photo, mark: string) => void }) {
  const p = props.photo;
  const note = [when(p), p.people.join(", "), p.favorite ? "favorite" : ""].filter(Boolean).join(" · ");
  const done = p.saved ? "Saved for printing" : p.in_album ? "In the album" : "";
  return (
    <figure class={"shot" + (p.mark ? " " + p.mark : "")}>
      <img src={`/app/api/photos/thumb/${p.id}`} alt={note || "A photo"} loading="lazy" decoding="async" />
      <figcaption>
        <span class="shot-note">{note}{done ? ` · ${done}` : ""}</span>
        <div class="shot-marks" role="group" aria-label="Mark this photo">
          {MARKS.map(([value, name]) => (
            <button type="button" key={value} class={"choice" + (p.mark === value ? " on" : "")} aria-pressed={p.mark === value}
              disabled={props.locked} onClick={() => props.mark(p, p.mark === value ? "clear" : value)}>{name}</button>
          ))}
        </div>
      </figcaption>
    </figure>
  );
}

export function Photos() {
  const [found, setFound] = useState<PhotosData | null>(null);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [month, setMonth] = useState("");
  const [more, setMore] = useState(false);
  const load = (which = month) => api.photos(which).then((now) => { setFound(now); setFailed(false); }, () => setFailed(true));
  useEffect(() => { void load(); }, [month]);

  async function mark(photo: Photo, value: string) {
    setBusy(true);
    const answer = await api.post("photos/" + value, { key: photo.id });
    setBusy(false);
    say(answer.error ?? answer.said ?? "Done");
    await load();
  }

  const locked = busy || !me.value.signed_in;
  const at = found?.months.findIndex((m) => m.month === found.month) ?? -1;
  const counts = found?.counts;
  return (
    <div class="wrap">
      <Top />
      <button type="button" class="uplink" onClick={() => go("system")}>System</button>
      <h1 class="say">{found?.ready ? `The best of ${found.name}` : "Photos"}</h1>
      <p class="sub">{failed ? (me.value.signed_in ? "Can’t reach Hub to read them." : "Open this on your phone or laptop to see the photos.")
        : !found ? "Reading…"
        : !found.ready ? "No month has been chosen yet. The curator chooses each month on the 1st."
        : `${found.picks.length} chosen from ${found.looked_at ?? 0} photos, on this Mac. Keep puts one in the month’s album in Photos; Print also saves a full-size copy.`}</p>
      {found?.ready && (
        <div class="btns tight">
          <button type="button" class="btn ghost" disabled={at < 0 || at >= found.months.length - 1}
            onClick={() => { setMore(false); setMonth(found.months[at + 1]?.month ?? ""); }}>Earlier</button>
          <button type="button" class="btn ghost" disabled={at <= 0}
            onClick={() => { setMore(false); setMonth(found.months[at - 1]?.month ?? ""); }}>Later</button>
        </div>
      )}
      {found?.ready && counts && (
        <p class="quiet">{`${counts.keep} kept · ${counts.print} to print · ${counts.skip} skipped`}
          {(found.waiting ?? 0) > 0 ? ` · ${found.waiting} waiting for the Mac to add or save` : ""}</p>
      )}
      {found?.ready && (
        <section class="block shots">
          {found.picks.map((p) => <Tile key={p.id} photo={p} locked={locked} mark={(photo, value) => void mark(photo, value)} />)}
        </section>
      )}
      {found?.ready && found.more.length > 0 && (
        <section class="block">
          <h2>More from {found.name}</h2>
          {!more ? <button type="button" class="btn ghost" onClick={() => setMore(true)}>Show {found.more.length} more</button>
            : <div class="shots">{found.more.map((p) => <Tile key={p.id} photo={p} locked={locked} mark={(photo, value) => void mark(photo, value)} />)}</div>}
        </section>
      )}
      {found?.ready && <p class="quiet">Clearing a mark doesn’t take a photo back out of the album; do that in Photos.</p>}
    </div>
  );
}
