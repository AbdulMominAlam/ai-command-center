export function Connect() {
  return (
    <main className="grid min-h-dvh place-items-center px-4">
      <div className="w-full max-w-sm">
        <div className="flex items-center gap-2">
          <span className="size-2.5 rounded-full bg-accent" aria-hidden />
          <span className="eyebrow">Command Center</span>
        </div>
        <h1 className="mt-6 font-display text-display text-ink">Your week, in one place.</h1>
        <p className="mt-4 text-lead text-muted">
          Connect your Google account to pull in Gmail and Calendar. Access is read-only, and you can disconnect it any
          time from your Google account settings.
        </p>
        <a
          href="/auth/google/login"
          className="mt-8 flex w-full items-center justify-center gap-2 rounded-lg bg-ink px-4 py-3 text-body font-medium text-paper hover:opacity-90"
        >
          Connect Google
        </a>
      </div>
    </main>
  );
}
