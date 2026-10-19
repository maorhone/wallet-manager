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

export function freshness(ts: string | Date, now: Date = new Date()): string {
  const minutes = Math.max(0, Math.floor((now.getTime() - new Date(ts).getTime()) / 60000));
  return `עודכן לפני ${minutes} דקות`;
}
