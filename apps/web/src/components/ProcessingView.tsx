'use client';

/**
 * The engaged wait (docs/28 §W5).
 *
 * A 20-minute job with a silent progress bar feels broken. This narrates the
 * real pipeline, stage by stage, from the SSE stream the API already publishes
 * — the copy is tied to actual stage names, so it cannot drift into fiction
 * while the backend does something else.
 *
 * Three rules it follows:
 *   - Never show a countdown that was not measured. Until the pipeline is
 *     benchmarked (W8) the estimate is a range, and it widens rather than
 *     lying when a stage runs long.
 *   - Never park at 99%. Progress comes from completed stages, so it moves
 *     when something real finishes and stops when nothing has.
 *   - Survive a dropped connection and a backgrounded tab, because people will
 *     switch away and come back.
 */

import { useEffect, useRef, useState } from 'react';

import { IconCheck } from '@/components/icons';
import { Badge, Progress, Slab, SpeakerDot, Spinner, cx } from '@/components/ui';
import { api } from '@/lib/store';
import { formatDuration } from '@/lib/upload/resumable';

/**
 * What each stage is actually doing, in words a person can picture.
 *
 * Keyed on the stage identifiers the worker emits (docs/05 §2), so a renamed
 * stage shows its raw name rather than a confidently wrong description.
 */
export const STAGE_COPY: Record<string, { title: string; detail: string }> = {
  S0_ingest: {
    title: 'Reading the recording',
    detail: 'Checking the file is what it claims to be, and pulling out the audio.',
  },
  S1_enhance: {
    title: 'Cleaning the room',
    detail: 'Removing reverb and background noise before anyone is separated.',
  },
  S2a_audio: {
    title: 'Listening for turns',
    detail: 'Finding who speaks when, and where voices collide.',
  },
  S2b_video: {
    title: 'Watching faces',
    detail: 'Tracking each face and reading which mouth is moving.',
  },
  S3_fuse: {
    title: 'Matching faces to voices',
    detail: 'Deciding which face belongs to which voice.',
  },
  S4_enrol: {
    title: 'Learning each voice',
    detail: 'Mining clean moments so every speaker has a reference of their own.',
  },
  S5_extract: {
    title: 'Separating the speakers',
    detail: 'The part that needs the GPU. Each speaker is pulled out separately.',
  },
  S6_restore: {
    title: 'Restoring what was buried',
    detail: 'Repairing speech that was masked, without inventing words.',
  },
  S7_transcribe: {
    title: 'Writing the captions',
    detail: 'Transcribing each speaker on their own track.',
  },
  S8_audit: {
    title: 'Checking for leakage',
    detail: 'Looking for words that appear on two tracks at once.',
  },
  S9_package: {
    title: 'Packaging for playback',
    detail: 'Encoding the tracks so they stay in sync in the browser.',
  },
};

export interface JobEvent {
  status: string;
  progress: number;
  stage: string | null;
  speaker?: number | null;
  speakers?: number | null;
}

const TERMINAL = new Set(['succeeded', 'partial', 'failed', 'cancelled']);

/**
 * Subscribe to job progress, reconnecting with backoff.
 *
 * EventSource reconnects on its own, but not after the server closes the stream
 * cleanly, and not with any backoff worth the name. A job outlives a browser
 * tab's attention span, so this has to survive sleep, a lost network and a
 * backgrounded tab.
 */
function useJobProgress(projectId: string, token: string | null): JobEvent | null {
  const [event, setEvent] = useState<JobEvent | null>(null);
  const attemptRef = useRef(0);

  useEffect(() => {
    if (!token) return;
    let source: EventSource | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let closed = false;

    const base = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

    function connect(): void {
      if (closed) return;
      // EventSource cannot set an Authorization header, so the token rides as a
      // query parameter on a URL that is never logged with its query string.
      source = new EventSource(
        `${base}/v1/projects/${projectId}/events?access_token=${encodeURIComponent(token ?? '')}`,
      );

      source.addEventListener('progress', (e) => {
        attemptRef.current = 0;
        setEvent(JSON.parse((e as MessageEvent<string>).data) as JobEvent);
      });

      source.addEventListener('done', (e) => {
        setEvent(JSON.parse((e as MessageEvent<string>).data) as JobEvent);
        closed = true;
        source?.close();
      });

      source.addEventListener('error', () => {
        source?.close();
        if (closed) return;
        // Capped exponential backoff: a server that is down should not be hit
        // once a second by every open tab.
        attemptRef.current += 1;
        const delay = Math.min(30000, 1000 * 2 ** (attemptRef.current - 1));
        timer = setTimeout(connect, delay);
      });
    }

    connect();
    return () => {
      closed = true;
      source?.close();
      if (timer) clearTimeout(timer);
    };
  }, [projectId, token]);

  return event;
}

/** Tab title and favicon badge, so a backgrounded tab still reports progress. */
function useTabSignal(event: JobEvent | null, projectTitle: string): void {
  useEffect(() => {
    if (!event) return;
    const original = document.title;
    if (TERMINAL.has(event.status)) {
      document.title = event.status === 'succeeded' ? `✓ ${projectTitle}` : `× ${projectTitle}`;
    } else {
      document.title = `${String(event.progress)}% · ${projectTitle}`;
    }
    return () => {
      document.title = original;
    };
  }, [event, projectTitle]);
}

export function ProcessingView({
  projectId,
  projectTitle,
  token,
  speakerCount,
  onDone,
}: {
  projectId: string;
  projectTitle: string;
  token: string | null;
  speakerCount: number | null;
  /** Called once when the stream reports the job finished, so the page can
   *  load the result now instead of on its next slow poll. */
  onDone?: () => void;
}) {
  const event = useJobProgress(projectId, token);
  useTabSignal(event, projectTitle);

  const finished = event !== null && TERMINAL.has(event.status);
  useEffect(() => {
    if (finished) onDone?.();
  }, [finished, onDone]);

  const [startedAt] = useState(() => Date.now());
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const id = setInterval(() => {
      setElapsed((Date.now() - startedAt) / 1000);
    }, 1000);
    return () => {
      clearInterval(id);
    };
  }, [startedAt]);

  const stage = event?.stage ?? null;
  const copy = stage ? STAGE_COPY[stage] : undefined;
  const pct = event?.progress ?? 0;

  /**
   * Time left, from the server (apps/api estimates.py): each stage's time on
   * this machine, learned from its finished jobs, counted from where this job
   * actually is - so a reload or a slow stage does not throw it off. Asked
   * for every few seconds and counted down in between.
   */
  const [eta, setEta] = useState<{ seconds: number; at: number; jobs: number } | null>(null);
  useEffect(() => {
    let stopped = false;
    const ask = async () => {
      try {
        const job = await api().getJob(projectId);
        if (!stopped && job.eta_seconds != null) {
          setEta({ seconds: job.eta_seconds, at: Date.now(), jobs: job.estimate_jobs });
        }
      } catch {
        // The bar and the stages still work without it.
      }
    };
    void ask();
    const id = setInterval(() => void ask(), 5000);
    return () => {
      stopped = true;
      clearInterval(id);
    };
  }, [projectId]);
  const left = eta === null ? null : Math.max(0, eta.seconds - (Date.now() - eta.at) / 1000);
  // `elapsed` ticks every second, which re-renders this countdown.
  void elapsed;

  if (finished) return null;

  const current = stage ? PIPELINE_ORDER.indexOf(stage) : -1;

  return (
    <Slab>
      <div className="flex flex-col gap-6 p-5 sm:p-6">
        <div className="flex items-start justify-between gap-4">
          <div className="flex items-start gap-3.5">
            <span className="grid h-10 w-10 shrink-0 place-items-center rounded-[var(--radius-md)] bg-accent-soft text-accent">
              <Spinner size={18} />
            </span>
            <div>
              <h2 className="text-[17px] font-semibold">{copy?.title ?? 'Getting started'}</h2>
              <p className="mt-0.5 text-sm text-fg-secondary">
                {copy?.detail ?? 'Your recording is queued and will begin shortly.'}
              </p>
            </div>
          </div>
          <Badge tone="accent" dot="pulse">
            {event?.status === 'running' ? 'Processing' : (event?.status ?? 'Queued')}
          </Badge>
        </div>

        <div className="flex flex-col gap-2">
          <Progress value={pct} label="Processing progress" showValue={false} />
          <div className="flex items-center justify-between font-mono text-[12px] text-fg-muted">
            <span className="tabular">{pct}% complete</span>
            <span
              className="tabular"
              title={
                eta ? `Learned from ${String(eta.jobs)} recent jobs on this server` : undefined
              }
            >
              {left === null
                ? 'estimating…'
                : left < 5
                  ? 'finishing up…'
                  : `about ${formatDuration(left)} left`}
            </span>
          </div>
        </div>

        {/* The stage list is the honest version of a progress bar: it shows what
            has actually been finished rather than a number that only goes up.
            Only the stages the deployed pipeline runs, in the order it runs them. */}
        <ol className="grid gap-2 sm:grid-cols-5">
          {PIPELINE_ORDER.map((id, mine) => {
            const entry = STAGE_COPY[id];
            const state =
              current < 0
                ? 'pending'
                : mine < current
                  ? 'done'
                  : mine === current
                    ? 'active'
                    : 'pending';
            return (
              <li
                key={id}
                className={cx(
                  'flex items-center gap-2 rounded-[var(--radius-sm)] border px-3 py-2 text-[13px]',
                  state === 'active' && 'border-accent bg-accent-soft font-medium text-fg-primary',
                  state === 'done' && 'border-border-subtle bg-bg-surface text-fg-secondary',
                  state === 'pending' && 'border-border-subtle bg-bg-subtle text-fg-muted',
                )}
              >
                <span aria-hidden className="shrink-0">
                  {state === 'done' ? (
                    <IconCheck size={14} className="text-success" />
                  ) : state === 'active' ? (
                    <span className="block h-2 w-2 rounded-full bg-accent ticker" />
                  ) : (
                    <span className="block h-2 w-2 rounded-full bg-border-default" />
                  )}
                </span>
                <span className="truncate">{entry?.title ?? id}</span>
                <span className="sr-only">
                  {state === 'done' ? 'complete' : state === 'active' ? 'in progress' : 'waiting'}
                </span>
                {state === 'active' && speakerCount != null && id === 'S5_extract' && (
                  <span className="ml-auto flex items-center gap-1">
                    {Array.from({ length: Math.min(speakerCount, 4) }, (_, i) => (
                      <SpeakerDot key={i} index={i + 1} size={7} />
                    ))}
                  </span>
                )}
              </li>
            );
          })}
        </ol>

        <p className="text-[13px] text-fg-muted">
          You can close this tab. Processing continues, and the page picks up where it left off.
        </p>
      </div>
    </Slab>
  );
}

/** The stages the deployed pipeline runs, in the order it runs them
 *  (services/worker-gpu real_pipeline: S0 -> S5 -> S3 -> S7 -> S9). */
const PIPELINE_ORDER = ['S0_ingest', 'S5_extract', 'S3_fuse', 'S7_transcribe', 'S9_package'];
