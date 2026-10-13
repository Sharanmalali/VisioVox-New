'use client';

/**
 * Translated captions (ADR-0020): pick a language, read the whole
 * conversation in it - in the transcript, in the player's caption, and in
 * every export.
 *
 * Translation runs on the GPU worker with NLLB-200. Each language is made
 * once per project and kept, so switching back to one is instant. Translated
 * captions are segment-level: word order changes in translation, so the
 * per-word highlight is the original's alone.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { TranslationLanguageOption, TranslationResponse } from '@visiovox/ts-client';

import { IconCheck, IconChevronDown, IconLanguages, IconSearch } from '@/components/icons';
import { Spinner, cx } from '@/components/ui';
import { parseTranscript } from '@/lib/playback/captions';
import type { Manifest } from '@/lib/playback/manifest';
import { api } from '@/lib/store';

const POLL_MS = 2500;

export interface TranslationState {
  readonly languages: readonly TranslationLanguageOption[];
  readonly items: readonly TranslationResponse[];
  /** The language being shown, or null for the original. */
  readonly active: string | null;
  /** Line key (`speakerId:start_ms`) -> translated text, once loaded. */
  readonly texts: ReadonlyMap<string, string> | null;
  readonly working: boolean;
  readonly error: string | null;
  readonly choose: (code: string | null) => void;
}

/**
 * Each speaker's translated captions, keyed like the transcript's lines
 * (`speakerId:start_ms`). A file that cannot be fetched costs that speaker's
 * lines, not the rest.
 */
export async function fetchTranslatedTexts(
  files: NonNullable<TranslationResponse['speakers']>,
  speakers: Manifest['speakers'],
): Promise<Map<string, string>> {
  const map = new Map<string, string>();
  for (const file of files) {
    const speaker = speakers.find((s) => s.ordinal === file.ordinal);
    if (speaker === undefined) continue;
    const response = await fetch(file.url);
    if (!response.ok) continue;
    const transcript = parseTranscript(await response.json());
    for (const segment of transcript?.segments ?? []) {
      if (segment.text.trim() !== '')
        map.set(`${speaker.id}:${String(segment.start_ms)}`, segment.text.trim());
    }
  }
  return map;
}

export function useTranslations(projectId: string, manifest: Manifest | null): TranslationState {
  const [languages, setLanguages] = useState<TranslationLanguageOption[]>([]);
  const [items, setItems] = useState<TranslationResponse[]>([]);
  const [active, setActive] = useState<string | null>(null);
  // Tagged with its language, so switching languages can never show the
  // previous language's text under the new one's name while it loads.
  const [shown, setShown] = useState<{ language: string; map: Map<string, string> } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const loaded = useRef(new Map<string, Map<string, string>>());

  const refresh = useCallback(async () => {
    const body = await api().listTranslations(projectId);
    setLanguages(body.languages);
    setItems(body.items);
    return body.items;
  }, [projectId]);

  useEffect(() => {
    if (manifest === null) return;
    refresh().catch(() => undefined);
  }, [manifest, refresh]);

  const current = items.find((i) => i.language === active) ?? null;
  const working = current !== null && (current.status === 'queued' || current.status === 'running');

  // While the chosen language is being made, ask again until it is done.
  useEffect(() => {
    if (!working) return;
    const timer = setInterval(() => {
      refresh().catch(() => undefined);
    }, POLL_MS);
    return () => {
      clearInterval(timer);
    };
  }, [working, refresh]);

  // Once ready, fetch each speaker's translated captions and key them like
  // the transcript's lines.
  useEffect(() => {
    if (active === null || manifest === null) return;
    const cached = loaded.current.get(active);
    if (cached !== undefined) {
      setShown({ language: active, map: cached });
      return;
    }
    if (current?.status === 'failed') {
      setError(current.error ?? 'This translation could not be made.');
      setActive(null);
      return;
    }
    if (current?.status !== 'ready') return;
    let cancelled = false;
    // Through a function: TypeScript narrows the flag to false across await.
    const stopped = () => cancelled;
    const language = active;
    void (async () => {
      const map = await fetchTranslatedTexts(current.speakers ?? [], manifest.speakers);
      if (stopped()) return;
      loaded.current.set(language, map);
      setShown({ language, map });
    })().catch(() => {
      if (!cancelled) setError('The translation could not be loaded.');
    });
    return () => {
      cancelled = true;
    };
  }, [active, current, manifest]);

  const choose = useCallback(
    (code: string | null) => {
      setError(null);
      setActive(code);
      if (code === null) return;
      const known = items.find((i) => i.language === code);
      if (known?.status === 'ready' || known?.status === 'queued' || known?.status === 'running')
        return;
      void api()
        .requestTranslation(projectId, code)
        .then(() => refresh())
        .catch((err: unknown) => {
          setError(err instanceof Error ? err.message : 'Translation could not be started.');
          setActive(null);
        });
    },
    [items, projectId, refresh],
  );

  const texts = active !== null && shown?.language === active ? shown.map : null;
  return { languages, items, active, texts, working, error, choose };
}

/** The language picker in the transcript's header. */
export function TranslateMenu({ state }: { state: TranslationState }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('pointerdown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return state.languages.filter(
      (l) => q === '' || l.name.toLowerCase().includes(q) || l.native.toLowerCase().includes(q),
    );
  }, [state.languages, query]);

  const statusOf = (code: string) => state.items.find((i) => i.language === code)?.status;
  const activeLanguage = state.languages.find((l) => l.code === state.active);

  if (state.languages.length === 0) return null;

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => {
          setOpen((o) => !o);
        }}
        className={cx(
          'inline-flex h-8 items-center gap-1.5 rounded-[var(--radius-sm)] border px-2.5 text-[12.5px] font-medium',
          state.active !== null
            ? 'border-accent bg-accent-soft text-accent'
            : 'border-border-default bg-bg-surface hover:bg-bg-subtle',
        )}
      >
        {state.working ? <Spinner size={13} /> : <IconLanguages size={14} />}
        {activeLanguage !== undefined ? activeLanguage.native : 'Translate'}
        <IconChevronDown size={13} className="opacity-70" />
      </button>
      {open && (
        <div className="absolute right-0 top-[calc(100%+6px)] z-30 flex w-72 flex-col overflow-hidden rounded-[var(--radius-md)] border border-border-subtle bg-bg-surface shadow-lg">
          <div className="relative border-b border-border-subtle p-2">
            <IconSearch
              size={14}
              className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-fg-muted"
            />
            <input
              autoFocus
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
              }}
              placeholder="Find a language"
              aria-label="Find a language"
              className="h-8 w-full rounded-[var(--radius-sm)] bg-bg-subtle pl-8 pr-2 text-[13px] outline-none"
            />
          </div>
          <ul
            role="listbox"
            aria-label="Translate captions into"
            className="max-h-80 overflow-y-auto p-1"
          >
            <Option
              label="Original"
              hint="as spoken"
              selected={state.active === null}
              onPick={() => {
                state.choose(null);
                setOpen(false);
              }}
            />
            {(['india', 'world'] as const).map((group) => {
              const list = shown.filter((l) => l.group === group);
              if (list.length === 0) return null;
              return (
                <li key={group} role="presentation">
                  <p className="px-3 pb-1 pt-3 text-[11px] font-semibold uppercase tracking-wide text-fg-muted">
                    {group === 'india' ? 'Languages of India' : 'World'}
                  </p>
                  <ul role="presentation">
                    {list.map((l) => (
                      <Option
                        key={l.code}
                        label={l.native}
                        hint={l.native === l.name ? '' : l.name}
                        status={statusOf(l.code)}
                        selected={state.active === l.code}
                        onPick={() => {
                          state.choose(l.code);
                          setOpen(false);
                        }}
                      />
                    ))}
                  </ul>
                </li>
              );
            })}
          </ul>
          <p className="border-t border-border-subtle px-3 py-2 text-[11px] leading-snug text-fg-muted">
            Machine translation by NLLB-200 (Meta AI). Made once per language, then kept.
          </p>
        </div>
      )}
    </div>
  );
}

function Option({
  label,
  hint,
  status,
  selected,
  onPick,
}: {
  label: string;
  hint: string;
  status?: string | undefined;
  selected: boolean;
  onPick: () => void;
}) {
  return (
    <li role="option" aria-selected={selected}>
      <button
        type="button"
        onClick={onPick}
        className={cx(
          'flex w-full items-center gap-2 rounded-[var(--radius-sm)] px-3 py-1.5 text-left hover:bg-bg-subtle',
          selected && 'bg-bg-subtle',
        )}
      >
        <span className="min-w-0 flex-1">
          <span className="text-[13px] font-medium">{label}</span>
          {hint !== '' && <span className="ml-2 text-[12px] text-fg-muted">{hint}</span>}
        </span>
        {status === 'ready' && !selected && (
          <span className="text-[11px] text-fg-muted">ready</span>
        )}
        {(status === 'queued' || status === 'running') && <Spinner size={12} />}
        {selected && <IconCheck size={14} className="text-accent" />}
      </button>
    </li>
  );
}
