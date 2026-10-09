'use client';

/**
 * Download and share (docs/28 §W6, §W7; clips and captions ADR-0022).
 *
 * The quality list comes from the server, which derives it from the source's
 * real height, so a 720p upload is never offered 1080p. A menu that lists
 * qualities the recording cannot produce is worse than a short menu.
 *
 * An export can be the whole recording or one clip of it - set here, or from a
 * transcript line - and can carry captions in the picture, in the original or
 * any language already translated. Burned-in captions play wherever the video
 * does, including feeds that autoplay muted.
 *
 * Sharing is deliberately honest about what a browser can do (docs/28 §D3):
 * `navigator.share` with a file opens the native sheet; where that is
 * unavailable the link is copied. There is no button claiming to post
 * somewhere it cannot.
 */

import { useCallback, useEffect, useState } from 'react';

import { IconDownload, IconFilm, IconShare, IconX } from '@/components/icons';
import { Alert, Badge, Button, Select, Slab, SlabHead, SpeakerDot, Spinner } from '@/components/ui';
import { readableTime } from '@/lib/playback/transcript';
import { formatBytes } from '@/lib/upload/resumable';
import { api } from '@/lib/store';
import type { ExportResponse, RenditionOption } from '@visiovox/ts-client';

const POLL_MS = 2000;

export interface Clip {
  readonly start_ms: number;
  readonly end_ms: number;
}

interface Props {
  projectId: string;
  projectTitle: string;
  speakers: { ordinal: number; label: string }[];
  durationMs: number | null;
  /** The clip to export, or null for the whole recording. */
  clip: Clip | null;
  onClipChange: (clip: Clip | null) => void;
  /** Languages captions can be burned in: "original" and ready translations. */
  captionOptions: { code: string; label: string }[];
  /** The listener's mix from the player's mixer (ordinal -> level), if any. */
  mix?: Readonly<Record<string, number>> | null;
}

/** "1:05" or "65" -> ms; null when unreadable. */
function parseTime(value: string): number | null {
  const parts = value.trim().split(':').map(Number);
  if (parts.length === 0 || parts.length > 3 || parts.some((p) => !Number.isFinite(p) || p < 0))
    return null;
  return Math.round(parts.reduce((acc, p) => acc * 60 + p, 0) * 1000);
}

function currentVideoMs(): number | null {
  const video = document.querySelector('video');
  return video === null ? null : Math.round(video.currentTime * 1000);
}

export function ExportPanel({
  projectId,
  projectTitle,
  speakers,
  durationMs,
  clip,
  onClipChange,
  captionOptions,
  mix = null,
}: Props) {
  const [options, setOptions] = useState<RenditionOption[]>([]);
  const [exports, setExports] = useState<ExportResponse[]>([]);
  // A speaker's ordinal, or 0 for "your mix".
  const [speaker, setSpeaker] = useState<number>(speakers[0]?.ordinal ?? 1);
  const usingMix = speaker === 0 && mix !== null;

  // Opening the mixer offers its mix here; closing it falls back to a speaker.
  useEffect(() => {
    if (mix !== null) setSpeaker(0);
    else setSpeaker((s) => (s === 0 ? (speakers[0]?.ordinal ?? 1) : s));
  }, [mix, speakers]);
  const [quality, setQuality] = useState<string>('');
  const [captions, setCaptions] = useState<string>('none');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [shareNote, setShareNote] = useState<string | null>(null);
  const [startText, setStartText] = useState('');
  const [endText, setEndText] = useState('');

  // Keep the editable fields in step when a clip is set from the transcript.
  useEffect(() => {
    setStartText(clip === null ? '' : readableTime(clip.start_ms));
    setEndText(clip === null ? '' : readableTime(clip.end_ms));
  }, [clip]);

  const refresh = useCallback(async () => {
    const list = await api().listExports(projectId);
    setExports(list.items);
    return list.items;
  }, [projectId]);

  useEffect(() => {
    void api()
      .exportOptions(projectId)
      .then((opts) => {
        setOptions(opts);
        setQuality(opts.find((o) => o.kind === 'video')?.name ?? 'audio');
      })
      .catch(() => {
        setError('Could not load the available download qualities.');
      });
    void refresh();
  }, [projectId, refresh]);

  // Poll only while something is actually rendering. A permanent timer on a
  // finished project is a request per two seconds per open tab, forever.
  useEffect(() => {
    const pending = exports.some((e) => e.status === 'queued' || e.status === 'running');
    if (!pending) return;
    const id = setTimeout(() => void refresh(), POLL_MS);
    return () => {
      clearTimeout(id);
    };
  }, [exports, refresh]);

  const isAudio = quality === 'audio';

  function applyTyped(which: 'start' | 'end', text: string) {
    const ms = parseTime(text);
    if (ms === null) return;
    const base = clip ?? { start_ms: 0, end_ms: durationMs ?? ms };
    onClipChange(which === 'start' ? { ...base, start_ms: ms } : { ...base, end_ms: ms });
  }

  function setFromPlayer(which: 'start' | 'end') {
    const ms = currentVideoMs();
    if (ms === null) return;
    const base = clip ?? { start_ms: 0, end_ms: durationMs ?? ms };
    onClipChange(which === 'start' ? { ...base, start_ms: ms } : { ...base, end_ms: ms });
  }

  const clipProblem =
    clip === null
      ? null
      : clip.end_ms - clip.start_ms < 500
        ? 'The clip must end at least half a second after it starts.'
        : durationMs !== null && clip.end_ms > durationMs + 1000
          ? 'The clip ends after the recording does.'
          : null;

  async function request() {
    if (clipProblem !== null) return;
    setBusy(true);
    setError(null);
    try {
      const kind = isAudio ? 'audio' : 'video';
      await api().createExport(projectId, {
        kind,
        speaker_ordinal: usingMix ? null : speaker,
        mix: usingMix ? { ...mix } : null,
        quality: kind === 'audio' ? null : quality,
        start_ms: clip?.start_ms ?? null,
        end_ms: clip?.end_ms ?? null,
        captions: kind === 'video' && captions !== 'none' ? captions : null,
      });
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not start the export');
    } finally {
      setBusy(false);
    }
  }

  // The signed URL is fetched with the session's token, then navigated to: a
  // plain link to the API carries no token and would be refused.
  async function download(row: ExportResponse) {
    setError(null);
    try {
      const link = await api().exportLink(projectId, row.id);
      window.location.assign(link.url);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The download link could not be made.');
    }
  }

  async function share(row: ExportResponse) {
    setShareNote(null);
    let link: { url: string; filename: string };
    try {
      link = await api().exportLink(projectId, row.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The link could not be made.');
      return;
    }
    const title = `${projectTitle} - ${describe(row)}`;

    // Sharing the FILE is the good path: it lands in the target app as media
    // rather than as a link somebody else may not be able to open.
    try {
      const response = await fetch(link.url);
      const blob = await response.blob();
      const file = new File([blob], link.filename, { type: blob.type });
      if (typeof navigator.canShare === 'function' && navigator.canShare({ files: [file] })) {
        await navigator.share({ files: [file], title });
        return;
      }
    } catch {
      // Fall through to link sharing; a failed file share is not an error the
      // user needs to see.
    }

    if (typeof navigator.share === 'function') {
      try {
        await navigator.share({ title, url: link.url });
        return;
      } catch {
        /* dismissed */
      }
    }

    try {
      await navigator.clipboard.writeText(link.url);
      setShareNote('Link copied. It works for a limited time.');
    } catch {
      setShareNote(link.url);
    }
  }

  function describe(row: ExportResponse): string {
    const who =
      row.mix != null
        ? 'Your mix'
        : (speakers.find((s) => s.ordinal === row.speaker_ordinal)?.label ??
          (row.speaker_ordinal ? `Speaker ${String(row.speaker_ordinal)}` : 'Everyone'));
    const part =
      row.start_ms != null && row.end_ms != null
        ? ` · ${readableTime(row.start_ms)}–${readableTime(row.end_ms)}`
        : '';
    const cc =
      row.captions != null
        ? ` · captions (${captionOptions.find((o) => o.code === row.captions)?.label ?? row.captions})`
        : '';
    return `${who}${part}${cc}`;
  }

  const videoOptions = options.filter((o) => o.kind === 'video');

  return (
    <Slab>
      <SlabHead
        icon={<IconDownload size={17} />}
        label="Download"
        description="One speaker's voice, as video or audio - whole or a clip, with captions if you like."
        right={options.length === 0 ? <Spinner label="Loading options" /> : undefined}
      />
      <div className="flex flex-col gap-5 p-5 sm:p-6">
        <div className="grid gap-4 sm:grid-cols-2">
          <Select
            label="Speaker"
            value={speaker}
            onChange={(e) => {
              setSpeaker(Number(e.target.value));
            }}
          >
            {mix !== null && <option value={0}>Your mix (from the mixer)</option>}
            {speakers.map((s) => (
              <option key={s.ordinal} value={s.ordinal}>
                {s.label}
              </option>
            ))}
          </Select>

          <Select
            label="Quality"
            value={quality}
            onChange={(e) => {
              setQuality(e.target.value);
            }}
          >
            {videoOptions.map((o) => (
              <option key={o.name} value={o.name}>
                {o.name} video
              </option>
            ))}
            <option value="audio">Audio only (M4A)</option>
          </Select>
        </div>

        <fieldset className="flex flex-col gap-3">
          <legend className="mb-1.5 text-[13px] font-medium">Part</legend>
          <div className="seg w-fit" role="group" aria-label="Part of the recording">
            <button
              type="button"
              className={clip === null ? 'seg-on' : ''}
              onClick={() => {
                onClipChange(null);
              }}
            >
              Whole recording
            </button>
            <button
              type="button"
              className={clip !== null ? 'seg-on' : ''}
              onClick={() => {
                if (clip === null) {
                  const now = currentVideoMs() ?? 0;
                  const end = Math.min(now + 30_000, durationMs ?? now + 30_000);
                  onClipChange({ start_ms: now, end_ms: Math.max(end, now + 500) });
                }
              }}
            >
              A clip
            </button>
          </div>
          {clip !== null && (
            <div className="flex flex-col gap-2 rounded-[var(--radius-md)] border border-border-subtle bg-bg-subtle p-3">
              <div className="flex flex-wrap items-end gap-3">
                {(['start', 'end'] as const).map((which) => (
                  <label key={which} className="flex flex-col gap-1 text-[12px] font-medium">
                    {which === 'start' ? 'From' : 'To'}
                    <span className="flex items-center gap-1.5">
                      <input
                        value={which === 'start' ? startText : endText}
                        onChange={(e) => {
                          if (which === 'start') setStartText(e.target.value);
                          else setEndText(e.target.value);
                        }}
                        onBlur={(e) => {
                          applyTyped(which, e.target.value);
                        }}
                        inputMode="numeric"
                        aria-label={which === 'start' ? 'Clip starts at' : 'Clip ends at'}
                        className="h-9 w-20 rounded-[var(--radius-sm)] border border-border-default bg-bg-surface px-2 font-mono text-[13px] outline-none focus-visible:border-accent"
                      />
                      <button
                        type="button"
                        onClick={() => {
                          setFromPlayer(which);
                        }}
                        className="h-9 rounded-[var(--radius-sm)] px-2 text-[12px] font-medium text-accent hover:bg-accent-soft"
                      >
                        Now
                      </button>
                    </span>
                  </label>
                ))}
                <span className="pb-2 text-[12.5px] text-fg-muted tabular">
                  {String(Math.max(0, Math.round((clip.end_ms - clip.start_ms) / 1000)))} s
                </span>
                <button
                  type="button"
                  aria-label="Export the whole recording instead"
                  onClick={() => {
                    onClipChange(null);
                  }}
                  className="ml-auto grid size-8 place-items-center rounded-[var(--radius-sm)] text-fg-muted hover:bg-bg-surface"
                >
                  <IconX size={14} />
                </button>
              </div>
              <p className="text-[12px] text-fg-muted">
                Tip: the clip button on any transcript line sets this to that line; using it on a
                later line stretches the clip to cover both.
              </p>
              {clipProblem !== null && <p className="text-[12px] text-danger">{clipProblem}</p>}
            </div>
          )}
        </fieldset>

        {!isAudio && (
          <Select
            label="Captions in the picture"
            value={captions}
            onChange={(e) => {
              setCaptions(e.target.value);
            }}
          >
            <option value="none">None</option>
            {captionOptions.map((o) => (
              <option key={o.code} value={o.code}>
                {o.label}
              </option>
            ))}
          </Select>
        )}

        <Button
          onClick={() => void request()}
          loading={busy}
          busyLabel="Preparing…"
          disabled={clipProblem !== null}
          full
        >
          {clip === null ? 'Prepare download' : 'Prepare clip'}
        </Button>

        {videoOptions.length === 0 && options.length > 0 && (
          <p className="text-[13px] text-fg-muted">
            This recording has no video track, so only the isolated audio can be downloaded.
          </p>
        )}

        {error !== null && <Alert tone="danger">{error}</Alert>}
        {shareNote !== null && <Alert tone="accent">{shareNote}</Alert>}

        {exports.length > 0 && (
          <ul className="flex flex-col divide-y divide-border-subtle rounded-[var(--radius-md)] border border-border-subtle">
            {exports.map((row) => (
              <li key={row.id} className="flex flex-wrap items-center gap-3 px-4 py-3 text-sm">
                {row.start_ms != null ? (
                  <IconFilm size={14} className="text-fg-muted" />
                ) : (
                  <SpeakerDot index={row.speaker_ordinal ?? 1} size={10} />
                )}
                <span className="min-w-0 font-medium">{describe(row)}</span>
                <span className="text-fg-muted">{row.quality ? row.quality : 'audio'}</span>
                {row.size_bytes !== null && (
                  <span className="font-mono text-[12px] text-fg-muted">
                    {formatBytes(row.size_bytes)}
                  </span>
                )}

                <span className="ml-auto flex items-center gap-2">
                  {row.status === 'ready' ? (
                    <>
                      <Button size="sm" variant="secondary" onClick={() => void download(row)}>
                        <IconDownload size={14} />
                        Download
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => void share(row)}>
                        <IconShare size={14} />
                        Share
                      </Button>
                    </>
                  ) : row.status === 'failed' ? (
                    <Badge tone="danger" dot>
                      Failed
                    </Badge>
                  ) : (
                    <Badge tone="accent" dot="pulse">
                      {row.status === 'running' ? 'Rendering' : 'Queued'}
                    </Badge>
                  )}
                </span>
              </li>
            ))}
          </ul>
        )}

        <p className="text-[12.5px] leading-relaxed text-fg-muted">
          Downloads are prepared once and kept for 7 days. Asking again returns the same file rather
          than re-encoding it.
        </p>
      </div>
    </Slab>
  );
}
