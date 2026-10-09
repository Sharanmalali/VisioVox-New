'use client';

/**
 * Per-speaker dubbing (ADR-0024): the recording in another language, each
 * person in a voice of their own, the room kept.
 *
 * Made on request on the GPU worker - translated first if needed - and shown
 * here as its own video, beside the original player, with what it did:
 * which voice each speaker got and how well the lines fit their time.
 */

import { useCallback, useEffect, useState } from 'react';
import type { DubResponse, TranslationLanguageOption } from '@visiovox/ts-client';

import { IconDownload, IconFilm } from '@/components/icons';
import { Button, Select, Spinner } from '@/components/ui';
import { api } from '@/lib/store';

const POLL_MS = 4000;

function percent(x: unknown): string {
  return typeof x === 'number' ? `${String(Math.round(x * 100))}%` : '—';
}

export function DubPanel({
  projectId,
  nameOf,
}: {
  projectId: string;
  nameOf: (ordinal: number) => string;
}) {
  const [languages, setLanguages] = useState<TranslationLanguageOption[]>([]);
  const [items, setItems] = useState<DubResponse[]>([]);
  const [choice, setChoice] = useState('hi');
  const [shown, setShown] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const body = await api().listDubs(projectId);
    setLanguages(body.languages);
    setItems(body.items);
    return body.items;
  }, [projectId]);

  useEffect(() => {
    refresh()
      .then((list) => {
        const ready = list.find((d) => d.status === 'ready');
        if (ready !== undefined) setShown(ready.language);
      })
      .catch(() => undefined);
  }, [refresh]);

  const working = items.some((d) => d.status === 'queued' || d.status === 'running');
  useEffect(() => {
    if (!working) return;
    const timer = setInterval(() => {
      refresh().catch(() => undefined);
    }, POLL_MS);
    return () => {
      clearInterval(timer);
    };
  }, [working, refresh]);

  const request = async () => {
    setError(null);
    try {
      await api().requestDub(projectId, choice);
      setShown(choice);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The dub could not be started.');
    }
  };

  const nativeOf = (code: string) => languages.find((l) => l.code === code)?.native ?? code;
  const current = items.find((d) => d.language === shown) ?? null;
  const stats = current?.stats ?? null;
  const voices = (stats?.['voices'] ?? {}) as Record<string, string>;

  if (languages.length === 0) return null;

  return (
    <section className="insights" aria-labelledby="dub-title">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <span className="grid size-9 shrink-0 place-items-center rounded-[var(--radius-sm)] bg-accent-soft text-accent">
            <IconFilm size={18} />
          </span>
          <div>
            <h2 id="dub-title" className="text-[16px] font-semibold">
              Dub into another language
            </h2>
            <p className="text-[13px] text-fg-muted">
              Each person speaks in a voice of their own, timed to what they said; the room and
              everyone else stay as recorded.
            </p>
          </div>
        </div>
      </header>

      <div className="flex flex-wrap items-end gap-3">
        <div className="w-60">
          <Select
            label="Language"
            value={choice}
            onChange={(e) => {
              setChoice(e.target.value);
            }}
          >
            <optgroup label="Languages of India">
              {languages
                .filter((l) => l.group === 'india')
                .map((l) => (
                  <option key={l.code} value={l.code}>
                    {l.native} - {l.name}
                  </option>
                ))}
            </optgroup>
            <optgroup label="Other">
              {languages
                .filter((l) => l.group !== 'india')
                .map((l) => (
                  <option key={l.code} value={l.code}>
                    {l.name}
                  </option>
                ))}
            </optgroup>
          </Select>
        </div>
        <Button
          onClick={() => {
            void request();
          }}
          disabled={working}
        >
          Dub into {nativeOf(choice)}
        </Button>
        {items
          .filter((d) => d.status === 'ready')
          .map((d) => (
            <button
              key={d.language}
              type="button"
              onClick={() => {
                setShown(d.language);
              }}
              aria-pressed={shown === d.language}
              className={
                'h-9 rounded-full border px-3 text-[13px] font-medium ' +
                (shown === d.language
                  ? 'border-accent bg-accent-soft text-accent'
                  : 'border-border-default hover:bg-bg-subtle')
              }
            >
              {nativeOf(d.language)}
            </button>
          ))}
      </div>

      {error !== null && <p className="text-[13.5px] text-danger">{error}</p>}

      {current !== null && (current.status === 'queued' || current.status === 'running') && (
        <p className="inline-flex items-center gap-2 text-[13.5px] text-fg-secondary" role="status">
          <Spinner size={14} />
          Translating and re-voicing every line in {nativeOf(current.language)}… this takes a few
          minutes for a long recording.
        </p>
      )}
      {current?.status === 'failed' && (
        <p className="text-[13.5px] text-danger">
          The dub could not be made: {current.error ?? 'unknown error'}
        </p>
      )}

      {current?.status === 'ready' && current.url != null && (
        <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
          <video
            key={current.url}
            className="w-full rounded-[var(--radius-md)] bg-black"
            src={current.url}
            controls
            playsInline
            preload="metadata"
          />
          <div className="flex flex-col gap-3 text-[13.5px]">
            <dl className="flex flex-col gap-1.5">
              {Object.entries(voices).map(([ordinal, voice]) => (
                <div key={ordinal} className="flex justify-between gap-3">
                  <dt className="text-fg-secondary">{nameOf(Number(ordinal))}</dt>
                  <dd className="font-medium">voiced by {voice}</dd>
                </div>
              ))}
            </dl>
            <p className="text-fg-secondary">
              {percent(stats?.['as_is'])} of lines fit as spoken, {percent(stats?.['sped_up'])} were
              sped up to fit, {percent(stats?.['overrun'])} ran over.
            </p>
            <a
              href={current.url}
              className="inline-flex h-9 w-fit items-center gap-1.5 rounded-[var(--radius-sm)] border border-border-default bg-bg-surface px-3 text-[13px] font-medium hover:bg-bg-subtle"
            >
              <IconDownload size={14} />
              Download the dubbed video
            </a>
            <p className="text-[12px] leading-relaxed text-fg-muted">
              Machine-translated and spoken by synthetic voices (Indic Parler-TTS) - the speakers'
              own voices are not imitated. The room is the recording with their separated voices
              taken out, so a little of the original may remain.
            </p>
          </div>
        </div>
      )}
    </section>
  );
}
