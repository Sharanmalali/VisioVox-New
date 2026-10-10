'use client';

/**
 * An imported recording: the download's progress, then the part to process.
 *
 * Shown while the project waits (status `pending`). The whole video plays
 * here; two handles - or "start here" / "end here" at the playhead - pick the
 * part, which can be played on its own before committing, since processing
 * takes minutes and a wrong start is only noticed afterwards.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, type ImportResponse } from '@visiovox/ts-client';

import { Alert, Button, Progress, Slab, SlabHead, Spinner } from '@/components/ui';
import { api } from '@/lib/store';

const POLL_MS = 2000;
const MIN_PART_MS = 5000;

function clock(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = String(s % 60).padStart(2, '0');
  return h > 0 ? `${String(h)}:${String(m).padStart(2, '0')}:${sec}` : `${String(m)}:${sec}`;
}

export function ImportPanel({
  projectId,
  maxSeconds,
  onStarted,
  onStatus,
}: {
  projectId: string;
  maxSeconds: number | null;
  onStarted: () => void;
  /** Each status as it is seen, so the page header can say what is happening. */
  onStatus?: (status: ImportResponse['status']) => void;
}) {
  const [state, setState] = useState<ImportResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  // A project waiting for an ordinary upload is not an import: show nothing.
  const [notImport, setNotImport] = useState(false);

  const refresh = useCallback(async () => {
    const body = await api().getImport(projectId);
    setState(body);
    onStatus?.(body.status);
    return body;
  }, [projectId, onStatus]);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const tick = async () => {
      try {
        const body = await refresh();
        if (!cancelled && (body.status === 'queued' || body.status === 'downloading')) {
          timer = setTimeout(() => void tick(), POLL_MS);
        }
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) setNotImport(true);
        else setError(err instanceof Error ? err.message : 'Could not load the import');
      }
    };
    void tick();
    return () => {
      cancelled = true;
      if (timer !== null) clearTimeout(timer);
    };
  }, [refresh]);

  if (notImport) return null;
  if (error !== null) return <Alert tone="danger">{error}</Alert>;
  if (state === null) return null;

  if (state.status === 'failed') {
    return (
      <Alert tone="danger" title="The video could not be imported">
        {state.error ?? 'The download failed.'}
      </Alert>
    );
  }

  if (state.status !== 'ready' || state.preview_url == null || state.duration_ms == null) {
    return (
      <Slab>
        <SlabHead
          label={state.status === 'queued' ? 'Waiting to download' : 'Downloading the video'}
          description={state.url}
        />
        <div className="flex flex-col gap-3 p-5 sm:p-6">
          <Progress value={state.progress} label="Download progress" />
          <p className="inline-flex items-center gap-2 text-[13.5px] text-fg-secondary">
            <Spinner size={14} />
            {state.status === 'queued'
              ? 'Queued behind other work on the server.'
              : `${String(state.progress)}% - you can choose the part to process once it is here.`}
          </p>
        </div>
      </Slab>
    );
  }

  return (
    <Trimmer
      projectId={projectId}
      src={state.preview_url}
      durationMs={state.duration_ms}
      sourceTitle={state.source_title ?? null}
      maxSeconds={maxSeconds}
      onStarted={onStarted}
    />
  );
}

function Trimmer({
  projectId,
  src,
  durationMs,
  sourceTitle,
  maxSeconds,
  onStarted,
}: {
  projectId: string;
  src: string;
  durationMs: number;
  sourceTitle: string | null;
  maxSeconds: number | null;
  onStarted: () => void;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const [start, setStart] = useState(0);
  const [end, setEnd] = useState(durationMs);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const stopAt = useRef<number | null>(null);

  const length = end - start;
  const maxMs = maxSeconds !== null ? maxSeconds * 1000 : Infinity;
  const problem =
    length < MIN_PART_MS
      ? 'Choose at least 5 seconds.'
      : length > maxMs
        ? `Choose at most ${String(Math.round(maxMs / 60000))} minutes.`
        : null;

  const now = () => Math.round((video.current?.currentTime ?? 0) * 1000);

  const playPart = () => {
    const v = video.current;
    if (v === null) return;
    v.currentTime = start / 1000;
    stopAt.current = end;
    void v.play();
  };

  const go = async (whole: boolean) => {
    setBusy(true);
    setError(null);
    try {
      await api().processPart(projectId, whole ? {} : { start_ms: start, end_ms: end });
      onStarted();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not start processing');
      setBusy(false);
    }
  };

  return (
    <Slab>
      <SlabHead
        label="Choose the part to process"
        description={
          sourceTitle !== null
            ? `Downloaded: ${sourceTitle} (${clock(durationMs)})`
            : `Downloaded (${clock(durationMs)})`
        }
      />
      <div className="flex flex-col gap-5 p-5 sm:p-6">
        <video
          ref={video}
          src={src}
          controls
          playsInline
          preload="metadata"
          className="aspect-video w-full rounded-[var(--radius-md)] bg-black"
          onTimeUpdate={() => {
            const v = video.current;
            if (v !== null && stopAt.current !== null && v.currentTime * 1000 >= stopAt.current) {
              v.pause();
              stopAt.current = null;
            }
          }}
        />

        <div className="trim" aria-label="Part to process">
          <div
            className="trim-band"
            style={{
              left: `${String((start / durationMs) * 100)}%`,
              width: `${String((length / durationMs) * 100)}%`,
            }}
          />
          <input
            type="range"
            aria-label="Start of the part"
            min={0}
            max={durationMs}
            step={100}
            value={start}
            onChange={(e) => {
              const v = Math.min(Number(e.target.value), end - MIN_PART_MS);
              setStart(Math.max(0, v));
              if (video.current !== null) video.current.currentTime = Math.max(0, v) / 1000;
            }}
          />
          <input
            type="range"
            aria-label="End of the part"
            min={0}
            max={durationMs}
            step={100}
            value={end}
            onChange={(e) => {
              const v = Math.max(Number(e.target.value), start + MIN_PART_MS);
              setEnd(Math.min(durationMs, v));
              if (video.current !== null)
                video.current.currentTime = Math.min(durationMs, v) / 1000;
            }}
          />
        </div>

        <div className="flex flex-wrap items-center gap-2 text-[13.5px]">
          <Button
            variant="secondary"
            size="sm"
            onClick={() => {
              setStart(Math.min(now(), end - MIN_PART_MS));
            }}
          >
            Start here
          </Button>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => {
              setEnd(Math.max(now(), start + MIN_PART_MS));
            }}
          >
            End here
          </Button>
          <Button variant="secondary" size="sm" onClick={playPart}>
            Play this part
          </Button>
          <span className="ml-auto font-mono tabular-nums text-fg-secondary">
            {clock(start)} - {clock(end)} · {clock(length)}
          </span>
        </div>

        {problem !== null && <p className="text-[13.5px] text-danger">{problem}</p>}
        {error !== null && <Alert tone="danger">{error}</Alert>}

        <div className="flex flex-wrap items-center gap-3 border-t border-border-subtle pt-5">
          <Button
            size="lg"
            disabled={busy || problem !== null}
            onClick={() => {
              void go(start === 0 && end === durationMs);
            }}
          >
            {start === 0 && end === durationMs ? 'Process the whole video' : 'Process this part'}
          </Button>
          {(start !== 0 || end !== durationMs) && (
            <Button
              variant="secondary"
              disabled={busy || durationMs > maxMs}
              onClick={() => {
                setStart(0);
                setEnd(durationMs);
              }}
            >
              Reset to the whole video
            </Button>
          )}
          <span className="text-[13px] text-fg-muted">
            Only the chosen part is separated and transcribed.
          </span>
        </div>
      </div>
    </Slab>
  );
}
