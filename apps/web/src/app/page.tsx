/**
 * Landing.
 *
 * A Server Component: the copy is real HTML on first paint, and the only
 * client islands are the header and the hero demo. Every number on this page
 * is measured (docs/29, the production job table), every limit is stated, and
 * nothing here is social proof we do not have - no logos, no testimonials.
 */

import type { ReactNode } from 'react';

import {
  IconArrowRight,
  IconCaptions,
  IconCheck,
  IconCheckCircle,
  IconCloudUpload,
  IconCpu,
  IconDownload,
  IconFace,
  IconGlobe,
  IconLayers,
  IconLock,
  IconPlay,
  IconShield,
  IconSparkles,
  IconUsers,
  IconWaveform,
  IconZap,
} from '@/components/icons';
import { HeroDemo } from '@/components/marketing/HeroDemo';
import { MarketingFooter } from '@/components/shell/MarketingFooter';
import { MarketingHeader } from '@/components/shell/MarketingHeader';
import { ButtonLink, SectionHeader } from '@/components/ui';
import { cx } from '@/lib/cx';

// ---------------------------------------------------------------- content --

const FEATURES: ReadonlyArray<{
  icon: ReactNode;
  title: string;
  body: string;
  wide?: boolean;
  visual?: ReactNode;
}> = [
  {
    icon: <IconFace />,
    title: 'Follows faces, not guesses',
    body: 'Each person’s lip movement steers the extraction of their voice, so the right words end up on the right track — even when everyone talks at once.',
    wide: true,
    visual: <FaceLanes />,
  },
  {
    icon: <IconGlobe />,
    title: 'Captions in 100 languages',
    body: 'Word-timed captions per speaker. Pick the spoken language, including 14 Indian languages, or let it be detected.',
    visual: <ScriptCloud />,
  },
  {
    icon: <IconUsers />,
    title: 'One person, one track',
    body: 'Face identity keeps someone together across camera cuts and turns of the head, so one person never shows up as two.',
  },
  {
    icon: <IconZap />,
    title: 'Switch in 80 milliseconds',
    body: 'Jump between speakers mid-sentence. The audio crossfades instantly and the picture never drifts out of sync.',
  },
  {
    icon: <IconDownload />,
    title: 'Download or share',
    body: 'Export any speaker as video or audio at the source’s quality, or send a link that you can revoke at any time.',
  },
  {
    icon: <IconLock />,
    title: 'Private by design',
    body: 'Processed on our own hardware. Face crops and voiceprints are deleted the moment a job finishes.',
  },
  {
    icon: <IconCaptions />,
    title: 'Shows its uncertainty',
    body: 'Words the recogniser is unsure of are dimmed, not hidden, and every word is a link to its moment in the audio.',
  },
  {
    icon: <IconCloudUpload />,
    title: 'Picks up where you left off',
    body: 'Large uploads are sent in parts and resume after a dropped connection. Close the tab mid-job and come back later.',
  },
];

const STEPS = [
  {
    icon: <IconCloudUpload />,
    title: 'Upload',
    body: 'Drop a meeting, interview or panel — up to 60 minutes. Uploads resume if your connection drops.',
  },
  {
    icon: <IconFace />,
    title: 'We find every face',
    body: 'Each visible person is tracked through the video, across cuts and movement.',
  },
  {
    icon: <IconLayers />,
    title: 'Voices are separated',
    body: 'Every speaker gets a clean, loudness-matched track of their own, with captions.',
  },
  {
    icon: <IconPlay />,
    title: 'Listen, read, share',
    body: 'Pick anyone and hear only them, in sync with the video. Export or share in a click.',
  },
];

const INDIAN = [
  ['English', 'English'],
  ['हिन्दी', 'Hindi'],
  ['বাংলা', 'Bengali'],
  ['తెలుగు', 'Telugu'],
  ['मराठी', 'Marathi'],
  ['தமிழ்', 'Tamil'],
  ['اردو', 'Urdu'],
  ['ગુજરાતી', 'Gujarati'],
  ['ಕನ್ನಡ', 'Kannada'],
  ['മലയാളം', 'Malayalam'],
  ['ਪੰਜਾਬੀ', 'Punjabi'],
  ['অসমীয়া', 'Assamese'],
  ['नेपाली', 'Nepali'],
  ['سنڌي', 'Sindhi'],
  ['संस्कृतम्', 'Sanskrit'],
] as const;

const WORLD = [
  'Arabic',
  'Chinese',
  'French',
  'German',
  'Indonesian',
  'Italian',
  'Japanese',
  'Korean',
  'Portuguese',
  'Russian',
  'Spanish',
  'Swahili',
  'Thai',
  'Turkish',
  'Vietnamese',
];

const RESULTS = [
  {
    value: '+3.5 dB',
    label: 'Cleaner overlapped speech',
    note: 'When the speaker’s face is visible, measured on 65 speakers across 30 real AMI meetings.',
  },
  {
    value: '−32 pts',
    label: 'Fewer caption attribution errors',
    note: 'Cross-track deduplication: 110% → 78% speaker-attributed WER on 10 held-out meetings.',
  },
  {
    value: '0',
    label: 'People split into two tracks',
    note: 'On held-out meetings with face-identity merging, down from 11 of 38 with voice alone.',
  },
  {
    value: '2.6×',
    label: 'Median processing time',
    note: 'Relative to video length, across 24 production jobs on a single workstation GPU.',
  },
];

const PRIVACY = [
  {
    icon: <IconCpu />,
    title: 'Processed on our own hardware',
    body: 'Your video is never sent to a third-party AI service. Separation and captions run on our own GPU.',
  },
  {
    icon: <IconFace />,
    title: 'Biometrics are temporary',
    body: 'Face crops and voiceprints exist only while your job runs, and are deleted when it finishes.',
  },
  {
    icon: <IconShield />,
    title: 'No identification, by design',
    body: 'VisioVox never matches a face or voice across recordings. It separates people; it does not recognise them.',
  },
  {
    icon: <IconLock />,
    title: 'Links you control',
    body: 'Share links can be revoked instantly, expire automatically, and are never indexed by search engines.',
  },
];

const FAQ = [
  {
    q: 'Does it work live, during a call?',
    a: 'No. VisioVox works on a finished recording. Processing typically takes two to three times the length of the video.',
  },
  {
    q: 'How many people can it separate?',
    a: 'Up to four speakers per video. Quality is best with two or three; at four it drops noticeably.',
  },
  {
    q: 'What if someone is off camera?',
    a: 'Separation follows faces, so a person who is never on screen is not given a track of their own. Their speech may still appear in others’ captions.',
  },
  {
    q: 'Which languages are supported for captions?',
    a: 'All 100 languages of the Whisper large-v3 recogniser, including 14 Indian languages. Odia, Maithili, Konkani, Kashmiri, Manipuri, Bodo, Dogri and Santali are not supported yet.',
  },
  {
    q: 'Will it make up words that were not said?',
    a: 'No. Captions are transcribed from the separated audio itself, not from any restored or generated version, and words the recogniser is unsure of are shown dimmed.',
  },
  {
    q: 'Can it handle music or singing?',
    a: 'It is built for speech. Music and singing are not separated well, and are not a supported use.',
  },
];

// ------------------------------------------------------------- visuals --

/** Four lanes, one per face: what the feature card is talking about. */
function FaceLanes() {
  const rows = [
    [3, 7, 4, 9, 6, 2, 8, 5, 3, 7, 9, 4, 2, 6, 8, 3, 5, 9, 4, 6, 2, 7],
    [6, 2, 8, 3, 9, 5, 2, 7, 6, 3, 4, 8, 7, 2, 5, 9, 3, 6, 8, 2, 7, 4],
    [2, 5, 3, 6, 2, 8, 9, 4, 7, 2, 6, 3, 9, 5, 2, 4, 8, 3, 2, 9, 5, 6],
    [8, 4, 6, 2, 5, 3, 7, 2, 9, 6, 3, 5, 4, 8, 6, 2, 3, 7, 9, 5, 4, 3],
  ];
  return (
    <div className="flex flex-col gap-2.5" aria-hidden="true">
      {rows.map((bars, i) => (
        <div key={i} className="flex items-center gap-3">
          <span
            className="grid h-8 w-8 shrink-0 place-items-center rounded-full text-[12px] font-semibold text-white shadow-sm"
            style={{ background: `var(--spk-${String(i + 1)})` }}
          >
            {i + 1}
          </span>
          <div className="flex h-8 flex-1 items-center justify-between">
            {[...bars, ...bars.slice().reverse()].map((h, j) => (
              <span
                key={j}
                className="w-[4px] shrink-0 rounded-full"
                style={{
                  height: `${String(h * 10)}%`,
                  background: `var(--spk-${String(i + 1)})`,
                  opacity: 0.85,
                }}
              />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

/** A handful of scripts, to show "100 languages" rather than state it. */
function ScriptCloud() {
  const samples = [
    'नमस्ते',
    'வணக்கம்',
    'Hello',
    'ನಮಸ್ಕಾರ',
    'নমস্কার',
    'Hola',
    'سلام',
    'നമസ്കാരം',
    'こんにちは',
  ];
  return (
    <ul className="flex flex-wrap gap-2" aria-hidden="true">
      {samples.map((s, i) => (
        <li
          key={s}
          className="rounded-full border border-border-default bg-bg-surface px-3 py-1 text-[13px] text-fg-secondary shadow-xs"
          style={i === 0 ? { borderColor: 'var(--accent)', color: 'var(--accent)' } : undefined}
        >
          {s}
        </li>
      ))}
    </ul>
  );
}

function Check({ children }: { children: ReactNode }) {
  return (
    <li className="flex items-start gap-2.5 text-[15px] text-fg-secondary">
      <IconCheck size={18} className="mt-0.5 shrink-0 text-success" />
      <span>{children}</span>
    </li>
  );
}

// ------------------------------------------------------------------ page --

export default function Home() {
  return (
    <>
      <MarketingHeader />
      <main id="content">
        {/* ---------------------------------------------------------------- hero */}
        <section className="relative overflow-hidden">
          <div className="pointer-events-none absolute inset-0 -z-10 bg-grid" aria-hidden="true" />
          <div
            className="pointer-events-none absolute inset-0 -z-10 glow-backdrop"
            aria-hidden="true"
          />

          <div className="container-x flex flex-col items-center pb-16 pt-14 text-center sm:pt-20 lg:pb-24">
            <a
              href="#languages"
              className="animate-fade-up group inline-flex items-center gap-2 rounded-full border border-border-default bg-bg-surface/80 py-1 pl-1 pr-3 text-[13px] text-fg-secondary shadow-xs backdrop-blur transition-colors hover:border-border-strong"
            >
              <span className="rounded-full bg-accent-soft px-2 py-0.5 text-[12px] font-semibold text-accent">
                New
              </span>
              Captions in 100 languages, including 14 Indian
              <IconArrowRight
                size={14}
                className="transition-transform group-hover:translate-x-0.5"
              />
            </a>

            <h1
              className="animate-fade-up display mt-7 max-w-4xl text-[44px] sm:text-6xl lg:text-[76px]"
              style={{ animationDelay: '60ms' }}
            >
              Hear every voice.
              <br />
              <span className="text-gradient">One at a time.</span>
            </h1>

            <p
              className="animate-fade-up mt-6 max-w-2xl text-[17px] leading-relaxed text-fg-secondary sm:text-lg"
              style={{ animationDelay: '120ms' }}
            >
              Upload a video where people talk over each other. VisioVox follows every face, gives
              each speaker their own clean audio track, and captions what they said — in sync with
              the video, right in your browser.
            </p>

            <div
              className="animate-fade-up mt-9 flex flex-col gap-3 sm:flex-row"
              style={{ animationDelay: '180ms' }}
            >
              <ButtonLink href="/login" size="lg">
                Get started free
                <IconArrowRight size={18} />
              </ButtonLink>
              <ButtonLink href="/demo" size="lg" variant="secondary">
                <IconPlay size={16} />
                Try the live demo
              </ButtonLink>
            </div>

            <ul
              className="animate-fade-up mt-7 flex flex-wrap justify-center gap-x-6 gap-y-2 text-[13px] text-fg-muted"
              style={{ animationDelay: '240ms' }}
            >
              {['Nothing to install', 'Videos up to 60 minutes', 'Up to 4 speakers'].map((t) => (
                <li key={t} className="inline-flex items-center gap-1.5">
                  <IconCheckCircle size={15} className="text-success" />
                  {t}
                </li>
              ))}
            </ul>

            <div
              className="animate-fade-up mt-14 w-full max-w-5xl text-left sm:mt-16"
              style={{ animationDelay: '320ms' }}
            >
              <HeroDemo />
            </div>
          </div>
        </section>

        {/* ------------------------------------------------------ credibility */}
        <section className="border-y border-border-subtle bg-bg-subtle" aria-label="Foundations">
          <div className="container-x flex flex-col items-center gap-5 py-10">
            <p className="text-center text-[13px] font-medium text-fg-muted">
              Evaluated on 30 real meetings from the AMI Meeting Corpus. Built on open research.
            </p>
            <ul className="flex flex-wrap items-center justify-center gap-x-10 gap-y-3">
              {['AV-MossFormer2', 'Whisper large-v3', 'ArcFace', 'ECAPA-TDNN', 'AMI Corpus'].map(
                (n) => (
                  <li
                    key={n}
                    className="text-[15px] font-semibold tracking-[-0.01em] text-fg-secondary/80"
                  >
                    {n}
                  </li>
                ),
              )}
            </ul>
          </div>
        </section>

        {/* --------------------------------------------------------- features */}
        <section id="product" className="container-x py-24 sm:py-32">
          <SectionHeader eyebrow="Product" title="Everything you need to hear who said what.">
            Meeting tools give you labelled text and leave the audio mixed. VisioVox gives every
            person their own track, their own captions, and the video, all in step.
          </SectionHeader>

          <div className="mt-14 grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {FEATURES.map((f) => (
              <article
                key={f.title}
                className={cx(
                  'group relative flex flex-col gap-4 overflow-hidden rounded-[var(--radius-lg)] border border-border-subtle bg-bg-surface p-7 shadow-sm transition-[box-shadow,border-color,transform] duration-300 hover:-translate-y-0.5 hover:border-border-default hover:shadow-lg',
                  f.wide === true && 'md:col-span-2',
                )}
              >
                <span className="grid h-10 w-10 place-items-center rounded-[var(--radius-md)] border border-border-subtle bg-bg-subtle text-fg-primary">
                  {f.icon}
                </span>
                <div className={cx('flex flex-col gap-2', f.wide === true && 'max-w-md')}>
                  <h3 className="text-lg font-semibold">{f.title}</h3>
                  <p className="text-[15px] leading-relaxed text-fg-secondary">{f.body}</p>
                </div>
                {f.visual !== undefined && (
                  <div className="mt-2 rounded-[var(--radius-md)] border border-border-subtle bg-bg-subtle p-5">
                    {f.visual}
                  </div>
                )}
              </article>
            ))}
          </div>
        </section>

        {/* ----------------------------------------------------- how it works */}
        <section
          id="how-it-works"
          className="border-y border-border-subtle bg-bg-subtle py-24 sm:py-32"
        >
          <div className="container-x">
            <SectionHeader
              eyebrow="How it works"
              title="From a crowded recording to clear voices in four steps."
            />
            <ol className="relative mt-16 grid gap-10 md:grid-cols-2 lg:grid-cols-4 lg:gap-6">
              <span
                aria-hidden="true"
                className="absolute left-0 right-0 top-6 hidden h-px bg-gradient-to-r from-transparent via-border-default to-transparent lg:block"
              />
              {STEPS.map((s, i) => (
                <li key={s.title} className="relative flex flex-col gap-4">
                  <span className="relative grid h-12 w-12 place-items-center rounded-full border border-border-default bg-bg-surface text-fg-primary shadow-sm">
                    {s.icon}
                    <span className="absolute -right-1 -top-1 grid h-5 w-5 place-items-center rounded-full bg-primary text-[11px] font-semibold text-primary-fg">
                      {i + 1}
                    </span>
                  </span>
                  <h3 className="text-lg font-semibold">{s.title}</h3>
                  <p className="text-[15px] leading-relaxed text-fg-secondary">{s.body}</p>
                </li>
              ))}
            </ol>
          </div>
        </section>

        {/* -------------------------------------------------------- languages */}
        <section id="languages" className="container-x py-24 sm:py-32">
          <div className="relative overflow-hidden rounded-[28px] bg-[#0b0d16] px-6 py-14 text-white shadow-xl sm:px-12 sm:py-20">
            <div
              aria-hidden="true"
              className="pointer-events-none absolute inset-0 opacity-90"
              style={{
                background:
                  'radial-gradient(45% 60% at 15% 10%, rgba(99,102,241,.45), transparent 70%), radial-gradient(40% 55% at 90% 90%, rgba(14,165,233,.3), transparent 70%)',
              }}
            />
            <div className="relative grid gap-12 lg:grid-cols-[1fr_1.2fr] lg:items-center">
              <div className="flex flex-col gap-5">
                <span className="text-[13px] font-semibold text-indigo-200">Languages</span>
                <h2 className="display text-3xl sm:text-[44px]">
                  Speaks your language. Including fourteen from India.
                </h2>
                <p className="text-[17px] leading-relaxed text-white/75">
                  Captions in 100 languages. Choose the spoken language when you upload — useful for
                  accents automatic detection can misread — or let VisioVox detect it.
                </p>
                <p className="text-[13px] leading-relaxed text-white/55">
                  Not yet supported: Odia, Maithili, Konkani, Kashmiri, Manipuri, Bodo, Dogri and
                  Santali. Speaker separation itself works in any language.
                </p>
              </div>
              <div className="flex flex-col gap-6">
                <ul className="grid grid-cols-3 gap-2.5">
                  {INDIAN.map(([native, english]) => (
                    <li
                      key={english}
                      className="flex flex-col rounded-[14px] border border-white/10 bg-white/[0.06] px-4 py-3 backdrop-blur transition-colors hover:bg-white/[0.1]"
                    >
                      <span className="text-lg font-medium leading-tight" lang="und">
                        {native}
                      </span>
                      <span className="text-[12px] text-white/55">{english}</span>
                    </li>
                  ))}
                </ul>
                <p className="text-sm text-white/65">
                  Plus {WORLD.join(', ')} <span className="text-white/45">and 70 more.</span>
                </p>
              </div>
            </div>
          </div>
        </section>

        {/* ---------------------------------------------------------- results */}
        <section id="results" className="container-x pb-24 sm:pb-32">
          <SectionHeader
            eyebrow="Measured results"
            title="Measured on real meetings, not promised."
          >
            Every figure below comes from our own evaluation on recorded meetings with ground-truth
            microphones for each person, and from real jobs on the live service.
          </SectionHeader>
          <dl className="mt-14 grid gap-px overflow-hidden rounded-[var(--radius-lg)] border border-border-subtle bg-border-subtle shadow-sm sm:grid-cols-2 lg:grid-cols-4">
            {RESULTS.map((r) => (
              <div key={r.label} className="flex flex-col gap-2 bg-bg-surface p-7">
                <dd className="display order-first text-[40px] text-fg-primary">{r.value}</dd>
                <dt className="text-[15px] font-semibold">{r.label}</dt>
                <p className="text-[13px] leading-relaxed text-fg-muted">{r.note}</p>
              </div>
            ))}
          </dl>
        </section>

        {/* ---------------------------------------------------------- privacy */}
        <section id="privacy" className="border-y border-border-subtle bg-bg-subtle py-24 sm:py-32">
          <div className="container-x grid gap-14 lg:grid-cols-2 lg:items-center">
            <div className="flex flex-col gap-10">
              <SectionHeader eyebrow="Privacy" title="Your recordings stay yours.">
                A tool that sees faces and hears voices has to earn trust. These are design
                decisions, not settings.
              </SectionHeader>
              <ul className="grid gap-6 sm:grid-cols-2">
                {PRIVACY.map((p) => (
                  <li key={p.title} className="flex flex-col gap-2">
                    <span className="grid h-9 w-9 place-items-center rounded-[var(--radius-sm)] bg-bg-surface text-fg-primary shadow-sm ring-1 ring-border-subtle">
                      {p.icon}
                    </span>
                    <h3 className="text-[15px] font-semibold">{p.title}</h3>
                    <p className="text-sm leading-relaxed text-fg-secondary">{p.body}</p>
                  </li>
                ))}
              </ul>
            </div>

            <div
              className="rounded-[var(--radius-xl)] border border-border-subtle bg-bg-surface p-6 shadow-lg sm:p-8"
              aria-label="Example job record"
            >
              <div className="flex items-center justify-between">
                <p className="text-sm font-semibold">weekly-sync.mp4</p>
                <span className="inline-flex items-center gap-1.5 rounded-full bg-success-soft px-2.5 py-0.5 text-[12px] font-medium text-success">
                  <span className="h-1.5 w-1.5 rounded-full bg-current" /> Complete
                </span>
              </div>
              <ol className="mt-6 flex flex-col">
                {[
                  ['Video received', 'Encrypted in transit'],
                  ['4 faces tracked', 'Crops made for this job only'],
                  ['4 voices separated', 'Voiceprints never saved'],
                  ['Captions written', 'From the separated audio'],
                  ['Face crops and voiceprints deleted', 'Automatically, on completion'],
                ].map(([title, detail], i, all) => (
                  <li key={title} className="relative flex gap-4 pb-6 last:pb-0">
                    {i < all.length - 1 && (
                      <span
                        aria-hidden="true"
                        className="absolute left-[11px] top-6 h-[calc(100%-12px)] w-px bg-border-default"
                      />
                    )}
                    <span className="relative grid h-6 w-6 shrink-0 place-items-center rounded-full bg-success text-white">
                      <IconCheck size={13} />
                    </span>
                    <div>
                      <p className="text-sm font-medium">{title}</p>
                      <p className="text-[13px] text-fg-muted">{detail}</p>
                    </div>
                  </li>
                ))}
              </ol>
            </div>
          </div>
        </section>

        {/* ------------------------------------------------------------- FAQ */}
        <section
          id="limits"
          className="container-x grid gap-12 py-24 sm:py-32 lg:grid-cols-[1fr_1.4fr]"
        >
          <SectionHeader eyebrow="Straight answers" title="What it does, and what it will not do.">
            Said here rather than discovered later.
          </SectionHeader>
          <div className="divide-y divide-border-subtle rounded-[var(--radius-lg)] border border-border-subtle bg-bg-surface shadow-sm">
            {FAQ.map((item) => (
              <details
                key={item.q}
                className="group px-6 py-5 [&_summary::-webkit-details-marker]:hidden"
              >
                <summary className="flex cursor-pointer list-none items-center justify-between gap-4 text-[15px] font-medium">
                  {item.q}
                  <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full border border-border-default text-fg-muted transition-transform duration-200 group-open:rotate-45">
                    <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
                      <path
                        d="M6 1v10M1 6h10"
                        stroke="currentColor"
                        strokeWidth="1.6"
                        strokeLinecap="round"
                      />
                    </svg>
                  </span>
                </summary>
                <p className="mt-3 pr-10 text-[15px] leading-relaxed text-fg-secondary">{item.a}</p>
              </details>
            ))}
          </div>
        </section>

        {/* --------------------------------------------------------- pricing */}
        <section id="pricing" className="border-t border-border-subtle bg-bg-subtle py-24 sm:py-32">
          <div className="container-x">
            <SectionHeader align="center" eyebrow="Pricing" title="Free while in preview.">
              Every feature, no card required. We will say clearly, in advance, if that ever
              changes.
            </SectionHeader>
            <div className="mx-auto mt-14 max-w-3xl overflow-hidden rounded-[var(--radius-xl)] border border-border-default bg-bg-surface shadow-xl">
              <div className="grid md:grid-cols-[1fr_1.15fr]">
                <div className="flex flex-col gap-5 border-b border-border-subtle p-8 md:border-b-0 md:border-r">
                  <span className="inline-flex w-fit items-center gap-1.5 rounded-full bg-accent-soft px-2.5 py-0.5 text-[12px] font-semibold text-accent">
                    <IconSparkles size={13} /> Preview
                  </span>
                  <div>
                    <p className="display text-5xl">₹0</p>
                    <p className="mt-1 text-sm text-fg-muted">per month, during the preview</p>
                  </div>
                  <ButtonLink href="/login" size="lg" className="w-full">
                    Create your account
                  </ButtonLink>
                  <p className="text-[12px] text-fg-muted">Sign in with Google or email.</p>
                </div>
                <ul className="flex flex-col gap-3.5 p-8">
                  <Check>Videos up to 60 minutes</Check>
                  <Check>Up to 4 speakers, each on their own track</Check>
                  <Check>Word-timed captions in 100 languages</Check>
                  <Check>Downloads at your source’s quality</Check>
                  <Check>Revocable share links</Check>
                  <Check>Resumable uploads</Check>
                </ul>
              </div>
            </div>
          </div>
        </section>

        {/* ------------------------------------------------------- final CTA */}
        <section className="container-x py-24 sm:py-28">
          <div className="relative overflow-hidden rounded-[28px] border border-border-subtle bg-bg-surface px-6 py-16 text-center shadow-lg sm:px-12">
            <div
              className="pointer-events-none absolute inset-0 glow-backdrop opacity-80"
              aria-hidden="true"
            />
            <div className="relative mx-auto flex max-w-2xl flex-col items-center gap-6">
              <span className="grid h-12 w-12 place-items-center rounded-[var(--radius-md)] bg-primary text-primary-fg shadow-md">
                <IconWaveform size={22} />
              </span>
              <h2 className="display text-3xl sm:text-5xl">Hear what everyone actually said.</h2>
              <p className="text-[17px] text-fg-secondary">
                Your first recording takes a minute to upload and a few to process.
              </p>
              <div className="flex flex-col gap-3 sm:flex-row">
                <ButtonLink href="/login" size="lg">
                  Get started free <IconArrowRight size={18} />
                </ButtonLink>
                <ButtonLink href="/demo" size="lg" variant="secondary">
                  <IconCaptions size={18} /> See the demo
                </ButtonLink>
              </div>
            </div>
          </div>
        </section>
      </main>
      <MarketingFooter />
    </>
  );
}
