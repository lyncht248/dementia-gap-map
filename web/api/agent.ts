// Vercel serverless function: POST /api/agent -> one model turn.
// Root directory for the Vercel project is `web/`, so this lives at web/api/.
import { runAgentProxy } from "./_agent-core";

export default async function handler(req: any, res: any) {
  if (req.method !== "POST") {
    res.status(405).json({ error: "Method not allowed" });
    return;
  }
  try {
    const body =
      typeof req.body === "string" ? JSON.parse(req.body || "{}") : req.body ?? {};
    const result = await runAgentProxy(body);
    res.status(result.status).json(result.body);
  } catch (e) {
    res.status(500).json({ error: String(e instanceof Error ? e.message : e) });
  }
}
