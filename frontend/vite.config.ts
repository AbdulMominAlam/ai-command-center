import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const BACKEND = process.env.BACKEND_URL ?? "http://localhost:8000";

// The browser only talks to the Vite server, which forwards API paths to FastAPI.
// Same origin means the session cookie just works and no CORS setup is needed.
// Pages use hash URLs (#/tasks), so they never collide with these API paths.
const apiPaths = ["/auth", "/me", "/today", "/tasks", "/chat", "/actions", "/sync", "/evals"];

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: Object.fromEntries(apiPaths.map((path) => [path, { target: BACKEND }])),
  },
});
