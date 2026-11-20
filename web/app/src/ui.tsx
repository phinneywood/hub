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
  plus: <path d="M12 5.5v13M5.5 12h13" />,
  up: <path d="M12 19V5.5M6 11l6-6 6 6" />,
  check: <path d="M5 12.5l4.5 4.5L19 7.5" />,
  out: <path d="M8 16 16 8M9.5 8H16v6.5" />,
  mail: <><rect x="3.5" y="6" width="17" height="12" rx="2.2" /><path d="M4.5 7.5l7.5 5.5 7.5-5.5" /></>,
  call: <path d="M7.2 4.5h2.6l1.4 3.6-1.8 1.2a10.5 10.5 0 0 0 5.3 5.3l1.2-1.8 3.6 1.4v2.6a1.7 1.7 0 0 1-1.8 1.7A14.6 14.6 0 0 1 5.5 6.3a1.7 1.7 0 0 1 1.7-1.8z" />,
  calendar: <><rect x="4" y="5.5" width="16" height="14" rx="2.2" /><path d="M4 10h16M8.5 3.5v4M15.5 3.5v4" /></>,
  bag: <><path d="M5.5 8.5h13l-1 11h-11z" /><path d="M9 8.5V7a3 3 0 0 1 6 0v1.5" /></>,
  stop: <rect x="7" y="7" width="10" height="10" rx="2" />,
  right: <path d="M9.5 6l6 6-6 6" />,
  home: <path d="M4.5 10.8 12 4.5l7.5 6.3V19a.8.8 0 0 1-.8.8H14.6v-5.3H9.4v5.3H5.3a.8.8 0 0 1-.8-.8z" />,
  search: <><circle cx="11" cy="11" r="6.2" /><path d="M20 20l-4.6-4.6" /></>,
};
const SOLID = new Set(["more", "play", "pause", "next", "previous", "stop"]);

export function Icon({ name }: { name: keyof typeof PATHS }) {
  return <svg class={SOLID.has(name) ? "ic solid" : "ic"} viewBox="0 0 24 24" aria-hidden="true">{PATHS[name]}</svg>;
}

/** The night drift: warm lamp colours flowing slowly left to right, breathing, behind the top of Ask
 * and along the foot of the wall. It moves only by transform and opacity, so nothing is redrawn, and it stands still
 * where motion is turned down. */
export function Glow({ class: kind }: { class?: string }) {
  return <div class={"glow" + (kind ? " " + kind : "")} aria-hidden="true"><i /></div>;
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

/** A sheet that rises over where you are. Escape, the dimmed area, the close button or a finger pulling it down
 * put it away, and it goes the way it came, down. */
export function SheetFrame(props: { title?: string; sub?: string; tall?: boolean; children: ComponentChildren }) {
  const box = useRef<HTMLDivElement>(null);
  const shade = useRef<HTMLDivElement>(null);
  // A tap on the dimmed area closes the sheet only if it began there. A finger held on a room opens the room's sheet
  // under it, and the click the browser sends when that finger lifts must not close what it just opened.
  const pressed = useRef(false);
  const close = () => {
    const el = box.current;
    if (!el || el.classList.contains("out")) return;
    const mine = sheet.value;
    el.classList.add("out");
    el.classList.remove("in");
    el.style.transform = "";
    shade.current?.classList.remove("in");
    window.setTimeout(() => { if (sheet.value === mine) sheet.value = null; }, 230);
  };
  useEffect(() => {
    const frame = requestAnimationFrame(() => box.current?.classList.add("in"));
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") close(); };
    document.addEventListener("keydown", key);
    box.current?.focus();
    const el = box.current;
    // Pulled down from its top edge (or anywhere, once it is scrolled to the top), the sheet follows the finger and
    // goes when let go far enough or fast enough; otherwise it settles back. Sliders and fields keep their own drags.
    let from = -1, at = 0, moved = 0, since = 0;
    const down = (e: TouchEvent) => {
      const t = e.touches[0];
      if (!el || !t || e.touches.length > 1) return;
      const target = e.target as Element;
      if (target.closest("input, textarea, select, .dimmer, .slim, .chips, .picks, .scenes")) return;
      const top = el.getBoundingClientRect().top;
      if (el.scrollTop > 0 && t.clientY - top > 64) return;
      from = t.clientY; at = Date.now(); moved = 0;
    };
    const move = (e: TouchEvent) => {
      const t = e.touches[0];
      if (!el || from < 0 || !t) return;
      moved = Math.max(0, t.clientY - from);
      if (moved > 0 && el.scrollTop <= 0) {
        if (e.cancelable) e.preventDefault();
        if (!since) { since = 1; el.classList.add("held"); }
        el.style.transform = `translateY(${moved}px)`;
      }
    };
    const up = () => {
      if (!el || from < 0) return;
      const fast = moved / Math.max(1, Date.now() - at) > 0.6;
      el.classList.remove("held");
      if (moved > 120 || (fast && moved > 40)) close();
      else el.style.transform = "";
      from = -1; since = 0;
    };
    el?.addEventListener("touchstart", down, { passive: true });
    el?.addEventListener("touchmove", move, { passive: false });
    el?.addEventListener("touchend", up);
    el?.addEventListener("touchcancel", up);
    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener("keydown", key);
      el?.removeEventListener("touchstart", down);
      el?.removeEventListener("touchmove", move);
      el?.removeEventListener("touchend", up);
      el?.removeEventListener("touchcancel", up);
    };
  }, []);
  return (
    <>
      <div class="scrim in" ref={shade} onPointerDown={() => (pressed.current = true)}
        onClick={() => { if (pressed.current) close(); pressed.current = false; }} />
      <div class={"sheet" + (props.tall ? " tall" : "")} role="dialog" aria-modal="true" aria-label={props.title ?? "Ask"} tabIndex={-1} ref={box}>
        <div class="grab" />
        {props.title !== undefined && (
          <div class="sheet-head">
            <div><h1>{props.title}</h1>{props.sub && <p>{props.sub}</p>}</div>
            <button type="button" class="x" aria-label="Close" onClick={close}><Icon name="close" /></button>
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
    const timer = window.setTimeout(() => { if (toast.value?.n === now.n) toast.value = null; }, now.batch || now.back ? 6000 : 3500);
    return () => clearTimeout(timer);
  }, [now?.n]);
  if (!now) return null;
  return (
    <div class="toast in" role="status" key={now.n}>
      <span>{now.text}</span>
      {now.batch ? <button type="button" onClick={() => void undo(now.batch!)}>Undo</button>
        : now.back && <button type="button" onClick={() => { toast.value = null; now.back!(); }}>Undo</button>}
    </div>
  );
}
