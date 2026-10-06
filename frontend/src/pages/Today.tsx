import { ApiError } from "../api";
import { SyncIcon } from "../components/icons";
import { Section } from "../components/Section";
import { TaskRow } from "../components/TaskRow";
import { dueLabel, headerDate, time, ymd } from "../format";
import { useSyncAll, useToday } from "../queries";
import type { Assignment, CalendarEvent, SyncResult } from "../types";

function plural(n: number, word: string) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

/** One line per source: what the sync actually changed. */
function syncSummary(r: SyncResult): string[] {
  const cal = r.calendar;
  const calChanges = [cal.added && `${cal.added} added`, cal.updated && `${cal.updated} updated`, cal.deleted && `${cal.deleted} removed`].filter(Boolean);
  const su = r.sucourse;
  let sucourse: string;
  if ("skipped" in su) sucourse = "SUCourse: not set up";
  else if ("error" in su) sucourse = `SUCourse: ${su.error}`;
  else sucourse = `SUCourse: ${su.tasks_created ? `${plural(su.tasks_created, "new task")}, ` : ""}${plural(su.events, "item")} checked`;
  return [
    `Gmail: ${r.gmail.added ? plural(r.gmail.added, "new email") : "nothing new"}`,
    `Calendar: ${calChanges.length ? calChanges.join(", ") : "no changes"}`,
    sucourse,
  ];
}

function SyncButton() {
  const sync = useSyncAll();
  return (
    <div className="flex flex-col items-start gap-2 sm:items-end">
      <button
        type="button"
        onClick={() => sync.mutate()}
        disabled={sync.isPending}
        className="flex items-center gap-2 rounded-lg border border-line bg-surface px-3.5 py-2 text-body font-medium text-ink hover:border-faint disabled:cursor-wait disabled:text-muted"
      >
        <SyncIcon spinning={sync.isPending} />
        {sync.isPending ? "Syncing…" : "Sync now"}
      </button>
      <div aria-live="polite" className="font-mono text-meta text-muted sm:text-right">
        {sync.isPending && <p>Gmail, Calendar and SUCourse. A first Gmail sync can take a few minutes.</p>}
        {sync.isSuccess && (
          <ul>
            {syncSummary(sync.data).map((line) => <li key={line}>{line}</li>)}
            <li className="text-faint">done in {sync.data.elapsed_seconds.toFixed(1)}s</li>
          </ul>
        )}
        {sync.isError && !(sync.error instanceof ApiError && sync.error.reconnect) && (
          <p className="text-accent">Sync failed: {sync.error.message}</p>
        )}
      </div>
    </div>
  );
}

function EventList({ events }: { events: CalendarEvent[] }) {
  return (
    <ol className="divide-y divide-line">
      {events.map((e) => (
        <li key={e.id} className="flex gap-4 py-3">
          <span className="w-12 shrink-0 font-mono text-meta tabular-nums text-muted">
            {e.all_day || !e.start ? "All day" : time(e.start)}
          </span>
          <div className="min-w-0">
            <p className="text-body text-ink">{e.title}</p>
            {(e.location || (!e.all_day && e.end)) && (
              <p className="mt-0.5 truncate text-meta text-muted">
                {!e.all_day && e.end && <span className="font-mono">until {time(e.end)}</span>}
                {!e.all_day && e.end && e.location && " · "}
                {e.location}
              </p>
            )}
          </div>
        </li>
      ))}
    </ol>
  );
}

function SucourseList({ items }: { items: Assignment[] }) {
  return (
    <ol className="divide-y divide-line">
      {items.map((a) => (
        <li key={a.id} className={`py-3 ${a.task_status === "done" ? "opacity-50" : ""}`}>
          <div className="flex items-baseline justify-between gap-3 font-mono text-meta tabular-nums">
            <span className="text-ink">{a.course ?? "SUCourse"}</span>
            {a.due_at && <span className="text-muted">{dueLabel(a.due_at, "date")}</span>}
          </div>
          <p className={`mt-1 text-body ${a.task_status === "done" ? "line-through" : "text-ink"}`}>{a.title}</p>
        </li>
      ))}
    </ol>
  );
}

export function TodayPage() {
  const { data, isPending, error } = useToday();
  const now = data ? new Date(data.now) : new Date();
  const head = headerDate(data?.now ?? new Date().toISOString());

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-8 sm:py-12">
      <header className="flex flex-col gap-6 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="eyebrow">{data ? `Today · ${data.date}` : `Today · ${ymd(new Date())}`}</p>
          <h1 className="mt-3 font-display text-display text-ink sm:text-hero">
            {head.weekday}
            <span className="text-muted">, {head.date}</span>
          </h1>
        </div>
        <SyncButton />
      </header>

      {isPending && <p className="mt-12 text-body text-muted">Loading your day…</p>}
      {error && !(error instanceof ApiError && error.reconnect) && (
        <p className="mt-12 text-body text-accent">Couldn't load today: {error.message}</p>
      )}

      {data && (
        <div className="mt-10 grid gap-12 lg:mt-14 lg:grid-cols-[minmax(0,1fr)_320px] lg:gap-16">
          <div className="flex flex-col gap-10">
            {data.overdue.length > 0 && (
              <Section title="Overdue" count={data.overdue.length} accent>
                <ul className="divide-y divide-line">
                  {data.overdue.map((t) => <TaskRow key={t.id} task={t} due="date" overdueAt={now} />)}
                </ul>
              </Section>
            )}
            <Section title="Today" count={data.today.length} empty="Nothing else due today.">
              <ul className="divide-y divide-line">
                {data.today.map((t) => <TaskRow key={t.id} task={t} due="time" />)}
              </ul>
            </Section>
            <Section title="This week" count={data.this_week.length} empty="Nothing due in the next 7 days.">
              <ul className="divide-y divide-line">
                {data.this_week.map((t) => <TaskRow key={t.id} task={t} due="weekday" />)}
              </ul>
            </Section>
            {data.undated_count > 0 && (
              <a href="#/tasks" className="self-start text-meta text-muted underline decoration-line underline-offset-4 hover:text-ink">
                {plural(data.undated_count, "open task")} with no due date →
              </a>
            )}
          </div>

          <aside className="flex flex-col gap-10">
            <Section title="Calendar" count={data.events.length} empty="No events today.">
              <EventList events={data.events} />
            </Section>
            <Section title="Upcoming from SUCourse" count={data.sucourse.length} empty="No upcoming SUCourse deadlines.">
              <SucourseList items={data.sucourse} />
            </Section>
          </aside>
        </div>
      )}
    </div>
  );
}
