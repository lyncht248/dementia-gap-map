// Vercel serverless function: POST /api/agent -> one model turn.
// Root directory for the Vercel project is `web/`, so this lives at web/api/.
// The shared proxy core lives in web/server/ (NOT api/, since Vercel drops
// underscore-prefixed helpers from the function bundle).
import {
  checkAccess,
  runAgentProxy,
  DEFAULT_MODEL,
  DEFAULT_BASE_URL,
} from "../server/agentProxy";

// Allow slower (reasoning) model turns. Plan ceilings: Hobby 60s, Pro 300s
// (up to 800s with Fluid Compute). Only actual execution time is billed.
export const config = { maxDuration: 300 };

export default async function handler(req: any, res: any) {
  // Health check: visit /api/agent in a browser to confirm the function loads
  // and whether the key/model are configured (no secret is exposed).
  if (req.method === "GET") {
    res.status(200).json({
      ok: true,
      hasKey: !!process.env.OPENAI_API_KEY,
      model: process.env.OPENAI_MODEL || DEFAULT_MODEL,
      baseUrl: process.env.OPENAI_BASE_URL || DEFAULT_BASE_URL,
    });
    return;
  }
  if (req.method !== "POST") {
    res.status(405).json({ error: "Method not allowed" });
    return;
  }
  try {
    const gate = checkAccess({
      origin: req.headers.origin,
      referer: req.headers.referer,
      host: req.headers["x-forwarded-host"] || req.headers.host,
    });
    if (gate) {
      res.status(gate.status).json(gate.body);
      return;
    }
    const body =
      typeof req.body === "string" ? JSON.parse(req.body || "{}") : req.body ?? {};
    const result = await runAgentProxy(body);
    res.status(result.status).json(result.body);
  } catch (e) {
    res.status(500).json({ error: String(e instanceof Error ? e.message : e) });
  }
}
