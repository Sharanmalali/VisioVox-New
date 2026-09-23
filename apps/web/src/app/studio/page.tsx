'use client';

/**
 * The console. Everything after sign-in happens here.
 *
 * INGEST, PREVIEW and VAULT are faces of one machine, so they are tabs over
 * shared state rather than routes: a job started on one face is still running
 * when you look at another, and a route change would throw that away.
 *
 * Nothing on this screen is theatre. The chunk counter is the real part count
 * from the resumable uploader, the telemetry lines are appended from real
 * upload and job events, and the estimate is the server's, not a guess made
 * here. When the machine does not know how long something will take, it says
 * so rather than inventing a bar that creeps.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { ConsoleChrome, Module, type Tab } from '@/components/console/ConsoleChrome';
import {
  TelemetryLog,
  type LogLine,
  type PhaseId,
  type PhaseState,
} from '@/components/console/TelemetryLog';
import { Alert, Badge, Key, Progress, cx } from '@/components/ui';
import { signOutEverywhere } from '@/lib/firebase';
import { accessToken, api, useSession } from '@/lib/store';
import {
  formatBytes,
  formatDuration,
  inspectLocally,
  startUpload,
  type UploadHandle,
  type UploadProgress,
} from '@/lib/upload/resumable';
import type { LimitsResponse } from '@visiovox/ts-client';

import { useRouter } from 'next/navigation';

/** The part size the uploader actually uses; shown so the number is not a claim. */
const PART_MIB = 16;

interface JobEvent {
  status: string;
  progress: number;
  stage: string | null;
  speakers?: number | null;
}

const TERMINAL = new Set(['succeeded', 'partial', 'failed', 'cancelled']);

export default function StudioPage() {
  const router = useRouter();
  const signedIn = useSession((s) => s.signedIn);
  const ready = useSession((s) => s.ready);
  const email = useSession((s) => s.email);
  const hydrate = useSession((s) => s.hydrate);

  const [tab, setTab] = useState<Tab>('ingest');
  const [limits, setLimits] = useState<LimitsResponse | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [inspected, setInspected] = useState<{
    durationSeconds: number | null;
    width: number | null;
    height: number | null;
  } | null>(null);
  const [dragging, setDragging] = useState(false);
  const [progress, setProgress] = useState<UploadProgress | null>(null);
  const [projectId, setProjectId] = useState<string | null>(null);
  const [job, setJob] = useState<JobEvent | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lines, setLines] = useState<LogLine[]>([]);

  const handleRef = useRef<UploadHandle | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const lineId = useRef(0);
  const lastPart = useRef(-1);
  const lastStage = useRef<string | null>(null);

  const log = useCallback((text: string, tone?: LogLine['tone']) => {
    lineId.current += 1;
    const entry: LogLine =
      tone === undefined ? { id: lineId.current, text } : { id: lineId.current, text, tone };
    // Bounded: a long job would otherwise grow this array without limit.
    setLines((prev) => [...prev, entry].slice(-200));
  }, []);

  useEffect(() => {
    hydrate();
  }, [hydrate]);

  useEffect(() => {
    if (ready && !signedIn) {
      router.replace('/login');
    }
  }, [ready, signedIn, router]);

  // What the server will accept right now, computed from live headroom rather
  // than a constant — the whole point of D2.
  useEffect(() => {
    if (!signedIn) return;
    api()
      .getLimits()
      .then(setLimits)
      .catch(() => {
        // Not fatal: the upload is still validated server side.
      });
  }, [signedIn]);

  // ------------------------------------------------------------- job feed --
  useEffect(() => {
    if (projectId === null) return;
    const token = accessToken();
    if (token === null) return;

    const base = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';
    let closed = false;
    // EventSource cannot set an Authorization header, so the token rides as a
    // query parameter on a URL that is never logged with its query string.
    const source = new EventSource(
      `${base}/v1/projects/${projectId}/events?access_token=${encodeURIComponent(token)}`,
    );

    const onEvent = (e: Event) => {
      const data = JSON.parse((e as MessageEvent<string>).data) as JobEvent;
      setJob(data);
      if (data.stage !== null && data.stage !== lastStage.current) {
        lastStage.current = data.stage;
        log(data.stage.toUpperCase().replace(/[\s-]+/g, '_'));
      }
      if (TERMINAL.has(data.status)) {
        closed = true;
        source.close();
        if (data.status === 'succeeded' || data.status === 'partial') {
          log(`SEPARATION_COMPLETE · ${String(data.speakers ?? 0)}_SPEAKERS`, 'good');
          setTab('preview');
        } else {
          log(`JOB_${data.status.toUpperCase()}`, 'bad');
        }
      }
    };

    source.addEventListener('progress', onEvent);
    source.addEventListener('done', onEvent);
    source.addEventListener('error', () => {
      if (!closed) source.close();
    });

    return () => {
      closed = true;
      source.close();
    };
  }, [projectId, log]);

  // ---------------------------------------------------------------- pick --
  function choose(picked: File) {
    setFile(picked);
    setError(null);
    setInspected(null);
    log(`FILE_SELECTED · ${picked.name} · ${formatBytes(picked.size)}`);
    void inspectLocally(picked).then((meta) => {
      setInspected(meta);
      if (meta.durationSeconds !== null) {
        log(`LOCAL_PROBE · ${formatDuration(meta.durationSeconds)}`);
      }
    });
  }

  async function begin() {
    if (file === null) return;
    setError(null);
    lastPart.current = -1;
    try {
      const project = await api().createProject(file.name);
      setProjectId(project.id);
      log(`PROJECT_OPENED · ${project.id}`);
      handleRef.current = await startUpload({
        client: api(),
        projectId: project.id,
        file,
        onProgress: (p) => {
          setProgress(p);
          // One line per part that actually landed — not per render.
          if (p.partsDone > lastPart.current) {
            lastPart.current = p.partsDone;
            if (p.partsDone > 0) {
              log(`CHUNK ${String(p.partsDone)}/${String(p.partsTotal)} → TEMPORARY_CACHE`);
            }
          }
          if (p.phase === 'finalising') log('ASSEMBLING_PARTS_SERVER_SIDE');
          if (p.phase === 'done') log('UPLOAD_COMPLETE · QUEUED_FOR_INFERENCE', 'good');
          if (p.phase === 'error') {
            setError(p.error ?? 'Upload failed');
            log(`UPLOAD_FAILED · ${p.error ?? 'unknown'}`, 'bad');
          }
        },
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Could not start the upload';
      setError(message);
      log(`ERROR · ${message}`, 'bad');
    }
  }

  function reset() {
    void handleRef.current?.abort();
    handleRef.current = null;
    setFile(null);
    setInspected(null);
    setProgress(null);
    setProjectId(null);
    setJob(null);
    setLines([]);
    lastStage.current = null;
    lastPart.current = -1;
  }

  // -------------------------------------------------------------- derived --
  const uploading = progress !== null && !['done', 'error', 'aborted'].includes(progress.phase);
  const uploaded = progress?.phase === 'done';
  const jobDone = job !== null && TERMINAL.has(job.status);

  const phases: Record<PhaseId, PhaseState> = {
    slice: uploading ? 'active' : uploaded ? 'done' : 'idle',
    infer: jobDone ? 'done' : job !== null ? 'active' : 'idle',
    pack: jobDone && (job.status === 'succeeded' || job.status === 'partial') ? 'done' : 'idle',
  };

  const maxBytes = limits?.max_upload_bytes ?? null;
  const tooBig = file !== null && maxBytes !== null && file.size > maxBytes;

  const status =
    error !== null
      ? 'Error'
      : jobDone
        ? job.status === 'succeeded' || job.status === 'partial'
          ? 'Ready'
          : 'Failed'
        : job !== null
          ? `Processing · ${String(Math.round(job.progress * 100))}%`
          : uploading
            ? `Uploading · ${String(Math.round(progress.fraction * 100))}%`
            : 'Idle';

  if (!ready) {
    return (
      <div className="grid min-h-[60vh] place-items-center">
        <p className="etch">Checking your session…</p>
      </div>
    );
  }

  return (
    <ConsoleChrome
      tab={tab}
      onTab={setTab}
      email={email}
      status={status}
      onSignOut={() => {
        void signOutEverywhere().then(() => {
          router.replace('/login');
        });
      }}
    >
      {tab === 'ingest' && (
        <div className="grid gap-5 lg:grid-cols-[2fr_1fr]">
          <Module
            label="Ingestion node"
            tone="bone"
            right={`${String(PART_MIB)} MB / chunk`}
            bodyClassName="p-5"
          >
            {file === null ? (
              <div
                onDragOver={(e) => {
                  e.preventDefault();
                  setDragging(true);
                }}
                onDragLeave={() => {
                  setDragging(false);
                }}
                onDrop={(e) => {
                  e.preventDefault();
                  setDragging(false);
                  const dropped = e.dataTransfer.files[0];
                  if (dropped) choose(dropped);
                }}
                className={cx(
                  'flex flex-col items-center justify-center gap-4 border-2 border-dashed px-6 py-16 text-center transition-colors',
                  dragging ? 'border-acid bg-acid/5' : 'border-dust/40',
                )}
              >
                <p className="display text-2xl text-chalk">Drop a recording</p>
                <p className="font-mono text-[12px] text-dust">
                  {maxBytes === null
                    ? 'Checking available space…'
                    : `Up to ${formatBytes(maxBytes)}`}{' '}
                  · sliced in the browser, streamed direct to storage
                </p>
                <Key
                  tone="bone"
                  onClick={() => {
                    inputRef.current?.click();
                  }}
                >
                  Choose file
                </Key>
                <input
                  ref={inputRef}
                  type="file"
                  accept="video/*,audio/*"
                  className="sr-only"
                  onChange={(e) => {
                    const picked = e.target.files?.[0];
                    if (picked) choose(picked);
                  }}
                />
              </div>
            ) : (
              <div className="flex flex-col gap-5">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate font-mono text-sm text-chalk">{file.name}</p>
                    <p className="mt-1 font-mono text-[11px] text-dust">
                      {formatBytes(file.size)}
                      {inspected?.durationSeconds != null &&
                        ` · ${formatDuration(inspected.durationSeconds)}`}
                      {inspected?.width != null &&
                        ` · ${String(inspected.width)}×${String(inspected.height ?? 0)}`}
                    </p>
                  </div>
                  <Badge tone={tooBig ? 'danger' : uploaded ? 'success' : 'neutral'}>
                    {tooBig
                      ? 'Too large'
                      : uploaded
                        ? 'Uploaded'
                        : uploading
                          ? 'Uploading'
                          : 'Ready'}
                  </Badge>
                </div>

                {tooBig && (
                  <Alert tone="danger" title="Larger than this machine can take right now">
                    The limit is {formatBytes(maxBytes)}, computed from free space on the media
                    volume. It rises again when other jobs finish.
                  </Alert>
                )}

                {progress !== null && (
                  <div className="flex flex-col gap-3">
                    <Progress value={progress.fraction * 100} label="Stream to storage" />
                    <div className="flex flex-wrap justify-between gap-3 font-mono text-[11px] text-dust">
                      <span>
                        chunk {String(progress.partsDone)}/{String(progress.partsTotal)}
                      </span>
                      <span>
                        {progress.bytesPerSecond === null
                          ? 'measuring rate…'
                          : `${formatBytes(progress.bytesPerSecond)}/s`}
                      </span>
                      <span>
                        {/* Null until the rate means something, rather than a
                            confident number that is wrong for the first seconds. */}
                        {progress.secondsRemaining === null
                          ? 'estimating…'
                          : `${formatDuration(progress.secondsRemaining)} left`}
                      </span>
                    </div>
                  </div>
                )}

                {job !== null && (
                  <Progress
                    value={job.progress * 100}
                    label={job.stage ?? 'Processing'}
                    tone="success"
                  />
                )}

                {error !== null && <Alert tone="danger">{error}</Alert>}

                <div className="flex flex-wrap gap-3">
                  {progress === null && (
                    <Key tone="crimson" disabled={tooBig} onClick={() => void begin()}>
                      Begin separation
                    </Key>
                  )}
                  {jobDone && projectId !== null && (
                    <Key
                      tone="acid"
                      onClick={() => {
                        setTab('preview');
                      }}
                    >
                      Open preview
                    </Key>
                  )}
                  <Key tone="ghost" onClick={reset}>
                    {uploading ? 'Cancel' : 'Clear'}
                  </Key>
                </div>
              </div>
            )}
          </Module>

          <Module label="Telemetry" tone="crimson" right="▣ −" bodyClassName="p-0">
            <TelemetryLog lines={lines} phases={phases} />
          </Module>
        </div>
      )}

      {tab === 'preview' && (
        <Module label="Preview module" tone="acid">
          {projectId === null ? (
            <p className="font-mono text-[12px] text-dust">
              Nothing loaded. Ingest a recording, or open one from the vault.
            </p>
          ) : (
            <div className="flex flex-col gap-4">
              <p className="font-mono text-[12px] text-dust">
                {jobDone ? 'Separation complete.' : 'Still processing — this updates as it runs.'}
              </p>
              <Key
                tone="acid"
                onClick={() => {
                  router.push(`/projects/${projectId}`);
                }}
              >
                Open the player
              </Key>
            </div>
          )}
        </Module>
      )}

      {tab === 'vault' && (
        <Module label="Vault" tone="bone">
          <div className="flex flex-col gap-4">
            <p className="font-mono text-[12px] text-dust">
              Everything you have separated, with its retention clock.
            </p>
            <Key
              tone="bone"
              onClick={() => {
                router.push('/projects');
              }}
            >
              Open the vault
            </Key>
          </div>
        </Module>
      )}
    </ConsoleChrome>
  );
}
