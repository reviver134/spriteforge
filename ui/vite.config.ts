import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The build is committed to the Python package so users never need Node.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../src/spriteforge/app/static",
    emptyOutDir: true,
    // Fonts stay files (not data: URIs) so the HTML and JS remain small and diffable.
    assetsInlineLimit: 0,
  },
  server: {
    // `npm run dev`: FastAPI keeps serving the API and the generated games.
    proxy: { "/api": "http://127.0.0.1:8765" },
  },
});
