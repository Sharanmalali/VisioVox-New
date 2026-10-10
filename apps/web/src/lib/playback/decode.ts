/**
 * Decoding a track's audio, robustly enough for Safari.
 *
 * Safari's `decodeAudioData` fails with "EncodingError: Decoding failed" on
 * files Chrome and ffmpeg decode without complaint, most readily when many
 * decodes run at once (a nine-track project did; three-track ones did not).
 * So a failed decode is tried again on a fresh decoder - an
 * OfflineAudioContext, which shares nothing with the playing context - after
 * a pause, and only then reported. `decodeAudioData` detaches the buffer it is
 * given, so every attempt decodes its own copy.
 */

export type Decoder = (bytes: ArrayBuffer) => Promise<AudioBuffer>;

export const RETRY_DELAYS_MS = [300, 900] as const;

export async function decodeWithRetry(
  bytes: ArrayBuffer,
  decoders: readonly Decoder[],
  wait: (ms: number) => Promise<void> = (ms) => new Promise((r) => setTimeout(r, ms)),
): Promise<AudioBuffer> {
  let last: unknown = null;
  for (let attempt = 0; attempt <= RETRY_DELAYS_MS.length; attempt++) {
    const decoder = decoders[Math.min(attempt, decoders.length - 1)];
    if (decoder === undefined) break;
    try {
      return await decoder(bytes.slice(0));
    } catch (error) {
      last = error;
      const delay = RETRY_DELAYS_MS[attempt];
      if (delay !== undefined) await wait(delay);
    }
  }
  throw last instanceof Error ? last : new Error(String(last));
}

/** What a person reads when a track will not decode, instead of the browser's two words. */
export function decodeFailureMessage(label: string): string {
  return (
    `${label}'s audio could not be decoded by this browser. The other tracks still play; ` +
    'reloading the page usually fixes it.'
  );
}
