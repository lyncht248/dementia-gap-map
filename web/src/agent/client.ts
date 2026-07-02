// Client-side agent loop. The browser orchestrates tool use: it asks the
// serverless proxy for one model turn, runs any tool calls locally (DuckDB +
// map control), feeds results back, and repeats until the model answers.
import { SYSTEM_PROMPT } from "./systemPrompt";
import { TOOLS, dispatchTool } from "./tools";
import { schemaText } from "../lib/duckdb";
import type { AgentController } from "./types";

export interface ToolCall {
  id: string;
  type: "function";
  function: { name: string; arguments: string };
}

export interface ChatMessage {
  role: "system" | "user" | "assistant" | "tool";
  content: string | null;
  tool_calls?: ToolCall[];
  tool_call_id?: string;
  name?: string;
}

export interface ToolEvent {
  name: string;
  args: Record<string, unknown>;
  result?: unknown;
}

export interface AgentCallbacks {
  onToolCall?: (e: ToolEvent) => void;
  onToolResult?: (e: ToolEvent) => void;
}

const MAX_STEPS = 8;
const AGENT_ENDPOINT = "/api/agent";

/** Empty transcript; the system message (with live schema) is injected lazily by
 * runConversation on the first turn so it always reflects the current data. */
export function initialMessages(): ChatMessage[] {
  return [];
}

let systemMessage: ChatMessage | null = null;

async function getSystemMessage(): Promise<ChatMessage> {
  if (systemMessage) return systemMessage;
  let live: string;
  try {
    live = await schemaText();
  } catch {
    live = "(live schema unavailable — verify columns with describe_schema)";
  }
  systemMessage = {
    role: "system",
    content: `${SYSTEM_PROMPT}\n\n## LIVE SCHEMA (authoritative — current columns & types)\n${live}`,
  };
  return systemMessage;
}

async function callProxy(
  messages: ChatMessage[],
  signal?: AbortSignal
): Promise<ChatMessage> {
  const res = await fetch(AGENT_ENDPOINT, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ messages, tools: TOOLS }),
    signal,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`Agent request failed (${res.status}). ${text}`.trim());
  }
  const data = (await res.json()) as { message?: ChatMessage; error?: string };
  if (data.error) throw new Error(data.error);
  if (!data.message) throw new Error("Agent proxy returned no message.");
  return data.message;
}

/**
 * Run the model/tool loop starting from `history` (which must include the system
 * message and the just-added user turn). Returns the full updated transcript and
 * the assistant's final text.
 */
export async function runConversation(
  history: ChatMessage[],
  controller: AgentController,
  cb: AgentCallbacks = {},
  signal?: AbortSignal
): Promise<{ messages: ChatMessage[]; finalText: string }> {
  // Ensure the (live-schema) system message is present at the head of the convo.
  const convo =
    history[0]?.role === "system"
      ? [...history]
      : [await getSystemMessage(), ...history];

  for (let step = 0; step < MAX_STEPS; step++) {
    const assistant = await callProxy(convo, signal);
    convo.push(assistant);

    const calls = assistant.tool_calls ?? [];
    if (calls.length === 0) {
      return { messages: convo, finalText: assistant.content ?? "" };
    }

    for (const call of calls) {
      let args: Record<string, unknown> = {};
      try {
        args = JSON.parse(call.function.arguments || "{}");
      } catch {
        args = {};
      }
      cb.onToolCall?.({ name: call.function.name, args });
      let result: unknown;
      try {
        result = await dispatchTool(controller, call.function.name, args);
      } catch (e) {
        result = { error: String(e instanceof Error ? e.message : e) };
      }
      cb.onToolResult?.({ name: call.function.name, args, result });
      convo.push({
        role: "tool",
        tool_call_id: call.id,
        name: call.function.name,
        content: JSON.stringify(result).slice(0, 100_000),
      });
    }
  }

  return {
    messages: convo,
    finalText:
      "I ran out of reasoning steps before finishing. Try narrowing the question.",
  };
}
