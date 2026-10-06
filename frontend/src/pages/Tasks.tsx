import { useState } from "react";
import { ApiError } from "../api";
import { TaskRow } from "../components/TaskRow";
import { useTasks } from "../queries";
import type { TaskStatus } from "../types";

const TABS: { status: TaskStatus; label: string; empty: string }[] = [
  { status: "open", label: "Open", empty: "No open tasks. Nice." },
  { status: "done", label: "Done", empty: "Nothing marked done yet." },
  { status: "expired", label: "Expired", empty: "No expired tasks." },
];

export function TasksPage() {
  const [status, setStatus] = useState<TaskStatus>("open");
  const { data, isPending, error } = useTasks(status);
  const tab = TABS.find((t) => t.status === status)!;

  return (
    <div className="mx-auto max-w-3xl px-4 py-8 sm:px-8 sm:py-12">
      <p className="eyebrow">Tasks</p>
      <h1 className="mt-3 font-display text-display text-ink">Everything on your list</h1>

      <div role="tablist" aria-label="Task status" className="mt-8 flex gap-1 border-b border-line">
        {TABS.map((t) => (
          <button
            key={t.status}
            type="button"
            role="tab"
            aria-selected={status === t.status}
            onClick={() => setStatus(t.status)}
            className={`-mb-px border-b-2 px-3 py-2 text-body transition-colors ${
              status === t.status ? "border-accent font-medium text-ink" : "border-transparent text-muted hover:text-ink"
            }`}
          >
            {t.label}
            {status === t.status && data && <span className="ml-2 font-mono text-meta text-faint">{data.length}</span>}
          </button>
        ))}
      </div>

      <div role="tabpanel" className="mt-2">
        {isPending && <p className="py-6 text-body text-muted">Loading…</p>}
        {error && !(error instanceof ApiError && error.reconnect) && (
          <p className="py-6 text-body text-accent">Couldn't load tasks: {error.message}</p>
        )}
        {data?.length === 0 && <p className="py-6 text-body text-faint">{tab.empty}</p>}
        {data && data.length > 0 && (
          <ul className="divide-y divide-line">
            {data.map((t) => <TaskRow key={t.id} task={t} due="date" editablePriority />)}
          </ul>
        )}
      </div>
    </div>
  );
}
