/**
 * How the app talks about a project: one place for status words, times and
 * language names, so the list and the detail page never describe the same
 * project differently.
 */

export type StatusTone = 'neutral' | 'accent' | 'success' | 'warning' | 'danger';

export interface StatusMeta {
  label: string;
  tone: StatusTone;
  /** Work is under way; shown with a pulsing dot. */
  live: boolean;
}

const STATUS: Record<string, StatusMeta> = {
  pending: { label: 'Waiting for upload', tone: 'neutral', live: false },
  validating: { label: 'Checking file', tone: 'accent', live: true },
  queued: { label: 'Queued', tone: 'accent', live: true },
  processing: { label: 'Processing', tone: 'accent', live: true },
  ready: { label: 'Ready', tone: 'success', live: false },
  failed: { label: 'Failed', tone: 'danger', live: false },
  cancelled: { label: 'Cancelled', tone: 'neutral', live: false },
  expired: { label: 'Expired', tone: 'warning', live: false },
};

export function statusMeta(status: string): StatusMeta {
  return STATUS[status] ?? { label: status, tone: 'neutral', live: false };
}

const RELATIVE = new Intl.RelativeTimeFormat('en', { numeric: 'auto' });

/** "3 minutes ago", "yesterday", or a date once it is more than a week old. */
export function relativeTime(iso: string, now: number = Date.now()): string {
  const then = new Date(iso).getTime();
  const seconds = Math.round((then - now) / 1000);
  const abs = Math.abs(seconds);
  if (abs < 45) return 'just now';
  if (abs < 3600) return RELATIVE.format(Math.round(seconds / 60), 'minute');
  if (abs < 86400) return RELATIVE.format(Math.round(seconds / 3600), 'hour');
  if (abs < 7 * 86400) return RELATIVE.format(Math.round(seconds / 86400), 'day');
  return new Date(iso).toLocaleDateString(undefined, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  });
}

/** 125000 -> "2:05"; 3725000 -> "1:02:05". */
export function clockDuration(ms: number | null | undefined): string | null {
  if (ms === null || ms === undefined) return null;
  const total = Math.round(ms / 1000);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const ss = String(s).padStart(2, '0');
  return h > 0 ? `${String(h)}:${String(m).padStart(2, '0')}:${ss}` : `${String(m)}:${ss}`;
}

let names: Intl.DisplayNames | null = null;

/** "ta" -> "Tamil". Falls back to the code for Whisper-only codes. */
export function languageName(code: string | null | undefined): string | null {
  if (!code) return null;
  try {
    names ??= new Intl.DisplayNames(['en'], { type: 'language' });
    return names.of(code) ?? code;
  } catch {
    return code;
  }
}
