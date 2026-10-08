'use client';

/**
 * Studio. Everything after sign-in starts here.
 *
 * Upload and Preview are faces of one job, so they are tabs over shared state
 * rather than routes: a job started on one is still running when you look at
 * the other, and a route change would throw that away.
 *
 * Nothing on this screen is theatre. The byte counts come from the resumable
 * uploader, the activity lines are appended from real upload and job events,
 * and the estimate is measured, not invented. When the machine does not know
 * how long something will take, it says so.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { TelemetryLog, type LogLine } from '@/components/console/TelemetryLog';
import {
  IconCloudUpload,
  IconFileVideo,
  IconFolder,
  IconPlay,
  IconShare,
  IconSparkles,
  IconUpload,
} from '@/components/icons';
import { LanguageSelect } from '@/components/LanguageSelect';
import { Player } from '@/components/Player';
import { AppPage, AppShell, PageHeader } from '@/components/shell/AppShell';
import {
  Alert,
  Badge,
  ButtonLink,
  EmptyState,
  Key,
  Progress,
  Skeleton,
  Slab,
  SlabHead,
  Tabs,
  cx,
} from '@/components/ui';
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

type Tab = 'ingest' | 'preview';

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
  const hydrate = useSession((s) => s.hydrate);

  const [tab, setTab] = useState<Tab>('ingest');
  const [limits, setLimits] = useState<LimitsResponse | null>(null);
  const [language, setLanguage] = useState<string | null>(null);
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
  // than a constant.
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
  // The job row is created when the upload completes. Opened before that, the
  // stream is answered "no job yet", closes, and never comes back.
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
  // that ended before the job did would otherwise leave the page stuck.
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
      const project = await api().createProject(file.name, language);
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
  // the status badge, so none of them can say something the others do not.
  const trip = journey({
    uploadPhase: progress?.phase ?? null,
    uploadFraction: progress?.fraction ?? 0,
    job,
  });

  const maxBytes = limits?.max_upload_bytes ?? null;
  const tooBig = file !== null && maxBytes !== null && file.size > maxBytes;

  const status: {
    label: string;
    tone: 'neutral' | 'accent' | 'success' | 'danger';
    live: boolean;
  } =
    error !== null || trip.failed
      ? { label: 'Needs attention', tone: 'danger', live: false }
      : trip.finished
        ? { label: 'Ready', tone: 'success', live: false }
        : progress !== null
          ? { label: `${trip.label} · ${String(trip.percent)}%`, tone: 'accent', live: true }
          : { label: 'Idle', tone: 'neutral', live: false };

  if (!ready) {
    return (
      <AppShell>
        <AppPage>
          <Skeleton className="h-10 w-64" />
          <div className="grid gap-6 lg:grid-cols-[1.3fr_1fr]">
            <Skeleton className="h-80" />
            <Skeleton className="h-80" />
          </div>
        </AppPage>
      </AppShell>
    );
  }

  const fileState = tooBig
    ? { label: 'Too large', tone: 'danger' as const }
    : trip.failed
      ? { label: 'Needs attention', tone: 'danger' as const }
      : trip.finished
        ? { label: 'Ready', tone: 'success' as const }
        : uploading
          ? { label: 'Uploading', tone: 'accent' as const }
          : uploaded
            ? { label: 'Processing', tone: 'accent' as const }
            : { label: 'Not started', tone: 'neutral' as const };

  return (
    <AppShell>
      <AppPage>
        <PageHeader
          title="Studio"
          description="Upload a recording and watch it get separated, live. Your video lands in Preview the moment it is ready."
          actions={
            <Badge tone={status.tone} dot={status.live ? 'pulse' : true}>
              {status.label}
            </Badge>
          }
        />

        <div className="flex flex-wrap items-center justify-between gap-3">
          <Tabs<Tab>
            label="Studio"
            value={tab}
            onChange={setTab}
            items={[
              { id: 'ingest', label: 'Upload', icon: <IconUpload size={15} /> },
              {
                id: 'preview',
                label: 'Preview',
                icon: <IconPlay size={14} />,
                badge:
                  succeeded && tab !== 'preview' ? (
                    <span className="h-1.5 w-1.5 rounded-full bg-success" aria-label="ready" />
                  ) : undefined,
              },
            ]}
          />
          <ButtonLink href="/projects" variant="ghost" size="sm">
            <IconFolder size={16} />
            All projects
          </ButtonLink>
        </div>

        {tab === 'ingest' && (
          <div className="grid items-start gap-6 lg:grid-cols-[1.3fr_1fr]">
            <Slab>
              <SlabHead
                icon={<IconCloudUpload size={17} />}
                label="New recording"
                description="Video with the speakers’ faces on screen works best."
              />
              <div className="p-5 sm:p-6">
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
                      'flex flex-col items-center justify-center gap-4 rounded-[var(--radius-lg)] border-2 border-dashed px-6 py-16 text-center transition-[border-color,background-color] duration-200',
                      dragging
                        ? 'border-accent bg-accent-soft'
                        : 'border-border-default bg-bg-subtle hover:border-border-strong',
                    )}
                  >
                    <span className="grid h-14 w-14 place-items-center rounded-[var(--radius-md)] border border-border-subtle bg-bg-surface text-accent shadow-sm">
                      <IconCloudUpload size={26} />
                    </span>
                    <div className="flex flex-col gap-1">
                      <p className="text-lg font-semibold">Drop a recording here</p>
                      <p className="text-sm text-fg-muted">
                        {maxBytes === null
                          ? 'Checking available space…'
                          : `Up to ${formatBytes(maxBytes)} · up to ${String(Math.round((limits?.max_duration_seconds ?? 3600) / 60))} minutes`}
                      </p>
                    </div>
                    <Key
                      tone="secondary"
                      onClick={() => {
                        inputRef.current?.click();
                      }}
                    >
                      Browse files
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
                  <div className="flex flex-col gap-6">
                    <div className="flex items-start gap-4 rounded-[var(--radius-md)] border border-border-subtle bg-bg-subtle p-4">
                      <span className="grid h-11 w-11 shrink-0 place-items-center rounded-[var(--radius-sm)] bg-bg-surface text-fg-secondary shadow-sm ring-1 ring-border-subtle">
                        <IconFileVideo size={20} />
                      </span>
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-[15px] font-medium">{file.name}</p>
                        <p className="mt-0.5 font-mono text-[12px] text-fg-muted">
                          {formatBytes(file.size)}
                          {inspected?.durationSeconds != null &&
                            ` · ${formatDuration(inspected.durationSeconds)}`}
                          {inspected?.width != null &&
                            ` · ${String(inspected.width)}×${String(inspected.height ?? 0)}`}
                        </p>
                      </div>
                      <Badge
                        tone={fileState.tone}
                        dot={fileState.tone === 'accent' ? 'pulse' : true}
                      >
                        {fileState.label}
                      </Badge>
                    </div>

                    {tooBig && (
                      <Alert tone="danger" title="Larger than we can take right now">
                        The limit is {formatBytes(maxBytes)}, computed from free space on our media
                        volume. It rises again when other jobs finish.
                      </Alert>
                    )}

                    {progress !== null && (
                      <div className="flex flex-col gap-3">
                        {/* The whole journey in one bar, named by the same step as
                            the checklist beside it (lib/journey.ts). */}
                        <Progress
                          value={trip.percent}
                          label={
                            trip.failed
                              ? `Stopped at: ${trip.current.title}`
                              : trip.finished
                                ? 'Ready to watch'
                                : trip.label
                          }
                          tone={trip.failed ? 'warning' : trip.finished ? 'success' : 'accent'}
                        />
                        {uploading && (
                          <div className="flex flex-wrap justify-between gap-3 font-mono text-[12px] text-fg-muted">
                            <span>
                              {formatBytes(progress.fraction * file.size)} of{' '}
                              {formatBytes(file.size)}
                            </span>
                            <span>
                              {progress.bytesPerSecond === null
                                ? 'measuring speed…'
                                : `${formatBytes(progress.bytesPerSecond)}/s`}
                            </span>
                            <span>
                              {progress.secondsRemaining === null
                                ? 'estimating…'
                                : `${formatDuration(progress.secondsRemaining)} left`}
                            </span>
                          </div>
                        )}
                      </div>
                    )}

                    {error !== null && <Alert tone="danger">{error}</Alert>}

                    {progress === null && (
                      <LanguageSelect
                        languages={limits?.caption_languages}
                        value={language}
                        onChange={setLanguage}
                      />
                    )}

                    <div className="flex flex-wrap gap-3 border-t border-border-subtle pt-5">
                      {progress === null && (
                        <Key
                          tone="primary"
                          size="lg"
                          disabled={tooBig}
                          onClick={() => void begin()}
                        >
                          <IconSparkles size={17} />
                          Begin separation
                        </Key>
                      )}
                      {jobDone && projectId !== null && (
                        <Key
                          tone="primary"
                          size="lg"
                          onClick={() => {
                            setTab('preview');
                          }}
                        >
                          <IconPlay size={15} />
                          Open preview
                        </Key>
                      )}
                      <Key tone="secondary" size="lg" onClick={reset}>
                        {uploading
                          ? 'Cancel upload'
                          : progress !== null
                            ? 'New recording'
                            : 'Clear'}
                      </Key>
                    </div>
                  </div>
                )}
              </div>
            </Slab>

            <Slab>
              <SlabHead
                label="Activity"
                description="What is happening to your video, as it happens."
                right={
                  progress !== null && !trip.finished && !trip.failed ? (
                    <Badge tone="accent" dot="pulse">
                      Live
                    </Badge>
                  ) : undefined
                }
              />
              <TelemetryLog lines={lines} steps={trip.states} />
            </Slab>
          </div>
        )}

        {tab === 'preview' && (
          <div className="flex flex-col gap-6">
            {projectId === null ? (
              <EmptyState
                icon={<IconPlay size={22} />}
                title="Nothing to preview yet"
                action={
                  <div className="flex flex-wrap justify-center gap-3">
                    <Key
                      tone="primary"
                      onClick={() => {
                        setTab('ingest');
                      }}
                    >
                      <IconUpload size={16} />
                      Upload a recording
                    </Key>
                    <ButtonLink href="/projects" variant="secondary">
                      Open a past project
                    </ButtonLink>
                  </div>
                }
              >
                Upload a recording, or open one you have already separated.
              </EmptyState>
            ) : !jobDone ? (
              <Slab>
                <SlabHead
                  label="Still separating"
                  description="The player appears here as soon as it is ready — no need to leave this page."
                />
                <div className="p-6">
                  <Progress value={trip.percent} label={trip.label} />
                </div>
              </Slab>
            ) : succeeded ? (
              <>
                {manifest !== null ? (
                  <Slab className="p-4 sm:p-6">
                    <Player manifest={manifest} />
                  </Slab>
                ) : error !== null ? (
                  <Alert tone="danger" title="The result could not be loaded">
                    {error}
                  </Alert>
                ) : (
                  <div className="flex flex-col gap-4">
                    <Skeleton className="aspect-video w-full" />
                    <Skeleton className="h-24 w-full" />
                  </div>
                )}
                <div className="flex flex-wrap items-center justify-between gap-4 rounded-[var(--radius-lg)] border border-border-subtle bg-bg-surface px-5 py-4 shadow-sm">
                  <div>
                    <p className="text-[15px] font-semibold">Download or share</p>
                    <p className="text-sm text-fg-muted">
                      Export any speaker as video or audio, or send a revocable link.
                    </p>
                  </div>
                  <Key
                    tone="primary"
                    onClick={() => {
                      router.push(`/projects/${projectId}`);
                    }}
                  >
                    <IconShare size={16} />
                    Downloads and sharing
                  </Key>
                </div>
              </>
            ) : (
              <Alert tone="danger" title="This recording could not be separated">
                The job ended {job.status}. Open it from your projects for the stage that stopped
                it. VisioVox needs the speakers&apos; faces on screen to tell their voices apart.
              </Alert>
            )}
          </div>
        )}
      </AppPage>
    </AppShell>
  );
}
