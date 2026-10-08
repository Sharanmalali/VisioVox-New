/**
 * Conversation insights: talk time, turns, overlap, and who interrupted whom.
 *
 * A line-for-line port of `ml/pipeline/insights.py`, which the AMI benchmark
 * (scripts/bench_ami_insights.py) scores against hand-aligned ground truth -
 * so what a user reads here is what was measured. Both implementations are
 * pinned to `packages/contracts/examples/insights.json`. Change the
 * definitions in both places or in neither.
 *
 * Input is each speaker's word timings, taken from their separated track's
 * captions. Speech nobody could caption is therefore not counted, and the
 * panel says so.
 */

import type { TranscriptLine } from './transcript';

export const MERGE_GAP_MS = 500;
export const MIN_OVERLAP_MS = 250;
export const ONSET_MARGIN_MS = 200;
export const BACKCHANNEL_MS = 1000;
export const YIELD_MS = 2000;

export type Interval = readonly [number, number];

export interface Spurt {
  readonly speaker: number;
  readonly start: number;
  readonly end: number;
  readonly words: number;
}

export type EventKind = 'interruption' | 'talk_over' | 'backchannel';

export interface ConversationEvent {
  readonly kind: EventKind;
  readonly at: number;
  readonly by: number;
  readonly of: number;
}

export interface SpeakerStats {
  readonly speaker: number;
  talk_ms: number;
  words: number;
  turns: number;
  longest_turn_ms: number;
  interruptions_made: number;
  interruptions_received: number;
  backchannels: number;
  share: number;
}

export interface Insights {
  readonly speakers: ReadonlyMap<number, SpeakerStats>;
  readonly spurts: readonly Spurt[];
  readonly overlaps: readonly Interval[];
  readonly events: readonly ConversationEvent[];
  readonly overlap_ms: number;
  readonly speech_ms: number;
  readonly duration_ms: number;
  readonly silence_ms: number;
}

const byStart = (a: Spurt, b: Spurt): number => a.start - b.start || a.speaker - b.speaker;

/** Join one speaker's words into spurts. */
export function spurtsOf(speaker: number, intervals: readonly Interval[]): Spurt[] {
  const out: Spurt[] = [];
  const sorted = [...intervals].sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  for (const [start, end] of sorted) {
    if (end <= start) continue;
    const last = out[out.length - 1];
    if (last !== undefined && start - last.end <= MERGE_GAP_MS) {
      out[out.length - 1] = {
        speaker,
        start: last.start,
        end: Math.max(last.end, end),
        words: last.words + 1,
      };
    } else {
      out.push({ speaker, start, end, words: 1 });
    }
  }
  return out;
}

function unionLength(intervals: readonly Interval[]): number {
  let total = 0;
  let cursor = -1;
  for (const [start, end] of [...intervals].sort((a, b) => a[0] - b[0] || a[1] - b[1])) {
    if (end <= cursor) continue;
    total += end - Math.max(start, cursor);
    cursor = end;
  }
  return total;
}

function overlapRegions(spurts: readonly Spurt[]): Interval[] {
  const edges: [number, number][] = [];
  for (const s of spurts) {
    edges.push([s.start, 1], [s.end, -1]);
  }
  // Ends before starts at the same instant: touching spurts do not overlap.
  edges.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const regions: Interval[] = [];
  let active = 0;
  let opened = 0;
  for (const [at, step] of edges) {
    const before = active;
    active += step;
    if (before < 2 && active >= 2) opened = at;
    else if (before >= 2 && active < 2 && at > opened) regions.push([opened, at]);
  }
  const joined: [number, number][] = [];
  for (const [start, end] of regions) {
    const last = joined[joined.length - 1];
    if (last !== undefined && start - last[1] < MIN_OVERLAP_MS) last[1] = end;
    else joined.push([start, end]);
  }
  return joined.filter(([a, b]) => b - a >= MIN_OVERLAP_MS);
}

function conversationEvents(spurts: readonly Spurt[]): ConversationEvent[] {
  const ordered = [...spurts].sort(byStart);
  const events: ConversationEvent[] = [];
  for (const b of ordered) {
    let holder: Spurt | null = null;
    for (const a of ordered) {
      if (a.start > b.start) break;
      if (a.speaker === b.speaker) continue;
      if (a.start <= b.start - ONSET_MARGIN_MS && b.start < a.end) {
        // The floor holder is whoever started most recently.
        if (
          holder === null ||
          a.start > holder.start ||
          (a.start === holder.start && a.speaker < holder.speaker)
        )
          holder = a;
      }
    }
    if (holder === null) continue;
    let kind: EventKind;
    if (b.end - b.start < BACKCHANNEL_MS && b.end <= holder.end) kind = 'backchannel';
    else if (holder.end - b.start <= YIELD_MS && b.end > holder.end) kind = 'interruption';
    else kind = 'talk_over';
    events.push({ kind, at: b.start, by: b.speaker, of: holder.speaker });
  }
  return events;
}

/** Insights for one recording. `intervals` maps speaker to word timings. */
export function computeInsights(
  intervals: ReadonlyMap<number, readonly Interval[]>,
  durationMs: number,
  wordCounts?: ReadonlyMap<number, number>,
): Insights {
  const spurts: Spurt[] = [];
  const stats = new Map<number, SpeakerStats>();
  for (const [speaker, ws] of intervals) {
    stats.set(speaker, {
      speaker,
      talk_ms: 0,
      words: 0,
      turns: 0,
      longest_turn_ms: 0,
      interruptions_made: 0,
      interruptions_received: 0,
      backchannels: 0,
      share: 0,
    });
    spurts.push(...spurtsOf(speaker, ws));
  }
  const get = (speaker: number): SpeakerStats => {
    const s = stats.get(speaker);
    if (s === undefined) throw new Error(`unknown speaker ${String(speaker)}`);
    return s;
  };
  for (const s of spurts) {
    get(s.speaker).talk_ms += s.end - s.start;
    get(s.speaker).words += s.words;
  }
  // Diarized segments are not words; the captions say how many were spoken.
  if (wordCounts !== undefined) {
    for (const [speaker, n] of wordCounts) {
      const s = stats.get(speaker);
      if (s !== undefined) s.words = n;
    }
  }

  const events = conversationEvents(spurts);
  const backchannelStarts = new Set(
    events.filter((e) => e.kind === 'backchannel').map((e) => `${String(e.by)}:${String(e.at)}`),
  );
  for (const e of events) {
    if (e.kind === 'interruption') {
      get(e.by).interruptions_made += 1;
      get(e.of).interruptions_received += 1;
    } else if (e.kind === 'backchannel') {
      get(e.by).backchannels += 1;
    }
  }

  let holder: number | null = null;
  for (const s of [...spurts].sort(byStart)) {
    if (backchannelStarts.has(`${String(s.speaker)}:${String(s.start)}`)) continue;
    if (s.speaker === holder) continue;
    holder = s.speaker;
    get(s.speaker).turns += 1;
  }

  // The longest stretch of speech without a pause - not the longest turn,
  // which during sustained overlap spans the gaps while the other person
  // talks and so can exceed the speaker's whole talk time.
  for (const s of spurts) {
    const t = get(s.speaker);
    t.longest_turn_ms = Math.max(t.longest_turn_ms, s.end - s.start);
  }

  let totalTalk = 0;
  for (const t of stats.values()) totalTalk += t.talk_ms;
  for (const t of stats.values()) t.share = totalTalk > 0 ? t.talk_ms / totalTalk : 0;

  const overlaps = overlapRegions(spurts);
  const speechMs = unionLength(spurts.map((s): Interval => [s.start, s.end]));
  return {
    speakers: stats,
    spurts: [...spurts].sort(byStart),
    overlaps,
    events,
    overlap_ms: overlaps.reduce((n, [a, b]) => n + b - a, 0),
    speech_ms: speechMs,
    duration_ms: durationMs,
    silence_ms: Math.max(0, durationMs - speechMs),
  };
}

/**
 * Word timings per speaker, from the merged transcript. A segment without word
 * timings contributes its own bounds as one interval.
 */
export function intervalsFromLines(
  lines: readonly TranscriptLine[],
  ordinals: readonly number[],
): Map<number, Interval[]> {
  const out = new Map<number, Interval[]>(ordinals.map((o) => [o, []]));
  for (const line of lines) {
    const list = out.get(line.ordinal);
    if (list === undefined) continue;
    if (line.words.length === 0) list.push([line.start_ms, line.end_ms]);
    else for (const w of line.words) list.push([w.start_ms, w.end_ms]);
  }
  return out;
}
