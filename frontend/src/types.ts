export type Priority = "low" | "medium" | "high";
export type TaskStatus = "open" | "done" | "expired";

export interface Task {
  id: number;
  title: string;
  due_at: string | null; // ISO with the Istanbul offset
  priority: Priority;
  status: TaskStatus | "duplicate";
  source: string; // "gmail" | "sucourse" | "agent" | "user" | ...
  item_id: number | null;
  account: AccountLabel | null; // the Google account its email came from
}

export type AccountLabel = "Personal" | "Sabancı";

export interface UniversitySender {
  address: string;
  name: string | null;
  email_count: number;
  last_email_at: string | null;
  blocked: boolean;
}

export interface LinkedAccount {
  id: number;
  email: string;
  label: AccountLabel;
  linked_at: string | null;
  last_synced_at: string | null;
}

export interface CalendarEvent {
  id: number;
  title: string;
  start: string | null;
  end: string | null;
  all_day: boolean;
  location: string | null;
  account: AccountLabel | null;
}

export interface Assignment {
  id: number;
  title: string;
  due_at: string | null;
  course: string | null;
  task_id: number | null;
  task_status: string | null;
}

export interface Today {
  now: string;
  date: string;
  overdue: Task[];
  today: Task[];
  this_week: Task[];
  undated_count: number;
  events: CalendarEvent[];
  sucourse: Assignment[];
}

export interface Me {
  id: number;
  email: string;
}

export interface SyncResult {
  gmail: { added: number };
  reconnect_needed?: string[]; // accounts skipped because Google access expired
  calendar: { added: number; updated: number; deleted: number };
  sucourse:
    | { events: number; added: number; updated: number; tasks_created: number; tasks_updated: number }
    | { skipped: string }
    | { error: string };
  extraction:
    | {
        processed: number;
        tasks_created: number;
        tasks_resolved: number;
        failed: number;
        estimated_cost_usd: number | null;
      }
    | { error: string; credit_low?: boolean };
  elapsed_seconds: number;
}

export interface PendingAction {
  id: number;
  kind: string;
  status: "pending" | "confirmed" | "cancelled";
  title: string;
  due_at: string | null;
  priority: Priority;
}

export interface ChatResponse {
  reply: string;
  pending_actions: PendingAction[];
  tool_calls: string[];
  rounds: number;
  stopped_early: boolean;
  input_tokens: number;
  output_tokens: number;
  estimated_cost_usd: number | null;
}
