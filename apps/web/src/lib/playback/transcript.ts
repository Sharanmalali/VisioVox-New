/**
 * One conversation, in order: every speaker's captions merged into a single
 * "who said what" transcript.
 *
 * Each separated track is transcribed on its own (S7), so the files arrive one
 * per person. People read a meeting as one thread, though, so the lines are
 * interleaved by start time here. Overlapping speech stays overlapping: two
 * lines may both be "now", and the panel highlights both rather than pretending
 * one person had the floor.
 *
 * Pure functions only - no DOM, no clock - so the merge, search and every
 * export format are unit-tested without a browser.
 */

import type { CaptionIndex, CaptionWord } from './captions';
import type { Speaker } from './manifest';

export interface TranscriptLine {
  /** Stable across renders: speaker id plus start time. */
  readonly key: string;
  readonly speakerId: string;
  readonly ordinal: number;
  readonly start_ms: number;
  readonly end_ms: number;
  readonly text: string;
  readonly words: readonly CaptionWord[];
}

/** Merge per-speaker captions into one list, earliest first. */
export function mergeTranscript(
  speakers: readonly Pick<Speaker, 'id' | 'ordinal'>[],
  captions: Readonly<Record<string, CaptionIndex>>,
): TranscriptLine[] {
  const lines: TranscriptLine[] = [];
  for (const speaker of speakers) {
    const index = captions[speaker.id];
    if (index === undefined) continue;
    for (const segment of index.segments) {
      const text = segment.text.trim();
      if (text === '') continue;
      lines.push({
        key: `${speaker.id}:${String(segment.start_ms)}`,
        speakerId: speaker.id,
        ordinal: speaker.ordinal,
        start_ms: segment.start_ms,
        end_ms: segment.end_ms,
        text,
        words: segment.words,
      });
    }
  }
  // Ties go to the lower ordinal so the order is deterministic.
  return lines.sort((a, b) => a.start_ms - b.start_ms || a.ordinal - b.ordinal);
}

/**
 * Indices of every line being spoken at `ms` - more than one during overlap.
 *
 * Lines are sorted by start, so everything after the first line starting past
 * `ms` is in the future; walking back from there stops once lines are far
 * enough in the past that none can still be running.
 */
export function activeLines(lines: readonly TranscriptLine[], ms: number): number[] {
  let low = 0;
  let high = lines.length;
  while (low < high) {
    const mid = (low + high) >> 1;
    const line = lines[mid];
    if (line !== undefined && line.start_ms <= ms) low = mid + 1;
    else high = mid;
  }
  const active: number[] = [];
  // No caption segment runs longer than this (Whisper caps them near 30 s).
  const LONGEST_MS = 60_000;
  for (let i = low - 1; i >= 0; i--) {
    const line = lines[i];
    if (line === undefined || ms - line.start_ms > LONGEST_MS) break;
    if (ms < line.end_ms) active.push(i);
  }
  return active.reverse();
}

/** The most recent line to have started at or before `ms`, or -1. */
export function latestLine(lines: readonly TranscriptLine[], ms: number): number {
  let low = 0;
  let high = lines.length;
  while (low < high) {
    const mid = (low + high) >> 1;
    const line = lines[mid];
    if (line !== undefined && line.start_ms <= ms) low = mid + 1;
    else high = mid;
  }
  return low - 1;
}

/**
 * Fold text for searching: case-insensitive, and accents ignored for Latin
 * scripts ("cafe" finds "café"). Indic and other scripts pass through intact,
 * because stripping their combining marks would change the word.
 */
export function foldForSearch(text: string): string {
  return text
    .toLowerCase()
    .normalize('NFD')
    .replace(/(\p{Script=Latin})\p{M}+/gu, '$1')
    .normalize('NFC');
}

/** Indices of the lines containing `query`, in order. */
export function searchLines(lines: readonly TranscriptLine[], query: string): number[] {
  const needle = foldForSearch(query.trim());
  if (needle === '') return [];
  const hits: number[] = [];
  lines.forEach((line, i) => {
    if (foldForSearch(line.text).includes(needle)) hits.push(i);
  });
  return hits;
}

/**
 * Split text around matches of `query`, for highlighting. Matching runs on the
 * folded text; folding Latin accents keeps lengths only after NFC, so when the
 * folded length differs from the original the whole text is returned unsplit
 * rather than highlighting the wrong characters.
 */
export function splitMatches(text: string, query: string): { text: string; match: boolean }[] {
  const needle = foldForSearch(query.trim());
  const folded = foldForSearch(text);
  if (needle === '' || folded.length !== text.length) {
    return [{ text, match: false }];
  }
  const parts: { text: string; match: boolean }[] = [];
  let from = 0;
  for (;;) {
    const at = folded.indexOf(needle, from);
    if (at === -1) break;
    if (at > from) parts.push({ text: text.slice(from, at), match: false });
    parts.push({ text: text.slice(at, at + needle.length), match: true });
    from = at + needle.length;
  }
  if (from < text.length) parts.push({ text: text.slice(from), match: false });
  return parts;
}

// ------------------------------------------------------------------ exports --

export type NameOf = (ordinal: number) => string;

function pad(n: number, width = 2): string {
  return String(n).padStart(width, '0');
}

/** 3725042 -> "01:02:05,042" (SRT) or "01:02:05.042" (VTT). */
export function timestamp(ms: number, separator: ',' | '.'): string {
  const total = Math.max(0, Math.round(ms));
  const h = Math.floor(total / 3_600_000);
  const m = Math.floor((total % 3_600_000) / 60_000);
  const s = Math.floor((total % 60_000) / 1000);
  return `${pad(h)}:${pad(m)}:${pad(s)}${separator}${pad(total % 1000, 3)}`;
}

/** 125000 -> "02:05"; 3725000 -> "1:02:05". For reading, not for players. */
export function readableTime(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return h > 0 ? `${String(h)}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
}

/** SubRip: plays in VLC, YouTube, Premiere. The speaker leads each cue. */
export function toSrt(lines: readonly TranscriptLine[], nameOf: NameOf): string {
  return lines
    .map(
      (line, i) =>
        `${String(i + 1)}\n${timestamp(line.start_ms, ',')} --> ${timestamp(line.end_ms, ',')}\n` +
        `${nameOf(line.ordinal)}: ${line.text}\n`,
    )
    .join('\n');
}

/** WebVTT with voice tags, so players that understand speakers can style them. */
export function toVtt(lines: readonly TranscriptLine[], nameOf: NameOf): string {
  const escape = (s: string): string =>
    s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const cues = lines.map(
    (line) =>
      `${timestamp(line.start_ms, '.')} --> ${timestamp(line.end_ms, '.')}\n` +
      `<v ${escape(nameOf(line.ordinal))}>${escape(line.text)}`,
  );
  return ['WEBVTT', '', ...cues.flatMap((c) => [c, ''])].join('\n');
}

/**
 * Plain text, the way minutes are written: consecutive lines by the same
 * person are joined into one paragraph under one timestamp.
 */
export function toPlainText(
  lines: readonly TranscriptLine[],
  nameOf: NameOf,
  title: string,
): string {
  const out: string[] = [title, ''];
  for (const turn of turns(lines)) {
    out.push(`[${readableTime(turn.start_ms)}] ${nameOf(turn.ordinal)}: ${turn.text}`, '');
  }
  return out.join('\n');
}

export interface Turn {
  readonly ordinal: number;
  readonly start_ms: number;
  readonly end_ms: number;
  readonly text: string;
}

/** Consecutive lines by one speaker, joined - how a reader thinks of a turn. */
export function turns(lines: readonly TranscriptLine[]): Turn[] {
  const result: Turn[] = [];
  for (const line of lines) {
    const last = result[result.length - 1];
    if (last !== undefined && last.ordinal === line.ordinal) {
      result[result.length - 1] = {
        ...last,
        end_ms: Math.max(last.end_ms, line.end_ms),
        text: `${last.text} ${line.text}`,
      };
    } else {
      result.push({
        ordinal: line.ordinal,
        start_ms: line.start_ms,
        end_ms: line.end_ms,
        text: line.text,
      });
    }
  }
  return result;
}

/** A filename-safe stem from a project title. */
export function fileStem(title: string): string {
  const stem = title
    .normalize('NFKD')
    .replace(/[^\p{L}\p{N}]+/gu, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 60);
  return stem === '' ? 'transcript' : stem;
}
