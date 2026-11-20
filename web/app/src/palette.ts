// The palette on screen. Chosen by hand in System > The look, or, by default, picked by Hub from what is going on in the
// household's life (hub/app/mood.py, sent with the day): Clay for a celebration, Evergreen for time off, Dusk for an
// ordinary day. Hub's pick is worn when the app opens and when it comes back to the front, never while someone is
// looking at it, so the screen never changes colour under a finger. The one live rule: Clay while a light show is on.
import { computed, effect, signal } from "@preact/signals";
import { day, loadDay } from "./day";
import { palette, PALETTES, paletteChoice, seen } from "./state";
import type { Palette } from "./state";

interface Mood { palette: Palette; name: string; why: string }

const held = signal<Mood | null>(null);      // Hub's pick as last taken up
let fresh = true;                             // the app has just opened or come back: the next pick may be worn

const known = (p: string): p is Palette => PALETTES.some(([id]) => id === p);

/** Hub's pick for now, with why in a few words ("for the weekend"), or null when the palette is chosen by hand. */
export const mood = computed<Mood | null>(() => {
  if (paletteChoice.value !== "auto") return null;
  if (seen.value?.show) return { palette: "clay", name: "Clay", why: "while the light show is on" };
  return held.value;
});

export function watchPalette() {
  effect(() => {
    const said = day.value?.mood;
    if (!said || !known(said.palette) || !fresh) return;
    held.value = { palette: said.palette, name: said.name, why: said.why };
    fresh = false;
  });
  // Back at the front: read the day now and wear its pick then, or not at all until the next return, so a later
  // poll never repaints under a finger.
  let expire = 0;
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) { fresh = false; return; }
    fresh = true;
    void loadDay();
    clearTimeout(expire);
    expire = window.setTimeout(() => (fresh = false), 6000);
  });
  effect(() => {
    const choice = paletteChoice.value;
    const picked = choice === "auto" ? mood.value?.palette : choice;
    if (picked) palette.value = picked;
  });
}
