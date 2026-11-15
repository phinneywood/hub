// Placeholder: shows that Preact, signals, TypeScript and the stylesheet all reach the built files.
// The real app replaces everything below the imports.
import { render } from "preact";
import { signal } from "@preact/signals";
import "./app.css";

const taps = signal(0);

function App() {
  return (
    <main class="placeholder">
      <h1>Hub</h1>
      <button type="button" onClick={() => taps.value++}>
        Tapped {taps} {taps.value === 1 ? "time" : "times"}
      </button>
    </main>
  );
}

const root = document.getElementById("app");
if (root) render(<App />, root);
