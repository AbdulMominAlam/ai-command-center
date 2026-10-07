import { useState, type ReactNode } from "react";
import { ChatIcon, ListIcon, MoonIcon, SunIcon, TodayIcon } from "./icons";
import { CreditBanner } from "./CreditBanner";
import { ReconnectBanner } from "./ReconnectBanner";

// "label" (the eval labeling tool) is reached by typing #/label; it is not in the nav.
export type Page = "today" | "tasks" | "chat" | "label";

const NAV: { page: Exclude<Page, "label">; label: string; icon: () => ReactNode }[] = [
  { page: "today", label: "Today", icon: TodayIcon },
  { page: "tasks", label: "Tasks", icon: ListIcon },
  { page: "chat", label: "Ask", icon: ChatIcon },
];

function isDark() {
  const set = document.documentElement.dataset.theme;
  return set ? set === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
}

function ThemeToggle() {
  const [dark, setDark] = useState(isDark);
  const toggle = () => {
    const next = dark ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem("theme", next);
    } catch {}
    setDark(!dark);
  };
  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={dark ? "Switch to light mode" : "Switch to dark mode"}
      className="grid size-9 place-items-center rounded-lg text-muted hover:bg-sunken hover:text-ink"
    >
      {dark ? <SunIcon /> : <MoonIcon />}
    </button>
  );
}

export function Shell({ page, email, children }: { page: Page; email?: string; children: ReactNode }) {
  return (
    <div className="min-h-dvh md:grid md:grid-cols-[208px_1fr]">
      {/* Laptop: a quiet left rail */}
      <aside className="sticky top-0 hidden h-dvh flex-col border-r border-line px-4 py-6 md:flex">
        <div className="flex items-center gap-2 px-2">
          <span className="size-2.5 rounded-full bg-accent" aria-hidden />
          <span className="text-body font-semibold tracking-tight">Command Center</span>
        </div>
        <nav className="mt-8 flex flex-col gap-0.5" aria-label="Main">
          {NAV.map(({ page: p, label, icon: Icon }) => (
            <a
              key={p}
              href={`#/${p === "today" ? "" : p}`}
              aria-current={page === p ? "page" : undefined}
              className={`flex items-center gap-3 rounded-lg px-2 py-2 text-body transition-colors ${
                page === p ? "bg-surface font-medium text-ink shadow-[0_0_0_1px_var(--line)]" : "text-muted hover:text-ink"
              }`}
            >
              <span className={page === p ? "text-accent" : ""}><Icon /></span>
              {label}
            </a>
          ))}
        </nav>
        <div className="mt-auto flex items-center justify-between gap-2 px-2">
          <span className="truncate font-mono text-meta text-faint" title={email}>{email}</span>
          <ThemeToggle />
        </div>
      </aside>

      <div className="flex min-w-0 flex-col">
        {/* Phone: slim top bar */}
        <div className="flex items-center justify-between border-b border-line px-4 py-2 md:hidden">
          <div className="flex items-center gap-2">
            <span className="size-2.5 rounded-full bg-accent" aria-hidden />
            <span className="text-body font-semibold tracking-tight">Command Center</span>
          </div>
          <ThemeToggle />
        </div>
        <ReconnectBanner />
        <CreditBanner />
        <main className="flex-1 pb-20 md:pb-0">{children}</main>
      </div>

      {/* Phone: bottom tab bar, within thumb reach */}
      <nav
        aria-label="Main"
        className="fixed inset-x-0 bottom-0 z-20 grid grid-cols-3 border-t border-line bg-paper/95 pb-[env(safe-area-inset-bottom)] backdrop-blur md:hidden"
      >
        {NAV.map(({ page: p, label, icon: Icon }) => (
          <a
            key={p}
            href={`#/${p === "today" ? "" : p}`}
            aria-current={page === p ? "page" : undefined}
            className={`flex flex-col items-center gap-1 py-2.5 text-meta ${page === p ? "text-accent" : "text-muted"}`}
          >
            <Icon />
            {label}
          </a>
        ))}
      </nav>
    </div>
  );
}
