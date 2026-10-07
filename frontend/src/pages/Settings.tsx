import { AccountTag } from "../components/AccountTag";
import { Section } from "../components/Section";
import { dueLabel } from "../format";
import { useAccounts, useSetSenderBlocked, useUniversitySenders } from "../queries";

export function SettingsPage() {
  const { data: accounts, isPending, error } = useAccounts();
  const hasUniversity = !!accounts?.some((a) => a.label === "Sabancı");

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
                        Emails are read by Claude unless they mention NS101, recitations, worksheets or LA, or
                        come from a sender you block below.
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

      {hasUniversity && <UniversitySenders />}
    </div>
  );
}

function UniversitySenders() {
  const { data: senders, isPending, error } = useUniversitySenders(true);
  const setBlocked = useSetSenderBlocked();

  return (
    <div className="mt-12">
      <Section title="University senders" count={senders?.length} empty="No university emails synced yet.">
        <p className="py-3 text-meta text-muted">
          Everyone who emailed your Sabancı account. Blocking a sender stops their emails from reaching Claude, hides
          them from Ask, and marks their open tasks as done.
        </p>
        {isPending && <p className="py-3 text-body text-muted">Loading…</p>}
        {error && <p className="py-3 text-body text-accent">{error.message}</p>}
        {setBlocked.isError && <p className="py-1 text-meta text-accent">Couldn't save. Try again.</p>}
        {senders && senders.length > 0 && (
          <ul className="divide-y divide-line">
            {senders.map((s) => {
              const busy = setBlocked.isPending && setBlocked.variables?.address === s.address;
              return (
                <li key={s.address} className={`flex items-center gap-3 py-3 ${s.blocked ? "opacity-60" : ""}`}>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-body text-ink">{s.name || s.address}</p>
                    <p className="truncate font-mono text-meta text-muted">
                      {s.name ? `${s.address} · ` : ""}
                      {s.email_count} {s.email_count === 1 ? "email" : "emails"}
                      {s.last_email_at && ` · last ${dueLabel(s.last_email_at, "date")}`}
                    </p>
                  </div>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => setBlocked.mutate({ address: s.address, block: !s.blocked })}
                    className={`shrink-0 rounded-lg border px-3 py-1 text-meta disabled:opacity-60 ${
                      s.blocked ? "border-line text-muted hover:text-ink" : "border-accent text-accent hover:bg-accent-soft"
                    }`}
                  >
                    {busy ? "Saving…" : s.blocked ? "Unblock" : "Block"}
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </Section>
    </div>
  );
}
