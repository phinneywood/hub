// The small parts everything else is made of: drawings, a switch, a slider, a sheet and the line that says
// what just happened.
import type { ComponentChildren, JSX } from "preact";
import { useEffect, useRef } from "preact/hooks";
import { sheet, toast, undo } from "./state";

const PATHS = {
  more: <><circle cx="5.5" cy="12" r="1.5" /><circle cx="12" cy="12" r="1.5" /><circle cx="18.5" cy="12" r="1.5" /></>,
  play: <path d="M8 5.6v12.8a.6.6 0 0 0 .92.5l10-6.4a.6.6 0 0 0 0-1l-10-6.4a.6.6 0 0 0-.92.5z" />,
  pause: <><rect x="6.5" y="5" width="4" height="14" rx="1.2" /><rect x="13.5" y="5" width="4" height="14" rx="1.2" /></>,
  next: <><path d="M5.5 6.2v11.6a.5.5 0 0 0 .78.42L15 12.4a.5.5 0 0 0 0-.84L6.28 5.78a.5.5 0 0 0-.78.42z" /><rect x="16.5" y="5.5" width="2.4" height="13" rx="1.1" /></>,
  previous: <><path d="M18.5 6.2v11.6a.5.5 0 0 1-.78.42L9 12.4a.5.5 0 0 1 0-.84l8.72-5.78a.5.5 0 0 1 .78.42z" /><rect x="5.1" y="5.5" width="2.4" height="13" rx="1.1" /></>,
  close: <path d="M6.5 6.5l11 11M17.5 6.5l-11 11" />,
  wave: <path d="M4.5 10.5v3M8.25 7.5v9M12 4.5v15M15.75 8v8M19.5 10.5v3" />,
  bolt: <path d="M13 3 5.5 13.5H11L10 21l8-10.5h-5.5z" />,
};
const SOLID = new Set(["more", "play", "pause", "next", "previous"]);

export function Icon({ name }: { name: keyof typeof PATHS }) {
  return <svg class={SOLID.has(name) ? "ic solid" : "ic"} viewBox="0 0 24 24" aria-hidden="true">{PATHS[name]}</svg>;
}

/** Three bars: music is playing here. */
export function Bars() {
  return <span class="eq" role="img" aria-label="Playing"><i /><i /><i /></span>;
}

export function Switch(props: { on: boolean; label: string; sound?: boolean; off?: boolean; onChange: (on: boolean) => void }) {
  return (
    <button type="button" role="switch" aria-checked={props.on} aria-label={props.label} disabled={props.off}
      class={"switch" + (props.on ? " on" : "") + (props.sound ? " snd" : "")} onClick={() => props.onChange(!props.on)} />
  );
}

/** A slider that shows the level while a finger moves it and asks for the change once, when the finger lifts. */
export function Slider(props: { value: number; label: string; sound?: boolean; dim?: boolean; big?: boolean;
  onDrag: (value: number) => void; onSet: (value: number) => void }) {
  const value = Math.max(0, Math.min(100, Math.round(props.value)));
  const style = { "--v": value } as JSX.CSSProperties;
  const input = (
    <input type="range" min="0" max="100" value={value} aria-label={props.label}
      onInput={(e) => props.onDrag(Number(e.currentTarget.value))} onChange={(e) => props.onSet(Number(e.currentTarget.value))} />
  );
  if (props.big) {
    return (
      <div class={"dimmer" + (value ? "" : " zero")} style={style}>
        <div class="dimmer-fill" />
        <span class="dimmer-num">{value ? value + "%" : "Off"}</span>
        {input}
      </div>
    );
  }
  return <div class={"slim" + (props.sound ? " snd" : "") + (props.dim ? " off" : "")} style={style}><b />{input}</div>;
}

/** A sheet that rises over where you are. Escape, the dimmed area or the close button put it away. */
export function SheetFrame(props: { title?: string; sub?: string; tall?: boolean; children: ComponentChildren }) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const frame = requestAnimationFrame(() => box.current?.classList.add("in"));
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") sheet.value = null; };
    document.addEventListener("keydown", key);
    box.current?.focus();
    return () => { cancelAnimationFrame(frame); document.removeEventListener("keydown", key); };
  }, []);
  return (
    <>
      <div class="scrim in" onClick={() => (sheet.value = null)} />
      <div class={"sheet" + (props.tall ? " tall" : "")} role="dialog" aria-modal="true" aria-label={props.title ?? "Ask"} tabIndex={-1} ref={box}>
        <div class="grab" />
        {props.title !== undefined && (
          <div class="sheet-head">
            <div><h1>{props.title}</h1>{props.sub && <p>{props.sub}</p>}</div>
            <button type="button" class="x" aria-label="Close" onClick={() => (sheet.value = null)}><Icon name="close" /></button>
          </div>
        )}
        {props.children}
      </div>
    </>
  );
}

export function ToastLine() {
  const now = toast.value;
  useEffect(() => {
    if (!now) return;
    const timer = window.setTimeout(() => { if (toast.value?.n === now.n) toast.value = null; }, now.batch ? 6000 : 3500);
    return () => clearTimeout(timer);
  }, [now?.n]);
  if (!now) return null;
  return (
    <div class="toast in" role="status" key={now.n}>
      <span>{now.text}</span>
      {now.batch && <button type="button" onClick={() => void undo(now.batch!)}>Undo</button>}
    </div>
  );
}
