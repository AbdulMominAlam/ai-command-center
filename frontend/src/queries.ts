import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, setCreditLow } from "./api";
import type { LinkedAccount, Me, Priority, SyncResult, Task, TaskStatus, Today, UniversitySender } from "./types";

/** null means "not signed in" (the backend answered 401). */
export function useMe() {
  return useQuery({
    queryKey: ["me"],
    queryFn: async () => {
      try {
        return await api<Me>("/me");
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) return null;
        throw e;
      }
    },
    staleTime: 5 * 60_000,
  });
}

export function useAccounts() {
  return useQuery({
    queryKey: ["accounts"],
    queryFn: () => api<{ accounts: LinkedAccount[] }>("/accounts").then((r) => r.accounts),
  });
}

export function useUniversitySenders(enabled: boolean) {
  return useQuery({
    queryKey: ["university-senders"],
    queryFn: () => api<{ senders: UniversitySender[] }>("/university/senders").then((r) => r.senders),
    enabled,
  });
}

/** Block or unblock a university sender. Blocking closes their tasks, so task lists refresh too. */
export function useSetSenderBlocked() {
  const qc = useQueryClient();
  const refresh = useRefreshTasks();
  return useMutation({
    mutationFn: ({ address, block }: { address: string; block: boolean }) =>
      api(`/university/senders/${block ? "block" : "unblock"}`, { method: "POST", body: { address } }),
    onSettled: () => Promise.all([qc.invalidateQueries({ queryKey: ["university-senders"] }), refresh()]),
  });
}

export function useToday() {
  return useQuery({ queryKey: ["today"], queryFn: () => api<Today>("/today") });
}

export function useTasks(status: TaskStatus) {
  return useQuery({
    queryKey: ["tasks", status],
    queryFn: () => api<{ tasks: Task[] }>(`/tasks?status=${status}`).then((r) => r.tasks),
  });
}

/** Anything that changes tasks refreshes both the Today page and the task lists. */
export function useRefreshTasks() {
  const qc = useQueryClient();
  return () => Promise.all([qc.invalidateQueries({ queryKey: ["today"] }), qc.invalidateQueries({ queryKey: ["tasks"] })]);
}

export function useUpdateTask() {
  const refresh = useRefreshTasks();
  return useMutation({
    mutationFn: ({ id, ...body }: { id: number; status?: TaskStatus; priority?: Priority }) =>
      api<Task>(`/tasks/${id}`, { method: "PATCH", body }),
    onSettled: refresh,
  });
}

export function useSyncAll() {
  const refresh = useRefreshTasks();
  return useMutation({
    mutationFn: () => api<SyncResult>("/sync/all", { method: "POST" }),
    onSuccess: (result) => {
      // The syncs still ran; only reading emails stopped, so the reply is 200.
      if ("credit_low" in result.extraction && result.extraction.credit_low) setCreditLow(true);
      return refresh();
    },
  });
}
