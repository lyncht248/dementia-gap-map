import { useEffect, useRef, useState } from "react";
import type { AgentController } from "../agent/types";
import { initialMessages, runConversation, type ChatMessage } from "../agent/client";
import Markdown from "./Markdown";

type UiItem =
  | { kind: "user"; text: string }
  | { kind: "assistant"; text: string }
  | { kind: "tool"; name: string; args: Record<string, unknown>; result?: unknown }
  | { kind: "error"; text: string }
  | { kind: "note"; text: string };

interface Conversation {
  id: string;
  title: string;
  transcript: ChatMessage[]; // LLM messages incl. system
  items: UiItem[]; // rendered timeline
  busy: boolean;
}

let seq = 0;
const newId = () =>
  globalThis.crypto?.randomUUID?.() ?? `chat-${Date.now()}-${++seq}`;

const newConversation = (n: number): Conversation => ({
  id: newId(),
  title: `Chat ${n}`,
  transcript: initialMessages(),
  items: [],
  busy: false,
});

const SUGGESTIONS = [
  "Which mechanisms have active clinical development, and which are stalled?",
  "Trace the latest anti-amyloid trials back to their earliest GWAS anchors.",
  "Which pathways have strong genetics but little clinical translation?",
  "Show the strongest genetic targets for Alzheimer's and highlight them.",
];

export default function AgentPanel({
  controller,
  onMinimize,
}: {
  controller: AgentController;
  onMinimize?: () => void;
}) {
  const [convos, setConvos] = useState<Conversation[]>(() => [newConversation(1)]);
  const [activeId, setActiveId] = useState<string>(() => convos[0].id);
  const [input, setInput] = useState("");
  const countRef = useRef(1);
  const scrollRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<Record<string, AbortController>>({});

  const active = convos.find((c) => c.id === activeId) ?? convos[0];

  const update = (id: string, fn: (c: Conversation) => Conversation) =>
    setConvos((prev) => prev.map((c) => (c.id === id ? fn(c) : c)));

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [active.items.length, active.busy]);

  const addChat = () => {
    countRef.current += 1;
    const c = newConversation(countRef.current);
    setConvos((prev) => [...prev, c]);
    setActiveId(c.id);
  };

  const closeChat = (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setConvos((prev) => {
      if (prev.length === 1) return prev;
      const next = prev.filter((c) => c.id !== id);
      if (id === activeId) setActiveId(next[next.length - 1].id);
      return next;
    });
  };

  const send = async (text: string) => {
    const q = text.trim();
    if (!q || active.busy) return;
    const id = active.id;
    const ac = new AbortController();
    abortRef.current[id] = ac;
    const transcript: ChatMessage[] = [
      ...active.transcript,
      { role: "user", content: q },
    ];
    update(id, (c) => ({
      ...c,
      title: c.items.length === 0 ? q.slice(0, 32) : c.title,
      items: [...c.items, { kind: "user", text: q }],
      transcript,
      busy: true,
    }));
    setInput("");

    try {
      const { messages, finalText } = await runConversation(
        transcript,
        controller,
        {
          onToolCall: (e) =>
            update(id, (c) => ({
              ...c,
              items: [...c.items, { kind: "tool", name: e.name, args: e.args }],
            })),
          onToolResult: (e) =>
            update(id, (c) => {
              const items = [...c.items];
              for (let i = items.length - 1; i >= 0; i--) {
                const it = items[i];
                if (it.kind === "tool" && it.name === e.name && it.result === undefined) {
                  items[i] = { ...it, result: e.result };
                  break;
                }
              }
              return { ...c, items };
            }),
        },
        ac.signal
      );
      update(id, (c) => ({
        ...c,
        transcript: messages,
        items: [...c.items, { kind: "assistant", text: finalText }],
        busy: false,
      }));
    } catch (e) {
      const aborted =
        ac.signal.aborted || (e instanceof Error && e.name === "AbortError");
      update(id, (c) => ({
        ...c,
        items: [
          ...c.items,
          aborted
            ? { kind: "note", text: "Stopped." }
            : { kind: "error", text: String(e instanceof Error ? e.message : e) },
        ],
        busy: false,
      }));
    } finally {
      delete abortRef.current[id];
    }
  };

  const stop = () => abortRef.current[active.id]?.abort();

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void send(input);
    }
  };

  return (
    <div className="agent-panel">
      <div className="agent-header">
        <div className="agent-tabs" role="tablist">
          {convos.map((c) => (
          <button
            key={c.id}
            className={`agent-tab ${c.id === activeId ? "active" : ""}`}
            onClick={() => setActiveId(c.id)}
            role="tab"
            aria-selected={c.id === activeId}
            title={c.title}
          >
            <span className="agent-tab-label">{c.title}</span>
            {convos.length > 1 && (
              <span className="agent-tab-x" onClick={(e) => closeChat(c.id, e)}>
                ×
              </span>
            )}
          </button>
        ))}
          <button className="agent-tab-new" onClick={addChat} title="New chat">
            + New chat
          </button>
        </div>
        {onMinimize && (
          <button
            className="agent-minimize"
            onClick={onMinimize}
            title="Minimize agent  ( [ )"
            aria-label="Minimize agent"
            aria-keyshortcuts="["
          >
            ‹
          </button>
        )}
      </div>

      <div className="agent-messages" ref={scrollRef}>
        {active.items.length === 0 && (
          <div className="agent-empty">
            <div className="agent-empty-title">Ask the map</div>
            <p className="agent-empty-sub">
              I can query the evidence data and drive the graph — select, highlight,
              zoom, and filter.
            </p>
            <div className="agent-suggestions">
              {SUGGESTIONS.map((s) => (
                <button key={s} className="agent-suggestion" onClick={() => void send(s)}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {active.items.map((it, i) => (
          <MessageItem key={i} item={it} />
        ))}

        {active.busy && (
          <div className="agent-thinking">
            <span className="dot-pulse" />
            <span className="dot-pulse" />
            <span className="dot-pulse" />
          </div>
        )}
      </div>

      <form
        className="agent-input"
        onSubmit={(e) => {
          e.preventDefault();
          void send(input);
        }}
      >
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Ask about the data or tell me what to show…"
          rows={2}
        />
        {active.busy ? (
          <button type="button" className="agent-send agent-stop" onClick={stop}>
            Stop
          </button>
        ) : (
          <button type="submit" className="agent-send" disabled={!input.trim()}>
            Send
          </button>
        )}
      </form>
    </div>
  );
}

function MessageItem({ item }: { item: UiItem }) {
  if (item.kind === "user")
    return <div className="msg msg-user">{item.text}</div>;
  if (item.kind === "assistant")
    return (
      <div className="msg msg-assistant">
        <Markdown text={item.text} />
      </div>
    );
  if (item.kind === "error")
    return <div className="msg msg-error">⚠ {item.text}</div>;
  if (item.kind === "note")
    return <div className="msg msg-note">{item.text}</div>;
  return <ToolItem item={item} />;
}

function ToolItem({
  item,
}: {
  item: Extract<UiItem, { kind: "tool" }>;
}) {
  const [open, setOpen] = useState(false);
  const pending = item.result === undefined;
  const summary = toolSummary(item);
  const sql = item.name === "query_data" ? String(item.args.sql ?? "") : "";
  const hasDetail = !!sql || item.result !== undefined;

  return (
    <div className={`msg msg-tool ${pending ? "pending" : ""}`}>
      <button
        className="tool-head"
        onClick={() => hasDetail && setOpen((v) => !v)}
        disabled={!hasDetail}
      >
        <span className="tool-icon">{pending ? "◴" : "✓"}</span>
        <span className="tool-name">{item.name}</span>
        <span className="tool-summary">{summary}</span>
      </button>
      {open && (
        <div className="tool-detail">
          {sql && <pre className="tool-sql">{sql}</pre>}
          {item.result !== undefined && (
            <pre className="tool-result">
              {JSON.stringify(item.result, null, 2).slice(0, 4000)}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

function toolSummary(item: Extract<UiItem, { kind: "tool" }>): string {
  const r = item.result as Record<string, unknown> | undefined;
  if (item.result === undefined) return "running…";
  if (r && typeof r === "object" && "error" in r) return `error: ${String(r.error)}`;
  switch (item.name) {
    case "query_data":
      return r
        ? `${r.rowCount ?? 0} rows${r.truncated ? "+" : ""}`
        : "done";
    case "select_papers":
      return `selected ${r?.selected ?? 0}`;
    case "highlight_papers":
      return `highlighted ${r?.highlighted ?? 0}`;
    case "zoom_to_papers":
      return `framed ${r?.zoomed ?? 0}`;
    case "zoom_to_community":
      return r?.error ? `error` : `community ${r?.topic_id} · ${r?.members} papers`;
    case "focus_entity":
      return `resolved ${r?.resolved ?? 0} (${r?.by ?? "?"})`;
    default:
      return "done";
  }
}
