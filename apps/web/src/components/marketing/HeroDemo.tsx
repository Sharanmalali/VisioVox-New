'use client';

/**
 * The hero: the product's one interaction, shown working.
 *
 * Four people in a call, everyone talking. Pick one - or let it cycle - and
 * that person's waveform lights while the others fall away, their tile picks up
 * a ring, and the captions switch to what they said. It is the real player's
 * idea at postcard size, built from HTML, CSS and SVG rather than a video or a
 * WebGL scene, so it costs no download and stays crisp at any width.
 *
 * It is an illustration and says so. Reduced-motion visitors get a still
 * frame with the same affordances; nothing auto-advances for them.
 */

import { useEffect, useMemo, useState } from 'react';

import { IconCaptions, IconCheck, IconWaveform } from '@/components/icons';
import { cx } from '@/components/ui';

const PEOPLE = [
  {
    name: 'Ananya',
    role: 'Product',
    line: 'Let’s ship the beta on Friday and gather feedback first.',
  },
  {
    name: 'Marcus',
    role: 'Engineering',
    line: 'The export pipeline is ready — the last tests passed this morning.',
  },
  {
    name: 'Priya',
    role: 'Design',
    line: 'I’d keep the captions larger; they’re what people actually read.',
  },
  {
    name: 'Daniel',
    role: 'Research',
    line: 'Overlap is the hard case, so let’s demo the busiest five minutes.',
  },
] as const;

const BARS = 64;
const CYCLE_MS = 3400;

/** Deterministic pseudo-random bar heights, so server and client agree. */
function lane(seed: number): number[] {
  let x = seed * 9301 + 49297;
  return Array.from({ length: BARS }, (_, i) => {
    x = (x * 9301 + 49297) % 233280;
    const r = x / 233280;
    // Talk in bursts: an envelope over the noise, phase-shifted per speaker.
    const envelope = 0.35 + 0.65 * Math.max(0, Math.sin((i / BARS) * Math.PI * 3 + seed * 1.7));
    return Math.max(0.08, r * envelope);
  });
}

function Person({ index, on }: { index: number; on: boolean }) {
  const hue = `var(--spk-${String(index + 1)})`;
  const person = PEOPLE[index];
  return (
    <div
      className={cx(
        'relative overflow-hidden rounded-[10px] transition-[box-shadow] duration-300',
        on ? 'shadow-[0_0_0_2px_var(--accent)]' : '',
      )}
      style={{
        background: `radial-gradient(120% 90% at 50% 15%, color-mix(in srgb, ${hue} 32%, #1d2030), #0d0f16 75%)`,
      }}
    >
      <svg viewBox="0 0 160 100" className="h-full w-full" aria-hidden="true">
        <circle
          cx="80"
          cy="42"
          r="17"
          fill={`color-mix(in srgb, ${hue} 55%, #e9ecf2)`}
          opacity="0.92"
        />
        <path
          d="M44 100c3-21 18-33 36-33s33 12 36 33"
          fill={`color-mix(in srgb, ${hue} 45%, #c9cfdb)`}
          opacity="0.8"
        />
      </svg>
      <div className="absolute inset-x-0 bottom-0 flex items-center justify-between gap-2 bg-gradient-to-t from-black/70 to-transparent px-2 pb-1.5 pt-4">
        <span className="truncate text-[10px] font-medium text-white/90 sm:text-[11px]">
          {person?.name}
        </span>
        <span className={cx('flex h-3 items-end gap-[2px]', on ? 'opacity-100' : 'opacity-0')}>
          {[0, 1, 2].map((b) => (
            <span
              key={b}
              className="eq-bar block w-[3px] rounded-full bg-white"
              style={{ height: '100%', animationDelay: `${String(b * 140)}ms` }}
            />
          ))}
        </span>
      </div>
    </div>
  );
}

export function HeroDemo() {
  const lanes = useMemo(() => [0, 1, 2, 3].map((i) => lane(i + 3)), []);
  const [active, setActive] = useState(0);
  const [auto, setAuto] = useState(true);
  const [still, setStill] = useState(false);
  const [playhead, setPlayhead] = useState(0.18);

  useEffect(() => {
    setStill(window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  }, []);

  useEffect(() => {
    if (!auto || still) return;
    const id = setInterval(() => {
      setActive((a) => (a + 1) % PEOPLE.length);
    }, CYCLE_MS);
    return () => {
      clearInterval(id);
    };
  }, [auto, still]);

  useEffect(() => {
    if (still) return;
    let raf = 0;
    let last = performance.now();
    const tick = (now: number) => {
      const dt = now - last;
      last = now;
      setPlayhead((p) => (p + dt / 26000) % 1);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(raf);
    };
  }, [still]);

  const person = PEOPLE[active];

  return (
    <figure className="relative">
      {/* The window */}
      <div className="overflow-hidden rounded-[18px] border border-border-default bg-bg-surface shadow-xl">
        <div className="flex items-center gap-3 border-b border-border-subtle bg-bg-subtle px-4 py-2.5">
          <div className="flex gap-1.5" aria-hidden="true">
            <span className="h-2.5 w-2.5 rounded-full bg-[#ff5f57]" />
            <span className="h-2.5 w-2.5 rounded-full bg-[#febc2e]" />
            <span className="h-2.5 w-2.5 rounded-full bg-[#28c840]" />
          </div>
          <div className="mx-auto hidden max-w-xs flex-1 truncate rounded-md border border-border-subtle bg-bg-surface px-3 py-1 text-center font-mono text-[11px] text-fg-muted sm:block">
            visiovox · weekly-sync.mp4
          </div>
          <span className="ml-auto inline-flex items-center gap-1.5 rounded-full border border-[color-mix(in_srgb,var(--success)_30%,transparent)] bg-success-soft px-2 py-0.5 text-[11px] font-medium text-success sm:ml-0">
            <IconCheck size={12} />4 speakers separated
          </span>
        </div>

        <div className="grid gap-4 p-4 sm:p-5 lg:grid-cols-[1.35fr_1fr]">
          {/* Stage */}
          <div className="flex flex-col gap-3">
            <div className="relative aspect-video overflow-hidden rounded-[12px] bg-[#07080c] p-1.5">
              <div className="grid h-full grid-cols-2 grid-rows-2 gap-1.5">
                {PEOPLE.map((p, i) => (
                  <Person key={p.name} index={i} on={i === active} />
                ))}
              </div>
              <div className="pointer-events-none absolute inset-x-3 bottom-3 flex justify-center">
                <p
                  key={active}
                  className="animate-fade-up max-w-[92%] rounded-md bg-black/75 px-3 py-1.5 text-center text-[11px] leading-snug text-white backdrop-blur sm:text-[13px]"
                  aria-live="polite"
                >
                  <span
                    className="font-semibold"
                    style={{ color: `var(--spk-${String(active + 1)})` }}
                  >
                    {person?.name}:
                  </span>{' '}
                  {person?.line}
                </p>
              </div>
            </div>

            {/* Lanes */}
            <div className="relative rounded-[12px] border border-border-subtle bg-bg-inset p-3">
              <div className="flex flex-col gap-1.5">
                {lanes.map((heights, i) => (
                  <div
                    key={i}
                    className={cx(
                      'flex h-7 items-center justify-between transition-opacity duration-300',
                      i === active ? 'opacity-100' : 'opacity-25',
                    )}
                  >
                    {heights.map((h, j) => (
                      <span
                        key={j}
                        className="block w-[3px] shrink-0 rounded-full"
                        style={{
                          height: `${String(Math.round(h * 100))}%`,
                          background: `var(--spk-${String(i + 1)})`,
                        }}
                      />
                    ))}
                  </div>
                ))}
              </div>
              <span
                aria-hidden="true"
                className="absolute inset-y-2 w-px bg-fg-primary/70"
                style={{ left: `calc(12px + (100% - 24px) * ${String(playhead)})` }}
              />
            </div>
          </div>

          {/* Rail */}
          <div className="flex flex-col gap-3">
            <div className="flex items-center justify-between">
              <p className="text-[13px] font-semibold">Listen to</p>
              <span className="inline-flex items-center gap-1 text-[11px] text-fg-muted">
                <IconWaveform size={13} /> 80 ms switch
              </span>
            </div>
            <div
              role="radiogroup"
              aria-label="Preview speaker"
              className="grid grid-cols-2 gap-2 lg:grid-cols-1"
            >
              {PEOPLE.map((p, i) => {
                const on = i === active;
                return (
                  <button
                    key={p.name}
                    type="button"
                    role="radio"
                    aria-checked={on}
                    onClick={() => {
                      setActive(i);
                      setAuto(false);
                    }}
                    className={cx(
                      'flex items-center gap-3 rounded-[10px] border px-3 py-2 text-left transition-[border-color,box-shadow,background-color] duration-200',
                      on
                        ? 'border-accent bg-accent-soft shadow-[0_0_0_3px_var(--accent-ring)]'
                        : 'border-border-subtle bg-bg-surface hover:border-border-strong',
                    )}
                  >
                    <span
                      className="grid h-7 w-7 shrink-0 place-items-center rounded-full text-[11px] font-semibold text-white"
                      style={{ background: `var(--spk-${String(i + 1)})` }}
                    >
                      {i + 1}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[13px] font-medium">{p.name}</span>
                      <span className="block truncate text-[11px] text-fg-muted">{p.role}</span>
                    </span>
                    {on && <IconCheck size={15} className="shrink-0 text-accent" />}
                  </button>
                );
              })}
            </div>
            <div className="mt-auto hidden items-start gap-2 rounded-[10px] border border-border-subtle bg-bg-subtle p-3 lg:flex">
              <IconCaptions size={16} className="mt-0.5 shrink-0 text-fg-muted" />
              <p className="text-[12px] leading-relaxed text-fg-secondary">
                Captions follow whoever you choose. Click any word in the real player to jump to it.
              </p>
            </div>
          </div>
        </div>
      </div>
      <figcaption className="mt-3 text-center text-[12px] text-fg-muted">
        Interface illustration. Try it on a real recording in the{' '}
        <a href="/demo" className="font-medium text-accent hover:underline">
          live demo
        </a>
        .
      </figcaption>
    </figure>
  );
}
