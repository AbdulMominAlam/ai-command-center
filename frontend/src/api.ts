import { useSyncExternalStore } from "react";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public reconnect = false,
  ) {
    super(message);
  }
}

// --- "Reconnect Google" flag, shared by every request -------------------------

let reconnectNeeded = false;
const listeners = new Set<() => void>();

function setReconnect(value: boolean) {
  reconnectNeeded = value;
  listeners.forEach((l) => l());
}

export function dismissReconnect() {
  setReconnect(false);
}

export function useReconnectNeeded() {
  return useSyncExternalStore(
    (l) => (listeners.add(l), () => listeners.delete(l)),
    () => reconnectNeeded,
  );
}

// --- "API credit is low" flag: set by a 402 reply or a sync that stopped for it --

let creditLow = false;
const creditListeners = new Set<() => void>();

export function setCreditLow(value: boolean) {
  creditLow = value;
  creditListeners.forEach((l) => l());
}

export function useCreditLow() {
  return useSyncExternalStore(
    (l) => (creditListeners.add(l), () => creditListeners.delete(l)),
    () => creditLow,
  );
}

// --- fetch wrapper ------------------------------------------------------------

function detailText(detail: unknown): string {
  if (typeof detail === "string") return detail;
  // FastAPI validation errors are a list of {msg}
  if (Array.isArray(detail)) return detail.map((d) => d?.msg ?? String(d)).join("; ");
  return "Something went wrong.";
}

export async function api<T>(path: string, init: { method?: string; body?: unknown } = {}): Promise<T> {
  const res = await fetch(path, {
    method: init.method ?? "GET",
    credentials: "same-origin",
    headers: init.body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
  });
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    // The backend adds reconnect_url when the stored Google token stopped working.
    const reconnect = res.status === 401 && Boolean(data?.reconnect_url);
    if (reconnect) setReconnect(true);
    // The backend answers 402 with credit_low when the Anthropic account is out of credit.
    if (res.status === 402 && data?.credit_low) setCreditLow(true);
    throw new ApiError(res.status, detailText(data?.detail) || res.statusText, reconnect);
  }
  return data as T;
}
