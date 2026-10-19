import { ApiError } from '../api/client';
import { credits } from '../format';

type Env = {
  requested: string;
  available: string;
  parent_total: string;
  missing: string;
  min: string;
  max: string;
  owner_role: string;
};

const MESSAGES: Record<string, (e: Env) => string> = {
  PARENT_OVERALLOCATED: (e) =>
    `חילקת ${e.requested} מתוך ${e.available}. יש להפחית ${e.missing} לפני שמירה`,
  PMO_POOL_INSUFFICIENT: (e) =>
    `נדרשים ${e.requested} אך בקופת גוף הפרויקטים זמינים ${e.available}. יש להקטין את הסכום או לבקש הגדלת הקופה ממנהל המחלקה`,
  SHARE_EXCEEDS_GRANT: (e) =>
    `סכום החלוקה ${e.requested} חורג ממסגרת המענק ${e.available}. יש להקטין את החלוקה`,
  SHARE_EXPIRY_AFTER_GRANT: () => 'תוקף החלוקה מאוחר מתוקף המענק. יש לבחור תאריך עד תום תוקף המענק',
  LIMIT_OUT_OF_POLICY: (e) =>
    `הערך ${e.requested} מחוץ לטווח המותר (${e.min}–${e.max}). יש לבחור ערך בטווח או לפנות למנהל הפלטפורמה`,
  SELF_ALLOCATION_FORBIDDEN: (e) =>
    `לא ניתן לשנות ערכים אישיים של עצמך. הערכים נקבעים על ידי ${e.owner_role}`,
  SCOPE_FORBIDDEN: () => 'היעד אינו בתחום האחריות שלך. יש לבחור עובד או קבוצה מתוך התחום',
  GRANT_BELOW_SHARED: (e) =>
    `סכום המענק ${e.requested} נמוך ממה שכבר חולק (${e.available}). יש להגדיל את המענק או לצמצם חלוקות`,
  FORBIDDEN: () => 'אין לך הרשאה לפעולה זו. יש לפנות למנהל המחלקה או הפלטפורמה',
  CSRF_FAILED: () => 'פג תוקף ההפעלה של הדף. יש לרענן את הדף ולנסות שוב',
  INVALID_REQUEST: () => 'הבקשה אינה תקינה. יש לבדוק את הערכים, את סיבת השינוי ולנסות שוב',
  NOT_FOUND: () => 'הפריט לא נמצא או שאינו בתחום שלך. יש לבחור תחום אחר מהתפריט',
  UNAUTHENTICATED: () => 'פג תוקף ההתחברות. יש להתחבר מחדש',
  NETWORK: () => 'אין חיבור לשרת. יש לבדוק את החיבור ולנסות שוב',
  UNKNOWN: () => 'אירעה שגיאה לא צפויה. יש לנסות שוב, ואם הבעיה נמשכת לפנות למנהל הפלטפורמה',
};

export const ERROR_CODES = Object.keys(MESSAGES);

const num = (n: unknown) => (typeof n === 'number' ? credits(n) : '');

export function errorMessage(err: unknown): string {
  if (!(err instanceof ApiError)) return MESSAGES.NETWORK({} as Env);
  const { code, requested, available, parent_total, details } = err.envelope;
  const d = details ?? {};
  const env: Env = {
    requested: num(requested),
    available: num(available ?? parent_total),
    parent_total: num(parent_total),
    missing:
      typeof requested === 'number' && typeof available === 'number'
        ? credits(requested - available)
        : '',
    min: String(d.min ?? ''),
    max: String(d.max ?? ''),
    owner_role: String(d.owner_role ?? 'בעל התפקיד המוסמך'),
  };
  return (MESSAGES[code] ?? MESSAGES.UNKNOWN)(env);
}
