import { describe, expect, it } from 'vitest';

import { buildCaptionIndex } from './captions';
import type { CaptionSegment } from './captions';
import {
  activeLines,
  fileStem,
  foldForSearch,
  latestLine,
  mergeTranscript,
  searchLines,
  splitMatches,
  timestamp,
  toPlainText,
  toSrt,
  toVtt,
  turns,
} from './transcript';

function seg(start: number, end: number, text: string): CaptionSegment {
  return { start_ms: start, end_ms: end, text, words: [] };
}

const speakers = [
  { id: 'face_1', ordinal: 1 },
  { id: 'face_2', ordinal: 2 },
];

const captions = {
  face_1: buildCaptionIndex({
    language: 'en',
    language_probability: 1,
    segments: [seg(0, 2000, ' Hello everyone.'), seg(5000, 9000, 'Shall we begin?')],
  }),
  face_2: buildCaptionIndex({
    language: 'en',
    language_probability: 1,
    segments: [seg(1500, 4000, 'Hi, thanks for having me.'), seg(9500, 10000, '   ')],
  }),
};

const lines = mergeTranscript(speakers, captions);
function lineAt(i: number) {
  const line = lines[i];
  if (line === undefined) throw new Error(`no line ${String(i)}`);
  return line;
}

const nameOf = (o: number): string => (o === 1 ? 'Priya' : `Speaker ${String(o)}`);

describe('mergeTranscript', () => {
  it('interleaves every speaker by start time and drops blank lines', () => {
    expect(lines.map((l) => [l.ordinal, l.start_ms, l.text])).toEqual([
      [1, 0, 'Hello everyone.'],
      [2, 1500, 'Hi, thanks for having me.'],
      [1, 5000, 'Shall we begin?'],
    ]);
  });

  it('skips a speaker whose captions failed to load', () => {
    const only = mergeTranscript(speakers, { face_2: captions.face_2 });
    expect(only.every((l) => l.ordinal === 2)).toBe(true);
  });
});

describe('activeLines', () => {
  it('returns both lines while two people overlap', () => {
    expect(activeLines(lines, 1800)).toEqual([0, 1]);
  });

  it('returns one line when one person speaks, none in silence', () => {
    expect(activeLines(lines, 3000)).toEqual([1]);
    expect(activeLines(lines, 4500)).toEqual([]);
  });

  it('latestLine holds the last line started, for following through pauses', () => {
    expect(latestLine(lines, 4500)).toBe(1);
    expect(latestLine(lines, -1)).toBe(-1);
  });
});

describe('search', () => {
  it('ignores case and Latin accents', () => {
    expect(foldForSearch('Café ÉCOLE')).toBe('cafe ecole');
    expect(searchLines(lines, 'HELLO')).toEqual([0]);
  });

  it('keeps Indic text intact, so a matra still distinguishes words', () => {
    expect(foldForSearch('नमस्ते')).toBe('नमस्ते');
  });

  it('finds nothing for a blank query', () => {
    expect(searchLines(lines, '   ')).toEqual([]);
  });

  it('splits text around every match for highlighting', () => {
    expect(splitMatches('we we go', 'we')).toEqual([
      { text: 'we', match: true },
      { text: ' ', match: false },
      { text: 'we', match: true },
      { text: ' go', match: false },
    ]);
  });
});

describe('exports', () => {
  it('formats SRT and VTT timestamps', () => {
    expect(timestamp(3_725_042, ',')).toBe('01:02:05,042');
    expect(timestamp(3_725_042, '.')).toBe('01:02:05.042');
  });

  it('writes SRT with the speaker name leading each cue', () => {
    expect(toSrt(lines.slice(0, 1), nameOf)).toBe(
      '1\n00:00:00,000 --> 00:00:02,000\nPriya: Hello everyone.\n',
    );
  });

  it('writes VTT with voice tags and escaped text', () => {
    const vtt = toVtt([{ ...lineAt(0), text: 'a < b' }], nameOf);
    expect(vtt).toBe('WEBVTT\n\n00:00:00.000 --> 00:00:02.000\n<v Priya>a &lt; b\n');
  });

  it('joins consecutive lines by one person into a turn', () => {
    const merged = turns([lineAt(0), { ...lineAt(2), start_ms: 2100 }]);
    expect(merged).toHaveLength(1);
    expect(merged[0]?.text).toBe('Hello everyone. Shall we begin?');
  });

  it('writes minutes-style plain text', () => {
    expect(toPlainText(lines, nameOf, 'Standup')).toContain(
      '[00:01] Speaker 2: Hi, thanks for having me.',
    );
  });

  it('makes a safe file name from any title', () => {
    expect(fileStem('Q3 review: "final"/v2')).toBe('Q3-review-final-v2');
    expect(fileStem('कन्नड़ सभा')).not.toBe('transcript');
    expect(fileStem('///')).toBe('transcript');
  });
});
