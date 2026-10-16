'use client';

/**
 * The signed-in frame: one header for the whole product (navigation, a quick
 * "New" action, theme, account), and an optional page header so every screen
 * opens the same way - title, one line of context, the actions that matter.
 */

import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import type { ReactNode } from 'react';
import { useEffect, useRef, useState } from 'react';

import { IconFolder, IconLogOut, IconPlus, Logo } from '@/components/icons';
import { ButtonLink, cx } from '@/components/ui';
import { useSession } from '@/lib/store';

import { ThemeToggle } from './ThemeToggle';

const NAV = [{ href: '/projects', label: 'Projects', icon: <IconFolder size={16} /> }];

function UserMenu() {
  const email = useSession((s) => s.email);
  const signOut = useSession((s) => s.signOut);
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const initial = (email ?? '?').trim().charAt(0).toUpperCase();

  return (
    <div ref={root} className="relative">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Account"
        onClick={() => {
          setOpen((v) => !v);
        }}
        className="grid h-9 w-9 place-items-center rounded-full bg-gradient-to-br from-brand-1 to-brand-2 text-sm font-semibold text-white shadow-sm ring-2 ring-bg-base transition-transform hover:scale-[1.04]"
      >
        {initial}
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 top-11 z-50 w-64 overflow-hidden rounded-[var(--radius-md)] border border-border-subtle bg-bg-elevated shadow-lg animate-fade-up"
          style={{ animationDuration: '160ms' }}
        >
          <div className="border-b border-border-subtle px-4 py-3">
            <p className="text-[12px] text-fg-muted">Signed in as</p>
            <p className="truncate text-sm font-medium">{email ?? 'your account'}</p>
          </div>
          <div className="p-1.5">
            <Link
              role="menuitem"
              href="/projects"
              className="flex items-center gap-2.5 rounded-[var(--radius-sm)] px-2.5 py-2 text-sm text-fg-secondary hover:bg-bg-inset hover:text-fg-primary"
            >
              <IconFolder size={16} />
              Your projects
            </Link>
            <button
              role="menuitem"
              type="button"
              onClick={() => {
                void signOut().then(() => {
                  router.replace('/login');
                });
              }}
              className="flex w-full items-center gap-2.5 rounded-[var(--radius-sm)] px-2.5 py-2 text-left text-sm text-fg-secondary hover:bg-bg-inset hover:text-fg-primary"
            >
              <IconLogOut size={16} />
              Sign out
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  return (
    <div className="flex min-h-dvh flex-col bg-bg-subtle">
      <header className="glass sticky top-0 z-40 border-b border-border-subtle">
        <div className="mx-auto flex h-16 w-full max-w-[1280px] items-center gap-6 px-4 sm:px-6">
          <Link href="/" aria-label="VisioVox home" className="rounded-[var(--radius-sm)]">
            <Logo size={26} />
          </Link>
          <nav aria-label="App" className="flex items-center gap-1">
            {NAV.map((item) => {
              const on = pathname === item.href || pathname.startsWith(`${item.href}/`);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  aria-current={on ? 'page' : undefined}
                  className={cx(
                    'inline-flex items-center gap-2 rounded-[var(--radius-sm)] px-3 py-2 text-sm font-medium transition-colors',
                    on
                      ? 'bg-bg-inset text-fg-primary'
                      : 'text-fg-muted hover:bg-bg-inset hover:text-fg-primary',
                  )}
                >
                  <span className="hidden sm:inline">{item.icon}</span>
                  {item.label}
                </Link>
              );
            })}
          </nav>
          <div className="ml-auto flex items-center gap-2">
            <ButtonLink href="/upload" size="sm">
              <IconPlus size={16} />
              <span className="hidden sm:inline">New upload</span>
              <span className="sm:hidden">New</span>
            </ButtonLink>
            <ThemeToggle />
            <UserMenu />
          </div>
        </div>
      </header>
      <main id="content" className="flex-1">
        {children}
      </main>
    </div>
  );
}

/** The opening of every app screen: what this is, and what you can do here. */
export function PageHeader({
  title,
  description,
  actions,
  eyebrow,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  eyebrow?: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
      <div className="flex min-w-0 flex-col gap-1.5">
        {eyebrow}
        <h1 className="text-[28px] font-semibold tracking-[-0.025em] sm:text-[32px]">{title}</h1>
        {description !== undefined && (
          <p className="max-w-2xl text-[15px] text-fg-secondary">{description}</p>
        )}
      </div>
      {actions !== undefined && <div className="flex shrink-0 flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

/** Page width and rhythm for app screens. */
export function AppPage({ children, narrow = false }: { children: ReactNode; narrow?: boolean }) {
  return (
    <div
      className={cx(
        'mx-auto flex w-full flex-col gap-8 px-4 py-8 sm:px-6 sm:py-10',
        narrow ? 'max-w-3xl' : 'max-w-[1280px]',
      )}
    >
      {children}
    </div>
  );
}
