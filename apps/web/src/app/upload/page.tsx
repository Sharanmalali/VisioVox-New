'use client';

/**
 * Upload.
 *
 * The brief: tell the user what they can upload *before* they choose, and what
 * will happen *before* they commit. Both numbers are honest — the limit is
 * fetched live because it moves with free space and other uploads in flight,
 * and the processing estimate is a range labelled as an estimate.
 */

import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useRef, useState } from 'react';

import {
  IconCheckCircle,
  IconClock,
  IconCloudUpload,
  IconFace,
  IconFileVideo,
  IconSparkles,
  IconUsers,
  IconX,
} from '@/components/icons';
import { LanguageSelect } from '@/components/LanguageSelect';
import { ConfirmDialog } from '@/components/ConfirmDialog';
import {
  DRIVE_SCOPE,
  PHOTOS_SCOPE,
  forgetToken,
  googleAccessToken,
  pickFromDrive,
} from '@/lib/google-media';
import { AppPage, AppShell, PageHeader } from '@/components/shell/AppShell';
import {
  Alert,
  Badge,
  Button,
  Checkbox,
  Field,
  Progress,
  Skeleton,
  Slab,
  SlabHead,
  Spinner,
  cx,
} from '@/components/ui';
import {
  formatBytes,
  formatDuration,
  inspectLocally,
  startUpload,
  type UploadHandle,
  type UploadProgress,
} from '@/lib/upload/resumable';
import { api, useSession } from '@/lib/store';
import { ApiError, type GoogleImport, type LimitsResponse } from '@visiovox/ts-client';

interface Inspected {
  durationSeconds: number | null;
  width: number | null;
  height: number | null;
}

/**
 * Processing time on this server, learned from its finished jobs (the API's
 * estimates.py, via /v1/limits): a fixed start-up plus a time per second of
 * video. Median error 16% on 53 past jobs, against 27% for the flat 2.6x
 * realtime rule it replaced.
 */
function estimateProcessing(
  durationSeconds: number | null,
  limits: LimitsResponse | null,
): number | null {
  if (durationSeconds === null) return null;
  const fixed = limits?.processing_fixed_seconds ?? 0;
  const perSecond = limits?.processing_seconds_per_media_second ?? 2.6;
  return fixed + perSecond * durationSeconds;
}

const TIPS = [
  {
    icon: <IconFace size={17} />,
    title: 'Faces on screen',
    body: 'Separation follows faces. Someone who is never visible will not get a track of their own.',
  },
  {
    icon: <IconUsers size={17} />,
    title: 'Up to four speakers',
    body: 'Best with two or three people. At four, quality drops noticeably.',
  },
  {
    icon: <IconClock size={17} />,
    title: 'Close the tab if you like',
    body: 'Uploads resume after a dropped connection, and processing carries on without you.',
  },
];

export default function UploadPage() {
  const router = useRouter();
  const signedIn = useSession((s) => s.signedIn);
  const ready = useSession((s) => s.ready);
  const hydrate = useSession((s) => s.hydrate);

  const [limits, setLimits] = useState<LimitsResponse | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [inspected, setInspected] = useState<Inspected | null>(null);
  const [title, setTitle] = useState('');
  const [language, setLanguage] = useState<string | null>(null);
  const [attested, setAttested] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [progress, setProgress] = useState<UploadProgress | null>(null);
  const [error, setError] = useState<string | null>(null);
  const handleRef = useRef<UploadHandle | null>(null);
  const [link, setLink] = useState('');
  const [importing, setImporting] = useState(false);
  // A video picked in Google Drive or Photos, waiting for the size dialog.
  const [google, setGoogle] = useState<(GoogleImport & { sizeBytes?: number | null }) | null>(null);
  // An open Google Photos picker, polled until the person picks.
  const [photos, setPhotos] = useState<{
    sessionId: string;
    pickerUri: string;
    token: string;
    pollMs: number;
    until: number;
  } | null>(null);
  const [googleBusy, setGoogleBusy] = useState<'drive' | 'photos' | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    hydrate();
  }, [hydrate]);

  useEffect(() => {
    if (ready && !signedIn) router.replace('/login');
  }, [ready, signedIn, router]);

  useEffect(() => {
    if (!signedIn) return;
    void api()
      .getLimits()
      .then(setLimits)
      .catch(() => {
        // A missing limits call should not block the page; the server still
        // enforces the real limit at init.
      });
  }, [signedIn]);

  const choose = useCallback((picked: File) => {
    setFile(picked);
    setError(null);
    setTitle((current) => current || picked.name.replace(/\.[^.]+$/, ''));
    setInspected(null);
    void inspectLocally(picked).then(setInspected);
  }, []);

  function onDrop(event: React.DragEvent) {
    event.preventDefault();
    setDragging(false);
    const dropped = event.dataTransfer.files[0];
    if (dropped) choose(dropped);
  }

  async function begin() {
    if (!file) return;
    setError(null);
    try {
      const project = await api().createProject(title || file.name, language);
      handleRef.current = await startUpload({
        client: api(),
        projectId: project.id,
        file,
        onProgress: (p) => {
          setProgress(p);
          if (p.phase === 'done') router.push(`/projects/${project.id}`);
          if (p.phase === 'error') setError(p.error ?? 'Upload failed');
        },
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not start the upload');
    }
  }

  function say(err: unknown, fallback: string): string {
    if (err instanceof ApiError) return err.detail || fallback;
    return err instanceof Error && err.message ? err.message : fallback;
  }

  async function chooseFromDrive() {
    setError(null);
    setGoogleBusy('drive');
    try {
      const token = await googleAccessToken(DRIVE_SCOPE);
      if (token === null) return;
      const pick = await pickFromDrive(token);
      if (pick === null) return;
      if (pick.mimeType !== '' && !pick.mimeType.startsWith('video/')) {
        setError('That file is not a video. Choose a video in Google Drive.');
        return;
      }
      setGoogle({
        kind: 'drive',
        access_token: token,
        file_id: pick.fileId,
        filename: pick.name,
        mime_type: pick.mimeType || null,
        sizeBytes: pick.sizeBytes,
      });
      setConfirming('google');
    } catch (err) {
      setError(say(err, 'Could not open Google Drive.'));
    } finally {
      setGoogleBusy(null);
    }
  }

  async function chooseFromPhotos() {
    setError(null);
    setGoogleBusy('photos');
    try {
      const token = await googleAccessToken(PHOTOS_SCOPE);
      if (token === null) return;
      const session = await api().startPhotosPick(token);
      setPhotos({
        sessionId: session.session_id,
        // "/autoclose" closes Google's tab once the person has picked.
        pickerUri: `${session.picker_uri.replace(/\/$/, '')}/autoclose`,
        token,
        pollMs: Math.max(2000, session.poll_seconds * 1000),
        until: Date.now() + 20 * 60 * 1000,
      });
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) forgetToken(PHOTOS_SCOPE);
      setError(say(err, 'Could not open Google Photos.'));
    } finally {
      setGoogleBusy(null);
    }
  }

  useEffect(() => {
    if (photos === null) return;
    let stopped = false;
    const timer = setInterval(() => {
      if (Date.now() > photos.until) {
        setPhotos(null);
        setError('Google Photos waited too long for a choice. Try again.');
        return;
      }
      api()
        .pollPhotosPick(photos.sessionId, photos.token)
        .then((state) => {
          if (stopped || !state.done) return;
          setPhotos(null);
          const video = state.items.find((i) => i.is_video && i.base_url);
          if (video === undefined || !video.base_url) {
            setError('That was a photo. Choose a video in Google Photos.');
            return;
          }
          setGoogle({
            kind: 'photos',
            access_token: photos.token,
            base_url: video.base_url,
            session_id: photos.sessionId,
            filename: video.filename ?? 'Google Photos video',
            mime_type: video.mime_type ?? null,
          });
          setConfirming('google');
        })
        .catch((err: unknown) => {
          if (stopped) return;
          setPhotos(null);
          setError(say(err, 'Google Photos stopped responding. Try again.'));
        });
    }, photos.pollMs);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [photos]);

  async function importGoogle() {
    if (google === null) return;
    setError(null);
    setImporting(true);
    try {
      // Only what the server needs; the size was for the dialog.
      const pick: GoogleImport = {
        kind: google.kind,
        access_token: google.access_token,
        file_id: google.file_id ?? null,
        base_url: google.base_url ?? null,
        session_id: google.session_id ?? null,
        filename: google.filename ?? null,
        mime_type: google.mime_type ?? null,
      };
      const started = await api().importFromGoogle(pick, title.trim() || null, language);
      router.push(`/projects/${started.project_id}`);
    } catch (err) {
      setError(say(err, 'Could not import that video'));
      setImporting(false);
    }
  }

  async function importLink() {
    setError(null);
    setImporting(true);
    try {
      const started = await api().importFromLink(link.trim(), title.trim() || null, language);
      router.push(`/projects/${started.project_id}`);
    } catch (err) {
      setError(say(err, 'Could not import that link'));
      setImporting(false);
    }
  }
  const linkLooksRight = /^https?:\/\/([a-z]+\.)?(youtube\.com|youtu\.be|vimeo\.com)\//i.test(
    link.trim(),
  );

  const maxBytes = limits?.max_upload_bytes ?? null;
  const maxSeconds = limits?.max_duration_seconds ?? null;
  const localDuration = inspected?.durationSeconds ?? null;
  const overBy = file !== null && maxBytes !== null && file.size > maxBytes ? maxBytes : null;
  const longBy =
    localDuration !== null && maxSeconds !== null && localDuration > maxSeconds ? maxSeconds : null;
  const tooBig = overBy !== null;
  const tooLong = longBy !== null;
  const busy =
    progress !== null && ['uploading', 'preparing', 'finalising'].includes(progress.phase);
  const estimate = estimateProcessing(localDuration, limits);
  const minutesPerMinute = limits?.processing_seconds_per_media_second ?? 2.6;
  const [confirming, setConfirming] = useState<'upload' | 'import' | 'google' | null>(null);

  if (!ready) {
    return (
      <AppShell>
        <AppPage>
          <Skeleton className="h-10 w-72" />
          <Skeleton className="h-72 w-full" />
        </AppPage>
      </AppShell>
    );
  }

  return (
    <AppShell>
      <ConfirmDialog
        open={confirming !== null}
        title="Bigger videos take longer"
        confirmLabel={confirming === 'upload' ? 'Upload and process' : 'Import video'}
        onCancel={() => {
          setConfirming(null);
          setGoogle(null);
        }}
        onConfirm={() => {
          const what = confirming;
          setConfirming(null);
          if (what === 'upload') void begin();
          if (what === 'import') void importLink();
          if (what === 'google') void importGoogle();
        }}
      >
        {confirming === 'upload' && file !== null ? (
          <>
            <p>
              The larger the file, the longer the <strong>upload</strong> takes - this one is{' '}
              {formatBytes(file.size)}, and how long it takes depends on your connection.
            </p>
            <p>
              The longer the video, the longer the <strong>processing</strong> takes: on this server
              about {formatDuration(minutesPerMinute * 60)} for every minute of video
              {estimate !== null && localDuration !== null
                ? `, so about ${formatDuration(estimate)} for this ${formatDuration(localDuration)} recording`
                : ''}
              .
            </p>
          </>
        ) : confirming === 'google' && google !== null ? (
          <>
            <p>
              <strong>{google.filename}</strong> is copied from your Google{' '}
              {google.kind === 'drive' ? 'Drive' : 'Photos'} to our server first
              {google.sizeBytes != null ? ` (${formatBytes(google.sizeBytes)})` : ''} - the larger
              the video, the longer that takes.
            </p>
            <p>
              Then you choose the part to process. The longer that part, the longer the{' '}
              <strong>processing</strong> takes: about {formatDuration(minutesPerMinute * 60)} for
              every minute on this server.
            </p>
          </>
        ) : (
          <>
            <p>
              The video is <strong>downloaded</strong> to our server first - the longer and sharper
              it is, the longer that takes, and YouTube sometimes slows downloads down.
            </p>
            <p>
              Then you choose the part to process. The longer that part, the longer the{' '}
              <strong>processing</strong> takes: about {formatDuration(minutesPerMinute * 60)} for
              every minute on this server.
            </p>
          </>
        )}
        <p className="text-[13px] text-fg-muted">
          You can close this tab; the work carries on and the project page shows the time left.
        </p>
      </ConfirmDialog>
      <AppPage>
        <PageHeader
          title="Upload a recording"
          description="A meeting, an interview, a panel — anything where people talk over each other."
        />

        <div className="grid items-start gap-6 lg:grid-cols-[1fr_340px]">
          <div className="flex flex-col gap-6">
            {/* ---- drop zone ---- */}
            <div
              onDragOver={(e) => {
                e.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => {
                setDragging(false);
              }}
              onDrop={onDrop}
              className={cx(
                'relative overflow-hidden rounded-[var(--radius-lg)] border-2 border-dashed transition-[border-color,background-color] duration-200',
                file === null ? 'px-6 py-16' : 'p-5',
                dragging
                  ? 'border-accent bg-accent-soft'
                  : 'border-border-default bg-bg-surface hover:border-border-strong',
              )}
            >
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
              {file === null ? (
                <div className="flex flex-col items-center gap-4 text-center">
                  <span className="grid h-14 w-14 place-items-center rounded-[var(--radius-md)] border border-border-subtle bg-bg-subtle text-accent shadow-sm">
                    <IconCloudUpload size={26} />
                  </span>
                  <div className="flex flex-col gap-1">
                    <p className="text-lg font-semibold">Drop a video here</p>
                    <p className="text-sm text-fg-muted">
                      {limits
                        ? `MP4, MOV, WebM and more · up to ${formatBytes(limits.max_upload_bytes)} · up to ${String(Math.round(limits.max_duration_seconds / 60))} minutes`
                        : 'MP4, MOV, WebM and more'}
                    </p>
                  </div>
                  <Button
                    variant="secondary"
                    onClick={() => {
                      inputRef.current?.click();
                    }}
                  >
                    Choose a file
                  </Button>
                </div>
              ) : (
                <div className="flex items-center gap-4">
                  <span className="grid h-12 w-12 shrink-0 place-items-center rounded-[var(--radius-sm)] bg-bg-subtle text-fg-secondary ring-1 ring-border-subtle">
                    <IconFileVideo size={22} />
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
                  {!busy && (
                    <button
                      type="button"
                      aria-label="Choose a different file"
                      title="Choose a different file"
                      onClick={() => {
                        setFile(null);
                        setInspected(null);
                        setProgress(null);
                      }}
                      className="grid h-9 w-9 shrink-0 place-items-center rounded-[var(--radius-sm)] text-fg-muted transition-colors hover:bg-bg-inset hover:text-fg-primary"
                    >
                      <IconX size={17} />
                    </button>
                  )}
                </div>
              )}
            </div>

            {/* ---- or a link ---- */}
            {file === null && (
              <Slab>
                <SlabHead
                  label="Or bring a video from Google or a link"
                  description="Choose a video in your Google Drive or Google Photos, or paste a YouTube or Vimeo link. It is copied to our server; then you choose the part to process."
                />
                <div className="flex flex-col gap-5 p-5 sm:p-6">
                  <div className="flex flex-col gap-2">
                    <div className="grid gap-3 sm:grid-cols-2">
                      <Button
                        variant="secondary"
                        size="lg"
                        disabled={!attested || googleBusy !== null || importing || photos !== null}
                        onClick={() => {
                          void chooseFromDrive();
                        }}
                      >
                        {googleBusy === 'drive' ? <Spinner size={16} /> : <DriveMark />}
                        Google Drive
                      </Button>
                      <Button
                        variant="secondary"
                        size="lg"
                        disabled={!attested || googleBusy !== null || importing || photos !== null}
                        onClick={() => {
                          void chooseFromPhotos();
                        }}
                      >
                        {googleBusy === 'photos' ? <Spinner size={16} /> : <PhotosMark />}
                        Google Photos
                      </Button>
                    </div>
                    {!attested && (
                      <span className="text-[13px] text-fg-muted">
                        Tick the box below first - it applies to every way of bringing a video.
                      </span>
                    )}
                    {photos !== null && (
                      <div className="flex flex-col gap-3 rounded-[var(--radius-md)] border border-accent bg-accent-soft p-4">
                        <p className="inline-flex items-center gap-2 text-[14px] font-medium">
                          <Spinner size={14} />
                          Choose one video in Google Photos
                        </p>
                        <div className="flex flex-wrap gap-3">
                          <a
                            href={photos.pickerUri}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="inline-flex h-9 items-center rounded-[var(--radius-sm)] bg-accent px-4 text-[14px] font-semibold text-fg-on-accent"
                          >
                            Open Google Photos
                          </a>
                          <Button
                            variant="secondary"
                            size="sm"
                            onClick={() => {
                              setPhotos(null);
                            }}
                          >
                            Cancel
                          </Button>
                        </div>
                        <p className="text-[12.5px] text-fg-secondary">
                          It opens in a new tab and closes itself after you pick; this page carries
                          on by itself.
                        </p>
                      </div>
                    )}
                  </div>
                  <Field
                    label="Video link"
                    placeholder="https://www.youtube.com/watch?v=..."
                    value={link}
                    inputMode="url"
                    onChange={(e) => {
                      setLink(e.target.value);
                    }}
                  />
                  <Field
                    label="Project name (optional - the video's title is used otherwise)"
                    value={title}
                    maxLength={200}
                    onChange={(e) => {
                      setTitle(e.target.value);
                    }}
                  />
                  <LanguageSelect
                    languages={limits?.caption_languages}
                    value={language}
                    onChange={setLanguage}
                  />
                  <Checkbox checked={attested} onChange={setAttested}>
                    I have the right to use this video and to process the voices in it - it is mine,
                    its licence allows it, or its owner agreed.
                  </Checkbox>
                  {error !== null && <Alert tone="danger">{error}</Alert>}
                  <div className="flex flex-wrap items-center gap-3 border-t border-border-subtle pt-5">
                    <Button
                      size="lg"
                      disabled={!attested || !linkLooksRight || importing}
                      onClick={() => {
                        setConfirming('import');
                      }}
                    >
                      {importing ? 'Starting…' : 'Import video'}
                    </Button>
                    {link.trim() !== '' && !linkLooksRight && (
                      <span className="text-[13px] text-fg-muted">
                        Only YouTube and Vimeo links can be imported.
                      </span>
                    )}
                  </div>
                </div>
              </Slab>
            )}

            {/* ---- what will happen, before committing ---- */}
            {file && !busy && (
              <Slab>
                <SlabHead
                  label="Before you start"
                  right={
                    tooBig || tooLong ? (
                      <Badge tone="danger" dot>
                        Cannot process
                      </Badge>
                    ) : (
                      <Badge tone="success" dot>
                        Ready
                      </Badge>
                    )
                  }
                />
                <div className="flex flex-col gap-6 p-5 sm:p-6">
                  {overBy !== null && (
                    <Alert tone="danger" title="This file is too large right now">
                      It is {formatBytes(file.size)} and the current limit is {formatBytes(overBy)}.
                      The limit reflects free space and uploads already in progress, so it may rise
                      shortly.
                    </Alert>
                  )}

                  {longBy !== null && localDuration !== null && (
                    <Alert tone="danger" title="This recording is too long">
                      It runs {formatDuration(localDuration)} and the limit is{' '}
                      {Math.round(longBy / 60)} minutes. Processing time scales with length, so
                      please trim it first.
                    </Alert>
                  )}

                  <Field
                    label="Project name"
                    value={title}
                    maxLength={200}
                    onChange={(e) => {
                      setTitle(e.target.value);
                    }}
                  />

                  <LanguageSelect
                    languages={limits?.caption_languages}
                    value={language}
                    onChange={setLanguage}
                  />

                  {!tooBig && !tooLong && (
                    <div className="grid gap-3 rounded-[var(--radius-md)] border border-border-subtle bg-bg-subtle p-4 sm:grid-cols-2">
                      <div>
                        <p className="text-[12px] font-medium text-fg-muted">Upload time</p>
                        <p className="mt-0.5 text-sm font-medium">Depends on your connection</p>
                      </div>
                      <div>
                        <p className="text-[12px] font-medium text-fg-muted">Processing time</p>
                        <p className="mt-0.5 text-sm font-medium tabular">
                          {estimate !== null
                            ? `About ${formatDuration(estimate)}`
                            : 'Estimated once we can read the file'}
                        </p>
                      </div>
                      <p className="text-[12px] leading-relaxed text-fg-muted sm:col-span-2">
                        Learned from {String(limits?.processing_estimate_jobs ?? 0)} recent jobs on
                        this server. More people on screen take longer.
                      </p>
                    </div>
                  )}

                  <Checkbox checked={attested} onChange={setAttested}>
                    I have the right to upload this recording and to process the voices in it.
                  </Checkbox>

                  <div className="flex flex-wrap items-center gap-3 border-t border-border-subtle pt-5">
                    <Button
                      size="lg"
                      disabled={!attested || tooBig || tooLong}
                      onClick={() => {
                        setConfirming('upload');
                      }}
                    >
                      <IconSparkles size={17} />
                      Start processing
                    </Button>
                    {!attested && (
                      <span className="text-[13px] text-fg-muted">
                        Confirm the statement above to continue.
                      </span>
                    )}
                  </div>
                </div>
              </Slab>
            )}

            {/* ---- in flight ---- */}
            {progress && busy && (
              <Slab>
                <SlabHead
                  label={progress.phase === 'finalising' ? 'Finishing up' : 'Uploading'}
                  description="You can close this tab — the upload resumes from where it stopped when you come back."
                  right={
                    <span className="font-mono text-[12px] tabular-nums text-fg-muted">
                      {formatBytes(progress.uploadedBytes)} / {formatBytes(progress.totalBytes)}
                    </span>
                  }
                />
                <div className="flex flex-col gap-4 p-5 sm:p-6">
                  <Progress value={progress.fraction * 100} label="Upload progress" />
                  <div className="flex flex-wrap items-center justify-between gap-2 font-mono text-[12px] text-fg-muted">
                    <span>
                      {progress.bytesPerSecond != null
                        ? `${formatBytes(progress.bytesPerSecond)}/s`
                        : 'measuring speed…'}
                      {progress.secondsRemaining != null &&
                        ` · ${formatDuration(progress.secondsRemaining)} left`}
                    </span>
                    <span>
                      part {progress.partsDone} of {progress.partsTotal}
                    </span>
                  </div>
                  <div className="flex gap-2">
                    {progress.phase === 'paused' ? (
                      <Button
                        size="sm"
                        variant="secondary"
                        onClick={() => {
                          handleRef.current?.resume();
                        }}
                      >
                        Resume
                      </Button>
                    ) : (
                      <Button
                        size="sm"
                        variant="secondary"
                        onClick={() => {
                          handleRef.current?.pause();
                        }}
                      >
                        Pause
                      </Button>
                    )}
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => {
                        void handleRef.current?.abort().then(() => {
                          setProgress(null);
                          setFile(null);
                        });
                      }}
                    >
                      Cancel
                    </Button>
                  </div>
                </div>
              </Slab>
            )}

            {error !== null && (
              <Alert tone="danger" title="Upload problem">
                {error}
              </Alert>
            )}
          </div>

          {/* ---- sidebar ---- */}
          <aside className="flex flex-col gap-4">
            <Slab>
              <SlabHead label="For the best result" />
              <ul className="flex flex-col gap-5 p-5">
                {TIPS.map((tip) => (
                  <li key={tip.title} className="flex gap-3">
                    <span className="grid h-8 w-8 shrink-0 place-items-center rounded-[var(--radius-sm)] bg-bg-inset text-fg-secondary">
                      {tip.icon}
                    </span>
                    <div>
                      <p className="text-sm font-medium">{tip.title}</p>
                      <p className="mt-0.5 text-[13px] leading-relaxed text-fg-muted">{tip.body}</p>
                    </div>
                  </li>
                ))}
              </ul>
            </Slab>
            <div className="flex items-start gap-3 rounded-[var(--radius-lg)] border border-border-subtle bg-bg-surface p-4 text-[13px] leading-relaxed text-fg-muted shadow-sm">
              <IconCheckCircle size={17} className="mt-0.5 shrink-0 text-success" />
              Face crops and voiceprints are deleted automatically when processing finishes.
            </div>
          </aside>
        </div>
      </AppPage>
    </AppShell>
  );
}

/** A folder: where Drive files live (a plain glyph, not Google's logo). */
function DriveMark() {
  return (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M3 7.5A1.5 1.5 0 0 1 4.5 6h4.4l2 2h8.6A1.5 1.5 0 0 1 21 9.5v8A1.5 1.5 0 0 1 19.5 19h-15A1.5 1.5 0 0 1 3 17.5v-10Z"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** A photo: where Photos videos live (a plain glyph, not Google's logo). */
function PhotosMark() {
  return (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <rect x="3" y="5" width="18" height="14" rx="2" stroke="currentColor" strokeWidth="1.7" />
      <path
        d="m3.5 16 5-5 4 4 2.5-2.5L20.5 18"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinejoin="round"
      />
      <circle cx="16" cy="9.5" r="1.5" fill="currentColor" />
    </svg>
  );
}
