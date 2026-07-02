import { defineConfig, loadEnv, type PluginOption } from "vite";
import react from "@vitejs/plugin-react";
import { runAgentProxy } from "./api/_agent-core";

// Serve POST /api/agent during `vite dev` using the same proxy core Vercel runs,
// so the agent works locally without `vercel dev`.
function agentDevProxy(): PluginOption {
  return {
    name: "agent-dev-proxy",
    configureServer(server) {
      server.middlewares.use("/api/agent", async (req, res) => {
        if (req.method !== "POST") {
          res.statusCode = 405;
          res.end("Method not allowed");
          return;
        }
        try {
          const chunks: Buffer[] = [];
          for await (const chunk of req) chunks.push(chunk as Buffer);
          const raw = Buffer.concat(chunks).toString("utf8") || "{}";
          const result = await runAgentProxy(JSON.parse(raw));
          res.statusCode = result.status;
          res.setHeader("Content-Type", "application/json");
          res.end(JSON.stringify(result.body));
        } catch (e) {
          res.statusCode = 500;
          res.setHeader("Content-Type", "application/json");
          res.end(JSON.stringify({ error: String(e) }));
        }
      });
    },
  };
}

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
  // Load .env / .env.local (unprefixed) so the dev proxy can read the API key.
  const env = loadEnv(mode, process.cwd(), "");
  for (const k of [
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_MODEL",
    "OPENROUTER_REFERER",
    "OPENROUTER_TITLE",
  ]) {
    if (env[k] && !process.env[k]) process.env[k] = env[k];
  }
  return { plugins: [react(), agentDevProxy()] };
});
