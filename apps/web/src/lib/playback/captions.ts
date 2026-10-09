/**
 * Word-timed captions, indexed for playback (docs/12 §5).
 *
 * Captions read the same clock as the audio rather than running their own
 * timer, so this module holds no state about time — it answers "what is
 * showing at t" and the player asks once per frame.
 *
 * The runtime format is the word-timed JSON emitted by S7, not the VTT. VTT
 * cannot carry word timings cleanly, and word timings are what make
 * click-to-seek (FR-PLAY-09) and per-word confidence possible. VTT is still
 * exported for download and for HLS subtitle renditions; it is simply not what
 * the player reads.
 */

/** One word, as `ml/pipeline/s7_transcribe.py` serialises it. */
import type { FaceBox } from './faces';
import { parseFaces } from './faces';

export interface CaptionWord {
  readonly text: string;
  readonly start_ms: number;
  readonly end_ms: number;
  readonly probability: number;
}

export interface CaptionSegment {
  readonly text: string;
  readonly start_ms: number;
  readonly end_ms: number;
  readonly words: readonly CaptionWord[];
}

export interface Transcript {
  readonly language: string;
  readonly language_probability: number;
  readonly segments: readonly CaptionSegment[];
  /**
   * When this person was speaking, from face-anchored diarization
   * (ml/pipeline/diarize.py), as [start_ms, end_ms]. Absent on projects
   * processed before it shipped; the insights then fall back to word timings.
   */
  readonly activity?: readonly (readonly [number, number])[];
  /** Where this person's face is over time (lib/playback/faces.ts). */
  readonly faces?: readonly FaceBox[];
}

/**
 * Segments sorted by start time, so lookup can bisect.
 *
 * Sorting on construction rather than trusting the file is cheap insurance: a
 * single out-of-order segment would make a binary search silently return the
 * wrong caption, which reads as a sync bug rather than a data bug and would be
 * chased in the wrong place.
 */
export interface CaptionIndex {
  readonly segments: readonly CaptionSegment[];
  readonly activity?: readonly (readonly [number, number])[];
  readonly faces?: readonly FaceBox[];
}

export function buildCaptionIndex(transcript: Transcript): CaptionIndex {
  const segments = [...transcript.segments].sort((a, b) => a.start_ms - b.start_ms);
  return {
    segments,
    ...(transcript.activity === undefined ? {} : { activity: transcript.activity }),
    ...(transcript.faces === undefined ? {} : { faces: transcript.faces }),
  };
}

export const EMPTY_CAPTION_INDEX: CaptionIndex = { segments: [] };

/**
 * The segment covering `ms`, or null in a gap between segments.
 *
 * Returns null rather than the nearest segment: holding the previous caption
 * on screen through a pause makes the speaker look like they are still
 * talking, which in a product about who said what is exactly the wrong lie.
 */
export function segmentAt(index: CaptionIndex, ms: number): CaptionSegment | null {
  const segments = index.segments;
  let low = 0;
  let high = segments.length - 1;
  while (low <= high) {
    const mid = (low + high) >> 1;
    const segment = segments[mid];
    if (segment === undefined) break;
    if (ms < segment.start_ms) high = mid - 1;
    else if (ms >= segment.end_ms) low = mid + 1;
    else return segment;
  }
  return null;
}

/** The word being spoken at `ms` within a segment, for highlighting. */
export function wordAt(segment: CaptionSegment, ms: number): CaptionWord | null {
  for (const word of segment.words) {
    if (ms >= word.start_ms && ms < word.end_ms) return word;
  }
  return null;
}

/**
 * Parse the transcript body defensively.
 *
 * Captions arrive from a signed CDN URL, so the failure worth handling is a
 * truncated or expired response, not a malicious one. An unreadable transcript
 * degrades to no captions; it never takes playback down with it.
 */
export function parseTranscript(body: unknown): Transcript | null {
  if (typeof body !== 'object' || body === null) return null;
  const record = body as Record<string, unknown>;
  const rawSegments = record['segments'];
  if (!Array.isArray(rawSegments)) return null;

  const segments: CaptionSegment[] = [];
  for (const entry of rawSegments as unknown[]) {
    if (typeof entry !== 'object' || entry === null) continue;
    const seg = entry as Record<string, unknown>;
    const start = seg['start_ms'];
    const end = seg['end_ms'];
    const text = seg['text'];
    if (typeof start !== 'number' || typeof end !== 'number' || typeof text !== 'string') continue;

    const words: CaptionWord[] = [];
    const rawWords = seg['words'];
    if (Array.isArray(rawWords)) {
      for (const wordEntry of rawWords as unknown[]) {
        if (typeof wordEntry !== 'object' || wordEntry === null) continue;
        const word = wordEntry as Record<string, unknown>;
        const wStart = word['start_ms'];
        const wEnd = word['end_ms'];
        const wText = word['text'];
        if (typeof wStart !== 'number' || typeof wEnd !== 'number' || typeof wText !== 'string')
          continue;
        const probability = word['probability'];
        words.push({
          text: wText,
          start_ms: wStart,
          end_ms: wEnd,
          probability: typeof probability === 'number' ? probability : 1,
        });
      }
    }
    segments.push({ text, start_ms: start, end_ms: end, words });
  }

  const language = record['language'];
  const languageProbability = record['language_probability'];
  const activity = parseActivity(record['activity']);
  const faces = parseFaces(record['faces']);
  const parsed: Transcript = {
    language: typeof language === 'string' ? language : 'unknown',
    language_probability: typeof languageProbability === 'number' ? languageProbability : 0,
    segments,
  };
  return {
    ...parsed,
    ...(activity === null ? {} : { activity }),
    ...(faces === null ? {} : { faces }),
  };
}

/** `{ intervals: [[start_ms, end_ms], ...] }`, or null if absent or malformed. */
function parseActivity(raw: unknown): [number, number][] | null {
  if (typeof raw !== 'object' || raw === null) return null;
  const intervals = (raw as Record<string, unknown>)['intervals'];
  if (!Array.isArray(intervals)) return null;
  const out: [number, number][] = [];
  for (const entry of intervals as unknown[]) {
    if (!Array.isArray(entry) || entry.length !== 2) return null;
    const [a, b] = entry as unknown[];
    if (typeof a !== 'number' || typeof b !== 'number') return null;
    out.push([a, b]);
  }
  return out;
}
