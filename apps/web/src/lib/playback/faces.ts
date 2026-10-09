/**
 * Where each person's face is, for click-a-face listening.
 *
 * S5 records each face track's box about four times a second and S9 writes a
 * person's boxes into their caption file (`faces.boxes`): `[ms, cx, cy,
 * half_w, half_h]`, the last four as fractions of the video frame. The player
 * draws a clickable region over each face that is on screen *now*.
 */

/** `[ms, cx, cy, half_w, half_h]`, coordinates as fractions of the frame. */
export type FaceBox = readonly [number, number, number, number, number];

/** A sample further than this from `ms` does not place the face (it has left). */
export const MAX_GAP_MS = 400;

/**
 * The face's box at `ms`: the nearest sample within MAX_GAP_MS, or null if the
 * face is not on screen. `boxes` must be sorted by time.
 */
export function boxAt(boxes: readonly FaceBox[], ms: number): FaceBox | null {
  if (boxes.length === 0) return null;
  let low = 0;
  let high = boxes.length - 1;
  while (low < high) {
    const mid = (low + high) >> 1;
    const box = boxes[mid];
    if (box !== undefined && box[0] < ms) low = mid + 1;
    else high = mid;
  }
  // `low` is the first sample at or after ms; its predecessor may be closer.
  let best: FaceBox | null = null;
  for (const i of [low - 1, low]) {
    const box = boxes[i];
    if (box === undefined) continue;
    if (best === null || Math.abs(box[0] - ms) < Math.abs(best[0] - ms)) best = box;
  }
  return best !== null && Math.abs(best[0] - ms) <= MAX_GAP_MS ? best : null;
}

export interface Rect {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}

/**
 * Where the picture actually is inside a video element showing it with
 * `object-fit: contain` (the default): letterboxed or pillarboxed.
 */
export function contentRect(
  elementWidth: number,
  elementHeight: number,
  videoWidth: number,
  videoHeight: number,
): Rect {
  if (videoWidth <= 0 || videoHeight <= 0 || elementWidth <= 0 || elementHeight <= 0)
    return { x: 0, y: 0, width: elementWidth, height: elementHeight };
  const scale = Math.min(elementWidth / videoWidth, elementHeight / videoHeight);
  const width = videoWidth * scale;
  const height = videoHeight * scale;
  return { x: (elementWidth - width) / 2, y: (elementHeight - height) / 2, width, height };
}

/**
 * A face box in pixels within `picture`, grown to take in the head around the
 * detected face (detectors box the face tightly; people point at heads).
 */
export function faceRect(box: FaceBox, picture: Rect, grow = 1.35): Rect {
  const [, cx, cy, hw, hh] = box;
  const width = 2 * hw * grow * picture.width;
  const height = 2 * hh * grow * picture.height;
  return {
    x: picture.x + cx * picture.width - width / 2,
    y: picture.y + cy * picture.height - height / 2,
    width,
    height,
  };
}

/** `{ boxes: [[ms, cx, cy, hw, hh], ...] }`, or null if absent or malformed. */
export function parseFaces(raw: unknown): FaceBox[] | null {
  if (typeof raw !== 'object' || raw === null) return null;
  const boxes = (raw as Record<string, unknown>)['boxes'];
  if (!Array.isArray(boxes)) return null;
  const out: FaceBox[] = [];
  for (const entry of boxes as unknown[]) {
    if (!Array.isArray(entry) || entry.length < 5) return null;
    const values = (entry as unknown[]).slice(0, 5);
    if (!values.every((v) => typeof v === 'number' && Number.isFinite(v))) return null;
    const [ms, cx, cy, hw, hh] = values as number[];
    out.push([ms ?? 0, cx ?? 0, cy ?? 0, hw ?? 0, hh ?? 0]);
  }
  return out.sort((a, b) => a[0] - b[0]);
}
