import { setCreditLow, useCreditLow } from "../api";

export function CreditBanner() {
  if (!useCreditLow()) return null;
  return (
    <div role="alert" className="flex items-center gap-3 border-b border-accent/30 bg-accent-soft px-4 py-2.5 text-body sm:px-8">
      <span className="size-2 shrink-0 rounded-full bg-accent" aria-hidden />
      <p className="flex-1">
        API credit is low. Reading emails and chat are paused until you add credit in the Anthropic Console. No emails were marked as failed.
      </p>
      <button type="button" onClick={() => setCreditLow(false)} aria-label="Dismiss" className="shrink-0 px-1 text-muted hover:text-ink">
        ✕
      </button>
    </div>
  );
}
