const creditsFmt = new Intl.NumberFormat('he-IL', { maximumFractionDigits: 0 });
const pctFmt = new Intl.NumberFormat('en-US', {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});

export const credits = (n: number): string => creditsFmt.format(n);

export const pct = (n: number): string => pctFmt.format(n) + '%';

/** dd/mm/yyyy in the configured timezone (§12.1). */
export function date(d: string | Date, tz: string): string {
  return new Intl.DateTimeFormat('en-GB', {
    timeZone: tz,
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
  }).format(typeof d === 'string' ? new Date(d) : d);
}

/** yyyy-mm-dd today in the given timezone. */
export const todayIn = (tz: string, now: Date = new Date()): string =>
  new Intl.DateTimeFormat('en-CA', { timeZone: tz }).format(now);

/** The instant at which `day` (yyyy-mm-dd) shows `time` (hh:mm:ss) on a wall clock in `tz`. */
export function zonedInstant(day: string, time: string, tz: string): string {
  const wall = Date.parse(`${day}T${time}Z`);
  const offset = (t: number) => {
    const p = Object.fromEntries(
      new Intl.DateTimeFormat('en-US', {
        timeZone: tz,
        hourCycle: 'h23',
        year: 'numeric',
        month: 'numeric',
        day: 'numeric',
        hour: 'numeric',
        minute: 'numeric',
        second: 'numeric',
      })
        .formatToParts(new Date(t))
        .map((x) => [x.type, Number(x.value)]),
    );
    return (
      Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second) - Math.floor(t / 1000) * 1000
    );
  };
  const first = wall - offset(wall);
  return new Date(wall - offset(first)).toISOString();
}

export function freshness(ts: string | Date, now: Date = new Date()): string {
  const minutes = Math.max(0, Math.floor((now.getTime() - new Date(ts).getTime()) / 60000));
  return `עודכן לפני ${minutes} דקות`;
}
