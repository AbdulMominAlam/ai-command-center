// Every time on the dashboard is shown in Istanbul time, whatever the device's zone.
const TZ = "Europe/Istanbul";

const fmt = (opts: Intl.DateTimeFormatOptions) => new Intl.DateTimeFormat("en-GB", { timeZone: TZ, ...opts });

const timeFmt = fmt({ hour: "2-digit", minute: "2-digit", hour12: false });
const weekdayFmt = fmt({ weekday: "short" });
const dayFmt = fmt({ weekday: "short", day: "numeric", month: "short" });
const longWeekday = fmt({ weekday: "long" });
const longDate = fmt({ day: "numeric", month: "long" });
const ymdFmt = new Intl.DateTimeFormat("en-CA", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit" });

export const time = (iso: string) => timeFmt.format(new Date(iso));
export const ymd = (d: Date) => ymdFmt.format(d);

/** Date-only deadlines are stored as Istanbul midnight; showing "00:00" would mislead. */
const isMidnight = (iso: string) => time(iso) === "00:00";

/** "23:59" for today, "Thu 23:59" this week, "Mon 12 Oct · 23:59" otherwise. */
export function dueLabel(iso: string, style: "time" | "weekday" | "date") {
  const d = new Date(iso);
  const t = isMidnight(iso) ? "" : time(iso);
  if (style === "time") return t || "Today";
  const day = style === "weekday" ? weekdayFmt.format(d) : dayFmt.format(d);
  return t ? `${day} · ${t}` : day;
}

/** "3h late", "2d late" for overdue tasks. */
export function lateBy(iso: string, now: Date) {
  const mins = Math.max(0, (now.getTime() - new Date(iso).getTime()) / 60000);
  if (mins < 60) return `${Math.round(mins)}m late`;
  if (mins < 60 * 24) return `${Math.round(mins / 60)}h late`;
  return `${Math.round(mins / 60 / 24)}d late`;
}

export function headerDate(iso: string) {
  const d = new Date(iso);
  return { weekday: longWeekday.format(d), date: longDate.format(d) };
}

export function money(usd: number | null) {
  if (usd === null) return "cost unknown";
  return usd < 0.01 ? `$${usd.toFixed(4)}` : `$${usd.toFixed(3)}`;
}

export const SOURCE_LABEL: Record<string, string> = {
  gmail: "Gmail",
  sucourse: "SUCourse",
  agent: "Agent",
  user: "You",
  calendar: "Calendar",
  extraction: "Gmail",
};
