'use client';

/**
 * Download and share (docs/28 §W6, §W7) — the restrained surface.
 *
 * The quality list comes from the server, which derives it from the source's
 * real height, so a 720p upload is never offered 1080p. A menu that lists
 * qualities the recording cannot produce is worse than a short menu.
 *
 * Sharing is deliberately honest about what a browser can do (docs/28 §D3):
 * `navigator.share` with a file opens the native sheet — Instagram, WhatsApp,
 * whatever the device has — and where that is unavailable the options are a
 * WhatsApp link and copy-to-clipboard. There is no button claiming to post
 * somewhere it cannot.
 */

import { useCallback, useEffect, useState } from 'react';

import { IconDownload, IconShare } from '@/components/icons';
import { Alert, Badge, Button, Select, Slab, SlabHead, SpeakerDot, Spinner } from '@/components/ui';
import { formatBytes } from '@/lib/upload/resumable';
import { api } from '@/lib/store';
import type { ExportResponse, RenditionOption } from '@visiovox/ts-client';

const POLL_MS = 2000;

interface Props {
  projectId: string;
  projectTitle: string;
  speakers: { ordinal: number; label: string }[];
}

export function ExportPanel({ projectId, projectTitle, speakers }: Props) {
  const [options, setOptions] = useState<RenditionOption[]>([]);
  const [exports, setExports] = useState<ExportResponse[]>([]);
  const [speaker, setSpeaker] = useState<number>(speakers[0]?.ordinal ?? 1);
  const [quality, setQuality] = useState<string>('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [shareNote, setShareNote] = useState<string | null>(null);

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

  async function request() {
    setBusy(true);
    setError(null);
    try {
      const kind = quality === 'audio' ? 'audio' : 'video';
      await api().createExport(projectId, {
        kind,
        speaker_ordinal: speaker,
        quality: kind === 'audio' ? null : quality,
      });
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not start the export');
    } finally {
      setBusy(false);
    }
  }

  function downloadUrl(row: ExportResponse): string {
    return api().exportDownloadUrl(projectId, row.id);
  }

  async function share(row: ExportResponse) {
    setShareNote(null);
    const url = new URL(downloadUrl(row), window.location.origin).toString();
    const title = `${projectTitle} — speaker ${String(row.speaker_ordinal ?? 1)}`;

    // Sharing the FILE is the good path: it lands in the target app as media
    // rather than as a link somebody else may not be able to open.
    try {
      const response = await fetch(url);
      const blob = await response.blob();
      const file = new File([blob], `${title}.${row.kind === 'audio' ? 'm4a' : 'mp4'}`, {
        type: blob.type,
      });
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
        await navigator.share({ title, url });
        return;
      } catch {
        /* dismissed */
      }
    }

    try {
      await navigator.clipboard.writeText(url);
      setShareNote('Link copied. Note it expires when the download does.');
    } catch {
      setShareNote(url);
    }
  }

  const videoOptions = options.filter((o) => o.kind === 'video');

  return (
    <Slab>
      <SlabHead
        icon={<IconDownload size={17} />}
        label="Download"
        description="Export one speaker as video or audio."
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

        <Button onClick={() => void request()} loading={busy} busyLabel="Preparing…" full>
          Prepare download
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
                <SpeakerDot index={row.speaker_ordinal ?? 1} size={10} />
                <span className="font-medium">
                  {row.speaker_ordinal ? `Speaker ${String(row.speaker_ordinal)}` : 'All speakers'}
                </span>
                <span className="text-fg-muted">{row.quality ? row.quality : 'audio'}</span>
                {row.size_bytes !== null && (
                  <span className="font-mono text-[12px] text-fg-muted">
                    {formatBytes(row.size_bytes)}
                  </span>
                )}

                <span className="ml-auto flex items-center gap-2">
                  {row.status === 'ready' ? (
                    <>
                      <a
                        href={downloadUrl(row)}
                        download
                        className="inline-flex h-8 items-center gap-1.5 rounded-[var(--radius-sm)] border border-border-default bg-bg-surface px-3 text-[13px] font-medium shadow-xs transition-colors hover:border-border-strong hover:bg-bg-subtle"
                      >
                        <IconDownload size={14} />
                        Download
                      </a>
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
