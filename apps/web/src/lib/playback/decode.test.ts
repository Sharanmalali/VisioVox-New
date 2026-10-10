import { describe, expect, it } from 'vitest';

import { RETRY_DELAYS_MS, decodeFailureMessage, decodeWithRetry, type Decoder } from './decode';

const buffer = { duration: 1 } as unknown as AudioBuffer;
const noWait = () => Promise.resolve();

describe('decodeWithRetry', () => {
  it('returns the first successful decode', async () => {
    const calls: number[] = [];
    const ok: Decoder = (b) => {
      calls.push(b.byteLength);
      return Promise.resolve(buffer);
    };
    await expect(decodeWithRetry(new ArrayBuffer(8), [ok], noWait)).resolves.toBe(buffer);
    expect(calls).toEqual([8]);
  });

  it('tries a fresh decoder after a failure, with its own copy of the bytes', async () => {
    const seen: string[] = [];
    const flaky: Decoder = (b) => {
      seen.push(`main:${String(b.byteLength)}`);
      return Promise.reject(new Error('EncodingError: Decoding failed'));
    };
    const fresh: Decoder = (b) => {
      seen.push(`fresh:${String(b.byteLength)}`);
      return Promise.resolve(buffer);
    };
    const waited: number[] = [];
    const result = await decodeWithRetry(new ArrayBuffer(16), [flaky, fresh], (ms) => {
      waited.push(ms);
      return Promise.resolve();
    });
    expect(result).toBe(buffer);
    expect(seen).toEqual(['main:16', 'fresh:16']);
    expect(waited).toEqual([RETRY_DELAYS_MS[0]]);
  });

  it('gives up after every attempt fails, with the last error', async () => {
    let n = 0;
    const bad: Decoder = () => Promise.reject(new Error(`fail ${String(++n)}`));
    await expect(decodeWithRetry(new ArrayBuffer(4), [bad], noWait)).rejects.toThrow('fail 3');
  });
});

describe('decodeFailureMessage', () => {
  it('names the speaker and says the rest still play', () => {
    expect(decodeFailureMessage('Speaker 3')).toMatch(
      /^Speaker 3's audio .* other tracks still play/,
    );
  });
});
