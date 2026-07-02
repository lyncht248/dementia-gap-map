import path from "node:path";
import { defineConfig, loadEnv, type PluginOption } from "vite";
import react from "@vitejs/plugin-react";
import { checkAccess, runAgentProxy } from "./server/agentProxy";

const ENV_KEYS = [
  "OPENAI_API_KEY",
  "OPENAI_BASE_URL",
  "OPENAI_MODEL",
  "OPENAI_TEMPERATURE",
  "OPENROUTER_REFERER",
  "OPENROUTER_TITLE",
];

// Serve POST /api/agent during `vite dev` using the same proxy core Vercel runs,
// so the agent works locally without `vercel dev`.
function agentDevProxy(): PluginOption {
  return {
    name: "agent-dev-proxy",
    configureServer(server) {
      server.middlewares.use("/api/agent", async (req, res) => {
        if (req.method === "GET") {
          res.statusCode = 200;
          res.setHeader("Content-Type", "application/json");
          res.end(
            JSON.stringify({
              ok: true,
              hasKey: !!process.env.OPENAI_API_KEY,
              model: process.env.OPENAI_MODEL || "gpt-5.5",
              baseUrl: process.env.OPENAI_BASE_URL || "https://api.openai.com/v1",
            })
          );
          return;
        }
        if (req.method !== "POST") {
          res.statusCode = 405;
          res.end("Method not allowed");
          return;
        }
        try {
          const gate = checkAccess({
            origin: req.headers.origin,
            referer: req.headers.referer,
            host: req.headers.host,
          });
          if (gate) {
            res.statusCode = gate.status;
            res.setHeader("Content-Type", "application/json");
            res.end(JSON.stringify(gate.body));
            return;
          }
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
  // Check both web/ and the repo root above it, so a root-level .env works too.
  const cwd = process.cwd();
  const env = { ...loadEnv(mode, path.dirname(cwd), ""), ...loadEnv(mode, cwd, "") };
  for (const k of ENV_KEYS) {
    if (env[k] && !process.env[k]) process.env[k] = env[k];
  }
  return { plugins: [react(), agentDevProxy()] };
});
