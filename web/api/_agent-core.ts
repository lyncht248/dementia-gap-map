// Stateless proxy core: relays one model turn to an OpenAI-compatible
// /chat/completions endpoint. Shared by the Vercel function (api/agent.ts) and
// the Vite dev middleware (vite.config.ts) so local dev and prod behave the same.
//
// Config via env (server-side only — the key never reaches the browser):
//   OPENAI_API_KEY   (required)
//   OPENAI_BASE_URL  (default https://api.openai.com/v1; set to
//                     https://openrouter.ai/api/v1 for OpenRouter)
//   OPENAI_MODEL     (default gpt-5.5)
//   OPENAI_TEMPERATURE (optional; omitted by default so models that only accept
//                       their default temperature — e.g. some GPT-5-series — work)
//   OPENROUTER_REFERER / OPENROUTER_TITLE (optional attribution headers)

export interface AgentProxyPayload {
  messages?: unknown;
  tools?: unknown;
}

export interface AgentProxyResult {
  status: number;
  body: Record<string, unknown>;
}

export async function runAgentProxy(
  payload: AgentProxyPayload
): Promise<AgentProxyResult> {
  const apiKey = process.env.OPENAI_API_KEY;
  if (!apiKey) {
    return { status: 500, body: { error: "OPENAI_API_KEY is not set on the server." } };
  }

  const baseUrl = (process.env.OPENAI_BASE_URL || "https://api.openai.com/v1").replace(
    /\/+$/,
    ""
  );
  const model = process.env.OPENAI_MODEL || "gpt-5.5";

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

  const requestBody: Record<string, unknown> = {
    model,
    messages,
  };
  // Only send temperature if explicitly configured — some GPT-5-series models
  // reject a non-default temperature.
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
