import { defineConfig } from "vite";
import preact from "@preact/preset-vite";

// Builds the Hub app into hub/static/app as two fixed-name files, app.js and app.css, which are committed.
// Hub serves them as plain static files; nothing here runs on the Mac that hosts Hub.
//
// Hub's content policy is `default-src 'self'`: no inline script or style, no eval, no data: addresses.
// So the styles leave as their own file, nothing is inlined, and there is one script with no helper chunks.
//
// hub/static/app also holds files the build does not own (fonts/, later sw.js). It never empties that
// folder; record-build.mjs removes only its own three files first. Naming the folder as publicDir
// (without copying it) lets the styles refer to those files from the folder's root, as in
// url("/fonts/Jost.ttf"): Vite checks the file exists and writes the address under `base`.
export default defineConfig({
  plugins: [preact()],
  base: "/static/app/",
  publicDir: "../../hub/static/app",
  build: {
    outDir: "../../hub/static/app",
    emptyOutDir: false,
    copyPublicDir: false,
    target: ["es2022", "safari17", "ios17", "chrome120"],
    sourcemap: false,
    cssCodeSplit: false,
    modulePreload: false,
    assetsInlineLimit: 0,
    rollupOptions: {
      input: "src/main.tsx",
      output: { entryFileNames: "app.js", assetFileNames: "app.[ext]", codeSplitting: false },
    },
  },
});
