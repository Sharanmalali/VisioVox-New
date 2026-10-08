/**
 * /s/[token] — the public share page (docs/28 §W7).
 *
 * A Server Component so the Open Graph tags are real HTML: an unfurl in
 * WhatsApp or Slack is fetched by a bot that runs no JavaScript, so metadata
 * rendered on the client is metadata nobody sees.
 *
 * Deliberately `noindex`. A link someone sent to three people should not turn
 * up in a search result, and `robots` here is belt to the API's `X-Robots-Tag`
 * braces.
 */

import type { Metadata } from 'next';
import Link from 'next/link';
import type { ReactNode } from 'react';

import { IconLink, IconShare, Logo, LogoMark } from '@/components/icons';
import { SharePlayer } from '@/components/SharePlayer';
import { ThemeToggle } from '@/components/shell/ThemeToggle';
import { ButtonLink } from '@/components/ui';

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

interface SharePayload {
  title: string;
  duration_ms: number | null;
  speaker_count: number;
  speaker_ordinal: number | null;
  manifest: unknown;
}

async function load(token: string): Promise<SharePayload | null> {
  try {
    const response = await fetch(`${API}/v1/shared/${encodeURIComponent(token)}`, {
      // Never cached: revocation has to take effect immediately, and a cached
      // copy of a revoked share is precisely the failure this must not have.
      cache: 'no-store',
    });
    if (!response.ok) return null;
    return (await response.json()) as SharePayload;
  } catch {
    return null;
  }
}

function minutes(ms: number | null): string {
  if (ms === null) return '';
  const m = Math.round(ms / 60000);
  return m <= 1 ? '1 minute' : `${String(m)} minutes`;
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ token: string }>;
}): Promise<Metadata> {
  const { token } = await params;
  const data = await load(token);

  if (!data) {
    return { title: 'Link unavailable', robots: { index: false, follow: false } };
  }

  const who =
    data.speaker_ordinal !== null
      ? `Speaker ${String(data.speaker_ordinal)} isolated`
      : `${String(data.speaker_count)} speakers, separated`;
  const description = `${who}${data.duration_ms ? ` · ${minutes(data.duration_ms)}` : ''}`;

  return {
    title: `${data.title} — VisioVox`,
    description,
    robots: { index: false, follow: false },
    openGraph: {
      title: data.title,
      description,
      type: 'video.other',
    },
    twitter: { card: 'summary_large_image', title: data.title, description },
  };
}

function ShareChrome({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-dvh flex-col bg-bg-subtle">
      <header className="glass sticky top-0 z-40 border-b border-border-subtle">
        <div className="mx-auto flex h-16 w-full max-w-5xl items-center justify-between gap-4 px-4 sm:px-6">
          <Link href="/" aria-label="VisioVox home" className="rounded-[var(--radius-sm)]">
            <Logo size={26} />
          </Link>
          <div className="flex items-center gap-2">
            <ThemeToggle />
            <ButtonLink href="/" size="sm">
              Try VisioVox
            </ButtonLink>
          </div>
        </div>
      </header>
      <main id="content" className="flex-1">
        {children}
      </main>
    </div>
  );
}

export default async function SharePage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  const data = await load(token);

  if (!data) {
    return (
      <ShareChrome>
        <div className="mx-auto flex max-w-md flex-col items-center gap-4 px-6 py-24 text-center">
          <span className="grid h-14 w-14 place-items-center rounded-[var(--radius-md)] border border-border-subtle bg-bg-surface text-fg-secondary shadow-sm">
            <IconLink size={24} />
          </span>
          <h1 className="text-2xl font-semibold tracking-[-0.02em]">This link is not available</h1>
          <p className="text-[15px] leading-relaxed text-fg-secondary">
            It may have been revoked by whoever shared it, or it may have expired. Links cannot be
            recovered once revoked — ask the sender for a new one.
          </p>
          <ButtonLink href="/" variant="secondary" className="mt-2">
            What is VisioVox?
          </ButtonLink>
        </div>
      </ShareChrome>
    );
  }

  return (
    <ShareChrome>
      <div className="mx-auto flex w-full max-w-5xl flex-col gap-6 px-4 py-8 sm:px-6 sm:py-10">
        <div className="flex flex-col gap-2">
          <span className="inline-flex w-fit items-center gap-1.5 rounded-full border border-border-default bg-bg-surface px-2.5 py-0.5 text-[12px] font-medium text-fg-secondary">
            <IconShare size={12} />
            Shared with you
          </span>
          <h1 className="text-[28px] font-semibold tracking-[-0.025em] sm:text-[34px]">
            {data.title}
          </h1>
          <p className="text-[15px] text-fg-secondary">
            {data.speaker_ordinal !== null
              ? `Speaker ${String(data.speaker_ordinal)}, isolated from the other voices`
              : `${String(data.speaker_count)} speakers, each separated onto their own track`}
            {data.duration_ms ? ` · ${minutes(data.duration_ms)}` : ''}
          </p>
        </div>

        <div className="rounded-[var(--radius-lg)] border border-border-subtle bg-bg-surface p-4 shadow-sm sm:p-6">
          <SharePlayer manifest={data.manifest} title={data.title} />
        </div>

        <div className="flex flex-col items-start justify-between gap-4 rounded-[var(--radius-lg)] border border-border-subtle bg-bg-surface p-5 shadow-sm sm:flex-row sm:items-center">
          <div className="flex items-start gap-3">
            <LogoMark size={36} />
            <div>
              <p className="text-[15px] font-semibold">Separated with VisioVox</p>
              <p className="text-sm text-fg-muted">
                Upload a recording where people talk over each other, and hear one of them at a
                time.
              </p>
            </div>
          </div>
          <ButtonLink href="/login" className="shrink-0">
            Try it free
          </ButtonLink>
        </div>

        <p className="text-[12.5px] leading-relaxed text-fg-muted">
          Whoever shared this can revoke the link at any time. Voiceprints and face crops from the
          original recording were deleted when it finished processing.
        </p>
      </div>
    </ShareChrome>
  );
}
