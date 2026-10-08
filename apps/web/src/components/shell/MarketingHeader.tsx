'use client';

/**
 * The public site's header: sticky, frosted once the page scrolls, and a
 * proper disclosure menu on small screens (focus moves into it, Escape and a
 * route change close it).
 */

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useEffect, useState } from 'react';

import { IconMenu, IconX, Logo } from '@/components/icons';
import { ButtonLink, cx } from '@/components/ui';

import { ThemeToggle } from './ThemeToggle';

const NAV = [
  { href: '/#product', label: 'Product' },
  { href: '/#how-it-works', label: 'How it works' },
  { href: '/#languages', label: 'Languages' },
  { href: '/#results', label: 'Results' },
  { href: '/#privacy', label: 'Privacy' },
  { href: '/demo', label: 'Demo' },
];

export function MarketingHeader() {
  const [scrolled, setScrolled] = useState(false);
  const [open, setOpen] = useState(false);
  const pathname = usePathname();

  useEffect(() => {
    const onScroll = () => {
      setScrolled(window.scrollY > 8);
    };
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => {
      window.removeEventListener('scroll', onScroll);
    };
  }, []);

  useEffect(() => {
    setOpen(false);
  }, [pathname]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <header
      className={cx(
        'sticky top-0 z-50 border-b transition-[background-color,border-color] duration-200',
        scrolled || open ? 'glass border-border-subtle' : 'border-transparent bg-transparent',
      )}
    >
      <div className="container-x flex h-16 items-center gap-8">
        <Link href="/" aria-label="VisioVox home" className="shrink-0 rounded-[var(--radius-sm)]">
          <Logo />
        </Link>

        <nav aria-label="Main" className="hidden items-center gap-1 lg:flex">
          {NAV.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className="rounded-[var(--radius-sm)] px-3 py-2 text-sm font-medium text-fg-secondary transition-colors hover:text-fg-primary"
            >
              {item.label}
            </Link>
          ))}
        </nav>

        <div className="ml-auto flex items-center gap-2">
          <ThemeToggle />
          {/* Visibility lives on a wrapper: the button's own inline-flex would
              otherwise override `hidden` on small screens. */}
          <div className="hidden items-center gap-2 sm:flex">
            <ButtonLink href="/login" variant="ghost" size="sm">
              Sign in
            </ButtonLink>
            <ButtonLink href="/login" size="sm">
              Get started
            </ButtonLink>
          </div>
          <button
            type="button"
            className="grid h-9 w-9 place-items-center rounded-[var(--radius-sm)] text-fg-secondary hover:bg-bg-inset lg:hidden"
            aria-expanded={open}
            aria-controls="mobile-nav"
            aria-label={open ? 'Close menu' : 'Open menu'}
            onClick={() => {
              setOpen((v) => !v);
            }}
          >
            {open ? <IconX /> : <IconMenu />}
          </button>
        </div>
      </div>

      {open && (
        <nav
          id="mobile-nav"
          aria-label="Main"
          className="container-x border-t border-border-subtle pb-6 pt-3 lg:hidden"
        >
          <ul className="flex flex-col">
            {NAV.map((item) => (
              <li key={item.href}>
                <Link
                  href={item.href}
                  onClick={() => {
                    setOpen(false);
                  }}
                  className="block rounded-[var(--radius-sm)] px-2 py-3 text-[15px] font-medium text-fg-secondary hover:bg-bg-inset hover:text-fg-primary"
                >
                  {item.label}
                </Link>
              </li>
            ))}
          </ul>
          <div className="mt-4 grid grid-cols-2 gap-3">
            <ButtonLink href="/login" variant="secondary">
              Sign in
            </ButtonLink>
            <ButtonLink href="/login">Get started</ButtonLink>
          </div>
        </nav>
      )}
    </header>
  );
}
