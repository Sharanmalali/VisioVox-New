'use client';

/**
 * Summary and action items, every point tied to where it was said (ADR-0021).
 *
 * Written on request by a language model running on this server. A model's
 * summary is only as good as a reader's ability to check it, so every item
 * carries play buttons for the transcript lines it cites; the worker has
 * already dropped anything that cited nothing real.
 */

import { useCallback, useEffect, useState } from 'react';
import type { SummaryResponse } from '@visiovox/ts-client';

import { IconCheck, IconPlay, IconSparkles } from '@/components/icons';
import type { PlayerAsideContext } from '@/components/Player';
import { Button, SpeakerDot, Spinner } from '@/components/ui';
import type { DocBlock } from '@/lib/docx';
import { readableTime } from '@/lib/playback/transcript';
import { api } from '@/lib/store';

const POLL_MS = 3000;

interface Cited {
  readonly text: string;
  readonly lines: readonly string[];
}

interface ActionItem {
  readonly owner: number | null;
  readonly task: string;
  readonly due: string | null;
  readonly lines: readonly string[];
}

export interface SummaryContent {
  readonly overview: readonly Cited[];
  readonly key_points: readonly Cited[];
  readonly decisions: readonly Cited[];
  readonly action_items: readonly ActionItem[];
  readonly dropped_ungrounded: number;
  readonly model: string;
  /** Line id -> [start_ms, speaker ordinal]. */
  readonly lines: Readonly<Record<string, readonly [number, number]>>;
}

function asContent(raw: unknown): SummaryContent | null {
  if (typeof raw !== 'object' || raw === null) return null;
  const c = raw as Partial<SummaryContent>;
  if (!Array.isArray(c.overview) || !Array.isArray(c.action_items) || c.lines === undefined)
    return null;
  return raw as SummaryContent;
}

/** The project's summary: load it, ask for it, and follow it while it is written. */
export function useSummary(projectId: string, enabled: boolean) {
  const [state, setState] = useState<SummaryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const s = await api().getSummary(projectId);
    setState(s);
    return s;
  }, [projectId]);

  useEffect(() => {
    if (!enabled) return;
    load().catch(() => undefined);
  }, [enabled, load]);

  const working = state?.status === 'queued' || state?.status === 'running';
  useEffect(() => {
    if (!working) return;
    const timer = setInterval(() => {
      load().catch(() => undefined);
    }, POLL_MS);
    return () => {
      clearInterval(timer);
    };
  }, [working, load]);

  const request = useCallback(async () => {
    setError(null);
    try {
      setState(await api().requestSummary(projectId));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The summary could not be started.');
    }
  }, [projectId]);

  return {
    status: state?.status ?? 'none',
    content: asContent(state?.content),
    failure: state?.status === 'failed' ? (state.error ?? 'The summary failed.') : error,
    working,
    request,
  };
}

export type SummaryState = ReturnType<typeof useSummary>;

/** The summary as Word blocks, for the transcript export. */
export function summaryBlocks(
  content: SummaryContent,
  nameOf: (ordinal: number) => string,
): DocBlock[] {
  const blocks: DocBlock[] = [{ kind: 'heading', text: 'Summary' }];
  for (const s of content.overview) blocks.push({ kind: 'paragraph', text: s.text });
  if (content.key_points.length > 0) {
    blocks.push({ kind: 'heading', text: 'Key points' });
    for (const k of content.key_points) blocks.push({ kind: 'paragraph', text: `• ${k.text}` });
  }
  if (content.decisions.length > 0) {
    blocks.push({ kind: 'heading', text: 'Decisions' });
    for (const d of content.decisions) blocks.push({ kind: 'paragraph', text: `• ${d.text}` });
  }
  if (content.action_items.length > 0) {
    blocks.push({ kind: 'heading', text: 'Action items' });
    blocks.push({
      kind: 'table',
      header: ['Who', 'What', 'When'],
      rows: content.action_items.map((a) => [
        a.owner === null ? '—' : nameOf(a.owner),
        a.task,
        a.due ?? '—',
      ]),
    });
  }
  blocks.push({
    kind: 'meta',
    text: `Written by ${content.model} from the transcript; each point was checked to cite real lines.`,
  });
  return blocks;
}

export function SummaryPanel({
  context,
  summary,
}: {
  context: PlayerAsideContext;
  summary: SummaryState;
}) {
  const { speakers, nameOf, selectTrack, playFrom } = context;
  const { content, status, working, failure, request } = summary;

  const play = (lineId: string) => {
    const at = content?.lines[lineId];
    if (at === undefined) return;
    const speaker = speakers.find((s) => s.ordinal === at[1]);
    if (speaker?.extraction_ok === true) selectTrack(speaker.id);
    playFrom(at[0]);
  };

  const Evidence = ({ lines }: { lines: readonly string[] }) => (
    <span className="ml-1 inline-flex flex-wrap gap-1 align-middle">
      {lines.slice(0, 3).map((id) => {
        const at = content?.lines[id];
        if (at === undefined) return null;
        return (
          <button
            key={id}
            type="button"
            onClick={() => {
              play(id);
            }}
            title={`Play what ${nameOf(at[1])} said at ${readableTime(at[0])}`}
            className="inline-flex h-5 items-center gap-1 rounded-full bg-bg-inset px-1.5 font-mono text-[11px] text-fg-secondary hover:bg-accent-soft hover:text-accent"
          >
            <IconPlay size={9} />
            {readableTime(at[0])}
          </button>
        );
      })}
    </span>
  );

  return (
    <section className="insights" aria-labelledby="summary-title">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <span className="grid size-9 shrink-0 place-items-center rounded-[var(--radius-sm)] bg-accent-soft text-accent">
            <IconSparkles size={18} />
          </span>
          <div>
            <h2 id="summary-title" className="text-[16px] font-semibold">
              Summary and action items
            </h2>
            <p className="text-[13px] text-fg-muted">
              Who agreed to do what, with a play button on every point for where it was said.
            </p>
          </div>
        </div>
        {status !== 'none' && !working && (
          <Button
            size="sm"
            variant="secondary"
            onClick={() => {
              void request();
            }}
          >
            Write again
          </Button>
        )}
      </header>

      {status === 'none' && (
        <div className="flex flex-col items-start gap-3 rounded-[var(--radius-md)] border border-dashed border-border-default p-5">
          <p className="text-[13.5px] text-fg-secondary">
            A language model running on this server reads the transcript and lists the main points,
            decisions and action items - each linked to the moment it was said. Nothing leaves the
            server.
          </p>
          <Button
            size="sm"
            onClick={() => {
              void request();
            }}
          >
            <IconSparkles size={14} />
            Write the summary
          </Button>
        </div>
      )}

      {working && (
        <p className="inline-flex items-center gap-2 text-[13.5px] text-fg-secondary" role="status">
          <Spinner size={14} />
          Reading the transcript… usually under a minute.
        </p>
      )}

      {failure !== null && !working && <p className="text-[13.5px] text-danger">{failure}</p>}

      {content !== null && status === 'ready' && (
        <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
          <div className="flex min-w-0 flex-col gap-5">
            {content.overview.length > 0 && (
              <p className="text-[14.5px] leading-relaxed">
                {content.overview.map((s, i) => (
                  <span key={i}>
                    {s.text}
                    <Evidence lines={s.lines} />{' '}
                  </span>
                ))}
              </p>
            )}
            {content.key_points.length > 0 && (
              <div className="flex flex-col gap-2">
                <h3 className="text-[13px] font-semibold">Key points</h3>
                <ul className="flex list-disc flex-col gap-1.5 pl-5 text-[13.5px] leading-relaxed">
                  {content.key_points.map((k, i) => (
                    <li key={i}>
                      {k.text}
                      <Evidence lines={k.lines} />
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {content.decisions.length > 0 && (
              <div className="flex flex-col gap-2">
                <h3 className="text-[13px] font-semibold">Decisions</h3>
                <ul className="flex flex-col gap-1.5 text-[13.5px] leading-relaxed">
                  {content.decisions.map((d, i) => (
                    <li key={i} className="flex gap-2">
                      <IconCheck size={15} className="mt-0.5 shrink-0 text-success" />
                      <span>
                        {d.text}
                        <Evidence lines={d.lines} />
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>

          <div className="flex min-w-0 flex-col gap-3">
            <h3 className="text-[13px] font-semibold">Action items</h3>
            {content.action_items.length === 0 ? (
              <p className="rounded-[var(--radius-md)] border border-dashed border-border-default px-4 py-5 text-center text-[13px] text-fg-muted">
                Nobody committed to doing anything in this recording.
              </p>
            ) : (
              <ol className="flex flex-col divide-y divide-border-subtle overflow-hidden rounded-[var(--radius-md)] border border-border-subtle">
                {content.action_items.map((a, i) => (
                  <li key={i} className="flex flex-col gap-1.5 px-4 py-3">
                    <span className="flex flex-wrap items-center gap-2 text-[12.5px]">
                      {a.owner !== null ? (
                        <span className="inline-flex items-center gap-1.5 font-semibold">
                          <SpeakerDot index={a.owner} size={8} />
                          {nameOf(a.owner)}
                        </span>
                      ) : (
                        <span className="text-fg-muted">Owner not stated</span>
                      )}
                      {a.due !== null && (
                        <span className="rounded-full bg-warning-soft px-2 py-0.5 text-[11px] font-medium text-warning">
                          {a.due}
                        </span>
                      )}
                    </span>
                    <span className="text-[13.5px] leading-snug">
                      {a.task}
                      <Evidence lines={a.lines} />
                    </span>
                  </li>
                ))}
              </ol>
            )}
          </div>

          <p className="text-[11.5px] leading-relaxed text-fg-muted xl:col-span-2">
            Written by {content.model} running on this server, from the transcript. Each point links
            to the lines it rests on - listen before relying on it.
            {content.dropped_ungrounded > 0 &&
              ` ${String(content.dropped_ungrounded)} point${content.dropped_ungrounded === 1 ? '' : 's'} that cited nothing in the transcript ${content.dropped_ungrounded === 1 ? 'was' : 'were'} left out.`}
          </p>
        </div>
      )}
    </section>
  );
}
