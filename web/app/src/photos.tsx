// Best of the month, on the Archive screen: the photos the curator chose on this Mac, to mark Keep, Print or Skip.
// Read from Hub (hub/app/photos.py) when the Archive is looked at. Previews come from Hub, which reads them from the
// curator's folder; nothing about a photo is kept on the device.
import { useEffect, useState } from "preact/hooks";
import * as api from "./api";
import { me, say } from "./state";
import type { Photo, PhotoMark, Photos as PhotosData } from "./types";

const DAY = new Intl.DateTimeFormat(undefined, { weekday: "short", month: "short", day: "numeric" });
const MARKS: [Exclude<PhotoMark, "">, string][] = [["keep", "Keep"], ["print", "Print"], ["skip", "Skip"]];
const FIRST = 6;            // the month's picks shown before "Show all"

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

/** The month the curator chose most recently, or the one moved to with Earlier and Later. Nothing shows until the
 * curator has chosen a month. */
export function BestOfMonth({ start = "" }: { start?: string }) {
  const [found, setFound] = useState<PhotosData | null>(null);
  const [busy, setBusy] = useState(false);
  const [month, setMonth] = useState(start);
  const [more, setMore] = useState(false);
  const [all, setAll] = useState(false);
  const load = (which = month) => api.photos(which).then(setFound, () => setFound(null));
  useEffect(() => { void load(); }, [month]);

  async function mark(photo: Photo, value: string) {
    setBusy(true);
    const answer = await api.post("photos/" + value, { key: photo.id });
    setBusy(false);
    say(answer.error ?? answer.said ?? "Done");
    await load();
  }

  if (!found?.ready) return null;
  const locked = busy || !me.value.signed_in;
  const at = found.months.findIndex((m) => m.month === found.month);
  const counts = found.counts;
  return (
    <section class="block best" aria-label="Best of the month">
      <div class="block-head">
        <h2>The best of {found.name}</h2>
        <div class="pager">
          <button type="button" class="linkish" disabled={at < 0 || at >= found.months.length - 1}
            onClick={() => { setMore(false); setAll(false); setMonth(found.months[at + 1]?.month ?? ""); }}>Earlier</button>
          <button type="button" class="linkish" disabled={at <= 0}
            onClick={() => { setMore(false); setAll(false); setMonth(found.months[at - 1]?.month ?? ""); }}>Later</button>
        </div>
      </div>
      <p class="quiet-line">{`${found.picks.length} chosen${found.looked_at ? ` from ${found.looked_at} photos on this Mac` : ""}. Keep puts one in the month’s album in Photos; Print also saves a full-size copy.`}
        {counts ? ` ${counts.keep} kept · ${counts.print} to print · ${counts.skip} skipped.` : ""}
        {(found.waiting ?? 0) > 0 ? ` ${found.waiting} waiting for the Mac to add or save.` : ""}</p>
      <div class="shots">{(all ? found.picks : found.picks.slice(0, FIRST)).map((p) =>
        <Tile key={p.id} photo={p} locked={locked} mark={(photo, value) => void mark(photo, value)} />)}</div>
      {!all && found.picks.length > FIRST && <button type="button" class="btn ghost more-btn" onClick={() => setAll(true)}>{`Show all ${found.picks.length}`}</button>}
      {all && found.more.length > 0 && (!more
        ? <button type="button" class="btn ghost more-btn" onClick={() => setMore(true)}>Show {found.more.length} more</button>
        : <div class="shots">{found.more.map((p) => <Tile key={p.id} photo={p} locked={locked} mark={(photo, value) => void mark(photo, value)} />)}</div>)}
    </section>
  );
}
