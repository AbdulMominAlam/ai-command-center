import type { ReactNode } from "react";

interface Props {
  title: string;
  count?: number;
  accent?: boolean;
  empty?: string;
  children?: ReactNode;
}

export function Section({ title, count, accent = false, empty, children }: Props) {
  const isEmpty = count === 0;
  return (
    <section>
      <header className="flex items-baseline gap-2 border-b border-line pb-2">
        <h2 className={`eyebrow ${accent && !isEmpty ? "!text-accent" : ""}`}>{title}</h2>
        {count !== undefined && <span className="font-mono text-meta text-faint tabular-nums">{count}</span>}
      </header>
      {isEmpty && empty ? <p className="py-4 text-body text-faint">{empty}</p> : children}
    </section>
  );
}
