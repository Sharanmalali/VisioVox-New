'use client';

/**
 * Light / dark. The current theme is whatever the bootstrap script in
 * layout.tsx put on <html> before first paint; this only flips it and
 * remembers the choice. Until mounted it renders a same-size placeholder, so
 * the server HTML never guesses the wrong icon.
 */

import { useEffect, useState } from 'react';

import { IconMoon, IconSun } from '@/components/icons';
import { cx } from '@/components/ui';

type Theme = 'light' | 'dark';

export function ThemeToggle({ className }: { className?: string }) {
  const [theme, setTheme] = useState<Theme | null>(null);

  useEffect(() => {
    setTheme(document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light');
  }, []);

  function flip() {
    const next: Theme = theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem('vv-theme', next);
    } catch {
      // Private mode: the choice lasts for the page, which is fine.
    }
    setTheme(next);
  }

  return (
    <button
      type="button"
      onClick={flip}
      aria-label={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}
      title={theme === 'dark' ? 'Light theme' : 'Dark theme'}
      className={cx(
        'grid h-9 w-9 place-items-center rounded-[var(--radius-sm)] text-fg-secondary transition-colors hover:bg-bg-inset hover:text-fg-primary',
        className,
      )}
    >
      {theme === null ? (
        <span className="h-[18px] w-[18px]" />
      ) : theme === 'dark' ? (
        <IconSun />
      ) : (
        <IconMoon />
      )}
    </button>
  );
}
