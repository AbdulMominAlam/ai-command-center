import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, ApiError } from "../api";
import { ArrowUpIcon } from "../components/icons";
import { dueLabel, money } from "../format";
import { useRefreshTasks } from "../queries";
import type { ChatResponse, PendingAction } from "../types";

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  meta?: Omit<ChatResponse, "reply" | "pending_actions">;
  actions?: PendingAction[];
}

const SUGGESTIONS = [
  "What do I need to finish before Friday?",
  "What's on my calendar tomorrow?",
  "Any SUCourse deadlines this week?",
];
const MAX_HISTORY = 50; // the backend's limit

/** Turns citations like [task 12] into inline code, which is styled as a small chip. */
const chipCitations = (md: string) => md.replace(/\[((?:task|email|event|assignment) \d+)\](?!\()/g, "`$1`");

function ActionCard({ action, onChange }: { action: PendingAction; onChange: (a: PendingAction) => void }) {
  const refresh = useRefreshTasks();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const resolve = async (verb: "confirm" | "cancel") => {
    setBusy(true);
    setError(null);
    try {
      const updated = await api<PendingAction>(`/actions/${action.id}/${verb}`, { method: "POST" });
      onChange({ ...action, status: updated.status });
      if (verb === "confirm") refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mt-3 rounded-xl border border-line bg-surface p-4">
      <p className="eyebrow">{action.status === "pending" ? "Proposed task · needs your OK" : "Proposed task"}</p>
      <p className={`mt-2 text-body font-medium ${action.status === "cancelled" ? "text-muted line-through" : "text-ink"}`}>
        {action.title}
      </p>
      <p className="mt-1 font-mono text-meta text-muted">
        {action.due_at ? dueLabel(action.due_at, "date") : "No due date"} · <span className="capitalize">{action.priority}</span>
      </p>
      {action.status === "pending" ? (
        <div className="mt-4 flex gap-2">
          <button type="button" disabled={busy} onClick={() => resolve("confirm")}
            className="rounded-lg bg-accent px-3.5 py-1.5 text-body font-medium text-accent-ink hover:opacity-90 disabled:opacity-60">
            Confirm
          </button>
          <button type="button" disabled={busy} onClick={() => resolve("cancel")}
            className="rounded-lg border border-line px-3.5 py-1.5 text-body text-muted hover:text-ink disabled:opacity-60">
            Cancel
          </button>
        </div>
      ) : (
        <p className={`mt-3 text-meta ${action.status === "confirmed" ? "text-ink" : "text-muted"}`}>
          {action.status === "confirmed" ? "✓ Added to your tasks" : "Cancelled"}
        </p>
      )}
      {error && <p className="mt-2 text-meta text-accent">{error}</p>}
    </div>
  );
}

interface Props {
  messages: ChatMessage[];
  setMessages: (update: (prev: ChatMessage[]) => ChatMessage[]) => void;
}

export function ChatPage({ messages, setMessages }: Props) {
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Braces matter: newer browsers return a Promise here, and React treats any return value as cleanup.
    bottom.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [messages.length, sending]);

  const send = async (text: string) => {
    const question = text.trim();
    if (!question || sending) return;
    const history = [...messages, { role: "user" as const, content: question }];
    setMessages(() => history);
    setInput("");
    setError(null);
    setSending(true);
    try {
      // The backend keeps no chat state: send the whole conversation (most recent 50 turns),
      // starting with a user message.
      let recent = history.slice(-MAX_HISTORY);
      while (recent[0].role !== "user") recent = recent.slice(1);
      const res = await api<ChatResponse>("/chat", {
        method: "POST",
        body: { messages: recent.map(({ role, content }) => ({ role, content: content || "(no reply)" })) },
      });
      const { reply, pending_actions, ...meta } = res;
      setMessages((prev) => [...prev, { role: "assistant", content: reply || "(no reply)", meta, actions: pending_actions }]);
    } catch (e) {
      // Take the question back out so the history stays user/assistant pairs, and let them retry.
      setMessages((prev) => prev.slice(0, -1));
      setInput(question);
      if (!(e instanceof ApiError && e.reconnect)) setError(e instanceof Error ? e.message : "Something went wrong.");
    } finally {
      setSending(false);
    }
  };

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    send(input);
  };
  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      send(input);
    }
  };
  const updateAction = (msgIndex: number, action: PendingAction) =>
    setMessages((prev) =>
      prev.map((m, i) => (i === msgIndex ? { ...m, actions: m.actions?.map((a) => (a.id === action.id ? action : a)) } : m)),
    );

  return (
    <div className="mx-auto flex min-h-[calc(100dvh-5rem)] max-w-3xl flex-col px-4 sm:px-8 md:min-h-dvh">
      <div className="flex-1 py-8 sm:py-12">
        <p className="eyebrow">Ask</p>
        {messages.length === 0 ? (
          <>
            <h1 className="mt-3 font-display text-display text-ink">What do you need to know?</h1>
            <p className="mt-3 text-body text-muted">Answers come from your tasks, Gmail, Calendar and SUCourse, with sources cited.</p>
            <div className="mt-8 flex flex-col items-start gap-2">
              {SUGGESTIONS.map((s) => (
                <button key={s} type="button" onClick={() => send(s)}
                  className="rounded-full border border-line px-3.5 py-1.5 text-left text-body text-muted hover:border-faint hover:text-ink">
                  {s}
                </button>
              ))}
            </div>
          </>
        ) : (
          <ol className="mt-6 flex flex-col gap-8">
            {messages.map((m, i) =>
              m.role === "user" ? (
                <li key={i} className="self-end max-w-[85%] rounded-2xl rounded-br-md bg-ink px-4 py-2.5 text-body whitespace-pre-wrap text-paper">
                  {m.content}
                </li>
              ) : (
                <li key={i} className="max-w-full">
                  <div className="prose-chat text-body text-ink">
                    <Markdown remarkPlugins={[remarkGfm]}>{chipCitations(m.content)}</Markdown>
                  </div>
                  {m.actions?.map((a) => <ActionCard key={a.id} action={a} onChange={(next) => updateAction(i, next)} />)}
                  {m.meta && (
                    <p className="mt-2 font-mono text-[0.6875rem] text-faint">
                      {money(m.meta.estimated_cost_usd)} · {(m.meta.input_tokens + m.meta.output_tokens).toLocaleString()} tokens
                      {m.meta.tool_calls.length > 0 && ` · ${m.meta.tool_calls.length} lookup${m.meta.tool_calls.length === 1 ? "" : "s"}`}
                    </p>
                  )}
                </li>
              ),
            )}
            {sending && (
              <li className="flex items-center gap-2 text-body text-muted" aria-live="polite">
                <span className="size-1.5 animate-pulse rounded-full bg-accent" aria-hidden />
                Looking through your data…
              </li>
            )}
          </ol>
        )}
        <div ref={bottom} />
      </div>

      <form onSubmit={onSubmit} className="sticky bottom-20 pb-4 md:bottom-0 md:pb-8">
        {error && <p className="mb-2 text-meta text-accent">{error}</p>}
        <div className="flex items-end gap-2 rounded-2xl border border-line bg-surface p-2 shadow-[0_8px_24px_-12px_rgb(0_0_0/0.15)] focus-within:border-faint">
          <label htmlFor="chat-input" className="sr-only">Your question</label>
          <textarea
            id="chat-input"
            rows={1}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            maxLength={8000}
            placeholder="Ask about deadlines, emails, events…"
            className="field-sizing-content max-h-40 min-h-9 flex-1 resize-none bg-transparent px-2 py-1.5 text-lead text-ink outline-none placeholder:text-faint sm:text-body"
          />
          <button type="submit" disabled={sending || !input.trim()} aria-label="Send"
            className="grid size-9 shrink-0 place-items-center rounded-xl bg-accent text-accent-ink disabled:bg-sunken disabled:text-faint">
            <ArrowUpIcon />
          </button>
        </div>
        {messages.length > 0 && (
          <button type="button" onClick={() => setMessages(() => [])} disabled={sending}
            className="mt-2 text-meta text-faint hover:text-muted">
            New conversation
          </button>
        )}
      </form>
    </div>
  );
}
