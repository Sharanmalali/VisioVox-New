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
import { TelemetryLog, type LogLine } from '@/components/console/TelemetryLog';
import { Player } from '@/components/Player';
import { Alert, Badge, Key, Progress, cx } from '@/components/ui';
import { signOutEverywhere } from '@/lib/firebase';
import { STAGE_MESSAGE, formatElapsed, journey } from '@/lib/journey';
import type { Manifest } from '@/lib/playback/manifest';
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
const SUCCEEDED = new Set(['succeeded', 'partial']);

/** How often to ask the API directly, in case the live stream is not arriving. */
const POLL_MS = 5000;

/** Upload milestones worth a line in the feed; every part would be noise on a big file. */
const UPLOAD_MILESTONES = [25, 50, 75];

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
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lines, setLines] = useState<LogLine[]>([]);

  const handleRef = useRef<UploadHandle | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const lineId = useRef(0);
  const lastMilestone = useRef(0);
  const lastStage = useRef<string | null>(null);
  const startedAt = useRef<number | null>(null);
  // The job this page has already announced as finished, so the stream and the
  // poll reporting the same ending do not log it (or switch tabs) twice.
  const announced = useRef<string | null>(null);

  const log = useCallback((text: string, tone?: LogLine['tone']) => {
    lineId.current += 1;
    const base = { id: lineId.current, at: Date.now(), text };
    const entry: LogLine = tone === undefined ? base : { ...base, tone };
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
  // The job row is created when the upload completes. Opened before that - as
  // this once was, the moment the project existed - the stream is answered
  // "no job yet", closes, and never comes back: the bar then only moved in
  // the poll's 5 s steps, with no stage names.
  const uploadDone = progress?.phase === 'done';
  useEffect(() => {
    if (projectId === null || !uploadDone) return;
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
        const message = STAGE_MESSAGE[data.stage];
        if (message !== undefined) log(message);
      }
      if (TERMINAL.has(data.status)) {
        closed = true;
        source.close();
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
  }, [projectId, uploadDone, log]);

  // The stream above is the fast path. This is the one that cannot be lost: a
  // proxy that buffers text/event-stream, a dropped connection or a stream
  // that ended before the job did would otherwise leave the page at its last
  // event for good - never reaching `ready`, never offering the preview.
  useEffect(() => {
    if (projectId === null || !uploadDone) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const check = async (): Promise<void> => {
      try {
        const j = await api().getJob(projectId);
        if (stopped) return;
        setJob((prev) => {
          // Never step backwards: the stream may already be further along.
          if (prev !== null && !TERMINAL.has(j.status) && prev.progress > j.progress) return prev;
          return { status: j.status, progress: j.progress, stage: prev?.stage ?? null };
        });
        if (TERMINAL.has(j.status)) return;
      } catch {
        // Transient; the next check will tell.
      }
      if (!stopped) timer = setTimeout(() => void check(), POLL_MS);
    };
    timer = setTimeout(() => void check(), POLL_MS);
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [projectId, uploadDone]);

  // One place that reacts to the job ending, whichever path reported it.
  useEffect(() => {
    if (job === null || projectId === null || !TERMINAL.has(job.status)) return;
    if (announced.current === projectId) return;
    announced.current = projectId;
    const took = startedAt.current !== null ? formatElapsed(Date.now() - startedAt.current) : null;
    if (SUCCEEDED.has(job.status)) {
      const partial = job.status === 'partial';
      const announce = (speakers: number | null): void => {
        const who =
          speakers !== null
            ? `${String(speakers)} speaker${speakers === 1 ? '' : 's'} separated`
            : 'Speakers separated';
        log(`Your video is ready — ${who}${took !== null ? ` in ${took}` : ''}.`, 'good');
        if (partial) {
          log('Some passages could not be fully separated — they are marked in the player.');
        }
        log('Opening the preview…');
        setTab('preview');
      };
      // The stream's final event carries the count; the backup poll does not,
      // so ask the project rather than say something vaguer.
      if (job.speakers != null) {
        announce(job.speakers);
      } else {
        void api()
          .getProject(projectId)
          .then((p) => {
            announce(p.speaker_count ?? null);
          })
          .catch(() => {
            announce(null);
          });
      }
    } else if (job.status === 'cancelled') {
      log('Processing was cancelled.', 'bad');
    } else {
      log(
        "We couldn't separate the speakers in this video. VisioVox needs each speaker's face clearly on screen — please try another recording.",
        'bad',
      );
    }
  }, [job, projectId, log]);

  // The player needs the manifest, whose URLs are signed with an expiry - so
  // it is fetched when there is something to play, not before.
  const succeeded = job !== null && SUCCEEDED.has(job.status);
  useEffect(() => {
    if (projectId === null || !succeeded) return;
    let cancelled = false;
    api()
      .getManifest(projectId)
      .then((body) => {
        if (!cancelled) setManifest(body as Manifest);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Could not load the result');
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, succeeded]);

  // ---------------------------------------------------------------- pick --
  function choose(picked: File) {
    setFile(picked);
    setError(null);
    setInspected(null);
    log(`Selected "${picked.name}" (${formatBytes(picked.size)}).`);
    void inspectLocally(picked).then((meta) => {
      setInspected(meta);
      if (meta.durationSeconds !== null) {
        const size =
          meta.width !== null ? `, ${String(meta.width)}×${String(meta.height ?? 0)}` : '';
        log(`Checked your file: ${formatDuration(meta.durationSeconds)}${size}. Ready to upload.`);
      }
    });
  }

  async function begin() {
    if (file === null) return;
    setError(null);
    lastMilestone.current = 0;
    startedAt.current = Date.now();
    try {
      const project = await api().createProject(file.name);
      setProjectId(project.id);
      log('Uploading your video…');
      handleRef.current = await startUpload({
        client: api(),
        projectId: project.id,
        file,
        onProgress: (p) => {
          setProgress(p);
          const pct = Math.floor(p.fraction * 100);
          const next = UPLOAD_MILESTONES.find((m) => m > lastMilestone.current && pct >= m);
          if (p.phase === 'uploading' && p.partsTotal > 1 && next !== undefined) {
            lastMilestone.current = next;
            log(`Uploading… ${String(next)}% sent.`);
          }
          if (p.phase === 'done') {
            log(
              'Upload successful. Your video has been received and is queued for processing.',
              'good',
            );
          }
          if (p.phase === 'error') {
            setError(p.error ?? 'Upload failed');
            log(
              `The upload didn't finish${p.error ? ` (${p.error})` : ''}. Please check your connection and try again.`,
              'bad',
            );
          }
        },
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Could not start the upload';
      setError(message);
      log(`We couldn't start the upload: ${message}`, 'bad');
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
    setManifest(null);
    setError(null);
    setLines([]);
    lastStage.current = null;
    announced.current = null;
    lastMilestone.current = 0;
    startedAt.current = null;
  }

  // -------------------------------------------------------------- derived --
  const uploading = progress !== null && !['done', 'error', 'aborted'].includes(progress.phase);
  const uploaded = progress?.phase === 'done';
  const jobDone = job !== null && TERMINAL.has(job.status);

  // One reading of where the video is, shared by the bar, the checklist and
  // the status line, so none of them can say something the others do not.
  const trip = journey({
    uploadPhase: progress?.phase ?? null,
    uploadFraction: progress?.fraction ?? 0,
    job,
  });

  const maxBytes = limits?.max_upload_bytes ?? null;
  const tooBig = file !== null && maxBytes !== null && file.size > maxBytes;

  const status =
    error !== null || trip.failed
      ? 'Needs attention'
      : trip.finished
        ? 'Ready'
        : progress !== null
          ? `${trip.label} · ${String(trip.percent)}%`
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
        <div className="grid gap-5 lg:grid-cols-[1fr_1fr]">
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
                  <Badge
                    tone={tooBig || trip.failed ? 'danger' : trip.finished ? 'success' : 'neutral'}
                  >
                    {tooBig
                      ? 'Too large'
                      : trip.failed
                        ? 'Needs attention'
                        : trip.finished
                          ? 'Ready'
                          : uploading
                            ? 'Uploading'
                            : uploaded
                              ? 'Processing'
                              : 'Not started'}
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
                    {/* The whole journey in one bar, named by the same step as the
                        checklist beside it (lib/journey.ts). The upload is its
                        first tenth; the server's percentage fills the rest. */}
                    <Progress
                      value={trip.percent}
                      label={
                        trip.failed
                          ? `Stopped at: ${trip.current.title}`
                          : trip.finished
                            ? 'Ready to watch'
                            : trip.label
                      }
                      tone={trip.failed ? 'warning' : 'success'}
                    />
                    {uploading && (
                      <div className="flex flex-wrap justify-between gap-3 font-mono text-[11px] text-dust">
                        <span>
                          {formatBytes(progress.fraction * file.size)} of {formatBytes(file.size)}
                        </span>
                        <span>
                          {progress.bytesPerSecond === null
                            ? 'measuring speed…'
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
                    )}
                  </div>
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

          <Module label="Activity" tone="crimson" right="live" bodyClassName="p-0">
            <TelemetryLog lines={lines} steps={trip.states} />
          </Module>
        </div>
      )}

      {tab === 'preview' && (
        <Module label="Preview module" tone="acid">
          {projectId === null ? (
            <p className="font-mono text-[12px] text-dust">
              Nothing loaded. Ingest a recording, or open one from the vault.
            </p>
          ) : !jobDone ? (
            // Still going: say where it is, and that the player lands here.
            <div className="flex flex-col gap-4">
              <p className="font-mono text-[12px] text-dust">
                Still separating. The player appears here as soon as it is ready - no need to leave
                this page.
              </p>
              <Progress value={trip.percent} label={trip.label} tone="success" />
            </div>
          ) : succeeded ? (
            <div className="flex flex-col gap-4">
              {manifest !== null ? (
                <Player manifest={manifest} />
              ) : error !== null ? (
                <Alert tone="danger" title="The result could not be loaded">
                  {error}
                </Alert>
              ) : (
                <p className="font-mono text-[12px] text-dust">Loading the player…</p>
              )}
              <Key
                tone="acid"
                onClick={() => {
                  router.push(`/projects/${projectId}`);
                }}
              >
                Downloads and sharing
              </Key>
            </div>
          ) : (
            <Alert tone="danger" title="This recording could not be separated">
              The job ended {job.status}. Open it from the vault for the stage that stopped it.
              VisioVox needs the speakers&apos; faces on screen to tell their voices apart.
            </Alert>
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
