'use client';

/**
 * The conversation as text: who said what, in order, following the playback.
 *
 * Built on the player's clock (`PlayerAsideContext`), so the highlighted line
 * is the line being heard - including two lines at once when people overlap.
 * Clicking a line plays it in that person's own voice: the transcript is a way
 * to navigate the separation, not only to read it.
 *
 * Re-rendered every animation frame by the player, so the list itself is
 * memoised on what actually changes (the active lines and word, the query, the
 * filter) rather than on the media time.
 */

import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { KeyboardEvent as ReactKeyboardEvent } from 'react';

import {
  IconCheck,
  IconChevronDown,
  IconChevronUp,
  IconCopy,
  IconCrosshair,
  IconDownload,
  IconFileText,
  IconPencil,
  IconScissors,
  IconSearch,
  IconX,
} from '@/components/icons';
import type { PlayerAsideContext } from '@/components/Player';
import { SpeakerDot, Spinner, cx } from '@/components/ui';
import type { DocBlock } from '@/lib/docx';
import type { TranscriptLine } from '@/lib/playback/transcript';
import {
  activeLines,
  fileStem,
  latestLine,
  readableTime,
  searchLines,
  splitMatches,
  toPlainText,
  toSrt,
  toVtt,
  turns,
} from '@/lib/playback/transcript';

/** Speaker hues for documents, matching --spk-1..4 in light mode. */
const DOC_COLORS = ['1F6FC0', '1E8A4C', 'A86400', 'B8327A'];

export function docColor(ordinal: number): string {
  return DOC_COLORS[(ordinal - 1) % DOC_COLORS.length] ?? '0B0D12';
}

export interface TranscriptPanelProps {
  readonly context: PlayerAsideContext;
  readonly title: string;
  /** Present for the owner; absent on a share page, where names are read-only. */
  readonly onRename?: ((ordinal: number, name: string | null) => Promise<void>) | undefined;
  /** Extra blocks for the Word export, placed before the transcript. */
  readonly docxPreamble?: () => readonly DocBlock[];
  /**
   * Line key -> translated text. When present the transcript reads in that
   * language - search and exports included - with the original under each line.
   */
  readonly translated?: ReadonlyMap<string, string> | null;
  /** Controls beside Export (the language picker). */
  readonly headerExtra?: React.ReactNode;
  /** A note above the lines (e.g. "Translating into Hindi"). */
  readonly notice?: React.ReactNode;
  /** Present when lines can be clipped for download (the owner's page). */
  readonly onClip?: ((line: TranscriptLine) => void) | undefined;
}

function download(name: string, body: BlobPart, type: string): void {
  const url = URL.createObjectURL(new Blob([body], { type }));
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => {
    URL.revokeObjectURL(url);
  }, 1000);
}

export function TranscriptPanel({
  context,
  title,
  onRename,
  docxPreamble,
  translated,
  headerExtra,
  notice,
  onClip,
}: TranscriptPanelProps) {
  const { lines: originalLines, captionsReady, mediaMs, speakers, nameOf, playLine } = context;
  // Translated lines keep their timing and drop word timings (word order
  // changes); everything below - search, follow, exports - reads these.
  const lines = useMemo(
    () =>
      translated === undefined || translated === null
        ? originalLines
        : originalLines.map((l) => {
            const text = translated.get(l.key);
            return text === undefined ? l : { ...l, text, words: [] };
          }),
    [originalLines, translated],
  );
  const isTranslated = translated !== undefined && translated !== null;

  const [query, setQuery] = useState('');
  const [hitCursor, setHitCursor] = useState(0);
  const [hidden, setHidden] = useState<ReadonlySet<number>>(new Set());
  const [follow, setFollow] = useState(true);
  const scrollerRef = useRef<HTMLDivElement | null>(null);

  const visible = useMemo(
    () => lines.map((line, i) => ({ line, i })).filter(({ line }) => !hidden.has(line.ordinal)),
    [lines, hidden],
  );

  const hits = useMemo(() => {
    const visibleLines = visible.map((v) => v.line);
    return searchLines(visibleLines, query).map((k) => visible[k]?.i ?? -1);
  }, [visible, query]);

  useEffect(() => {
    setHitCursor(0);
  }, [query]);

  // What is being spoken now. A string signature so the memoised list only
  // re-renders when the highlight actually moves, not on every frame.
  const active = activeLines(lines, mediaMs);
  const latest = latestLine(lines, mediaMs);
  let activeWord = -1;
  const firstActive = active[0];
  if (firstActive !== undefined) {
    const words = lines[firstActive]?.words ?? [];
    activeWord = words.findIndex((w) => mediaMs >= w.start_ms && mediaMs < w.end_ms);
  }
  const signature = `${active.join(',')}|${String(activeWord)}|${String(latest)}`;

  // ---------------------------------------------------------- follow along

  const scrollToLine = useCallback((index: number, smooth: boolean) => {
    const scroller = scrollerRef.current;
    if (scroller === null) return;
    const row = scroller.querySelector<HTMLElement>(`[data-line="${String(index)}"]`);
    if (row === null) return;
    const top = row.offsetTop - scroller.clientHeight / 3;
    scroller.scrollTo({ top: Math.max(0, top), behavior: smooth ? 'smooth' : 'auto' });
  }, []);

  const followTarget = firstActive ?? latest;
  useEffect(() => {
    if (!follow || followTarget < 0) return;
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    scrollToLine(followTarget, !reduced);
  }, [follow, followTarget, scrollToLine]);

  // A person scrolling the list is reading, not following - stop pulling the
  // list out from under them until they ask for it back.
  const stopFollowing = useCallback(() => {
    setFollow(false);
  }, []);

  // ---------------------------------------------------------------- search

  const gotoHit = useCallback(
    (step: number) => {
      if (hits.length === 0) return;
      const next = (hitCursor + step + hits.length) % hits.length;
      setHitCursor(next);
      setFollow(false);
      const target = hits[next];
      if (target !== undefined) scrollToLine(target, true);
    },
    [hits, hitCursor, scrollToLine],
  );

  useEffect(() => {
    const first = hits[0];
    if (query.trim() !== '' && first !== undefined) {
      setFollow(false);
      scrollToLine(first, true);
    }
  }, [hits, query, scrollToLine]);

  const onSearchKey = (e: ReactKeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      gotoHit(e.shiftKey ? -1 : 1);
    } else if (e.key === 'Escape') {
      setQuery('');
    }
  };

  // --------------------------------------------------------------- exports

  const exportAs = useCallback(
    async (kind: 'docx' | 'srt' | 'vtt' | 'txt' | 'copy') => {
      const stem = fileStem(title);
      if (kind === 'srt') download(`${stem}.srt`, toSrt(lines, nameOf), 'application/x-subrip');
      else if (kind === 'vtt') download(`${stem}.vtt`, toVtt(lines, nameOf), 'text/vtt');
      else if (kind === 'txt')
        download(`${stem}.txt`, toPlainText(lines, nameOf, title), 'text/plain;charset=utf-8');
      else if (kind === 'copy')
        await navigator.clipboard.writeText(toPlainText(lines, nameOf, title));
      else {
        const { buildDocx, DOCX_MIME } = await import('@/lib/docx');
        const exported = new Date().toLocaleDateString(undefined, {
          day: 'numeric',
          month: 'long',
          year: 'numeric',
        });
        const last = lines.reduce((m, l) => Math.max(m, l.end_ms), 0);
        const blocks: DocBlock[] = [
          { kind: 'title', text: title },
          {
            kind: 'meta',
            text: `Transcript · ${readableTime(last)} · ${String(speakers.length)} speakers · exported ${exported} from VisioVox`,
          },
          ...(docxPreamble?.() ?? []),
          { kind: 'heading', text: 'Transcript' },
          ...turns(lines).map((t): DocBlock => ({
            kind: 'turn',
            speaker: nameOf(t.ordinal),
            color: docColor(t.ordinal),
            time: readableTime(t.start_ms),
            text: t.text,
          })),
        ];
        const bytes = buildDocx(blocks);
        download(`${stem}.docx`, bytes, DOCX_MIME);
      }
    },
    [lines, nameOf, title, speakers.length, docxPreamble],
  );

  // ------------------------------------------------------------------ list

  const activeSet = useMemo(
    () => new Set(signature.split('|')[0]?.split(',').filter(Boolean).map(Number)),
    [signature],
  );
  const currentHit = hits[hitCursor];

  const list = useMemo(
    () =>
      visible.map(({ line, i }) => (
        <Line
          key={line.key}
          index={i}
          line={line}
          name={nameOf(line.ordinal)}
          active={activeSet.has(i)}
          activeWord={i === firstActive ? activeWord : -1}
          query={query}
          current={i === currentHit}
          original={isTranslated ? originalLines[i]?.text : undefined}
          onPlay={playLine}
          onClip={onClip}
        />
      )),
    // `signature` stands in for the per-frame values it encodes.
    [visible, nameOf, signature, query, currentHit, playLine, isTranslated, originalLines, onClip],
  );

  const toggleSpeaker = (ordinal: number) => {
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(ordinal)) next.delete(ordinal);
      else next.add(ordinal);
      // Hiding everyone reads as a bug; keep at least one person visible.
      return next.size >= speakers.length ? prev : next;
    });
  };

  return (
    <section className="transcript" aria-label="Transcript">
      <header className="flex flex-col gap-3 border-b border-border-subtle p-4">
        <div className="flex items-center justify-between gap-3">
          <div className="flex min-w-0 items-baseline gap-2">
            <h2 className="text-[15px] font-semibold">Transcript</h2>
            {captionsReady && lines.length > 0 && headerExtra === undefined && (
              <span className="text-[12px] text-fg-muted tabular">
                {String(lines.length)} lines
              </span>
            )}
          </div>
          <div className="flex items-center gap-1.5">
            {headerExtra}
            <ExportMenu disabled={lines.length === 0} onExport={exportAs} />
          </div>
        </div>

        <div className="relative">
          <IconSearch
            size={15}
            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-fg-muted"
          />
          <input
            type="search"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
            }}
            onKeyDown={onSearchKey}
            placeholder="Search what was said"
            aria-label="Search the transcript"
            className="h-9 w-full rounded-[var(--radius-sm)] border border-border-default bg-bg-surface pl-9 pr-28 text-[13.5px] outline-none placeholder:text-fg-muted focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/25"
          />
          {query.trim() !== '' && (
            <div className="absolute right-1.5 top-1/2 flex -translate-y-1/2 items-center gap-0.5">
              <span className="px-1.5 text-[12px] text-fg-muted tabular" aria-live="polite">
                {hits.length === 0
                  ? 'No match'
                  : `${String(hitCursor + 1)} of ${String(hits.length)}`}
              </span>
              <IconButton
                label="Previous match"
                onClick={() => {
                  gotoHit(-1);
                }}
                disabled={hits.length === 0}
              >
                <IconChevronUp size={15} />
              </IconButton>
              <IconButton
                label="Next match"
                onClick={() => {
                  gotoHit(1);
                }}
                disabled={hits.length === 0}
              >
                <IconChevronDown size={15} />
              </IconButton>
            </div>
          )}
        </div>

        {speakers.length > 0 && (
          <ul className="flex flex-wrap gap-1.5" aria-label="Speakers">
            {speakers.map((s) => (
              <li key={s.id}>
                <SpeakerChip
                  ordinal={s.ordinal}
                  name={nameOf(s.ordinal)}
                  shown={!hidden.has(s.ordinal)}
                  onToggle={() => {
                    toggleSpeaker(s.ordinal);
                  }}
                  onRename={onRename}
                />
              </li>
            ))}
          </ul>
        )}
      </header>

      {notice !== undefined && notice !== null && (
        <div className="border-b border-border-subtle bg-accent-soft px-4 py-2 text-[12.5px] text-accent">
          {notice}
        </div>
      )}

      <div className="relative min-h-0 flex-1">
        <div
          ref={scrollerRef}
          className="transcript-scroll"
          onWheel={stopFollowing}
          onTouchMove={stopFollowing}
          tabIndex={-1}
        >
          {!captionsReady ? (
            <div className="flex flex-col gap-4 p-4" aria-busy="true">
              {[0, 1, 2, 3, 4].map((i) => (
                <div key={i} className="flex flex-col gap-2">
                  <span className="skeleton h-3 w-24" />
                  <span className="skeleton h-3.5 w-full" />
                  <span className="skeleton h-3.5 w-2/3" />
                </div>
              ))}
            </div>
          ) : lines.length === 0 ? (
            <div className="flex h-full flex-col items-center justify-center gap-2 p-8 text-center">
              <IconFileText size={22} className="text-fg-muted" />
              <p className="text-[14px] font-medium">No transcript for this recording</p>
              <p className="max-w-[28ch] text-[13px] text-fg-muted">
                Nobody was heard clearly enough to caption. The separated audio still plays.
              </p>
            </div>
          ) : (
            <ol className="flex flex-col py-2">{list}</ol>
          )}
        </div>

        {!follow && lines.length > 0 && (
          <button
            type="button"
            onClick={() => {
              setFollow(true);
            }}
            className="absolute bottom-3 left-1/2 inline-flex -translate-x-1/2 items-center gap-1.5 rounded-full border border-border-default bg-bg-surface px-3 py-1.5 text-[12.5px] font-medium shadow-md hover:bg-bg-subtle"
          >
            <IconCrosshair size={14} />
            Follow along
          </button>
        )}
      </div>
    </section>
  );
}

// -------------------------------------------------------------------- line --

interface LineProps {
  readonly index: number;
  readonly line: TranscriptLine;
  readonly name: string;
  readonly active: boolean;
  readonly activeWord: number;
  readonly query: string;
  readonly current: boolean;
  /** The words as spoken, when the line is shown translated. */
  readonly original?: string | undefined;
  readonly onClip?: ((line: TranscriptLine) => void) | undefined;
  readonly onPlay: (line: TranscriptLine) => void;
}

const Line = memo(function Line({
  index,
  line,
  name,
  active,
  activeWord,
  query,
  current,
  original,
  onPlay,
  onClip,
}: LineProps) {
  const slot = ((line.ordinal - 1) % 4) + 1;
  let body: React.ReactNode;
  if (active && activeWord >= 0 && query.trim() === '' && line.words.length > 0) {
    body = line.words.map((w, k) => (
      <span
        key={`${String(w.start_ms)}:${String(k)}`}
        className={k === activeWord ? 'tw-on' : undefined}
      >
        {w.text}
      </span>
    ));
  } else if (query.trim() !== '') {
    body = splitMatches(line.text, query).map((part, k) =>
      part.match ? (
        <mark key={k} className={current ? 'tm-current' : undefined}>
          {part.text}
        </mark>
      ) : (
        <span key={k}>{part.text}</span>
      ),
    );
  } else {
    body = line.text;
  }

  return (
    <li data-line={index} className="group relative">
      <button
        type="button"
        className={cx('tline', active && 'tline-on')}
        style={{ ['--tline-color' as string]: `var(--spk-${String(slot)})` }}
        onClick={() => {
          onPlay(line);
        }}
        aria-current={active || undefined}
        title={`Play ${name} from ${readableTime(line.start_ms)}`}
      >
        <span className="tline-meta">
          <span className="tline-name">{name}</span>
          <span className="tline-time">{readableTime(line.start_ms)}</span>
        </span>
        <span className="tline-text">{body}</span>
        {original !== undefined && original !== line.text && (
          <span className="tline-original">{original}</span>
        )}
      </button>
      {onClip !== undefined && (
        <button
          type="button"
          onClick={() => {
            onClip(line);
          }}
          aria-label={`Clip ${name} at ${readableTime(line.start_ms)} for download`}
          title="Clip this line for download (again on a later line to stretch the clip)"
          className="absolute right-2 top-1.5 grid size-7 place-items-center rounded-[var(--radius-sm)] bg-bg-surface text-fg-muted opacity-0 shadow-xs transition-opacity hover:text-accent focus-visible:opacity-100 group-hover:opacity-100"
        >
          <IconScissors size={14} />
        </button>
      )}
    </li>
  );
});

// ------------------------------------------------------------ speaker chip --

function SpeakerChip({
  ordinal,
  name,
  shown,
  onToggle,
  onRename,
}: {
  ordinal: number;
  name: string;
  shown: boolean;
  onToggle: () => void;
  onRename?: ((ordinal: number, name: string | null) => Promise<void>) | undefined;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(name);
  const [saving, setSaving] = useState(false);
  const [failed, setFailed] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (editing) inputRef.current?.select();
  }, [editing]);

  const save = async () => {
    if (onRename === undefined) return;
    const next = draft.trim();
    if (next === name) {
      setEditing(false);
      return;
    }
    setSaving(true);
    setFailed(false);
    try {
      await onRename(ordinal, next === '' ? null : next);
      setEditing(false);
    } catch {
      setFailed(true);
    } finally {
      setSaving(false);
    }
  };

  if (editing) {
    return (
      <form
        className="inline-flex h-8 items-center gap-1 rounded-full border border-accent bg-bg-surface pl-2.5 pr-1 ring-2 ring-accent/20"
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        <SpeakerDot index={ordinal} size={8} />
        <input
          ref={inputRef}
          value={draft}
          maxLength={60}
          onChange={(e) => {
            setDraft(e.target.value);
          }}
          onKeyDown={(e) => {
            if (e.key === 'Escape') {
              setDraft(name);
              setEditing(false);
            }
          }}
          aria-label={`Name for speaker ${String(ordinal)}`}
          aria-invalid={failed || undefined}
          placeholder={`Speaker ${String(ordinal)}`}
          className="w-32 bg-transparent text-[12.5px] font-medium outline-none"
        />
        <button
          type="submit"
          aria-label="Save name"
          className="grid size-6 place-items-center rounded-full text-accent hover:bg-accent/10"
        >
          {saving ? <Spinner size={12} /> : <IconCheck size={14} />}
        </button>
        <button
          type="button"
          aria-label="Cancel"
          onClick={() => {
            setDraft(name);
            setEditing(false);
          }}
          className="grid size-6 place-items-center rounded-full text-fg-muted hover:bg-bg-subtle"
        >
          <IconX size={13} />
        </button>
      </form>
    );
  }

  return (
    <span
      className={cx(
        'inline-flex h-8 items-center rounded-full border text-[12.5px] font-medium transition-colors',
        shown
          ? 'border-border-default bg-bg-surface text-fg-primary'
          : 'border-dashed border-border-default bg-transparent text-fg-muted',
      )}
    >
      <button
        type="button"
        onClick={onToggle}
        aria-pressed={shown}
        title={shown ? `Hide ${name}'s lines` : `Show ${name}'s lines`}
        className={cx(
          'inline-flex h-full items-center gap-1.5 rounded-full pl-2.5',
          onRename !== undefined ? 'pr-1.5' : 'pr-3',
        )}
      >
        <span className={shown ? undefined : 'opacity-40'}>
          <SpeakerDot index={ordinal} size={8} />
        </span>
        <span className={cx('max-w-[14ch] truncate', !shown && 'line-through')}>{name}</span>
      </button>
      {onRename !== undefined && (
        <button
          type="button"
          onClick={() => {
            setDraft(name);
            setEditing(true);
          }}
          aria-label={`Rename ${name}`}
          title="Rename"
          className="mr-1 grid size-6 place-items-center rounded-full text-fg-muted hover:bg-bg-subtle hover:text-fg-primary"
        >
          <IconPencil size={12} />
        </button>
      )}
    </span>
  );
}

// ------------------------------------------------------------- export menu --

const EXPORTS = [
  { kind: 'docx', label: 'Word document', hint: '.docx · formatted, with names' },
  { kind: 'srt', label: 'Subtitles', hint: '.srt · YouTube, VLC, Premiere' },
  { kind: 'vtt', label: 'Web captions', hint: '.vtt · HTML video players' },
  { kind: 'txt', label: 'Plain text', hint: '.txt · minutes-style' },
] as const;

function ExportMenu({
  disabled,
  onExport,
}: {
  disabled: boolean;
  onExport: (kind: 'docx' | 'srt' | 'vtt' | 'txt' | 'copy') => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
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

  return (
    <div ref={rootRef} className="relative flex items-center gap-1">
      <IconButton
        label={copied ? 'Copied' : 'Copy transcript'}
        disabled={disabled}
        onClick={() => {
          void onExport('copy').then(() => {
            setCopied(true);
            setTimeout(() => {
              setCopied(false);
            }, 1600);
          });
        }}
      >
        {copied ? <IconCheck size={15} className="text-success" /> : <IconCopy size={15} />}
      </IconButton>
      <button
        type="button"
        disabled={disabled}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => {
          setOpen((o) => !o);
        }}
        className="inline-flex h-8 items-center gap-1.5 rounded-[var(--radius-sm)] border border-border-default bg-bg-surface px-2.5 text-[12.5px] font-medium hover:bg-bg-subtle disabled:opacity-50"
      >
        <IconDownload size={14} />
        Export
        <IconChevronDown size={13} className="text-fg-muted" />
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 top-[calc(100%+6px)] z-20 w-64 overflow-hidden rounded-[var(--radius-md)] border border-border-subtle bg-bg-surface p-1 shadow-lg"
        >
          {EXPORTS.map((item) => (
            <button
              key={item.kind}
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                void onExport(item.kind);
              }}
              className="flex w-full flex-col items-start rounded-[var(--radius-sm)] px-3 py-2 text-left hover:bg-bg-subtle focus-visible:bg-bg-subtle"
            >
              <span className="text-[13px] font-medium">{item.label}</span>
              <span className="text-[11.5px] text-fg-muted">{item.hint}</span>
            </button>
          ))}
          <p className="border-t border-border-subtle px-3 py-2 text-[11.5px] text-fg-muted">
            Exports include every speaker, with the names shown here.
          </p>
        </div>
      )}
    </div>
  );
}

function IconButton({
  label,
  onClick,
  disabled,
  children,
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      disabled={disabled}
      className="grid size-8 place-items-center rounded-[var(--radius-sm)] text-fg-secondary hover:bg-bg-subtle hover:text-fg-primary disabled:opacity-40"
    >
      {children}
    </button>
  );
}
