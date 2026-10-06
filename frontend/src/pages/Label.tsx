import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { api } from "../api";

// Dev-only page (#/label) for labeling the extraction eval set in backend/evals/emails.jsonl.
// It never shows what the model extracted, so the labels aren't biased by it.

interface LabelTask {
  title: string;
  due_date: string | null; // "2026-03-06", an Istanbul calendar day
  due_time: string | null; // "23:55" (the backend may send "23:55:00")
}

interface Label {
  is_actionable: boolean;
  tasks: LabelTask[];
}

interface EvalEmail {
  id: number;
  sent_at: string | null;
  sender: string | null;
  subject: string;
  body: string | null;
  expected: Label | null;
}

interface Draft {
  is_actionable: boolean | null;
  tasks: { title: string; due_date: string; due_time: string }[];
}

const TZ = "Europe/Istanbul";
const sentFmt = new Intl.DateTimeFormat("en-GB", {
  timeZone: TZ, weekday: "long", day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", hour12: false,
});
const weekdayOf = (ymd: string) =>
  new Intl.DateTimeFormat("en-GB", { weekday: "short", timeZone: "UTC" }).format(new Date(`${ymd}T00:00:00Z`));

const emptyTask = () => ({ title: "", due_date: "", due_time: "" });

function draftFrom(label: Label | null): Draft {
  if (!label) return { is_actionable: null, tasks: [] };
  return {
    is_actionable: label.is_actionable,
    tasks: label.tasks.map((t) => ({ title: t.title, due_date: t.due_date ?? "", due_time: t.due_time?.slice(0, 5) ?? "" })),
  };
}

/** The draft as a Label, or an error message saying what is missing. */
function toLabel(d: Draft): Label | string {
  if (d.is_actionable === null) return "Choose actionable yes (Y) or no (N) first.";
  if (!d.is_actionable) return { is_actionable: false, tasks: [] };
  const tasks = d.tasks.filter((t) => t.title.trim() || t.due_date || t.due_time);
  if (tasks.some((t) => !t.title.trim())) return "Every task needs a title.";
  if (tasks.some((t) => t.due_time && !t.due_date)) return "A due time needs a due date.";
  return {
    is_actionable: true,
    tasks: tasks.map((t) => ({ title: t.title.trim(), due_date: t.due_date || null, due_time: t.due_time || null })),
  };
}

const isTyping = (el: EventTarget | null) =>
  el instanceof HTMLElement && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable);

export function LabelPage() {
  const qc = useQueryClient();
  const { data: emails, error, isPending } = useQuery({
    queryKey: ["eval-emails"],
    queryFn: () => api<{ emails: EvalEmail[] }>("/evals/emails").then((r) => r.emails),
    staleTime: Infinity, // only this page changes the file
  });

  const [index, setIndex] = useState<number | null>(null);
  const [draft, setDraft] = useState<Draft>({ is_actionable: null, tasks: [] });
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const titleRefs = useRef<(HTMLInputElement | null)[]>([]);
  const focusTask = useRef<number | null>(null);

  // Start at the first unlabeled email.
  useEffect(() => {
    if (emails && index === null) {
      const first = emails.findIndex((e) => !e.expected);
      setIndex(first === -1 ? 0 : first);
    }
  }, [emails, index]);

  const email = emails && index !== null ? emails[index] : undefined;

  // A fresh draft whenever you move to another email.
  useEffect(() => {
    if (email) {
      setDraft(draftFrom(email.expected));
      setDirty(false);
      setMessage(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [email?.id]);

  // Focus a task title after it was added.
  useEffect(() => {
    if (focusTask.current !== null) {
      titleRefs.current[focusTask.current]?.focus();
      focusTask.current = null;
    }
  });

  const edit = (fn: (d: Draft) => Draft) => {
    setDraft(fn);
    setDirty(true);
    setMessage(null);
  };

  const go = useCallback((i: number) => {
    if (emails) setIndex(Math.max(0, Math.min(emails.length - 1, i)));
  }, [emails]);

  const nextUnlabeled = useCallback((from: number, list = emails) => {
    if (!list) return from;
    for (let k = 1; k <= list.length; k++) {
      const i = (from + k) % list.length;
      if (!list[i].expected) return i;
    }
    return Math.min(from + 1, list.length - 1); // all labeled: just step forward
  }, [emails]);

  const save = useCallback(async (d: Draft) => {
    if (!email || index === null || saving) return;
    const label = toLabel(d);
    if (typeof label === "string") {
      setMessage(label);
      return;
    }
    setSaving(true);
    try {
      await api(`/evals/emails/${email.id}/label`, { method: "PUT", body: label });
      const updated = emails!.map((e) => (e.id === email.id ? { ...e, expected: label } : e));
      qc.setQueryData(["eval-emails"], updated);
      setIndex(nextUnlabeled(index, updated));
    } catch (e) {
      setMessage(`Couldn't save: ${(e as Error).message}`);
    } finally {
      setSaving(false);
    }
  }, [email, emails, index, nextUnlabeled, qc, saving]);

  const clear = async () => {
    if (!email) return;
    await api(`/evals/emails/${email.id}/label`, { method: "DELETE" });
    qc.setQueryData(["eval-emails"], emails!.map((e) => (e.id === email.id ? { ...e, expected: null } : e)));
    setDraft({ is_actionable: null, tasks: [] });
    setDirty(false);
  };

  const addTask = useCallback(() => {
    focusTask.current = draft.tasks.length;
    edit((d) => ({ is_actionable: true, tasks: [...d.tasks, emptyTask()] }));
  }, [draft.tasks.length]);

  const setActionable = useCallback((yes: boolean) => {
    if (yes) {
      if (draft.tasks.length === 0) focusTask.current = 0;
      edit((d) => ({ is_actionable: true, tasks: d.tasks.length ? d.tasks : [emptyTask()] }));
    } else {
      edit(() => ({ is_actionable: false, tasks: [] }));
    }
  }, [draft.tasks.length]);

  // Keyboard shortcuts. Letters only work outside text fields; Enter and Esc work everywhere.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!emails || index === null) return;
      if (e.key === "Escape") {
        (document.activeElement as HTMLElement | null)?.blur();
        return;
      }
      if (e.key === "Enter" && !e.shiftKey && !e.altKey && (isTyping(e.target) || e.metaKey || e.ctrlKey || e.target === document.body)) {
        e.preventDefault();
        save(draft);
        return;
      }
      if (isTyping(e.target) || e.metaKey || e.ctrlKey || e.altKey) return;
      const key = e.key.toLowerCase();
      if (key === "y") { e.preventDefault(); setActionable(true); }
      else if (key === "n") { e.preventDefault(); save({ is_actionable: false, tasks: [] }); }
      else if (key === "t") { e.preventDefault(); addTask(); }
      else if (key === "j" || e.key === "ArrowRight") go(index + 1);
      else if (key === "k" || e.key === "ArrowLeft") go(index - 1);
      else if (key === "u") go(nextUnlabeled(index));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [emails, index, draft, save, setActionable, addTask, go, nextUnlabeled]);

  if (isPending) return <Frame><p className="text-body text-muted">Loading…</p></Frame>;
  if (error) return <Frame><p className="text-body text-accent">{error.message}</p></Frame>;
  if (!emails?.length || !email || index === null)
    return <Frame><p className="text-body text-muted">The eval set is empty. Run <code className="font-mono">uv run python -m evals.export</code> in backend/.</p></Frame>;

  const labeled = emails.filter((e) => e.expected).length;

  return (
    <Frame>
      <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2">
        <h1 className="font-display text-title text-ink sm:text-[1.75rem]">Label emails</h1>
        <p className="font-mono text-meta text-muted">
          {labeled} / {emails.length} labeled · email {index + 1}
        </p>
      </div>

      {/* One dot per email: filled when labeled, ringed for the current one. Click to jump. */}
      <div className="mt-3 flex flex-wrap gap-1" aria-label="Emails">
        {emails.map((e, i) => (
          <button
            key={e.id}
            type="button"
            onClick={() => go(i)}
            aria-label={`Email ${i + 1}${e.expected ? ", labeled" : ""}`}
            aria-current={i === index ? "true" : undefined}
            className={`size-3 rounded-sm ${e.expected ? "bg-ink/70" : "bg-sunken"} ${i === index ? "ring-2 ring-accent ring-offset-1 ring-offset-paper" : ""}`}
          />
        ))}
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-[minmax(0,1fr)_380px]">
        {/* The email */}
        <article className="min-w-0 rounded-xl border border-line bg-surface">
          <header className="border-b border-line p-4">
            <h2 className="text-lead font-semibold text-ink [overflow-wrap:anywhere]">{email.subject || "(no subject)"}</h2>
            <p className="mt-1 text-body text-muted [overflow-wrap:anywhere]">{email.sender ?? "unknown sender"}</p>
            <p className="mt-2 inline-block rounded-md bg-accent-soft px-2 py-0.5 font-mono text-meta text-ink">
              Sent {email.sent_at ? sentFmt.format(new Date(email.sent_at)) : "unknown"} (Istanbul)
            </p>
          </header>
          <pre className="max-h-[60dvh] overflow-y-auto p-4 font-sans text-body whitespace-pre-wrap text-ink [overflow-wrap:anywhere]">
            {email.body || "(empty body)"}
          </pre>
        </article>

        {/* Your label */}
        <section className="flex flex-col gap-4 lg:sticky lg:top-6 lg:self-start" aria-label="Your label">
          <div>
            <p className="eyebrow">Actionable?</p>
            <div className="mt-2 grid grid-cols-2 gap-2">
              <Choice active={draft.is_actionable === true} onClick={() => setActionable(true)} kbd="Y">Yes</Choice>
              <Choice active={draft.is_actionable === false} onClick={() => save({ is_actionable: false, tasks: [] })} kbd="N">
                No, next
              </Choice>
            </div>
          </div>

          {draft.is_actionable && (
            <div>
              <p className="eyebrow">Tasks</p>
              <ol className="mt-2 flex flex-col gap-3">
                {draft.tasks.map((t, i) => (
                  <li key={i} className="rounded-xl border border-line bg-surface p-3">
                    <div className="flex items-center gap-2">
                      <input
                        ref={(el) => { titleRefs.current[i] = el; }}
                        value={t.title}
                        onChange={(e) => edit((d) => ({ ...d, tasks: d.tasks.map((x, j) => (j === i ? { ...x, title: e.target.value } : x)) }))}
                        placeholder="Task title, e.g. Submit CS204 HW2"
                        aria-label={`Task ${i + 1} title`}
                        className="min-w-0 flex-1 rounded-lg border border-line bg-paper px-2.5 py-1.5 text-body text-ink outline-none placeholder:text-faint focus:border-faint"
                      />
                      <button
                        type="button"
                        onClick={() => edit((d) => ({ ...d, tasks: d.tasks.filter((_, j) => j !== i) }))}
                        aria-label={`Remove task ${i + 1}`}
                        className="grid size-8 shrink-0 place-items-center rounded-lg text-faint hover:bg-sunken hover:text-ink"
                      >
                        ×
                      </button>
                    </div>
                    <div className="mt-2 flex items-center gap-2">
                      <input
                        type="date"
                        value={t.due_date}
                        onChange={(e) => edit((d) => ({ ...d, tasks: d.tasks.map((x, j) => (j === i ? { ...x, due_date: e.target.value } : x)) }))}
                        aria-label={`Task ${i + 1} due date`}
                        className="min-w-0 flex-1 rounded-lg border border-line bg-paper px-2 py-1 font-mono text-meta text-ink outline-none focus:border-faint"
                      />
                      <input
                        type="time"
                        value={t.due_time}
                        onChange={(e) => edit((d) => ({ ...d, tasks: d.tasks.map((x, j) => (j === i ? { ...x, due_time: e.target.value } : x)) }))}
                        aria-label={`Task ${i + 1} due time`}
                        className="w-24 rounded-lg border border-line bg-paper px-2 py-1 font-mono text-meta text-ink outline-none focus:border-faint"
                      />
                      <span className="w-8 font-mono text-meta text-muted">{t.due_date ? weekdayOf(t.due_date) : ""}</span>
                    </div>
                    <p className="mt-1 text-meta text-faint">
                      {t.due_date ? (t.due_time ? "Exact time" : "Date only, no time given") : "No due date"}
                    </p>
                  </li>
                ))}
              </ol>
              <button type="button" onClick={addTask} className="mt-2 text-body text-muted hover:text-ink">
                + Add task <Kbd>T</Kbd>
              </button>
            </div>
          )}

          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => save(draft)}
              disabled={saving}
              className="rounded-lg bg-accent px-3.5 py-1.5 text-body font-medium text-accent-ink hover:opacity-90 disabled:opacity-60"
            >
              {saving ? "Saving…" : "Save & next"} <span className="ml-1 font-mono text-meta opacity-80">↵</span>
            </button>
            {email.expected && (
              <button type="button" onClick={clear} className="rounded-lg border border-line px-3 py-1.5 text-body text-muted hover:text-ink">
                Clear label
              </button>
            )}
            <span className="ml-auto text-meta text-faint">{dirty ? "Unsaved" : email.expected ? "Saved" : "Unlabeled"}</span>
          </div>
          {message && <p className="text-meta text-accent" role="alert">{message}</p>}

          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 rounded-xl border border-line p-3 text-meta text-muted">
            <dt><Kbd>Y</Kbd></dt><dd>Actionable, start a task</dd>
            <dt><Kbd>N</Kbd></dt><dd>Not actionable, save and go to the next</dd>
            <dt><Kbd>T</Kbd></dt><dd>Add another task</dd>
            <dt><Kbd>↵</Kbd></dt><dd>Save and go to the next unlabeled (also inside fields)</dd>
            <dt><Kbd>Esc</Kbd></dt><dd>Leave the field, so letter keys work again</dd>
            <dt><Kbd>J</Kbd> <Kbd>K</Kbd></dt><dd>Next / previous email (drops unsaved edits)</dd>
            <dt><Kbd>U</Kbd></dt><dd>Next unlabeled email</dd>
          </dl>
          <p className="text-meta text-faint">
            Dates are in Istanbul time. Fill in the time only when the email gives one; "this Friday" counts from the sent date above.
          </p>
        </section>
      </div>
    </Frame>
  );
}

function Frame({ children }: { children: ReactNode }) {
  return <div className="mx-auto max-w-6xl px-4 py-8 sm:px-8">{children}</div>;
}

function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="rounded border border-line bg-surface px-1 font-mono text-[0.6875rem] text-muted">{children}</kbd>;
}

function Choice({ active, onClick, kbd, children }: { active: boolean; onClick: () => void; kbd: string; children: ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`flex items-center justify-between rounded-lg border px-3 py-2 text-body ${
        active ? "border-accent bg-accent-soft font-medium text-ink" : "border-line text-muted hover:text-ink"
      }`}
    >
      {children} <Kbd>{kbd}</Kbd>
    </button>
  );
}
