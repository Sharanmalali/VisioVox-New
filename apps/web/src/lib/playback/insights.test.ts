import { readFileSync } from 'node:fs';

import { describe, expect, it } from 'vitest';

import type { Interval } from './insights';
import {
  BACKCHANNEL_MS,
  MERGE_GAP_MS,
  computeInsights,
  intervalsFromLines,
  spurtsOf,
} from './insights';
import type { TranscriptLine } from './transcript';

/** Shared with tests/test_insights.py: both implementations must agree. */
interface Fixture {
  input: { duration_ms: number; speakers: Record<string, [number, number][]> };
  expected: {
    speakers: Record<string, Record<string, number>>;
    overlaps: [number, number][];
    events: { kind: string; at: number; by: number; of: number }[];
    overlap_ms: number;
    speech_ms: number;
    silence_ms: number;
  };
}

const fixture = JSON.parse(
  readFileSync(
    new URL('../../../../../packages/contracts/examples/insights.json', import.meta.url),
    'utf8',
  ),
) as Fixture;

function fromFixture() {
  const intervals = new Map<number, Interval[]>(
    Object.entries(fixture.input.speakers).map(([k, ws]) => [Number(k), ws]),
  );
  return computeInsights(intervals, fixture.input.duration_ms);
}

describe('computeInsights (shared fixture)', () => {
  const got = fromFixture();

  it('matches every per-speaker number', () => {
    for (const [key, want] of Object.entries(fixture.expected.speakers)) {
      const stats = got.speakers.get(Number(key));
      expect(stats).toBeDefined();
      for (const [name, value] of Object.entries(want)) {
        expect(stats?.[name as keyof typeof stats], `${key}.${name}`).toBeCloseTo(value, 6);
      }
    }
  });

  it('matches overlaps, events and totals', () => {
    expect(got.overlaps).toEqual(fixture.expected.overlaps);
    expect(got.events).toEqual(fixture.expected.events);
    expect(got.overlap_ms).toBe(fixture.expected.overlap_ms);
    expect(got.speech_ms).toBe(fixture.expected.speech_ms);
    expect(got.silence_ms).toBe(fixture.expected.silence_ms);
  });
});

describe('computeInsights (edges)', () => {
  it('joins words into spurts across short gaps only', () => {
    const spurts = spurtsOf(1, [
      [0, 100],
      [100 + MERGE_GAP_MS, 900],
      [2000, 2100],
    ]);
    expect(spurts.map((s) => [s.start, s.end, s.words])).toEqual([
      [0, 900, 2],
      [2000, 2100, 1],
    ]);
  });

  it('does not count touching speech as overlap', () => {
    const got = computeInsights(
      new Map<number, Interval[]>([
        [1, [[0, 1000]]],
        [2, [[1000, 2000]]],
      ]),
      2000,
    );
    expect(got.overlaps).toEqual([]);
    expect(got.events).toEqual([]);
  });

  it('treats a short reply after the floor is free as a turn, not a backchannel', () => {
    const got = computeInsights(
      new Map<number, Interval[]>([
        [1, [[0, 1000]]],
        [2, [[1200, 1200 + BACKCHANNEL_MS / 2]]],
      ]),
      3000,
    );
    expect(got.events).toEqual([]);
    expect(got.speakers.get(2)?.turns).toBe(1);
  });

  it('handles a silent recording', () => {
    const got = computeInsights(
      new Map<number, Interval[]>([
        [1, []],
        [2, []],
      ]),
      5000,
    );
    expect(got.silence_ms).toBe(5000);
    expect([...got.speakers.values()].every((s) => s.share === 0)).toBe(true);
  });
});

describe('intervalsFromLines', () => {
  it('uses word timings, or the line itself when it has none', () => {
    const lines: TranscriptLine[] = [
      {
        key: 'a',
        speakerId: 'face_1',
        ordinal: 1,
        start_ms: 0,
        end_ms: 1000,
        text: 'hi there',
        words: [
          { text: 'hi', start_ms: 0, end_ms: 300, probability: 1 },
          { text: 'there', start_ms: 400, end_ms: 900, probability: 1 },
        ],
      },
      {
        key: 'b',
        speakerId: 'face_2',
        ordinal: 2,
        start_ms: 500,
        end_ms: 800,
        text: 'ok',
        words: [],
      },
    ];
    const got = intervalsFromLines(lines, [1, 2, 3]);
    expect(got.get(1)).toEqual([
      [0, 300],
      [400, 900],
    ]);
    expect(got.get(2)).toEqual([[500, 800]]);
    expect(got.get(3)).toEqual([]);
  });
});
