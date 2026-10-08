'use client';

/**
 * The activity panel: where the customer's video is, in their words.
 *
 * Every line here is something that actually happened. The temptation with a
 * feed like this is to invent activity so the wait feels shorter - a fake
 * crawl, a stage that ticks on a timer. That would be the interface claiming a
 * capability the product does not have, and the first time a job stalled the
 * feed would keep cheerfully scrolling. So lines are appended only when the
 * upload reports progress or the server sends a job event.
 *
 * Above the feed, the same steps as the progress bar (lib/journey.ts), as a
 * stepper: a bar implies a rate, and these steps have very different lengths.
 */

import { useEffect, useRef } from 'react';

import { IconCheck, IconX } from '@/components/icons';
import { cx } from '@/components/ui';
import { STEPS, type StepId, type StepState } from '@/lib/journey';

export interface LogLine {
  /** Monotonic, so React keys stay stable as the buffer is trimmed. */
  id: number;
  /** When it happened (epoch ms), shown as a clock time. */
  at: number;
  text: string;
  tone?: 'normal' | 'good' | 'bad';
}

const clock = new Intl.DateTimeFormat(undefined, {
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
});

const STATE_WORD: Record<StepState, string> = {
  done: 'Complete',
  active: 'In progress',
  failed: 'Stopped',
  pending: 'Waiting',
};

export function TelemetryLog({
  lines,
  steps,
  idleMessage = 'Choose a recording to begin. Updates about your video will appear here.',
}: {
  lines: readonly LogLine[];
  steps: Readonly<Record<StepId, StepState>>;
  idleMessage?: string;
}) {
  const scroller = useRef<HTMLDivElement>(null);

  // Follow the tail. The feed is short-lived and rarely scrolled back.
  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lines]);

  return (
    <div className="flex flex-col">
      <ol className="flex flex-col px-5 pb-2 pt-5 sm:px-6" aria-label="Progress">
        {STEPS.map((step, i) => {
          const state = steps[step.id];
          const last = i === STEPS.length - 1;
          return (
            <li key={step.id} className="relative flex gap-3.5 pb-5 last:pb-3">
              {!last && (
                <span
                  aria-hidden="true"
                  className={cx(
                    'absolute left-[11px] top-7 h-[calc(100%-22px)] w-0.5 rounded-full',
                    state === 'done' ? 'bg-success' : 'bg-border-subtle',
                  )}
                />
              )}
              <span
                aria-hidden="true"
                className={cx(
                  'relative z-[1] grid h-6 w-6 shrink-0 place-items-center rounded-full text-[11px] font-semibold',
                  state === 'done' && 'bg-success text-white',
                  state === 'active' &&
                    'bg-accent text-fg-on-accent shadow-[0_0_0_4px_var(--accent-ring)]',
                  state === 'failed' && 'bg-danger text-white',
                  state === 'pending' && 'border border-border-default bg-bg-surface text-fg-muted',
                )}
              >
                {state === 'done' ? (
                  <IconCheck size={13} strokeWidth={2.5} />
                ) : state === 'failed' ? (
                  <IconX size={12} strokeWidth={2.5} />
                ) : (
                  String(i + 1)
                )}
              </span>
              <div className="flex min-w-0 flex-1 items-center justify-between gap-3 pt-0.5">
                <span
                  className={cx(
                    'text-sm',
                    state === 'pending' ? 'text-fg-muted' : 'font-medium text-fg-primary',
                  )}
                >
                  {step.title}
                </span>
                {/* Never colour alone: the state is also a word (NFR-A11Y-06). */}
                <span
                  className={cx(
                    'text-[12px]',
                    state === 'done' && 'text-success',
                    state === 'active' && 'font-medium text-accent',
                    state === 'failed' && 'text-danger',
                    state === 'pending' && 'text-fg-muted',
                  )}
                >
                  {STATE_WORD[state]}
                </span>
              </div>
            </li>
          );
        })}
      </ol>

      <div className="border-t border-border-subtle">
        <div
          ref={scroller}
          // A feed: announce new lines, but politely - this is status, not
          // something the reader must act on.
          role="log"
          aria-live="polite"
          aria-label="Activity for your video"
          className="max-h-[300px] min-h-[160px] overflow-y-auto px-5 py-4 text-[13.5px] leading-relaxed sm:px-6"
        >
          {lines.length === 0 ? (
            <p className="text-fg-muted">{idleMessage}</p>
          ) : (
            <ol className="flex flex-col gap-2.5">
              {lines.map((line) => (
                <li key={line.id} className="grid grid-cols-[auto_1fr] gap-x-3">
                  <time
                    dateTime={new Date(line.at).toISOString()}
                    className="pt-[2px] font-mono text-[11.5px] tabular-nums text-fg-muted"
                  >
                    {clock.format(line.at)}
                  </time>
                  <span
                    className={cx(
                      line.tone === 'good' && 'font-medium text-success',
                      line.tone === 'bad' && 'font-medium text-danger',
                      (line.tone === undefined || line.tone === 'normal') && 'text-fg-secondary',
                    )}
                  >
                    {line.text}
                  </span>
                </li>
              ))}
            </ol>
          )}
        </div>
      </div>
    </div>
  );
}
