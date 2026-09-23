'use client';

/**
 * The console chrome: the fascia the whole product is mounted on.
 *
 * The top bar is the navigation — INGEST, PREVIEW, VAULT are faces of one
 * machine rather than separate pages, so they are tabs holding client state
 * and not routes. The status strip along the bottom is the machine reporting
 * what it is doing, which is the one place a claim about the product belongs.
 */

import type { ReactNode } from 'react';

import { Key, cx } from '@/components/ui';

export type Tab = 'ingest' | 'preview' | 'vault';

const TABS: ReadonlyArray<{ id: Tab; label: string }> = [
  { id: 'ingest', label: 'Ingest' },
  { id: 'preview', label: 'Preview' },
  { id: 'vault', label: 'Vault' },
];

export function ConsoleChrome({
  tab,
  onTab,
  email,
  onSignOut,
  status,
  children,
}: {
  tab: Tab;
  onTab: (next: Tab) => void;
  email: string | null;
  onSignOut: () => void;
  /** The machine's own words for what it is doing right now. */
  status: string;
  children: ReactNode;
}) {
  return (
    <div className="mx-auto flex w-full max-w-[1400px] flex-col gap-5 px-4 py-6">
      {/* ---------------------------------------------------------- fascia -- */}
      <header className="flex flex-wrap items-stretch gap-3 border-4 border-edge bg-panel p-3 shadow-[7px_7px_0_0_var(--edge)]">
        <span className="display flex items-center bg-acid px-6 py-2 text-xl text-edge">
          VISIOVOX
        </span>

        <nav className="flex flex-wrap items-center gap-2" aria-label="Console">
          {TABS.map((t) => (
            <Key
              key={t.id}
              size="sm"
              tone={t.id === tab ? 'crimson' : 'ghost'}
              aria-current={t.id === tab ? 'page' : undefined}
              onClick={() => {
                onTab(t.id);
              }}
            >
              {t.label}
            </Key>
          ))}
        </nav>

        <div className="ml-auto flex items-center gap-3">
          {email !== null && (
            <span className="hidden font-mono text-[11px] text-dust sm:block">{email}</span>
          )}
          <Key size="sm" tone="ghost" onClick={onSignOut}>
            Sign out
          </Key>
        </div>
      </header>

      {children}

      {/* ------------------------------------------------------ status bar -- */}
      <footer className="flex flex-wrap items-center justify-between gap-3 border-t-4 border-edge pt-4">
        <p className="font-mono text-[11px] text-dust">
          Offline separation · four speakers maximum · never invents words
        </p>
        <p className="font-mono text-[11px] text-dust">{status}</p>
      </footer>
    </div>
  );
}

/** A module: the head strip names it, the body is whatever it does. */
export function Module({
  label,
  tone = 'bone',
  right,
  bodyClassName,
  className,
  children,
}: {
  label: string;
  tone?: 'bone' | 'crimson' | 'acid';
  right?: ReactNode;
  bodyClassName?: string;
  className?: string;
  children: ReactNode;
}) {
  const head = {
    bone: 'bg-chalk text-edge',
    crimson: 'bg-crimson text-white',
    acid: 'bg-acid text-edge',
  }[tone];
  return (
    <section
      className={cx(
        'flex flex-col border-4 border-edge bg-panel shadow-[7px_7px_0_0_var(--edge)]',
        className,
      )}
    >
      <div
        className={cx(
          'flex items-center justify-between gap-3 border-b-4 border-edge px-4 py-2',
          head,
        )}
      >
        <h2 className="font-mono text-[11px] font-bold uppercase tracking-[0.22em]">{label}</h2>
        {right !== undefined && <div className="font-mono text-[11px] font-bold">{right}</div>}
      </div>
      <div className={cx('flex-1', bodyClassName ?? 'p-5')}>{children}</div>
    </section>
  );
}
