'use client';

import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useCallback, useEffect, useState } from 'react';
import type { JobResponse, ProjectResponse } from '@visiovox/ts-client';

import { DubPanel } from '@/components/DubPanel';
import { ExportPanel } from '@/components/ExportPanel';
import type { Clip } from '@/components/ExportPanel';
import { InsightsPanel, insightBlocks, insightsFor } from '@/components/InsightsPanel';
import { IconChevronDown, IconChevronRight, IconInfo } from '@/components/icons';
import { Player } from '@/components/Player';
import { ProcessingView } from '@/components/ProcessingView';
import { SharePanel } from '@/components/SharePanel';
import { SummaryPanel, summaryBlocks, useSummary } from '@/components/SummaryPanel';
import { TranscriptPanel } from '@/components/TranscriptPanel';
import { TranslateMenu, useTranslations } from '@/components/Translation';
import { AppPage, AppShell } from '@/components/shell/AppShell';
import { Alert, Badge, Skeleton, Slab } from '@/components/ui';
import { clockDuration, languageName, statusMeta } from '@/lib/format';
import type { Manifest } from '@/lib/playback/manifest';
import type { TranscriptLine } from '@/lib/playback/transcript';
import { accessToken, api, useRequireSignIn } from '@/lib/store';

interface Progress {
  status: string;
  progress: number;
  stage: string | null;
}

const TERMINAL = new Set(['succeeded', 'partial', 'failed', 'cancelled']);

/** The pipeline's stage ids, in the words the rest of the product uses. */
const STAGE_NAME: Record<string, string> = {
  S0_ingest: 'Prepare video',
  S5_extract: 'Separate speakers',
  S3_fuse: 'Match voices to people',
  S7_transcribe: 'Write captions',
  S9_package: 'Package for playback',
};

/** Warning codes, said as a person would say them. */
function warningText(code: string): string {
  const known: Record<string, string> = {
    silent_face_track_dropped: 'A face that never spoke was left out.',
    face_tracks_merged: 'Some people appeared in several shots and were joined into one track.',
    captions_unavailable: 'Captions could not be produced for this recording.',
    video_resampled_to_25fps: 'The video was converted to 25 frames per second for processing.',
  };
  return known[code] ?? code.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase());
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5 rounded-[var(--radius-md)] border border-border-subtle bg-bg-surface px-4 py-3 shadow-xs">
      <dt className="text-[12px] font-medium text-fg-muted">{label}</dt>
      <dd className="text-[15px] font-semibold tabular">{value}</dd>
    </div>
  );
}

export default function ProjectPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const [project, setProject] = useState<ProjectResponse | null>(null);
  const [job, setJob] = useState<JobResponse | null>(null);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Nothing is fetched until the session is restored: opened from the vault
  // or refreshed, the first render has no user yet, and a request then goes
  // out without a token.
  const authed = useRequireSignIn();
  const translation = useTranslations(projectId, manifest);
  const summary = useSummary(projectId, manifest !== null);

  // The stretch to download, set from the Export panel or from transcript
  // lines: a line's clip button sets it, and on a later line stretches it.
  const [clip, setClip] = useState<Clip | null>(null);
  // The listener's mix from the player's mixer, offered as a download.
  const [mix, setMix] = useState<Readonly<Record<string, number>> | null>(null);
  const clipLine = useCallback((line: TranscriptLine) => {
    setClip((current) =>
      current === null
        ? { start_ms: line.start_ms, end_ms: line.end_ms }
        : {
            start_ms: Math.min(current.start_ms, line.start_ms),
            end_ms: Math.max(current.end_ms, line.end_ms),
          },
    );
    document.getElementById('downloads')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, []);
  const captionOptions = [
    { code: 'original', label: 'As spoken' },
    ...translation.items
      .filter((i) => i.status === 'ready')
      .map((i) => ({
        code: i.language,
        label: translation.languages.find((l) => l.code === i.language)?.native ?? i.language,
      })),
  ];
  const translatingTo = translation.languages.find((l) => l.code === translation.active);
  const translationNotice =
    translation.error !== null ? (
      <>Translation stopped: {translation.error}</>
    ) : translatingTo === undefined ? null : translation.texts === null ? (
      <>Translating into {translatingTo.native}. This takes a few seconds.</>
    ) : (
      <span className="flex items-center justify-between gap-3">
        <span>Showing {translatingTo.native}, machine-translated. Exports use it too.</span>
        <button
          type="button"
          className="shrink-0 font-medium underline underline-offset-2"
          onClick={() => {
            translation.choose(null);
          }}
        >
          Show original
        </button>
      </span>
    );

  useEffect(() => {
    if (!authed) return;
    // Read through a function: TypeScript narrows a plain boolean to `false`
    // after the first check and keeps that narrowing across `await`.
    let cancelled = false;
    const stopped = () => cancelled;

    async function poll() {
      try {
        const p = await api().getProject(projectId);
        if (stopped()) return;
        setProject(p);

        const j: JobResponse | null = await api()
          .getJob(projectId)
          .catch(() => null);
        if (stopped() || j === null) return;
        setJob(j);
        setProgress({ status: j.status, progress: j.progress, stage: null });

        // A slow safety net only. Live progress arrives over SSE in
        // ProcessingView; this exists so the page still converges if the
        // stream is blocked by a proxy that buffers text/event-stream.
        if (!TERMINAL.has(j.status)) {
          setTimeout(() => void poll(), 10000);
        }
      } catch (err) {
        if (!stopped()) setError(err instanceof Error ? err.message : 'Failed to load');
      }
    }

    void poll();
    return () => {
      cancelled = true;
    };
  }, [projectId, authed]);

  // The manifest carries signed URLs with an expiry, so it is fetched once the
  // project reaches `ready` rather than alongside the project record.
  useEffect(() => {
    if (project?.status !== 'ready') return;
    let cancelled = false;
    void api()
      .getManifest(projectId)
      .then((body) => {
        if (!cancelled) setManifest(body as Manifest);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load manifest');
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, project?.status]);

  const names = project?.speaker_names ?? {};
  const speakers = Array.from({ length: project?.speaker_count ?? 0 }, (_, i) => ({
    ordinal: i + 1,
    label: names[String(i + 1)] ?? `Speaker ${String(i + 1)}`,
  }));

  // Optimistic: the name shows at once everywhere on the page, and is put back
  // if the server refuses it (the chip then shows the field as invalid).
  const renameSpeaker = useCallback(
    async (ordinal: number, name: string | null) => {
      const before = project;
      if (before === null) return;
      const key = String(ordinal);
      const next = Object.fromEntries(
        Object.entries(before.speaker_names ?? {}).filter(([k]) => k !== key),
      );
      if (name !== null) next[key] = name;
      setProject({ ...before, speaker_names: next });
      try {
        setProject(await api().renameSpeaker(projectId, ordinal, name));
      } catch (err) {
        setProject(before);
        throw err;
      }
    },
    [project, projectId],
  );

  return (
    <AppShell>
      <AppPage>
        <nav
          aria-label="Breadcrumb"
          className="-mb-4 flex items-center gap-1.5 text-[13px] text-fg-muted"
        >
          <Link href="/projects" className="rounded-[var(--radius-xs)] hover:text-fg-primary">
            Projects
          </Link>
          <IconChevronRight size={14} />
          <span className="truncate text-fg-secondary">{project?.title ?? 'Loading…'}</span>
        </nav>

        {error !== null && (
          <Alert tone="danger" title="Something went wrong">
            {error}
          </Alert>
        )}

        {project === null && error === null && (
          <div className="flex flex-col gap-6">
            <Skeleton className="h-9 w-80" />
            <div className="grid gap-3 sm:grid-cols-4">
              {[0, 1, 2, 3].map((i) => (
                <Skeleton key={i} className="h-16" />
              ))}
            </div>
            <Skeleton className="aspect-video w-full" />
          </div>
        )}

        {project !== null && (
          <>
            <header className="flex flex-col gap-5">
              <div className="flex flex-wrap items-center gap-3">
                <h1 className="min-w-0 truncate text-[28px] font-semibold tracking-[-0.025em] sm:text-[32px]">
                  {project.title}
                </h1>
                {/* While the job runs, ProcessingView shows the live status;
                    this one comes from a slow poll and would contradict it. */}
                {(progress === null || TERMINAL.has(progress.status)) &&
                  (() => {
                    const s = statusMeta(project.status);
                    return (
                      <Badge tone={s.tone} dot={s.live ? 'pulse' : true}>
                        {s.label}
                      </Badge>
                    );
                  })()}
              </div>

              {project.status === 'ready' && (
                <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
                  <Fact label="Speakers" value={String(project.speaker_count ?? 0)} />
                  <Fact label="Length" value={clockDuration(project.duration_ms) ?? '—'} />
                  <Fact
                    label="Overlap"
                    value={
                      project.overlap_ratio !== null
                        ? `${String(Math.round(project.overlap_ratio * 100))}%`
                        : '—'
                    }
                  />
                  <Fact
                    label="Difficulty"
                    value={
                      project.difficulty
                        ? project.difficulty.charAt(0).toUpperCase() + project.difficulty.slice(1)
                        : 'Unrated'
                    }
                  />
                  <Fact
                    label="Caption language"
                    value={languageName(project.language) ?? 'Detected automatically'}
                  />
                </dl>
              )}
            </header>

            {project.warnings.length > 0 && (
              <Alert tone="neutral" title="Good to know">
                <ul className="flex flex-col gap-1">
                  {project.warnings.map((w) => (
                    <li key={w}>{warningText(w)}</li>
                  ))}
                </ul>
              </Alert>
            )}

            {progress !== null && !TERMINAL.has(progress.status) && (
              <ProcessingView
                projectId={projectId}
                projectTitle={project.title}
                token={accessToken()}
                speakerCount={project.speaker_count}
              />
            )}

            {project.status === 'failed' && (
              <Alert tone="danger" title="This recording could not be separated">
                VisioVox needs the speakers’ faces clearly on screen to tell their voices apart. The
                processing details below show the step that stopped it.
              </Alert>
            )}

            {manifest !== null ? (
              <Slab className="p-4 sm:p-6">
                <Player
                  manifest={manifest}
                  names={project.speaker_names ?? {}}
                  translated={translation.texts}
                  onMixChange={setMix}
                  aside={(context) => (
                    <TranscriptPanel
                      context={context}
                      title={project.title}
                      onRename={renameSpeaker}
                      translated={translation.texts}
                      headerExtra={<TranslateMenu state={translation} />}
                      notice={translationNotice}
                      onClip={clipLine}
                      docxPreamble={() => [
                        ...(summary.content !== null
                          ? summaryBlocks(summary.content, context.nameOf)
                          : []),
                        ...insightBlocks(
                          insightsFor(
                            context.lines,
                            context.speakers.map((s) => s.ordinal),
                            manifest.duration_ms,
                            context.activity,
                          ),
                          context.nameOf,
                        ),
                      ]}
                    />
                  )}
                  below={(context) => (
                    <>
                      <SummaryPanel context={context} summary={summary} />
                      <DubPanel projectId={projectId} nameOf={context.nameOf} />
                      <InsightsPanel context={context} durationMs={manifest.duration_ms} />
                    </>
                  )}
                />
              </Slab>
            ) : (
              project.status === 'ready' && <Skeleton className="aspect-video w-full" />
            )}

            {project.status === 'ready' && (
              <div id="downloads" className="grid scroll-mt-24 items-start gap-6 lg:grid-cols-2">
                <ExportPanel
                  projectId={projectId}
                  projectTitle={project.title}
                  speakers={speakers}
                  durationMs={project.duration_ms}
                  clip={clip}
                  onClipChange={setClip}
                  captionOptions={captionOptions}
                  mix={mix}
                />
                <SharePanel
                  projectId={projectId}
                  projectTitle={project.title}
                  speakers={speakers}
                />
              </div>
            )}

            {job && job.stages.length > 0 && (
              <details className="group overflow-hidden rounded-[var(--radius-lg)] border border-border-subtle bg-bg-surface shadow-sm [&_summary::-webkit-details-marker]:hidden">
                <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-5 py-4 text-sm font-medium sm:px-6">
                  <span className="inline-flex items-center gap-2">
                    <IconInfo size={16} className="text-fg-muted" />
                    Processing details
                  </span>
                  <IconChevronDown
                    size={17}
                    className="text-fg-muted transition-transform group-open:rotate-180"
                  />
                </summary>
                <div className="overflow-x-auto border-t border-border-subtle">
                  <table>
                    <thead>
                      <tr>
                        <th className="pl-5 sm:pl-6">Step</th>
                        <th>Status</th>
                        <th className="pr-5 text-right sm:pr-6">Time</th>
                      </tr>
                    </thead>
                    <tbody>
                      {job.stages.map((s) => (
                        <tr key={s.stage}>
                          <td className="pl-5 sm:pl-6">{STAGE_NAME[s.stage] ?? s.stage}</td>
                          <td>
                            <Badge
                              tone={
                                s.status === 'succeeded'
                                  ? 'success'
                                  : s.status === 'failed'
                                    ? 'danger'
                                    : s.status === 'running'
                                      ? 'accent'
                                      : 'neutral'
                              }
                            >
                              {s.status}
                            </Badge>
                          </td>
                          <td className="pr-5 text-right font-mono text-[12.5px] text-fg-secondary sm:pr-6">
                            {s.duration_ms !== null
                              ? s.duration_ms >= 1000
                                ? `${(s.duration_ms / 1000).toFixed(1)} s`
                                : `${String(s.duration_ms)} ms`
                              : '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </details>
            )}
          </>
        )}
      </AppPage>
    </AppShell>
  );
}
