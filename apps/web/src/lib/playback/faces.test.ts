import { describe, expect, it } from 'vitest';

import type { FaceBox } from './faces';
import { MAX_GAP_MS, boxAt, contentRect, faceRect, parseFaces } from './faces';

const boxes: FaceBox[] = [
  [0, 0.25, 0.4, 0.1, 0.18],
  [240, 0.26, 0.4, 0.1, 0.18],
  [480, 0.27, 0.41, 0.1, 0.18],
  [5000, 0.7, 0.4, 0.1, 0.18],
];

describe('boxAt', () => {
  it('returns the nearest sample', () => {
    expect(boxAt(boxes, 100)?.[0]).toBe(0);
    expect(boxAt(boxes, 200)?.[0]).toBe(240);
    expect(boxAt(boxes, 480)?.[0]).toBe(480);
  });

  it('says the face is gone when no sample is close', () => {
    expect(boxAt(boxes, 480 + MAX_GAP_MS + 1)).toBeNull();
    expect(boxAt(boxes, 3000)).toBeNull();
    expect(boxAt([], 0)).toBeNull();
  });

  it('finds a face that comes back later', () => {
    expect(boxAt(boxes, 5100)?.[1]).toBe(0.7);
  });
});

describe('contentRect', () => {
  it('pillarboxes a 4:3 picture in a 16:9 element', () => {
    expect(contentRect(1600, 900, 640, 480)).toEqual({ x: 200, y: 0, width: 1200, height: 900 });
  });

  it('letterboxes a wide picture in a square element', () => {
    const r = contentRect(1000, 1000, 1920, 1080);
    expect(r.x).toBeCloseTo(0);
    expect(r.y).toBeCloseTo(218.75);
    expect(r.width).toBeCloseTo(1000);
    expect(r.height).toBeCloseTo(562.5);
  });

  it('falls back to the element before the video has loaded', () => {
    expect(contentRect(800, 450, 0, 0)).toEqual({ x: 0, y: 0, width: 800, height: 450 });
  });
});

describe('faceRect', () => {
  it('places a face in pixels and grows it to take in the head', () => {
    const r = faceRect([0, 0.5, 0.5, 0.1, 0.2], { x: 10, y: 20, width: 1000, height: 500 }, 1);
    expect(r).toEqual({ x: 410, y: 170, width: 200, height: 200 });
  });
});

describe('parseFaces', () => {
  it('reads and sorts boxes', () => {
    expect(
      parseFaces({
        boxes: [
          [240, 0.3, 0.4, 0.1, 0.1],
          [0, 0.2, 0.4, 0.1, 0.1],
        ],
      }),
    ).toEqual([
      [0, 0.2, 0.4, 0.1, 0.1],
      [240, 0.3, 0.4, 0.1, 0.1],
    ]);
  });

  it('rejects malformed data, so no face is made clickable from garbage', () => {
    expect(parseFaces(undefined)).toBeNull();
    expect(parseFaces({ boxes: [[0, 0.5]] })).toBeNull();
    expect(parseFaces({ boxes: [[0, 'x', 0.5, 0.1, 0.1]] })).toBeNull();
  });
});
