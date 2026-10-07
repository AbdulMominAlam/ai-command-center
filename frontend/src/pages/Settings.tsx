import { AccountTag } from "../components/AccountTag";
import { Section } from "../components/Section";
import { dueLabel } from "../format";
import { useAccounts } from "../queries";

export function SettingsPage() {
  const { data: accounts, isPending, error } = useAccounts();

  return (
    <div className="mx-auto max-w-3xl px-4 py-8 sm:px-8 sm:py-12">
      <p className="eyebrow">Settings</p>
      <h1 className="mt-3 font-display text-display text-ink">Accounts</h1>

      <div className="mt-8">
        <Section title="Linked Google accounts" count={accounts?.length} empty="No Google account linked.">
          {isPending && <p className="py-3 text-body text-muted">Loading…</p>}
          {error && <p className="py-3 text-body text-accent">{error.message}</p>}
          {accounts && accounts.length > 0 && (
            <ul className="divide-y divide-line">
              {accounts.map((a) => (
                <li key={a.id} className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 py-3">
                  <div className="min-w-0">
                    <p className="text-body text-ink [overflow-wrap:anywhere]">
                      {a.email} <AccountTag label={a.label} />
                    </p>
                    {a.label === "Sabancı" && (
                      <p className="mt-0.5 text-meta text-muted">
                        Course-admin emails (NS101, recitations, worksheets, LA) are never sent to Claude.
                      </p>
                    )}
                  </div>
                  <span className="font-mono text-meta text-muted">
                    {a.last_synced_at ? `Synced ${dueLabel(a.last_synced_at, "date")}` : "Not synced yet"}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Section>
      </div>

      {/* A full page load, not a fetch: the backend redirects to Google and back. */}
      <a
        href="/auth/google/link"
        className="mt-6 inline-block rounded-lg bg-accent px-3.5 py-1.5 text-body font-medium text-accent-ink hover:opacity-90"
      >
        Link another Google account
      </a>
      <p className="mt-2 text-meta text-faint">
        Google asks which account to use. Its first sync reads the last 14 days of mail, and reading those emails
        stops at an estimated $0.30.
      </p>
    </div>
  );
}
