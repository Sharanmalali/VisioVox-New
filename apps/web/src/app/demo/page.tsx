'use client';

/**
 * The player, running against real audio, with no API and no GPU.
 *
 * This page exists because everything else about the sync engine can pass —
 * types, lint, unit tests — while the thing itself has never decoded a byte.
 * Here it loads real AAC, crossfades real speech and renders real transcripts,
 * which is the only way to find out whether it actually works.
 *
 * The fixture is real recorded material with its true isolated sources — by
 * default an AMI meeting, where the sources are the participants' own headset
 * microphones. So what you hear when you pick a speaker is what a perfect
 * extractor would return: a target rather than a mock, and a direct comparison
 * once SEAVE has a checkpoint to point this page at.
 */

import { useEffect, useState } from 'react';
import { Player } from '@/components/Player';
import { MarketingFooter } from '@/components/shell/MarketingFooter';
import { MarketingHeader } from '@/components/shell/MarketingHeader';
import { Skeleton } from '@/components/ui';
import type { Manifest, PlaybackHint } from '@/lib/playback/manifest';

const FIXTURE = '/fixtures/manifest.json';
const GENERATE = 'uv run python scripts/make_player_fixtures.py';

/**
 * Point the fixture's absolute URLs at whoever is serving this page.
 *
 * The manifest is generated with a baked-in base URL because the contract
 * requires absolute URIs — a real manifest carries signed CDN links. That
 * would tie the demo to one port, so the fixture (and only the fixture) is
 * retargeted on load, which lets `pnpm dev` on 3000 and the Playwright suite
 * on 3210 read the same file.
 */
function retarget<T>(value: T, origin: string): T {
  if (typeof value === 'string') {
    return value.replace(/^https?:\/\/[^/]+\/fixtures\//, `${origin}/fixtures/`) as T;
  }
  if (Array.isArray(value)) return value.map((v: unknown) => retarget(v, origin)) as T;
  if (typeof value === 'object' && value !== null) {
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, retarget(v, origin)])) as T;
  }
  return value;
}

type Load = { state: 'loading' } | { state: 'missing' } | { state: 'ready'; manifest: Manifest };

/**
 * Describe the fixture that actually loaded, rather than the one that was
 * loaded when this copy was written. The generator can emit either an AMI
 * meeting or a Libri3Mix clip, and prose that quietly describes the wrong one
 * is how a demo starts lying about itself.
 */
function FixtureNotes({ manifest }: { readonly manifest: Manifest }): React.JSX.Element {
  const meeting = manifest.speakers.some((s) => s.modality === 'audiovisual');
  const seconds = Math.round(manifest.duration_ms / 1000);
  const overlap = manifest.overlap_ratio;

  return (
    <div className="grid gap-4 md:grid-cols-3">
      <div className="rounded-[var(--radius-lg)] border border-border-subtle bg-bg-surface p-5 shadow-sm md:col-span-2">
        <h2 className="text-[15px] font-semibold">What you are listening to</h2>
        {meeting ? (
          <div className="mt-2 flex flex-col gap-2 text-sm leading-relaxed text-fg-secondary">
            <p>
              {String(manifest.speakers.length)} people in a real recorded meeting —{' '}
              {String(seconds)} seconds from the AMI Meeting Corpus. They talk over each other
              {overlap !== undefined
                ? ` about ${String(Math.round(overlap * 100))}% of the time`
                : ''}
              . The video is their four close-up cameras, tiled.
            </p>
            <p>
              Each isolated track here is that person’s own headset microphone: the ground truth a
              separator is scored against, and the ideal it aims for. Some bleed between headsets is
              audible, which is exactly why meeting audio is the hard case.
            </p>
          </div>
        ) : (
          <p className="mt-2 text-sm leading-relaxed text-fg-secondary">
            A Libri3Mix mixture with its true isolated sources, loudness-normalised to −16 LUFS and
            packaged exactly as real results are. The video is a test pattern with a running
            timestamp, so audio-video sync can be checked by eye.
          </p>
        )}
      </div>
      <div className="rounded-[var(--radius-lg)] border border-border-subtle bg-bg-surface p-5 shadow-sm">
        <h2 className="text-[15px] font-semibold">Try this</h2>
        <ul className="mt-2 flex flex-col gap-2 text-sm text-fg-secondary">
          <li>Press play, then switch speakers mid-sentence.</li>
          <li>Click any caption word to jump to it.</li>
          <li>Compare the two engines with the switch above.</li>
        </ul>
      </div>
    </div>
  );
}

export default function DemoPage() {
  const [load, setLoad] = useState<Load>({ state: 'loading' });
  // Both engines against the same material, so the difference is audible:
  // WebAudio switches in 80 ms, HLS flushes its buffer and takes a few hundred.
  const [engine, setEngine] = useState<PlaybackHint>('webaudio');

  useEffect(() => {
    let cancelled = false;
    void fetch(FIXTURE)
      .then(async (response) => {
        if (!response.ok) throw new Error('no fixtures');
        const manifest = retarget((await response.json()) as Manifest, window.location.origin);
        if (!cancelled) setLoad({ state: 'ready', manifest });
      })
      .catch(() => {
        // The fixtures are build artifacts, not committed: they are derived
        // from a 69 GB dataset and regenerate in about two minutes.
        if (!cancelled) setLoad({ state: 'missing' });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <>
      <MarketingHeader />
      <main id="content" className="bg-bg-subtle">
        <div className="container-x flex flex-col gap-8 py-10 sm:py-14">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
            <div className="flex max-w-2xl flex-col gap-3">
              <span className="inline-flex w-fit items-center gap-1.5 rounded-full border border-border-default bg-bg-surface px-2.5 py-0.5 text-[12px] font-medium text-fg-secondary">
                <span className="h-1.5 w-1.5 rounded-full bg-success" /> Live demo · no account
                needed
              </span>
              <h1 className="display text-4xl sm:text-5xl">Player demo</h1>
              <p className="text-[17px] leading-relaxed text-fg-secondary">
                People talking over each other. Pick one and you hear only them, with the picture
                still in step and captions that follow whoever is selected.
              </p>
            </div>
            {load.state === 'ready' && (
              <div className="flex flex-col items-start gap-1.5 sm:items-end">
                <span className="text-[12px] font-medium text-fg-muted">Playback engine</span>
                <div className="seg" role="group" aria-label="Playback engine">
                  <button
                    type="button"
                    className={engine === 'webaudio' ? 'seg-on' : ''}
                    onClick={() => {
                      setEngine('webaudio');
                    }}
                  >
                    WebAudio
                  </button>
                  <button
                    type="button"
                    className={engine === 'hls' ? 'seg-on' : ''}
                    onClick={() => {
                      setEngine('hls');
                    }}
                  >
                    HLS
                  </button>
                </div>
              </div>
            )}
          </div>

          {load.state === 'loading' && (
            <div className="flex flex-col gap-4">
              <Skeleton className="aspect-video w-full" />
              <Skeleton className="h-28 w-full" />
            </div>
          )}

          {load.state === 'missing' && (
            <div className="flex flex-col gap-3 rounded-[var(--radius-lg)] border border-border-subtle bg-bg-surface p-6 shadow-sm">
              <p className="font-medium">No fixtures yet. Generate them, then reload:</p>
              <pre className="code-block">{GENERATE}</pre>
              <p className="text-sm text-fg-muted">
                Needs Libri3Mix and the Whisper weights already on this machine. Runs on CPU, so it
                will not disturb a training run.
              </p>
            </div>
          )}

          {load.state === 'ready' && (
            <>
              <div className="rounded-[var(--radius-lg)] border border-border-subtle bg-bg-surface p-4 shadow-sm sm:p-6">
                <Player key={engine} manifest={load.manifest} forceEngine={engine} />
              </div>
              <FixtureNotes manifest={load.manifest} />
            </>
          )}
        </div>
      </main>
      <MarketingFooter />
    </>
  );
}
