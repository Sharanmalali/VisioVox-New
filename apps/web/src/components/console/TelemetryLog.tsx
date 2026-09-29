'use client';

/**
 * The activity feed: what is happening to the customer's video, in their words.
 *
 * Every line here is something that actually happened. The temptation with a
 * feed like this is to invent activity so the wait feels shorter - a fake
 * crawl, a stage that ticks on a timer. That would be the interface claiming a
 * capability the product does not have, and the first time a job stalled the
 * feed would keep cheerfully scrolling. So lines are appended only when the
 * upload reports progress or the server sends a job event.
 *
 * Below the feed, the same steps as the progress bar (lib/journey.ts), as a
 * checklist: a bar implies a rate, and these steps have very different lengths.
 */

import { useEffect, useRef } from 'react';

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
  done: 'complete',
  active: 'in progress',
  failed: 'stopped',
  pending: 'waiting',
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
    <div className="flex h-full flex-col">
      <div
        ref={scroller}
        // A feed: announce new lines, but politely - this is status, not
        // something the reader must act on.
        role="log"
        aria-live="polite"
        aria-label="Activity for your video"
        className="scanlines min-h-[220px] flex-1 overflow-y-auto bg-void p-4 text-[13px] leading-relaxed"
      >
        {lines.length === 0 ? (
          <p className="text-dust">{idleMessage}</p>
        ) : (
          <ol className="flex flex-col gap-2.5">
            {lines.map((line) => (
              <li key={line.id} className="grid grid-cols-[auto_1fr] gap-x-3">
                <time
                  dateTime={new Date(line.at).toISOString()}
                  className="pt-px font-mono text-[11px] tabular-nums text-dust"
                >
                  {clock.format(line.at)}
                </time>
                <span
                  className={cx(
                    line.tone === 'good' && 'text-acid',
                    line.tone === 'bad' && 'text-crimson',
                    (line.tone === undefined || line.tone === 'normal') && 'text-chalk/90',
                  )}
                >
                  {line.text}
                </span>
              </li>
            ))}
          </ol>
        )}
      </div>

      <ol className="flex flex-col gap-2 border-t-4 border-edge p-4" aria-label="Progress">
        {STEPS.map((step, i) => {
          const state = steps[step.id];
          return (
            <li key={step.id} className="flex items-center gap-3">
              <span
                aria-hidden="true"
                className={cx(
                  'grid h-5 w-5 shrink-0 place-items-center border-2 border-edge font-mono text-[10px] font-bold',
                  state === 'done' && 'bg-acid text-edge',
                  state === 'active' && 'ticker bg-crimson text-white',
                  state === 'failed' && 'bg-crimson text-white',
                  state === 'pending' && 'bg-raised text-dust',
                )}
              >
                {state === 'done' ? '✓' : state === 'failed' ? '✕' : String(i + 1)}
              </span>
              <span
                className={cx(
                  'text-[13px]',
                  state === 'done' && 'text-acid',
                  (state === 'active' || state === 'failed') && 'font-semibold text-chalk',
                  state === 'pending' && 'text-dust',
                )}
              >
                {step.title}
              </span>
              {/* Never colour alone: the state is also a word (NFR-A11Y-06). */}
              <span className="sr-only">{STATE_WORD[state]}</span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
