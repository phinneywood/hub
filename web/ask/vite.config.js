import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Builds the Ask page into hub/static/ask as two fixed-name files, which are committed.
// Hub serves them as plain static files; nothing here runs on the Mac that hosts Hub.
export default defineConfig({
  plugins: [react()],
  define: { "process.env.NODE_ENV": '"production"' },
  build: {
    outDir: "../../hub/static/ask",
    emptyOutDir: true,
    sourcemap: false,
    cssCodeSplit: false,
    modulePreload: false,
    rollupOptions: {
      input: "src/main.jsx",
      output: { entryFileNames: "ask.js", assetFileNames: "ask.[ext]", codeSplitting: false },
    },
  },
});
