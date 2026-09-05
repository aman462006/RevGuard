/// <reference types="vitest" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The dashboard talks to the FastAPI backend over HTTP (base URL from VITE_API_BASE_URL,
// default http://localhost:8000). The backend enables CORS for the dev server origin.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173 },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
