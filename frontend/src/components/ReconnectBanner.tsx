import { dismissReconnect, useReconnectNeeded } from "../api";

export function ReconnectBanner() {
  if (!useReconnectNeeded()) return null;
  return (
    <div role="alert" className="flex items-center gap-3 border-b border-accent/30 bg-accent-soft px-4 py-2.5 text-body sm:px-8">
      <span className="size-2 shrink-0 rounded-full bg-accent" aria-hidden />
      <p className="flex-1">Google access has expired or was revoked. Syncing Gmail and Calendar won't work until you reconnect.</p>
      <a href="/auth/google/login" className="shrink-0 rounded-md bg-accent px-3 py-1.5 font-medium text-accent-ink hover:opacity-90">
        Reconnect Google
      </a>
      <button type="button" onClick={dismissReconnect} aria-label="Dismiss" className="shrink-0 px-1 text-muted hover:text-ink">
        ✕
      </button>
    </div>
  );
}
