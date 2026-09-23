'use client';

/**
 * The telemetry console.
 *
 * Every line here is something that actually happened. The temptation with a
 * log like this is to invent activity so the wait feels shorter — a fake crawl,
 * a stage that ticks on a timer. That would be the interface claiming a
 * capability the product does not have, and the first time a job stalled the
 * log would keep cheerfully scrolling. So lines are appended only when the
 * upload reports a part, or the server sends a job event.
 */

import { useEffect, useRef } from 'react';

import { cx } from '@/components/ui';

/** The three phases the machine passes through, in the machine's own words. */
export const PHASES = [
  { id: 'slice', label: 'SLICING_STREAM_INTO_TEMPORARY_CACHE' },
  { id: 'infer', label: 'MODEL_INFERENCE_CORE_PROCESSING' },
  { id: 'pack', label: 'HLS_TRANSCODING_MULTI_RESOLUTION' },
] as const;

export type PhaseId = (typeof PHASES)[number]['id'];
export type PhaseState = 'idle' | 'active' | 'done';

export interface LogLine {
  /** Monotonic, so React keys stay stable as the buffer is trimmed. */
  id: number;
  text: string;
  tone?: 'normal' | 'good' | 'bad';
}

export function TelemetryLog({
  lines,
  phases,
  idleMessage = 'Idle. Drop a recording to begin.',
}: {
  lines: readonly LogLine[];
  phases: Readonly<Record<PhaseId, PhaseState>>;
  idleMessage?: string;
}) {
  const scroller = useRef<HTMLDivElement>(null);

  // Follow the tail. Jumping to the bottom on every render would fight anyone
  // scrolling back, but the log is short-lived and unscrolled in practice.
  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lines]);

  return (
    <div className="flex h-full flex-col">
      <div
        ref={scroller}
        // A log is a feed: announce new lines, but politely — this is status,
        // not something the reader must act on.
        role="log"
        aria-live="polite"
        aria-label="Processing telemetry"
        className="scanlines min-h-[220px] flex-1 overflow-y-auto bg-void p-4 font-mono text-[12px] leading-relaxed"
      >
        {lines.length === 0 ? (
          <p className="text-dust">{idleMessage}</p>
        ) : (
          <ul className="flex flex-col gap-1">
            {lines.map((line) => (
              <li
                key={line.id}
                className={cx(
                  line.tone === 'good' && 'text-acid',
                  line.tone === 'bad' && 'text-crimson',
                  (line.tone === undefined || line.tone === 'normal') && 'text-chalk/85',
                )}
              >
                <span className="text-dust">›</span> {line.text}
              </li>
            ))}
            <li className="text-acid">
              <span className="cursor-blink">█</span>
            </li>
          </ul>
        )}
      </div>

      {/* The three phases, as a checklist rather than a progress bar: a bar
          implies a rate, and these have wildly different durations. */}
      <ul className="flex flex-col gap-2 border-t-4 border-edge p-4">
        {PHASES.map((phase) => {
          const state = phases[phase.id];
          return (
            <li key={phase.id} className="flex items-center gap-3">
              <span
                aria-hidden="true"
                className={cx(
                  'grid h-4 w-4 shrink-0 place-items-center border-2 border-edge text-[10px] font-bold',
                  state === 'done' && 'bg-acid text-edge',
                  state === 'active' && 'ticker bg-crimson text-white',
                  state === 'idle' && 'bg-raised',
                )}
              >
                {state === 'done' ? '✓' : ''}
              </span>
              <span
                className={cx(
                  'font-mono text-[11px] tracking-[0.06em]',
                  state === 'done' && 'text-acid',
                  state === 'active' && 'text-chalk',
                  state === 'idle' && 'text-dust',
                )}
              >
                {phase.label}
              </span>
              {/* Never colour alone: the state is also a word (NFR-A11Y-06). */}
              <span className="sr-only">
                {state === 'done' ? 'complete' : state === 'active' ? 'in progress' : 'waiting'}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
