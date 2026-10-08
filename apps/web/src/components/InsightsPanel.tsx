'use client';

/**
 * Conversation insights: who talked, how much, and when people talked over
 * each other - measured from each person's separated track.
 *
 * Everything is derived from the transcript's word timings with the same
 * algorithm the AMI benchmark scores (lib/playback/insights.ts), and the panel
 * says what it measures and what it cannot see. The timeline and the list of
 * moments are ways into the recording: clicking either plays that moment.
 */

import { memo, useMemo, useState } from 'react';

import { IconChart, IconChevronDown, IconInfo, IconPlay } from '@/components/icons';
import type { PlayerAsideContext } from '@/components/Player';
import { SpeakerDot, cx } from '@/components/ui';
import type { DocBlock } from '@/lib/docx';
import type { ConversationEvent, Insights, SpeakerStats } from '@/lib/playback/insights';
import { computeInsights, intervalsFromLines } from '@/lib/playback/insights';
import { MIXED_TRACK } from '@/lib/playback/manifest';
import type { TranscriptLine } from '@/lib/playback/transcript';
import { readableTime } from '@/lib/playback/transcript';

/** How far before a moment playback starts, so the lead-in is heard. */
const LEAD_IN_MS = 1500;

/**
 * Face-anchored diarization when the project has it - it found overlap and
 * interruptions far more reliably on AMI (docs/29 section 9) - and the
 * captions' word timings otherwise. Word counts always come from the captions.
 */
export function insightsFor(
  lines: readonly TranscriptLine[],
  ordinals: readonly number[],
  durationMs: number,
  activity: PlayerAsideContext['activity'],
): Insights {
  const words = intervalsFromLines(lines, ordinals);
  if (activity === null) return computeInsights(words, durationMs);
  const counts = new Map([...words].map(([o, ws]) => [o, ws.length]));
  return computeInsights(activity, durationMs, counts);
}

function seconds(ms: number): string {
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(s < 10 ? 1 : 0)} s`;
  return readableTime(ms);
}

function percent(x: number): string {
  return `${String(Math.round(x * 100))}%`;
}

/** Words per minute while actually speaking. */
function pace(s: SpeakerStats): string {
  if (s.talk_ms < 5000 || s.words === 0) return '—';
  return String(Math.round(s.words / (s.talk_ms / 60_000)));
}

/** The table the Word export puts above the transcript. */
export function insightBlocks(ins: Insights, nameOf: (o: number) => string): DocBlock[] {
  const rows = [...ins.speakers.values()]
    .sort((a, b) => b.talk_ms - a.talk_ms)
    .map((s) => [
      nameOf(s.speaker),
      `${seconds(s.talk_ms)} (${percent(s.share)})`,
      String(s.turns),
      pace(s),
      String(s.interruptions_made),
      String(s.interruptions_received),
    ]);
  return [
    { kind: 'heading', text: 'Who spoke' },
    {
      kind: 'table',
      header: [
        'Person',
        'Talk time',
        'Turns',
        'Words/min',
        'Interrupted others',
        'Was interrupted',
      ],
      rows,
    },
    {
      kind: 'meta',
      text: `People talked over each other for ${seconds(ins.overlap_ms)} in total. Measured from each person's separated, captioned speech; speech that could not be captioned is not counted.`,
    },
  ];
}

export function InsightsPanel({
  context,
  durationMs,
}: {
  context: PlayerAsideContext;
  durationMs: number;
}) {
  const { lines, captionsReady, speakers, nameOf, mediaMs, activity } = context;
  const ordinals = useMemo(() => speakers.map((s) => s.ordinal), [speakers]);
  const ins = useMemo(
    () => insightsFor(lines, ordinals, durationMs, activity),
    [lines, ordinals, durationMs, activity],
  );

  if (!captionsReady) {
    return (
      <section className="insights" aria-busy="true" aria-label="Conversation insights">
        <span className="skeleton h-5 w-56" />
        <span className="skeleton h-24 w-full" />
      </section>
    );
  }
  if (ins.speech_ms === 0) return null;

  const stats = [...ins.speakers.values()].sort((a, b) => a.speaker - b.speaker);
  const interruptions = ins.events.filter((e) => e.kind === 'interruption').length;

  return (
    <section className="insights" aria-labelledby="insights-title">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <span className="grid size-9 shrink-0 place-items-center rounded-[var(--radius-sm)] bg-accent-soft text-accent">
            <IconChart size={18} />
          </span>
          <div>
            <h2 id="insights-title" className="text-[16px] font-semibold">
              Conversation insights
            </h2>
            <p className="text-[13px] text-fg-muted">
              Who spoke, for how long, and when people talked over each other.
            </p>
          </div>
        </div>
        <Method diarized={activity !== null} />
      </header>

      <dl className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat
          label="Speech"
          value={seconds(ins.speech_ms)}
          note={`of ${readableTime(durationMs)}`}
        />
        <Stat
          label="Talked over each other"
          value={seconds(ins.overlap_ms)}
          note={ins.speech_ms > 0 ? `${percent(ins.overlap_ms / ins.speech_ms)} of speech` : ''}
        />
        <Stat label="Interruptions" value={String(interruptions)} note="someone took the floor" />
        <Stat
          label="Silence"
          value={seconds(ins.silence_ms)}
          note={durationMs > 0 ? `${percent(ins.silence_ms / durationMs)} of the recording` : ''}
        />
      </dl>

      <ShareBar stats={stats} nameOf={nameOf} />

      <Timeline ins={ins} ordinals={ordinals} nameOf={nameOf} context={context} mediaMs={mediaMs} />

      <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <PeopleTable stats={stats} nameOf={nameOf} />
        <Moments events={ins.events} nameOf={nameOf} context={context} />
      </div>
    </section>
  );
}

function Stat({ label, value, note }: { label: string; value: string; note: string }) {
  return (
    <div className="flex flex-col gap-0.5 rounded-[var(--radius-md)] border border-border-subtle bg-bg-subtle px-4 py-3">
      <dt className="text-[12px] font-medium text-fg-muted">{label}</dt>
      <dd className="text-[20px] font-semibold tracking-[-0.01em] tabular">{value}</dd>
      <dd className="text-[12px] text-fg-muted">{note}</dd>
    </div>
  );
}

function Method({ diarized }: { diarized: boolean }) {
  return (
    <details className="group relative">
      <summary className="inline-flex cursor-pointer list-none items-center gap-1.5 rounded-[var(--radius-sm)] px-2 py-1 text-[12.5px] font-medium text-fg-secondary hover:bg-bg-subtle [&::-webkit-details-marker]:hidden">
        <IconInfo size={14} />
        How this is measured
        <IconChevronDown size={13} className="transition-transform group-open:rotate-180" />
      </summary>
      <div className="absolute right-0 z-20 mt-2 w-[min(26rem,calc(100vw-3rem))] rounded-[var(--radius-md)] border border-border-subtle bg-bg-surface p-4 text-[12.5px] leading-relaxed text-fg-secondary shadow-lg">
        <p>
          {diarized
            ? 'When each person spoke comes from a voice-activity diarizer run on the original audio; who each voice belongs to comes from matching it to the speech separated for each face.'
            : 'From the word timings in each person’s separated track.'}{' '}
          Speech less than half a second apart counts as one stretch.
        </p>
        <ul className="mt-2 flex list-disc flex-col gap-1 pl-4">
          <li>
            <strong className="text-fg-primary">Interruption:</strong> someone starts while another
            person is talking, and that person stops within 2 seconds.
          </li>
          <li>
            <strong className="text-fg-primary">Talk-over:</strong> both keep talking.
          </li>
          <li>
            <strong className="text-fg-primary">Backchannel:</strong> a short “yeah” or “right”
            inside someone else’s speech. Not counted as a turn.
          </li>
        </ul>
        <p className="mt-2">
          Speech spoken while a face was off screen may be credited to the wrong person. Tested on
          recorded meetings, this found most moments people talked over each other but only about
          one interruption in seven - treat the numbers as a guide, and the moments as places to
          listen.
        </p>
      </div>
    </details>
  );
}

function ShareBar({
  stats,
  nameOf,
}: {
  stats: readonly SpeakerStats[];
  nameOf: (o: number) => string;
}) {
  return (
    <div className="flex flex-col gap-3">
      <h3 className="text-[13px] font-semibold">Share of talk time</h3>
      <div
        className="flex h-3 w-full overflow-hidden rounded-full bg-bg-inset"
        role="img"
        aria-label={stats.map((s) => `${nameOf(s.speaker)} ${percent(s.share)}`).join(', ')}
      >
        {stats.map((s) =>
          s.share > 0 ? (
            <span
              key={s.speaker}
              style={{
                width: `${String(s.share * 100)}%`,
                background: `var(--spk-${String(((s.speaker - 1) % 4) + 1)})`,
              }}
              className="h-full border-r-2 border-bg-surface last:border-r-0"
            />
          ) : null,
        )}
      </div>
      <ul className="flex flex-wrap gap-x-5 gap-y-1.5">
        {stats.map((s) => (
          <li key={s.speaker} className="inline-flex items-center gap-2 text-[13px]">
            <SpeakerDot index={s.speaker} size={9} />
            <span className="font-medium">{nameOf(s.speaker)}</span>
            <span className="text-fg-muted tabular">
              {percent(s.share)} · {seconds(s.talk_ms)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------- timeline --

const TimelineLanes = memo(function TimelineLanes({
  ins,
  ordinals,
  nameOf,
}: {
  ins: Insights;
  ordinals: readonly number[];
  nameOf: (o: number) => string;
}) {
  const d = Math.max(ins.duration_ms, 1);
  const pct = (ms: number): string => `${String((ms / d) * 100)}%`;
  return (
    <>
      <div className="timeline-row">
        <span className="timeline-label text-fg-muted">Overlap</span>
        <div className="timeline-lane">
          {ins.overlaps.map(([a, b]) => (
            <span
              key={a}
              className="timeline-overlap"
              style={{ left: pct(a), width: pct(b - a) }}
            />
          ))}
        </div>
      </div>
      {ordinals.map((o) => (
        <div key={o} className="timeline-row">
          <span className="timeline-label">
            <SpeakerDot index={o} size={8} />
            <span className="truncate">{nameOf(o)}</span>
          </span>
          <div className="timeline-lane">
            {ins.spurts
              .filter((s) => s.speaker === o)
              .map((s) => (
                <span
                  key={s.start}
                  className="timeline-spurt"
                  style={{
                    left: pct(s.start),
                    width: pct(s.end - s.start),
                    background: `var(--spk-${String(((o - 1) % 4) + 1)})`,
                  }}
                />
              ))}
            {ins.events
              .filter((e) => e.kind === 'interruption' && e.by === o)
              .map((e) => (
                <span
                  key={`i${String(e.at)}`}
                  className="timeline-mark"
                  style={{ left: pct(e.at) }}
                  title={`${nameOf(e.by)} interrupted ${nameOf(e.of)} at ${readableTime(e.at)}`}
                />
              ))}
          </div>
        </div>
      ))}
    </>
  );
});

function Timeline({
  ins,
  ordinals,
  nameOf,
  context,
  mediaMs,
}: {
  ins: Insights;
  ordinals: readonly number[];
  nameOf: (o: number) => string;
  context: PlayerAsideContext;
  mediaMs: number;
}) {
  const d = Math.max(ins.duration_ms, 1);
  const ticks = useMemo(() => {
    const step =
      [5, 10, 15, 30, 60, 120, 300, 600, 900, 1800].find((s) => d / 1000 / s <= 8) ?? 3600;
    const out: number[] = [];
    for (let t = 0; t <= d; t += step * 1000) out.push(t);
    return out;
  }, [d]);

  const seekFromPointer = (e: React.MouseEvent<HTMLDivElement>) => {
    // The playhead track spans exactly the lanes, so it maps x to time.
    const track = e.currentTarget.querySelector('.timeline-playhead-track');
    if (track === null) return;
    const box = track.getBoundingClientRect();
    if (box.width <= 0 || e.clientX < box.left) return;
    context.playFrom(Math.min(d, ((e.clientX - box.left) / box.width) * d));
  };

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="text-[13px] font-semibold">Who spoke when</h3>
        <span className="text-[12px] text-fg-muted">Click anywhere to play from there</span>
      </div>
      <div className="timeline" onClick={seekFromPointer} role="presentation">
        <TimelineLanes ins={ins} ordinals={ordinals} nameOf={nameOf} />
        <div className="timeline-row" aria-hidden="true">
          <span className="timeline-label" />
          <div className="timeline-axis">
            {ticks.map((t) => (
              <span key={t} style={{ left: `${String((t / d) * 100)}%` }}>
                {readableTime(t)}
              </span>
            ))}
          </div>
        </div>
        <div className="timeline-playhead-track" aria-hidden="true">
          <span
            className="timeline-playhead"
            style={{ left: `${String(Math.min(100, (mediaMs / d) * 100))}%` }}
          />
        </div>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ people --

function PeopleTable({
  stats,
  nameOf,
}: {
  stats: readonly SpeakerStats[];
  nameOf: (o: number) => string;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-3">
      <h3 className="text-[13px] font-semibold">Per person</h3>
      <div className="overflow-x-auto rounded-[var(--radius-md)] border border-border-subtle">
        <table className="insights-table">
          <thead>
            <tr>
              <th>Person</th>
              <th>Talk time</th>
              <th title="Times this person took the floor">Turns</th>
              <th title="Longest stretch without someone else taking over">Longest</th>
              <th title="Words per minute while speaking">Pace</th>
              <th title="Interrupted someone / was interrupted">Interrupted · by</th>
            </tr>
          </thead>
          <tbody>
            {stats.map((s) => (
              <tr key={s.speaker}>
                <td>
                  <span className="inline-flex items-center gap-2 font-medium">
                    <SpeakerDot index={s.speaker} size={8} />
                    <span className="max-w-[16ch] truncate">{nameOf(s.speaker)}</span>
                  </span>
                </td>
                <td className="tabular">
                  {seconds(s.talk_ms)} <span className="text-fg-muted">({percent(s.share)})</span>
                </td>
                <td className="tabular">{s.turns}</td>
                <td className="tabular">
                  {s.longest_turn_ms > 0 ? seconds(s.longest_turn_ms) : '—'}
                </td>
                <td className="tabular">
                  {pace(s)}
                  {pace(s) !== '—' && <span className="text-fg-muted"> wpm</span>}
                </td>
                <td className="tabular">
                  {s.interruptions_made} · {s.interruptions_received}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ----------------------------------------------------------------- moments --

function Moments({
  events,
  nameOf,
  context,
}: {
  events: readonly ConversationEvent[];
  nameOf: (o: number) => string;
  context: PlayerAsideContext;
}) {
  const [all, setAll] = useState(false);
  const moments = events.filter((e) => e.kind !== 'backchannel');
  const shown = all ? moments : moments.slice(0, 6);

  return (
    <div className="flex min-w-0 flex-col gap-3">
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="text-[13px] font-semibold">Moments to review</h3>
        <span className="text-[12px] text-fg-muted">plays both voices</span>
      </div>
      {moments.length === 0 ? (
        <p className="rounded-[var(--radius-md)] border border-dashed border-border-default px-4 py-6 text-center text-[13px] text-fg-muted">
          Nobody talked over anyone. Turns were taken cleanly.
        </p>
      ) : (
        <ol className="flex flex-col divide-y divide-border-subtle overflow-hidden rounded-[var(--radius-md)] border border-border-subtle">
          {shown.map((e) => (
            <li key={`${String(e.by)}:${String(e.at)}`}>
              <button
                type="button"
                className="flex w-full items-center gap-3 px-3 py-2.5 text-left hover:bg-bg-subtle"
                onClick={() => {
                  // The original mix, so both people are heard as they were.
                  context.selectTrack(MIXED_TRACK);
                  context.playFrom(Math.max(0, e.at - LEAD_IN_MS));
                }}
              >
                <span className="grid size-7 shrink-0 place-items-center rounded-full bg-bg-inset text-fg-secondary">
                  <IconPlay size={13} />
                </span>
                <span className="font-mono text-[12px] text-fg-muted">{readableTime(e.at)}</span>
                <span className="min-w-0 flex-1 text-[13px]">
                  <span className="font-medium">{nameOf(e.by)}</span>{' '}
                  {e.kind === 'interruption' ? 'interrupted' : 'talked over'}{' '}
                  <span className="font-medium">{nameOf(e.of)}</span>
                </span>
                <span
                  className={cx(
                    'shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium',
                    e.kind === 'interruption'
                      ? 'bg-warning-soft text-warning'
                      : 'bg-bg-inset text-fg-secondary',
                  )}
                >
                  {e.kind === 'interruption' ? 'Interruption' : 'Talk-over'}
                </span>
              </button>
            </li>
          ))}
        </ol>
      )}
      {moments.length > 6 && (
        <button
          type="button"
          onClick={() => {
            setAll((a) => !a);
          }}
          className="self-start text-[12.5px] font-medium text-accent hover:underline"
        >
          {all ? 'Show fewer' : `Show all ${String(moments.length)}`}
        </button>
      )}
    </div>
  );
}
