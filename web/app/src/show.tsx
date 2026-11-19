// The light show's card, at the top of Ask while a show is on (hub/tools/house/lightshow.py): its music with play,
// pause, next and volume, the lights moved earlier or later against it, and End show. The show's music is one of the
// house's musics, so its buttons are the ones every music has.
import { Sleeve } from "./screens";
import { dragGroupVolume, endShow, seen, setGroupVolume, setShowTiming, streamPlay, streamSkip } from "./state";
import type { Timing } from "./types";
import { Icon, Slider } from "./ui";

/** How the lights sit against the music: "In time", "150 ms earlier", "100 ms later". */
export function timingWords(ms: number): string {
  const n = Math.round(ms);
  return n === 0 ? "In time" : `${Math.abs(n)} ms ${n > 0 ? "earlier" : "later"}`;
}

export function ShowCard() {
  const show = seen.value?.show;
  if (!show) return null;
  const speaker = show.speaker;
  return (
    <section class={"showcard" + (show.playing ? "" : " paused")} aria-label="Light show">
      <p class="show-kicker">Light show</p>
      <div class="tune-head">
        <Sleeve name={show.album || show.title || "Light show"} />
        <div class="tune-text">
          <b>{show.title || (show.playing ? "Music" : "Paused")}</b>
          {show.artist && <span>{show.artist}</span>}
        </div>
        {speaker && <>
          <button type="button" class="mini" aria-label={show.playing ? "Pause the light show’s music" : "Play the light show’s music"}
            onClick={() => void streamPlay(speaker, !show.playing)}><Icon name={show.playing ? "pause" : "play"} /></button>
          <button type="button" class="mini bare" aria-label="Next song" onClick={() => void streamSkip(speaker, "next")}><Icon name="next" /></button>
        </>}
      </div>
      {speaker && show.volume !== null && (
        <div class="ctl vol"><div class="ctl-name"><b>Volume</b><span>{show.volume}%</span></div>
          <Slider value={show.volume} sound label="Volume of the light show"
            onDrag={(v) => dragGroupVolume(speaker, v)} onSet={(v) => void setGroupVolume(speaker, v)} /></div>
      )}
      {show.timing && <TimingRow timing={show.timing} />}
      <button type="button" class="btn end-show" onClick={() => void endShow()}>End show</button>
    </section>
  );
}

/** The lights a step later or earlier than the music; Home Assistant keeps where they are for the next show. */
function TimingRow({ timing }: { timing: Timing }) {
  return (
    <div class="ctl acts show-timing">
      <div class="ctl-name"><b>Lights</b><span>{timingWords(timing.value)}</span></div>
      <div class="pills">
        <button type="button" class="pill" disabled={timing.value <= timing.min}
          onClick={() => void setShowTiming(timing.value - timing.step)}>Later</button>
        <button type="button" class="pill" disabled={timing.value >= timing.max}
          onClick={() => void setShowTiming(timing.value + timing.step)}>Earlier</button>
      </div>
    </div>
  );
}
