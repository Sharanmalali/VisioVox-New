'use client';

/**
 * The player: pick a speaker, hear only them, in sync with the picture.
 *
 * The component owns the DOM and the engine owns the clock. Nothing here
 * measures time by counting frames or running an interval — the media position
 * is read from the video element once per frame and everything else derives
 * from it, so captions and highlighting cannot drift away from the audio.
 *
 * Selecting a speaker is deliberately not awaited anywhere: the engine
 * schedules the crossfade on the audio thread and it completes in 80 ms
 * whatever the main thread is doing, so blocking the UI on it would add
 * latency rather than remove it.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import type { AudioMode, Manifest, PlaybackHint, Speaker, TrackId } from '@/lib/playback/manifest';
import { MIXED_TRACK, hasNaturalMode } from '@/lib/playback/manifest';
import type { CaptionIndex, CaptionSegment } from '@/lib/playback/captions';
import {
  EMPTY_CAPTION_INDEX,
  buildCaptionIndex,
  parseTranscript,
  segmentAt,
  wordAt,
} from '@/lib/playback/captions';
import type { EngineState, PlaybackEngine } from '@/lib/playback/engine';
import { WebAudioSyncEngine } from '@/lib/playback/web-audio-engine';
import { HlsSyncEngine } from '@/lib/playback/hls-engine';
import { MAX_AV_OFFSET_MS, resolveEngine } from '@/lib/playback/sync';
import type { TranscriptLine } from '@/lib/playback/transcript';
import { mergeTranscript } from '@/lib/playback/transcript';

/** What a side panel (the transcript) can read from and do to the player. */
export interface PlayerAsideContext {
  readonly lines: readonly TranscriptLine[];
  /** False until every speaker's captions have been fetched (or failed). */
  readonly captionsReady: boolean;
  /**
   * Face-anchored diarization per speaker ordinal, when every speaker has it;
   * null for projects processed before it shipped.
   */
  readonly activity: ReadonlyMap<number, readonly (readonly [number, number])[]> | null;
  readonly mediaMs: number;
  readonly activeId: TrackId;
  readonly ready: boolean;
  readonly speakers: readonly Speaker[];
  readonly nameOf: (ordinal: number) => string;
  readonly seekToMs: (ms: number) => void;
  /** Switch to this line's speaker, jump to it, and play. */
  readonly playLine: (line: TranscriptLine) => void;
  /** Switch the audio to a speaker, or to the original mix. */
  readonly selectTrack: (id: TrackId) => void;
  /** Jump to a time and play from there. */
  readonly playFrom: (ms: number) => void;
}

interface PlayerProps {
  readonly manifest: Manifest;
  /**
   * Override the server's engine choice. Only the demo page uses this — in
   * production the policy lives in `playback_hint` so the thresholds exist in
   * one place, and a client that could silently upgrade itself to WebAudio
   * would decode a two-hour recording into memory.
   */
  readonly forceEngine?: PlaybackHint;
  /**
   * Names by ordinal ("1" -> "Priya"), overriding the manifest's labels. The
   * project page passes these so a rename shows at once, without refetching
   * the manifest and reloading every audio track.
   */
  readonly names?: Readonly<Record<string, string>>;
  /** A panel beside the player, given the player's state (the transcript). */
  readonly aside?: (context: PlayerAsideContext) => ReactNode;
  /** A full-width panel under the player and its aside (insights). */
  readonly below?: (context: PlayerAsideContext) => ReactNode;
  /**
   * Translated captions, keyed like transcript lines (`speakerId:start_ms`).
   * The caption box then shows the translation with the original under it.
   */
  readonly translated?: ReadonlyMap<string, string> | null;
}

export function Player({
  manifest,
  forceEngine,
  names,
  aside,
  below,
  translated,
}: PlayerProps): React.JSX.Element {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const engineRef = useRef<PlaybackEngine | null>(null);

  const [mode, setMode] = useState<AudioMode>('faithful');
  const [activeId, setActiveId] = useState<TrackId>(manifest.speakers[0]?.id ?? MIXED_TRACK);
  const [ready, setReady] = useState(false);
  const [drift, setDrift] = useState(0);
  const [offsetMs, setOffsetMs] = useState(0);
  const [mediaMs, setMediaMs] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [engineState, setEngineState] = useState<EngineState>('idle');
  const [captions, setCaptions] = useState<Record<string, CaptionIndex>>({});
  const [captionsReady, setCaptionsReady] = useState(false);

  const engineKind = useMemo(
    () => forceEngine ?? resolveEngine(manifest, typeof AudioContext !== 'undefined'),
    [manifest, forceEngine],
  );
  const naturalAvailable = useMemo(() => hasNaturalMode(manifest), [manifest]);

  // ------------------------------------------------------------ engine wiring

  useEffect(() => {
    const video = videoRef.current;
    if (video === null) return;

    const engine: PlaybackEngine =
      engineKind === 'hls' ? new HlsSyncEngine({ mode }) : new WebAudioSyncEngine({ mode });
    engineRef.current = engine;

    const offTrack = engine.on('trackchange', setActiveId);
    const offDrift = engine.on('drift', setDrift);
    const offState = engine.on('state', setEngineState);
    const offError = engine.on('error', (e) => {
      setError(e.message);
    });

    let cancelled = false;
    void engine
      .load(manifest, video)
      .then(() => {
        if (cancelled) return;
        setReady(true);
        setOffsetMs(engine.getOffsetMs());
        const active = engine.getActiveTrack();
        if (active !== null) setActiveId(active);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Could not load audio');
      });

    return () => {
      cancelled = true;
      offTrack();
      offDrift();
      offState();
      offError();
      engine.destroy();
      engineRef.current = null;
      setReady(false);
    };
  }, [manifest, mode, engineKind]);

  // --------------------------------------------------------- caption loading

  useEffect(() => {
    let cancelled = false;
    async function loadCaptions(): Promise<void> {
      const entries = await Promise.all(
        manifest.speakers.map(async (speaker): Promise<[string, CaptionIndex] | null> => {
          const url = speaker.captions.json;
          if (url === undefined) return null;
          try {
            const response = await fetch(url);
            if (!response.ok) return null;
            const transcript = parseTranscript(await response.json());
            return transcript === null ? null : [speaker.id, buildCaptionIndex(transcript)];
          } catch {
            // A missing transcript costs captions for one speaker, not the
            // session. The warning already rides in manifest.warnings.
            return null;
          }
        }),
      );
      if (cancelled) return;
      setCaptions(Object.fromEntries(entries.filter((e) => e !== null)));
      setCaptionsReady(true);
    }
    void loadCaptions();
    return () => {
      cancelled = true;
    };
  }, [manifest]);

  // -------------------------------------------------------- media time ticker

  useEffect(() => {
    let raf = 0;
    const tick = (): void => {
      const video = videoRef.current;
      if (video !== null) setMediaMs(video.currentTime * 1000);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(raf);
    };
  }, []);

  // -------------------------------------------------------------- interaction

  const select = useCallback((id: TrackId): void => {
    setActiveId(id);
    void engineRef.current?.selectTrack(id);
  }, []);

  const seekToMs = useCallback((ms: number): void => {
    const video = videoRef.current;
    if (video !== null) video.currentTime = ms / 1000;
  }, []);

  const changeOffset = useCallback((value: number): void => {
    setOffsetMs(value);
    engineRef.current?.setOffsetMs(value);
  }, []);

  const activeIndex = captions[activeId] ?? EMPTY_CAPTION_INDEX;
  const segment = segmentAt(activeIndex, mediaMs);

  const nameOf = useCallback(
    (ordinal: number): string => {
      if (names !== undefined) return names[String(ordinal)] ?? `Speaker ${String(ordinal)}`;
      return (
        manifest.speakers.find((s) => s.ordinal === ordinal)?.label ?? `Speaker ${String(ordinal)}`
      );
    },
    [names, manifest],
  );

  const lines = useMemo(() => mergeTranscript(manifest.speakers, captions), [manifest, captions]);

  const activity = useMemo(() => {
    const out = new Map<number, readonly (readonly [number, number])[]>();
    for (const speaker of manifest.speakers) {
      const intervals = captions[speaker.id]?.activity;
      if (intervals === undefined) return null;
      out.set(speaker.ordinal, intervals);
    }
    return out;
  }, [manifest, captions]);

  const playLine = useCallback(
    (line: TranscriptLine): void => {
      const speaker = manifest.speakers.find((s) => s.id === line.speakerId);
      if (speaker?.extraction_ok === true) select(speaker.id);
      seekToMs(line.start_ms);
      void videoRef.current?.play().catch(() => undefined);
    },
    [manifest, select, seekToMs],
  );

  const playFrom = useCallback(
    (ms: number): void => {
      seekToMs(ms);
      void videoRef.current?.play().catch(() => undefined);
    },
    [seekToMs],
  );

  const player = (
    <div className="player stack">
      <div className="stage">
        {/* `muted` is set by the engine, not here. WebAudio mutes the element
            because it renders the audio itself; HLS must not, because the
            element is what carries the sound. Declaring it in JSX would let
            React reassert it on any re-render and silence the HLS path. */}
        {manifest.has_video && manifest.video !== undefined ? (
          <video
            ref={videoRef}
            className="stage-video"
            src={engineKind === 'hls' ? undefined : manifest.video.url}
            controls
            playsInline
            preload="auto"
          />
        ) : (
          <div className="stage-audio-only">
            <video ref={videoRef} controls preload="auto" />
            <p className="muted">Audio only — no video track in this recording.</p>
          </div>
        )}
      </div>

      {error !== null && <p className="error">{error}</p>}

      {engineState === 'switching' && (
        <p className="muted switching" role="status">
          Switching speaker…
        </p>
      )}

      <SpeakerRail
        speakers={manifest.speakers}
        activeId={activeId}
        ready={ready}
        onSelect={select}
        nameOf={nameOf}
      />

      <CaptionView
        segment={segment}
        mediaMs={mediaMs}
        onSeek={seekToMs}
        translation={
          segment === null ? undefined : translated?.get(`${activeId}:${String(segment.start_ms)}`)
        }
      />

      <div className="player-controls row">
        {naturalAvailable && (
          <div className="seg" role="group" aria-label="Audio mode">
            <button
              type="button"
              className={mode === 'faithful' ? 'seg-on' : ''}
              onClick={() => {
                setMode('faithful');
              }}
            >
              Faithful
            </button>
            <button
              type="button"
              className={mode === 'natural' ? 'seg-on' : ''}
              onClick={() => {
                setMode('natural');
              }}
            >
              Natural
            </button>
          </div>
        )}

        {/* Offered only on WebAudio. Under HLS one element carries both
            streams, so nothing in the page can shift them against each other
            and a slider that appeared to would be theatre. */}
        {engineKind === 'webaudio' && (
          <label className="offset">
            Audio delay
            <input
              type="range"
              min={-MAX_AV_OFFSET_MS}
              max={MAX_AV_OFFSET_MS}
              step={10}
              value={offsetMs}
              onChange={(e) => {
                changeOffset(Number(e.target.value));
              }}
            />
            <output>{String(Math.round(offsetMs))} ms</output>
          </label>
        )}

        <span className="muted drift" title="Video minus audio, corrected continuously">
          {engineKind === 'webaudio'
            ? `drift ${String(Math.round(drift))} ms`
            : 'streaming · browser-synced'}
        </span>
      </div>
    </div>
  );

  if (aside === undefined && below === undefined) return player;
  const context: PlayerAsideContext = {
    lines,
    captionsReady,
    activity,
    mediaMs,
    activeId,
    ready,
    speakers: manifest.speakers,
    nameOf,
    seekToMs,
    playLine,
    selectTrack: select,
    playFrom,
  };
  return (
    <div className="flex flex-col gap-6">
      {aside === undefined ? (
        player
      ) : (
        <div className="player-grid">
          {player}
          <div className="player-aside">{aside(context)}</div>
        </div>
      )}
      {below?.(context)}
    </div>
  );
}

// ----------------------------------------------------------------- speaker rail

interface SpeakerRailProps {
  readonly speakers: readonly Speaker[];
  readonly activeId: TrackId;
  readonly ready: boolean;
  readonly onSelect: (id: TrackId) => void;
  readonly nameOf: (ordinal: number) => string;
}

function SpeakerRail({
  speakers,
  activeId,
  ready,
  onSelect,
  nameOf,
}: SpeakerRailProps): React.JSX.Element {
  return (
    <div className="rail" role="radiogroup" aria-label="Speaker">
      {speakers.map((speaker) => (
        <button
          key={speaker.id}
          type="button"
          role="radio"
          aria-checked={activeId === speaker.id}
          className={'rail-item' + (activeId === speaker.id ? ' rail-on' : '')}
          disabled={!ready || !speaker.extraction_ok}
          onClick={() => {
            onSelect(speaker.id);
          }}
        >
          {speaker.thumbnail_url !== undefined ? (
            // A plain <img>, not next/image: these are signed CDN URLs with an
            // expiry, and the image optimiser would cache them past
            // signed_until and then serve 403s from its own cache.
            <img className="rail-face" src={speaker.thumbnail_url} alt="" />
          ) : (
            <span className="rail-face rail-face-none" aria-hidden="true">
              {String(speaker.ordinal)}
            </span>
          )}
          <span className="rail-label">{nameOf(speaker.ordinal)}</span>
          <span className="rail-meta muted">
            {nameOf(speaker.ordinal) !== `Speaker ${String(speaker.ordinal)}`
              ? `Speaker ${String(speaker.ordinal)} · `
              : ''}
            {String(Math.round(speaker.speaking_ratio * 100))}% ·{' '}
            {speaker.modality === 'audio_only' ? 'no face' : 'face + voice'}
          </span>
        </button>
      ))}

      <button
        type="button"
        role="radio"
        aria-checked={activeId === MIXED_TRACK}
        className={'rail-item' + (activeId === MIXED_TRACK ? ' rail-on' : '')}
        disabled={!ready}
        onClick={() => {
          onSelect(MIXED_TRACK);
        }}
      >
        <span className="rail-face rail-face-none" aria-hidden="true">
          ∑
        </span>
        <span className="rail-label">Original mix</span>
        <span className="rail-meta muted">everyone, unprocessed</span>
      </button>
    </div>
  );
}

// --------------------------------------------------------------------- captions

interface CaptionViewProps {
  readonly segment: CaptionSegment | null;
  readonly mediaMs: number;
  readonly onSeek: (ms: number) => void;
  readonly translation?: string | undefined;
}

function CaptionView({
  segment,
  mediaMs,
  onSeek,
  translation,
}: CaptionViewProps): React.JSX.Element {
  if (segment === null) {
    return (
      <p className="captions captions-idle muted" aria-live="off">
        —
      </p>
    );
  }

  if (translation !== undefined) {
    // Word timings do not survive translation, so the whole line shows.
    return (
      <p className="captions" aria-live="polite">
        {translation}
        <span className="captions-original">{segment.text}</span>
      </p>
    );
  }

  const current = wordAt(segment, mediaMs);
  if (segment.words.length === 0) {
    return (
      <p className="captions" aria-live="polite">
        {segment.text}
      </p>
    );
  }

  return (
    <p className="captions" aria-live="polite">
      {segment.words.map((word, i) => (
        <button
          key={String(word.start_ms) + ':' + String(i)}
          type="button"
          className={'word' + (current === word ? ' word-on' : '')}
          // Confidence is disclosed rather than hidden: a word the model was
          // unsure of is exactly the word a reader should check against the
          // audio, and dimming it is the cheapest way to say so.
          style={word.probability < 0.6 ? { opacity: 0.55 } : undefined}
          onClick={() => {
            onSeek(word.start_ms);
          }}
        >
          {word.text}
        </button>
      ))}
    </p>
  );
}
