import type { AccountLabel } from "../types";

// A small tag saying which Google account a task or event came from.
export function AccountTag({ label }: { label: AccountLabel | null | undefined }) {
  if (!label) return null;
  return (
    <span
      className={`inline-block rounded px-1.5 py-px align-[1px] font-mono text-[0.6875rem] leading-4 ${
        label === "Sabancı" ? "bg-accent-soft text-ink" : "bg-sunken text-muted"
      }`}
    >
      {label}
    </span>
  );
}
