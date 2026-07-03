// Vercel serverless function: /api/agent -> one model turn.
//
// SELF-CONTAINED ON PURPOSE: no relative imports. Vercel builds this with
// package.json `"type": "module"`, and an extensionless relative import (e.g.
// `../server/agentProxy`) fails to resolve under native ESM at runtime →
// FUNCTION_INVOCATION_FAILED before the handler even runs. Keeping this file
// import-free removes that entire failure mode.
//
// The identical logic lives in web/server/agentProxy.ts for the Vite dev
// middleware (esbuild resolves imports there). Keep the two in sync.

export const config = { maxDuration: 300 }; // Pro ceiling; Hobby caps at 60.

const DEFAULT_MODEL = "gpt-5.5";
const DEFAULT_BASE_URL = "https://api.openai.com/v1";

function hostOf(u?: string): string | null {
  if (!u) return null;
  try {
    return new URL(u).host;
  } catch {
    return null;
  }
}

function checkAccess(ctx: { origin?: string; referer?: string; host?: string }) {
  const originHost = hostOf(ctx.origin) ?? hostOf(ctx.referer);
  if (originHost && ctx.host && originHost !== ctx.host) {
    return { status: 403, body: { error: "Cross-origin requests are not allowed." } };
  }
  return null;
}

async function runAgentProxy(payload: { messages?: unknown; tools?: unknown }) {
  const apiKey = process.env.OPENAI_API_KEY;
  if (!apiKey) {
    return { status: 500, body: { error: "OPENAI_API_KEY is not set on the server." } };
  }
  const baseUrl = (process.env.OPENAI_BASE_URL || DEFAULT_BASE_URL).replace(/\/+$/, "");
  const model = process.env.OPENAI_MODEL || DEFAULT_MODEL;

  const messages = payload?.messages;
  const tools = payload?.tools;
  if (!Array.isArray(messages) || messages.length === 0) {
    return { status: 400, body: { error: "messages[] is required." } };
  }

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${apiKey}`,
  };
  if (process.env.OPENROUTER_REFERER) headers["HTTP-Referer"] = process.env.OPENROUTER_REFERER;
  if (process.env.OPENROUTER_TITLE) headers["X-Title"] = process.env.OPENROUTER_TITLE;

  const requestBody: Record<string, unknown> = { model, messages };
  const temp = process.env.OPENAI_TEMPERATURE;
  if (temp !== undefined && temp !== "") {
    const n = Number(temp);
    if (!Number.isNaN(n)) requestBody.temperature = n;
  }
  if (Array.isArray(tools) && tools.length) {
    requestBody.tools = tools;
    requestBody.tool_choice = "auto";
  }

  let resp: { ok: boolean; status: number; json: () => Promise<unknown> };
  try {
    resp = await fetch(`${baseUrl}/chat/completions`, {
      method: "POST",
      headers,
      body: JSON.stringify(requestBody),
    });
  } catch (e) {
    return { status: 502, body: { error: `Upstream request failed: ${String(e)}` } };
  }

  const data = (await resp.json().catch(() => ({}))) as {
    error?: { message?: string };
    choices?: { message?: unknown }[];
  };
  if (!resp.ok) {
    const msg = data?.error?.message || `Upstream error ${resp.status}.`;
    return { status: resp.status, body: { error: msg } };
  }
  const message = data?.choices?.[0]?.message;
  if (!message) {
    return { status: 502, body: { error: "No message in upstream response." } };
  }
  return { status: 200, body: { message } };
}

export default async function handler(req: any, res: any) {
  // Health check: open /api/agent in a browser to confirm the function loads
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
