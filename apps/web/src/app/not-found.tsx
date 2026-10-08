import type { Metadata } from 'next';

import { IconArrowLeft, IconWaveform } from '@/components/icons';
import { MarketingFooter } from '@/components/shell/MarketingFooter';
import { MarketingHeader } from '@/components/shell/MarketingHeader';
import { ButtonLink } from '@/components/ui';

export const metadata: Metadata = { title: 'Page not found' };

export default function NotFound() {
  return (
    <>
      <MarketingHeader />
      <main id="content" className="relative overflow-hidden">
        <div className="pointer-events-none absolute inset-0 -z-10 bg-grid" aria-hidden="true" />
        <div className="container-x flex min-h-[60vh] flex-col items-center justify-center gap-6 py-24 text-center">
          <span className="grid h-14 w-14 place-items-center rounded-[var(--radius-md)] border border-border-subtle bg-bg-surface text-fg-secondary shadow-sm">
            <IconWaveform size={24} />
          </span>
          <p className="font-mono text-sm text-accent">404</p>
          <h1 className="display text-4xl sm:text-5xl">This page has gone quiet.</h1>
          <p className="max-w-md text-[17px] text-fg-secondary">
            The address may be mistyped, or the page may have moved. Everything else is right where
            you left it.
          </p>
          <div className="flex flex-col gap-3 sm:flex-row">
            <ButtonLink href="/">
              <IconArrowLeft size={16} />
              Back to home
            </ButtonLink>
            <ButtonLink href="/projects" variant="secondary">
              Your projects
            </ButtonLink>
          </div>
        </div>
      </main>
      <MarketingFooter />
    </>
  );
}
