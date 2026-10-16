'use client';

/**
 * Per-speaker dubbing (ADR-0024): the recording in another language, each
 * person in a voice of their own, the room kept.
 *
 * Made on request on the GPU worker - translated first if needed - and shown
 * here two ways: speaker by speaker, in the same player as the original (each
 * person's dubbed voice alone, or the whole dub), and as the dubbed video.
 * Beside them, what it did: which voice each speaker got and how well the
 * lines fit their time.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import type { DubResponse, TranslationLanguageOption } from '@visiovox/ts-client';

import { IconDownload, IconFilm } from '@/components/icons';
import { Player } from '@/components/Player';
import { fetchTranslatedTexts } from '@/components/Translation';
import { Button, Select, Spinner } from '@/components/ui';
import type { Manifest } from '@/lib/playback/manifest';
import { api } from '@/lib/store';

const POLL_MS = 4000;

function percent(x: unknown): string {
  return typeof x === 'number' ? `${String(Math.round(x * 100))}%` : '—';
}

function seconds(ms: unknown): string {
  return typeof ms === 'number' ? `${(ms / 1000).toFixed(1)} s` : '—';
}

/** Whether the dub kept each speaker's voice alone (dubs made since 2026-10-13). */
function hasTracks(dub: DubResponse | null): boolean {
  const tracks = dub?.stats?.['tracks'];
  return typeof tracks === 'object' && tracks !== null;
}

/**
 * The dub, speaker by speaker: its manifest and translated captions, fetched
 * once the dub is ready. Null until then, or for a dub made before speakers
 * were kept apart.
 */
function useDubPlayback(projectId: string, dub: DubResponse | null) {
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [texts, setTexts] = useState<ReadonlyMap<string, string> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const language = dub?.status === 'ready' && hasTracks(dub) ? dub.language : null;

  useEffect(() => {
    setManifest(null);
    setTexts(null);
    setError(null);
    if (language === null) return;
    let cancelled = false;
    // Through a function: TypeScript narrows the flag to false across await.
    const stopped = () => cancelled;
    void (async () => {
      const loaded = (await api().getDubManifest(projectId, language)) as Manifest;
      if (stopped()) return;
      setManifest(loaded);
      // The dub was spoken from these translated captions; show them in the
      // caption box, the original under each line, as the main player does.
      const { items } = await api().listTranslations(projectId);
      const made = items.find((t) => t.language === language && t.status === 'ready');
      if (made === undefined || stopped()) return;
      const map = await fetchTranslatedTexts(made.speakers ?? [], loaded.speakers);
      if (!stopped()) setTexts(map);
    })().catch((err: unknown) => {
      if (!stopped()) setError(err instanceof Error ? err.message : 'The dub could not be loaded.');
    });
    return () => {
      cancelled = true;
    };
  }, [projectId, language]);

  return { manifest, texts, error };
}

/**
 * One recording at a time: starting any video on the page pauses the others,
 * so the dub and the original never talk over each other.
 */
function useOnePlayerAtATime() {
  useEffect(() => {
    const onPlay = (event: Event) => {
      const started = event.target;
      if (!(started instanceof HTMLVideoElement)) return;
      for (const other of document.querySelectorAll('video')) {
        if (other !== started && !other.paused) other.pause();
      }
    };
    // `play` does not bubble; capturing sees it on its way down.
    document.addEventListener('play', onPlay, true);
    return () => {
      document.removeEventListener('play', onPlay, true);
    };
  }, []);
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
  const [view, setView] = useState<'speakers' | 'video'>('speakers');

  const refresh = useCallback(async () => {
    const body = await api().listDubs(projectId);
    setLanguages(body.languages);
    setItems(body.items);
    return body.items;
  }, [projectId]);

  useEffect(() => {
    refresh()
      .then((list) => {
        // A dub still being made comes first: otherwise, opened mid-dub, the
        // button is disabled and nothing on the panel says why.
        const pick =
          list.find((d) => d.status === 'queued' || d.status === 'running') ??
          list.find((d) => d.status === 'ready');
        if (pick !== undefined) setShown(pick.language);
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

  const request = async (language: string = choice) => {
    setError(null);
    try {
      await api().requestDub(projectId, language);
      setShown(language);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The dub could not be started.');
    }
  };

  const nativeOf = (code: string) => languages.find((l) => l.code === code)?.native ?? code;
  const current = items.find((d) => d.language === shown) ?? null;
  const stats = current?.stats ?? null;
  const voices = (stats?.['voices'] ?? {}) as Record<string, string>;
  const playback = useDubPlayback(projectId, current);
  useOnePlayerAtATime();
  const separate = hasTracks(current);
  const names = useMemo(() => {
    const out: Record<string, string> = {};
    for (const sp of playback.manifest?.speakers ?? [])
      out[String(sp.ordinal)] = nameOf(sp.ordinal);
    return out;
  }, [playback.manifest, nameOf]);

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
        <div className="flex flex-col gap-4">
          {separate ? (
            <div className="seg w-fit" role="group" aria-label="How to watch the dub">
              <button
                type="button"
                className={view === 'speakers' ? 'seg-on' : ''}
                aria-pressed={view === 'speakers'}
                onClick={() => {
                  setView('speakers');
                }}
              >
                Speaker by speaker
              </button>
              <button
                type="button"
                className={view === 'video' ? 'seg-on' : ''}
                aria-pressed={view === 'video'}
                onClick={() => {
                  setView('video');
                }}
              >
                Dubbed video
              </button>
            </div>
          ) : (
            <div className="flex flex-wrap items-center gap-3 text-[13.5px] text-fg-secondary">
              <span>
                This dub was made before each speaker&apos;s dubbed voice was kept separately.
              </span>
              <Button
                variant="secondary"
                onClick={() => {
                  void request(current.language);
                }}
                disabled={working}
              >
                Make it speaker by speaker
              </Button>
            </div>
          )}

          {separate && view === 'speakers' ? (
            playback.manifest !== null ? (
              <Player
                key={current.language}
                manifest={playback.manifest}
                names={names}
                translated={playback.texts}
                mixedLabels={{
                  label: 'Whole dub',
                  meta: `everyone, in ${nativeOf(current.language)}`,
                  mixer: 'Whole dub (room and voices)',
                }}
              />
            ) : playback.error !== null ? (
              <p className="text-[13.5px] text-danger">{playback.error}</p>
            ) : (
              <p
                className="inline-flex items-center gap-2 text-[13.5px] text-fg-secondary"
                role="status"
              >
                <Spinner size={14} />
                Loading each speaker&apos;s dubbed voice…
              </p>
            )
          ) : (
            <video
              key={current.url}
              className="w-full max-w-3xl rounded-[var(--radius-md)] bg-black"
              src={current.url}
              controls
              playsInline
              preload="metadata"
            />
          )}

          <div className="flex flex-col gap-3 text-[13.5px]">
            <dl className="flex flex-col gap-1.5 sm:max-w-md">
              {Object.entries(voices).map(([ordinal, voice]) => (
                <div key={ordinal} className="flex justify-between gap-3">
                  <dt className="text-fg-secondary">{nameOf(Number(ordinal))}</dt>
                  <dd className="font-medium">voiced by {voice}</dd>
                </div>
              ))}
            </dl>
            <p className="text-fg-secondary">
              {percent(stats?.['as_is'])} of lines fit as spoken and {percent(stats?.['sped_up'])}{' '}
              were sped up to fit.{' '}
              {typeof stats?.['late'] === 'number' && stats['late'] > 0
                ? `${percent(stats['late'])} start a little late (at most ${seconds(stats['max_lag_ms'])}) so that no one talks over themselves.`
                : 'Every line starts on time.'}
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
              own voices are not imitated. Speaker by speaker, each person is their dubbed voice
              alone; the whole dub is the room with their separated voices taken out and the dubbed
              ones put in, so a little of the original may remain.
            </p>
          </div>
        </div>
      )}
    </section>
  );
}
