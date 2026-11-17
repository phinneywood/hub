// The part of the screen a person can see. On a phone the keyboard covers the bottom of the page without making the
// page any shorter, so while the keyboard is up Ask sizes itself to what is left (--vvh, moved down by --vvt) and keeps
// its field on the keyboard; without it, Ask is pinned above the bar (app.css). `kb` on the root says the keyboard is up, so the bar under Ask steps aside while it is.

const KEYBOARD = 120;       // a visible area this much shorter than the window is a keyboard, not a toolbar

export function watchViewport() {
  const seen = window.visualViewport;
  const root = document.documentElement;
  if (!seen) return;
  let frame = 0, typed = false;
  const fit = () => {
    frame = 0;
    root.style.setProperty("--vvh", `${Math.round(seen.height)}px`);
    root.style.setProperty("--vvt", `${Math.round(seen.offsetTop)}px`);
    const zoomed = seen.scale > 1.01;          // a pinch makes the visible part smaller too; that is not a keyboard
    const keyboard = !zoomed && window.innerHeight - seen.height > KEYBOARD;
    root.classList.toggle("kb", keyboard);
    // When the keyboard goes, an iPhone can leave the page scrolled by what it moved to show the field. Ask's page
    // never scrolls itself (only its conversation does), so put it back: otherwise Ask stays pushed under the bar.
    if (typed && !keyboard && window.scrollY && document.querySelector(".app.asking")) window.scrollTo(0, 0);
    typed = keyboard;
  };
  const soon = () => { if (!frame) frame = requestAnimationFrame(fit); };
  seen.addEventListener("resize", soon);
  seen.addEventListener("scroll", soon);
  fit();
}

/** Keep --dockh at the height of the bar at the bottom, so what sits above it never hides under it. */
export function watchDock() {
  const dock = document.querySelector<HTMLElement>(".dock");
  if (!dock || !("ResizeObserver" in window)) return;
  new ResizeObserver(() => document.documentElement.style.setProperty("--dockh", `${dock.offsetHeight}px`)).observe(dock);
}
