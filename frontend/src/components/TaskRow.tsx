import { dueLabel, lateBy, SOURCE_LABEL } from "../format";
import { useUpdateTask } from "../queries";
import type { Priority, Task } from "../types";
import { CheckIcon } from "./icons";

const PRIORITY_DOT: Record<Priority, string> = {
  high: "bg-accent",
  medium: "border border-muted",
  low: "border border-dashed border-faint",
};

interface Props {
  task: Task;
  due: "time" | "weekday" | "date";
  overdueAt?: Date; // set for overdue tasks: shows "2d late" in the accent colour
  editablePriority?: boolean;
}

export function TaskRow({ task, due, overdueAt, editablePriority = false }: Props) {
  const update = useUpdateTask();
  const done = task.status === "done";
  // Show the new state right away while the request runs.
  const checked = update.isPending && update.variables?.status ? update.variables.status === "done" : done;
  const priority = (update.isPending && update.variables?.priority) || task.priority;
  const low = priority === "low";

  return (
    <li className={`group flex items-start gap-3 py-3 ${low && !checked ? "opacity-60" : ""}`}>
      <button
        type="button"
        role="checkbox"
        aria-checked={checked}
        aria-label={checked ? `Mark "${task.title}" as open` : `Mark "${task.title}" as done`}
        disabled={update.isPending}
        onClick={() => update.mutate({ id: task.id, status: checked ? "open" : "done" })}
        className={`mt-0.5 grid size-[18px] shrink-0 place-items-center rounded-[5px] border transition-colors ${
          checked ? "border-ink bg-ink text-paper" : "border-faint hover:border-ink"
        }`}
      >
        {checked && <CheckIcon />}
      </button>

      <div className="min-w-0 flex-1">
        <p className={`text-body break-words ${checked ? "text-muted line-through decoration-faint" : "text-ink"}`}>
          {task.title}
        </p>
        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-meta text-muted">
          {editablePriority ? (
            <label className="flex items-center gap-1.5">
              <span className={`size-2 rounded-full ${PRIORITY_DOT[priority]}`} aria-hidden />
              <span className="sr-only">Priority</span>
              <select
                value={priority}
                disabled={update.isPending}
                onChange={(e) => update.mutate({ id: task.id, priority: e.target.value as Priority })}
                className="-ml-0.5 field-sizing-content cursor-pointer rounded bg-transparent py-0.5 pr-1 capitalize hover:text-ink"
              >
                <option value="high">High</option>
                <option value="medium">Medium</option>
                <option value="low">Low</option>
              </select>
            </label>
          ) : (
            <span className="flex items-center gap-1.5 capitalize">
              <span className={`size-2 rounded-full ${PRIORITY_DOT[priority]}`} aria-hidden />
              {priority}
            </span>
          )}
          <span>{SOURCE_LABEL[task.source] ?? task.source}</span>
          {update.isError && <span className="text-accent">Couldn't save. Try again.</span>}
        </div>
      </div>

      {task.due_at && (
        <div className="shrink-0 pt-px text-right font-mono text-meta tabular-nums">
          <div className={overdueAt ? "text-accent" : "text-muted"}>{dueLabel(task.due_at, due)}</div>
          {overdueAt && <div className="mt-1 text-accent/80">{lateBy(task.due_at, overdueAt)}</div>}
        </div>
      )}
    </li>
  );
}
