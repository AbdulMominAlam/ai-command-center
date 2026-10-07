// Small stroke icons, drawn at 16px and coloured by currentColor.
const base = { width: 16, height: 16, viewBox: "0 0 16 16", fill: "none", stroke: "currentColor", strokeWidth: 1.5, strokeLinecap: "round", strokeLinejoin: "round" } as const;

export const SunIcon = () => (
  <svg {...base} aria-hidden><circle cx="8" cy="8" r="3" /><path d="M8 1.5v1.5M8 13v1.5M1.5 8H3M13 8h1.5M3.4 3.4l1 1M11.6 11.6l1 1M3.4 12.6l1-1M11.6 4.4l1-1" /></svg>
);
export const MoonIcon = () => (
  <svg {...base} aria-hidden><path d="M13.5 9.5A5.5 5.5 0 0 1 6.5 2.5a5.5 5.5 0 1 0 7 7Z" /></svg>
);
export const TodayIcon = () => (
  <svg {...base} aria-hidden><rect x="2" y="3" width="12" height="11" rx="2" /><path d="M2 6.5h12M5.5 1.5v3M10.5 1.5v3" /><circle cx="8" cy="10" r="1" fill="currentColor" /></svg>
);
export const ListIcon = () => (
  <svg {...base} aria-hidden><path d="M6 4h8M6 8h8M6 12h8" /><path d="m1.8 4 .9.9L4.4 3.2" /><circle cx="3" cy="8" r=".6" fill="currentColor" /><circle cx="3" cy="12" r=".6" fill="currentColor" /></svg>
);
export const ChatIcon = () => (
  <svg {...base} aria-hidden><path d="M2.5 4.5a2 2 0 0 1 2-2h7a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2H7l-3 2.5v-2.5h0a2 2 0 0 1-1.5-2Z" /></svg>
);
export const GearIcon = () => (
  <svg {...base} aria-hidden><circle cx="8" cy="8" r="2" /><path d="M8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2M3.4 3.4l1.4 1.4M11.2 11.2l1.4 1.4M3.4 12.6l1.4-1.4M11.2 4.8l1.4-1.4" /></svg>
);
export const SyncIcon = ({ spinning = false }: { spinning?: boolean }) => (
  <svg {...base} aria-hidden className={spinning ? "spin" : undefined}><path d="M13.5 8a5.5 5.5 0 0 1-9.6 3.6M2.5 8a5.5 5.5 0 0 1 9.6-3.6" /><path d="M12.5 1.8v2.8H9.7M3.5 14.2v-2.8h2.8" /></svg>
);
export const CheckIcon = () => (
  <svg {...base} width={12} height={12} strokeWidth={2.2} aria-hidden><path d="m3.5 8.5 3 3 6-7" /></svg>
);
export const ArrowUpIcon = () => (
  <svg {...base} strokeWidth={1.8} aria-hidden><path d="M8 13V3M3.5 7.5 8 3l4.5 4.5" /></svg>
);
